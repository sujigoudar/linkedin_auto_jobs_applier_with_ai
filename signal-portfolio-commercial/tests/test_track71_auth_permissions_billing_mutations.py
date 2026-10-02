"""Track 71: comprehensive mutation-testing regression suite for signal-
portfolio-commercial's authorization and billing modules: auth.py,
permissions.py, and customer_billing.py.

Mutation-testing approach (per Track 60-67 precedent):
This suite targets the specific, high-severity mutations that would silently
misbehave if critical operators/conditions flip or drop. Focus areas:

AUTH.PY:
- Token validation (missing claims, empty vs None checks)
- Credential boundaries (tenant_id/user_id presence)
- JTI claim verification (presence, non-emptiness)
- Revocation denylist fail-closed enforcement

PERMISSIONS.PY:
- Authorization gate inversions (allowed vs denied flips)
- Role membership in frozensets (in vs not-in)
- Action allow-list lookup (empty set defaults, dropped entries)
- Fail-closed default (no action = no permission)

CUSTOMER_BILLING.PY:
- Subscription state transitions and authorization
- Billing calculations (decimal precision, boundary conditions)
- Tenant isolation in billing queries
- Entitlement separation (new entry vs risk-reducing management)

Each test is designed to fail under a targeted mutant pattern.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.models.billing import ProductTier, Subscription, SubscriptionState
from app.models.tenancy import MembershipRole as Role
from app.services.auth import InvalidTokenError, TenantScope, decode_token, issue_token, verify_token
from app.services.customer_billing import CustomerBillingState, get_own_billing_state
from app.services.entitlement import authorizes_new_entry, authorizes_risk_reducing_management
from app.services.permissions import PermissionDenied, is_allowed, require_permission


# =============================================================================
# AUTH.PY MUTATION TESTS
# =============================================================================
class TestTokenDecodingClaimValidation:
    """Mutation target: missing or flipped claim validation checks.
    Boundary: None vs empty string, presence vs non-emptiness."""

    def test_decode_rejects_token_missing_tenant_id_claim(self):
        """Mutation target: dropped `if not tenant_id` guard or flipped to
        `if tenant_id`. Without this check, a token without tenant_id would
        silently construct a TenantScope with tenant_id=None, a critical
        cross-tenant-authority bug."""
        from app import config

        import jwt

        forged = jwt.encode(
            {"user_id": "user-a", "role": "owner", "jti": "jti-a"},
            config.LOCAL_JWT_SECRET,
            algorithm="HS256",
        )
        with pytest.raises(InvalidTokenError) as exc_info:
            decode_token(forged)
        assert "tenant_id or user_id" in str(exc_info.value)

    def test_decode_rejects_token_with_empty_string_tenant_id(self):
        """Mutation target: `not tenant_id` vs `tenant_id is None` -- empty
        string is falsy and must be rejected equally."""
        from app import config

        import jwt

        forged = jwt.encode(
            {"tenant_id": "", "user_id": "user-a", "role": "owner", "jti": "jti-a"},
            config.LOCAL_JWT_SECRET,
            algorithm="HS256",
        )
        with pytest.raises(InvalidTokenError):
            decode_token(forged)

    def test_decode_rejects_token_missing_user_id_claim(self):
        """Mutation target: dropped `if not user_id` guard or flipped to
        `if user_id`. Without this check, a token without user_id would
        silently construct a TenantScope with user_id=None."""
        from app import config

        import jwt

        forged = jwt.encode(
            {"tenant_id": "tenant-a", "role": "owner", "jti": "jti-a"},
            config.LOCAL_JWT_SECRET,
            algorithm="HS256",
        )
        with pytest.raises(InvalidTokenError) as exc_info:
            decode_token(forged)
        assert "tenant_id or user_id" in str(exc_info.value)

    def test_decode_rejects_token_with_empty_string_user_id(self):
        """Mutation target: `not user_id` vs `user_id is None` -- empty string
        is falsy and must be rejected equally."""
        from app import config

        import jwt

        forged = jwt.encode(
            {"tenant_id": "tenant-a", "user_id": "", "role": "owner", "jti": "jti-a"},
            config.LOCAL_JWT_SECRET,
            algorithm="HS256",
        )
        with pytest.raises(InvalidTokenError):
            decode_token(forged)

    def test_decode_rejects_token_missing_jti_claim(self):
        """Mutation target: dropped `if not jti` guard or flipped to
        `if jti`. Without this check, a token without jti would construct
        a scope with jti=None, breaking revocation tracking."""
        from app import config

        import jwt

        forged = jwt.encode(
            {"tenant_id": "tenant-a", "user_id": "user-a", "role": "owner"},
            config.LOCAL_JWT_SECRET,
            algorithm="HS256",
        )
        with pytest.raises(InvalidTokenError) as exc_info:
            decode_token(forged)
        assert "jti" in str(exc_info.value)

    def test_decode_rejects_token_with_empty_string_jti(self):
        """Mutation target: `not jti` vs `jti is None` -- empty string must be
        rejected equally as it breaks revocation tracking."""
        from app import config

        import jwt

        forged = jwt.encode(
            {"tenant_id": "tenant-a", "user_id": "user-a", "role": "owner", "jti": ""},
            config.LOCAL_JWT_SECRET,
            algorithm="HS256",
        )
        with pytest.raises(InvalidTokenError):
            decode_token(forged)


class TestTokenRoleValidation:
    """Mutation target: role claim validation (KeyError handling, ValueError
    handling, unrecognized role rejection)."""

    def test_decode_rejects_token_with_missing_role_claim(self):
        """Mutation target: dropped KeyError exception handler or flipped
        exception type catch. Missing role must raise InvalidTokenError."""
        from app import config

        import jwt

        forged = jwt.encode(
            {"tenant_id": "tenant-a", "user_id": "user-a", "jti": "jti-a"},
            config.LOCAL_JWT_SECRET,
            algorithm="HS256",
        )
        with pytest.raises(InvalidTokenError) as exc_info:
            decode_token(forged)
        assert "unrecognized or missing role" in str(exc_info.value)

    def test_decode_rejects_token_with_unrecognized_role_value(self):
        """Mutation target: dropped ValueError exception handler or wrong
        enum constructor call. Unrecognized role string must raise."""
        from app import config

        import jwt

        forged = jwt.encode(
            {"tenant_id": "tenant-a", "user_id": "user-a", "role": "super_admin", "jti": "jti-a"},
            config.LOCAL_JWT_SECRET,
            algorithm="HS256",
        )
        with pytest.raises(InvalidTokenError) as exc_info:
            decode_token(forged)
        assert "unrecognized or missing role" in str(exc_info.value)

    def test_decode_accepts_all_valid_roles(self):
        """Mutation target: missing a valid role from the MembershipRole enum
        would cause decode to reject a valid token."""
        for role in Role:
            token = issue_token("tenant-a", "user-a", role)
            scope = decode_token(token)
            assert scope.role == role


class TestTokenJTIUniqueness:
    """Mutation target: jti generation (uuid.uuid4() call, string conversion)."""

    def test_every_issued_token_has_a_unique_jti(self):
        """Mutation target: removed `str(uuid.uuid4())` or replaced with
        deterministic value (hash, counter). Without real uniqueness, two
        tokens minted in sequence could collide on jti and break revocation."""
        jtis = set()
        for _ in range(100):
            token = issue_token("tenant-a", "user-a", Role.OWNER)
            scope = decode_token(token)
            assert scope.jti not in jtis, f"duplicate jti found: {scope.jti}"
            jtis.add(scope.jti)

    def test_issued_jti_decodes_back_identically(self):
        """Mutation target: jti string conversion or encoding issue. The jti
        issued must decode back as an exact string match."""
        import jwt

        from app import config

        token = issue_token("tenant-a", "user-a", Role.OWNER)
        scope = decode_token(token)
        claims = jwt.decode(token, config.LOCAL_JWT_SECRET, algorithms=["HS256"])
        assert scope.jti == claims["jti"]


class TestTokenTTL:
    """Mutation target: TTL calculation (seconds offset, timedelta,
    default value)."""

    def test_default_ttl_is_exactly_3600_seconds(self):
        """Mutation target: changed default TTL value, or operator flip in
        calculation. The one-hour default is a documented contract."""
        import jwt

        from app import config

        token = issue_token("tenant-a", "user-a", Role.OWNER)
        claims = jwt.decode(token, config.LOCAL_JWT_SECRET, algorithms=["HS256"])
        assert claims["exp"] - claims["iat"] == 3600

    def test_custom_ttl_overrides_default(self):
        """Mutation target: ignored ttl_seconds parameter or operator flip.
        Custom TTL must actually be used."""
        import jwt

        from app import config

        token = issue_token("tenant-a", "user-a", Role.OWNER, ttl_seconds=7200)
        claims = jwt.decode(token, config.LOCAL_JWT_SECRET, algorithms=["HS256"])
        assert claims["exp"] - claims["iat"] == 7200

    def test_zero_ttl_creates_expired_token(self):
        """Mutation target: off-by-one in expiry calculation or missing check.
        Zero TTL must produce an already-expired token."""
        token = issue_token("tenant-a", "user-a", Role.OWNER, ttl_seconds=0)
        with pytest.raises(InvalidTokenError):
            decode_token(token)


class TestVerifyTokenRevocationCheck:
    """Mutation target: revocation check fail-closed enforcement, missing
    denylist check, or swapped true/false logic."""

    def test_verify_token_rejects_revoked_token(self, db_session):
        """Mutation target: missing denylist check or flipped `if revoked`
        condition. A revoked token must be rejected."""
        from app.services.token_revocation import revoke_token

        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(hours=1)
        token = issue_token("tenant-a", "user-a", Role.OWNER, ttl_seconds=3600, session=db_session)
        db_session.commit()
        scope = decode_token(token)

        # Revoke the token
        revoke_token(
            db_session,
            jti=scope.jti,  # type: ignore[arg-type]
            tenant_id="tenant-a",
            expires_at=expires_at,
            actor_user_id="user-a",
            target_user_id="user-a",
        )
        db_session.commit()

        # verify_token must reject it
        with pytest.raises(InvalidTokenError) as exc_info:
            verify_token(token, db_session)
        assert "revoked" in str(exc_info.value)

    def test_verify_token_accepts_non_revoked_token(self, db_session):
        """Mutation target: flipped denylist check logic or missing accept path.
        A valid, non-revoked token must be accepted."""
        token = issue_token("tenant-a", "user-a", Role.OWNER, session=db_session)
        db_session.commit()

        scope = verify_token(token, db_session)
        assert scope.tenant_id == "tenant-a"
        assert scope.user_id == "user-a"

    def test_verify_token_fails_closed_on_denylist_error(self, db_session, monkeypatch):
        """Mutation target: swallowed exception (silent default to not-revoked)
        or missing fail-closed error handling. A denylist check error must
        raise InvalidTokenError, never silently pass."""
        token = issue_token("tenant-a", "user-a", Role.OWNER, session=db_session)
        db_session.commit()

        # Simulate a denylist check error
        def mock_is_token_revoked(session, jti):
            raise RuntimeError("denylist check failed")

        from app.services import auth

        monkeypatch.setattr(auth, "is_token_revoked", mock_is_token_revoked)

        # Must raise InvalidTokenError, not pass or raise RuntimeError
        with pytest.raises(InvalidTokenError) as exc_info:
            verify_token(token, db_session)
        assert "could not verify" in str(exc_info.value)


class TestTenantScopeImmutability:
    """Mutation target: frozen=True dataclass enforcement."""

    def test_tenant_scope_is_immutable(self):
        """Mutation target: `frozen=True` -> `frozen=False`. A scope
        decoded/verified must never be mutatable in place after the fact."""
        scope = TenantScope(tenant_id="tenant-a", user_id="user-a", role=Role.OWNER, jti="jti-a")

        from dataclasses import FrozenInstanceError

        with pytest.raises(FrozenInstanceError):
            scope.tenant_id = "tenant-b"  # type: ignore[misc]

    def test_tenant_scope_jti_defaults_to_none(self):
        """Mutation target: changed default value or removed default. The jti
        field's default of None is load-bearing for web-session paths."""
        scope = TenantScope(tenant_id="tenant-a", user_id="user-a", role=Role.OWNER)
        assert scope.jti is None


# =============================================================================
# PERMISSIONS.PY MUTATION TESTS
# =============================================================================
class TestAuthorizationFailsClosed:
    """Mutation target: dropped action lookup, wrong empty default, or
    flipped boolean logic."""

    def test_unknown_action_denies_every_role_including_owner(self):
        """Mutation target: changed `.get()` default from `frozenset()` to
        a non-empty set, or removed the `.get()` call entirely. An action
        with no entry must deny every role."""
        for role in Role:
            assert is_allowed(role, "unknown_action_xyz") is False

    def test_unregistered_action_does_not_grant_even_to_owner(self):
        """Mutation target: implicit permission hierarchy (owner can do
        everything) or wrong default. OWNER must also be denied for
        unregistered actions."""
        assert is_allowed(Role.OWNER, "nonexistent_action") is False

    def test_is_allowed_returns_boolean_not_truthy_value(self):
        """Mutation target: returned set membership directly instead of
        converting to bool, or changed return type. The return must be
        exactly `True` or `False`, not a set object or other truthy value."""
        result = is_allowed(Role.OWNER, "grant_rights")
        assert result is True
        assert type(result) is bool

        result = is_allowed(Role.RESEARCHER, "grant_rights")
        assert result is False
        assert type(result) is bool


class TestRoleAllowList:
    """Mutation target: wrong frozenset member (role omitted, extra role
    added, role misspelled)."""

    def test_owner_can_grant_rights(self):
        """Mutation target: OWNER role removed from grant_rights frozenset."""
        assert is_allowed(Role.OWNER, "grant_rights") is True

    def test_only_owner_can_grant_rights(self):
        """Mutation target: added REVIEWER, RESEARCHER, or other role to
        frozenset. Non-owner roles must be denied."""
        for role in Role:
            expected = role is Role.OWNER
            result = is_allowed(role, "grant_rights")
            assert result is expected, f"grant_rights for {role}: expected {expected}, got {result}"

    def test_owner_and_reviewer_can_release_strategy(self):
        """Mutation target: removed REVIEWER from frozenset or added extra
        role. Exactly OWNER and REVIEWER are allowed."""
        assert is_allowed(Role.OWNER, "release_strategy") is True
        assert is_allowed(Role.REVIEWER, "release_strategy") is True

    def test_non_owner_non_reviewer_cannot_release_strategy(self):
        """Mutation target: frozenset member addition. BILLING_OPERATOR and
        others must be denied."""
        assert is_allowed(Role.BILLING_OPERATOR, "release_strategy") is False
        assert is_allowed(Role.PUBLISHER_OPERATOR, "release_strategy") is False

    def test_only_owner_can_view_broker_credentials(self):
        """Mutation target: added SUPPORT_READONLY, CUSTOMER, or other role
        to frozenset. This is explicitly single-role access."""
        for role in Role:
            expected = role is Role.OWNER
            assert is_allowed(role, "view_broker_credentials") is expected

    def test_billing_operator_cannot_view_broker_credentials(self):
        """Mutation target: implicit permission hierarchy (billing ops can
        view financial secrets). BILLING_OPERATOR must be denied."""
        assert is_allowed(Role.BILLING_OPERATOR, "view_broker_credentials") is False

    def test_customer_cannot_view_broker_credentials(self):
        """Mutation target: CUSTOMER somehow added to frozenset. CUSTOMER is
        retail and must never see broker credentials."""
        assert is_allowed(Role.CUSTOMER, "view_broker_credentials") is False

    def test_billing_operator_can_manage_billing(self):
        """Mutation target: removed BILLING_OPERATOR from frozenset."""
        assert is_allowed(Role.BILLING_OPERATOR, "manage_billing") is True

    def test_only_owner_and_billing_operator_can_manage_billing(self):
        """Mutation target: added PUBLISHER_OPERATOR, RESEARCHER, or other
        role. Exactly OWNER and BILLING_OPERATOR are allowed."""
        assert is_allowed(Role.OWNER, "manage_billing") is True
        assert is_allowed(Role.BILLING_OPERATOR, "manage_billing") is True
        assert is_allowed(Role.PUBLISHER_OPERATOR, "manage_billing") is False
        assert is_allowed(Role.RESEARCHER, "manage_billing") is False

    def test_customer_can_manage_own_eligibility(self):
        """Mutation target: removed CUSTOMER from frozenset or added other
        role. Only CUSTOMER can manage their own eligibility."""
        assert is_allowed(Role.CUSTOMER, "manage_own_eligibility") is True

    def test_only_customer_can_manage_own_eligibility(self):
        """Mutation target: added OWNER, RESEARCHER, or other role."""
        for role in Role:
            expected = role is Role.CUSTOMER
            assert is_allowed(role, "manage_own_eligibility") is expected

    def test_only_owner_can_manage_staff_access(self):
        """Mutation target: added REVIEWER, BILLING_OPERATOR, or other role.
        This is explicitly owner-only for role granting/revoking."""
        for role in Role:
            expected = role is Role.OWNER
            assert is_allowed(role, "manage_staff_access") is expected


class TestRequirePermissionRaisesOnDenial:
    """Mutation target: flipped if condition, removed exception, or changed
    exception type."""

    def test_require_permission_allows_authorized_role(self):
        """Mutation target: flipped `if not` to `if`, which would raise on
        allowed roles."""
        # Must not raise
        require_permission(Role.OWNER, "grant_rights")

    def test_require_permission_raises_on_denied_role(self):
        """Mutation target: removed exception or changed exception type."""
        with pytest.raises(PermissionDenied) as exc_info:
            require_permission(Role.RESEARCHER, "grant_rights")
        assert "grant_rights" in str(exc_info.value)
        assert "researcher" in str(exc_info.value).lower()

    def test_require_permission_raises_for_unknown_action(self):
        """Mutation target: dropped unknown-action denial or flipped logic."""
        with pytest.raises(PermissionDenied):
            require_permission(Role.OWNER, "nonexistent_action_xyz")

    def test_require_permission_error_message_includes_role_and_action(self):
        """Mutation target: dropped error message components or changed
        message format. Diagnostic info must be present."""
        with pytest.raises(PermissionDenied) as exc_info:
            require_permission(Role.BILLING_OPERATOR, "view_broker_credentials")
        msg = str(exc_info.value)
        assert "view_broker_credentials" in msg
        assert "billing_operator" in msg.lower()


# =============================================================================
# CUSTOMER_BILLING.PY MUTATION TESTS
# =============================================================================
class TestBillingStateCurrentSubscription:
    """Mutation target: subscription sorting (DESC vs ASC), index selection
    (0 vs 1), None vs empty list check."""

    @staticmethod
    def _subscription(tenant_id, subscription_id, *, state, created_at, tier=ProductTier.ALERTS_ONE, price_cents=3900):
        return Subscription(
            subscription_id=subscription_id,
            tenant_id=tenant_id,
            tier=tier,
            state=state,
            price_cents=price_cents,
            currency="usd",
            current_period_end=datetime.now(timezone.utc) + timedelta(days=30),
            created_at=created_at,
        )

    def test_current_subscription_is_most_recent_by_created_at(self, db_session):
        """Mutation target: DESC vs ASC ordering (would pick oldest instead
        of newest), or wrong index [1] instead of [0]."""
        now = datetime.now(timezone.utc)
        older = self._subscription("tenant-a", "sub-older", state=SubscriptionState.ENDED, created_at=now - timedelta(days=60))
        newer = self._subscription("tenant-a", "sub-newer", state=SubscriptionState.ACTIVE_PAID, created_at=now - timedelta(days=1))
        # Insert newer first to catch insertion-order vs created_at bugs
        db_session.add(newer)
        db_session.add(older)
        db_session.commit()

        state = get_own_billing_state(db_session, tenant_id="tenant-a")

        assert state.current.subscription_id == "sub-newer"

    def test_current_returns_none_when_no_subscriptions(self, db_session):
        """Mutation target: changed [0] check to [1] or removed empty list
        guard. Empty list must return None, not IndexError."""
        state = get_own_billing_state(db_session, tenant_id="tenant-a")

        assert state.current is None

    def test_subscriptions_list_is_ordered_newest_first(self, db_session):
        """Mutation target: DESC vs ASC ordering (would reverse list order).
        The list itself must be newest-first, not just current."""
        now = datetime.now(timezone.utc)
        sub1 = self._subscription("tenant-a", "sub-1", state=SubscriptionState.ENDED, created_at=now - timedelta(days=30))
        sub2 = self._subscription("tenant-a", "sub-2", state=SubscriptionState.ACTIVE_PAID, created_at=now - timedelta(days=1))
        sub3 = self._subscription("tenant-a", "sub-3", state=SubscriptionState.PENDING_PAYMENT, created_at=now)
        db_session.add(sub1)
        db_session.add(sub2)
        db_session.add(sub3)
        db_session.commit()

        state = get_own_billing_state(db_session, tenant_id="tenant-a")

        ids = [s.subscription_id for s in state.subscriptions]
        assert ids == ["sub-3", "sub-2", "sub-1"]

    def test_subscriptions_includes_all_tenant_rows(self, db_session):
        """Mutation target: dropped WHERE clause or wrong tenant_id check.
        All tenant subscriptions must be included (not just current)."""
        now = datetime.now(timezone.utc)
        db_session.add(self._subscription("tenant-a", "sub-a1", state=SubscriptionState.ENDED, created_at=now - timedelta(days=30)))
        db_session.add(self._subscription("tenant-a", "sub-a2", state=SubscriptionState.ACTIVE_PAID, created_at=now))
        db_session.add(self._subscription("tenant-b", "sub-b", state=SubscriptionState.ACTIVE_PAID, created_at=now))
        db_session.commit()

        state = get_own_billing_state(db_session, tenant_id="tenant-a")

        ids = [s.subscription_id for s in state.subscriptions]
        assert set(ids) == {"sub-a1", "sub-a2"}
        assert "sub-b" not in ids


class TestBillingStateTenantScoping:
    """Mutation target: dropped WHERE clause on tenant_id, wrong comparison
    operator (== vs !=)."""

    @staticmethod
    def _subscription(tenant_id, subscription_id, *, state, created_at, tier=ProductTier.ALERTS_ONE, price_cents=3900):
        return Subscription(
            subscription_id=subscription_id,
            tenant_id=tenant_id,
            tier=tier,
            state=state,
            price_cents=price_cents,
            currency="usd",
            current_period_end=datetime.now(timezone.utc) + timedelta(days=30),
            created_at=created_at,
        )

    def test_billing_state_filters_by_tenant_id_exactly(self, db_session):
        """Mutation target: == vs !=, dropped WHERE, or wrong column."""
        now = datetime.now(timezone.utc)
        db_session.add(self._subscription("tenant-a", "sub-a", state=SubscriptionState.ACTIVE_PAID, created_at=now))
        db_session.add(self._subscription("tenant-b", "sub-b", state=SubscriptionState.ACTIVE_PAID, created_at=now))
        db_session.add(self._subscription("tenant-c", "sub-c", state=SubscriptionState.ACTIVE_PAID, created_at=now))
        db_session.commit()

        state_a = get_own_billing_state(db_session, tenant_id="tenant-a")
        state_b = get_own_billing_state(db_session, tenant_id="tenant-b")
        state_c = get_own_billing_state(db_session, tenant_id="tenant-c")

        assert [s.subscription_id for s in state_a.subscriptions] == ["sub-a"]
        assert [s.subscription_id for s in state_b.subscriptions] == ["sub-b"]
        assert [s.subscription_id for s in state_c.subscriptions] == ["sub-c"]

    def test_billing_state_rejects_cross_tenant_query(self, db_session):
        """Mutation target: dropped or inverted tenant_id check. Querying
        tenant-a with tenant-b's data must not leak."""
        now = datetime.now(timezone.utc)
        db_session.add(self._subscription("tenant-b", "sub-b", state=SubscriptionState.ACTIVE_PAID, created_at=now))
        db_session.commit()

        state = get_own_billing_state(db_session, tenant_id="tenant-a")

        assert state.subscriptions == []


class TestBillingStateEntitlementDelegation:
    """Mutation target: property logic, None handling, delegation call."""

    @staticmethod
    def _subscription(tenant_id, subscription_id, *, state, created_at, tier=ProductTier.ALERTS_ONE, price_cents=3900):
        return Subscription(
            subscription_id=subscription_id,
            tenant_id=tenant_id,
            tier=tier,
            state=state,
            price_cents=price_cents,
            currency="usd",
            current_period_end=datetime.now(timezone.utc) + timedelta(days=30),
            created_at=created_at,
        )

    def test_authorizes_new_entry_is_none_when_no_current(self, db_session):
        """Mutation target: returned False instead of None, or dropped None
        check. Empty state must return None, not False."""
        state = get_own_billing_state(db_session, tenant_id="tenant-a")

        assert state.authorizes_new_entry is None

    def test_authorizes_new_entry_delegates_to_entitlement_module(self, db_session):
        """Mutation target: hardcoded True/False instead of calling
        authorizes_new_entry(), or wrong subscription property accessed."""
        now = datetime.now(timezone.utc)
        db_session.add(self._subscription("tenant-a", "sub-a", state=SubscriptionState.ACTIVE_PAID, created_at=now))
        db_session.commit()

        state = get_own_billing_state(db_session, tenant_id="tenant-a")

        # Must match what the entitlement module returns, not a hardcoded value
        expected = authorizes_new_entry(state.current)
        assert state.authorizes_new_entry is expected
        assert state.authorizes_new_entry is True

    def test_authorizes_risk_reducing_management_is_none_when_no_current(self, db_session):
        """Mutation target: returned False instead of None, or dropped None
        check."""
        state = get_own_billing_state(db_session, tenant_id="tenant-a")

        assert state.authorizes_risk_reducing_management is None

    def test_authorizes_risk_reducing_management_delegates_to_entitlement_module(self, db_session):
        """Mutation target: hardcoded True/False instead of calling
        authorizes_risk_reducing_management(), or wrong subscription property."""
        now = datetime.now(timezone.utc)
        db_session.add(self._subscription("tenant-a", "sub-a", state=SubscriptionState.PAST_DUE, created_at=now))
        db_session.commit()

        state = get_own_billing_state(db_session, tenant_id="tenant-a")

        expected = authorizes_risk_reducing_management(state.current)
        assert state.authorizes_risk_reducing_management is expected
        assert state.authorizes_risk_reducing_management is True


class TestSubscriptionStateAuthorization:
    """Mutation target: membership check (in vs not-in), frozenset member
    (state omitted, extra state added), boundary conditions."""

    @staticmethod
    def _subscription(tenant_id, subscription_id, *, state, created_at, tier=ProductTier.ALERTS_ONE, price_cents=3900):
        return Subscription(
            subscription_id=subscription_id,
            tenant_id=tenant_id,
            tier=tier,
            state=state,
            price_cents=price_cents,
            currency="usd",
            current_period_end=datetime.now(timezone.utc) + timedelta(days=30),
            created_at=created_at,
        )

    def test_active_paid_authorizes_new_entry(self, db_session):
        """Mutation target: removed ACTIVE_PAID from frozenset or flipped
        membership check."""
        now = datetime.now(timezone.utc)
        db_session.add(self._subscription("tenant-a", "sub-a", state=SubscriptionState.ACTIVE_PAID, created_at=now))
        db_session.commit()

        state = get_own_billing_state(db_session, tenant_id="tenant-a")
        assert state.authorizes_new_entry is True

    def test_trial_authorized_allows_new_entry(self, db_session):
        """Mutation target: removed TRIAL_AUTHORIZED from frozenset."""
        now = datetime.now(timezone.utc)
        db_session.add(self._subscription("tenant-a", "sub-a", state=SubscriptionState.TRIAL_AUTHORIZED, created_at=now))
        db_session.commit()

        state = get_own_billing_state(db_session, tenant_id="tenant-a")
        assert state.authorizes_new_entry is True

    def test_cancel_at_period_end_allows_new_entry(self, db_session):
        """Mutation target: removed CANCEL_AT_PERIOD_END from frozenset.
        Customer remains paid through period_end; new entries aren't blocked
        until after the period ends (ENDED state)."""
        now = datetime.now(timezone.utc)
        db_session.add(self._subscription("tenant-a", "sub-a", state=SubscriptionState.CANCEL_AT_PERIOD_END, created_at=now))
        db_session.commit()

        state = get_own_billing_state(db_session, tenant_id="tenant-a")
        assert state.authorizes_new_entry is True

    def test_pending_payment_blocks_new_entry(self, db_session):
        """Mutation target: added PENDING_PAYMENT to frozenset."""
        now = datetime.now(timezone.utc)
        db_session.add(self._subscription("tenant-a", "sub-a", state=SubscriptionState.PENDING_PAYMENT, created_at=now))
        db_session.commit()

        state = get_own_billing_state(db_session, tenant_id="tenant-a")
        assert state.authorizes_new_entry is False

    def test_past_due_blocks_new_entry(self, db_session):
        """Mutation target: added PAST_DUE to new-entry frozenset."""
        now = datetime.now(timezone.utc)
        db_session.add(self._subscription("tenant-a", "sub-a", state=SubscriptionState.PAST_DUE, created_at=now))
        db_session.commit()

        state = get_own_billing_state(db_session, tenant_id="tenant-a")
        assert state.authorizes_new_entry is False

    def test_ended_blocks_new_entry(self, db_session):
        """Mutation target: added ENDED to new-entry frozenset."""
        now = datetime.now(timezone.utc)
        db_session.add(self._subscription("tenant-a", "sub-a", state=SubscriptionState.ENDED, created_at=now))
        db_session.commit()

        state = get_own_billing_state(db_session, tenant_id="tenant-a")
        assert state.authorizes_new_entry is False

    def test_suspended_new_entries_blocks_new_entry(self, db_session):
        """Mutation target: added SUSPENDED_NEW_ENTRIES to new-entry frozenset."""
        now = datetime.now(timezone.utc)
        db_session.add(self._subscription("tenant-a", "sub-a", state=SubscriptionState.SUSPENDED_NEW_ENTRIES, created_at=now))
        db_session.commit()

        state = get_own_billing_state(db_session, tenant_id="tenant-a")
        assert state.authorizes_new_entry is False

    def test_disputed_blocks_new_entry(self, db_session):
        """Mutation target: added DISPUTED to new-entry frozenset."""
        now = datetime.now(timezone.utc)
        db_session.add(self._subscription("tenant-a", "sub-a", state=SubscriptionState.DISPUTED, created_at=now))
        db_session.commit()

        state = get_own_billing_state(db_session, tenant_id="tenant-a")
        assert state.authorizes_new_entry is False

    def test_manual_review_blocks_new_entry(self, db_session):
        """Mutation target: added MANUAL_REVIEW to new-entry frozenset."""
        now = datetime.now(timezone.utc)
        db_session.add(self._subscription("tenant-a", "sub-a", state=SubscriptionState.MANUAL_REVIEW, created_at=now))
        db_session.commit()

        state = get_own_billing_state(db_session, tenant_id="tenant-a")
        assert state.authorizes_new_entry is False


class TestRiskReducingManagementAuthorization:
    """Mutation target: frozenset membership (in vs not-in), state omission
    or addition, boundary conditions."""

    @staticmethod
    def _subscription(tenant_id, subscription_id, *, state, created_at, tier=ProductTier.ALERTS_ONE, price_cents=3900):
        return Subscription(
            subscription_id=subscription_id,
            tenant_id=tenant_id,
            tier=tier,
            state=state,
            price_cents=price_cents,
            currency="usd",
            current_period_end=datetime.now(timezone.utc) + timedelta(days=30),
            created_at=created_at,
        )

    def test_pending_payment_blocks_management(self, db_session):
        """Mutation target: removed exclusion of PENDING_PAYMENT from
        management states. PENDING_PAYMENT has never represented an admitted
        customer relationship -- there is no existing exposure to protect."""
        now = datetime.now(timezone.utc)
        db_session.add(self._subscription("tenant-a", "sub-a", state=SubscriptionState.PENDING_PAYMENT, created_at=now))
        db_session.commit()

        state = get_own_billing_state(db_session, tenant_id="tenant-a")
        assert state.authorizes_risk_reducing_management is False

    def test_active_paid_allows_management(self, db_session):
        """Mutation target: added ACTIVE_PAID to exclusion set or flipped
        membership check."""
        now = datetime.now(timezone.utc)
        db_session.add(self._subscription("tenant-a", "sub-a", state=SubscriptionState.ACTIVE_PAID, created_at=now))
        db_session.commit()

        state = get_own_billing_state(db_session, tenant_id="tenant-a")
        assert state.authorizes_risk_reducing_management is True

    def test_past_due_allows_management(self, db_session):
        """Mutation target: added PAST_DUE to exclusion set. Billing trouble
        must not revoke protective order management."""
        now = datetime.now(timezone.utc)
        db_session.add(self._subscription("tenant-a", "sub-a", state=SubscriptionState.PAST_DUE, created_at=now))
        db_session.commit()

        state = get_own_billing_state(db_session, tenant_id="tenant-a")
        assert state.authorizes_risk_reducing_management is True

    def test_suspended_new_entries_allows_management(self, db_session):
        """Mutation target: added SUSPENDED_NEW_ENTRIES to exclusion set."""
        now = datetime.now(timezone.utc)
        db_session.add(self._subscription("tenant-a", "sub-a", state=SubscriptionState.SUSPENDED_NEW_ENTRIES, created_at=now))
        db_session.commit()

        state = get_own_billing_state(db_session, tenant_id="tenant-a")
        assert state.authorizes_risk_reducing_management is True

    def test_disputed_allows_management(self, db_session):
        """Mutation target: added DISPUTED to exclusion set."""
        now = datetime.now(timezone.utc)
        db_session.add(self._subscription("tenant-a", "sub-a", state=SubscriptionState.DISPUTED, created_at=now))
        db_session.commit()

        state = get_own_billing_state(db_session, tenant_id="tenant-a")
        assert state.authorizes_risk_reducing_management is True

    def test_manual_review_allows_management(self, db_session):
        """Mutation target: added MANUAL_REVIEW to exclusion set."""
        now = datetime.now(timezone.utc)
        db_session.add(self._subscription("tenant-a", "sub-a", state=SubscriptionState.MANUAL_REVIEW, created_at=now))
        db_session.commit()

        state = get_own_billing_state(db_session, tenant_id="tenant-a")
        assert state.authorizes_risk_reducing_management is True

    def test_ended_allows_management(self, db_session):
        """Mutation target: added ENDED to exclusion set. Even after
        subscription ends, existing positions may still need management."""
        now = datetime.now(timezone.utc)
        db_session.add(self._subscription("tenant-a", "sub-a", state=SubscriptionState.ENDED, created_at=now))
        db_session.commit()

        state = get_own_billing_state(db_session, tenant_id="tenant-a")
        assert state.authorizes_risk_reducing_management is True

    def test_cancel_at_period_end_allows_management(self, db_session):
        """Mutation target: added CANCEL_AT_PERIOD_END to exclusion set."""
        now = datetime.now(timezone.utc)
        db_session.add(self._subscription("tenant-a", "sub-a", state=SubscriptionState.CANCEL_AT_PERIOD_END, created_at=now))
        db_session.commit()

        state = get_own_billing_state(db_session, tenant_id="tenant-a")
        assert state.authorizes_risk_reducing_management is True

    def test_trial_authorized_allows_management(self, db_session):
        """Mutation target: added TRIAL_AUTHORIZED to exclusion set."""
        now = datetime.now(timezone.utc)
        db_session.add(self._subscription("tenant-a", "sub-a", state=SubscriptionState.TRIAL_AUTHORIZED, created_at=now))
        db_session.commit()

        state = get_own_billing_state(db_session, tenant_id="tenant-a")
        assert state.authorizes_risk_reducing_management is True


class TestCustomerBillingStateImmutability:
    """Mutation target: frozen=True dataclass enforcement."""

    def test_customer_billing_state_is_immutable(self):
        """Mutation target: `frozen=True` -> `frozen=False`. A billing state
        value object must not be mutable."""
        from dataclasses import FrozenInstanceError

        state = CustomerBillingState(subscriptions=[])

        with pytest.raises(FrozenInstanceError):
            state.subscriptions = []  # type: ignore[misc]
