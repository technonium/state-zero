"""Explicit test setup; never imported by the production entry point."""
import os
import sys
from pathlib import Path

os.environ['STATE_ZERO_TEST_MODE'] = '1'
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/scripts'))
import notifier  # Capture explicit test mode before fixtures clear environment variables.
