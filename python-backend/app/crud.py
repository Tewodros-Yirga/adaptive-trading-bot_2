"""
CRUD helpers — rewritten for MongoDB (pymongo).

The `db` parameter throughout is a pymongo `Database` object (from get_database()),
NOT a SQLAlchemy Session. All collection names come from db.py constants.
"""
import hashlib
import json
from datetime import datetime, timezone

from pymongo.database import Database

from .db import (
    COLL_TRADES, COLL_PARAMETER_VERSIONS, COLL_ADAPTATION_LOGS,
    COLL_APP_SETTINGS, COLL_STRATEGIES, COLL_BACKTEST_RESULTS,
    COLL_BACKTEST_CANDIDATES, COLL_BACKTEST_BATCHES,
    COLL_STRATEGY_PAIR_ANALYSES, COLL_ENSEMBLE_DECISIONS,
    COLL_NEWS_VETO_DECISIONS,
    next_id,
)
from .models import (
    AdaptationLog, AppSetting, BacktestBatch, BacktestCandidate,
    BacktestResult, EnsembleDecision, NewsVetoDecision, ParameterVersion,
    StrategyPairAnalysis, Strategy, Trade,
)


# ---------------------------------------------------------------------------
# Trade CRUD
# ---------------------------------------------------------------------------

def log_trade(db: Database, fields: dict) -> Trade:
    """
    Insert a new trade document.

    Guarantees:
    - ``opened_at`` defaults to utcnow if not supplied.
    - ``result`` defaults to ``"OPEN"`` if not supplied.
    - ``closed_at`` is explicitly removed from the insert document so that
      ``get_recent_trades`` (which queries ``closed_at: {$exists: false}``)
      correctly finds it as an open trade.
    """
    fields = dict(fields)
    fields.setdefault("opened_at", datetime.utcnow())
    fields.setdefault("result", "OPEN")
    # Never persist closed_at on a freshly opened trade
    fields.pop("closed_at", None)
    fields["_id"] = next_id(db, COLL_TRADES)
    db[COLL_TRADES].insert_one(fields)
    return Trade.from_doc(fields)


def close_trade(
    db: Database,
    trade_id: int,
    exit_price: float,
    pnl: float,
    result: str,
    mt5_ticket: int | None = None,
) -> Trade | None:
    """
    Mark a trade as closed.

    Args:
        db: MongoDB database handle.
        trade_id: Integer ``_id`` of the trade document.
        exit_price: Price at which the position was closed.
        pnl: Realised profit/loss in account currency.
        result: ``"WIN"``, ``"LOSS"``, or ``"BLOCKED"``.
        mt5_ticket: Optional MT5 ticket number. Written to the document if provided,
                    enabling future reconciliation lookups.

    Returns:
        Updated :class:`Trade` dataclass, or ``None`` if not found.
    """
    doc = db[COLL_TRADES].find_one({"_id": trade_id})
    if not doc:
        return None
    now = datetime.utcnow()
    opened_at = doc.get("opened_at")
    duration_mins: float | None = None
    if opened_at is not None:
        try:
            # Strip tzinfo if present so subtraction works cleanly with naive utcnow()
            if hasattr(opened_at, "tzinfo") and opened_at.tzinfo is not None:
                opened_at = opened_at.replace(tzinfo=None)
            duration_mins = round((now - opened_at).total_seconds() / 60.0, 1)
        except Exception:
            duration_mins = None
    update: dict = {
        "exit_price": exit_price,
        "pnl": pnl,
        "result": result,
        "closed_at": now,
        "duration_mins": duration_mins,
    }
    if mt5_ticket is not None:
        update["mt5_ticket"] = mt5_ticket
    db[COLL_TRADES].update_one({"_id": trade_id}, {"$set": update})
    doc.update(update)
    return Trade.from_doc(doc)


def get_recent_trades(db: Database, limit: int = 50) -> list[Trade]:
    """
    Return the most-recent *open* trades (those with no ``closed_at`` field).

    Previously this returned all trades sorted by open time, which meant closed
    trades appeared in the "open positions" view. The query now strictly filters
    to documents where ``closed_at`` does not exist.
    """
    docs = (
        db[COLL_TRADES]
        .find({"closed_at": {"$exists": False}})
        .sort("opened_at", -1)
        .limit(limit)
    )
    return [Trade.from_doc(d) for d in docs]


def get_closed_trades(db: Database, limit: int = 100) -> list[Trade]:
    """
    Return recently closed trades (result WIN, LOSS, or BLOCKED) that also
    have a ``closed_at`` timestamp — the two conditions together prevent
    partially-updated documents from leaking into analytics.
    """
    docs = (
        db[COLL_TRADES]
        .find({
            "result": {"$in": ["WIN", "LOSS", "BLOCKED"]},
            "closed_at": {"$exists": True},
        })
        .sort("closed_at", -1)
        .limit(limit)
    )
    return [Trade.from_doc(d) for d in docs]


def get_recent_closed_trades_for_strategy(
    db: Database, strategy_name: str, limit: int = 20
) -> list[Trade]:
    docs = (
        db[COLL_TRADES]
        .find({"result": {"$in": ["WIN", "LOSS"]}, "strategy_name": strategy_name})
        .sort("closed_at", -1)
        .limit(limit)
    )
    return [Trade.from_doc(d) for d in docs]


# Alias used by startup_checks and other services
get_recent_closed_trades = get_recent_closed_trades_for_strategy


def get_stats(db: Database) -> dict:
    """
    Compute aggregate trading statistics.

    Returns:
        Dict with keys:
        - ``total_trades``, ``wins``, ``losses``, ``win_rate`` (decimal 0–1),
          ``profit_factor``, ``total_pnl``, ``max_drawdown``, ``avg_rr``
        - ``today_pnl`` — sum of PnL for trades closed today (UTC)
        - ``today_trades`` — count of trades *opened* today (UTC)
        - ``open_trades`` — count of trades currently open (no ``closed_at``)
    """
    trades = get_closed_trades(db, 1000)

    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    if not trades:
        # Still compute today/open counts even with no closed trades
        today_pnl = 0.0
        today_trades = db[COLL_TRADES].count_documents({
            "opened_at": {"$gte": today_start},
        })
        open_trades = db[COLL_TRADES].count_documents({"closed_at": {"$exists": False}})
        return {
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "total_pnl": 0.0,
            "max_drawdown": 0.0,
            "avg_rr": 0.0,
            "today_pnl": today_pnl,
            "today_trades": today_trades,
            "open_trades": open_trades,
        }

    wins = [t for t in trades if t.result == "WIN"]
    losses = [t for t in trades if t.result == "LOSS"]
    gross_profit = sum(t.pnl or 0 for t in wins)
    gross_loss = abs(sum(t.pnl or 0 for t in losses))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else 999.0

    cumulative, peak, max_drawdown = 0.0, 0.0, 0.0
    for t in reversed(trades):
        cumulative += t.pnl or 0
        peak = max(peak, cumulative)
        max_drawdown = max(max_drawdown, peak - cumulative)

    rr_values = []
    for t in trades:
        if t.entry_price and t.stop_loss and t.take_profit:
            risk = abs(t.entry_price - t.stop_loss)
            reward = abs(t.take_profit - t.entry_price)
            if risk > 0:
                rr_values.append(reward / risk)
    avg_rr = sum(rr_values) / len(rr_values) if rr_values else 0.0

    # Today's PnL — only trades with closed_at >= today UTC midnight
    today_pnl = sum(
        (t.pnl or 0)
        for t in trades
        if t.closed_at and (
            t.closed_at if t.closed_at.tzinfo
            else t.closed_at.replace(tzinfo=timezone.utc)
        ) >= today_start
    )

    # Today's opened trade count (open + closed that started today)
    today_trades = db[COLL_TRADES].count_documents({
        "opened_at": {"$gte": today_start},
    })

    # Currently open positions
    open_trades = db[COLL_TRADES].count_documents({"closed_at": {"$exists": False}})

    return {
        "total_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        # win_rate as decimal (0.0–1.0) so the frontend multiplies by 100
        "win_rate": round(len(wins) / len(trades), 4) if trades else 0.0,
        "profit_factor": round(profit_factor, 3),
        "total_pnl": round(sum(t.pnl or 0 for t in trades), 4),
        "max_drawdown": round(max_drawdown, 4),
        "avg_rr": round(avg_rr, 2),
        "today_pnl": round(today_pnl, 4),
        "today_trades": today_trades,
        "open_trades": open_trades,
    }


# ---------------------------------------------------------------------------
# Parameter CRUD
# ---------------------------------------------------------------------------

def save_params(
    db: Database,
    params: dict,
    reason: str = "",
    trigger: str = "AUTO",
    confidence_score: float | None = None,
    delta_magnitude: float | None = None,
    rollback_from_version: int | None = None,
    strategy_name: str | None = None,
) -> ParameterVersion:
    last = db[COLL_PARAMETER_VERSIONS].find_one(sort=[("version", -1)])
    version = (last["version"] + 1) if last else 1
    doc = {
        "_id": next_id(db, COLL_PARAMETER_VERSIONS),
        "version": version,
        "params_json": json.dumps(params),
        "reason": reason,
        "trigger": trigger,
        "confidence_score": confidence_score,
        "delta_magnitude": delta_magnitude,
        "rollback_from_version": rollback_from_version,
        "strategy_name": strategy_name,
        "created_at": datetime.utcnow(),
    }
    db[COLL_PARAMETER_VERSIONS].insert_one(doc)
    return ParameterVersion.from_doc(doc)


def get_current_params(db: Database) -> dict | None:
    last = db[COLL_PARAMETER_VERSIONS].find_one(sort=[("version", -1)])
    return json.loads(last["params_json"]) if last else None


def get_params_history(db: Database, limit: int = 30) -> list[ParameterVersion]:
    docs = db[COLL_PARAMETER_VERSIONS].find().sort("version", -1).limit(limit)
    return [ParameterVersion.from_doc(d) for d in docs]


def get_latest_param_version_for_strategy(
    db: Database, strategy_name: str
) -> ParameterVersion | None:
    doc = db[COLL_PARAMETER_VERSIONS].find_one(
        {"strategy_name": strategy_name},
        sort=[("version", -1)],
    )
    return ParameterVersion.from_doc(doc) if doc else None


# Alias used by startup_checks and other services
get_latest_param_version = get_latest_param_version_for_strategy


# ---------------------------------------------------------------------------
# Adaptation Log CRUD
# ---------------------------------------------------------------------------

def log_adaptation(db: Database, fields: dict) -> AdaptationLog:
    fields = dict(fields)
    fields.setdefault("evaluated_at", datetime.utcnow())
    fields["_id"] = next_id(db, COLL_ADAPTATION_LOGS)
    db[COLL_ADAPTATION_LOGS].insert_one(fields)
    return AdaptationLog.from_doc(fields)


# ---------------------------------------------------------------------------
# AppSetting CRUD
# ---------------------------------------------------------------------------

def get_setting(db: Database, key: str) -> str | None:
    doc = db[COLL_APP_SETTINGS].find_one({"key": key})
    return doc["value"] if doc else None


# Synchronous alias (used inside background tasks that already have a db)
get_setting_sync = get_setting


def set_setting(db: Database, key: str, value: str) -> AppSetting:
    now = datetime.utcnow()
    # Check if key already exists to reuse its _id; otherwise generate a new one
    existing = db[COLL_APP_SETTINGS].find_one({"key": key}, {"_id": 1})
    new_id = existing["_id"] if existing else next_id(db, COLL_APP_SETTINGS)
    doc = db[COLL_APP_SETTINGS].find_one_and_update(
        {"key": key},
        {
            "$set": {"value": value, "updated_at": now},
            "$setOnInsert": {"key": key, "_id": new_id},
        },
        upsert=True,
        return_document=True,
    )
    return AppSetting.from_doc(doc)


def get_settings(db: Database, keys: list[str]) -> dict[str, str]:
    docs = db[COLL_APP_SETTINGS].find({"key": {"$in": keys}})
    return {d["key"]: d["value"] for d in docs}


# ---------------------------------------------------------------------------
# Strategy CRUD helpers
# ---------------------------------------------------------------------------

def get_strategy_by_name(db: Database, name: str) -> Strategy | None:
    doc = db[COLL_STRATEGIES].find_one({"name": name})
    return Strategy.from_doc(doc) if doc else None


def get_active_strategies(db: Database) -> list[Strategy]:
    docs = db[COLL_STRATEGIES].find({"is_active": True})
    return [Strategy.from_doc(d) for d in docs]


def get_all_strategies(db: Database) -> list[Strategy]:
    docs = db[COLL_STRATEGIES].find()
    return [Strategy.from_doc(d) for d in docs]


def update_strategy_params(db: Database, strategy_name: str, params: dict) -> Strategy | None:
    now = datetime.utcnow()
    doc = db[COLL_STRATEGIES].find_one_and_update(
        {"name": strategy_name},
        {"$set": {"params_json": json.dumps(params), "updated_at": now}},
        return_document=True,
    )
    return Strategy.from_doc(doc) if doc else None


# ---------------------------------------------------------------------------
# BacktestResult CRUD (extended)
# ---------------------------------------------------------------------------

def get_backtest_result(db: Database, bt_id: int) -> BacktestResult | None:
    doc = db[COLL_BACKTEST_RESULTS].find_one({"_id": bt_id})
    return BacktestResult.from_doc(doc) if doc else None


def get_backtest_results_by_batch(db: Database, batch_id: str) -> list[BacktestResult]:
    docs = db[COLL_BACKTEST_RESULTS].find({"batch_id": batch_id}).sort("created_at", 1)
    return [BacktestResult.from_doc(d) for d in docs]


# ---------------------------------------------------------------------------
# BacktestCandidate CRUD
# ---------------------------------------------------------------------------

def create_backtest_candidate(db: Database, candidate: BacktestCandidate) -> BacktestCandidate:
    doc = candidate.to_dict()
    doc["_id"] = next_id(db, COLL_BACKTEST_CANDIDATES)
    db[COLL_BACKTEST_CANDIDATES].insert_one(doc)
    candidate.id = str(doc["_id"])
    return candidate


def get_backtest_candidates(
    db: Database,
    strategy_name: str,
    page: int = 1,
    limit: int = 50,
    qualified_only: bool = False,
) -> list[BacktestCandidate]:
    query: dict = {"strategy_name": strategy_name}
    if qualified_only:
        query["qualified"] = True
    docs = (
        db[COLL_BACKTEST_CANDIDATES]
        .find(query)
        .sort("evaluated_at", -1)
        .skip((page - 1) * limit)
        .limit(limit)
    )
    return [BacktestCandidate.from_doc(d) for d in docs]


def get_best_backtest_candidate(db: Database, strategy_name: str) -> BacktestCandidate | None:
    doc = db[COLL_BACKTEST_CANDIDATES].find_one(
        {"strategy_name": strategy_name, "qualified": True},
        sort=[("composite_score", -1)],
    )
    return BacktestCandidate.from_doc(doc) if doc else None


# ---------------------------------------------------------------------------
# BacktestBatch CRUD
# ---------------------------------------------------------------------------

def create_backtest_batch(
    db: Database,
    batch_id: str,
    strategy_names: list[str],
    shared_settings: dict | None = None,
) -> BacktestBatch:
    doc = {
        "_id": next_id(db, COLL_BACKTEST_BATCHES),
        "batch_id": batch_id,
        "strategy_names": strategy_names,
        "shared_settings_json": shared_settings or {},
        "status": "RUNNING",
        "cross_analysis_json": None,
        "created_at": datetime.utcnow(),
        "completed_at": None,
    }
    db[COLL_BACKTEST_BATCHES].insert_one(doc)
    return BacktestBatch.from_doc(doc)


def get_backtest_batch(db: Database, batch_id: str) -> BacktestBatch | None:
    doc = db[COLL_BACKTEST_BATCHES].find_one({"batch_id": batch_id})
    return BacktestBatch.from_doc(doc) if doc else None


def update_backtest_batch(
    db: Database,
    batch_id: str,
    status: str,
    cross_analysis_json: dict | None = None,
) -> BacktestBatch | None:
    update: dict = {"status": status}
    if cross_analysis_json is not None:
        update["cross_analysis_json"] = cross_analysis_json
    if status in ("COMPLETE", "PARTIAL_FAILURE"):
        update["completed_at"] = datetime.utcnow()
    doc = db[COLL_BACKTEST_BATCHES].find_one_and_update(
        {"batch_id": batch_id},
        {"$set": update},
        return_document=True,
    )
    return BacktestBatch.from_doc(doc) if doc else None


# Alias used in some services
update_backtest_batch_status = update_backtest_batch


def get_backtest_results_for_batch(db: Database, batch_id: str) -> list[BacktestResult]:
    docs = db[COLL_BACKTEST_RESULTS].find({"batch_id": batch_id}).sort("created_at", 1)
    return [BacktestResult.from_doc(d) for d in docs]


# ---------------------------------------------------------------------------
# StrategyPairAnalysis CRUD
# ---------------------------------------------------------------------------

def create_pair_analysis(db: Database, row: StrategyPairAnalysis) -> StrategyPairAnalysis:
    doc = row.to_dict()
    doc["_id"] = next_id(db, COLL_STRATEGY_PAIR_ANALYSES)
    db[COLL_STRATEGY_PAIR_ANALYSES].insert_one(doc)
    row.id = str(doc["_id"])
    return row


def get_pair_analyses_for_batch(
    db: Database,
    batch_id: str,
) -> list[StrategyPairAnalysis]:
    docs = (
        db[COLL_STRATEGY_PAIR_ANALYSES]
        .find({"batch_id": batch_id})
        .sort("synergy_score", -1)
    )
    return [StrategyPairAnalysis.from_doc(d) for d in docs]


# Alias
get_pair_analyses = get_pair_analyses_for_batch


# ---------------------------------------------------------------------------
# EnsembleDecision CRUD
# ---------------------------------------------------------------------------

def create_ensemble_decision(db: Database, fields: dict) -> EnsembleDecision:
    fields = dict(fields)
    fields["_id"] = next_id(db, COLL_ENSEMBLE_DECISIONS)
    db[COLL_ENSEMBLE_DECISIONS].insert_one(fields)
    return EnsembleDecision.from_doc(fields)


def update_ensemble_decision_trade_id(
    db: Database, decision_id: int, trade_id: int
) -> EnsembleDecision | None:
    doc = db[COLL_ENSEMBLE_DECISIONS].find_one_and_update(
        {"_id": decision_id},
        {"$set": {"trade_id": trade_id}},
        return_document=True,
    )
    return EnsembleDecision.from_doc(doc) if doc else None


def get_ensemble_decisions(
    db: Database,
    page: int = 1,
    limit: int = 50,
    symbol: str | None = None,
) -> list[EnsembleDecision]:
    query: dict = {}
    if symbol:
        query["symbol"] = symbol
    docs = (
        db[COLL_ENSEMBLE_DECISIONS]
        .find(query)
        .sort("timestamp", -1)
        .skip((page - 1) * limit)
        .limit(limit)
    )
    return [EnsembleDecision.from_doc(d) for d in docs]


def get_ensemble_decision(db: Database, decision_id: int) -> EnsembleDecision | None:
    doc = db[COLL_ENSEMBLE_DECISIONS].find_one({"_id": decision_id})
    return EnsembleDecision.from_doc(doc) if doc else None


def get_ensemble_decision_by_trade_id(
    db: Database, trade_id: int
) -> EnsembleDecision | None:
    """Most recent EnsembleDecision linked to a given trade (for score feedback)."""
    doc = db[COLL_ENSEMBLE_DECISIONS].find_one(
        {"trade_id": trade_id}, sort=[("timestamp", -1)]
    )
    return EnsembleDecision.from_doc(doc) if doc else None


# ---------------------------------------------------------------------------
# NewsVetoDecision CRUD
# ---------------------------------------------------------------------------

def create_news_veto_decision(db: Database, row: NewsVetoDecision) -> NewsVetoDecision:
    doc = row.to_dict()
    doc["_id"] = next_id(db, COLL_NEWS_VETO_DECISIONS)
    db[COLL_NEWS_VETO_DECISIONS].insert_one(doc)
    row.id = str(doc["_id"])
    return row


def update_news_veto_decision_trade_id(
    db: Database, decision_id: int, trade_id: int
) -> NewsVetoDecision | None:
    doc = db[COLL_NEWS_VETO_DECISIONS].find_one_and_update(
        {"_id": decision_id},
        {"$set": {"trade_id": trade_id}},
        return_document=True,
    )
    return NewsVetoDecision.from_doc(doc) if doc else None


def get_news_veto_decisions(
    db: Database,
    page: int = 1,
    limit: int = 50,
    symbol: str | None = None,
) -> list[NewsVetoDecision]:
    query: dict = {}
    if symbol:
        query["symbol"] = symbol
    docs = (
        db[COLL_NEWS_VETO_DECISIONS]
        .find(query)
        .sort("timestamp", -1)
        .skip((page - 1) * limit)
        .limit(limit)
    )
    return [NewsVetoDecision.from_doc(d) for d in docs]


def get_news_veto_decision(db: Database, decision_id: int) -> NewsVetoDecision | None:
    doc = db[COLL_NEWS_VETO_DECISIONS].find_one({"_id": decision_id})
    return NewsVetoDecision.from_doc(doc) if doc else None


# ---------------------------------------------------------------------------
# Live score feedback helpers
#
# At trade close we update each voting strategy's ``live_score`` — a decaying
# average of realised R-multiples (reward earned per unit of risk taken). This
# is a SEPARATE field from the backtest ``composite_score`` so the backtester
# can never clobber the live signal and vice-versa. The EnsembleVoter blends
# live_score in as a weight multiplier at vote time.
# ---------------------------------------------------------------------------

def _params_fingerprint(params_json: str | None) -> str:
    """
    Stable fingerprint of a strategy's param set. live_score is tied to this
    fingerprint so that when a NEW parameter set is promoted (by either service,
    via update_strategy_params → a different params_json), the accumulated
    live_score is treated as stale and resets to 0 — the old R-multiples were
    earned by parameters that are no longer live.

    Normalised (parse + sort keys) so cosmetic re-serialisation of identical
    params does not trigger a spurious reset.
    """
    raw = params_json or "{}"
    try:
        normalised = json.dumps(json.loads(raw), sort_keys=True)
    except (ValueError, TypeError):
        normalised = raw
    return hashlib.sha1(normalised.encode("utf-8")).hexdigest()


def get_strategy_live_score(db: Database, strategy_name: str) -> float:
    """
    Return the strategy's ``live_score`` — an EWMA of realised R-multiples from
    live trades, centred on 0.0 (positive = recently profitable). Kept separate
    from the backtest ``composite_score`` so the two control loops never corrupt
    each other.

    Returns 0.0 if the stored score was accumulated under a different parameter
    set than the one currently live (i.e. a new candidate has been promoted).
    """
    doc = db[COLL_STRATEGIES].find_one(
        {"name": strategy_name}, {"live_score": 1, "live_score_params_hash": 1, "params_json": 1}
    )
    if not doc:
        return 0.0
    current_hash = _params_fingerprint(doc.get("params_json"))
    if doc.get("live_score_params_hash") != current_hash:
        return 0.0  # stale — earned under params that are no longer live
    return float(doc.get("live_score") or 0.0)


def get_live_scores(db: Database) -> dict[str, float]:
    """
    All strategies' live_score, keyed by name (missing → 0.0). Scores stamped
    with a params hash that no longer matches the live params are reported as 0.0.
    """
    out: dict[str, float] = {}
    for doc in db[COLL_STRATEGIES].find(
        {}, {"name": 1, "live_score": 1, "live_score_params_hash": 1, "params_json": 1}
    ):
        current_hash = _params_fingerprint(doc.get("params_json"))
        if doc.get("live_score_params_hash") == current_hash:
            out[doc.get("name", "")] = float(doc.get("live_score") or 0.0)
        else:
            out[doc.get("name", "")] = 0.0
    return out


def update_strategy_live_score(db: Database, strategy_name: str, value: float) -> None:
    """
    Persist a strategy's updated live_score, stamped with the fingerprint of the
    params currently live. If a new param set was promoted since the last update,
    this naturally rebases the score onto the new params.
    """
    doc = db[COLL_STRATEGIES].find_one({"name": strategy_name}, {"params_json": 1})
    params_hash = _params_fingerprint(doc.get("params_json") if doc else None)
    db[COLL_STRATEGIES].update_one(
        {"name": strategy_name},
        {"$set": {
            "live_score": value,
            "live_score_params_hash": params_hash,
            "updated_at": datetime.utcnow(),
        }},
    )


# ---------------------------------------------------------------------------
# Seed default AppSettings
# ---------------------------------------------------------------------------

def seed_default_settings(db: Database) -> None:
    """
    Insert default AppSetting rows for all subsystems.
    Skips keys that already exist (never overwrites).
    Called once from startup.
    """
    from .strategy.registry import STRATEGY_REGISTRY

    # ── Risk management ───────────────────────────────────────────────────
    risk_defaults: dict[str, str] = {
        "account_balance": "10.0",
        "leverage": "2000",
        "risk_per_trade_pct": "1.0",
        "max_open_trades": "5",
        "max_daily_loss_pct": "5.0",
        "max_drawdown_pct": "20.0",
        "lot_size_mode": "FIXED",
        "trading_halt": "false",
        "symbol_exposure_limit": "1.0",
    }

    # ── News intelligence ─────────────────────────────────────────────────
    news_defaults: dict[str, str] = {
        "newsapi_key": "",
        "alphavantage_key": "",
        "finnhub_key": "",
        "groq_api_key": "",
        "twelve_data_key": "",
        "news_lookback_hours": "4",
        "news_block_threshold": "0.7",
        "news_caution_factor": "0.5",
        "retrospective_learning_interval_hours": "4",
        "global_context_interval_minutes": "30",
        "news_analysis_system_prompt": "",
        "global_market_context": "{}",
        "news_trade_learning_window_hours": "4",
    }

    # ── Backtest engine ────────────────────────────────────────────────────
    backtest_globals: dict[str, str] = {
        "backtest_adapt_every_n_trades": "20",
    }

    # ── News-veto (the only surviving piece of the old strategy picker) ────
    news_veto_defaults: dict[str, str] = {
        "news_veto_bias_threshold": "0.5",
        "news_veto_threshold": "0.85",
    }

    # ── Live score feedback (R-multiple live_score at trade close) ──────────
    # On every closed trade, each voting strategy's `live_score` (EWMA of
    # realised R-multiples, centred on 0) is updated. The EnsembleVoter scales
    # each backtest weight by (1 + weight_gain * live_score), floored at
    # weight_floor — so recently-profitable strategies are amplified and
    # recently-losing ones dampened, WITHOUT touching the backtest composite_score.
    score_feedback_defaults: dict[str, str] = {
        "score_feedback_enabled": "true",
        "score_feedback_alpha": "0.2",          # EWMA smoothing per closed trade
        "score_feedback_score_bound": "3.0",    # clamp on accumulated live_score
        "score_feedback_weight_gain": "0.25",   # how hard live_score tilts the vote weight
        "score_feedback_weight_floor": "0.1",   # min multiplier (never fully zero a strategy)
    }

    # ── Live trading loop ──────────────────────────────────────────────────
    live_trading_defaults: dict[str, str] = {
        "live_trading_interval_seconds": "60",
        "live_trading_symbols": "XAUUSD",
        # How often (seconds) to check for ghost trades closed externally by MT5
        "position_reconciliation_interval_seconds": "60",
    }

    # ── Position stacking / reversal ──────────────────────────────────────
    position_mgmt_defaults: dict[str, str] = {
        # Opposite-direction reversal thresholds
        "reversal_full_close_confidence": "0.80",
        "reversal_partial_close_confidence": "0.65",
        "reversal_partial_close_pct": "0.50",
        # Same-direction stacking — escalating confidence per additional position
        "duplicate_min_confidence": "0.75",
        "duplicate_min_price_distance_atr": "1.0",
        "duplicate_min_strategy_count": "2",
        "duplicate_confidence_escalation": "0.10",
    }

    per_strategy_defaults: dict[str, str] = {
        "qualify_threshold_win_rate": "55.0",
        "score_weight_win_rate": "0.6",
        "score_weight_roi": "0.4",
        "backtest_interval_seconds": "300",
        "backtest_timeframes": '["1h","4h","1d"]',
        "backtest_symbols": '["XAUUSD"]',
        "param_step_size": "0.05",
        "range_expansion_months": "6",
        "max_history_months": "36",
        # Exploration thresholds — used for coordinate-ascent momentum decisions only.
        # Lower than qualify thresholds to allow gradient signal even below promotion bar.
        "explore_threshold_win_rate": "35.0",
        "explore_threshold_profit_factor": "0.5",
        "explore_min_trades": "5",
    }

    def _maybe_insert(key: str, value: str) -> None:
        """Insert only if the key doesn't already exist (upsert with $setOnInsert)."""
        # Generate _id only for genuinely new documents
        existing = db[COLL_APP_SETTINGS].find_one({"key": key}, {"_id": 1})
        if existing:
            return  # already exists, skip
        new_id = next_id(db, COLL_APP_SETTINGS)
        db[COLL_APP_SETTINGS].update_one(
            {"key": key},
            {"$setOnInsert": {"_id": new_id, "key": key, "value": value, "updated_at": datetime.utcnow()}},
            upsert=True,
        )

    for key, value in risk_defaults.items():
        _maybe_insert(key, value)

    for key, value in news_defaults.items():
        _maybe_insert(key, value)

    for key, value in backtest_globals.items():
        _maybe_insert(key, value)

    for key, value in news_veto_defaults.items():
        _maybe_insert(key, value)

    for key, value in score_feedback_defaults.items():
        _maybe_insert(key, value)

    for key, value in live_trading_defaults.items():
        _maybe_insert(key, value)

    for key, value in position_mgmt_defaults.items():
        _maybe_insert(key, value)

    for strategy_name in STRATEGY_REGISTRY.keys():
        for suffix, value in per_strategy_defaults.items():
            _maybe_insert(f"{strategy_name}_{suffix}", value)

    # ── Alchemist-specific overrides ──────────────────────────────────────
    alchemist_overrides: dict[str, str] = {
        "Alchemist_qualify_threshold_win_rate": "45.0",
        "Alchemist_backtest_timeframes": '["1d"]',
        "Alchemist_backtest_symbols": '["XAUUSD"]',
        "Alchemist_param_step_size": "0.03",
    }
    for key, value in alchemist_overrides.items():
        _maybe_insert(key, value)