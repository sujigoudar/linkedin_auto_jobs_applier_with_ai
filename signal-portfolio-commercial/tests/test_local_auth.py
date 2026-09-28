"""Real tests for app/services/local_auth.py -- ID-01/ID-02/ID-03's own
backing service. See that module's own docstring for what this is (a
real, working local credential system) and is not (Supabase Auth)."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models.local_auth import AuthToken, AuthTokenType, WebSession
from app.models.tenancy import Membership, MembershipRole, UserIdentity
from app.services.local_auth import (
    AccountAlreadyExistsError,
    InvalidCredentialsError,
    InvalidTokenError,
    authenticate,
    create_account,
    create_web_session,
    delete_web_session,
    get_web_session,
    request_password_reset,
    reset_password,
    verify_email,
)


def test_create_account_creates_identity_tenant_and_customer_membership(db_session):
    """CUSTOMER, not OWNER -- every CU-0X screen's own
    require_permission check (e.g. "view_own_customer_overview") is
    scoped to Role.CUSTOMER specifically; OWNER is the platform-operator
    role, provisioned only out of band via ops/bootstrap.py."""
    user, token = create_account(
        db_session, email="a@example.com", password="correct horse battery staple", tenant_display_name="A's workspace"
    )

    assert user.email == "a@example.com"
    assert user.password_hash is not None
    assert user.password_hash != "correct horse battery staple"  # never stored raw
    assert user.email_verified_at is None

    membership = db_session.scalar(select(Membership).where(Membership.user_id == user.user_id))
    assert membership is not None
    assert membership.role == MembershipRole.CUSTOMER

    assert token.token_type == AuthTokenType.EMAIL_VERIFICATION
    assert token.consumed_at is None


def test_create_account_rejects_duplicate_email(db_session):
    create_account(db_session, email="dup@example.com", password="pw12345678", tenant_display_name="t")

    with pytest.raises(AccountAlreadyExistsError):
        create_account(db_session, email="dup@example.com", password="different-pw", tenant_display_name="t2")


def test_authenticate_succeeds_with_correct_password(db_session):
    create_account(db_session, email="b@example.com", password="my-real-password", tenant_display_name="t")

    user = authenticate(db_session, email="b@example.com", password="my-real-password")

    assert user.email == "b@example.com"


def test_authenticate_rejects_wrong_password(db_session):
    create_account(db_session, email="c@example.com", password="right-password", tenant_display_name="t")

    with pytest.raises(InvalidCredentialsError):
        authenticate(db_session, email="c@example.com", password="wrong-password")


def test_authenticate_rejects_unknown_email_with_the_same_error_as_wrong_password(db_session):
    """No email enumeration -- ID-01's own acceptance text: unknown
    email and wrong password must raise the exact same exception type,
    not a distinguishable one."""
    with pytest.raises(InvalidCredentialsError):
        authenticate(db_session, email="never-signed-up@example.com", password="anything")


def test_verify_email_marks_verified_and_is_single_use(db_session):
    user, token = create_account(db_session, email="d@example.com", password="pw-12345678", tenant_display_name="t")
    assert user.email_verified_at is None

    verified_user = verify_email(db_session, token=token.token)
    assert verified_user.email_verified_at is not None

    with pytest.raises(InvalidTokenError):
        verify_email(db_session, token=token.token)  # already consumed


def test_verify_email_rejects_unknown_token(db_session):
    with pytest.raises(InvalidTokenError):
        verify_email(db_session, token="not-a-real-token")


def test_verify_email_rejects_expired_token(db_session):
    user, token = create_account(db_session, email="e@example.com", password="pw-12345678", tenant_display_name="t")
    row = db_session.get(AuthToken, token.token)
    row.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
    db_session.commit()

    with pytest.raises(InvalidTokenError):
        verify_email(db_session, token=token.token)


def test_request_password_reset_returns_none_for_unknown_email_no_enumeration(db_session):
    result = request_password_reset(db_session, email="never-existed@example.com")
    assert result is None


def test_request_password_reset_then_reset_password_changes_the_hash(db_session):
    user, _verify_token = create_account(db_session, email="f@example.com", password="old-password-1", tenant_display_name="t")
    old_hash = user.password_hash

    reset_token = request_password_reset(db_session, email="f@example.com")
    assert reset_token is not None
    assert reset_token.token_type == AuthTokenType.PASSWORD_RESET

    reset_password(db_session, token=reset_token.token, new_password="new-password-2")

    refreshed = db_session.get(UserIdentity, user.user_id)
    assert refreshed.password_hash != old_hash
    # old password no longer works, new one does
    with pytest.raises(InvalidCredentialsError):
        authenticate(db_session, email="f@example.com", password="old-password-1")
    authenticate(db_session, email="f@example.com", password="new-password-2")


def test_reset_password_is_single_use(db_session):
    create_account(db_session, email="g@example.com", password="pw-12345678", tenant_display_name="t")
    reset_token = request_password_reset(db_session, email="g@example.com")

    reset_password(db_session, token=reset_token.token, new_password="new-pw-1")

    with pytest.raises(InvalidTokenError):
        reset_password(db_session, token=reset_token.token, new_password="new-pw-2")


def test_verification_token_cannot_be_consumed_as_a_reset_token(db_session):
    """A token's own type is enforced -- an EMAIL_VERIFICATION token
    must never double as a PASSWORD_RESET token just because both are
    rows in the same table."""
    _user, verify_token = create_account(db_session, email="h@example.com", password="pw-12345678", tenant_display_name="t")

    with pytest.raises(InvalidTokenError):
        reset_password(db_session, token=verify_token.token, new_password="hijacked-password")


def test_web_session_round_trips_and_expires(db_session):
    user, _token = create_account(db_session, email="i@example.com", password="pw-12345678", tenant_display_name="t")
    membership = db_session.scalar(select(Membership).where(Membership.user_id == user.user_id))

    session_id, csrf_token = create_web_session(
        db_session, user_id=user.user_id, tenant_id=membership.tenant_id, role=membership.role
    )

    fetched = get_web_session(db_session, session_id=session_id)
    assert fetched is not None
    assert fetched.user_id == user.user_id
    assert fetched.tenant_id == membership.tenant_id
    assert fetched.csrf_token == csrf_token

    assert get_web_session(db_session, session_id=None) is None
    assert get_web_session(db_session, session_id="not-a-real-session") is None


def test_web_session_expired_is_treated_as_absent_and_deleted(db_session):
    user, _token = create_account(db_session, email="j@example.com", password="pw-12345678", tenant_display_name="t")
    membership = db_session.scalar(select(Membership).where(Membership.user_id == user.user_id))
    session_id, _csrf = create_web_session(db_session, user_id=user.user_id, tenant_id=membership.tenant_id, role=membership.role)

    row = db_session.get(WebSession, session_id)
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()

    assert get_web_session(db_session, session_id=session_id) is None
    assert db_session.get(WebSession, session_id) is None  # actually deleted, not just ignored


def test_delete_web_session_revokes_it(db_session):
    user, _token = create_account(db_session, email="k@example.com", password="pw-12345678", tenant_display_name="t")
    membership = db_session.scalar(select(Membership).where(Membership.user_id == user.user_id))
    session_id, _csrf = create_web_session(db_session, user_id=user.user_id, tenant_id=membership.tenant_id, role=membership.role)

    delete_web_session(db_session, session_id=session_id)

    assert get_web_session(db_session, session_id=session_id) is None
