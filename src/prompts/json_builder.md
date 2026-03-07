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
- **Crystal Caves:** Angular crystals, gems, prismatic light refraction
- **Stone Monuments:** Weathered stone, granite, ancient carved formations
- **Mist/Fog Realms:** Volumetric fog, obscured visibility, moisture
- **Void/Space (Low):** Cosmic dust, minimal light, deep space darkness

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

---

## BLEND OPTION SELECTION (AI-DRIVEN)

**CRITICAL:** You must SELECT which blend option (A, B, or C) to use for this specific scene.

**Selection criteria:**

1. **Primary influence:** The art keywords `{art_keywords}` — these reflect the behavioral/emotional state and should heavily influence your choice:
   - Heavy/still keywords (crushing, collapsed, sinking, heavy, still, subdued, smoldering, suffocating) → **lean toward Option A**
   - Light/flow keywords (luminous, flowing, rhythmic, drifting, serene, expansive, balanced, harmonious, muted) → **lean toward Option B**
   - Volatile/distortion keywords (volatile, fractured, turbulent, suspended, dissociated, brittle, unstable, devastated) → **lean toward Option C**

2. **Secondary considerations:**
   - **Environment redundancy:** Does the environment `{environment}` already provide pattern-based or physics-based qualities?
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
    -   **Interpretation resonance:** Does the theme `{theme_essence}` suggest a specific approach?
        -   Transformation/flow themes → patterns (Option B)
        -   Pressure/gravity/collapse themes → physics (Option C)
        -   Permanence/endurance themes → pure sculptural (Option A)

3.  **Critical constraint:** Do NOT make automatic environment-based assumptions. Just because the environment is Crystalline doesn't mean Option B is automatic. Consider the full scene.

**Your task:** Analyze the art keywords, environment, creature, and {theme_essence}. Choose Option A, B, or C. State your choice and provide a brief justification (1 sentence).

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
- **Required phrases:** "formations barely evoke essence", "geological features", "natural erosion patterns", "accidental arrangement", "viewer's pareidolia"
- **creature_integration.visibility example:** "Geological formations barely evoke [QUALITY: the creature's abstract essence — e.g. 'ancient stillness', 'fluid permanence', 'crushing weight'] through natural erosion patterns — massive stone structures whose accidental arrangement might suggest [QUALITY] to the viewer's imagination, nothing more"
- NO secondary texture mentioned

### Option B — Sculptural 60-70% + Pattern-Based 30-40%
- Sculptural primary, with delicate crystal/frost/light patterns threading through
- **Required phrases:** "geological formations" + "patterns that might suggest", "viewer's pareidolia"
- **CRYSTALLINE OVERRIDE:** If environment is "Crystalline (Active)", the sculptural mass itself IS optically active crystal. Describe with: "subsurface light scattering", "internal refraction", "volumetric mineral glow". The mass IS crystal, not stone decorated with crystal.
- **creature_integration.visibility example:** "Sculptural geological masses dominate, with delicate [secondary texture] threading through that might suggest [QUALITY: abstract essence — e.g. 'swift precision', 'delicate tracing', 'pointed focus'] — the viewer's pareidolia connects [pattern details] to [QUALITY], but landscape remains primary"
- Secondary is ALWAYS subordinate, never equal

### Option C — Sculptural 60-70% + Physics 30-40%
- Sculptural primary, with spatial distortion/gravitational lensing adding uncanny tension
- **Required phrases:** "geological formations" + "gravitational lensing", "atmospheric phenomenon", "spatial warping"
- **creature_integration.visibility example:** "Massive geological formations barely evoke [QUALITY: abstract essence — e.g. 'oceanic weight', 'gravitational mass', 'crushing permanence'] through natural arrangement — gravitational lensing around formation edges creates subtle spatial distortion that adds uncanny tension, purely atmospheric phenomenon enhancing the viewer's perception"
- Physics effects are environmental, not dominant

---

## CRITICAL: NO CREATURE NAME IN CONTENT FIELDS

The creature name (`{creature}`) is **FORBIDDEN** in all descriptive/positive fields. The image generator reads this JSON directly — naming the creature anchors it toward literal representation even when surrounded by "avoid" language.

**Allowed (negative prompt lists only):**
- `rendering.avoid` — creature name in negative list is fine
- `mandatory_exclusions` — creature name here is fine

**Forbidden (all other fields):**
- `core_concept` — NO creature name, describe only landscape qualities
- `composition.midground` — NO creature name
- `creature_integration.visibility` — NO creature name, use [QUALITY] only

**How to reference the creature's essence without naming it:**

| Creature type | Abstract quality to use |
|---------------|------------------------|
| Fast/aerial (Falcon, Eagle, Hawk) | "swift precision", "pointed focus", "velocity", "diving clarity" |
| Aquatic (Whale, Shark, Octopus) | "oceanic pressure", "fluid depth", "weight from below" |
| Large/heavy (Elephant, Bear, Bison) | "gravitational mass", "crushing permanence", "ancient stillness" |
| Delicate (Moth, Butterfly, Dragonfly) | "fragile tracery", "ephemeral pattern", "delicate threading" |
| Serpentine (Snake, Eel) | "sinuous flow", "serpentine depth", "coiling tension" |
| Ancient (Turtle, Crocodile) | "primordial patience", "enduring weight", "geological time" |

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

**MUST appear in appropriate fields:**
- `creature_integration.visibility`: "formations barely evoke", "geological formations", "viewer's pareidolia" or "viewer's imagination perceives"
- Option A specific: "accidental arrangement", "natural erosion patterns"
- Option B specific: "patterns that might suggest" (for secondary only)
- Option C specific: "atmospheric phenomenon", "gravitational lensing" (for secondary only)

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
  "core_concept": "Surreal alien {environment} at {depth_level} where [APPLY BLEND OPTION LANGUAGE — describe landscape formations using abstract qualities; DO NOT write the creature name {creature}], felt state: {theme_essence}, more landscape than creature",

  "style_aesthetic": {
    "era": "1970s-1980s science fiction",
    "mood": "dreamy, mystical, otherworldly, nostalgic",
    "reference": "vintage sci-fi album covers and movie posters",
    "quality": "cinematic fantasy concept art"
  },

  "rendering": {
    "technique": "high detail digital art with vintage film effects",
    "contrast": "[APPLY CONTRAST MAPPING from art keywords]",
    "definition": "clear forms with atmospheric depth",
    "material_quality": "[LOOKUP from Material Quality table above using environment type]",
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
    "atmosphere": "{art_keywords}",
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
    "atmosphere": "{one_liner}, depth layers creating spatial separation",
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
- [ ] Behavioral theme `{theme_essence}` reflected in core_concept felt state (no planet names, no house numbers, no astrology)
- [ ] `avoid` array includes "{creature}-shaped objects" and "{creature} anatomy"
- [ ] `"text": "NO TEXT, NO TITLES, NO OVERLAYS"` present in technical_specifications
- [ ] Sky description includes "{moon_count} moons in upper third"
- [ ] Depth level {depth_level} influences lighting.time and composition.sky appropriately
- [ ] If environment is "Crystalline (Active)" AND YOUR SELECTED OPTION is "B", Crystalline Override applied

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
