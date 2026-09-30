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


# -- Key rotation (CURRENT + PREVIOUS overlap window) --------------------


def test_a_payload_signed_with_only_the_previous_secret_is_accepted_while_previous_is_configured():
    payload = b'{"events": ["a"]}'
    header = sign_relay_payload(payload, "old-secret", timestamp=1_700_000_000)
    # CURRENT is the new secret; PREVIOUS is the old one the payload was
    # actually signed with -- must still verify during the overlap window.
    verify_relay_signature(
        payload, header, "new-secret", secret_previous="old-secret", now=1_700_000_000
    )


def test_a_payload_signed_with_the_previous_secret_is_rejected_once_previous_is_unset():
    payload = b'{"events": ["a"]}'
    header = sign_relay_payload(payload, "old-secret", timestamp=1_700_000_000)
    # Same request as above, but the operator has finished rotating and
    # unset PREVIOUS (secret_previous=None, the default) -- must now fail.
    with pytest.raises(RelaySignatureMismatchError):
        verify_relay_signature(payload, header, "new-secret", now=1_700_000_000)


def test_a_payload_signed_with_neither_current_nor_previous_secret_is_rejected():
    payload = b'{"events": ["a"]}'
    header = sign_relay_payload(payload, "attacker-guessed-secret", timestamp=1_700_000_000)
    with pytest.raises(RelaySignatureMismatchError):
        verify_relay_signature(
            payload, header, "new-secret", secret_previous="old-secret", now=1_700_000_000
        )


def test_fails_closed_when_current_and_previous_are_both_unset_rather_than_accepting_anything():
    """An attacker-guessed secret must never be accepted just because the
    receiver's own configured secrets happen to both be blank -- a blank
    CURRENT/PREVIOUS is a misconfiguration to fail closed on, not a
    wildcard that accepts every signature."""
    payload = b'{"events": ["a"]}'
    header = sign_relay_payload(payload, "attacker-guessed-secret", timestamp=1_700_000_000)
    with pytest.raises(RelaySignatureMismatchError):
        verify_relay_signature(payload, header, "", secret_previous="", now=1_700_000_000)
    with pytest.raises(RelaySignatureMismatchError):
        verify_relay_signature(payload, header, "", secret_previous=None, now=1_700_000_000)


def test_current_is_tried_before_previous_and_a_current_match_is_accepted():
    payload = b'{"events": ["a"]}'
    header = sign_relay_payload(payload, "new-secret", timestamp=1_700_000_000)
    verify_relay_signature(
        payload, header, "new-secret", secret_previous="old-secret", now=1_700_000_000
    )
