"""Signature verification for the ONE public-reachable-through-a-relay
route this service exposes: `POST /catalog/providers/{source}/fit-
simulation` (app/main.py). This is the bounded, service-to-service path
signal-portfolio-commercial's own backend uses to run the Alertsify-
style "what would copying this PUBLISHED provider have done to MY
account" simulator for an anonymous public-catalog visitor, described
in that endpoint's own docstring and in `/providers/{source}/fit-
simulation`'s (the owner-gated original) docstring.

## Why this is a SEPARATE module/secret from app/services -- wait, this
## service has no `relay_auth.py` of its own; the shared-scheme half
## lives in `app/relay_worker.py` (`sign_relay_payload`) --

`CATALOG_FIT_SIM_SIGNING_SECRET` (app/config.py) is a distinct secret
from `RELAY_SIGNING_SECRET`, and this is a distinct verifier from
anything relay-related, for the exact "a stolen credential must not
become a different credential" reason INTEGRATION_DECISION.md S11
already states for the relay secret vs the commercial billing-webhook
secret: a leaked catalog-fit-sim secret must never be usable to forge a
relay export-event batch (which, unlike this endpoint, feeds real
ledger entries), and a leaked relay secret must never be usable to call
this endpoint. Reusing one secret/verifier for both would silently
merge two trust boundaries that this project has consistently kept
separate everywhere else (see also app/auth.py's `RequireOwner`, a
third, completely independent trust boundary this module's token can
NEVER satisfy -- there is no code path anywhere that accepts a valid
catalog-fit-sim signature in place of an owner session/CSRF pair).

## The scheme: the same "t=<unix ts>,v1=<hex hmac>" shape as
## app/relay_worker.py's `sign_relay_payload`, but audience-bound

`signed_payload = f"{ts}.{_AUDIENCE}.".encode() + payload` -- the fixed
audience string `_AUDIENCE` is part of what gets signed, not just
documentation. This means a signature computed for ANY other purpose
with the SAME secret value (e.g. if an operator ever mistakenly reused
this secret for something else) still fails verification here, because
the exact bytes being signed differ. It is not full "one token, one
scoped capability" infrastructure (there's no key-rotation or per-
request audience list, matching this project's own already-documented
relay-auth limitation) -- it is the same audience-bound signed-service-
token shape INTEGRATION_DECISION.md S4 asks for, sized to the one
narrow use this needs.

## Honest limitations (same shape as app/services/relay_auth.py's own
## documented ones on the commercial side)

- Replay protection is timestamp-window only: a request captured and
  replayed WITHIN `tolerance_seconds` is not rejected by this module
  alone. Unlike the relay ingest (idempotent by event_id/payload_hash),
  a replayed fit-simulation call just recomputes the same bounded, read-
  only report again -- wasted work, not a data-integrity or financial
  risk, which is why this module's default tolerance is deliberately
  short (60s, not the relay's 300s) rather than needing a separate
  idempotency layer.
- No key rotation is implemented: a single static secret, a placeholder
  default that must be replaced (and kept in sync with signal-
  portfolio-commercial's own identically-named setting) before any real
  deployment.
"""
from __future__ import annotations

import hashlib
import hmac
import time

_AUDIENCE = "catalog-fit-sim-v1"
_DEFAULT_TOLERANCE_SECONDS = 60


class InvalidCatalogFitSimSignatureHeaderError(Exception):
    pass


class CatalogFitSimSignatureMismatchError(Exception):
    pass


class StaleCatalogFitSimTimestampError(Exception):
    pass


def _parse_signature_header(sig_header: str) -> tuple[int, str]:
    """Same "t=<unix ts>,v1=<hex hmac>" shape as
    app/relay_worker.py's own header -- fail closed on anything that
    doesn't contain both a parseable `t` and a `v1`."""
    parts: dict[str, str] = {}
    for item in sig_header.split(","):
        if "=" not in item:
            continue
        key, _, value = item.partition("=")
        parts[key.strip()] = value.strip()

    if "t" not in parts or "v1" not in parts:
        raise InvalidCatalogFitSimSignatureHeaderError(
            f"catalog fit-sim signature header missing t= or v1=: {sig_header!r}"
        )
    try:
        timestamp = int(parts["t"])
    except ValueError as exc:
        raise InvalidCatalogFitSimSignatureHeaderError(
            f"non-integer timestamp in catalog fit-sim signature header: {parts['t']!r}"
        ) from exc

    return timestamp, parts["v1"]


def sign_catalog_fit_sim_request(payload: bytes, secret: str, *, timestamp: int | None = None) -> str:
    """Builds the `X-Catalog-Fit-Sim-Signature` header value the caller
    (signal-portfolio-commercial's own backend) sends. Used by that
    service's own client code and by this module's own tests -- never by
    this service itself, which only ever verifies."""
    ts = timestamp if timestamp is not None else int(time.time())
    signed_payload = f"{ts}.{_AUDIENCE}.".encode() + payload
    signature = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={signature}"


def verify_catalog_fit_sim_signature(
    payload: bytes,
    sig_header: str,
    secret: str,
    *,
    tolerance_seconds: int = _DEFAULT_TOLERANCE_SECONDS,
    now: float | None = None,
) -> None:
    """Raises on any failure -- a malformed header, a signature that
    doesn't match (wrong secret, tampered body, or a signature computed
    for a different audience/scheme entirely), or a timestamp outside the
    replay-tolerance window. Returns None so a caller cannot accidentally
    ignore a False result and proceed anyway."""
    timestamp, provided_signature = _parse_signature_header(sig_header)

    signed_payload = f"{timestamp}.{_AUDIENCE}.".encode() + payload
    expected_signature = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_signature, provided_signature):
        raise CatalogFitSimSignatureMismatchError(
            "catalog fit-sim signature does not match the expected value for this payload"
        )

    current_time = now if now is not None else time.time()
    if abs(current_time - timestamp) > tolerance_seconds:
        raise StaleCatalogFitSimTimestampError(
            f"catalog fit-sim timestamp {timestamp} is outside the {tolerance_seconds}s replay-tolerance "
            f"window (current time {current_time})"
        )
