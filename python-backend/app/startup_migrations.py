"""
MongoDB startup migrations — replaces SQL DDL startup_migrations.py.

Creates all collection indexes. Fully idempotent — safe to run on every boot.
Does NOT use SQLAlchemy at all.
"""
import logging
import os

from pymongo import MongoClient, ASCENDING, DESCENDING

logger = logging.getLogger(__name__)

# How long decision audit records live before MongoDB's TTL monitor deletes
# them. Decisions are only useful for short-term debugging / score feedback;
# keeping them forever bloats the DB. Default 90 days to ensure trades can close
# and score feedback can access the decision. Override with DECISION_TTL_SECONDS.
# NOTE: EnsembleDecisions must live longer than the longest trade duration!
DECISION_TTL_SECONDS = int(os.environ.get("DECISION_TTL_SECONDS", 90 * 24 * 60 * 60))


def _get_client() -> MongoClient:
    uri = os.environ.get("MONGODB_URI", "")
    if not uri:
        raise RuntimeError("MONGODB_URI environment variable is not set")
    return MongoClient(uri, maxPoolSize=10, serverSelectionTimeoutMS=5000)


def _ensure_ttl_index(db, coll_name: str, field: str, seconds: int, name: str) -> None:
    """
    Ensure a single-field TTL index exists on ``field`` expiring after
    ``seconds``. Idempotent and self-healing:

      • If a non-TTL index already exists on ``field`` (e.g. an older
        descending sort index), it is dropped and recreated as a TTL index.
      • If a TTL index exists but with a different expiry, expireAfterSeconds
        is updated in-place via collMod (no rebuild).

    A TTL index is also a normal index, so it still serves sort/range queries
    on ``field`` — no separate query index is needed.
    """
    coll = db[coll_name]
    try:
        existing = coll.index_information()
    except Exception as exc:  # collection may not exist yet — create fresh
        existing = {}
        logger.debug("index_information failed for %s: %s", coll_name, exc)

    for idx_name, spec in existing.items():
        keys = spec.get("key", [])
        if len(keys) == 1 and keys[0][0] == field:
            current_ttl = spec.get("expireAfterSeconds")
            if current_ttl is None:
                # Plain index on this field — drop so we can recreate as TTL.
                coll.drop_index(idx_name)
                logger.info("Dropped non-TTL index %s on %s.%s to convert to TTL",
                            idx_name, coll_name, field)
            elif current_ttl != seconds:
                # TTL exists but wrong duration — adjust without a rebuild.
                db.command("collMod", coll_name,
                           index={"name": idx_name, "expireAfterSeconds": seconds})
                logger.info("Updated TTL on %s.%s: %ss -> %ss",
                            coll_name, field, current_ttl, seconds)
                return
            else:
                return  # already correct
            break

    coll.create_index(
        [(field, ASCENDING)],
        expireAfterSeconds=seconds,
        background=True,
        name=name,
    )
    logger.info("Created TTL index %s on %s.%s (expire after %ss)",
                name, coll_name, field, seconds)


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
        # Serves the recency-ranked "latest deployed candidate" reads
        # (promoted/qualified filter + sort by evaluated_at DESC).
        db["backtest_candidates"].create_index(
            [("strategy_name", ASCENDING), ("evaluated_at", DESCENDING)],
            background=True,
            name="ix_backtest_candidates_strategy_recent",
        )

        # ── ensemble_decisions ────────────────────────────────────────────
        # TTL on timestamp auto-deletes stale decisions (also serves the
        # `.sort("timestamp", -1)` queries, so no extra index needed).
        db["ensemble_decisions"].create_index([("symbol", ASCENDING)], background=True)
        _ensure_ttl_index(db, "ensemble_decisions", "timestamp",
                          DECISION_TTL_SECONDS, "ix_ensemble_decisions_timestamp_ttl")

        # ── news_veto_decisions ───────────────────────────────────────────
        db["news_veto_decisions"].create_index(
            [("symbol", ASCENDING)], background=True,
            name="ix_news_veto_decisions_symbol",
        )
        _ensure_ttl_index(db, "news_veto_decisions", "timestamp",
                          DECISION_TTL_SECONDS, "ix_news_veto_decisions_timestamp_ttl")

        # ── Retired strategy-picker collections (REMOVED) ─────────────────
        # The strategy picker was reduced to a news-veto and online picker-weight
        # learning was retired when the EnsembleVoter became the sole direction
        # authority. Strategy feedback is now a direct composite_score nudge at
        # trade close (see app/services/score_feedback.py). Drop the dead
        # collections; news-veto audit now lives in news_veto_decisions.
        for _obsolete in ("picker_weight_history", "strategy_picker_decisions"):
            try:
                if _obsolete in db.list_collection_names():
                    db[_obsolete].drop()
                    logger.info("Dropped obsolete collection %s", _obsolete)
            except Exception as exc:
                logger.warning("Could not drop %s: %s", _obsolete, exc)

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