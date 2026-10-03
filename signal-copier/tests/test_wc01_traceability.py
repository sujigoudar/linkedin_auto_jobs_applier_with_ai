"""Tests for WC-01: Inventory, reasons, traceability skeleton.

Validates the traceability mapping, scenario catalog, and generated JSON files.
"""

import json
from pathlib import Path

import pytest
import yaml


# Allowed statuses from IMPLEMENTATION_PLAN.md spec §21
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


@pytest.fixture
def docs_dir():
    """Path to docs/workflow-contract/."""
    p = Path(__file__).parent.parent / "docs" / "workflow-contract"
    assert p.exists(), f"docs/workflow-contract/ not found at {p}"
    return p


@pytest.fixture
def repo_root():
    """Path to signal-copier repo root."""
    return Path(__file__).parent.parent


@pytest.fixture
def scenario_catalog(docs_dir):
    """Load SCENARIO_CATALOG.json."""
    path = docs_dir / "SCENARIO_CATALOG.json"
    assert path.exists(), f"SCENARIO_CATALOG.json not found at {path}"
    with open(path) as f:
        return json.load(f)


@pytest.fixture
def traceability_map(docs_dir):
    """Load traceability_map.yaml."""
    path = docs_dir / "traceability_map.yaml"
    assert path.exists(), f"traceability_map.yaml not found at {path}"
    with open(path) as f:
        return yaml.safe_load(f)


class TestScenarioCatalog:
    """Validate SCENARIO_CATALOG.json structure and content."""

    def test_catalog_exists(self, docs_dir):
        """Catalog file exists."""
        assert (docs_dir / "SCENARIO_CATALOG.json").exists()

    def test_catalog_valid_json(self, docs_dir):
        """Catalog is valid JSON."""
        path = docs_dir / "SCENARIO_CATALOG.json"
        with open(path) as f:
            data = json.load(f)
        assert data is not None

    def test_catalog_has_scenarios(self, scenario_catalog):
        """Catalog contains scenarios."""
        assert "scenarios" in scenario_catalog
        scenarios = scenario_catalog["scenarios"]
        assert isinstance(scenarios, list)
        assert len(scenarios) > 0, "Catalog must have at least one scenario"

    def test_catalog_scenario_count(self, scenario_catalog):
        """Catalog has exactly 292 scenarios (per spec)."""
        scenarios = scenario_catalog["scenarios"]
        assert len(scenarios) == 292, f"Expected 292 scenarios, got {len(scenarios)}"

    def test_all_scenarios_have_id(self, scenario_catalog):
        """Every scenario has a unique id."""
        scenarios = scenario_catalog["scenarios"]
        ids = []
        for scenario in scenarios:
            assert "id" in scenario, "Scenario missing 'id' field"
            scenario_id = scenario["id"]
            assert isinstance(scenario_id, str), f"Scenario id must be string, got {type(scenario_id)}"
            assert scenario_id not in ids, f"Duplicate scenario id: {scenario_id}"
            ids.append(scenario_id)

    def test_all_scenarios_have_required_fields(self, scenario_catalog):
        """Every scenario has category, title, given, when, then."""
        scenarios = scenario_catalog["scenarios"]
        required_fields = ["category", "title", "given", "when", "then"]
        for scenario in scenarios:
            for field in required_fields:
                assert field in scenario, (
                    f"Scenario {scenario.get('id', '?')} missing required field '{field}'"
                )

    def test_all_scenarios_have_status_field(self, scenario_catalog):
        """Every scenario has a status field (for tracking)."""
        scenarios = scenario_catalog["scenarios"]
        for scenario in scenarios:
            # The catalog itself carries a status field
            assert "status" in scenario, f"Scenario {scenario.get('id')} missing status"


class TestTraceabilityMap:
    """Validate traceability_map.yaml structure and content."""

    def test_map_exists(self, docs_dir):
        """Traceability map file exists."""
        assert (docs_dir / "traceability_map.yaml").exists()

    def test_map_valid_yaml(self, docs_dir):
        """Map is valid YAML."""
        path = docs_dir / "traceability_map.yaml"
        with open(path) as f:
            data = yaml.safe_load(f)
        assert data is not None

    def test_map_has_scenarios(self, traceability_map):
        """Map contains a scenarios list."""
        assert "scenarios" in traceability_map
        scenarios = traceability_map["scenarios"]
        assert isinstance(scenarios, list)
        assert len(scenarios) > 0

    def test_map_scenario_count_matches_catalog(self, scenario_catalog, traceability_map):
        """Map has same number of scenarios as catalog."""
        catalog_count = len(scenario_catalog["scenarios"])
        map_count = len(traceability_map["scenarios"])
        assert map_count == catalog_count, (
            f"Map has {map_count} scenarios but catalog has {catalog_count}"
        )

    def test_all_map_scenarios_have_id(self, traceability_map):
        """Every scenario in map has an id."""
        scenarios = traceability_map["scenarios"]
        for scenario in scenarios:
            assert "id" in scenario, "Scenario in map missing 'id'"
            assert isinstance(scenario["id"], str)

    def test_all_map_scenarios_in_catalog(self, scenario_catalog, traceability_map):
        """Every scenario in map exists in catalog."""
        catalog_ids = {s["id"] for s in scenario_catalog["scenarios"]}
        map_ids = {s["id"] for s in traceability_map["scenarios"]}
        for map_id in map_ids:
            assert map_id in catalog_ids, f"Map scenario {map_id} not in catalog"

    def test_all_catalog_scenarios_in_map(self, scenario_catalog, traceability_map):
        """Every scenario in catalog exists in map."""
        catalog_ids = {s["id"] for s in scenario_catalog["scenarios"]}
        map_ids = {s["id"] for s in traceability_map["scenarios"]}
        for catalog_id in catalog_ids:
            assert catalog_id in map_ids, f"Catalog scenario {catalog_id} not in map"

    def test_all_statuses_valid(self, traceability_map):
        """Every scenario in map has a valid status."""
        scenarios = traceability_map["scenarios"]
        for scenario in scenarios:
            assert "status" in scenario, f"Scenario {scenario.get('id')} missing status"
            status = scenario["status"]
            assert status in ALLOWED_STATUSES, (
                f"Scenario {scenario['id']} has invalid status '{status}'. "
                f"Must be one of: {', '.join(sorted(ALLOWED_STATUSES))}"
            )

    def test_implementation_paths_is_list(self, traceability_map):
        """implementation_paths is always a list."""
        scenarios = traceability_map["scenarios"]
        for scenario in scenarios:
            assert "implementation_paths" in scenario
            paths = scenario["implementation_paths"]
            assert isinstance(paths, list), (
                f"Scenario {scenario['id']} implementation_paths must be list, got {type(paths)}"
            )

    def test_test_paths_is_list(self, traceability_map):
        """test_paths is always a list."""
        scenarios = traceability_map["scenarios"]
        for scenario in scenarios:
            assert "test_paths" in scenario
            paths = scenario["test_paths"]
            assert isinstance(paths, list), (
                f"Scenario {scenario['id']} test_paths must be list, got {type(paths)}"
            )

    def test_implementation_paths_exist(self, traceability_map, repo_root):
        """Every implementation_path exists in repo."""
        scenarios = traceability_map["scenarios"]
        for scenario in scenarios:
            paths = scenario.get("implementation_paths", [])
            for path in paths:
                if path:  # Skip empty strings
                    full_path = repo_root / path
                    assert full_path.exists(), (
                        f"Scenario {scenario['id']}: implementation_path {path} does not exist"
                    )

    def test_test_paths_exist(self, traceability_map, repo_root):
        """Every test_path exists in repo.

        A test_path is either a test file or a pytest nodeid
        (``tests/test_x.py::TestClass::test_fn``). For a nodeid the file
        must exist AND the named test function must be defined in it --
        a nodeid naming a function that is not there is exactly the
        fabricated-evidence case this check exists to catch.
        """
        scenarios = traceability_map["scenarios"]
        for scenario in scenarios:
            paths = scenario.get("test_paths", [])
            for path in paths:
                if not path:  # Skip empty strings
                    continue
                file_part, _, node_part = path.partition("::")
                full_path = repo_root / file_part
                assert full_path.exists(), (
                    f"Scenario {scenario['id']}: test_path {path} does not exist"
                )
                if node_part:
                    func_name = node_part.split("::")[-1].split("[")[0]
                    source = full_path.read_text()
                    assert f"def {func_name}(" in source, (
                        f"Scenario {scenario['id']}: test_path {path} names a test "
                        f"function that is not defined in {file_part}"
                    )

    def test_no_stray_scenarios(self, scenario_catalog, traceability_map):
        """No scenarios in map that aren't in catalog."""
        catalog_ids = {s["id"] for s in scenario_catalog["scenarios"]}
        map_ids = {s["id"] for s in traceability_map["scenarios"]}
        stray = map_ids - catalog_ids
        assert not stray, f"Map has scenarios not in catalog: {stray}"


class TestRequirementsTraceability:
    """Validate generated requirements_traceability.json (if it exists)."""

    def test_build_traceability_script_executable(self, docs_dir):
        """build_traceability.py exists."""
        script = docs_dir / "build_traceability.py"
        assert script.exists(), f"build_traceability.py not found at {script}"

    def test_build_traceability_runs(self, docs_dir, tmp_path):
        """build_traceability.py runs without error; writes to tmp_path so the
        tracked requirements_traceability.json is never rewritten by a test."""
        # Note: Only run if PyYAML is available
        try:
            import subprocess
            result = subprocess.run(
                ["python3", str(docs_dir / "build_traceability.py"), "--output", str(tmp_path / "rt.json")],
                capture_output=True,
                text=True,
                cwd=str(docs_dir.parent.parent),
                timeout=10,
            )
            assert result.returncode == 0, (
                f"build_traceability.py failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
            )
        except ImportError:
            pytest.skip("PyYAML not installed; skipping build_traceability.py test")

    def test_requirements_traceability_generated(self, docs_dir):
        """requirements_traceability.json was generated (optional)."""
        path = docs_dir / "requirements_traceability.json"
        if path.exists():
            with open(path) as f:
                data = json.load(f)
            assert "scenarios" in data
            assert len(data["scenarios"]) == 292


class TestWorkflowModules:
    """Validate new workflow modules (reasons.py, money.py, __init__.py)."""

    def test_workflow_init_exists(self, repo_root):
        """app/workflow/__init__.py exists."""
        path = repo_root / "app" / "workflow" / "__init__.py"
        assert path.exists(), "app/workflow/__init__.py not found"

    def test_workflow_reasons_exists(self, repo_root):
        """app/workflow/reasons.py exists."""
        path = repo_root / "app" / "workflow" / "reasons.py"
        assert path.exists(), "app/workflow/reasons.py not found"

    def test_workflow_money_exists(self, repo_root):
        """app/workflow/money.py exists."""
        path = repo_root / "app" / "workflow" / "money.py"
        assert path.exists(), "app/workflow/money.py not found"

    def test_reason_enum_importable(self, repo_root):
        """Reason enum can be imported."""
        import sys
        sys.path.insert(0, str(repo_root))
        try:
            from app.workflow.reasons import Reason, ADMISSION_BLOCKING_ORDER
            assert hasattr(Reason, "AUTH_REJECTED")
            assert hasattr(Reason, "CLOSED")
            assert isinstance(ADMISSION_BLOCKING_ORDER, list)
            assert len(ADMISSION_BLOCKING_ORDER) > 0
        finally:
            sys.path.pop(0)

    def test_money_module_importable(self, repo_root):
        """Money module can be imported."""
        import sys
        sys.path.insert(0, str(repo_root))
        try:
            from app.workflow.money import Cents, to_cents
            from decimal import Decimal

            # Test Cents type
            assert Cents == int

            # Test to_cents function
            # Note: integers are treated as already in cents (HEAD's WC-04 semantics)
            assert to_cents(10) == 10  # 10 cents already
            assert to_cents("10.50") == 1050  # String "10.50" = $10.50 = 1050 cents
            assert to_cents(Decimal("10.50")) == 1050  # Decimal 10.50 = $10.50 = 1050 cents
        finally:
            sys.path.pop(0)


class TestInventoryDocumentation:
    """Validate inventory documentation files."""

    def test_inventory_md_exists(self, docs_dir):
        """INVENTORY.md exists."""
        path = docs_dir / "INVENTORY.md"
        assert path.exists(), "INVENTORY.md not found"

    def test_inventory_md_content(self, docs_dir):
        """INVENTORY.md has required sections."""
        path = docs_dir / "INVENTORY.md"
        content = path.read_text()

        required_sections = [
            "HTTP Routes",
            "Database Schema Tables",
            "Broker Adapters",
            "Signal Sources",
            "Signal Model Fields",
            "DestinationAccount Model Fields",
            "Scheduled Jobs",
            "Release Gates",
        ]
        for section in required_sections:
            assert section in content, f"INVENTORY.md missing section: {section}"

    def test_effective_policy_md_exists(self, docs_dir):
        """EFFECTIVE_POLICY.md exists."""
        path = docs_dir / "EFFECTIVE_POLICY.md"
        assert path.exists(), "EFFECTIVE_POLICY.md not found"

    def test_effective_policy_md_content(self, docs_dir):
        """EFFECTIVE_POLICY.md has required sections."""
        path = docs_dir / "EFFECTIVE_POLICY.md"
        content = path.read_text()

        required_sections = [
            "Configuration Hierarchy",
            "Conflict Resolution Rules",
            "Effective Policy Invariants",
        ]
        for section in required_sections:
            assert section in content, f"EFFECTIVE_POLICY.md missing section: {section}"
