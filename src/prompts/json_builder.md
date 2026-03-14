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
- **Required phrases:** "formations barely evoke", "geological features", "natural erosion patterns", "accidental arrangement", "viewer's pareidolia"
- **creature_integration.visibility example:** "Geological formations barely evoke [QUALITY] through natural erosion patterns — massive stone structures whose accidental arrangement might suggest [QUALITY] to the viewer's imagination, nothing more"
- **core_concept phrasing (condensed):** "massive [environment] formations through natural erosion patterns, accidental geological arrangement carrying [QUALITY] to the viewer's imagination"
- NO secondary texture mentioned

### Option B — Sculptural 60-70% + Pattern-Based 30-40%
- Sculptural primary, with delicate crystal/frost/light patterns threading through
- **Required phrases:** "geological formations" + "patterns that might suggest", "viewer's pareidolia"
- **CRYSTALLINE OVERRIDE:** If environment is "Crystalline (Active)", the sculptural mass itself IS optically active crystal. Describe with: "subsurface light scattering", "internal refraction", "volumetric mineral glow". The mass IS crystal, not stone decorated with crystal.
- **creature_integration.visibility example:** "Sculptural geological masses dominate, with delicate [secondary texture] threading through that might suggest [QUALITY] — the viewer's pareidolia connects [pattern details] to [QUALITY], but landscape remains primary"
- **core_concept phrasing (condensed):** "sculptural [environment] masses dominate, delicate [secondary texture: heat refraction / frost tracery / light patterns] threading through, viewer's pareidolia finding [QUALITY] in [secondary texture details]"
- Secondary is ALWAYS subordinate, never equal
- **FORBIDDEN in core_concept when Option B selected:** "barely evoke", "natural erosion patterns", "accidental arrangement" — these are Option A phrases. Do NOT use them. core_concept must start with "sculptural [environment] masses dominate"

### Option C — Sculptural 60-70% + Physics 30-40%
- Sculptural primary, with spatial distortion/gravitational lensing adding uncanny tension
- **Required phrases:** "geological formations" + "gravitational lensing", "atmospheric phenomenon", "spatial warping"
- **creature_integration.visibility example:** "Massive geological formations barely evoke [QUALITY] through natural arrangement — gravitational lensing around formation edges creates subtle spatial distortion that adds uncanny tension, purely atmospheric phenomenon enhancing the viewer's perception"
- **core_concept phrasing (condensed):** "massive [environment] formations carrying [QUALITY], subtle spatial distortion warping the atmosphere around geological forms"
- Physics effects are environmental, not dominant

**Important:** `creature_integration.visibility` gets the full expanded language. `core_concept` gets the condensed version. Both must use language matching YOUR SELECTED OPTION. Both use [QUALITY] — the creature's physical essence in geological terms, drawn from the table below.

---

## CRITICAL: NO CREATURE NAME IN CONTENT FIELDS

The creature name (`{creature}`) is **FORBIDDEN** in all descriptive/positive fields. The image generator reads this JSON directly — naming the creature anchors it toward literal representation even when surrounded by "avoid" language.

**Allowed (negative prompt lists only):**
- `rendering.avoid` — creature name in negative list is fine
- `mandatory_exclusions` — creature name here is fine

**Forbidden (all other fields):**
- `core_concept` — NO creature name, use [QUALITY] only
- `composition.midground` — NO creature name
- `creature_integration.visibility` — NO creature name, use [QUALITY] only

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
| **Arachnid / Angular** (scorpions, spiders, mantis, lobsters) | "angular precision", "geometric density", "tense stillness" |
| **Delicate / Ephemeral** (moths, butterflies, dragonflies, fireflies) | "fragile tracery", "ephemeral pattern", "delicate threading" |
| **Canine / Pack** (wolves, foxes, jackals, hyenas) | "lean tension", "predatory stillness", "low-center weight" |
| **Feline / Predator** (lions, tigers, leopards, jaguars) | "coiled readiness", "compressed power", "muscular density" |
| **Avian / Patient** (owls, vultures, ravens, herons) | "patient weight", "hollow density", "watchful mass" |
| **Mythological / Hybrid** (gryphons, phoenixes, dragons, chimeras) | "composite mass", "ancient fusion", "primordial scale" |

**Deriving your own:** Describe how the creature's *body* would feel as stone or landscape — its mass, density, tension, flow, or precision. Physical and geological only. NOT what the creature does, NOT its personality or mythology.

**CRITICAL — these quality phrases will cause creature bleed. AVOID them:**
- Behavioral: "storm command", "watchful patience", "hunting focus" → produce recognizable creature poses
- Character: "sovereign stillness", "regal weight", "noble mass" → produce humanoid/throne forms
- Action: "poised to strike", "mid-flight tension" → produce literal creature action

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

- **SURFACE:** "perpetual twilight", "eternal dawn", "celestial day", "open sky illumination"
- **MID-DEPTH:** "filtered twilight", "enclosed dusk", "cavern half-light"
- **DEEP:** "subterranean darkness", "buried twilight", "deep shadow with limited glow"
- **ABYSS:** "primordial darkness", "near-extinction light", "void compression"

---

## DEPTH VISUAL PHRASE FOR CORE_CONCEPT

**CRITICAL:** Do NOT write the depth label (SURFACE / MID-DEPTH / DEEP / ABYSS) literally in `core_concept`. These are system category labels — the image generator has no idea what they mean. Translate to spatial visual language using the table below and the specific environment chosen.

You have `{depth_level}` and `{depth_keywords}` available. Use them to derive a concrete spatial phrase.

| Depth Level | Depth Keywords | Visual phrase pattern for core_concept |
|---|---|---|
| SURFACE | Celestial, Elevated, Bright, Open | "above towering [environment] formations under open [sky/void]" |
| MID-DEPTH | Sheltered, Cavern, Enclosed, Filtered | "within enclosed [environment] [chambers/caverns] with filtered [light/glow]" |
| DEEP | Subterranean, Obscured, Limited, Buried | "deep in subterranean [environment] with limited [glow/illumination]" |
| ABYSS | Void, Compressed, Primordial, Darkness | "at the primordial [environment] core, near-darkness pressing in from all sides" |

**Examples (combine depth pattern + specific environment):**
- Volcanic + MID-DEPTH → "within enclosed volcanic chambers with filtered magma glow"
- Ice + SURFACE → "above towering glacier peaks under open alien sky"
- Ocean + DEEP → "deep in subterranean ocean trenches with limited bioluminescent illumination"
- Volcanic + ABYSS → "at the primordial volcanic core, near-darkness pressing in from all sides"

The phrasing is yours — adapt the pattern to fit the specific environment. Never use the raw depth label.

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
- Anatomical terms: body, head, wings, tail, legs, antennae, scales, fins, claws
- Direct sculptural forms: "wing-shaped rock", "serpent body formation", "creature silhouette"
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
- 40% minimum (ABYSS): "deep darkness with mandatory 40% minimum visible content"

**CRITICAL:** Never go below 40% visible content. Darkness is accent, not dominant.

---

## MASTER JSON TEMPLATE

Fill ALL placeholders below using the input data and rules above:

```json
{
  "core_concept": "Surreal alien {environment} [SPATIAL LOCATION: use DEPTH VISUAL PHRASE mapping — translate {depth_level} + {depth_keywords} into spatial visual language for this specific {environment}; NEVER write the depth label literally] where [APPLY YOUR SELECTED BLEND OPTION LANGUAGE from core_concept phrasing guide — describe landscape formations only; DO NOT write the creature name {creature}; NO abstract creature quality phrases], felt state: {one_liner}, more landscape than creature",

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
    "material_quality": "[LOOKUP from Material Quality table above using environment type, THEN MODULATE intensity qualifiers per MATERIAL QUALITY BEHAVIORAL MODULATION section using art keywords {art_keywords}]",
    "avoid": [
      "soft painterly brush strokes",
      "2D cartoon style",
      "flat illustration",
      "watercolor softness",
      "modern photorealistic 3D render",
      "hyper-realistic CG",
      "obvious creature shapes",
      "{creature}-shaped objects",
      "{creature} anatomy"
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
    "midground": "[BLEND OPTION DESCRIPTION: sculptural formations with optional secondary texture based on Option A/B/C]",
    "background": "layered terrain with atmospheric perspective",
    "sky": "[DEPTH-APPROPRIATE sky from Lighting Time Mapping] with {moon_count} moons in upper third"
  },

  "lighting": {
    "time": "[APPLY Lighting Time Mapping based on {depth_level} and {environment}]",
    "quality": "[Derive from art keywords] with [recovery_zone-based intensity: HIGH=bright / MID=moderate / LOW=dim] bloom",
    "atmosphere": "[In 1-2 short phrases describe how light and air physically behave — e.g. 'light settles without drama, air still and undisturbed' or 'light harsh and cutting, atmosphere charged at every surface'. Physical language only. Do NOT copy art keywords literally.]",
    "glow": "[ENVIRONMENT-SPECIFIC glow sources: magma orange / ice blue / lightning white / plasma purple / etc.]"
  },

  "color_palette": {
    "primary_tones": ["[COLOR_1 from environment]", "[COLOR_2 from environment]", "[COLOR_3 from environment]", "[COLOR_4 from depth/atmosphere]"],
    "sky_gradient": "[Based on {depth_level}: SURFACE=open sky / MID-DEPTH=filtered / DEEP=obscured / ABYSS=void]",
    "treatment": "vintage film stock rendering, slightly desaturated",
    "temperature": "[APPLY Color Temperature Mapping from body keywords and environment]"
  },

  "environment_details": {
    "setting": "{environment} at {depth_level}",
    "terrain": "[Combine environment materials + depth manifestation: e.g. 'subterranean volcanic chambers with limited magma glow' or 'elevated ice peaks under open sky']",
    "atmosphere": "[In 1-2 short phrases describe what the physical {environment} looks like carrying this state — what the viewer sees in the materials, formations, and air. Visual description of the place only, NOT behavioral or emotional text. depth layers creating spatial separation]",
    "celestial": "{moon_count} moons, [visibility modifier: bright at SURFACE / dimmed at MID-DEPTH / faint at DEEP / barely visible at ABYSS]"
  },

  "creature_integration": {
    "blend": "Option [A/B/C based on art keywords analysis]: [Copy exact blend description: 'Sculptural 100%' or 'Sculptural 60-70% + Pattern-Based 30-40%' or 'Sculptural 60-70% + Physics 30-40%']",
    "clarity": "extremely abstract — sculptural geological dominant, secondary accent if B/C",
    "visibility": "[APPLY BLEND OPTION LANGUAGE RULES based on YOUR SELECTED OPTION — this is the most critical field]",
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
    "no obvious literal {creature}",
    "no recognizable creature shapes",
    "no modern 3D render aesthetic"
  ],

  "consistency_anchors": {
    "ALWAYS_INCLUDE": [
      "[ENVIRONMENT-SPECIFIC foreground element based on {environment}]",
      "{moon_count} moons in sky",
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
- [ ] `avoid` array includes "{creature}-shaped objects" and "{creature} anatomy"
- [ ] `"text": "NO TEXT, NO TITLES, NO OVERLAYS"` present in technical_specifications
- [ ] Sky description includes "{moon_count} moons in upper third"
- [ ] Depth level {depth_level} influences lighting.time and composition.sky appropriately
- [ ] If environment is "Crystalline (Active)" AND YOUR SELECTED OPTION is "B", Crystalline Override applied
- [ ] `core_concept` uses blend-appropriate language matching YOUR SELECTED OPTION (not default Option A phrasing when B or C was selected)
- [ ] `core_concept` does NOT contain a raw depth label (SURFACE/MID-DEPTH/DEEP/ABYSS) — must be spatial visual language
- [ ] `rendering.material_quality` intensity qualifiers adjusted for art keywords (not blindly copied from table)
- [ ] `lighting.atmosphere` is 1-2 short physical phrases describing light/air behavior — NOT raw art keywords
- [ ] `environment_details.atmosphere` is 1-2 short visual phrases describing the physical space — NOT behavioral or emotional text
- [ ] `rendering.technique` specifies volumetric 3D cinematic quality — output must NOT look like 2D illustration, flat art, or digital painting
- [ ] `creature_integration.visibility` and `core_concept` use [QUALITY] from the creature archetype table — physical/atmospheric descriptor only, NOT behavioral or character-based

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
