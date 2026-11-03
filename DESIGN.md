# DESIGN.md (internal)

**Concept:** a gym logbook for your head. Hard edges, ruled paper, one loud colour. The five pillars
are shown as a five-cell tally (like set counts on a whiteboard). That tally is the one memorable
element; it fills in once on first load, and later fills as pillars are completed.

**Type:** Space Grotesk 700/500 (display + body, tight tracking on headlines) and JetBrains Mono 500
(buttons, inputs, metrics, metadata). Sentence case everywhere. No all-caps labels, no arrows on
buttons, no eyebrow labels, no numbered markers (content is not a sequence).

**Colour (5 tokens + derived on-accent):**
| token | light | dark |
|---|---|---|
| --bg | #F9F9FB | #09090B |
| --surface | #FFFFFF | #121214 |
| --text-main | #000000 | #FFFFFF |
| --text-muted | #71717A | #A1A1AA |
| --accent | **#0047FF** Electric Cobalt | **#D4FF00** Acid Lime |
Accent flips per theme because lime fails contrast on off-white and cobalt fails on ink.
Correct/wrong answers (Phase 2) must not rely on red/green: accent fill = correct, diagonal hatch = wrong.

**Shape:** radius 0-2px, 1.5px solid --text-main borders, hard offset shadows (no blur).
**Motion:** mechanical. Hover shifts -1/-1, active compresses +2/+2 and drops the shadow.
Only one unprompted animation (the tally). prefers-reduced-motion disables it and the cursor trail.

**Layout:** asymmetric 12-col grid. Auth: huge left-aligned headline over cols 1-8, form dropped
into cols 7-12 and offset downward. Dashboard: heavy left header, metrics in an offset right column.

## Phase 2 additions
- Training screen: one column, big monospace prompts, a 12px time bar that fills with the accent and switches to a
  hatched fill under 30s (no red/green). The five-cell tally is the circuit progress indicator.
- Correct/wrong in reviews: accent left rule = right, diagonal hatch = missed.
- Tables use 1.5px ink rules, no zebra; your own row gets an accent rule and tint.
- Art pillar draws whitelisted parametric curves on canvas (no formulas are ever sent or eval'd).
