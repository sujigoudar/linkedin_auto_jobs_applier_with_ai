"""app/services/onboarding.py -- pure function tests, no database needed."""
import pytest

from app.services.onboarding import (
    InvalidOnboardingTransitionError,
    OnboardingStage,
    advance,
    is_live_copying,
)


def test_the_normal_sequence_advances_one_stage_at_a_time():
    stage = OnboardingStage.SIGNED_UP
    for next_stage in list(OnboardingStage)[1:]:
        stage = advance(stage, next_stage)
    assert stage == OnboardingStage.COPY_ACTIVATED


def test_platform_authorized_can_be_skipped():
    stage = advance(OnboardingStage.ALERT_PREFERENCES_SET, OnboardingStage.MANDATE_PREVIEWED)
    assert stage == OnboardingStage.MANDATE_PREVIEWED


def test_payment_completed_does_not_make_a_customer_live():
    """The spec's own rule: "Do not call a customer live merely because
    payment succeeded." Jumping straight from PAYMENT_COMPLETED to
    COPY_ACTIVATED skips ENTITLEMENT_VERIFIED, ALERT_PREFERENCES_SET and
    MANDATE_PREVIEWED -- all mandatory -- so it must be refused."""
    with pytest.raises(InvalidOnboardingTransitionError):
        advance(OnboardingStage.PAYMENT_COMPLETED, OnboardingStage.COPY_ACTIVATED)


def test_entitlement_verified_is_not_implied_by_payment_alone():
    with pytest.raises(InvalidOnboardingTransitionError):
        advance(OnboardingStage.PRODUCT_SELECTED, OnboardingStage.ENTITLEMENT_VERIFIED)


def test_cannot_move_backwards():
    with pytest.raises(InvalidOnboardingTransitionError):
        advance(OnboardingStage.PAYMENT_COMPLETED, OnboardingStage.PRODUCT_SELECTED)


def test_cannot_stay_on_the_same_stage_via_advance():
    with pytest.raises(InvalidOnboardingTransitionError):
        advance(OnboardingStage.SIGNED_UP, OnboardingStage.SIGNED_UP)


def test_only_copy_activated_counts_as_live():
    for stage in OnboardingStage:
        expected = stage is OnboardingStage.COPY_ACTIVATED
        assert is_live_copying(stage) is expected


def test_mandate_previewed_is_not_itself_live():
    assert is_live_copying(OnboardingStage.MANDATE_PREVIEWED) is False
