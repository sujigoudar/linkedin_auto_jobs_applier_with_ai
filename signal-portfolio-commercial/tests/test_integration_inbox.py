"""Signal Platform Integration Correction Pack's own INTEGRATION_DECISION.md
S4.4/S6 -- app/services/integration_inbox.py's own tests. Real Postgres,
real tenant-scoped session."""
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
from signal_platform_contracts import (
    Environment,
    EventEnvelope,
    EventType,
    EvidenceClass,
    ExecutionAppliedPayload,
    FeePayload,
    InstrumentIdentity,
    PrivateAccountIdentity,
    RoutingAdmissionOutcomePayload,
    SourceEventKind,
    SourceEventPayload,
    SourceIdentity,
    SourceReceiptPayload,
    build_subject,
    compute_payload_hash,
)

from app.models.integration_inbox import InboxEvent
from app.models.ledger import Book, LedgerEntry
from app.models.sleeve import Sleeve
from app.models.tenancy import Tenant
from app.services.integration_inbox import (
    PARKED_REASON_EDIT_WITHOUT_RESOLVABLE_TARGET,
    PARKED_REASON_SOURCE_EVENT_KIND_NOT_LEDGER_REPRESENTABLE,
    EventIntegrityError,
    StreamAlreadyRegisteredToAnotherTenantError,
    UnregisteredStreamError,
    ingest_export_event,
    register_export_stream,
)
from app.services.analyst_attribution import compute_analyst_attribution
from app.services.platform_performance import compute_platform_performance


def _seed_tenant(db_session, tenant_id="tenant-a"):
    if db_session.get(Tenant, tenant_id) is None:
        db_session.add(Tenant(tenant_id=tenant_id, display_name="Tenant", environment="LOCAL_SIM"))
        db_session.commit()


def _instrument():
    return InstrumentIdentity(
        instrument_id="AAPL", venue="NASDAQ", market_type="equity", currency="USD",
        multiplier="1", quantity_convention="shares",
    )


def _execution_envelope(
    *,
    event_id="evt-1",
    export_sequence=0,
    source_stream="signal-copier:acct1",
    fee=None,
    filled_price="150.00",
    schema_version=None,
    side="buy",
    filled_quantity="10",
    broker_order_id="paper-1",
    originating_analyst_id=None,
):
    payload = ExecutionAppliedPayload(
        account=PrivateAccountIdentity(account_id="acct1"),
        instrument=_instrument(),
        side=side,
        filled_quantity=filled_quantity,
        filled_price=filled_price,
        fee=fee,
        broker="paper",
        broker_order_id=broker_order_id,
        originating_analyst_id=originating_analyst_id,
    )
    payload_dict = payload.model_dump(mode="json")
    now = datetime.now(timezone.utc)
    kwargs = dict(
        event_type=EventType.EXECUTION_APPLIED,
        event_id=event_id,
        producer_id="signal-copier-instance-1",
        source_stream=source_stream,
        export_sequence=export_sequence,
        subject=build_subject(account=PrivateAccountIdentity(account_id="acct1"), instrument=_instrument()),
        event_time=now,
        effective_time=now,
        availability_time=now,
        receipt_time=now,
        environment=Environment.LOCAL_SIM,
        evidence_class=EvidenceClass.INTERNAL_PAPER,
        payload_hash=compute_payload_hash(payload_dict),
        payload=payload_dict,
    )
    if schema_version is not None:
        kwargs["schema_version"] = schema_version
    return EventEnvelope(**kwargs)


def _fee_envelope(*, event_id, export_sequence, broker_order_id, fee, source_stream="signal-copier:acct1"):
    payload = FeePayload(
        account=PrivateAccountIdentity(account_id="acct1"),
        instrument=_instrument(),
        broker="paper",
        broker_order_id=broker_order_id,
        fee=fee,
    )
    payload_dict = payload.model_dump(mode="json")
    now = datetime.now(timezone.utc)
    return EventEnvelope(
        event_type=EventType.FEE,
        event_id=event_id,
        producer_id="signal-copier-instance-1",
        source_stream=source_stream,
        export_sequence=export_sequence,
        subject=build_subject(account=PrivateAccountIdentity(account_id="acct1"), instrument=_instrument()),
        event_time=now,
        effective_time=now,
        availability_time=now,
        receipt_time=now,
        environment=Environment.LOCAL_SIM,
        evidence_class=EvidenceClass.INTERNAL_PAPER,
        payload_hash=compute_payload_hash(payload_dict),
        payload=payload_dict,
    )


def _source_receipt_envelope(
    *,
    event_id="evt-src-1",
    export_sequence=0,
    source_stream="signal-copier:acct1",
    quantity="10",
    price="150.00",
    source_provider_id="telegram",
    analyst_id=None,
    parser_version="v3",
    source_event_id="src-evt-1",
    source_catalog_id=None,
):
    payload = SourceReceiptPayload(
        source=SourceIdentity(
            source_provider_id=source_provider_id, analyst_id=analyst_id, parser_version=parser_version,
            source_event_id=source_event_id, source_catalog_id=source_catalog_id,
        ),
        instrument=_instrument(),
        side="buy",
        quantity=quantity,
        price=price,
    )
    payload_dict = payload.model_dump(mode="json")
    now = datetime.now(timezone.utc)
    return EventEnvelope(
        event_type=EventType.SOURCE_RECEIPT,
        event_id=event_id,
        producer_id="signal-copier-instance-1",
        source_stream=source_stream,
        export_sequence=export_sequence,
        subject=build_subject(source=SourceIdentity(
            source_provider_id="telegram", parser_version="v3", source_event_id="src-evt-1",
        )),
        event_time=now,
        effective_time=now,
        availability_time=now,
        receipt_time=now,
        environment=Environment.LOCAL_SIM,
        evidence_class=EvidenceClass.SYNTHETIC_FIXTURE,
        payload_hash=compute_payload_hash(payload_dict),
        payload=payload_dict,
    )


def _routing_outcome_envelope(
    *,
    event_id="evt-outcome-1",
    export_sequence=1,
    source_stream="signal-copier:acct1",
    originating_source_event_id="evt-src-1",
    outcome="admitted_filled",
    account_id="acct1",
    broker="paper",
    order_status="filled",
    message=None,
):
    """Mirrors exactly what `signal-copier/app/export_events.py`'s own
    `build_routing_admission_outcome_envelope` produces -- built from the
    SAME shared `signal_platform_contracts` models, the same idiom every
    other envelope builder in this file already uses (`_execution_envelope`,
    `_fee_envelope`, `_source_receipt_envelope`) to exercise the real
    ingest path without crossing this repo's own application-code
    boundary into signal-copier's."""
    account = PrivateAccountIdentity(account_id=account_id) if account_id is not None else None
    payload = RoutingAdmissionOutcomePayload(
        originating_source_event_id=originating_source_event_id,
        outcome=outcome,
        account=account,
        broker=broker,
        order_status=order_status,
        message=message,
    )
    payload_dict = payload.model_dump(mode="json")
    now = datetime.now(timezone.utc)
    subject_clusters = {"source": SourceIdentity(
        source_provider_id="telegram", parser_version="v3", source_event_id="src-evt-1",
    )}
    if account is not None:
        subject_clusters["account"] = account
    return EventEnvelope(
        event_type=EventType.ROUTING_ADMISSION_OUTCOME,
        event_id=event_id,
        producer_id="signal-copier-instance-1",
        source_stream=source_stream,
        export_sequence=export_sequence,
        subject=build_subject(**subject_clusters),
        event_time=now,
        effective_time=now,
        availability_time=now,
        receipt_time=now,
        environment=Environment.LOCAL_SIM,
        evidence_class=EvidenceClass.SYNTHETIC_FIXTURE,
        payload_hash=compute_payload_hash(payload_dict),
        payload=payload_dict,
    )


def _source_event_envelope(
    *,
    event_id="evt-source-event-1",
    export_sequence=0,
    source_stream="signal-copier:acct1",
    kind="original",
    quantity="10",
    price="150.00",
    source_provider_id="telegram",
    source_channel_id=None,
    analyst_id=None,
    parser_version="v3",
    source_event_id="src-evt-1",
    original_source_event_id=None,
    parent_event_id=None,
    revision_id=None,
    has_signal=True,
):
    """Mirrors exactly what signal-copier's own `app/export_events.py`
    `build_source_event_envelope` produces -- same shared contracts
    models every other envelope builder in this file already uses."""
    source_identity = SourceIdentity(
        source_provider_id=source_provider_id, source_channel_id=source_channel_id, analyst_id=analyst_id,
        parser_version=parser_version, source_event_id=source_event_id,
        original_source_event_id=original_source_event_id, parent_event_id=parent_event_id,
        revision_id=revision_id,
    )
    inner_signal = None
    if has_signal and (quantity is not None or price is not None):
        inner_signal = SourceReceiptPayload(
            source=source_identity, instrument=_instrument(), side="buy", quantity=quantity, price=price,
        )
    now = datetime.now(timezone.utc)
    payload = SourceEventPayload(
        kind=SourceEventKind(kind),
        source=source_identity,
        provider_timestamp=now,
        local_receipt_timestamp=now,
        instrument=_instrument() if has_signal else None,
        signal=inner_signal,
    )
    payload_dict = payload.model_dump(mode="json")
    return EventEnvelope(
        event_type=EventType.SOURCE_EVENT,
        event_id=event_id,
        producer_id="signal-copier-instance-1",
        source_stream=source_stream,
        export_sequence=export_sequence,
        subject=build_subject(source=source_identity),
        event_time=now,
        effective_time=now,
        availability_time=now,
        receipt_time=now,
        environment=Environment.LOCAL_SIM,
        evidence_class=EvidenceClass.SYNTHETIC_FIXTURE,
        payload_hash=compute_payload_hash(payload_dict),
        payload=payload_dict,
    )


def test_register_export_stream_persists_the_registration(db_session):
    _seed_tenant(db_session)
    registration = register_export_stream(
        db_session, tenant_id="tenant-a", source_stream="signal-copier:acct1", environment="LOCAL_SIM",
    )
    db_session.commit()
    assert registration.tenant_id == "tenant-a"


def test_registering_the_same_stream_twice_for_the_same_tenant_is_idempotent(db_session):
    _seed_tenant(db_session)
    first = register_export_stream(
        db_session, tenant_id="tenant-a", source_stream="signal-copier:acct1", environment="LOCAL_SIM",
    )
    db_session.commit()
    second = register_export_stream(
        db_session, tenant_id="tenant-a", source_stream="signal-copier:acct1", environment="LOCAL_SIM",
    )
    assert first.registration_id == second.registration_id


def test_registering_the_same_stream_for_a_different_tenant_is_refused(db_session):
    _seed_tenant(db_session, "tenant-a")
    _seed_tenant(db_session, "tenant-b")
    register_export_stream(db_session, tenant_id="tenant-a", source_stream="signal-copier:acct1", environment="LOCAL_SIM")
    db_session.commit()

    with pytest.raises(StreamAlreadyRegisteredToAnotherTenantError):
        register_export_stream(db_session, tenant_id="tenant-b", source_stream="signal-copier:acct1", environment="LOCAL_SIM")


def test_ingest_execution_applied_event_is_refused_for_an_unregistered_stream(db_session):
    _seed_tenant(db_session)
    envelope = _execution_envelope()
    with pytest.raises(UnregisteredStreamError):
        ingest_export_event(db_session, envelope.model_dump_json())


def test_ingest_execution_applied_event_creates_a_real_platform_ledger_entry(db_session):
    _seed_tenant(db_session)
    register_export_stream(db_session, tenant_id="tenant-a", source_stream="signal-copier:acct1", environment="LOCAL_SIM")
    db_session.commit()

    envelope = _execution_envelope(fee="1.50")
    inbox_event = ingest_export_event(db_session, envelope.model_dump_json())
    db_session.commit()

    assert inbox_event.tenant_id == "tenant-a"
    assert inbox_event.applied_at is not None
    assert inbox_event.ledger_entry_id is not None

    entry = db_session.get(LedgerEntry, inbox_event.ledger_entry_id)
    assert entry is not None
    assert entry.tenant_id == "tenant-a"
    assert entry.book == Book.PLATFORM
    assert entry.instrument == "AAPL"
    assert entry.quantity == Decimal("10")
    assert entry.price == Decimal("150.00")
    assert entry.fee == Decimal("1.50")
    assert entry.evidence_class == EvidenceClass.INTERNAL_PAPER


def test_ingest_execution_applied_event_with_unknown_fee_leaves_ledger_fee_none(db_session):
    _seed_tenant(db_session)
    register_export_stream(db_session, tenant_id="tenant-a", source_stream="signal-copier:acct1", environment="LOCAL_SIM")
    db_session.commit()

    envelope = _execution_envelope()  # fee=None
    inbox_event = ingest_export_event(db_session, envelope.model_dump_json())
    db_session.commit()

    entry = db_session.get(LedgerEntry, inbox_event.ledger_entry_id)
    assert entry.fee is None


def test_ingest_source_receipt_event_with_known_quantity_and_price_creates_a_real_source_book_entry(db_session):
    """S12 step 5 "Portfolio Lab source feed": a SOURCE_RECEIPT with a
    real quantity/price produces a real Book.SOURCE ledger entry, never
    a Book.PLATFORM/execution one."""
    _seed_tenant(db_session)
    register_export_stream(db_session, tenant_id="tenant-a", source_stream="signal-copier:acct1", environment="LOCAL_SIM")
    db_session.commit()

    envelope = _source_receipt_envelope()
    inbox_event = ingest_export_event(db_session, envelope.model_dump_json())
    db_session.commit()

    assert inbox_event.applied_at is not None
    assert inbox_event.ledger_entry_id is not None
    entry = db_session.get(LedgerEntry, inbox_event.ledger_entry_id)
    assert entry is not None
    assert entry.book == Book.SOURCE
    assert entry.instrument == "AAPL"
    assert entry.quantity == Decimal("10")
    assert entry.price == Decimal("150.00")
    assert entry.sleeve_id is None  # no sleeve admitted for this (provider, analyst, parser_version) tuple


def test_ingest_source_receipt_event_with_unknown_quantity_or_price_creates_no_ledger_entry(db_session):
    """A bare "buy AAPL" recommendation with no quantity/price genuinely
    has nothing numeric to record -- applied (received, nothing
    further this event type could apply), but no ledger row, never a
    fabricated placeholder."""
    _seed_tenant(db_session)
    register_export_stream(db_session, tenant_id="tenant-a", source_stream="signal-copier:acct1", environment="LOCAL_SIM")
    db_session.commit()

    envelope = _source_receipt_envelope(quantity=None, price=None)
    inbox_event = ingest_export_event(db_session, envelope.model_dump_json())
    db_session.commit()

    assert inbox_event.ledger_entry_id is None
    assert inbox_event.applied_at is not None


def test_ingest_source_receipt_event_with_a_catalog_source_id_is_accepted_and_ignored(db_session):
    """Track 29: SourceIdentity.source_catalog_id is a new, additive,
    optional field on the SAME shared `signal_platform_contracts`
    package this repo imports -- a SOURCE_RECEIPT carrying a real value
    for it must still ingest and apply exactly as before. This consumer
    has no column for it yet (nothing real produces it in this
    deployment today); the assertion here is that an envelope carrying
    it does not raise, does not get parked, and produces the identical
    ledger entry a payload without it would."""
    _seed_tenant(db_session)
    register_export_stream(db_session, tenant_id="tenant-a", source_stream="signal-copier:acct1", environment="LOCAL_SIM")
    db_session.commit()

    envelope = _source_receipt_envelope(source_catalog_id="catalog-source-42")
    assert envelope.payload["source"]["source_catalog_id"] == "catalog-source-42"
    inbox_event = ingest_export_event(db_session, envelope.model_dump_json())
    db_session.commit()

    assert inbox_event.applied_at is not None
    entry = db_session.get(LedgerEntry, inbox_event.ledger_entry_id)
    assert entry is not None
    assert entry.book == Book.SOURCE
    assert entry.quantity == Decimal("10")


def test_a_routing_admission_outcome_attaches_the_real_outcome_to_its_receipt(db_session):
    """INT-027 "All permitted source outcomes reach research": a real,
    later, correlated ROUTING_ADMISSION_OUTCOME applies onto its
    originating SOURCE_RECEIPT's own InboxEvent.routing_outcome -- never
    a second, separate row a reader has to join by hand."""
    _seed_tenant(db_session)
    register_export_stream(db_session, tenant_id="tenant-a", source_stream="signal-copier:acct1", environment="LOCAL_SIM")
    db_session.commit()

    receipt = _source_receipt_envelope(event_id="evt-src-1", export_sequence=0)
    ingest_export_event(db_session, receipt.model_dump_json())
    db_session.commit()

    outcome = _routing_outcome_envelope(
        event_id="evt-outcome-1", export_sequence=1, originating_source_event_id="evt-src-1",
        outcome="admitted_filled",
    )
    outcome_row = ingest_export_event(db_session, outcome.model_dump_json())
    db_session.commit()

    assert outcome_row.applied_at is not None
    receipt_row = db_session.get(InboxEvent, "evt-src-1")
    assert receipt_row.routing_outcome == "admitted_filled"


def test_a_routing_admission_outcome_only_attaches_to_the_exact_receipt_it_names(db_session):
    """Two signals on the SAME stream (so a wrong correlation that just
    grabbed "some" SOURCE_RECEIPT on the stream, rather than the exact
    one named by `originating_source_event_id`, could look correct by
    accident) get two DIFFERENT real outcomes -- each must land on its
    own receipt only, never the other's."""
    _seed_tenant(db_session)
    register_export_stream(db_session, tenant_id="tenant-a", source_stream="signal-copier:acct1", environment="LOCAL_SIM")
    db_session.commit()

    receipt_1 = _source_receipt_envelope(event_id="evt-src-1", export_sequence=0)
    receipt_2 = _source_receipt_envelope(event_id="evt-src-2", export_sequence=1)
    ingest_export_event(db_session, receipt_1.model_dump_json())
    db_session.commit()
    ingest_export_event(db_session, receipt_2.model_dump_json())
    db_session.commit()

    outcome_for_2 = _routing_outcome_envelope(
        event_id="evt-outcome-2", export_sequence=2, originating_source_event_id="evt-src-2",
        outcome="rejected",
    )
    ingest_export_event(db_session, outcome_for_2.model_dump_json())
    db_session.commit()

    assert db_session.get(InboxEvent, "evt-src-2").routing_outcome == "rejected"
    # The OTHER receipt must stay untouched -- a correlation that matched
    # by something looser than the exact event_id (e.g. "the first
    # receipt on this stream") would wrongly leave this None too, or
    # wrongly set it.
    assert db_session.get(InboxEvent, "evt-src-1").routing_outcome is None


def test_a_routing_admission_outcome_for_an_unarrived_receipt_is_parked_not_dropped(db_session):
    """The defensive branch -- see app/services/integration_inbox.py's
    own ROUTING_ADMISSION_OUTCOME docstring for why this is normally
    unreachable in practice (both event types share one stream/sequence
    counter), but is still real and checked, not assumed."""
    _seed_tenant(db_session)
    register_export_stream(db_session, tenant_id="tenant-a", source_stream="signal-copier:acct1", environment="LOCAL_SIM")
    db_session.commit()

    outcome = _routing_outcome_envelope(
        event_id="evt-outcome-orphan", export_sequence=0, originating_source_event_id="evt-src-never-arrived",
    )
    outcome_row = ingest_export_event(db_session, outcome.model_dump_json())
    db_session.commit()

    assert outcome_row.applied_at is None
    assert outcome_row.parked_reason == "routing_outcome_target_not_found:evt-src-never-arrived"


def test_a_source_receipt_matching_an_admitted_sleeve_is_tagged_with_it(db_session):
    """INT-026-adjacent (sleeve<->source identity mapping, S12 step 5):
    an EXACT (provider, analyst, parser_version) match against a real
    admitted Sleeve tags the resulting Book.SOURCE entry with it."""
    _seed_tenant(db_session)
    register_export_stream(db_session, tenant_id="tenant-a", source_stream="signal-copier:acct1", environment="LOCAL_SIM")
    sleeve = Sleeve(
        tenant_id="tenant-a", provider="telegram", analyst="alice", strategy_horizon="swing",
        asset_class="equity", parser_version="v3", execution_policy_id="policy-1", cost_model_id="cost-1",
        capacity_policy_id="capacity-1", risk_unit_id="risk-1", history_origin="live",
    )
    db_session.add(sleeve)
    db_session.commit()

    envelope = _source_receipt_envelope(source_provider_id="telegram", analyst_id="alice", parser_version="v3")
    inbox_event = ingest_export_event(db_session, envelope.model_dump_json())
    db_session.commit()

    entry = db_session.get(LedgerEntry, inbox_event.ledger_entry_id)
    assert entry.sleeve_id == sleeve.sleeve_id
    assert entry.originating_analyst_id == "alice"


def test_a_source_receipt_is_never_matched_to_a_sleeve_with_a_different_analyst(db_session):
    """No "join only by provider": two sleeves share the SAME provider
    but different analysts -- a receipt for bob must never be tagged
    with alice's sleeve, even though it's the only other one for this
    provider."""
    _seed_tenant(db_session)
    register_export_stream(db_session, tenant_id="tenant-a", source_stream="signal-copier:acct1", environment="LOCAL_SIM")
    alice_sleeve = Sleeve(
        tenant_id="tenant-a", provider="telegram", analyst="alice", strategy_horizon="swing",
        asset_class="equity", parser_version="v3", execution_policy_id="policy-1", cost_model_id="cost-1",
        capacity_policy_id="capacity-1", risk_unit_id="risk-1", history_origin="live",
    )
    db_session.add(alice_sleeve)
    db_session.commit()

    envelope = _source_receipt_envelope(source_provider_id="telegram", analyst_id="bob", parser_version="v3")
    inbox_event = ingest_export_event(db_session, envelope.model_dump_json())
    db_session.commit()

    entry = db_session.get(LedgerEntry, inbox_event.ledger_entry_id)
    assert entry.sleeve_id is None  # never alice_sleeve.sleeve_id


def test_a_source_receipt_with_no_analyst_is_never_matched_to_any_sleeve(db_session):
    """A sleeve requires a real analyst (app/models/sleeve.py's own
    NOT NULL `analyst` column) -- an unattributed source receipt can
    never match one, by construction, never "the whole provider's"
    sleeve."""
    _seed_tenant(db_session)
    register_export_stream(db_session, tenant_id="tenant-a", source_stream="signal-copier:acct1", environment="LOCAL_SIM")
    sleeve = Sleeve(
        tenant_id="tenant-a", provider="telegram", analyst="alice", strategy_horizon="swing",
        asset_class="equity", parser_version="v3", execution_policy_id="policy-1", cost_model_id="cost-1",
        capacity_policy_id="capacity-1", risk_unit_id="risk-1", history_origin="live",
    )
    db_session.add(sleeve)
    db_session.commit()

    envelope = _source_receipt_envelope(source_provider_id="telegram", analyst_id=None, parser_version="v3")
    inbox_event = ingest_export_event(db_session, envelope.model_dump_json())
    db_session.commit()

    entry = db_session.get(LedgerEntry, inbox_event.ledger_entry_id)
    assert entry.sleeve_id is None


def test_reingesting_the_identical_event_is_a_harmless_no_op(db_session):
    _seed_tenant(db_session)
    register_export_stream(db_session, tenant_id="tenant-a", source_stream="signal-copier:acct1", environment="LOCAL_SIM")
    db_session.commit()

    envelope = _execution_envelope(event_id="evt-dup")
    first = ingest_export_event(db_session, envelope.model_dump_json())
    db_session.commit()
    second = ingest_export_event(db_session, envelope.model_dump_json())
    db_session.commit()

    assert first.event_id == second.event_id
    assert first.ledger_entry_id == second.ledger_entry_id
    ledger_entries = db_session.scalars(select(LedgerEntry).where(LedgerEntry.tenant_id == "tenant-a")).all()
    assert len(ledger_entries) == 1


def test_reingesting_the_same_event_id_with_a_different_payload_raises(db_session):
    """S6: "The same identity with a different hash is an integrity
    incident, not last-write-wins.\""""
    _seed_tenant(db_session)
    register_export_stream(db_session, tenant_id="tenant-a", source_stream="signal-copier:acct1", environment="LOCAL_SIM")
    db_session.commit()

    original = _execution_envelope(event_id="evt-tamper", filled_price="150.00")
    ingest_export_event(db_session, original.model_dump_json())
    db_session.commit()

    tampered = _execution_envelope(event_id="evt-tamper", filled_price="999.00")
    with pytest.raises(EventIntegrityError):
        ingest_export_event(db_session, tampered.model_dump_json())


def test_two_tenants_streams_never_leak_ledger_entries_across_tenants(db_session):
    _seed_tenant(db_session, "tenant-a")
    _seed_tenant(db_session, "tenant-b")
    register_export_stream(db_session, tenant_id="tenant-a", source_stream="signal-copier:acct-a", environment="LOCAL_SIM")
    register_export_stream(db_session, tenant_id="tenant-b", source_stream="signal-copier:acct-b", environment="LOCAL_SIM")
    db_session.commit()

    envelope_a = _execution_envelope(event_id="evt-a", source_stream="signal-copier:acct-a")
    ingest_export_event(db_session, envelope_a.model_dump_json())
    db_session.commit()

    tenant_b_entries = db_session.scalars(select(LedgerEntry).where(LedgerEntry.tenant_id == "tenant-b")).all()
    assert tenant_b_entries == []


def test_an_event_with_an_unsupported_schema_version_is_parked_not_coerced(db_session):
    """INTEGRATION_ACCEPTANCE_CASES.json INT-007 "Unsupported schema
    version": "Unknown event is parked without economic application...
    Schema incompatibility and affected cutoff are visible." Never
    "best-effort financial coercion" -- an envelope this build has
    never been verified against must not silently produce a ledger
    entry as if it were understood."""
    _seed_tenant(db_session)
    register_export_stream(db_session, tenant_id="tenant-a", source_stream="signal-copier:acct1", environment="LOCAL_SIM")
    db_session.commit()

    envelope = _execution_envelope(schema_version="99.0.0")
    inbox_event = ingest_export_event(db_session, envelope.model_dump_json())
    db_session.commit()

    assert inbox_event.applied_at is None
    assert inbox_event.ledger_entry_id is None
    assert inbox_event.parked_reason == "unsupported_schema_version:99.0.0"
    assert db_session.scalars(select(LedgerEntry).where(LedgerEntry.tenant_id == "tenant-a")).all() == []


def test_late_fee_corrections_revise_net_pnl_and_a_duplicate_never_double_counts(db_session):
    """INTEGRATION_ACCEPTANCE_CASES.json INT-012 "Late fee revises net
    report", exercised through the real relay/inbox/ledger-correction
    path -- not a hand-built ledger fixture. Buy10@100 and sell10@110
    (both fee=None) are ingested first: gross P&L is 100, net is
    unavailable (never coerced to 100). An entry-fill FEE (1.00) and an
    exit-fill FEE (1.00) are ingested next, each correlating to its own
    execution by (broker, broker_order_id) -- never by a shared
    event_id, which the two payload shapes don't have. Net becomes 98.
    Both FEE events are then retried (identical event_id and bytes):
    the existing redelivery-dedup at the inbox layer makes this a
    harmless no-op, so net must stay 98, never drop to 96."""
    _seed_tenant(db_session)
    register_export_stream(db_session, tenant_id="tenant-a", source_stream="signal-copier:acct1", environment="LOCAL_SIM")
    db_session.commit()

    entry_fill = _execution_envelope(
        event_id="evt-entry", export_sequence=0, side="buy", filled_price="100.00", broker_order_id="order-entry",
    )
    exit_fill = _execution_envelope(
        event_id="evt-exit", export_sequence=1, side="sell", filled_price="110.00", broker_order_id="order-exit",
    )
    ingest_export_event(db_session, entry_fill.model_dump_json())
    ingest_export_event(db_session, exit_fill.model_dump_json())
    db_session.commit()

    gross_only_report = compute_platform_performance(db_session, tenant_id="tenant-a")
    assert gross_only_report.realized_pnl == Decimal(100)
    assert gross_only_report.net_pnl is None
    assert gross_only_report.per_instrument["AAPL"].net_pnl is None
    assert gross_only_report.per_instrument["AAPL"].unknown_fee_entry_count == 2

    entry_fee = _fee_envelope(event_id="evt-entry-fee", export_sequence=2, broker_order_id="order-entry", fee="1.00")
    exit_fee = _fee_envelope(event_id="evt-exit-fee", export_sequence=3, broker_order_id="order-exit", fee="1.00")
    entry_fee_inbox_event = ingest_export_event(db_session, entry_fee.model_dump_json())
    exit_fee_inbox_event = ingest_export_event(db_session, exit_fee.model_dump_json())
    db_session.commit()

    assert entry_fee_inbox_event.applied_at is not None
    assert entry_fee_inbox_event.ledger_entry_id is not None
    assert exit_fee_inbox_event.applied_at is not None

    corrected_report = compute_platform_performance(db_session, tenant_id="tenant-a")
    assert corrected_report.realized_pnl == Decimal(100)  # gross is unaffected by a fee correction
    assert corrected_report.net_pnl == Decimal(98)
    assert corrected_report.per_instrument["AAPL"].net_pnl == Decimal(98)
    assert corrected_report.per_instrument["AAPL"].unknown_fee_entry_count == 0

    ledger_entries = db_session.scalars(select(LedgerEntry).where(LedgerEntry.tenant_id == "tenant-a")).all()
    assert len(ledger_entries) == 4  # 2 original fills + 2 fee corrections -- never replayed as 4 trades

    # Retry both fee observations (identical bytes) -- idempotent no-op.
    ingest_export_event(db_session, entry_fee.model_dump_json())
    ingest_export_event(db_session, exit_fee.model_dump_json())
    db_session.commit()

    retried_report = compute_platform_performance(db_session, tenant_id="tenant-a")
    assert retried_report.net_pnl == Decimal(98)  # never 96
    ledger_entries_after_retry = db_session.scalars(select(LedgerEntry).where(LedgerEntry.tenant_id == "tenant-a")).all()
    assert len(ledger_entries_after_retry) == 4  # no new rows from the retry


def test_interleaved_analyst_fills_ingested_through_the_relay_attribute_correctly(db_session):
    """INTEGRATION_ACCEPTANCE_CASES.json INT-026 "Analyst allocation
    survives shared symbol", exercised through the real relay/inbox
    path: alice and bob's fills for the SAME instrument/account,
    interleaved, ingested as real EXECUTION_APPLIED envelopes carrying
    originating_analyst_id. Alice's own first exit must realize against
    her own lot (FIFO, oldest first), never bob's, and never joined by
    symbol/account alone; once the whole position is closed, the sum
    of both analysts' own totals reconciles exactly to the aggregate
    platform_performance replay (a fully-closed position's total
    realized P&L is accounting-method-independent -- FIFO-lot and
    blended-average-cost necessarily agree once nothing is left open)."""
    _seed_tenant(db_session)
    register_export_stream(db_session, tenant_id="tenant-a", source_stream="signal-copier:acct1", environment="LOCAL_SIM")
    db_session.commit()

    alice_buy = _execution_envelope(
        event_id="evt-alice-buy", export_sequence=0, side="buy", filled_price="100.00",
        broker_order_id="order-alice-buy", originating_analyst_id="alice",
    )
    bob_buy = _execution_envelope(
        event_id="evt-bob-buy", export_sequence=1, side="buy", filled_price="121.00", filled_quantity="5",
        broker_order_id="order-bob-buy", originating_analyst_id="bob",
    )
    alice_sell = _execution_envelope(
        event_id="evt-alice-sell", export_sequence=2, side="sell", filled_price="130.00",
        broker_order_id="order-alice-sell", originating_analyst_id="alice",
    )
    bob_sell = _execution_envelope(
        event_id="evt-bob-sell", export_sequence=3, side="sell", filled_price="140.00", filled_quantity="5",
        broker_order_id="order-bob-sell", originating_analyst_id="bob",
    )
    for envelope in (alice_buy, bob_buy, alice_sell, bob_sell):
        ingest_export_event(db_session, envelope.model_dump_json())
    db_session.commit()

    report = compute_analyst_attribution(db_session, tenant_id="tenant-a")
    alice = report.per_instrument_analyst[("AAPL", "alice")]
    bob = report.per_instrument_analyst[("AAPL", "bob")]
    assert alice.realized_pnl == Decimal(300)  # (130-100) * 10 -- alice's own lot, closed first (FIFO)
    assert bob.realized_pnl == Decimal(95)  # (140-121) * 5 -- bob's own lot, closed by his own exit

    aggregate = compute_platform_performance(db_session, tenant_id="tenant-a")
    assert report.account_total_realized_pnl == aggregate.realized_pnl == Decimal(395)


def test_a_fee_for_an_execution_not_yet_applied_is_parked_not_discarded_or_misattributed(db_session):
    """A FEE event can genuinely arrive before its own execution has been
    applied (ordinary redelivery/backfill timing) -- it must be parked,
    honestly and visibly, never silently dropped and never attached to
    some other entry that happens to exist."""
    _seed_tenant(db_session)
    register_export_stream(db_session, tenant_id="tenant-a", source_stream="signal-copier:acct1", environment="LOCAL_SIM")
    db_session.commit()

    orphan_fee = _fee_envelope(event_id="evt-orphan-fee", export_sequence=0, broker_order_id="order-nonexistent", fee="1.00")
    inbox_event = ingest_export_event(db_session, orphan_fee.model_dump_json())
    db_session.commit()

    assert inbox_event.applied_at is None
    assert inbox_event.parked_reason == "fee_target_not_found:paper|order-nonexistent"
    assert db_session.scalars(select(LedgerEntry).where(LedgerEntry.tenant_id == "tenant-a")).all() == []


def test_a_schema_incompatible_event_permanently_parks_every_later_sequence_on_its_stream(db_session):
    """The same "keep separate received/applied cursors" consequence as
    an unimplemented event type: a schema-incompatible event never
    advances `_next_expected_sequence`, so a perfectly valid successor
    stays parked behind it until a real version-negotiation slice adds
    support -- never silently skipped ahead of the incompatible one."""
    _seed_tenant(db_session)
    register_export_stream(db_session, tenant_id="tenant-a", source_stream="signal-copier:acct1", environment="LOCAL_SIM")
    db_session.commit()

    incompatible = _execution_envelope(event_id="evt-0", export_sequence=0, schema_version="99.0.0")
    ingest_export_event(db_session, incompatible.model_dump_json())
    db_session.commit()

    follow_up = _execution_envelope(event_id="evt-1", export_sequence=1)
    follow_up_event = ingest_export_event(db_session, follow_up.model_dump_json())
    db_session.commit()

    assert follow_up_event.applied_at is None
    assert follow_up_event.parked_reason is None  # received, just waiting behind the gap -- not itself incompatible
    assert db_session.scalars(select(LedgerEntry).where(LedgerEntry.tenant_id == "tenant-a")).all() == []


def test_a_source_event_then_receipt_then_routing_outcome_all_apply_in_order(db_session):
    """Reproduces the exact live scenario that used to permanently park a
    stream at sequence 0: a real webhook-ingested signal exports a
    `SOURCE_EVENT` (kind=original) BEFORE its `SOURCE_RECEIPT` on the
    same `signal-copier:source:<name>` stream, followed by a
    `ROUTING_ADMISSION_OUTCOME` for that same receipt. Before this fix,
    the SOURCE_EVENT fell into the generic "unimplemented event type"
    branch and parked forever at sequence 0 -- which, because
    `_next_expected_sequence` is keyed strictly off `applied_at`,
    permanently blocked the SOURCE_RECEIPT and ROUTING_ADMISSION_OUTCOME
    that followed it on the SAME stream too. All three must now apply,
    in order."""
    _seed_tenant(db_session)
    register_export_stream(db_session, tenant_id="tenant-a", source_stream="signal-copier:source:telegram", environment="LOCAL_SIM")
    db_session.commit()

    source_event = _source_event_envelope(
        event_id="evt-source-event-1", export_sequence=0, source_stream="signal-copier:source:telegram",
        kind="original",
    )
    source_event_row = ingest_export_event(db_session, source_event.model_dump_json())
    db_session.commit()

    # Before the fix: parked at sequence 0 forever, blocking everything below.
    assert source_event_row.applied_at is not None
    assert source_event_row.parked_reason is None

    receipt = _source_receipt_envelope(
        event_id="evt-src-1", export_sequence=1, source_stream="signal-copier:source:telegram",
        source_provider_id="telegram", source_event_id="src-evt-1",
    )
    receipt_row = ingest_export_event(db_session, receipt.model_dump_json())
    db_session.commit()

    assert receipt_row.applied_at is not None
    assert receipt_row.parked_reason is None
    assert receipt_row.ledger_entry_id is not None

    outcome = _routing_outcome_envelope(
        event_id="evt-outcome-1", export_sequence=2, source_stream="signal-copier:source:telegram",
        originating_source_event_id="evt-src-1",
    )
    outcome_row = ingest_export_event(db_session, outcome.model_dump_json())
    db_session.commit()

    assert outcome_row.applied_at is not None
    assert outcome_row.parked_reason is None
    db_session.refresh(receipt_row)
    assert receipt_row.routing_outcome == "admitted_filled"


def test_a_source_event_kind_with_no_ledger_representation_still_parks_and_blocks_its_stream(db_session):
    """Track 35: TARGET_UPDATE/STOP_UPDATE are genuinely UNDERSTOOD kinds
    (unlike the old generic "unimplemented" bucket) -- but
    `LedgerEntry` has no stop-loss/target column at all, so there is
    nowhere honest to write one even if it correlated perfectly. Parked
    with the new, specific reason, and -- same as any other un-applied
    shape -- still blocks its stream's later sequences."""
    _seed_tenant(db_session)
    register_export_stream(db_session, tenant_id="tenant-a", source_stream="signal-copier:source:telegram", environment="LOCAL_SIM")
    db_session.commit()

    target_update_event = _source_event_envelope(
        event_id="evt-source-event-target-update", export_sequence=0, source_stream="signal-copier:source:telegram",
        kind="target_update", has_signal=False,
    )
    target_update_row = ingest_export_event(db_session, target_update_event.model_dump_json())
    db_session.commit()

    assert target_update_row.applied_at is None
    assert target_update_row.parked_reason == f"{PARKED_REASON_SOURCE_EVENT_KIND_NOT_LEDGER_REPRESENTABLE}:target_update"

    follow_up = _source_receipt_envelope(
        event_id="evt-src-after-target-update", export_sequence=1, source_stream="signal-copier:source:telegram",
    )
    follow_up_row = ingest_export_event(db_session, follow_up.model_dump_json())
    db_session.commit()

    assert follow_up_row.applied_at is None
    assert follow_up_row.parked_reason is None  # received, waiting behind the still-parked TARGET_UPDATE


def test_a_source_event_stop_update_also_parks_with_the_not_ledger_representable_reason(db_session):
    _seed_tenant(db_session)
    register_export_stream(db_session, tenant_id="tenant-a", source_stream="signal-copier:source:telegram", environment="LOCAL_SIM")
    db_session.commit()

    stop_update_event = _source_event_envelope(
        event_id="evt-source-event-stop-update", export_sequence=0, source_stream="signal-copier:source:telegram",
        kind="stop_update", has_signal=False,
    )
    stop_update_row = ingest_export_event(db_session, stop_update_event.model_dump_json())
    db_session.commit()

    assert stop_update_row.applied_at is None
    assert stop_update_row.parked_reason == f"{PARKED_REASON_SOURCE_EVENT_KIND_NOT_LEDGER_REPRESENTABLE}:stop_update"


@pytest.mark.parametrize("kind", ["delete", "cancel", "close"])
def test_a_retraction_or_close_source_event_applies_as_a_no_op_and_unblocks_its_stream(db_session, kind):
    """DELETE/CANCEL/CLOSE never carry new instruction content by the
    taxonomy's own definition (signal_platform_contracts's own
    SourceEventKind docstring) -- there is no economic fact for them to
    book, so applying them as a no-op-advance is honest, not a guess,
    and must not block the stream behind them."""
    _seed_tenant(db_session)
    register_export_stream(db_session, tenant_id="tenant-a", source_stream="signal-copier:source:telegram", environment="LOCAL_SIM")
    db_session.commit()

    event = _source_event_envelope(
        event_id=f"evt-source-event-{kind}", export_sequence=0, source_stream="signal-copier:source:telegram",
        kind=kind, has_signal=False,
    )
    row = ingest_export_event(db_session, event.model_dump_json())
    db_session.commit()

    assert row.applied_at is not None
    assert row.parked_reason is None
    assert db_session.scalars(select(LedgerEntry).where(LedgerEntry.tenant_id == "tenant-a")).all() == []

    follow_up = _source_receipt_envelope(
        event_id=f"evt-src-after-{kind}", export_sequence=1, source_stream="signal-copier:source:telegram",
    )
    follow_up_row = ingest_export_event(db_session, follow_up.model_dump_json())
    db_session.commit()

    assert follow_up_row.applied_at is not None
    assert follow_up_row.parked_reason is None


@pytest.mark.parametrize("kind", ["add", "reply"])
def test_an_add_or_reply_source_event_with_its_own_signal_applies_as_a_no_op(db_session, kind):
    """ADD/REPLY: verified against signal-copier's own live adapters --
    an add-on entry or a reply carrying a real correction still calls
    `on_signal` on its OWN fresh signal id before emitting this
    SOURCE_EVENT, so its economic content (if any) is independently
    booked by its own, separate SOURCE_RECEIPT, never by this row.
    Economically identical to ORIGINAL -- no-op-advance, no second
    ledger entry from this row."""
    _seed_tenant(db_session)
    register_export_stream(db_session, tenant_id="tenant-a", source_stream="signal-copier:source:telegram", environment="LOCAL_SIM")
    db_session.commit()

    event = _source_event_envelope(
        event_id=f"evt-source-event-{kind}", export_sequence=0, source_stream="signal-copier:source:telegram",
        kind=kind, parent_event_id="src-evt-original",
    )
    row = ingest_export_event(db_session, event.model_dump_json())
    db_session.commit()

    assert row.applied_at is not None
    assert row.parked_reason is None
    assert db_session.scalars(select(LedgerEntry).where(LedgerEntry.tenant_id == "tenant-a")).all() == []


def test_a_reply_source_event_with_no_new_instruction_also_applies_as_a_no_op(db_session):
    """A reply that carries no correction at all (`signal is None`) has
    nothing economic to book in the first place -- still a no-op, for a
    different, equally honest reason."""
    _seed_tenant(db_session)
    register_export_stream(db_session, tenant_id="tenant-a", source_stream="signal-copier:source:telegram", environment="LOCAL_SIM")
    db_session.commit()

    event = _source_event_envelope(
        event_id="evt-source-event-reply-noop", export_sequence=0, source_stream="signal-copier:source:telegram",
        kind="reply", has_signal=False, parent_event_id="src-evt-original",
    )
    row = ingest_export_event(db_session, event.model_dump_json())
    db_session.commit()

    assert row.applied_at is not None
    assert row.parked_reason is None


def test_an_edit_source_event_resolving_its_original_applies_as_a_no_op(db_session):
    """Track 35: an EDIT whose `source.original_source_event_id` names a
    real, already-applied SOURCE_EVENT for this tenant resolves cleanly
    -- applied as a no-op-advance (its own revised content, if any, is
    independently booked by the edit's own SOURCE_RECEIPT elsewhere on
    the stream, never by this row)."""
    _seed_tenant(db_session)
    register_export_stream(db_session, tenant_id="tenant-a", source_stream="signal-copier:source:telegram", environment="LOCAL_SIM")
    db_session.commit()

    original = _source_event_envelope(
        event_id="evt-source-event-original", export_sequence=0, source_stream="signal-copier:source:telegram",
        kind="original", source_provider_id="telegram", source_channel_id="chan-1", source_event_id="msg-1",
    )
    original_row = ingest_export_event(db_session, original.model_dump_json())
    db_session.commit()
    assert original_row.applied_at is not None

    edit = _source_event_envelope(
        event_id="evt-source-event-edit", export_sequence=1, source_stream="signal-copier:source:telegram",
        kind="edit", source_provider_id="telegram", source_channel_id="chan-1", source_event_id="msg-1",
        original_source_event_id="msg-1", revision_id="msg-1:edit-1",
    )
    edit_row = ingest_export_event(db_session, edit.model_dump_json())
    db_session.commit()

    assert edit_row.applied_at is not None
    assert edit_row.parked_reason is None
    assert db_session.scalars(select(LedgerEntry).where(LedgerEntry.tenant_id == "tenant-a")).all() == []


def test_an_edit_source_event_with_no_original_source_event_id_parks_honestly(db_session):
    """An EDIT this build cannot even attempt to correlate (the adapter
    never wired the revision chain) must never be silently treated as
    safe, harmless provenance just to unblock the stream -- parked with
    a specific, honest reason distinct from the old generic
    "unimplemented" bucket, and -- same as any other un-applied shape --
    still blocks its stream's later sequences."""
    _seed_tenant(db_session)
    register_export_stream(db_session, tenant_id="tenant-a", source_stream="signal-copier:source:telegram", environment="LOCAL_SIM")
    db_session.commit()

    edit = _source_event_envelope(
        event_id="evt-source-event-edit-no-target", export_sequence=0, source_stream="signal-copier:source:telegram",
        kind="edit",
    )
    edit_row = ingest_export_event(db_session, edit.model_dump_json())
    db_session.commit()

    assert edit_row.applied_at is None
    assert edit_row.parked_reason == f"{PARKED_REASON_EDIT_WITHOUT_RESOLVABLE_TARGET}:missing_original_source_event_id"

    follow_up = _source_receipt_envelope(
        event_id="evt-src-after-edit-no-target", export_sequence=1, source_stream="signal-copier:source:telegram",
    )
    follow_up_row = ingest_export_event(db_session, follow_up.model_dump_json())
    db_session.commit()

    assert follow_up_row.applied_at is None
    assert follow_up_row.parked_reason is None  # waiting behind the still-parked EDIT


def test_an_edit_source_event_naming_an_unresolvable_original_parks_rather_than_guesses(db_session):
    """The reference IS present (`original_source_event_id` is set) but
    doesn't match any already-applied SOURCE_EVENT for this tenant --
    this must never be treated as "close enough" (e.g. by matching on
    source_provider_id alone, ignoring channel/message identity) just
    to unblock the stream. Parked with the unresolved key itself in the
    reason, never silently dropped or coerced onto some other row."""
    _seed_tenant(db_session)
    register_export_stream(db_session, tenant_id="tenant-a", source_stream="signal-copier:source:telegram", environment="LOCAL_SIM")
    db_session.commit()

    # A DIFFERENT original actually exists for this tenant/provider --
    # proving a wrong, looser correlation (e.g. "any ORIGINAL on this
    # provider") could look right by accident if it weren't exact.
    decoy_original = _source_event_envelope(
        event_id="evt-source-event-decoy", export_sequence=0, source_stream="signal-copier:source:telegram",
        kind="original", source_provider_id="telegram", source_channel_id="chan-1", source_event_id="msg-decoy",
    )
    ingest_export_event(db_session, decoy_original.model_dump_json())
    db_session.commit()

    edit = _source_event_envelope(
        event_id="evt-source-event-edit-unresolved", export_sequence=1, source_stream="signal-copier:source:telegram",
        kind="edit", source_provider_id="telegram", source_channel_id="chan-1", source_event_id="msg-99",
        original_source_event_id="msg-never-arrived",
    )
    edit_row = ingest_export_event(db_session, edit.model_dump_json())
    db_session.commit()

    assert edit_row.applied_at is None
    expected_key = "tenant-a|telegram|chan-1|msg-never-arrived"
    assert edit_row.parked_reason == f"{PARKED_REASON_EDIT_WITHOUT_RESOLVABLE_TARGET}:{expected_key}"


def test_an_edit_source_event_does_not_resolve_against_a_different_tenants_identical_native_key(db_session):
    """Two tenants whose adapters happen to relay the SAME native
    provider/channel/message identity (e.g. the same public Telegram
    channel registered to two different tenants) must never let one
    tenant's EDIT resolve against the other's ORIGINAL -- the native
    key is tenant-scoped specifically to prevent this cross-tenant
    misattribution."""
    _seed_tenant(db_session, "tenant-a")
    _seed_tenant(db_session, "tenant-b")
    register_export_stream(db_session, tenant_id="tenant-a", source_stream="signal-copier:source:telegram-a", environment="LOCAL_SIM")
    register_export_stream(db_session, tenant_id="tenant-b", source_stream="signal-copier:source:telegram-b", environment="LOCAL_SIM")
    db_session.commit()

    original_for_a = _source_event_envelope(
        event_id="evt-source-event-a-original", export_sequence=0, source_stream="signal-copier:source:telegram-a",
        kind="original", source_provider_id="telegram", source_channel_id="chan-1", source_event_id="msg-shared",
    )
    ingest_export_event(db_session, original_for_a.model_dump_json())
    db_session.commit()

    edit_for_b = _source_event_envelope(
        event_id="evt-source-event-b-edit", export_sequence=0, source_stream="signal-copier:source:telegram-b",
        kind="edit", source_provider_id="telegram", source_channel_id="chan-1", source_event_id="msg-shared",
        original_source_event_id="msg-shared",
    )
    edit_for_b_row = ingest_export_event(db_session, edit_for_b.model_dump_json())
    db_session.commit()

    assert edit_for_b_row.applied_at is None
    expected_key = "tenant-b|telegram|chan-1|msg-shared"
    assert edit_for_b_row.parked_reason == f"{PARKED_REASON_EDIT_WITHOUT_RESOLVABLE_TARGET}:{expected_key}"
