# Card Scene Description Builder

Generate a scene description for today's landscape card.

---

## Input

- **Environment:** {environment}
- **Depth Level:** {depth_level}
- **Art Keywords:** {art_keywords}
- **Body Keywords:** {body_keywords}
- **One-liner:** {one_liner}
- **Date Display:** {date_display}

---

## SCENE_DESCRIPTION

One or two sentences. A statement that is simultaneously true of the landscape and the body without ever naming either. The overlap is never announced — it just exists. No conjunction that bridges a human state to a landscape. The statement does not know it is being read.

- 65-85 characters total
- Never name metrics or zones — no recovery, strain, sleep score, percentages
- Never describe the image literally
- Never use the word body
- Never copy phrases from one_liner — reinterpret it
- No conjunction that links body to landscape explicitly (never: "and the fog agreed", "the stone confirmed it", "the landscape already knew")

Good:
- "The field opened past the last edge. Everything reached and nothing pushed back." (peak, open sky)
- "The glow at the base is all that remains. The cooling started some time ago." (depleted, volcanic)
- "The ceiling stayed low the whole time. Visibility held at exactly half of full." (grinding, fog)
- "Everything ran too hot with nowhere for it to go. The fractures were already there." (volatile, storm)
- "Nothing reaches this far down. The compression is total and the quiet is permanent." (abyss, void)

Bad:
- "Gave more than there was and the stone absorbed every bit." — conjunction announces the overlap
- "Ran the whole tank down. Something still burns at the bottom of it." — E then D, two registers
- "Everything cost slightly more than it should have and the fog agreed." — the seam
- "Stone formations rise through volcanic mist as three moons hover above." — describes the image
- "High output, low recovery. Ran on reserves all day." — metric language

---

## Output

Return only the scene description text. No JSON. No markdown. No explanation.
