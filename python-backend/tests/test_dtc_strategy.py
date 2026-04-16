from app.strategy.dtc import compute_levels, trend_shift_signal


def test_trend_shift_signal_parity():
    assert trend_shift_signal(False, True, True, False) == "BUY"
    assert trend_shift_signal(True, False, False, True) == "SELL"
    assert trend_shift_signal(True, False, True, False) is None


def test_long_short_level_formulas():
    class P:
        stop_loss_pct = 0.25
        tp1_multiplier = 1.0
        tp2_multiplier = 2.0
        tp3_multiplier = 3.0
        tp4_multiplier = 4.0

    price = 2000.0
    buy = compute_levels("BUY", price, P())
    sell = compute_levels("SELL", price, P())
    assert buy["sl"] < price and buy["tp1"] > price and buy["tp4"] > buy["tp1"]
    assert sell["sl"] > price and sell["tp1"] < price and sell["tp4"] < sell["tp1"]
