"""Parametric multi-stable curves. The client draws from a FIXED whitelist of curve families with
numeric parameters. No formula string is ever sent or eval'd. Responses are for fun, not graded."""
import re

from app.core.hashrng import HashRng

KINDS = ("rose", "lissajous", "spiro")

ARCHETYPES = {
    "organic": ("The Pattern Gardener", "You read the shape as something growing or alive. You tend to see systems as ecosystems."),
    "structural": ("The Systems Builder", "You read the shape as built or mechanical. You look for the load-bearing parts first."),
    "cosmic": ("The Horizon Chaser", "You read the shape as vast or in motion. You zoom out before you zoom in."),
    "figure": ("The People Reader", "You read the shape as a face, body or creature. Your first instinct is to look for intent."),
    "wildcard": ("The Wildcard", "Your answer didn't fit a box, which is the point of the exercise."),
}
KEYWORDS = {
    "organic": {"flower", "petal", "leaf", "plant", "tree", "rose", "lotus", "butterfly", "wing", "fish", "shell", "bloom", "seed"},
    "structural": {"gear", "grid", "machine", "cube", "tower", "bridge", "network", "circuit", "horn", "arrow", "web", "knot", "lattice", "clock"},
    "cosmic": {"galaxy", "star", "planet", "orbit", "spiral", "universe", "vortex", "nebula", "atom", "wave", "ripple", "infinity"},
    "figure": {"face", "person", "eye", "bird", "elephant", "cat", "dog", "mask", "skull", "hand", "owl", "bear", "human", "monster"},
}
WORD_RE = re.compile(r"^[A-Za-z][A-Za-z '\-]{0,23}$")

# Perspective-openers: each nudges a different way of looking, never naming a concrete answer.
# Rotated across the set so all four lenses get represented in every session.
HINTS = [
    "Try squinting at it. Does the outline suggest something that grows, or something that was built?",
    "Zoom out in your mind. Is this something you'd find in the sky, or in a machine?",
    "Follow the lines with your eyes. Does the shape feel like it's moving, or locked still?",
    "Look for faces. Does anything in the curves seem to be looking back at you?",
]


def clean_word(w) -> str | None:
    if not isinstance(w, str):
        return None
    w = " ".join(w.strip().split())
    return w.lower() if WORD_RE.match(w) else None


def classify_word(word: str) -> str:
    tokens = set(re.findall(r"[a-z]+", word.lower()))
    for key, kws in KEYWORDS.items():
        if tokens & kws:
            return key
    return "wildcard"


def validate_params(p: dict) -> bool:
    try:
        return p["kind"] in KINDS and all(isinstance(p[k], (int, float)) and -50 <= p[k] <= 50 for k in ("a", "b", "c", "phase"))
    except KeyError:
        return False


def generate(bucket: int, count: int, seed: str) -> list[dict]:
    rng = HashRng("art", bucket, seed)
    offset = rng.randint(0, len(HINTS) - 1)
    out = []
    for k in range(count):
        kind = rng.choice(KINDS)
        if kind == "rose":      # r = cos((a/b)·θ), petals
            a, b, c = rng.randint(2, 5 + bucket * 2), rng.randint(1, 2 + bucket), 0
        elif kind == "lissajous":  # x = sin(a·t + phase), y = sin(b·t)
            a, b, c = rng.randint(1, 3 + bucket * 2), rng.randint(2, 4 + bucket * 2), 0
        else:                   # hypotrochoid with R=a, r=b, d=c
            a, b, c = rng.randint(5, 9), rng.randint(2, 4), rng.randint(2, 6)
        p = {"kind": kind, "a": a, "b": b, "c": c, "phase": round(rng.random() * 3.14, 2),
             "layers": 1 + bucket, "rotate": rng.randint(0, 359)}
        out.append({"id": f"ap{bucket}-{k}", "type": "illusion", "prompt": "What is the first thing you see?",
                    "hint": HINTS[(k + offset) % len(HINTS)], "params": p, "answer": None})
    return out
