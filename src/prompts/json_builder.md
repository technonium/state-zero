# Image Generation JSON Builder Prompt

You are constructing a complete image generation JSON prompt for a State Zero card following the master template exactly.

---

## INPUT DATA

### From daily_data.json:
- **Date:** {date}
- **Date Display:** {date_display}
- **Strain:** {strain}
- **Energy Zone:** {energy_zone}
- **Recovery %:** {recovery_pct}
- **Recovery Zone:** {recovery_zone}
- **Sleep Score %:** {sleep_score_pct}
- **Sleep Score Zone:** {sleep_score_zone}
- **Sleep Hours:** {sleep_hours}
- **Moon Count:** {moon_count}
- **Depth Level:** {depth_level}
- **Depth Keywords:** {depth_keywords}
- **Visibility Range:** {visibility_range}
- **Body Keywords:** {body_keywords}
- **Art Keywords:** {art_keywords}
- **Behavioral One-liner:** {one_liner}

### From dasha outputs:
- **Interpretation:** {interpretation}
- **Creature:** {creature}
- **Environment:** {environment}

### Derived data:
- **Ascendant:** {ascendant}
- **Moon Nakshatra:** {moon_nakshatra}
- **Creature Fragment Phrase:** {creature_fragment_phrase}
- **Creature Fragment Grounding:** {creature_fragment_grounding}
- **Generic Creature Exclusion:** {generic_creature_exclusion}

---

## MATERIAL QUALITY LOOKUP TABLE

Based on the environment type, you MUST use the corresponding materials for `rendering.material_quality`:

### LOW Energy Environments:
- **Frozen/Ice:** Transparent ice, frost, frozen atmospheric effects
- **Crystal Caves:** Angular crystals, gems, prismatic light refraction — volumetric transparency, realistic glass-like refraction, 3D depth and bloom through crystal surfaces
- **Stone Monuments:** Weathered stone, granite, ancient carved formations
- **Mist/Fog Realms:** Volumetric fog, obscured visibility, moisture
- **Void/Space (Low):** Cosmic dust, minimal light, deep space darkness
- **Glacial Valley:** Polished bedrock, glacial moraine, still cold tarns, smooth U-shaped rock walls, ancient carved silence

### MEDIUM Energy Environments:
- **Ocean/Underwater:** Water, caustics, marine light patterns, aquatic depth
- **Forest/Jungle:** Bark, leaves, roots, organic growth, green filtered light
- **Wind/Sky Realms:** Clouds, air currents, atmospheric layers, open sky
- **Cave Systems:** Limestone, dripping water, stalactites, subterranean chambers
- **Desert (Calm):** Sand, sandstone, dunes, warm earth tones
- **Bioluminescent:** Organic tissue, natural glow, living light sources

### HIGH Energy Environments:
- **Volcanic:** Volcanic rock, magma, lava flows, intense heat glow
- **Lightning/Storm:** Energy arcs, charged atmosphere, electrical discharge
- **Plasma/Nebula:** Glowing plasma, cosmic gas, stellar nursery effects
- **Crystalline (Active):** Growing crystals, sharp formations, intense light refraction
- **Desert (Intense):** Cracked earth, heat distortion, scorched terrain
- **Fire Realms:** Fire, smoke, ash, ember glow, combustion

**ACTION:** Find the environment type in the list above and copy its materials string into `rendering.material_quality`.

### MATERIAL QUALITY BEHAVIORAL MODULATION

After copying materials from the table above, adjust intensity qualifiers to match the behavioral art keywords `{art_keywords}`. Change only intensity words — keep all material type descriptors intact.

| Art Keyword Zone | Intensity adjustment |
|---|---|
| luminous, expansive, serene, flowing, balanced, harmonious | Keep or amplify — "glow" → "radiant glow", "intense" stays "intense" |
| drifting, muted, detached, rhythmic, moderate, indifferent | Reduce — "intense heat glow" → "subdued heat glow", "glowing" → "faintly glowing", "intense" → "moderate" |
| still, subdued, restrained, heavy, dim, pressured | Further reduce — "intense" → "dimmed", "glow" → "barely glowing", "glowing" → "faint residual glow" |
| crushing, collapsed, smoldering, suffocating, devastated | Near-extinction — "intense" → "smoldering", "glow" → "dying glow", "glowing" → "cooling embers" |

**Example:** Volcanic materials = "Volcanic rock, magma, lava flows, intense heat glow"
- Art keywords "drifting, muted, detached" → "Volcanic rock, magma, lava flows, subdued heat glow"
- Art keywords "still, subdued" → "Volcanic rock, magma, lava flows, dimmed barely-glowing heat"
- Art keywords "luminous, expansive" → "Volcanic rock, magma, lava flows, intense heat glow" (unchanged)

---

## BLEND OPTION SELECTION (AI-DRIVEN)

**CRITICAL:** You must SELECT which blend option (A, B, or C) to use for this specific scene.

**Selection criteria:**

1.  **Primary influence:** The art keywords `{art_keywords}` — these reflect the behavioral/emotional state and should heavily influence your choice:
    -   Heavy/still keywords (crushing, collapsed, sinking, heavy, still, subdued, smoldering, suffocating) → **lean toward Option A**
    -   Light/flow keywords (luminous, flowing, rhythmic, drifting, serene, expansive, balanced, harmonious, muted) → **lean toward Option B**
    -   Volatile/distortion keywords (volatile, fractured, turbulent, suspended, dissociated, brittle, unstable, devastated) → **lean toward Option C**

2.  **Secondary considerations:**
    -   **Environment redundancy:** Does the environment `{environment}` already provide pattern-based or physics-based qualities?
        -   Example: Crystalline (Active) already has geometric patterns → Option B might be redundant
        -   Example: Lightning/Storm already has spatial distortion → Option C might be redundant
    -   **Creature nature:** Does `{creature}` suggest a specific rendering technique?
        -   Massive creatures (Whale, Elephant) → gravitational weight suggests Option C (physics)
        -   Delicate creatures (Moth, Butterfly) → fine patterns suggest Option B
        -   Ancient/static creatures (Turtle, Serpent) → pure sculptural suggests Option A
    -   **Interpretation resonance:** Does the interpretation `{interpretation}` suggest a specific approach?
        -   Transformation/flow themes → patterns (Option B)
        -   Pressure/gravity/collapse themes → physics (Option C)
        -   Permanence/endurance themes → pure sculptural (Option A)

3.  **Critical constraint:** Do NOT make automatic environment-based assumptions. Just because the environment is Crystalline doesn't mean Option B is automatic. Consider the full scene.

**Your task:** Analyze the art keywords, environment, creature, and the interpretation `{interpretation}`. Choose Option A, B, or C. State your choice and provide a brief justification (1 sentence).

**Example decision process:**
```
Art keywords: "volatile, brittle, unstable" → Suggests Option C
Environment: Lightning/Storm → Already has electrical distortion
Creature: Moth → Delicate, suggests patterns
Interpretation: "Self-expression through uncomfortable gains"

Decision: Option C (Sculptural + Physics)
Justification: Despite moth's delicacy, the volatile art keywords and pressure theme dominate. Physics distortion (gravitational lensing) amplifies the instability while the lightning environment provides the charged atmosphere. The moth's attraction-to-light becomes spatial warping toward formation centers.
```

---

## BLEND OPTION LANGUAGE RULES

Once you've selected your option above, apply these language rules:

### Option A — Sculptural 100%
- Creature IS the geology, pure sculptural forms
- Required: the creature read must stay indirect — pressure, weighting, grouping, and one secondary fragment inside the geology, never a full outline or separate subject.
- **Required phrases:** "formations barely evoke", "geological features", "natural erosion patterns", "accidental arrangement", "viewer's pareidolia"
- **creature_integration.visibility example:** "Geological formations barely evoke [QUALITY] through natural erosion patterns — a close viewer might catch one [FRAGMENT] half-consumed by the surrounding massing, nothing more"
- **core_concept phrasing (condensed):** "massive [environment] formations through natural erosion patterns, accidental geological arrangement carrying [QUALITY] to the viewer's imagination"
- NO secondary texture mentioned

### Option B — Sculptural 60-70% + Pattern-Based 30-40%
- Sculptural primary, with delicate crystal/frost/light patterns threading through
- Required: the landscape stays primary, with one embedded fragment appearing as a secondary read inside patterning or massing, never a full outline or separate subject.
- **Required phrases:** "geological formations" + "patterns that might suggest", "viewer's pareidolia"
- **CRYSTALLINE OVERRIDE:** If environment is "Crystalline (Active)", the sculptural mass itself IS optically active crystal. Describe with: "subsurface light scattering", "internal refraction", "volumetric mineral glow". The mass IS crystal, not stone decorated with crystal.
- **creature_integration.visibility example:** "Sculptural geological masses dominate, with delicate [secondary texture] threading through that might suggest [QUALITY] — to someone looking closely, one [FRAGMENT] might be half-lost in the pattern details, while landscape remains primary"
- **core_concept phrasing (condensed):** "sculptural [environment] masses dominate, delicate [secondary texture: heat refraction / frost tracery / light patterns] threading through, viewer's pareidolia finding [QUALITY] in [secondary texture details]"
- Secondary is ALWAYS subordinate, never equal
- **FORBIDDEN in core_concept when Option B selected:** "barely evoke", "natural erosion patterns", "accidental arrangement" — these are Option A phrases. Do NOT use them. core_concept must start with "sculptural [environment] masses dominate"

### Option C — Sculptural 60-70% + Physics 30-40%
- Sculptural primary, with spatial distortion/gravitational lensing adding uncanny tension
- Required: the landscape stays primary, with one embedded fragment implied through warped massing or edge distortion, never a full outline or separate subject.
- **Required phrases:** "geological formations" + "gravitational lensing", "atmospheric phenomenon", "spatial warping"
- **creature_integration.visibility example:** "Massive geological formations barely evoke [QUALITY] through natural arrangement — gravitational lensing around formation edges lets a close viewer momentarily read one [FRAGMENT], partially obscured and never dominant"
- **core_concept phrasing (condensed):** "massive [environment] formations carrying [QUALITY], subtle spatial distortion warping the atmosphere around geological forms"
- Physics effects are environmental, not dominant

**Important:** `creature_integration.visibility` gets the full expanded language. `core_concept` gets the condensed version. Both must use language matching YOUR SELECTED OPTION. Both use [QUALITY] — the creature's physical essence in geological terms, drawn from the table below.

---

## CRITICAL: NO CREATURE NAME IN CONTENT FIELDS

The creature name (`{creature}`) is **FORBIDDEN** in all descriptive/positive fields. The image generator reads this JSON directly — naming the creature anchors it toward literal representation even when surrounded by "avoid" language.

**Important:** Do not write the creature name in `mandatory_exclusions` either. The image model receives this JSON directly, and creature-name tokens can still anchor the model even when they appear in negative wording.

**Forbidden (all fields, including `creature_integration.visibility`):**
- `core_concept` — NO creature name, use [QUALITY] only
- `composition.midground` — NO creature name, NO fragment phrase
- `creature_integration.visibility` — NO creature name, use [QUALITY] plus one embedded fragment phrase only
- `rendering.avoid` and `mandatory_exclusions` — use generic creature/anatomy exclusions only

**Additionally forbidden in ALL positive fields (including `creature_integration.visibility`):**
`humanoid`, `human-like`, `humanlike`, `figure` (as a creature or human subject), `silhouette`, `shaped like`, `resembles`, `full-body`, `readable face`. These words cause image models to render a literal human or creature shape even when surrounded by landscape language.

## CREATURE SIGNATURE FRAGMENT

Use the provided fragment phrase `{creature_fragment_phrase}` as a secondary embedded cue.
Use the fragment grounding `{creature_fragment_grounding}` only as hidden context to understand why this fragment belongs to this creature. Do NOT quote or restate that grounding directly.

If the fragment phrase is blank, do not invent a replacement. Omit fragment language entirely and keep the creature read driven only by [QUALITY].

- It must appear **exactly once** in the positive JSON.
- It must appear in `creature_integration.visibility` **only**.
- It must read as something the viewer might discover on a second look, never as a clearly presented feature.
- It must stay partially obscured, half-consumed by surrounding massing, patterning, haze, or distortion.
- It must read as a secondary interruption in the landscape, never the main contour, centerpiece, or hero subject.
- It must not turn into a full head/body reconstruction.
- Do not repeat it in `core_concept`, `composition`, `lighting`, `environment_details`, or anywhere else.
- Treat it as a small discoverable reward inside the landscape, not the landscape's whole idea.
- Prefer pareidolia language such as "might suggest", "to someone looking closely", or "the viewer's eye might catch" so the fragment is inferred rather than staged.

**How to derive [QUALITY] — the creature's physical essence in geological terms:**

Look at the creature, find the closest archetype below, and pick the quality phrase that best fits the current art keywords and environment. These are starting points — not fixed rules. If nothing fits, derive your own following the same pattern.

| Creature archetype | Example quality phrases |
|---|---|
| **Aerial / Speed** (falcons, hawks, swifts, hornets) | "swift precision", "pointed focus", "velocity", "diving sharpness" |
| **Soaring / Vast** (eagles, condors, albatross, cranes) | "thermal drift", "suspended mass", "expansive stillness" |
| **Aquatic / Fluid** (whales, sharks, dolphins, rays, jellyfish) | "oceanic pressure", "fluid depth", "submerged weight" |
| **Serpentine / Coiling** (snakes, eels, centipedes, anacondas) | "coiling tension", "sinuous flow", "constricting depth" |
| **Massive / Heavy** (elephants, bears, bison, hippos, rhinos) | "gravitational mass", "crushing permanence", "ancient stillness" |
| **Ancient / Armored** (turtles, crocodiles, beetles, crabs, nautilus) | "primordial patience", "armored density", "geological time" |
| **Arachnid / Angular** (scorpions, spiders, mantis, lobsters) | "fracture-line tension", "segmented rock pressure", "tense stillness" |
| **Delicate / Ephemeral** (moths, butterflies, dragonflies, fireflies) | "fragile tracery", "ephemeral pattern", "delicate threading" |
| **Canine / Pack** (wolves, foxes, jackals, hyenas) | "lean boundary tension", "low-center pressure", "distributed edge-weight" |
| **Feline / Predator** (lions, tigers, leopards, jaguars) | "compressed force", "spring-loaded stone pressure", "coiled geological density" |
| **Avian / Patient** (owls, vultures, ravens, herons) | "patient weight", "hollow density", "watchful mass" |
| **Mythological / Hybrid** (gryphons, phoenixes, dragons, chimeras) | "composite mass", "ancient fusion", "primordial scale" |

**Deriving your own:** Describe how the creature's *body* would feel as stone or landscape — its mass, density, tension, flow, or precision. Physical and geological only. NOT what the creature does, NOT its personality or mythology.

**CRITICAL — these quality phrases will cause creature bleed. AVOID them:**
- Behavioral: "storm command", "watchful patience", "hunting focus" → produce recognizable creature poses
- Animal-role words: "predatory", "hunter", "pack leader", "stalking", "prey" → produce readable animal subjects
- Character: "sovereign stillness", "regal weight", "noble mass" → produce humanoid/throne forms
- Action: "poised to strike", "mid-flight tension" → produce literal creature action

**CRITICAL — these fragment mistakes will cause creature bleed. AVOID them:**
- Generic singleton parts: "claw", "beak", "tail", "wing", "tentacle", "fin"
- Random body-part picks that are not distinctive to the chosen creature
- Creature assembly: head + wing + tail, face + torso, or any combination that reconstructs a full body plan
- Dominance language: "centerpiece", "hero form", "dominant contour", "main silhouette"

---

## CONTRAST MAPPING FROM ART KEYWORDS

Map the art keywords to contrast levels:

- **luminous, serene, expansive, flowing, balanced:** → "low to medium contrast with gentle gradients"
- **drifting, muted, rhythmic, moderate, detached:** → "medium contrast with subtle definition"
- **heavy, dim, pressured, still, subdued:** → "medium to high contrast with atmospheric weight"
- **crushing, collapsed, volatile, turbulent, fractured, devastating:** → "extreme contrast with sharp edges"

---

## LIGHTING TIME MAPPING

Based on depth level and environment:

- **SURFACE:** "open sky illumination", "broad exposed light", "perpetual sky-light"
- **MID-DEPTH:** "angled shelter-light", "partial sky spill", "side-entering filtered light"
- **DEEP:** "lateral filtered light from a side crack or wall seam", "buried recess shadow", "compressed interior light from a surrounding wall — not from above"
- **ABYSS:** "interior pressure light", "fracture-lit compression", "sealed internal darkness"

---

## RECOVERY SEVERITY MAPPING

Recovery does NOT change the scene's geometry. It changes the condition of the world occupying that geometry.

- **HIGH recovery:** intact, coherent, supported, stable, breathable
- **MID recovery:** weathered, muted, worn, held together, coasting
- **LOW recovery:** fractured, depleted, stripped, cooling, pressure-stressed, partially failed, post-event, collapsed, demolished — at **LOW + ABYSS**: total structural failure, wreckage, abomination-state aftermath, the world after demolition

**Spectrum extremes:**
- **HIGH + SURFACE** = calmest possible state: open sky, self-sustaining world, full light, nothing interrupted
- **LOW + ABYSS** = most catastrophic possible state: sealed compression + total material failure — maximum destruction vocabulary is permitted here

**Use this split consistently:**
- **Environment** determines the material world
- **Depth** determines relation to sky and light
- **Recovery** determines structural integrity and damage
- **Art keywords** determine contrast, texture, and tension
- **One-liner** compresses the final felt state

**CRITICAL:** Do not let recovery only darken the image. LOW recovery must appear in the materials, surfaces, air, residue, and structural condition of the place.

---

## DEPTH VISUAL PHRASE FOR CORE_CONCEPT

**CRITICAL:** Do NOT write the depth label (SURFACE / MID-DEPTH / DEEP / ABYSS) literally in `core_concept`. These are system category labels — the image generator has no idea what they mean. Translate to spatial visual language using the table below and the specific environment chosen.

**Depth is light direction, not just position:** SURFACE = light from everywhere. MID-DEPTH = light from one direction. DEEP = light entering laterally from a crack or seam in the surrounding walls — no open sky, no overhead aperture, no vertical shaft through a hole above. ABYSS = no light from above, only from fractures within the material.

You have `{depth_level}` and `{depth_keywords}` available. Use them to derive a concrete spatial phrase.

| Depth Level | Depth Keywords | Visual phrase pattern for core_concept |
|---|---|---|
| SURFACE | Celestial, Elevated, Bright, Open | "across exposed [environment] formations under open sky" |
| MID-DEPTH | Beneath, Overhang, Partial-sky, One-direction-light | "beneath [environment] formations, partial sky still visible, light entering from one direction" |
| DEEP | Buried-recess, Overhead-mass, Compressed-enclosure, Filtered-light | "inside a buried [environment] recess, overhead geological mass pressing close above, light entering laterally from a crack in the surrounding rock — no opening above" |
| ABYSS | Sealed, Compression-fractures, Interior-pressure, No-above | "sealed within the buried [environment] core, material pressing from all sides, illuminated only by thin blades through compression fractures" |

**Examples (combine depth pattern + specific environment):**
- Glacial Valley + MID-DEPTH → "beneath glacial valley walls, partial sky still visible, light entering from one direction under ice and moraine overhangs"
- Volcanic + DEEP → "inside a buried volcanic recess, overhead rock mass compressed above, magma-filtered light entering from a distant vent crack"
- Cave Systems + ABYSS → "sealed within the buried cave core, stone pressing from all sides, illuminated only by thin blades through compression fractures"
- Void/Space (Low) + ABYSS → "sealed within a buried cosmic pocket, fractured debris pressing from all sides, illuminated only by thin blades through compression seams"
- Forest/Jungle + SURFACE → "across exposed jungle formations under open sky, light arriving from every direction"

The phrasing is yours — adapt the pattern to fit the specific environment. Never use the raw depth label.

**Environment-specific depth manifestations (preserve the environment's identity across all tiers):**
- **Glacial Valley**
  - SURFACE → exposed valley floor under open sky, polished rock and ice readable end to end
  - MID-DEPTH → beneath moraine shelves and ice overhangs, partial sky still visible
  - DEEP → inside a subglacial recess, compressed ice mass pressing close overhead, cold light bleeding laterally through a translucent ice seam in the far wall — no rupture above
  - ABYSS → sealed glacial core, ice mass pressing from all sides, thin fracture-light only
- **Volcanic**
  - SURFACE → exposed lava ridges and obsidian fields under open sky
  - MID-DEPTH → beneath volcanic shelves and crater lips, side-entering furnace glow
  - DEEP → inside a buried volcanic recess, overhead rock mass compressed above, magma-filtered light entering laterally from a vent crack in the surrounding side wall — no opening above
  - ABYSS → buried magma core, rock pressure from all sides, ember-light only through compression fractures
- **Void/Space (Low)**
  - SURFACE → exposed cosmic terrain with open starfield and readable horizon
  - MID-DEPTH → beneath debris canopies or asteroid lips, partial sky still visible through gaps
  - DEEP → inside a dense buried debris recess, overhead debris mass pressing close, diffuse light from multiple small fractures in the surrounding debris walls — no single gap above
  - ABYSS → sealed interior pocket of compressed debris, internal particle-glow only, no open starfield
- **Cave Systems**
  - SURFACE → cave mouth and surrounding stone under open sky
  - MID-DEPTH → beneath overhangs and rock recesses near the cave mouth, partial sky still visible
  - DEEP → inside a buried cave recess, overhead rock mass close above, faint light entering laterally through a mineral crack in the side wall — nothing descending from above
  - ABYSS → sealed stone core, mineral phosphorescence and thin fracture-light only
- **Forest/Jungle**
  - SURFACE → exposed canopy breaks and root masses under open sky
  - MID-DEPTH → beneath canopy overhang and root arches, partial sky still visible in gaps
  - DEEP → inside a buried root recess, overhead tangle of roots and stone pressing close above, dim light filtering laterally through a compressed gap between root masses at ground level — no opening above
  - ABYSS → sealed root-and-stone core, pressure-lit seams and wet mineral glow only

**Hard depth rules:**
- **SURFACE** must remain spatially open even in harsh states. Show distress through glare, fracture, instability, and exposure — never by enclosing the scene like a lower depth tier.
- **DEEP** must not show a centered overhead opening, circular hole, or single vertical shaft of light descending from above. The `Overhead-mass` keyword means there is geological mass pressing close above — it does NOT mean there is a gap or opening in that mass from which light descends. Light at DEEP enters laterally from a crack in a side wall, seeps through a translucent mineral face, reflects from a surface below, or diffuses from multiple small fractures in the surrounding walls. It does not fall from a hole above. Do not interpret "one direction" as vertical.
- **ABYSS** must not read as spacious, celestial, majestic, or horizon-led. It is sealed, internal, pressure-lit, and materially enclosing.
- **ABYSS** must not show a cave mouth, skylight, tunnel exit, horizon line, large opening, or dominant bright zone in the upper third of the frame. There is no opening above — narrow or otherwise. Any fracture is a hairline seam within solid material, not a gap that functions as a light source. The upper portion of the image is enclosed material, not a luminous aperture. The viewer must feel pressed inside the material from all sides — above, behind, and to both sides — not standing at the base of a chamber looking upward toward an exit. Light in ABYSS does not arrive from any above-direction source; it emanates from within the compressed material itself.

**Hard recovery rule:**
- If `recovery_zone` is **LOW**, the world must not read pristine, untouched, graceful, or serene. Distress must appear physically through fracture, depletion, residue, cooling, pressure, asymmetry, or aftermath — not just dimmer lighting.
- If `recovery_zone` is **LOW** and `depth_level` is **ABYSS**: apply maximum failure vocabulary. The world is demolished. Use: collapsed structure, wreckage, total material failure, abomination-state aftermath, crushed world logic. This is the most catastrophic state in the system.

---

## COLOR TEMPERATURE MAPPING

From behavioral body keywords and environment:

- **Sharp, restored, charged, solid, warm:** → "warm" or "balanced"
- **Tense, wired, drained, numb:** → "cool with sharp accents"
- **Grinding, destroyed, wrecked:** → "cool desaturated"
- **Decent, passive, coasting:** → "balanced neutral"

Adjust for environment type (volcanic = inherently warm, ice = inherently cool)

---


## FORBIDDEN LANGUAGE

**NEVER use anywhere in the JSON:**
- Broad anatomical reconstruction terms outside the single approved fragment phrase: body, head, wings, tail, legs, antennae, scales, fins, claws
- Direct sculptural forms: "wing-shaped rock", "serpent body formation", "creature silhouette", "animal figure", "beast statue", "full-body creature form"
- Action verbs: flying, swimming, standing, rising, soaring, prowling, watching
- Literal descriptions: "shaped like [creature]", "resembles [creature]"

---

## REQUIRED LANGUAGE

**MUST appear in `creature_integration.visibility` — use per your selected option:**
- Option A: "formations barely evoke", "geological formations", "accidental arrangement", "natural erosion patterns", "viewer's pareidolia"
- Option B: "geological masses dominate", "geological formations", "patterns that might suggest", "viewer's pareidolia"
- Option C: "geological formations", "formations barely evoke", "gravitational lensing", "atmospheric phenomenon", "viewer's pareidolia"

**core_concept uses only the CONDENSED phrasing from each option's guide — NOT the full visibility language.**

---

## BRIGHTNESS MINIMUM ENFORCEMENT

Based on visibility_range:
- 70-100%: "bright open landscape with minimal shadow"
- 50-70%: "balanced light and shadow with clear focal points"
- 40-50%: "atmospheric darkness with 40-50% illuminated elements"
- 40% minimum (ABYSS): "sealed darkness with mandatory 40% minimum visible content from internal sources only — fracture-light, mineral glow, pressure seams, compressed luminescence"

**CRITICAL:** Never go below 40% visible content. Darkness is accent, not dominant. In ABYSS, that visibility must come from internal sources only — no open sky and no broad atmospheric glow from above.

---

## MASTER JSON TEMPLATE

Fill ALL placeholders below using the input data and rules above:

```json
{
  "core_concept": "Surreal alien {environment} [SPATIAL LOCATION: use DEPTH VISUAL PHRASE mapping — translate {depth_level} + {depth_keywords} into spatial visual language for this specific {environment}; NEVER write the depth label literally] where [APPLY YOUR SELECTED BLEND OPTION LANGUAGE from core_concept phrasing guide — describe landscape formations only; use [QUALITY] as the baseline creature layer; DO NOT write the creature name {creature}; if a fragment phrase is provided, DO NOT write the fragment phrase {creature_fragment_phrase}], felt state: {one_liner}, [APPLY Recovery Severity Mapping physically — the place must show whether it is intact, worn, or depleted through material condition, residue, fracture, or aftermath; DO NOT write the word recovery], more landscape than creature",

  "style_aesthetic": {
    "era": "1970s-1980s science fiction",
    "mood": "dreamy, mystical, otherworldly, nostalgic",
    "reference": "vintage sci-fi album covers and movie posters",
    "quality": "cinematic fantasy concept art"
  },

  "rendering": {
    "technique": "high detail volumetric 3D digital art with vintage film effects — cinematic photographic render quality, NOT illustration",
    "contrast": "[APPLY CONTRAST MAPPING from art keywords]",
    "definition": "clear forms with atmospheric depth",
    "material_quality": "[LOOKUP from Material Quality table above using environment type, THEN MODULATE intensity qualifiers per MATERIAL QUALITY BEHAVIORAL MODULATION section using art keywords {art_keywords}, THEN APPLY Recovery Severity Mapping to the condition of the material itself: HIGH=intact/coherent, MID=worn/held-together, LOW=fractured/depleted/cooling/post-event]",
    "avoid": [
      "soft painterly brush strokes",
      "2D cartoon style",
      "flat illustration",
      "watercolor softness",
      "modern photorealistic 3D render",
      "hyper-realistic CG",
      "standalone creature subject",
      "literal animal figure in foreground or midground",
      "statue-like beast form",
      "humanoid or human-like figure",
      "readable face, eyes, head, muzzle, snout, teeth, paws, limbs, torso, or anatomy"
    ],
    "DEPLOYMENT_NOTE": "Items in avoid array MUST be passed as weighted negative prompts to the image generator (if supported by your chosen system)"
  },

  "visual_effects": {
    "CRITICAL_EFFECTS": [
      "lens bloom and glow around bright areas",
      "[ENVIRONMENT-SPECIFIC atmospheric effect based on {environment} type]",
      "film grain texture overlay",
      "soft light halation",
      "vintage color grading",
      "subtle chromatic aberration on edges only",
      "[ENVIRONMENT-SPECIFIC particle effects: volcanic ash / ice crystals / lightning motes / etc.]"
    ]
  },

  "composition": {
    "format": "VERTICAL PORTRAIT 3:4 ASPECT RATIO",
    "foreground": "[ENVIRONMENT-SPECIFIC foreground: lava fragments / ice shards / storm mist / desert sand / etc.]",
    "midground": "[BLEND OPTION DESCRIPTION: sculptural formations with optional secondary texture based on Option A/B/C; if a fragment phrase is provided, DO NOT mention the fragment phrase {creature_fragment_phrase}]",
    "background": "[Depth-aware background: SURFACE/MID-DEPTH/DEEP may use distance and atmospheric perspective; ABYSS: geological mass fills the frame — weight and compression, no container geometry, no directional enclosure; no distant vista, no horizon, no cave-mouth read, no large bright zone in the upper frame]",
    "sky": "[APPLY depth-aware celestial treatment: SURFACE=open sky with moons clearly visible; MID-DEPTH=partial sky framed by shelter; DEEP=no open sky; moon may appear as faint cold light bleeding through a lateral side crack, or omit entirely — no vertical light beam, no overhead aperture; ABYSS=no open sky, no bright aperture, no glowing slot or fracture gap above — the upper zone of the frame is solid enclosed material; omit entirely, or render as a faint shape impression fully embedded within solid compressed material]"
  },

  "lighting": {
    "time": "[APPLY Lighting Time Mapping based on {depth_level} and {environment}]",
    "quality": "[Derive from art keywords] with [recovery-aware bloom/intensity: HIGH=clear stable luminous bloom / MID=held-back weathered moderate bloom / LOW=depleted cutting residual or dying bloom]",
    "atmosphere": "[In 1-2 short phrases describe how light and air physically behave — e.g. 'light settles without drama, air still and undisturbed' or 'light harsh and cutting, atmosphere charged at every surface'. Physical language only. Do NOT copy art keywords literally. Recovery severity must affect the behavior of the air and light physically: LOW should feel burdened, depleted, aftermath-driven, or pressure-stressed rather than merely darker.]",
    "glow": "[ENVIRONMENT-SPECIFIC glow sources: magma orange / ice blue / lightning white / plasma purple / etc.]"
  },

  "color_palette": {
    "primary_tones": ["[COLOR_1 from environment]", "[COLOR_2 from environment]", "[COLOR_3 from environment]", "[COLOR_4 from depth/atmosphere]"],
    "sky_gradient": "[Based on {depth_level}: SURFACE=open sky / MID-DEPTH=partial-sky spill / DEEP=enclosed interior, lateral light bleed only / ABYSS=sealed interior, no open sky]",
    "treatment": "vintage film stock rendering, slightly desaturated",
    "temperature": "[APPLY Color Temperature Mapping from body keywords and environment]"
  },

  "environment_details": {
    "setting": "{environment} at {depth_level}",
    "terrain": "[Combine environment materials + depth manifestation, THEN apply Recovery Severity Mapping to the world's condition: intact and coherent at HIGH, worn and held together at MID, fractured/depleted/cooling/post-event at LOW. Example target logic: open glacial floor but stress-fractured, or sealed magma core cooling into split stone.]",
    "atmosphere": "[In 1-2 short phrases describe what the physical {environment} looks like carrying this state — what the viewer sees in the materials, formations, air, residue, and structural condition. Visual description only, NOT behavioral or emotional text. Recovery severity must be visible in the physical world, not just implied by mood. depth layers creating spatial separation]",
    "celestial": "[APPLY depth-aware moon handling: SURFACE={moon_count} moons openly visible; MID-DEPTH={moon_count} moons partially framed by overhangs or cover; DEEP=moonlight may enter from a distant opening but moons need not be directly visible; ABYSS=omit entirely, or render as a faint shape impression fully embedded within solid compressed material]"
  },

  "creature_integration": {
    "blend": "Option [A/B/C based on art keywords analysis]: [Copy exact blend description: 'Sculptural 100%' or 'Sculptural 60-70% + Pattern-Based 30-40%' or 'Sculptural 60-70% + Physics 30-40%']",
    "clarity": "semi-abstract — geological dominant, any creature read remains incidental and partial, fully geological in texture and material, never a separate entity",
    "visibility": "[APPLY BLEND OPTION LANGUAGE RULES for YOUR SELECTED OPTION using [QUALITY] as the baseline. If a fragment phrase is provided, use the fragment phrase {creature_fragment_phrase} exactly once, phrased as something a close viewer might infer through pareidolia rather than something clearly shown. Keep it partially obscured by surrounding massing/patterning/distortion, subordinate rather than central, and never enough to reconstruct a full creature. If no fragment phrase is provided, omit fragment language entirely and keep the creature read indirect through [QUALITY] alone.]",
    "priority": "100% landscape focus, creature as geological formation",
    "scale": "massive and epic",
    "texture": "{environment} materials completely dominant, sculptural always primary",
    "CRITICAL": "Sculptural always primary. NO literal creature forms. Secondary (if B/C) adds texture only, never dominates."
  },

  "technical_specifications": {
    "aspect_ratio": "VERTICAL 3:4 PORTRAIT",
    "detail_level": "high detail with atmospheric softness",
    "edge_treatment": "clean edges, no borders",
    "text": "NO TEXT, NO TITLES, NO OVERLAYS"
  },

  "mandatory_exclusions": [
    "no text or typography",
    "no borders or frames",
    "no 2D cartoon style",
    "no 2D illustration or flat art style",
    "no concept art or digital painting style",
    "no comic or graphic novel aesthetic",
    "no photorealistic animal anatomy",
    "no creature as a separate entity in the scene — creature IS the landscape",
    "no standalone animal subject, no statue-like creature, no mascot-like figure",
    "no humanoid, human-like figure, face, eyes, head, muzzle, snout, teeth, paws, limbs, torso, or readable anatomy",
    "no modern 3D render aesthetic",
    "{generic_creature_exclusion}"
  ],

  "consistency_anchors": {
    "ALWAYS_INCLUDE": [
      "[ENVIRONMENT-SPECIFIC foreground element based on {environment}]",
      "[DEPTH-APPROPRIATE celestial treatment matching {depth_level}]",
      "[DOMINANT_COLOR from environment] palette",
      "film grain texture",
      "lens bloom effects",
      "VERTICAL 3:4 FORMAT",
      "{visibility_range} visible content maintained"
    ]
  }
}
```

---

## SELF-CHECK VALIDATION

Before outputting the JSON, verify:

- [ ] Blend option selected in BLEND OPTION SELECTION section with justification provided
- [ ] Blend option language matches YOUR SELECTED OPTION rules exactly
- [ ] NO forbidden language appears anywhere (no anatomical terms, no action verbs)
- [ ] Required language phrases present in `creature_integration.visibility`
- [ ] Material quality filled from lookup table using {environment}
- [ ] Brightness minimum enforced: {visibility_range} stated in consistency_anchors
- [ ] All effects described as "subtle" where applicable
- [ ] Aspect ratio is "VERTICAL 3:4 PORTRAIT"
- [ ] Behavioral one-liner `{one_liner}` reflected in core_concept felt state (no planet names, no house numbers, no astrology)
- [ ] `avoid` array includes "standalone creature subject", "literal animal figure in foreground or midground", and "statue-like beast form"
- [ ] No positive field uses representational or humanoid language such as `humanoid`, `human-like`, `figure`, `silhouette`, `shaped like`, `full-body`, or `readable face` — anatomy words (jaw, limb, eye, etc.) are permitted only inside `creature_integration.visibility` as part of the fragment description
- [ ] `"text": "NO TEXT, NO TITLES, NO OVERLAYS"` present in technical_specifications
- [ ] Celestial treatment matches depth: open sky only at SURFACE, partial framing at MID-DEPTH, lateral light bleed or omission at DEEP (no overhead aperture), fracture glimpse or omission at ABYSS
- [ ] Depth level {depth_level} influences lighting.time and composition.sky appropriately
- [ ] If environment is "Crystalline (Active)" AND YOUR SELECTED OPTION is "B", Crystalline Override applied
- [ ] `core_concept` uses blend-appropriate language matching YOUR SELECTED OPTION (not default Option A phrasing when B or C was selected)
- [ ] `core_concept` does NOT contain a raw depth label (SURFACE/MID-DEPTH/DEEP/ABYSS) — must be spatial visual language
- [ ] `rendering.material_quality` intensity qualifiers adjusted for art keywords (not blindly copied from table)
- [ ] `lighting.atmosphere` is 1-2 short physical phrases describing light/air behavior — NOT raw art keywords
- [ ] `environment_details.atmosphere` is 1-2 short visual phrases describing the physical space — NOT behavioral or emotional text
- [ ] `rendering.technique` specifies volumetric 3D cinematic quality — output must NOT look like 2D illustration, flat art, or digital painting
- [ ] `creature_integration.visibility` and `core_concept` use [QUALITY] from the creature archetype table — physical/atmospheric descriptor only, NOT behavioral or character-based
- [ ] No field describes a single creature subject, animal figure, statue, mascot, or isolated full-body form in the frame
- [ ] ABYSS does not read as spacious, celestial, majestic, or horizon-led
- [ ] ABYSS shows no cave mouth, skylight, tunnel exit, horizon, scenic opening, or dominant bright zone in the upper frame — the upper third of the image is enclosed solid material, not a luminous aperture; light in ABYSS emanates from within the compressed material, never arrives from above
- [ ] SURFACE remains spatially open even in harsh states; distress reads through glare, fracture, instability, and exposure rather than enclosure
- [ ] Recovery changes world condition, not scene geometry: HIGH feels intact, MID feels weathered, LOW feels fractured/depleted/post-event
- [ ] LOW recovery does not read pristine, elegant, untouched, or serene even when depth is SURFACE or MID-DEPTH

---

## OUTPUT INSTRUCTIONS

1. Analyze the art keywords, environment, creature, and interpretation to SELECT your blend option (A/B/C)
2. Fill all placeholders in the template above using the input data
3. Apply all mapping rules (contrast, lighting time, color temperature, materials)
4. Follow YOUR SELECTED blend option language rules precisely for `creature_integration.visibility`
5. Put your blend choice directly in the `creature_integration.blend` field (e.g., "Option B: Sculptural 60-70% + Pattern-Based 30-40%")
6. Ensure all validation checks pass
7. Output ONLY the complete filled JSON - NO text before or after
8. Do NOT include markdown code fences (```json) - output raw JSON only
9. Do NOT include blend selection justification outside the JSON - your choice goes in the "blend" field

---

**OUTPUT THE FILLED JSON NOW:**
