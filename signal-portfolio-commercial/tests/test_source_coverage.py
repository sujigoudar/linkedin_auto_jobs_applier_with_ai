"""INTEGRATION_ACCEPTANCE_CASES.json INT-027 "All permitted source
outcomes reach research" -- app/services/source_coverage.py's own
tests. Real Postgres, exercised through the real ingest path
(ingest_export_event), never a hand-built InboxEvent fixture, so
disposition labeling reflects exactly what the real projection logic
produces."""
from app.models.tenancy import Tenant
from app.services.integration_inbox import ingest_export_event, register_export_stream
from app.services.source_coverage import (
    DISPOSITION_LEDGER_RECORDED,
    DISPOSITION_PARKED,
    DISPOSITION_RECEIVED_NO_LEDGER_ENTRY,
    compute_source_coverage,
)
from tests.test_integration_inbox import _execution_envelope, _routing_outcome_envelope, _source_receipt_envelope


def _seed_tenant(db_session, tenant_id="tenant-a"):
    if db_session.get(Tenant, tenant_id) is None:
        db_session.add(Tenant(tenant_id=tenant_id, display_name="Tenant", environment="LOCAL_SIM"))
        db_session.commit()


def test_an_empty_tenant_has_an_empty_report(db_session):
    _seed_tenant(db_session)
    report = compute_source_coverage(db_session, tenant_id="tenant-a")
    assert report.total_count == 0
    assert report.ledger_recorded_count == 0
    assert report.parked_count == 0
    assert report.received_no_ledger_entry_count == 0


def test_a_source_receipt_with_known_quantity_and_price_is_ledger_recorded(db_session):
    _seed_tenant(db_session)
    register_export_stream(db_session, tenant_id="tenant-a", source_stream="signal-copier:acct1", environment="LOCAL_SIM")
    db_session.commit()

    envelope = _source_receipt_envelope(quantity="10", price="150.00")
    ingest_export_event(db_session, envelope.model_dump_json())
    db_session.commit()

    report = compute_source_coverage(db_session, tenant_id="tenant-a")
    assert report.total_count == 1
    assert report.ledger_recorded_count == 1
    row = report.rows[0]
    assert row.disposition == DISPOSITION_LEDGER_RECORDED
    assert row.ledger_entry_id is not None
    assert row.parked_reason is None
    assert row.routing_outcome is None  # no correlated outcome has arrived yet


def test_a_source_receipt_with_unknown_quantity_or_price_has_no_ledger_entry(db_session):
    _seed_tenant(db_session)
    register_export_stream(db_session, tenant_id="tenant-a", source_stream="signal-copier:acct1", environment="LOCAL_SIM")
    db_session.commit()

    envelope = _source_receipt_envelope(quantity=None, price=None)
    ingest_export_event(db_session, envelope.model_dump_json())
    db_session.commit()

    report = compute_source_coverage(db_session, tenant_id="tenant-a")
    assert report.total_count == 1
    assert report.received_no_ledger_entry_count == 1
    assert report.rows[0].disposition == DISPOSITION_RECEIVED_NO_LEDGER_ENTRY


def test_an_out_of_order_source_receipt_is_parked_until_its_gap_fills(db_session):
    _seed_tenant(db_session)
    register_export_stream(db_session, tenant_id="tenant-a", source_stream="signal-copier:acct1", environment="LOCAL_SIM")
    db_session.commit()

    first = _source_receipt_envelope(event_id="evt-src-0", export_sequence=0, quantity="10", price="100.00")
    ahead = _source_receipt_envelope(event_id="evt-src-1", export_sequence=1, quantity="10", price="100.00")
    ingest_export_event(db_session, ahead.model_dump_json())
    db_session.commit()

    report = compute_source_coverage(db_session, tenant_id="tenant-a")
    assert report.total_count == 1
    assert report.parked_count == 1
    assert report.rows[0].disposition == DISPOSITION_PARKED
    assert report.rows[0].parked_reason is None  # received, waiting behind the gap -- not itself incompatible

    ingest_export_event(db_session, first.model_dump_json())
    db_session.commit()
    report_after_gap_fills = compute_source_coverage(db_session, tenant_id="tenant-a")
    assert report_after_gap_fills.parked_count == 0
    assert report_after_gap_fills.ledger_recorded_count == 2


def test_rows_are_scoped_per_tenant(db_session):
    _seed_tenant(db_session, "tenant-a")
    _seed_tenant(db_session, "tenant-b")
    register_export_stream(db_session, tenant_id="tenant-a", source_stream="signal-copier:acct-a", environment="LOCAL_SIM")
    register_export_stream(db_session, tenant_id="tenant-b", source_stream="signal-copier:acct-b", environment="LOCAL_SIM")
    db_session.commit()

    envelope_a = _source_receipt_envelope(event_id="evt-a", source_stream="signal-copier:acct-a")
    ingest_export_event(db_session, envelope_a.model_dump_json())
    db_session.commit()

    report_b = compute_source_coverage(db_session, tenant_id="tenant-b")
    assert report_b.total_count == 0


def test_execution_applied_events_are_never_counted_here(db_session):
    """This report is specifically about SOURCE_RECEIPT coverage -- an
    EXECUTION_APPLIED event on the same stream must never inflate its
    counts."""
    _seed_tenant(db_session)
    register_export_stream(db_session, tenant_id="tenant-a", source_stream="signal-copier:acct1", environment="LOCAL_SIM")
    db_session.commit()

    source_envelope = _source_receipt_envelope(event_id="evt-src", export_sequence=0)
    ingest_export_event(db_session, source_envelope.model_dump_json())
    db_session.commit()

    execution_envelope = _execution_envelope(event_id="evt-exec", export_sequence=1)
    ingest_export_event(db_session, execution_envelope.model_dump_json())
    db_session.commit()

    report = compute_source_coverage(db_session, tenant_id="tenant-a")
    assert report.total_count == 1


def test_a_correlated_routing_outcome_surfaces_on_its_own_receipt_row(db_session):
    """INT-027 "All permitted source outcomes reach research", end to
    end through this report: a SOURCE_RECEIPT (the "one simulated source
    instruction" S12 step 5 always exports, exactly as signal-copier's
    real `app/engine.py`/`app/export_events.py` produce it -- see
    `_source_receipt_envelope`'s own docstring) followed by its own
    real, correlated ROUTING_ADMISSION_OUTCOME (`_routing_outcome_envelope`,
    the exact shape `build_routing_admission_outcome_envelope` produces)
    shows the real matching outcome on this report's own row -- never a
    second row, never guessed from disposition alone."""
    _seed_tenant(db_session)
    register_export_stream(db_session, tenant_id="tenant-a", source_stream="signal-copier:acct1", environment="LOCAL_SIM")
    db_session.commit()

    receipt = _source_receipt_envelope(event_id="evt-src-1", export_sequence=0, quantity="10", price="150.00")
    ingest_export_event(db_session, receipt.model_dump_json())
    db_session.commit()

    # Before the outcome arrives, the report is honest that it doesn't
    # know yet -- never fabricated from the ledger disposition alone.
    report_before = compute_source_coverage(db_session, tenant_id="tenant-a")
    assert report_before.total_count == 1
    assert report_before.rows[0].disposition == DISPOSITION_LEDGER_RECORDED
    assert report_before.rows[0].routing_outcome is None

    outcome = _routing_outcome_envelope(
        event_id="evt-outcome-1", export_sequence=1, originating_source_event_id="evt-src-1",
        outcome="admitted_filled", account_id="acct1",
    )
    ingest_export_event(db_session, outcome.model_dump_json())
    db_session.commit()

    report_after = compute_source_coverage(db_session, tenant_id="tenant-a")
    # The ROUTING_ADMISSION_OUTCOME event is its own InboxEvent row too,
    # but it is NOT a SOURCE_RECEIPT -- this report's own total_count
    # (SOURCE_RECEIPT rows only) must not double-count it.
    assert report_after.total_count == 1
    row = report_after.rows[0]
    assert row.event_id == "evt-src-1"
    assert row.disposition == DISPOSITION_LEDGER_RECORDED
    assert row.routing_outcome == "admitted_filled"
