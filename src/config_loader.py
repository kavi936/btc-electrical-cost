"""Load and validate config.yaml — single source of economic assumptions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config.yaml"


def load_config(path: Path | str | None = None) -> dict[str, Any]:
    cfg_path = Path(path) if path else DEFAULT_CONFIG
    if not cfg_path.exists():
        raise FileNotFoundError(f"Config not found: {cfg_path}")
    with cfg_path.open() as f:
        cfg = yaml.safe_load(f)
    if not isinstance(cfg, dict):
        raise ValueError("config.yaml must parse to a mapping")
    return cfg


def resolve_path(cfg: dict[str, Any], key: str) -> Path:
    """Resolve a paths.* entry relative to project root."""
    rel = cfg["paths"][key]
    p = Path(rel)
    return p if p.is_absolute() else ROOT / p
