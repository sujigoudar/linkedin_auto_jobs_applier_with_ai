"""app/services/stripe_webhook.py -- signature verification against
synthetic, Stripe-shaped payloads (no real Stripe account/SDK involved),
and event-id idempotency recording (requires a real DB, per
app.models.webhook_event.ProcessedWebhookEvent)."""
import hashlib
import hmac
import time

import pytest

from app.services.stripe_webhook import (
    InvalidSignatureHeaderError,
    SignatureMismatchError,
    StaleTimestampError,
    record_event_if_new,
    verify_signature,
)

_SECRET = "whsec_test_secret"


def _sign(payload: bytes, secret: str = _SECRET, *, timestamp: int | None = None) -> str:
    ts = timestamp if timestamp is not None else int(time.time())
    signed_payload = f"{ts}.".encode() + payload
    signature = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={signature}"


def test_a_correctly_signed_payload_verifies():
    payload = b'{"id": "evt_1", "type": "customer.subscription.updated"}'
    header = _sign(payload)
    verify_signature(payload, header, _SECRET)  # does not raise


def test_a_tampered_payload_is_rejected():
    payload = b'{"id": "evt_1"}'
    header = _sign(payload)
    with pytest.raises(SignatureMismatchError):
        verify_signature(b'{"id": "evt_1_tampered"}', header, _SECRET)


def test_the_wrong_secret_is_rejected():
    payload = b'{"id": "evt_1"}'
    header = _sign(payload, secret="whsec_a_different_secret")
    with pytest.raises(SignatureMismatchError):
        verify_signature(payload, header, _SECRET)


def test_a_stale_timestamp_outside_tolerance_is_rejected():
    payload = b'{"id": "evt_1"}'
    old_timestamp = int(time.time()) - 10_000
    header = _sign(payload, timestamp=old_timestamp)
    with pytest.raises(StaleTimestampError):
        verify_signature(payload, header, _SECRET, tolerance_seconds=300)


def test_a_malformed_header_is_rejected():
    with pytest.raises(InvalidSignatureHeaderError):
        verify_signature(b"{}", "not-a-valid-header", _SECRET)


def test_a_header_missing_v1_is_rejected():
    with pytest.raises(InvalidSignatureHeaderError):
        verify_signature(b"{}", "t=12345", _SECRET)


def test_recording_a_new_event_id_returns_true(db_session):
    assert record_event_if_new(db_session, "evt_new_1") is True


def test_recording_the_same_event_id_twice_returns_false_the_second_time(db_session):
    assert record_event_if_new(db_session, "evt_dup_1") is True
    assert record_event_if_new(db_session, "evt_dup_1") is False
