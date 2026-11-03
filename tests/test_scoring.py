from types import SimpleNamespace as NS

from app.services import scoring


def test_arithmetic_penalises_guessing_and_caps():
    qs = [{"id": f"a{i}", "answer": i} for i in range(30)]
    all_right = scoring.score_questions("arithmetic", qs, {f"a{i}": i for i in range(30)})
    assert all_right["score"] == 100
    guessy = scoring.score_questions("arithmetic", qs, {f"a{i}": 999 for i in range(30)})
    assert guessy["score"] == 0


def test_unknown_ids_ignored():
    r = scoring.score_questions("arithmetic", [{"id": "a", "answer": 1}], {"zzz": 1})
    assert r["answered"] == 0


def test_level_weight_easy_less_than_expert():
    assert scoring.level_weight(0) == 0.5 and scoring.level_weight(3) == 1.0


def test_reaction_cheating_is_zeroed():
    qs = [{"id": f"k{i}", "type": "reaction", "delay_ms": 3000} for i in range(10)]
    fast = [NS(id=f"k{i}", rt_ms=50, outcome="hit") for i in range(10)]
    assert scoring.score_kinesthetic(qs, fast, 240000)["score"] == 0
    # 10 trials x 3s delay cannot fit in 5 seconds
    ok_rts = [NS(id=f"k{i}", rt_ms=250, outcome="hit") for i in range(10)]
    r = scoring.score_kinesthetic(qs, ok_rts, 5000)
    assert "impossible_timing" in r["flags"] and r["score"] == 0
    assert scoring.score_kinesthetic(qs, ok_rts, 60000)["score"] > 0


def test_art_archetype():
    qs = [{"id": "x"}, {"id": "y"}]
    r = scoring.score_art(qs, {"x": "galaxy", "y": "star"})
    assert r["archetype"]["key"] == "cosmic" and r["score"] == 100


def test_perf_blends_accuracy_with_completion():
    qs = [{"id": f"a{i}", "answer": i} for i in range(20)]
    # all 20 attempted, all right: perf 1.0
    full = scoring.score_questions("arithmetic", qs, {f"a{i}": i for i in range(20)})
    assert full["perf"] == 1.0 and full["completion"] == 1.0 and full["accuracy"] == 1.0
    # 5 attempted, all right: accuracy 1.0 but only a quarter of the set touched
    part = scoring.score_questions("arithmetic", qs, {f"a{i}": i for i in range(5)})
    assert part["accuracy"] == 1.0 and part["completion"] == 0.25 and 0.5 < part["perf"] < 1.0
    # 20 attempted, half wrong: accuracy suffers
    half = scoring.score_questions("arithmetic", qs, {f"a{i}": (i if i % 2 else 999) for i in range(20)})
    assert half["accuracy"] < 0.5 and half["perf"] < full["perf"]
    # nothing attempted: zero, no division by zero
    assert scoring.score_questions("arithmetic", qs, {})["perf"] == 0.0


def test_volume_multiplier_rewards_more_questions():
    from app.services.customize import LIMITS, validate_counts, volume_weight
    lo, hi, _ = LIMITS["arithmetic"]
    assert volume_weight("arithmetic", hi) == 1.0
    assert volume_weight("arithmetic", lo) < volume_weight("arithmetic", 15) < 1.0
    assert volume_weight("arithmetic", lo) >= 0.5
    import pytest
    with pytest.raises(ValueError):
        validate_counts({"arithmetic": hi + 1})
    with pytest.raises(ValueError):
        validate_counts({"arithmetic": 7.5})


def test_points_scale_with_both_multipliers():
    res = {"arithmetic": {"score": 80.0, "offered": 30}}
    easy_full = scoring.day_points(res, {"arithmetic": 0.0})
    hard_full = scoring.day_points(res, {"arithmetic": 2.0})
    hard_small = scoring.day_points({"arithmetic": {"score": 80.0, "offered": 5}}, {"arithmetic": 2.0})
    assert hard_full > easy_full > 0 and hard_small < hard_full
