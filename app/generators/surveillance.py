"""Surveillance: the first dark pillar.

A character is a small dialogue graph. Each character brings its own TAG PALETTE (Gemini
invents the tags per scenario), and every option carries an explicit outcome. The validator
then enforces variety, so no tag is ever "the answer": across the graph at least four
different tags must win, every winning tag must also lose somewhere, and two consecutive
turns on the optimal path must not share a winning tag.

Reveals (strength/weakness/goal) may only sit on optimal options, and every character must
be able to reveal all three. The client never receives outcomes before picking.
"""
import re
from collections import Counter

TAG_RE = re.compile(r"^[a-z][a-z '\-]{1,23}$")
OUTCOMES = ("optimal", "neutral", "bad")
SECRETS = ("strength", "weakness", "goal")
MIN_NODES = 4
MAX_NODES = 9
OPTIONS_PER_NODE = 4
MIN_WINNING_TAGS = 4
LABEL_RE = re.compile(r"^[a-z0-9_]{2,32}$")

# Labels the generator may pick when the admin has not uploaded any faces yet.
DEFAULT_IMAGE_LABELS = [
    "student_1", "student_2", "male_1", "male_2", "female_1", "female_2",
    "boss_1", "boss_2", "owner_1", "rich_1", "police_1", "neighbour_1",
]


def _txt(v, limit: int) -> str:
    s = " ".join(str(v or "").split())
    return s[:limit]


def _optimal_chain(nodes: dict) -> list[str]:
    """Tags along the path from n1 when the player always picks the optimal option."""
    tags, seen, cur = [], set(), "n1"
    while cur in nodes and cur not in seen:
        seen.add(cur)
        opt = next(o for o in nodes[cur]["options"] if o["outcome"] == "optimal")
        tags.append(opt["tag"])
        cur = opt["next"]
        if cur is None:
            break
    return tags


def validate_character(raw, bucket: int, key: int) -> dict | None:
    """LLM output -> character dict. Structural + variety checks; no single 'correct' dialogue."""
    try:
        label = str(raw.get("image_label", "")).strip().lower()
        if not LABEL_RE.match(label):
            return None
        name = _txt(raw.get("name"), 24)
        description = _txt(raw.get("description"), 240)
        personality = _txt(raw.get("personality"), 200)
        tone = _txt(raw.get("tone"), 60)
        read = _txt(raw.get("read"), 240)
        if not (1 <= len(name) <= 24 and 10 <= len(description) <= 240
                and 10 <= len(personality) <= 200 and 2 <= len(tone) <= 60
                and 20 <= len(read) <= 240):
            return None
        tags = [str(t).strip().lower() for t in (raw.get("tags") or [])]
        if not 4 <= len(tags) <= 5 or len(set(tags)) != len(tags):
            return None
        if any(not TAG_RE.match(t) for t in tags):
            return None
        palette = set(tags)
        secrets = {k: _txt((raw.get("secrets") or {}).get(k), 160) for k in SECRETS}
        if not all(secrets.values()):
            return None
        nodes_raw = raw.get("nodes") or []
        if not MIN_NODES <= len(nodes_raw) <= MAX_NODES:
            return None
        ids = {str(n.get("id", "")).strip() for n in nodes_raw}
        if "" in ids or len(ids) != len(nodes_raw):
            return None
        nodes: dict[str, dict] = {}
        revealed = set()
        winning: list[str] = []
        losing: list[str] = []
        for n in nodes_raw:
            nid = str(n["id"]).strip()
            text = _txt(n.get("text"), 400)
            opts = n.get("options") or []
            if not text or len(opts) != OPTIONS_PER_NODE:
                return None
            built, node_tags, outcomes = [], [], []
            for o in opts:
                tag = str(o.get("tag", "")).strip().lower()
                outcome = o.get("outcome")
                if tag not in palette or outcome not in OUTCOMES:
                    return None
                nxt = o.get("next")
                nxt = str(nxt).strip() if nxt else None
                reveal = o.get("reveal") or None
                if reveal not in (None,) + SECRETS:
                    return None
                if outcome != "optimal":
                    reveal = None   # only a working approach earns a secret
                elif reveal:
                    revealed.add(reveal)
                node_tags.append(tag)
                outcomes.append(outcome)
                built.append({
                    "text": _txt(o.get("text"), 160), "tag": tag, "outcome": outcome,
                    "next": nxt, "reveal": reveal, "rationale": _txt(o.get("rationale"), 200),
                })
            if len(set(node_tags)) != OPTIONS_PER_NODE:
                return None   # no duplicate tags in one turn: the player must read the line
            if outcomes.count("optimal") != 1 or "bad" not in outcomes or "neutral" not in outcomes:
                return None
            if any(not o["text"] or not o["rationale"] for o in built):
                return None
            winning.extend(o["tag"] for o in built if o["outcome"] == "optimal")
            losing.extend(o["tag"] for o in built if o["outcome"] == "bad")
            nodes[nid] = {"id": nid, "text": text, "options": built}
        for node in nodes.values():
            for o in node["options"]:
                if o["next"] is not None and o["next"] not in nodes:
                    return None
        if "n1" not in nodes:
            return None
        reachable, stack = set(), ["n1"]
        while stack:
            cur = stack.pop()
            if cur in reachable or cur not in nodes:
                continue
            reachable.add(cur)
            stack.extend(o["next"] for o in nodes[cur]["options"] if o["next"])
        if reachable != set(nodes):
            return None
        if revealed != set(SECRETS):
            return None
        if not any(o["next"] is None for node in nodes.values() for o in node["options"]):
            return None
        # Variety: no tag may be the permanent answer, and the answer must move around.
        if len(set(winning)) < MIN_WINNING_TAGS:
            return None
        counts = Counter(winning)
        if counts.most_common(1)[0][1] > len(winning) / 2:
            return None
        if any(t not in losing for t in winning):
            return None
        chain = _optimal_chain(nodes)
        if any(a == b for a, b in zip(chain, chain[1:])):
            return None
        return {
            "id": f"sv{bucket}-{key}", "image_label": label, "name": name, "description": description,
            "personality": personality, "tone": tone, "read": read, "tags": tags,
            "secrets": secrets, "nodes": nodes, "start": "n1",
        }
    except (TypeError, ValueError, AttributeError, StopIteration):
        return None


# ---------------------------------------------------------------- bundled fallback bank
def _o(text, tag, outcome, nxt=None, reveal=None, why=""):
    # The bank writes options without a reveal as _o(text, tag, outcome, next, why): a 5th
    # arg that is not a secret is the rationale, not a reveal.
    if reveal is not None and reveal not in SECRETS:
        why, reveal = reveal, None
    return {"text": text, "tag": tag, "outcome": outcome, "next": nxt, "reveal": reveal, "rationale": why}


def _n(nid, text, opts):
    return {"id": nid, "text": text, "options": opts}


BANK = {
    "easy": [{
        "image_label": "student_1", "name": "Ravi", "tone": "warm but guarded",
        "description": "You have shared classes for two years. Lately he has gone quiet and skipped study sessions.",
        "personality": "Friendly, avoids conflict, buries his problems until someone asks twice.",
        "read": "Warmth opens him early. Once he trusts you, plain talk and even a firm push land; near the end, praise does more than honesty.",
        "tags": ["warmth", "straight talk", "pressure", "praise"],
        "secrets": {
            "strength": "He has been building a small app at night, alone, for months.",
            "weakness": "He takes criticism to heart and hides when he feels judged.",
            "goal": "He wants a quiet, stable life where the people around him actually understand him.",
        },
        "nodes": [
            _n("n1", "Hey. Didn't expect to see you here.", [
                _o("Honestly, I came to check how you're doing.", "warmth", "optimal", "n2", "weakness",
                   "Open warmth lets him speak without defending himself."),
                _o("Let's skip the small talk. What's going on?", "straight talk", "neutral", "n1",
                   "Too direct too early; he deflects and waits."),
                _o("What are you hiding from me?", "pressure", "bad", "n1",
                   "Pressure makes him go quiet instead of open."),
                _o("You always had it together. What happened?", "praise", "bad", "n1",
                   "Praise when he feels low reads as sarcasm."),
            ]),
            _n("n2", "She called me careless last week. Maybe she's right.", [
                _o("She called you careless. What exactly did she say?", "straight talk", "optimal", "n3", "strength",
                   "Plain, specific questions work now that he has opened the door."),
                _o("That sounds like it stung. Take your time.", "warmth", "neutral", "n2",
                   "Kind, but too soft to move the story."),
                _o("So she left you? Wow.", "pressure", "bad", "n2",
                   "Poking the wound makes him retreat."),
                _o("She's wrong. You're perfect.", "praise", "bad", "n2",
                   "Emptiness reads as mockery to him."),
            ]),
            _n("n3", "I've been building a project at night. Nobody knows.", [
                _o("You've been dodging the details. Say it plainly.", "pressure", "optimal", "n4",
                   "He is invested now; a firm nudge gets the whole story."),
                _o("That's impressive. You're a natural builder.", "praise", "neutral", "n3",
                   "He shrugs it off; the moment stays put."),
                _o("Take your time, I'm here.", "warmth", "bad", "n3",
                   "At this point softness lets him drift away from the point."),
                _o("What are the technical details?", "straight talk", "bad", "n3",
                   "Dry specifics break the mood he finally let himself feel."),
            ]),
            _n("n4", "Thanks for hearing me out. I want a quiet life, you know?", [
                _o("You've always been the one who figures things out.", "praise", "optimal", None, "goal",
                   "A bit of belief in him closes the last door, even if it is half a manoeuvre."),
                _o("So what does a quiet life look like to you?", "straight talk", "neutral", "n4",
                   "He answers vaguely; the moment passes."),
                _o("A quiet life is for losers.", "pressure", "bad", "n4",
                   "Unkindness ends the openness."),
                _o("I'm here for you, always.", "warmth", "bad", "n4",
                   "He smiles but gives nothing more."),
            ]),
        ],
    }],
    "medium": [{
        "image_label": "boss_1", "name": "Cole", "tone": "clipped and professional",
        "description": "Your manager of two years. Sharp, guarded, and never says a word more than she needs to.",
        "personality": "Competent and controlling. Values candour, hates being managed upward, quietly proud.",
        "read": "Start blunt, then give her the respect she craves. Later a push works; at the end, pride opens the last door.",
        "tags": ["candour", "deference", "pressure", "flattery"],
        "secrets": {
            "strength": "She spots problems a month before anyone else and quietly fixes them.",
            "weakness": "She cannot delegate; she redoes her team's work at night and resents it.",
            "goal": "She wants the team to outgrow her so she can finally stop being the safety net.",
        },
        "nodes": [
            _n("n1", "You wanted to see me. Make it quick.", [
                _o("Straight to it: I need your read on the reorg.", "candour", "optimal", "n2",
                   "Directness matches her tempo; she gives you a real answer."),
                _o("I know how much this team leans on you.", "deference", "neutral", "n1",
                   "She accepts the respect but keeps the clock running."),
                _o("I know what really happened with the budget.", "pressure", "bad", "n1",
                   "Pressure makes her shut the meeting down."),
                _o("You're the sharpest person in this building.", "flattery", "bad", "n1",
                   "She marks flattery as agenda and narrows her eyes."),
            ]),
            _n("n2", "The reorg is a mess. I saw it coming in March.", [
                _o("You saw it in March. What did the rest of us miss?", "deference", "optimal", "n3", "strength",
                   "Respectful curiosity about her judgement gets past the professional mask."),
                _o("You should be running this place.", "flattery", "neutral", "n2",
                   "She has heard it before; it buys a pause, nothing else."),
                _o("Answer the question or I go over your head.", "pressure", "bad", "n2",
                   "Threats make her close ranks."),
                _o("Fine. Let's review the numbers directly.", "candour", "bad", "n2",
                   "The spreadsheet is her armour; she retreats into it."),
            ]),
            _n("n3", "The truth is I can't let go. I redo everyone's work at night.", [
                _o("Then name the real blocker. Why can't you hand it off?", "pressure", "optimal", "n4", "weakness",
                   "She has admitted the cost; a direct demand forces the honest answer."),
                _o("You don't have to justify yourself to me.", "candour", "neutral", "n3",
                   "Gracious, but she stays on the surface."),
                _o("You built this team. They'd be lost without you.", "deference", "bad", "n3",
                   "Flattery of her indispensability is exactly the trap she is trying to escape."),
                _o("Everyone knows you're the real boss here.", "flattery", "bad", "n3",
                   "She sees the manipulation and stops sharing."),
            ]),
            _n("n4", "I want the team to outgrow me. That's the whole point of this.", [
                _o("They would follow you anywhere. Say the word and they will.", "flattery", "optimal", None, "goal",
                   "It lands on her pride, and pride is what finally gets her to say it out loud."),
                _o("Then make that the actual plan, not a side hope.", "pressure", "neutral", "n4",
                   "She nods, but the moment cools."),
                _o("I'll draft the handover myself.", "candour", "bad", "n4",
                   "Taking over her work is the one thing she cannot stand."),
                _o("No one else could run this team.", "deference", "bad", "n4",
                   "It feeds the very thing she wants to escape; she closes up."),
            ]),
        ],
    }],
    "hard": [{
        "image_label": "owner_1", "name": "Ilya", "tone": "measured, unnerving",
        "description": "You have known the owner of the club for a year through work. Tonight he agreed to five minutes.",
        "personality": "Patient and watchful. Respects nerve and straight answers; reads early sympathy as weakness.",
        "read": "Open with nerve, then give him straight talk. Once he is invested, concern lands, and at the very end pride does the work.",
        "tags": ["nerve", "straight talk", "concern", "flattery"],
        "secrets": {
            "strength": "He built the club from nothing and knows every lever in it.",
            "weakness": "The place owns him; he has not slept a full night in years.",
            "goal": "He wants out within two years, to hand the club to his nephew and disappear.",
        },
        "nodes": [
            _n("n1", "You've got five minutes. Talk.", [
                _o("I'm not here to waste your time. Here's what I need.", "nerve", "optimal", "n2",
                   "He respects a person who states the ask and holds their ground."),
                _o("I'll keep this short and to the point.", "straight talk", "neutral", "n1",
                   "Reasonable, but it gives him nothing to test."),
                _o("I can see you're exhausted. I'm sorry.", "concern", "bad", "n1",
                   "Early sympathy reads as weakness and loses his interest."),
                _o("You're a legend in this scene.", "flattery", "bad", "n1",
                   "He has heard the line from every drunk in the room."),
            ]),
            _n("n2", "Everything here is something I built. Every corner.", [
                _o("You built this from nothing. So answer me straight.", "straight talk", "optimal", "n3", "strength",
                   "Acknowledging the work, then demanding a direct answer, earns real respect."),
                _o("I came to you and not the others. That says something.", "nerve", "neutral", "n2",
                   "He hears the flattery in it and waits."),
                _o("That sounds lonely. I'm here for you.", "concern", "bad", "n2",
                   "He hears therapy-speak and shuts the door."),
                _o("Must be nice to have all this.", "flattery", "bad", "n2",
                   "He hears envy dressed as praise."),
            ]),
            _n("n3", "You want the truth? This place runs me. I don't run it.", [
                _o("How long since you slept a full night?", "concern", "optimal", "n4", "weakness",
                   "Now that he has admitted it, being seen lands instead of stinging."),
                _o("Then change it. You're the one in charge.", "nerve", "neutral", "n3",
                   "He shrugs; willpower is not the missing piece."),
                _o("Then sell it and walk away.", "straight talk", "bad", "n3",
                   "Too simple, and it dismisses the thing he is carrying."),
                _o("You've earned every bit of this.", "flattery", "bad", "n3",
                   "He laughs without warmth."),
            ]),
            _n("n4", "Two years. Then my nephew takes it and I disappear.", [
                _o("This city answers to you. You could leave tomorrow if you wanted.", "flattery", "optimal", None, "goal",
                   "It feeds his pride exactly where he needs it to say the rest out loud."),
                _o("What would the first week look like?", "concern", "neutral", "n4",
                   "He considers it, then lets the thought drift."),
                _o("Walking away is the same as losing.", "nerve", "bad", "n4",
                   "He does not take the bait, and the moment closes."),
                _o("Two years is a long time to wait.", "straight talk", "bad", "n4",
                   "Practical, and it ends the honesty."),
            ]),
        ],
    }],
}


def bank_pool(bucket: int) -> list[dict]:
    name = ("easy", "medium", "hard")[min(bucket, 2)]
    return [c for c in (validate_character(raw, bucket, k) for k, raw in enumerate(BANK[name])) if c]
