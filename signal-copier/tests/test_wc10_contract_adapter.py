"""WC-10 tests: Contract adapter for the restricted planning suites.

Tests the workflow_adapter.run_case function against the admission and linear_sizing
suites, ensuring zero errors and proper handling of all input combinations.

- Sample test: 40 cases per suite run through run_contracts' compare function
- Full-run smoke test (behind WC10_FULL=1): all 9,108 cases (4,608 admission + 4,500 sizing)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Load generate_cases and run_contracts from docs/workflow-contract (not a package)
_CONTRACT_DIR = Path(__file__).resolve().parent.parent / "docs" / "workflow-contract"
if str(_CONTRACT_DIR) not in sys.path:
    sys.path.insert(0, str(_CONTRACT_DIR))

from generate_cases import all_cases  # noqa: E402
from run_contracts import compare  # noqa: E402
from tests.workflow_adapter import run_case  # noqa: E402


class TestAdmissionContractAdapter:
    """Sample of 40 admission cases through run_contracts' compare function."""

    @pytest.mark.parametrize("case_index", range(min(40, sum(1 for _ in all_cases("admission")))))
    def test_admission_sample(self, case_index):
        """Run a sample admission case and verify against expected values."""
        case = None
        for i, c in enumerate(all_cases("admission")):
            if i == case_index:
                case = c
                break

        assert case is not None, f"Could not load case at index {case_index}"

        # Run through adapter
        request = {"id": case["id"], "suite": case["suite"], "inputs": case["inputs"]}
        response = run_case(request)

        # Check structure
        assert "actual" in response, f"{case['id']}: missing actual"
        assert "implementation_paths" in response, f"{case['id']}: missing implementation_paths"
        assert "evidence" in response, f"{case['id']}: missing evidence"

        # Compare with expected
        errors = compare(case["expected"], response["actual"])
        assert not errors, f"{case['id']}: {errors}"


class TestLinearSizingContractAdapter:
    """Sample of 40 linear sizing cases through run_contracts' compare function."""

    @pytest.mark.parametrize("case_index", range(min(40, sum(1 for _ in all_cases("linear_sizing")))))
    def test_sizing_sample(self, case_index):
        """Run a sample sizing case and verify against expected values."""
        case = None
        for i, c in enumerate(all_cases("linear_sizing")):
            if i == case_index:
                case = c
                break

        assert case is not None, f"Could not load case at index {case_index}"

        # Run through adapter
        request = {"id": case["id"], "suite": case["suite"], "inputs": case["inputs"]}
        response = run_case(request)

        # Check structure
        assert "actual" in response, f"{case['id']}: missing actual"
        assert "implementation_paths" in response, f"{case['id']}: missing implementation_paths"
        assert "evidence" in response, f"{case['id']}: missing evidence"

        # Compare with expected
        errors = compare(case["expected"], response["actual"])
        assert not errors, f"{case['id']}: {errors}"


@pytest.mark.skipif(
    not os.environ.get("WC10_FULL"),
    reason="Full run disabled; set WC10_FULL=1 to run all 9,108 cases",
)
class TestFullContractRun:
    """Full smoke test: all admission + sizing cases (9,108 total)."""

    def test_full_admission_suite(self):
        """Run all 4,608 admission cases."""
        failed_cases = []
        passed = 0

        for case in all_cases("admission"):
            request = {"id": case["id"], "suite": case["suite"], "inputs": case["inputs"]}
            try:
                response = run_case(request)
                errors = compare(case["expected"], response["actual"])
                if errors:
                    failed_cases.append((case["id"], errors))
                else:
                    passed += 1
            except Exception as exc:
                failed_cases.append((case["id"], str(exc)))

        if failed_cases:
            summary = f"\n{passed} passed, {len(failed_cases)} failed:\n"
            for case_id, error in failed_cases[:10]:  # Show first 10
                summary += f"  {case_id}: {error}\n"
            pytest.fail(summary)

    def test_full_sizing_suite(self):
        """Run all 4,500 linear sizing cases."""
        failed_cases = []
        passed = 0

        for case in all_cases("linear_sizing"):
            request = {"id": case["id"], "suite": case["suite"], "inputs": case["inputs"]}
            try:
                response = run_case(request)
                errors = compare(case["expected"], response["actual"])
                if errors:
                    failed_cases.append((case["id"], errors))
                else:
                    passed += 1
            except Exception as exc:
                failed_cases.append((case["id"], str(exc)))

        if failed_cases:
            summary = f"\n{passed} passed, {len(failed_cases)} failed:\n"
            for case_id, error in failed_cases[:10]:  # Show first 10
                summary += f"  {case_id}: {error}\n"
            pytest.fail(summary)
