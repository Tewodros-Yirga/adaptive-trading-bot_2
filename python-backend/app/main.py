"""
AlgoTrade Pro — FastAPI Application Entry Point

NOTE: Continuous backtesting has been moved to the standalone backtester-service
(backtester_service/). The backend no longer runs a ProcessPoolExecutor or
start_continuous_backtest tasks. Strategy param updates are picked up from
MongoDB (strategies collection) which the backtester writes to.
"""
import asyncio
import logging
import os
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, HTTPException, Security
from fastapi.security import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .db import get_database          # ← MongoDB: no SessionLocal/engine
from .auth_deps import get_current_user, require_admin, require_write_access, seed_admin_user

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── 1. Ensure MongoDB indexes exist ───────────────────────────────────
    try:
        from .db import create_indexes
        db = get_database()
        create_indexes(db)
        logger.info("MongoDB indexes verified.")
    except Exception as e:
        logger.error(f"MongoDB index creation failed: {e}")
        raise

    # ── 2. Seed admin user from env vars ──────────────────────────────────
    try:
        db = get_database()
        seed_admin_user(db)
    except Exception as e:
        logger.warning(f"Could not seed admin user: {e}")

    # ── 3. Ensure default params exist ────────────────────────────────────
    try:
        db = get_database()
        from .crud import get_current_params, save_params
        from .strategy.dtc import DEFAULT_PARAMS
        if not get_current_params(db):
            save_params(db, DEFAULT_PARAMS.copy(), reason="Initial defaults", trigger="SYSTEM")
            logger.info("Seeded default DTC parameters.")
    except Exception as e:
        logger.warning(f"Could not seed default params: {e}")

    # ── 4. Seed strategy registry into DB ─────────────────────────────────
    try:
        db = get_database()
        from .routers.strategies import _ensure_strategies_exist
        _ensure_strategies_exist(db)
        logger.info("Strategy registry seeded.")
    except Exception as e:
        logger.warning(f"Could not seed strategies: {e}")

    # ── 5. Seed default AppSettings for continuous backtest + picker ───────
    try:
        db = get_database()
        from .crud import seed_default_settings
        seed_default_settings(db)
        logger.info("Default AppSettings seeded.")
    except Exception as e:
        logger.warning(f"Could not seed default settings: {e}")

    # ── 6. Run startup health checks ───────────────────────────────────────
    try:
        db = get_database()
        from .services.startup_checks import run_startup_checks
        checks = await run_startup_checks(db)
        app.state.startup_checks = checks
        logger.info(f"Startup checks complete: {len(checks)} checks run.")
    except Exception as e:
        logger.warning(f"Startup checks failed: {e}")
        app.state.startup_checks = []

    # ── 7. Start background tasks ─────────────────────────────────────────
    bg_tasks = []
    bg_tasks.append(asyncio.create_task(_news_fetch_loop()))
    bg_tasks.append(asyncio.create_task(_news_learning_loop()))
    bg_tasks.append(asyncio.create_task(_global_context_loop()))

    # ── 8. Start live trading loop ─────────────────────────────────────────
    # NOTE: Continuous backtesting now runs in the dedicated backtester-service.
    #       The backend only needs the live trading loop here.
    try:
        bg_tasks.append(asyncio.create_task(_live_trading_loop()))
        logger.info("Started live trading loop.")
    except Exception as e:
        logger.warning(f"Could not start live trading loop: {e}")

    # ── 9. Start backtester keepalive loop ─────────────────────────────────
    # Pings the HuggingFace-hosted backtester every 4 minutes to prevent
    # the Space from sleeping due to inactivity.
    bg_tasks.append(asyncio.create_task(_backtester_keepalive_loop()))
    logger.info("Started backtester keepalive loop.")

    logger.info("Application startup complete.")
    yield

    # ── Shutdown ──────────────────────────────────────────────────────────
    for task in bg_tasks:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    logger.info("Application shutdown complete.")


# ---------------------------------------------------------------------------
# Background loops
# ---------------------------------------------------------------------------

async def _news_fetch_loop():
    """Fetch news every 30 minutes."""
    await asyncio.sleep(60)  # initial delay
    while True:
        try:
            db = get_database()
            from .services.news_intelligence import fetch_and_store_news
            from .config import settings
            fetch_and_store_news(db, getattr(settings, "symbol", "XAUUSD"))
        except Exception as e:
            logger.warning(f"News fetch loop error: {e}")
        await asyncio.sleep(1800)  # 30 min


async def _news_learning_loop():
    """Run retrospective learning every 2 hours."""
    await asyncio.sleep(120)
    while True:
        try:
            db = get_database()
            from .services.news_intelligence import run_retrospective_learning
            updated = run_retrospective_learning(db)
            if updated:
                logger.info(f"Retrospective learning updated {updated} news items.")
        except Exception as e:
            logger.warning(f"News learning loop error: {e}")
        await asyncio.sleep(7200)  # 2 hours


async def _global_context_loop():
    """Update global market context every 30 minutes."""
    await asyncio.sleep(90)
    while True:
        try:
            db = get_database()
            from .services.news_intelligence import update_global_context
            update_global_context(db)
        except Exception as e:
            logger.warning(f"Global context loop error: {e}")
        await asyncio.sleep(1800)


async def _backtester_keepalive_loop():
    """
    Ping the HuggingFace-hosted backtester service every 4 minutes so the
    Space does not go to sleep from inactivity (HF sleeps after ~5 min idle).

    Uses BacktesterClient so the HF Bearer token is always included — required
    for private Spaces.  Configure via env/secrets:
        BACKTESTER_URL       e.g. https://youruser-spacename.hf.space
        BACKTESTER_HF_TOKEN  your HuggingFace read token
    """
    await asyncio.sleep(30)  # let the backtester finish cold-starting

    from .services.backtester_client import BacktesterClient, BacktesterUnavailable

    client = BacktesterClient()

    if not client.is_configured:
        logger.warning(
            "Backtester keepalive: BACKTESTER_URL not set — keepalive disabled. "
            "Add it to your backend env/secrets."
        )
        return

    interval = 240  # 4 minutes — safely under HF's ~5-min idle threshold

    while True:
        try:
            data = await client.ping()
            logger.debug("Backtester keepalive OK — uptime=%ss", data.get("uptime_seconds", "?"))
        except BacktesterUnavailable as exc:
            logger.warning("Backtester keepalive: %s", exc)
            return  # misconfigured — stop looping
        except httpx.TimeoutException:
            logger.warning("Backtester keepalive: request timed out (Space may be cold-starting)")
        except httpx.HTTPStatusError as exc:
            logger.warning("Backtester keepalive: HTTP %d from %s", exc.response.status_code, exc.request.url)
        except Exception as exc:
            logger.warning("Backtester keepalive error: %s", exc)

        await asyncio.sleep(interval)


async def _live_trading_loop():
    """
    Autonomous live trading loop.

    Every 60 seconds (configurable via ``live_trading_interval_seconds`` AppSetting):
      1. Skip if simulation_mode is True.
      2. Fetch the latest price from the MT5 bridge for each configured symbol.
      3. Pass price + minimal market_data to process_signal().
      4. The orchestrator + picker handle signal gathering, strategy selection,
         risk checks, and order placement.
    """
    await asyncio.sleep(30)  # brief initial delay so the bridge has time to connect
    while True:
        interval = 60  # default; overridden by AppSetting each iteration
        try:
            db = get_database()
            from .config import settings as _settings
            from .services.orchestrator import process_signal
            from . import crud as _crud

            interval = float(
                _crud.get_setting(db, "live_trading_interval_seconds") or 60
            )

            if _settings.simulation_mode:
                logger.debug("Live trading loop: simulation_mode=True, skipping order placement.")
            else:
                active_symbols_raw = _crud.get_setting(db, "live_trading_symbols") or "XAUUSD"
                symbols = [s.strip() for s in active_symbols_raw.split(",") if s.strip()]

                for symbol in symbols:
                    try:
                        from datetime import date, timedelta
                        from .services.ohlcv import fetch_ohlcv_with_fallback
                        from .services.bridge_client import bridge_client as _bridge
                        from_dt = (date.today() - timedelta(days=2)).isoformat()
                        to_dt = date.today().isoformat()

                        # fetch_ohlcv_with_fallback is async but its MT5 bridge
                        # path calls sync httpx internally.  Use it as-is here;
                        # the true blocking risk is get_account / get_positions
                        # called on the bridge directly — those must go through
                        # run_in_executor so they don't stall the event loop.
                        loop = asyncio.get_event_loop()

                        # Price fetch: try bridge first (sync → executor), then
                        # fall back to the async ohlcv chain.
                        df = None
                        try:
                            candles = await loop.run_in_executor(
                                None,
                                lambda: _bridge.get_candles(
                                    symbol=symbol,
                                    timeframe="1h",
                                    from_date=from_dt,
                                    to_date=to_dt,
                                ),
                            )
                            if candles:
                                import pandas as _pd
                                df = _pd.DataFrame(candles)
                                for col in ("open", "high", "low", "close"):
                                    df[col] = _pd.to_numeric(df[col], errors="coerce")
                                df = df.dropna(subset=["close"])
                        except Exception as _bridge_exc:
                            logger.debug(
                                "Live loop bridge fetch failed for %s, falling back: %s",
                                symbol, _bridge_exc,
                            )

                        if df is None or df.empty:
                            df, _src = await fetch_ohlcv_with_fallback(
                                symbol, from_dt, to_dt, "1h", db
                            )

                        if df is None or df.empty:
                            logger.warning("Live loop: no price data for %s", symbol)
                            continue

                        price = float(df["close"].iloc[-1])
                        atr = float((df["high"] - df["low"]).tail(14).mean()) if len(df) >= 14 else price * 0.005

                        market_data = {
                            "symbol": symbol,
                            "price": price,
                            "current_price": price,
                            "atr": atr,
                            # Raw DataFrame passed so the orchestrator can compute
                            # per-strategy indicators (EMA, RSI, MACD, BB, VWAP)
                            # via _enrich_market_data_from_df().  Not serialised.
                            "_df": df,
                        }

                        result = await process_signal(db, market_data, symbol, price)
                        status = result.get("status", "?")

                        if status == "OK":
                            logger.info(
                                "Live trade placed: %s %s @ %.5f (trade_id=%s)",
                                result.get("signal"), symbol, price, result.get("trade_id")
                            )
                        elif status not in ("NO_SIGNAL", "NO_ACTIVE_STRATEGIES"):
                            logger.info("Live loop %s: %s", symbol, status)

                    except Exception as sym_exc:
                        logger.warning("Live loop error for %s: %s", symbol, sym_exc)

        except Exception as exc:
            logger.error("Live trading loop crashed: %s", exc, exc_info=True)

        await asyncio.sleep(interval)


# ── API key guard for webhook (machine-to-machine) ────────────────────────────
API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(api_key: str = Security(API_KEY_HEADER)):
    expected = os.environ.get("APP_API_KEY")
    if not expected or api_key != expected:
        raise HTTPException(status_code=403, detail="Forbidden")
    return api_key


# ── App factory ────────────────────────────────────────────────────────────────
def create_app() -> FastAPI:
    app = FastAPI(
        title="AlgoTrade Pro",
        version="2.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Import all routers ─────────────────────────────────────────────────
    from .routers.auth import router as auth_router
    from .routers.webhook import router as webhook_router
    from .routers.strategies import router as strategies_router
    from .routers.risk import router as risk_router
    from .routers.news import router as news_router
    from .routers.backtest import router as backtest_router
    from .routers.websocket import router as ws_router
    from .routers.settings import router as settings_router
    from .routers.trades import router as trades_router
    from .routers.adapt import router as adapt_router
    from .routers.params import router as params_router
    from .routers.bridge import router as bridge_router
    from .routers.shadow_signals import router as shadow_router
    from .routers.ensemble import router as ensemble_router
    from .routers.picker import router as picker_router
    from .routers.system import router as system_router
    from .routers.health import router as health_router

    # ── Auth router (public — no auth required for login) ──────────────────
    app.include_router(auth_router)

    # ── Webhook keeps X-API-Key auth (machine-to-machine) ──────────────────
    app.include_router(webhook_router, dependencies=[Depends(verify_api_key)])

    # ── All other routes require JWT authentication ────────────────────────
    jwt_deps = [Depends(get_current_user)]

    app.include_router(strategies_router, dependencies=jwt_deps)
    app.include_router(risk_router, dependencies=jwt_deps)
    app.include_router(news_router, dependencies=jwt_deps)
    app.include_router(backtest_router, dependencies=jwt_deps)
    app.include_router(ws_router)  # WebSocket handles auth separately via ?token=
    app.include_router(settings_router, dependencies=jwt_deps)
    app.include_router(trades_router, dependencies=jwt_deps)
    app.include_router(adapt_router, dependencies=jwt_deps)
    app.include_router(params_router, dependencies=jwt_deps)
    app.include_router(bridge_router, dependencies=jwt_deps)
    app.include_router(shadow_router, dependencies=jwt_deps)
    app.include_router(ensemble_router, dependencies=jwt_deps)
    app.include_router(picker_router, dependencies=jwt_deps)
    app.include_router(system_router, dependencies=jwt_deps)
    # /health/db is public — no jwt_deps
    app.include_router(health_router)

    # ── Serve frontend SPA ────────────────────────────────────────────────
    frontend_dist = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")
    if os.path.exists(frontend_dist):
        app.mount("/app", StaticFiles(directory=frontend_dist, html=True), name="frontend")

    @app.get("/health")
    def health_check():
        return {"status": "ok", "version": "2.0.0"}

    return app


app = create_app()