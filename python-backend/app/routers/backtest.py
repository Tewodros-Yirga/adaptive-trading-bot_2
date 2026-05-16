"""
app/routers/backtest.py

Backtest endpoints — single run, batch, reports, PDF export.
"""
import importlib
import json
import uuid
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import Response, JSONResponse
from pymongo.database import Database

from ..auth_deps import get_current_user, require_write_access
from ..db import get_db, COLL_BACKTEST_RESULTS
from ..models import BacktestResult
from ..schemas import (
    BatchBacktestRequest,
    BatchBacktestResponse,
)
from .. import crud
from ..services.backtester import run_backtest, run_batch_backtest

router = APIRouter(prefix="/backtest", tags=["backtest"])


# ---------------------------------------------------------------------------
# Helper: executor removed — backtester runs as a standalone service
# ---------------------------------------------------------------------------

def _get_executor():
    return None


# ---------------------------------------------------------------------------
# POST /backtest/run — single or batch
# ---------------------------------------------------------------------------

@router.post("/run", status_code=202)
async def run(
    body: dict,
    background_tasks: BackgroundTasks,
    db: Database = Depends(get_db),
    _w=Depends(require_write_access),
):
    """
    Accept either a single-run payload (backward-compatible) or a batch payload
    containing a "runs" key.

    Single-run returns: {"backtest_id": int, "status": "started"}
    Batch returns:      {"batch_id": str, "run_count": int}
    """
    if "runs" in body:
        # ── Batch mode ────────────────────────────────────────────────────────
        try:
            batch_req = BatchBacktestRequest(**body)
        except Exception as e:
            raise HTTPException(status_code=422, detail=str(e))

        batch_id = str(uuid.uuid4())
        strategy_names = list({r.strategy_name for r in batch_req.runs})

        crud.create_backtest_batch(
            db,
            batch_id=batch_id,
            strategy_names=strategy_names,
            shared_settings=batch_req.shared_settings,
        )

        runs_as_dicts = [r.model_dump() for r in batch_req.runs]

        background_tasks.add_task(
            _run_batch_background,
            batch_id,
            runs_as_dicts,
            batch_req.shared_settings,
        )

        return BatchBacktestResponse(batch_id=batch_id, run_count=len(batch_req.runs))

    else:
        # ── Single-run mode (existing behaviour, backward-compatible) ─────────
        strategy_name = body.get("strategy_name", "DTC")
        symbol = body.get("symbol", "XAUUSD")
        from_date = body.get("from_date", "2024-01-01")
        to_date = body.get("to_date", "2024-12-31")
        params = body.get("params", {})
        initial_balance = float(body.get("initial_balance", 10000))
        leverage = int(body.get("leverage", 100))
        risk_per_trade_pct = float(body.get("risk_per_trade_pct", 1.0))

        bt_id = run_backtest(
            db,
            strategy_name=strategy_name,
            symbol=symbol,
            from_date=from_date,
            to_date=to_date,
            params=params,
            initial_balance=initial_balance,
            leverage=leverage,
            risk_per_trade_pct=risk_per_trade_pct,
        )
        return {"backtest_id": bt_id, "status": "started"}


async def _run_batch_background(
    batch_id: str,
    runs: list[dict],
    shared_settings: dict,
) -> None:
    """Background coroutine wrapper — creates its own DB session."""
    from ..db import get_database
    db = get_database()
    try:
        await run_batch_backtest(db, batch_id, runs, shared_settings, None)
    finally:
        pass  # pymongo client is managed at app level; no per-request close needed


# ---------------------------------------------------------------------------
# GET /backtest/results — list
# ---------------------------------------------------------------------------

@router.get("/results")
def list_results(limit: int = 20, db: Database = Depends(get_db)):
    docs = list(
        db[COLL_BACKTEST_RESULTS]
        .find()
        .sort("created_at", -1)
        .limit(limit)
    )
    results = []
    for d in docs:
        metrics = d.get("metrics_json", {})
        if isinstance(metrics, str):
            try:
                metrics = json.loads(metrics)
            except Exception:
                metrics = {}
        results.append({
            "id": d["_id"],
            "strategy_name": d.get("strategy_name"),
            "symbol": d.get("symbol"),
            "from_date": d.get("from_date"),
            "to_date": d.get("to_date"),
            "initial_balance": d.get("initial_balance"),
            "leverage": d.get("leverage"),
            "risk_per_trade_pct": d.get("risk_per_trade_pct"),
            "status": d.get("status"),
            "metrics": metrics,
            "batch_id": d.get("batch_id"),
            "created_at": d.get("created_at"),
            "completed_at": d.get("completed_at"),
        })
    return results


# ---------------------------------------------------------------------------
# GET /backtest/results/{id}
# ---------------------------------------------------------------------------

@router.get("/results/{bt_id}")
def get_result(bt_id: int, db: Database = Depends(get_db)):
    row = crud.get_backtest_result(db, bt_id)
    if not row:
        raise HTTPException(404, "Backtest not found")

    def _safe_loads(v, default):
        if isinstance(v, (dict, list)):
            return v
        try:
            return json.loads(v or json.dumps(default))
        except Exception:
            return default

    return {
        "id": row.id,
        "strategy_name": row.strategy_name,
        "symbol": row.symbol,
        "from_date": row.from_date,
        "to_date": row.to_date,
        "params": _safe_loads(row.params_json, {}),
        "initial_balance": row.initial_balance,
        "leverage": row.leverage,
        "risk_per_trade_pct": row.risk_per_trade_pct,
        "status": row.status,
        "metrics": _safe_loads(row.metrics_json, {}),
        "equity_curve": _safe_loads(row.equity_curve_json, []),
        "batch_id": row.batch_id,
        "created_at": row.created_at,
        "completed_at": row.completed_at,
    }


# ---------------------------------------------------------------------------
# GET /backtest/results/{id}/trade-log
# ---------------------------------------------------------------------------

@router.get("/results/{bt_id}/trade-log")
def get_trade_log(
    bt_id: int,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=500),
    db: Database = Depends(get_db),
):
    row = crud.get_backtest_result(db, bt_id)
    if not row:
        raise HTTPException(404, "Backtest not found")
    trade_log = row.trade_log_json or []
    total = len(trade_log)
    start = (page - 1) * limit
    end = start + limit
    return {
        "total": total,
        "page": page,
        "limit": limit,
        "trades": trade_log[start:end],
    }


# ---------------------------------------------------------------------------
# GET /backtest/results/{id}/parameter-evolution
# ---------------------------------------------------------------------------

@router.get("/results/{bt_id}/parameter-evolution")
def get_parameter_evolution(bt_id: int, db: Database = Depends(get_db)):
    row = crud.get_backtest_result(db, bt_id)
    if not row:
        raise HTTPException(404, "Backtest not found")
    return row.parameter_evolution_log_json or {"adaptation_events": []}


# ---------------------------------------------------------------------------
# GET /backtest/results/{id}/monthly-breakdown
# ---------------------------------------------------------------------------

@router.get("/results/{bt_id}/monthly-breakdown")
def get_monthly_breakdown(bt_id: int, db: Database = Depends(get_db)):
    row = crud.get_backtest_result(db, bt_id)
    if not row:
        raise HTTPException(404, "Backtest not found")
    return row.monthly_breakdown_json or {}


# ---------------------------------------------------------------------------
# GET /backtest/results/{id}/strategy-breakdown
# ---------------------------------------------------------------------------

@router.get("/results/{bt_id}/strategy-breakdown")
def get_strategy_breakdown(bt_id: int, db: Database = Depends(get_db)):
    row = crud.get_backtest_result(db, bt_id)
    if not row:
        raise HTTPException(404, "Backtest not found")

    def _safe_loads(v, default):
        if isinstance(v, (dict, list)):
            return v
        try:
            return json.loads(v or json.dumps(default))
        except Exception:
            return default

    return {
        "strategy_name": row.strategy_name,
        "metrics": _safe_loads(row.metrics_json, {}),
        "drawdown_periods": row.drawdown_periods_json or [],
        "strategy_performance_timeline": row.strategy_performance_timeline_json or {},
    }


# ---------------------------------------------------------------------------
# GET /backtest/results/{id}/report.pdf  — PDF export
# ---------------------------------------------------------------------------

@router.get("/results/{bt_id}/report.pdf")
async def get_backtest_pdf_report(
    bt_id: int,
    current_user=Depends(get_current_user),
    db: Database = Depends(get_db),
):
    """
    Generate and stream a professional PDF backtest report.
    Requires WeasyPrint (pip install weasyprint).
    Auth: any authenticated user.
    """
    try:
        importlib.import_module("weasyprint")
    except ImportError:
        return JSONResponse(
            status_code=501,
            content={
                "detail": (
                    "PDF generation requires WeasyPrint. "
                    "Install with: pip install weasyprint"
                )
            },
        )

    row = crud.get_backtest_result(db, bt_id)
    if not row:
        raise HTTPException(status_code=404, detail="Backtest result not found")

    html = _render_backtest_report_html(row)
    pdf_bytes = _html_to_pdf(html)

    filename = f"backtest_{bt_id}_{row.strategy_name}_{row.symbol}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# PDF helpers
# ---------------------------------------------------------------------------

def _html_to_pdf(html: str) -> bytes:
    from weasyprint import HTML
    import io

    buf = io.BytesIO()
    HTML(string=html).write_pdf(buf)
    return buf.getvalue()


def _render_backtest_report_html(result: BacktestResult) -> str:  # noqa: C901
    """Build a complete, self-contained HTML document for the backtest report."""
    def _safe_loads(v, default):
        if isinstance(v, (dict, list)):
            return v
        try:
            return json.loads(v or json.dumps(default))
        except Exception:
            return default

    metrics: dict = _safe_loads(result.metrics_json, {})
    equity_curve: list = _safe_loads(result.equity_curve_json, [])
    monthly: dict = result.monthly_breakdown_json or {}
    evolution: dict = result.parameter_evolution_log_json or {}
    trade_log: list = result.trade_log_json or []

    generated_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    # ── Equity curve SVG ──────────────────────────────────────────────────
    equity_svg = _build_equity_svg(equity_curve)

    # ── Metric rows ───────────────────────────────────────────────────────
    metric_keys = [
        ("total_trades", "Total Trades", ""),
        ("win_rate", "Win Rate", "%"),
        ("profit_factor", "Profit Factor", ""),
        ("net_roi_pct", "Net ROI", "%"),
        ("max_drawdown_pct", "Max Drawdown", "%"),
        ("sharpe_ratio", "Sharpe Ratio", ""),
        ("sortino_ratio", "Sortino Ratio", ""),
        ("calmar_ratio", "Calmar Ratio", ""),
        ("avg_rr", "Average R:R", ""),
        ("expectancy", "Expectancy", ""),
        ("consecutive_wins", "Max Consecutive Wins", ""),
        ("consecutive_losses", "Max Consecutive Losses", ""),
        ("total_pnl", "Net PnL", ""),
    ]

    metrics_rows = ""
    for i, (key, label, suffix) in enumerate(metric_keys):
        val = metrics.get(key, "—")
        if isinstance(val, float):
            val = f"{val:.2f}{suffix}"
        elif val != "—":
            val = f"{val}{suffix}"
        row_class = "alt" if i % 2 == 0 else ""
        metrics_rows += f'<tr class="{row_class}"><td>{label}</td><td>{val}</td></tr>\n'

    # ── Monthly breakdown table ────────────────────────────────────────────
    monthly_rows = ""
    if monthly:
        for i, (period, data) in enumerate(sorted(monthly.items())):
            row_class = "alt" if i % 2 == 0 else ""
            wins = data.get("wins", 0)
            losses = data.get("losses", 0)
            total = wins + losses
            wr = f"{(wins / total * 100):.1f}%" if total > 0 else "—"
            pnl = data.get("net_pnl", 0)
            monthly_rows += (
                f'<tr class="{row_class}">'
                f"<td>{period}</td><td>{wins}</td><td>{losses}</td>"
                f'<td style="color:{"#2ecc71" if pnl >= 0 else "#e74c3c"}">'
                f"{pnl:+.2f}</td><td>{wr}</td></tr>\n"
            )
    else:
        monthly_rows = '<tr><td colspan="5" class="empty">No monthly data available</td></tr>'

    # ── Parameter evolution ───────────────────────────────────────────────
    events = evolution.get("adaptation_events", [])
    evolution_rows = ""
    if events:
        for i, evt in enumerate(events):
            row_class = "alt" if i % 2 == 0 else ""
            idx = evt.get("after_trade_index", "?")
            wr_at = evt.get("win_rate_at_time", "?")
            if isinstance(wr_at, float):
                wr_at = f"{wr_at:.1f}%"
            deltas = evt.get("param_deltas", {})
            delta_str = ", ".join(
                f"{k}: {v:+.4f}" if isinstance(v, float) else f"{k}: {v}"
                for k, v in list(deltas.items())[:5]
            )
            evolution_rows += (
                f'<tr class="{row_class}">'
                f"<td>{idx}</td><td>{wr_at}</td><td>{delta_str or '—'}</td></tr>\n"
            )
    else:
        evolution_rows = '<tr><td colspan="3" class="empty">No parameter adaptation events recorded</td></tr>'

    # ── Top winning / losing trades ────────────────────────────────────────
    def _trade_table(trades: list, top_n: int = 10) -> str:
        if not trades:
            return '<tr><td colspan="8" class="empty">No trades available</td></tr>'
        rows = ""
        for i, t in enumerate(trades[:top_n]):
            row_class = "alt" if i % 2 == 0 else ""
            pnl_val = t.get("pnl", 0) or 0
            pnl_color = "#2ecc71" if pnl_val >= 0 else "#e74c3c"
            rows += (
                f'<tr class="{row_class}">'
                f'<td>{i + 1}</td>'
                f'<td>{t.get("symbol", "?")}</td>'
                f'<td>{t.get("direction", "?")}</td>'
                f'<td>{t.get("entry_price", "?")}</td>'
                f'<td>{t.get("exit_price", "?")}</td>'
                f'<td style="color:{pnl_color}">{pnl_val:+.2f}</td>'
                f'<td>{t.get("duration_mins", "?")}</td>'
                f'<td>{t.get("exit_reason", "?")}</td>'
                "</tr>\n"
            )
        return rows

    sorted_by_pnl = sorted(trade_log, key=lambda t: t.get("pnl") or 0, reverse=True)
    winning_rows = _trade_table(sorted_by_pnl, 10)
    losing_rows = _trade_table(list(reversed(sorted_by_pnl)), 10)

    # ── Assemble HTML ──────────────────────────────────────────────────────
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Backtest Report — {result.strategy_name} on {result.symbol}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif; font-size: 11pt; color: #1a1a2e; background: #fff; line-height: 1.5; }}
  .page {{ padding: 28px 36px; page-break-after: always; }}
  .page:last-child {{ page-break-after: auto; }}
  .report-header {{ background: #1e2d3d; color: #fff; padding: 20px 24px; border-radius: 6px; margin-bottom: 24px; }}
  .report-header h1 {{ font-size: 18pt; font-weight: 700; margin-bottom: 4px; }}
  .report-header .subtitle {{ font-size: 10pt; opacity: 0.75; }}
  .report-header .meta {{ margin-top: 12px; font-size: 9.5pt; opacity: 0.85; display: flex; gap: 24px; }}
  h2 {{ font-size: 13pt; font-weight: 600; color: #1e2d3d; border-left: 4px solid #3a7bd5; padding-left: 10px; margin: 20px 0 12px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 9.5pt; margin-bottom: 16px; }}
  thead tr {{ background: #1e2d3d; color: #fff; }}
  thead th {{ padding: 8px 10px; text-align: left; font-weight: 600; font-size: 9pt; letter-spacing: 0.03em; }}
  tbody tr {{ background: #fff; }}
  tbody tr.alt {{ background: #f8f9fa; }}
  tbody td {{ padding: 6px 10px; border-bottom: 1px solid #e8ecef; }}
  .empty {{ color: #999; font-style: italic; text-align: center; padding: 16px; }}
  .equity-wrap {{ border: 1px solid #e8ecef; border-radius: 6px; overflow: hidden; margin: 12px 0; }}
  .footer {{ font-size: 8.5pt; color: #aaa; text-align: center; margin-top: 24px; padding-top: 8px; border-top: 1px solid #e8ecef; }}
</style>
</head>
<body>
<div class="page">
  <div class="report-header">
    <h1>Backtest Report — {result.strategy_name} on {result.symbol}</h1>
    <div class="subtitle">AlgoTrade Pro · Automated Strategy Analysis</div>
    <div class="meta">
      <span>📅 {result.from_date} → {result.to_date}</span>
      <span>💰 Balance: ${result.initial_balance:,.0f}</span>
      <span>⚡ Leverage: {result.leverage}x</span>
      <span>🎯 Risk/Trade: {result.risk_per_trade_pct}%</span>
      <span>🕐 Generated: {generated_at}</span>
    </div>
  </div>
  <h2>Performance Summary</h2>
  <table><thead><tr><th>Metric</th><th>Value</th></tr></thead><tbody>{metrics_rows}</tbody></table>
  <h2>Equity Curve</h2>
  <div class="equity-wrap">{equity_svg}</div>
  <div class="footer">AlgoTrade Pro — Backtest ID #{result.id} — {result.strategy_name}</div>
</div>
<div class="page">
  <div class="report-header">
    <h1>Monthly Performance Breakdown</h1>
    <div class="subtitle">{result.strategy_name} · {result.symbol} · {result.from_date} → {result.to_date}</div>
  </div>
  <h2>Month-by-Month Results</h2>
  <table><thead><tr><th>Period</th><th>Wins</th><th>Losses</th><th>Net PnL</th><th>Win Rate</th></tr></thead><tbody>{monthly_rows}</tbody></table>
  <div class="footer">AlgoTrade Pro — Backtest ID #{result.id}</div>
</div>
<div class="page">
  <div class="report-header">
    <h1>Parameter Adaptation Log</h1>
    <div class="subtitle">{result.strategy_name} · Continuous optimisation events during backtest</div>
  </div>
  <h2>Adaptation Events</h2>
  <table><thead><tr><th>After Trade #</th><th>Win Rate at Event</th><th>Parameter Deltas (top 5)</th></tr></thead><tbody>{evolution_rows}</tbody></table>
  <div class="footer">AlgoTrade Pro — Backtest ID #{result.id}</div>
</div>
<div class="page">
  <div class="report-header">
    <h1>Top 10 Winning Trades</h1>
    <div class="subtitle">{result.strategy_name} · {result.symbol} · sorted by PnL descending</div>
  </div>
  <h2>Best Trades</h2>
  <table><thead><tr><th>#</th><th>Symbol</th><th>Dir</th><th>Entry</th><th>Exit</th><th>PnL</th><th>Duration (min)</th><th>Exit Reason</th></tr></thead><tbody>{winning_rows}</tbody></table>
  <div class="footer">AlgoTrade Pro — Backtest ID #{result.id}</div>
</div>
<div class="page">
  <div class="report-header">
    <h1>Top 10 Losing Trades</h1>
    <div class="subtitle">{result.strategy_name} · {result.symbol} · sorted by PnL ascending</div>
  </div>
  <h2>Worst Trades</h2>
  <table><thead><tr><th>#</th><th>Symbol</th><th>Dir</th><th>Entry</th><th>Exit</th><th>PnL</th><th>Duration (min)</th><th>Exit Reason</th></tr></thead><tbody>{losing_rows}</tbody></table>
  <div class="footer">AlgoTrade Pro — Backtest ID #{result.id}</div>
</div>
</body>
</html>"""


def _build_equity_svg(equity_curve: list, width: int = 700, height: int = 200) -> str:
    if not equity_curve:
        return (
            f'<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">'
            f'<rect width="{width}" height="{height}" fill="#f8f9fa"/>'
            f'<text x="{width//2}" y="{height//2}" text-anchor="middle" '
            f'fill="#aaa" font-family="Inter,sans-serif" font-size="12">No equity data</text>'
            f"</svg>"
        )

    values: list[float] = []
    for item in equity_curve:
        if isinstance(item, (int, float)):
            values.append(float(item))
        elif isinstance(item, dict):
            v = item.get("cumulative_pnl") or item.get("equity") or item.get("value") or 0
            values.append(float(v))

    if not values:
        return f'<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg"></svg>'

    pad = 20
    chart_w = width - pad * 2
    chart_h = height - pad * 2
    min_v = min(values)
    max_v = max(values)
    v_range = max_v - min_v or 1.0

    def _x(i: int) -> float:
        return pad + (i / max(len(values) - 1, 1)) * chart_w

    def _y(v: float) -> float:
        return pad + (1 - (v - min_v) / v_range) * chart_h

    points = " ".join(f"{_x(i):.1f},{_y(v):.1f}" for i, v in enumerate(values))
    baseline_y = _y(0) if min_v < 0 < max_v else (pad + chart_h)
    fill_points = f"{pad:.1f},{baseline_y:.1f} " + points + f" {pad + chart_w:.1f},{baseline_y:.1f}"

    zero_line = ""
    if min_v < 0 < max_v:
        zy = _y(0)
        zero_line = (
            f'<line x1="{pad}" y1="{zy:.1f}" x2="{pad + chart_w}" y2="{zy:.1f}" '
            f'stroke="#ccc" stroke-width="1" stroke-dasharray="4,3"/>'
        )

    final_val = values[-1]
    final_color = "#2ecc71" if final_val >= 0 else "#e74c3c"

    return f"""<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">
  <rect width="{width}" height="{height}" fill="#fafbfc"/>
  {zero_line}
  <polygon points="{fill_points}" fill="rgba(58,123,213,0.12)"/>
  <polyline points="{points}" fill="none" stroke="#3a7bd5" stroke-width="2" stroke-linejoin="round"/>
  <text x="{pad}" y="{height - 4}" font-family="Inter,sans-serif" font-size="9" fill="#999">{min_v:+.2f}</text>
  <text x="{pad}" y="{pad - 4}" font-family="Inter,sans-serif" font-size="9" fill="#999">{max_v:+.2f}</text>
  <text x="{width - pad}" y="{_y(final_val):.1f}" font-family="Inter,sans-serif" font-size="9"
    fill="{final_color}" text-anchor="end" dy="-4">{final_val:+.2f}</text>
</svg>"""


# ---------------------------------------------------------------------------
# GET /backtest/batch/{batch_id}
# ---------------------------------------------------------------------------

@router.get("/batch/{batch_id}")
def get_batch(batch_id: str, db: Database = Depends(get_db)):
    batch = crud.get_backtest_batch(db, batch_id)
    if not batch:
        raise HTTPException(404, "Batch not found")
    results = crud.get_backtest_results_for_batch(db, batch_id)

    def _safe_loads(v, default):
        if isinstance(v, (dict, list)):
            return v
        try:
            return json.loads(v or json.dumps(default))
        except Exception:
            return default

    return {
        "id": batch.id,
        "batch_id": batch.batch_id,
        "strategy_names": batch.strategy_names,
        "shared_settings_json": batch.shared_settings_json,
        "status": batch.status,
        "cross_analysis_json": batch.cross_analysis_json,
        "created_at": batch.created_at,
        "completed_at": batch.completed_at,
        "results": [
            {
                "id": r.id,
                "strategy_name": r.strategy_name,
                "symbol": r.symbol,
                "from_date": r.from_date,
                "to_date": r.to_date,
                "status": r.status,
                "metrics": _safe_loads(r.metrics_json, {}),
                "created_at": r.created_at,
                "completed_at": r.completed_at,
            }
            for r in results
        ],
    }


# ---------------------------------------------------------------------------
# GET /backtest/batch/{batch_id}/pair-analysis
# ---------------------------------------------------------------------------

@router.get("/batch/{batch_id}/pair-analysis")
def get_pair_analysis(batch_id: str, db: Database = Depends(get_db)):
    batch = crud.get_backtest_batch(db, batch_id)
    if not batch:
        raise HTTPException(404, "Batch not found")
    analyses = crud.get_pair_analyses_for_batch(db, batch_id)
    return [
        {
            "id": a.id,
            "batch_id": a.batch_id,
            "strategy_names": a.strategy_names_json,
            "combination_type": a.combination_type,
            "combined_win_rate": a.combined_win_rate,
            "combined_roi_pct": a.combined_roi_pct,
            "combined_profit_factor": a.combined_profit_factor,
            "combined_composite_score": a.combined_composite_score,
            "individual_scores": a.individual_scores_json,
            "agreement_rate": a.agreement_rate,
            "disagreement_win_rate": a.disagreement_win_rate,
            "correlation": a.correlation,
            "synergy_score": a.synergy_score,
            "recommended": a.recommended,
            "analysis": a.analysis_json,
            "computed_at": a.computed_at,
        }
        for a in analyses
    ]


# ---------------------------------------------------------------------------
# GET /backtest/batch/{batch_id}/ensemble-simulation
# ---------------------------------------------------------------------------

@router.get("/batch/{batch_id}/ensemble-simulation")
def get_ensemble_simulation(batch_id: str, db: Database = Depends(get_db)):
    batch = crud.get_backtest_batch(db, batch_id)
    if not batch:
        raise HTTPException(404, "Batch not found")
    cross = batch.cross_analysis_json or {}
    return cross.get("ensemble_simulation", {})


# ---------------------------------------------------------------------------
# GET /backtest/batch/{batch_id}/report  — full combined JSON report
# ---------------------------------------------------------------------------

@router.get("/batch/{batch_id}/report")
def get_batch_report(batch_id: str, db: Database = Depends(get_db)):
    batch = crud.get_backtest_batch(db, batch_id)
    if not batch:
        raise HTTPException(404, "Batch not found")
    results = crud.get_backtest_results_for_batch(db, batch_id)
    analyses = crud.get_pair_analyses_for_batch(db, batch_id)

    def _safe_loads(v, default):
        if isinstance(v, (dict, list)):
            return v
        try:
            return json.loads(v or json.dumps(default))
        except Exception:
            return default

    results_out = [
        {
            "id": r.id,
            "strategy_name": r.strategy_name,
            "symbol": r.symbol,
            "from_date": r.from_date,
            "to_date": r.to_date,
            "status": r.status,
            "params": _safe_loads(r.params_json, {}),
            "initial_balance": r.initial_balance,
            "leverage": r.leverage,
            "risk_per_trade_pct": r.risk_per_trade_pct,
            "metrics": _safe_loads(r.metrics_json, {}),
            "equity_curve": _safe_loads(r.equity_curve_json, []),
            "monthly_breakdown": r.monthly_breakdown_json or {},
            "drawdown_periods": r.drawdown_periods_json or [],
            "parameter_evolution": r.parameter_evolution_log_json or {},
            "strategy_performance_timeline": r.strategy_performance_timeline_json or {},
            "trade_log_count": len(r.trade_log_json or []),
            "created_at": r.created_at,
            "completed_at": r.completed_at,
        }
        for r in results
    ]

    pair_analyses_out = [
        {
            "id": a.id,
            "strategy_names": a.strategy_names_json,
            "combination_type": a.combination_type,
            "combined_win_rate": a.combined_win_rate,
            "combined_roi_pct": a.combined_roi_pct,
            "combined_profit_factor": a.combined_profit_factor,
            "combined_composite_score": a.combined_composite_score,
            "individual_scores": a.individual_scores_json,
            "agreement_rate": a.agreement_rate,
            "disagreement_win_rate": a.disagreement_win_rate,
            "correlation": a.correlation,
            "synergy_score": a.synergy_score,
            "recommended": a.recommended,
            "analysis": a.analysis_json,
            "computed_at": a.computed_at,
        }
        for a in analyses
    ]

    return {
        "batch": {
            "id": batch.id,
            "batch_id": batch.batch_id,
            "strategy_names": batch.strategy_names,
            "shared_settings": batch.shared_settings_json,
            "status": batch.status,
            "created_at": batch.created_at,
            "completed_at": batch.completed_at,
        },
        "individual_results": results_out,
        "cross_analysis": batch.cross_analysis_json or {},
        "pair_analyses": pair_analyses_out,
    }


# ---------------------------------------------------------------------------
# POST /backtest/compare
# ---------------------------------------------------------------------------

@router.post("/compare")
def compare(body: dict, db: Database = Depends(get_db)):
    def _safe_loads(v, default):
        if isinstance(v, (dict, list)):
            return v
        try:
            return json.loads(v or json.dumps(default))
        except Exception:
            return default

    ids = body.get("ids", [])
    results = []
    for bt_id in ids:
        row = crud.get_backtest_result(db, bt_id)
        if row:
            results.append({
                "id": row.id,
                "strategy_name": row.strategy_name,
                "symbol": row.symbol,
                "from_date": row.from_date,
                "to_date": row.to_date,
                "metrics": _safe_loads(row.metrics_json, {}),
                "equity_curve": _safe_loads(row.equity_curve_json, []),
                "monthly_breakdown": row.monthly_breakdown_json or {},
                "drawdown_periods": row.drawdown_periods_json or [],
                "parameter_evolution": row.parameter_evolution_log_json or {},
                "batch_id": row.batch_id,
            })
    return {"comparisons": results}