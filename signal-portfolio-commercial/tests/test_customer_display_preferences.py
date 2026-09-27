"""CU-13 "Profile, security and display preferences" --
app/services/customer_display_preferences.py's own tests. Real
Postgres, real tenant-scoped session."""
import pytest

from app.models.customer_display_preferences import DisplayDensity, DisplayTheme
from app.models.tenancy import Membership, MembershipRole, Tenant, UserIdentity
from app.services.customer_display_preferences import (
    InvalidDisplayPreferencesError,
    get_display_preferences,
    save_display_preferences,
)


def _seed_membership(db_session, *, tenant_id="tenant-a", user_id="user-a"):
    if db_session.get(Tenant, tenant_id) is None:
        db_session.add(Tenant(tenant_id=tenant_id, display_name="Tenant", environment="LOCAL_SIM"))
        db_session.flush()
    db_session.add(UserIdentity(user_id=user_id, email=f"{user_id}@example.com"))
    db_session.flush()
    db_session.add(Membership(tenant_id=tenant_id, user_id=user_id, role=MembershipRole.CUSTOMER))
    db_session.commit()


def test_get_display_preferences_is_none_before_any_are_saved(db_session):
    _seed_membership(db_session)
    assert get_display_preferences(db_session, tenant_id="tenant-a", user_id="user-a") is None


def test_save_display_preferences_creates_the_row(db_session):
    _seed_membership(db_session)
    preferences = save_display_preferences(
        db_session,
        tenant_id="tenant-a",
        user_id="user-a",
        display_name="Jane",
        timezone_name="America/New_York",
        theme="dark",
        density="compact",
        number_locale="en-US",
        view_currency="USD",
        reduce_motion="on",
    )
    assert preferences.display_name == "Jane"
    assert preferences.theme == DisplayTheme.DARK
    assert preferences.density == DisplayDensity.COMPACT


def test_save_then_reload_persists_the_real_row(db_session):
    _seed_membership(db_session)
    save_display_preferences(
        db_session,
        tenant_id="tenant-a",
        user_id="user-a",
        display_name="Jane",
        timezone_name="UTC",
        theme="system",
        density="comfortable",
        number_locale="en-US",
        view_currency=None,
        reduce_motion="system",
    )
    db_session.commit()

    preferences = get_display_preferences(db_session, tenant_id="tenant-a", user_id="user-a")
    assert preferences is not None
    assert preferences.display_name == "Jane"


def test_get_display_preferences_is_none_for_a_different_tenant(db_session):
    _seed_membership(db_session)
    save_display_preferences(
        db_session,
        tenant_id="tenant-a",
        user_id="user-a",
        display_name=None,
        timezone_name="UTC",
        theme="system",
        density="comfortable",
        number_locale="en-US",
        view_currency=None,
        reduce_motion="system",
    )
    db_session.commit()

    assert get_display_preferences(db_session, tenant_id="tenant-b", user_id="user-a") is None


def test_save_display_preferences_rejects_html_in_display_name(db_session):
    _seed_membership(db_session)
    with pytest.raises(InvalidDisplayPreferencesError, match="raw HTML"):
        save_display_preferences(
            db_session,
            tenant_id="tenant-a",
            user_id="user-a",
            display_name="<script>alert(1)</script>",
            timezone_name="UTC",
            theme="system",
            density="comfortable",
            number_locale="en-US",
            view_currency=None,
            reduce_motion="system",
        )


def test_save_display_preferences_rejects_an_unknown_theme(db_session):
    _seed_membership(db_session)
    with pytest.raises(InvalidDisplayPreferencesError, match="theme"):
        save_display_preferences(
            db_session,
            tenant_id="tenant-a",
            user_id="user-a",
            display_name=None,
            timezone_name="UTC",
            theme="neon",
            density="comfortable",
            number_locale="en-US",
            view_currency=None,
            reduce_motion="system",
        )


def test_save_display_preferences_rejects_a_badly_formatted_locale(db_session):
    _seed_membership(db_session)
    with pytest.raises(InvalidDisplayPreferencesError, match="supported locale"):
        save_display_preferences(
            db_session,
            tenant_id="tenant-a",
            user_id="user-a",
            display_name=None,
            timezone_name="UTC",
            theme="system",
            density="comfortable",
            number_locale="not-a-locale",
            view_currency=None,
            reduce_motion="system",
        )


def test_save_display_preferences_rejects_a_badly_formatted_currency(db_session):
    _seed_membership(db_session)
    with pytest.raises(InvalidDisplayPreferencesError, match="supported currency"):
        save_display_preferences(
            db_session,
            tenant_id="tenant-a",
            user_id="user-a",
            display_name=None,
            timezone_name="UTC",
            theme="system",
            density="comfortable",
            number_locale="en-US",
            view_currency="dollars",
            reduce_motion="system",
        )
