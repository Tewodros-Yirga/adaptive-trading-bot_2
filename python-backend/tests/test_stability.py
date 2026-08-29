from app.services.adaptation import _tiny_step


def test_tiny_step_is_clipped_by_percent():
    current = 100.0
    next_value, delta = _tiny_step(current=current, target_delta=20.0, max_change_pct=0.3)
    assert round(delta, 6) == 0.3
    assert round(next_value, 6) == 100.3


def test_tiny_step_allows_small_changes():
    current = 100.0
    next_value, delta = _tiny_step(current=current, target_delta=0.05, max_change_pct=0.3)
    assert round(delta, 6) == 0.05
    assert round(next_value, 6) == 100.05
