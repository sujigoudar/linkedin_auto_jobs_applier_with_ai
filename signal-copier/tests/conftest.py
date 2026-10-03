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

# Register the scenario evidence plugin
pytest_plugins = ["tests.scenario_evidence"]


@pytest.fixture(autouse=True)
def reset_capital_allocator_state():
    """Prevent test state leakage by resetting capital allocator
    pending reservations before each test. The engine holds a shared
    CapitalAllocator instance that persists across test fixtures."""
    try:
        import app.main as main_module
        if hasattr(main_module, 'engine') and hasattr(main_module.engine, 'capital_allocator'):
            main_module.engine.capital_allocator._pending.clear()
    except Exception:
        pass
    yield
    try:
        import app.main as main_module
        if hasattr(main_module, 'engine') and hasattr(main_module.engine, 'capital_allocator'):
            main_module.engine.capital_allocator._pending.clear()
    except Exception:
        pass

@pytest.fixture(autouse=True)
def reset_shared_paper_broker():
    """app.main builds ONE module-level PaperBroker that the engine, the
    lifecycle manager and the daily-loss limiter all share. Its simulated
    cash, positions and last-fill quote prices therefore leaked from one
    TestClient-based test into the next (a later priceless BUY was gated by
    an earlier test's fill price and depleted cash). Swap in a fresh
    PaperBroker before every test; the registries all hold the same dict, so
    replacing the entry is enough."""
    try:
        import sys

        main_module = sys.modules.get("app.main")
        if main_module is not None and "paper" in getattr(main_module, "brokers", {}):
            from app.brokers.paper import PaperBroker

            main_module.brokers["paper"] = PaperBroker()
    except Exception:
        pass
    yield


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
    """Find a free port, with retry logic for TIME_WAIT state."""
    for _ in range(5):
        try:
            with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(("127.0.0.1", 0))
                return s.getsockname()[1]
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("Could not find a free port after 5 attempts")


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
            # TR-0x transition flag (see app/config.py): off by default in
            # production now that the redesign has parity, but this shared
            # real-browser fixture backs tests across many files -- at
            # least test_tr01_tr04_trading_screens.py's real-browser test
            # still exercises the legacy dashboard directly
            # (#legacy-content/#legacy-nav-link), so the test server keeps
            # it reachable rather than adjusting that test's own
            # assertions.
            "LEGACY_DASHBOARD_ENABLED": "true",
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
        for _attempt in range(75):
            try:
                response = httpx.get(f"{base_url}/health", timeout=1.0)
                if response.status_code in (200, 503):
                    break
            except (httpx.TransportError, httpx.ConnectError):
                pass
            time.sleep(0.2)
        else:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
            raise RuntimeError("live_server did not become reachable in time")
        yield base_url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=1)
        time.sleep(0.1)
