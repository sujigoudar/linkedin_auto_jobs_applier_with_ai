#!/usr/bin/env python3
"""Build requirements_traceability.json from SCENARIO_CATALOG.json and traceability_map.yaml.

This script reads the scenario catalog (292 named requirements) and the traceability
map (status + implementation_paths for each scenario), then writes requirements_traceability.json
with the effective status and paths. Used by WC-01 to validate test coverage and gate releases.

Usage:
    python docs/workflow-contract/build_traceability.py

Reads:
    - docs/workflow-contract/SCENARIO_CATALOG.json (292 scenarios, read-only)
    - docs/workflow-contract/traceability_map.yaml (status and paths for each scenario)

Writes:
    - docs/workflow-contract/requirements_traceability.json (merged state for validation/reports)

Validation:
    - Every scenario ID in SCENARIO_CATALOG must appear in traceability_map or be initialized as NOT_IMPLEMENTED
    - Every status must be one of: NOT_IMPLEMENTED, IMPLEMENTED_NOT_WIRED, WIRED_NOT_TESTED, TESTED_SIMULATOR, TESTED_BROKER_PAPER, TESTED_OWNER_LIVE, BLOCKED, NOT_APPLICABLE_WITH_REASON
    - Every implementation_path must exist (or be empty)
    - All test_paths must reference real test files
"""

import json
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML not installed. Run: pip install pyyaml")
    sys.exit(1)


# Allowed status values (from IMPLEMENTATION_PLAN.md spec §21)
ALLOWED_STATUSES = {
    "NOT_IMPLEMENTED",
    "IMPLEMENTED_NOT_WIRED",
    "WIRED_NOT_TESTED",
    "TESTED_SIMULATOR",
    "TESTED_BROKER_PAPER",
    "TESTED_OWNER_LIVE",
    "BLOCKED",
    "NOT_APPLICABLE_WITH_REASON",
}


def load_scenario_catalog(catalog_path: Path) -> dict[str, Any]:
    """Load SCENARIO_CATALOG.json and index by scenario id."""
    with open(catalog_path) as f:
        data = json.load(f)

    scenarios = {}
    for scenario in data.get("scenarios", []):
        scenario_id = scenario.get("id")
        if scenario_id:
            scenarios[scenario_id] = scenario

    return scenarios


def load_traceability_map(map_path: Path) -> dict[str, dict[str, Any]]:
    """Load traceability_map.yaml and index by scenario id."""
    if not map_path.exists():
        # If map doesn't exist yet, return empty dict (will initialize all as NOT_IMPLEMENTED)
        return {}

    with open(map_path) as f:
        data = yaml.safe_load(f) or {}

    # Index by scenario id
    map_by_id = {}
    for entry in data.get("scenarios", []):
        scenario_id = entry.get("id")
        if scenario_id:
            map_by_id[scenario_id] = entry

    return map_by_id


def validate_status(status: str) -> bool:
    """Check if status is in allowed set."""
    return status in ALLOWED_STATUSES


def validate_implementation_paths(paths: list[str], repo_root: Path) -> bool:
    """Check if implementation paths exist (or are empty)."""
    for path in paths:
        if path and not (repo_root / path).exists():
            print(f"WARNING: implementation_path does not exist: {path}")
            return False
    return True


def validate_test_paths(paths: list[str], repo_root: Path) -> bool:
    """Check if test paths exist."""
    for path in paths:
        if path and not (repo_root / path).exists():
            print(f"WARNING: test_path does not exist: {path}")
            return False
    return True


def build_traceability(
    scenarios: dict[str, Any],
    traceability_map: dict[str, dict[str, Any]],
    repo_root: Path,
) -> dict[str, Any]:
    """Merge scenarios and traceability map into unified output."""
    result_scenarios = []

    for scenario_id, scenario_data in scenarios.items():
        # Get traceability info from map, or initialize as NOT_IMPLEMENTED
        map_entry = traceability_map.get(scenario_id, {})
        status = map_entry.get("status", "NOT_IMPLEMENTED")
        implementation_paths = map_entry.get("implementation_paths", [])
        test_paths = map_entry.get("test_paths", [])
        evidence_paths = map_entry.get("evidence_paths", [])
        blocked_reason = map_entry.get("blocked_reason")
        notes = map_entry.get("notes", "")

        # Validation
        if not validate_status(status):
            print(f"ERROR: {scenario_id} has invalid status '{status}'")
            sys.exit(1)

        if not validate_implementation_paths(implementation_paths, repo_root):
            print(f"WARNING: {scenario_id} has invalid implementation_paths")

        if not validate_test_paths(test_paths, repo_root):
            print(f"WARNING: {scenario_id} has invalid test_paths")

        # Build merged entry
        result_entry = {
            "id": scenario_id,
            "category": scenario_data.get("category", ""),
            "title": scenario_data.get("title", ""),
            "status": status,
            "implementation_paths": implementation_paths,
            "test_paths": test_paths,
            "evidence_paths": evidence_paths,
            "blocked_reason": blocked_reason,
            "notes": notes,
        }

        # Optional fields from catalog
        if "given" in scenario_data:
            result_entry["given"] = scenario_data["given"]
        if "when" in scenario_data:
            result_entry["when"] = scenario_data["when"]
        if "then" in scenario_data:
            result_entry["then"] = scenario_data["then"]
        if "required_observations" in scenario_data:
            result_entry["required_observations"] = scenario_data["required_observations"]
        if "required_variants" in scenario_data:
            result_entry["required_variants"] = scenario_data["required_variants"]
        if "execution_authority" in scenario_data:
            result_entry["execution_authority"] = scenario_data["execution_authority"]

        result_scenarios.append(result_entry)

    # Check for stray IDs in traceability map (not in catalog)
    for map_id in traceability_map:
        if map_id not in scenarios:
            print(f"WARNING: {map_id} in traceability_map but not in SCENARIO_CATALOG")

    # Compute summary stats
    status_counts = {}
    for entry in result_scenarios:
        status = entry["status"]
        status_counts[status] = status_counts.get(status, 0) + 1

    return {
        "schema_version": "1.0",
        "generated_at": str(Path(__file__).stat().st_mtime),
        "total_scenarios": len(result_scenarios),
        "status_counts": status_counts,
        "scenarios": result_scenarios,
    }


def main():
    """Main entry point."""
    script_dir = Path(__file__).parent
    repo_root = script_dir.parent.parent

    catalog_path = script_dir / "SCENARIO_CATALOG.json"
    map_path = script_dir / "traceability_map.yaml"
    output_path = script_dir / "requirements_traceability.json"

    # Load inputs
    print(f"Loading SCENARIO_CATALOG from {catalog_path}...")
    scenarios = load_scenario_catalog(catalog_path)
    print(f"  Found {len(scenarios)} scenarios")

    print(f"Loading traceability_map from {map_path}...")
    traceability_map = load_traceability_map(map_path)
    print(f"  Found {len(traceability_map)} mapped scenarios")

    # Build merged output
    print("Building requirements_traceability.json...")
    result = build_traceability(scenarios, traceability_map, repo_root)

    # Write output
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)

    print(f"✓ Wrote {output_path}")
    print("\nStatus Summary:")
    for status, count in sorted(result["status_counts"].items()):
        print(f"  {status}: {count}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
