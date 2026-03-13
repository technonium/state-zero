import os
import sys
import json
import argparse
import requests
from pathlib import Path
from dotenv import load_dotenv
from utils import get_project_root, get_output_root

load_dotenv(dotenv_path=get_project_root() / '.env', override=True)

# Import OpenRouter client for LLM calls
from openrouter_client import create_llm_client, OpenRouterError, LLMProviderError

class PromptOrchestrator:
    def __init__(self, llm_api_key: str, openrouter_api_key: str = None):
        """
        Initialize the PromptOrchestrator.
        
        Args:
            llm_api_key: Google API key (used as fallback)
            openrouter_api_key: OpenRouter API key (primary)
        """
        self.google_api_key = llm_api_key  # Keep for fallback
        
        # Create OpenRouter client with Google as fallback
        # Get OpenRouter key from parameter or environment
        or_key = openrouter_api_key or os.getenv("OPENROUTER_API_KEY")
        
        if or_key:
            self.llm_client = create_llm_client(
                openrouter_api_key=or_key,
                google_api_key=llm_api_key,
                model="minimax/minimax-m2.5",
                temperature=1.0,
                max_tokens=12000,
                thinking_budget=8000
            )
            self.use_openrouter = True
            print("✅ LLM Client initialized: OpenRouter (Minimax) with Google Gemini fallback")
        else:
            # Fall back to direct Google API usage
            self.api_key = llm_api_key
            self.use_openrouter = False
            print("⚠️  No OpenRouter key found, using direct Google API")
        
        # Base directory correctly resolved to project root using utils
        self.base_dir = get_project_root()
        run_date = os.getenv('PIPELINE_DATE')
        output_root = get_output_root()
        if run_date:
            self.output_dir = output_root / run_date
        else:
            self.output_dir = output_root
        self.templates_dir = self.base_dir / 'src' / 'prompts'
        
        # Ensure directories exist
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.templates_dir.mkdir(parents=True, exist_ok=True)

    def load_template(self, template_name: str) -> str:
        """Load prompt template from src/prompts/"""
        template_path = self.templates_dir / f"{template_name}.md"
        if not template_path.exists():
            print(f"Template {template_name}.md not found at {template_path}. Defaulting to empty prompt space.")
            return "Placeholder prompt."
            
        with open(template_path, 'r', encoding='utf-8') as f:
            return f.read()

    def fill_template(self, template: str, data: dict) -> str:
        """Fill template placeholders with data"""
        for key, value in data.items():
            placeholder = f"{{{key}}}"
            template = template.replace(placeholder, str(value))
        return template

    def _extract_json_from_response(self, text: str) -> str:
        """
        Extract JSON from LLM response, handling multiple formats:
        1. Raw JSON (no fences)
        2. Markdown code blocks: ```json...```
        3. Mixed content (text + code blocks)
        4. Balanced brace extraction as last resort
        """
        import re

        stripped = text.strip()

        # Pattern 1: Try to find ```json...``` block
        json_block_match = re.search(r'```json\s*\n(.*?)\n```', stripped, re.DOTALL)
        if json_block_match:
            return json_block_match.group(1).strip()

        # Pattern 2: Try to find any ``` block (language-agnostic)
        code_block_match = re.search(r'```\s*\n(.*?)\n```', stripped, re.DOTALL)
        if code_block_match:
            return code_block_match.group(1).strip()

        # Pattern 3: Simple fence removal (backward compatibility)
        if stripped.startswith("```json"):
            stripped = stripped[7:]
        elif stripped.startswith("```"):
            stripped = stripped[3:]
        if stripped.endswith("```"):
            stripped = stripped[:-3]
        stripped = stripped.strip()

        # Pattern 4: Find first complete JSON object by brace matching
        # Look for { ... } with balanced braces
        brace_start = stripped.find('{')
        if brace_start != -1:
            brace_count = 0
            for i in range(brace_start, len(stripped)):
                if stripped[i] == '{':
                    brace_count += 1
                elif stripped[i] == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        # Found complete JSON object
                        json_candidate = stripped[brace_start:i+1]
                        # Quick validation: try to parse it
                        try:
                            json.loads(json_candidate)
                            return json_candidate
                        except json.JSONDecodeError:
                            pass

        # Return cleaned text if no extraction patterns worked
        return stripped

    def _extract_blend_option_from_text(self, text: str) -> str:
        """
        Extract blend option from raw LLM text when JSON parsing fails.
        Looks for patterns like:
        - "I choose Option: B"
        - "Option B: Sculptural..."
        - "Decision: Option C"
        """
        import re

        # Pattern 1: "I choose Option: X" or "I choose Option X"
        match = re.search(r'I choose Option:?\s*([ABC])', text, re.IGNORECASE)
        if match:
            option_letter = match.group(1).upper()
            return f"Option {option_letter}"

        # Pattern 2: "Option X:" at start of line
        match = re.search(r'^Option\s+([ABC]):', text, re.MULTILINE)
        if match:
            option_letter = match.group(1).upper()
            return f"Option {option_letter}"

        # Pattern 3: "Decision: Option X"
        match = re.search(r'Decision:?\s*Option\s+([ABC])', text, re.IGNORECASE)
        if match:
            option_letter = match.group(1).upper()
            return f"Option {option_letter}"

        # Fallback to default with warning
        print("⚠️  Could not extract blend option from text, defaulting to Option A")
        return "Option A - Default (Extraction Failed)"

    # BLEND OPTION VALIDATION REFERENCE (from WHOOP_CARD_MASTER_RULEBOOK_V4.5.md):
    # Option A (Sculptural 100%): crushing, collapsed, sinking, heavy, still, subdued, smoldering, suffocating
    # Option B (Sculptural + Pattern-Based): luminous, flowing, rhythmic, drifting, serene, expansive, balanced, harmonious, muted
    # Option C (Sculptural + Physics): volatile, fractured, turbulent, suspended, dissociated, brittle, unstable, devastated
    # Art keywords come from behavioral matrix (Recovery × Sleep Score lookup in lookups.py)

    def _validate_blend_option(self, chosen_option: str, art_keywords_str: str) -> str:
        """
        Validate LLM's blend option choice against rulebook art keyword mappings.
        Returns expected option based on keywords, with proper tie handling.
        """
        # Extract just the letter (A, B, or C)
        if not chosen_option or len(chosen_option) < 7:
            return chosen_option

        art_keywords = [kw.strip().lower() for kw in art_keywords_str.split(',')]

        # Rulebook mappings
        OPTION_A_KEYWORDS = {'crushing', 'collapsed', 'sinking', 'heavy', 'still',
                             'subdued', 'smoldering', 'suffocating'}
        OPTION_B_KEYWORDS = {'luminous', 'flowing', 'rhythmic', 'drifting', 'serene',
                             'expansive', 'balanced', 'harmonious', 'muted'}
        OPTION_C_KEYWORDS = {'volatile', 'fractured', 'turbulent', 'suspended',
                             'dissociated', 'brittle', 'unstable', 'devastated'}

        # Count keyword matches
        a_matches = sum(1 for kw in art_keywords if kw in OPTION_A_KEYWORDS)
        b_matches = sum(1 for kw in art_keywords if kw in OPTION_B_KEYWORDS)
        c_matches = sum(1 for kw in art_keywords if kw in OPTION_C_KEYWORDS)

        # Determine expected option with proper tie handling
        max_matches = max(a_matches, b_matches, c_matches)

        # No keywords matched any option - allow LLM judgment
        if max_matches == 0:
            print(f"ℹ️  No keyword matches found - allowing LLM creative choice")
            return chosen_option

        # Count how many options have the max matches (detect ties)
        tied_options = []
        if a_matches == max_matches:
            tied_options.append('A')
        if b_matches == max_matches:
            tied_options.append('B')
        if c_matches == max_matches:
            tied_options.append('C')

        # If there's a tie, allow LLM to break it
        if len(tied_options) > 1:
            print(f"ℹ️  Keyword tie detected: Options {'/'.join(tied_options)} each have {max_matches} match(es)")
            print(f"   Matched keywords: {[kw for kw in art_keywords if kw in (OPTION_A_KEYWORDS | OPTION_B_KEYWORDS | OPTION_C_KEYWORDS)]}")
            # Verify LLM choice is one of the tied options
            chosen_letter = chosen_option.split()[1] if len(chosen_option.split()) > 1 else chosen_option[7]
            if chosen_letter in tied_options:
                print(f"   LLM choice '{chosen_option}' is valid (within tied options)")
                return chosen_option
            else:
                print(f"⚠️  LLM choice '{chosen_option}' is NOT in tied options {tied_options} - using highest priority")
                # Return the tied option with highest priority (A > C > B for safety)
                if 'A' in tied_options:
                    return "Option A"
                elif 'C' in tied_options:
                    return "Option C"
                else:
                    return "Option B"

        # Clear winner - return it
        if b_matches == max_matches:
            return "Option B"
        elif c_matches == max_matches:
            return "Option C"
        else:  # a_matches == max_matches
            return "Option A"

    def call_llm(self, prompt: str) -> str:
        """
        Call LLM API to generate text.
        
        Uses OpenRouter (Minimax) as primary with Google Gemini 2.5 Pro as fallback.
        """
        # Mock mode only when no real API is available (OpenRouter takes priority)
        if (not self.google_api_key or self.google_api_key == 'mock') and not self.use_openrouter:
            print("⚠️  Running LLM in Mock Mode")
            # Check for JSON templates first (before other keywords that might appear in prompts)
            if "MASTER JSON TEMPLATE" in prompt or "OUTPUT INSTRUCTIONS" in prompt:
                return '{\n  "creature_integration": {"blend": "Option B: Sculptural 60-70% + Pattern-Based 30-40%"},\n  "core_concept": "Mock concept",\n  "scene_config": {"atmosphere": "thick"}\n}'
            elif "metadata" in prompt.lower() and "title" in prompt.lower():
                return '{\n  "title": "The Daily Resurgence",\n  "scene_description": "A crystalline environment reflecting inner depths",\n  "date_display": "TEST DATE 2026"\n}'
            elif "interpretation" in prompt.lower() and "maha" in prompt.lower():
                return "The current planetary periods suggest an introspective and solitary focus. This highlights the depth of rest achieved today."
            elif "creature" in prompt.lower() and "archetype" in prompt.lower():
                return "The Architect — A towering being of silent geometry, mapping cosmic patterns onto the terrain below."
            elif "environment" in prompt.lower() and "energy" in prompt.lower():
                return "Bioluminescent — Organic tissue, natural glow, living light sources"
            elif "video" in prompt.lower():
                return "Smooth tracking shot drifting past luminous crystal formations towards the towering Architect."
            return "Mock LLM response."

        # Use OpenRouter client if available
        if self.use_openrouter:
            try:
                return self.llm_client.generate(prompt)
            except (OpenRouterError, LLMProviderError) as e:
                print(f"❌ LLM call failed: {e}")
                raise
        
        # Fallback to direct Google API (legacy behavior)
        try:
            url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-pro:generateContent"
            headers = {
                "x-goog-api-key": self.google_api_key,
                "Content-Type": "application/json"
            }
            payload = {
                "contents": [
                    {
                        "parts": [
                            {
                                "text": prompt
                            }
                        ]
                    }
                ]
            }

            response = requests.post(url, headers=headers, json=payload, timeout=90)
            response.raise_for_status()

            data = response.json()
            candidates = data.get('candidates') or []
            if not candidates:
                # Surface Gemini block/safety signals in logs for quick debugging.
                raise RuntimeError(f"No candidates returned by Gemini. Response keys: {list(data.keys())}")

            parts = candidates[0].get('content', {}).get('parts', [])
            texts = [part.get('text', '') for part in parts if part.get('text')]
            if not texts:
                raise RuntimeError("Gemini candidate had no text parts.")

            return "\n".join(texts).strip()

        except requests.exceptions.HTTPError as e:
            print(f"❌ Gemini API HTTP Error: {e.response.status_code}")
            print(f"   Response: {e.response.text[:500]}")
            raise
        except requests.exceptions.Timeout:
            print("❌ Gemini API Timeout (>90s)")
            raise
        except (KeyError, RuntimeError) as e:
            print(f"❌ Gemini API Response Parsing Error: {e}")
            print(f"   Response: {response.text[:500]}")
            raise
        except Exception as e:
            print(f"❌ Unexpected Gemini API Error: {e}")
            raise

    def save_output(self, filename: str, content: str):
        """Save LLM output to output/ directory"""
        output_path = self.output_dir / filename
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(content)

    def format_natal_chart(self, natal_context: dict) -> str:
        PLANET_ORDER = ['Sun', 'Moon', 'Mars', 'Mercury', 'Jupiter', 'Venus', 'Saturn', 'Rahu', 'Ketu']
        ascendant    = natal_context.get('ascendant', 'Unknown')
        moon_nak     = natal_context.get('moon_nakshatra', 'Unknown')
        planets      = natal_context.get('planets', {})
        lordships    = natal_context.get('house_lordships', {})
        conjunctions = natal_context.get('conjunctions', {})
        aspects      = natal_context.get('planet_aspects', {})
        house_contents = {}
        for p, d in planets.items():
            house_contents.setdefault(d['house'], []).append(p)

        lines = [f"**Ascendant:** {ascendant} | **Moon Nakshatra:** {moon_nak}", "", "**Natal Planets:**"]
        for planet in PLANET_ORDER:
            if planet not in planets: continue
            d = planets[planet]
            ruled = lordships.get(planet, [])
            lords_str = f"lords H{', H'.join(str(h) for h in sorted(ruled))}" if ruled else "karmic node"
            lines.append(f"- {planet}: {d['sign']}, H{d['house']}, {d['dignity']} — {lords_str}")

        if conjunctions:
            lines.extend(["", "**Conjunctions:**"])
            for house, conj_planets in sorted(conjunctions.items(), key=lambda x: int(x[0])):
                sign = planets[conj_planets[0]]['sign']
                lines.append(f"- H{house} ({sign}): {' + '.join(conj_planets)}")

        if aspects:
            lines.extend(["", "**Planetary Aspects (Graha Drishti):**"])
            for planet in PLANET_ORDER:
                if planet not in aspects: continue
                ph = planets.get(planet, {}).get('house', '?')
                aspect_strs = []
                for h in sorted(aspects[planet]):
                    occupants = [p for p in house_contents.get(h, []) if p != planet]
                    aspect_strs.append(f"H{h} ({' + '.join(occupants)})" if occupants else f"H{h}")
                lines.append(f"- {planet} (H{ph}) → {', '.join(aspect_strs)}")

        return "\n".join(lines)

    def _format_planet_detail(self, planet_name: str, planets_detail: dict) -> str:
        """Format planet details for display in prompts."""
        if not planet_name: return "Unknown"
        details = planets_detail.get(planet_name, {})
        return f"{planet_name} in {details.get('sign', 'Unknown')}, House {details.get('house', 'Unknown')}, {details.get('dignity', 'Unknown')} dignity"

    def generate_interpretation(self, daily_data: dict) -> str:
        """Generate 2-sentence dasha interpretation"""
        template = self.load_template('interpretation')
        dasha = daily_data.get('dasha', {})
        natal = daily_data.get('natal_context', {})
        planets_detail = dasha.get('planets_detail', {})

        placeholders = {
            'natal_chart': self.format_natal_chart(natal),
            'maha_planet': self._format_planet_detail(dasha.get('maha'), planets_detail),
            'antar_planet': self._format_planet_detail(dasha.get('antar'), planets_detail),
            'pratyantar_planet': self._format_planet_detail(dasha.get('pratyantar'), planets_detail),
            'sookshma_planet': self._format_planet_detail(dasha.get('sookshma'), planets_detail),
            'prana_planet': self._format_planet_detail(dasha.get('prana'), planets_detail),
        }

        filled_prompt = self.fill_template(template, placeholders)
        self.save_output('last_prompt_interpretation.txt', filled_prompt)
        interpretation = self.call_llm(filled_prompt)
        self.save_output('interpretation.txt', interpretation)
        return interpretation

    def generate_creature(self, daily_data: dict, interpretation: str) -> str:
        """Generate creature archetype (independent of energy zone)"""
        template = self.load_template('creature')
        dasha = daily_data.get('dasha', {})
        natal = daily_data.get('natal_context', {})
        planets_detail = dasha.get('planets_detail', {})

        placeholders = {
            'natal_chart': self.format_natal_chart(natal),
            'interpretation': interpretation,
            'maha_planet': self._format_planet_detail(dasha.get('maha'), planets_detail),
            'antar_planet': self._format_planet_detail(dasha.get('antar'), planets_detail),
            'pratyantar_planet': self._format_planet_detail(dasha.get('pratyantar'), planets_detail),
            'sookshma_planet': self._format_planet_detail(dasha.get('sookshma'), planets_detail),
            'prana_planet': self._format_planet_detail(dasha.get('prana'), planets_detail),
        }

        filled_prompt = self.fill_template(template, placeholders)
        self.save_output('last_prompt_creature.txt', filled_prompt)
        creature_output = self.call_llm(filled_prompt)
        self.save_output('creature.txt', creature_output)
        return creature_output

    def get_environment_options(self, energy_zone: str) -> str:
        """Build environment options list based on energy zone"""
        ENVIRONMENT_OPTIONS = {
            "LOW": [
                "Frozen/Ice — Transparent ice, frost, frozen atmospheric effects",
                "Crystal Caves — Angular crystals, gems, prismatic light refraction",
                "Stone Monuments — Weathered stone, granite, ancient carved formations",
                "Mist/Fog Realms — Volumetric fog, obscured visibility, moisture",
                "Void/Space (Low) — Cosmic dust, minimal light, deep space darkness",
                "Glacial Valley — Polished bedrock, glacial moraine, still cold tarns, smooth U-shaped rock walls, ancient carved silence"
            ],
            "MEDIUM": [
                "Ocean/Underwater — Water, caustics, marine light patterns, aquatic depth",
                "Forest/Jungle — Bark, leaves, roots, organic growth, green filtered light",
                "Wind/Sky Realms — Clouds, air currents, atmospheric layers, open sky",
                "Cave Systems — Limestone, dripping water, stalactites, subterranean chambers",
                "Desert (Calm) — Sand, sandstone, dunes, warm earth tones",
                "Bioluminescent — Organic tissue, natural glow, living light sources"
            ],
            "HIGH": [
                "Volcanic — Volcanic rock, magma, lava flows, intense heat glow",
                "Lightning/Storm — Energy arcs, charged atmosphere, electrical discharge",
                "Plasma/Nebula — glowing plasma, cosmic gas, stellar nursery effects",
                "Crystalline (Active) — Growing crystals, sharp formations, intense light refraction",
                "Desert (Intense) — Cracked earth, heat distortion, scorched terrain",
                "Fire Realms — Fire, smoke, ash, ember glow, combustion"
            ]
        }
        options = ENVIRONMENT_OPTIONS.get(energy_zone, ENVIRONMENT_OPTIONS["MEDIUM"])
        return "\n".join([f"- {opt}" for opt in options])

    def generate_environment(self, daily_data: dict, interpretation: str) -> str:
        """Generate environment type (energy-constrained, independent of creature)"""
        template = self.load_template('environment')
        energy_zone = daily_data.get('energy_zone', 'MEDIUM')
        environment_options = self.get_environment_options(energy_zone)

        placeholders = {
            'energy_zone': energy_zone,
            'interpretation': interpretation,
            'environment_options': environment_options
        }

        filled_prompt = self.fill_template(template, placeholders)
        self.save_output('last_prompt_environment.txt', filled_prompt)
        environment_output = self.call_llm(filled_prompt)
        self.save_output('environment.txt', environment_output)
        return environment_output

    def build_image_json(self, daily_data: dict, interpretation: str, creature: str, environment: str) -> dict:
        """Build complete image generation JSON with blend option selection"""
        template = self.load_template('json_builder')
        creature_name = creature.split('—')[0].strip() if '—' in creature else creature.strip()
        environment_name = environment.split('—')[0].strip() if '—' in environment else environment.strip()
        behavior = daily_data.get('behavior_matrix', {})

        placeholders = {
            'interpretation': interpretation,
            'creature': creature_name,
            'environment': environment_name,
            'depth_level': daily_data.get('depth_level', 'Unknown'),
            'depth_keywords': ', '.join(daily_data.get('depth_keywords', [])),
            'visibility_range': daily_data.get('visibility_range', 'Unknown'),
            'moon_count': str(daily_data.get('moon_count', 0)),
            'energy_zone': daily_data.get('energy_zone', 'Unknown'),
            'art_keywords': ', '.join(behavior.get('art_keywords', [])),
            'body_keywords': ', '.join(behavior.get('body_keywords', [])),
            'one_liner': behavior.get('one_liner', ''),
            # theme_essence: clean behavioral phrase — NO astrology, NO planet names, NO house numbers
            # Derived from body_keywords + one_liner so the image prompt stays clean
            'theme_essence': ', '.join(behavior.get('body_keywords', [])) + ' — ' + behavior.get('one_liner', ''),
            'ascendant': daily_data.get('natal_context', {}).get('ascendant', ''),
            'moon_nakshatra': daily_data.get('natal_context', {}).get('moon_nakshatra', ''),
            # WHOOP metrics — were missing and causing 8 unfilled placeholders in the prompt
            'date': daily_data.get('date', 'Unknown'),
            'date_display': daily_data.get('date_display', 'Unknown'),
            'strain': str(daily_data.get('strain', 'Unknown')),
            'recovery_pct': str(daily_data.get('recovery_pct', 'Unknown')),
            'recovery_zone': daily_data.get('recovery_zone', 'Unknown'),
            'sleep_score_pct': str(daily_data.get('sleep_score_pct', 'Unknown')),
            'sleep_score_zone': daily_data.get('sleep_score_zone', 'Unknown'),
            'sleep_hours': str(daily_data.get('sleep_hours', 'Unknown')),
        }

        filled_prompt = self.fill_template(template, placeholders)
        self.save_output('last_prompt_image_json.txt', filled_prompt)
        json_output = self.call_llm(filled_prompt)

        try:
            json_str = self._extract_json_from_response(json_output)
            image_json = json.loads(json_str)
        except json.JSONDecodeError as e:
            print(f"⚠️  Failed to decode JSON from LLM: {e}")

            # Try to extract blend option from raw text before falling back
            extracted_blend = self._extract_blend_option_from_text(json_output)

            print(f"⚠️  Attempting blend extraction from raw text: {extracted_blend}")

            image_json = {
                "creature_integration": {"blend": extracted_blend},
                "scene_config": {"atmosphere": "thick"},
                "error": "Failed to decode",
                "raw": json_output
            }

            # If we couldn't extract a valid blend, this is a hard error
            if extracted_blend.startswith("Option A - Default"):
                print(f"❌ Could not extract blend option from LLM response. This may indicate a prompt or API issue.")

        with open(self.output_dir / 'image_prompt.json', 'w') as f:
            json.dump(image_json, f, indent=2)

        # Extract blend option safely with validation
        blend_full = image_json.get('creature_integration', {}).get('blend', 'Option A - Default (Fallback)')
        blend_option = blend_full.split(':')[0].strip() if ':' in blend_full else blend_full.split('-')[0].strip()

        # Validate against art keywords
        expected_blend = self._validate_blend_option(
            blend_option,
            placeholders.get('art_keywords', '')
        )
        if expected_blend != blend_option:
            print(f"⚠️  Blend option mismatch: LLM chose {blend_option}, but art keywords suggest {expected_blend}")
            print(f"   Art keywords: {placeholders.get('art_keywords', '')}")
            print(f"   Proceeding with LLM choice (allowing creative flexibility)")

        with open(self.output_dir / 'blend_option.txt', 'w') as f:
            f.write(blend_option)

        return image_json

    def extract_metadata(self, daily_data: dict, date_display: str, environment: str = "", image_json: dict = None) -> dict:
        """Extract card metadata (title, scene description) from behavioral state only"""
        template = self.load_template('metadata_builder')
        behavior = daily_data.get('behavior_matrix', {})
        body_keywords = ', '.join(behavior.get('body_keywords', []))
        art_keywords = ', '.join(behavior.get('art_keywords', []))
        one_liner = behavior.get('one_liner', '')
        env_name = environment.split('—')[0].strip() if '—' in environment else environment.strip()

        placeholders = {
            'environment': env_name,
            'depth_level': str(daily_data.get('depth_level', 'Unknown')),
            'art_keywords': art_keywords,
            'body_keywords': body_keywords,
            'one_liner': one_liner,
            'date_display': date_display
        }

        filled_prompt = self.fill_template(template, placeholders)
        self.save_output('last_prompt_metadata.txt', filled_prompt)
        metadata = None

        for attempt in range(2):
            metadata_output = self.call_llm(filled_prompt)
            try:
                json_str = self._extract_json_from_response(metadata_output)
                parsed = json.loads(json_str)
                metadata = {
                    "title": parsed.get("title", "").strip() or "Untitled State",
                    "scene_description": parsed.get("scene_description", "").strip() or "Scene description unavailable.",
                    "date_display": parsed.get("date_display", "").strip() or date_display,
                }
                break
            except json.JSONDecodeError as e:
                if attempt == 0:
                    print(f"⚠ Metadata JSON decode failed on attempt 1: {e}. Retrying once...")
                else:
                    print(f"⚠ Metadata JSON decode failed on attempt 2: {e}. Using fallback metadata.")

        if metadata is None:
            image_json = image_json or {}
            core_concept = str(image_json.get('core_concept', '')).strip()
            short_scene = (core_concept[:157] + '...') if len(core_concept) > 160 else core_concept
            metadata = {
                "title": "Daily State Card",
                "scene_description": short_scene or "Scene description unavailable.",
                "date_display": date_display,
            }

        with open(self.output_dir / 'card_metadata.json', 'w') as f:
            json.dump(metadata, f, indent=2)

        return metadata

    def build_video_prompt(self, daily_data: dict, environment: str, blend_option: str) -> str:
        """Build video animation prompt — scene continuation, no creature reference"""
        template = self.load_template('video')
        environment_name = environment.split('—')[0].strip() if '—' in environment else environment.strip()
        behavior = daily_data.get('behavior_matrix', {})

        placeholders = {
            'environment': environment_name,
            'depth_level': daily_data.get('depth_level', 'Unknown'),
            'energy_zone': daily_data.get('energy_zone', 'Unknown'),
            'art_keywords': ', '.join(behavior.get('art_keywords', [])),
            'one_liner': behavior.get('one_liner', ''),
            'moon_count': str(daily_data.get('moon_count', 0)),
            'blend_option': blend_option
        }

        filled_prompt = self.fill_template(template, placeholders)
        self.save_output('last_prompt_video.txt', filled_prompt)
        video_prompt = self.call_llm(filled_prompt)
        self.save_output('video_prompt.txt', video_prompt)

        return video_prompt


def main():
    parser = argparse.ArgumentParser(description="WHOOP Pipeline LLM Orchestrator")
    parser.add_argument('--step', choices=['interpretation', 'creature', 'environment', 'json', 'metadata', 'video', 'all'], help="Which prompt step to run", default='all')
    parser.add_argument('--data', help="Path to daily_data.json relative to project root")
    args = parser.parse_args()

    api_key = os.getenv('GOOGLE_API_KEY_PRIMARY', 'mock')
    or_key = os.getenv('OPENROUTER_API_KEY')
    print(f"🔍 DEBUG: GOOGLE_API_KEY_PRIMARY={'set' if api_key and api_key != 'mock' else api_key}")
    print(f"🔍 DEBUG: OPENROUTER_API_KEY={'set' if or_key else 'NOT SET'}")
    orchestrator = PromptOrchestrator(
        llm_api_key=api_key,
        openrouter_api_key=or_key  # Explicitly pass to ensure subprocess can find it
    )

    run_date = os.getenv('PIPELINE_DATE')
    output_root = get_output_root()
    if run_date:
        data_path = output_root / run_date / 'daily_data.json'
    else:
        data_path = output_root / 'daily_data.json'
    
    if not data_path.exists():
        # Fallback to root output dir
        data_path = output_root / 'daily_data.json'

    print(f"▶ Loading data from {data_path}...")
    with open(data_path, 'r', encoding='utf-8') as f:
        daily_data = json.load(f)

    if args.step == 'all':
        print("▶ Generating Interpretation...")
        interpretation = orchestrator.generate_interpretation(daily_data)
        print("▶ Generating Creature...")
        creature = orchestrator.generate_creature(daily_data, interpretation)
        print("▶ Generating Environment...")
        environment = orchestrator.generate_environment(daily_data, interpretation)
        print("▶ Building Image JSON...")
        image_json = orchestrator.build_image_json(daily_data, interpretation, creature, environment)
        print("▶ Extracting Metadata...")
        metadata = orchestrator.extract_metadata(daily_data, daily_data.get('date_display', 'Today'), environment, image_json)

        try:
            with open(orchestrator.output_dir / 'blend_option.txt', 'r') as f:
                blend_option = f.read().strip()
        except FileNotFoundError:
            blend_option = "Option A"
            
        print("▶ Building Video Prompt...")
        video_prompt = orchestrator.build_video_prompt(daily_data, environment, blend_option)

        print("✅ All prompts completed successfully.")
    else:
        if args.step == 'video':
            try:
                with open(orchestrator.output_dir / 'environment.txt', 'r') as f:
                    environment = f.read().strip()
                with open(orchestrator.output_dir / 'blend_option.txt', 'r') as f:
                    blend_option = f.read().strip()
                
                print("▶ Building Video Prompt...")
                video_prompt = orchestrator.build_video_prompt(daily_data, environment, blend_option)
                print("✅ Video prompt completed successfully.")
            except FileNotFoundError as e:
                print(f"❌ Error: Missing required files for video step: {e}")
        else:
            print(f"Running individual step: {args.step} (stubbed, run 'all' for full flow)")

if __name__ == '__main__':
    main()
