"""AD-06 "Candidate comparison and shadow report" -- the real
query/command service backing `/ops/research/compare`. See
dashboard_spec/screens/AD-06.md for the full screen contract this
implements a bounded slice of.

Bounded scope: there is no job queue anywhere in this build (see
app/services/research_run.py's own docstring), so no ResearchRun ever
actually finishes a walk-forward study -- there is no real "completed
candidate" with measured net/tail-loss/turnover/coverage/capacity/
holdout-status numbers anywhere for this screen to read. Fabricating
those would violate this build's own "never generate fabricated
returns" rule (app/services/portfolio_research.py's own docstring).
What IS real and reusable: a candidate's DECLARATION -- its exact
sleeve membership (app/services/portfolio_research.py's
`enumerate_candidate_subsets`, the same deterministic combinatorics
AD-04/AD-05 already use) and its deterministic equal-weight capital
allocation (`equal_weight_recipe`) when the run actually requested the
one implemented recipe. This module compares exactly that -- real,
computed composition/allocation and real, computed sleeve overlap
between two declared candidates -- and leaves every performance metric
the spec names (M-AD-06-01/02 and the Return/drawdown, Correlations/
co-loss, Cost/capacity, Rejected results and Shadow qualification
panels) as an honest "not available: no research job has ever run"
gap, never a guessed number. AD-06-A02 "Inspect counterexample" has no
real "counterexample"/evidence concept anywhere in this codebase either
-- `inspect_candidate` below is the simpler, real, read-only substitute
this module actually has data for.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.portfolio_version import PortfolioVersion, PortfolioVersionSleeve
from app.models.research_run import ResearchRun
from app.models.sleeve import Sleeve
from app.services.portfolio_research import (
    CandidateAllocation,
    TooManyEligibleSleevesError,
    enumerate_candidate_subsets,
    equal_weight_recipe,
)
from app.services.research_run import (
    ResearchRunPreview,
    compute_research_run_preview,
    list_research_runs,
)

_ONLY_IMPLEMENTED_RECIPE = "equal_capital"


class CandidateNotFoundError(Exception):
    pass


class InvalidCandidateDraftError(Exception):
    pass


@dataclass
class ComparableRun:
    run: ResearchRun
    preview: ResearchRunPreview


def list_comparable_research_runs(session: Session, *, tenant_id: str) -> list[ComparableRun]:
    """Runs with a real, blocker-free declared candidate set of at
    least two -- the honest equivalent, in a build with no job queue,
    of "completed candidates available to compare": nothing here is
    ever a measured/finished walk-forward result, only a validated,
    zero-blocker declaration ready to be read and drafted from."""
    out: list[ComparableRun] = []
    for run in list_research_runs(session, tenant_id=tenant_id):
        preview = compute_research_run_preview(session, run, tenant_id=tenant_id)
        if not preview.blockers and preview.declared_candidates_count >= 2:
            out.append(ComparableRun(run=run, preview=preview))
    return out


def _candidates_for_run(run: ResearchRun) -> list[tuple[str, ...]]:
    try:
        return enumerate_candidate_subsets(run.sleeve_ids, min_size=run.subset_min, max_size=run.subset_max)
    except (TooManyEligibleSleevesError, ValueError) as exc:
        raise CandidateNotFoundError(str(exc)) from exc


def _get_candidate(run: ResearchRun, candidate_index: int) -> tuple[str, ...]:
    candidates = _candidates_for_run(run)
    if candidate_index < 0 or candidate_index >= len(candidates):
        raise CandidateNotFoundError(f"candidate index {candidate_index} is out of range for run {run.research_run_id}")
    return candidates[candidate_index]


@dataclass
class CandidateView:
    candidate_index: int
    sleeve_ids: tuple[str, ...]
    sleeve_labels: dict[str, str]
    allocation: CandidateAllocation | None
    allocation_unavailable_reason: str | None


def _sleeve_labels(session: Session, sleeve_ids: tuple[str, ...], *, tenant_id: str) -> dict[str, str]:
    labels: dict[str, str] = {}
    for sleeve_id in sleeve_ids:
        sleeve = session.get(Sleeve, sleeve_id)
        if sleeve is not None and sleeve.tenant_id == tenant_id:
            labels[sleeve_id] = f"{sleeve.provider} / {sleeve.analyst} / {sleeve.strategy_horizon}"
        else:
            labels[sleeve_id] = sleeve_id
    return labels


def inspect_candidate(session: Session, run: ResearchRun, candidate_index: int, *, tenant_id: str) -> CandidateView:
    """AD-06-A02 "Inspect counterexample" -- see this module's own
    docstring on why this is a real composition/allocation read, not a
    fabricated evidence/counterexample system."""
    sleeve_ids = _get_candidate(run, candidate_index)
    allocation: CandidateAllocation | None = None
    reason: str | None = None
    if _ONLY_IMPLEMENTED_RECIPE in run.recipes:
        allocation = equal_weight_recipe(sleeve_ids)
    else:
        reason = "RECIPE_NOT_IMPLEMENTED: this run did not request the one implemented recipe (equal_capital)"
    return CandidateView(
        candidate_index=candidate_index,
        sleeve_ids=sleeve_ids,
        sleeve_labels=_sleeve_labels(session, sleeve_ids, tenant_id=tenant_id),
        allocation=allocation,
        allocation_unavailable_reason=reason,
    )


@dataclass
class CandidateComparison:
    candidate_a: CandidateView
    candidate_b: CandidateView
    shared_sleeve_ids: frozenset[str] = field(default_factory=frozenset)


def compare_candidates(
    session: Session, run: ResearchRun, candidate_a_index: int, candidate_b_index: int, *, tenant_id: str
) -> CandidateComparison:
    candidate_a = inspect_candidate(session, run, candidate_a_index, tenant_id=tenant_id)
    candidate_b = inspect_candidate(session, run, candidate_b_index, tenant_id=tenant_id)
    shared = frozenset(candidate_a.sleeve_ids) & frozenset(candidate_b.sleeve_ids)
    return CandidateComparison(candidate_a=candidate_a, candidate_b=candidate_b, shared_sleeve_ids=shared)


def create_portfolio_version_draft_from_candidate(
    session: Session,
    *,
    tenant_id: str,
    research_run: ResearchRun,
    candidate_index: int,
    portfolio_id: str,
    consent_disclosure_version: str,
    max_subscriber_capacity: int,
) -> PortfolioVersion:
    """AD-06-A01 "Choose candidate for draft" -- "Create an unreleased
    portfolio version draft referencing one completed candidate...; do
    not publish or activate." This NEVER can publish or activate
    anything: `PortfolioVersion` (app/models/portfolio_version.py) has
    no activation/publication field of its own at all -- a version only
    ever becomes reachable by a customer once a SEPARATE `Product` is
    drafted to reference it (app/services/product_admin.py) and THAT
    product clears its own independent review/admission chain
    (app/services/release_review.py, app/services/publication_admin.py,
    app/services/publication_admission.py). Creating a row here touches
    none of those tables and flips no status anywhere -- there is
    structurally nothing left for this function to "not publish"."""
    if not portfolio_id or not portfolio_id.strip():
        raise InvalidCandidateDraftError("portfolio_id is required")
    if max_subscriber_capacity <= 0:
        raise InvalidCandidateDraftError("max_subscriber_capacity must be positive")
    if not consent_disclosure_version or not consent_disclosure_version.strip():
        raise InvalidCandidateDraftError("consent_disclosure_version is required")

    sleeve_ids = _get_candidate(research_run, candidate_index)
    if _ONLY_IMPLEMENTED_RECIPE not in research_run.recipes:
        raise InvalidCandidateDraftError(
            "RECIPE_NOT_IMPLEMENTED: this run did not request the one implemented recipe (equal_capital); "
            "there is no real allocation to draft from"
        )
    allocation = equal_weight_recipe(sleeve_ids)

    existing_max = session.scalars(
        select(PortfolioVersion.version_number).where(
            PortfolioVersion.tenant_id == tenant_id, PortfolioVersion.portfolio_id == portfolio_id
        )
    ).all()
    version_number = (max(existing_max) if existing_max else 0) + 1

    version = PortfolioVersion(
        tenant_id=tenant_id,
        portfolio_id=portfolio_id,
        version_number=version_number,
        cash_weight=allocation.cash,
        research_cutoff=datetime.now(timezone.utc),
        max_subscriber_capacity=max_subscriber_capacity,
        consent_disclosure_version=consent_disclosure_version,
    )
    session.add(version)
    session.flush()

    for sleeve_id, weight in allocation.weights.items():
        session.add(
            PortfolioVersionSleeve(
                portfolio_version_id=version.portfolio_version_id,
                sleeve_id=sleeve_id,
                weight=weight,
            )
        )
    session.flush()
    return version
