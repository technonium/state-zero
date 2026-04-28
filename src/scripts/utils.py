import os
import posixpath
from ipaddress import ip_address
from datetime import date as date_cls, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv


VALID_MEDIA_MODES = ("local_test", "live_vps")
SUPPORTED_INSTAGRAM_PUBLISH_STRATEGIES = frozenset({"resumable_binary", "video_url", "auto"})
PROJECT_ROOT_SENTINELS = (".git", "requirements.txt", "Dockerfile")
DEFAULT_PIPELINE_TIMEZONE = "Asia/Kolkata"
LOCALHOST_HOSTS = {"localhost", "127.0.0.1", "::1"}
LOCALISH_HOST_ALIASES = {
    "host.docker.internal",
    "gateway.docker.internal",
    "docker.for.mac.localhost",
    "docker.for.win.localhost",
}
LOCAL_PATH_PREFIXES = (
    "/users/",
    "/home/",
    "/tmp/",
    "/var/tmp/",
    "/private/tmp/",
    "/run/user/",
)


def env_bool(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def resolve_instagram_publish_strategy(strategy: str | None = None) -> str:
    """Resolve Instagram publish strategy with a safe production default."""
    resolved = (strategy or os.getenv("INSTAGRAM_PUBLISH_STRATEGY") or "resumable_binary").strip().lower()
    if resolved not in SUPPORTED_INSTAGRAM_PUBLISH_STRATEGIES:
        return "resumable_binary"
    return resolved


def get_project_root() -> Path:
    """
    Return the repository root by walking upward to a stable project sentinel.
    """
    def is_project_root(path: Path) -> bool:
        return any((path / sentinel).exists() for sentinel in PROJECT_ROOT_SENTINELS)

    def walk_up(start: Path):
        current = start.resolve()
        while True:
            yield current
            if current == current.parent:
                break
            current = current.parent

    current_file = Path(__file__).resolve()
    for candidate in walk_up(current_file.parent):
        if is_project_root(candidate):
            return candidate

    for candidate in walk_up(Path.cwd()):
        if is_project_root(candidate):
            return candidate

    return current_file.parents[2]

def get_private_root() -> Path:
    """
    Resolve the private storage root.

    Resolution order:
    1. STATE_ZERO_PRIVATE_ROOT env var
    2. Existing sibling folder named "<project>-private"
    3. Existing sibling folder named "<project> Private"
    4. Default sibling folder path using the current hyphenated layout
    """
    configured = os.getenv("STATE_ZERO_PRIVATE_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser()

    project_root = get_project_root()
    sibling_candidates = (
        project_root.parent / f"{project_root.name}-private",
        project_root.parent / f"{project_root.name} Private",
    )

    for candidate in sibling_candidates:
        if candidate.is_dir():
            return candidate

    return sibling_candidates[0]


def get_runtime_root() -> Path:
    """Resolve the runtime root for mutable/private artifacts."""
    private_root = get_private_root()
    if private_root == get_project_root():
        return private_root
    return private_root / "runtime"


def get_astrology_root() -> Path:
    """Resolve the root that stores natal and dasha YAML files."""
    private_root = get_private_root()
    return private_root / "astrology"


def get_output_root() -> Path:
    return get_runtime_root() / "output"

def get_database_root() -> Path:
    return get_runtime_root() / "database"


def get_state_root() -> Path:
    return get_runtime_root() / "state"


def get_local_vps_root() -> Path:
    return get_runtime_root() / "local_vps"


def get_media_mode() -> str:
    """
    Resolve how public media URLs are backed.

    Resolution order:
    1. PIPELINE_MEDIA_MODE env var
    2. Infer local_test when VPS_SSH_HOST points at localhost
    3. Default to live_vps
    """
    configured = os.getenv("PIPELINE_MEDIA_MODE", "").strip().lower()
    if configured:
        return configured

    ssh_host = (os.getenv("VPS_SSH_HOST") or "").strip().lower()
    if ssh_host in {"localhost", "127.0.0.1", "::1"}:
        return "local_test"

    return "live_vps"


def get_pipeline_timezone_name() -> str:
    configured = os.getenv("PIPELINE_TIMEZONE", "").strip()
    return configured or DEFAULT_PIPELINE_TIMEZONE


def get_pipeline_timezone() -> ZoneInfo:
    return ZoneInfo(get_pipeline_timezone_name())


def get_pipeline_now() -> datetime:
    return datetime.now(get_pipeline_timezone())


def get_pipeline_today():
    return get_pipeline_now().date()


def get_pipeline_run_date_str() -> str:
    configured = os.getenv("PIPELINE_DATE", "").strip()
    if configured:
        return configured
    return get_pipeline_today().isoformat()


def _coerce_pipeline_now(now: datetime | None = None) -> datetime:
    tz = get_pipeline_timezone()
    if now is None:
        return datetime.now(tz)
    if now.tzinfo is None:
        return now.replace(tzinfo=tz)
    return now.astimezone(tz)


def get_pipeline_deadline(now: datetime | None = None) -> tuple[datetime, str]:
    current_time = _coerce_pipeline_now(now)
    effective_deadline = (os.getenv("PIPELINE_EFFECTIVE_DEADLINE_ISO") or "").strip()
    if effective_deadline:
        parsed_deadline = datetime.fromisoformat(effective_deadline)
        if parsed_deadline.tzinfo is None:
            parsed_deadline = parsed_deadline.replace(tzinfo=current_time.tzinfo)
        else:
            parsed_deadline = parsed_deadline.astimezone(current_time.tzinfo)
        reason = (os.getenv("PIPELINE_EFFECTIVE_DEADLINE_REASON") or "effective_deadline_override").strip()
        return parsed_deadline, reason

    deadline_mode = (os.getenv("PIPELINE_MANUAL_DEADLINE_MODE") or "run_date").strip().lower()

    if deadline_mode == "from_now":
        try:
            minutes = int((os.getenv("PIPELINE_MANUAL_WINDOW_MINUTES") or "120").strip())
        except ValueError:
            minutes = 120
        minutes = max(1, minutes)
        return current_time + timedelta(minutes=minutes), f"from_now(+{minutes}m)"

    configured_run_date = (os.getenv("PIPELINE_DATE") or "").strip()
    if configured_run_date:
        try:
            run_day = date_cls.fromisoformat(configured_run_date)
        except ValueError:
            run_day = current_time.date()
    else:
        run_day = current_time.date()

    deadline_raw = (os.getenv("PIPELINE_MANUAL_DEADLINE_LOCAL") or "14:00").strip() or "14:00"
    try:
        hour_str, minute_str = deadline_raw.split(":", 1)
        hour = int(hour_str)
        minute = int(minute_str)
    except Exception:
        deadline_raw = "14:00"
        hour = 14
        minute = 0

    deadline = datetime(run_day.year, run_day.month, run_day.day, hour, minute, tzinfo=current_time.tzinfo)
    return deadline, f"run_date({deadline_raw})"


def is_terminal_rescue_run(now: datetime | None = None, *, deadline: datetime | None = None) -> bool:
    if env_bool("PIPELINE_TERMINAL_RESCUE_RUN", default=False):
        return True
    current_time = _coerce_pipeline_now(now)
    resolved_deadline = deadline
    if resolved_deadline is None:
        resolved_deadline, _reason = get_pipeline_deadline(now=current_time)
    elif resolved_deadline.tzinfo is None:
        resolved_deadline = resolved_deadline.replace(tzinfo=current_time.tzinfo)
    else:
        resolved_deadline = resolved_deadline.astimezone(current_time.tzinfo)
    return current_time >= resolved_deadline


def load_project_dotenv(*, override: bool = False) -> bool:
    """Load the repo .env file without clobbering real process env by default."""
    return load_dotenv(dotenv_path=get_project_root() / ".env", override=override)


def is_localhost_host(host: str | None) -> bool:
    normalized = (host or "").strip().lower()
    if normalized in LOCALHOST_HOSTS or normalized in LOCALISH_HOST_ALIASES:
        return True
    try:
        parsed = ip_address(normalized)
    except ValueError:
        return normalized.startswith("localhost.")
    return parsed.is_loopback


def is_invalid_live_vps_path(path: str | None) -> bool:
    normalized = (path or "").strip().replace("\\", "/")
    if not normalized:
        return False
    if not posixpath.isabs(normalized):
        return True
    lowered = normalized.lower()
    if lowered in {"/tmp", "/var/tmp", "/private/tmp"}:
        return True
    return lowered.startswith(LOCAL_PATH_PREFIXES)


def get_live_vps_config_error() -> str | None:
    """
    Return a human-readable configuration error for dangerous live_vps settings.

    These guards intentionally only apply when the effective media mode is live_vps.
    """
    if get_media_mode() != "live_vps":
        return None

    ssh_host = (os.getenv("VPS_SSH_HOST") or "").strip()
    ssh_path = (os.getenv("VPS_SSH_PATH") or "").strip()

    if is_localhost_host(ssh_host):
        return (
            "Invalid live_vps configuration: VPS_SSH_HOST resolves to localhost. "
            "Production live_vps uploads must target the real VPS host."
        )

    normalized_path = ssh_path.replace("\\", "/")
    if is_invalid_live_vps_path(normalized_path):
        return (
            "Invalid live_vps configuration: VPS_SSH_PATH must be an absolute remote server path, "
            "not a local user/scratch/relative path."
        )

    return None


def resolve_path(relative_path: str) -> Path:
    """Resolve a safe/public path relative to the project root."""
    return get_project_root() / relative_path


def ensure_dir(relative_path: str) -> Path:
    """Ensure a safe/public directory exists relative to the project root."""
    path = resolve_path(relative_path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def ensure_path(path: Path) -> Path:
    """Ensure an arbitrary directory exists."""
    path.mkdir(parents=True, exist_ok=True)
    return path
