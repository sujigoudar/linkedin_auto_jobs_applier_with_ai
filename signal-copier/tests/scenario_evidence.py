"""Pytest plugin for evidence collection from scenario-tagged tests.

This plugin registers the @pytest.mark.scenario() marker and collects
evidence from executed tests to write executed_tests.json. Used by WC-22
to prove which scenarios have been tested and passed.

Marker: @pytest.mark.scenario(*scenario_ids)
    scenario_ids: one or more scenario ID strings like "ING-001"

Option: --scenario-evidence=PATH
    Path to write executed_tests.json (default: docs/workflow-contract/evidence/executed_tests.json)

Output: executed_tests.json with structure:
    {
        "generated_at": ISO UTC timestamp,
        "git_commit": git revision or null,
        "code_hash": sha256 over sorted app/**/*.py,
        "config_hash": sha256 over pyproject.toml+pytest.ini+requirements.txt,
        "data_hash": sha256 over generated/*.jsonl + SCENARIO_CATALOG.json,
        "environment": "isolated_non_live_pytest",
        "tests": [
            {
                "nodeid": test node id,
                "outcome": "passed" | "failed" | "skipped" | "error",
                "duration_s": duration in seconds,
                "scenarios": [scenario_ids...]
            }
        ],
        "scenarios": {
            "scenario_id": {
                "result": "PASS" if every tagged test passed and ≥1 ran, "FAIL" if any failed/errored, "NOT_RUN" if only skipped,
                "tests": [nodeids that tagged this scenario]
            }
        }
    }
"""

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Global state
_plugin_state: dict[str, Any] = {
    "config": None,
    "catalog_ids": set(),
    "test_results": [],
    "collected_scenarios": {},
    "output_path": None,
}


def _load_catalog_ids(config: Any) -> set[str]:
    """Load all valid scenario IDs from SCENARIO_CATALOG.json."""
    catalog_path = Path(config.rootdir) / "docs" / "workflow-contract" / "SCENARIO_CATALOG.json"
    if not catalog_path.exists():
        return set()

    try:
        with open(catalog_path) as f:
            data = json.load(f)
        return set(s.get("id") for s in data.get("scenarios", []) if s.get("id"))
    except Exception as e:
        print(f"WARNING: Failed to load SCENARIO_CATALOG: {e}")
        return set()


def pytest_addoption(parser: Any) -> None:
    """Add pytest options."""
    parser.addoption(
        "--scenario-evidence",
        action="store",
        default=None,
        help="Path to write executed_tests.json (default: docs/workflow-contract/evidence/executed_tests.json)",
    )


def pytest_configure(config: Any) -> None:
    """Register the scenario marker and initialize plugin."""
    config.addinivalue_line("markers", "scenario(*ids): tag test with scenario IDs from SCENARIO_CATALOG.json")

    # Initialize global state
    _plugin_state["config"] = config
    _plugin_state["catalog_ids"] = _load_catalog_ids(config)

    # Get output path
    evidence_option = config.getoption("--scenario-evidence", None)
    if evidence_option:
        _plugin_state["output_path"] = Path(evidence_option)
    else:
        _plugin_state["output_path"] = Path(config.rootdir) / "docs" / "workflow-contract" / "evidence" / "executed_tests.json"


def pytest_collection_finish(session: Any) -> None:
    """After collection, validate scenario IDs."""
    unknown_ids = set()

    for item in session.items:
        markers = item.iter_markers("scenario")
        for marker in markers:
            for scenario_id in marker.args:
                if scenario_id not in _plugin_state["catalog_ids"]:  # type: ignore
                    unknown_ids.add(scenario_id)

    # Remember which scenario ids each collected item carries: TestReport has
    # no `item` attribute, so pytest_runtest_logreport looks nodeids up here.
    by_nodeid: dict[str, list[str]] = {}
    for item in session.items:
        ids: list[str] = []
        for marker in item.iter_markers("scenario"):
            ids.extend(marker.args)
        if ids:
            by_nodeid[item.nodeid] = ids
    _plugin_state["scenario_by_nodeid"] = by_nodeid

    if unknown_ids:
        unknown_str = ", ".join(sorted(unknown_ids))
        raise ValueError(
            f"Unknown scenario IDs in @pytest.mark.scenario(): {unknown_str}\n"
            f"Valid IDs are in SCENARIO_CATALOG.json"
        )


def pytest_runtest_logreport(report: Any) -> None:
    """Collect one outcome per scenario-tagged test.

    The call phase decides passed/failed; a setup-phase failure is recorded as
    "error" and a setup-phase skip as "skipped" (the call phase never runs in
    those cases). Tests without a scenario marker are not recorded.
    """
    by_nodeid = _plugin_state.get("scenario_by_nodeid") or {}
    scenario_ids = by_nodeid.get(report.nodeid)  # type: ignore[union-attr]
    if not scenario_ids:
        return

    if report.when == "call":
        outcome_str = report.outcome if report.outcome in ("passed", "failed", "skipped") else "error"
    elif report.when == "setup" and report.outcome in ("failed", "skipped"):
        outcome_str = "error" if report.outcome == "failed" else "skipped"
    else:
        return

    test_result = {
        "nodeid": report.nodeid,
        "outcome": outcome_str,
        "duration_s": getattr(report, "duration", 0.0),
        "scenarios": list(scenario_ids),
    }
    _plugin_state["test_results"].append(test_result)  # type: ignore

    for scenario_id in scenario_ids:
        if scenario_id not in _plugin_state["collected_scenarios"]:  # type: ignore
            _plugin_state["collected_scenarios"][scenario_id] = set()  # type: ignore
        _plugin_state["collected_scenarios"][scenario_id].add(report.nodeid)  # type: ignore


def pytest_sessionfinish(session: Any, exitstatus: int) -> None:
    """Write evidence at session end."""
    try:
        evidence = _build_evidence()

        # Ensure output directory exists
        output_path = _plugin_state["output_path"]  # type: ignore
        if output_path:
            output_path.parent.mkdir(parents=True, exist_ok=True)

            # Write JSON
            with open(output_path, "w") as f:
                json.dump(evidence, f, indent=2)

            print(f"\nScenario evidence written to {output_path}")
    except Exception as e:
        print(f"WARNING: Failed to write scenario evidence: {e}")
        import traceback
        traceback.print_exc()


def _build_evidence() -> dict[str, Any]:
    """Build evidence JSON."""
    config = _plugin_state["config"]

    # Compute hashes
    code_hash = _compute_code_hash(config)
    config_hash = _compute_config_hash(config)
    data_hash = _compute_data_hash(config)

    # Get git commit
    git_commit = _get_git_commit(config)

    # Build scenario summary
    scenarios = {}
    for scenario_id, test_nodeids in _plugin_state["collected_scenarios"].items():  # type: ignore
        result = _compute_scenario_result(test_nodeids)
        scenarios[scenario_id] = {
            "result": result,
            "tests": sorted(test_nodeids),
        }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": git_commit,
        "code_hash": code_hash,
        "config_hash": config_hash,
        "data_hash": data_hash,
        "environment": "isolated_non_live_pytest",
        "tests": _plugin_state["test_results"],
        "scenarios": scenarios,
    }


def _compute_scenario_result(test_nodeids: set[str]) -> str:
    """Compute scenario result from test outcomes."""
    outcomes = set()
    for test_result in _plugin_state["test_results"]:
        if test_result["nodeid"] in test_nodeids:
            outcomes.add(test_result["outcome"])

    if not outcomes:
        return "NOT_RUN"
    if "failed" in outcomes or "error" in outcomes:
        return "FAIL"
    if outcomes == {"skipped"}:
        return "NOT_RUN"
    if "passed" in outcomes:
        return "PASS"
    return "NOT_RUN"


def _compute_code_hash(config: Any) -> str:
    """Compute SHA256 over sorted app/**/*.py files."""
    try:
        app_dir = Path(config.rootdir) / "app"
        if not app_dir.exists():
            return ""

        files = sorted(app_dir.rglob("*.py"))
        hasher = hashlib.sha256()

        for fpath in files:
            with open(fpath, "rb") as f:
                hasher.update(f.read())

        return hasher.hexdigest()
    except Exception as e:
        print(f"WARNING: Failed to compute code hash: {e}")
        return ""


def _compute_config_hash(config: Any) -> str:
    """Compute SHA256 over pyproject.toml, pytest.ini, requirements.txt."""
    try:
        hasher = hashlib.sha256()

        for fname in ["pyproject.toml", "pytest.ini", "requirements.txt"]:
            fpath = Path(config.rootdir) / fname
            if fpath.exists():
                with open(fpath, "rb") as f:
                    hasher.update(f.read())

        return hasher.hexdigest()
    except Exception as e:
        print(f"WARNING: Failed to compute config hash: {e}")
        return ""


def _compute_data_hash(config: Any) -> str:
    """Compute SHA256 over generated/*.jsonl and SCENARIO_CATALOG.json."""
    try:
        hasher = hashlib.sha256()

        # Add SCENARIO_CATALOG.json
        catalog_path = Path(config.rootdir) / "docs" / "workflow-contract" / "SCENARIO_CATALOG.json"
        if catalog_path.exists():
            with open(catalog_path, "rb") as f:
                hasher.update(f.read())

        # Add generated/*.jsonl files in sorted order
        generated_dir = Path(config.rootdir) / "docs" / "workflow-contract" / "generated"
        if generated_dir.exists():
            for fpath in sorted(generated_dir.glob("*.jsonl")):
                with open(fpath, "rb") as f:
                    hasher.update(f.read())

        return hasher.hexdigest()
    except Exception as e:
        print(f"WARNING: Failed to compute data hash: {e}")
        return ""


def _get_git_commit(config: Any) -> str | None:
    """Get current git commit hash."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=config.rootdir,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None
