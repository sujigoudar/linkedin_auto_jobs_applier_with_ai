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
