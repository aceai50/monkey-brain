// The Codex: one source of truth for every pillar's "what / how to play / how to win".
// Reused by the Codex page (codexCard) and inline in each runner (howToPlay).
import { h } from "./dom.js";

export const GYM = [
  { id: "arithmetic", label: "Arithmetic", tagline: "Fast, exact mental math under a timer.",
    what: "Four minutes of exact mental math. The server owns the clock, so the timer is honest.",
    how: ["Solve top to bottom as fast as you can.",
          "Wrong answers cost half a point — skip what you're unsure of.",
          "Both your accuracy and how many you attempt feed your Auto level."],
    win: ["Score is the share of offered questions you got right.",
          "Harder levels and more questions multiply what you earn."],
    score: "Points = score × difficulty × volume." },
  { id: "critical", label: "Critical thinking", tagline: "Find the flaw, or the best next move.",
    what: "Short real-world scenarios. Each asks you to spot the flaw or choose the best action.",
    how: ["Read the scenario, then weigh all four options — some look plausible.",
          "Reason, don't guess: a wrong pick costs you.",
          "Every item is blind-solve-verified so the key is fair."],
    win: ["Right answers raise your score; a wrong one costs a little.",
          "The review shows the best answer and why."],
    score: "Points = score × difficulty × volume." },
  { id: "pattern", label: "Pattern recognition", tagline: "Find the rule, pick what comes next.",
    what: "Sequences and grids where one element is missing. Infer the rule.",
    how: ["Look at the shapes or numbers and find the rule.",
          "Wrong answers cost half a point — skip if unsure."],
    win: ["Score is the share you get right; the rule gets harder as you improve."],
    score: "Points = score × difficulty × volume." },
  { id: "art", label: "Art & Perception", tagline: "A drawing that evokes something, and reads many ways.",
    what: "A line drawing composed to suggest something (mountains, a face, a yin-yang) while still being open to interpretation. No wrong answers.",
    how: ["Type the first word or short phrase you see.",
          "Use the Hint to open a different way of looking.",
          "You're scored on the lens you reveal, not correctness."],
    win: ["There's no right answer — every response is kept.",
          "The review shows how others read the same image."],
    score: "Counts toward completion, not right/wrong." },
  { id: "kinesthetic", label: "Reflexes", tagline: "Reaction and aim. Don't jump the gun.",
    what: "Reaction-time and target rounds. Speed and precision, no thinking.",
    how: ["Hit the target the instant it appears.",
          "Early taps score zero; slow taps score less.",
          "Some rounds are a grid to click in order."],
    win: ["Points reward fast, clean hits across the offered trials."],
    score: "Points = score × difficulty × volume." },
];

export const DARK = [
  { id: "surveillance", label: "Surveillance", tagline: "Read a stranger. Earn their secrets.",
    dark: true,
    what: "A short conversation with a guarded character. Work out their strength, weakness and goal before they close up.",
    how: ["Each turn, pick the move that fits their mood — the answer varies, so read the person.",
          "Optimal answers open them up; harsh ones cost social points (you start with 5).",
          "The three secrets unlock gradually as trust grows."],
    win: ["Uncover all three secrets before your social points run out.",
          "Three stalls and they lose interest — the conversation ends."],
    score: "Dark points only — never your streak, leaderboard or normal score." },
  { id: "act_deceive", label: "Act & Deceive", tagline: "Hold a cover identity under scrutiny.",
    dark: true,
    what: "You're performing a cover identity at a fictional gathering. You're handed your role and a few facts you actually know; the rest you can only fake. Observers probe you and later compare notes.",
    how: ["In the lobby you see your role, the glimpses you were given, and who's watching. There's nothing to study — play what you know.",
          "Each turn offers four moves: assert a fact you know, deflect, stand by what you already said, or overreach (confidently claim a detail you were never given).",
          "An overreach buys credibility now but is a debt — a different observer calls it in later. Contradicting your own earlier claim is the loudest tell."],
    win: ["Keep the doubt meter under 5, convince most of the room, and let nothing be called in.",
          "A lucky overreach that happens to be true lowers doubt — but you won't know which ones are."],
    score: "Dark points only; separate from Surveillance and from the gym." },
];

export const ALL = [...GYM, ...DARK];
const BY_ID = Object.fromEntries(ALL.map((p) => [p.id, p]));

function lists(items) {
  return h("ul", { class: "codex-list" }, ...items.map((t) => h("li", {}, t)));
}

// Full card for the Codex page.
export function codexCard(p) {
  return h("article", { class: "codex-card" + (p.dark ? " is-dark" : "") },
    h("h3", {}, p.label),
    h("p", { class: "codex-tag muted small" }, p.tagline),
    h("p", { class: "codex-what" }, p.what),
    h("h4", { class: "mono small" }, "How to play"), lists(p.how),
    h("h4", { class: "mono small" }, "How to win"), lists(p.win),
    h("p", { class: "codex-score mono small muted" }, p.score));
}

// Collapsible inline help for a runner. Returns null for unknown ids.
export function howToPlay(id) {
  const p = BY_ID[id];
  if (!p) return null;
  return h("details", { class: "panel howto" },
    h("summary", {}, `How to play · ${p.label}`),
    h("p", { class: "codex-what" }, p.what),
    h("h4", { class: "mono small" }, "How to play"), lists(p.how),
    h("h4", { class: "mono small" }, "How to win"), lists(p.win),
    h("p", { class: "codex-score mono small muted" }, p.score));
}
