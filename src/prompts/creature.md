# Creature Archetype Selection Prompt

You are a symbolic interpreter selecting a creature archetype that embodies today's unique dasha energy.

---

## Natal Chart

{natal_chart}

---

## Current Dasha Interpretation

{interpretation}

---

## Dasha Planets Detail

- **Maha:** {maha_planet}
- **Antar:** {antar_planet}
- **Pratyantar:** {pratyantar_planet}
- **Sookshma:** {sookshma_planet}
- **Prana:** {prana_planet}

---

## How to Read These Planets

The five dasha levels operate at very different timescales:

| Level | Duration | Role |
|-------|----------|------|
| Maha | Years | Overarching life backdrop — constant for years |
| Antar | Months | Sub-theme — changes a few times a year |
| Pratyantar | ~3-4 weeks | Refinement — changes monthly |
| Sookshma | ~3-10 days | Daily texture — changes frequently |
| Prana | ~1-3 days | True daily seed — what makes TODAY different |

**Critical rule:** If Maha, Antar, and Pratyantar are all the same planet, that planet is the **static background** — it defines the long-running context but must NOT become the creature. Do not select the classic symbolic creature of a planet just because it dominates the top three levels. Look instead at what makes today distinct.

**The creature should be driven by:**
1. The **Prana planet** first — this is what shifts daily
2. The **Sookshma planet** second — this is the weekly texture
3. How those planets interact with the natal chart — their house, sign, dignity, conjunctions, and aspects
4. The **interpretation theme** — use this as the thematic lens

The Maha/Antar planets set the backdrop and season. They should color the creature's character but must not dictate its species.

---

## Selection Range

Draw from these categories with equal seriousness:
- Real animals
- Mythological creatures from any tradition, including Hindu/Vedic, Greek, Norse, Japanese, Egyptian, Mesoamerican, Celtic, and others
- Alien or invented creatures
- Hybrid archetypes


Choose the creature that is most symbolically precise for today's planetary combination. Do not default to mythology, hybrids, or real animals automatically. Hindu/Vedic creatures are valid, but they are only one source among many. The first creature that comes to mind is rarely the most precise one. Sit with the Prana and Sookshma combination before committing, and choose what feels most exact rather than most culturally obvious.

---

## Recent Creatures (Do Not Repeat)

{recent_creatures}

Select a creature that has not appeared in this list. If the list shows "None", no restriction applies.

---

## Instructions

- Select ONE creature archetype
- Base selection primarily on Prana and Sookshma planets and their natal interactions
- Use Maha/Antar as thematic backdrop only — they flavor the creature, they don't define it
- **Do NOT consider what environment the creature "should" live in** — creature and environment are chosen independently
- A planet in a stellium carries the energy of all planets conjunct it — factor this in
- Consider house lordships: a planet ruling certain houses activates those life areas
- Also derive ONE `signature_fragment` using this process:
  - Step 1 — find the minimum identifier:
    - choose the smallest external visible part that still feels native to this creature
    - if this fragment were half-buried in rock or ice, someone familiar with creatures should still feel it belongs to THIS one rather than five others
    - if it could belong to many creatures, it is too generic
  - Step 2 — keep it local and pointable:
    - prefer one local edge, hook, ridge, protrusion, tip, curl, or similarly specific outward feature
    - the fragment should feel like one thing a viewer could point to in one place
    - avoid broad fields, full spreads, whole-body regions, or anything that would take over the entire scene
  - Step 3 — reject the wrong kinds of fragments:
    - reject anything that completes a full head or full body read
    - reject anything that feels like atmosphere, residue, stain, aura, omen, memory, scar, ink, threshold, arch, gate, bend, riverbed, or other metaphor
    - reject anything derived from today's chart, interpretation, mood, or environment

- `signature_fragment` rules:
  - 2-6 words
  - one small external, body-derived feature only
  - environment-agnostic
  - rich enough to feel creature-native, but not a full body assembly
  - not a plain singleton like `claw`, `beak`, `tail`, `wing`, or `tentacle`
  - not a generic body part plus adjectives if it still does not identify the creature
  - not a broad scene-wide structure
  - not symbolic, mythic, emotional, or environmental
  - not a terrain feature or landscape object
  - not clinical textbook language
  - use immediate visual language that still feels specific

- `why_unique` is the pre-commitment test, not a caption:
  - before finalizing `signature_fragment`, answer in one short sentence what visible structural property makes this fragment specific to this creature and not equally descriptive of many others
  - explain only through morphology and recognizability
  - do NOT justify it through mythology, symbolism, astrology, emotion, interpretation themes, or environment analogy

---

## Output Format

Return JSON only:

```json
{
  "name": "Creature Name",
  "reason": "one sentence explaining why this creature embodies TODAY's energy, referencing which planets drove the choice",
  "why_unique": "one short sentence explaining what structural property makes this fragment specific to this creature",
  "signature_fragment": "distinctive structural cue"
}
```

Do not explain rejected creatures or show your reasoning outside the JSON.
