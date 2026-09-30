"""Shared test helper: seed the real signals `app/services/
onboarding_progress.py` reads so a customer fixture reaches
`OnboardingStage.ALERT_PREFERENCES_SET` -- the stage
`create_copy_mandate_draft` now requires (see that module's own
docstring for why). Every test file that drafts a copy mandate for an
already-membershipped customer calls this right after seeding that
membership, matching the same real service functions
`tests/test_copy_mandate.py`'s own `_seed_membership` uses -- never a
stage column set directly.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models.billing import ProductTier, Subscription, SubscriptionState
from app.models.eligibility import CustomerType, EligibilityAssessment
from app.models.tenancy import UserIdentity
from app.services.notification_preferences import save_notification_preferences


def complete_onboarding_prerequisites(db_session: Session, *, tenant_id: str = "tenant-a", user_id: str = "user-a") -> None:
    """Call once, after the customer's `Membership` row is committed and
    before any `create_copy_mandate_draft` call, to satisfy
    `ALERT_PREFERENCES_SET` for this (tenant_id, user_id)."""
    identity = db_session.get(UserIdentity, user_id)
    if identity is not None and identity.email_verified_at is None:
        identity.email_verified_at = datetime.now(timezone.utc)

    if db_session.get(EligibilityAssessment, (tenant_id, user_id)) is None:
        db_session.add(
            EligibilityAssessment(
                tenant_id=tenant_id,
                user_id=user_id,
                residence_country="US",
                tax_residence=["US"],
                customer_type=CustomerType.INDIVIDUAL,
                requested_service_modes=["copying"],
                document_versions=["v1"],
                facts_confirmed=True,
            )
        )
    db_session.commit()

    db_session.add(
        Subscription(
            tenant_id=tenant_id,
            tier=ProductTier.PORTFOLIOS_THREE,
            state=SubscriptionState.ACTIVE_PAID,
            price_cents=9900,
            currency="usd",
            current_period_end=datetime.now(timezone.utc) + timedelta(days=30),
        )
    )
    db_session.commit()

    save_notification_preferences(
        db_session,
        tenant_id=tenant_id,
        user_id=user_id,
        email=f"{user_id}@example.com",
        webhook_endpoint_id=None,
        categories=["safety"],
        timezone_name="UTC",
        quiet_start=None,
        quiet_end=None,
        marketing_consent=False,
    )
    db_session.commit()
