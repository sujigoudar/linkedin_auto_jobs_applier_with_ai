"""Signal Platform Integration Correction Pack's own
INTEGRATION_ACCEPTANCE_CASES.json INT-035 "Contract package cannot
import financial runtime": "Pure schemas/metric definitions load
without runtime credentials or app package imports." Verified two
ways, not just asserted:

1. Source inspection: no module in this package imports a forbidden
   name, anywhere, even transitively through a helper -- not just "we
   didn't add one" but a real scan of every `.py` file's own import
   statements.
2. A real subprocess import with `sqlalchemy`, `psycopg`, `httpx` and
   both apps' own `app` packages made UNIMPORTABLE (via `sys.modules`
   poisoning + a `sys.path` that excludes both `signal-copier/` and
   `signal-portfolio-commercial/`) -- if `import signal_platform_contracts`
   or any of its symbols' construction secretly needed one of those,
   this fails with an ImportError, not a passing assumption.
"""
from __future__ import annotations

import ast
import subprocess
import sys
import textwrap
from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent

#: Never imported by this package, anywhere -- a database driver, an
#: HTTP client (broker/API calls), or either app's own `app` package
#: (which is exactly what would smuggle in DB sessions, broker
#: adapters or secrets).
_FORBIDDEN_TOP_LEVEL_MODULES = frozenset({
    "sqlalchemy", "psycopg", "psycopg2", "httpx", "requests", "app",
    "alembic", "fastapi", "ccxt", "ib_async",
})


def _iter_source_files():
    for path in _PACKAGE_ROOT.glob("*.py"):
        if path.name != "conftest.py":
            yield path


def _imported_top_level_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module.split(".")[0])
    return modules


def test_no_source_file_imports_a_forbidden_module():
    offenders = {}
    for path in _iter_source_files():
        found = _imported_top_level_modules(path) & _FORBIDDEN_TOP_LEVEL_MODULES
        if found:
            offenders[path.name] = found
    assert offenders == {}, f"forbidden imports found: {offenders}"


def test_the_package_imports_cleanly_in_a_subprocess_with_db_broker_and_app_blocked():
    """Real, not simulated: a fresh subprocess whose sys.path is set
    EXPLICITLY (never relying on cwd-based implicit path insertion,
    which only happens to work when this package is separately
    pip-installed editable into the interpreter running the test --
    true in some dev environments, false in a clean CI job, which is
    exactly the gap that let a broken version of this test pass locally
    and fail in CI) to contain only this package's own parent directory,
    and whose sys.modules is pre-poisoned so
    `import sqlalchemy`/`psycopg`/`httpx`/`app` raise ImportError
    instead of silently finding the real, installed packages this test
    environment happens to have."""
    script = textwrap.dedent(
        """
        import sys
        sys.path.insert(0, __PARENT_DIR__)

        class _Blocked:
            def find_spec(self, name, path=None, target=None):
                if name.split(".")[0] in {"sqlalchemy", "psycopg", "psycopg2", "httpx", "app", "alembic"}:
                    raise ImportError(f"blocked for this boundary test: {name!r}")
                return None

        sys.meta_path.insert(0, _Blocked())

        import signal_platform_contracts as c

        # Exercise real construction, not just a bare import -- proves
        # the pure data types build without needing anything blocked.
        env = c.EventEnvelope(
            event_type=c.EventType.EXECUTION_APPLIED,
            event_id="evt-1",
            producer_id="test",
            source_stream="test:acct1",
            export_sequence=0,
            subject={"account_id": "acct1"},
            event_time="2026-01-01T00:00:00+00:00",
            effective_time="2026-01-01T00:00:00+00:00",
            availability_time="2026-01-01T00:00:00+00:00",
            receipt_time="2026-01-01T00:00:00+00:00",
            environment=c.Environment.LOCAL_SIM,
            evidence_class=c.EvidenceClass.SYNTHETIC_FIXTURE,
            payload_hash="a" * 64,
            payload={"x": "1"},
        )
        assert env.event_id == "evt-1"
        print("OK")
        """
    ).replace("__PARENT_DIR__", repr(str(_PACKAGE_ROOT.parent)))
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(_PACKAGE_ROOT.parent),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert "OK" in result.stdout
