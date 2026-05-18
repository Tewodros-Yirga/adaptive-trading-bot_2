"""
News Intelligence Service
Uses free APIs: NewsAPI, Alpha Vantage, Finnhub, and RSS feeds.
Sentiment analysis uses Groq (free tier) with llama-3 as replacement for Claude API.
Falls back to simple keyword-based scoring if no AI key is available.
"""
import json
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from pymongo.database import Database
from tenacity import retry, stop_after_attempt, wait_exponential

from .. import crud

COLL_NEWS = "news_items"

# ── Positive / negative word lists for fallback sentiment ─────────────────────
POSITIVE_WORDS = {
    "rise", "rises", "rose", "gain", "gains", "rally", "rallies", "surges", "surge",
    "bullish", "bull", "strong", "strength", "buy", "upside", "positive", "growth",
    "growth", "recovery", "recovers", "boost", "boosts", "jump", "jumps", "climbs",
    "optimism", "optimistic", "green", "profit", "profits", "beat", "beats",
}
NEGATIVE_WORDS = {
    "fall", "falls", "fell", "drop", "drops", "decline", "declines", "crash", "crashes",
    "bearish", "bear", "weak", "weakness", "sell", "downside", "negative", "loss",
    "losses", "miss", "misses", "concern", "concerns", "risk", "risks", "warning",
    "recession", "inflation", "fear", "fears", "slump", "slumps", "plunge", "plunges",
    "red", "deficit", "crisis",
}


def _keyword_sentiment(text: str) -> dict:
    words = re.findall(r"\b\w+\b", text.lower())
    pos = sum(1 for w in words if w in POSITIVE_WORDS)
    neg = sum(1 for w in words if w in NEGATIVE_WORDS)
    total = pos + neg
    if total == 0:
        return {"label": "NEUTRAL", "score": 0.0, "confidence": 0.3}
    score = (pos - neg) / total
    label = "BULLISH" if score > 0.1 else "BEARISH" if score < -0.1 else "NEUTRAL"
    confidence = min(0.7, total * 0.05 + 0.2)
    return {"label": label, "score": round(score, 3), "confidence": round(confidence, 3)}


@retry(wait=wait_exponential(multiplier=1, min=2, max=30), stop=stop_after_attempt(3), reraise=False)
def _groq_sentiment(headline: str, summary: str, groq_key: str) -> dict | None:
    """Use Groq API (free tier) with llama-3 for sentiment analysis."""
    text = f"{headline}\n\n{summary or ''}"
    payload = {
        "model": "llama3-8b-8192",
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a financial news sentiment analyzer. "
                    "Respond ONLY with valid JSON (no markdown, no explanation) with keys: "
                    "sentiment_label (BULLISH/BEARISH/NEUTRAL), "
                    "sentiment_score (-1.0 to 1.0), "
                    "confidence (0.0 to 1.0), "
                    "market_impact_predicted (-1.0 to 1.0), "
                    "affected_symbols (list of strings), "
                    "reasoning (max 20 words)."
                ),
            },
            {"role": "user", "content": f"Analyze this financial news:\n{text}"},
        ],
        "max_tokens": 256,
        "temperature": 0.1,
    }
    resp = httpx.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=15,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"].strip()
    # strip markdown fences if present
    content = re.sub(r"^```json\s*|```$", "", content, flags=re.MULTILINE).strip()
    return json.loads(content)


def analyze_sentiment(headline: str, summary: str, groq_key: str | None) -> dict:
    if groq_key:
        try:
            result = _groq_sentiment(headline, summary or "", groq_key)
            if result:
                return {
                    "ai_sentiment_label": result.get("sentiment_label", "NEUTRAL"),
                    "ai_sentiment_score": float(result.get("sentiment_score", 0.0)),
                    "ai_confidence": float(result.get("confidence", 0.5)),
                    "market_impact_predicted": float(result.get("market_impact_predicted", 0.0)),
                }
        except Exception:
            pass
    kw = _keyword_sentiment(f"{headline} {summary or ''}")
    return {
        "ai_sentiment_label": kw["label"],
        "ai_sentiment_score": kw["score"],
        "ai_confidence": kw["confidence"],
        "market_impact_predicted": kw["score"],
    }


# ── Data Fetchers ──────────────────────────────────────────────────────────────

def _fetch_newsapi(symbol: str, api_key: str) -> list[dict]:
    keywords = {
        "XAUUSD": "gold OR XAU",
        "XAGUSD": "silver OR XAG",
        "EURUSD": "EUR/USD OR euro dollar",
        "GBPUSD": "GBP/USD OR pound dollar",
        "USDJPY": "USD/JPY OR dollar yen",
    }.get(symbol.upper(), symbol)
    try:
        resp = httpx.get(
            "https://newsapi.org/v2/everything",
            params={"q": keywords, "pageSize": 20, "sortBy": "publishedAt", "apiKey": api_key},
            timeout=10,
        )
        resp.raise_for_status()
        articles = resp.json().get("articles", [])
        return [
            {
                "source": f"newsapi:{a.get('source', {}).get('name', 'unknown')}",
                "headline": a.get("title", ""),
                "summary": a.get("description", ""),
                "url": a.get("url", ""),
                "published_at": a.get("publishedAt"),
            }
            for a in articles
            if a.get("title")
        ]
    except Exception:
        return []


def _fetch_alphavantage(symbol: str, api_key: str) -> list[dict]:
    ticker_map = {"XAUUSD": "XAUUSD", "EURUSD": "EURUSD", "GBPUSD": "GBPUSD"}
    ticker = ticker_map.get(symbol.upper(), symbol)
    try:
        resp = httpx.get(
            "https://www.alphavantage.co/query",
            params={"function": "NEWS_SENTIMENT", "tickers": ticker, "apikey": api_key, "limit": 20},
            timeout=10,
        )
        resp.raise_for_status()
        feed = resp.json().get("feed", [])
        results = []
        for item in feed:
            score = None
            for ts in item.get("ticker_sentiment", []):
                if ts.get("ticker", "").upper() == ticker.upper():
                    try:
                        score = float(ts.get("ticker_sentiment_score", 0))
                    except Exception:
                        pass
            results.append({
                "source": "alphavantage",
                "headline": item.get("title", ""),
                "summary": item.get("summary", ""),
                "url": item.get("url", ""),
                "published_at": item.get("time_published"),
                "raw_sentiment_score": score,
            })
        return results
    except Exception:
        return []


def _fetch_finnhub(api_key: str) -> list[dict]:
    try:
        resp = httpx.get(
            "https://finnhub.io/api/v1/news",
            params={"category": "forex", "token": api_key},
            timeout=10,
        )
        resp.raise_for_status()
        items = resp.json()
        return [
            {
                "source": "finnhub",
                "headline": item.get("headline", ""),
                "summary": item.get("summary", ""),
                "url": item.get("url", ""),
                "published_at": datetime.fromtimestamp(item["datetime"], tz=timezone.utc).isoformat()
                if item.get("datetime") else None,
            }
            for item in items
            if item.get("headline")
        ]
    except Exception:
        return []


def _fetch_rss(feed_url: str, source_name: str) -> list[dict]:
    try:
        import feedparser
        feed = feedparser.parse(feed_url)
        results = []
        for entry in feed.entries[:15]:
            published = None
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                import time
                published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc).isoformat()
            results.append({
                "source": f"rss:{source_name}",
                "headline": getattr(entry, "title", ""),
                "summary": getattr(entry, "summary", ""),
                "url": getattr(entry, "link", ""),
                "published_at": published,
            })
        return results
    except Exception:
        return []


RSS_FEEDS = [
    ("https://feeds.reuters.com/reuters/businessNews", "reuters"),
    ("https://www.forexlive.com/feed/news", "forexlive"),
    ("https://www.fxstreet.com/rss", "fxstreet"),
]


def _parse_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    # Try %z-aware format first (handles ISO 8601 with offset)
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y%m%dT%H%M%S%z"):
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.astimezone(timezone.utc).replace(tzinfo=timezone.utc)
        except Exception:
            pass
    # Try naive formats — assume UTC
    for fmt in ("%Y%m%dT%H%M%S", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(raw[:19].rstrip("Z"), fmt)
            return dt.replace(tzinfo=timezone.utc)
        except Exception:
            pass
    try:
        from dateutil import parser as dp
        dt = dp.parse(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def fetch_and_store_news(db: Database, symbol: str | None = None) -> int:
    newsapi_key = crud.get_setting(db, "newsapi_key")
    alphavantage_key = crud.get_setting(db, "alphavantage_key")
    finnhub_key = crud.get_setting(db, "finnhub_key")
    groq_key = crud.get_setting(db, "groq_api_key")
    sym = symbol or "XAUUSD"

    raw_items: list[dict] = []
    if newsapi_key:
        raw_items.extend(_fetch_newsapi(sym, newsapi_key))
    if alphavantage_key:
        raw_items.extend(_fetch_alphavantage(sym, alphavantage_key))
    if finnhub_key:
        raw_items.extend(_fetch_finnhub(finnhub_key))
    for url, name in RSS_FEEDS:
        raw_items.extend(_fetch_rss(url, name))

    stored = 0
    seen_headlines: set[str] = set()
    for item in raw_items:
        headline = (item.get("headline") or "").strip()
        if not headline or headline in seen_headlines:
            continue
        seen_headlines.add(headline)

        # Skip if headline already exists in the database (cross-fetch deduplication)
        if db[COLL_NEWS].find_one({"headline": headline}, {"_id": 1}):
            continue

        sentiment = analyze_sentiment(headline, item.get("summary", ""), groq_key)
        pub_dt = _parse_dt(item.get("published_at"))

        try:
            from ..db import next_id
            db[COLL_NEWS].insert_one({
                "_id": next_id(db, COLL_NEWS),
                "source": item.get("source", "unknown"),
                "headline": headline,
                "summary": item.get("summary", ""),
                "url": item.get("url", ""),
                "published_at": pub_dt,
                "fetched_at": datetime.now(timezone.utc),
                "symbols_mentioned": json.dumps([sym]),
                "raw_sentiment_score": item.get("raw_sentiment_score"),
                "ai_sentiment_score": sentiment["ai_sentiment_score"],
                "ai_sentiment_label": sentiment["ai_sentiment_label"],
                "ai_confidence": sentiment["ai_confidence"],
                "market_impact_predicted": sentiment["market_impact_predicted"],
                "market_impact_actual": None,
                "impact_learning_weight": 1.0,
            })
            stored += 1
        except Exception as e:
            import logging as _log
            _log.getLogger(__name__).warning("Failed to insert news item '%s': %s", headline[:60], e)

    return stored


def get_news_bias(db: Database, symbol: str, hours: int = 12) -> dict:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    docs = list(
        db[COLL_NEWS]
        .find({"published_at": {"$gte": cutoff}})
        .sort("published_at", -1)
        .limit(50)
    )
    items = docs
    if not items:
        return {"bias": 0.0, "confidence": 0.0, "item_count": 0}

    now = datetime.now(timezone.utc)
    total_weight = 0.0
    weighted_score = 0.0
    for item in items:
        if item.get("ai_sentiment_score") is None:
            continue
        pub = item.get("published_at")
        if pub is None:
            continue
        pub_tz = pub.replace(tzinfo=timezone.utc) if pub.tzinfo is None else pub
        age_hours = (now - pub_tz).total_seconds() / 3600
        recency_decay = math.exp(-0.15 * age_hours)
        w = recency_decay * (item.get("impact_learning_weight") or 1.0) * (item.get("ai_confidence") or 0.5)
        weighted_score += w * item["ai_sentiment_score"]
        total_weight += w

    if total_weight == 0:
        return {"bias": 0.0, "confidence": 0.0, "item_count": len(items)}

    bias = weighted_score / total_weight
    confidence = min(1.0, total_weight / (len(items) * 0.5))
    return {"bias": round(bias, 4), "confidence": round(confidence, 4), "item_count": len(items)}


def run_retrospective_learning(db: Database) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=2)
    docs = list(
        db[COLL_NEWS]
        .find({"market_impact_actual": None, "published_at": {"$lt": cutoff}})
        .limit(50)
    )
    updated = 0
    for item in docs:
        pub = item.get("published_at")
        if not pub:
            continue
        window_start = pub.replace(tzinfo=timezone.utc) if pub.tzinfo is None else pub
        window_end = window_start + timedelta(hours=4)
        sym_list = json.loads(item.get("symbols_mentioned") or "[]")
        relevant_trades = crud.get_closed_trades(db, 10000)
        trade_pnls = [
            t.pnl or 0
            for t in relevant_trades
            if t.opened_at
            and window_start <= t.opened_at.replace(tzinfo=timezone.utc) <= window_end
            and (not sym_list or (t.symbol in sym_list))
        ]
        if not trade_pnls:
            continue
        avg_pnl = sum(trade_pnls) / len(trade_pnls)
        impact_actual = max(-1.0, min(1.0, avg_pnl * 10))
        update_fields: dict = {"market_impact_actual": impact_actual}
        predicted = item.get("market_impact_predicted")
        if predicted is not None:
            accuracy = 1 - abs(predicted - impact_actual) / 2
            old_w = item.get("impact_learning_weight") or 1.0
            update_fields["impact_learning_weight"] = round(0.8 * old_w + 0.2 * accuracy, 4)
        db[COLL_NEWS].update_one({"_id": item["_id"]}, {"$set": update_fields})
        updated += 1
    return updated


def get_global_context(db: Database) -> dict:
    raw = crud.get_setting(db, "global_market_context")
    if raw:
        try:
            return json.loads(raw)
        except Exception:
            pass
    return {
        "sentiment": "NEUTRAL",
        "risk_appetite": 0.0,
        "key_themes": [],
        "summary": "No global context available yet. Trigger a news fetch to populate.",
        "updated_at": None,
    }


def update_global_context(db: Database) -> dict:
    items = list(
        db[COLL_NEWS]
        .find({"published_at": {"$gte": datetime.now(timezone.utc) - timedelta(hours=6)}})
        .sort("published_at", -1)
        .limit(20)
    )
    if not items:
        return get_global_context(db)

    scores = [i.get("ai_sentiment_score") for i in items if i.get("ai_sentiment_score") is not None]
    avg_score = sum(scores) / len(scores) if scores else 0.0
    labels = [i.get("ai_sentiment_label") for i in items if i.get("ai_sentiment_label")]
    bullish_count = labels.count("BULLISH")
    bearish_count = labels.count("BEARISH")
    dominant = "BULLISH" if bullish_count > bearish_count else "BEARISH" if bearish_count > bullish_count else "NEUTRAL"

    groq_key = crud.get_setting(db, "groq_api_key")
    summary = f"Market shows {dominant.lower()} sentiment based on {len(items)} recent articles."
    key_themes: list[str] = []

    if groq_key:
        try:
            headlines = "\n".join(f"- {i.get('headline', '')}" for i in items[:15])
            payload = {
                "model": "llama3-8b-8192",
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You analyze financial market news. Respond ONLY with JSON: "
                            "{sentiment: BULLISH/BEARISH/NEUTRAL, risk_appetite: -1.0 to 1.0, "
                            "key_themes: [list of 3-5 short strings], summary: string max 50 words}"
                        ),
                    },
                    {"role": "user", "content": f"Summarize market sentiment from these headlines:\n{headlines}"},
                ],
                "max_tokens": 300,
                "temperature": 0.2,
            }
            resp = httpx.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"},
                json=payload,
                timeout=15,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"].strip()
            content = re.sub(r"^```json\s*|```$", "", content, flags=re.MULTILINE).strip()
            parsed = json.loads(content)
            dominant = parsed.get("sentiment", dominant)
            avg_score = float(parsed.get("risk_appetite", avg_score))
            key_themes = parsed.get("key_themes", [])
            summary = parsed.get("summary", summary)
        except Exception:
            pass

    context = {
        "sentiment": dominant,
        "risk_appetite": round(avg_score, 3),
        "key_themes": key_themes,
        "summary": summary,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "article_count": len(items),
    }
    crud.set_setting(db, "global_market_context", json.dumps(context))
    return context