"""Load optional, private board configuration without third-party packages."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULTS = {
    "home": {"latitude": 39.8283, "longitude": -98.5795, "label": "Home"},
    "primary_state": "",
    "primary_label": "Primary area",
    "secondary_state": "",
    "secondary_label": "Secondary area",
    "linkedin_queries": [
        ["software engineer intern", "United States"],
        ["data science intern", "United States"],
        ["machine learning intern", "United States"],
    ],
}


@lru_cache(maxsize=1)
def board_config() -> dict:
    """Return private config when present, otherwise safe generic defaults."""
    path = ROOT / "config" / "board.json"
    if not path.is_file():
        return DEFAULTS
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return DEFAULTS
    merged = {**DEFAULTS, **loaded}
    merged["home"] = {**DEFAULTS["home"], **loaded.get("home", {})}
    return merged


def profile_name() -> str:
    """Read the user's name from the private YAML using a tiny safe parser."""
    path = ROOT / "config" / "profile.yml"
    if not path.is_file():
        return "Your Name"
    in_identity = False
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()
        if line == "identity:":
            in_identity = True
            continue
        if in_identity and line and not line.startswith(" "):
            break
        if in_identity and line.strip().startswith("name:"):
            return line.split(":", 1)[1].strip().strip("\"'") or "Your Name"
    return "Your Name"
