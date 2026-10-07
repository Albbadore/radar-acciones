"""Configuration loading."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config.yaml"


def load_config(path: str | os.PathLike | None = None) -> dict[str, Any]:
    cfg_path = Path(path) if path else DEFAULT_CONFIG
    with open(cfg_path, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    for key in ("db_path", "cache_dir", "log_file"):
        cfg[key] = str((ROOT / cfg[key]).resolve()) if not os.path.isabs(cfg[key]) else cfg[key]
    return cfg


def sec_user_agent(cfg: dict[str, Any]) -> str:
    agent = os.environ.get(cfg["sec"]["user_agent_env"], "").strip()
    if not agent:
        raise RuntimeError(
            "Falta la variable de entorno SEC_USER_AGENT (la SEC exige un "
            "User-Agent con contacto, p. ej. 'MiRadar/1.0 nombre@dominio.com')."
        )
    return agent
