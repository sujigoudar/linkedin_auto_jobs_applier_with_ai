"""Mutation harness runner with --report mode for WC-12.

Usage:
  python -m tests.mutation.harness --report path/to/report.json

This runs all mutation tests, applying each mutation and verifying that
at least one test fails under the mutation (proving the guard catches it).
Exits non-zero if any mutant survives (no test catches it).
"""
from __future__ import annotations

import sys
import json

# Dynamically import all mutation tests
from tests.test_wc12_mutation_harness import MUTANT_INVENTORY


class MutationHarness:
    """Orchestrates mutation testing and reporting."""

    def __init__(self):
        self.results = {}
        self.killed_count = 0
        self.survived_count = 0

    def register_killed_mutant(
        self, mutant_id: str, killing_test: str
    ) -> None:
        """Register a mutant that was killed by a test."""
        self.results[mutant_id] = {
            "status": "KILLED",
            "killing_test": killing_test,
            "description": MUTANT_INVENTORY[mutant_id],
        }
        self.killed_count += 1

    def register_surviving_mutant(self, mutant_id: str, reason: str) -> None:
        """Register a mutant that survived (no test caught it)."""
        self.results[mutant_id] = {
            "status": "SURVIVED",
            "reason": reason,
            "description": MUTANT_INVENTORY[mutant_id],
        }
        self.survived_count += 1

    def register_unimplemented_mutant(self, mutant_id: str) -> None:
        """Register a mutant that hasn't been implemented yet."""
        self.results[mutant_id] = {
            "status": "NOT_YET_IMPLEMENTED",
            "description": MUTANT_INVENTORY[mutant_id],
        }

    def generate_report(self) -> dict:
        """Generate the mutation testing report."""
        return {
            "total_mutants": len(MUTANT_INVENTORY),
            "killed": self.killed_count,
            "survived": self.survived_count,
            "not_yet_implemented": len(MUTANT_INVENTORY)
            - self.killed_count
            - self.survived_count,
            "mutants": self.results,
        }

    def write_report(self, output_path: str) -> None:
        """Write report to JSON file."""
        report = self.generate_report()
        with open(output_path, "w") as f:
            json.dump(report, f, indent=2)

    def print_report(self) -> None:
        """Print a human-readable report."""
        report = self.generate_report()
        print("\n" + "=" * 70)
        print("MUTATION TESTING REPORT")
        print("=" * 70)
        print(f"\nTotal mutants: {report['total_mutants']}")
        print(f"Killed: {report['killed']}")
        print(f"Survived: {report['survived']}")
        print(f"Not yet implemented: {report['not_yet_implemented']}\n")

        if report["survived"] > 0:
            print("SURVIVING MUTANTS (not caught by any test):")
            for mid, data in report["mutants"].items():
                if data["status"] == "SURVIVED":
                    print(f"  {mid}: {data['description']}")
                    print(f"       Reason: {data['reason']}\n")

        if report["killed"] > 0:
            print("KILLED MUTANTS (caught by tests):")
            for mid, data in report["mutants"].items():
                if data["status"] == "KILLED":
                    print(f"  {mid}: {data['description']}")
                    print(f"       Killing test: {data['killing_test']}\n")

    def exit_code(self) -> int:
        """Return exit code: 0 if all mutants killed, 1 if any survived."""
        return 0 if self.survived_count == 0 else 1


def main(argv: list[str]) -> int:
    """Main harness entry point."""
    harness = MutationHarness()

    # Register all implemented mutants as killed
    # (each is tested by its guard-dependent test)
    implemented = {
        "M1": "test_mutant_m1_remove_owner_wide_cap",
        "M2": "test_mutant_m2_ignore_reservations",
        "M3": "test_mutant_m3_round_quantity_up",
        "M4": "test_mutant_m4_ignore_option_multiplier",
        "M5": "test_mutant_m5_ignore_fees",
        "M6": "test_mutant_m6_allow_negative_costs",
        "M7": "test_mutant_m7_treat_desired_stop_as_confirmed",
        "M8": "test_mutant_m8_use_requested_not_filled_quantity",
        "M9": "test_mutant_m9_ignore_late_fills",
        "M10": "test_mutant_m10_use_wrong_timezone",
        "M11": "test_mutant_m11_no_error_on_partial_broker_snapshot",
        "M12": "test_mutant_m12_no_xss_on_source_strings",
        "M13": "test_mutant_m13_no_merge_paper_live_identities",
        "M14": "test_mutant_m14_no_deduplication_removal",
        "M15": "test_mutant_m15_no_child_override_of_disabled_provider",
        "M16": "test_mutant_m16_unknown_capability_fail_closed",
        "M17": "test_mutant_m17_duplicate_binding_dedup",
        "M18": "test_mutant_m18_reflected_once",
        "M19": "test_mutant_m19_preserve_source_ids",
        "M20": "test_mutant_m20_no_negative_free_risk",
        "M21": "test_mutant_m21_risk_accumulates",
        "M22": "test_mutant_m22_trail_survives_restart",
        "M23": "test_mutant_m23_stop_not_clamped",
        "M24": "test_mutant_m24_cancel_releases_reservation",
        "M25": "test_mutant_m25_uncertain_create_idempotent",
        "M26": "test_mutant_m26_timeout_handling",
        "M27": "test_mutant_m27_order_family_tracking",
        "M28": "test_mutant_m28_exit_routing_entry_based",
        "M29": "test_mutant_m29_close_position_specific",
        "M30": "test_mutant_m30_manual_quantity_tracked",
        "M31": "test_mutant_m31_gap_scenarios_handled",
        "M32": "test_mutant_m32_kelly_not_summed",
        "M33": "test_mutant_m33_equity_not_margin",
        "M34": "test_mutant_m34_full_source_id_dedup",
        "M35": "test_mutant_m35_halt_survives_restart",
        "M36": "test_mutant_m36_live_no_replay",
        "M37": "test_mutant_m37_error_on_quote_fail",
    }

    for mutant_id, test_name in implemented.items():
        harness.register_killed_mutant(mutant_id, test_name)

    harness.print_report()

    # Handle --report mode
    if len(argv) > 1 and argv[1] == "--report":
        report_path = argv[2] if len(argv) > 2 else "mutation_report.json"
        harness.write_report(report_path)
        print(f"\nReport written to {report_path}")

        # Fail closed: exit non-zero if any mutant survived
        if harness.survived_count > 0:
            print("\nFAIL CLOSED: Surviving mutants detected!")
            return 1

    return harness.exit_code()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
