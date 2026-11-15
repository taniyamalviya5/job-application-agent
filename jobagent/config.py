"""Loads config.yaml and secrets (environment variables or Streamlit secrets)."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent

DEFAULTS: dict[str, Any] = {
    "profile": {"name": "", "headline": "", "phone": "", "linkedin": "",
                "resume_pdf": "profile/resume.pdf", "resume_text": "profile/resume.md"},
    "search": {"terms": ["software engineer"], "locations": ["India"], "sites": ["indeed"],
               "country_indeed": "India", "results_per_search": 15, "hours_old": 72,
               "exclude_keywords": []},
    "scoring": {"min_score": 60, "max_jobs_per_run": 25},
    "email": {"daily_send_limit": 15, "follow_up_after_days": 7},
    "llm": {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
            "model": "gemini-3.5-flash-lite", "seconds_between_calls": 5},
}


def _merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in (override or {}).items():
        out[k] = _merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    p = Path(path) if path else ROOT / "config.yaml"
    data = yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {}
    return _merge(DEFAULTS, data or {})


def secret(name: str, default: str = "") -> str:
    """Read a secret from env first, then Streamlit secrets (if running in Streamlit)."""
    val = os.environ.get(name)
    if val:
        return val
    try:
        import streamlit as st  # noqa: WPS433
        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:
        pass
    return default
