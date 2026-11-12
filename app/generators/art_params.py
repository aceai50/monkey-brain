"""Art & Perception: recognizable-but-ambiguous *scenes* composed from a fixed glyph vocabulary.

The client draws from a whitelist of glyph ids + a layout + numeric params. No formula strings,
no free coordinates, no eval. Parametric curves (rose/lissajous/...) are kept as glyphs so the
abstract look survives, but new content is composed to *evoke* something (mountains, yin-yang,
a face) while still reading multiple ways. Readings/ambiguity/concept are server-only.
"""
import re

from app.core.hashrng import HashRng

# Glyph vocabulary the renderer knows. Primitives, composites, and the parametric curves.
GLYPHS = (
    "triangle", "circle", "arc", "line", "star", "spiral", "wave", "blob", "rect", "ring", "dot", "polygon",
    "yinyang", "sun", "mountain", "skyline", "tree", "face", "boat", "bird", "fish", "heart", "eye", "arrow",
    "rose", "lissajous", "spiro", "maurer", "super", "harm",
)
LAYOUTS = ("row", "stack", "radial", "split", "grid", "center", "arc")
ROLES = ("anchor", "accent", "echo")
PALETTES = ("mono", "duotone")

LENS = ("organic", "structural", "cosmic", "figure")

ARCHETYPES = {
    "organic": ("The Pattern Gardener", "You read the shape as something growing or alive. You tend to see systems as ecosystems."),
    "structural": ("The Systems Builder", "You read the shape as built or mechanical. You look for the load-bearing parts first."),
    "cosmic": ("The Horizon Chaser", "You read the shape as vast or in motion. You zoom out before you zoom in."),
    "figure": ("The People Reader", "You read the shape as a face, body or creature. Your first instinct is to look for intent."),
    "wildcard": ("The Wildcard", "Your answer didn't fit a box, which is the point of the exercise."),
}
KEYWORDS = {
    "organic": {"flower", "petal", "leaf", "plant", "tree", "rose", "lotus", "butterfly", "wing", "fish",
                "shell", "bloom", "seed", "wave", "water", "sea", "cloud", "vine", "coral"},
    "structural": {"gear", "grid", "machine", "cube", "tower", "bridge", "network", "circuit", "horn",
                   "arrow", "web", "knot", "lattice", "clock", "city", "building", "mountain", "roof", "sail"},
    "cosmic": {"galaxy", "star", "planet", "orbit", "spiral", "universe", "vortex", "nebula", "atom",
               "wave", "ripple", "infinity", "sun", "moon", "sky", "horizon", "eye", "yin", "yang"},
    "figure": {"face", "person", "eye", "bird", "elephant", "cat", "dog", "mask", "skull", "hand",
               "owl", "bear", "human", "monster", "profile", "smile", "boat", "ship"},
}
WORD_RE = re.compile(r"^[A-Za-z][A-Za-z '\-]{0,59}$")
HINT_RE = re.compile(r"^[A-Za-z][A-Za-z ,'\-?.!0-9]{9,159}$")
STOP = {"a", "an", "the", "of", "to", "in", "is", "it", "and", "or", "that", "this", "these",
        "are", "as", "at", "on", "for", "with", "you", "your", "something", "else", "entirely",
        "look", "again", "see", "do", "does"}

# Perspective-openers for the offline curve scenes (never name a concrete answer).
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
    if not WORD_RE.match(w) or len(w.split()) > 6:
        return None
    return w.lower()


def classify_word(word: str) -> str:
    tokens = set(re.findall(r"[a-z]+", word.lower()))
    for key, kws in KEYWORDS.items():
        if tokens & kws:
            return key
    return "wildcard"


def _clean_readings(raw_readings) -> dict:
    out = {}
    for lens in LENS:
        ws = (raw_readings or {}).get(lens) or []
        ws = [str(x).strip().lower() for x in ws if re.fullmatch(r"[A-Za-z][A-Za-z '\-]{0,30}", str(x).strip())]
        ws = list(dict.fromkeys(ws))
        if len(ws) >= 4:
            out[lens] = ws
    return out


def _hint_ok(hint: str, readings: dict) -> bool:
    if not HINT_RE.match(hint):
        return False
    words = {w for w in re.findall(r"[a-z]+", hint.lower()) if w not in STOP}
    flat = {w for ws in readings.values() for phrase in ws for w in phrase.split() if w not in STOP}
    return not (words & flat)


def _layer(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    glyph = str(raw.get("glyph", "")).strip().lower()
    role = str(raw.get("role", "anchor")).strip().lower()
    if glyph not in GLYPHS or role not in ROLES:
        return None
    try:
        count = int(raw.get("count", 1))
        size = float(raw.get("size", 0.6))
        scale = float(raw.get("scale", 1.0))
        rotation = int(raw.get("rotation", 0)) % 360
        seed = int(raw.get("seed", 0))
    except (TypeError, ValueError):
        return None
    if not 1 <= count <= 12 or not 0.05 <= size <= 1.0 or not 0.1 <= scale <= 2.0:
        return None
    return {"glyph": glyph, "role": role, "count": count, "size": round(size, 3),
            "scale": round(scale, 3), "rotation": rotation, "seed": seed % 100000}


def validate_scene_item(raw, bucket: int, difficulty: str, key: int) -> dict | None:
    """Gemini scene output -> question dict. Enforces one recognizable anchor + multi-lens ambiguity."""
    try:
        layout = str(raw.get("layout", "")).strip().lower()
        palette = str(raw.get("palette", "mono")).strip().lower()
        if layout not in LAYOUTS or palette not in PALETTES:
            return None
        layers_raw = raw.get("layers") or []
        if not 1 <= len(layers_raw) <= 4:
            return None
        layers = [_layer(x) for x in layers_raw]
        if any(x is None for x in layers):
            return None
        if sum(1 for x in layers if x["role"] == "anchor") != 1:   # exactly one recognizable anchor
            return None
        readings = _clean_readings(raw.get("readings"))
        if len(readings) < 3:
            return None
        hint = str(raw.get("hint", "")).strip()
        if not _hint_ok(hint, readings):
            return None
        concept = " ".join(str(raw.get("concept", "")).split())[:120]
        if not concept:
            return None
        return {
            "id": f"as{bucket}-{key}", "type": "scene", "difficulty": difficulty,
            "concept": concept, "palette": palette, "layout": layout, "layers": layers,
            "prompt": str(raw.get("prompt") or "What is the first thing you see?")[:120],
            "hint": hint, "readings": readings,
            "ambiguity_note": " ".join(str(raw.get("ambiguity_note", "")).split())[:200],
            "answer": None,
        }
    except (TypeError, ValueError):
        return None


def validate_art_item(raw, bucket: int, key: int, difficulty: str = "easy") -> dict | None:
    """Unified entry point: a scene if it carries layers, else a legacy parametric-curve item."""
    if isinstance(raw, dict) and raw.get("layers"):
        return validate_scene_item(raw, bucket, difficulty, key)
    return _validate_legacy_curve(raw, bucket, key)


def _validate_legacy_curve(raw, bucket: int, key: int) -> dict | None:
    try:
        p = {
            "kind": str(raw.get("kind", "")).lower(),
            "a": float(raw.get("a", 0)), "b": float(raw.get("b", 1)), "c": float(raw.get("c", 0)),
            "d": float(raw.get("d", 0)), "e": float(raw.get("e", 0)), "phase": float(raw.get("phase", 0)),
            "layers": max(1, min(5, int(raw.get("layers", 1)))), "rotate": int(raw.get("rotate", 0)) % 360,
        }
        if not validate_params(p) or p["b"] == 0:
            return None
        readings = _clean_readings(raw.get("readings"))
        if len(readings) < 3:
            return None
        hint = str(raw.get("hint", "")).strip()
        if not _hint_ok(hint, readings):
            return None
        return {
            "id": f"ai{bucket}-{key}", "type": "illusion",
            "prompt": str(raw.get("prompt") or "What is the first thing you see?")[:120],
            "hint": hint, "params": p, "readings": readings,
            "ambiguity_note": str(raw.get("ambiguity_note", "")).strip()[:200], "answer": None,
        }
    except (TypeError, ValueError):
        return None


def validate_params(p: dict) -> bool:
    if p.get("kind") not in ("rose", "lissajous", "spiro", "maurer", "super", "harm"):
        return False
    for k in ("a", "b", "c", "d", "e", "phase"):
        v = p.get(k, 0)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not -50 <= v <= 50:
            return False
    if not 1 <= int(p.get("layers", 1)) <= 5:
        return False
    return -360 <= p.get("rotate", 0) <= 360


# ---------------------------------------------------------------- offline bank
def _scene(concept, layout, palette, hint, note, layers, readings):
    return {"concept": concept, "layout": layout, "palette": palette, "hint": hint,
            "ambiguity_note": note, "prompt": "What is the first thing you see?",
            "layers": [{"glyph": g, "role": r, "count": c, "size": s, "scale": sc, "rotation": rot, "seed": 0}
                       for (g, r, c, s, sc, rot) in layers],
            "readings": readings}


# (glyph, role, count, size, scale, rotation)
CURATED = [
    _scene("a mountain range at dusk", "row", "mono",
           "Squint and let the peaks blur. Are these rocks, or something else entirely?",
           "Viewers split between mountains, waves and teeth.",
           [("triangle", "anchor", 4, 0.5, 1.0, 0), ("circle", "accent", 1, 0.16, 1.0, 0)],
           {"structural": ["mountain range", "peaks", "skyline", "pyramids"],
            "cosmic": ["waves", "horizon", "sun setting", "dunes"],
            "figure": ["teeth", "crowns", "a sleeping jaw", "clenched grin"]}),
    _scene("a yin-yang seen through fog", "center", "duotone",
           "Watch the curve that divides it. Is it one shape or two holding each other?",
           "Readers see duality, a face, a wave, or a pair of fish.",
           [("yinyang", "anchor", 1, 0.8, 1.0, 0)],
           {"cosmic": ["yin and yang", "duality", "the moon", "orbit"],
            "figure": ["a face", "two heads", "eyes", "profiles"],
            "organic": ["two fish", "a comma pair", "cells dividing"],
            "structural": ["a gear", "a hinge", "a lock"]}),
    _scene("sun over open water", "stack", "duotone",
           "Is the round thing above the lines, or reflected below them?",
           "Splits between a sunrise, an eye, and a coin on a table.",
           [("circle", "anchor", 1, 0.4, 1.0, 0), ("wave", "accent", 3, 0.5, 1.0, 0)],
           {"cosmic": ["sunrise", "the sun", "a planet", "dusk"],
            "figure": ["an eye", "a face over water", "a staring pupil"],
            "structural": ["a coin", "a clock", "a porthole"],
            "organic": ["a yolk", "a jellyfish", "a floating egg"]}),
    _scene("a city skyline at night", "row", "mono",
           "Some of these are buildings. Are all of them?",
           "Readers see a skyline, a graph, a barcode, or a sound equaliser.",
           [("rect", "anchor", 7, 0.5, 1.0, 0), ("dot", "echo", 5, 0.06, 1.0, 0)],
           {"structural": ["city skyline", "buildings", "a wall", "towers"],
            "cosmic": ["a graph", "a heartbeat", "a spectrum", "an equaliser"],
            "organic": ["a barcode", "teeth", "keys", "a comb"]}),
    _scene("a face hiding in a landscape", "center", "mono",
           "Find the two points that are close together. What are they looking at?",
           "Oscillates between a face, a house, and a pair of headlights.",
           [("circle", "anchor", 2, 0.14, 1.0, 0), ("arc", "accent", 1, 0.5, 1.0, 180),
            ("triangle", "echo", 1, 0.4, 1.0, 0)],
           {"figure": ["a face", "two eyes", "a profile", "a mask"],
            "structural": ["a house", "a window", "headlights", "a robot"],
            "cosmic": ["two moons", "planets", "a smile in the sky"]}),
    _scene("rolling waves under a storm", "stack", "mono",
           "Are these lines of water, or lines of something else?",
           "Readers see waves, hills, hair, or cables.",
           [("wave", "anchor", 4, 0.6, 1.0, 0), ("spiral", "echo", 1, 0.2, 1.0, 0)],
           {"cosmic": ["waves", "the sea", "a storm", "currents"],
            "organic": ["hair", "a vine", "caterpillar", "a spine"],
            "structural": ["cables", "coils", "a spring", "rooftiles"]}),
]


def _curve_scene(bucket: int, k: int, rng: HashRng) -> dict:
    glyph = rng.choice(("rose", "lissajous", "spiro", "maurer", "super", "harm"))
    return {
        "id": f"ap{bucket}-{k}", "type": "scene", "difficulty": "easy",
        "concept": "an abstract curve", "palette": "mono", "layout": "center",
        "prompt": "What is the first thing you see?", "hint": HINTS[k % len(HINTS)],
        "layers": [{"glyph": glyph, "role": "anchor", "count": 1, "size": 0.9,
                    "scale": 1.0, "rotation": rng.randint(0, 359), "seed": rng.randint(0, 99999)}],
        "readings": {}, "ambiguity_note": "", "answer": None,
    }


def _curated_item(c: dict, bucket: int, k: int) -> dict:
    return {"id": f"as{bucket}-{k}", "type": "scene", "difficulty": "easy", "concept": c["concept"],
            "palette": c["palette"], "layout": c["layout"], "layers": c["layers"], "prompt": c["prompt"],
            "hint": c["hint"], "readings": c["readings"], "ambiguity_note": c["ambiguity_note"], "answer": None}


def generate(bucket: int, count: int, seed: str) -> list[dict]:
    """Offline pool: a blend of curated recognizable scenes and parametric-curve scenes."""
    rng = HashRng("art", bucket, seed)
    out = []
    for k in range(count):
        if k % 2 == 0:
            out.append(_curated_item(CURATED[k % len(CURATED)], bucket, k))
        else:
            out.append(_curve_scene(bucket, k, rng))
    return out
