"""Procedural arithmetic. Every answer is computed with exact Fractions: no LLM, no rounding."""
from fractions import Fraction

from app.core.hashrng import HashRng

SYM = {"+": "+", "-": "−", "*": "×", "/": "÷"}
PREC = {"+": 1, "-": 1, "*": 2, "/": 2}

# bucket -> (ops in expression, operand range, allowed ops, max |result|, allow negative result)
PARAMS = [
    (2, (2, 12), "+-*", 150, False),
    (3, (2, 20), "+-*/", 400, False),
    (4, (2, 30), "+-*/", 1500, True),
    (5, (2, 50), "+-*/", 5000, True),
]


def _build(rng: HashRng, n_ops: int, lo: int, hi: int, ops: str):
    if n_ops == 0:
        return rng.randint(lo, hi)
    k = rng.randint(0, n_ops - 1)
    return (rng.choice(ops), _build(rng, k, lo, hi, ops), _build(rng, n_ops - 1 - k, lo, hi, ops))


def evaluate(t) -> Fraction | None:
    """None if any division is by zero or inexact (we only ask integer-clean questions)."""
    if isinstance(t, int):
        return Fraction(t)
    op, l, r = t
    a, b = evaluate(l), evaluate(r)
    if a is None or b is None:
        return None
    if op == "+":
        return a + b
    if op == "-":
        return a - b
    if op == "*":
        return a * b
    if b == 0:
        return None
    q = a / b
    return q if q.denominator == 1 else None


def render(t, parent_prec: int = 0, is_right: bool = False) -> str:
    if isinstance(t, int):
        return str(t)
    op, l, r = t
    p = PREC[op]
    s = f"{render(l, p, False)} {SYM[op]} {render(r, p, True)}"
    return f"({s})" if (p < parent_prec or (p == parent_prec and is_right)) else s


def _calc(rng: HashRng, b: int) -> dict:
    n_ops, (lo, hi), ops, cap, neg = PARAMS[b]
    for _ in range(400):
        t = _build(rng, n_ops, lo, hi, ops)
        v = evaluate(t)
        if v is None or abs(v) > cap or (v < 0 and not neg):
            continue
        s = render(t)
        if "(" not in s and b >= 1 and rng.random() < 0.7:
            continue  # medium+ should usually exercise parentheses
        return {"type": "calc", "prompt": s, "options": None, "answer": int(v)}
    return {"type": "calc", "prompt": "6 + 4 × 3", "options": None, "answer": 18}


def _apply(o1: str, o2: str, a: int, b: int, c: int) -> Fraction | None:
    if o1 in "+-" and o2 in "*/":
        t = (o1, a, (o2, b, c))
    else:
        t = (o2, (o1, a, b), c)
    return evaluate(t)


def _missing_op(rng: HashRng, b: int) -> dict:
    _, (lo, hi), _, _, _ = PARAMS[b]
    syms = ["+", "-", "*", "/"]
    for _ in range(400):
        if b == 0:
            a, c, o = rng.randint(lo, hi), rng.randint(lo, hi), rng.choice(syms)
            r = evaluate((o, a, c))
            hits = [x for x in syms if evaluate((x, a, c)) == r]
            if r is None or len(hits) != 1:
                continue
            return {"type": "missing_op", "prompt": f"{a} ? {c} = {int(r)}",
                    "options": [SYM[x] for x in syms], "answer": SYM[o]}
        a, bb, c = rng.randint(lo, hi), rng.randint(lo, hi), rng.randint(lo, hi)
        o1, o2 = rng.choice(syms), rng.choice(syms)
        r = _apply(o1, o2, a, bb, c)
        if r is None:
            continue
        hits = [(x, y) for x in syms for y in syms if _apply(x, y, a, bb, c) == r]
        if len(hits) != 1:
            continue
        pairs = [f"{SYM[x]}  {SYM[y]}" for x in syms for y in syms]
        right = f"{SYM[o1]}  {SYM[o2]}"
        wrong = [p for p in pairs if p != right]
        rng.shuffle(wrong)
        opts = rng.shuffle([right] + wrong[:3])
        return {"type": "missing_op", "prompt": f"{a} ? {bb} ? {c} = {int(r)}", "options": opts, "answer": right}
    return {"type": "missing_op", "prompt": "8 ? 2 = 4", "options": [SYM[x] for x in syms], "answer": SYM["/"]}


def _countdown(rng: HashRng, b: int) -> dict:
    start = rng.randint(60, 200)
    k = rng.randint(3, 9) + b * 2
    n = rng.randint(3, 4 + b)
    if b < 2:
        return {"type": "countdown", "prompt": f"Start at {start}. Take away {k}, {n} times. What's left?",
                "options": None, "answer": start - k * n}
    j = rng.randint(1, k - 1)
    return {"type": "countdown",
            "prompt": f"Start at {start}. Take away {k}, then add {j}. Repeat {n} times. What's left?",
            "options": None, "answer": start + (j - k) * n}


def generate(bucket: int, count: int, seed: str) -> list[dict]:
    rng = HashRng("arithmetic", bucket, seed)
    out = []
    for k in range(count):
        kind = rng.weighted([("calc", 70), ("missing_op", 18), ("countdown", 12)])
        q = {"calc": _calc, "missing_op": _missing_op, "countdown": _countdown}[kind](rng, bucket)
        q["id"] = f"ar{bucket}-{k}"
        out.append(q)
    return out
