"""Environment variables: ``REFRACT_<NAME>``, with the old ``LIGHTSIM_<NAME>`` (the app's name before it was renamed) as a fallback."""

from __future__ import annotations

import os


def env(name: str, default: str | None = None) -> str | None:
    """``REFRACT_<name>`` if set and not empty, else ``LIGHTSIM_<name>``, else ``default``."""
    return os.environ.get(f"REFRACT_{name}") or os.environ.get(f"LIGHTSIM_{name}") or default
