"""Server-side grading. The client never sees answer keys, and never decides its own score.

Three separate numbers come out of every pillar:
  score  (0-100)  net correct answers as a share of the questions OFFERED.
  perf   (0-1)    what the adaptive engine sees: accuracy (right / attempted) blended with
                  completion (attempted / offered). See _perf().
  points          score x difficulty multiplier x volume multiplier (circuit only).
"""
from app.generators.art_params import ARCHETYPES, classify_word, clean_word
from app.generators.kinesthetic import BENCHMARK_MS
from app.services.customize import volume_weight

WRONG_PENALTY = {"arithmetic": 0.5, "pattern": 0.5, "critical": 0.0}
KIN_PAR = 0.8            # an average of 0.8 points per trial counts as a perfect reflex set
MIN_HUMAN_RT_MS = 100
SLACK_MS = 3000


def level_weight(level: float) -> float:
    """Difficulty multiplier. Easy counts for half, Expert for full."""
    return round(0.5 + level / 6.0, 4)


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _empty(pillar: str, offered: int) -> dict:
    return {"pillar": pillar, "offered": offered, "score": 0.0, "accuracy": 0.0, "completion": 0.0, "perf": 0.0,
            "correct": 0, "wrong": 0, "answered": 0, "flags": [], "review": []}


def _finish(r: dict, net: float, par_fraction: float = 1.0) -> dict:
    """Fills score / accuracy / completion / perf from the net points and attempt counts."""
    offered, answered = max(1, r["offered"]), r["answered"]
    r["score"] = round(100.0 * _clamp(net / (offered * par_fraction)), 1)
    r["accuracy"] = round(_clamp(net / (answered * par_fraction)), 3) if answered else 0.0
    r["completion"] = round(_clamp(answered / offered), 3)
    # Right-to-attempted, discounted when you left a lot of the set untouched (but never to zero:
    # a slow, accurate player is still evidence the level is workable).
    r["perf"] = round(r["accuracy"] * (0.5 + 0.5 * r["completion"]), 3)
    return r


def _norm(v) -> str:
    return str(v).strip().replace("  ", " ")


def score_questions(pillar: str, questions: list[dict], answers: dict) -> dict:
    r = _empty(pillar, len(questions))
    by_id = {q["id"]: q for q in questions}
    for qid, given in answers.items():
        q = by_id.get(qid)
        if q is None or given is None or given == "":
            continue
        if pillar == "arithmetic":
            ok = _norm(given) == _norm(q["answer"])
        else:  # option index
            try:
                ok = int(given) == int(q["answer"])
            except (TypeError, ValueError):
                continue
        r["answered"] += 1
        r["correct" if ok else "wrong"] += 1
        if pillar == "critical":
            opts = q["options"]
            r["review"].append({
                "scenario": q["scenario"], "prompt": q["prompt"], "correct": ok,
                "your": opts[int(given)] if str(given).lstrip("-").isdigit() and 0 <= int(given) < len(opts) else None,
                "right": opts[q["answer"]], "explanation": q.get("explanation", ""),
            })
    net = max(0.0, r["correct"] - WRONG_PENALTY[pillar] * r["wrong"])
    return _finish(r, net)


def score_art(questions: list[dict], answers: dict) -> dict:
    r = _empty("art", len(questions))
    tally: dict[str, int] = {}
    ids = {q["id"] for q in questions}
    for qid, w in answers.items():
        if qid not in ids:
            continue
        word = clean_word(w)
        if word:
            r["answered"] += 1
            key = classify_word(word)
            tally[key] = tally.get(key, 0) + 1
    r["correct"] = r["answered"]          # art has no wrong answers
    _finish(r, float(r["answered"]))
    if tally:
        top = max(tally, key=lambda k: (tally[k], k != "wildcard"))
        r["archetype"] = {"key": top, "name": ARCHETYPES[top][0], "text": ARCHETYPES[top][1]}
    return r


def trial_points(kind: str, outcome: str, rt: float | None, ttl: int | None) -> float:
    if outcome != "hit" or rt is None or rt < MIN_HUMAN_RT_MS:
        return 0.0
    slow = 700.0 if kind == "reaction" else float(ttl or 1500)
    return max(0.0, min(1.0, (slow - rt) / (slow - BENCHMARK_MS)))


def score_kinesthetic(questions: list[dict], trials: list, elapsed_ms: float) -> dict:
    r = _empty("kinesthetic", len(questions))
    by_id = {q["id"]: q for q in questions}
    seen: set[str] = set()
    pts, needed, hits, rts = 0.0, 0.0, 0, []
    for t in trials:
        q = by_id.get(t.id)
        if q is None or t.id in seen:
            continue
        seen.add(t.id)
        r["answered"] += 1
        needed += q.get("delay_ms", 0) + (t.rt_ms or 0)
        if t.outcome == "hit" and t.rt_ms is not None and t.rt_ms >= MIN_HUMAN_RT_MS:
            pts += trial_points(q["type"], t.outcome, t.rt_ms, q.get("ttl_ms"))
            hits += 1
            rts.append(t.rt_ms)
        elif t.outcome == "hit":
            r["flags"].append("implausible_rt")
    if needed > elapsed_ms + SLACK_MS:   # the reported trials cannot fit in the time that elapsed
        r["flags"].append("impossible_timing")
        pts, hits, rts = 0.0, 0, []
    r["correct"], r["wrong"] = hits, r["answered"] - hits
    _finish(r, pts, KIN_PAR)
    r["best_rt_ms"] = round(min(rts)) if rts else None
    r["avg_rt_ms"] = round(sum(rts) / len(rts)) if rts else None
    return r


def grade(pillar: str, questions: list[dict], payload, elapsed_ms: float) -> dict:
    if pillar == "art":
        return score_art(questions, payload.answers)
    if pillar == "kinesthetic":
        return score_kinesthetic(questions, payload.trials, elapsed_ms)
    return score_questions(pillar, questions, payload.answers)


def row_points(pillar: str, result: dict, level: float) -> int:
    return int(round(result["score"] * level_weight(level) * volume_weight(pillar, result["offered"])))


def day_points(results: dict[str, dict], levels: dict[str, float]) -> int:
    return sum(row_points(p, results[p], levels[p]) for p in results)
