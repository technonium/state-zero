import os
import json
import yaml
from datetime import datetime, timedelta, date
from pathlib import Path
import argparse
import sys
import asyncio
import logging
import tempfile
import httpx
from whoop_client import WHOOPClient, WhoopAPIError
from utils import (
    get_astrology_root,
    get_output_root,
    get_pipeline_run_date_str,
)

logger = logging.getLogger(__name__)

MILLISECONDS_PER_HOUR = 3_600_000

ZODIAC_SIGNS = [
    'Aries', 'Taurus', 'Gemini', 'Cancer', 'Leo', 'Virgo',
    'Libra', 'Scorpio', 'Sagittarius', 'Capricorn', 'Aquarius', 'Pisces'
]
SIGN_LORDS = {
    'Aries': 'Mars', 'Taurus': 'Venus', 'Gemini': 'Mercury',
    'Cancer': 'Moon', 'Leo': 'Sun', 'Virgo': 'Mercury',
    'Libra': 'Venus', 'Scorpio': 'Mars', 'Sagittarius': 'Jupiter',
    'Capricorn': 'Saturn', 'Aquarius': 'Saturn', 'Pisces': 'Jupiter'
}
SPECIAL_ASPECT_OFFSETS = {
    'Mars': [4, 8], 'Jupiter': [5, 9], 'Saturn': [3, 10],
}


class WhoopRecoveryNotReady(Exception):
    """Raised when today's WHOOP recovery entry has not landed yet."""


class RetryableLookupFailure(Exception):
    """Raised for transient WHOOP or external lookup failures that should retry until rescue."""


LOOKUP_EXIT_WHOOP_NOT_READY = 2
LOOKUP_EXIT_RETRYABLE_EXTERNAL_FAILURE = 3
LOOKUP_EXIT_TERMINAL_FAILURE = 4


def _write_json_atomic(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            'w',
            encoding='utf-8',
            dir=path.parent,
            delete=False,
        ) as handle:
            json.dump(payload, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
            tmp_path = handle.name
        os.replace(tmp_path, path)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def _safe_ms(value) -> float:
    """Best-effort conversion of WHOOP millisecond fields to float milliseconds."""
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def derive_sleep_hours(sleep_data: dict) -> float:
    """
    Derive sleep duration in hours from WHOOP sleep payload.

    Fallback order:
    1) score.total_sleep_time_milli
    2) score.stage_summary total_in_bed - awake - no_data
    3) score.stage_summary light + slow_wave + rem
    4) end - start timestamps
    """
    score = sleep_data.get("score", {}) if isinstance(sleep_data, dict) else {}

    total_sleep_ms = _safe_ms(score.get("total_sleep_time_milli"))
    if total_sleep_ms > 0:
        return total_sleep_ms / MILLISECONDS_PER_HOUR

    stage = score.get("stage_summary", {}) if isinstance(score, dict) else {}

    in_bed_ms = _safe_ms(stage.get("total_in_bed_time_milli"))
    awake_ms = _safe_ms(stage.get("total_awake_time_milli"))
    no_data_ms = _safe_ms(stage.get("total_no_data_time_milli"))
    if in_bed_ms > 0:
        derived_ms = max(0.0, in_bed_ms - awake_ms - no_data_ms)
        if derived_ms > 0:
            logger.info("Sleep duration fallback used: stage_summary in_bed-awake-no_data")
            return derived_ms / MILLISECONDS_PER_HOUR

    light_ms = _safe_ms(stage.get("total_light_sleep_time_milli"))
    slow_wave_ms = _safe_ms(stage.get("total_slow_wave_sleep_time_milli"))
    rem_ms = _safe_ms(stage.get("total_rem_sleep_time_milli"))
    stage_sum_ms = light_ms + slow_wave_ms + rem_ms
    if stage_sum_ms > 0:
        logger.info("Sleep duration fallback used: stage_summary stage sum")
        return stage_sum_ms / MILLISECONDS_PER_HOUR

    start_raw = sleep_data.get("start")
    end_raw = sleep_data.get("end")
    if start_raw and end_raw:
        try:
            start_dt = datetime.fromisoformat(str(start_raw).replace("Z", "+00:00"))
            end_dt = datetime.fromisoformat(str(end_raw).replace("Z", "+00:00"))
            delta_ms = max(0.0, (end_dt - start_dt).total_seconds() * 1000.0)
            if delta_ms > 0:
                logger.warning("Sleep duration fallback used: end-start timestamp delta")
                return delta_ms / MILLISECONDS_PER_HOUR
        except ValueError:
            logger.warning("Could not parse sleep start/end timestamps for duration derivation")

    logger.warning("Could not derive sleep duration from WHOOP payload; defaulting to 0.0h")
    return 0.0

def get_energy_zone(strain: float) -> str:
    """Strain → Energy Zone"""
    if strain >= 14:
        return "HIGH"
    elif strain >= 9:
        return "MEDIUM"
    else:
        return "LOW"

def get_recovery_zone(recovery_pct: int) -> str:
    """Recovery % → Recovery Zone"""
    if recovery_pct >= 76:
        return "HIGH"
    elif recovery_pct >= 55:
        return "MID"
    else:
        return "LOW"

def get_sleep_score_zone(sleep_score_pct: int) -> str:
    """Sleep Score % → Sleep Zone (dual role: depth + behavioral)"""
    if sleep_score_pct >= 84:
        return "SURFACE"
    elif sleep_score_pct >= 78:
        return "MID-DEPTH"
    elif sleep_score_pct >= 72:
        return "DEEP"
    else:
        return "ABYSS"

def get_moon_count(sleep_hours: float) -> int:
    """Sleep Hours → Moon Count"""
    if sleep_hours >= 7.5:
        return 3
    elif sleep_hours >= 6.0:
        return 2
    else:
        return 1

def get_depth_keywords(sleep_zone: str) -> list:
    """Sleep Zone → Depth Keywords (SPATIAL only)"""
    mapping = {
        "SURFACE": ["Celestial", "Elevated", "Bright", "Open"],
        "MID-DEPTH": ["Beneath", "Overhang", "Partial-sky", "One-direction-light"],
        "DEEP": ["Chamber", "Ceiling-visible", "Shaft-light", "Distant-opening"],
        "ABYSS": ["Sealed", "Compression-fractures", "Interior-pressure", "No-above"]
    }
    return mapping.get(sleep_zone, [])

def get_visibility_range(sleep_zone: str) -> str:
    """Sleep Zone → Visibility Range"""
    mapping = {
        "SURFACE": "70-100%",
        "MID-DEPTH": "50-70%",
        "DEEP": "40-50%",
        "ABYSS": "40%"
    }
    return mapping.get(sleep_zone, "40%")

BEHAVIOR_MATRIX = {
    ("HIGH", "SURFACE"): {
        "body_keywords": ["Sharp", "restored", "charged"],
        "art_keywords": ["Luminous", "expansive", "serene"],
        "one_liner": "Peak state — wide open landscape, nothing blocking the horizon, everything exactly where it should be"
    },
    ("HIGH", "MID-DEPTH"): {
        "body_keywords": ["Solid", "warm", "capable"],
        "art_keywords": ["Flowing", "balanced", "harmonious"],
        "one_liner": "Well recovered with slight residual weight — moves smoothly, depth visible but unthreatening"
    },
    ("HIGH", "DEEP"): {
        "body_keywords": ["Quiet", "functional", "unhurried"],
        "art_keywords": ["Still", "subdued", "restrained"],
        "one_liner": "Body healed but sleep was thin — capable but dimmer, nothing urgent pressing through"
    },
    ("HIGH", "ABYSS"): {
        "body_keywords": ["Stable", "disconnected", "autopilot"],
        "art_keywords": ["Suspended", "stark", "vacant"],
        "one_liner": "Body fully restored, presence didn't follow — everything intact, nothing inhabited"
    },
    ("MID", "SURFACE"): {
        "body_keywords": ["Functional", "understated", "incomplete"],
        "art_keywords": ["Measured", "subdued", "indifferent"],
        "one_liner": "Slept well, body didn't fully follow — functional and present, but the gap between rest and readiness is quietly there"
    },
    ("MID", "MID-DEPTH"): {
        "body_keywords": ["Passive", "coasting", "carrying weight"],
        "art_keywords": ["Drifting", "muted", "burdened"],
        "one_liner": "Going through the motions with a slight drag — coasting, but the body adds a small tax to every step"
    },
    ("MID", "DEEP"): {
        "body_keywords": ["Slow", "foggy", "resistant"],
        "art_keywords": ["Heavy", "dim", "pressured"],
        "one_liner": "Everything costs slightly more than it should — atmosphere pressing inward, low visibility, small effort for small return"
    },
    ("MID", "ABYSS"): {
        "body_keywords": ["Hollow", "grinding", "close to breaking"],
        "art_keywords": ["Fractured", "turbulent", "consuming"],
        "one_liner": "Both the body and the night failed — hollow at the center, grinding without traction, the surface holds but nothing beneath it does"
    },
    ("LOW", "SURFACE"): {
        "body_keywords": ["Tense", "wired", "fraying"],
        "art_keywords": ["Taut", "brittle", "unstable"],
        "one_liner": "Yesterday's strain held through the night — sleep arrived but the tension didn't release, still wired and stretched past comfortable"
    },
    ("LOW", "MID-DEPTH"): {
        "body_keywords": ["Drained", "numb", "fading"],
        "art_keywords": ["Sinking", "stripped", "oppressive"],
        "one_liner": "Both metrics pulling down. Bare. No colour, no energy. Just form getting through"
    },
    ("LOW", "DEEP"): {
        "body_keywords": ["Wrecked", "shutdown", "leaden"],
        "art_keywords": ["Collapsed", "smoldering", "suffocating"],
        "one_liner": "Day after the damage — post-event silence, everything cooling into wreckage and ash"
    },
    ("LOW", "ABYSS"): {
        "body_keywords": ["Destroyed", "void", "primal"],
        "art_keywords": ["Crushing", "devastated", "primordial"],
        "one_liner": "Complete system failure. Nothing left. The landscape is what remains after everything already collapsed"
    }
}

def get_behavior_state(recovery_zone: str, sleep_zone: str) -> dict:
    """Recovery Zone × Sleep Zone → Behavior State"""
    return BEHAVIOR_MATRIX.get((recovery_zone, sleep_zone), {
        "body_keywords": ["Unknown"],
        "art_keywords": ["Unknown"],
        "one_liner": "Unknown state"
    })

def lookup_dasha(target_date: date, base_dir: Path = None) -> dict:
    """Look up dasha period for target date from YAML file"""
    dasha_path = get_astrology_root() / 'dasha_periods.yaml'
    with open(dasha_path) as f:
        data = yaml.safe_load(f)
        periods = data.get('periods', [])

    for entry in periods:
        # Expected format: "2025-12-28 15:23", converting strings to date objects for comparison
        start_str = entry['start'].split(' ')[0]
        end_str = entry['end'].split(' ')[0]
        
        start = datetime.strptime(start_str, "%Y-%m-%d").date()
        end = datetime.strptime(end_str, "%Y-%m-%d").date()
        
        if start <= target_date <= end:
            return {
                'maha': entry['maha'],
                'antar': entry['antar'],
                'pratyantar': entry['pratyantar'],
                'sookshma': entry['sookshma'],
                'prana': entry['prana']
            }

    if target_date < datetime.strptime(periods[0]['start'].split(' ')[0], "%Y-%m-%d").date():
        logger.warning(f"Target date {target_date} is before Dasha records start. Falling back to earliest available.")
        entry = periods[0]
        return {
            'maha': entry['maha'],
            'antar': entry['antar'],
            'pratyantar': entry['pratyantar'],
            'sookshma': entry['sookshma'],
            'prana': entry['prana']
        }

    raise ValueError(f"No dasha period found for {target_date}")

def get_planet_details(planet_name: str, base_dir: Path = None) -> dict:
    """Get planet sign/house/dignity from natal chart"""
    natal_path = get_astrology_root() / 'natal.yaml'
    with open(natal_path) as f:
        data = yaml.safe_load(f)
        natal = data.get('natal', {})
        planets = natal.get('planets', {})

    if planet_name in planets:
        planet = planets[planet_name]
        return {
            'sign': planet['sign'],
            'house': planet['house'],
            'dignity': planet['dignity']
        }

    raise ValueError(f"Planet {planet_name} not found in natal chart")

def get_house_lordships(ascendant: str) -> dict:
    asc_idx = ZODIAC_SIGNS.index(ascendant)
    lordships = {}
    for house_num in range(1, 13):
        sign = ZODIAC_SIGNS[(asc_idx + house_num - 1) % 12]
        lordships.setdefault(SIGN_LORDS[sign], []).append(house_num)
    return lordships

def get_conjunctions(planets: dict) -> dict:
    house_map = {}
    for planet, data in planets.items():
        house_map.setdefault(data['house'], []).append(planet)
    return {h: ps for h, ps in house_map.items() if len(ps) > 1}

def get_planet_aspects(planets: dict) -> dict:
    aspects = {}
    for planet, data in planets.items():
        natal_house = data['house']
        aspected = [(natal_house - 1 + 6) % 12 + 1]  # universal 7th
        for offset in SPECIAL_ASPECT_OFFSETS.get(planet, []):
            aspected.append((natal_house - 1 + offset - 1) % 12 + 1)
        aspects[planet] = aspected
    return aspects

def build_daily_data(strain: float, recovery_pct: int, sleep_score_pct: int, sleep_hours: float, target_date: date):
    """Build complete daily_data.json structure"""
    # Compute zones
    energy_zone = get_energy_zone(strain)
    recovery_zone = get_recovery_zone(recovery_pct)
    sleep_score_zone = get_sleep_score_zone(sleep_score_pct)
    depth_level = sleep_score_zone  # Same value, dual role
    moon_count = get_moon_count(sleep_hours)

    # Lookup behavior matrix
    behavior = get_behavior_state(recovery_zone, sleep_score_zone)

    # Depth keywords and visibility
    depth_keywords = get_depth_keywords(sleep_score_zone)
    visibility_range = get_visibility_range(sleep_score_zone)

    # Read natal.yaml once
    natal_path = get_astrology_root() / 'natal.yaml'
    with open(natal_path) as f:
        natal = yaml.safe_load(f).get('natal', {})

    natal_planets = natal.get('planets', {})
    ascendant = natal['ascendant']

    # Dasha lookup
    dasha = lookup_dasha(target_date)

    # Get planet details for each dasha level (from already-loaded natal data)
    planets_detail = {}
    for planet_name in set([dasha['maha'], dasha['antar'], dasha['pratyantar'],
                             dasha['sookshma'], dasha['prana']]):
        if planet_name not in natal_planets:
            raise ValueError(f"Planet {planet_name} not found in natal chart")
        p = natal_planets[planet_name]
        planets_detail[planet_name] = {'sign': p['sign'], 'house': p['house'], 'dignity': p['dignity']}

    # Build output structure
    output = {
        "date": str(target_date),
        "date_display": target_date.strftime("%d %b %Y").upper(),

        "strain": strain,
        "recovery_pct": recovery_pct,
        "sleep_score_pct": sleep_score_pct,
        "sleep_hours": round(sleep_hours, 2),

        "energy_zone": energy_zone,
        "recovery_zone": recovery_zone,
        "sleep_score_zone": sleep_score_zone,

        "moon_count": moon_count,
        "depth_level": depth_level,
        "depth_keywords": depth_keywords,
        "visibility_range": visibility_range,

        "behavior_matrix": behavior,

        "dasha": {
            "maha": dasha['maha'],
            "antar": dasha['antar'],
            "pratyantar": dasha['pratyantar'],
            "sookshma": dasha['sookshma'],
            "prana": dasha['prana'],
            "planets_detail": planets_detail
        },

        "natal_context": {
            "ascendant": ascendant,
            "moon_nakshatra": natal['moon_nakshatra'],
            "planets": natal_planets,
            "house_lordships": get_house_lordships(ascendant),
            "conjunctions": get_conjunctions(natal_planets),
            "planet_aspects": get_planet_aspects(natal_planets),
        }
    }

    # Write to file
    # Always use target_date (from --date CLI arg) as the authoritative date for
    # the output directory. Do NOT use os.getenv('PIPELINE_DATE') here because
    # this runs as a subprocess and .env may still hold a stale previous date,
    # causing daily_data.json to be written to the wrong output/{date}/ folder.
    run_date = str(target_date)
    output_dir = get_output_root() / run_date
    _write_json_atomic(output_dir / 'daily_data.json', output)

    return output

def get_whoop_data(target_date: date = None):
    """Fetch real WHOOP data from API. Raises on any failure — no silent fallback."""
    try:
        return asyncio.run(_fetch_whoop_data(target_date))
    except WhoopAPIError as e:
        if e.status_code == 404 and "No recovery entry found" in e.message:
            print(f"⚠ [WHOOP] Recovery not ready yet: {e.message}")
            raise WhoopRecoveryNotReady(e.message) from e
        if e.status_code == 404 and (
            "No cycle data around" in e.message
            or "No strain cycle found" in e.message
            or "No primary sleep found" in e.message
        ):
            print(f"⚠ [WHOOP] Daily data still incomplete: {e.message}")
            raise RetryableLookupFailure(e.message) from e
        if e.status_code == 401:
            print(f"❌ [WHOOP] Auth error (401): {e.message}")
            print("   → Run ops/auth_whoop.py to get a fresh token.")
            raise RetryableLookupFailure(e.message) from e
        if e.status_code == 429 or e.status_code >= 500:
            print(f"⚠ [WHOOP] Transient API error ({e.status_code}): {e.message}")
            raise RetryableLookupFailure(e.message) from e
        else:
            print(f"❌ [WHOOP] API error ({e.status_code}): {e.message}")
        raise
    except (httpx.HTTPError, asyncio.TimeoutError) as e:
        print(f"⚠ [WHOOP] Network error: {e}")
        raise RetryableLookupFailure(str(e)) from e
    except Exception as e:
        print(f"❌ [WHOOP] Unexpected error: {e}")
        raise

async def _fetch_whoop_data(target_date: date = None):
    """Async function to fetch WHOOP data."""
    client = WHOOPClient()
    
    target_dt = datetime.combine(target_date, datetime.min.time()) if target_date else None
    
    # Fetch yesterday's cycle (for strain)
    cycle = await client.get_yesterday_cycle(target_dt)
    strain = cycle.get("score", {}).get("strain", 0)

    # Fetch today's recovery
    recovery_data = await client.get_today_recovery(target_dt)
    recovery_score = recovery_data.get("score", {}).get("recovery_score", 0)

    # Fetch last night's sleep
    sleep_data = await client.get_last_sleep(target_dt)
    sleep_score = sleep_data.get("score", {}).get("sleep_performance_percentage", 0)
    sleep_hours = derive_sleep_hours(sleep_data)

    return {
        "strain": strain,              # 0-21 scale
        "recovery": recovery_score,    # 0-100 percentage
        "sleep_score": sleep_score,    # 0-100 percentage
        "sleep_hours": round(sleep_hours, 1)
    }

def main():
    parser = argparse.ArgumentParser(description="Lookup and compile daily data")
    parser.add_argument('--date', help="YYYY-MM-DD format date string", default=None)
    args = parser.parse_args()

    # Default to today's date if not specified
    if args.date:
        try:
            target_date = datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            print("❌ Invalid date format. Please use YYYY-MM-DD")
            sys.exit(1)
    else:
        target_date = date.fromisoformat(get_pipeline_run_date_str())

    # REAL DATA MODE - Call WHOOP API (no mock path)
    print(f"▶ Fetching WHOOP data for {target_date}...")
    try:
        whoop_data = get_whoop_data(target_date)
    except WhoopRecoveryNotReady:
        sys.exit(LOOKUP_EXIT_WHOOP_NOT_READY)
    except RetryableLookupFailure:
        sys.exit(LOOKUP_EXIT_RETRYABLE_EXTERNAL_FAILURE)
    except Exception:
        sys.exit(LOOKUP_EXIT_TERMINAL_FAILURE)
    strain = whoop_data["strain"]
    recovery_pct = whoop_data["recovery"]
    sleep_score_pct = whoop_data["sleep_score"]
    sleep_hours = whoop_data["sleep_hours"]
    
    print(f"▶ Generating daily_data.json for {target_date}...")
    try:
        output = build_daily_data(strain, recovery_pct, sleep_score_pct, sleep_hours, target_date)
    except Exception as e:
        print(f"❌ [LOOKUPS] Terminal data assembly error: {e}")
        sys.exit(LOOKUP_EXIT_TERMINAL_FAILURE)
    print("✅ Successfully generated output/daily_data.json")
    
if __name__ == '__main__':
    main()
