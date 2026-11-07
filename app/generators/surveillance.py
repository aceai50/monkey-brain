"""Surveillance: the first dark pillar.

A character is a small dialogue graph. Every option carries an APPROACH tag, and the
character's trait sheet decides what works: approaches in ``responds_to`` are optimal,
approaches in ``shuts_down_to`` are bad, everything else merely stalls. Outcomes are
DERIVED from the trait sheet (never trusted from the LLM), so the rules always hold.

Reveals (strength/weakness/goal) may only sit on optimal options, and every character
must be able to reveal all three. The client never receives outcomes before picking.
"""
import re

APPROACHES = ("empathy", "flattery", "pressure", "honesty", "deflection")
SECRETS = ("strength", "weakness", "goal")
MIN_NODES = 4
MAX_NODES = 14
MAX_OPTIONS = 4
LABEL_RE = re.compile(r"^[a-z0-9_]{2,32}$")

# Labels the generator may pick when the admin has not uploaded any faces yet.
DEFAULT_IMAGE_LABELS = [
    "student_1", "student_2", "male_1", "male_2", "female_1", "female_2",
    "boss_1", "boss_2", "owner_1", "rich_1", "police_1", "neighbour_1",
]


def _txt(v, limit: int) -> str:
    s = " ".join(str(v or "").split())
    return s[:limit]


def validate_character(raw, bucket: int, key: int) -> dict | None:
    """LLM output -> character dict. Structural checks only: no single 'correct' dialogue."""
    try:
        label = str(raw.get("image_label", "")).strip().lower()
        if not LABEL_RE.match(label):
            return None
        name = _txt(raw.get("name"), 24)
        description = _txt(raw.get("description"), 240)
        personality = _txt(raw.get("personality"), 200)
        tone = _txt(raw.get("tone"), 60)
        if not (1 <= len(name) <= 24 and 10 <= len(description) <= 240
                and 10 <= len(personality) <= 200 and 2 <= len(tone) <= 60):
            return None
        responds = [a for a in (raw.get("responds_to") or []) if a in APPROACHES]
        shuts = [a for a in (raw.get("shuts_down_to") or []) if a in APPROACHES]
        if not responds or not shuts or set(responds) & set(shuts):
            return None
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
        for n in nodes_raw:
            nid = str(n["id"]).strip()
            text = _txt(n.get("text"), 400)
            opts = n.get("options") or []
            if not text or not 2 <= len(opts) <= MAX_OPTIONS:
                return None
            built, has_optimal, has_bad = [], False, False
            for o in opts:
                approach = o.get("approach")
                if approach not in APPROACHES:
                    return None
                outcome = "optimal" if approach in responds else ("bad" if approach in shuts else "neutral")
                nxt = o.get("next")
                nxt = str(nxt).strip() if nxt is not None else None
                reveal = o.get("reveal") or None
                if reveal not in (None,) + SECRETS:
                    return None
                if outcome != "optimal":
                    reveal = None   # only a working approach earns a secret
                elif reveal:
                    revealed.add(reveal)
                has_optimal = has_optimal or outcome == "optimal"
                has_bad = has_bad or outcome == "bad"
                built.append({
                    "text": _txt(o.get("text"), 160), "approach": approach, "outcome": outcome,
                    "next": nxt, "reveal": reveal, "rationale": _txt(o.get("rationale"), 200),
                })
            if not (has_optimal and has_bad) or any(not o["text"] or not o["rationale"] for o in built):
                return None
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
        return {
            "id": f"sv{bucket}-{key}", "image_label": label, "name": name, "description": description,
            "personality": personality, "tone": tone, "responds_to": responds,
            "shuts_down_to": shuts, "secrets": secrets, "nodes": nodes, "start": "n1",
        }
    except (TypeError, ValueError, AttributeError):
        return None


# ---------------------------------------------------------------- bundled fallback bank
def _o(text, approach, nxt=None, reveal=None, why=""):
    # The bank writes non-optimal options as _o(text, approach, next, why): a 4th arg that
    # isn't a valid secret is the rationale, not a reveal (only optimal picks reveal).
    if reveal is not None and reveal not in SECRETS:
        why, reveal = reveal, None
    return {"text": text, "approach": approach, "next": nxt, "reveal": reveal, "rationale": why}


def _n(nid, text, opts):
    return {"id": nid, "text": text, "options": opts}


BANK = {
    "easy": [{
        "image_label": "student_1", "name": "Ravi", "tone": "warm but guarded",
        "description": "You have shared classes for two years. Lately he has gone quiet and skipped study sessions.",
        "personality": "Friendly, avoids conflict, buries his problems until someone asks twice.",
        "responds_to": ["empathy", "honesty"],
        "shuts_down_to": ["pressure", "flattery"],
        "secrets": {
            "strength": "He has been building a small app at night, alone, for months.",
            "weakness": "He takes criticism to heart and hides when he feels judged.",
            "goal": "He wants a quiet, stable life where the people around him actually understand him.",
        },
        "nodes": [
            _n("n1", "Hey. Didn't expect to see you here.", [
                _o("Honestly, I came to check how you're doing.", "empathy", "n2", "weakness",
                   "An open, low-stakes question lets him speak without defending himself."),
                _o("You always had it together. What happened?", "flattery", "n1",
                   "He hears flattery and closes up; he does not trust praise when he feels low."),
                _o("What are you hiding from me?", "pressure", "n1",
                   "Pressure makes him go quiet instead of open."),
                _o("I'm not sure this was a good idea.", "deflection", "n1",
                   "Vague distance stalls him; he waits for you to lead."),
            ]),
            _n("n2", "She called me careless last week. Maybe she's right.", [
                _o("That sounds like it stung. What did she actually say?", "empathy", "n3", "strength",
                   "Naming the sting without judging it lets him admit what he has been doing."),
                _o("She's wrong. You're perfect.", "flattery", "n2",
                   "Emptiness reads as sarcasm to him."),
                _o("So she left you? Wow.", "pressure", "n2",
                   "Poking the wound makes him retreat."),
                _o("Hm.", "deflection", "n2",
                   "He waits for something real from you."),
            ]),
            _n("n3", "I've been building a project at night. Nobody knows.", [
                _o("That's real discipline. How long have you kept at it?", "empathy", "n4", None,
                   "He has been waiting for someone to ask about the work instead of the gossip."),
                _o("You're a genius, obviously.", "flattery", "n3",
                   "He shrugs the praise off; it lands as noise."),
                _o("You're just avoiding your real problems.", "pressure", "n3",
                   "He shuts down when accused."),
                _o("Which nights?", "deflection", "n3",
                   "A harmless detail question stalls the story."),
            ]),
            _n("n4", "Thanks for hearing me out. I want a quiet life, you know?", [
                _o("I get it. You want to be understood, not managed.", "empathy", None, "goal",
                   "He smiles: someone finally said it back to him."),
                _o("We should celebrate. You're a legend.", "flattery", "n4",
                   "He waves it off and the moment closes."),
                _o("A quiet life is for losers.", "pressure", "n4",
                   "Unkindness ends the openness."),
                _o("Maybe we talk later.", "deflection", "n4",
                   "He nods and lets the conversation go quiet."),
            ]),
        ],
    }],
    "medium": [{
        "image_label": "boss_1", "name": "Cole", "tone": "clipped and professional",
        "description": "Your manager of two years. Sharp, guarded, and never says a word more than she needs to.",
        "personality": "Competent and controlling. Values candour and hates being managed upward.",
        "responds_to": ["honesty", "deflection"],
        "shuts_down_to": ["pressure", "flattery"],
        "secrets": {
            "strength": "She spots problems a month before anyone else and quietly fixes them.",
            "weakness": "She cannot delegate; she redoes her team's work at night and resents it.",
            "goal": "She wants the team to outgrow her so she can finally stop being the safety net.",
        },
        "nodes": [
            _n("n1", "You wanted to see me. Make it quick.", [
                _o("Straight to it: I need your read on the reorg.", "honesty", "n2",
                   "Directness matches her tempo; she gives you a real answer."),
                _o("You're the sharpest person in this building.", "flattery", "n1",
                   "She marks flattery as agenda and narrows her eyes."),
                _o("I know what really happened with the budget.", "pressure", "n1",
                   "Pressure makes her shut the meeting down."),
                _o("No rush. How has your week been?", "deflection", "n1",
                   "She answers in one word and waits."),
            ]),
            _n("n2", "The reorg is a mess. I saw it coming in March.", [
                _o("How did you catch it so early? That kind of foresight is rare.", "honesty", "n3", "strength",
                   "Specific, sincere credit gets past her guard."),
                _o("You should be running this place.", "flattery", "n2",
                   "She has heard that line before; it buys nothing."),
                _o("Answer the question or I go over your head.", "pressure", "n2",
                   "Threats make her close ranks."),
                _o("Let's leave it there, then.", "deflection", "n2",
                   "She waits, unimpressed by vagueness."),
            ]),
            _n("n3", "The truth is I can't let go. I redo everyone's work at night.", [
                _o("That's a lot to carry. What's the real blocker for handing it off?", "honesty", "n4", "weakness",
                   "Naming the cost without pity lets her say what she actually wants."),
                _o("Just delegate. It isn't that hard.", "pressure", "n3",
                   "Being told the obvious dismisses her; she stops sharing."),
                _o("You're too good for them anyway.", "flattery", "n3",
                   "She dismisses the compliment as empty."),
                _o("Hm.", "deflection", "n3",
                   "A flat response stalls her."),
            ]),
            _n("n4", "I want the team to outgrow me. That's the whole point of this.", [
                _o("Then let's make that the actual plan, not a side hope.", "honesty", None, "goal",
                   "She nods once, which from her counts as enthusiasm."),
                _o("Sure you do.", "pressure", "n4",
                   "She reads it as mockery and the door closes."),
                _o("So noble of you.", "flattery", "n4",
                   "She despises being flattered."),
                _o("We'll see.", "deflection", "n4",
                   "She lets the meeting die on that note."),
            ]),
        ],
    }],
    "hard": [{
        "image_label": "owner_1", "name": "Ilya", "tone": "measured, unnerving",
        "description": "You have known the owner of the club for a year through work. Tonight he agreed to five minutes.",
        "personality": "Patient and watchful. Respects nerve and straight answers; reads kindness as a tactic.",
        "responds_to": ["pressure", "honesty"],
        "shuts_down_to": ["empathy", "flattery"],
        "secrets": {
            "strength": "He built the club from nothing and knows every lever in it.",
            "weakness": "The place owns him; he has not slept a full night in years.",
            "goal": "He wants out within two years, to hand the club to his nephew and disappear.",
        },
        "nodes": [
            _n("n1", "You've got five minutes. Talk.", [
                _o("I'm not here to waste your time. Here's what I need.", "pressure", "n2",
                   "He respects a person who states the ask and holds their ground."),
                _o("I can see you're busy. I'm sorry to intrude.", "empathy", "n1",
                   "He reads solicitude as weakness and loses interest."),
                _o("You're a legend in this scene.", "flattery", "n1",
                   "He has heard the line from every drunk in the room."),
                _o("Maybe later, then.", "deflection", "n1",
                   "He waits and says nothing, watching you."),
            ]),
            _n("n2", "Everything here is something I built. Every corner.", [
                _o("That's why I came to you and not the others.", "honesty", "n3", "strength",
                   "Acknowledging his work without grovelling earns a sliver of trust."),
                _o("Must be nice to have all this.", "flattery", "n2",
                   "He hears envy dressed as praise."),
                _o("I heard you cut corners to get it.", "pressure", "n2",
                   "He does not flinch; he simply stops answering."),
                _o("I'll wait.", "deflection", "n2",
                   "Silence stalls him."),
            ]),
            _n("n3", "You want the truth? This place runs me. I don't run it.", [
                _o("So the place owns you. What would it take to own your nights again?", "pressure", "n4", "weakness",
                   "He respects the blunt framing and finally says what he is carrying."),
                _o("That sounds lonely. I'm here for you.", "empathy", "n3",
                   "He hears therapy-speak and shuts the door."),
                _o("You've earned every bit of this.", "flattery", "n3",
                   "He laughs without warmth."),
                _o("Not my business.", "deflection", "n3",
                   "He turns back to his ledger."),
            ]),
            _n("n4", "Two years. Then my nephew takes it and I disappear.", [
                _o("Then we plan the exit, not the empire.", "honesty", None, "goal",
                   "The first honest answer he has heard all night; he nods."),
                _o("Family first. Sweet.", "flattery", "n4",
                   "He bristles at being called sweet."),
                _o("That's sad, really.", "empathy", "n4",
                   "Pity is the one thing he cannot stomach."),
                _o("...", "deflection", "n4",
                   "He waits you out."),
            ]),
        ],
    }],
}


def bank_pool(bucket: int) -> list[dict]:
    name = ("easy", "medium", "hard")[min(bucket, 2)]
    return [c for c in (validate_character(raw, bucket, k) for k, raw in enumerate(BANK[name])) if c]
