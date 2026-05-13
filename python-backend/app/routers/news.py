from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from .. import crud
from ..db import get_db
from ..models import NewsItem
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
    db: Session = Depends(get_db),
):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    q = select(NewsItem).where(NewsItem.published_at >= cutoff).order_by(desc(NewsItem.published_at)).limit(limit)
    items = list(db.scalars(q).all())
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
def get_bias(symbol: str, hours: int = 12, db: Session = Depends(get_db)):
    return get_news_bias(db, symbol, hours)


@router.get("/context")
def get_context(db: Session = Depends(get_db)):
    return get_global_context(db)


@router.post("/fetch")
def trigger_fetch(symbol: str | None = None, db: Session = Depends(get_db)):
    count = fetch_and_store_news(db, symbol)
    context = update_global_context(db)
    return {"stored": count, "context_updated": True, "context": context}


@router.post("/context/refresh")
def refresh_context(db: Session = Depends(get_db)):
    return update_global_context(db)


@router.post("/learn")
def trigger_learning(db: Session = Depends(get_db)):
    updated = run_retrospective_learning(db)
    return {"updated_items": updated}


@router.get("/learning-stats")
def learning_stats(db: Session = Depends(get_db)):
    items = list(db.scalars(select(NewsItem).order_by(desc(NewsItem.fetched_at)).limit(500)).all())
    source_stats: dict[str, dict] = {}
    for item in items:
        src = item.source
        if src not in source_stats:
            source_stats[src] = {"count": 0, "avg_weight": 0.0, "weights": []}
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
