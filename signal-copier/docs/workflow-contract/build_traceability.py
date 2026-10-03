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
    """Check if test paths exist.

    A path is a test file or a pytest nodeid (``file.py::Class::test_fn``);
    for a nodeid the file must exist and define the named function.
    """
    for path in paths:
        if not path:
            continue
        file_part, _, node_part = path.partition("::")
        file_path = repo_root / file_part
        if not file_path.exists():
            print(f"WARNING: test_path does not exist: {path}")
            return False
        if node_part:
            func_name = node_part.split("::")[-1].split("[")[0]
            if f"def {func_name}(" not in file_path.read_text():
                print(f"WARNING: test_path names an undefined test function: {path}")
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
    status_counts: dict[str, int] = {}
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


def apply_executed_evidence(
    scenarios: dict[str, Any],
    traceability_map: dict[str, dict[str, Any]],
    executed_file: Path,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Apply executed test evidence to traceability map.

    Returns:
        (updated_map, warnings) where updated_map has TESTED_SIMULATOR status
        for scenarios with PASS result in executed file, and warnings for
        downgraded scenarios.
    """
    warnings = []

    if not executed_file.exists():
        warnings.append(f"--executed file not found: {executed_file}")
        return traceability_map, warnings

    with open(executed_file) as f:
        executed_data = json.load(f)

    executed_scenarios = executed_data.get("scenarios", {})

    # Check for scenarios marked TESTED_* in map but absent from executed
    for scenario_id, map_entry in traceability_map.items():
        status = map_entry.get("status", "NOT_IMPLEMENTED")
        if status in {"TESTED_SIMULATOR", "TESTED_BROKER_PAPER", "TESTED_OWNER_LIVE"}:
            if scenario_id not in executed_scenarios:
                warnings.append(
                    f"{scenario_id}: marked {status} in map but absent from executed evidence; "
                    "downgrading to WIRED_NOT_TESTED"
                )
                map_entry["status"] = "WIRED_NOT_TESTED"
                map_entry["last_result"] = "NOT_RUN"

    # Update scenarios with PASS result from executed file
    for scenario_id, scenario_data in executed_scenarios.items():
        result = scenario_data.get("result", "NOT_RUN")
        test_nodes = scenario_data.get("tests", [])

        # Ensure entry exists in traceability map
        if scenario_id not in traceability_map:
            traceability_map[scenario_id] = {}

        map_entry = traceability_map[scenario_id]

        if result == "PASS":
            # Update to TESTED_SIMULATOR
            if map_entry.get("status") != "TESTED_SIMULATOR":
                map_entry["status"] = "TESTED_SIMULATOR"

            # Add test_paths from executed file if not already present
            if "test_paths" not in map_entry or not map_entry["test_paths"]:
                # Extract file part from node ids
                test_paths = set()
                for nodeid in test_nodes:
                    # nodeid format: path/to/test_file.py::test_name
                    if "::" in nodeid:
                        test_file = nodeid.split("::")[0]
                        test_paths.add(test_file)
                map_entry["test_paths"] = sorted(test_paths)

            # Copy evidence fields
            if "evidence_paths" not in map_entry:
                map_entry["evidence_paths"] = [str(executed_file)]

            # Copy hashes and execution info
            for field in ["code_hash", "config_hash", "environment", "executed_at"]:
                if field == "executed_at":
                    if "generated_at" in executed_data:
                        map_entry[field] = executed_data["generated_at"]
                elif field in executed_data:
                    map_entry[field] = executed_data[field]

            map_entry["result"] = "PASS"
        elif result == "FAIL":
            # Keep existing status but mark last_result as FAIL
            map_entry["last_result"] = "FAIL"
            warnings.append(f"{scenario_id}: test execution FAILED")

    return traceability_map, warnings


def main(argv: list[str] | None = None):
    """Main entry point. `--output PATH` writes elsewhere (tests use tmp_path)."""
    import argparse

    script_dir = Path(__file__).parent
    repo_root = script_dir.parent.parent

    parser = argparse.ArgumentParser(description="Build requirements_traceability.json")
    parser.add_argument("--output", type=Path, default=script_dir / "requirements_traceability.json")
    parser.add_argument(
        "--executed",
        type=Path,
        default=None,
        help="Path to executed_tests.json from pytest plugin (optional)"
    )
    args = parser.parse_args(argv)

    catalog_path = script_dir / "SCENARIO_CATALOG.json"
    map_path = script_dir / "traceability_map.yaml"
    output_path = args.output

    # Load inputs
    print(f"Loading SCENARIO_CATALOG from {catalog_path}...")
    scenarios = load_scenario_catalog(catalog_path)
    print(f"  Found {len(scenarios)} scenarios")

    print(f"Loading traceability_map from {map_path}...")
    traceability_map = load_traceability_map(map_path)
    print(f"  Found {len(traceability_map)} mapped scenarios")

    # Apply executed evidence if provided
    exit_code = 0
    if args.executed:
        print(f"Applying executed evidence from {args.executed}...")
        traceability_map, warnings = apply_executed_evidence(scenarios, traceability_map, args.executed)
        for warning in warnings:
            print(f"  WARNING: {warning}")
        if any(w.startswith("ERROR:") for w in warnings):
            exit_code = 1
    else:
        print("WARNING: --executed not provided; statuses are not verified against test execution")

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

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
