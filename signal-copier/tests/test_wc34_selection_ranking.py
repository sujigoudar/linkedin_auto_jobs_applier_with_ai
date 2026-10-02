"""WC-34: Deterministic account ranking in selection (§5.2).

Tests the select_account function with criteria precedence:
1. Feasibility (infeasible candidates excluded with rank=None)
2. Released strategy/account preference (explicit ordering)
3. Full order-recipe qualification (known > unknown)
4. Lower incremental concentration (basis points)
5. Lower incremental stress (cents)
6. Lower cost (cents)
7. Fresher evidence (more recent > older > unknown)
8. Stable account ID (lexicographic tiebreaker)

Invariants: I01 (single canonical selection, alternatives with reasons),
I04 (determinism from input shuffling).
"""
from __future__ import annotations

from datetime import datetime
from datetime import timedelta

import pytest

from app.workflow.selection import Candidate
from app.workflow.selection import RankingPolicy
from app.workflow.selection import select_account


class TestFeasibilityFirst:
    """Infeasible candidates are excluded regardless of ranking inputs."""

    def test_infeasible_not_selected_even_if_preferred(self):
        """Infeasible candidate with preference is not selected."""
        candidates = [
            Candidate(
                physical_account_id="pa1",
                feasible=False,
                rank=None,
                reason="cannot_afford_one_unit",
                recipe_fully_qualified=True,
            ),
            Candidate(
                physical_account_id="pa2",
                feasible=True,
                rank=None,
                reason="",
                recipe_fully_qualified=False,
            ),
        ]
        policy = RankingPolicy(strategy_account_preference=["pa1"])
        selection = select_account(candidates, policy)

        # pa2 is selected even though pa1 is preferred, because pa1 is infeasible.
        assert selection.selected_physical_account_id == "pa2"
        selected = next(c for c in selection.all_candidates if c.physical_account_id == "pa2")
        assert selected.rank == 1
        assert selected.reason == "selected"

        # pa1 has rank=None and exclusion reason preserved.
        infeasible = next(c for c in selection.all_candidates if c.physical_account_id == "pa1")
        assert infeasible.rank is None
        assert infeasible.reason == "cannot_afford_one_unit"

    def test_all_infeasible_raises_error(self):
        """ValueError when all candidates are infeasible."""
        candidates = [
            Candidate(
                physical_account_id="pa1",
                feasible=False,
                rank=None,
                reason="cannot_afford_one_unit",
            ),
            Candidate(
                physical_account_id="pa2",
                feasible=False,
                rank=None,
                reason="unauthorized_instrument",
            ),
        ]
        policy = RankingPolicy(strategy_account_preference=[])
        with pytest.raises(ValueError, match="no feasible candidates"):
            select_account(candidates, policy)


class TestPreferenceOrdering:
    """Preference index determines rank when other criteria tie."""

    def test_preference_decides_alone(self):
        """Earlier preference index wins."""
        candidates = [
            Candidate(
                physical_account_id="pa3",
                feasible=True,
                rank=None,
                reason="",
                recipe_fully_qualified=True,
            ),
            Candidate(
                physical_account_id="pa1",
                feasible=True,
                rank=None,
                reason="",
                recipe_fully_qualified=True,
            ),
            Candidate(
                physical_account_id="pa2",
                feasible=True,
                rank=None,
                reason="",
                recipe_fully_qualified=True,
            ),
        ]
        # pa2 is preferred first, pa1 second, pa3 unpreferred.
        policy = RankingPolicy(strategy_account_preference=["pa2", "pa1"])
        selection = select_account(candidates, policy)

        assert selection.selected_physical_account_id == "pa2"
        candidates_by_id = {c.physical_account_id: c for c in selection.all_candidates}
        assert candidates_by_id["pa2"].rank == 1
        assert candidates_by_id["pa1"].rank == 2
        assert candidates_by_id["pa3"].rank == 3

    def test_unordered_preference_set_ties(self):
        """Unordered set (all members tie on preference)."""
        candidates = [
            Candidate(
                physical_account_id="pa1",
                feasible=True,
                rank=None,
                reason="",
                recipe_fully_qualified=True,
            ),
            Candidate(
                physical_account_id="pa2",
                feasible=True,
                rank=None,
                reason="",
                recipe_fully_qualified=True,
            ),
        ]
        # Both in preference set, so preference ties; tiebreaker is stable ID.
        policy = RankingPolicy(strategy_account_preference={"pa1", "pa2"})
        selection = select_account(candidates, policy)

        # Lexicographic tiebreaker: pa1 < pa2.
        assert selection.selected_physical_account_id == "pa1"


class TestRecipeQualification:
    """Full recipe qualification ranks known before unknown."""

    def test_qualified_beats_unqualified(self):
        """recipe_fully_qualified=True ranks before False."""
        candidates = [
            Candidate(
                physical_account_id="pa1",
                feasible=True,
                rank=None,
                reason="",
                recipe_fully_qualified=False,
            ),
            Candidate(
                physical_account_id="pa2",
                feasible=True,
                rank=None,
                reason="",
                recipe_fully_qualified=True,
            ),
        ]
        policy = RankingPolicy(strategy_account_preference=[])
        selection = select_account(candidates, policy)

        assert selection.selected_physical_account_id == "pa2"

    def test_known_beats_unknown_qualification(self):
        """recipe_fully_qualified=True beats None."""
        candidates = [
            Candidate(
                physical_account_id="pa1",
                feasible=True,
                rank=None,
                reason="",
                recipe_fully_qualified=None,
            ),
            Candidate(
                physical_account_id="pa2",
                feasible=True,
                rank=None,
                reason="",
                recipe_fully_qualified=True,
            ),
        ]
        policy = RankingPolicy(strategy_account_preference=[])
        selection = select_account(candidates, policy)

        assert selection.selected_physical_account_id == "pa2"

    def test_unknown_qualification_loses_to_known(self):
        """recipe_fully_qualified=None ranks after False."""
        candidates = [
            Candidate(
                physical_account_id="pa1",
                feasible=True,
                rank=None,
                reason="",
                recipe_fully_qualified=False,
            ),
            Candidate(
                physical_account_id="pa2",
                feasible=True,
                rank=None,
                reason="",
                recipe_fully_qualified=None,
            ),
        ]
        policy = RankingPolicy(strategy_account_preference=[])
        selection = select_account(candidates, policy)

        # pa1 (False) beats pa2 (unknown).
        assert selection.selected_physical_account_id == "pa1"


class TestConcentration:
    """Lower incremental concentration ranks first."""

    def test_lower_concentration_wins(self):
        """Lower concentration_bps ranks first."""
        candidates = [
            Candidate(
                physical_account_id="pa1",
                feasible=True,
                rank=None,
                reason="",
                incremental_concentration_bps=100,
            ),
            Candidate(
                physical_account_id="pa2",
                feasible=True,
                rank=None,
                reason="",
                incremental_concentration_bps=50,
            ),
        ]
        policy = RankingPolicy(strategy_account_preference=[])
        selection = select_account(candidates, policy)

        assert selection.selected_physical_account_id == "pa2"

    def test_known_concentration_beats_unknown(self):
        """Known concentration_bps beats None."""
        candidates = [
            Candidate(
                physical_account_id="pa1",
                feasible=True,
                rank=None,
                reason="",
                incremental_concentration_bps=None,
            ),
            Candidate(
                physical_account_id="pa2",
                feasible=True,
                rank=None,
                reason="",
                incremental_concentration_bps=100,
            ),
        ]
        policy = RankingPolicy(strategy_account_preference=[])
        selection = select_account(candidates, policy)

        assert selection.selected_physical_account_id == "pa2"

    def test_concentration_boundary_minus_one(self):
        """Boundary test: concentration - 1 bps wins."""
        candidates = [
            Candidate(
                physical_account_id="pa1",
                feasible=True,
                rank=None,
                reason="",
                incremental_concentration_bps=100,
            ),
            Candidate(
                physical_account_id="pa2",
                feasible=True,
                rank=None,
                reason="",
                incremental_concentration_bps=99,
            ),
        ]
        policy = RankingPolicy(strategy_account_preference=[])
        selection = select_account(candidates, policy)

        assert selection.selected_physical_account_id == "pa2"

    def test_concentration_boundary_plus_one(self):
        """Boundary test: concentration + 1 bps loses."""
        candidates = [
            Candidate(
                physical_account_id="pa1",
                feasible=True,
                rank=None,
                reason="",
                incremental_concentration_bps=100,
            ),
            Candidate(
                physical_account_id="pa2",
                feasible=True,
                rank=None,
                reason="",
                incremental_concentration_bps=101,
            ),
        ]
        policy = RankingPolicy(strategy_account_preference=[])
        selection = select_account(candidates, policy)

        assert selection.selected_physical_account_id == "pa1"


class TestStress:
    """Lower incremental stress ranks first."""

    def test_lower_stress_wins(self):
        """Lower stress_cents ranks first."""
        candidates = [
            Candidate(
                physical_account_id="pa1",
                feasible=True,
                rank=None,
                reason="",
                incremental_stress_cents=1000,
            ),
            Candidate(
                physical_account_id="pa2",
                feasible=True,
                rank=None,
                reason="",
                incremental_stress_cents=500,
            ),
        ]
        policy = RankingPolicy(strategy_account_preference=[])
        selection = select_account(candidates, policy)

        assert selection.selected_physical_account_id == "pa2"

    def test_known_stress_beats_unknown(self):
        """Known stress_cents beats None."""
        candidates = [
            Candidate(
                physical_account_id="pa1",
                feasible=True,
                rank=None,
                reason="",
                incremental_stress_cents=None,
            ),
            Candidate(
                physical_account_id="pa2",
                feasible=True,
                rank=None,
                reason="",
                incremental_stress_cents=1000,
            ),
        ]
        policy = RankingPolicy(strategy_account_preference=[])
        selection = select_account(candidates, policy)

        assert selection.selected_physical_account_id == "pa2"

    def test_stress_boundary_minus_one(self):
        """Boundary test: stress - 1 cent wins."""
        candidates = [
            Candidate(
                physical_account_id="pa1",
                feasible=True,
                rank=None,
                reason="",
                incremental_stress_cents=1000,
            ),
            Candidate(
                physical_account_id="pa2",
                feasible=True,
                rank=None,
                reason="",
                incremental_stress_cents=999,
            ),
        ]
        policy = RankingPolicy(strategy_account_preference=[])
        selection = select_account(candidates, policy)

        assert selection.selected_physical_account_id == "pa2"

    def test_stress_boundary_plus_one(self):
        """Boundary test: stress + 1 cent loses."""
        candidates = [
            Candidate(
                physical_account_id="pa1",
                feasible=True,
                rank=None,
                reason="",
                incremental_stress_cents=1000,
            ),
            Candidate(
                physical_account_id="pa2",
                feasible=True,
                rank=None,
                reason="",
                incremental_stress_cents=1001,
            ),
        ]
        policy = RankingPolicy(strategy_account_preference=[])
        selection = select_account(candidates, policy)

        assert selection.selected_physical_account_id == "pa1"


class TestCost:
    """Lower estimated cost ranks first."""

    def test_lower_cost_wins(self):
        """Lower cost_cents ranks first."""
        candidates = [
            Candidate(
                physical_account_id="pa1",
                feasible=True,
                rank=None,
                reason="",
                estimated_cost_cents=500,
            ),
            Candidate(
                physical_account_id="pa2",
                feasible=True,
                rank=None,
                reason="",
                estimated_cost_cents=250,
            ),
        ]
        policy = RankingPolicy(strategy_account_preference=[])
        selection = select_account(candidates, policy)

        assert selection.selected_physical_account_id == "pa2"

    def test_known_cost_beats_unknown(self):
        """Known cost_cents beats None."""
        candidates = [
            Candidate(
                physical_account_id="pa1",
                feasible=True,
                rank=None,
                reason="",
                estimated_cost_cents=None,
            ),
            Candidate(
                physical_account_id="pa2",
                feasible=True,
                rank=None,
                reason="",
                estimated_cost_cents=500,
            ),
        ]
        policy = RankingPolicy(strategy_account_preference=[])
        selection = select_account(candidates, policy)

        assert selection.selected_physical_account_id == "pa2"

    def test_cost_boundary_minus_one(self):
        """Boundary test: cost - 1 cent wins."""
        candidates = [
            Candidate(
                physical_account_id="pa1",
                feasible=True,
                rank=None,
                reason="",
                estimated_cost_cents=500,
            ),
            Candidate(
                physical_account_id="pa2",
                feasible=True,
                rank=None,
                reason="",
                estimated_cost_cents=499,
            ),
        ]
        policy = RankingPolicy(strategy_account_preference=[])
        selection = select_account(candidates, policy)

        assert selection.selected_physical_account_id == "pa2"

    def test_cost_boundary_plus_one(self):
        """Boundary test: cost + 1 cent loses."""
        candidates = [
            Candidate(
                physical_account_id="pa1",
                feasible=True,
                rank=None,
                reason="",
                estimated_cost_cents=500,
            ),
            Candidate(
                physical_account_id="pa2",
                feasible=True,
                rank=None,
                reason="",
                estimated_cost_cents=501,
            ),
        ]
        policy = RankingPolicy(strategy_account_preference=[])
        selection = select_account(candidates, policy)

        assert selection.selected_physical_account_id == "pa1"


class TestEvidence:
    """Fresher evidence ranks first."""

    def test_fresher_evidence_wins(self):
        """More recent datetime ranks first."""
        now = datetime.utcnow()
        candidates = [
            Candidate(
                physical_account_id="pa1",
                feasible=True,
                rank=None,
                reason="",
                evidence_as_of=now - timedelta(hours=1),
            ),
            Candidate(
                physical_account_id="pa2",
                feasible=True,
                rank=None,
                reason="",
                evidence_as_of=now,
            ),
        ]
        policy = RankingPolicy(strategy_account_preference=[])
        selection = select_account(candidates, policy)

        assert selection.selected_physical_account_id == "pa2"

    def test_known_evidence_beats_unknown(self):
        """Known evidence_as_of beats None."""
        now = datetime.utcnow()
        candidates = [
            Candidate(
                physical_account_id="pa1",
                feasible=True,
                rank=None,
                reason="",
                evidence_as_of=None,
            ),
            Candidate(
                physical_account_id="pa2",
                feasible=True,
                rank=None,
                reason="",
                evidence_as_of=now,
            ),
        ]
        policy = RankingPolicy(strategy_account_preference=[])
        selection = select_account(candidates, policy)

        assert selection.selected_physical_account_id == "pa2"

    def test_evidence_boundary_one_second_older(self):
        """Boundary test: evidence 1 second older loses."""
        now = datetime.utcnow()
        candidates = [
            Candidate(
                physical_account_id="pa1",
                feasible=True,
                rank=None,
                reason="",
                evidence_as_of=now - timedelta(seconds=1),
            ),
            Candidate(
                physical_account_id="pa2",
                feasible=True,
                rank=None,
                reason="",
                evidence_as_of=now,
            ),
        ]
        policy = RankingPolicy(strategy_account_preference=[])
        selection = select_account(candidates, policy)

        assert selection.selected_physical_account_id == "pa2"

    def test_evidence_boundary_one_second_newer(self):
        """Boundary test: evidence 1 second newer wins."""
        now = datetime.utcnow()
        candidates = [
            Candidate(
                physical_account_id="pa1",
                feasible=True,
                rank=None,
                reason="",
                evidence_as_of=now,
            ),
            Candidate(
                physical_account_id="pa2",
                feasible=True,
                rank=None,
                reason="",
                evidence_as_of=now + timedelta(seconds=1),
            ),
        ]
        policy = RankingPolicy(strategy_account_preference=[])
        selection = select_account(candidates, policy)

        assert selection.selected_physical_account_id == "pa2"


class TestStableAccountId:
    """Lexicographic account ID is final tiebreaker."""

    def test_account_id_tiebreaker(self):
        """Stable account ID breaks ties when all other criteria equal."""
        candidates = [
            Candidate(
                physical_account_id="pa_z",
                feasible=True,
                rank=None,
                reason="",
            ),
            Candidate(
                physical_account_id="pa_a",
                feasible=True,
                rank=None,
                reason="",
            ),
            Candidate(
                physical_account_id="pa_m",
                feasible=True,
                rank=None,
                reason="",
            ),
        ]
        policy = RankingPolicy(strategy_account_preference=[])
        selection = select_account(candidates, policy)

        assert selection.selected_physical_account_id == "pa_a"


class TestCriteriaPrecedence:
    """Earlier criteria override later ones (preference > qualification > ... > id)."""

    def test_preference_beats_qualification(self):
        """Preference wins over recipe qualification."""
        candidates = [
            Candidate(
                physical_account_id="pa1",
                feasible=True,
                rank=None,
                reason="",
                recipe_fully_qualified=True,
            ),
            Candidate(
                physical_account_id="pa2",
                feasible=True,
                rank=None,
                reason="",
                recipe_fully_qualified=False,
            ),
        ]
        policy = RankingPolicy(strategy_account_preference=["pa2"])
        selection = select_account(candidates, policy)

        assert selection.selected_physical_account_id == "pa2"

    def test_qualification_beats_concentration(self):
        """Recipe qualification wins over concentration."""
        candidates = [
            Candidate(
                physical_account_id="pa1",
                feasible=True,
                rank=None,
                reason="",
                recipe_fully_qualified=False,
                incremental_concentration_bps=10,
            ),
            Candidate(
                physical_account_id="pa2",
                feasible=True,
                rank=None,
                reason="",
                recipe_fully_qualified=True,
                incremental_concentration_bps=1000,
            ),
        ]
        policy = RankingPolicy(strategy_account_preference=[])
        selection = select_account(candidates, policy)

        assert selection.selected_physical_account_id == "pa2"

    def test_concentration_beats_stress(self):
        """Concentration wins over stress."""
        candidates = [
            Candidate(
                physical_account_id="pa1",
                feasible=True,
                rank=None,
                reason="",
                incremental_concentration_bps=10,
                incremental_stress_cents=10000,
            ),
            Candidate(
                physical_account_id="pa2",
                feasible=True,
                rank=None,
                reason="",
                incremental_concentration_bps=1000,
                incremental_stress_cents=100,
            ),
        ]
        policy = RankingPolicy(strategy_account_preference=[])
        selection = select_account(candidates, policy)

        assert selection.selected_physical_account_id == "pa1"

    def test_stress_beats_cost(self):
        """Stress wins over cost."""
        candidates = [
            Candidate(
                physical_account_id="pa1",
                feasible=True,
                rank=None,
                reason="",
                incremental_stress_cents=100,
                estimated_cost_cents=10000,
            ),
            Candidate(
                physical_account_id="pa2",
                feasible=True,
                rank=None,
                reason="",
                incremental_stress_cents=1000,
                estimated_cost_cents=10,
            ),
        ]
        policy = RankingPolicy(strategy_account_preference=[])
        selection = select_account(candidates, policy)

        assert selection.selected_physical_account_id == "pa1"

    def test_cost_beats_evidence(self):
        """Cost wins over evidence freshness."""
        now = datetime.utcnow()
        candidates = [
            Candidate(
                physical_account_id="pa1",
                feasible=True,
                rank=None,
                reason="",
                estimated_cost_cents=100,
                evidence_as_of=now - timedelta(days=10),
            ),
            Candidate(
                physical_account_id="pa2",
                feasible=True,
                rank=None,
                reason="",
                estimated_cost_cents=1000,
                evidence_as_of=now,
            ),
        ]
        policy = RankingPolicy(strategy_account_preference=[])
        selection = select_account(candidates, policy)

        assert selection.selected_physical_account_id == "pa1"

    def test_evidence_beats_id(self):
        """Evidence freshness wins over stable account ID."""
        now = datetime.utcnow()
        candidates = [
            Candidate(
                physical_account_id="pa_a",
                feasible=True,
                rank=None,
                reason="",
                evidence_as_of=now - timedelta(hours=1),
            ),
            Candidate(
                physical_account_id="pa_z",
                feasible=True,
                rank=None,
                reason="",
                evidence_as_of=now,
            ),
        ]
        policy = RankingPolicy(strategy_account_preference=[])
        selection = select_account(candidates, policy)

        assert selection.selected_physical_account_id == "pa_z"


class TestAlternativeReasons:
    """Alternative candidates have reasons indicating which criterion lost."""

    def test_three_candidates_one_selected_two_alternatives_with_reasons(self):
        """I01: Three feasible -> one selected, two alternatives (I01)."""
        candidates = [
            Candidate(
                physical_account_id="pa1",
                feasible=True,
                rank=None,
                reason="",
                recipe_fully_qualified=True,
                incremental_concentration_bps=50,
                incremental_stress_cents=1000,
                estimated_cost_cents=100,
            ),
            Candidate(
                physical_account_id="pa2",
                feasible=True,
                rank=None,
                reason="",
                recipe_fully_qualified=True,
                incremental_concentration_bps=100,  # Higher.
                incremental_stress_cents=1000,
                estimated_cost_cents=100,
            ),
            Candidate(
                physical_account_id="pa3",
                feasible=True,
                rank=None,
                reason="",
                recipe_fully_qualified=True,
                incremental_concentration_bps=50,  # Tied.
                incremental_stress_cents=2000,  # Higher.
                estimated_cost_cents=100,
            ),
        ]
        policy = RankingPolicy(strategy_account_preference=[])
        selection = select_account(candidates, policy)

        assert selection.selected_physical_account_id == "pa1"

        # Find candidates by ID in result.
        result_by_id = {c.physical_account_id: c for c in selection.all_candidates}

        # pa1 is selected.
        assert result_by_id["pa1"].rank == 1
        assert result_by_id["pa1"].reason == "selected"

        # pa3 lost on stress (tied on concentration, but higher stress).
        assert result_by_id["pa3"].rank == 2
        assert result_by_id["pa3"].reason == "alternative:stress"

        # pa2 lost on concentration (highest concentration).
        assert result_by_id["pa2"].rank == 3
        assert result_by_id["pa2"].reason == "alternative:concentration"

    def test_alternative_reason_preference(self):
        """Alternative lost on preference."""
        candidates = [
            Candidate(
                physical_account_id="pa1",
                feasible=True,
                rank=None,
                reason="",
            ),
            Candidate(
                physical_account_id="pa2",
                feasible=True,
                rank=None,
                reason="",
            ),
        ]
        policy = RankingPolicy(strategy_account_preference=["pa1"])
        selection = select_account(candidates, policy)

        result_by_id = {c.physical_account_id: c for c in selection.all_candidates}
        assert result_by_id["pa1"].rank == 1
        assert result_by_id["pa2"].rank == 2
        assert result_by_id["pa2"].reason == "alternative:preference"

    def test_alternative_reason_qualification(self):
        """Alternative lost on recipe qualification."""
        candidates = [
            Candidate(
                physical_account_id="pa1",
                feasible=True,
                rank=None,
                reason="",
                recipe_fully_qualified=True,
            ),
            Candidate(
                physical_account_id="pa2",
                feasible=True,
                rank=None,
                reason="",
                recipe_fully_qualified=False,
            ),
        ]
        policy = RankingPolicy(strategy_account_preference=[])
        selection = select_account(candidates, policy)

        result_by_id = {c.physical_account_id: c for c in selection.all_candidates}
        assert result_by_id["pa2"].rank == 2
        assert result_by_id["pa2"].reason == "alternative:recipe_fully_qualified"

    def test_alternative_reason_account_id(self):
        """Alternative lost on stable account ID."""
        candidates = [
            Candidate(
                physical_account_id="pa_z",
                feasible=True,
                rank=None,
                reason="",
            ),
            Candidate(
                physical_account_id="pa_a",
                feasible=True,
                rank=None,
                reason="",
            ),
        ]
        policy = RankingPolicy(strategy_account_preference=[])
        selection = select_account(candidates, policy)

        result_by_id = {c.physical_account_id: c for c in selection.all_candidates}
        assert result_by_id["pa_z"].rank == 2
        assert result_by_id["pa_z"].reason == "alternative:account_id"


class TestDeterminism:
    """Same inputs in different order produce the same selection."""

    def test_determinism_shuffled_input_order(self):
        """Same candidates in different input order -> same result."""
        base_candidates = [
            Candidate(
                physical_account_id="pa1",
                feasible=True,
                rank=None,
                reason="",
                recipe_fully_qualified=True,
                incremental_concentration_bps=100,
            ),
            Candidate(
                physical_account_id="pa2",
                feasible=True,
                rank=None,
                reason="",
                recipe_fully_qualified=True,
                incremental_concentration_bps=50,
            ),
            Candidate(
                physical_account_id="pa3",
                feasible=True,
                rank=None,
                reason="",
                recipe_fully_qualified=True,
                incremental_concentration_bps=75,
            ),
        ]
        policy = RankingPolicy(strategy_account_preference=[])

        # Test with different input orders.
        orders = [
            [0, 1, 2],  # Original.
            [2, 1, 0],  # Reversed.
            [1, 0, 2],  # Shuffled.
        ]

        results = []
        for order in orders:
            candidates = [base_candidates[i] for i in order]
            selection = select_account(candidates, policy)
            results.append(selection.selected_physical_account_id)

        # All should select pa2 (lowest concentration).
        assert len(set(results)) == 1
        assert results[0] == "pa2"

    def test_determinism_infeasible_order(self):
        """Infeasible candidates in different positions don't affect result."""
        candidates_order1 = [
            Candidate(
                physical_account_id="pa_infeas",
                feasible=False,
                rank=None,
                reason="cannot_afford",
            ),
            Candidate(
                physical_account_id="pa1",
                feasible=True,
                rank=None,
                reason="",
                estimated_cost_cents=100,
            ),
        ]

        candidates_order2 = [
            Candidate(
                physical_account_id="pa1",
                feasible=True,
                rank=None,
                reason="",
                estimated_cost_cents=100,
            ),
            Candidate(
                physical_account_id="pa_infeas",
                feasible=False,
                rank=None,
                reason="cannot_afford",
            ),
        ]

        policy = RankingPolicy(strategy_account_preference=[])

        sel1 = select_account(candidates_order1, policy)
        sel2 = select_account(candidates_order2, policy)

        assert sel1.selected_physical_account_id == sel2.selected_physical_account_id
        assert sel1.selected_physical_account_id == "pa1"
