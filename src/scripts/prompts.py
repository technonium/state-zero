import os
import sys
import json
import argparse
import re
from pathlib import Path
from dotenv import load_dotenv
from database_manager import CardDatabase
from creature_utils import (
    format_creature_output,
    normalize_creature_name,
    parse_creature_payload,
    split_creature_output,
)
from environment_utils import (
    extract_valid_environment_name,
    format_environment_output,
    select_least_recent_candidate,
    split_environment_output,
    normalize_environment_name,
)
from title_utils import (
    assess_title_candidate,
    build_structural_title_keys,
    clean_title,
    normalize_title,
    structural_title_key,
)
from utils import env_bool, get_project_root, get_output_root

load_dotenv(dotenv_path=get_project_root() / '.env', override=True)

# Import OpenRouter client for LLM calls
from openrouter_client import (
    GEMINI_MODEL,
    GEMINI_TIMEOUT_SECONDS,
    LLMProviderError,
    OpenRouterError,
    call_google_gemini_generate_content,
    create_llm_client,
)


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

PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z0-9_]+)\}")
SOFT_EMPTY_PLACEHOLDERS = {
    "art_keywords",
    "body_keywords",
    "depth_keywords",
}
SOFT_NONE_PLACEHOLDERS = {
    "recent_creatures",
    "recent_titles",
    "recent_structural_keys",
}
TEMPLATE_CRITICAL_PLACEHOLDERS = {
    "environment": {"environment_options"},
    "video": {"environment", "blend_option"},
}
GENERIC_FRAGMENT_SINGLETONS = {
    "arm",
    "beak",
    "claw",
    "crest",
    "eye",
    "fang",
    "fin",
    "horn",
    "leg",
    "muzzle",
    "paw",
    "snout",
    "tail",
    "tentacle",
    "tooth",
    "wing",
}
FRAGMENT_ENVIRONMENTAL_TERMS = {
    "arch",
    "bank",
    "bend",
    "bridge",
    "canyon",
    "crater",
    "dune",
    "gate",
    "horizon",
    "mesa",
    "pass",
    "path",
    "portal",
    "ridgeway",
    "riverbed",
    "shore",
    "shoreline",
    "threshold",
    "valley",
}
FRAGMENT_SYMBOLIC_TERMS = {
    "aura",
    "echo",
    "halo",
    "ink",
    "mark",
    "memory",
    "omen",
    "rebirth",
    "scar",
    "shadow",
    "signal",
    "spirit",
    "stain",
    "symbol",
    "trace",
}
FRAGMENT_CELESTIAL_MYTHIC_TERMS = {
    "ascendant",
    "celestial",
    "cosmic",
    "divine",
    "karmic",
    "mythic",
    "sacred",
    "sovereign",
    "stellar",
}
FRAGMENT_ENVIRONMENTAL_CONTAMINATION_TERMS = {
    "ash",
    "ember",
    "frost",
    "glacial",
    "lava",
    "lunar",
    "mist",
    "molten",
    "nebular",
    "rain",
    "sand",
    "smoke",
    "solar",
    "storm",
    "volcanic",
}
FRAGMENT_POINTABLE_FEATURE_TERMS = {
    "barb",
    "bill",
    "carapace",
    "casque",
    "curl",
    "curve",
    "edge",
    "feather",
    "frill",
    "gill",
    "hook",
    "hood",
    "jaw",
    "mandible",
    "mane",
    "plate",
    "plume",
    "ridge",
    "ruff",
    "sail",
    "scale",
    "scute",
    "seam",
    "segment",
    "socket",
    "spike",
    "spine",
    "spur",
    "sucker",
    "tip",
    "tine",
    "tusk",
    "tuft",
    "ventral",
}
FRAGMENT_GENERAL_BODY_REGION_TERMS = {
    "architecture",
    "back",
    "body",
    "face",
    "field",
    "flank",
    "form",
    "frame",
    "head",
    "mantle",
    "neck",
    "outline",
    "overhead",
    "profile",
    "shape",
    "silhouette",
    "spread",
    "structure",
    "torso",
    "trunk",
}
FRAGMENT_SCENE_WIDE_TERMS = {
    "array",
    "canopy",
    "curtain",
    "fan",
    "field",
    "mantle",
    "sheet",
    "spread",
    "train",
    "veil",
}
WHY_UNIQUE_FORBIDDEN_TERMS = {
    "astrolog",
    "ascension",
    "chart",
    "dasha",
    "emotion",
    "firebird",
    "house",
    "intuit",
    "karm",
    "myth",
    "natal",
    "planet",
    "prana",
    "rebirth",
    "realm",
    "scene",
    "symbol",
    "sookshma",
    "terrain",
    "threshold",
    "landscape",
    "environment",
}
WHY_UNIQUE_STRUCTURAL_TERMS = FRAGMENT_POINTABLE_FEATURE_TERMS | {
    "curve",
    "distinctive",
    "edge",
    "external",
    "feature",
    "form",
    "geometry",
    "hook",
    "local",
    "localized",
    "outline",
    "pointable",
    "protrusion",
    "recognizable",
    "shape",
    "specific",
    "structur",
    "tip",
    "visible",
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
        self.template_fill_warnings = {}
        
        # Ensure directories exist
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.templates_dir.mkdir(parents=True, exist_ok=True)
        self.latest_creature_fragment = ""
        self.latest_creature_fragment_why_unique = ""

    def load_template(self, template_name: str) -> str:
        """Load prompt template from src/prompts/"""
        template_path = self.templates_dir / f"{template_name}.md"
        if not template_path.exists():
            raise FileNotFoundError(f"Required template not found: {template_path}")
            
        with open(template_path, 'r', encoding='utf-8') as f:
            return f.read()

    def _extract_template_placeholders(self, template: str) -> set[str]:
        return set(PLACEHOLDER_RE.findall(template))

    def fill_template_strict(self, template: str, data: dict) -> str:
        """Fill template placeholders with strict validation."""
        required_placeholders = self._extract_template_placeholders(template)
        missing_placeholders = sorted(
            placeholder for placeholder in required_placeholders if placeholder not in data
        )
        if missing_placeholders:
            raise ValueError(
                f"Missing required template placeholders: {', '.join(missing_placeholders)}"
            )

        for key, value in data.items():
            placeholder = f"{{{key}}}"
            template = template.replace(placeholder, str(value))

        unresolved_placeholders = sorted(self._extract_template_placeholders(template))
        if unresolved_placeholders:
            raise ValueError(
                f"Unresolved template placeholders after fill: {', '.join(unresolved_placeholders)}"
            )
        return template

    def _default_placeholder_value(self, placeholder: str, *, unresolved: bool = False) -> str:
        if placeholder in SOFT_EMPTY_PLACEHOLDERS:
            return ""
        if placeholder in SOFT_NONE_PLACEHOLDERS:
            return "None — no restriction."
        if unresolved:
            return ""
        return "Unknown"

    def _record_template_fill_warnings(self, template_name: str, warnings: list[dict]):
        self.template_fill_warnings[template_name] = warnings
        self.save_json_output('template_fill_warnings.json', self.template_fill_warnings)
        if warnings:
            warning_summary = ", ".join(
                f"{item['type']}:{item['placeholder']}" for item in warnings
            )
            print(f"⚠️  Template fill warnings for {template_name}: {warning_summary}")

    def fill_template(self, template: str, data: dict, *, template_name: str = "unknown") -> str:
        """Fill template placeholders with best-effort validation for live runs."""
        working_data = dict(data)
        required_placeholders = self._extract_template_placeholders(template)
        critical_placeholders = TEMPLATE_CRITICAL_PLACEHOLDERS.get(template_name, set())
        warnings: list[dict] = []

        missing_placeholders = sorted(
            placeholder for placeholder in required_placeholders if placeholder not in working_data
        )
        missing_critical = [placeholder for placeholder in missing_placeholders if placeholder in critical_placeholders]
        if missing_critical:
            raise ValueError(
                f"Missing critical template placeholders: {', '.join(missing_critical)}"
            )

        for placeholder in missing_placeholders:
            if placeholder in critical_placeholders:
                continue
            default_value = self._default_placeholder_value(placeholder)
            working_data[placeholder] = default_value
            warnings.append(
                {
                    "type": "missing_placeholder_defaulted",
                    "placeholder": placeholder,
                    "default": default_value,
                }
            )

        for key, value in working_data.items():
            placeholder = f"{{{key}}}"
            template = template.replace(placeholder, str(value))

        unresolved_placeholders = sorted(self._extract_template_placeholders(template))
        unresolved_critical = [placeholder for placeholder in unresolved_placeholders if placeholder in critical_placeholders]
        if unresolved_critical:
            raise ValueError(
                f"Unresolved critical template placeholders after fill: {', '.join(unresolved_critical)}"
            )

        for placeholder in unresolved_placeholders:
            default_value = self._default_placeholder_value(placeholder, unresolved=True)
            template = template.replace(f"{{{placeholder}}}", default_value)
            warnings.append(
                {
                    "type": "unresolved_placeholder_defaulted",
                    "placeholder": placeholder,
                    "default": default_value,
                }
            )

        recursive_unresolved = sorted(self._extract_template_placeholders(template))
        if recursive_unresolved:
            raise ValueError(
                f"Unresolved template placeholders after best-effort fill: {', '.join(recursive_unresolved)}"
            )

        self._record_template_fill_warnings(template_name, warnings)
        return template

    def _extract_json_from_response(self, text: str) -> str:
        """
        Extract JSON from LLM response, handling multiple formats:
        1. Raw JSON (no fences)
        2. Markdown code blocks: ```json...```
        3. Mixed content (text + code blocks)
        4. Balanced brace extraction as last resort
        """
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
            last_candidate = None
            for i in range(brace_start, len(stripped)):
                if stripped[i] == '{':
                    brace_count += 1
                elif stripped[i] == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        # Found complete JSON object
                        json_candidate = stripped[brace_start:i+1]
                        last_candidate = json_candidate
                        # Quick validation: try to parse it
                        try:
                            json.loads(json_candidate)
                            return json_candidate
                        except json.JSONDecodeError:
                            pass
            if last_candidate:
                return last_candidate

        # Return cleaned text if no extraction patterns worked
        return stripped

    def _repair_json_candidate(self, text: str) -> str:
        """
        Apply bounded repairs for recurrent LLM JSON formatting mistakes without
        trying to guess arbitrary structure.
        """
        repaired = text.strip()
        if not repaired:
            return repaired

        # Some model responses arrive as a quoted JSON string instead of a JSON object.
        if repaired[:1] in {'"', "'"} and repaired[-1:] == repaired[:1]:
            try:
                decoded = json.loads(repaired)
            except (json.JSONDecodeError, TypeError):
                decoded = None
            if isinstance(decoded, str) and decoded.strip().startswith("{"):
                repaired = decoded.strip()

        # JSON never uses parentheses; drop any that appear outside strings.
        chars: list[str] = []
        in_string = False
        escape = False
        for ch in repaired:
            if in_string:
                chars.append(ch)
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                continue

            if ch == '"':
                in_string = True
                chars.append(ch)
                continue

            if ch in "()":
                continue

            chars.append(ch)

        repaired = "".join(chars)

        # Repair a few recurrent malformed transitions the model has produced:
        # - object end followed by `: "next_key":`
        # - missing comma between a completed value and the next object key
        # - accidental `"foo" with "bar"` split inside one string value
        # - trailing commas before object/array close
        patterns = (
            (r'([}\]])\s*:\s*"([A-Za-z0-9_]+)"\s*:', r'\1, "\2":'),
            (r'([}\]])\s*"([A-Za-z0-9_]+)"\s*:', r'\1, "\2":'),
            (r'(")\s*"([A-Za-z0-9_]+)"\s*:', r'\1, "\2":'),
            (
                r'(:\s*)"([^"\\]*(?:\\.[^"\\]*)*)"\s+with\s+"([^"\\]*(?:\\.[^"\\]*)*)"',
                r'\1"\2 with \3"',
            ),
            (r',\s*([}\]])', r'\1'),
        )

        previous = None
        while repaired != previous:
            previous = repaired
            for pattern, replacement in patterns:
                repaired = re.sub(pattern, replacement, repaired)

        return repaired

    def _parse_llm_json_response(self, text: str) -> dict:
        """Parse JSON strictly first, then retry with narrow repairs for known LLM glitches."""
        json_candidate = self._extract_json_from_response(text)
        errors: list[json.JSONDecodeError] = []

        for candidate in (json_candidate, self._repair_json_candidate(json_candidate)):
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError as exc:
                errors.append(exc)
                continue
            if isinstance(parsed, dict):
                return parsed
            raise json.JSONDecodeError("Top-level JSON value must be an object", candidate, 0)

        raise errors[-1]

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

    def _predict_blend_option(self, art_keywords_str: str) -> str:
        """Predict blend option from art keywords without requiring an existing choice."""
        art_keywords = [kw.strip().lower() for kw in art_keywords_str.split(',') if kw.strip()]

        option_a_keywords = {
            'crushing', 'collapsed', 'sinking', 'heavy', 'still',
            'subdued', 'smoldering', 'suffocating',
        }
        option_b_keywords = {
            'luminous', 'flowing', 'rhythmic', 'drifting', 'serene',
            'expansive', 'balanced', 'harmonious', 'muted',
        }
        option_c_keywords = {
            'volatile', 'fractured', 'turbulent', 'suspended',
            'dissociated', 'brittle', 'unstable', 'devastated',
        }

        a_matches = sum(1 for kw in art_keywords if kw in option_a_keywords)
        b_matches = sum(1 for kw in art_keywords if kw in option_b_keywords)
        c_matches = sum(1 for kw in art_keywords if kw in option_c_keywords)
        max_matches = max(a_matches, b_matches, c_matches)

        if max_matches == 0:
            return "Option B"
        if b_matches == max_matches:
            return "Option B"
        if c_matches == max_matches:
            return "Option C"
        return "Option A"

    def _is_generic_fragment_phrase(self, fragment_phrase: str) -> bool:
        words = self._tokenize_fragment_text(fragment_phrase)
        return len(words) == 1 and words[0] in GENERIC_FRAGMENT_SINGLETONS

    def _reset_fragment_state(self):
        self.latest_creature_fragment = ""
        self.latest_creature_fragment_why_unique = ""

    def _normalize_fragment_token(self, token: str) -> str:
        normalized = token.casefold()
        if normalized.endswith('ies') and len(normalized) > 4:
            return normalized[:-3] + 'y'
        if normalized.endswith('s') and len(normalized) > 3 and not normalized.endswith('ss'):
            singular_candidate = normalized[:-1]
            known_fragment_terms = (
                GENERIC_FRAGMENT_SINGLETONS
                | FRAGMENT_ENVIRONMENTAL_TERMS
                | FRAGMENT_SYMBOLIC_TERMS
                | FRAGMENT_CELESTIAL_MYTHIC_TERMS
                | FRAGMENT_ENVIRONMENTAL_CONTAMINATION_TERMS
                | FRAGMENT_POINTABLE_FEATURE_TERMS
                | FRAGMENT_GENERAL_BODY_REGION_TERMS
                | FRAGMENT_SCENE_WIDE_TERMS
            )
            if singular_candidate in known_fragment_terms:
                return singular_candidate
        return normalized

    def _tokenize_fragment_text(self, text: str) -> list[str]:
        return [self._normalize_fragment_token(token) for token in re.findall(r'[a-zA-Z]+', (text or '').casefold())]

    def _analyze_signature_fragment(self, fragment_phrase: str, why_unique: str, creature_name: str) -> tuple[str, list[str]]:
        reasons: list[str] = []
        if not isinstance(fragment_phrase, str) or not fragment_phrase.strip():
            return 'generic', ['fragment_missing_phrase']

        normalized_fragment = fragment_phrase.strip()
        fragment_tokens = self._tokenize_fragment_text(normalized_fragment)

        if creature_name and creature_name.casefold() in normalized_fragment.casefold():
            reasons.append('fragment_mentions_creature_name')
        if self._is_generic_fragment_phrase(normalized_fragment):
            reasons.append('fragment_generic_singleton')

        word_count = len(fragment_tokens)
        if word_count < 2:
            reasons.append('fragment_too_short')
        if word_count > 6:
            reasons.append('fragment_too_long')
        if re.search(r'\b(and|with)\b|[,/]', normalized_fragment, re.IGNORECASE):
            reasons.append('fragment_multi_part_assembly')

        if any(token in FRAGMENT_ENVIRONMENTAL_TERMS for token in fragment_tokens):
            reasons.append('fragment_environmental_metaphor')
        elif any(token in FRAGMENT_ENVIRONMENTAL_CONTAMINATION_TERMS for token in fragment_tokens):
            reasons.append('fragment_environmental_contamination')
        elif any(token in FRAGMENT_SYMBOLIC_TERMS or token in FRAGMENT_CELESTIAL_MYTHIC_TERMS for token in fragment_tokens):
            reasons.append('fragment_symbolic_abstraction')
        if any(token in FRAGMENT_SCENE_WIDE_TERMS for token in fragment_tokens):
            reasons.append('fragment_scene_wide_structure')
        if fragment_tokens and fragment_tokens[-1] in FRAGMENT_GENERAL_BODY_REGION_TERMS:
            reasons.append('fragment_general_body_region')
        if 'mane' in fragment_tokens and 'crown' in fragment_tokens:
            reasons.append('fragment_full_identity_cue')
        if not any(token in FRAGMENT_POINTABLE_FEATURE_TERMS for token in fragment_tokens):
            reasons.append('fragment_not_pointable')

        if word_count == 2 and fragment_tokens and fragment_tokens[-1] in GENERIC_FRAGMENT_SINGLETONS:
            reasons.append('fragment_generic_decorated_singleton')

        why_tokens = self._tokenize_fragment_text(why_unique)
        why_unique_cf = (why_unique or '').casefold()
        if why_tokens:
            if any(any(term in token for term in WHY_UNIQUE_FORBIDDEN_TERMS) for token in why_tokens):
                reasons.append('why_unique_symbolic')
            if any(token in FRAGMENT_ENVIRONMENTAL_CONTAMINATION_TERMS for token in why_tokens):
                reasons.append('why_unique_symbolic')
            if not any(any(term in token for term in WHY_UNIQUE_STRUCTURAL_TERMS) for token in why_tokens):
                reasons.append('why_unique_not_morphological')
            if any(word in why_unique_cf for word in ('because it symbolizes', 'symbolizing', 'represents', 'echoes the myth', 'mythic passage')):
                reasons.append('why_unique_symbolic')
        else:
            reasons.append('why_unique_missing')

        generic_reason_keys = {
            'fragment_generic_singleton',
            'fragment_generic_decorated_singleton',
            'fragment_too_short',
            'fragment_too_long',
            'fragment_missing_phrase',
            'why_unique_missing',
        }
        symbolic_reason_keys = {
            'fragment_environmental_metaphor',
            'fragment_environmental_contamination',
            'fragment_symbolic_abstraction',
            'why_unique_symbolic',
            'why_unique_not_morphological',
        }
        assembly_reason_keys = {
            'fragment_multi_part_assembly',
            'fragment_scene_wide_structure',
            'fragment_full_identity_cue',
        }
        generic_reason_keys |= {
            'fragment_general_body_region',
            'fragment_not_pointable',
        }

        if any(reason in assembly_reason_keys for reason in reasons):
            verdict = 'full-assembly risk'
        elif any(reason in symbolic_reason_keys for reason in reasons):
            verdict = 'symbolic/environmental'
        elif any(reason in generic_reason_keys for reason in reasons):
            verdict = 'generic'
        else:
            verdict = 'morphological'

        return verdict, reasons

    def _build_fragment_repair_prompt(
        self,
        *,
        creature_name: str,
        creature_reason: str,
        previous_fragment: str,
        previous_why_unique: str,
        rejection_reasons: list[str],
    ) -> str:
        reason_map = {
            'fragment_missing_phrase': 'signature_fragment is missing.',
            'fragment_mentions_creature_name': 'signature_fragment must not contain the creature name.',
            'fragment_generic_singleton': 'signature_fragment is too generic; avoid plain singleton parts.',
            'fragment_generic_decorated_singleton': 'signature_fragment is still just a generic body part with decoration; pick a more creature-specific structural cue.',
            'fragment_too_short': 'signature_fragment must be at least two words.',
            'fragment_too_long': 'signature_fragment must be at most six words.',
            'fragment_multi_part_assembly': 'signature_fragment is trying to assemble multiple body parts.',
            'fragment_environmental_metaphor': 'signature_fragment is a terrain or landscape metaphor instead of creature morphology.',
            'fragment_environmental_contamination': 'signature_fragment is contaminated by environment or material language instead of creature body structure.',
            'fragment_symbolic_abstraction': 'signature_fragment is symbolic, mythic, emotional, or celestial instead of structural.',
            'fragment_scene_wide_structure': 'signature_fragment is too broad or scene-wide; it should be one local pointable cue.',
            'fragment_general_body_region': 'signature_fragment names a general body region instead of one specific local feature.',
            'fragment_full_identity_cue': 'signature_fragment completes too much of a face or head read and will literalize the creature.',
            'fragment_not_pointable': 'signature_fragment does not describe one small external feature a viewer could point to.',
            'why_unique_symbolic': 'why_unique explains the fragment through symbolism, mythology, astrology, or theme instead of morphology.',
            'why_unique_not_morphological': 'why_unique must explain what structural trait makes the fragment belong to the creature.',
            'why_unique_missing': 'why_unique is missing.',
        }
        issue_lines = "\n".join(f"- {reason_map.get(reason, reason)}" for reason in rejection_reasons) or "- fragment validation failed"
        return (
            "You are repairing only the signature fragment for an already selected creature.\n\n"
            f"Creature: {creature_name}\n"
            f"Reason: {creature_reason}\n\n"
            "Return JSON only with exactly these keys:\n"
            "{\n"
            '  "signature_fragment": "distinctive structural cue",\n'
            '  "why_unique": "one short sentence explaining what structural property makes this fragment specific to this creature"\n'
            "}\n\n"
            "Rules:\n"
            "- keep the creature fixed; do not rename or replace it\n"
            "- find the minimum pointable identifier: one small external visible cue native to this creature\n"
            "- prefer one local edge, hook, ridge, protrusion, tip, curl, or similarly specific outward feature\n"
            "- reject anything that completes a full head/body read, spreads across the whole scene, or feels symbolic/environmental\n"
            "- 2-6 words only\n"
            "- not a generic singleton like claw, beak, tail, wing, or tentacle\n"
            "- not a terrain feature, atmosphere phrase, or chart-derived metaphor\n"
            "- why_unique must justify the fragment morphologically, not mythically or symbolically\n\n"
            "Previous invalid fragment:\n"
            f"- signature_fragment: {previous_fragment or '<missing>'}\n"
            f"- why_unique: {previous_why_unique or '<missing>'}\n\n"
            "Validation issues:\n"
            f"{issue_lines}\n"
        )

    def _repair_signature_fragment(
        self,
        *,
        creature_name: str,
        creature_reason: str,
        previous_fragment: str,
        previous_why_unique: str,
        rejection_reasons: list[str],
    ) -> dict:
        repair_prompt = self._build_fragment_repair_prompt(
            creature_name=creature_name,
            creature_reason=creature_reason,
            previous_fragment=previous_fragment,
            previous_why_unique=previous_why_unique,
            rejection_reasons=rejection_reasons,
        )
        self.save_output('last_prompt_fragment_repair.txt', repair_prompt)
        repair_raw_output = self.call_llm(repair_prompt)
        repair_payload = parse_creature_payload(repair_raw_output)
        repaired_fragment = (repair_payload.get('signature_fragment') or '').strip()
        repaired_why_unique = (repair_payload.get('why_unique') or '').strip()
        repair_verdict, repair_reasons = self._analyze_signature_fragment(
            repaired_fragment,
            repaired_why_unique,
            creature_name,
        )
        return {
            'raw_output': repair_raw_output,
            'payload': repair_payload,
            'signature_fragment': repaired_fragment,
            'why_unique': repaired_why_unique,
            'validation_verdict': repair_verdict,
            'validation_reasons': repair_reasons,
        }

    def _parse_creature_response(self, raw_output: str) -> dict:
        payload = parse_creature_payload(raw_output)
        if payload:
            name = (payload.get('name') or '').strip()
            reason = (payload.get('reason') or '').strip()
            signature_fragment = (payload.get('signature_fragment') or '').strip()
            why_unique = (payload.get('why_unique') or '').strip()
            return {
                'name': name,
                'reason': reason,
                'signature_fragment': signature_fragment,
                'why_unique': why_unique,
                'format': 'json',
                'raw_payload': payload,
            }

        name, reason = split_creature_output(raw_output)
        return {
            'name': name,
            'reason': reason,
            'signature_fragment': '',
            'why_unique': '',
            'format': 'legacy',
            'raw_payload': {},
        }

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
            elif "# Card Title Builder" in prompt:
                return "\n".join(
                    [
                        "BASALT VISTA",
                        "GLASS HARBOR",
                        "SILENT BASIN",
                        "EMBER CAIRN",
                        "HOLLOW MONOLITH",
                    ]
                )
            elif "# Card Scene Description Builder" in prompt:
                return "The field opened past the last edge. Everything reached and nothing pushed back."
            elif "video" in prompt.lower():
                return (
                    "The camera glides with slow restraint through the enclosed formation. "
                    "Mineral dust pours from a side-wall fissure and keeps falling through the full shot. "
                    "Film grain persists while dim light bleeds laterally across the surrounding rock."
                )
            elif "interpretation" in prompt.lower() and "maha" in prompt.lower():
                return "The current planetary periods suggest an introspective and solitary focus. This highlights the depth of rest achieved today."
            elif "creature" in prompt.lower() and "archetype" in prompt.lower():
                return json.dumps(
                    {
                        "name": "The Architect",
                        "reason": "A towering being of silent geometry, mapping cosmic patterns onto the terrain below.",
                        "signature_fragment": "segmented crown lattice",
                        "why_unique": "Uses a distinctive structural feature instead of a generic body part."
                    }
                )
            elif "environment" in prompt.lower() and "energy" in prompt.lower():
                return "Bioluminescent — Organic tissue, natural glow, living light sources"
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
            print("🔄 Using direct Google Gemini 2.5 Pro")
            return call_google_gemini_generate_content(
                prompt=prompt,
                api_key=self.google_api_key,
                model=GEMINI_MODEL,
                temperature=1.0,
                max_output_tokens=12000,
                thinking_budget=8000,
                timeout=GEMINI_TIMEOUT_SECONDS,
            )
        except LLMProviderError as e:
            print(f"❌ Gemini API Error: {e}")
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

        filled_prompt = self.fill_template(template, placeholders, template_name='interpretation')
        self.save_output('last_prompt_interpretation.txt', filled_prompt)
        interpretation = self.call_llm(filled_prompt)
        self.save_output('interpretation.txt', interpretation)
        return interpretation

    def generate_creature(self, daily_data: dict, interpretation: str) -> str:
        """Generate creature archetype (independent of energy zone)"""
        self._reset_fragment_state()
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

        filled_prompt = self.fill_template(template, placeholders, template_name='creature')
        self.save_output('last_prompt_creature.txt', filled_prompt)
        retry_prompt_path = self.output_dir / 'last_prompt_creature_retry.txt'
        first_raw_output = self.call_llm(filled_prompt)
        first_payload = self._parse_creature_response(first_raw_output)
        first_name = first_payload['name']
        first_reason = first_payload['reason']
        first_fragment = first_payload['signature_fragment']
        first_why_unique = first_payload['why_unique']
        first_norm = normalize_creature_name(first_name)
        first_canonical_output = format_creature_output(first_name, first_reason) if first_name else ""
        if first_payload['format'] == 'json':
            first_fragment_verdict, first_fragment_reasons = self._analyze_signature_fragment(
                first_fragment,
                first_why_unique,
                first_name,
            )
        else:
            first_fragment_verdict, first_fragment_reasons = 'no-fragment', []
        first_has_valid_fragment = not first_fragment_reasons

        banned_recent_names = recent_creature_context['banned_recent_names']
        retry_triggered = False
        retry_raw_output = ""
        retry_payload = {}
        retry_name = ""
        retry_reason = ""
        retry_fragment = ""
        retry_why_unique = ""
        retry_fragment_verdict = ""
        retry_fragment_reasons: list[str] = []
        retry_canonical_output = ""
        final_output = first_canonical_output or first_raw_output
        final_name = first_name
        final_reason = first_reason
        final_fragment = first_fragment if first_has_valid_fragment else ""
        final_why_unique = first_why_unique
        retained_parseable_source = 'first'
        invalid_first_payload = not first_norm or not first_reason
        should_retry = invalid_first_payload or (
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
                fragment_rejection_reasons=first_fragment_reasons,
            )
            self.save_output('last_prompt_creature_retry.txt', retry_prompt)
            retry_raw_output = self.call_llm(retry_prompt)
            retry_payload = self._parse_creature_response(retry_raw_output)
            retry_name = retry_payload['name']
            retry_reason = retry_payload['reason']
            retry_fragment = retry_payload['signature_fragment']
            retry_why_unique = retry_payload['why_unique']
            retry_norm = normalize_creature_name(retry_name)
            retry_canonical_output = format_creature_output(retry_name, retry_reason) if retry_name else ""
            if retry_payload['format'] == 'json':
                retry_fragment_verdict, retry_fragment_reasons = self._analyze_signature_fragment(
                    retry_fragment,
                    retry_why_unique,
                    retry_name,
                )
            else:
                retry_fragment_verdict, retry_fragment_reasons = 'no-fragment', []
            retry_has_valid_fragment = not retry_fragment_reasons

            if retry_norm and retry_reason and retry_norm not in banned_recent_names:
                final_output = retry_canonical_output
                final_name = retry_name
                final_reason = retry_reason
                retained_parseable_source = 'retry'
                final_selection_source = 'corrective_retry_valid'
                final_fragment = retry_fragment if retry_has_valid_fragment else ""
                final_why_unique = retry_why_unique
            elif retry_name and retry_reason:
                final_output = retry_canonical_output
                final_name = retry_name
                final_reason = retry_reason
                retained_parseable_source = 'retry'
                final_selection_source = 'repeat_after_retry_warning'
                final_fragment = retry_fragment if retry_has_valid_fragment else ""
                final_why_unique = retry_why_unique
            elif first_name and first_reason:
                final_output = first_canonical_output
                final_name = first_name
                final_reason = first_reason
                retained_parseable_source = 'first'
                final_selection_source = 'repeat_after_retry_warning'
                final_fragment = first_fragment if first_has_valid_fragment else ""
                final_why_unique = first_why_unique
            else:
                raise RuntimeError("Creature selection failed: both attempts were unparseable.")
        else:
            if retry_prompt_path.exists():
                retry_prompt_path.unlink()
            if recent_creature_context['db_lookup_status'] != 'ok':
                final_selection_source = 'history_lookup_failed_no_guard'
            else:
                final_selection_source = 'llm_valid'

        fragment_repair = {
            'attempted': False,
            'raw_output': '',
            'payload': {},
            'signature_fragment': '',
            'why_unique': '',
            'validation_verdict': '',
            'validation_reasons': [],
            'source': '',
        }
        winning_fragment_source = retained_parseable_source
        final_fragment_verdict = retry_fragment_verdict if winning_fragment_source == 'retry' else first_fragment_verdict
        final_fragment_reasons = retry_fragment_reasons if winning_fragment_source == 'retry' else first_fragment_reasons

        if final_name and final_reason and final_fragment_reasons:
            fragment_repair['attempted'] = True
            fragment_repair = {
                **fragment_repair,
                **self._repair_signature_fragment(
                    creature_name=final_name,
                    creature_reason=final_reason,
                    previous_fragment=final_fragment,
                    previous_why_unique=final_why_unique,
                    rejection_reasons=final_fragment_reasons,
                ),
            }
            if not fragment_repair['validation_reasons']:
                final_fragment = fragment_repair['signature_fragment']
                final_why_unique = fragment_repair['why_unique']
                final_fragment_verdict = fragment_repair['validation_verdict']
                final_fragment_reasons = fragment_repair['validation_reasons']
                final_selection_source = 'fragment_repair'
                winning_fragment_source = 'fragment_repair'
                fragment_repair['source'] = 'fragment_repair'
            else:
                final_fragment = ""
                final_why_unique = ""
                final_fragment_verdict = 'no-fragment'
                final_fragment_reasons = fragment_repair['validation_reasons']
                final_selection_source = 'no_fragment_continuation'
                winning_fragment_source = 'no_fragment'
                fragment_repair['source'] = 'no_fragment_continuation'

        self.latest_creature_fragment = final_fragment
        self.latest_creature_fragment_why_unique = final_why_unique
        self.save_output('creature.txt', final_output)
        self.save_output('creature_selected.txt', final_output)
        self.save_json_output(
            'creature_fragment.json',
            {
                'signature_fragment': final_fragment,
                'why_unique': final_why_unique,
                'source': final_selection_source,
                'validation_verdict': final_fragment_verdict,
                'validation_reasons': final_fragment_reasons,
                'fragment_enabled': bool(final_fragment),
            },
        )
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
                'first_payload_format': first_payload['format'],
                'first_payload': first_payload['raw_payload'],
                'first_parsed_name': first_name,
                'first_parsed_reason': first_reason,
                'first_signature_fragment': first_fragment,
                'first_signature_fragment_verdict': first_fragment_verdict,
                'first_signature_fragment_rejections': first_fragment_reasons,
                'first_canonical_output': first_canonical_output,
                'retry_triggered': retry_triggered,
                'retry_raw_output': retry_raw_output,
                'retry_payload_format': retry_payload['format'] if retry_triggered else '',
                'retry_payload': retry_payload['raw_payload'] if retry_triggered else {},
                'retry_parsed_name': retry_name,
                'retry_parsed_reason': retry_reason,
                'retry_signature_fragment': retry_fragment,
                'retry_signature_fragment_verdict': retry_fragment_verdict if retry_triggered else '',
                'retry_signature_fragment_rejections': retry_fragment_reasons if retry_triggered else [],
                'retry_canonical_output': retry_canonical_output,
                'fragment_repair_attempted': fragment_repair['attempted'],
                'fragment_repair_raw_output': fragment_repair['raw_output'],
                'fragment_repair_payload': fragment_repair['payload'],
                'fragment_repair_signature_fragment': fragment_repair['signature_fragment'],
                'fragment_repair_why_unique': fragment_repair['why_unique'],
                'fragment_repair_verdict': fragment_repair['validation_verdict'],
                'fragment_repair_rejections': fragment_repair['validation_reasons'],
                'winning_fragment_source': winning_fragment_source,
                'final_name': final_name,
                'final_reason': final_reason,
                'final_signature_fragment': final_fragment,
                'final_fragment_debug_reason': final_why_unique,
                'final_signature_fragment_verdict': final_fragment_verdict,
                'final_signature_fragment_rejections': final_fragment_reasons,
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
        fragment_rejection_reasons: list[str] | None = None,
    ) -> str:
        if is_repeat and previous_name:
            retry_reason = (
                f'Your previous choice "{previous_name}" is invalid because it appears in the recent-creatures exclusion list.'
            )
        else:
            retry_reason = 'Your previous response could not be parsed into a valid creature payload.'

        recent_names_text = ", ".join(recent_creature_names) if recent_creature_names else "None"
        fragment_feedback = ""
        if fragment_rejection_reasons:
            reason_map = {
                'fragment_missing_phrase': 'signature_fragment is missing.',
                'fragment_mentions_creature_name': 'signature_fragment must not contain the creature name.',
                'fragment_generic_singleton': 'signature_fragment is too generic; avoid plain singleton parts.',
                'fragment_generic_decorated_singleton': 'signature_fragment is still just a generic body part with decoration; pick a more creature-specific structural cue.',
                'fragment_too_short': 'signature_fragment must be at least two words.',
                'fragment_too_long': 'signature_fragment must be at most six words.',
                'fragment_multi_part_assembly': 'signature_fragment is trying to assemble multiple body parts.',
                'fragment_environmental_metaphor': 'signature_fragment is a terrain or landscape metaphor instead of creature morphology.',
                'fragment_environmental_contamination': 'signature_fragment is contaminated by environment or material language instead of creature body structure.',
                'fragment_symbolic_abstraction': 'signature_fragment is symbolic, mythic, emotional, or celestial instead of structural.',
                'fragment_scene_wide_structure': 'signature_fragment is too broad or scene-wide; it should be one local pointable cue.',
                'fragment_general_body_region': 'signature_fragment names a general body region instead of one specific local feature.',
                'fragment_full_identity_cue': 'signature_fragment completes too much of a face or head read and will literalize the creature.',
                'fragment_not_pointable': 'signature_fragment does not describe one small external feature a viewer could point to.',
                'why_unique_symbolic': 'why_unique explains the fragment through symbolism, mythology, astrology, or theme instead of morphology.',
                'why_unique_not_morphological': 'why_unique must explain what structural trait makes the fragment belong to the creature.',
                'why_unique_missing': 'why_unique is missing.',
            }
            bullet_lines = [f"- {reason_map.get(reason, reason)}" for reason in fragment_rejection_reasons]
            fragment_feedback = (
                "\nSignature fragment issues:\n"
                f"{chr(10).join(bullet_lines)}\n"
            )
        return (
            f"{filled_prompt}\n\n"
            "---\n\n"
            "## Correction\n\n"
            f"{retry_reason}\n"
            f"Recent banned creatures: {recent_names_text}\n"
            f"{fragment_feedback}"
            "Return one different creature in valid JSON with keys: name, reason, signature_fragment, why_unique.\n"
            "Do not repeat any banned creature.\n"
            "signature_fragment must be the creature's minimum pointable identifier: one small external feature a viewer could point to in one place.\n"
            "Prefer a local edge, hook, ridge, tip, curl, or similarly specific outward feature, not a broad field or full-body region.\n"
            "Reject anything symbolic, environmental, scene-wide, or close to a full head/body solve.\n"
            "why_unique must justify the fragment structurally, not through mythology, astrology, environment, or interpretation themes.\n"
            "Do not explain the correction outside the JSON.\n\n"
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

    def generate_environment(
        self,
        daily_data: dict,
        interpretation: str,
        *,
        persist_environment_history: bool = False,
    ) -> str:
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

        filled_prompt = self.fill_template(template, placeholders, template_name='environment')
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
        history_persist_status = 'skipped_non_persistent_run'
        history_persist_error = None
        if persist_environment_history and run_date:
            try:
                CardDatabase().upsert_environment_history(
                    run_date=run_date,
                    energy_zone=energy_zone,
                    environment_name=final_name,
                    environment_text=final_environment_output,
                    selection_stage='environment_selected',
                )
                history_persist_status = 'ok'
            except Exception as e:
                history_persist_status = 'failed'
                history_persist_error = str(e)
                print(f"⚠️  Environment history persist failed: {e}")
        elif persist_environment_history:
            history_persist_status = 'missing_run_date'
            history_persist_error = 'Missing run date; environment history not persisted.'
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
                'history_persist_status': history_persist_status,
                'history_persist_error': history_persist_error,
            },
        )
        return final_environment_output

    def build_image_json(
        self,
        daily_data: dict,
        interpretation: str,
        creature: str,
        environment: str,
        *,
        fragment_phrase: str | None = None,
        fragment_grounding: str | None = None,
    ) -> dict:
        """Build complete image generation JSON with blend option selection"""
        template = self.load_template('json_builder')
        creature_name = creature.split('—')[0].strip() if '—' in creature else creature.strip()
        environment_name = environment.split('—')[0].strip() if '—' in environment else environment.strip()
        behavior = daily_data.get('behavior_matrix', {})
        if fragment_phrase is not None:
            creature_fragment_phrase = fragment_phrase.strip()
        else:
            creature_fragment_phrase = self.latest_creature_fragment.strip()
        if fragment_phrase is not None:
            creature_fragment_grounding = (fragment_grounding or '').strip()
        elif fragment_grounding is not None:
            creature_fragment_grounding = fragment_grounding.strip()
        else:
            creature_fragment_grounding = self.latest_creature_fragment_why_unique.strip()
        fragment_enabled = bool(creature_fragment_phrase)

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
            'creature_fragment_phrase': creature_fragment_phrase,
            'creature_fragment_grounding': creature_fragment_grounding,
            'creature_negative_exclusion': f'no obvious literal {creature_name}',
        }

        filled_prompt = self.fill_template(template, placeholders, template_name='json_builder')
        self.save_output('last_prompt_image_json.txt', filled_prompt)
        prompt_to_send = filled_prompt
        json_output = ''
        image_json: dict = {}
        last_rejection_reasons: list[str] = []
        depth_level = daily_data.get('depth_level', 'Unknown')

        for attempt in range(3):
            json_output = self.call_llm(prompt_to_send)
            rejection_reasons: list[str] = []

            try:
                image_json = self._parse_llm_json_response(json_output)
            except json.JSONDecodeError as e:
                print(f"⚠️  Failed to decode JSON from LLM: {e}")
                image_json = {}
                rejection_reasons.append('image_json_parse_failure')

            if not rejection_reasons:
                rejection_reasons.extend(self._validate_image_json_shape(image_json))
                rejection_reasons.extend(
                    self._validate_image_json(
                        image_json,
                        depth_level,
                        creature_name=creature_name,
                        fragment_phrase=creature_fragment_phrase,
                        fragment_required=fragment_enabled,
                    )
                )

            if not rejection_reasons:
                self.save_json_output('image_prompt.json', image_json)
                break

            last_rejection_reasons = rejection_reasons
            if attempt == 2:
                reason_text = ', '.join(last_rejection_reasons)
                raise RuntimeError(
                    'Image JSON validation failed after 3 attempts. '
                    f'Reasons: {reason_text}. Last output: {json_output.strip()}'
                )

            retry_prompt = self._build_image_json_retry_prompt(
                filled_prompt,
                previous_output=json_output,
                rejection_reasons=rejection_reasons,
            )
            suffix = '' if attempt == 0 else f'_{attempt + 1}'
            self.save_output(f'last_prompt_image_json_retry{suffix}.txt', retry_prompt)
            prompt_to_send = retry_prompt

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

    def _parse_title_candidates(self, raw_output: str) -> list[str]:
        candidates: list[str] = []
        seen: set[str] = set()
        lines = raw_output.splitlines() if raw_output.strip() else [raw_output]

        for line in lines:
            candidate = line.strip()
            if not candidate:
                continue
            candidate = re.sub(r"^(title|candidate)\s*\d*\s*:\s*", "", candidate, flags=re.IGNORECASE)
            for separator in (" — ", " - "):
                if separator in candidate:
                    candidate = candidate.split(separator, 1)[0].strip()
            cleaned = clean_title(candidate)
            normalized = normalize_title(cleaned)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            candidates.append(cleaned)

        if not candidates:
            cleaned = clean_title(raw_output)
            if cleaned:
                candidates.append(cleaned)

        return candidates[:5]

    def _build_title_retry_prompt(
        self,
        filled_prompt: str,
        *,
        previous_output: str,
        rejected_candidates: list[str],
        recent_titles: list[str],
        recent_structural_keys: list[str],
    ) -> str:
        recent_titles_text = ", ".join(recent_titles) if recent_titles else "None"
        recent_structural_keys_text = ", ".join(recent_structural_keys) if recent_structural_keys else "None"
        rejected_text = ", ".join(rejected_candidates) if rejected_candidates else "None"
        return (
            f"{filled_prompt}\n\n"
            "---\n\n"
            "## Correction\n\n"
            "None of your previous title candidates could be accepted.\n"
            f"Rejected candidates: {rejected_text}\n"
            f"Exact banned titles: {recent_titles_text}\n"
            f"Structural keys to avoid: {recent_structural_keys_text}\n"
            "Return 5 NEW title candidates, strongest first, one per line.\n"
            "Do not number them. Do not explain them. Do not repeat any rejected candidate.\n\n"
            "Previous response:\n"
            f"{previous_output.strip()}\n"
        )

    def _clean_scene_description_output(self, raw_output: str) -> str:
        cleaned = raw_output.strip()
        if not cleaned:
            return ""

        if cleaned.startswith("{"):
            try:
                parsed = json.loads(self._extract_json_from_response(cleaned))
                if isinstance(parsed, dict):
                    cleaned = str(
                        parsed.get("scene_description")
                        or parsed.get("description")
                        or cleaned
                    ).strip()
            except json.JSONDecodeError:
                pass

        fence_match = re.search(r"```(?:\w+)?\s*(.*?)```", cleaned, re.DOTALL)
        if fence_match:
            cleaned = fence_match.group(1).strip()

        cleaned = re.sub(r"^scene_description\s*:\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = " ".join(line.strip() for line in cleaned.splitlines() if line.strip())
        return cleaned.strip("\"'` ")

    def _validate_scene_description(self, value: str) -> list[str]:
        reasons: list[str] = []
        if not value:
            return ["blank_scene_description"]

        if len(value) < 55:
            reasons.append("scene_description_too_short")
        if len(value) > 100:
            reasons.append("scene_description_too_long")

        forbidden_patterns = [
            (r"\bbody\b", "mentions_body"),
            (r"\brecovery\b", "mentions_metric_language"),
            (r"\bstrain\b", "mentions_metric_language"),
            (r"\bsleep score\b", "mentions_metric_language"),
            (r"\bscore\b", "mentions_metric_language"),
            (r"\bzone\b", "mentions_metric_language"),
            (r"%", "mentions_percentage"),
            (r"\bpercent(?:age)?\b", "mentions_percentage"),
        ]
        for pattern, reason in forbidden_patterns:
            if re.search(pattern, value, flags=re.IGNORECASE) and reason not in reasons:
                reasons.append(reason)
        return reasons

    def _build_scene_retry_prompt(
        self,
        filled_prompt: str,
        *,
        previous_output: str,
        rejection_reasons: list[str],
    ) -> str:
        return (
            f"{filled_prompt}\n\n"
            "---\n\n"
            "## Correction\n\n"
            "Your previous scene description is invalid.\n"
            f"Validation issues: {', '.join(rejection_reasons)}\n"
            "Return one corrected scene description only. No JSON. No explanation.\n\n"
            "Previous response:\n"
            f"{previous_output.strip()}\n"
        )

    def _fallback_scene_description(self, image_json: dict | None) -> str:
        image_json = image_json or {}
        core_concept = str(image_json.get('core_concept', '')).strip()
        short_scene = (core_concept[:157] + '...') if len(core_concept) > 160 else core_concept
        return short_scene or "Scene description unavailable."

    def generate_title(self, daily_data: dict, date_display: str, environment: str = "") -> dict:
        template = self.load_template('title_builder')
        behavior = daily_data.get('behavior_matrix', {})
        env_name = environment.split('—')[0].strip() if '—' in environment else environment.strip()
        run_date = daily_data.get('date') or os.getenv('PIPELINE_DATE')
        recent_title_context = self._resolve_recent_titles(run_date)

        placeholders = {
            'environment': env_name,
            'depth_level': str(daily_data.get('depth_level', 'Unknown')),
            'art_keywords': ', '.join(behavior.get('art_keywords', [])),
            'body_keywords': ', '.join(behavior.get('body_keywords', [])),
            'one_liner': behavior.get('one_liner', ''),
            'date_display': date_display,
            'recent_titles': recent_title_context['recent_titles_prompt'],
            'recent_structural_keys': recent_title_context['recent_structural_keys_prompt'],
        }

        filled_prompt = self.fill_template(template, placeholders, template_name='title_builder')
        self.save_output('last_prompt_title.txt', filled_prompt)
        first_raw_output = self.call_llm(filled_prompt)
        first_candidates = self._parse_title_candidates(first_raw_output)
        first_assessments = [
            assess_title_candidate(
                candidate,
                banned_exact_titles=recent_title_context['banned_recent_titles'],
                banned_structural_keys=recent_title_context['banned_structural_keys'],
            )
            for candidate in first_candidates
        ]

        first_valid = next((assessment for assessment in first_assessments if assessment.is_fully_valid), None)
        retry_triggered = False
        retry_raw_output = ""
        retry_candidates: list[str] = []
        retry_assessments = []
        selected_assessment = first_valid
        selected_title_source = (
            'history_lookup_failed_no_guard'
            if first_valid and recent_title_context['db_lookup_status'] != 'ok'
            else 'first_batch_valid'
        )

        title_retry_path = self.output_dir / 'last_prompt_title_retry.txt'
        if not first_valid:
            retry_triggered = True
            retry_prompt = self._build_title_retry_prompt(
                filled_prompt,
                previous_output=first_raw_output,
                rejected_candidates=[assessment.cleaned for assessment in first_assessments if assessment.cleaned],
                recent_titles=recent_title_context['recent_titles'],
                recent_structural_keys=recent_title_context['recent_structural_keys'],
            )
            self.save_output('last_prompt_title_retry.txt', retry_prompt)
            retry_raw_output = self.call_llm(retry_prompt)
            retry_candidates = self._parse_title_candidates(retry_raw_output)
            retry_assessments = [
                assess_title_candidate(
                    candidate,
                    banned_exact_titles=recent_title_context['banned_recent_titles'],
                    banned_structural_keys=recent_title_context['banned_structural_keys'],
                )
                for candidate in retry_candidates
            ]
            retry_valid = next((assessment for assessment in retry_assessments if assessment.is_fully_valid), None)
            if retry_valid:
                selected_assessment = retry_valid
                selected_title_source = 'retry_batch_valid'
            else:
                combined_soft = next(
                    (
                        (assessment, source)
                        for source, assessments in (
                            ('first_batch', first_assessments),
                            ('retry_batch', retry_assessments),
                        )
                        for assessment in assessments
                        if assessment.is_soft_acceptable
                    ),
                    None,
                )
                if combined_soft:
                    selected_assessment, selected_batch = combined_soft
                    selected_title_source = f'soft_accept_family_collision_{selected_batch}'
                else:
                    selected_assessment = None
                    selected_title_source = 'hard_failure_fallback'
        else:
            if title_retry_path.exists():
                title_retry_path.unlink()

        final_title = selected_assessment.cleaned if selected_assessment else "Daily State Card"
        return {
            'title': final_title,
            'prompt': filled_prompt,
            'debug': {
                'recent_titles_used': recent_title_context['recent_titles'],
                'recent_structural_keys_used': recent_title_context['recent_structural_keys'],
                'first_raw_output': first_raw_output,
                'first_candidates': first_candidates,
                'first_candidate_assessments': [assessment.as_dict() for assessment in first_assessments],
                'retry_triggered': retry_triggered,
                'retry_raw_output': retry_raw_output,
                'retry_candidates': retry_candidates,
                'retry_candidate_assessments': [assessment.as_dict() for assessment in retry_assessments],
                'selected_title': final_title,
                'selected_structural_key': structural_title_key(final_title),
                'selected_title_source': selected_title_source,
            },
            'recent_title_context': recent_title_context,
        }

    def generate_scene_description(
        self,
        daily_data: dict,
        date_display: str,
        environment: str = "",
        image_json: dict | None = None,
    ) -> dict:
        template = self.load_template('scene_description_builder')
        behavior = daily_data.get('behavior_matrix', {})
        env_name = environment.split('—')[0].strip() if '—' in environment else environment.strip()

        placeholders = {
            'environment': env_name,
            'depth_level': str(daily_data.get('depth_level', 'Unknown')),
            'art_keywords': ', '.join(behavior.get('art_keywords', [])),
            'body_keywords': ', '.join(behavior.get('body_keywords', [])),
            'one_liner': behavior.get('one_liner', ''),
            'date_display': date_display,
        }

        filled_prompt = self.fill_template(template, placeholders, template_name='scene_description_builder')
        self.save_output('last_prompt_scene_description.txt', filled_prompt)
        first_raw_output = self.call_llm(filled_prompt)
        first_cleaned = self._clean_scene_description_output(first_raw_output)
        first_rejection_reasons = self._validate_scene_description(first_cleaned)

        retry_triggered = False
        retry_raw_output = ""
        retry_cleaned = ""
        retry_rejection_reasons: list[str] = []
        selected_scene = first_cleaned
        selected_scene_source = 'first_pass_valid'

        scene_retry_path = self.output_dir / 'last_prompt_scene_description_retry.txt'
        if first_rejection_reasons:
            retry_triggered = True
            retry_prompt = self._build_scene_retry_prompt(
                filled_prompt,
                previous_output=first_raw_output,
                rejection_reasons=first_rejection_reasons,
            )
            self.save_output('last_prompt_scene_description_retry.txt', retry_prompt)
            retry_raw_output = self.call_llm(retry_prompt)
            retry_cleaned = self._clean_scene_description_output(retry_raw_output)
            retry_rejection_reasons = self._validate_scene_description(retry_cleaned)
            if not retry_rejection_reasons:
                selected_scene = retry_cleaned
                selected_scene_source = 'retry_valid'
            else:
                selected_scene = self._fallback_scene_description(image_json)
                selected_scene_source = 'fallback_core_concept'
        else:
            if scene_retry_path.exists():
                scene_retry_path.unlink()

        return {
            'scene_description': selected_scene,
            'prompt': filled_prompt,
            'debug': {
                'first_raw_output': first_raw_output,
                'first_cleaned_output': first_cleaned,
                'first_rejection_reasons': first_rejection_reasons,
                'retry_triggered': retry_triggered,
                'retry_raw_output': retry_raw_output,
                'retry_cleaned_output': retry_cleaned,
                'retry_rejection_reasons': retry_rejection_reasons,
                'selected_scene_description': selected_scene,
                'selected_scene_source': selected_scene_source,
            },
        }

    def extract_metadata(self, daily_data: dict, date_display: str, environment: str = "", image_json: dict = None) -> dict:
        """Extract card metadata by generating title and scene description separately."""
        run_date = daily_data.get('date') or os.getenv('PIPELINE_DATE')
        title_result = self.generate_title(daily_data, date_display, environment)
        scene_result = self.generate_scene_description(daily_data, date_display, environment, image_json=image_json)

        combined_prompt = (
            "# Metadata Prompt Trace\n\n"
            "## Title Prompt\n\n"
            f"{title_result['prompt']}\n\n"
            "---\n\n"
            "## Scene Description Prompt\n\n"
            f"{scene_result['prompt']}\n"
        )
        self.save_output('last_prompt_metadata.txt', combined_prompt)

        final_metadata = {
            "title": title_result['title'],
            "scene_description": scene_result['scene_description'],
            "date_display": date_display,
        }
        self.save_json_output(
            'metadata_selection_debug.json',
            {
                'run_date': run_date,
                'db_lookup_status': title_result['recent_title_context']['db_lookup_status'],
                'db_lookup_error': title_result['recent_title_context']['db_lookup_error'],
                'raw_recent_history': title_result['recent_title_context']['raw_recent_history'],
                'recent_titles_used': title_result['recent_title_context']['recent_titles'],
                'recent_structural_keys_used': title_result['recent_title_context']['recent_structural_keys'],
                'normalized_banned_titles': title_result['recent_title_context']['normalized_banned_titles'],
                'normalized_banned_structural_keys': title_result['recent_title_context']['normalized_banned_structural_keys'],
                'title_debug': title_result['debug'],
                'scene_debug': scene_result['debug'],
                'title_selection_source': title_result['debug']['selected_title_source'],
                'scene_selection_source': scene_result['debug']['selected_scene_source'],
                'final_title': final_metadata.get('title', ''),
                'final_scene_description': final_metadata.get('scene_description', ''),
                'final_selection_source': (
                    f"title:{title_result['debug']['selected_title_source']}"
                    f"|scene:{scene_result['debug']['selected_scene_source']}"
                ),
            },
        )
        with open(self.output_dir / 'card_metadata.json', 'w', encoding='utf-8') as f:
            json.dump(final_metadata, f, indent=2)

        return final_metadata

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
        recent_structural_keys = []
        normalized_banned_structural_keys = []
        banned_structural_keys = set()

        if db_lookup_status == 'ok':
            for title in raw_recent_history:
                cleaned_title = clean_title(title)
                normalized_title = normalize_title(cleaned_title)
                if not normalized_title or normalized_title in banned_recent_titles:
                    continue
                banned_recent_titles.add(normalized_title)
                normalized_banned_titles.append(normalized_title)
                recent_titles.append(cleaned_title)
            recent_structural_keys = [key.upper() for key in build_structural_title_keys(recent_titles)]
            normalized_banned_structural_keys = [key.casefold() for key in recent_structural_keys]
            banned_structural_keys = set(normalized_banned_structural_keys)

        recent_titles_prompt = "\n".join(f"- {title}" for title in recent_titles)
        if not recent_titles_prompt:
            recent_titles_prompt = "None — no restriction."
        recent_structural_keys_prompt = "\n".join(f"- {key}" for key in recent_structural_keys)
        if not recent_structural_keys_prompt:
            recent_structural_keys_prompt = "None — no restriction."

        return {
            'db_lookup_status': db_lookup_status,
            'db_lookup_error': db_lookup_error,
            'raw_recent_history': raw_recent_history,
            'recent_titles': recent_titles,
            'recent_structural_keys': recent_structural_keys,
            'recent_titles_prompt': recent_titles_prompt,
            'recent_structural_keys_prompt': recent_structural_keys_prompt,
            'normalized_banned_titles': normalized_banned_titles,
            'normalized_banned_structural_keys': normalized_banned_structural_keys,
            'banned_recent_titles': banned_recent_titles,
            'banned_structural_keys': banned_structural_keys,
        }

    _DEEP_ARCHITECTURAL = re.compile(
        r'\b(chamber|chambers|vaulted|vault|corridor|corridors|hull|hulls|hangar|hangars)\b'
        r'|tunnel interior|ribbed structure|engineered arch|ceiling framework',
        re.IGNORECASE,
    )

    _DEEP_OVERHEAD_APERTURE = re.compile(
        r'\b(shaft|shaft-light|skylight|aperture|oculus)\b'
        r'|hole above|opening above|open ceiling|ceiling hole|vertical beam|vertical shaft'
        r'|descend\w* from above|fall\w* from above|light entering from above',
        re.IGNORECASE,
    )

    _ABYSS_BRIGHT_OPENING = re.compile(
        r'cave mouth|skylight|tunnel exit|horizon(?: line)?|bright upper opening|upper opening'
        r'|bright zone in the upper third|dominant bright zone|opening above|open ceiling|ceiling hole'
        r'|\b(shaft|shaft-light|aperture|oculus)\b',
        re.IGNORECASE,
    )

    _LOW_FAILURE_EVENT = re.compile(
        r'\b(fractur\w*|crack\w*|collaps\w*|surg\w*|ruptur\w*|burst\w*|falls?|shed\w*|calv\w*|'
        r'cascad\w*|stream\w*|blast\w*|overwhelm\w*|buckl\w*|drops?|shear\w*|extinguish\w*|'
        r'fails?|failing|fragment\w*|breach\w*|fissur\w*|pour\w*|accumulat\w*|demolish\w*|'
        r'wreck\w*|shatter\w*|engulf\w*|widens?|separ\w*)\b',
        re.IGNORECASE,
    )

    # Material class lookup — used by video prompt generation only.
    # Environments not listed here default to solid behavior.
    ENVIRONMENT_MATERIAL_CLASS: dict[str, str] = {
        'Wind/Sky Realms':      'atmospheric',
        'Mist/Fog Realms':      'atmospheric',
        'Plasma/Nebula':        'atmospheric',
        'Ocean/Underwater':     'fluid',
    }

    # Solid-matter failure terms that are inappropriate for atmospheric / fluid environments
    # unless a named solid formation is the explicit subject.
    _ATMOSPHERIC_SOLID_FAILURE = re.compile(
        r'\b(crack(?:s|ed|ing|le)?|fractur\w*|shatter\w*|debris|splinter\w*|ruptur\w*)\b'
        r'|broken surface|impact point|surface collapse',
        re.IGNORECASE,
    )

    # Narrow list of clearly solid subjects. Intentionally excludes generic words like
    # "formation", "structure", "wall", and "ground" because those appear in soft-matter
    # scenes (cloud formation, fog wall, storm structure) and weaken the guardrail.
    _SOLID_SUBJECT = re.compile(
        r'\b(stone|rock\w*|ice|crystal\w*|cliff\w*|reef\w*|pillar\w*|column\w*|'
        r'slab\w*|boulder\w*|crust|mineral\w*|stalactite\w*|stalagmite\w*|granite|basalt)\b',
        re.IGNORECASE,
    )

    _SOLID_FAILURE_VERB = re.compile(
        r'\b(crack(?:s|ed|ing|le)?|fractur\w*|shatter\w*|ruptur\w*|splinter\w*|'
        r'collaps\w*|shed\w*|falls?|drops?|calv\w*|cascad\w*)\b',
        re.IGNORECASE,
    )

    _POSITIVE_LITERALIZING = re.compile(
        r'\b(silhouette|outline readable|shaped like|resembles|full-body|statue|animal figure)\b',
        re.IGNORECASE,
    )
    _FRAGMENT_STAGING = re.compile(
        r'\b(clearly visible|clearly shown|fully visible|fully revealed|displayed|showcased|staged|presented)\b',
        re.IGNORECASE,
    )

    def _split_video_sentences(self, video_prompt: str) -> list[str]:
        lines = [line.strip() for line in video_prompt.splitlines() if line.strip()]
        normalized = ' '.join(lines)
        if not normalized:
            return []
        return [part.strip() for part in re.split(r'(?<=[.!?])\s+', normalized) if part.strip()]

    def _is_negated_visual_match(self, text: str, match: re.Match[str]) -> bool:
        window = text[max(0, match.start() - 48):match.start()].lower()
        return bool(
            re.search(r'(?:\bno|\bnot|\bwithout|\bavoid|\bnever|\bomit|\babsent from)\s+$', window)
            or window.endswith('does not ')
            or window.endswith("doesn't ")
            or window.endswith('must not ')
        )

    def _first_unnegated_match(self, pattern: re.Pattern[str], text: str) -> re.Match[str] | None:
        for match in pattern.finditer(text):
            if not self._is_negated_visual_match(text, match):
                return match
        return None

    def _iter_text_fragments(self, value, path: str):
        if isinstance(value, str):
            yield path, value
            return
        if isinstance(value, dict):
            for key, child in value.items():
                child_path = f'{path}.{key}' if path else key
                yield from self._iter_text_fragments(child, child_path)
            return
        if isinstance(value, list):
            for idx, child in enumerate(value):
                child_path = f'{path}[{idx}]'
                yield from self._iter_text_fragments(child, child_path)

    def _iter_image_visual_fields(self, image_json: dict):
        for key in ('core_concept', 'lighting', 'composition', 'color_palette', 'scene_config'):
            if key in image_json:
                yield from self._iter_text_fragments(image_json[key], key)

    def _iter_positive_image_fields(self, image_json: dict):
        for key, value in image_json.items():
            if key == 'mandatory_exclusions':
                continue
            if key == 'rendering':
                if isinstance(value, dict):
                    for subkey, child in value.items():
                        if subkey == 'avoid':
                            continue
                        yield from self._iter_text_fragments(child, f'rendering.{subkey}')
                continue
            if key == 'consistency_anchors':
                continue
            yield from self._iter_text_fragments(value, key)

    def _validate_image_json(
        self,
        image_json: dict,
        depth_level: str,
        *,
        creature_name: str = '',
        fragment_phrase: str = '',
        fragment_required: bool = True,
    ) -> list[str]:
        reasons: list[str] = []
        positive_fragments = list(self._iter_positive_image_fields(image_json))

        creature_name_cf = creature_name.casefold().strip()
        if creature_name_cf:
            for path, text in positive_fragments:
                if creature_name_cf in text.casefold():
                    reasons.append(f'creature_name_in_positive:{path}')
                    break

        fragment_phrase_cf = fragment_phrase.casefold().strip()
        if fragment_required and fragment_phrase_cf:
            fragment_occurrences: list[tuple[str, int]] = []
            for path, text in positive_fragments:
                count = text.casefold().count(fragment_phrase_cf)
                if count:
                    fragment_occurrences.append((path, count))
            total_occurrences = sum(count for _path, count in fragment_occurrences)
            if total_occurrences != 1:
                reasons.append(f'fragment_occurrence_count:{total_occurrences}')
            for path, _count in fragment_occurrences:
                if path != 'creature_integration.visibility':
                    reasons.append(f'fragment_outside_visibility:{path}')
                    break
            visibility_text = str(image_json.get('creature_integration', {}).get('visibility', ''))
            if visibility_text:
                match = self._first_unnegated_match(self._FRAGMENT_STAGING, visibility_text)
                if match:
                    reasons.append(f'fragment_staged_visibility:{match.group(0).lower()}')

        for path, text in positive_fragments:
            match = self._first_unnegated_match(self._POSITIVE_LITERALIZING, text)
            if match:
                reasons.append(f'positive_literalizing_language:{path}:{match.group(0).lower()}')
                break

        rendering_avoid = image_json.get('rendering', {}).get('avoid', [])
        if creature_name_cf and isinstance(rendering_avoid, list):
            for item in rendering_avoid:
                if isinstance(item, str) and creature_name_cf in item.casefold():
                    reasons.append('creature_name_in_rendering_avoid')
                    break

        mandatory_exclusions = image_json.get('mandatory_exclusions', [])
        if creature_name_cf and isinstance(mandatory_exclusions, list):
            expected_negative = f'no obvious literal {creature_name}'.casefold()
            creature_specific_entries = [
                item for item in mandatory_exclusions
                if isinstance(item, str) and item.casefold() == expected_negative
            ]
            if len(creature_specific_entries) != 1:
                reasons.append(f'creature_negative_count:{len(creature_specific_entries)}')
            if not creature_specific_entries:
                reasons.append('missing_standard_creature_negative')

        if depth_level not in {'DEEP', 'ABYSS'}:
            return reasons

        for path, text in self._iter_image_visual_fields(image_json):
            if depth_level == 'DEEP':
                match = self._first_unnegated_match(self._DEEP_ARCHITECTURAL, text)
                if match:
                    reasons.append(f'deep_image_architectural_language:{path}:{match.group(0).lower()}')
                match = self._first_unnegated_match(self._DEEP_OVERHEAD_APERTURE, text)
                if match:
                    reasons.append(f'deep_image_overhead_aperture:{path}:{match.group(0).lower()}')
            if depth_level == 'ABYSS':
                match = self._first_unnegated_match(self._ABYSS_BRIGHT_OPENING, text)
                if match:
                    reasons.append(f'abyss_image_bright_opening:{path}:{match.group(0).lower()}')
        return reasons

    def _validate_image_json_shape(self, image_json: dict) -> list[str]:
        reasons: list[str] = []
        if not isinstance(image_json, dict):
            return ['image_json_not_object']

        if not any(key in image_json for key in ('core_concept', 'lighting', 'composition', 'color_palette', 'scene_config')):
            reasons.append('image_json_missing_visual_fields')

        creature_integration = image_json.get('creature_integration')
        if not isinstance(creature_integration, dict):
            reasons.append('image_json_missing_creature_integration')
            return reasons

        blend = creature_integration.get('blend')
        if not isinstance(blend, str) or not blend.strip():
            reasons.append('image_json_missing_blend_option')

        return reasons

    def _build_image_json_retry_prompt(
        self,
        filled_prompt: str,
        *,
        previous_output: str,
        rejection_reasons: list[str],
    ) -> str:
        corrections = []
        for reason in rejection_reasons:
            if reason == 'image_json_parse_failure':
                corrections.append(
                    'Your previous response was not valid JSON. '
                    'Return one valid JSON object only, with no markdown fences and no prose.'
                )
            elif reason == 'image_json_not_object':
                corrections.append('The top-level response must be a JSON object (not an array or scalar).')
            elif reason == 'image_json_missing_visual_fields':
                corrections.append(
                    'The JSON is missing scene description fields. Include core_concept plus visual blocks such as lighting/composition/color_palette.'
                )
            elif reason == 'image_json_missing_creature_integration':
                corrections.append('The JSON must include creature_integration with a valid blend choice.')
            elif reason == 'image_json_missing_blend_option':
                corrections.append('The JSON must include creature_integration.blend as a non-empty string.')
            elif reason.startswith('creature_name_in_positive:'):
                _, path = reason.split(':', 1)
                corrections.append(
                    f'The JSON field "{path}" contains the creature name in positive scene language. '
                    'Remove the creature name from all positive fields and keep it only in the standardized negative exclusion.'
                )
            elif reason.startswith('fragment_occurrence_count:'):
                _, count = reason.split(':', 1)
                corrections.append(
                    f'The creature fragment appears {count} times in positive fields. '
                    'Use the fragment phrase exactly once, and only in creature_integration.visibility.'
                )
            elif reason.startswith('fragment_outside_visibility:'):
                _, path = reason.split(':', 1)
                corrections.append(
                    f'The creature fragment appears in "{path}". '
                    'The fragment phrase must appear only in creature_integration.visibility.'
                )
            elif reason.startswith('fragment_staged_visibility:'):
                _, term = reason.split(':', 1)
                corrections.append(
                    f'creature_integration.visibility uses "{term}", which stages the fragment too explicitly. '
                    'Write the fragment as something a close viewer might catch through pareidolia, partially obscured by surrounding massing or patterning.'
                )
            elif reason.startswith('positive_literalizing_language:'):
                _, path, term = reason.split(':', 2)
                corrections.append(
                    f'The JSON field "{path}" contains "{term}", which literalizes the creature. '
                    'Do not use silhouette, outline-readable, shaped-like, full-body, statue, or animal-figure language.'
                )
            elif reason == 'creature_name_in_rendering_avoid':
                corrections.append(
                    'Do not repeat the creature name inside rendering.avoid. '
                    'Keep creature-specific naming only in one mandatory_exclusions entry.'
                )
            elif reason.startswith('creature_negative_count:'):
                _, count = reason.split(':', 1)
                corrections.append(
                    f'The JSON contains {count} creature-specific mandatory exclusions. '
                    'Keep exactly one creature-specific exclusion.'
                )
            elif reason == 'missing_standard_creature_negative':
                corrections.append(
                    'mandatory_exclusions must include exactly one standardized creature-specific entry: '
                    '"no obvious literal <Creature>".'
                )
            elif reason.startswith('deep_image_architectural_language:'):
                _, path, term = reason.split(':', 2)
                corrections.append(
                    f'The JSON field "{path}" contains "{term}" and reads too much like built architecture. '
                    'DEEP image prompts must stay geological and buried, not engineered or chamber-like.'
                )
            elif reason.startswith('deep_image_overhead_aperture:'):
                _, path, term = reason.split(':', 2)
                corrections.append(
                    f'The JSON field "{path}" contains "{term}" and implies an opening above the scene. '
                    'DEEP must not show a centered overhead opening, circular hole, skylight, or vertical shaft of light. '
                    'Keep the overhead mass solid and enclosed. '
                    'Move the light source to a lateral crack, wall seam, translucent mineral face, or diffuse side-entry.'
                )
            elif reason.startswith('abyss_image_bright_opening:'):
                _, path, term = reason.split(':', 2)
                corrections.append(
                    f'The JSON field "{path}" contains "{term}" and introduces an opening, horizon, or bright upper zone. '
                    'ABYSS must stay sealed and interior: no cave mouth, skylight, tunnel exit, horizon line, large opening, or dominant bright upper area.'
                )
        correction_text = '\n'.join(f'- {c}' for c in corrections)
        return (
            f"{filled_prompt}\n\n"
            "---\n\n"
            "## Correction Required\n\n"
            "Your previous image JSON violated one or more rules:\n"
            f"{correction_text}\n\n"
            "Rewrite the full response as valid JSON only. Do not add commentary or markdown fences.\n\n"
            "Previous response:\n"
            f"{previous_output.strip()}\n"
        )

    def _check_materiality_violation(self, sentence: str, material_class: str) -> str | None:
        """Return a rejection reason if a soft-matter scene uses hard-material failure language."""
        if material_class not in ('atmospheric', 'fluid'):
            return None
        for match in self._ATMOSPHERIC_SOLID_FAILURE.finditer(sentence):
            if self._is_negated_visual_match(sentence, match):
                continue
            if self._has_explicit_solid_failure_subject(sentence, match.start(), match.end()):
                continue
            term = match.group(0).lower()
            return f'materiality_violation_{material_class}:{term}'
        return None

    def _has_explicit_solid_failure_subject(self, sentence: str, match_start: int, match_end: int) -> bool:
        """Return True only when a clearly solid subject is explicitly the thing failing.

        This is intentionally stricter than a proximity check. A nearby solid noun should
        not pardon phrases like "the water fractures around a reef" or "the cloud bank
        shatters beside a cliff wall".
        """
        prefix = sentence[:match_end]
        for solid_match in self._SOLID_SUBJECT.finditer(prefix):
            gap_text = prefix[solid_match.end():match_end]
            failure_match = self._SOLID_FAILURE_VERB.search(gap_text)
            if not failure_match:
                continue
            # The solid subject needs to lead directly into the failure clause with only a
            # few descriptive words between them. Once that clause is established, later
            # debris/fallout in the same sentence is allowed to ride on it.
            subject_to_failure = gap_text[:failure_match.start()]
            if len(re.findall(r'\b\w+\b', subject_to_failure)) > 4:
                continue
            return True
        return False

    def _validate_video_prompt(
        self,
        video_prompt: str,
        depth_level: str,
        recovery_zone: str,
        material_class: str = '',
    ) -> list[str]:
        reasons: list[str] = []
        if depth_level == 'DEEP':
            m = self._first_unnegated_match(self._DEEP_ARCHITECTURAL, video_prompt)
            if m:
                reasons.append(f'deep_architectural_language:{m.group(0).lower()}')
            m = self._first_unnegated_match(self._DEEP_OVERHEAD_APERTURE, video_prompt)
            if m:
                reasons.append(f'deep_overhead_aperture:{m.group(0).lower()}')
        if depth_level == 'ABYSS':
            m = self._first_unnegated_match(self._ABYSS_BRIGHT_OPENING, video_prompt)
            if m:
                reasons.append(f'abyss_bright_opening:{m.group(0).lower()}')
        if recovery_zone == 'LOW':
            sentences = self._split_video_sentences(video_prompt)
            motion_sentence = sentences[1] if len(sentences) >= 2 else ''
            if not motion_sentence or not self._LOW_FAILURE_EVENT.search(motion_sentence):
                reasons.append('low_recovery_sentence_two_no_failure_event')
        if material_class in ('atmospheric', 'fluid'):
            sentences = self._split_video_sentences(video_prompt)
            motion_sentence = sentences[1] if len(sentences) >= 2 else video_prompt
            violation = self._check_materiality_violation(motion_sentence, material_class)
            if violation:
                reasons.append(violation)
        return reasons

    def _build_video_retry_prompt(
        self,
        filled_prompt: str,
        *,
        previous_output: str,
        rejection_reasons: list[str],
    ) -> str:
        corrections = []
        for reason in rejection_reasons:
            if reason.startswith('deep_architectural_language:'):
                term = reason.split(':', 1)[1]
                corrections.append(
                    f'Your output contains "{term}" and the scene reads too much like built architecture. '
                    'Rewrite DEEP as natural geological enclosure — buried recess, overhead rock mass, '
                    'light entering laterally from a crack or seam in the surrounding walls. '
                    'Steer away from explicit built-interior wording and keep the scene feeling geological.'
                )
            elif reason.startswith('deep_overhead_aperture:'):
                term = reason.split(':', 1)[1]
                corrections.append(
                    f'Your output contains "{term}" and implies an opening above the scene. '
                    'DEEP must not show a centered overhead opening, circular hole, skylight, or vertical shaft of light descending from above. '
                    'Keep the overhead mass solid and enclosed. '
                    'Move the light source to a lateral crack, wall seam, translucent mineral face, or diffuse side-entry.'
                )
            elif reason.startswith('abyss_bright_opening:'):
                term = reason.split(':', 1)[1]
                corrections.append(
                    f'Your output contains "{term}" and introduces an opening, exit, horizon, or bright upper zone. '
                    'ABYSS must not show a cave mouth, skylight, tunnel exit, horizon line, large opening, or dominant bright zone in the upper third. '
                    'Keep the frame sealed, interior, and directionless with no readable way out above.'
                )
            elif reason == 'low_recovery_sentence_two_no_failure_event':
                corrections.append(
                    'Sentence 2 contains no legible physical failure event. '
                    'LOW recovery needs one clear physical failure or persistent material problem specifically in sentence 2. '
                    'Keep it scene-specific and visible in the world itself. '
                    'Mood and atmosphere are not substitutes — the viewer should be able to tell what is physically wrong.'
                )
            elif reason.startswith('materiality_violation_atmospheric:'):
                term = reason.split(':', 1)[1]
                corrections.append(
                    f'Your output used "{term}" — a solid-matter failure term — in an atmospheric environment. '
                    'Clouds, vapor, wind, and fog do not crack, fracture, shatter, or produce debris. '
                    'Rewrite sentence 2 so that failure happens through weather physics: '
                    'pressure fronts, shear, turbulence, compression, cloud-bank collapse, '
                    'density drop, visibility suppression, directional bands, or storm-thickened air. '
                    'Keep the same intensity — just make the physics correct for atmosphere.'
                )
            elif reason.startswith('materiality_violation_fluid:'):
                term = reason.split(':', 1)[1]
                corrections.append(
                    f'Your output used "{term}" — a solid-matter failure term — in a fluid/underwater environment. '
                    'Water, sediment, and current do not crack or fracture. '
                    'Rewrite sentence 2 so failure uses fluid physics: surge, churn, billowing sediment, '
                    'undertow, pressure displacement, or obscuring cloud of disturbed matter. '
                    'Solid failure terms are only acceptable when a named rock, reef, or formation is '
                    'explicitly the object that is failing.'
                )
        correction_text = '\n'.join(f'- {c}' for c in corrections)
        return (
            f"{filled_prompt}\n\n"
            "---\n\n"
            "## Correction Required\n\n"
            "Your previous video prompt violated one or more rules:\n"
            f"{correction_text}\n\n"
            "Rewrite the prompt correcting these issues. Three sentences only. Raw prompt text.\n\n"
            "Previous response:\n"
            f"{previous_output.strip()}\n"
        )

    def build_video_prompt(self, daily_data: dict, environment: str, blend_option: str) -> str:
        """Build video animation prompt — scene continuation, no creature reference"""
        template = self.load_template('video')
        environment_name = environment.split('—')[0].strip() if '—' in environment else environment.strip()
        behavior = daily_data.get('behavior_matrix', {})
        depth_level = daily_data.get('depth_level', '')
        recovery_zone = daily_data.get('recovery_zone', '')

        material_class = self.ENVIRONMENT_MATERIAL_CLASS.get(environment_name, 'solid')

        placeholders = {
            'environment': environment_name,
            'depth_level': depth_level,
            'energy_zone': daily_data.get('energy_zone', 'Unknown'),
            'recovery_zone': recovery_zone,
            'body_keywords': ', '.join(behavior.get('body_keywords', [])),
            'art_keywords': ', '.join(behavior.get('art_keywords', [])),
            'one_liner': behavior.get('one_liner', ''),
            'moon_count': str(daily_data.get('moon_count', 0)),
            'blend_option': blend_option,
            'material_class': material_class,
        }

        filled_prompt = self.fill_template(template, placeholders, template_name='video')
        self.save_output('last_prompt_video.txt', filled_prompt)
        prompt_to_send = filled_prompt
        video_prompt = ''
        last_rejection_reasons: list[str] = []
        for attempt in range(3):
            video_prompt = self.call_llm(prompt_to_send)
            rejection_reasons = self._validate_video_prompt(video_prompt, depth_level, recovery_zone, material_class)
            if not rejection_reasons:
                self.save_output('video_prompt.txt', video_prompt)
                return video_prompt

            last_rejection_reasons = rejection_reasons

            if attempt == 2:
                break

            retry_prompt = self._build_video_retry_prompt(
                filled_prompt,
                previous_output=video_prompt,
                rejection_reasons=rejection_reasons,
            )
            suffix = '' if attempt == 0 else f'_{attempt + 1}'
            self.save_output(f'last_prompt_video_retry{suffix}.txt', retry_prompt)
            prompt_to_send = retry_prompt

        reason_text = ', '.join(last_rejection_reasons) if last_rejection_reasons else 'unknown_validation_failure'
        raise RuntimeError(
            'Video prompt validation failed after 3 attempts. '
            f'Reasons: {reason_text}. Last output: {video_prompt.strip()}'
        )


def main():
    parser = argparse.ArgumentParser(description="WHOOP Pipeline LLM Orchestrator")
    parser.add_argument('--step', choices=['interpretation', 'creature', 'environment', 'json', 'metadata', 'video', 'all'], help="Which prompt step to run", default='all')
    parser.add_argument('--data', help="Path to daily_data.json relative to project root")
    args = parser.parse_args()

    api_key = os.getenv('GOOGLE_API_KEY_PRIMARY', 'mock')
    or_key = os.getenv('OPENROUTER_API_KEY')
    persist_environment_history = env_bool('PIPELINE_PERSIST_ENVIRONMENT_HISTORY', default=False)
    print(f"🔍 DEBUG: GOOGLE_API_KEY_PRIMARY={'set' if api_key and api_key != 'mock' else api_key}")
    print(f"🔍 DEBUG: OPENROUTER_API_KEY={'set' if or_key else 'NOT SET'}")
    orchestrator = PromptOrchestrator(
        llm_api_key=api_key,
        openrouter_api_key=or_key  # Explicitly pass to ensure subprocess can find it
    )

    if args.data:
        data_path = Path(args.data)
        if not data_path.is_absolute():
            data_path = get_project_root() / data_path
        if not data_path.exists():
            raise FileNotFoundError(f"Specified daily_data.json not found: {data_path}")
    else:
        run_date = os.getenv('PIPELINE_DATE')
        output_root = get_output_root()
        if run_date:
            data_path = output_root / run_date / 'daily_data.json'
        else:
            data_path = output_root / 'daily_data.json'

        if not data_path.exists():
            # Fallback to root output dir only when --data is omitted.
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
        environment = orchestrator.generate_environment(
            daily_data,
            interpretation,
            persist_environment_history=persist_environment_history,
        )
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
