import json
from math import sqrt

from sqlalchemy.orm import Session

from .. import crud
from ..strategy.dtc import DEFAULT_PARAMS
from .runtime_settings import get_learning_settings


def _clamp(value: float, min_value: float, max_value: float) -> float:
    return max(min_value, min(max_value, value))


def _tiny_step(current: float, target_delta: float, max_change_pct: float) -> tuple[float, float]:
    max_abs_delta = abs(current) * (max_change_pct / 100.0)
    bounded_delta = _clamp(target_delta, -max_abs_delta, max_abs_delta)
    return current + bounded_delta, bounded_delta


def run_adaptation(db: Session, window: int = 20) -> dict:
    learning = get_learning_settings(db)
    params = crud.get_current_params(db) or DEFAULT_PARAMS.copy()
    all_closed = crud.get_closed_trades(db, 100000)
    last_adapt_count_raw = crud.get_setting(db, "last_adapt_closed_count")
    last_adapt_count = int(last_adapt_count_raw) if last_adapt_count_raw else 0
    if len(all_closed) - last_adapt_count < learning["adaptation_cooldown_trades"]:
        return {"skipped": True, "reason": "Adaptation cooldown active"}

    trades = all_closed[:window]
    if len(trades) < learning["adaptation_min_closed_trades"]:
        return {"skipped": True, "reason": f"Only {len(trades)} closed trades"}

    wins = [t for t in trades if t.result == "WIN"]
    losses = [t for t in trades if t.result == "LOSS"]
    win_rate = len(wins) / len(trades)
    gross_profit = sum(t.pnl or 0 for t in wins)
    gross_loss = abs(sum(t.pnl or 0 for t in losses)) or 1e-6
    profit_factor = gross_profit / gross_loss

    atr_values = [t.atr_at_entry for t in trades if t.atr_at_entry]
    avg_atr = sum(atr_values) / len(atr_values) if atr_values else None

    confidence = abs(win_rate - 0.5) + (max(0.0, profit_factor - 1.0) * 0.05)
    if confidence < learning["adaptation_confidence_threshold"]:
        return {"skipped": True, "reason": f"Confidence {confidence:.4f} below threshold"}

    lr = learning["adaptation_lr"]
    actions = []
    deltas = []
    new_params = params.copy()

    sl_signal = (0.5 - win_rate) * 0.2
    sl_target_delta = sl_signal * lr * 100
    new_sl, sl_delta = _tiny_step(float(params["stop_loss_pct"]), sl_target_delta, learning["adaptation_max_change_pct"])
    new_sl = _clamp(new_sl, params["min_stop_loss_pct"], params["max_stop_loss_pct"])
    if abs(new_sl - params["stop_loss_pct"]) > 0:
        new_params["stop_loss_pct"] = round(new_sl, 5)
        deltas.append(sl_delta)
        actions.append({"rule": "tiny_sl_update", "detail": f"SL {params['stop_loss_pct']} -> {new_params['stop_loss_pct']}"})

    tp_signal = (profit_factor - 1.0) * 0.02
    tp_target_delta = tp_signal * lr * 100
    for key in ["tp1_multiplier", "tp2_multiplier", "tp3_multiplier", "tp4_multiplier"]:
        current = float(params[key])
        next_value, delta = _tiny_step(current, tp_target_delta, learning["adaptation_max_change_pct"])
        next_value = _clamp(next_value, params["min_tp_multiplier"], params["max_tp_multiplier"])
        if abs(next_value - current) > 0:
            new_params[key] = round(next_value, 5)
            deltas.append(delta)

    if avg_atr:
        atr_signal = -0.01 if profit_factor > 1.0 else 0.01
        for key in ["ema_1", "ema_2", "ema_3", "ema_4", "ema_5", "ema_6"]:
            current = float(params[key])
            next_value, delta = _tiny_step(current, atr_signal * lr * current, learning["adaptation_max_change_pct"])
            if key == "ema_1":
                next_value = _clamp(next_value, params["min_ema_1"], params["max_ema_1"])
            if key == "ema_6":
                next_value = _clamp(next_value, params["min_ema_6"], params["max_ema_6"])
            next_value = round(next_value)
            if next_value != int(current):
                new_params[key] = int(next_value)
                deltas.append(delta)

    delta_magnitude = sqrt(sum(d * d for d in deltas)) if deltas else 0.0
    if not actions and not deltas:
        actions.append({"rule": "no_op", "detail": "No bounded change passed confidence and clip checks"})

    version_row = crud.save_params(
        db,
        new_params,
        reason="; ".join(a["detail"] for a in actions),
        trigger="AUTO",
        confidence_score=confidence,
        delta_magnitude=delta_magnitude,
    )
    crud.log_adaptation(
        db,
        {
            "trades_evaluated": len(trades),
            "win_rate": round(win_rate * 100, 2),
            "profit_factor": round(profit_factor, 3),
            "avg_atr": avg_atr,
            "actions_taken": json.dumps(actions),
            "new_params_version": version_row.version,
            "confidence_score": confidence,
            "delta_magnitude": delta_magnitude,
            "rollback_triggered": 0,
        },
    )
    crud.set_setting(db, "last_adapt_closed_count", str(len(all_closed)))
    return {
        "trades_evaluated": len(trades),
        "win_rate": round(win_rate * 100, 2),
        "profit_factor": round(profit_factor, 3),
        "confidence": round(confidence, 6),
        "actions": actions,
        "new_params_version": version_row.version,
        "new_params": new_params,
    }
