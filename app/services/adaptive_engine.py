"""Pure functions, no I/O: easy to unit-test.

Each pillar has a continuous difficulty d in [0, 1]. level = d * 3 maps onto the four buckets
(0 easy, 1 medium, 2 hard, 3 expert). Fractional levels mix two neighbouring buckets, easiest first.
"""
import math

TARGET = 0.70        # the "productive struggle" score we steer towards
ETA = 0.25           # Auto-mode learning rate
MANUAL_WEIGHT = 0.30  # manual picks nudge the engine, they don't drive it
D_MIN, D_MAX = 0.03, 1.0
PICK_LEVEL = {"easy": 0.0, "medium": 1.0, "hard": 2.0}


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def level_from_d(d: float) -> float:
    return clamp(d, 0.0, 1.0) * 3.0


def slider_to_level(s: float) -> float:
    """Practice slider: 0 easy, 0.5 medium, 1 hard (expert is Auto-only)."""
    return clamp(s, 0.0, 1.0) * 2.0


def plan_buckets(n: int, level: float) -> list[int]:
    """Bucket index for each of n questions: easy-to-hard, monotone.
    level 0.5 with n=10 -> 5 easy then 5 medium (the spec's own example)."""
    level = clamp(level, 0.0, 3.0)
    k = min(int(math.floor(level)), 3)
    f = level - k if k < 3 else 0.0
    hi = int(round(n * f))
    return [k] * (n - hi) + [min(k + 1, 3)] * hi


def decay_for_absence(d: float, days_away: int) -> float:
    """Missing days eases you back in, but never erases more than 25% of d (or 0.15 absolute)."""
    if days_away <= 1:
        return d
    loss = min(0.03 * (days_away - 1), 0.15, d * 0.25)
    return round(max(d - loss, min(d, D_MIN)), 4)


def _consecutive(history: list[str], pick: str) -> int:
    n = 0
    for h in reversed(history):
        if h != pick:
            break
        n += 1
    return n


def update_difficulty(d: float, perf: float, pick: str, history: list[str]) -> float:
    """perf in [0,1]. pick in auto|easy|medium|hard. history = recent picks INCLUDING this one."""
    if pick == "auto":
        d += ETA * (perf - TARGET)
    else:
        step = ETA * MANUAL_WEIGHT * (perf - TARGET)
        if pick == "easy":
            step = min(step, 0.0)       # farming Easy never raises your difficulty
        d += step
        run = _consecutive(history, pick)
        if pick == "hard" and run >= 3 and perf >= 0.6:
            d += 0.05                   # three strong Hard days in a row: the engine follows you up
        if pick == "easy" and run >= 3:
            d -= min(0.04 * (run - 2), 0.12)  # 3-5 Easy days in a row: it settles down
    return round(clamp(d, D_MIN, D_MAX), 4)
