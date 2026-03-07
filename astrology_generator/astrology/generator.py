from __future__ import annotations

import argparse
import shutil
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_SCRIPTS = PROJECT_ROOT / "src" / "scripts"
if SRC_SCRIPTS.exists() and str(SRC_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SRC_SCRIPTS))

try:  # pragma: no cover - convenience import for standalone repo usage
    from utils import get_astrology_root
except Exception:  # pragma: no cover
    get_astrology_root = None

from astrology.provider import ProviderError, VedicRishiExposedProvider
from astrology.vimshottari import (
    OUTPUT_PLANET_ORDER,
    compute_dignity,
    compute_prana_periods,
    compute_whole_sign_house,
    format_output_datetime,
    parse_vr_datetime,
)


@dataclass
class BirthProfile:
    birth_date: date
    birth_time: time
    timezone_offset: float
    birthplace: str
    latitude: float
    longitude: float
    dasha_start_date: date
    dasha_end_date: date
    output_dir: Path
    country: str = ""
    language: str = "english"
    validate: bool = False
    sample_count: int = 12


def detect_default_output_dir() -> Path:
    if get_astrology_root is not None:
        try:
            return get_astrology_root()
        except Exception:
            pass
    return Path.cwd()


def parse_iso_date(value: str) -> date:
    return datetime.strptime(value.strip(), "%Y-%m-%d").date()


def parse_clock_time(value: str) -> time:
    return datetime.strptime(value.strip(), "%H:%M").time()


def prompt_value(label: str, parser, current=None, default=None):
    if current is not None:
        return parser(current)

    while True:
        suffix = f" [{default}]" if default is not None else ""
        raw = input(f"{label}{suffix}: ").strip()
        if not raw:
            if default is None:
                print("A value is required.")
                continue
            raw = str(default)
        try:
            return parser(raw)
        except Exception as exc:
            print(f"Invalid value: {exc}")


def prompt_text(label: str, current=None, default=None) -> str:
    return prompt_value(label, lambda x: x, current=current, default=default)


def infer_country(birthplace: str, explicit: str) -> str:
    if explicit:
        return explicit
    if "," in birthplace:
        return birthplace.split(",")[-1].strip()
    return "India" if birthplace else "India"


def build_user_data(profile: BirthProfile) -> dict[str, object]:
    country = infer_country(profile.birthplace, profile.country)
    birthplace = profile.birthplace or country
    return {
        "nameu": "User",
        "birth": birthplace,
        "day": profile.birth_date.day,
        "month": profile.birth_date.month,
        "year": profile.birth_date.year,
        "min": profile.birth_time.minute,
        "hour": profile.birth_time.hour,
        "tzone": profile.timezone_offset,
        "lat": str(profile.latitude),
        "lon": str(profile.longitude),
        "name": birthplace,
        "country": country,
        "language": profile.language,
    }


def build_natal_yaml(kp_details: dict[str, object]) -> dict[str, object]:
    response = kp_details
    basic = response.get("basic_details", {})
    planets = response.get("planets", [])

    ascendant = basic["ascendant"]
    moon_nakshatra = basic["nakshatra"]

    planet_map: dict[str, dict[str, object]] = {}
    for planet_name in OUTPUT_PLANET_ORDER:
        api_planet = next(
            (entry for entry in planets if entry.get("planet_name") == planet_name),
            None,
        )
        if api_planet is None:
            raise ValueError(f"Missing planet '{planet_name}' in KP details response")

        sign_name = api_planet["sign_name"]
        planet_map[planet_name] = {
            "sign": sign_name,
            "house": compute_whole_sign_house(ascendant, sign_name),
            "dignity": compute_dignity(planet_name, sign_name),
        }

    return {
        "natal": {
            "ascendant": ascendant,
            "moon_nakshatra": moon_nakshatra,
            "planets": planet_map,
        }
    }


def fetch_pratyantar_boundaries(
    provider: VedicRishiExposedProvider,
    user_data: dict[str, object],
    start_date: date,
    end_date: date,
) -> list[dict[str, object]]:
    pratyantars: list[dict[str, object]] = []
    current_probe = start_date

    while current_probe <= end_date:
        response = provider.fetch_current_vdasha(user_data, current_probe)
        major = response["major"]
        minor = response["minor"]
        sub_minor = response["sub_minor"]

        prat_start = parse_vr_datetime(sub_minor["start"])
        prat_end = parse_vr_datetime(sub_minor["end"])

        entry = {
            "maha": major["planet"],
            "antar": minor["planet"],
            "pratyantar": sub_minor["planet"],
            "prat_start": prat_start,
            "prat_end": prat_end,
        }

        if not pratyantars or pratyantars[-1]["prat_start"] != prat_start:
            pratyantars.append(entry)

        current_probe = (prat_end + timedelta(days=1, hours=12)).date()

    return pratyantars


def extract_api_prana_snapshot(response: dict[str, object]) -> dict[str, object]:
    return {
        "maha": response["major"]["planet"],
        "antar": response["minor"]["planet"],
        "pratyantar": response["sub_minor"]["planet"],
        "sookshma": response["sub_sub_minor"]["planet"],
        "prana": response["sub_sub_sub_minor"]["planet"],
        "start": parse_vr_datetime(response["sub_sub_sub_minor"]["start"]),
        "end": parse_vr_datetime(response["sub_sub_sub_minor"]["end"]),
    }


def validate_generated_periods(
    provider: VedicRishiExposedProvider,
    user_data: dict[str, object],
    periods: list[dict[str, str]],
    start_date: date,
    end_date: date,
    sample_count: int,
) -> dict[str, object]:
    if not periods:
        return {"checked": 0, "matched": 0, "mismatches": []}

    total_days = max((end_date - start_date).days, 0)
    sample_count = max(1, sample_count)

    sampled_dates: list[date] = []
    if total_days == 0:
        sampled_dates.append(start_date)
    else:
        step = total_days / max(sample_count - 1, 1)
        for idx in range(sample_count):
            sampled_dates.append(start_date + timedelta(days=round(step * idx)))

    deduped_dates: list[date] = []
    seen = set()
    for sample_date in sampled_dates:
        if sample_date not in seen:
            seen.add(sample_date)
            deduped_dates.append(sample_date)

    mismatches: list[str] = []
    matched = 0
    for sample_date in deduped_dates:
        snapshot = extract_api_prana_snapshot(provider.fetch_current_vdasha(user_data, sample_date))
        found = False
        for period in periods:
            if (
                period["maha"] == snapshot["maha"]
                and period["antar"] == snapshot["antar"]
                and period["pratyantar"] == snapshot["pratyantar"]
                and period["sookshma"] == snapshot["sookshma"]
                and period["prana"] == snapshot["prana"]
            ):
                start_diff = abs(
                    (
                        datetime.strptime(period["start"], "%Y-%m-%d %H:%M") - snapshot["start"]
                    ).total_seconds()
                )
                end_diff = abs(
                    (
                        datetime.strptime(period["end"], "%Y-%m-%d %H:%M") - snapshot["end"]
                    ).total_seconds()
                )
                if start_diff <= 120 and end_diff <= 120:
                    matched += 1
                    found = True
                    break

        if not found:
            mismatches.append(
                f"{sample_date}: expected "
                f"{snapshot['maha']}/{snapshot['antar']}/{snapshot['pratyantar']}/"
                f"{snapshot['sookshma']}/{snapshot['prana']} "
                f"{format_output_datetime(snapshot['start'])} -> {format_output_datetime(snapshot['end'])}"
            )

    return {
        "checked": len(deduped_dates),
        "matched": matched,
        "mismatches": mismatches,
    }


def write_natal_yaml(data: dict[str, object], output_path: Path) -> None:
    natal = data["natal"]
    planets = natal["planets"]
    lines = [
        "natal:",
        f'  ascendant: "{natal["ascendant"]}"',
        f'  moon_nakshatra: "{natal["moon_nakshatra"]}"',
        "",
        "  planets:",
    ]
    for planet_name in OUTPUT_PLANET_ORDER:
        planet = planets[planet_name]
        lines.append(
            f'    {planet_name}: {{ sign: "{planet["sign"]}", house: {planet["house"]}, '
            f'dignity: "{planet["dignity"]}" }}'
        )
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_dasha_yaml(periods: list[dict[str, str]], output_path: Path) -> None:
    lines = ["periods:"]
    for period in periods:
        lines.extend(
            [
                f'  - start: "{period["start"]}"',
                f'    end:   "{period["end"]}"',
                f'    maha:       "{period["maha"]}"',
                f'    antar:      "{period["antar"]}"',
                f'    pratyantar: "{period["pratyantar"]}"',
                f'    sookshma:   "{period["sookshma"]}"',
                f'    prana:      "{period["prana"]}"',
            ]
        )
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def confirm_overwrite(paths: list[Path]) -> bool:
    print("These files already exist and will be overwritten:")
    for path in paths:
        print(f"  - {path}")
    answer = input("Overwrite them? Type 'yes' to continue: ").strip().lower()
    return answer == "yes"


def backup_existing_files(paths: list[Path]) -> None:
    backups_dir = paths[0].parent / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    for path in paths:
        backup_path = backups_dir / f"{path.name}.{timestamp}.bak"
        shutil.copy2(path, backup_path)
        print(f"Backed up {path} -> {backup_path}")


def prepare_output_paths(profile: BirthProfile, non_interactive: bool, overwrite_existing: bool) -> tuple[Path, Path]:
    natal_path = profile.output_dir / "natal.yaml"
    dasha_path = profile.output_dir / "dasha_periods.yaml"
    existing = [path for path in (natal_path, dasha_path) if path.exists()]
    if not existing:
        return natal_path, dasha_path

    if non_interactive and not overwrite_existing:
        raise ValueError(
            "Output files already exist. Re-run with --overwrite-existing to replace them."
        )

    if not non_interactive and not confirm_overwrite(existing):
        raise RuntimeError("Generation cancelled. Existing files were left untouched.")

    backup_existing_files(existing)
    return natal_path, dasha_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate natal.yaml and dasha_periods.yaml for any birth profile."
    )
    parser.add_argument("--birth-date", help="Birth date in YYYY-MM-DD format")
    parser.add_argument("--birth-time", help="Birth time in HH:MM 24h format")
    parser.add_argument("--timezone", type=float, help="Timezone offset, e.g. 5.5")
    parser.add_argument(
        "--birthplace",
        help="Optional internal location label. Not required for normal use.",
    )
    parser.add_argument("--latitude", type=float, help="Birth latitude")
    parser.add_argument("--longitude", type=float, help="Birth longitude")
    parser.add_argument("--country", default="", help="Optional country override")
    parser.add_argument("--language", default="english", help="API language, default english")
    parser.add_argument("--dasha-start-date", help="First date to cover in dasha YAML (YYYY-MM-DD)")
    parser.add_argument("--dasha-end-date", help="Last date to cover in dasha YAML (YYYY-MM-DD)")
    parser.add_argument("--output-dir", help="Directory where natal.yaml and dasha_periods.yaml will be written")
    parser.add_argument(
        "--provider",
        choices=("exposed",),
        default="exposed",
        help="Astrology provider backend. Only 'exposed' is implemented today.",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Sample live dates and verify generated dasha periods against the API.",
    )
    parser.add_argument(
        "--sample-count",
        type=int,
        default=12,
        help="How many dates to sample when --validate is enabled.",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Require all inputs via flags instead of prompting.",
    )
    parser.add_argument(
        "--overwrite-existing",
        action="store_true",
        help="Allow replacing existing natal.yaml or dasha_periods.yaml in the output folder.",
    )
    return parser.parse_args(argv)


def build_profile(args: argparse.Namespace) -> BirthProfile:
    today = date.today()
    try:
        default_end = today.replace(year=today.year + 10)
    except ValueError:
        default_end = today + timedelta(days=3652)
    default_output_dir = detect_default_output_dir()
    default_timezone = 5.5
    default_birthplace = "India"

    if args.non_interactive:
        birth_date = parse_iso_date(args.birth_date)
        birth_time = parse_clock_time(args.birth_time)
        timezone_offset = float(args.timezone) if args.timezone is not None else default_timezone
        birthplace = args.birthplace or default_birthplace
        latitude = float(args.latitude)
        longitude = float(args.longitude)
        dasha_start_date = (
            parse_iso_date(args.dasha_start_date) if args.dasha_start_date else today
        )
        dasha_end_date = (
            parse_iso_date(args.dasha_end_date) if args.dasha_end_date else default_end
        )
        output_dir = Path(args.output_dir).expanduser() if args.output_dir else default_output_dir
    else:
        birth_date = prompt_value("Birth date (YYYY-MM-DD)", parse_iso_date, args.birth_date)
        birth_time = prompt_value("Birth time (HH:MM)", parse_clock_time, args.birth_time)
        timezone_offset = prompt_value(
            "Timezone offset (use 5.5 for India)",
            float,
            args.timezone,
            default=default_timezone,
        )
        birthplace = args.birthplace or default_birthplace
        latitude = prompt_value(
            "Latitude (decimal format, e.g. 19.0760)",
            float,
            args.latitude,
        )
        longitude = prompt_value(
            "Longitude (decimal format, e.g. 72.8777)",
            float,
            args.longitude,
        )
        dasha_start_date = prompt_value(
            "Dasha output start date (YYYY-MM-DD)",
            parse_iso_date,
            args.dasha_start_date,
            default=today.isoformat(),
        )
        dasha_end_date = prompt_value(
            "Dasha output end date (YYYY-MM-DD)",
            parse_iso_date,
            args.dasha_end_date,
            default=default_end.isoformat(),
        )
        output_dir = Path(
            prompt_text(
                "Output folder",
                current=args.output_dir,
                default=str(default_output_dir),
            )
        ).expanduser()

    if dasha_end_date < dasha_start_date:
        raise ValueError("Dasha end date must be on or after the start date.")

    return BirthProfile(
        birth_date=birth_date,
        birth_time=birth_time,
        timezone_offset=timezone_offset,
        birthplace=birthplace,
        latitude=latitude,
        longitude=longitude,
        dasha_start_date=dasha_start_date,
        dasha_end_date=dasha_end_date,
        output_dir=output_dir,
        country=args.country,
        language=args.language,
        validate=args.validate,
        sample_count=args.sample_count,
    )


def ensure_non_interactive_requirements(args: argparse.Namespace) -> None:
    required = {
        "--birth-date": args.birth_date,
        "--birth-time": args.birth_time,
        "--latitude": args.latitude,
        "--longitude": args.longitude,
    }
    missing = [flag for flag, value in required.items() if value in (None, "")]
    if missing:
        raise ValueError(
            "Non-interactive mode requires these flags: " + ", ".join(sorted(missing))
        )


def run_generation(profile: BirthProfile, *, non_interactive: bool, overwrite_existing: bool) -> int:
    user_data = build_user_data(profile)
    profile.output_dir.mkdir(parents=True, exist_ok=True)
    natal_path, dasha_path = prepare_output_paths(
        profile,
        non_interactive=non_interactive,
        overwrite_existing=overwrite_existing,
    )

    window_start = datetime.combine(profile.dasha_start_date, time.min)
    window_end_exclusive = datetime.combine(
        profile.dasha_end_date + timedelta(days=1),
        time.min,
    )

    with VedicRishiExposedProvider() as provider:
        kp_details = provider.fetch_kp_details(user_data)
        natal_yaml = build_natal_yaml(kp_details)

        pratyantar_periods = fetch_pratyantar_boundaries(
            provider,
            user_data,
            profile.dasha_start_date,
            profile.dasha_end_date,
        )
        if not pratyantar_periods:
            raise RuntimeError("No Pratyantar boundaries were returned for the requested range.")

        dasha_periods = compute_prana_periods(
            pratyantar_periods,
            window_start=window_start,
            window_end_exclusive=window_end_exclusive,
        )
        if not dasha_periods:
            raise RuntimeError("No Prana periods were generated for the requested range.")

        write_natal_yaml(natal_yaml, natal_path)
        write_dasha_yaml(dasha_periods, dasha_path)

        print(f"Wrote {natal_path}")
        print(f"Wrote {dasha_path}")
        print(
            f"Generated {len(dasha_periods)} dasha periods from "
            f"{dasha_periods[0]['start']} to {dasha_periods[-1]['end']}"
        )

        if profile.validate:
            results = validate_generated_periods(
                provider,
                user_data,
                dasha_periods,
                profile.dasha_start_date,
                profile.dasha_end_date,
                profile.sample_count,
            )
            print(
                f"Validation checked {results['checked']} samples; "
                f"{results['matched']} matched."
            )
            if results["mismatches"]:
                print("Validation mismatches:")
                for mismatch in results["mismatches"]:
                    print(f"  - {mismatch}")
                return 1

    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.non_interactive:
            ensure_non_interactive_requirements(args)
        profile = build_profile(args)
        return run_generation(
            profile,
            non_interactive=args.non_interactive,
            overwrite_existing=args.overwrite_existing,
        )
    except (ProviderError, ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
