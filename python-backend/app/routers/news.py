from datetime import datetime, timedelta, timezone
import math

from fastapi import APIRouter, Depends, Query
from pymongo.database import Database

from .. import crud
from ..auth_deps import require_write_access
from ..db import get_db, COLL_NEWS_ITEMS, COLL_TRADES
from ..models import NewsItem, Trade

from ..services.news_intelligence import (
    fetch_and_store_news,
    get_global_context,
    get_news_bias,
    run_retrospective_learning,
    update_global_context,
)

router = APIRouter(prefix="/news", tags=["news"])


@router.get("/items")
def get_news_items(
    symbol: str | None = None,
    limit: int = 50,
    hours: int = 24,
    db: Database = Depends(get_db),
):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    docs = (
        db[COLL_NEWS_ITEMS]
        .find({"published_at": {"$gte": cutoff}})
        .sort("published_at", -1)
        .limit(limit)
    )
    items = [NewsItem.from_doc(d) for d in docs]
    return [
        {
            "id": i.id,
            "source": i.source,
            "headline": i.headline,
            "summary": i.summary,
            "url": i.url,
            "published_at": i.published_at,
            "ai_sentiment_label": i.ai_sentiment_label,
            "ai_sentiment_score": i.ai_sentiment_score,
            "ai_confidence": i.ai_confidence,
            "market_impact_predicted": i.market_impact_predicted,
            "market_impact_actual": i.market_impact_actual,
            "impact_learning_weight": i.impact_learning_weight,
        }
        for i in items
    ]


@router.get("/bias/{symbol}")
def get_bias(symbol: str, hours: int = 12, db: Database = Depends(get_db)):
    return get_news_bias(db, symbol, hours)


@router.get("/context")
def get_context(db: Database = Depends(get_db)):
    return get_global_context(db)


@router.post("/fetch")
def trigger_fetch(body: dict | None = None, db: Database = Depends(get_db), _w=Depends(require_write_access)):
    symbol = (body or {}).get("symbol")
    count = fetch_and_store_news(db, symbol)
    context = update_global_context(db)
    return {"stored": count, "context_updated": True, "context": context}


@router.post("/context/refresh")
def refresh_context(db: Database = Depends(get_db), _w=Depends(require_write_access)):
    return update_global_context(db)


@router.post("/learn")
def trigger_learning(db: Database = Depends(get_db), _w=Depends(require_write_access)):
    updated = run_retrospective_learning(db)
    return {"updated_items": updated}


@router.get("/learning-stats")
def learning_stats(db: Database = Depends(get_db)):
    docs = db[COLL_NEWS_ITEMS].find().sort("fetched_at", -1).limit(500)
    items = [NewsItem.from_doc(d) for d in docs]
    source_stats: dict[str, dict] = {}
    for item in items:
        src = item.source
        if src not in source_stats:
            source_stats[src] = {"count": 0, "weights": []}
        source_stats[src]["count"] += 1
        source_stats[src]["weights"].append(item.impact_learning_weight or 1.0)
    result = []
    for src, data in source_stats.items():
        weights = data["weights"]
        result.append({
            "source": src,
            "count": data["count"],
            "avg_learning_weight": round(sum(weights) / len(weights), 4) if weights else 1.0,
        })
    return sorted(result, key=lambda x: x["avg_learning_weight"], reverse=True)


def _pearson(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = math.sqrt(
        sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys)
    )
    return round(num / den, 4) if den > 0 else 0.0


@router.get("/trade-correlation")
def trade_correlation(
    hours_window: int = Query(4, ge=1, le=48),
    days: int = Query(30, ge=1, le=180),
    db: Database = Depends(get_db),
):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    docs = (
        db[COLL_NEWS_ITEMS]
        .find({"market_impact_actual": {"$ne": None}, "published_at": {"$gte": cutoff}})
        .sort("published_at", -1)
    )
    news_items = [NewsItem.from_doc(d) for d in docs]

    if not news_items:
        return {
            "correlation_coefficient": None,
            "accuracy_rate": None,
            "sample_size": 0,
            "message": "No news items with market_impact_actual found in the period.",
            "best_predictions": [],
            "worst_predictions": [],
        }

    predicted_vals: list[float] = []
    actual_vals: list[float] = []
    correct_direction = 0
    samples = []

    for item in news_items:
        predicted = item.market_impact_predicted
        actual = item.market_impact_actual
        if predicted is None:
            continue

        predicted_vals.append(float(predicted))
        actual_vals.append(float(actual))

        direction_correct = (predicted >= 0) == (actual >= 0)
        if direction_correct:
            correct_direction += 1

        window_end = item.published_at + timedelta(hours=hours_window)
        nearby_docs = db[COLL_TRADES].find({
            "opened_at": {"$gte": item.published_at, "$lte": window_end},
            "result": {"$in": ["WIN", "LOSS"]},
        })
        nearby_trades = [Trade.from_doc(d) for d in nearby_docs]

        avg_pnl = (
            round(sum(t.pnl or 0 for t in nearby_trades) / len(nearby_trades), 4)
            if nearby_trades else None
        )

        error = abs(float(actual) - float(predicted))
        samples.append({
            "news_id": item.id,
            "headline": item.headline[:80] if item.headline else "",
            "source": item.source,
            "published_at": item.published_at.isoformat() if item.published_at else None,
            "predicted_impact": float(predicted),
            "actual_impact": float(actual),
            "prediction_error": round(error, 4),
            "direction_correct": direction_correct,
            "nearby_trades": len(nearby_trades),
            "avg_trade_pnl": avg_pnl,
        })

    n = len(predicted_vals)
    correlation = _pearson(predicted_vals, actual_vals) if n >= 2 else None
    accuracy_rate = round(correct_direction / n * 100, 2) if n > 0 else None

    sorted_samples = sorted(samples, key=lambda s: s["prediction_error"])
    return {
        "correlation_coefficient": correlation,
        "accuracy_rate": accuracy_rate,
        "sample_size": n,
        "hours_window": hours_window,
        "period_days": days,
        "best_predictions": sorted_samples[:5],
        "worst_predictions": sorted_samples[-5:][::-1],
    }