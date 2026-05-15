"""
MongoDB startup migrations — replaces SQL DDL startup_migrations.py.

Creates all collection indexes. Fully idempotent — safe to run on every boot.
Does NOT use SQLAlchemy at all.
"""
import logging
import os

from pymongo import MongoClient, ASCENDING, DESCENDING

logger = logging.getLogger(__name__)


def _get_client() -> MongoClient:
    uri = os.environ.get("MONGODB_URI", "")
    if not uri:
        raise RuntimeError("MONGODB_URI environment variable is not set")
    return MongoClient(uri, maxPoolSize=10, serverSelectionTimeoutMS=5000)


def run_startup_migrations() -> None:
    """
    Create all MongoDB indexes. Idempotent — MongoDB silently skips
    indexes that already exist with the same definition.
    Call BEFORE importing any model or CRUD code.
    """
    client = _get_client()
    db = client["trading_bot"]

    try:
        # ── trades ────────────────────────────────────────────────────────
        db["trades"].create_index([("opened_at", DESCENDING)], background=True)
        db["trades"].create_index([("strategy_name", ASCENDING)], background=True)
        db["trades"].create_index([("result", ASCENDING)], background=True)

        # ── parameter_versions ───────────────────────────────────────────
        db["parameter_versions"].create_index([("version", DESCENDING)], background=True)
        db["parameter_versions"].create_index([("strategy_name", ASCENDING)], background=True)

        # ── adaptation_logs ───────────────────────────────────────────────
        db["adaptation_logs"].create_index([("evaluated_at", DESCENDING)], background=True)
        db["adaptation_logs"].create_index([("strategy_name", ASCENDING)], background=True)

        # ── app_settings ─────────────────────────────────────────────────
        db["app_settings"].create_index(
            [("key", ASCENDING)], unique=True, background=True
        )

        # ── strategies ───────────────────────────────────────────────────
        db["strategies"].create_index(
            [("name", ASCENDING)], unique=True, background=True
        )

        # ── shadow_signals ────────────────────────────────────────────────
        db["shadow_signals"].create_index([("signal_time", DESCENDING)], background=True)
        db["shadow_signals"].create_index([("strategy_name", ASCENDING)], background=True)

        # ── backtest_results ─────────────────────────────────────────────
        db["backtest_results"].create_index([("batch_id", ASCENDING)], background=True)
        db["backtest_results"].create_index([("strategy_name", ASCENDING)], background=True)
        db["backtest_results"].create_index([("created_at", DESCENDING)], background=True)

        # ── ohlcv_cache ───────────────────────────────────────────────────
        db["ohlcv_cache"].create_index(
            [("symbol", ASCENDING), ("interval", ASCENDING),
             ("from_date", ASCENDING), ("to_date", ASCENDING)],
            background=True,
        )

        # ── news_items ────────────────────────────────────────────────────
        db["news_items"].create_index([("published_at", DESCENDING)], background=True)
        db["news_items"].create_index([("fetched_at", DESCENDING)], background=True)

        # ── users ─────────────────────────────────────────────────────────
        db["users"].create_index(
            [("username", ASCENDING)], unique=True, background=True
        )

        # ── backtest_candidates ───────────────────────────────────────────
        db["backtest_candidates"].create_index(
            [("strategy_name", ASCENDING)], background=True,
            name="ix_backtest_candidates_strategy_name",
        )
        db["backtest_candidates"].create_index(
            [("strategy_name", ASCENDING), ("composite_score", DESCENDING)],
            background=True,
            name="ix_backtest_candidates_strategy_score",
        )
        db["backtest_candidates"].create_index(
            [("strategy_name", ASCENDING), ("qualified", ASCENDING)],
            background=True,
            name="ix_backtest_candidates_strategy_qualified",
        )

        # ── ensemble_decisions ────────────────────────────────────────────
        db["ensemble_decisions"].create_index([("timestamp", DESCENDING)], background=True)
        db["ensemble_decisions"].create_index([("symbol", ASCENDING)], background=True)

        # ── strategy_picker_decisions ─────────────────────────────────────
        db["strategy_picker_decisions"].create_index(
            [("symbol", ASCENDING)], background=True,
            name="ix_strategy_picker_decisions_symbol",
        )
        db["strategy_picker_decisions"].create_index(
            [("timestamp", DESCENDING)], background=True,
            name="ix_strategy_picker_decisions_timestamp",
        )

        # ── picker_weight_history ─────────────────────────────────────────
        db["picker_weight_history"].create_index(
            [("trade_id", ASCENDING)], background=True,
            name="ix_picker_weight_history_trade_id",
        )

        # ── backtest_batches ──────────────────────────────────────────────
        db["backtest_batches"].create_index(
            [("batch_id", ASCENDING)], unique=True, background=True,
            name="ix_backtest_batches_batch_id",
        )

        # ── strategy_pair_analyses ────────────────────────────────────────
        db["strategy_pair_analyses"].create_index(
            [("batch_id", ASCENDING)], background=True,
            name="ix_strategy_pair_analyses_batch_id",
        )

        # ── counters (auto-increment emulation) ───────────────────────────
        db["counters"].create_index([("_id", ASCENDING)], background=True)

        logger.info("MongoDB startup migrations complete — all indexes created/verified.")

    finally:
        client.close()