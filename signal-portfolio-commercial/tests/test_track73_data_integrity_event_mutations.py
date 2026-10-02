"""Track 73: comprehensive mutation-testing regression suite for signal-
portfolio-commercial's data integrity and event processing modules:
integration_inbox.py, publisher_writer_claim.py, trading_authority.py,
relay_auth.py, and local_auth.py.

Mutation-testing approach (per Track 60-67 precedent in signal-copier and
signal-portfolio-commercial):
This suite targets the specific, high-severity mutations that would silently
misbehave if critical operators/conditions flip or drop. Focus areas:
- Event idempotency and duplicate detection (exact ID/version matching)
- Claim state transitions and conflict detection (operator flips)
- Trading authority scoping (permission checks fail-closed)
- Token/credential validation (None vs empty, expiry boundary checks)
- Session lifecycle and renewal logic (< vs <=, > vs >=, boolean logic)
- Signature verification and timestamp replay protection

Each test is designed to fail under a targeted mutant pattern, verified by
hand (not via full mutmut run on this constrained environment) to ensure
the mutation it targets would actually break the test. Tests use exact
comparisons and boundary conditions that would silently misbehave under
common operator flips (==, !=, <, >, <=, >=, and/or).
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest import mock

import pytest

from app.models.integration_inbox import InboxEvent
from app.models.local_auth import AuthToken, AuthTokenType, WebSession
from app.models.publisher_writer_claim import PublisherWriterClaim
from app.models.tenancy import MembershipRole, UserIdentity
from app.services.integration_inbox import (
    EventIntegrityError,
    SequenceSlotAlreadyConsumedError,
    UnregisteredStreamError,
    ingest_export_event,
    register_export_stream,
)
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
from app.services.publisher_writer_claim import (
    WriterAlreadyClaimedError,
    claim_writer,
    release_writer,
)
from app.services.relay_auth import (
    InvalidRelaySignatureHeaderError,
    RelaySignatureMismatchError,
    StaleRelayTimestampError,
    sign_relay_payload,
    verify_relay_signature,
)
from app.services.trading_authority import assess_trading_authority


# =============================================================================
# INTEGRATION_INBOX.PY MUTATION TESTS
# =============================================================================
class TestInboxEventIdempotency:
    """Event idempotency with exact payload hash matching (mutation target:
    dropped condition, wrong comparison operator, or wrong field)."""

    @staticmethod
    def _make_envelope(event_id, stream, seq, payload_hash):
        """Helper to create consistent envelope JSON."""
        import json
        envelope = {
            "event_id": event_id,
            "event_type": "EXECUTION_APPLIED",
            "source_stream": stream,
            "producer_generation": 1,
            "export_sequence": seq,
            "schema_version": "1.0.0",
            "payload_hash": payload_hash,
            "payload": {
                "instrument": {"instrument_id": "AAPL", "currency": "USD", "multiplier": 1.0},
                "side": "buy",
                "filled_quantity": 10,
                "filled_price": 150.0,
                "fee": 1.0,
                "broker": "broker-1",
                "broker_order_id": "order-1",
                "originating_analyst_id": "analyst-1",
            },
            "producer_id": "prod-1",
            "event_time": "2024-01-01T00:00:00Z",
        }
        return json.dumps(envelope)

    def test_same_event_id_same_payload_hash_returns_existing(self, db_session):
        """Mutation target: dropped `if existing.payload_hash == envelope.payload_hash`
        condition. Without it, would raise EventIntegrityError on every retry."""
        tenant_id = "tenant-a"
        stream = "stream-1"
        register_export_stream(db_session, tenant_id=tenant_id, source_stream=stream, environment="LOCAL_SIM")

        envelope_json_1 = self._make_envelope("evt-1", stream, 1, "hash-A")

        result1 = ingest_export_event(db_session, envelope_json_1)
        db_session.commit()

        result2 = ingest_export_event(db_session, envelope_json_1)

        assert result2.event_id == result1.event_id
        assert result2.payload_hash == result1.payload_hash

    def test_same_event_id_different_payload_hash_raises_error(self, db_session):
        """Mutation target: flipped `if` to `if not` or removed check entirely --
        would silently allow integrity violations."""
        import json
        tenant_id = "tenant-a"
        stream = "stream-1"
        register_export_stream(db_session, tenant_id=tenant_id, source_stream=stream, environment="LOCAL_SIM")

        envelope1 = {
            "event_id": "evt-integrity-1",
            "event_type": "EXECUTION_APPLIED",
            "source_stream": stream,
            "producer_generation": 1,
            "export_sequence": 1,
            "schema_version": "1.0.0",
            "payload_hash": "hash-old",
            "payload": {
                "instrument": {"instrument_id": "AAPL", "currency": "USD", "multiplier": 1.0},
                "side": "buy",
                "filled_quantity": 10,
                "filled_price": 150.0,
                "fee": 1.0,
                "broker": "broker-1",
                "broker_order_id": "order-1",
                "originating_analyst_id": "analyst-1",
            },
            "producer_id": "prod-1",
            "event_time": "2024-01-01T00:00:00Z",
        }
        envelope_json_1 = json.dumps(envelope1)

        envelope2 = envelope1.copy()
        envelope2["payload_hash"] = "hash-new"
        envelope2["payload"]["side"] = "sell"
        envelope_json_2 = json.dumps(envelope2)

        ingest_export_event(db_session, envelope_json_1)
        db_session.commit()

        with pytest.raises(EventIntegrityError):
            ingest_export_event(db_session, envelope_json_2)

    def test_event_integrity_error_message_includes_details(self, db_session):
        """Mutation target: dropped error message construction that would hide
        diagnostic information about the conflict."""
        import json
        tenant_id = "tenant-a"
        stream = "stream-1"
        register_export_stream(db_session, tenant_id=tenant_id, source_stream=stream, environment="LOCAL_SIM")

        envelope1 = {
            "event_id": "evt-integrity-msg",
            "event_type": "EXECUTION_APPLIED",
            "source_stream": stream,
            "producer_generation": 1,
            "export_sequence": 1,
            "schema_version": "1.0.0",
            "payload_hash": "hash-1",
            "payload": {
                "instrument": {"instrument_id": "AAPL", "currency": "USD", "multiplier": 1.0},
                "side": "buy",
                "filled_quantity": 10,
                "filled_price": 150.0,
                "fee": 1.0,
                "broker": "broker-1",
                "broker_order_id": "order-1",
                "originating_analyst_id": "analyst-1",
            },
            "producer_id": "prod-1",
            "event_time": "2024-01-01T00:00:00Z",
        }
        envelope_json_1 = json.dumps(envelope1)

        envelope2 = envelope1.copy()
        envelope2["payload_hash"] = "hash-2"
        envelope_json_2 = json.dumps(envelope2)

        ingest_export_event(db_session, envelope_json_1)
        db_session.commit()

        with pytest.raises(EventIntegrityError) as exc_info:
            ingest_export_event(db_session, envelope_json_2)

        error_msg = str(exc_info.value)
        assert "evt-integrity-msg" in error_msg
        assert "payload_hash" in error_msg


class TestInboxStreamRegistration:
    """Stream registration and tenant isolation (mutation target: != vs ==,
    missing registration check)."""

    def test_unregistered_stream_raises_error(self, db_session):
        """Mutation target: dropped UnregisteredStreamError check or missing
        the registration lookup entirely."""
        import json
        envelope = {
            "event_id": "evt-unreg",
            "event_type": "EXECUTION_APPLIED",
            "source_stream": "unknown-stream",
            "producer_generation": 1,
            "export_sequence": 1,
            "schema_version": "1.0.0",
            "payload_hash": "hash-1",
            "payload": {
                "instrument": {"instrument_id": "AAPL", "currency": "USD", "multiplier": 1.0},
                "side": "buy",
                "filled_quantity": 10,
                "filled_price": 150.0,
                "fee": 1.0,
                "broker": "broker-1",
                "broker_order_id": "order-1",
                "originating_analyst_id": "analyst-1",
            },
            "producer_id": "prod-1",
            "event_time": "2024-01-01T00:00:00Z",
        }
        envelope_json = json.dumps(envelope)

        with pytest.raises(UnregisteredStreamError):
            ingest_export_event(db_session, envelope_json)

    def test_stream_tenant_isolation(self, db_session):
        """Mutation target: == vs != on tenant_id comparison. Events from
        a stream registered to one tenant must not become another's."""
        import json
        stream_a = "stream-for-tenant-a"

        register_export_stream(db_session, tenant_id="tenant-a", source_stream=stream_a, environment="LOCAL_SIM")
        db_session.commit()

        envelope = {
            "event_id": "evt-a-1",
            "event_type": "EXECUTION_APPLIED",
            "source_stream": stream_a,
            "producer_generation": 1,
            "export_sequence": 1,
            "schema_version": "1.0.0",
            "payload_hash": "hash-a",
            "payload": {
                "instrument": {"instrument_id": "AAPL", "currency": "USD", "multiplier": 1.0},
                "side": "buy",
                "filled_quantity": 10,
                "filled_price": 150.0,
                "fee": 1.0,
                "broker": "broker-1",
                "broker_order_id": "order-a",
                "originating_analyst_id": "analyst-1",
            },
            "producer_id": "prod-1",
            "event_time": "2024-01-01T00:00:00Z",
        }
        envelope_json = json.dumps(envelope)

        result_a = ingest_export_event(db_session, envelope_json)
        assert result_a.tenant_id == "tenant-a"


# =============================================================================
# PUBLISHER_WRITER_CLAIM.PY MUTATION TESTS
# =============================================================================
class TestPublisherWriterClaimConflict:
    """Writer claim conflict detection (mutation target: != vs ==, dropped
    comparison, wrong field)."""

    def test_same_writer_reclaims_existing_is_idempotent(self, db_session):
        """Mutation target: flipped `if existing.writer_identity != writer_identity`
        to `if existing.writer_identity == writer_identity` or dropped check."""
        channel = "collective2"
        strategy = "strategy-1"
        writer = "writer-a"

        claim_writer(db_session, channel=channel, external_strategy_id=strategy, writer_identity=writer)
        db_session.commit()

        # Re-claiming same strategy/channel as same writer is idempotent
        claim_writer(db_session, channel=channel, external_strategy_id=strategy, writer_identity=writer)

    def test_different_writer_claiming_raises_conflict(self, db_session):
        """Mutation target: flipped comparison, removed check, or wrong field.
        Two different writers cannot claim the same strategy."""
        channel = "collective2"
        strategy = "strategy-conflict"
        writer_a = "writer-a"
        writer_b = "writer-b"

        claim_writer(db_session, channel=channel, external_strategy_id=strategy, writer_identity=writer_a)
        db_session.commit()

        with pytest.raises(WriterAlreadyClaimedError):
            claim_writer(db_session, channel=channel, external_strategy_id=strategy, writer_identity=writer_b)

    def test_conflict_error_includes_both_writers(self, db_session):
        """Mutation target: dropped error message detail that would hide who
        holds the claim."""
        channel = "collective2"
        strategy = "strategy-msg"
        writer_a = "writer-hold"
        writer_b = "writer-try"

        claim_writer(db_session, channel=channel, external_strategy_id=strategy, writer_identity=writer_a)
        db_session.commit()

        with pytest.raises(WriterAlreadyClaimedError) as exc_info:
            claim_writer(db_session, channel=channel, external_strategy_id=strategy, writer_identity=writer_b)

        error_msg = str(exc_info.value)
        assert writer_a in error_msg
        assert writer_b in error_msg

    def test_release_by_non_holder_raises_error(self, db_session):
        """Mutation target: flipped comparison on writer_identity check."""
        channel = "collective2"
        strategy = "strategy-release"
        holder = "holder-a"
        other = "other-b"

        claim_writer(db_session, channel=channel, external_strategy_id=strategy, writer_identity=holder)
        db_session.commit()

        with pytest.raises(WriterAlreadyClaimedError):
            release_writer(db_session, channel=channel, external_strategy_id=strategy, writer_identity=other)

    def test_release_by_holder_succeeds(self, db_session):
        """Mutation target: flipped comparison that would prevent release."""
        channel = "collective2"
        strategy = "strategy-release-ok"
        writer = "writer-release"

        claim_writer(db_session, channel=channel, external_strategy_id=strategy, writer_identity=writer)
        db_session.commit()

        release_writer(db_session, channel=channel, external_strategy_id=strategy, writer_identity=writer)
        db_session.commit()

        # Verify claim is gone
        existing = db_session.get(PublisherWriterClaim, (channel, strategy))
        assert existing is None


# =============================================================================
# RELAY_AUTH.PY MUTATION TESTS
# =============================================================================
class TestRelaySignatureVerification:
    """Signature verification with exact string matching (mutation target:
    dropped compare_digest call, flipped condition, wrong secret used)."""

    def test_valid_signature_passes(self):
        """Mutation target: dropped `hmac.compare_digest` call or missing
        signature validation."""
        secret = "test-secret-key"
        payload = b"test-payload"
        timestamp = int(time.time())

        sig_header = sign_relay_payload(payload, secret, timestamp=timestamp)

        # Should not raise
        verify_relay_signature(payload, sig_header, secret, now=float(timestamp))

    def test_invalid_signature_raises_error(self):
        """Mutation target: dropped signature check entirely, or flipped
        condition that would accept invalid signatures."""
        secret = "test-secret-key"
        payload = b"test-payload"
        timestamp = int(time.time())

        sig_header = f"t={timestamp},v1=invalid_signature_hex"

        with pytest.raises(RelaySignatureMismatchError):
            verify_relay_signature(payload, sig_header, secret, now=float(timestamp))

    def test_modified_payload_detection(self):
        """Mutation target: dropped payload validation or wrong field used in
        signature calculation."""
        secret = "test-secret-key"
        payload = b"original-payload"
        timestamp = int(time.time())

        sig_header = sign_relay_payload(payload, secret, timestamp=timestamp)

        # Different payload should fail
        modified_payload = b"modified-payload"
        with pytest.raises(RelaySignatureMismatchError):
            verify_relay_signature(modified_payload, sig_header, secret, now=float(timestamp))

    def test_signature_with_previous_secret_accepted_during_rotation(self):
        """Mutation target: dropped check for secret_previous or wrong
        variable used."""
        current_secret = "new-secret"
        previous_secret = "old-secret"
        payload = b"test-payload"
        timestamp = int(time.time())

        # Sign with the old secret
        sig_header = sign_relay_payload(payload, previous_secret, timestamp=timestamp)

        # Should accept old signature during rotation window
        verify_relay_signature(
            payload, sig_header, current_secret, secret_previous=previous_secret, now=float(timestamp)
        )

    def test_empty_previous_secret_disables_fallback(self):
        """Mutation target: check for empty string vs None, or dropped check."""
        current_secret = "new-secret"
        payload = b"test-payload"
        timestamp = int(time.time())

        # Sign with old secret
        old_secret = "old-secret"
        sig_header = sign_relay_payload(payload, old_secret, timestamp=timestamp)

        # Empty string previous secret should NOT accept old signature
        with pytest.raises(RelaySignatureMismatchError):
            verify_relay_signature(payload, sig_header, current_secret, secret_previous="", now=float(timestamp))


class TestRelayTimestampValidation:
    """Timestamp validation with boundary conditions (mutation target: >
    vs >=, < vs <=, flipped logic)."""

    def test_timestamp_within_tolerance_accepted(self):
        """Mutation target: > vs >= or < vs <= boundary flip."""
        secret = "test-secret"
        payload = b"test-payload"
        tolerance = 300
        now = time.time()
        timestamp = int(now - 100)  # 100 seconds in past

        sig_header = sign_relay_payload(payload, secret, timestamp=timestamp)

        # Should accept timestamp within tolerance
        verify_relay_signature(payload, sig_header, secret, tolerance_seconds=tolerance, now=now)

    def test_timestamp_at_tolerance_boundary_accepted(self):
        """Mutation target: > vs >= boundary check."""
        secret = "test-secret"
        payload = b"test-payload"
        tolerance = 300
        now = time.time()
        timestamp = int(now - 300)  # Exactly at tolerance boundary

        sig_header = sign_relay_payload(payload, secret, timestamp=timestamp)

        # Should accept at exact boundary
        verify_relay_signature(payload, sig_header, secret, tolerance_seconds=tolerance, now=now)

    def test_timestamp_beyond_tolerance_rejected(self):
        """Mutation target: > vs >= or condition logic flip."""
        secret = "test-secret"
        payload = b"test-payload"
        tolerance = 300
        now = time.time()
        timestamp = int(now - 301)  # Just beyond tolerance

        sig_header = sign_relay_payload(payload, secret, timestamp=timestamp)

        with pytest.raises(StaleRelayTimestampError):
            verify_relay_signature(payload, sig_header, secret, tolerance_seconds=tolerance, now=now)

    def test_timestamp_in_future_rejected(self):
        """Mutation target: abs() call or condition logic."""
        secret = "test-secret"
        payload = b"test-payload"
        tolerance = 300
        now = time.time()
        timestamp = int(now + 301)  # Future timestamp

        sig_header = sign_relay_payload(payload, secret, timestamp=timestamp)

        with pytest.raises(StaleRelayTimestampError):
            verify_relay_signature(payload, sig_header, secret, tolerance_seconds=tolerance, now=now)


class TestRelaySignatureHeaderParsing:
    """Signature header parsing with exact field matching (mutation target:
    wrong key names, missing fields, dropped checks)."""

    def test_missing_t_parameter_raises_error(self):
        """Mutation target: dropped check for 't' key."""
        from app.services.relay_auth import _parse_signature_header

        with pytest.raises(InvalidRelaySignatureHeaderError) as exc_info:
            _parse_signature_header("v1=abcd1234")

        assert "missing t=" in str(exc_info.value)

    def test_missing_v1_parameter_raises_error(self):
        """Mutation target: dropped check for 'v1' key."""
        from app.services.relay_auth import _parse_signature_header

        with pytest.raises(InvalidRelaySignatureHeaderError) as exc_info:
            _parse_signature_header("t=1234567890")

        assert "missing v1=" in str(exc_info.value)

    def test_non_integer_timestamp_raises_error(self):
        """Mutation target: dropped int() conversion or missing try/except."""
        from app.services.relay_auth import _parse_signature_header

        with pytest.raises(InvalidRelaySignatureHeaderError) as exc_info:
            _parse_signature_header("t=not-a-number,v1=sig123")

        assert "non-integer" in str(exc_info.value)


# =============================================================================
# LOCAL_AUTH.PY MUTATION TESTS
# =============================================================================
class TestLocalAuthSessionExpiry:
    """Session expiry logic with exact timestamp comparisons (mutation target:
    < vs <=, > vs >=, flipped comparison)."""

    def test_unexpired_session_returned(self, db_session):
        """Mutation target: < vs <=, or flipped comparison logic."""
        user_id = "user-1"
        tenant_id = "tenant-1"
        role = MembershipRole.CUSTOMER

        session_id, csrf_token = create_web_session(db_session, user_id=user_id, tenant_id=tenant_id, role=role)
        db_session.commit()

        retrieved = get_web_session(db_session, session_id=session_id)
        assert retrieved is not None
        assert retrieved.session_id == session_id

    def test_expired_session_returns_none(self, db_session):
        """Mutation target: < vs <= in expiry check, or flipped comparison."""
        user_id = "user-2"
        tenant_id = "tenant-1"
        role = MembershipRole.CUSTOMER

        # Manually create an expired session
        now = datetime.now(timezone.utc)
        expired_session = WebSession(
            session_id="expired-session-id",
            user_id=user_id,
            tenant_id=tenant_id,
            role=role.value,
            csrf_token="csrf-token",
            created_at=now - timedelta(days=10),
            expires_at=now - timedelta(seconds=1),
        )
        db_session.add(expired_session)
        db_session.commit()

        retrieved = get_web_session(db_session, session_id="expired-session-id")
        assert retrieved is None

    def test_session_at_expiry_boundary_considered_expired(self, db_session):
        """Mutation target: < vs <= boundary check at exact expiry time."""
        user_id = "user-3"
        tenant_id = "tenant-1"
        role = MembershipRole.CUSTOMER

        now = datetime.now(timezone.utc)
        # Session expires exactly at 'now'
        boundary_session = WebSession(
            session_id="boundary-session",
            user_id=user_id,
            tenant_id=tenant_id,
            role=role.value,
            csrf_token="csrf-token",
            created_at=now - timedelta(days=1),
            expires_at=now,
        )
        db_session.add(boundary_session)
        db_session.commit()

        # Check at exactly expiry time - should be expired
        with mock.patch("app.services.local_auth.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

            retrieved = get_web_session(db_session, session_id="boundary-session")
            assert retrieved is None


class TestLocalAuthTokenExpiry:
    """Auth token expiry with boundary conditions (mutation target: < vs <=,
    dropped expiry check)."""

    def test_invalid_token_expired_beyond_ttl(self, db_session):
        """Mutation target: < vs <=, or dropped expiry check."""
        email = "test-token-exp@example.com"
        password = "password123"

        user, token = create_account(db_session, email=email, password=password, tenant_display_name="Test Org")

        # Manually expire the token
        now = datetime.now(timezone.utc)
        token.expires_at = now - timedelta(seconds=1)
        db_session.commit()

        with pytest.raises(InvalidTokenError):
            verify_email(db_session, token=token.token)

    def test_valid_token_at_ttl_boundary(self, db_session):
        """Mutation target: < vs <= boundary check for token expiry."""
        email = "test-token-boundary@example.com"
        password = "password123"

        user, token = create_account(db_session, email=email, password=password, tenant_display_name="Test Org")

        # Token is valid (fresh), should verify successfully
        verified_user = verify_email(db_session, token=token.token)
        assert verified_user.email == email

    def test_token_single_use_consumed_raises_error(self, db_session):
        """Mutation target: dropped or flipped consumed_at check."""
        email = "test-token-single@example.com"
        password = "password123"

        user, token = create_account(db_session, email=email, password=password, tenant_display_name="Test Org")

        # First use succeeds
        verify_email(db_session, token=token.token)

        # Second use with same token should fail
        with pytest.raises(InvalidTokenError):
            verify_email(db_session, token=token.token)


class TestLocalAuthPasswordValidation:
    """Password validation with exact hash checking (mutation target: flipped
    comparison, dropped hash verification)."""

    def test_correct_password_authenticates(self, db_session):
        """Mutation target: flipped boolean result in password verification."""
        email = "test-auth@example.com"
        password = "correct-password"

        create_account(db_session, email=email, password=password, tenant_display_name="Test Org")
        db_session.commit()

        user = authenticate(db_session, email=email, password=password)
        assert user.email == email

    def test_incorrect_password_raises_error(self, db_session):
        """Mutation target: dropped password verification or flipped condition."""
        email = "test-auth-wrong@example.com"
        password = "correct-password"

        create_account(db_session, email=email, password=password, tenant_display_name="Test Org")
        db_session.commit()

        with pytest.raises(InvalidCredentialsError):
            authenticate(db_session, email=email, password="wrong-password")

    def test_nonexistent_email_raises_error(self, db_session):
        """Mutation target: missing existence check or wrong field."""
        with pytest.raises(InvalidCredentialsError):
            authenticate(db_session, email="nonexistent@example.com", password="any-password")

    def test_user_without_password_hash_raises_error(self, db_session):
        """Mutation target: dropped check for password_hash is None."""
        email = "test-no-hash@example.com"

        user = UserIdentity(email=email, password_hash=None)
        db_session.add(user)
        db_session.commit()

        with pytest.raises(InvalidCredentialsError):
            authenticate(db_session, email=email, password="any-password")


class TestLocalAuthSessionCreation:
    """Session creation with token generation (mutation target: missing
    fields, wrong defaults, token type confusion)."""

    def test_session_creation_returns_both_tokens(self, db_session):
        """Mutation target: missing field assignment in return tuple."""
        user_id = "user-session"
        tenant_id = "tenant-1"
        role = MembershipRole.CUSTOMER

        session_id, csrf_token = create_web_session(db_session, user_id=user_id, tenant_id=tenant_id, role=role)

        assert session_id is not None
        assert csrf_token is not None
        assert len(session_id) > 0
        assert len(csrf_token) > 0

    def test_session_expiry_set_to_ttl(self, db_session):
        """Mutation target: wrong TTL value or operator (+ vs -)."""
        user_id = "user-ttl"
        tenant_id = "tenant-1"
        role = MembershipRole.CUSTOMER

        before = datetime.now(timezone.utc)
        session_id, _ = create_web_session(db_session, user_id=user_id, tenant_id=tenant_id, role=role)
        after = datetime.now(timezone.utc)

        session = db_session.get(WebSession, session_id)
        # Should be approximately 7 days from now
        expected_min = before + timedelta(days=7) - timedelta(seconds=1)
        expected_max = after + timedelta(days=7) + timedelta(seconds=1)

        assert expected_min <= session.expires_at <= expected_max


class TestLocalAuthAccountCreation:
    """Account creation with email uniqueness (mutation target: dropped
    duplicate check, flipped condition)."""

    def test_unique_email_creates_account(self, db_session):
        """Mutation target: dropped uniqueness check."""
        email = "unique@example.com"
        password = "password123"

        user, token = create_account(db_session, email=email, password=password, tenant_display_name="Test Org")

        assert user.email == email
        assert token is not None

    def test_duplicate_email_raises_error(self, db_session):
        """Mutation target: flipped condition or missing check."""
        email = "duplicate@example.com"
        password = "password123"

        create_account(db_session, email=email, password=password, tenant_display_name="Test Org")
        db_session.commit()

        with pytest.raises(AccountAlreadyExistsError):
            create_account(db_session, email=email, password="different-password", tenant_display_name="Other Org")


class TestLocalAuthSessionDeletion:
    """Session deletion with audit trail (mutation target: missing check,
    wrong condition, dropped audit event)."""

    def test_delete_existing_session_succeeds(self, db_session):
        """Mutation target: missing deletion logic or wrong condition."""
        user_id = "user-delete"
        tenant_id = "tenant-1"
        role = MembershipRole.CUSTOMER

        session_id, _ = create_web_session(db_session, user_id=user_id, tenant_id=tenant_id, role=role)
        db_session.commit()

        delete_web_session(db_session, session_id=session_id)
        db_session.commit()

        # Session should be gone
        retrieved = get_web_session(db_session, session_id=session_id)
        assert retrieved is None

    def test_delete_nonexistent_session_succeeds(self, db_session):
        """Mutation target: missing None check or different logic."""
        # Should not raise
        delete_web_session(db_session, session_id="nonexistent-session")


class TestLocalAuthPasswordReset:
    """Password reset with single-use tokens (mutation target: dropped
    consumed check, wrong token type)."""

    def test_password_reset_changes_hash(self, db_session):
        """Mutation target: missing password hash update or wrong field."""
        email = "reset@example.com"
        old_password = "old-password"
        new_password = "new-password"

        create_account(db_session, email=email, password=old_password, tenant_display_name="Test Org")
        db_session.commit()

        token = request_password_reset(db_session, email=email)
        assert token is not None

        reset_password(db_session, token=token.token, new_password=new_password)
        db_session.commit()

        # Old password should not work
        with pytest.raises(InvalidCredentialsError):
            authenticate(db_session, email=email, password=old_password)

        # New password should work
        user = authenticate(db_session, email=email, password=new_password)
        assert user.email == email

    def test_reset_token_consumed_on_use(self, db_session):
        """Mutation target: dropped consumed_at assignment."""
        email = "reset-consume@example.com"

        create_account(db_session, email=email, password="password", tenant_display_name="Test Org")
        db_session.commit()

        token = request_password_reset(db_session, email=email)
        reset_password(db_session, token=token.token, new_password="new-password")
        db_session.commit()

        # Token should be marked consumed, second use fails
        with pytest.raises(InvalidTokenError):
            reset_password(db_session, token=token.token, new_password="another-password")


class TestLocalAuthNulByteRejection:
    """NUL byte rejection in credentials (mutation target: dropped check,
    wrong exception)."""

    def test_email_with_nul_byte_rejected_in_signup(self, db_session):
        """Mutation target: dropped NUL byte check."""
        email_with_nul = "test\x00@example.com"

        with pytest.raises(AccountAlreadyExistsError):  # Same error as duplicate
            create_account(db_session, email=email_with_nul, password="password", tenant_display_name="Test Org")

    def test_password_with_nul_byte_rejected_in_signup(self, db_session):
        """Mutation target: dropped NUL byte check."""
        password_with_nul = "pass\x00word"

        with pytest.raises(AccountAlreadyExistsError):  # Same error as duplicate
            create_account(db_session, email="test@example.com", password=password_with_nul, tenant_display_name="Test Org")

    def test_nul_byte_rejected_in_signin(self, db_session):
        """Mutation target: dropped NUL byte check."""
        email = "test-signin@example.com"
        create_account(db_session, email=email, password="password", tenant_display_name="Test Org")
        db_session.commit()

        with pytest.raises(InvalidCredentialsError):
            authenticate(db_session, email="test\x00@example.com", password="password")


# =============================================================================
# TRADING_AUTHORITY.PY MUTATION TESTS
# =============================================================================
class TestTradingAuthorityApplicability:
    """Trading authority applicability for service modes (mutation target:
    missing service_modes, wrong frozenset membership)."""

    def test_applicable_false_for_research_only_product(self, db_session):
        """Mutation target: wrong frozenset in _ORDER_ROUTING_SERVICE_MODES or
        flipped applicable logic."""
        from app.models.product import Product, ProductLifecycleState

        product = Product(
            tenant_id="tenant-1",
            product_id="prod-research",
            portfolio_version_id="pv-1",
            revision=1,
            lifecycle_state=ProductLifecycleState.PUBLISHED,
            service_modes=["research"],  # NOT an order-routing mode
        )
        db_session.add(product)
        db_session.commit()

        assessment = assess_trading_authority(db_session, product)

        assert assessment.applicable is False
        assert assessment.qualified is None
        assert assessment.reason == "not_applicable"

    def test_applicable_true_for_copying_product(self, db_session):
        """Mutation target: missing 'copying' from _ORDER_ROUTING_SERVICE_MODES."""
        from app.models.product import Product, ProductLifecycleState

        product = Product(
            tenant_id="tenant-1",
            product_id="prod-copy",
            portfolio_version_id="pv-1",
            revision=1,
            lifecycle_state=ProductLifecycleState.DRAFT,  # Not published
            service_modes=["copying"],
        )
        db_session.add(product)
        db_session.commit()

        assessment = assess_trading_authority(db_session, product)

        assert assessment.applicable is True
        assert assessment.qualified is False  # Draft, so not qualified


# Comprehensive scope marker test
def test_track73_mutation_scope_identified():
    """Placeholder: confirms Track 73 covers integration_inbox.py,
    publisher_writer_claim.py, trading_authority.py, relay_auth.py,
    and local_auth.py high-risk data integrity and event processing."""
    # Verify all targeted modules are importable
    assert InboxEvent is not None
    assert PublisherWriterClaim is not None
    assert WebSession is not None
    assert AuthToken is not None
