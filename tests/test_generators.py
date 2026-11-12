from fractions import Fraction

from app.core.hashrng import HashRng
from app.generators import BUCKETS, arithmetic, art_params, kinesthetic, pattern
from app.services.content_orchestrator import build_procedural


def test_rng_is_deterministic_and_seed_sensitive():
    a, b = HashRng("x", 1), HashRng("x", 1)
    assert [a.randint(0, 99) for _ in range(20)] == [b.randint(0, 99) for _ in range(20)]
    assert [HashRng("x", 2).randint(0, 99) for _ in range(5)] != [HashRng("x", 1).randint(0, 99) for _ in range(5)]


def test_daily_pools_identical_for_everyone():
    assert build_procedural("2026-11-03") == build_procedural("2026-11-03")
    assert build_procedural("2026-11-03") != build_procedural("2026-11-04")


def _safe_eval(prompt: str) -> Fraction:
    expr = prompt.replace("×", "*").replace("÷", "/").replace("−", "-")
    import ast

    def ev(n):
        if isinstance(n, ast.Constant):
            return Fraction(n.value)
        if isinstance(n, ast.UnaryOp):
            return -ev(n.operand)
        a, b = ev(n.left), ev(n.right)
        return {ast.Add: a + b, ast.Sub: a - b, ast.Mult: a * b}.get(type(n.op)) if not isinstance(n.op, ast.Div) else a / b
    return ev(ast.parse(expr, mode="eval").body)


def test_arithmetic_answers_match_independent_evaluation():
    for b in range(4):
        for q in arithmetic.generate(b, 120, "t"):
            if q["type"] == "calc":
                assert _safe_eval(q["prompt"]) == q["answer"], q
            if q["type"] == "missing_op":
                assert q["answer"] in q["options"]
            assert q["id"].startswith(f"ar{b}-")


def test_pattern_options_valid():
    for b in range(4):
        for q in pattern.generate(b, 200, "t"):
            assert len(q["options"]) == 4 and len({str(o) for o in q["options"]}) == 4
            assert 0 <= q["answer"] < 4


def test_cycle_answer_is_the_cycle_not_a_guess():
    """The blueprint's hand-written example had the wrong key (rotation of 3 returns to the start)."""
    rng = HashRng("cyc")
    for _ in range(100):
        q = pattern._cycle(rng, 0)          # 3 slots, rotate by 1
        shown = q["display"][:-1]
        assert q["options"][q["answer"]] == shown[0]


def test_art_params_scenes_are_whitelisted():
    for b in range(4):
        items = art_params.generate(b, 8, "t")
        assert items
        for q in items:
            assert q["type"] == "scene"
            assert q["layout"] in art_params.LAYOUTS and q["palette"] in art_params.PALETTES
            assert 1 <= len(q["layers"]) <= 4
            assert sum(1 for l in q["layers"] if l["role"] == "anchor") == 1
            for l in q["layers"]:
                assert l["glyph"] in art_params.GLYPHS
                assert 1 <= l["count"] <= 12 and 0.05 <= l["size"] <= 1.0
    assert any(q["readings"] for q in art_params.generate(0, 8, "t"))   # curated recognizable scenes
    assert art_params.clean_word("<script>") is None
    assert art_params.classify_word("a Galaxy") == "cosmic"


def test_validate_scene_rejects_bad_items():
    base = {"concept": "mountains", "layout": "row", "palette": "mono",
            "hint": "Squint and look again.", "prompt": "What do you see?",
            "readings": {"structural": ["peaks", "ridge", "wall", "spire"],
                         "cosmic": ["dunes", "waves", "horizon", "drift"],
                         "figure": ["teeth", "crowns", "jaws", "spikes"]},
            "layers": [{"glyph": "triangle", "role": "anchor", "count": 4, "size": 0.5, "scale": 1, "rotation": 0, "seed": 1}]}
    assert art_params.validate_scene_item(base, 0, "easy", 0) is not None
    two_anchor = {**base, "layers": base["layers"] + [{"glyph": "circle", "role": "anchor", "count": 1, "size": 0.3}]}
    assert art_params.validate_scene_item(two_anchor, 0, "easy", 0) is None
    bad_glyph = {**base, "layers": [{**base["layers"][0], "glyph": "smiley"}]}
    assert art_params.validate_scene_item(bad_glyph, 0, "easy", 0) is None
    naming_hint = {**base, "hint": "Do you see the peaks and dunes?"}   # names readings
    assert art_params.validate_scene_item(naming_hint, 0, "easy", 0) is None
    thin = {**base, "readings": {"structural": ["a", "b", "c", "d"]}}   # <3 lenses
    assert art_params.validate_scene_item(thin, 0, "easy", 0) is None


def test_kinesthetic_schedule():
    t = kinesthetic.generate(2, 42, "t")
    assert len(t) == 42 and t[0]["type"] == "reaction" and t[1]["type"] == "target"
