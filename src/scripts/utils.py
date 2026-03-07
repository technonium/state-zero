import os
from pathlib import Path


VALID_MEDIA_MODES = ("local_test", "live_vps")
PROJECT_ROOT_SENTINELS = (".git", "requirements.txt", "Dockerfile")


def env_bool(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


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
    2. Sibling folder named "<project> Private" if it exists
    3. Default sibling folder path (preferred local layout)
    """
    configured = os.getenv("STATE_ZERO_PRIVATE_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser()

    project_root = get_project_root()
    sibling_private = project_root.parent / f"{project_root.name} Private"

    if sibling_private.exists() and (
        (sibling_private / "astrology").exists()
        or (sibling_private / "runtime").exists()
    ):
        return sibling_private

    return sibling_private


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
