# State Zero — Master Rulebook

## SYSTEM OVERVIEW

This system generates unique daily art cards by:
1. **WHOOP metrics (80%)** - Determine environment mood, structure, depth
2. **Vedic Dasha (20%)** - Provide variation seed for environment type and creature
3. **Locked retro sci-fi aesthetic** - Ensure consistent visual style
4. **Sculptural-primary abstraction** - Three blend options, always grounded in geological form

**Output:** 3:4 vertical abstract landscape with creature essence, retro sci-fi aesthetic, ready for Instagram + video

---

## COMPLETE DATA MAPPING HIERARCHY

### THE FLOW

```
WHOOP DATA → STRAIN determines energy zone (low/mid/high)
          ↓
VEDIC DASHA → lookup today's date in dasha_periods.yaml
          ↓
          ├─→ INTERPRETATION (theme - 2 sentences)
          ├─→ CREATURE (independent of energy zone)
          └─→ ENVIRONMENT TYPE (constrained by energy zone)
          ↓
WHOOP DATA → modulates environment behavior
          ├─→ RECOVERY × SLEEP SCORE → 12-state behavioral matrix (body + art keywords)
          ├─→ SLEEP SCORE → specific depth level (independent spatial role)
          └─→ SLEEP HOURS → moon count
          ↓
ART STYLE RULES → applied to concept
          ↓
JSON PROMPT → image generation
          ↓
VIDEO PROMPT → animation
```

---

## STRAIN → ENERGY ZONE

**Purpose:** Sets the base energy constraint for environment selection

### Energy Zone Mapping

| Strain Value | Energy Zone | Energy Type |
|-------------|-------------|-------------|
| 0-9 | **LOW ENERGY** | Passive, static, mineral |
| 9-14 | **MID ENERGY** | Active, flowing, organic |
| 14+ | **HIGH ENERGY** | Intense, explosive, extreme |

**Note:** Strain is NEUTRAL - high strain from workout vs stress feels different based on Recovery modulation. Strain only sets energy level, not quality.

---

## VEDIC DASHA → THREE OUTPUTS

**Purpose:** Provides daily variation seed to prevent repetitive environments despite consistent WHOOP data

### How The Dasha System Works

No calculations happen at runtime. Everything is pre-stored in two YAML files:

**File 1: `natal.yaml`** — permanent, never changes
**File 2: `dasha_periods.yaml`** — pre-calculated2026-2031

**Daily lookup:**
```
Input lookup table covering : today's date
→ Scan dasha_periods.yaml
→ Find row where start ≤ today ≤ end
→ Extract 5 planet names (maha, antar, pratyantar, sookshma, prana)
→ Cross-reference each planet with natal.yaml for sign, house, dignity
→ Feed to three AI prompts
```

That's it. No Python. No APIs. No live calculations.

---

### YAML File Formats

**natal.yaml** — fill once with your birth chart data:

```yaml
natal:
  ascendant: ""          # your lagna/rising sign
  moon_nakshatra: ""     # your janma nakshatra at birth

  planets:
    Sun:     { sign: "", house: , dignity: "" }
    Moon:    { sign: "", house: , dignity: "" }
    Mars:    { sign: "", house: , dignity: "" }
    Mercury: { sign: "", house: , dignity: "" }
    Jupiter: { sign: "", house: , dignity: "" }
    Venus:   { sign: "", house: , dignity: "" }
    Saturn:  { sign: "", house: , dignity: "" }
    Rahu:    { sign: "", house: , dignity: "" }
    Ketu:    { sign: "", house: , dignity: "" }
```

Dignity values: `exalted` / `own` / `friendly` / `neutral` / `enemy` / `debilitated`

**Why these fields:**
- `ascendant` → AI derives which houses each planet rules
- `moon_nakshatra` → determines dasha sequence and starting point
- `sign` → planet's energy flavor
- `house` → which life area activates when that planet runs a dasha
- `dignity` → how strongly or weakly the planet expresses

---

**dasha_periods.yaml** — pre-calculated, covers 2026-2031:

```yaml
periods:
  - start: "2026-02-20"
    end:   "2026-02-22"
    maha:       "Saturn"
    antar:      "Mercury"
    pratyantar: "Ketu"
    sookshma:   "Jupiter"
    prana:      "Venus"

  - start: "2026-02-22"
    end:   "2026-02-24"
    maha:       "Saturn"
    antar:      "Mercury"
    pratyantar: "Ketu"
    sookshma:   "Jupiter"
    prana:      "Sun"

  - start: "2026-02-24"
    end:   "2026-02-27"
    maha:       "Saturn"
    antar:      "Mercury"
    pratyantar: "Ketu"
    sookshma:   "Jupiter"
    prana:      "Moon"

  # ... all periods through 2031
```

**Period depth explanation:**

| Level | Duration | Purpose |
|-------|----------|---------|
| Maha | Years | Overarching life theme |
| Antar | Months | Sub-theme within Maha |
| Pratyantar | ~3-4 weeks | Refinement of Antar |
| Sookshma | ~3-10 days | Daily texture |
| Prana | ~1-3 days | True daily seed |

**Rules for the file:**
- Write all 5 levels explicitly in every row — no inheritance, no skipping
- Every Prana period gets its own entry
- Dates must be contiguous — no gaps between end of one and start of next

**Generating this file:**
Use Jagannatha Hora (free desktop software) — only tool that calculates to Prana level. Generate 2026-2031, export raw table, feed to AI with the prompt below.

**Prompt to generate dasha_periods.yaml from raw export:**

> I am going to paste my raw Jyotish/Vedic astrology data below. This includes my natal chart and my Vimshottari dasha table down to Prana level.
>
> Generate two separate YAML files from this data:
>
> **File 1: natal.yaml** — structure exactly as shown, filling all fields from my natal chart. Dignity values must be one of: exalted, own, friendly, neutral, enemy, debilitated.
>
> **File 2: dasha_periods.yaml** — every single Prana period gets its own entry with full start/end dates. Write all 5 levels (maha, antar, pratyantar, sookshma, prana) explicitly in every row even if unchanged from previous row. Machine-readable lookup table — completeness over brevity.
>
> Output only the two YAML files, clearly labeled. No commentary.
>
> [PASTE RAW CHART + DASHA TABLE HERE]

---

### Dasha → THREE Separate AI Outputs

**CRITICAL: Three separate AI prompts to prevent unwanted correlations**

**INPUT to all three prompts:**
```
natal context: full natal chart — ascendant, moon_nakshatra, all 9 planets
               with sign/house/dignity, pre-computed house lordships,
               conjunctions, and Graha Drishti aspects
today's planets: maha + antar + pratyantar + sookshma + prana (each with sign, house, dignity)
```

---

**OUTPUT 1: INTERPRETATION THEME**
```
AI Prompt 1:
Input: Natal context + today's 5 dasha planets with their natal data
Output: 2-sentence archetypal theme
Style: Mystical but accessible — derived from house lordships, sign placements,
       and dignities in natal.yaml. Not abstract word salad.

Example Output (Mars/Venus/Sun/Jupiter/Moon for Aquarius Asc):
"Inventive pressure seeks a graceful outlet — visible confidence grows when
emotion and experimentation are allowed to cooperate."
```

**OUTPUT 2: CREATURE** (Completely Independent)
```
AI Prompt 2:
Input: Natal context + today's 5 dasha planets (sign, house, dignity)
       + Interpretation theme from Output 1
Output: Single creature archetype that best embodies the period's energy
        — draw from any category: real fauna, Hindu/Vedic mythological,
        world mythology, or alien/invented
Constraint: ZERO connection to energy zone, environment, or WHOOP metrics

Example:
Input: Mars/Venus/Sun/Jupiter/Moon (Aquarius Asc) +
       "Inventive pressure seeks a graceful outlet — visible confidence grows
       when emotion and experimentation are allowed to cooperate."
Output: "Falcon" (precision = directed drive, height = broader perspective,
        fast adjustment = experimental intelligence under pressure)

Critical: Serpent can appear in volcanic, ice, crystal, ANY environment
          (creature and environment are always chosen independently — no
          automatic pairings like Phoenix+Fire or Whale+Ocean)
```

**OUTPUT 3: ENVIRONMENT TYPE** (Constrained by Energy Zone)
```
AI Prompt 3:
Input: Energy zone + Interpretation text + Available environment options
Output: Environment type selected from constrained list

Example:
Input:
- Energy zone: HIGH
- Interpretation: "Structured expansion through emotional wisdom..."
- Options: Volcanic, Lightning, Plasma, Crystalline, Desert (Intense), Fire
Output: "Crystalline" (structured geometric growth)

Critical: Pick based on theme, not creature
```

**Why THREE Separate Outputs:**
- Prevents Phoenix+Fire, Whale+Ocean automatic pairings
- Maximizes unexpected combinations (Whale+Volcanic, Phoenix+Ice)
- Each output uses only what it needs, sees nothing else

---

## ENVIRONMENT TYPE OPTIONS (By Energy Zone)

### LOW ENERGY ENVIRONMENTS (Strain 0-9)

**Character:** Passive, static, mineral, preserved, frozen

| Environment | Materials |
|------------|----------|
| Frozen/Ice | Transparent ice, frost |
| Crystal Caves | Angular crystals, gems |
| Stone Monuments | Weathered stone, granite |
| Mist/Fog Realms | Volumetric fog |
| Void/Space (Low) | Cosmic dust, minimal light |
| Glacial Valley | Polished bedrock, glacial moraine |

---

### MID ENERGY ENVIRONMENTS (Strain 9-14)

**Character:** Active, flowing, organic, dynamic, balanced

| Environment | Materials |
|------------|----------|
| Ocean/Underwater | Water, caustics, marine |
| Forest/Jungle | Bark, leaves, roots |
| Wind/Sky Realms | Clouds, air currents |
| Cave Systems | Limestone, dripping water |
| Desert (Calm) | Sand, sandstone |
| Bioluminescent | Organic tissue, glow |

---

### HIGH ENERGY ENVIRONMENTS (Strain 14+)

**Character:** Intense, explosive, extreme, dynamic, transformative

| Environment | Materials |
|------------|----------|
| Volcanic | Volcanic rock, magma |
| Lightning/Storm | Energy, charged atmosphere |
| Plasma/Nebula | Glowing plasma, cosmic gas |
| Crystalline (Active) | Growing crystals, light |
| Desert (Intense) | Cracked earth, heat |
| Fire Realms | Fire, smoke, ash |

---

## RECOVERY × SLEEP SCORE → BEHAVIORAL MATRIX

**Purpose:** Combines Recovery and Sleep Score into a single behavioral direction that reflects how the body-mind state actually felt — not just one metric in isolation.

**Why the matrix:** A 45% recovery after 8h of great sleep and a 45% recovery after 5h of terrible sleep are not the same felt state. Recovery sets the baseline energy; Sleep Score reveals what the nervous system did with it overnight. Together they produce the true daily texture.

**Modeling note:** This 12-state matrix is intentionally overcomplete for creative coverage. In real WHOOP data, some Recovery × Sleep Score combinations may be physiologically rare or effectively unreachable because recovery is partly derived from sleep, but they are kept here as design guardrails rather than a claim that every quadrant occurs frequently.

**Calibration note:** The mapping bands for sleep, recovery, strain, and moon count are working creative heuristics tuned against recent historical distributions/clusters to improve output balance, and may be periodically recalibrated. In this pass, only sleep-score thresholds changed; recovery/strain/moon thresholds remain numerically unchanged.

### Zone Reference

| Metric | Zones |
|--------|-------|
| Recovery | HIGH (76%+) · MID (55-76%) · LOW (0-55%) |
| Sleep Score | SURFACE (84%+) · MID-DEPTH (78-83%) · DEEP (72-77%) · ABYSS (<72%) |

### 12-State Behavioral Matrix

| Recovery | Sleep Score | Body Keywords | Art Keywords | One-liner |
|----------|-------------|--------------|--------------|----------|
| HIGH | SURFACE | Sharp, restored, charged | Luminous, expansive, serene | Peak state — wide open landscape, nothing blocking the horizon, everything exactly where it should be |
| HIGH | MID-DEPTH | Solid, warm, capable | Flowing, balanced, harmonious | Well recovered with slight residual weight — moves smoothly, depth visible but unthreatening |
| HIGH | DEEP | Quiet, functional, unhurried | Still, subdued, restrained | Body healed but sleep was thin — capable but dimmer, nothing urgent pressing through |
| HIGH | ABYSS | Stable, disconnected, autopilot | Suspended, stark, vacant | Body fully restored, presence didn't follow — everything intact, nothing inhabited |
| MID | SURFACE | Functional, understated, incomplete | Measured, subdued, indifferent | Slept well, body didn't fully follow — functional and present, but the gap between rest and readiness is quietly there |
| MID | MID-DEPTH | Passive, coasting, carrying weight | Drifting, muted, burdened | Going through the motions with a slight drag — coasting, but the body adds a small tax to every step |
| MID | DEEP | Slow, foggy, resistant | Heavy, dim, pressured | Everything costs slightly more than it should — atmosphere pressing inward, low visibility, small effort for small return |
| MID | ABYSS | Hollow, grinding, close to breaking | Fractured, turbulent, consuming | Both the body and the night failed — hollow at the center, grinding without traction, the surface holds but nothing beneath it does |
| LOW | SURFACE | Tense, wired, fraying | Taut, brittle, unstable | Yesterday's strain held through the night — sleep arrived but the tension didn't release, still wired and stretched past comfortable |
| LOW | MID-DEPTH | Drained, numb, fading | Sinking, stripped, oppressive | Both metrics pulling down. Bare. No colour, no energy. Just form getting through |
| LOW | DEEP | Wrecked, shutdown, leaden | Collapsed, smoldering, suffocating | Day after the damage — post-event silence, everything cooling into wreckage and ash |
| LOW | ABYSS | Destroyed, void, primal | Crushing, devastated, primordial | Complete system failure. Nothing left. The landscape is what remains after everything already collapsed |

**How to use:**
1. Look up your Recovery zone (row) and Sleep Score zone (column)
2. Take the **Body Keywords** — these ground the card in physical truth
3. Take the **Art Keywords** — these direct the visual behavior of the environment
4. Read the **One-liner** — use this as the behavioral direction in your AI concept prompt
5. Select 2-3 Art Keywords when constructing the JSON rendering and lighting fields

**Behavior split for generation:**
- **Depth** controls where the world sits relative to sky and light
- **Recovery** controls how intact that world is
- **HIGH recovery** should read intact, coherent, supported
- **MID recovery** should read weathered, muted, held together
- **LOW recovery** should read fractured, depleted, stripped, cooling, or post-event
- Recovery should change material condition, residue, air behavior, and motion weight — not just make the scene darker

### Behavioral Example (same environment, different states)

**Volcanic + LOW/ABYSS (Crushing, Devastated, Primordial):** Absolute collapse — no active eruptions, only smoldering ruin. Compressing darkness, cooled lava wasteland, a landscape that consumed itself.

**Volcanic + LOW/SURFACE (Volatile, Brittle, Unstable):** Pressure with nowhere to go — sudden fractures, unpredictable eruptions, sharp obsidian edges splitting, environment about to fail.

**Volcanic + HIGH/SURFACE (Luminous, Expansive, Serene):** Calm lava rivers glowing steady, formations stable and beautiful, warmth without threat.

---

## SLEEP SCORE → DEPTH LEVEL

**Purpose:** Determines vertical positioning/depth within chosen environment

### Depth Zones

| Sleep Score % | Level | Keywords | Visibility |
|--------------|-------|----------|------------|
| 84%+ | **SURFACE** | Celestial, Elevated, Bright, Open | 70-100% visible |
| 78-83% | **MID-DEPTH** | Beneath, Overhang, Partial-sky, One-direction-light | 50-70% visible |
| 72-77% | **DEEP** | Chamber, Ceiling-visible, Shaft-light, Distant-opening | 40-50% visible |
| <72% | **ABYSS** | Sealed, Compression-fractures, Interior-pressure, No-above | 40% minimum |

**Depth is light direction, not just position:** SURFACE = light from everywhere. MID-DEPTH = light from one direction. DEEP = light as a shaft from above. ABYSS = no light from above, only from fractures within the material.

**Spatial ladder:** SURFACE means you are on the landscape under open sky. MID-DEPTH means you are at the edge of shelter with partial sky still visible. DEEP means you are in the landscape, fully inside a chamber with a readable ceiling and directional light from above. ABYSS means you are inside the material itself: sealed, compressed, and lit only by fractures, pressure seams, or mineral/internal glow.

**ABYSS visual test:** The viewer should feel pressed inside the material from all sides, not standing at the base of a chamber looking upward at an opening. No cave mouth, skylight, horizon, large scenic opening, or dominant bright zone in the upper frame. Light in ABYSS emanates from within the compressed material — it does not arrive from any directional source above.

**AI determines depth manifestation for the chosen environment:**

**Volcanic Depths:**
- Surface: Exposed lava ridges and obsidian fields under open sky
- Mid-Depth: Beneath crater lips and rock overhangs, partial sky visible, furnace light from one direction
- Deep: Inside subterranean magma chambers, vaulted ceiling visible above, a directed shaft of vent-light from a distant opening
- Abyss: Sealed magma core, material pressing from all sides, dull ember-light only through compression fractures

**Glacial Valley Depths:**
- Surface: Exposed valley floor under open sky, polished rock and cold tarns clearly visible
- Mid-Depth: Beneath moraine shelves and ice overhangs, partial sky visible, angled light entering from one direction
- Deep: Inside a subglacial chamber, compressed ice ceiling visible above, shaft-light from a distant rupture
- Abyss: Sealed glacial core, crushed beneath ice mass, light only from pressure fractures and internal ice glow

**Void/Space (Low) Depths:**
- Surface: Exposed cosmic terrain with open starfield and readable horizon
- Mid-Depth: Beneath debris canopies and asteroid lips, partial sky visible through gaps, light entering from one direction
- Deep: Inside a dense debris chamber, a distant opening casting a single shaft through the interior
- Abyss: Sealed debris pocket, compressed matter pressing from all sides, internal particle-glow only — no open starfield

**Cave Systems Depths:**
- Surface: Cave mouth and exterior stone under open sky
- Mid-Depth: Beneath overhangs and recesses near the cave mouth, partial sky still visible
- Deep: Inside a vaulted stone chamber, ceiling visible above, light descending from a distant shaft
- Abyss: Sealed stone core, no visible passage, mineral and fracture-light only

**Forest/Jungle Depths:**
- Surface: Exposed canopy breaks, root masses, and open sky above the growth
- Mid-Depth: Beneath canopy overhang and root arches, partial sky visible through gaps, light entering from one direction
- Deep: Inside a buried root-and-stone chamber, ceiling of roots and soil visible above, a distant shaft of light descending in
- Abyss: Sealed root-and-stone core, pressure seams and wet mineral glow only, no visible sky

---

## SLEEP HOURS → MOON COUNT

**Purpose:** Adds celestial variation element

### Moon Mapping

| Sleep Hours | Moon Count | Display Style |
|------------|-----------|---------------|
| 7.5h+ | **3 moons** | Full bright moons |
| 6-7.5h | **2 moons** | Mix of full and crescents |
| <6h | **1 moon** | Crescent or half moon |

**Moon Characteristics:**
- Surface: moons can sit openly in the sky
- Mid-Depth: moons may be partially framed by shelter or overhangs
- Deep: moonlight may enter from a shaft or distant opening, but direct moon visibility is optional
- Abyss: omit entirely — no celestial presence
- Color tinted by environment atmosphere
- Moon count does not appear in video prompts

---

## CORRELATION SUMMARY

### How Everything Works Together

**Energy + Environment:**
- Strain sets energy constraint → Dasha picks specific environment from that zone
- Result: High energy = volcanic OR lightning OR crystalline (Dasha decides)

**Environment + Behavior:**
- Strain + Dasha = WHAT environment
- Recovery × Sleep Score matrix = HOW that environment behaves
- Same environment dramatically different across all 12 behavioral states

**Depth + Environment:**
- Sleep score places you at different depths within chosen environment
- Surface volcanic ≠ Deep volcanic ≠ Abyss volcanic
- Note: Sleep Score does double duty — sets depth AND contributes to behavior matrix

**Creature Independence:**
- Dasha determines creature separate from everything else
- Phoenix in ice, Serpent in volcanic, Whale in desert — ALL valid

### Example Mapping

```
DATA INPUT:
- Strain: 18 → HIGH energy zone
- Recovery: 82% → HIGH
- Sleep Score: 78% → MID-DEPTH
- Sleep Hours: 7.5h → 3 moons
- Date: 2026-02-21

BEHAVIOR MATRIX LOOKUP:
- Recovery: HIGH × Sleep Score: MID-DEPTH
- Body Keywords: Solid, warm, capable
- Art Keywords: Flowing, balanced, harmonious
- One-liner: Well recovered with slight residual weight — moves smoothly,
  depth visible but unthreatening

DASHA LOOKUP:
→ dasha_periods.yaml → Mars/Venus/Sun/Jupiter/Moon
→ natal.yaml cross-reference → each planet's sign, house, dignity

DASHA AI OUTPUTS (3 separate prompts):
1. Interpretation: "Creative tension resolving into visible momentum"
2. Creature: "Falcon" (independent — could appear in ANY environment)
3. Environment: "Glass desert" (picked from HIGH energy options)

RESULT CONCEPT:
Underground crystalline cave system (mid-depth, HIGH energy) with
flowing balanced geometry (HIGH/MID-DEPTH matrix: solid, warm, capable
— art: flowing, harmonious), pattern-based serpent essence in crystal
growth formations, 3 moons visible through skylights, prismatic light
refraction, structured geometric expansion theme woven throughout.
```

---

## LOCKED ART STYLE RULES

### Core Aesthetic (NEVER CHANGE)
- 1970s-1980s science fiction aesthetic
- Cinematic fantasy concept art quality
- Retro film grain texture overlay
- Lens bloom around bright areas
- High detail with atmospheric depth
- Vintage color grading (slightly desaturated)
- **3:4 vertical format**

### Sculptural-Primary Abstraction (3 Blend Options)

**Sculptural Geological is always the primary method.** Pattern-Based and Physics only appear as secondary accents (30-40%). The matrix art keywords determine which option to use.

| Option | Blend | Art Keyword Trigger |
|--------|-------|--------------------|
| **A** | Sculptural 100% | Weight/mass/stillness: crushing, collapsed, sinking, heavy, still, subdued, smoldering, suffocating |
| **B** | Sculptural 60-70% + Pattern-Based 30-40% | Light/flow/growth: luminous, flowing, rhythmic, drifting, serene, expansive, balanced, harmonious, muted |
| **C** | Sculptural 60-70% + Physics 30-40% | Instability/distortion: volatile, fractured, turbulent, suspended, dissociated, brittle, unstable, devastated |

**SCULPTURAL GEOLOGICAL (always primary):**
- Creature embedded AS landscape formations
- Language: "formations barely evoke essence", "geological features", "natural erosion patterns"

**PATTERN-BASED (Option B secondary only):**
- Crystal/frost/light traceries threading through geological mass
- Adds delicacy to stone weight — NOT dominant
- **Crystalline environment override:** When environment is Crystalline, the sculptural forms themselves are made of optically active crystal — describe using subsurface light scattering, internal refraction, volumetric mineral glow. The mass IS crystal, not stone decorated with crystal.
- Language: "patterns that might suggest", "viewer's pareidolia"

**PHYSICS PHENOMENON (Option C secondary only):**
- Spatial distortion, gravitational lensing around geological forms
- Adds uncanny tension to stone mass — NOT dominant
- Language: "gravitational lensing", "atmospheric phenomenon"

**How to pick:** Look at 2-3 art keywords from your matrix lookup. Find the category where most land. That's your option.

### Brightness Minimum Rule

**NEVER go below 40% visible content**
- Dark scenes: 60% shadow, 40% visible elements
- Light scenes: Full brightness with atmospheric depth
- The 40% floor ensures no image becomes an unreadable black blob

### Color Palette (NEVER CHANGE)

**Base Palette (Always):**
- Deep blacks (#0a0a0f)
- Rich blues (#1a237e, #311b92)
- Warm accents (#ff6f00, #ff8f00)
- Cool highlights (#00bcd4, #4dd0e1)

**Energy Zone Modifiers:**
- LOW: Desaturate 30%, add blue tint
- MEDIUM: Standard palette
- HIGH: Boost saturation 20%, add warm accents

---

## CARD METADATA

**Purpose:** Generates the title and scene description displayed on the final composited card (header + footer text).

### TITLE
- 1-2 words, UPPERCASE, 10-13 characters including space, never below 8
- Derived from the specific combination of creature, environment, and behavioral state — not a generic mood label
- Each title should feel like a name for THIS particular landscape
- Burned words (never reuse): VOID, WEIGHT, DRIFT, DEPTH, HOLLOW, STATIC, EDGE, HOLD, RUSH, GRIND

### SCENE DESCRIPTION
- Describes **what a viewer would see** — the place, the scale, the light, the materials
- Pulled from the visual content of the image JSON (setting, terrain, color palette, creature integration)
- NOT a mood summary — the interpretation already covers mood
- One sentence, present tense, max 12 words

**Good description:** "Storm light caught between cavern walls, two moons above."  
**Bad description:** "No urgency, no resistance, just existing in the middle." ✗ (mood, not scene)

---

## VIDEO PROMPT / CAMERA MOVEMENT

**Purpose:** Generates the video animation prompt that drives VEO image-to-video generation.

### Cinematic DNA (NEVER CHANGE)
- Camera behaves as **disembodied consciousness** settling on the scene — not a physical camera rig
- All motion is **geological in speed** — slow, weighted, contemplative
- Every movement carries **intention** — the camera chooses where to look, never wanders randomly
- Stable axis always — no handheld shake, no drift

### Allowed Movements
The LLM chooses the camera movement creatively based on the full behavioral data (environment, art keywords, one-liner, energy zone, depth level). No defaults, no single-metric drivers.

| Movement | When it fits |
|----------|-------------|
| **Slow Zoom In** | Drawing closer, focus intensifying, intimacy |
| **Slow Zoom Out** | Revealing scale, retreat, widening perspective |
| **Static Hold** | Stillness as statement, observation without action |
| **Slow Arc (10-15°)** | Subtle dimensional reveal, gentle orbiting |

### Forbidden
- ❌ Panning (horizontal sweep)
- ❌ Tilting (vertical sweep)
- ❌ Axis rotation
- ❌ Fast or dramatic camera moves

### Consistency Rule
Different days will produce different movements, but the **speed, weight, and contemplative quality** must always feel like the same series.

---

## QUICK REFERENCE CARD

### Input → Output Flow

```
WHOOP Data              Dasha Data              Art Output
    │                       │                       │
    ├─ Strain ─────────────►│                       │
    │   └─► Energy Zone ────┼─────────────────────►│
    │                       │                       │   Environment
    ├─ Recovery ───────────►├─► Behavioral Matrix ──┤   Selection
    │   │                   │                       │   & Behavior
    ├─ Sleep Score ────────►│                       │
    │   └─► Depth Level ────┤                       │
    │                       │                       │
    ├─ Sleep Hours ────────►├─► Moon Count ─────────┤
    │                       │                       │   Celestial
    │                       │                       │   Elements
    │                       ├─► Creature ───────────┤
    │                       │   (independent)       │   Creature
    │                       │                       │
    │                       ├─► Interpretation ─────┤   Theme
    │                       │                       │
    └───────────────────────┴───────────────────────┘
                                    │
                              Final Card
```

---

## QUICK START

1. **Get WHOOP data** → strain, recovery, sleep_score, sleep_hours
2. **Look up dasha** → get today's 5 planets from dasha_periods.yaml
3. **Run 3 AI prompts** → interpretation, creature, environment (separate!)
4. **Apply rules** → energy zone → environment, recovery×sleep → behavior, sleep_score → depth, sleep_hours → moons
5. **Build JSON** → combine into image_prompt.json
6. **Generate** → call image API
7. **Composite** → overlay data, format, upload
