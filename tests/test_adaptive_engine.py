from app.services.adaptive_engine import (
    decay_for_absence, level_from_d, plan_buckets, slider_to_level, update_difficulty,
)


def test_spec_example_half_easy_half_medium():
    assert plan_buckets(10, 0.5) == [0] * 5 + [1] * 5


def test_plan_is_monotone_and_pure_at_anchors():
    for lvl in (0, 0.3, 1, 1.7, 2.4, 3):
        p = plan_buckets(24, lvl)
        assert p == sorted(p) and len(p) == 24
    assert set(plan_buckets(8, 1.0)) == {1}
    assert set(plan_buckets(8, 3.0)) == {3}


def test_slider_maps_to_easy_medium_hard():
    assert slider_to_level(0) == 0 and slider_to_level(0.5) == 1 and slider_to_level(1) == 2


def test_good_performance_raises_poor_lowers():
    assert update_difficulty(0.4, 1.0, "auto", ["auto"]) > 0.4
    assert update_difficulty(0.4, 0.2, "auto", ["auto"]) < 0.4


def test_absence_eases_but_is_bounded():
    assert decay_for_absence(0.5, 1) == 0.5
    assert decay_for_absence(0.5, 3) < 0.5
    assert decay_for_absence(0.5, 400) >= 0.5 * 0.75 - 1e-9


def test_three_hard_days_adapts_up_and_three_easy_days_down():
    base = 0.4
    assert update_difficulty(base, 0.9, "hard", ["hard"] * 3) > update_difficulty(base, 0.9, "hard", ["hard"])
    assert update_difficulty(base, 0.9, "easy", ["easy"] * 4) < base
    assert update_difficulty(base, 1.0, "easy", ["easy"]) <= base  # farming easy never raises d


def test_level_from_d():
    assert level_from_d(0) == 0 and level_from_d(1) == 3
