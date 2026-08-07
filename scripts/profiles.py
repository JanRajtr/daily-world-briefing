"""Shared, validated report-profile configuration."""

from __future__ import annotations

import json
from pathlib import Path

DEFAULT_PROFILE = "cs-buddhist"
PROFILE_FILE = Path(__file__).parents[1] / "config" / "profiles.json"


def load_profile(name: str = DEFAULT_PROFILE) -> dict:
    profiles = json.loads(PROFILE_FILE.read_text(encoding="utf-8"))
    if name not in profiles:
        raise ValueError(f"Unknown profile {name!r}; choose one of: {', '.join(sorted(profiles))}")
    profile = {"name": name, **profiles[name]}
    required = ("language", "language_name", "tradition", "output_path", "title")
    if any(not isinstance(profile.get(key), str) for key in required):
        raise ValueError(f"Profile {name!r} is incomplete")
    if profile["tradition"] not in ("buddhism", "islam"):
        raise ValueError(f"Unsupported tradition in profile {name!r}")
    return profile
