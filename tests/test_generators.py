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


def test_art_params_whitelisted():
    for b in range(4):
        for q in art_params.generate(b, 8, "t"):
            assert art_params.validate_params(q["params"])
    assert art_params.clean_word("<script>") is None
    assert art_params.classify_word("a Galaxy") == "cosmic"


def test_kinesthetic_schedule():
    t = kinesthetic.generate(2, 42, "t")
    assert len(t) == 42 and t[0]["type"] == "reaction" and t[1]["type"] == "target"
