import random
from typing import Any

from tenacity import retry, stop_after_attempt, wait_fixed

from .config import settings

try:
    import MetaTrader5 as mt5  # type: ignore
except Exception:
    mt5 = None


class MT5Adapter:
    def __init__(self) -> None:
        self.connected = False
        self.last_error: str | None = None

    @retry(wait=wait_fixed(2), stop=stop_after_attempt(3), reraise=True)
    def ensure_connection(self) -> None:
        if mt5 is None:
            self.last_error = "MetaTrader5 python package not available in this runtime"
            self.connected = False
            if not settings.mt_fallback_mode:
                raise RuntimeError(self.last_error)
            return
        if not mt5.initialize(path=settings.mt_terminal_exe, login=settings.mt_login, password=settings.mt_password, server=settings.mt_server):
            self.last_error = f"mt5 initialize failed: {mt5.last_error()}"
            self.connected = False
            if not settings.mt_fallback_mode:
                raise RuntimeError(self.last_error)
            return
        self.connected = True
        self.last_error = None

    def account(self) -> dict[str, Any]:
        self.ensure_connection()
        if mt5 is None or not self.connected:
            return {
                "balance": 0.0,
                "equity": 0.0,
                "margin": 0.0,
                "freeMargin": 0.0,
                "mode": "FALLBACK",
                "warning": self.last_error,
            }
        info = mt5.account_info()
        if info is None:
            raise RuntimeError(f"mt5 account_info failed: {mt5.last_error()}")
        return {
            "balance": info.balance,
            "equity": info.equity,
            "margin": info.margin,
            "freeMargin": info.margin_free,
            "mode": "LIVE",
        }

    def positions(self) -> list[dict[str, Any]]:
        self.ensure_connection()
        if mt5 is None or not self.connected:
            return []
        rows = mt5.positions_get()
        if rows is None:
            return []
        out = []
        for p in rows:
            out.append(
                {
                    "ticket": p.ticket,
                    "symbol": p.symbol,
                    "type": "BUY" if p.type == 0 else "SELL",
                    "volume": p.volume,
                    "openPrice": p.price_open,
                    "sl": p.sl,
                    "tp": p.tp,
                    "profit": p.profit,
                }
            )
        return out

    def place_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.ensure_connection()
        if mt5 is None or not self.connected:
            return {
                "ticket": random.randint(100000, 999999),
                "symbol": payload["symbol"],
                "type": payload["type"],
                "volume": payload["volume"],
                "openPrice": 0.0,
                "sl": payload["stopLoss"],
                "tp": payload["takeProfit"],
                "warning": self.last_error,
            }
        symbol = payload["symbol"]
        side = payload["type"].upper()
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            raise RuntimeError(f"symbol tick unavailable for {symbol}")
        order_type = mt5.ORDER_TYPE_BUY if side == "BUY" else mt5.ORDER_TYPE_SELL
        price = tick.ask if side == "BUY" else tick.bid
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": payload["volume"],
            "type": order_type,
            "price": price,
            "sl": payload["stopLoss"],
            "tp": payload["takeProfit"],
            "deviation": 20,
            "magic": 26042026,
            "comment": payload.get("comment", "adaptive-bot"),
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(request)
        if result is None:
            raise RuntimeError(f"order_send returned None: {mt5.last_error()}")
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            raise RuntimeError(f"order_send failed retcode={result.retcode}")
        return {
            "ticket": result.order,
            "symbol": symbol,
            "type": side,
            "volume": payload["volume"],
            "openPrice": price,
            "sl": payload["stopLoss"],
            "tp": payload["takeProfit"],
        }

    def close_position(self, ticket: int, volume: float | None) -> dict[str, Any]:
        self.ensure_connection()
        if mt5 is None or not self.connected:
            return {"closed": True, "ticket": ticket, "warning": self.last_error}
        positions = mt5.positions_get(ticket=ticket)
        if not positions:
            return {"closed": False, "ticket": ticket, "error": "position not found"}
        pos = positions[0]
        symbol = pos.symbol
        side_close = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            raise RuntimeError(f"symbol tick unavailable for {symbol}")
        price = tick.bid if side_close == mt5.ORDER_TYPE_SELL else tick.ask
        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": volume or pos.volume,
            "type": side_close,
            "position": ticket,
            "price": price,
            "deviation": 20,
            "magic": 26042026,
            "comment": "adaptive-close",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(req)
        if result is None:
            raise RuntimeError(f"close order_send returned None: {mt5.last_error()}")
        ok = result.retcode == mt5.TRADE_RETCODE_DONE
        return {"closed": ok, "ticket": ticket, "retcode": result.retcode}


adapter = MT5Adapter()
