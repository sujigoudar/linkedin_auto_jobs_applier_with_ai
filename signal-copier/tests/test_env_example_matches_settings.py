"""`.env.example` must list every setting in app/config.py, and loading it verbatim must work."""
from __future__ import annotations

import re
from pathlib import Path

from app.config import _Settings

_ENV_EXAMPLE = Path(__file__).resolve().parents[1] / ".env.example"


def _documented_names() -> set[str]:
    return set(re.findall(r"^\s*#?\s*([A-Z][A-Z0-9_]+)\s*=", _ENV_EXAMPLE.read_text(), re.M))


def test_every_setting_is_listed_in_env_example() -> None:
    missing = sorted(set(_Settings.model_fields) - _documented_names())
    assert not missing, f"settings missing from .env.example: {missing}"


def test_env_example_loads_verbatim(monkeypatch) -> None:
    for name in _Settings.model_fields:
        monkeypatch.delenv(name, raising=False)
    _Settings(_env_file=str(_ENV_EXAMPLE))  # must not raise a validation error
