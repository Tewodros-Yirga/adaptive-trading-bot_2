"""
MongoDB Atlas connection module — replaces SQLAlchemy db.py.
Uses pymongo[srv]==3.11 with connection pooling.
"""
import logging
import os

from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.collection import ReturnDocument
from pymongo.database import Database
from pymongo.errors import ConnectionFailure

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Collection name constants (mirror old table names exactly)
# ---------------------------------------------------------------------------
COLL_TRADES = "trades"
COLL_PARAMETER_VERSIONS = "parameter_versions"
COLL_ADAPTATION_LOGS = "adaptation_logs"
COLL_APP_SETTINGS = "app_settings"
COLL_STRATEGIES = "strategies"
COLL_SHADOW_SIGNALS = "shadow_signals"
COLL_BACKTEST_RESULTS = "backtest_results"
COLL_OHLCV_CACHE = "ohlcv_cache"
COLL_NEWS_ITEMS = "news_items"
COLL_USERS = "users"
COLL_BACKTEST_CANDIDATES = "backtest_candidates"
COLL_BACKTEST_BATCHES = "backtest_batches"
COLL_STRATEGY_PAIR_ANALYSES = "strategy_pair_analyses"
COLL_ENSEMBLE_DECISIONS = "ensemble_decisions"
COLL_STRATEGY_PICKER_DECISIONS = "strategy_picker_decisions"
COLL_PICKER_WEIGHT_HISTORY = "picker_weight_history"
COLL_COUNTERS = "counters"

_client: MongoClient | None = None
_db: Database | None = None


def _get_uri() -> str:
    uri = os.environ.get("MONGODB_URI", "")
    if not uri:
        raise RuntimeError("MONGODB_URI environment variable is not set")
    return uri


def get_client() -> MongoClient:
    global _client
    if _client is None:
        uri = _get_uri()
        _client = MongoClient(
            uri,
            maxPoolSize=50,
            serverSelectionTimeoutMS=5000,
        )
        # Verify connectivity
        try:
            _client.admin.command("ping")
            logger.info("MongoDB Atlas connection established.")
        except ConnectionFailure as exc:
            logger.error("MongoDB Atlas ping failed: %s", exc)
            raise
    return _client


def _parse_db_name(uri: str) -> str:
    """
    Extract the database name from a MongoDB URI.
    Falls back to 'trading_bot' if the URI contains no DB path segment.

    Examples:
      mongodb+srv://user:pass@cluster.mongodb.net/mydb  -> 'mydb'
      mongodb://localhost:27017/trading_bot             -> 'trading_bot'
      mongodb+srv://user:pass@cluster.mongodb.net/      -> 'trading_bot'
    """
    try:
        # Strip query string, then get the path component after the host
        path = uri.split("?")[0]
        # Split on "/" — for SRV URIs: ["mongodb+srv:", "", "host", "dbname"]
        parts = path.split("/")
        if len(parts) >= 4 and parts[3].strip():
            return parts[3].strip()
    except Exception:
        pass
    return "trading_bot"


def get_database() -> Database:
    global _db
    if _db is None:
        client = get_client()
        uri = _get_uri()
        db_name = _parse_db_name(uri)
        _db = client[db_name]
        logger.info("Using MongoDB database: %s", db_name)
    return _db


def get_db() -> Database:
    """
    FastAPI dependency — returns the MongoDB database object.
    Unlike SQLAlchemy's Session, MongoDB connections are managed by the
    driver pool; no explicit close is required per request.
    """
    return get_database()


def create_indexes(db: Database) -> None:
    """Create all collection indexes. Idempotent — safe to call on every boot."""

    # trades
    db[COLL_TRADES].create_index([("opened_at", DESCENDING)], background=True)
    db[COLL_TRADES].create_index([("strategy_name", ASCENDING)], background=True)
    db[COLL_TRADES].create_index([("result", ASCENDING)], background=True)

    # parameter_versions
    db[COLL_PARAMETER_VERSIONS].create_index([("version", DESCENDING)], background=True)
    db[COLL_PARAMETER_VERSIONS].create_index([("strategy_name", ASCENDING)], background=True)

    # backtest_candidates
    db[COLL_BACKTEST_CANDIDATES].create_index(
        [("strategy_name", ASCENDING), ("composite_score", DESCENDING)], background=True
    )
    db[COLL_BACKTEST_CANDIDATES].create_index(
        [("strategy_name", ASCENDING), ("qualified", ASCENDING)], background=True
    )

    # strategy_picker_decisions
    db[COLL_STRATEGY_PICKER_DECISIONS].create_index([("symbol", ASCENDING)], background=True)
    db[COLL_STRATEGY_PICKER_DECISIONS].create_index([("timestamp", DESCENDING)], background=True)

    # picker_weight_history
    db[COLL_PICKER_WEIGHT_HISTORY].create_index([("trade_id", ASCENDING)], background=True)

    # backtest_results
    db[COLL_BACKTEST_RESULTS].create_index([("batch_id", ASCENDING)], background=True)

    # backtest_batches — unique on batch_id
    db[COLL_BACKTEST_BATCHES].create_index(
        [("batch_id", ASCENDING)], unique=True, background=True
    )

    # strategy_pair_analyses
    db[COLL_STRATEGY_PAIR_ANALYSES].create_index([("batch_id", ASCENDING)], background=True)

    # app_settings — unique on key
    db[COLL_APP_SETTINGS].create_index(
        [("key", ASCENDING)], unique=True, background=True
    )

    # strategies — unique on name
    db[COLL_STRATEGIES].create_index(
        [("name", ASCENDING)], unique=True, background=True
    )

    # users — unique on username
    db[COLL_USERS].create_index(
        [("username", ASCENDING)], unique=True, background=True
    )

    # news_items
    db[COLL_NEWS_ITEMS].create_index([("published_at", DESCENDING)], background=True)
    db[COLL_NEWS_ITEMS].create_index([("fetched_at", DESCENDING)], background=True)
    db[COLL_NEWS_ITEMS].create_index([("headline", ASCENDING)], background=True)  # fast dedup lookups

    logger.info("MongoDB indexes created/verified.")


# ---------------------------------------------------------------------------
# Auto-increment counter helper
# ---------------------------------------------------------------------------

def next_id(db: Database, name: str) -> int:
    """
    Emulate PostgreSQL SERIAL using a counters collection.
    Returns the next integer ID for the given counter name.
    """
    result = db[COLL_COUNTERS].find_one_and_update(
        {"_id": name},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return result["seq"]