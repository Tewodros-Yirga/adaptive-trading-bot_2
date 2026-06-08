"""
app/services/ohlcv.py — Shared OHLCV Data Fetching Layer

All OHLCV fetching logic lives here. The backtester and continuous backtest service
both import from this module. Provides a 5-source fallback chain:
  1. MT5 Bridge (uses broker's live data via the bridge service — most accurate)
  2. yfinance   (free, wide coverage)
  3. Alpha Vantage (requires API key)
  4. OANDA      (forex only, public endpoint)
  5. Twelve Data (last resort, requires API key)

Also provides:
  - build_mtf_market_data() — assembles the multi-timeframe market_data dict
    expected by requires_mtf strategies (e.g. Alchemist).
"""
import logging
from datetime import datetime, timedelta

import httpx
import pandas as pd

logger = logging.getLogger(__name__)

YF_SYMBOL_MAP = {
    "XAUUSD": "GC=F",
    "XAGUSD": "SI=F",
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "JPY=X",
    "USDCAD": "CAD=X",
    "AUDUSD": "AUDUSD=X",
    "NZDUSD": "NZDUSD=X",
    "BTCUSD": "BTC-USD",
    "ETHUSD": "ETH-USD",
    "US30": "^DJI",
    "NAS100": "^NDX",
    "SPX500": "^GSPC",
}

TIMEFRAME_MAP_YF = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1h",
    "4h": "1h",   # resample 4h from 1h
    "1d": "1d",
    "1w": "1wk",
}

AV_FX_PAIRS = {
    "EURUSD": ("EUR", "USD"),
    "GBPUSD": ("GBP", "USD"),
    "USDJPY": ("USD", "JPY"),
    "USDCAD": ("USD", "CAD"),
    "AUDUSD": ("AUD", "USD"),
    "NZDUSD": ("NZD", "USD"),
    "XAUUSD": ("XAU", "USD"),
    "XAGUSD": ("XAG", "USD"),
}

OANDA_SUPPORTED = {"EURUSD", "GBPUSD", "USDJPY", "USDCAD", "AUDUSD", "NZDUSD"}

OANDA_TIMEFRAME_MAP = {
    "1m": "M1", "5m": "M5", "15m": "M15", "30m": "M30",
    "1h": "H1", "4h": "H4", "1d": "D", "1w": "W",
}

# Minimum bar counts required per timeframe for MTF strategies
MTF_MIN_BARS: dict[str, int] = {
    "1d":  30,
    "4h":  100,
    "1h":  100,
    "15m": 100,
}


def _normalize_symbol(symbol: str) -> str:
    """Strip broker-specific suffixes."""
    s = symbol.upper().rstrip("M").rstrip(".")
    for suffix in (".PRO", ".RAW", ".STD", ".ECN"):
        if s.endswith(suffix):
            s = s[: -len(suffix)]
    return s


# ---------------------------------------------------------------------------
# Source 1: yfinance
# ---------------------------------------------------------------------------

def fetch_yfinance(symbol: str, from_date: str, to_date: str, timeframe: str = "1h") -> pd.DataFrame:
    """Fetch OHLCV from yfinance with MultiIndex fix and 4h resampling."""
    try:
        import yfinance as yf
    except ImportError:
        raise ValueError("yfinance is not installed")

    norm = _normalize_symbol(symbol)
    yf_symbol = YF_SYMBOL_MAP.get(norm, norm)
    yf_interval = TIMEFRAME_MAP_YF.get(timeframe, "1h")

    import time as _time
    max_retries = 3
    df = None
    for attempt in range(max_retries):
        try:
            df = yf.download(
                yf_symbol,
                start=from_date,
                end=to_date,
                interval=yf_interval,
                auto_adjust=True,
                progress=False,
            )
            if df is not None and not df.empty:
                break
            logger.debug("yfinance returned empty data for %s (attempt %d)", yf_symbol, attempt + 1)
        except Exception as exc:
            err = str(exc).lower()
            if "rate" in err or "too many" in err:
                delay = 2 ** (attempt + 1)
                logger.debug("yfinance rate limited for %s, retrying in %ds", yf_symbol, delay)
                _time.sleep(delay)
            else:
                raise

    if df is None or df.empty:
        raise ValueError(f"yfinance returned empty DataFrame for {symbol} ({yf_symbol})")

    # FIX 1: Flatten MultiIndex columns
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [col[0].lower() for col in df.columns]
    else:
        df.columns = [c.lower() for c in df.columns]

    # FIX 2: Ensure scalar float series
    for col in ("open", "high", "low", "close", "volume"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col].squeeze(), errors="coerce")

    df = df.dropna(subset=["open", "high", "low", "close"])

    # Resample 1h → 4h
    if timeframe == "4h" and yf_interval == "1h":
        df = df.resample("4h").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
        ).dropna()

    if df.empty:
        raise ValueError(f"yfinance returned empty DataFrame for {symbol} ({yf_symbol}) after processing")

    return df


# ---------------------------------------------------------------------------
# Source 2: Alpha Vantage
# ---------------------------------------------------------------------------

AV_TIMEFRAME_MAP = {
    "1m": "1min", "5m": "5min", "15m": "15min", "30m": "30min",
    "1h": "60min", "4h": "60min",
    "1d": None,  # use FX_DAILY / TIME_SERIES_DAILY
    "1w": None,  # use WEEKLY
}


def fetch_alpha_vantage(symbol: str, from_date: str, to_date: str, timeframe: str = "1h", api_key: str = "") -> pd.DataFrame:
    """Fetch OHLCV from Alpha Vantage with proper error checking."""
    if not api_key:
        raise ValueError("Alpha Vantage API key not configured")

    norm = _normalize_symbol(symbol)
    is_fx = norm in AV_FX_PAIRS
    av_interval = AV_TIMEFRAME_MAP.get(timeframe, "60min")
    base_url = "https://www.alphavantage.co/query"

    if timeframe in ("1d", "1w") or av_interval is None:
        if is_fx:
            from_sym, to_sym = AV_FX_PAIRS[norm]
            func = "FX_DAILY" if timeframe == "1d" else "FX_WEEKLY"
            params = {
                "function": func,
                "from_symbol": from_sym,
                "to_symbol": to_sym,
                "outputsize": "full",
                "apikey": api_key,
            }
            ts_key_fragment = "Time Series FX"
        else:
            func = "TIME_SERIES_DAILY" if timeframe == "1d" else "TIME_SERIES_WEEKLY"
            params = {
                "function": func,
                "symbol": norm,
                "outputsize": "full",
                "apikey": api_key,
            }
            ts_key_fragment = "Time Series"
    else:
        if is_fx:
            from_sym, to_sym = AV_FX_PAIRS[norm]
            params = {
                "function": "FX_INTRADAY",
                "from_symbol": from_sym,
                "to_symbol": to_sym,
                "interval": av_interval,
                "outputsize": "full",
                "apikey": api_key,
            }
            ts_key_fragment = "Time Series FX"
        else:
            params = {
                "function": "TIME_SERIES_INTRADAY",
                "symbol": norm,
                "interval": av_interval,
                "outputsize": "full",
                "apikey": api_key,
            }
            ts_key_fragment = "Time Series"

    response = httpx.get(base_url, params=params, timeout=30)
    data = response.json()

    # FIX: Check for API-level errors
    if "Information" in data:
        raise ValueError(f"Alpha Vantage rate limit or key error: {data['Information']}")
    if "Error Message" in data:
        raise ValueError(f"Alpha Vantage error: {data['Error Message']}")
    if "Note" in data:
        raise ValueError(f"Alpha Vantage note (likely rate limit): {data['Note']}")

    time_series_key = next((k for k in data if ts_key_fragment in k), None)
    if not time_series_key or not data.get(time_series_key):
        raise ValueError(f"Alpha Vantage returned no data for {symbol}: {list(data.keys())}")

    series = data[time_series_key]
    records = []
    for date_str, values in sorted(series.items()):
        try:
            o_key = next((k for k in values if "open" in k.lower()), None)
            h_key = next((k for k in values if "high" in k.lower()), None)
            l_key = next((k for k in values if "low" in k.lower()), None)
            c_key = next((k for k in values if "close" in k.lower()), None)
            v_key = next((k for k in values if "volume" in k.lower()), None)
            records.append({
                "datetime": date_str,
                "open": float(values[o_key]) if o_key else 0.0,
                "high": float(values[h_key]) if h_key else 0.0,
                "low": float(values[l_key]) if l_key else 0.0,
                "close": float(values[c_key]) if c_key else 0.0,
                "volume": float(values[v_key]) if v_key else 0.0,
            })
        except (ValueError, KeyError, StopIteration):
            continue

    if not records:
        raise ValueError(f"Alpha Vantage parsed 0 records for {symbol}")

    df = pd.DataFrame(records)
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.set_index("datetime").sort_index()

    # Filter by date range
    df = df[(df.index >= pd.Timestamp(from_date)) & (df.index <= pd.Timestamp(to_date))]

    # Resample 1h → 4h if needed
    if timeframe == "4h" and av_interval == "60min":
        df = df.resample("4h").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
        ).dropna()

    if df.empty:
        raise ValueError(f"Alpha Vantage returned no data for {symbol} in range {from_date}–{to_date}")

    return df


# ---------------------------------------------------------------------------
# Source 3: Twelve Data
# ---------------------------------------------------------------------------

TWELVE_DATA_TIMEFRAME_MAP = {
    "1m": "1min", "5m": "5min", "15m": "15min", "30m": "30min",
    "1h": "1h", "4h": "4h", "1d": "1day", "1w": "1week",
}


async def fetch_twelve_data(symbol: str, from_date: str, to_date: str, timeframe: str, api_key: str = "") -> pd.DataFrame:
    """Fetch OHLCV from Twelve Data API."""
    if not api_key:
        raise ValueError("Twelve Data key not configured")

    interval = TWELVE_DATA_TIMEFRAME_MAP.get(timeframe, "1h")
    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": _normalize_symbol(symbol),
        "interval": interval,
        "start_date": from_date,
        "end_date": to_date,
        "apikey": api_key,
        "format": "JSON",
        "outputsize": 5000,
    }

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(url, params=params)

    data = response.json()

    if data.get("status") == "error":
        raise ValueError(f"Twelve Data error: {data.get('message', 'unknown error')}")

    values = data.get("values")
    if not values:
        raise ValueError(f"Twelve Data returned no values for {symbol}: {list(data.keys())}")

    records = []
    for item in values:
        try:
            records.append({
                "datetime": item["datetime"],
                "open": float(item["open"]),
                "high": float(item["high"]),
                "low": float(item["low"]),
                "close": float(item["close"]),
                "volume": float(item.get("volume", 0) or 0),
            })
        except (KeyError, ValueError):
            continue

    if not records:
        raise ValueError(f"Twelve Data parsed 0 records for {symbol}")

    df = pd.DataFrame(records)
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.set_index("datetime").sort_index()
    df = df[(df.index >= pd.Timestamp(from_date)) & (df.index <= pd.Timestamp(to_date))]

    if df.empty:
        raise ValueError(f"Twelve Data returned no data for {symbol} in range {from_date}–{to_date}")

    return df


# ---------------------------------------------------------------------------
# Source 4: OANDA public (forex only)
# ---------------------------------------------------------------------------

async def fetch_oanda_public(symbol: str, from_date: str, to_date: str, timeframe: str) -> pd.DataFrame:
    """Fetch OHLCV from OANDA public endpoint (forex pairs only)."""
    norm = _normalize_symbol(symbol)
    if norm not in OANDA_SUPPORTED:
        raise ValueError(f"OANDA public endpoint does not support {symbol}")

    instrument = f"{norm[:3]}_{norm[3:]}"
    gran = OANDA_TIMEFRAME_MAP.get(timeframe, "H1")
    from_ts = datetime.fromisoformat(from_date).strftime("%Y-%m-%dT%H:%M:%S.000000000Z")
    to_ts = datetime.fromisoformat(to_date).strftime("%Y-%m-%dT%H:%M:%S.000000000Z")

    url = f"https://api-fxtrade.oanda.com/v3/instruments/{instrument}/candles"
    headers = {"Accept-Datetime-Format": "RFC3339"}
    params = {
        "granularity": gran,
        "from": from_ts,
        "to": to_ts,
        "price": "M",
    }

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(url, headers=headers, params=params)

    if response.status_code != 200:
        raise ValueError(f"OANDA returned status {response.status_code} for {symbol}")

    data = response.json()
    candles = data.get("candles", [])
    if not candles:
        raise ValueError(f"OANDA returned empty candles for {symbol}")

    records = []
    for c in candles:
        if not c.get("complete"):
            continue
        mid = c.get("mid", {})
        try:
            records.append({
                "datetime": c["time"],
                "open": float(mid["o"]),
                "high": float(mid["h"]),
                "low": float(mid["l"]),
                "close": float(mid["c"]),
                "volume": float(c.get("volume", 0)),
            })
        except (KeyError, ValueError):
            continue

    if not records:
        raise ValueError(f"OANDA parsed 0 complete candles for {symbol}")

    df = pd.DataFrame(records)
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.set_index("datetime").sort_index()

    if df.empty:
        raise ValueError(f"OANDA returned no data for {symbol} in range {from_date}–{to_date}")

    return df


# ---------------------------------------------------------------------------
# Source 3: MT5 Bridge (uses broker's data)
# ---------------------------------------------------------------------------

async def fetch_mt5_bridge(
    symbol: str, from_date: str, to_date: str, timeframe: str
) -> pd.DataFrame:
    """
    Fetch OHLCV from the MT5 bridge service (async wrapper around the sync client).
    """
    from .bridge_client import bridge_client

    # Pass raw symbol — the bridge adapter handles XAUUSDm ↔ XAUUSD fallback itself.
    # _normalize_symbol is only needed for yfinance/AV ticker mapping, not for MT5.
    candles = bridge_client.get_candles(
        symbol=symbol,
        timeframe=timeframe,
        from_date=from_date,
        to_date=to_date,
    )

    if not candles:
        raise ValueError(f"MT5 bridge returned no candles for {symbol}")

    records = []
    for c in candles:
        try:
            records.append({
                "datetime": c.get("datetime") or c.get("time") or c.get("date", ""),
                "open": float(c["open"]),
                "high": float(c["high"]),
                "low": float(c["low"]),
                "close": float(c["close"]),
                "volume": float(c.get("volume", 0) or 0),
            })
        except (KeyError, ValueError, TypeError):
            continue

    if not records:
        raise ValueError(f"MT5 bridge parsed 0 candles for {symbol}")

    df = pd.DataFrame(records)
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.set_index("datetime").sort_index()
    df = df[(df.index >= pd.Timestamp(from_date)) & (df.index <= pd.Timestamp(to_date))]

    if df.empty:
        raise ValueError(f"MT5 bridge returned no data for {symbol} in range {from_date}–{to_date}")

    return df


# ---------------------------------------------------------------------------
# Unified fallback chain
# ---------------------------------------------------------------------------

async def fetch_ohlcv_with_fallback(
    symbol: str,
    from_date: str,
    to_date: str,
    timeframe: str,
    db,
) -> tuple[pd.DataFrame, str]:
    """
    Returns (dataframe, source_name_used). Tries each source in order:
      1. MT5 Bridge  (broker's live data — most accurate for the traded symbol)
      2. yfinance    (free, wide coverage)
      3. Alpha Vantage (requires API key)
      4. OANDA      (async, forex only)
      5. Twelve Data (async, last resort)

    Raises RuntimeError if all sources fail.
    """
    from .. import crud as _crud

    av_key = _crud.get_setting(db, "alphavantage_key") or ""
    td_key = _crud.get_setting(db, "twelve_data_key") or ""

    errors: list[str] = []

    # Source 1: MT5 Bridge (broker's data — preferred for accuracy)
    try:
        df = await fetch_mt5_bridge(symbol, from_date, to_date, timeframe)
        if not df.empty:
            logger.info("OHLCV source: mt5_bridge for %s", symbol)
            return df, "mt5_bridge"
    except Exception as exc:
        errors.append(f"mt5_bridge: {exc}")

    # Source 2: yfinance
    try:
        df = fetch_yfinance(symbol, from_date, to_date, timeframe)
        if not df.empty:
            return df, "yfinance"
    except Exception as exc:
        errors.append(f"yfinance: {exc}")

    # Source 3: Alpha Vantage
    try:
        df = fetch_alpha_vantage(symbol, from_date, to_date, timeframe, api_key=av_key)
        if not df.empty:
            return df, "alpha_vantage"
    except Exception as exc:
        errors.append(f"alpha_vantage: {exc}")

    # Source 4: OANDA (forex only)
    try:
        df = await fetch_oanda_public(symbol, from_date, to_date, timeframe)
        if not df.empty:
            return df, "oanda"
    except Exception as exc:
        errors.append(f"oanda: {exc}")

    # Source 5: Twelve Data (last resort)
    try:
        df = await fetch_twelve_data(symbol, from_date, to_date, timeframe, api_key=td_key)
        if not df.empty:
            return df, "twelve_data"
    except Exception as exc:
        errors.append(f"twelve_data: {exc}")

    raise RuntimeError(
        f"All OHLCV sources failed for {symbol}.\n" + "\n".join(errors)
    )


def df_to_ohlcv_list(df: pd.DataFrame) -> list[dict]:
    """Convert a DataFrame (index=datetime, cols=open/high/low/close/volume) to list[dict].

    The ``date`` field carries the FULL ISO datetime string for intraday data
    (e.g. "2023-01-15 08:00:00") so that _build_mtf_bars_for_index can build a
    proper per-bar DatetimeIndex instead of collapsing same-day bars to midnight.
    For daily/weekly data the index only has date precision so the string is
    naturally date-only ("2023-01-15").
    """
    records = []
    for ts, row in df.iterrows():
        try:
            # Preserve full timestamp for intraday frames; daily bars naturally
            # have no time component and isoformat() returns "YYYY-MM-DD".
            if hasattr(ts, "isoformat"):
                date_str = ts.isoformat()  # e.g. "2023-01-15T08:00:00" or "2023-01-15"
                # Normalise the separator from "T" to space for consistency with bridge records
                date_str = date_str.replace("T", " ")
            else:
                date_str = str(ts)
            records.append({
                "date": date_str,
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row.get("volume", 0) or 0),
            })
        except (KeyError, ValueError, TypeError):
            continue
    return records


def fetch_ohlcv_sync(
    symbol: str,
    from_date: str,
    to_date: str,
    timeframe: str = "1d",
    av_key: str = "",
) -> list[dict]:
    """
    Fully synchronous OHLCV fetch with a 3-source fallback chain:
      1. MT5 Bridge   — broker's live data, full history, no date limits
      2. yfinance     — free, but 1h data limited to last ~730 days
      3. Alpha Vantage — requires API key

    Safe to call from subprocesses (ProcessPoolExecutor) and threads.
    Returns a list[dict] with keys: date, open, high, low, close, volume.

    IMPORTANT — the ``date`` field always carries the FULL ISO datetime string
    (e.g. "2023-01-15 08:00:00") for intraday timeframes so that
    _build_mtf_bars_for_index can reconstruct a proper per-bar datetime index.
    For daily/weekly timeframes it remains a date-only string ("2023-01-15").
    Callers that only need the calendar date must slice [:10] themselves.

    Raises RuntimeError if all sources fail.
    """
    errors: list[str] = []

    # Whether this timeframe has intraday resolution (sub-daily).
    _INTRADAY_TFS = {"1m", "5m", "15m", "30m", "1h", "4h"}
    is_intraday = timeframe in _INTRADAY_TFS

    # ── Source 1: MT5 Bridge ──────────────────────────────────────────────
    _BRIDGE_CHUNK_DAYS: dict[str, int] = {
        "15m": 20, "1h": 60, "4h": 90, "1d": 365, "1w": 730,
    }
    try:
        from datetime import datetime as _dt, timedelta as _td
        from .bridge_client import bridge_client
        _bridge = bridge_client

        chunk_days = _BRIDGE_CHUNK_DAYS.get(timeframe, 30)
        start_dt = _dt.fromisoformat(from_date)
        end_dt   = _dt.fromisoformat(to_date)

        all_bridge_records: list[dict] = []
        seen_keys: set[str] = set()
        chunk_start = start_dt
        _bridge_fatal = False  # set True on unrecoverable errors to break early

        while chunk_start <= end_dt and not _bridge_fatal:
            chunk_end = min(chunk_start + _td(days=chunk_days - 1), end_dt)
            try:
                candles = _bridge.get_candles(
                    symbol=symbol,
                    timeframe=timeframe,
                    from_date=chunk_start.date().isoformat(),
                    to_date=chunk_end.date().isoformat(),
                )
                for c in candles or []:
                    try:
                        raw_dt = c.get("datetime") or c.get("time") or c.get("date", "")
                        dedup_key = str(raw_dt)
                        if dedup_key in seen_keys:
                            continue
                        seen_keys.add(dedup_key)
                        date_value = str(raw_dt) if is_intraday else str(raw_dt)[:10]
                        all_bridge_records.append({
                            "date":   date_value,
                            "open":   float(c["open"]),
                            "high":   float(c["high"]),
                            "low":    float(c["low"]),
                            "close":  float(c["close"]),
                            "volume": float(c.get("volume", 0) or 0),
                        })
                    except (KeyError, ValueError, TypeError):
                        continue
            except Exception as chunk_exc:
                exc_str = str(chunk_exc).lower()
                # Fatal errors — bridge is down or misconfigured; no point
                # trying remaining chunks, bail out immediately.
                _is_fatal = any(x in exc_str for x in (
                    "connection refused", "connect error", "connectionerror",
                    "name or service not known", "no route to host",
                    "401", "auth", "403 forbidden", "404",
                ))
                if _is_fatal:
                    logger.warning(
                        "MT5 bridge fatal error for %s %s — skipping bridge: %s",
                        symbol, timeframe, chunk_exc,
                    )
                    _bridge_fatal = True
                    errors.append(f"mt5_bridge (fatal): {chunk_exc}")
                else:
                    logger.warning(
                        "bridge chunk %s–%s failed for %s %s: %s",
                        chunk_start.date(), chunk_end.date(), symbol, timeframe, chunk_exc,
                    )
            chunk_start = chunk_end + _td(days=1)

        if all_bridge_records:
            logger.info(
                "fetch_ohlcv_sync: mt5_bridge %s %s — %d bars (chunked %dd)",
                symbol, timeframe, len(all_bridge_records), chunk_days,
            )
            return all_bridge_records

        if not _bridge_fatal:
            logger.warning(
                "MT5 bridge returned 0 candles for %s %s %s–%s — falling back to yfinance",
                symbol, timeframe, from_date, to_date,
            )
        errors.append("mt5_bridge: 0 candles returned across all chunks")
    except Exception as exc:
        logger.warning("MT5 bridge client error for %s: %s — falling back", symbol, exc)
        errors.append(f"mt5_bridge: {exc}")

    # ── Source 2: yfinance (fallback) ─────────────────────────────────────
    # yfinance prints "1 Failed download: ['GC=F']: ..." directly to stdout,
    # bypassing Python logging. Suppress it by redirecting sys.stdout/stderr.
    try:
        import io as _io
        import sys as _sys
        _captured_out = _io.StringIO()
        _captured_err = _io.StringIO()
        _old_stdout, _old_stderr = _sys.stdout, _sys.stderr
        _sys.stdout = _captured_out
        _sys.stderr = _captured_err
        try:
            df = fetch_yfinance(symbol, from_date, to_date, timeframe)
        finally:
            _sys.stdout = _old_stdout
            _sys.stderr = _old_stderr
            # Log suppressed yfinance output at DEBUG so it's available for debugging
            _yf_out = _captured_out.getvalue().strip()
            _yf_err = _captured_err.getvalue().strip()
            if _yf_out:
                logger.debug("yfinance stdout: %s", _yf_out)
            if _yf_err:
                logger.debug("yfinance stderr: %s", _yf_err)

        if not df.empty:
            logger.info("fetch_ohlcv_sync: yfinance for %s %s", symbol, timeframe)
            return df_to_ohlcv_list(df)
    except Exception as exc:
        errors.append(f"yfinance: {exc}")

    # ── Source 3: Alpha Vantage ────────────────────────────────────────────
    if av_key:
        try:
            df = fetch_alpha_vantage(symbol, from_date, to_date, timeframe, api_key=av_key)
            if not df.empty:
                logger.info("fetch_ohlcv_sync: alpha_vantage for %s %s", symbol, timeframe)
                return df_to_ohlcv_list(df)
        except Exception as exc:
            errors.append(f"alpha_vantage: {exc}")

    raise RuntimeError(
        f"fetch_ohlcv_sync: all sources failed for {symbol} {timeframe} "
        f"{from_date}–{to_date}.\n" + "\n".join(errors)
    )





# ---------------------------------------------------------------------------
# Multi-timeframe market_data builder
# ---------------------------------------------------------------------------

def build_mtf_market_data(
    symbol: str,
    current_idx: int,
    bars_by_tf: dict[str, pd.DataFrame],
    atr: float | None = None,
    correlated_bars: pd.DataFrame | None = None,
) -> dict:
    """
    Assemble the multi-timeframe ``market_data`` dict expected by
    ``requires_mtf`` strategies such as Alchemist.

    Parameters
    ----------
    symbol:
        Trading symbol (e.g. "XAUUSD").
    current_idx:
        The current bar index in the *primary* (highest-frequency) timeframe.
        All DataFrames are sliced up to and including this bar so the strategy
        never looks into the future.
    bars_by_tf:
        Dict mapping timeframe string → full DataFrame for that timeframe.
        Expected keys: "1d", "4h", "1h", "15m".
        Missing timeframes produce empty DataFrames.
    atr:
        Pre-computed ATR for the current bar. If None, derived from 1h close
        range as a rough proxy.
    correlated_bars:
        Optional DataFrame of correlated instrument bars (same slicing applied).

    Returns
    -------
    dict with keys:
        symbol, timestamp, current_price, atr,
        1d_bars, 4h_bars, 1h_bars, 15m_bars,
        correlated_bars (pd.DataFrame, may be empty)

    Notes
    -----
    - ``current_idx`` is used only for the primary bar's timestamp / price
      derivation. The individual DataFrames should already be sliced externally
      (e.g. in the backtest loop) so no double-slicing occurs.  If the
      DataFrames are full (live trading), pass ``current_idx=-1`` to use the
      last bar.
    - Minimum bar counts per timeframe (``MTF_MIN_BARS``) are not enforced
      here; the strategy's internal guards handle that gracefully.
    """
    def _safe_slice(df: pd.DataFrame) -> pd.DataFrame:
        """Return the dataframe as-is — caller is responsible for slicing."""
        if df is None or df.empty:
            return pd.DataFrame()
        return df

    bars_1d = _safe_slice(bars_by_tf.get("1d"))
    bars_4h = _safe_slice(bars_by_tf.get("4h"))
    bars_1h = _safe_slice(bars_by_tf.get("1h"))
    bars_15m = _safe_slice(bars_by_tf.get("15m"))

    # Determine current price and timestamp from the most granular available frame
    current_price = 0.0
    timestamp: datetime = datetime.utcnow()

    for bars in (bars_15m, bars_1h, bars_4h, bars_1d):
        if not bars.empty:
            last = bars.iloc[-1]
            current_price = float(last["close"])
            idx = bars.index[-1]
            timestamp = idx.to_pydatetime() if hasattr(idx, "to_pydatetime") else datetime.utcnow()
            break

    # ATR proxy: use provided value or compute a simple 14-bar average true range
    if atr is None:
        atr = _compute_atr_simple(bars_1h)

    return {
        "symbol": symbol,
        "timestamp": timestamp,
        "current_price": current_price,
        "atr": atr,
        "1d_bars": bars_1d,
        "4h_bars": bars_4h,
        "1h_bars": bars_1h,
        "15m_bars": bars_15m,
        "correlated_bars": correlated_bars if correlated_bars is not None else pd.DataFrame(),
    }


def _compute_atr_simple(bars: pd.DataFrame, period: int = 14) -> float:
    """Compute a simple Wilder ATR from a DataFrame with high/low/close columns."""
    if bars.empty or len(bars) < period + 1:
        return 0.0
    highs = bars["high"].values.astype(float)
    lows = bars["low"].values.astype(float)
    closes = bars["close"].values.astype(float)

    trs = []
    for i in range(1, len(bars)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)

    if len(trs) < period:
        return float(sum(trs) / len(trs)) if trs else 0.0

    atr_val = sum(trs[:period]) / period
    for i in range(period, len(trs)):
        atr_val = (atr_val * (period - 1) + trs[i]) / period
    return float(atr_val)