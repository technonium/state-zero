import json
import re


_EM_DASH_SPLIT_RE = re.compile(r"\s*[—–]\s*", re.UNICODE)
_HYPHEN_SPLIT_RE = re.compile(r"\s+-\s*", re.UNICODE)
_WHITESPACE_RE = re.compile(r"\s+")
_SURROUNDING_CHARS = "\"'`“”‘’*"
_PREFIX_PATTERNS = (
    re.compile(r"^\s*creature\s*:\s*", re.IGNORECASE),
    re.compile(r"^\s*selected\s*:\s*", re.IGNORECASE),
    re.compile(r"^\s*final\s+answer\s*:\s*", re.IGNORECASE),
    re.compile(r"^\s*i\s+(?:choose|pick|select)\s+", re.IGNORECASE),
)


def _strip_wrapping(text: str) -> str:
    cleaned = (text or "").strip()
    cleaned = re.sub(r"^[\-\*\d\.\)\s]+", "", cleaned)
    cleaned = cleaned.strip(_SURROUNDING_CHARS).strip()
    cleaned = re.sub(r"^[\-\*\d\.\)\s]+", "", cleaned)
    cleaned = cleaned.strip(_SURROUNDING_CHARS).strip()
    return cleaned


def _strip_creature_prefix(text: str) -> str:
    cleaned = _strip_wrapping(text)
    for pattern in _PREFIX_PATTERNS:
        cleaned = pattern.sub("", cleaned, count=1)
    return _strip_wrapping(cleaned)


def format_creature_output(name: str, reason: str | None = None) -> str:
    clean_name = _strip_wrapping(name).rstrip(".,:;!?")
    clean_reason = _strip_wrapping(reason or "")
    if clean_name and clean_reason:
        return f"{clean_name} — {clean_reason}"
    return clean_name


def extract_json_candidate(raw_text: str) -> str:
    stripped = (raw_text or "").strip()
    if not stripped:
        return ""

    for pattern in (r'```json\s*\n(.*?)\n```', r'```\s*\n(.*?)\n```'):
        match = re.search(pattern, stripped, re.DOTALL)
        if match:
            return match.group(1).strip()

    brace_start = stripped.find('{')
    if brace_start != -1:
        brace_count = 0
        for idx in range(brace_start, len(stripped)):
            ch = stripped[idx]
            if ch == '{':
                brace_count += 1
            elif ch == '}':
                brace_count -= 1
                if brace_count == 0:
                    return stripped[brace_start:idx + 1]

    return ""


def parse_creature_payload(raw_text: str) -> dict:
    json_candidate = extract_json_candidate(raw_text)
    if not json_candidate:
        return {}

    try:
        payload = json.loads(json_candidate)
    except json.JSONDecodeError:
        return {}

    if not isinstance(payload, dict):
        return {}

    return payload


def split_creature_output(raw_text: str) -> tuple[str, str]:
    lines = [_strip_wrapping(line) for line in (raw_text or "").splitlines() if line.strip()]
    if not lines:
        return "", ""

    first_line = _strip_creature_prefix(lines[0])

    parts = _EM_DASH_SPLIT_RE.split(first_line, maxsplit=1)
    if len(parts) != 2:
        parts = _HYPHEN_SPLIT_RE.split(first_line, maxsplit=1)
    if len(parts) == 2:
        name = _strip_wrapping(parts[0]).rstrip(".,:;!?")
        reason = _strip_wrapping(parts[1])
        return name, reason

    return _strip_wrapping(first_line).rstrip(".,:;!?"), ""


def normalize_creature_name(value: str) -> str:
    candidate = _strip_creature_prefix(value or "")
    candidate = _EM_DASH_SPLIT_RE.split(candidate, maxsplit=1)[0]
    candidate = _HYPHEN_SPLIT_RE.split(candidate, maxsplit=1)[0]
    candidate = candidate.strip(_SURROUNDING_CHARS).strip().rstrip(".,:;!?")
    candidate = _WHITESPACE_RE.sub(" ", candidate)
    return candidate.casefold()
