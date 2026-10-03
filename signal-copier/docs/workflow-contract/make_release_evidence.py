#!/usr/bin/env python3
"""Generate RELEASE_EVIDENCE.md from executed tests and supporting evidence files.

This script builds a release evidence report from:
- requirements_traceability.json (scenario statuses)
- evidence/executed_tests.json (test execution results)
- evidence/APPLICATION_RESULTS.json (if present; admission/sizing results)
- evidence/mutation_report.json (if present; mutant counts)
- evidence/release_notes.yaml (fixed sections for financial risks/blockers)
- generated/manifest.json (finite-domain counts)

Usage:
    python docs/workflow-contract/make_release_evidence.py [--output PATH]

Output:
    docs/workflow-contract/RELEASE_EVIDENCE.md (or PATH if specified)

The report includes executed/pass/fail/skip counts, code/config/data hashes,
killed/surviving mutants, UI journeys (tests tagged with UI-* scenarios),
and explicit statements that this does not certify live readiness.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore


def load_json_file(path: Path) -> dict[str, Any]:
    """Load a JSON file, return empty dict if not found."""
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as e:
        print(f"WARNING: Failed to load {path}: {e}")
        return {}


def load_yaml_file(path: Path) -> dict[str, Any]:
    """Load a YAML file, return empty dict if not found."""
    if not path.exists():
        return {}
    if yaml is None:
        print("WARNING: PyYAML not installed, skipping YAML file loading")
        return {}
    try:
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        print(f"WARNING: Failed to load {path}: {e}")
        return {}


def count_test_outcomes(executed_data: dict) -> dict[str, int]:
    """Count test outcomes from executed_tests.json."""
    outcomes = {"passed": 0, "failed": 0, "skipped": 0, "error": 0}

    for test in executed_data.get("tests", []):
        outcome = test.get("outcome", "unknown")
        if outcome in outcomes:
            outcomes[outcome] += 1

    return outcomes


def count_scenario_results(executed_data: dict) -> dict[str, int]:
    """Count scenario results from executed_tests.json."""
    results = {"PASS": 0, "FAIL": 0, "NOT_RUN": 0}

    for scenario_data in executed_data.get("scenarios", {}).values():
        result = scenario_data.get("result", "NOT_RUN")
        if result in results:
            results[result] += 1

    return results


def count_scenario_statuses(traceability_data: dict) -> dict[str, int]:
    """Count scenario statuses from requirements_traceability.json."""
    statuses: dict[str, int] = {}

    for scenario in traceability_data.get("scenarios", []):
        status = scenario.get("status", "NOT_IMPLEMENTED")
        statuses[status] = statuses.get(status, 0) + 1

    return statuses


def extract_ui_journeys(executed_data: dict) -> list[str]:
    """Extract test nodeids that are tagged with UI-* scenarios."""
    ui_tests = []

    for scenario_id, scenario_data in executed_data.get("scenarios", {}).items():
        if scenario_id.startswith("UI-"):
            ui_tests.extend(scenario_data.get("tests", []))

    return sorted(set(ui_tests))


def count_mutation_stats(mutation_data: dict) -> dict[str, int]:
    """Extract mutation report statistics."""
    stats = {}

    if "statistics" in mutation_data:
        stats_obj = mutation_data["statistics"]
        stats["killed"] = stats_obj.get("killed", 0)
        stats["survived"] = stats_obj.get("survived", 0)
    elif "killed" in mutation_data or "survived" in mutation_data:
        stats["killed"] = mutation_data.get("killed", 0)
        stats["survived"] = mutation_data.get("survived", 0)

    return stats


def count_finite_domain_coverage(manifest_data: dict, executed_data: dict) -> dict[str, int]:
    """Count finite-domain coverage from manifest and executed tests."""
    coverage = {}

    # Get admission and sizing counts from manifest
    admission_total = manifest_data.get("admission", {}).get("total_cases", 0)
    sizing_total = manifest_data.get("linear_sizing", {}).get("total_cases", 0)
    race_total = manifest_data.get("race_sequences", {}).get("total_vectors", 0)

    coverage["admission_total"] = admission_total
    coverage["sizing_total"] = sizing_total
    coverage["race_total"] = race_total

    # Count how many were executed (from APPLICATION_RESULTS or test count)
    coverage["admission_executed"] = 0
    coverage["sizing_executed"] = 0

    # Count tests tagged with TST-* (test scenario suite)
    tst_tests = set()
    for scenario_id, scenario_data in executed_data.get("scenarios", {}).items():
        if scenario_id.startswith("TST-"):
            tst_tests.update(scenario_data.get("tests", []))

    coverage["test_suite_count"] = len(tst_tests)

    return coverage


def build_release_evidence(
    traceability_path: Path,
    executed_path: Path,
    application_results_path: Path,
    mutation_path: Path,
    manifest_path: Path,
    release_notes_path: Path,
) -> str:
    """Build the RELEASE_EVIDENCE.md report."""

    # Load all data files
    traceability = load_json_file(traceability_path)
    executed = load_json_file(executed_path)
    mutation_stats = load_json_file(mutation_path)
    manifest = load_json_file(manifest_path)
    release_notes = load_yaml_file(release_notes_path)

    # Extract statistics
    test_outcomes = count_test_outcomes(executed)
    scenario_results = count_scenario_results(executed)
    scenario_statuses = count_scenario_statuses(traceability)
    ui_journeys = extract_ui_journeys(executed)
    mutation_info = count_mutation_stats(mutation_stats)
    domain_coverage = count_finite_domain_coverage(manifest, executed)

    # Get hashes
    code_hash = executed.get("code_hash", "")
    config_hash = executed.get("config_hash", "")
    data_hash = executed.get("data_hash", "")
    git_commit = executed.get("git_commit")

    # Build markdown
    md = []
    md.append("# Release Evidence Report\n")
    md.append(f"Generated: {datetime.now(timezone.utc).isoformat()}\n")

    if git_commit:
        md.append(f"Git Commit: `{git_commit}`\n")

    md.append("\n## Critical Statement\n")
    md.append(
        "**This report does not certify live readiness or profitability.** "
        "Software correctness evidence is separate from profitability evidence. "
        "This is a technical summary of test execution and code coverage, "
        "not a trading authorization or risk certification.\n"
    )

    md.append("\n## Execution Summary\n")
    md.append(f"Tests executed: {sum(test_outcomes.values())}\n")
    md.append(f"- Passed: {test_outcomes['passed']}\n")
    md.append(f"- Failed: {test_outcomes['failed']}\n")
    md.append(f"- Skipped: {test_outcomes['skipped']}\n")
    md.append(f"- Error: {test_outcomes['error']}\n\n")

    md.append("Scenarios executed: {}\n".format(sum(scenario_results.values())))
    md.append(f"- PASS: {scenario_results['PASS']}\n")
    md.append(f"- FAIL: {scenario_results['FAIL']}\n")
    md.append(f"- NOT_RUN: {scenario_results['NOT_RUN']}\n\n")

    md.append("## Scenario Status Counts\n\n")
    for status in sorted(scenario_statuses.keys()):
        count = scenario_statuses[status]
        md.append(f"- {status}: {count}\n")
    md.append("\n")

    md.append("## Integrity Hashes\n\n")
    md.append("Code (SHA256 over app/**/*.py):\n")
    md.append(f"```\n{code_hash}\n```\n\n")

    md.append("Configuration (SHA256 over pyproject.toml + pytest.ini + requirements.txt):\n")
    md.append(f"```\n{config_hash}\n```\n\n")

    md.append("Data (SHA256 over generated/*.jsonl + SCENARIO_CATALOG.json):\n")
    md.append(f"```\n{data_hash}\n```\n\n")

    md.append("## Finite-Domain Coverage\n\n")
    if domain_coverage.get("admission_total"):
        md.append(f"Admission test vectors: {domain_coverage['admission_executed']} / {domain_coverage['admission_total']}\n")
    if domain_coverage.get("sizing_total"):
        md.append(f"Sizing test vectors: {domain_coverage['sizing_executed']} / {domain_coverage['sizing_total']}\n")
    if domain_coverage.get("race_total"):
        md.append(f"Race condition vectors: {domain_coverage['race_total']} defined\n")
    if domain_coverage.get("test_suite_count"):
        md.append(f"Test suite (TST-*) scenarios: {domain_coverage['test_suite_count']}\n")
    md.append("\n")

    if mutation_info:
        md.append("## Mutation Testing\n\n")
        if "killed" in mutation_info:
            md.append(f"Mutants killed: {mutation_info['killed']}\n")
        if "survived" in mutation_info:
            md.append(f"Mutants survived: {mutation_info['survived']}\n")
        md.append("\n")

    if ui_journeys:
        md.append("## UI Journeys Tested\n\n")
        for test in ui_journeys[:20]:  # Show first 20
            md.append(f"- {test}\n")
        if len(ui_journeys) > 20:
            md.append(f"- ... and {len(ui_journeys) - 20} more\n")
        md.append("\n")

    md.append("## Financial Risks\n\n")
    risks = release_notes.get("financial_risks", [])
    if risks:
        for risk in risks:
            md.append(f"- {risk}\n")
    else:
        md.append("*(See release_notes.yaml for detailed risk assessment)*\n")
    md.append("\n")

    md.append("## External Blockers\n\n")
    blockers = release_notes.get("external_blockers", [])
    if blockers:
        for blocker in blockers:
            md.append(f"- {blocker}\n")
    else:
        md.append("*(See release_notes.yaml)*\n")
    md.append("\n")

    md.append("## Profitability Evidence\n\n")
    md.append("**None.** Profitability claims are kept separate from correctness evidence. "
              "This report covers test execution and code correctness only.\n\n")

    md.append("---\n\n")
    md.append("*This is evidence of test infrastructure execution in an isolated non-live environment. "
              "It does not constitute authorization to trade, deploy to live accounts, "
              "or make changes to production configuration.*\n")

    return "".join(md)


def main(argv: list[str] | None = None):
    """Main entry point."""
    import argparse

    script_dir = Path(__file__).parent

    parser = argparse.ArgumentParser(description="Generate RELEASE_EVIDENCE.md")
    parser.add_argument(
        "--output",
        type=Path,
        default=script_dir / "RELEASE_EVIDENCE.md",
        help="Output path for RELEASE_EVIDENCE.md",
    )
    args = parser.parse_args(argv)

    # Input file paths
    traceability_path = script_dir / "requirements_traceability.json"
    executed_path = script_dir / "evidence" / "executed_tests.json"
    application_results_path = script_dir / "evidence" / "APPLICATION_RESULTS.json"
    mutation_path = script_dir / "evidence" / "mutation_report.json"
    manifest_path = script_dir / "generated" / "manifest.json"
    release_notes_path = script_dir / "evidence" / "release_notes.yaml"

    # Check required files
    if not traceability_path.exists():
        print(f"ERROR: {traceability_path} not found")
        return 1

    # Build report
    print("Building RELEASE_EVIDENCE.md...")
    report = build_release_evidence(
        traceability_path,
        executed_path,
        application_results_path,
        mutation_path,
        manifest_path,
        release_notes_path,
    )

    # Write output
    with open(args.output, "w") as f:
        f.write(report)

    print(f"✓ Wrote {args.output}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
