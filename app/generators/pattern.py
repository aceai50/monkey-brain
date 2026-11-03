"""Procedural pattern puzzles. The answer is produced by the rule that built the puzzle,
so there is never an ambiguous or wrong key (unlike LLM-written puzzles)."""
from itertools import product

from app.core.hashrng import HashRng

SHAPES = ["▲", "■", "●", "◆", "★", "✚"]
VOCAB = ["BRAIN", "FOCUS", "LOGIC", "SHARP", "MIND", "THINK", "REFLEX", "SOLVE", "CLEAR", "QUICK",
         "ALERT", "CALM", "PULSE", "SPARK", "TRAIN", "LEARN", "SKILL", "STUDY", "DRIVE", "FLOW"]


def _options(rng: HashRng, answer, pool: list) -> tuple[list, int]:
    distract = []
    for x in pool:
        if x != answer and x not in distract:
            distract.append(x)
    rng.shuffle(distract)
    opts = rng.shuffle([answer] + distract[:3])
    return opts, opts.index(answer)


def _cycle(rng: HashRng, lvl: int) -> dict:
    size = 3 if lvl < 2 else 4
    syms = rng.shuffle(SHAPES[:])[: 2 if lvl == 0 else 3]
    base = [syms[0]] * size
    base[rng.randint(0, size - 1)] = syms[1]
    if lvl >= 2:
        base[rng.randint(0, size - 1)] = syms[2]
    step = 1 if lvl < 3 else rng.choice([1, 2, size - 1])
    seqs = [base]
    for _ in range(4):
        prev = seqs[-1]
        seqs.append(prev[step:] + prev[:step])
    shown = [("".join(s)) for s in seqs[: 3 if lvl < 2 else 4]]
    ans = "".join(seqs[len(shown)])
    pool = ["".join(seqs[i]) for i in range(5)]
    pool += ["".join(c) for c in product(syms, repeat=size)]  # guarantees >= 3 distractors
    opts, idx = _options(rng, ans, [p for p in pool if p != ans])
    return {"type": "sequence", "prompt": "What comes next?", "display": shown + ["?"], "options": opts, "answer": idx}


def _numeric(rng: HashRng, lvl: int) -> dict:
    kinds = ["ap"] if lvl == 0 else (["ap", "gp", "alt"] if lvl == 1 else ["gp", "alt", "fib", "square", "growing"])
    kind = rng.choice(kinds)
    n = 5 if lvl < 2 else 6
    if kind == "ap":
        a, d = rng.randint(2, 30), rng.randint(2, 12) * rng.choice([1, 1, -1])
        seq = [a + d * i for i in range(n + 1)]
    elif kind == "gp":
        a, r = rng.randint(1, 5), rng.choice([2, 3])
        seq = [a * r ** i for i in range(n + 1)]
    elif kind == "alt":
        a, b, d1, d2 = rng.randint(1, 20), rng.randint(40, 90), rng.randint(2, 7), rng.randint(2, 9)
        seq = []
        for i in range(n + 1):
            seq.append(a + d1 * (i // 2) if i % 2 == 0 else b - d2 * (i // 2))
    elif kind == "fib":
        x, y = rng.randint(1, 5), rng.randint(2, 7)
        seq = [x, y]
        while len(seq) < n + 1:
            seq.append(seq[-1] + seq[-2])
    elif kind == "square":
        off, s0 = rng.randint(-3, 5), rng.randint(1, 4)
        seq = [(s0 + i) ** 2 + off for i in range(n + 1)]
    else:  # growing differences
        a, d0, g = rng.randint(1, 10), rng.randint(1, 4), rng.randint(1, 3)
        seq, d = [a], d0
        for _ in range(n):
            seq.append(seq[-1] + d)
            d += g
    ans = seq[-1]
    pool = [ans + k for k in (-1, 1, -2, 2, 3, -3, 5, -5, 10)] + [seq[-2] + (seq[-2] - seq[-3])]
    opts, idx = _options(rng, ans, [p for p in pool if p != ans])
    return {"type": "sequence", "prompt": "What comes next?", "display": [str(x) for x in seq[:-1]] + ["?"],
            "options": [str(o) for o in opts], "answer": idx}


def _letters(rng: HashRng, lvl: int) -> dict:
    alt = lvl >= 2
    step = rng.randint(2, 4)
    step2 = rng.randint(1, 3) if alt else step
    start = rng.randint(0, 6)
    pos, seq = start, [start]
    for i in range(5):
        pos += step if i % 2 == 0 else step2
        seq.append(pos)
    letters = [chr(65 + p % 26) for p in seq]
    ans = letters[-1]
    pool = [chr(65 + (seq[-1] + k) % 26) for k in (-2, -1, 1, 2, 3)]
    opts, idx = _options(rng, ans, [p for p in pool if p != ans])
    return {"type": "sequence", "prompt": "Which letter comes next?", "display": letters[:-1] + ["?"],
            "options": opts, "answer": idx}


def _decode(rng: HashRng, lvl: int) -> dict:
    shift = rng.randint(1, 5 + lvl * 3)
    word = rng.choice(VOCAB)
    cipher = "".join(chr((ord(c) - 65 + shift) % 26 + 65) for c in word)
    hint = f"A → {chr(65 + shift % 26)}"
    opts, idx = _options(rng, word, [w for w in VOCAB if w != word and len(w) == len(word)] + [w for w in VOCAB if w != word])
    return {"type": "decode", "prompt": f"Each letter was shifted by the same amount ({hint}). Decode it.",
            "display": [cipher], "options": opts, "answer": idx}


def _grid(rng: HashRng, lvl: int) -> dict:
    n = 3 if lvl < 3 else 4
    syms = rng.shuffle(SHAPES[:])[:n]
    shift = rng.choice([1, n - 1])
    rows = [[syms[(c + r * shift) % n] for c in range(n)] for r in range(n)]
    r, c = rng.randint(0, n - 1), rng.randint(0, n - 1)
    ans = rows[r][c]
    display = [[("?" if (i, j) == (r, c) else rows[i][j]) for j in range(n)] for i in range(n)]
    extra = [s for s in SHAPES if s not in syms]
    opts, idx = _options(rng, ans, [s for s in syms if s != ans] + extra)
    return {"type": "grid", "prompt": "Each row and column uses every symbol once. What's missing?",
            "display": display, "options": opts, "answer": idx}


# level -> weighted generators
TABLE = [
    [(_cycle, 3), (_numeric, 3), (_grid, 2), (_letters, 1)],
    [(_cycle, 3), (_numeric, 3), (_grid, 2), (_letters, 2), (_decode, 1)],
    [(_cycle, 2), (_numeric, 4), (_grid, 2), (_letters, 2), (_decode, 2)],
    [(_cycle, 2), (_numeric, 4), (_grid, 3), (_letters, 2), (_decode, 3)],
]


def generate(bucket: int, count: int, seed: str) -> list[dict]:
    rng = HashRng("pattern", bucket, seed)
    out = []
    for k in range(count):
        q = rng.weighted(TABLE[bucket])(rng, bucket)
        q["id"] = f"pa{bucket}-{k}"
        out.append(q)
    return out
