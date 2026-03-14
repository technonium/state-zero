import re


_DASH_SPLIT_RE = re.compile(r"\s+[—–-]\s+", re.UNICODE)
_WHITESPACE_RE = re.compile(r"\s+")
_SEARCH_NORMALIZE_RE = re.compile(r"[^a-z0-9/()]+")
_SURROUNDING_CHARS = "\"'`“”‘’*"
_CHOICE_CUE_PATTERNS = (
    re.compile(r'^\s*environment\s*:\s*(.+)$', re.IGNORECASE),
    re.compile(r'^\s*i\s+(?:choose|pick|select)\s+(.+)$', re.IGNORECASE),
    re.compile(r'^\s*selected\s*:\s*(.+)$', re.IGNORECASE),
    re.compile(r'^\s*final\s+answer\s*:\s*(.+)$', re.IGNORECASE),
)


def _strip_wrapping(text: str) -> str:
    cleaned = (text or "").strip()
    cleaned = cleaned.strip(_SURROUNDING_CHARS).strip()
    cleaned = re.sub(r"^[\-\*\d\.\)\s]+", "", cleaned)
    cleaned = cleaned.strip(_SURROUNDING_CHARS).strip()
    return cleaned


def split_environment_output(raw_text: str) -> tuple[str, str]:
    lines = [_strip_wrapping(line) for line in (raw_text or "").splitlines() if line.strip()]
    if not lines:
        return "", ""

    first_line = lines[0]
    if first_line.lower().startswith("environment:"):
        first_line = first_line.split(":", 1)[1].strip()

    parts = _DASH_SPLIT_RE.split(first_line, maxsplit=1)
    if len(parts) == 2:
        name = _strip_wrapping(parts[0]).rstrip(".,:;!?")
        reason = _strip_wrapping(parts[1])
        return name, reason

    return _strip_wrapping(first_line).rstrip(".,:;!?"), ""


def normalize_environment_name(value: str) -> str:
    candidate = _strip_wrapping(value or "")
    if candidate.lower().startswith("environment:"):
        candidate = candidate.split(":", 1)[1].strip()
    candidate = _DASH_SPLIT_RE.split(candidate, maxsplit=1)[0]
    candidate = candidate.strip(_SURROUNDING_CHARS).strip().rstrip(".,:;!?")
    candidate = _WHITESPACE_RE.sub(" ", candidate)
    return candidate.casefold()


def _normalize_for_search(value: str) -> str:
    lowered = (value or "").casefold()
    lowered = lowered.replace("—", " ").replace("–", " ")
    lowered = lowered.translate(str.maketrans("", "", "\"'`“”‘’*"))
    lowered = _SEARCH_NORMALIZE_RE.sub(" ", lowered)
    lowered = _WHITESPACE_RE.sub(" ", lowered)
    return lowered.strip()


def extract_valid_environment_name(raw_text: str, allowed_names: list[str]) -> tuple[str | None, str]:
    allowed_lookup = {
        normalize_environment_name(name): name
        for name in allowed_names
        if normalize_environment_name(name)
    }
    if not allowed_lookup:
        return None, "no_allowed_names"

    parsed_name, _reason = split_environment_output(raw_text)
    parsed_norm = normalize_environment_name(parsed_name)
    if parsed_norm in allowed_lookup:
        return allowed_lookup[parsed_norm], "parsed_name_match"

    first_line = next((line for line in (raw_text or "").splitlines() if line.strip()), "")
    normalized_first_line = _normalize_for_search(first_line)

    for pattern in _CHOICE_CUE_PATTERNS:
        match = pattern.match(first_line.strip())
        if not match:
            continue
        cue_body = match.group(1).strip()
        repaired_name, repaired_status = extract_valid_environment_name(cue_body, allowed_names)
        if repaired_name:
            return repaired_name, f"cue_{repaired_status}"

    matched_candidates = []
    for norm_name, canonical_name in allowed_lookup.items():
        search_name = _normalize_for_search(norm_name)
        if search_name and search_name in normalized_first_line:
            matched_candidates.append(canonical_name)

    deduped_matches = []
    seen = set()
    for candidate in matched_candidates:
        candidate_norm = normalize_environment_name(candidate)
        if candidate_norm in seen:
            continue
        seen.add(candidate_norm)
        deduped_matches.append(candidate)

    if len(deduped_matches) == 1:
        return deduped_matches[0], "substring_match"
    if len(deduped_matches) > 1:
        return None, "ambiguous_multi_match"

    return None, "no_match"


def format_environment_output(name: str, reason: str | None = None) -> str:
    clean_name = (name or "").strip()
    clean_reason = (reason or "").strip()
    if clean_name and clean_reason:
        return f"{clean_name} — {clean_reason}"
    return clean_name


def select_least_recent_candidate(allowed_names: list[str], recent_names_desc: list[str]) -> str:
    if not allowed_names:
        return ""

    recent_index = {}
    for idx, name in enumerate(recent_names_desc):
        norm_name = normalize_environment_name(name)
        if not norm_name or norm_name in recent_index:
            continue
        recent_index[norm_name] = idx

    scored = []
    missing_rank = len(recent_names_desc)
    for original_idx, name in enumerate(allowed_names):
        norm = normalize_environment_name(name)
        recency_score = recent_index.get(norm, missing_rank)
        scored.append((recency_score, -original_idx, name))

    scored.sort(reverse=True)
    return scored[0][2]
