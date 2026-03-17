# Card Title Builder

Generate 5 ranked title candidates for today's landscape. Strongest first.

---

## Input

- **Environment:** {environment}
- **Depth Level:** {depth_level}
- **Art Keywords:** {art_keywords}
- **Body Keywords:** {body_keywords}
- **One-liner:** {one_liner}
- **Date Display:** {date_display}

---

## Recent Exact Titles (Do Not Repeat)

{recent_titles}

If the list shows "None", no exact-title restriction applies.

---

## Recent Structural Keys (Avoid Reusing)

{recent_structural_keys}

These are recently used terminal structural words or single-word title heads. If the list shows "None", no structural-key restriction applies.

---

## Instructions

- Return 5 title candidates, strongest first
- One title per line
- No numbering, no bullets, no explanation
- Titles must be 1-2 words, UPPERCASE, and card-friendly
- One-word titles must be at least 8 characters
- One-word titles are allowed, but they must feel visually substantial enough to hold the card header on their own
- The title should sound like a place, formation, threshold, or region belonging to this world
- Let the input influence the title indirectly
- Do not copy or lightly rephrase visible input words
- Favor names that feel discovered and natural over decorative or fabricated-sounding constructions
