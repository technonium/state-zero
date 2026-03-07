import os
import sys
import yaml
from pathlib import Path
from colorama import init, Fore, Style
from dotenv import load_dotenv
from utils import (
    get_project_root,
    get_astrology_root,
    get_output_root,
    get_database_root,
    get_runtime_root,
    get_state_root,
    get_local_vps_root,
    get_media_mode,
    VALID_MEDIA_MODES,
    env_bool,
    ensure_dir,
    ensure_path,
)

# Load .env
load_dotenv(dotenv_path=get_project_root() / '.env', override=True)

# Initialize colorama for cross-platform colored output
init()


def print_error(msg):
    print(f"{Fore.RED}❌ {msg}{Style.RESET_ALL}")

def print_warning(msg):
    print(f"{Fore.YELLOW}⚠️  {msg}{Style.RESET_ALL}")

def print_success(msg):
    print(f"{Fore.GREEN}✅ {msg}{Style.RESET_ALL}")

def print_info(msg):
    print(f"{Fore.CYAN}ℹ️  {msg}{Style.RESET_ALL}")

def _load_yaml(path, label: str):
    try:
        with open(path) as f:
            return yaml.safe_load(f)
    except Exception as e:
        print_error(f"Failed to parse {label}: {e}")
        sys.exit(1)


def validate_python_version():
    """Require Python 3.10+ because codebase uses 3.10 syntax (e.g. X | None)."""
    if sys.version_info < (3, 10):
        print_error(
            f"Unsupported Python version: {sys.version.split()[0]}. "
            "Use Python 3.10 or newer."
        )
        sys.exit(1)


def validate_deprecated_vars():
    """Fail if deprecated environment variables are present."""
    deprecated_vars = {
        'PIPELINE_LOCAL_DRY_RUN': 'Use PIPELINE_POST_TO_INSTAGRAM=false instead',
        'PIPELINE_MOCK_DATA': 'WHOOP mock data is no longer supported. Use real WHOOP API data.',
    }
    
    deprecated_modes = {
        'auto': 'Use PIPELINE_MODE=automatic instead',
        'manual': 'Use PIPELINE_MODE=telegram instead',
        'semi': 'Use PIPELINE_MODE=telegram instead',
    }
    
    errors = []
    
    # Check deprecated env vars
    for var, migration_hint in deprecated_vars.items():
        if os.getenv(var):
            errors.append(f"{var} is deprecated. {migration_hint}")
    
    # Check deprecated mode values
    raw_mode = os.getenv('PIPELINE_MODE', '').strip().lower()
    if raw_mode in deprecated_modes:
        errors.append(f"PIPELINE_MODE={raw_mode} is deprecated. {deprecated_modes[raw_mode]}")
    
    if errors:
        print_error("Configuration validation failed:")
        for err in errors:
            print(f"   - {err}")
        print_info("Valid PIPELINE_MODE values: automatic, telegram")
        print_info("Dry run: PIPELINE_POST_TO_INSTAGRAM=false")
        sys.exit(1)


def validate_environment():
    """Verify required environment variables based on execution mode."""
    # Validate deprecated vars first
    validate_deprecated_vars()
    
    # Get new configuration values
    mode = (os.getenv("PIPELINE_MODE") or "automatic").strip().lower()
    post_to_instagram = env_bool("PIPELINE_POST_TO_INSTAGRAM", default=True)
    media_mode = get_media_mode()
    
    # Validate mode
    valid_modes = {'automatic', 'telegram'}
    if mode not in valid_modes:
        print_error(f"Invalid PIPELINE_MODE: {mode}. Supported modes: {', '.join(sorted(valid_modes))}")
        sys.exit(1)
    
    print(f"   Pipeline mode: {mode}")
    print(f"   Post to Instagram: {post_to_instagram}")
    print(f"   Media mode: {media_mode}")

    if media_mode not in VALID_MEDIA_MODES:
        print_error(
            f"Invalid PIPELINE_MEDIA_MODE: {media_mode}. "
            f"Supported modes: {', '.join(VALID_MEDIA_MODES)}"
        )
        sys.exit(1)

    required_env_vars = [
        'OPENROUTER_API_KEY',
        'GOOGLE_API_KEY_PRIMARY',
    ]

    # Check if Google API fallback is enabled
    fallback_enabled = env_bool("GOOGLE_API_FALLBACK_ENABLED", default=False)
    print(f"   Google API fallback: {'enabled' if fallback_enabled else 'disabled'}")
    
    # Only require fallback key if fallback is enabled
    if fallback_enabled:
        required_env_vars.append('GOOGLE_API_KEY_FALLBACK')
        # Validate: if fallback is enabled, the key must not be empty
        if not os.getenv('GOOGLE_API_KEY_FALLBACK'):
            print_error("GOOGLE_API_FALLBACK_ENABLED is true but GOOGLE_API_KEY_FALLBACK is not set")
            sys.exit(1)

    # WHOOP credentials are ALWAYS required (no mock data path)
    required_env_vars.extend([
        'WHOOP_CLIENT_ID',
        'WHOOP_CLIENT_SECRET',
    ])

    # Instagram and VPS credentials only required when posting is enabled
    if post_to_instagram:
        required_env_vars.extend([
            'INSTAGRAM_ACCESS_TOKEN',
            'INSTAGRAM_USER_ID',
            'VPS_PUBLIC_BASE_URL',
        ])
        if media_mode == 'live_vps':
            required_env_vars.extend([
                'VPS_SSH_HOST',
                'VPS_SSH_USER',
                'VPS_SSH_PATH',
            ])
    
    # Telegram credentials are NOT fatal in telegram mode (prompt-only failover)
    # Emit warning guidance instead
    has_telegram = os.getenv('TELEGRAM_BOT_TOKEN') and os.getenv('TELEGRAM_CHAT_ID')
    if mode == 'telegram' and not has_telegram:
        print_warning("TELEGRAM_BOT_TOKEN and/or TELEGRAM_CHAT_ID not set.")
        print_warning("Telegram mode will attempt prompt dispatch but may auto-fallback to automatic generation.")

    missing_vars = []
    for var in required_env_vars:
        if not os.getenv(var):
            missing_vars.append(var)
    
    if missing_vars:
        print_error("Missing environment variable(s):")
        for var in missing_vars:
            print(f"   - {var}")
        print_info("Tip: If running dry-run, set PIPELINE_POST_TO_INSTAGRAM=false")
        sys.exit(1)

def validate_file_structure():
    """Verify required files and directories exist."""
    project_root = get_project_root()
    astrology_root = get_astrology_root()
    runtime_root = get_runtime_root()

    required_files = [
        astrology_root / 'natal.yaml',
        astrology_root / 'dasha_periods.yaml',
    ]

    # Safe/public dirs in the repo.
    for dir_path in ('src/assets/', 'src/prompts/'):
        ensure_dir(dir_path)

    missing_files = []
    for file_path in required_files:
        if not file_path.is_file():
            missing_files.append(str(file_path.relative_to(project_root)) if file_path.is_relative_to(project_root) else str(file_path))

    if missing_files:
        print_error("Missing required file(s):")
        for file in missing_files:
            print(f"   - {file}")
        sys.exit(1)

    # Only create runtime dirs after we know the private data root is valid.
    for dir_path in (
        runtime_root,
        get_output_root(),
        get_database_root(),
        get_state_root(),
    ):
        ensure_path(Path(dir_path))

    if get_media_mode() == 'local_test':
        ensure_path(get_local_vps_root())

def validate_data_schemas():
    """Validate structure of required YAML data files."""
    astrology_root = get_astrology_root()

    # Validate natal.yaml
    natal_data = _load_yaml(astrology_root / 'natal.yaml', 'natal.yaml')
    if 'natal' not in natal_data:
        print_error("natal.yaml missing 'natal' root key")
        sys.exit(1)
    natal_root = natal_data['natal']
    for key in ('ascendant', 'moon_nakshatra'):
        if key not in natal_root:
            print_error(f"natal.yaml missing '{key}'")
            sys.exit(1)
    if not isinstance(natal_root.get('planets'), dict):
        print_error("natal.yaml missing 'planets' dictionary")
        sys.exit(1)
    required_planet_keys = {'sign', 'house', 'dignity'}
    for p_name, planet in natal_root['planets'].items():
        missing_keys = required_planet_keys - set(planet.keys())
        if missing_keys:
            print_error(f"natal.yaml: Planet {p_name} missing keys: {missing_keys}")
            sys.exit(1)

    # Validate dasha_periods.yaml
    dasha_data = _load_yaml(astrology_root / 'dasha_periods.yaml', 'dasha_periods.yaml')
    if not isinstance(dasha_data.get('periods'), list):
        print_error("dasha_periods.yaml must contain a 'periods' list")
        sys.exit(1)
    required_dasha_keys = {'start', 'end', 'maha', 'antar', 'pratyantar', 'sookshma', 'prana'}
    for idx, period in enumerate(dasha_data['periods']):
        missing_keys = required_dasha_keys - set(period.keys())
        if missing_keys:
            print_error(f"dasha_periods.yaml: Period at index {idx} missing keys: {missing_keys}")
            sys.exit(1)

def main():
    """Run all validation checks."""
    print("▶ Running pre-flight checks...")
    
    validate_python_version()
    validate_environment()
    validate_file_structure()
    validate_data_schemas()
    
    print_success("All validations passed")

if __name__ == '__main__':
    main()
