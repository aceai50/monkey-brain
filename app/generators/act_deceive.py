"""Act & Deceive (v2) — hold a cover identity under scrutiny.

No cram cards. The player is given a role and a few *glimpses* of facts. Each turn offers 4
moves shown with dynamic AI tags; the hidden kind is safe / deflect / overreach / reinforce.
Overreaches are debts a LATER observer calls in; contradicting your own earlier claim is the
loudest exposure; suspicion has heat that compounds. Doubt runs 0->5. The engine (not the LLM)
decides everything; the validator replays every path so a scenario can't be unfair.
"""
import copy
from itertools import combinations

MAX_DOUBT = 5
VERSION = 2
KINDS = ("safe", "deflect", "overreach", "reinforce")
PERSONALITIES = ("curious", "skeptical", "narcissist", "formal", "hostile")
MIN_SCENE_NODES, MAX_SCENE_NODES = 4, 10
MIN_FACTS, MAX_FACTS = 3, 9
MIN_TAGS, MAX_TAGS = 4, 6
MIN_CHECKS = 1
FUSE = {"easy": 1, "medium": 2, "hard": 3}   # min scene-steps between a commit and its check

KIND_WHY = {
    "safe": "say something that fits what you already know — no new risk",
    "deflect": "duck it or turn the question back — safe, but a room full of dodges reads guilty",
    "overreach": "confidently assert a detail you were never given — buys credibility now, a later observer may call it in",
    "reinforce": "stand by or restate something you already said — keeps the story straight, or contradicts it loudly",
}


def clamp(v):
    return max(0, min(MAX_DOUBT, v))


# ---------------------------------------------------------------- conditions
def _valid_atom(a) -> bool:
    if not isinstance(a, dict):
        return False
    keys = set(a) & {"glimpsed", "claimed", "not_claimed", "target", "target_personality",
                     "turn_lt", "doubt_lt", "vague_streak_lt"}
    if len(keys) != 1:
        return False
    if "target_personality" in a:
        return a["target_personality"] in PERSONALITIES
    return True


def _valid_cond(c) -> bool:
    if not c:
        return True
    if not isinstance(c, dict):
        return False
    if "all" in c or "any" in c:
        items = c.get("all") or c.get("any") or []
        return isinstance(items, list) and all(_valid_cond(x) for x in items)
    return _valid_atom(c)


def eval_cond(c, ctx) -> bool:
    if not c:
        return True
    if "all" in c:
        return all(eval_cond(x, ctx) for x in c["all"])
    if "any" in c:
        return any(eval_cond(x, ctx) for x in c["any"])
    if "glimpsed" in c:
        return c["glimpsed"] in ctx["glimpsed"]
    if "claimed" in c:
        return c["claimed"] in ctx["claimed"]
    if "not_claimed" in c:
        return c["not_claimed"] not in ctx["claimed"]
    if "target" in c:
        return c["target"] == ctx["speaker"]
    if "target_personality" in c:
        return c["target_personality"] == ctx["personality"]
    if "turn_lt" in c:
        return ctx["turn"] < c["turn_lt"]
    if "doubt_lt" in c:
        return ctx["doubt"] < c["doubt_lt"]
    if "vague_streak_lt" in c:
        return ctx["vague_streak"] < c["vague_streak_lt"]
    return False


# ---------------------------------------------------------------- engine
def new_state(scenario) -> dict:
    return {"doubt": 0, "turn": 0, "vague_streak": 0, "claims": {}, "caught": [],
            "suspicion": {c["id"]: 0 for c in scenario["cast"]}}


def _facts_map(scenario):
    return {f["id"]: f for f in scenario["facts"]}


def _ctx(scenario, state, speaker):
    cast = {c["id"]: c for c in scenario["cast"]}
    return {"glimpsed": {f["id"] for f in scenario["facts"] if f["glimpsed"]},
            "claimed": set(state["claims"]), "speaker": speaker,
            "personality": cast.get(speaker, {}).get("personality"),
            "turn": state["turn"], "doubt": state["doubt"], "vague_streak": state["vague_streak"]}


def apply_option(state, scenario, node, option):
    """Mutates state; returns the outcome the client shows. Handles debt, contradiction and heat."""
    facts = _facts_map(scenario)
    speaker = node.get("speaker")
    cast = {c["id"]: c for c in scenario["cast"]}
    ctx = _ctx(scenario, state, speaker)
    kind, fact, value = option["kind"], option.get("fact"), option.get("value")
    ok = eval_cond(option.get("works_if"), ctx)
    before = state["doubt"]
    delta = option["doubt_on_success"] if ok else option["doubt_on_fail"]
    result = "pass" if ok else "fail"
    line = option["line_if_pass"] if ok else option["line_if_fail"]

    if kind == "deflect":
        state["vague_streak"] = state["vague_streak"] + 1 if ok else 0
    else:
        state["vague_streak"] = 0

    if kind in ("safe", "overreach", "reinforce") and fact and fact in facts:
        f = facts[fact]
        existing = state["claims"].get(fact)
        if existing is not None and existing["value"] != value:      # self-contradiction
            heat = 1 if state["suspicion"].get(speaker, 0) >= 2 else 0
            delta = 2 + heat
            result = "contradiction"
            line = option.get("line_if_fail", line)
            state["claims"][fact] = {"value": value, "to": cast.get(speaker, {}).get("name", "the room"),
                                     "held": False, "checked": False, "exposed": True}
        else:
            known = f["glimpsed"] or value == f["true_value"]
            prev = existing or {}
            state["claims"][fact] = {"value": value, "to": cast.get(speaker, {}).get("name", "the room"),
                                     "held": known, "checked": prev.get("checked", False),
                                     "exposed": prev.get("exposed", False)}
            if kind == "overreach" and not known:
                result = "debt"
            elif known and kind != "safe":
                result = "held"

    state["doubt"] = clamp(before + delta)
    if speaker in state["suspicion"]:
        state["suspicion"][speaker] += state["doubt"] - before
    return {"ok": ok, "result": result, "delta": state["doubt"] - before, "line": line,
            "fact": fact, "value": value, "kind": kind}


def run_check(state, node):
    facts_present = node.get("fact")
    speaker = node.get("speaker")
    entry = state["claims"].get(facts_present)
    out = {"result": "neutral", "caught": [], "line": ""}
    if entry is not None and not entry["checked"]:
        entry["checked"] = True
        if entry["held"]:
            out["result"] = "held"
            out["line"] = node.get("held_line", "")
        else:
            heat = 1 if state["suspicion"].get(speaker, 0) >= 2 else 0
            state["doubt"] = clamp(state["doubt"] + int(node.get("penalty", 1)) + heat)
            entry["exposed"] = True
            state["caught"].append(facts_present)
            if speaker in state["suspicion"]:
                state["suspicion"][speaker] += 1
            out["result"] = "exposed"
            out["caught"] = [facts_present]
            out["line"] = node.get("exposed_line", "")
    return out


def finish(state, scenario):
    exposed = sum(1 for c in state["claims"].values() if c.get("exposed"))
    busted = state["doubt"] >= MAX_DOUBT
    bought = sum(1 for c in scenario["cast"] if state["suspicion"][c["id"]] < 2)
    need = (len(scenario["cast"]) + 1) // 2
    convinced = (not busted) and bought >= need and exposed == 0
    outcome = "busted" if busted else ("convinced" if convinced else "partial")
    return {"outcome": outcome, "bought": bought, "exposed": exposed,
            "verdicts": {c["id"]: ("bought_it" if state["suspicion"][c["id"]] < 2 else
                                   "suspicious" if state["suspicion"][c["id"]] <= 3 else "sees_through")
                         for c in scenario["cast"]}}


def walk(scenario, pick):
    state = new_state(scenario)
    nodes = scenario["nodes"]
    node = nodes["n1"]
    for _ in range(80):
        if node.get("kind") == "end":
            break
        if node.get("kind") == "check":
            run_check(state, node)
        else:
            apply_option(state, scenario, node, pick(node, node["options"]))
            state["turn"] += 1
        node = nodes[node["next"]]
    return {"state": state, **finish(state, scenario)}


def _dfs_win(scenario):
    """Does a convincing path exist? DFS over options with state pruning."""
    nodes = scenario["nodes"]
    seen = set()

    def key(nid, st):
        claims = tuple(sorted((f, c["value"], c["held"], c["checked"], c["exposed"])
                              for f, c in st["claims"].items()))
        susp = tuple(sorted(st["suspicion"].items()))
        return (nid, st["doubt"], st["turn"], st["vague_streak"], claims, susp)

    def dfs(nid, st):
        k = key(nid, st)
        if k in seen:
            return False
        seen.add(k)
        node = nodes[nid]
        if node.get("kind") == "end":
            return finish(st, scenario)["outcome"] == "convinced"
        if node.get("kind") == "check":
            nxt = copy.deepcopy(st)
            run_check(nxt, node)
            return dfs(node["next"], nxt)
        for opt in node["options"]:
            branch = copy.deepcopy(st)
            apply_option(branch, scenario, node, opt)
            if dfs(node["next"], branch):
                return True
        return False

    return dfs("n1", new_state(scenario))


# ---------------------------------------------------------------- assembly
def assemble_nodes(scene_nodes, checks, end_line):
    """Chain scene chunks + checks + end into one linear script (engine owns ids/links)."""
    out = []
    for i, n in enumerate(scene_nodes, 1):
        out.append({"id": f"n{i}", "kind": "scene", "speaker": n.get("speaker"), "line": n.get("line"),
                    "options": [{**o, "next": None} for o in (n.get("options") or [])], "next": None})
    for j, c in enumerate(checks, 1):
        out.append({"id": f"c{j}", "kind": "check", "speaker": c.get("speaker"), "fact": c.get("fact"),
                    "line": c.get("line") or "", "exposed_line": c.get("exposed_line") or "",
                    "held_line": c.get("held_line") or "", "penalty": c.get("penalty") or 1, "next": None})
    out.append({"id": "end", "kind": "end", "line": end_line or "The room drifts on.", "next": None})
    for a, b in zip(out, out[1:]):
        a["next"] = b["id"]
    return out


# ---------------------------------------------------------------- validator
def _fact(raw):
    fid = str(raw.get("id", "")).strip().lower()
    tv = str(raw.get("true_value", "")).strip()
    if not fid or not tv:
        return None
    return {"id": fid, "true_value": tv, "glimpsed": bool(raw.get("glimpsed")),
            "glimpse": str(raw.get("glimpse", "")).strip()[:160]}


def _option(raw, facts):
    kind = str(raw.get("kind", "")).strip().lower()
    tag = str(raw.get("tag", "")).strip().lower()
    if kind not in KINDS or not tag:
        return None
    fact = raw.get("fact")
    fact = str(fact).strip().lower() if fact else None
    value = raw.get("value")
    value = str(value).strip() if value is not None else None
    if kind == "safe":
        if not fact or fact not in facts or not facts[fact]["glimpsed"] or value != facts[fact]["true_value"]:
            return None
    if kind == "overreach":
        if not fact or fact not in facts or facts[fact]["glimpsed"]:
            return None
    if kind == "deflect" and fact:
        return None
    if kind in ("overreach", "reinforce") and fact and (fact not in facts or not value):
        return None
    cond = raw.get("works_if") or {}
    if not _valid_cond(cond):
        return None
    ds, df = int(raw.get("doubt_on_success", 0)), int(raw.get("doubt_on_fail", 1))
    if not -2 <= ds <= 2 or not 0 <= df <= 3:
        return None
    return {"kind": kind, "tag": tag, "text": str(raw.get("text", "")).strip()[:160],
            "fact": fact, "value": value, "works_if": cond, "doubt_on_success": ds, "doubt_on_fail": df,
            "line_if_pass": str(raw.get("line_if_pass", "")).strip()[:200],
            "line_if_fail": str(raw.get("line_if_fail", "")).strip()[:200]}


def validate_scenario(raw, difficulty: str, key: int) -> dict | None:
    try:
        role = str(raw.get("role", "")).strip()[:120]
        setting = str(raw.get("setting", "")).strip()[:120]
        if not role or not setting:
            return None
        facts_raw = raw.get("facts") or []
        if not MIN_FACTS <= len(facts_raw) <= MAX_FACTS:
            return None
        facts, fset = [], set()
        for fr in facts_raw:
            f = _fact(fr)
            if f is None or f["id"] in fset:
                return None
            fset.add(f["id"])
            facts.append(f)
        facts_map = {f["id"]: f for f in facts}
        if not any(f["glimpsed"] for f in facts) or not any(not f["glimpsed"] for f in facts):
            return None
        cast_raw = raw.get("cast") or []
        if not 1 <= len(cast_raw) <= 4:
            return None
        cast, cids = [], set()
        for c in cast_raw:
            cid = str(c.get("id", "")).strip().lower()
            pers = str(c.get("personality", "")).strip().lower()
            name = str(c.get("name", "")).strip()[:24]
            if not cid or cid in cids or pers not in PERSONALITIES or not name:
                return None
            cids.add(cid)
            cast.append({"id": cid, "name": name, "image_label": str(c.get("image_label", "")).strip().lower()[:32],
                         "role": str(c.get("role", "")).strip()[:80], "personality": pers,
                         "hint": str(c.get("hint", "")).strip()[:120]})
        nodes_raw = raw.get("nodes") or []
        nodes, order = {}, []
        scene_count = 0
        overreach_facts = set()
        for n in nodes_raw:
            nid = str(n.get("id", "")).strip()
            kind = n.get("kind") or "scene"
            if not nid or nid in nodes:
                return None
            if kind == "end":
                nodes[nid] = {"id": nid, "kind": "end", "line": str(n.get("line", "")).strip()[:240], "next": None}
            elif kind == "check":
                fact = str(n.get("fact", "")).strip().lower()
                if fact not in facts_map:
                    return None
                nodes[nid] = {"id": nid, "kind": "check", "speaker": str(n.get("speaker", "")).strip().lower(),
                              "fact": fact, "line": str(n.get("line", "")).strip()[:240],
                              "exposed_line": str(n.get("exposed_line", "")).strip()[:240],
                              "held_line": str(n.get("held_line", "")).strip()[:240],
                              "penalty": max(1, min(2, int(n.get("penalty", 1)))), "next": str(n.get("next", "")).strip()}
            else:
                opts_raw = n.get("options") or []
                if len(opts_raw) != 4:
                    return None
                opts = [_option(o, facts_map) for o in opts_raw]
                if any(o is None for o in opts):
                    return None
                kinds = sorted(o["kind"] for o in opts)
                if kinds != sorted(KINDS):
                    return None
                tags = [o["tag"] for o in opts]
                if len(set(tags)) != 4:
                    return None
                for o in opts:
                    if o["kind"] == "overreach":
                        overreach_facts.add(o["fact"])
                scene_count += 1
                nodes[nid] = {"id": nid, "kind": "scene", "speaker": str(n.get("speaker", "")).strip().lower(),
                              "line": str(n.get("line", "")).strip()[:240], "options": opts,
                              "next": str(n.get("next", "")).strip()}
                if nodes[nid]["speaker"] not in cids:
                    return None
        if not MIN_SCENE_NODES <= scene_count <= MAX_SCENE_NODES:
            return None
        # linear chain covers everything, ends at end
        if "n1" not in nodes:
            return None
        cur, steps = "n1", []
        while cur in nodes and cur not in steps:
            steps.append(cur)
            cur = nodes[cur].get("next")
        if set(steps) != set(nodes) or nodes[steps[-1]].get("kind") != "end":
            return None
        checks = [nodes[i] for i in steps if nodes[i]["kind"] == "check"]
        if len(checks) < MIN_CHECKS:
            return None
        # every overreach fact must be called in by a later check (the debt comes due)
        pos = {nid: i for i, nid in enumerate(steps)}
        for fid in overreach_facts:
            commit_step = min(pos[n["id"]] for n in (nodes[s] for s in steps)
                               if n.get("kind") == "scene" and any(o["kind"] == "overreach" and o["fact"] == fid for o in n["options"]))
            covering = [c for c in checks if c["fact"] == fid]
            if not covering:
                return None
            if difficulty in FUSE and min(pos[c["id"]] for c in covering) - commit_step < FUSE[difficulty]:
                return None
        # cast members should speak
        if not all(any(n.get("kind") in ("scene", "check") and n.get("speaker") == c["id"] for n in nodes.values()) for c in cast):
            return None
        verdicts_raw = raw.get("verdicts") or []
        verdicts = {}
        for v in verdicts_raw:
            vid = str(v.get("id", "")).strip().lower()
            if vid in cids:
                verdicts[vid] = {"bought_it": str(v.get("bought_it", "")).strip()[:160],
                                 "suspicious": str(v.get("suspicious", "")).strip()[:160],
                                 "sees_through": str(v.get("sees_through", "")).strip()[:160]}
        if set(verdicts) != cids:
            return None
        scenario = {"id": f"ad2{difficulty[0]}-{key}", "version": VERSION, "difficulty": difficulty,
                    "role": role, "setting": setting, "facts": facts,
                    "glimpses": [f["glimpse"] or f"you know your {f['id']}" for f in facts if f["glimpsed"]],
                    "cast": cast, "turns": scene_count, "nodes": nodes, "verdicts": verdicts}
        if not _dfs_win(scenario):
            return None
        return scenario
    except (TypeError, ValueError, KeyError, StopIteration):
        return None


# ---------------------------------------------------------------- bundled bank
def _opt(kind, tag, text, ok, fail, *, fact=None, value=None, cond=None, ds=0, df=1):
    return {"kind": kind, "tag": tag, "text": text, "fact": fact, "value": value,
            "works_if": cond or {}, "doubt_on_success": ds, "doubt_on_fail": df,
            "line_if_pass": ok, "line_if_fail": fail}


def _scene(nid, speaker, line, options):
    return {"id": nid, "kind": "scene", "speaker": speaker, "line": line, "options": options, "next": None}


def _check(nid, speaker, fact, line, exposed, held, penalty=1):
    return {"id": nid, "kind": "check", "speaker": speaker, "fact": fact, "line": line,
            "exposed_line": exposed, "held_line": held, "penalty": penalty, "next": None}


def _end(nid, line):
    return {"id": nid, "kind": "end", "line": line, "next": None}


def _verdict(cid, b, s, x):
    return {"id": cid, "bought_it": b, "suspicious": s, "sees_through": x}


# Easy: one friendly-ish observer, short fuse, obvious probes, generous glimpses.
_E_FACTS = [
    {"id": "specialty", "true_value": "cardiology", "glimpsed": True, "glimpse": "you know your specialty is cardiology"},
    {"id": "hospital", "true_value": "St. Luke's", "glimpsed": True, "glimpse": "you work at St. Luke's"},
    {"id": "mentor", "true_value": "Dr. Alden", "glimpsed": False},
    {"id": "hometown", "true_value": "Lyon", "glimpsed": False},
]
_E_CAST = [{"id": "jack", "name": "Dr. Jack", "image_label": "boss_1", "role": "a senior researcher",
            "personality": "curious", "hint": "Friendly, but tests whether you actually know your own job."}]
_E_NODES = [
    _scene("n1", "jack", "Good evening! So — what do you actually work on?", [
        _opt("safe", "name your field", "Cardiology, mostly.", "Ah, a heart person.", "You hesitate too much.", fact="specialty", value="cardiology", cond={"glimpsed": "specialty"}),
        _opt("deflect", "bounce it back", "And what keeps *you* busy?", "Mine? Endless grants.", "Don't dodge, doctor.", cond={"target_personality": "curious"}),
        _opt("overreach", "one-up him", "I mostly do cardiac genetics — very niche.", "Niche indeed.", "Cardiac genetics?", fact="mentor", value="genetics", cond={"turn_lt": 0}, ds=-1, df=2),
        _opt("reinforce", "double down", "Yes, the heart. It's everything.", "Passion, good.", "You already said that.", fact="specialty", value="cardiology", cond={"claimed": "specialty"})]),
    _scene("n2", "jack", "Where do you hang your hat these days?", [
        _opt("safe", "name your hospital", "St. Luke's.", "Solid hospital.", "You fumbled that.", fact="hospital", value="St. Luke's", cond={"glimpsed": "hospital"}),
        _opt("deflect", "change the subject", "Somewhere with good coffee.", "Ha.", "Avoiding again.", cond={"vague_streak_lt": 2}),
        _opt("overreach", "claim a famous lab", "Mayo Clinic, mainly.", "Oh, Mayo!", "Mayo? Really?", fact="hometown", value="Mayo", cond={"turn_lt": 0}, ds=-1, df=2),
        _opt("reinforce", "restate it", "St. Luke's, as I said.", "Consistent. Good.", "You changed your story.", fact="hospital", value="St. Luke's", cond={"claimed": "hospital"})]),
    _scene("n3", "jack", "Who trained you? Every doctor has a mentor.", [
        _opt("safe", "lean on your field", "My training was all cardiology.", "Fair enough.", "That's not a name.", fact="specialty", value="cardiology", cond={"glimpsed": "specialty"}),
        _opt("deflect", "turn it back", "Who trained *you*, Jack?", "Alden, actually.", "I asked you.", cond={"target_personality": "curious"}),
        _opt("overreach", "guess a mentor", "Dr. Vance trained me.", "Vance? Interesting.", "…I don't know a Vance.", fact="mentor", value="Dr. Vance", ds=-1, df=2),
        _opt("reinforce", "stay vague on it", "A good one, in cardiology.", "Mm.", "Evasive.", fact="specialty", value="cardiology", cond={"claimed": "specialty"})]),
    _scene("n4", "jack", "And where are you from originally?", [
        _opt("safe", "use your field as an anchor", "I'm a heart doctor — that's my home, really.", "Poetic.", "Non-answer.", fact="specialty", value="cardiology", cond={"glimpsed": "specialty"}),
        _opt("deflect", "deflect with a smile", "Does it matter?", "It might.", "Careful.", cond={"vague_streak_lt": 2}),
        _opt("overreach", "invent a city", "Chicago, born and raised.", "Chicago!", "Chicago?", fact="hometown", value="Chicago", ds=-1, df=2),
        _opt("reinforce", "restate your hospital", "St. Luke's is where I'm from, these days.", "You said that.", "Repeating.", fact="hospital", value="St. Luke's", cond={"claimed": "hospital"})]),
    _check("c1", "jack", "mentor", "By the way — I know every trainee of Dr. Vance. Which one were you?",
           "There is no trainee by that name.", "Ah, yes, that tracks.", penalty=2),
    _check("c2", "jack", "hometown", "Funny, your accent isn't Chicago at all.",
           "…I said Chicago. It doesn't fit.", "Wherever you're from, it suits you.", penalty=1),
    _end("end", "Jack claps your shoulder, reading you one way or the other."),
]
_E = {"role": "a junior cardiologist", "setting": "a hospital research gala", "facts": _E_FACTS,
      "cast": _E_CAST, "turns": 4, "nodes": _E_NODES,
      "verdicts": [_verdict("jack", "Seemed like one of us.", "Kept tilting his head.", "Told the room you were faking.")]}

# Medium: two observers who compare notes; longer fuse; fewer glimpses.
_M_FACTS = [
    {"id": "firm", "true_value": "Klein & Associates", "glimpsed": True, "glimpse": "you're an architect at Klein & Associates"},
    {"id": "project", "true_value": "the Riverside tower", "glimpsed": False},
    {"id": "city", "true_value": "Rotterdam", "glimpsed": False},
    {"id": "years", "true_value": "nine", "glimpsed": True, "glimpse": "you have about nine years in the firm"},
]
_M_CAST = [
    {"id": "ravi", "name": "Ravi", "image_label": "male_1", "role": "a developer", "personality": "curious",
     "hint": "Warm, but repeats your answers back to the other person."},
    {"id": "dan", "name": "Dan", "image_label": "police_1", "role": "a rival architect", "personality": "skeptical",
     "hint": "Quietly checks everything against what he already knows."}]
_M_NODES = [
    _scene("n1", "ravi", "You design for Klein & Associates? I love the Riverside tower.", [
        _opt("safe", "claim your firm", "Klein & Associates, yes.", "Great firm.", "You weren't sure.", fact="firm", value="Klein & Associates", cond={"glimpsed": "firm"}),
        _opt("deflect", "deflect the praise", "The city deserves it more.", "Modest.", "Dodging.", cond={"vague_streak_lt": 2}),
        _opt("overreach", "take the project", "Riverside was largely my work.", "Was it?", "Was it, though?", fact="project", value="mine", ds=-1, df=2),
        _opt("reinforce", "restate the firm", "Yes, Klein & Associates.", "You said that.", "Repeating.", fact="firm", value="Klein & Associates", cond={"claimed": "firm"})]),
    _scene("n2", "dan", "Nine years at Klein? That's a long time to stay put.", [
        _opt("safe", "own your tenure", "Nine years, give or take.", "Loyal.", "You blinked.", fact="years", value="nine", cond={"glimpsed": "years"}),
        _opt("deflect", "turn it on him", "How long have *you* been here?", "Long enough.", "Don't redirect me.", cond={"target_personality": "skeptical"}),
        _opt("overreach", "claim a landmark", "I led the whole Rotterdam waterfront.", "The waterfront?", "The waterfront? No you didn't.", fact="city", value="waterfront", ds=-1, df=2),
        _opt("reinforce", "double down on tenure", "Nine solid years.", "Consistent.", "You changed.", fact="years", value="nine", cond={"claimed": "years"})]),
    _scene("n3", "ravi", "Dan says you're the one behind the Riverside tower. True?", [
        _opt("safe", "stay in your lane", "I design for Klein — Riverside isn't mine.", "Honest.", "Now you're dodging.", fact="firm", value="Klein & Associates", cond={"glimpsed": "firm"}),
        _opt("deflect", "dodge the he-said", "Dan says a lot of things.", "He does.", "Careful.", cond={"vague_streak_lt": 2}),
        _opt("overreach", "grab the credit", "Riverside is my signature.", "Is it?", "Riverside is not yours.", fact="project", value="mine", ds=-1, df=2),
        _opt("reinforce", "stick to the firm", "Just Klein work, as I said.", "Steady.", "You flip-flopped.", fact="firm", value="Klein & Associates", cond={"claimed": "firm"})]),
    _scene("n4", "dan", "Where's your studio based? You strike me as Rotterdam.", [
        _opt("safe", "anchor on tenure", "Nine years in one city is enough for me.", "Steady.", "Non-answer.", fact="years", value="nine", cond={"glimpsed": "years"}),
        _opt("deflect", "deflect", "Cities blur together.", "Do they.", "Avoiding.", cond={"vague_streak_lt": 2}),
        _opt("overreach", "confirm the guess", "Rotterdam, yes.", "Thought so.", "You're not from Rotterdam.", fact="city", value="Rotterdam", ds=-1, df=2),
        _opt("reinforce", "restate the firm", "Klein's city, basically.", "You keep saying that.", "Repeating.", fact="firm", value="Klein & Associates", cond={"claimed": "firm"})]),
    _scene("n5", "dan", "So — Riverside: yours or not?", [
        _opt("safe", "honest anchor", "Not mine. I do other Klein work.", "Straight shooter.", "Waffling.", fact="firm", value="Klein & Associates", cond={"glimpsed": "firm"}),
        _opt("deflect", "deflect again", "Does it matter whose?", "It does.", "That's three dodges.", cond={"vague_streak_lt": 2}),
        _opt("overreach", "claim it anyway", "Riverside is mine.", "…", "You said otherwise before.", fact="project", value="mine", ds=-1, df=2),
        _opt("reinforce", "hold your line", "Klein work, not Riverside.", "Consistent.", "You changed your story.", fact="project", value="not mine", cond={"claimed": "project"})]),
    _check("c1", "dan", "project", "I pulled the credits. Riverside lists someone else.",
           "So it isn't yours after all.", "You were straight about that.", penalty=2),
    _check("c2", "ravi", "city", "Dan says you're not Rotterdam. Earlier you seemed to agree you were.",
           "You told me Rotterdam, then not.", "You never claimed Rotterdam.", penalty=1),
    _end("end", "They exchange a look and decide what you are."),
]
_M = {"role": "a senior architect", "setting": "a property-development dinner", "facts": _M_FACTS,
      "cast": _M_CAST, "turns": 5, "nodes": _M_NODES,
      "verdicts": [_verdict("ravi", "Wanted to believe you.", "Started watching you.", "Warned Dan quietly."),
                   _verdict("dan", "Nodded along.", "Asked sharper questions.", "Said you were bluffing.")]}

# Hard: three observers, one who knows the real you; long fuse; sparse glimpses; disguised traps.
_H_FACTS = [
    {"id": "desk", "true_value": "the 14th floor", "glimpsed": True, "glimpse": "you sit on the 14th floor"},
    {"id": "account", "true_value": "Meridian", "glimpsed": False},
    {"id": "boss", "true_value": "Priya", "glimpsed": False},
    {"id": "city", "true_value": "Dublin", "glimpsed": False},
    {"id": "start", "true_value": "2019", "glimpsed": True, "glimpse": "you joined around 2019"},
]
_H_CAST = [
    {"id": "elena", "name": "Elena", "image_label": "boss_2", "role": "a director", "personality": "formal",
     "hint": "Small talk that is secretly a cross-check."},
    {"id": "jack", "name": "Jack", "image_label": "rich_1", "role": "a client", "personality": "narcissist",
     "hint": "Wants to be impressed; punishes being contradicted."},
    {"id": "dan", "name": "Dan", "image_label": "neighbour_1", "role": "an old colleague", "personality": "skeptical",
     "hint": "Knows the real person you're imitating."}]
_H_NODES = [
    _scene("n1", "elena", "Lovely to finally meet you. I hear you hold the Meridian account?", [
        _opt("safe", "stay on your floor", "I'm on the 14th floor, mostly operations.", "Ah, operations.", "You lost me.", fact="desk", value="the 14th floor", cond={"glimpsed": "desk"}),
        _opt("deflect", "deflect gracefully", "I support a few accounts.", "Mm.", "Non-committal.", cond={"vague_streak_lt": 2}),
        _opt("overreach", "claim the account", "Meridian is mine.", "Impressive.", "Meridian is not yours.", fact="account", value="Meridian", ds=-1, df=2),
        _opt("reinforce", "lean on your floor", "Fourteenth floor, as I said.", "You did.", "Repeating.", fact="desk", value="the 14th floor", cond={"claimed": "desk"})]),
    _scene("n2", "jack", "Meridian's a beast. Who signs off on it up there?", [
        _opt("safe", "hedge with your start", "I've been here since 2019; I see the sign-offs.", "Diligent.", "You're stalling.", fact="start", value="2019", cond={"glimpsed": "start"}),
        _opt("deflect", "flatter him", "You'd sign it off better than anyone.", "Ha, true.", "Cheap flattery.", cond={"target_personality": "narcissist"}, ds=-1),
        _opt("overreach", "name a boss", "Priya signs everything.", "Does she?", "Priya? She's in legal.", fact="boss", value="Priya", ds=-1, df=2),
        _opt("reinforce", "hold your line", "Fourteenth floor runs it, as I said.", "Bold claim.", "You changed.", fact="desk", value="the 14th floor", cond={"claimed": "desk"})]),
    _scene("n3", "dan", "You're new here, aren't you. What city did you start in?", [
        _opt("safe", "anchor on tenure", "Joined in 2019, same office since.", "Consistent.", "You hesitated.", fact="start", value="2019", cond={"glimpsed": "start"}),
        _opt("deflect", "deflect to Dan", "Where did *you* start, Dan?", "Dublin. You?", "Answer me.", cond={"target_personality": "skeptical"}),
        _opt("overreach", "claim a city", "Dublin, originally.", "Really?", "The real one is from Galway.", fact="city", value="Dublin", ds=-1, df=2),
        _opt("reinforce", "restate start", "2019, as I said.", "Steady.", "You flip-flopped.", fact="start", value="2019", cond={"claimed": "start"})]),
    _scene("n4", "elena", "So the Meridian renewal — Priya or you?", [
        _opt("safe", "stay in your lane", "I'm operations; renewals go up the chain.", "Careful.", "Dodging.", fact="desk", value="the 14th floor", cond={"glimpsed": "desk"}),
        _opt("deflect", "deflect", "Renewals are everyone's headache.", "They are.", "Evasive.", cond={"vague_streak_lt": 2}),
        _opt("overreach", "take it", "Priya and I run Meridian together.", "Do you?", "Priya has never met you.", fact="boss", value="Priya", ds=-1, df=2),
        _opt("reinforce", "hold the account claim", "Meridian's mine, as I said.", "You said that.", "Contradictory.", fact="account", value="Meridian", cond={"claimed": "account"})]),
    _scene("n5", "dan", "Funny — I worked with the real 14th-floor lead. He's from Galway.", [
        _opt("safe", "honest anchor", "Then I'm not him. I'm operations.", "Honest.", "Now you fold.", fact="start", value="2019", cond={"glimpsed": "start"}),
        _opt("deflect", "deflect hard", "People mix us up constantly.", "Do they.", "That's a tell.", cond={"vague_streak_lt": 2}),
        _opt("overreach", "insist", "I *am* the 14th-floor lead.", "Are you?", "He's standing right there.", fact="city", value="Dublin", ds=-1, df=2),
        _opt("reinforce", "double down on city", "Dublin, I said.", "You did.", "You're cornering yourself.", fact="city", value="Dublin", cond={"claimed": "city"})]),
    _scene("n6", "jack", "Last one — Meridian's renewal, whose name is on it?", [
        _opt("safe", "step back", "Not mine. Operations, remember?", "Consistent.", "Waffling again.", fact="desk", value="the 14th floor", cond={"glimpsed": "desk"}),
        _opt("deflect", "deflect to Jack", "You tell me, you're the client.", "I wish I knew.", "You're stalling.", cond={"target_personality": "narcissist"}),
        _opt("overreach", "claim it again", "Mine, as I said.", "You did say that.", "You also said operations.", fact="account", value="Meridian", ds=-1, df=2),
        _opt("reinforce", "restate honestly", "Operations. Meridian's not mine.", "Clear.", "You've said both.", fact="account", value="not mine", cond={"claimed": "account"})]),
    _check("c1", "elena", "account", "I checked — Meridian's owned by a different desk.",
           "So the account was never yours.", "You stayed honest about it.", penalty=2),
    _check("c2", "dan", "boss", "Priya's in legal. She signs nothing.",
           "You named a boss who doesn't exist in that role.", "You never leaned on Priya.", penalty=2),
    _check("c3", "dan", "city", "And the real lead is from Galway, not Dublin.",
           "Your city didn't hold up.", "You never claimed Dublin.", penalty=1),
    _end("end", "The three of them quietly compare notes on you."),
]
_H = {"role": "a mid-level operations lead", "setting": "a corporate client dinner", "facts": _H_FACTS,
      "cast": _H_CAST, "turns": 6, "nodes": _H_NODES,
      "verdicts": [_verdict("elena", "You held the line.", "Her questions sharpened.", "She flagged you to Dan."),
                   _verdict("jack", "Delighted to be flattered.", "He started testing you.", "He laughed it off, but noticed."),
                   _verdict("dan", "Gave you the benefit of the doubt.", "He kept cross-checking.", "He said you weren't the real one.")]}

BANK = {"easy": [_E], "medium": [_M], "hard": [_H]}
_BANK_CACHE: dict[str, list[dict]] = {}


def _chain(nodes_list):
    for a, b in zip(nodes_list, nodes_list[1:]):
        a["next"] = b["id"]
    if nodes_list:
        nodes_list[-1]["next"] = None
    return nodes_list


def bank_pool(bucket: int) -> list[dict]:
    name = ("easy", "medium", "hard")[min(bucket, 2)]
    if name not in _BANK_CACHE:
        built = []
        for k, raw in enumerate(BANK[name]):
            r = copy.deepcopy(raw)
            r["nodes"] = _chain(r["nodes"])
            sc = validate_scenario(r, name, k)
            if sc:
                built.append(sc)
        _BANK_CACHE[name] = built
    return _BANK_CACHE[name]
