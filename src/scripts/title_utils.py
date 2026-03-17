import re
from dataclasses import asdict, dataclass
from typing import Iterable


_WHITESPACE_RE = re.compile(r"\s+")
_SURROUNDING_CHARS = "\"'`“”‘’*"
_TITLE_NON_ALNUM_RE = re.compile(r"[^A-Za-z0-9]+")


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


def split_title_words(value: str) -> list[str]:
    cleaned = clean_title(value)
    if not cleaned:
        return []
    return [word for word in cleaned.split() if word]


def normalize_title_word(value: str) -> str:
    return _TITLE_NON_ALNUM_RE.sub("", clean_title(value).casefold())


def structural_title_key(value: str) -> str:
    words = split_title_words(value)
    if not words:
        return ""
    key_source = words[-1] if len(words) > 1 else words[0]
    return normalize_title_word(key_source)


def build_structural_title_keys(titles: Iterable[str]) -> list[str]:
    keys: list[str] = []
    seen: set[str] = set()
    for title in titles:
        key = structural_title_key(title)
        if not key or key in seen:
            continue
        seen.add(key)
        keys.append(key)
    return keys


@dataclass
class TitleCandidateAssessment:
    raw: str
    cleaned: str
    normalized: str
    word_count: int
    structural_key: str
    hard_rejection_reasons: list[str]
    soft_rejection_reasons: list[str]

    @property
    def is_fully_valid(self) -> bool:
        return not self.hard_rejection_reasons and not self.soft_rejection_reasons

    @property
    def is_soft_acceptable(self) -> bool:
        return not self.hard_rejection_reasons and bool(self.soft_rejection_reasons)

    def as_dict(self) -> dict:
        payload = asdict(self)
        payload["is_fully_valid"] = self.is_fully_valid
        payload["is_soft_acceptable"] = self.is_soft_acceptable
        return payload


def assess_title_candidate(
    value: str,
    *,
    banned_exact_titles: set[str],
    banned_structural_keys: set[str],
) -> TitleCandidateAssessment:
    cleaned = clean_title(value)
    words = split_title_words(cleaned)
    normalized = normalize_title(cleaned)
    structural_key = structural_title_key(cleaned)
    hard_rejection_reasons: list[str] = []
    soft_rejection_reasons: list[str] = []

    if not cleaned:
        hard_rejection_reasons.append("blank_title")
    elif len(words) == 0 or len(words) > 2:
        hard_rejection_reasons.append("invalid_word_count")

    if len(words) == 1:
        normalized_word = normalize_title_word(words[0]) if words else ""
        if normalized_word and len(normalized_word) < 8:
            hard_rejection_reasons.append("one_word_too_short")

    if normalized and normalized in banned_exact_titles:
        hard_rejection_reasons.append("exact_recent_repeat")

    if structural_key and structural_key in banned_structural_keys:
        soft_rejection_reasons.append("structural_recent_repeat")

    return TitleCandidateAssessment(
        raw=value,
        cleaned=cleaned,
        normalized=normalized,
        word_count=len(words),
        structural_key=structural_key,
        hard_rejection_reasons=hard_rejection_reasons,
        soft_rejection_reasons=soft_rejection_reasons,
    )
