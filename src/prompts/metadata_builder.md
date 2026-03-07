# Card Metadata Builder

Generate two fields from today's data. Return raw JSON only.

---

## Input

- **Environment:** {environment}
- **Depth Level:** {depth_level}
- **Art Keywords:** {art_keywords}
- **Body Keywords:** {body_keywords}
- **One-liner:** {one_liner}
- **Date Display:** {date_display}

---

## TITLE

A codename for this landscape. Sounds like a place that exists, not a phrase or a feeling.

- 1-2 words, UPPERCASE, 10-13 characters including space, never below 8
- Draw from environment, depth level, and art keywords — not from body state phrases
- Pattern that works: [material or quality] + [geographic or structural noun]
- Must NOT be a literal keyword translation — find what lies beneath it
- Burned words — never use: VOID, WEIGHT, DRIFT, DEPTH, HOLLOW, STATIC, EDGE, HOLD, RUSH, GRIND

Good: ASH MERIDIAN, AMBER DUNE, IRON MIST, STORM BONE, CINDER PLAIN, COLD LATTICE, EMBER SHELF, PALE TRANSIT, DUST MERIDIAN, CARBON VEIL

Bad: LOW GRIND (gym phrase), NO RUSH (casual phrase), LIVE EDGE (product name), THICK FOG (literal keyword), VOID WEIGHT (burned words)

---

## SCENE_DESCRIPTION

One sentence in two movements. First: what the day cost — honest, no numbers, no fitness language. Second: the landscape as quiet confirmation. Together they read as one continuous thought.

- 65-85 characters total
- Never name metrics or zones — no recovery, strain, sleep score, percentages
- Never describe the image literally
- Never use the word body
- Never copy phrases from one_liner — reinterpret it
- The second movement confirms the first, it does not describe what you see

Good:
- "Ran the whole tank down. Something still burns at the bottom of it." (depleted, volcanic)
- "Gave more than there was and the stone absorbed every bit of the rest." (depleted, volcanic)
- "Full output, clean burn. The horizon stayed open the entire way through." (peak, open sky)
- "Everything cost slightly more than it should have and the fog agreed." (grinding, low energy)
- "Pushed through the weight of it. The atmosphere was already doing the same." (pressing, mid-depth)

Bad:
- "Completely drained. No color, no energy, just the body getting through." — uses body, describes feeling
- "Going through the motions. No urgency, no resistance, just drifting." — one_liner verbatim
- "Stone formations rise through volcanic mist as three moons hover above." — describes the image
- "High output, low recovery. Ran on reserves all day." — metric language

---

## Output

Raw JSON only. No markdown, no explanation, no extra keys.

{"title": "TITLE", "scene_description": "One sentence, 65-85 characters.", "date_display": "{date_display}"}
