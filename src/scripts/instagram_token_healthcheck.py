#!/usr/bin/env python3

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ops.instagram_token_healthcheck import _format_expiry_window, _load_state, _parse_thresholds, _save_state, main


if __name__ == "__main__":
    main()
