"""The root docker-compose stack must not publish datastore ports on all interfaces.

The Postgres service ships a fixed local-simulation password, so any published
port has to be bound to loopback (``127.0.0.1:host:container``).
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

_COMPOSE_FILES = [
    Path(__file__).resolve().parents[2] / "docker-compose.yml",
    Path(__file__).resolve().parents[1] / "docker-compose.yml",
]
_DATASTORES = ("postgres", "redis", "mysql", "mongo", "db")


@pytest.mark.parametrize("compose_path", _COMPOSE_FILES, ids=lambda p: str(p.parent.name))
def test_datastore_ports_are_loopback_only(compose_path: Path) -> None:
    services = yaml.safe_load(compose_path.read_text())["services"]
    exposed = []
    for name, spec in services.items():
        if not any(token in name.lower() for token in _DATASTORES):
            continue
        for port in spec.get("ports", []) or []:
            if not str(port).startswith("127.0.0.1:"):
                exposed.append(f"{name}: {port}")
    assert not exposed, f"datastore ports published on all interfaces in {compose_path}: {exposed}"
