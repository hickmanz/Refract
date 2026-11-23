"""Test configuration: runs before any test module imports the app."""

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("REFRACT_DATA", tempfile.mkdtemp(prefix="refract-test-"))
os.environ.setdefault("REFRACT_CONFIG", tempfile.mkdtemp(prefix="refract-test-config-"))

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "server"))
SAMPLES = ROOT / "samples"
