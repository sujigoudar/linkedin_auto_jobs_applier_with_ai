"""app/services/catalog_fit_sim_auth.py -- the signed, audience-bound
service token `POST /catalog/providers/{source}/fit-simulation` requires
instead of an owner cookie session. See that module's own docstring for
the exact scheme and why it is a SEPARATE secret/verifier from the relay
ingest's own `sign_relay_payload`/`verify_relay_signature`."""
import pytest

from app.relay_worker import sign_relay_payload
from app.services.catalog_fit_sim_auth import (
    CatalogFitSimSignatureMismatchError,
    InvalidCatalogFitSimSignatureHeaderError,
    StaleCatalogFitSimTimestampError,
    sign_catalog_fit_sim_request,
    verify_catalog_fit_sim_signature,
)

SECRET = "test-catalog-fit-sim-secret"
PAYLOAD = b'{"source": "alerts_guy", "account_size": 25000, "max_per_trade": 2500, "csv_paths": {}}'


def test_a_correctly_signed_request_verifies():
    header = sign_catalog_fit_sim_request(PAYLOAD, SECRET, timestamp=1_700_000_000)
    verify_catalog_fit_sim_signature(PAYLOAD, header, SECRET, now=1_700_000_000)  # must not raise


def test_a_request_signed_with_the_wrong_secret_is_rejected():
    header = sign_catalog_fit_sim_request(PAYLOAD, "some-other-secret", timestamp=1_700_000_000)
    with pytest.raises(CatalogFitSimSignatureMismatchError):
        verify_catalog_fit_sim_signature(PAYLOAD, header, SECRET, now=1_700_000_000)


def test_an_expired_timestamp_is_rejected():
    header = sign_catalog_fit_sim_request(PAYLOAD, SECRET, timestamp=1_700_000_000)
    # 61 seconds later, past the default 60s tolerance window.
    with pytest.raises(StaleCatalogFitSimTimestampError):
        verify_catalog_fit_sim_signature(PAYLOAD, header, SECRET, now=1_700_000_061)


def test_a_timestamp_just_inside_the_tolerance_window_is_accepted():
    header = sign_catalog_fit_sim_request(PAYLOAD, SECRET, timestamp=1_700_000_000)
    verify_catalog_fit_sim_signature(PAYLOAD, header, SECRET, now=1_700_000_059)  # must not raise


def test_a_tampered_body_is_rejected():
    header = sign_catalog_fit_sim_request(PAYLOAD, SECRET, timestamp=1_700_000_000)
    tampered = PAYLOAD.replace(b"25000", b"999999999")
    with pytest.raises(CatalogFitSimSignatureMismatchError):
        verify_catalog_fit_sim_signature(tampered, header, SECRET, now=1_700_000_000)


def test_a_malformed_header_is_rejected():
    with pytest.raises(InvalidCatalogFitSimSignatureHeaderError):
        verify_catalog_fit_sim_signature(PAYLOAD, "not-a-real-header", SECRET)


def test_a_header_missing_the_signature_part_is_rejected():
    with pytest.raises(InvalidCatalogFitSimSignatureHeaderError):
        verify_catalog_fit_sim_signature(PAYLOAD, "t=1700000000", SECRET)


def test_a_request_signed_with_only_the_previous_secret_is_accepted_while_previous_is_configured():
    header = sign_catalog_fit_sim_request(PAYLOAD, "old-secret", timestamp=1_700_000_000)
    # CURRENT is the new secret; PREVIOUS is the old one the request was
    # actually signed with -- must still verify during the overlap window.
    verify_catalog_fit_sim_signature(
        PAYLOAD, header, "new-secret", secret_previous="old-secret", now=1_700_000_000
    )


def test_a_request_signed_with_the_previous_secret_is_rejected_once_previous_is_unset():
    header = sign_catalog_fit_sim_request(PAYLOAD, "old-secret", timestamp=1_700_000_000)
    # Same request as above, but the operator has finished rotating and
    # unset PREVIOUS (secret_previous=None, the default) -- must now fail.
    with pytest.raises(CatalogFitSimSignatureMismatchError):
        verify_catalog_fit_sim_signature(PAYLOAD, header, "new-secret", now=1_700_000_000)


def test_a_request_signed_with_neither_current_nor_previous_secret_is_rejected():
    header = sign_catalog_fit_sim_request(PAYLOAD, "attacker-guessed-secret", timestamp=1_700_000_000)
    with pytest.raises(CatalogFitSimSignatureMismatchError):
        verify_catalog_fit_sim_signature(
            PAYLOAD, header, "new-secret", secret_previous="old-secret", now=1_700_000_000
        )


def test_fails_closed_when_current_and_previous_are_both_unset_rather_than_accepting_anything():
    """An attacker-guessed secret must never be accepted just because the
    receiver's own configured secrets happen to both be blank -- a blank
    CURRENT/PREVIOUS is a misconfiguration to fail closed on, not a
    wildcard that accepts every signature."""
    header = sign_catalog_fit_sim_request(PAYLOAD, "attacker-guessed-secret", timestamp=1_700_000_000)
    with pytest.raises(CatalogFitSimSignatureMismatchError):
        verify_catalog_fit_sim_signature(PAYLOAD, header, "", secret_previous="", now=1_700_000_000)
    with pytest.raises(CatalogFitSimSignatureMismatchError):
        verify_catalog_fit_sim_signature(PAYLOAD, header, "", secret_previous=None, now=1_700_000_000)


def test_current_is_tried_before_previous_and_a_current_match_is_accepted():
    header = sign_catalog_fit_sim_request(PAYLOAD, "new-secret", timestamp=1_700_000_000)
    verify_catalog_fit_sim_signature(
        PAYLOAD, header, "new-secret", secret_previous="old-secret", now=1_700_000_000
    )


def test_wrong_audience_a_relay_signature_over_the_same_secret_and_body_does_not_verify():
    """The relay scheme signs `f"{ts}.".encode() + payload` (no audience
    string baked in); this scheme signs `f"{ts}.{AUDIENCE}.".encode() +
    payload`. Even if an operator mistakenly reused the SAME secret value
    for both RELAY_SIGNING_SECRET and CATALOG_FIT_SIM_SIGNING_SECRET, a
    signature computed by the relay's own signer must NOT verify here --
    proving the audience string is actually part of what's signed, not
    just documentation."""
    relay_style_header = sign_relay_payload(PAYLOAD, SECRET, timestamp=1_700_000_000)
    with pytest.raises(CatalogFitSimSignatureMismatchError):
        verify_catalog_fit_sim_signature(PAYLOAD, relay_style_header, SECRET, now=1_700_000_000)
