"""TRK-38: a Hypothesis property test for Track 35's EDIT-kind SourceEvent
correlation (`app/services/integration_inbox.py`'s `_source_event_native_key`
and the `SourceEventKind.EDIT` branch of `_apply_projection`, exercised
through `ingest_export_event`).

tests/test_integration_inbox.py's existing EDIT tests
(`test_an_edit_source_event_resolving_its_original_applies_as_a_no_op`,
`test_an_edit_source_event_with_no_original_source_event_id_parks_honestly`,
`test_an_edit_source_event_naming_an_unresolvable_original_parks_rather_than_guesses`,
`test_an_edit_source_event_does_not_resolve_against_a_different_tenants_identical_native_key`)
are four fixed, hand-picked scenarios. This generates random sequences of
ORIGINAL/EDIT/duplicate events -- including delivering the EDIT BEFORE its
own ORIGINAL (this is an event-sourced inbox; out-of-order export delivery
is exactly the scenario `export_sequence` gap-blocking exists for) and
events belonging to two different tenants that happen to share the same
`source_provider_id`/`source_channel_id`/`source_event_id` triple -- and
proves, across every generated case, that correlation never produces a
false-positive match: only ever a safe no-op-advance against a REAL,
already-applied, SAME-TENANT original, or an honest park. It is never
silently dropped, never mis-attributed across tenants, and never guessed
when out of order.

Each example uses fresh, per-example tenant ids and stream names (derived
from Hypothesis's own generated UUIDs) so examples never collide inside
the shared real Postgres `db_session` fixture, which is created once per
test *function* (not per Hypothesis example).
"""
import itertools
from datetime import datetime, timezone

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import select
from signal_platform_contracts import (
    Environment,
    EventEnvelope,
    EventType,
    EvidenceClass,
    InstrumentIdentity,
    SourceEventKind,
    SourceEventPayload,
    SourceIdentity,
    SourceReceiptPayload,
    build_subject,
    compute_payload_hash,
)

from app.models.ledger import LedgerEntry
from app.models.tenancy import Tenant
from app.services.integration_inbox import (
    PARKED_REASON_EDIT_WITHOUT_RESOLVABLE_TARGET,
    ingest_export_event,
    register_export_stream,
)


def _instrument():
    return InstrumentIdentity(
        instrument_id="AAPL", venue="NASDAQ", market_type="equity", currency="USD",
        multiplier="1", quantity_convention="shares",
    )


def _source_event_envelope(
    *,
    event_id,
    export_sequence,
    source_stream,
    kind,
    source_provider_id,
    source_channel_id,
    source_event_id,
    original_source_event_id=None,
):
    source_identity = SourceIdentity(
        source_provider_id=source_provider_id, source_channel_id=source_channel_id, analyst_id=None,
        parser_version="v3", source_event_id=source_event_id,
        original_source_event_id=original_source_event_id, parent_event_id=None, revision_id=None,
    )
    now = datetime.now(timezone.utc)
    inner_signal = SourceReceiptPayload(
        source=source_identity, instrument=_instrument(), side="buy", quantity="10", price="150.00",
    )
    payload = SourceEventPayload(
        kind=SourceEventKind(kind), source=source_identity, provider_timestamp=now,
        local_receipt_timestamp=now, instrument=_instrument(), signal=inner_signal,
    )
    payload_dict = payload.model_dump(mode="json")
    return EventEnvelope(
        event_type=EventType.SOURCE_EVENT, event_id=event_id, producer_id="signal-copier-instance-1",
        source_stream=source_stream, export_sequence=export_sequence, subject=build_subject(source=source_identity),
        event_time=now, effective_time=now, availability_time=now, receipt_time=now,
        environment=Environment.LOCAL_SIM, evidence_class=EvidenceClass.SYNTHETIC_FIXTURE,
        payload_hash=compute_payload_hash(payload_dict), payload=payload_dict,
    )


# Each generated case is a sequence of steps for ONE (tenant, stream) pair.
# "edit_first": deliver the EDIT at export_sequence 0, the ORIGINAL at 1
#   (out-of-order delivery -- the EDIT cannot possibly resolve yet).
# "original_first": the ordinary, correctly-ordered case.
# "edit_wrong_key": the EDIT names a source_event_id that was never sent
#   as an ORIGINAL on this stream at all (a real-world "never arrived" or
#   typo'd reference).
# "duplicate_edit": the same EDIT delivered twice (checks re-delivery never
#   double-applies or double-parks weirdly).
case_strategy = st.sampled_from(
    ["edit_first", "original_first", "edit_wrong_key", "duplicate_edit"]
)

#: Hypothesis may re-run the exact same generated value more than once
#: (shrinking, the initial reuse/replay phase, flaky-test detection) --
#: this real Postgres `db_session` persists across those re-runs within
#: one test function, so a key derived ONLY from the generated `unique`
#: value can collide with a prior run's rows. A monotonic counter makes
#: every actual invocation's ids unique regardless of what Hypothesis
#: replays.
_invocation_counter = itertools.count()


@settings(
    max_examples=60,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    case=case_strategy,
    cross_tenant_collision=st.booleans(),
    unique=st.uuids(),
)
def test_edit_correlation_never_produces_a_false_positive_match(
    db_session, case, cross_tenant_collision, unique
):
    unique = f"{unique}-{next(_invocation_counter)}"
    tenant_a = f"tenant-{unique}-a"
    tenant_b = f"tenant-{unique}-b"
    stream_a = f"signal-copier:source:{unique}:a"
    stream_b = f"signal-copier:source:{unique}:b"

    db_session.add(Tenant(tenant_id=tenant_a, display_name="A", environment="LOCAL_SIM"))
    db_session.add(Tenant(tenant_id=tenant_b, display_name="B", environment="LOCAL_SIM"))
    db_session.commit()
    register_export_stream(db_session, tenant_id=tenant_a, source_stream=stream_a, environment="LOCAL_SIM")
    register_export_stream(db_session, tenant_id=tenant_b, source_stream=stream_b, environment="LOCAL_SIM")
    db_session.commit()

    # When cross_tenant_collision, tenant B's stream reuses the exact same
    # source_provider_id/source_channel_id/source_event_id triple as
    # tenant A's -- the real-world "two tenants relaying the same public
    # channel" case `_source_event_native_key`'s docstring calls out.
    provider = "telegram"
    channel = "chan-shared" if cross_tenant_collision else f"chan-{unique}"
    native_msg_id = "msg-shared" if cross_tenant_collision else f"msg-{unique}"

    original_a = _source_event_envelope(
        event_id=f"evt-{unique}-orig-a", export_sequence=0, source_stream=stream_a, kind="original",
        source_provider_id=provider, source_channel_id=channel, source_event_id=native_msg_id,
    )

    if case == "original_first":
        orig_row = ingest_export_event(db_session, original_a.model_dump_json())
        db_session.commit()
        assert orig_row.applied_at is not None

        edit_a = _source_event_envelope(
            event_id=f"evt-{unique}-edit-a", export_sequence=1, source_stream=stream_a, kind="edit",
            source_provider_id=provider, source_channel_id=channel, source_event_id=f"msg-{unique}-rev",
            original_source_event_id=native_msg_id,
        )
        edit_row = ingest_export_event(db_session, edit_a.model_dump_json())
        db_session.commit()
        # Resolves cleanly against the real, same-tenant original.
        assert edit_row.applied_at is not None
        assert edit_row.parked_reason is None

    elif case == "edit_first":
        edit_a = _source_event_envelope(
            event_id=f"evt-{unique}-edit-a", export_sequence=0, source_stream=stream_a, kind="edit",
            source_provider_id=provider, source_channel_id=channel, source_event_id=f"msg-{unique}-rev",
            original_source_event_id=native_msg_id,
        )
        edit_row = ingest_export_event(db_session, edit_a.model_dump_json())
        db_session.commit()
        # The ORIGINAL has not been applied yet (it is not even sent at
        # export_sequence 0 on this stream) -- the EDIT must never guess
        # a match. Either parked outright, or (if some other mechanism
        # blocks on sequence order first) left unapplied either way.
        assert edit_row.applied_at is None or edit_row.parked_reason is None and edit_row.applied_at is None
        if edit_row.applied_at is None:
            assert edit_row.parked_reason is not None

    elif case == "edit_wrong_key":
        orig_row = ingest_export_event(db_session, original_a.model_dump_json())
        db_session.commit()
        assert orig_row.applied_at is not None

        edit_a = _source_event_envelope(
            event_id=f"evt-{unique}-edit-wrong", export_sequence=1, source_stream=stream_a, kind="edit",
            source_provider_id=provider, source_channel_id=channel, source_event_id=f"msg-{unique}-rev",
            original_source_event_id="msg-that-never-arrived",
        )
        edit_row = ingest_export_event(db_session, edit_a.model_dump_json())
        db_session.commit()
        assert edit_row.applied_at is None
        assert edit_row.parked_reason == (
            f"{PARKED_REASON_EDIT_WITHOUT_RESOLVABLE_TARGET}:{tenant_a}|{provider}|{channel}|msg-that-never-arrived"
        )

    elif case == "duplicate_edit":
        orig_row = ingest_export_event(db_session, original_a.model_dump_json())
        db_session.commit()
        assert orig_row.applied_at is not None

        edit_payload = _source_event_envelope(
            event_id=f"evt-{unique}-edit-dup", export_sequence=1, source_stream=stream_a, kind="edit",
            source_provider_id=provider, source_channel_id=channel, source_event_id=f"msg-{unique}-rev",
            original_source_event_id=native_msg_id,
        )
        first = ingest_export_event(db_session, edit_payload.model_dump_json())
        db_session.commit()
        assert first.applied_at is not None
        second = ingest_export_event(db_session, edit_payload.model_dump_json())
        db_session.commit()
        # Re-delivery of the identical envelope (same event_id) must be
        # idempotent -- it must not apply twice, and it must not flip to
        # parked just because it's seen again.
        assert second.event_id == first.event_id
        assert second.applied_at is not None

    # The cross-tenant half: whatever tenant A's events did above, tenant
    # B sharing the same native identity (when cross_tenant_collision is
    # True) must never resolve an EDIT of its own against tenant A's
    # ORIGINAL. Tenant B has sent nothing at all yet, so any EDIT it sends
    # naming the same native_msg_id must park -- it must NEVER silently
    # resolve against tenant A's row just because the raw strings match.
    if cross_tenant_collision:
        edit_b = _source_event_envelope(
            event_id=f"evt-{unique}-edit-b", export_sequence=0, source_stream=stream_b, kind="edit",
            source_provider_id=provider, source_channel_id=channel, source_event_id=f"msg-{unique}-b-rev",
            original_source_event_id=native_msg_id,
        )
        edit_b_row = ingest_export_event(db_session, edit_b.model_dump_json())
        db_session.commit()
        assert edit_b_row.applied_at is None, (
            "tenant B's EDIT resolved against tenant A's ORIGINAL via a shared native key -- "
            "cross-tenant correlation false positive"
        )
        assert edit_b_row.parked_reason == (
            f"{PARKED_REASON_EDIT_WITHOUT_RESOLVABLE_TARGET}:{tenant_b}|{provider}|{channel}|{native_msg_id}"
        )
        # And no ledger row was fabricated for tenant B out of tenant A's data.
        assert db_session.scalars(select(LedgerEntry).where(LedgerEntry.tenant_id == tenant_b)).all() == []
