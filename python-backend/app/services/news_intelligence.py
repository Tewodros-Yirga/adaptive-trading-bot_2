"""
News Intelligence Service
Uses free APIs: NewsAPI, Alpha Vantage, Finnhub, and RSS feeds.
Sentiment analysis uses Groq (free tier) with llama-3 as replacement for Claude API.
Falls back to simple keyword-based scoring if no AI key is available.

Model 2 additions:
  - learn_from_trade()         — real-time learning from opened/closed trades
  - update_source_credibility() — per-source accuracy aggregation
  - get_news_bias()            — now weights by source credibility score
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
                raw_syms = result.get("affected_symbols") or []
                affected = [str(s).upper() for s in raw_syms if isinstance(s, str)] if isinstance(raw_syms, list) else []
                return {
                    "ai_sentiment_label": result.get("sentiment_label", "NEUTRAL"),
                    "ai_sentiment_score": float(result.get("sentiment_score", 0.0)),
                    "ai_confidence": float(result.get("confidence", 0.5)),
                    "market_impact_predicted": float(result.get("market_impact_predicted", 0.0)),
                    "affected_symbols": affected,
                }
        except Exception:
            pass
    kw = _keyword_sentiment(f"{headline} {summary or ''}")
    return {
        "ai_sentiment_label": kw["label"],
        "ai_sentiment_score": kw["score"],
        "ai_confidence": kw["confidence"],
        "market_impact_predicted": kw["score"],
        "affected_symbols": [],
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
        # Tag with the fetch symbol PLUS any extra symbols the model flagged, so
        # multi-symbol news correlates with trades on those symbols too.
        symbols_mentioned = sorted({sym.upper(), *(sentiment.get("affected_symbols") or [])})

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
                "symbols_mentioned": json.dumps(symbols_mentioned),
                "raw_sentiment_score": item.get("raw_sentiment_score"),
                "ai_sentiment_score": sentiment["ai_sentiment_score"],
                "ai_sentiment_label": sentiment["ai_sentiment_label"],
                "ai_confidence": sentiment["ai_confidence"],
                "market_impact_predicted": sentiment["market_impact_predicted"],
                "market_impact_actual": None,
                "impact_learning_weight": 1.0,
                "trade_correlation_pending": False,
                "last_learned_from_trade_at": None,
            })
            stored += 1
        except Exception as e:
            import logging as _log
            _log.getLogger(__name__).warning("Failed to insert news item '%s': %s", headline[:60], e)

    return stored


# ── Source credibility helpers ─────────────────────────────────────────────────

def _load_source_credibility(db: Database) -> dict[str, float]:
    """
    Load the per-source credibility scores from AppSettings.
    Returns a dict mapping source name → credibility score (0.0–1.0).
    Defaults to an empty dict (callers should default missing keys to 1.0).
    """
    raw = crud.get_setting(db, "news_source_credibility")
    if raw:
        try:
            return json.loads(raw)
        except Exception:
            pass
    return {}


def get_news_bias(db: Database, symbol: str, hours: int = 12) -> dict:
    """
    Compute a weighted sentiment bias for the given symbol over the past N hours.

    Model 2 change: each news item's weight now incorporates the source credibility
    score loaded from the ``news_source_credibility`` AppSetting. High-credibility
    sources count proportionally more; missing sources default to 1.0.
    """
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

    # Load source credibility once for the whole batch
    credibility_map = _load_source_credibility(db)

    now = datetime.now(timezone.utc)
    total_weight = 0.0
    weighted_score = 0.0
    contributing_ids: list[int] = []
    for item in items:
        if item.get("ai_sentiment_score") is None:
            continue
        pub = item.get("published_at")
        if pub is None:
            continue
        pub_tz = pub.replace(tzinfo=timezone.utc) if pub.tzinfo is None else pub
        age_hours = (now - pub_tz).total_seconds() / 3600
        recency_decay = math.exp(-0.15 * age_hours)

        # Source credibility multiplier (default 1.0 for unknown sources)
        source_cred = float(credibility_map.get(item.get("source", ""), 1.0))

        w = (
            recency_decay
            * (item.get("impact_learning_weight") or 1.0)
            * (item.get("ai_confidence") or 0.5)
            * source_cred
        )
        weighted_score += w * item["ai_sentiment_score"]
        total_weight += w
        if item.get("_id") is not None:
            contributing_ids.append(item["_id"])

    if total_weight == 0:
        return {"bias": 0.0, "confidence": 0.0, "item_count": len(items), "item_ids": []}

    bias = weighted_score / total_weight
    confidence = min(1.0, total_weight / (len(items) * 0.5))
    # ``item_ids`` are the news items that actually contributed to this bias —
    # the decision path records them on the trade so close-time learning updates
    # exactly these items (see learn_from_trade).
    return {
        "bias": round(bias, 4),
        "confidence": round(confidence, 4),
        "item_count": len(items),
        "item_ids": contributing_ids,
    }


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

    # Keep per-source credibility scores fresh so get_news_bias weights are accurate.
    if updated > 0:
        try:
            update_source_credibility(db)
        except Exception as _cred_exc:
            import logging as _log
            _log.getLogger(__name__).warning(
                "run_retrospective_learning: source credibility update failed: %s", _cred_exc
            )

    return updated


# ── Real-time trade learning (Model 2) ────────────────────────────────────────

def learn_from_trade(db: Database, trade: dict) -> int:
    """
    Update news items in real time based on a trade event.

    Call this whenever a trade is opened OR closed.  The function finds all
    news items published within ``news_trade_learning_window_hours`` before
    ``trade["opened_at"]`` whose ``symbols_mentioned`` contains the trade
    symbol.

    For **open** trades (no ``closed_at`` / ``pnl`` is None):
      - Sets ``trade_correlation_pending = True`` on matching items so we
        know to revisit them once the trade closes.

    For **closed** trades (``pnl`` is not None):
      - Computes ``impact_actual`` from the realised PnL scaled by account
        balance (clipped to [-1, 1]).
      - Checks direction alignment (BUY+BULLISH or SELL+BEARISH → +0.1 bonus).
      - Updates ``market_impact_actual``, ``impact_learning_weight`` (EMA),
        and ``last_learned_from_trade_at``.

    Args:
        db:    MongoDB database handle.
        trade: Dict with keys: symbol, direction, opened_at, closed_at,
               pnl, result.

    Returns:
        Count of news items updated.
    """
    import logging as _log
    logger = _log.getLogger(__name__)

    symbol: str = trade.get("symbol", "")
    direction: str = (trade.get("direction") or "").upper()
    opened_at: datetime | None = trade.get("opened_at")
    pnl: float | None = trade.get("pnl")
    is_closed: bool = trade.get("closed_at") is not None and pnl is not None

    if not opened_at:
        return 0

    # Ensure timezone-aware
    if isinstance(opened_at, datetime) and opened_at.tzinfo is None:
        opened_at = opened_at.replace(tzinfo=timezone.utc)

    # Preferred path: the orchestrator recorded exactly which news items drove the
    # bias for this trade at decision time. Learn from those directly — no fuzzy
    # time/symbol reconstruction needed. This is what makes learning fire for every
    # news-driven trade regardless of when it eventually closed.
    recorded_ids: list = trade.get("contributing_news_ids") or []
    matching_docs: list = []
    if recorded_ids:
        matching_docs = list(db[COLL_NEWS].find({"_id": {"$in": recorded_ids}}))

    # Fallback (legacy trades with no recorded IDs): correlate by a window centered
    # on the open time and the symbol tag. The window is widened to look a little
    # past the open as well, since a news event can post moments after a trade fires.
    if not matching_docs:
        window_hours = int(crud.get_setting(db, "news_trade_learning_window_hours") or 4)
        window_start = opened_at - timedelta(hours=window_hours)
        window_end = opened_at + timedelta(hours=window_hours)
        docs = list(
            db[COLL_NEWS].find(
                {"published_at": {"$gte": window_start, "$lte": window_end}}
            )
        )
        for doc in docs:
            try:
                sym_list: list[str] = json.loads(doc.get("symbols_mentioned") or "[]")
            except Exception:
                sym_list = []
            if not sym_list or symbol in sym_list:
                matching_docs.append(doc)

    if not matching_docs:
        logger.info(
            "learn_from_trade: no correlated news for %s opened_at=%s "
            "(recorded_ids=%d, closed=%s)",
            symbol, opened_at, len(recorded_ids), is_closed,
        )
        return 0

    updated = 0

    if not is_closed:
        # Trade just opened — mark items as pending correlation
        for doc in matching_docs:
            db[COLL_NEWS].update_one(
                {"_id": doc["_id"]},
                {"$set": {"trade_correlation_pending": True}},
            )
            updated += 1
        return updated

    # Trade closed — compute actual impact and update learning weights
    # Account balance from the MT5 account (live), not the stored setting.
    from .risk_manager import get_effective_balance
    account_balance = get_effective_balance(db) or 10.0
    impact_actual = max(-1.0, min(1.0, (pnl or 0.0) * 10.0 / account_balance))

    now_utc = datetime.now(timezone.utc)

    for doc in matching_docs:
        predicted = doc.get("market_impact_predicted")
        ai_label: str = (doc.get("ai_sentiment_label") or "NEUTRAL").upper()

        # Direction alignment bonus
        aligned = (
            (direction == "BUY" and ai_label == "BULLISH") or
            (direction == "SELL" and ai_label == "BEARISH")
        )
        alignment_bonus = 0.1 if aligned else 0.0

        accuracy: float
        if predicted is not None:
            accuracy = min(1.0, (1.0 - abs(float(predicted) - impact_actual) / 2.0) + alignment_bonus)
        else:
            # No previous prediction — use alignment bonus only
            accuracy = 0.5 + alignment_bonus

        old_weight = float(doc.get("impact_learning_weight") or 1.0)
        # Confidence/evidence-weighted online update (item 24): stronger evidence
        # — a larger realised move AND higher AI confidence — moves the learning
        # weight more; a near-flat trade barely nudges it. A small floor keeps
        # learning from fully stalling. Reduces to the old fixed 0.2 EMA when
        # evidence is moderate and base_alpha is left at its default.
        ai_conf = float(doc.get("ai_confidence") or 0.5)
        evidence = min(1.0, abs(impact_actual) / 0.3) * ai_conf      # 0..1
        base_alpha = float(crud.get_setting(db, "news_learning_base_alpha") or 0.2)
        alpha = base_alpha * (0.25 + 0.75 * evidence)
        new_weight = round((1.0 - alpha) * old_weight + alpha * accuracy, 4)

        update_fields: dict = {
            "market_impact_actual": impact_actual,
            "impact_learning_weight": new_weight,
            "trade_correlation_pending": False,
            "last_learned_from_trade_at": now_utc,
        }

        try:
            db[COLL_NEWS].update_one({"_id": doc["_id"]}, {"$set": update_fields})
            updated += 1
        except Exception as exc:
            logger.warning("learn_from_trade update failed for doc %s: %s", doc.get("_id"), exc)

    return updated


def update_source_credibility(db: Database, symbol: str | None = None) -> dict:
    """
    Aggregate per-source accuracy from news items that have both predicted
    and actual market impact values.

    The credibility score for each source is the average accuracy across all
    its evaluated items, where accuracy = 1 - |predicted - actual| / 2.

    The result dict (source → credibility_score) is persisted to the
    ``news_source_credibility`` AppSetting key and also returned.

    Args:
        db:     MongoDB database handle.
        symbol: Optional — if provided, only items mentioning this symbol
                are included (for symbol-specific credibility).  When None,
                all evaluated items are used.

    Returns:
        Dict mapping source name → credibility score (0.0–1.0).
    """
    query: dict = {
        "market_impact_predicted": {"$ne": None},
        "market_impact_actual": {"$ne": None},
    }
    docs = list(db[COLL_NEWS].find(query))

    # Optional symbol filter
    if symbol:
        filtered = []
        for doc in docs:
            try:
                sym_list: list[str] = json.loads(doc.get("symbols_mentioned") or "[]")
            except Exception:
                sym_list = []
            if not sym_list or symbol in sym_list:
                filtered.append(doc)
        docs = filtered

    # Group by source with time-decay weighting (item 24): recent evidence
    # counts more, so a source that was accurate long ago but has drifted loses
    # credibility as fresh outcomes accumulate.
    now = datetime.now(timezone.utc)
    half_life_days = float(crud.get_setting(db, "news_credibility_halflife_days") or 14.0)
    decay_lambda = math.log(2) / max(half_life_days, 0.1)

    source_buckets: dict[str, list[tuple[float, float]]] = {}  # src -> [(accuracy, weight)]
    for doc in docs:
        src = doc.get("source") or "unknown"
        predicted = doc.get("market_impact_predicted")
        actual = doc.get("market_impact_actual")
        if predicted is None or actual is None:
            continue
        accuracy = 1.0 - abs(float(predicted) - float(actual)) / 2.0

        ref = doc.get("last_learned_from_trade_at") or doc.get("published_at")
        if isinstance(ref, datetime):
            ref_tz = ref.replace(tzinfo=timezone.utc) if ref.tzinfo is None else ref
            age_days = max(0.0, (now - ref_tz).total_seconds() / 86400.0)
        else:
            age_days = 0.0
        w = math.exp(-decay_lambda * age_days)
        source_buckets.setdefault(src, []).append((accuracy, w))

    credibility: dict[str, float] = {}
    for src, pairs in source_buckets.items():
        tw = sum(w for _, w in pairs) or 1.0
        wavg = sum(a * w for a, w in pairs) / tw
        # Small-sample shrinkage toward the neutral 1.0 default (same value used
        # for unknown sources in get_news_bias) so thin evidence can't swing a
        # source's multiplier hard in either direction.
        n = len(pairs)
        shrink = n / (n + 3.0)
        credibility[src] = round(shrink * wavg + (1.0 - shrink) * 1.0, 4)

    crud.set_setting(db, "news_source_credibility", json.dumps(credibility))
    return credibility


# ── Global context helpers ─────────────────────────────────────────────────────

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