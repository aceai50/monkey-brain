"""Act & Deceive (dark pillar #2) — pure engine, validator and bundled bank.

The engine (not the LLM) computes doubt. A scenario is a LINEAR scene: a chain of nodes,
each with exactly four answer styles (specific / vague / bold / deflect). Choices commit
claims to a ledger; improvised claims are checked later, by a different observer. Validation
replays every path, so the content can never be unfair.
"""
import copy
import math
import re
from itertools import combinations

MAX_DOUBT = 5
VAGUE_LIMIT = 2          # this many evasive passes in a row; the next evasion fails
PREP_PICK = 3
# Mechanical kinds (hidden from the player). The *tags* the player sees are written by the AI.
KINDS = ("commit", "evade", "risk", "redirect")
KIND_OF = {"specific": "commit", "vague": "evade", "bold": "risk", "deflect": "redirect"}
PERSONALITIES = ("curious", "skeptical", "narcissist", "formal", "hostile")
LABEL_RE = re.compile(r"^[a-z0-9_]{2,32}$")
TAG_RE = re.compile(r"^[a-z][a-z '\-]{1,23}$")
MIN_SCENE_NODES = 4
MAX_SCENE_NODES = 12
MIN_TAGS, MAX_TAGS = 4, 6
# Fallback "why" shown in the review when the author didn't supply one (offline bank).
KIND_WHY = {
    "commit": "assert a concrete fact about this topic — safe only if you actually studied it",
    "evade": "a smooth non-answer — harmless early, but three in a row reads as guilty",
    "risk": "a bold claim — the only move that can lower doubt, but it needs the right person",
    "redirect": "turn the question back on them or someone else — works on the right personality",
}


def _txt(v, limit: int) -> str:
    return " ".join(str(v or "").split())[:limit]


# ---------------------------------------------------------------- condition language
def _valid_cond(cond) -> bool:
    if not cond:
        return True
    if not isinstance(cond, dict):
        return False
    if "all" in cond or "any" in cond:
        items = cond.get("all") or cond.get("any") or []
        return isinstance(items, list) and all(_valid_cond(c) for c in items)
    keys = set(cond) & {"studied", "not_studied", "vague_streak_lt", "target",
                        "target_personality", "claim_present", "claim_absent", "turn_lt", "doubt_lt"}
    if len(keys) != 1:
        return False
    if "target_personality" in cond:
        return cond["target_personality"] in PERSONALITIES
    return True


def eval_cond(cond, ctx: dict) -> bool:
    if not cond:
        return True
    if "all" in cond:
        return all(eval_cond(c, ctx) for c in cond["all"])
    if "any" in cond:
        return any(eval_cond(c, ctx) for c in cond["any"])
    if "studied" in cond:
        return cond["studied"] in ctx["studied"]
    if "not_studied" in cond:
        return cond["not_studied"] not in ctx["studied"]
    if "vague_streak_lt" in cond:
        return ctx["vague_streak"] < cond["vague_streak_lt"]
    if "target" in cond:
        return cond["target"] == ctx["speaker"]
    if "target_personality" in cond:
        return cond["target_personality"] == ctx["personality"]
    if "claim_present" in cond:
        return cond["claim_present"] in ctx["ledger"]
    if "claim_absent" in cond:
        return cond["claim_absent"] not in ctx["ledger"]
    if "turn_lt" in cond:
        return ctx["turn"] < cond["turn_lt"]
    if "doubt_lt" in cond:
        return ctx["doubt"] < cond["doubt_lt"]
    return False


# ---------------------------------------------------------------- engine
def new_state(scenario: dict, studied: list[str]) -> dict:
    return {
        "doubt": 0, "vague_streak": 0, "vague_count": 0, "turn": 0, "studied": set(studied),
        "ledger": {}, "caught": [],
        "suspicion": {c["id"]: 0 for c in scenario["cast"]},
    }


def _cast(scenario: dict) -> dict:
    return {c["id"]: c for c in scenario["cast"]}


def apply_option(state: dict, scenario: dict, node: dict, option: dict) -> dict:
    """Mutates state (except suspicion copy) and returns the reaction the client should show."""
    speaker = node.get("speaker")
    cast = _cast(scenario)
    char = cast.get(speaker) or {}
    ctx = {"studied": state["studied"], "vague_streak": state["vague_streak"],
           "speaker": speaker, "personality": char.get("personality"),
           "ledger": state["ledger"], "turn": state["turn"], "doubt": state["doubt"]}
    ok = eval_cond(option.get("works_if"), ctx)
    before = state["doubt"]
    delta = option["doubt_on_success"] if ok else option["doubt_on_fail"]
    state["doubt"] = max(0, min(MAX_DOUBT, before + delta))

    style = option["kind"]
    for cid in option.get("commits") or []:
        if cid in state["ledger"]:
            continue
        improvised = style == "risk" or (style == "commit" and option.get("topic") not in state["studied"])
        state["ledger"][cid] = {"improvised": improvised, "checked": False, "survived": None}

    if style == "evade":
        state["vague_count"] += 1
        if ok:
            state["vague_streak"] += 1
    else:
        state["vague_streak"] = 0
    state["turn"] += 1
    if speaker in state["suspicion"]:
        state["suspicion"][speaker] += state["doubt"] - before
    return {"ok": ok, "line": option["line_if_pass"] if ok else option["line_if_fail"],
            "delta": state["doubt"] - before}


def run_check(state: dict, node: dict) -> dict:
    caught, confirmed = [], []
    for cid in node.get("claims") or []:
        entry = state["ledger"].get(cid)
        if not entry or entry["checked"]:
            continue
        if entry["improvised"]:
            state["doubt"] = min(MAX_DOUBT, state["doubt"] + int(node.get("penalty", 1)))
            entry["survived"] = False
            caught.append(cid)
        else:
            entry["survived"] = True
            confirmed.append(cid)
        entry["checked"] = True
    if caught and node.get("speaker") in state["suspicion"]:
        state["suspicion"][node["speaker"]] += len(caught)
    state["caught"].extend(caught)
    return {"caught": caught, "confirmed": confirmed,
            "line": node.get("bluff_line") if caught else node.get("confirm_line", "")}


def verdict(suspicion: int) -> str:
    if suspicion < 2:
        return "bought_it"
    if suspicion <= 3:
        return "suspicious"
    return "sees_through"


def finish(state: dict, scenario: dict) -> dict:
    # A room notices when you never actually said anything: too many vague picks add doubt.
    if not state.get("_evasive_done"):
        state["_evasive_done"] = True
        if state["vague_count"] * 2 >= max(1, scenario["turns"]):
            state["doubt"] = min(MAX_DOUBT, state["doubt"] + 2)
    bought = sum(1 for c in scenario["cast"] if state["suspicion"][c["id"]] < 2)
    busted = state["doubt"] >= MAX_DOUBT
    convinced = (not busted) and bought >= math.ceil(len(scenario["cast"]) / 2)
    outcome = "busted" if busted else ("convinced" if convinced else "partial")
    return {"outcome": outcome, "bought": bought,
            "verdicts": {c["id"]: verdict(state["suspicion"][c["id"]]) for c in scenario["cast"]}}


def walk(scenario: dict, studied: list[str], pick) -> dict:
    """Replay the linear scene. `pick(node, options) -> option`. Returns final state + outcome."""
    state = new_state(scenario, studied)
    nodes = scenario["nodes"]
    node = nodes["n1"]
    for _ in range(64):
        if node.get("kind") == "end":
            break
        if node.get("kind") == "check":
            run_check(state, node)
        else:
            apply_option(state, scenario, node, pick(node, node["options"]))
        node = nodes[node["next"]]
    return {"state": state, **finish(state, scenario)}


def _dfs_can_win(scenario: dict, studied: list[str]) -> tuple[bool, bool, bool]:
    """(survive, convince, varied) — varied = a convincing path whose answers actually varied."""
    nodes = scenario["nodes"]
    seen: set = set()
    result = {"survive": False, "convince": False, "varied": False}

    def key(node_id, st, kinds):
        return (node_id, st["doubt"], st["vague_streak"],
                tuple(sorted((k, v["improvised"]) for k, v in st["ledger"].items())),
                tuple(sorted(st["suspicion"].items())), st["turn"], tuple(kinds[-2:]))

    def dfs(node_id: str, st: dict, kinds: list) -> None:
        if result["varied"]:
            return
        k = key(node_id, st, kinds)
        if k in seen:
            return
        seen.add(k)
        node = nodes[node_id]
        if node.get("kind") == "end":
            fin = finish(st, scenario)
            if fin["outcome"] in ("convinced", "partial"):
                result["survive"] = True
            if fin["outcome"] == "convinced":
                result["convince"] = True
                if len(set(kinds)) >= 3 and all(a != b for a, b in zip(kinds, kinds[1:])):
                    result["varied"] = True
            return
        if node.get("kind") == "check":
            run_check(st, node)
            dfs(node["next"], st, kinds)
            return
        for opt in node["options"]:
            branch = copy.deepcopy(st)
            apply_option(branch, scenario, node, opt)
            dfs(node["next"], branch, kinds + [opt["kind"]])

    dfs("n1", new_state(scenario, studied), [])
    return result["survive"], result["convince"], result["varied"]


def _strategy(scenario: dict, studied: list[str], kind: str) -> dict:
    return walk(scenario, studied, lambda node, opts: next(o for o in opts if o["kind"] == kind))


# ---------------------------------------------------------------- validator
def validate_scenario(raw, difficulty: str, key: int) -> dict | None:
    try:
        label = str(raw.get("image_label", "")).strip().lower()
        if not LABEL_RE.match(label):
            return None
        cast_raw = raw.get("cast") or []
        if not 1 <= len(cast_raw) <= 4:
            return None
        cast, cast_ids = [], set()
        for c in cast_raw:
            cid = str(c.get("id", "")).strip().lower()
            if not re.match(r"^[a-z][a-z0-9_]{1,23}$", cid) or cid in cast_ids:
                return None
            personality = str(c.get("personality", "")).strip().lower()
            if personality not in PERSONALITIES:
                return None
            name = _txt(c.get("name"), 24)
            if not name:
                return None
            cast_ids.add(cid)
            cast.append({"id": cid, "name": name, "image_label": _txt(c.get("image_label"), 32),
                         "role": _txt(c.get("role"), 80), "personality": personality,
                         "hint": _txt(c.get("hint"), 120)})
        cover = raw.get("cover") or {}
        cover_out = {"role": _txt(cover.get("role"), 120), "setting": _txt(cover.get("setting"), 120),
                     "brief": _txt(cover.get("brief"), 240)}
        if not (cover_out["role"] and cover_out["setting"] and cover_out["brief"]):
            return None
        topics_raw = raw.get("topics") or []
        topics, topic_ids = [], set()
        for t in topics_raw:
            tid = str(t.get("id", "")).strip().lower()
            if not re.match(r"^t_[a-z0-9_]{1,24}$", tid) or tid in topic_ids:
                return None
            name = _txt(t.get("name"), 60)
            card = _txt(t.get("study_card"), 240)
            if not (name and len(card) >= 20):
                return None
            topic_ids.add(tid)
            topics.append({"id": tid, "name": name, "study_card": card})
        prep = [str(x).strip().lower() for x in (raw.get("prep_cards") or [])]
        if len(set(prep)) != len(prep) or not set(prep) <= topic_ids or len(prep) < PREP_PICK + 2:
            return None
        tags = [str(t).strip().lower() for t in (raw.get("tags") or [])]
        if not MIN_TAGS <= len(tags) <= MAX_TAGS or len(set(tags)) != len(tags):
            return None
        if any(not TAG_RE.match(t) for t in tags):
            return None
        palette = set(tags)
        used_tags: set[str] = set()
        nodes_raw = raw.get("nodes") or []
        nodes: dict[str, dict] = {}
        scene_count = 0
        committed: set[str] = set()
        checks: dict[str, int] = {}
        for n in nodes_raw:
            nid = str(n.get("id", "")).strip()
            if not nid or nid in nodes:
                return None
            kind = n.get("kind") or "scene"
            if kind == "end":
                nodes[nid] = {"id": nid, "kind": "end", "line": _txt(n.get("line"), 240), "next": None}
                continue
            nxt = str(n.get("next", "")).strip()
            if not nxt:
                return None
            if kind == "check":
                claims = [str(c).strip() for c in (n.get("claims") or [])]
                if not claims:
                    return None
                for cid in claims:
                    checks[cid] = checks.get(cid, 0) + 1
                nodes[nid] = {"id": nid, "kind": "check", "speaker": str(n.get("speaker", "")).strip().lower(),
                              "claims": claims, "line": _txt(n.get("line"), 240),
                              "bluff_line": _txt(n.get("bluff_line"), 240),
                              "confirm_line": _txt(n.get("confirm_line"), 240),
                              "penalty": max(1, min(2, int(n.get("penalty", 1)))), "next": nxt}
                continue
            speaker = str(n.get("speaker", "")).strip().lower()
            if speaker not in cast_ids:
                return None
            opts = n.get("options") or []
            if len(opts) != 4:
                return None
            kinds = [o.get("kind") for o in opts]
            if sorted(kinds) != sorted(KINDS):
                return None
            node_tags = [str(o.get("tag", "")).strip().lower() for o in opts]
            if len(set(node_tags)) != len(node_tags) or any(t not in palette for t in node_tags):
                return None
            built = []
            for o in opts:
                kind = o["kind"]
                tag = str(o.get("tag", "")).strip().lower()
                used_tags.add(tag)
                topic = o.get("topic")
                topic = str(topic).strip().lower() if topic else None
                if kind == "commit" and topic not in topic_ids:
                    return None
                if kind != "commit" and topic is not None:
                    return None
                cond = o.get("works_if") or {}
                if not _valid_cond(cond):
                    return None
                ds = int(o.get("doubt_on_success", 0))
                df = int(o.get("doubt_on_fail", 1))
                if not -2 <= ds <= 2 or not 0 <= df <= 3:
                    return None
                if kind == "risk" and ds != -1:
                    return None
                commits = [str(c).strip() for c in (o.get("commits") or [])]
                committed.update(commits)
                built.append({"kind": kind, "tag": tag, "topic": topic, "text": _txt(o.get("text"), 160),
                              "commits": commits, "works_if": cond, "doubt_on_success": ds,
                              "doubt_on_fail": df, "line_if_pass": _txt(o.get("line_if_pass"), 200),
                              "line_if_fail": _txt(o.get("line_if_fail"), 200),
                              "rationale": _txt(o.get("rationale"), 200) or KIND_WHY.get(kind, "")})
            if any(not o["text"] or not o["line_if_pass"] or not o["line_if_fail"] for o in built):
                return None
            scene_count += 1
            nodes[nid] = {"id": nid, "kind": "scene", "speaker": speaker, "line": _txt(n.get("line"), 240),
                          "options": built, "next": nxt}
        if not MIN_SCENE_NODES <= scene_count <= MAX_SCENE_NODES:
            return None
        # linear chain from n1 must cover every node and end at an end node
        if "n1" not in nodes:
            return None
        seen, cur = [], "n1"
        while cur in nodes and cur not in seen:
            seen.append(cur)
            cur = nodes[cur].get("next")
        if set(seen) != set(nodes) or not nodes[seen[-1]].get("kind") == "end":
            return None
        # every committed claim comes due exactly once
        if committed != set(checks) or any(v != 1 for v in checks.values()):
            return None
        if not any(n.get("kind") == "check" for n in nodes.values()):
            return None
        verdicts = raw.get("verdicts") or {}
        verdicts_out = {}
        for c in cast:
            v = verdicts.get(c["id"]) or {}
            verdicts_out[c["id"]] = {band: _txt(v.get(band), 160) for band in
                                     ("bought_it", "suspicious", "sees_through")}
        if used_tags != palette:
            return None   # every tag the AI invents must actually appear on an option
        scenario = {"id": f"ad{difficulty[0]}-{key}", "difficulty": difficulty, "image_label": label,
                    "cover": cover_out, "cast": cast, "topics": topics, "prep_cards": prep,
                    "tags": tags, "turns": scene_count, "nodes": nodes, "verdicts": verdicts_out}
        # play-through guarantees: survivable, convincing, and the winning move must VARY
        survive = convince = varied = False
        prep_list = sorted(prep)
        for combo in combinations(prep, PREP_PICK):
            s, c, v = _dfs_can_win(scenario, list(combo))
            survive = survive or s
            convince = convince or c
            varied = varied or v
            if survive and convince and varied:
                break   # stop early: the expensive DFS otherwise runs for every topic combo
        if not (survive and convince and varied):
            return None
        if _strategy(scenario, prep_list[:PREP_PICK], "evade")["outcome"] != "busted":
            return None
        if _strategy(scenario, prep_list[:PREP_PICK], "risk")["outcome"] != "busted":
            return None
        return scenario
    except (TypeError, ValueError, KeyError, StopIteration):
        return None


# ---------------------------------------------------------------- assembly (chunked generation)
def assemble_nodes(scene_nodes: list[dict], checks: list[dict], end_line: str) -> list[dict]:
    """Concatenate scene chunks into one linear chain: n1..nN -> c1..cK -> end.

    Chunk responses never carry ids/links; the engine owns the ordering so a split response is
    indistinguishable from a single one.
    """
    out: list[dict] = []
    for i, n in enumerate(scene_nodes, 1):
        out.append({
            "id": f"n{i}", "kind": "scene",
            "speaker": n.get("speaker"), "line": n.get("line"),
            "options": [{**o, "next": None} for o in (n.get("options") or [])],
            "next": None,
        })
    for j, c in enumerate(checks, 1):
        out.append({
            "id": f"c{j}", "kind": "check", "speaker": c.get("speaker"),
            "claims": c.get("claims") or [], "line": c.get("line") or "",
            "bluff_line": c.get("bluff_line") or "", "confirm_line": c.get("confirm_line") or "",
            "penalty": c.get("penalty") or 1, "next": None,
        })
    out.append({"id": "end", "kind": "end", "line": end_line or "The evening moves on.", "next": None})
    for a, b in zip(out, out[1:]):
        a["next"] = b["id"]
    return out


# ---------------------------------------------------------------- bundled bank
def _o(style, text, ok, fail, *, topic=None, commits=(), cond=None, ds=0, df=1):
    return {"kind": KIND_OF[style], "text": text, "commits": list(commits),
            "topic": topic, "works_if": cond or {}, "doubt_on_success": ds, "doubt_on_fail": df,
            "line_if_pass": ok, "line_if_fail": fail, "tag": ""}


def _scene(nid, speaker, line, nxt, specific, vague, bold, deflect):
    return {"id": nid, "kind": "scene", "speaker": speaker, "line": line, "next": nxt,
            "options": [specific, vague, bold, deflect]}


def _check(nid, speaker, claims, nxt, *, line="Wait — something you said earlier.", penalty=1):
    return {"id": nid, "kind": "check", "speaker": speaker, "claims": claims, "line": line,
            "bluff_line": "…that doesn't line up with what I know.",
            "confirm_line": "Good — that checks out.", "penalty": penalty, "next": nxt}


def _end(nid, line):
    return {"id": nid, "kind": "end", "line": line, "next": None}


def _verdicts(ids, buy, sus, see):
    return {i: {"bought_it": buy, "suspicious": sus, "sees_through": see} for i in ids}


def _scenario(difficulty, label, cover, cast, topics, prep, scenes, checks, end_line, tags):
    ids = [c["id"] for c in cast]
    tag_of = dict(zip(KINDS, tags))
    # Bold only works when your *previous* confident claim gave you credibility: it cannot be
    # spammed, and always-bold runs must fail (checked by the validator).
    for i, s in enumerate(scenes):
        prev_claim = scenes[i - 1]["specific"]["commits"][0] if i > 0 else None
        s["bold"]["works_if"] = {"claim_present": prev_claim} if prev_claim else {"turn_lt": 0}
        for slot in ("specific", "vague", "bold", "deflect"):
            s[slot]["tag"] = tag_of[s[slot]["kind"]]
    nodes, n = [], len(scenes)
    for i, s in enumerate(scenes):
        nxt = f"n{i + 2}" if i + 1 < n else "c1"
        nodes.append(_scene(f"n{i + 1}", s["speaker"], s["line"], nxt, s["specific"], s["vague"],
                            s["bold"], s["deflect"]))
    for j, c in enumerate(checks):
        nxt = f"c{j + 2}" if j + 1 < len(checks) else "end"
        nodes.append(_check(f"c{j + 1}", c["speaker"], c["claims"], nxt))
    nodes.append(_end("end", end_line))
    return {"image_label": label, "cover": cover, "cast": cast, "topics": topics,
            "prep_cards": prep, "tags": list(tags), "nodes": nodes,
            "verdicts": _verdicts(ids, "Seemed genuine.", "Kept glancing at you.",
                                  "Told the others you were faking.")}


def _mk(difficulty, label, setting, brief, cast, topics, prep, scenes, checks, end_line, tags):
    return _scenario(difficulty, label,
                     {"role": "a guest who belongs here", "setting": setting, "brief": brief},
                     cast, topics, prep, scenes, checks, end_line, tags)


# Easy — one curious observer.
_E_STUDY = [
    {"id": "t_impressionism", "name": "Impressionism", "study_card": "Monet and Renoir broke brushstrokes into light; the unfinished look was deliberate. Water Lilies is a safe reference."},
    {"id": "t_auction", "name": "Auctions", "study_card": "Paddle numbers, buyer's premium, and never quote prices in the room. 'Provenance' is the word that earns respect."},
    {"id": "t_names", "name": "Names", "study_card": "Elena Voss curates the modern wing. Praising her taste is safe; claiming her friendship is not."},
    {"id": "t_provenance", "name": "Provenance", "study_card": "A work's ownership history. Asking where a piece came from is always a strong question."},
    {"id": "t_etiquette", "name": "Gathering etiquette", "study_card": "Ask one question before offering an opinion, and never volunteer your credentials."},
]
_E_SCENES = [
    {"speaker": "ravi", "line": "You don't strike me as the gallery type.",
     "specific": _o("specific", "Guilty — I lose afternoons to the Impressionists.", "A Monet person, then.", "I doubt that.", topic="t_impressionism", commits=["liked_monet"], cond={"studied": "t_impressionism"}),
     "vague": _o("vague", "You know how these evenings go.", "Mm. Vague.", "That's not an answer.", cond={"vague_streak_lt": VAGUE_LIMIT}),
     "bold": _o("bold", "I advise this collection, actually.", "— Really? Do tell.", "Do you, now.", commits=["advises_collection"], cond={"target_personality": "curious"}, ds=-1, df=2),
     "deflect": _o("deflect", "Enough about me — what's your favourite piece?", "Mine? The early Schiele—", "I asked you a question.", cond={"target_personality": "curious"})},
    {"speaker": "ravi", "line": "What did you make of the Monet room?",
     "specific": _o("specific", "The Water Lilies room? I stayed until they dimmed the lights.", "That's the right way to see them.", "You didn't strike me as a regular.", topic="t_impressionism", commits=["saw_water_lilies"], cond={"studied": "t_impressionism"}),
     "vague": _o("vague", "It's a lot to take in, isn't it.", "It is.", "Very safe answer.", cond={"vague_streak_lt": VAGUE_LIMIT}),
     "bold": _o("bold", "I bought two last spring, sight unseen.", "Bold move. I respect it.", "The market disagrees.", commits=["bought_monet"], cond={"target_personality": "curious"}, ds=-1, df=2),
     "deflect": _o("deflect", "What did you think, honestly?", "Honestly? The lighting.", "You're dodging.", cond={"target_personality": "curious"})},
    {"speaker": "ravi", "line": "The spring auction was wild, wasn't it?",
     "specific": _o("specific", "I was in the room for the third lot — the bidding turned fast.", "Then you saw the paddle war.", "You weren't there, were you.", topic="t_auction", commits=["at_spring_auction"], cond={"studied": "t_auction"}),
     "vague": _o("vague", "Wild is one word for it.", "It really is.", "You didn't go, did you.", cond={"vague_streak_lt": VAGUE_LIMIT}),
     "bold": _o("bold", "I drove the price up on purpose.", "That was you? Ha!", "Sure you did.", commits=["drove_price"], cond={"target_personality": "curious"}, ds=-1, df=2),
     "deflect": _o("deflect", "Were you bidding?", "Guilty. Lot forty.", "Stop deflecting.", cond={"target_personality": "curious"})},
    {"speaker": "ravi", "line": "Do you know Elena Voss? She curates the modern wing.",
     "specific": _o("specific", "I admire her taste more than I know her.", "That's the honest answer.", "You two are close, then?", topic="t_names", commits=["knows_elena"], cond={"studied": "t_names"}),
     "vague": _o("vague", "We've moved in the same circles.", "Mm.", "Circles.", cond={"vague_streak_lt": VAGUE_LIMIT}),
     "bold": _o("bold", "Elena and I go way back.", "Then you'll know about the next show.", "I'll ask her myself.", commits=["elena_friend"], cond={"target_personality": "curious"}, ds=-1, df=2),
     "deflect": _o("deflect", "What's your read on her next show?", "Ambitious. Too ambitious.", "I asked you.", cond={"target_personality": "curious"})},
    {"speaker": "ravi", "line": "What keeps drawing you back to this world?",
     "specific": _o("specific", "The questions, mostly. I always ask where a piece came from.", "That's the connoisseur's habit.", "You sound like you read that somewhere.", topic="t_provenance", commits=["asks_provenance"], cond={"studied": "t_provenance"}),
     "vague": _o("vague", "It's hard to explain.", "Try.", "You're being evasive.", cond={"vague_streak_lt": VAGUE_LIMIT}),
     "bold": _o("bold", "The money, obviously.", "At least you're honest.", "How vulgar.", commits=["here_for_money"], cond={"target_personality": "curious"}, ds=-1, df=2),
     "deflect": _o("deflect", "What keeps you coming back?", "The people. Always the people.", "That's a deflection.", cond={"target_personality": "curious"})},
]
_E_CHECKS = [{"speaker": "ravi", "claims": ["liked_monet", "advises_collection", "saw_water_lilies",
                                            "bought_monet", "at_spring_auction", "drove_price",
                                            "knows_elena", "elena_friend", "asks_provenance",
                                            "here_for_money"]}]
_E_CAST = [{"id": "ravi", "name": "Ravi", "image_label": "student_1", "role": "a fellow guest",
            "personality": "curious", "hint": "Asks about your taste, then watches your hands."}]
_E = _mk("easy", "student_1", "a private art gathering",
         "You know nothing about art. Blend in and do not arouse suspicion.",
         _E_CAST, _E_STUDY, [t["id"] for t in _E_STUDY], _E_SCENES, _E_CHECKS,
         "Ravi raises his glass and drifts away.",
         tags=["name-drop", "play it vague", "brag", "turn it around"])

# Medium — two observers, compare notes.
_M_STUDY = [
    {"id": "t_runway", "name": "Runway maths", "study_card": "A margin is the multiple between cost and retail; founders are judged on it before anything else."},
    {"id": "t_churn", "name": "Churn", "study_card": "Monthly churn is lost customers over starting customers; below three percent is the number polite investors ask about."},
    {"id": "t_funding", "name": "Funding rounds", "study_card": "Pre-seed, seed, Series A. A 'bridge' is a short round to reach the next milestone."},
    {"id": "t_hiring", "name": "Early hiring", "study_card": "First ten hires set the culture; engineers before sales is the usual order at seed."},
    {"id": "t_growth", "name": "Growth loops", "study_card": "A loop compounds users without paid spend; referrals and shared docs are standard examples."},
    {"id": "t_competition", "name": "Competition", "study_card": "Founders never say 'no competition'; they describe an incumbent and a wedge."},
    {"id": "t_metrics", "name": "Investor metrics", "study_card": "ARR, burn multiple, and net revenue retention are the three numbers raised over dinner."},
]
_M_SCENES = [
    {"speaker": "ravi", "line": "Ray! Didn't expect to see you at a founder dinner.",
     "specific": _o("specific", "I'm mostly here for the runway conversations.", "A margins person. Rare.", "Odd thing to open with.", topic="t_runway", commits=["cares_runway"], cond={"studied": "t_runway"}),
     "vague": _o("vague", "You know how it is — the usual circuit.", "The usual circuit.", "That's not an answer.", cond={"vague_streak_lt": VAGUE_LIMIT}),
     "bold": _o("bold", "I raised a bridge last month, quietly.", "Quietly! Tell me more.", "Word travels fast, and that's not the word.", commits=["raised_bridge"], cond={"target_personality": "curious"}, ds=-1, df=2),
     "deflect": _o("deflect", "What did you think of the last round?", "Overpriced. Obviously.", "I asked you first.", cond={"target_personality": "curious"})},
    {"speaker": "jack", "line": "You're Ray? Jack. I hear you've been raising.",
     "specific": _o("specific", "We closed a modest seed — hiring two engineers first.", "Sensible. Engineers before sales.", "That's the safe script, isn't it.", topic="t_funding", commits=["seed_round"], cond={"studied": "t_funding"}),
     "vague": _o("vague", "Raising is always a process.", "A process. Right.", "Everyone says that.", cond={"vague_streak_lt": VAGUE_LIMIT}),
     "bold": _o("bold", "I could put money into your round if I liked it.", "Now that is interesting.", "You? A cheque?", commits=["offered_cheque"], cond={"target_personality": "narcissist"}, ds=-1, df=2),
     "deflect": _o("deflect", "What are you building these days?", "Something the market needs.", "Don't turn this on me.", cond={"target_personality": "narcissist"})},
    {"speaker": "ravi", "line": "Jack says you keep churn under three.",
     "specific": _o("specific", "Above three and the retention story falls apart.", "Exactly — someone who knows.", "Three? I heard otherwise.", topic="t_churn", commits=["churn_claim"], cond={"studied": "t_churn"}),
     "vague": _o("vague", "Churn is always a work in progress.", "Always is a dangerous word.", "You don't know the number.", cond={"vague_streak_lt": VAGUE_LIMIT}),
     "bold": _o("bold", "We run below one percent.", "Below one? Nobody runs below one.", "That's not believable.", commits=["churn_one"], cond={"target_personality": "curious"}, ds=-1, df=2),
     "deflect": _o("deflect", "What's your churn, Jack?", "Zero, apparently.", "Don't deflect onto Jack.", cond={"target_personality": "narcissist"})},
    {"speaker": "jack", "line": "Everyone here talks about growth loops. Do you?",
     "specific": _o("specific", "Referrals and shared documents are the honest loops.", "Honest. I like that.", "That's the textbook answer.", topic="t_growth", commits=["growth_loops"], cond={"studied": "t_growth"}),
     "vague": _o("vague", "Loops are fashionable this year.", "Fashionable. Sure.", "You're avoiding the question.", cond={"vague_streak_lt": VAGUE_LIMIT}),
     "bold": _o("bold", "Our loop doesn't exist yet. That's the point.", "Now that I believe.", "Come back when it exists.", commits=["no_loop"], cond={"target_personality": "narcissist"}, ds=-1, df=2),
     "deflect": _o("deflect", "You tell me yours first.", "Gladly. Retargeting.", "I'm not playing that game.", cond={"target_personality": "narcissist"})},
    {"speaker": "ravi", "line": "Jack mentioned you've hired ten people already.",
     "specific": _o("specific", "Engineers first, sales once the loop works.", "That's the right order.", "Ten already? That's fast.", topic="t_hiring", commits=["hired_ten"], cond={"studied": "t_hiring"}),
     "vague": _o("vague", "Hiring is a moving target.", "Moving target. Hm.", "How many, though?", cond={"vague_streak_lt": VAGUE_LIMIT}),
     "bold": _o("bold", "I hire only people smarter than me.", "Which is everyone, then.", "That's not an answer.", commits=["smarter_hires"], cond={"target_personality": "curious"}, ds=-1, df=2),
     "deflect": _o("deflect", "Who was your best hire?", "Oh, a designer from Berlin.", "Answer for yourself.", cond={"target_personality": "curious"})},
    {"speaker": "jack", "line": "So who's the competition? Everyone has one.",
     "specific": _o("specific", "One incumbent, and we wedge in on price transparency.", "A wedge. Good.", "Names or it didn't happen.", topic="t_competition", commits=["competition_claim"], cond={"studied": "t_competition"}),
     "vague": _o("vague", "Competition is mostly noise.", "Noise. Of course.", "That's what people say when they're losing.", cond={"vague_streak_lt": VAGUE_LIMIT}),
     "bold": _o("bold", "We have none. That's why we'll win.", "Famous last words.", "There is always competition.", commits=["no_competition"], cond={"target_personality": "narcissist"}, ds=-1, df=2),
     "deflect": _o("deflect", "You've backed a few — who worries you?", "Most of my portfolio.", "Deflection.", cond={"target_personality": "narcissist"})},
    {"speaker": "ravi", "line": "Quick one — what's your burn multiple?",
     "specific": _o("specific", "Under one point five, and falling.", "Falling is the word that matters.", "That's a guess, isn't it.", topic="t_metrics", commits=["burn_claim"], cond={"studied": "t_metrics"}),
     "vague": _o("vague", "Burn is a private number.", "Private. Convenient.", "You don't know it.", cond={"vague_streak_lt": VAGUE_LIMIT}),
     "bold": _o("bold", "I don't track it. Growth hides everything.", "That's either brave or insane.", "That's insane.", commits=["no_burn_tracking"], cond={"target_personality": "narcissist"}, ds=-1, df=2),
     "deflect": _o("deflect", "What's Jack's burn multiple?", "Better than yours, clearly.", "Don't put this on me.", cond={"target_personality": "narcissist"})},
]
_M_CHECKS = [
    {"speaker": "jack", "claims": ["cares_runway", "raised_bridge", "seed_round", "offered_cheque", "churn_one", "no_loop", "no_competition", "no_burn_tracking"]},
    {"speaker": "ravi", "claims": ["churn_claim", "growth_loops", "hired_ten", "smarter_hires", "competition_claim", "burn_claim"]},
]
_M_CAST = [
    {"id": "ravi", "name": "Ravi", "image_label": "male_1", "role": "another founder",
     "personality": "curious", "hint": "Friendly, repeats what others said to test you."},
    {"id": "jack", "name": "Jack", "image_label": "rich_1", "role": "an investor",
     "personality": "narcissist", "hint": "Loves being flattered; remembers every number."},
]
_M = _mk("medium", "boss_2", "a founders' dinner",
         "You are posing as a funded founder. Two people will compare notes about you.",
         _M_CAST, _M_STUDY, [t["id"] for t in _M_STUDY], _M_SCENES, _M_CHECKS,
         "Jack checks his watch; Ravi gives you a long look.",
         tags=["metrics talk", "hand-wave", "big claim", "bounce it back"])

# Hard — three observers, one of whom knows the real you.
_H_STUDY = [
    {"id": "t_cardio", "name": "Cardiology overview", "study_card": "The heart's four chambers, the difference between rate and rhythm, and why 'ejection fraction' is the phrase juniors stumble on."},
    {"id": "t_trial", "name": "Reading a trial", "study_card": "Sample size, controls, and endpoints. Always ask who was excluded before praising the result."},
    {"id": "t_triage", "name": "Triage", "study_card": "Airway, breathing, circulation. Escalate early; 'watch and wait' is only safe with a clear baseline."},
    {"id": "t_ethics", "name": "Research ethics", "study_card": "Consent, conflicts of interest, and who owns the data. Ethics questions at a gala are a test of humility."},
    {"id": "t_pharma", "name": "Medication basics", "study_card": "Half-life, interactions, adherence. Never quote dosages at a social gathering — ask about adherence instead."},
    {"id": "t_publish", "name": "Publishing", "study_card": "Peer review, replication, impact factor. Juniors overstate journals; seniors talk about replication."},
    {"id": "t_mentor", "name": "Mentorship", "study_card": "Every senior clinician has a mentor story. Asking about theirs is the safest move in the room."},
    {"id": "t_conference", "name": "Conferences", "study_card": "Posters, plenaries, and the corridor conversations that matter more than the talks."},
    {"id": "t_hospital", "name": "Hospital life", "study_card": "Handovers, rounds, and the difference between a registrar and a consultant in most systems."},
]
_H_SCENES = [
    {"speaker": "jack", "line": "Junior Dr Ray! Good to see you. What did you think of my new paper?",
     "specific": _o("specific", "The trial design was clean — I want to reread the exclusion criteria.", "Careful reader. I like that.", "You haven't read it, have you.", topic="t_trial", commits=["read_paper"], cond={"studied": "t_trial"}),
     "vague": _o("vague", "There's a lot to digest in it.", "Digest. Yes.", "That's a non-answer.", cond={"vague_streak_lt": VAGUE_LIMIT}),
     "bold": _o("bold", "I'd have run it differently, honestly.", "Oh? Do tell.", "You'd have run it differently?", commits=["critiques_paper"], cond={"target_personality": "narcissist"}, ds=-1, df=2),
     "deflect": _o("deflect", "What did the reviewers say first?", "They loved it, of course.", "Don't turn my question around.", cond={"target_personality": "narcissist"})},
    {"speaker": "dan", "line": "Ray, isn't it? Dan. You used to work somewhere quite different, didn't you?",
     "specific": _o("specific", "I moved from a smaller unit — different rhythm entirely.", "A smaller unit. Which one?", "That's not what I heard.", topic="t_hospital", commits=["moved_units"], cond={"studied": "t_hospital"}),
     "vague": _o("vague", "I've moved around a bit.", "A bit.", "You're being vague, Ray.", cond={"vague_streak_lt": VAGUE_LIMIT}),
     "bold": _o("bold", "I ran the whole department, actually.", "The whole department.", "That's a strange thing to claim.", commits=["ran_department"], cond={"target_personality": "skeptical"}, ds=-1, df=2),
     "deflect": _o("deflect", "Where did you train, Dan?", "Johns Hopkins. But we were talking about you.", "Don't deflect.", cond={"target_personality": "skeptical"})},
    {"speaker": "ravi", "line": "Jack's been telling me about your cardiology work.",
     "specific": _o("specific", "Rate versus rhythm is the bit I always come back to.", "A clinician's answer.", "You sound like a textbook.", topic="t_cardio", commits=["cardio_claim"], cond={"studied": "t_cardio"}),
     "vague": _o("vague", "The heart is endlessly interesting.", "Endlessly.", "That's empty, Ray.", cond={"vague_streak_lt": VAGUE_LIMIT}),
     "bold": _o("bold", "I've published more on it than Jack has.", "That's a bold thing to say in this room.", "I doubt that very much.", commits=["more_published"], cond={"target_personality": "curious"}, ds=-1, df=2),
     "deflect": _o("deflect", "What's Jack's work like, honestly?", "Thorough. Slow, but thorough.", "Answer for yourself.", cond={"target_personality": "curious"})},
    {"speaker": "jack", "line": "And how would you handle a patient who refuses the new medication?",
     "specific": _o("specific", "First ask why — adherence usually hides a cost or a fear.", "Good. That's the real answer.", "That's the polished answer.", topic="t_pharma", commits=["adherence_answer"], cond={"studied": "t_pharma"}),
     "vague": _o("vague", "It depends on the patient.", "It always depends.", "That's a dodge.", cond={"vague_streak_lt": VAGUE_LIMIT}),
     "bold": _o("bold", "I'd override them and document it.", "You'd override consent?", "That's not defensible.", commits=["override_claim"], cond={"target_personality": "narcissist"}, ds=-1, df=2),
     "deflect": _o("deflect", "What would you do, Jack?", "Exactly what you'd expect.", "Don't put this on me.", cond={"target_personality": "narcissist"})},
    {"speaker": "dan", "line": "Ethics gets skipped at these things. Do you ever think about consent?",
     "specific": _o("specific", "Constantly — especially who owns the data afterwards.", "That's the question people avoid.", "That's a rehearsed line.", topic="t_ethics", commits=["ethics_claim"], cond={"studied": "t_ethics"}),
     "vague": _o("vague", "It's complicated, isn't it.", "Complicated. Yes.", "You're avoiding it.", cond={"vague_streak_lt": VAGUE_LIMIT}),
     "bold": _o("bold", "Consent is mostly paperwork.", "Mostly paperwork?", "That's a chilling thing to say.", commits=["consent_paperwork"], cond={"target_personality": "skeptical"}, ds=-1, df=2),
     "deflect": _o("deflect", "How does your unit handle it?", "Rigorously. Unlike some.", "Don't deflect, Ray.", cond={"target_personality": "skeptical"})},
    {"speaker": "ravi", "line": "Do you think the trial should be replicated?",
     "specific": _o("specific", "Always. Replication is the only honest test.", "That's a senior's answer.", "That's what you're supposed to say.", topic="t_publish", commits=["replication_claim"], cond={"studied": "t_publish"}),
     "vague": _o("vague", "Replication is expensive, isn't it.", "Expensive. Yes.", "That's a dodge.", cond={"vague_streak_lt": VAGUE_LIMIT}),
     "bold": _o("bold", "Replication is a waste of money.", "You'll say that in front of Jack?", "That's indefensible.", commits=["replication_waste"], cond={"target_personality": "curious"}, ds=-1, df=2),
     "deflect": _o("deflect", "Has anyone here replicated a study?", "Jack has. Twice.", "Don't deflect.", cond={"target_personality": "curious"})},
    {"speaker": "jack", "line": "Last one, Ray. Who mentored you? Everyone has one.",
     "specific": _o("specific", "A registrar who taught me to escalate early.", "That's the right lesson.", "That's the safe answer.", topic="t_mentor", commits=["mentor_claim"], cond={"studied": "t_mentor"}),
     "vague": _o("vague", "A few people, over the years.", "A few people.", "You don't have one, do you.", cond={"vague_streak_lt": VAGUE_LIMIT}),
     "bold": _o("bold", "I mentored myself, honestly.", "Of course you did.", "That explains a lot.", commits=["self_mentored"], cond={"target_personality": "narcissist"}, ds=-1, df=2),
     "deflect": _o("deflect", "Who mentored you, Jack?", "The best. Naturally.", "Don't deflect.", cond={"target_personality": "narcissist"})},
]
_H_CHECKS = [
    {"speaker": "dan", "claims": ["moved_units", "ran_department", "ethics_claim", "consent_paperwork"]},
    {"speaker": "jack", "claims": ["read_paper", "critiques_paper", "more_published", "adherence_answer", "override_claim"]},
    {"speaker": "ravi", "claims": ["cardio_claim", "replication_claim", "replication_waste", "mentor_claim", "self_mentored"]},
]
_H_CAST = [
    {"id": "jack", "name": "Dr Jack", "image_label": "boss_1", "role": "a senior researcher",
     "personality": "narcissist", "hint": "Publishes loudly, hates being outdone."},
    {"id": "dan", "name": "Dr Dan", "image_label": "police_1", "role": "a sceptical clinician",
     "personality": "skeptical", "hint": "Knows the real you. Tests every claim twice."},
    {"id": "ravi", "name": "Dr Ravi", "image_label": "student_2", "role": "a junior colleague",
     "personality": "curious", "hint": "Asks what a real junior would ask."},
]
_H = _mk("hard", "owner_1", "a global medical gathering",
         "You are posing as a junior doctor. Three people will test the act — one knows the real you.",
         _H_CAST, _H_STUDY, [t["id"] for t in _H_STUDY], _H_SCENES, _H_CHECKS,
         "The group drifts towards the bar; Dan watches you leave.",
         tags=["cite the literature", "stay general", "one-up the room", "ask them back"])

BANK = {"easy": [_E], "medium": [_M], "hard": [_H]}
_BANK_CACHE: dict[str, list[dict]] = {}


def bank_pool(bucket: int) -> list[dict]:
    name = ("easy", "medium", "hard")[min(bucket, 2)]
    if name not in _BANK_CACHE:
        _BANK_CACHE[name] = [s for s in (validate_scenario(raw, name, k)
                                         for k, raw in enumerate(BANK[name])) if s]
    return _BANK_CACHE[name]
