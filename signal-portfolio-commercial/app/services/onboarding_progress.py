"""Wires `app/services/onboarding.py`'s own `OnboardingStage`/`advance()`
state machine into real data, and into the one real call site that
needed it: `app/services/copy_mandate.py`'s `create_copy_mandate_draft`.

Before this module existed, `OnboardingStage`/`advance()` were correct
but decorative -- referenced only by `onboarding.py` itself and its own
tests, never consulted by anything that actually admits a customer
action. There was also no persisted onboarding-stage field on any
model at all.

Two distinct questions, deliberately not conflated (the same discipline
`app/services/entitlement.py`'s own docstring insists on for its two
functions):

- `compute_current_stage`: what does this customer's CURRENT, live data
  say their furthest CONTIGUOUS completed stage is, right now? This is
  what a gate that admits a NEW action (a new mandate draft) must use --
  a customer's entitlement is a live billing fact, not a
  once-true-forever badge (see `entitlement.authorizes_new_entry`'s own
  docstring: a payment outage genuinely revokes NEW-entry admission).
- `sync_onboarding_progress`: the durable, resumable high-water mark
  (`OnboardingProgress`, one row per customer). It only ever advances
  forward, via `advance()` itself -- so it is never silently
  overwritten with an earlier stage just because a later signal (e.g.
  a lapsed subscription) regresses. It is the record of what this
  customer has ever completed, not of what they may do this instant.

No stage here is invented or guessed: each one is read from a real,
already-existing row this codebase's own other services already write
(`EligibilityAssessment.facts_confirmed`, an ACTIVE `PortfolioSelection`,
the tenant's own `Subscription` state via
`entitlement.authorizes_new_entry`, a saved `NotificationPreferences`
row) -- never a new manual "mark my onboarding complete" UI step, per
the audit finding's own instruction to wire in existing signals rather
than invent one.

`PLATFORM_AUTHORIZED` and `MANDATE_PREVIEWED` are deliberately never
computed here: `PLATFORM_AUTHORIZED` is the one stage the spec calls
optional (`advance` already lets a caller skip it), and
`MANDATE_PREVIEWED` is CU-09's own in-wizard "Preview" step -- which
happens strictly AFTER a mandate draft already exists, so requiring it
before draft creation would be circular. `create_copy_mandate_draft`
therefore gates on `ALERT_PREFERENCES_SET`, the last mandatory stage
that precedes the mandate wizard itself. `COPY_ACTIVATED` is not
reachable through this build at all: `CopyMandateState` has no ACTIVE
value, and CU-09's own "Confirm authorized activation" action does not
exist in `copy_mandate.py` (see that module's own docstring) -- there
is no second call site to gate until that real activation pipeline
exists.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.billing import Subscription
from app.models.eligibility import EligibilityAssessment
from app.models.notification_preferences import NotificationPreferences
from app.models.onboarding_progress import OnboardingProgress
from app.models.portfolio_selection import PortfolioSelection, PortfolioSelectionState
from app.models.tenancy import UserIdentity
from app.services.entitlement import authorizes_new_entry
from app.services.onboarding import OnboardingStage, advance

#: The canonical order, reused to answer "has stage X reached at least
#: stage Y" without duplicating `onboarding.py`'s own private `_ORDER`.
_ORDER: tuple[OnboardingStage, ...] = tuple(OnboardingStage)


class OnboardingIncompleteError(Exception):
    """Raised by `require_stage_at_least` with a reason code identifying
    exactly which prerequisite(s) are still missing -- never a bare
    denial, per this build's own "clear, actionable error" discipline
    (e.g. `copy_mandate.InvalidCopyMandateError`'s own reason-code
    strings)."""


def has_reached(current: OnboardingStage, required: OnboardingStage) -> bool:
    """True if `current` is `required` or later in the canonical order."""
    return _ORDER.index(current) >= _ORDER.index(required)


def _has_active_selection(session: Session, *, tenant_id: str, user_id: str) -> bool:
    return (
        session.scalar(
            select(PortfolioSelection.selection_id).where(
                PortfolioSelection.tenant_id == tenant_id,
                PortfolioSelection.user_id == user_id,
                PortfolioSelection.state == PortfolioSelectionState.ACTIVE,
            )
        )
        is not None
    )


def _current_subscription(session: Session, *, tenant_id: str) -> Subscription | None:
    return session.scalar(
        select(Subscription).where(Subscription.tenant_id == tenant_id).order_by(Subscription.created_at.desc())
    )


def _signal_checklist(session: Session, *, tenant_id: str, user_id: str) -> list[tuple[OnboardingStage, bool]]:
    """Each real, already-persisted signal this customer's other own
    service modules already write, paired with the `OnboardingStage` it
    evidences -- in canonical order. `compute_current_stage` walks this
    list and stops at the first unmet one, so a later signal being true
    (e.g. alert preferences saved) never papers over an earlier one
    that is still missing."""
    identity = session.get(UserIdentity, user_id)
    email_verified = identity is not None and identity.email_verified_at is not None

    eligibility = session.get(EligibilityAssessment, (tenant_id, user_id))
    eligibility_approved = eligibility is not None and eligibility.facts_confirmed

    product_selected = _has_active_selection(session, tenant_id=tenant_id, user_id=user_id)

    subscription = _current_subscription(session, tenant_id=tenant_id)
    payment_completed = subscription is not None
    entitlement_verified = subscription is not None and authorizes_new_entry(subscription)

    notification_preferences = session.get(NotificationPreferences, (tenant_id, user_id))
    alert_preferences_set = notification_preferences is not None

    return [
        (OnboardingStage.EMAIL_VERIFIED, email_verified),
        (OnboardingStage.ELIGIBILITY_APPROVED, eligibility_approved),
        (OnboardingStage.PRODUCT_SELECTED, product_selected),
        (OnboardingStage.PAYMENT_COMPLETED, payment_completed),
        (OnboardingStage.ENTITLEMENT_VERIFIED, entitlement_verified),
        (OnboardingStage.ALERT_PREFERENCES_SET, alert_preferences_set),
    ]


def compute_current_stage(session: Session, *, tenant_id: str, user_id: str) -> OnboardingStage:
    """The customer's furthest CONTIGUOUS stage, computed live from real
    signals -- see this module's own docstring for why this, not the
    persisted `OnboardingProgress` row, is what an admission gate must
    check."""
    stage = OnboardingStage.SIGNED_UP
    for candidate, satisfied in _signal_checklist(session, tenant_id=tenant_id, user_id=user_id):
        if not satisfied:
            break
        stage = advance(stage, candidate)
    return stage


def sync_onboarding_progress(session: Session, *, tenant_id: str, user_id: str) -> OnboardingProgress:
    """Persist the customer's current live stage into their durable
    `OnboardingProgress` row -- via `advance()` itself, so the stored
    high-water mark only ever moves forward. A live regression (e.g. a
    lapsed subscription) never rewrites the stored row backwards; it
    only ever means this call's `advance()` is skipped for this sync
    regression (e.g. a lapsed subscription) never rewrites the stored row
    backwards; it simply means the hop loop below never runs."""
    current_stage = compute_current_stage(session, tenant_id=tenant_id, user_id=user_id)

    progress = session.get(OnboardingProgress, (tenant_id, user_id))
    if progress is None:
        progress = OnboardingProgress(tenant_id=tenant_id, user_id=user_id, stage=OnboardingStage.SIGNED_UP)
        session.add(progress)
        session.flush()

    #: `advance()` refuses a multi-stage leap in one call (that is the
    #: whole point of it) -- `compute_current_stage` above only ever
    #: reaches `current_stage` by hopping one adjacent `_ORDER` slot at a
    #: time, so persisting the same result must hop the SAME way, one
    #: `advance()` call per adjacent stage, never a single
    #: `advance(progress.stage, current_stage)` leap (which `advance()`
    #: would correctly refuse whenever more than one stage separates
    #: them, silently leaving the stored row stuck at its old value).
    stage = progress.stage
    advanced = False
    for index in range(_ORDER.index(progress.stage) + 1, _ORDER.index(current_stage) + 1):
        stage = advance(stage, _ORDER[index])
        advanced = True
    if advanced:
        progress.stage = stage
        progress.updated_at = datetime.now(timezone.utc)
        session.flush()
    return progress


def require_stage_at_least(
    session: Session, *, tenant_id: str, user_id: str, required: OnboardingStage
) -> None:
    """The actual gate. Two checks, deliberately not conflated:

    - The durable `OnboardingProgress` high-water mark (synced from live
      signals first, so a customer's very first qualifying call sees
      immediate credit) must have reached `required`. Once a one-time
      onboarding milestone (email verified, eligibility approved,
      product selected, alert preferences saved) is reached, cancelling
      or changing an unrelated, later row (e.g. cancelling the specific
      selection this call happens to name) never un-reaches it --
      `copy_mandate.create_copy_mandate_draft`'s own selection/
      connection ownership-and-state checks are the right place for
      "is THIS selection still valid", not this gate.
    - If `required` is at or past `ENTITLEMENT_VERIFIED`, entitlement is
      ADDITIONALLY re-checked live against the tenant's CURRENT
      subscription -- never satisfied merely because it was once true.
      This matches `entitlement.authorizes_new_entry`'s own contract: a
      lapsed subscription genuinely revokes admission for a NEW action,
      even for a customer who fully completed onboarding before it
      lapsed.
    """
    progress = sync_onboarding_progress(session, tenant_id=tenant_id, user_id=user_id)
    if not has_reached(progress.stage, required):
        raise OnboardingIncompleteError(
            f"ONBOARDING_INCOMPLETE: customer has reached {progress.stage.value!r}, "
            f"not yet {required.value!r}"
        )

    if has_reached(required, OnboardingStage.ENTITLEMENT_VERIFIED):
        subscription = _current_subscription(session, tenant_id=tenant_id)
        if subscription is None or not authorizes_new_entry(subscription):
            raise OnboardingIncompleteError(
                "ONBOARDING_INCOMPLETE: this customer's subscription no longer authorizes a new copy "
                "action (entitlement has lapsed since onboarding was completed)"
            )
