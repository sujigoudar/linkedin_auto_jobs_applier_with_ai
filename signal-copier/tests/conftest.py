"""Shared fixtures for real-browser tests (C14's XSS regression,
C33/C34's accessibility check) -- both need an actual running HTTP
server and an actual browser, not TestClient/ASGI transport or a DOM
mock.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path

import httpx
import pytest

_SIGNAL_COPIER_DIR = Path(__file__).resolve().parent.parent


def resolve_chromium_executable() -> str | None:
    """An explicit override always wins. Otherwise, auto-detect this
    sandbox's own pre-installed, pinned Chromium build outside
    Playwright's managed browser cache (its own `playwright install`
    can't reach the network here) -- a normal CI runner has no such
    directory and gets None, letting Playwright resolve the browser it
    fetched via its own `playwright install chromium` CI step instead
    (see .github/workflows/signal-copier-ci.yml)."""
    override = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
    if override:
        return override
    pinned_dir = Path("/opt/pw-browsers")
    if pinned_dir.is_dir():
        for candidate in sorted(pinned_dir.glob("chromium-*/chrome-linux/chrome")):
            return str(candidate)
    return None


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def live_server(tmp_path):
    """A real `uvicorn` subprocess serving the real app.main:app."""
    port = _free_port()
    env = os.environ.copy()
    env.update(
        {
            "OWNER_PASSWORD": "test-owner-pw",
            "SESSION_SECRET": "test-session-secret",
            "WEBHOOK_SHARED_SECRET": "test-webhook-secret",
            "DATABASE_PATH": str(tmp_path / "e2e.db"),
        }
    )
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(_SIGNAL_COPIER_DIR),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(75):
            try:
                response = httpx.get(f"{base_url}/health", timeout=1.0)
                if response.status_code in (200, 503):
                    break
            except httpx.TransportError:
                pass
            time.sleep(0.2)
        else:
            proc.terminate()
            raise RuntimeError("live_server did not become reachable in time")
        yield base_url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
