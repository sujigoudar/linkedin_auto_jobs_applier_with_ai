"""Customer onboarding sequence, per spec/docs/09_website_dashboards_and_journeys.md's
"Customer onboarding" section: "Signup -> verify identity/email ->
residence and approved audience eligibility -> product/portfolio
selection -> full cost/risks -> hosted test/live-approved payment ->
entitlement verified -> alert preferences -> optional platform
authorization -> risk/capacity/mandate preview -> explicit copy
activation. Every intermediate state is resumable... Do not call a
customer live merely because payment succeeded."

The critical invariant this module exists to enforce structurally: a
customer becomes "live" (able to actually copy trades) ONLY at
COPY_ACTIVATED, an explicit, distinct final step -- never implied by
PAYMENT_COMPLETED, and never reachable by skipping any of the mandatory
stages in between (ENTITLEMENT_VERIFIED, ALERT_PREFERENCES_SET,
MANDATE_PREVIEWED). PLATFORM_AUTHORIZED is the one stage the spec calls
"optional" and is the only stage `advance` will ever let a caller skip.
"""
from __future__ import annotations

import enum


class OnboardingStage(str, enum.Enum):
    SIGNED_UP = "SIGNED_UP"
    EMAIL_VERIFIED = "EMAIL_VERIFIED"
    ELIGIBILITY_APPROVED = "ELIGIBILITY_APPROVED"
    PRODUCT_SELECTED = "PRODUCT_SELECTED"
    PAYMENT_COMPLETED = "PAYMENT_COMPLETED"
    ENTITLEMENT_VERIFIED = "ENTITLEMENT_VERIFIED"
    ALERT_PREFERENCES_SET = "ALERT_PREFERENCES_SET"
    PLATFORM_AUTHORIZED = "PLATFORM_AUTHORIZED"
    MANDATE_PREVIEWED = "MANDATE_PREVIEWED"
    COPY_ACTIVATED = "COPY_ACTIVATED"


#: The canonical, resumable sequence -- a customer may always re-enter
#: (repeat) their current stage; `advance` below is what decides which
#: FORWARD moves are legal.
_ORDER: tuple[OnboardingStage, ...] = tuple(OnboardingStage)

#: The one stage docs/09 explicitly calls optional -- the only stage
#: `advance` will ever let a caller skip over.
_OPTIONAL_STAGES: frozenset[OnboardingStage] = frozenset({OnboardingStage.PLATFORM_AUTHORIZED})


class InvalidOnboardingTransitionError(Exception):
    pass


def advance(current: OnboardingStage, target: OnboardingStage) -> OnboardingStage:
    """Move from `current` to `target`, or raise. `target` must be later
    in the canonical order, and every stage strictly between them must
    be optional -- skipping any MANDATORY stage (most importantly,
    reaching COPY_ACTIVATED without passing through
    ENTITLEMENT_VERIFIED/ALERT_PREFERENCES_SET/MANDATE_PREVIEWED) is
    always refused, regardless of what `target` claims to be."""
    current_index = _ORDER.index(current)
    target_index = _ORDER.index(target)

    if target_index <= current_index:
        raise InvalidOnboardingTransitionError(
            f"{target.value!r} is not after {current.value!r} in the onboarding sequence"
        )

    skipped = _ORDER[current_index + 1 : target_index]
    mandatory_skipped = [stage for stage in skipped if stage not in _OPTIONAL_STAGES]
    if mandatory_skipped:
        raise InvalidOnboardingTransitionError(
            f"cannot advance from {current.value!r} to {target.value!r}: would skip mandatory stage(s) "
            f"{[s.value for s in mandatory_skipped]}"
        )

    return target


def is_live_copying(stage: OnboardingStage) -> bool:
    """True only at COPY_ACTIVATED -- payment success, entitlement
    verification, even a previewed mandate, are all NOT this."""
    return stage is OnboardingStage.COPY_ACTIVATED
