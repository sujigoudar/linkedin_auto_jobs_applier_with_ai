"""Track 34: the qualification gate `/system/readiness` reports as
`trading_authority` -- the honest replacement for that field's prior
placeholder (`None`, because this build never computed a real answer).

Answers one question for real, per `Product`: "does this platform's own
stored state currently give this product authority to route a REAL
customer order" (`AUTOMATED_PUBLICATION`/`MANAGED_ACCOUNTS`-use trading,
not research/alerts distribution). This is the signal-portfolio-
commercial side of the live-routing qualification concept signal-
copier's own `app/qualification.py` builds for broker/exchange EXECUTION
ROUTES (Track 1b) -- a deliberately separate, higher layer: signal-
copier's module answers "is this adapter/account/venue combination
technically qualified to submit an order at all", this module answers
"does the COMMERCIAL PLATFORM'S OWN release/rights/eligibility state
currently authorize this product to reach that execution layer in the
first place." Neither module reads the other's tables; they are
intentionally checked independently, matching this repo's own `rights_
registry.py` precedent of fail-closed, never-cached checks.

Fail-closed by construction, exactly like `rights_registry.check_rights`
and `product_admin.compute_publication_blockers`: every branch below
returns a NAMED reason, there is no silent default to "qualified", and
a genuinely missing capability in this build (no execution-activation
pipeline exists for either `CopyMandate` or `ManagedProgram` -- see
those two models' own docstrings) reports `missing_input:...`, never a
fabricated pass. This is computed fresh on every call from real stored
rows (`Product`, `ReleaseReview`, `Incident`, `RightsGrant` via
`check_portfolio_rights`) -- never a stored verdict column, same
"recompute, don't cache" discipline `eligibility.py`'s own module
docstring documents for the exact same reason (a later change elsewhere
must never leave a stale answer in place).

`applicable` is a distinct axis from `qualified`: a research/alerts-only
product (no `copying`/`managed_program` in `service_modes`) never
routes a real order at all, so asking whether it has "trading authority"
is a category error, not a failed check -- this reports
`applicable=False`, `qualified=None`, matching this track's own
instruction that the field "remain correctly None ... for any tenant/
program this genuinely cannot [meaningfully] be assessed."
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.incident import Incident, IncidentSeverity, IncidentState
from app.models.product import Product, ProductLifecycleState
from app.models.release_review import ReleaseReview, ReleaseReviewState
from app.models.rights import RightsUse
from app.services.portfolio_rights import check_portfolio_rights
from app.services.product_admin import compute_publication_blockers

#: service_modes that mean "this product can route real customer
#: orders" -- `alerts`/`research` never place an order on anyone's
#: behalf, so they are deliberately excluded (see this module's own
#: docstring on `applicable`).
_ORDER_ROUTING_SERVICE_MODES: frozenset[str] = frozenset({"copying", "managed_program"})

#: The exact `RightsUse` this gate checks for each order-routing service
#: mode -- deliberately NOT `RightsUse.COMMERCIAL_ALERTS` (what
#: `compute_publication_blockers` checks, correctly, for the alerts/
#: distribution gate): routing a real order under a copy relationship or
#: a managed program is a different grant of rights than simply
#: publishing an alert, per `app/models/rights.py`'s own enum, and
#: granting one is not evidence the other was ever granted.
_RIGHTS_USE_BY_SERVICE_MODE: dict[str, RightsUse] = {
    "copying": RightsUse.AUTOMATED_PUBLICATION,
    "managed_program": RightsUse.MANAGED_ACCOUNTS,
}

#: Incident states this gate treats as "still open" -- RESOLVED is the
#: only state that does not block, matching `Incident`'s own state
#: machine (app/models/incident.py).
_OPEN_INCIDENT_STATES: frozenset[IncidentState] = frozenset(
    {IncidentState.OPEN, IncidentState.ACKNOWLEDGED, IncidentState.ASSIGNED}
)
_BLOCKING_INCIDENT_SEVERITIES: frozenset[IncidentSeverity] = frozenset(
    {IncidentSeverity.HIGH, IncidentSeverity.CRITICAL}
)

#: Named once, here, so a future build that actually adds a real
#: execution-activation pipeline for `CopyMandate`/`ManagedProgram` has
#: exactly one place to update this gate's own missing-input reason --
#: see those two models' own docstrings for why neither one has an
#: ACTIVE/enrolled-live state today.
MISSING_INPUT_EXECUTION_ACTIVATION = "missing_input:execution_activation_pipeline"


@dataclass(frozen=True)
class TradingAuthorityAssessment:
    #: False for a product with no order-routing service mode at all --
    #: see this module's own docstring. `qualified`/`reason` are
    #: meaningless (None/"not_applicable") in that case.
    applicable: bool
    #: None exactly when `applicable` is False. Otherwise a real,
    #: computed True/False -- never a hand-set flag.
    qualified: bool | None
    #: A short, named, machine-checkable code -- never free text. One of:
    #: "not_applicable", "qualified", "not_qualified:<check>",
    #: "missing_input:<name>".
    reason: str
    #: Every individual check this assessment ran and its own outcome --
    #: AD-22-P05's own panel contract: "Do not collapse payment,
    #: connection, rights and trading authority into one active badge."
    #: Exposed here so a caller (or a future UI) can show the full
    #: checklist, not just the first failing reason.
    checks: dict[str, str] = field(default_factory=dict)


_NOT_APPLICABLE = TradingAuthorityAssessment(applicable=False, qualified=None, reason="not_applicable")


def assess_trading_authority(session: Session, product: Product) -> TradingAuthorityAssessment:
    """Fail-closed, recomputed on every call -- see this module's own
    docstring. Checks run in a fixed order and the FIRST failing check's
    reason is reported (matching `rights_registry.check_rights`'s own
    "first disqualifying condition wins" shape), but every check's own
    outcome is still recorded in `checks` so nothing is lost."""
    order_routing_modes = sorted(set(product.service_modes) & _ORDER_ROUTING_SERVICE_MODES)
    if not order_routing_modes:
        return _NOT_APPLICABLE

    checks: dict[str, str] = {}

    published = product.lifecycle_state == ProductLifecycleState.PUBLISHED
    checks["product_published"] = "PASS" if published else "FAIL"
    if not published:
        return TradingAuthorityAssessment(
            applicable=True, qualified=False, reason="not_qualified:product_not_published", checks=checks
        )

    blockers = compute_publication_blockers(session, product)
    checks["no_publication_blockers"] = "PASS" if not blockers else f"FAIL:{','.join(blockers)}"
    if blockers:
        return TradingAuthorityAssessment(
            applicable=True,
            qualified=False,
            reason=f"not_qualified:publication_blockers:{','.join(blockers)}",
            checks=checks,
        )

    current_approved_review = session.scalars(
        select(ReleaseReview)
        .where(
            ReleaseReview.product_id == product.product_id,
            ReleaseReview.state == ReleaseReviewState.APPROVED,
            ReleaseReview.object_revision_reviewed == product.revision,
        )
        .order_by(ReleaseReview.decided_at.desc())
    ).first()
    checks["has_current_approved_release_review"] = "PASS" if current_approved_review else "FAIL"
    if current_approved_review is None:
        return TradingAuthorityAssessment(
            applicable=True,
            qualified=False,
            reason="not_qualified:no_approved_release_review_for_current_revision",
            checks=checks,
        )

    #: `compute_publication_blockers` already proved `portfolio_version_id`
    #: is set and every member sleeve resolves to a real `Sleeve` with
    #: a known `asset_class` -- see `product_admin.py`. Re-checking rights
    #: here with the order-routing-specific `RightsUse` (not the alerts
    #: one `compute_publication_blockers` checks) is deliberate, not
    #: redundant: see `_RIGHTS_USE_BY_SERVICE_MODE`'s own docstring.
    rights_failures: list[str] = []
    for mode in order_routing_modes:
        rights_use = _RIGHTS_USE_BY_SERVICE_MODE[mode]
        result = check_portfolio_rights(
            session,
            portfolio_version_id=product.portfolio_version_id,  # type: ignore[arg-type]
            use=rights_use,
            channel="execution",
            jurisdiction="US",
            asset="equity",
        )
        if not result.allowed:
            rights_failures.append(f"{mode}:{result.reason}")
    checks["order_routing_rights_granted"] = "PASS" if not rights_failures else f"FAIL:{','.join(rights_failures)}"
    if rights_failures:
        return TradingAuthorityAssessment(
            applicable=True,
            qualified=False,
            reason=f"not_qualified:order_routing_rights_not_granted:{','.join(rights_failures)}",
            checks=checks,
        )

    blocking_incident = session.scalars(
        select(Incident).where(
            Incident.affected_object_type == "product",
            Incident.affected_object_id == product.product_id,
            Incident.state.in_(_OPEN_INCIDENT_STATES),
            Incident.severity.in_(_BLOCKING_INCIDENT_SEVERITIES),
        )
    ).first()
    checks["no_blocking_open_incident"] = "PASS" if blocking_incident is None else f"FAIL:{blocking_incident.incident_id}"
    if blocking_incident is not None:
        return TradingAuthorityAssessment(
            applicable=True,
            qualified=False,
            reason=f"not_qualified:open_incident:{blocking_incident.incident_id}",
            checks=checks,
        )

    #: Every real platform-side gate above has passed -- the only
    #: remaining input is a REAL, activated execution authority
    #: (`CopyMandate`/`ManagedProgram` reaching a live/enrolled state).
    #: Neither model has one: `CopyMandateState` is only
    #: `draft`/`cancelled` and `ManagedProgramState` is only
    #: `DRAFT`/`SUBMITTED_FOR_REVIEW` -- both models' own docstrings say
    #: so explicitly ("activation needs a real scoped publisher/
    #: execution pipeline this build does not have"). This is therefore
    #: never a fabricated pass -- it is the one genuinely missing input,
    #: named, not silently defaulted.
    checks["execution_activation_pipeline"] = "MISSING"
    return TradingAuthorityAssessment(
        applicable=True, qualified=False, reason=MISSING_INPUT_EXECUTION_ACTIVATION, checks=checks
    )
