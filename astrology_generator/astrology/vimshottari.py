from __future__ import annotations

from datetime import datetime, timedelta

PLANET_SEQUENCE = [
    "Sun",
    "Moon",
    "Mars",
    "Rahu",
    "Jupiter",
    "Saturn",
    "Mercury",
    "Ketu",
    "Venus",
]

PLANET_YEARS = {
    "Sun": 6,
    "Moon": 10,
    "Mars": 7,
    "Rahu": 18,
    "Jupiter": 16,
    "Saturn": 19,
    "Mercury": 17,
    "Ketu": 7,
    "Venus": 20,
}

ZODIAC_SIGNS = [
    "Aries",
    "Taurus",
    "Gemini",
    "Cancer",
    "Leo",
    "Virgo",
    "Libra",
    "Scorpio",
    "Sagittarius",
    "Capricorn",
    "Aquarius",
    "Pisces",
]

SIGN_LORDS = {
    "Aries": "Mars",
    "Taurus": "Venus",
    "Gemini": "Mercury",
    "Cancer": "Moon",
    "Leo": "Sun",
    "Virgo": "Mercury",
    "Libra": "Venus",
    "Scorpio": "Mars",
    "Sagittarius": "Jupiter",
    "Capricorn": "Saturn",
    "Aquarius": "Saturn",
    "Pisces": "Jupiter",
}

OWN_SIGNS = {
    "Sun": {"Leo"},
    "Moon": {"Cancer"},
    "Mars": {"Aries", "Scorpio"},
    "Mercury": {"Gemini", "Virgo"},
    "Jupiter": {"Sagittarius", "Pisces"},
    "Venus": {"Taurus", "Libra"},
    "Saturn": {"Capricorn", "Aquarius"},
}

EXALTATION_SIGNS = {
    "Sun": "Aries",
    "Moon": "Taurus",
    "Mars": "Capricorn",
    "Mercury": "Virgo",
    "Jupiter": "Cancer",
    "Venus": "Pisces",
    "Saturn": "Libra",
}

DEBILITATION_SIGNS = {
    "Sun": "Libra",
    "Moon": "Scorpio",
    "Mars": "Cancer",
    "Mercury": "Pisces",
    "Jupiter": "Capricorn",
    "Venus": "Virgo",
    "Saturn": "Aries",
}

FRIENDS = {
    "Sun": {"Moon", "Mars", "Jupiter"},
    "Moon": {"Sun", "Mercury"},
    "Mars": {"Sun", "Moon", "Jupiter"},
    "Mercury": {"Sun", "Venus"},
    "Jupiter": {"Sun", "Moon", "Mars"},
    "Venus": {"Mercury", "Saturn"},
    "Saturn": {"Mercury", "Venus"},
}

ENEMIES = {
    "Sun": {"Venus", "Saturn"},
    "Moon": set(),
    "Mars": {"Mercury"},
    "Mercury": {"Moon"},
    "Jupiter": {"Mercury", "Venus"},
    "Venus": {"Sun", "Moon"},
    "Saturn": {"Sun", "Moon"},
}

OUTPUT_PLANET_ORDER = [
    "Sun",
    "Moon",
    "Mars",
    "Mercury",
    "Jupiter",
    "Venus",
    "Saturn",
    "Rahu",
    "Ketu",
]


def parse_vr_datetime(value: str) -> datetime:
    normalized = " ".join(value.split())
    return datetime.strptime(normalized, "%d-%m-%Y %H:%M")


def format_output_datetime(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M")


def get_sub_sequence(lord_planet: str) -> list[str]:
    idx = PLANET_SEQUENCE.index(lord_planet)
    return PLANET_SEQUENCE[idx:] + PLANET_SEQUENCE[:idx]


def compute_whole_sign_house(ascendant_sign: str, planet_sign: str) -> int:
    asc_idx = ZODIAC_SIGNS.index(ascendant_sign)
    sign_idx = ZODIAC_SIGNS.index(planet_sign)
    return ((sign_idx - asc_idx) % 12) + 1


def compute_dignity(planet_name: str, sign_name: str) -> str:
    if planet_name in {"Rahu", "Ketu"}:
        return "neutral"

    if sign_name == EXALTATION_SIGNS.get(planet_name):
        return "exalted"
    if sign_name == DEBILITATION_SIGNS.get(planet_name):
        return "debilitated"
    if sign_name in OWN_SIGNS.get(planet_name, set()):
        return "own"

    sign_lord = SIGN_LORDS[sign_name]
    if sign_lord in FRIENDS.get(planet_name, set()):
        return "friendly"
    if sign_lord in ENEMIES.get(planet_name, set()):
        return "enemy"
    return "neutral"


def overlaps_window(
    start_dt: datetime,
    end_dt: datetime,
    window_start: datetime,
    window_end_exclusive: datetime,
) -> bool:
    return end_dt > window_start and start_dt < window_end_exclusive


def compute_prana_periods(
    pratyantar_periods: list[dict[str, datetime | str]],
    window_start: datetime,
    window_end_exclusive: datetime,
) -> list[dict[str, str]]:
    all_pranas: list[dict[str, str]] = []

    for prat in pratyantar_periods:
        prat_start = prat["prat_start"]
        prat_end = prat["prat_end"]
        prat_hours = (prat_end - prat_start).total_seconds() / 3600.0
        prat_planet = prat["pratyantar"]

        sookshma_seq = get_sub_sequence(prat_planet)
        sookshma_current = prat_start

        for s_idx, sookshma_planet in enumerate(sookshma_seq):
            sookshma_hours = (PLANET_YEARS[sookshma_planet] / 120.0) * prat_hours
            if s_idx == len(sookshma_seq) - 1:
                sookshma_end = prat_end
            else:
                sookshma_end = sookshma_current + timedelta(hours=sookshma_hours)

            actual_sookshma_hours = (sookshma_end - sookshma_current).total_seconds() / 3600.0
            prana_seq = get_sub_sequence(sookshma_planet)
            prana_current = sookshma_current

            for p_idx, prana_planet in enumerate(prana_seq):
                prana_hours = (PLANET_YEARS[prana_planet] / 120.0) * actual_sookshma_hours
                if p_idx == len(prana_seq) - 1:
                    prana_end = sookshma_end
                else:
                    prana_end = prana_current + timedelta(hours=prana_hours)

                if overlaps_window(prana_current, prana_end, window_start, window_end_exclusive):
                    all_pranas.append(
                        {
                            "start": format_output_datetime(prana_current),
                            "end": format_output_datetime(prana_end),
                            "maha": prat["maha"],
                            "antar": prat["antar"],
                            "pratyantar": prat["pratyantar"],
                            "sookshma": sookshma_planet,
                            "prana": prana_planet,
                        }
                    )

                prana_current = prana_end

            sookshma_current = sookshma_end

    return all_pranas
