"""CU-14 "Support and incident case" -- app/services/support_case.py's
own tests. Real Postgres, real tenant-scoped session."""
import pytest

from app.models.billing import ProductTier, Subscription, SubscriptionState
from app.models.tenancy import Membership, MembershipRole, Tenant, UserIdentity
from app.services.support_case import InvalidSupportCaseError, create_support_case, list_support_cases


def _seed_membership(db_session, *, tenant_id="tenant-a", user_id="user-a"):
    db_session.add(Tenant(tenant_id=tenant_id, display_name="Tenant", environment="LOCAL_SIM"))
    db_session.flush()
    db_session.add(UserIdentity(user_id=user_id, email=f"{user_id}@example.com"))
    db_session.flush()
    db_session.add(Membership(tenant_id=tenant_id, user_id=user_id, role=MembershipRole.CUSTOMER))
    db_session.commit()


def test_list_support_cases_is_empty_before_any_are_created(db_session):
    _seed_membership(db_session)
    assert list_support_cases(db_session, tenant_id="tenant-a", user_id="user-a") == []


def test_create_support_case_rejects_an_unknown_category(db_session):
    _seed_membership(db_session)
    with pytest.raises(InvalidSupportCaseError):
        create_support_case(
            db_session,
            tenant_id="tenant-a",
            user_id="user-a",
            category="not-a-real-category",
            related_object_id=None,
            subject="Help",
            description="Something is wrong",
            attachment_ids=[],
        )


def test_create_support_case_rejects_an_empty_subject(db_session):
    _seed_membership(db_session)
    with pytest.raises(InvalidSupportCaseError):
        create_support_case(
            db_session,
            tenant_id="tenant-a",
            user_id="user-a",
            category="billing",
            related_object_id=None,
            subject="",
            description="Something is wrong",
            attachment_ids=[],
        )


def test_create_then_reload_persists_the_real_case(db_session):
    _seed_membership(db_session)
    create_support_case(
        db_session,
        tenant_id="tenant-a",
        user_id="user-a",
        category="delivery",
        related_object_id=None,
        subject="Missed alert",
        description="I did not receive last night's alert",
        attachment_ids=["evidence-1"],
    )
    db_session.commit()

    cases = list_support_cases(db_session, tenant_id="tenant-a", user_id="user-a")
    assert len(cases) == 1
    assert cases[0].subject == "Missed alert"
    assert cases[0].attachment_ids == ["evidence-1"]


def test_related_object_id_referencing_another_tenants_subscription_is_refused(db_session):
    _seed_membership(db_session)
    from datetime import datetime, timedelta, timezone

    other_tenant_subscription = Subscription(
        tenant_id="tenant-b",
        tier=ProductTier.ALERTS_ONE,
        state=SubscriptionState.ACTIVE_PAID,
        price_cents=3900,
        current_period_end=datetime.now(timezone.utc) + timedelta(days=30),
    )
    db_session.add(other_tenant_subscription)
    db_session.commit()

    with pytest.raises(InvalidSupportCaseError):
        create_support_case(
            db_session,
            tenant_id="tenant-a",
            user_id="user-a",
            category="billing",
            related_object_id=other_tenant_subscription.subscription_id,
            subject="Billing question",
            description="Why was I charged",
            attachment_ids=[],
        )


def test_related_object_id_referencing_your_own_tenants_subscription_is_accepted(db_session):
    _seed_membership(db_session)
    from datetime import datetime, timedelta, timezone

    own_subscription = Subscription(
        tenant_id="tenant-a",
        tier=ProductTier.ALERTS_ONE,
        state=SubscriptionState.ACTIVE_PAID,
        price_cents=3900,
        current_period_end=datetime.now(timezone.utc) + timedelta(days=30),
    )
    db_session.add(own_subscription)
    db_session.commit()

    case = create_support_case(
        db_session,
        tenant_id="tenant-a",
        user_id="user-a",
        category="billing",
        related_object_id=own_subscription.subscription_id,
        subject="Billing question",
        description="Why was I charged",
        attachment_ids=[],
    )
    assert case.related_object_id == own_subscription.subscription_id
