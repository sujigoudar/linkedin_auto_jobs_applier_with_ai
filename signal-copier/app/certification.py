"""Track 17: provider CERTIFICATION checklist -- the staged, owner-gated
promotion workflow the user's own spec describes: "A provider should not
go directly from added to live." This module is the vocabulary/
validation layer for `certification_checks` (see `app/db.py`'s own
table comment for the persisted shape) -- same split this codebase
already uses everywhere else (`app/phone_escalation.py`'s
`CapabilityState`/`validate_state_transition` is this module's direct
template; `app/qualification.py`'s ladder is the sibling, PER-ROUTE gate
this module composes with, never replaces or bypasses -- see this
module's own "Composition with route qualification" section below).

## Scope: provider x source x asset_class x account_route

Per the user's own words ("Certification should be scoped by provider x
source x asset class x account/broker route, not just provider"), every
`certification_checks` row is scoped by all four of
`(provider_id, source_id, asset_class, account_route)` -- NOT provider
alone. `provider_id` references `providers.id`, `source_id` references
`sources.id` (Track 14), `asset_class` is one of
`app.models.AssetClass`'s own values, `account_route` is a
`DestinationAccount.account_id` string (the same "finest per-route
distinction this schema actually carries" `app/engine.py`'s
`_check_route_qualified` already uses for its own `route_key`).

## The 14 checks, and which are automated vs. attestation-only

The user's own certification screen, verbatim: Connection PASS,
Historical retrieval PASS, Parser PASS, Entry PASS, Exit PASS, Stop
update PASS, Target update PASS, Duplicate handling PASS, Cross-channel
correlation PASS, Disconnect recovery PASS, Stale alert handling PASS,
Rejected-entry/exit test PASS, Partial fill/exit test PASS, Paper
execution PASS, then LIVE ELIGIBLE.

Every one of the 14 is represented by `CheckName` below. Honesty
constraint (this module's hard rule, matching CLAUDE.md's "never
fabricate a capability/test that doesn't exist" convention): a check is
`AUTOMATED` here ONLY when this codebase already has a real, queryable
signal to compute it from -- `CHECK_KIND` records exactly which, and
`app/certification_evidence.py` is where each automated check is
actually computed, at READ time, never stored as a separately-settable
flag (see this module's own "LIVE_ELIGIBLE is derived" section). Every
other check is `ATTESTATION_ONLY`: it can be marked PASS/FAIL only by an
explicit owner action (`POST /provider-certification/checks/{check_id}/
record`, `Depends(require_owner)`) carrying a real `evidence` note
describing what was verified -- there is no automatic path to PASS for
these, by construction (see `validate_check_record` below, which
REQUIRES a non-empty evidence note and a `checked_by` for any manual
record).

    AUTOMATED (computed fresh at read time from real underlying data --
    see app/certification_evidence.py for exactly which table/column
    each one reads):
      - CONNECTION: `connections.connection_state`/`health_score`
        (Track 14) for the `connections` row this scope's `sources.id`
        points at.
      - HISTORICAL_RETRIEVAL: whether this source has ever produced a
        signal with `Signal.import_batch` set (Track 5's own "backfilled
        vs. live-received" distinguishing field -- see
        `app.models.Signal.import_batch`'s own docstring) for this
        provider.
      - PARSER: Track 15's parser-accuracy metrics IF that track has
        landed in this codebase (checked by capability probing, not
        assumed -- see `app.certification_evidence.parser_check_evidence`);
        otherwise this check is honestly reported as requiring manual
        owner attestation (a `NOT_RUN`/attestation-shaped evidence
        placeholder, never a fabricated accuracy number).
      - DUPLICATE_HANDLING / CROSS_CHANNEL_CORRELATION:
        `signal_correlation_evidence` (Track 12) -- whether this
        provider has any recorded `corroborating`/`conflicting` match at
        all (duplicate_handling) or specifically a CROSS-channel one, a
        different `channel_id` than the canonical signal's own
        (cross_channel_correlation) -- see
        `app/signal_correlation.py`'s own module docstring for what a
        "different channel_id" candidate means.
      - PAPER_EXECUTION: real `orders` rows for this provider/route
        whose `broker` names a `PaperBroker`-backed account and whose
        `status` is a genuine terminal fill outcome (never a bare count
        of PENDING/REJECTED rows treated as if they were fills).

    ATTESTATION_ONLY (no automated verification path exists in this
    codebase today -- an explicit owner action, never a default PASS):
      - ENTRY, EXIT, STOP_UPDATE, TARGET_UPDATE: this codebase's `orders`
        table records WHAT was submitted, not whether a human reviewed
        that the entry/exit/stop-update/target-update behavior was
        CORRECT for this specific provider's alert conventions -- that
        judgment call is exactly what an owner attestation is for.
      - DISCONNECT_RECOVERY, STALE_ALERT_HANDLING,
        REJECTED_ENTRY_EXIT_TEST, PARTIAL_FILL_EXIT_TEST: no scenario
        harness for any of these four exists anywhere in this codebase
        (no fault-injection/disconnect-simulation, no stale-clock
        fixture, no rejected-order or partial-fill test rig scoped to a
        specific provider) -- fabricating an automated check for a test
        that was never run would be exactly the "rubber-stamp" this
        track's own brief forbids.

## LIVE_ELIGIBLE is a DERIVED property, never a settable flag

`is_live_eligible` (below) computes LIVE_ELIGIBLE fresh, every time,
from the real current status of every APPLICABLE check for one scope --
"every applicable check for that scope is PASS", per the user's own
words. There is no `live_eligible` column anywhere in `app/db.py`'s
`certification_checks` schema and no setter for one: a caller can never
independently flip a scope to LIVE_ELIGIBLE without every underlying
check actually being PASS, so this can never drift out of sync with the
checks it's computed from (see `test_t17_certification.py::
test_live_eligible_cannot_be_set_independently_of_its_checks`).

## Composition with route qualification -- an ADDITIONAL gate, never a
## replacement

`is_live_eligible` answers a DIFFERENT question than
`app/qualification.py`'s ladder / `app/engine.py`'s
`_check_route_qualified`: that existing gate is "has a human signed off
that THIS EXECUTION ROUTE (adapter_type/route_key/asset_class/
product_type) is safe to send live capital through" -- infrastructure/
venue readiness, independent of which signal provider happens to be
feeding it. This module's LIVE_ELIGIBLE is "has THIS PROVIDER, through
THIS SOURCE, for THIS ASSET CLASS, on THIS ACCOUNT ROUTE, actually been
certified end-to-end" -- provider/content readiness, independent of
which broker/venue technicalities are involved. A route can be fully
`release_approved` while its provider is nowhere near LIVE_ELIGIBLE (a
brand-new provider added to an already-qualified broker route), and the
reverse holds too (a fully certified provider whose broker route has
never been qualified). BOTH must independently be true before this
codebase would honestly call a (provider, route) combination ready for
live capital -- this module does not read, call, or modify
`app/qualification.py`/`app/engine.py`'s gate in any way, and nothing
in `app/engine.py`'s actual routing decision reads
`certification_checks`/`is_live_eligible` (wiring provider-certification
into that live gate, the way `providers.execution_eligibility` itself is
still NOT wired per Track 14's own documented scoping, is real,
valuable follow-up work deliberately left undone here -- see this
module's own module-level note in the Track 17 report for why).
"""
from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Any


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class CertificationError(ValueError):
    """Raised by this module's own validation -- never a silent
    best-effort accept, same convention as every earlier registry's own
    error type in this codebase."""


class CheckStatus(str, enum.Enum):
    NOT_RUN = "NOT_RUN"
    PASS = "PASS"
    FAIL = "FAIL"
    SKIPPED = "SKIPPED"


class CheckName(str, enum.Enum):
    CONNECTION = "connection"
    HISTORICAL_RETRIEVAL = "historical_retrieval"
    PARSER = "parser"
    ENTRY = "entry"
    EXIT = "exit"
    STOP_UPDATE = "stop_update"
    TARGET_UPDATE = "target_update"
    DUPLICATE_HANDLING = "duplicate_handling"
    CROSS_CHANNEL_CORRELATION = "cross_channel_correlation"
    DISCONNECT_RECOVERY = "disconnect_recovery"
    STALE_ALERT_HANDLING = "stale_alert_handling"
    REJECTED_ENTRY_EXIT_TEST = "rejected_entry_exit_test"
    PARTIAL_FILL_EXIT_TEST = "partial_fill_exit_test"
    PAPER_EXECUTION = "paper_execution"


class CheckKind(str, enum.Enum):
    """Whether a `CheckName` is computed automatically from real,
    existing evidence at read time, or requires an explicit owner
    attestation. See this module's own docstring for exactly why each
    check is classified as it is."""

    AUTOMATED = "automated"
    ATTESTATION_ONLY = "attestation_only"


#: The full, ordered checklist -- also the "every applicable check"
#: `is_live_eligible` iterates. Every provider x source x asset_class x
#: account_route scope is expected to have (or be given, lazily, at
#: read time -- see `app/db.py`'s `SignalStore.
#: ensure_certification_checks`) exactly these 14 rows, never more/fewer,
#: so "every applicable check is PASS" has one unambiguous meaning.
ALL_CHECKS: tuple[CheckName, ...] = tuple(CheckName)

#: Single source of truth for automated-vs-attestation classification --
#: `app/certification_evidence.py` and this module's own tests both
#: import this rather than re-deriving it, so the two can never drift
#: apart.
CHECK_KIND: dict[CheckName, CheckKind] = {
    CheckName.CONNECTION: CheckKind.AUTOMATED,
    CheckName.HISTORICAL_RETRIEVAL: CheckKind.AUTOMATED,
    CheckName.PARSER: CheckKind.AUTOMATED,
    CheckName.ENTRY: CheckKind.ATTESTATION_ONLY,
    CheckName.EXIT: CheckKind.ATTESTATION_ONLY,
    CheckName.STOP_UPDATE: CheckKind.ATTESTATION_ONLY,
    CheckName.TARGET_UPDATE: CheckKind.ATTESTATION_ONLY,
    CheckName.DUPLICATE_HANDLING: CheckKind.AUTOMATED,
    CheckName.CROSS_CHANNEL_CORRELATION: CheckKind.AUTOMATED,
    CheckName.DISCONNECT_RECOVERY: CheckKind.ATTESTATION_ONLY,
    CheckName.STALE_ALERT_HANDLING: CheckKind.ATTESTATION_ONLY,
    CheckName.REJECTED_ENTRY_EXIT_TEST: CheckKind.ATTESTATION_ONLY,
    CheckName.PARTIAL_FILL_EXIT_TEST: CheckKind.ATTESTATION_ONLY,
    CheckName.PAPER_EXECUTION: CheckKind.AUTOMATED,
}

#: The scorecard's own category grouping -- the user's own words:
#: "Setup 100%, Connectivity 100%, Parser coverage 97%, Historical tests
#: 100%, Shadow tests 94%, Execution tests 100%." Mapped onto this
#: module's real check vocabulary (see app/certification_scorecard.py for
#: where this grouping is actually consumed) -- "Setup" covers the two
#: checks that represent the transport/connection itself being wired up
#: at all, "Shadow tests" covers whatever `shadow_mode_results` this
#: scope has actually accumulated (not a `CheckName` at all -- see that
#: module), and every other category is a direct 1:1 or small group of
#: `CheckName`s.
SCORECARD_CATEGORIES: dict[str, tuple[CheckName, ...]] = {
    "setup": (CheckName.CONNECTION,),
    "connectivity": (CheckName.CONNECTION, CheckName.DISCONNECT_RECOVERY, CheckName.STALE_ALERT_HANDLING),
    "parser_coverage": (CheckName.PARSER,),
    "historical_tests": (CheckName.HISTORICAL_RETRIEVAL,),
    "execution_tests": (
        CheckName.ENTRY,
        CheckName.EXIT,
        CheckName.STOP_UPDATE,
        CheckName.TARGET_UPDATE,
        CheckName.PAPER_EXECUTION,
        CheckName.REJECTED_ENTRY_EXIT_TEST,
        CheckName.PARTIAL_FILL_EXIT_TEST,
        CheckName.DUPLICATE_HANDLING,
        CheckName.CROSS_CHANNEL_CORRELATION,
    ),
}


def parse_check_name(value: str) -> CheckName:
    try:
        return CheckName(value)
    except ValueError as exc:
        raise CertificationError(
            f"{value!r} is not a recognized certification check (valid: {[c.value for c in CheckName]})"
        ) from exc


def parse_check_status(value: str) -> CheckStatus:
    try:
        return CheckStatus(value)
    except ValueError as exc:
        raise CertificationError(
            f"{value!r} is not a recognized check status (valid: {[s.value for s in CheckStatus]})"
        ) from exc


def validate_scope(*, provider_id: str, source_id: str, asset_class: str, account_route: str) -> None:
    """Every `certification_checks` row -- and every read/record call
    against it -- must name a concrete scope. `asset_class` and
    `account_route` are deliberately plain strings here (not re-imported
    enums) so this module has no import-time dependency on
    `app.models`/`app.routing` -- callers pass `AssetClass(...).value`/a
    real `DestinationAccount.account_id`, validated at THEIR layer
    (`app/db.py`'s own registration methods), matching this codebase's
    existing split between "this module owns the vocabulary this table's
    OWN columns use" and "a caller is responsible for the cross-table
    identifiers it passes in" (see e.g. `app.provider_catalog.
    validate_source_registration`, which likewise never re-validates
    that `provider_id` refers to a real row -- `SignalStore.
    register_source` does that at the DB layer instead)."""
    for field_name, value in (
        ("provider_id", provider_id),
        ("source_id", source_id),
        ("asset_class", asset_class),
        ("account_route", account_route),
    ):
        if not value or not str(value).strip():
            raise CertificationError(f"{field_name} is required to scope a certification check")


def validate_check_record(
    *,
    check_name: str,
    status: str,
    evidence: dict[str, Any] | None,
    checked_by: str | None,
) -> None:
    """Validates ONE manual record call (`POST /provider-certification/
    checks/{check_id}/record`) before anything is persisted.

    Hard rule (the user's own words: "never a bare boolean with no
    backing"): recording `PASS` or `FAIL` REQUIRES both a non-empty
    `evidence` payload (an operator's own description of what was
    verified -- never accepted as an empty `{}`) and a `checked_by`
    identity (this must be an explicit human/operator action, never
    auto-passed or anonymous). `NOT_RUN`/`SKIPPED` may be recorded with
    no evidence (there is nothing yet to attest to)."""
    name = parse_check_name(check_name)
    parsed_status = parse_check_status(status)
    if parsed_status in (CheckStatus.PASS, CheckStatus.FAIL):
        if not evidence or not isinstance(evidence, dict) or not evidence:
            raise CertificationError(
                f"recording {name.value}={parsed_status.value} requires a non-empty `evidence` payload -- "
                "a bare boolean with no backing is never accepted (see app/certification.py's own docstring)"
            )
        if not checked_by or not str(checked_by).strip():
            raise CertificationError(
                f"recording {name.value}={parsed_status.value} requires `checked_by` (an explicit owner/operator "
                "identity) -- this must be a real human action, never auto-passed or anonymous"
            )


def is_live_eligible(check_rows: list[dict]) -> tuple[bool, list[CheckName]]:
    """The derived LIVE_ELIGIBLE computation -- `True` only when EVERY
    one of `ALL_CHECKS` is present in `check_rows` with `status == PASS`.
    Returns `(eligible, missing)` -- `missing` names every check that is
    absent entirely or not yet PASS, so a caller (the scorecard, the
    `/live-eligible` route) can report exactly what's outstanding rather
    than a bare boolean. `check_rows` is the real, current
    `certification_checks` rows for one scope (as `app/db.py`'s
    `SignalStore.list_certification_checks` returns them) -- this
    function does no I/O of its own and never assumes a check exists
    just because `ALL_CHECKS` names it; an absent row is treated
    identically to one still at `NOT_RUN` (not PASS, so not eligible)."""
    status_by_name: dict[CheckName, CheckStatus] = {}
    for row in check_rows:
        try:
            name = parse_check_name(row["check_name"])
            status_by_name[name] = parse_check_status(row["status"])
        except CertificationError:
            continue
    missing = [name for name in ALL_CHECKS if status_by_name.get(name) != CheckStatus.PASS]
    return (len(missing) == 0, missing)
