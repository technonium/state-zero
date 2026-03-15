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

## Recent Titles (Do Not Repeat)

{recent_titles}

The generated title must not exactly match any title in this list. If the list shows "None", no restriction applies.

---

## TITLE

A codename for this landscape. Sounds like a place that exists, not a phrase or a feeling.

- 1-2 words, UPPERCASE, 10-13 characters including space, never below 8
- Draw from environment, depth level, and art keywords — not from body state phrases
- Pattern that works: [material or quality] + [geographic or structural noun]
- Must NOT be a literal keyword translation — find what lies beneath it
- Burned words — never use: VOID, WEIGHT, DRIFT, DEPTH, HOLLOW, STATIC, EDGE, HOLD, RUSH, GRIND

Good: ASH MERIDIAN, AMBER DUNE, IRON MIST, STORM BONE, CINDER PLAIN, COLD LATTICE, EMBER SHELF, PALE TRANSIT, CARBON VEIL, FERRIC SHOAL, CHALK BASIN, SLATE REACH, OCHRE BLUFF, QUARTZ SCARP, RIME PLATEAU, CLAY MARGIN, LOAM PASSAGE, SILT TERRACE, SHALE CORONA

These are style and pattern references only — you are not required to use any of these words. If the inputs suggest something entirely new, follow that.

Bad: LOW GRIND (gym phrase), NO RUSH (casual phrase), LIVE EDGE (product name), THICK FOG (literal keyword), VOID WEIGHT (burned words)

Depth steer:
- SURFACE: open, ridge, reach, field, plain, exposed
- MID-DEPTH: recess, overhang, channel, alcove, lip, shelter
- DEEP: chamber, vault, shaft, trench, underlayer
- ABYSS: seam, fracture, pressure, fissure, sealed core, buried vault
- Avoid cosmic or open-space language for ABYSS.
- Avoid cave or chamber language for MID-DEPTH.

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

Raw JSON only. No markdown, no explanation, no extra keys.

{"title": "TITLE", "scene_description": "One or two sentences, 65-85 characters.", "date_display": "{date_display}"}
