import re


_WHITESPACE_RE = re.compile(r"\s+")
_SURROUNDING_CHARS = "\"'`“”‘’*"


def clean_title(value: str) -> str:
    cleaned = (value or "").strip()
    while cleaned:
        next_cleaned = re.sub(r"^[\-\*\d\.\)\s]+", "", cleaned)
        next_cleaned = next_cleaned.strip(_SURROUNDING_CHARS).strip()
        next_cleaned = next_cleaned.rstrip(".,:;!?").strip()
        if next_cleaned == cleaned:
            break
        cleaned = next_cleaned
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip()
    cleaned = cleaned.rstrip(".,:;!?")
    return cleaned


def normalize_title(value: str) -> str:
    return clean_title(value).casefold()
