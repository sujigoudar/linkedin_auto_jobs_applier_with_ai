"""app/services/relay_auth.py's own signature verification -- no DB
involved, mirrors tests/test_api.py's own coverage of
app/services/stripe_webhook.py's identical-shaped scheme."""
import pytest

from app.services.relay_auth import (
    InvalidRelaySignatureHeaderError,
    RelaySignatureMismatchError,
    StaleRelayTimestampError,
    sign_relay_payload,
    verify_relay_signature,
)

_SECRET = "test-relay-secret"


def test_a_correctly_signed_payload_verifies():
    payload = b'{"events": ["a"]}'
    header = sign_relay_payload(payload, _SECRET, timestamp=1_700_000_000)
    verify_relay_signature(payload, header, _SECRET, now=1_700_000_000)


def test_a_tampered_payload_is_rejected():
    payload = b'{"events": ["a"]}'
    header = sign_relay_payload(payload, _SECRET, timestamp=1_700_000_000)
    with pytest.raises(RelaySignatureMismatchError):
        verify_relay_signature(b'{"events": ["b"]}', header, _SECRET, now=1_700_000_000)


def test_the_wrong_secret_is_rejected():
    payload = b'{"events": ["a"]}'
    header = sign_relay_payload(payload, "a-different-secret", timestamp=1_700_000_000)
    with pytest.raises(RelaySignatureMismatchError):
        verify_relay_signature(payload, header, _SECRET, now=1_700_000_000)


def test_a_malformed_header_is_rejected():
    with pytest.raises(InvalidRelaySignatureHeaderError):
        verify_relay_signature(b"x", "not-a-real-header", _SECRET)


def test_a_stale_timestamp_outside_the_tolerance_window_is_rejected():
    payload = b'{"events": ["a"]}'
    header = sign_relay_payload(payload, _SECRET, timestamp=1_700_000_000)
    with pytest.raises(StaleRelayTimestampError):
        verify_relay_signature(payload, header, _SECRET, now=1_700_000_000 + 301)


def test_a_timestamp_within_the_tolerance_window_is_accepted():
    payload = b'{"events": ["a"]}'
    header = sign_relay_payload(payload, _SECRET, timestamp=1_700_000_000)
    verify_relay_signature(payload, header, _SECRET, now=1_700_000_000 + 299)
