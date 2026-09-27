"""AD-04 "Portfolio Lab builder" -- the real query/command service
backing the research-run builder. See dashboard_spec/screens/AD-04.md
for the full screen contract this implements a bounded slice of.

Bounded scope: create/save/reload a run declaration, and a real
"Preview: Compute full candidate denominator" that reuses
app/services/portfolio_research.py's own deterministic combinatorics
(never re-derived here). "Confirm: Enqueue research job only" is NOT
implemented -- there is no job queue, and running the actual walk-
forward study needs real authorized historical sleeve data this
environment doesn't have; a saved, previewed declaration is exactly as
far as this build can honestly go.
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.research_run import ResearchRun
from app.models.sleeve import Sleeve
from app.services.portfolio_research import (
    MAX_SLEEVE_WEIGHT,
    TooManyEligibleSleevesError,
    benchmark_subsets,
    enumerate_candidate_subsets,
)

#: Only `equal_weight_recipe` is actually implemented
#: (app/services/portfolio_research.py's own docstring: "does NOT
#: implement... the other three recipes"). Any other requested recipe
#: name is a real, named blocker, never silently accepted.
_IMPLEMENTED_RECIPES = frozenset({"equal_capital"})
_ALL_RECIPE_NAMES = frozenset({"equal_capital", "inverse_volatility", "hrp", "min_cvar"})

#: A clearly-labeled, deterministic ESTIMATE only -- "Unmeasured runtime
#: estimate labeled estimated" (AD-04's own metric definition). Not a
#: measured runtime; no job has ever actually run.
_ESTIMATED_RESOURCE_UNITS_PER_CANDIDATE = 1


class InvalidResearchRunError(Exception):
    pass


def list_research_runs(session: Session, *, tenant_id: str) -> list[ResearchRun]:
    return list(
        session.scalars(
            select(ResearchRun).where(ResearchRun.tenant_id == tenant_id).order_by(ResearchRun.created_at.desc())
        ).all()
    )


def get_research_run(session: Session, research_run_id: str, *, tenant_id: str) -> ResearchRun | None:
    run = session.get(ResearchRun, research_run_id)
    if run is None or run.tenant_id != tenant_id:
        return None
    return run


def _validate_fields(
    *,
    subset_min: int,
    subset_max: int,
    cash_bps: int,
    max_sleeve_bps: int,
    max_cluster_bps: int,
    holdout_fraction: Decimal,
) -> None:
    if subset_min < 1 or subset_max < subset_min:
        raise InvalidResearchRunError(f"invalid subset bounds: min={subset_min}, max={subset_max}")
    if not (0 <= cash_bps <= 10_000):
        raise InvalidResearchRunError("cash_bps must be between 0 and 10000")
    if not (0 <= max_sleeve_bps <= 10_000):
        raise InvalidResearchRunError("max_sleeve_bps must be between 0 and 10000")
    if not (0 <= max_cluster_bps <= 10_000):
        raise InvalidResearchRunError("max_cluster_bps must be between 0 and 10000")
    if not (Decimal("0") < holdout_fraction < Decimal("1")):
        raise InvalidResearchRunError("holdout_fraction must be strictly between 0 and 1")


def create_research_run(
    session: Session,
    *,
    tenant_id: str,
    sleeve_ids: list[str],
    recipes: list[str],
    subset_min: int,
    subset_max: int,
    cash_bps: int,
    max_sleeve_bps: int,
    max_cluster_bps: int,
    train_sessions: int,
    test_sessions: int,
    holdout_fraction: Decimal,
    cost_scenario_ids: list[str],
    resource_profile_id: str | None,
) -> ResearchRun:
    _validate_fields(
        subset_min=subset_min,
        subset_max=subset_max,
        cash_bps=cash_bps,
        max_sleeve_bps=max_sleeve_bps,
        max_cluster_bps=max_cluster_bps,
        holdout_fraction=holdout_fraction,
    )
    unknown_recipes = set(recipes) - _ALL_RECIPE_NAMES
    if unknown_recipes:
        raise InvalidResearchRunError(f"unknown recipe(s): {sorted(unknown_recipes)}")

    run = ResearchRun(
        tenant_id=tenant_id,
        sleeve_ids=sleeve_ids,
        recipes=recipes,
        subset_min=subset_min,
        subset_max=subset_max,
        cash_bps=cash_bps,
        max_sleeve_bps=max_sleeve_bps,
        max_cluster_bps=max_cluster_bps,
        train_sessions=train_sessions,
        test_sessions=test_sessions,
        holdout_fraction=holdout_fraction,
        cost_scenario_ids=cost_scenario_ids,
        resource_profile_id=resource_profile_id,
    )
    session.add(run)
    session.flush()
    return run


class ResearchRunPreview:
    def __init__(self) -> None:
        self.blockers: list[str] = []
        self.declared_candidates_count = 0
        self.benchmark_count = 0
        self.expected_resource_budget_estimated = 0


def compute_research_run_preview(session: Session, run: ResearchRun, *, tenant_id: str) -> ResearchRunPreview:
    """AD-04's own "Preview: Compute full candidate denominator, data
    gaps, resource quote and immutable manifest." Never collapses its
    findings into a single boolean -- every named blocker is reported,
    matching the same discipline as product_admin.compute_publication_blockers."""
    preview = ResearchRunPreview()

    unknown_sleeve_ids = [
        sid
        for sid in run.sleeve_ids
        if (sleeve := session.get(Sleeve, sid)) is None or sleeve.tenant_id != tenant_id
    ]
    for sid in unknown_sleeve_ids:
        preview.blockers.append(f"UNKNOWN_SLEEVE:{sid}")

    unimplemented_recipes = sorted(set(run.recipes) - _IMPLEMENTED_RECIPES)
    for recipe in unimplemented_recipes:
        preview.blockers.append(f"RECIPE_NOT_IMPLEMENTED:{recipe}")
    if not run.recipes:
        preview.blockers.append("NO_RECIPE_SELECTED")

    if run.cash_bps + run.max_sleeve_bps > 10_000:
        preview.blockers.append("CASH_AND_SLEEVE_CEILING_EXCEED_TEN_THOUSAND_BPS")
    if Decimal(run.max_sleeve_bps) / Decimal(10_000) > MAX_SLEEVE_WEIGHT:
        preview.blockers.append("SLEEVE_CEILING_EXCEEDS_IMPLEMENTED_RECIPE_CAP")

    if not unknown_sleeve_ids:
        try:
            candidates = enumerate_candidate_subsets(run.sleeve_ids, min_size=run.subset_min, max_size=run.subset_max)
            benchmarks = benchmark_subsets(run.sleeve_ids)
        except TooManyEligibleSleevesError as exc:
            preview.blockers.append(f"TOO_MANY_ELIGIBLE_SLEEVES:{exc}")
        except ValueError as exc:
            preview.blockers.append(f"INVALID_SUBSET_BOUNDS:{exc}")
        else:
            preview.declared_candidates_count = len(candidates)
            preview.benchmark_count = len(benchmarks)
            preview.expected_resource_budget_estimated = (
                len(candidates) + len(benchmarks)
            ) * _ESTIMATED_RESOURCE_UNITS_PER_CANDIDATE
            if not candidates:
                preview.blockers.append("NO_CANDIDATES_FROM_DECLARED_UNIVERSE")

    return preview
