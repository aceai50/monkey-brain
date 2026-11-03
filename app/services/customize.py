"""Per-pillar question counts the player can customise (Customize page).

min/max/default per pillar. `max` is the full generated pool for that pillar, so the player can
never ask for more questions than exist. Fewer questions also means a lower volume multiplier,
so picking 5 easy questions can't out-score a full set on the leaderboard.
"""
PILLARS = ("arithmetic", "critical", "pattern", "art", "kinesthetic")
LABELS = {
    "arithmetic": "Arithmetic", "critical": "Critical thinking", "pattern": "Pattern recognition",
    "art": "Art and perspective", "kinesthetic": "Reflexes",
}
#            min  max  default
LIMITS = {
    "arithmetic": (5, 30, 15),
    "critical": (2, 8, 5),
    "pattern": (5, 20, 10),
    "art": (2, 6, 4),
    "kinesthetic": (8, 30, 16),
}
VOLUME_FLOOR = 0.5   # volume multiplier runs from 0.5 (fewest questions) ... to 1.0 (all of them)


def default_counts() -> dict[str, int]:
    return {p: LIMITS[p][2] for p in PILLARS}


def counts_for(user: dict) -> dict[str, int]:
    """The user's saved counts, clamped into range, with defaults for anything missing."""
    saved = user.get("question_counts") or {}
    out = {}
    for p in PILLARS:
        lo, hi, dflt = LIMITS[p]
        v = saved.get(p, dflt)
        out[p] = max(lo, min(hi, v)) if isinstance(v, int) and not isinstance(v, bool) else dflt
    return out


def validate_counts(raw: dict) -> dict[str, int]:
    """Strict: unknown pillars, non-integers and out-of-range values are rejected (ValueError)."""
    for k in raw:
        if k not in LIMITS:
            raise ValueError(f"Unknown pillar: {k}")
    out = default_counts()
    for p, v in raw.items():
        lo, hi, _ = LIMITS[p]
        if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
            raise ValueError(f"{LABELS[p]} must be a whole number from {lo} to {hi}")
        out[p] = v
    return out


def volume_weight(pillar: str, offered: int) -> float:
    hi = LIMITS[pillar][1]
    frac = max(0.0, min(1.0, offered / hi))
    return round(VOLUME_FLOOR + (1.0 - VOLUME_FLOOR) * frac, 4)
