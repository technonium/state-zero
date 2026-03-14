import os
import sys
import json
import argparse
import requests
from pathlib import Path
from dotenv import load_dotenv
from database_manager import CardDatabase
from creature_utils import format_creature_output, normalize_creature_name, split_creature_output
from environment_utils import (
    extract_valid_environment_name,
    format_environment_output,
    select_least_recent_candidate,
    split_environment_output,
    normalize_environment_name,
)
from title_utils import clean_title, normalize_title
from utils import get_project_root, get_output_root

load_dotenv(dotenv_path=get_project_root() / '.env', override=True)

# Import OpenRouter client for LLM calls
from openrouter_client import create_llm_client, OpenRouterError, LLMProviderError


ENVIRONMENT_OPTIONS = {
    "LOW": [
        "Frozen/Ice — Transparent ice, frost, frozen atmospheric effects",
        "Crystal Caves — Angular crystals, gems, prismatic light refraction",
        "Stone Monuments — Weathered stone, granite, ancient carved formations",
        "Mist/Fog Realms — Volumetric fog, obscured visibility, moisture",
        "Void/Space (Low) — Cosmic dust, minimal light, deep space darkness",
        "Glacial Valley — Polished bedrock, glacial moraine, still cold tarns, smooth U-shaped rock walls, ancient carved silence",
    ],
    "MEDIUM": [
        "Ocean/Underwater — Water, caustics, marine light patterns, aquatic depth",
        "Forest/Jungle — Bark, leaves, roots, organic growth, green filtered light",
        "Wind/Sky Realms — Clouds, air currents, atmospheric layers, open sky",
        "Cave Systems — Limestone, dripping water, stalactites, subterranean chambers",
        "Desert (Calm) — Sand, sandstone, dunes, warm earth tones",
        "Bioluminescent — Organic tissue, natural glow, living light sources",
    ],
    "HIGH": [
        "Volcanic — Volcanic rock, magma, lava flows, intense heat glow",
        "Lightning/Storm — Energy arcs, charged atmosphere, electrical discharge",
        "Plasma/Nebula — glowing plasma, cosmic gas, stellar nursery effects",
        "Crystalline (Active) — Growing crystals, sharp formations, intense light refraction",
        "Desert (Intense) — Cracked earth, heat distortion, scorched terrain",
        "Fire Realms — Fire, smoke, ash, ember glow, combustion",
    ],
}

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

    def save_json_output(self, filename: str, payload: dict):
        output_path = self.output_dir / filename
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)

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
        run_date = daily_data.get('date') or os.getenv('PIPELINE_DATE')
        recent_creature_context = self._resolve_recent_creatures(run_date)

        placeholders = {
            'natal_chart': self.format_natal_chart(natal),
            'interpretation': interpretation,
            'maha_planet': self._format_planet_detail(dasha.get('maha'), planets_detail),
            'antar_planet': self._format_planet_detail(dasha.get('antar'), planets_detail),
            'pratyantar_planet': self._format_planet_detail(dasha.get('pratyantar'), planets_detail),
            'sookshma_planet': self._format_planet_detail(dasha.get('sookshma'), planets_detail),
            'prana_planet': self._format_planet_detail(dasha.get('prana'), planets_detail),
            'recent_creatures': recent_creature_context['recent_creatures_prompt'],
        }

        filled_prompt = self.fill_template(template, placeholders)
        self.save_output('last_prompt_creature.txt', filled_prompt)
        retry_prompt_path = self.output_dir / 'last_prompt_creature_retry.txt'
        resolved_creature_path = self.output_dir / 'creature_selected.txt'
        first_raw_output = self.call_llm(filled_prompt)
        first_name, first_reason = split_creature_output(first_raw_output)
        first_norm = normalize_creature_name(first_name)
        first_canonical_output = format_creature_output(first_name, first_reason) if first_name else ""

        banned_recent_names = recent_creature_context['banned_recent_names']
        retry_triggered = False
        retry_raw_output = ""
        retry_name = ""
        retry_reason = ""
        retry_canonical_output = ""
        final_output = first_canonical_output or first_raw_output
        final_name = first_name
        final_reason = first_reason
        retained_parseable_source = 'first'
        should_retry = not first_norm or (
            recent_creature_context['db_lookup_status'] == 'ok'
            and first_norm in banned_recent_names
        )

        if should_retry:
            retry_triggered = True
            retry_prompt = self._build_creature_retry_prompt(
                filled_prompt,
                first_raw_output,
                first_name,
                recent_creature_context['recent_creature_names'],
                is_repeat=bool(first_norm and first_norm in banned_recent_names),
            )
            self.save_output('last_prompt_creature_retry.txt', retry_prompt)
            retry_raw_output = self.call_llm(retry_prompt)
            retry_name, retry_reason = split_creature_output(retry_raw_output)
            retry_norm = normalize_creature_name(retry_name)
            retry_canonical_output = format_creature_output(retry_name, retry_reason) if retry_name else ""

            if retry_norm and retry_norm not in banned_recent_names:
                final_output = retry_canonical_output
                final_name = retry_name
                final_reason = retry_reason
                retained_parseable_source = 'retry'
                final_selection_source = 'corrective_retry_valid'
            elif retry_name:
                final_output = retry_canonical_output
                final_name = retry_name
                final_reason = retry_reason
                retained_parseable_source = 'retry'
                final_selection_source = 'repeat_after_retry_warning'
            elif first_name:
                final_output = first_canonical_output
                final_name = first_name
                final_reason = first_reason
                retained_parseable_source = 'first'
                final_selection_source = 'repeat_after_retry_warning'
            else:
                raise RuntimeError("Creature selection failed: both attempts were unparseable.")
        else:
            if retry_prompt_path.exists():
                retry_prompt_path.unlink()
            if recent_creature_context['db_lookup_status'] != 'ok':
                final_selection_source = 'history_lookup_failed_no_guard'
            else:
                final_selection_source = 'llm_valid'

        self.save_output('creature.txt', final_output)
        self.save_output('creature_selected.txt', final_output)
        self.save_json_output(
            'creature_selection_debug.json',
            {
                'run_date': run_date,
                'db_lookup_status': recent_creature_context['db_lookup_status'],
                'db_lookup_error': recent_creature_context['db_lookup_error'],
                'raw_recent_history': recent_creature_context['raw_recent_history'],
                'normalized_banned_names': recent_creature_context['normalized_banned_names'],
                'recent_creatures_prompt_names': recent_creature_context['recent_creature_names'],
                'first_raw_output': first_raw_output,
                'first_parsed_name': first_name,
                'first_parsed_reason': first_reason,
                'first_canonical_output': first_canonical_output,
                'retry_triggered': retry_triggered,
                'retry_raw_output': retry_raw_output,
                'retry_parsed_name': retry_name,
                'retry_parsed_reason': retry_reason,
                'retry_canonical_output': retry_canonical_output,
                'final_name': final_name,
                'final_reason': final_reason,
                'final_output': final_output,
                'final_selection_source': final_selection_source,
                'retained_parseable_source': retained_parseable_source,
            },
        )
        return final_output

    def _resolve_recent_creatures(self, run_date: str | None) -> dict:
        raw_recent_history = []
        db_lookup_status = 'ok'
        db_lookup_error = None

        if run_date:
            try:
                raw_recent_history = CardDatabase().get_recent_creature_names(run_date, limit=10)
            except Exception as e:
                db_lookup_status = 'failed'
                db_lookup_error = str(e)
                print(f"⚠️  Creature history lookup failed: {e}")
        else:
            db_lookup_status = 'missing_run_date'
            db_lookup_error = 'Missing run date; creature recency guard disabled.'

        recent_creature_names = []
        normalized_banned_names = []
        banned_recent_names = set()

        if db_lookup_status == 'ok':
            for name in raw_recent_history:
                normalized_name = normalize_creature_name(name)
                if not normalized_name or normalized_name in banned_recent_names:
                    continue
                banned_recent_names.add(normalized_name)
                normalized_banned_names.append(normalized_name)
                recent_creature_names.append(name)

        recent_creatures_prompt = "\n".join(f"- {name}" for name in recent_creature_names)
        if not recent_creatures_prompt:
            recent_creatures_prompt = "None — no restriction."

        return {
            'db_lookup_status': db_lookup_status,
            'db_lookup_error': db_lookup_error,
            'raw_recent_history': raw_recent_history,
            'normalized_banned_names': normalized_banned_names,
            'recent_creature_names': recent_creature_names,
            'recent_creatures_prompt': recent_creatures_prompt,
            'banned_recent_names': banned_recent_names,
        }

    def _build_creature_retry_prompt(
        self,
        filled_prompt: str,
        previous_output: str,
        previous_name: str,
        recent_creature_names: list[str],
        *,
        is_repeat: bool,
    ) -> str:
        if is_repeat and previous_name:
            retry_reason = (
                f'Your previous choice "{previous_name}" is invalid because it appears in the recent-creatures exclusion list.'
            )
        else:
            retry_reason = 'Your previous response could not be parsed into a valid creature name.'

        recent_names_text = ", ".join(recent_creature_names) if recent_creature_names else "None"
        return (
            f"{filled_prompt}\n\n"
            "---\n\n"
            "## Correction\n\n"
            f"{retry_reason}\n"
            f"Recent banned creatures: {recent_names_text}\n"
            "Return one different creature in the exact required format.\n"
            "Do not repeat any banned creature.\n"
            "Do not explain the correction.\n\n"
            "Previous response:\n"
            f"{previous_output.strip()}"
        )

    def get_environment_entries(self, energy_zone: str) -> list[str]:
        return list(ENVIRONMENT_OPTIONS.get(energy_zone, ENVIRONMENT_OPTIONS["MEDIUM"]))

    def get_environment_options(self, energy_zone: str, options: list[str] | None = None) -> str:
        """Build environment options list based on energy zone"""
        selected_options = list(options) if options is not None else self.get_environment_entries(energy_zone)
        return "\n".join([f"- {opt}" for opt in selected_options])

    def _resolve_environment_candidates(self, energy_zone: str, run_date: str | None) -> dict:
        full_options = self.get_environment_entries(energy_zone)
        recent_names = []
        db_lookup_status = 'ok'
        db_lookup_error = None
        soft_fallback = False
        candidate_source = 'filtered_candidates'

        if run_date:
            try:
                recent_names = CardDatabase().get_recent_environment_names(energy_zone, run_date, limit=5)
            except Exception as e:
                db_lookup_status = 'failed'
                db_lookup_error = str(e)
                candidate_source = 'history_lookup_failed_full_catalog'
                print(f"⚠️  Environment history lookup failed: {e}")
        else:
            db_lookup_status = 'missing_run_date'
            candidate_source = 'history_lookup_failed_full_catalog'
            db_lookup_error = 'Missing run date; using full environment list.'

        if db_lookup_status == 'ok':
            recent_lookup = {normalize_environment_name(name) for name in recent_names if normalize_environment_name(name)}
            filtered_options = []
            excluded_names = []

            for option in full_options:
                option_name, _option_reason = split_environment_output(option)
                if normalize_environment_name(option_name) in recent_lookup:
                    excluded_names.append(option_name)
                else:
                    filtered_options.append(option)

            if filtered_options:
                return {
                    'full_options': full_options,
                    'candidate_options': filtered_options,
                    'recent_names': recent_names,
                    'excluded_names': excluded_names,
                    'db_lookup_status': db_lookup_status,
                    'db_lookup_error': db_lookup_error,
                    'soft_fallback': soft_fallback,
                    'candidate_source': candidate_source,
                }

            soft_fallback = True
            candidate_source = 'soft_fallback_full_catalog'
            print(f"⚠️  Environment candidate list exhausted for {energy_zone}; using full zone catalog.")
            excluded_names = [split_environment_output(option)[0] for option in full_options]
        else:
            excluded_names = []

        return {
            'full_options': full_options,
            'candidate_options': full_options,
            'recent_names': recent_names,
            'excluded_names': excluded_names,
            'db_lookup_status': db_lookup_status,
            'db_lookup_error': db_lookup_error,
            'soft_fallback': soft_fallback,
            'candidate_source': candidate_source,
        }

    def generate_environment(self, daily_data: dict, interpretation: str) -> str:
        """Generate environment type (energy-constrained, independent of creature)"""
        template = self.load_template('environment')
        energy_zone = daily_data.get('energy_zone', 'MEDIUM')
        run_date = daily_data.get('date') or os.getenv('PIPELINE_DATE')
        candidate_context = self._resolve_environment_candidates(energy_zone, run_date)
        candidate_options = candidate_context['candidate_options']
        candidate_names = [split_environment_output(option)[0] for option in candidate_options]
        environment_options = self.get_environment_options(energy_zone, candidate_options)

        placeholders = {
            'energy_zone': energy_zone,
            'interpretation': interpretation,
            'environment_options': environment_options
        }

        filled_prompt = self.fill_template(template, placeholders)
        self.save_output('last_prompt_environment.txt', filled_prompt)
        raw_environment_output = self.call_llm(filled_prompt)
        self.save_output('environment.txt', raw_environment_output)

        parsed_name, parsed_reason = split_environment_output(raw_environment_output)
        allowed_lookup = {
            normalize_environment_name(name): name
            for name in candidate_names
            if normalize_environment_name(name)
        }
        parsed_norm = normalize_environment_name(parsed_name)
        final_selection_source = 'llm_valid'
        repair_status = 'not_needed'

        if parsed_norm in allowed_lookup:
            final_name = allowed_lookup[parsed_norm]
            final_reason = parsed_reason
        else:
            repaired_name, repair_status = extract_valid_environment_name(raw_environment_output, candidate_names)
            if repaired_name:
                final_name = repaired_name
                final_reason = parsed_reason
                final_selection_source = 'repaired'
            else:
                final_name = select_least_recent_candidate(candidate_names, candidate_context['recent_names'])
                final_reason = 'Selected deterministically after invalid environment output.'
                final_selection_source = 'deterministic_fallback'

        final_environment_output = format_environment_output(final_name, final_reason)
        self.save_output('environment_selected.txt', final_environment_output)
        self.save_json_output(
            'environment_selection_debug.json',
            {
                'run_date': run_date,
                'energy_zone': energy_zone,
                'db_lookup_status': candidate_context['db_lookup_status'],
                'db_lookup_error': candidate_context['db_lookup_error'],
                'recent_same_zone_history': candidate_context['recent_names'],
                'excluded_names': candidate_context['excluded_names'],
                'candidate_names': candidate_names,
                'candidate_source': candidate_context['candidate_source'],
                'soft_fallback': candidate_context['soft_fallback'],
                'raw_output': raw_environment_output,
                'parsed_name': parsed_name,
                'parsed_reason': parsed_reason,
                'repair_status': repair_status,
                'final_selection_source': final_selection_source,
                'final_name': final_name,
                'final_reason': final_reason,
                'final_output': final_environment_output,
            },
        )
        return final_environment_output

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
        run_date = daily_data.get('date') or os.getenv('PIPELINE_DATE')
        recent_title_context = self._resolve_recent_titles(run_date)

        placeholders = {
            'environment': env_name,
            'depth_level': str(daily_data.get('depth_level', 'Unknown')),
            'art_keywords': art_keywords,
            'body_keywords': body_keywords,
            'one_liner': one_liner,
            'date_display': date_display,
            'recent_titles': recent_title_context['recent_titles_prompt'],
        }

        filled_prompt = self.fill_template(template, placeholders)
        self.save_output('last_prompt_metadata.txt', filled_prompt)
        retry_prompt_path = self.output_dir / 'last_prompt_metadata_retry.txt'
        first_metadata, first_raw_output, first_parse_status = self._request_metadata_payload(
            filled_prompt,
            date_display,
            image_json=image_json,
        )
        first_title = first_metadata.get("title", "")
        first_title_normalized = normalize_title(first_title)

        retry_triggered = False
        retry_raw_output = ""
        retry_parse_status = "not_attempted"
        retry_parsed_title = ""
        final_metadata = first_metadata
        final_selection_source = "json_fallback" if first_parse_status == "json_fallback" else "llm_valid"

        should_retry = (
            first_parse_status != "json_fallback"
            and recent_title_context['db_lookup_status'] == 'ok'
            and first_title_normalized in recent_title_context['banned_recent_titles']
        )

        if should_retry:
            retry_triggered = True
            retry_prompt = self._build_metadata_retry_prompt(
                filled_prompt,
                previous_output=first_raw_output,
                previous_title=first_title,
                recent_titles=recent_title_context['recent_titles'],
            )
            self.save_output('last_prompt_metadata_retry.txt', retry_prompt)
            retry_metadata, retry_raw_output, retry_parse_status = self._request_metadata_payload(
                retry_prompt,
                date_display,
                image_json=image_json,
            )
            retry_parsed_title = retry_metadata.get("title", "")
            retry_title_normalized = normalize_title(retry_parsed_title)

            if retry_parse_status == "json_fallback":
                final_metadata = retry_metadata
                final_selection_source = "json_fallback"
            elif retry_title_normalized not in recent_title_context['banned_recent_titles']:
                final_metadata = retry_metadata
                final_selection_source = "corrective_retry_valid"
            else:
                final_metadata = retry_metadata
                final_selection_source = "repeat_after_retry_warning"
        else:
            if retry_prompt_path.exists():
                retry_prompt_path.unlink()
            if first_parse_status == "json_fallback":
                final_selection_source = "json_fallback"
            elif recent_title_context['db_lookup_status'] != 'ok':
                final_selection_source = "history_lookup_failed_no_guard"
            else:
                final_selection_source = "llm_valid"

        self.save_json_output(
            'metadata_selection_debug.json',
            {
                'run_date': run_date,
                'db_lookup_status': recent_title_context['db_lookup_status'],
                'db_lookup_error': recent_title_context['db_lookup_error'],
                'raw_recent_history': recent_title_context['raw_recent_history'],
                'recent_titles_used': recent_title_context['recent_titles'],
                'normalized_banned_titles': recent_title_context['normalized_banned_titles'],
                'first_raw_output': first_raw_output,
                'first_parse_status': first_parse_status,
                'first_parsed_title': first_title,
                'retry_triggered': retry_triggered,
                'retry_raw_output': retry_raw_output,
                'retry_parse_status': retry_parse_status,
                'retry_parsed_title': retry_parsed_title,
                'final_title': final_metadata.get('title', ''),
                'final_scene_description': final_metadata.get('scene_description', ''),
                'final_selection_source': final_selection_source,
            },
        )
        with open(self.output_dir / 'card_metadata.json', 'w', encoding='utf-8') as f:
            json.dump(final_metadata, f, indent=2)

        return final_metadata

    def _request_metadata_payload(self, prompt: str, date_display: str, *, image_json: dict | None = None):
        metadata = None
        last_raw_output = ""

        for attempt in range(2):
            metadata_output = self.call_llm(prompt)
            last_raw_output = metadata_output
            try:
                json_str = self._extract_json_from_response(metadata_output)
                parsed = json.loads(json_str)
                metadata = {
                    "title": clean_title(parsed.get("title", "")) or "Untitled State",
                    "scene_description": parsed.get("scene_description", "").strip() or "Scene description unavailable.",
                    "date_display": parsed.get("date_display", "").strip() or date_display,
                }
                return metadata, last_raw_output, "json_valid"
            except json.JSONDecodeError as e:
                if attempt == 0:
                    print(f"⚠ Metadata JSON decode failed on attempt 1: {e}. Retrying once...")
                else:
                    print(f"⚠ Metadata JSON decode failed on attempt 2: {e}. Using fallback metadata.")

        image_json = image_json or {}
        core_concept = str(image_json.get('core_concept', '')).strip()
        short_scene = (core_concept[:157] + '...') if len(core_concept) > 160 else core_concept
        metadata = {
            "title": "Daily State Card",
            "scene_description": short_scene or "Scene description unavailable.",
            "date_display": date_display,
        }
        return metadata, last_raw_output, "json_fallback"

    def _resolve_recent_titles(self, run_date: str | None) -> dict:
        raw_recent_history = []
        db_lookup_status = 'ok'
        db_lookup_error = None

        if run_date:
            try:
                raw_recent_history = CardDatabase().get_recent_titles(run_date, limit=10)
            except Exception as e:
                db_lookup_status = 'failed'
                db_lookup_error = str(e)
                print(f"⚠️  Title history lookup failed: {e}")
        else:
            db_lookup_status = 'missing_run_date'
            db_lookup_error = 'Missing run date; title recency guard disabled.'

        recent_titles = []
        normalized_banned_titles = []
        banned_recent_titles = set()

        if db_lookup_status == 'ok':
            for title in raw_recent_history:
                cleaned_title = clean_title(title)
                normalized_title = normalize_title(cleaned_title)
                if not normalized_title or normalized_title in banned_recent_titles:
                    continue
                banned_recent_titles.add(normalized_title)
                normalized_banned_titles.append(normalized_title)
                recent_titles.append(cleaned_title)

        recent_titles_prompt = "\n".join(f"- {title}" for title in recent_titles)
        if not recent_titles_prompt:
            recent_titles_prompt = "None — no restriction."

        return {
            'db_lookup_status': db_lookup_status,
            'db_lookup_error': db_lookup_error,
            'raw_recent_history': raw_recent_history,
            'recent_titles': recent_titles,
            'recent_titles_prompt': recent_titles_prompt,
            'normalized_banned_titles': normalized_banned_titles,
            'banned_recent_titles': banned_recent_titles,
        }

    def _build_metadata_retry_prompt(
        self,
        filled_prompt: str,
        *,
        previous_output: str,
        previous_title: str,
        recent_titles: list[str],
    ) -> str:
        recent_titles_text = ", ".join(recent_titles) if recent_titles else "None"
        return (
            f"{filled_prompt}\n\n"
            "---\n\n"
            "## Correction\n\n"
            f'Your previous title "{previous_title}" is invalid because it appears in the recent-title exclusion list.\n'
            f"Recent banned titles: {recent_titles_text}\n"
            "Return a new metadata JSON object with a different title.\n"
            "The scene description may change if needed, but the title must not repeat any banned title.\n"
            "Do not explain the correction.\n\n"
            "Previous response:\n"
            f"{previous_output}\n"
        )

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
                environment_path = orchestrator.output_dir / 'environment_selected.txt'
                if not environment_path.exists():
                    environment_path = orchestrator.output_dir / 'environment.txt'
                with open(environment_path, 'r') as f:
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
