"""CU-12 "Alert delivery preferences" --
app/services/notification_preferences.py's own tests. Real Postgres,
real tenant-scoped session."""
import pytest

from app.models.tenancy import Membership, MembershipRole, Tenant, UserIdentity
from app.services.notification_preferences import (
    MANDATORY_CATEGORIES,
    InvalidNotificationPreferencesError,
    get_notification_preferences,
    save_notification_preferences,
)


def _seed_membership(db_session, *, tenant_id="tenant-a", user_id="user-a"):
    if db_session.get(Tenant, tenant_id) is None:
        db_session.add(Tenant(tenant_id=tenant_id, display_name="Tenant", environment="LOCAL_SIM"))
        db_session.flush()
    db_session.add(UserIdentity(user_id=user_id, email=f"{user_id}@example.com"))
    db_session.flush()
    db_session.add(Membership(tenant_id=tenant_id, user_id=user_id, role=MembershipRole.CUSTOMER))
    db_session.commit()


def test_get_notification_preferences_is_none_before_any_are_saved(db_session):
    _seed_membership(db_session)
    assert get_notification_preferences(db_session, tenant_id="tenant-a", user_id="user-a") is None


def test_save_notification_preferences_creates_the_row(db_session):
    _seed_membership(db_session)
    preferences = save_notification_preferences(
        db_session,
        tenant_id="tenant-a",
        user_id="user-a",
        email="customer@example.com",
        webhook_endpoint_id=None,
        categories=["entry", "exit", "safety"],
        timezone_name="America/New_York",
        quiet_start=None,
        quiet_end=None,
        marketing_consent=False,
    )
    assert preferences.email == "customer@example.com"
    assert preferences.timezone_name == "America/New_York"


def test_save_then_reload_persists_the_real_row(db_session):
    _seed_membership(db_session)
    save_notification_preferences(
        db_session,
        tenant_id="tenant-a",
        user_id="user-a",
        email="customer@example.com",
        webhook_endpoint_id=None,
        categories=["safety"],
        timezone_name="UTC",
        quiet_start=None,
        quiet_end=None,
        marketing_consent=False,
    )
    db_session.commit()

    preferences = get_notification_preferences(db_session, tenant_id="tenant-a", user_id="user-a")
    assert preferences is not None
    assert preferences.email == "customer@example.com"


def test_save_notification_preferences_updates_the_existing_row_not_a_new_one(db_session):
    _seed_membership(db_session)
    save_notification_preferences(
        db_session,
        tenant_id="tenant-a",
        user_id="user-a",
        email="first@example.com",
        webhook_endpoint_id=None,
        categories=["safety"],
        timezone_name="UTC",
        quiet_start=None,
        quiet_end=None,
        marketing_consent=False,
    )
    db_session.commit()

    save_notification_preferences(
        db_session,
        tenant_id="tenant-a",
        user_id="user-a",
        email="second@example.com",
        webhook_endpoint_id=None,
        categories=["safety", "billing"],
        timezone_name="UTC",
        quiet_start=None,
        quiet_end=None,
        marketing_consent=True,
    )
    db_session.commit()

    preferences = get_notification_preferences(db_session, tenant_id="tenant-a", user_id="user-a")
    assert preferences.email == "second@example.com"
    assert preferences.marketing_consent is True


def test_get_notification_preferences_is_none_for_a_different_tenant(db_session):
    _seed_membership(db_session)
    save_notification_preferences(
        db_session,
        tenant_id="tenant-a",
        user_id="user-a",
        email="customer@example.com",
        webhook_endpoint_id=None,
        categories=["safety"],
        timezone_name="UTC",
        quiet_start=None,
        quiet_end=None,
        marketing_consent=False,
    )
    db_session.commit()

    assert get_notification_preferences(db_session, tenant_id="tenant-b", user_id="user-a") is None


def test_save_notification_preferences_rejects_an_unknown_category(db_session):
    with pytest.raises(InvalidNotificationPreferencesError, match="unknown categor"):
        save_notification_preferences(
            db_session,
            tenant_id="tenant-a",
            user_id="user-a",
            email=None,
            webhook_endpoint_id=None,
            categories=["safety", "not-a-real-category"],
            timezone_name="UTC",
            quiet_start=None,
            quiet_end=None,
            marketing_consent=False,
        )


def test_save_notification_preferences_rejects_excluding_safety(db_session):
    assert MANDATORY_CATEGORIES == frozenset({"safety"})
    with pytest.raises(InvalidNotificationPreferencesError, match="mandatory categories cannot be excluded"):
        save_notification_preferences(
            db_session,
            tenant_id="tenant-a",
            user_id="user-a",
            email=None,
            webhook_endpoint_id=None,
            categories=["marketing"],
            timezone_name="UTC",
            quiet_start=None,
            quiet_end=None,
            marketing_consent=False,
        )


def test_save_notification_preferences_rejects_an_empty_timezone(db_session):
    with pytest.raises(InvalidNotificationPreferencesError, match="timezone_name"):
        save_notification_preferences(
            db_session,
            tenant_id="tenant-a",
            user_id="user-a",
            email=None,
            webhook_endpoint_id=None,
            categories=["safety"],
            timezone_name="",
            quiet_start=None,
            quiet_end=None,
            marketing_consent=False,
        )


def test_save_notification_preferences_rejects_quiet_start_without_quiet_end(db_session):
    with pytest.raises(InvalidNotificationPreferencesError, match="quiet_start and quiet_end"):
        save_notification_preferences(
            db_session,
            tenant_id="tenant-a",
            user_id="user-a",
            email=None,
            webhook_endpoint_id=None,
            categories=["safety"],
            timezone_name="UTC",
            quiet_start="22:00",
            quiet_end=None,
            marketing_consent=False,
        )
