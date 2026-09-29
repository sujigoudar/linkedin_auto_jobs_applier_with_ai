"""AD-18 "Audit log and release evidence" -- app/services/evidence_manifest.py's
own tests. Real Postgres, real tenant-scoped session, real AuditEvent rows.
"""
import copy

from app.services.audit_log import append_audit_event, list_audit_events
from app.services.evidence_manifest import (
    EVIDENCE_MANIFEST_EXPORT_ACTION,
    compute_manifest_content_hash,
    generate_evidence_manifest,
)
from signal_platform_contracts import compute_payload_hash


def _seed_events(db_session):
    append_audit_event(
        db_session, tenant_id="tenant-a", actor_user_id="owner-a", object_type="membership",
        object_id="obj-1", action="invite_staff_member:researcher",
    )
    append_audit_event(
        db_session, tenant_id="tenant-a", actor_user_id="owner-a", object_type="membership",
        object_id="obj-2", action="revoke_staff_member",
    )
    # A different tenant's event must never leak into tenant-a's manifest.
    append_audit_event(
        db_session, tenant_id="tenant-b", actor_user_id="owner-b", object_type="membership",
        object_id="obj-3", action="invite_staff_member:researcher",
    )
    db_session.commit()


def test_generate_evidence_manifest_bundles_the_real_rows_verbatim(db_session):
    _seed_events(db_session)

    manifest = generate_evidence_manifest(
        db_session, tenant_id="tenant-a", generated_by_actor_user_id="reviewer-a",
    )
    db_session.commit()

    assert manifest.row_count == 2
    assert manifest.tenant_id == "tenant-a"
    assert manifest.generated_by_actor_user_id == "reviewer-a"
    assert {row["object_id"] for row in manifest.rows} == {"obj-1", "obj-2"}
    # No tenant-b row leaked into this tenant's evidence bundle.
    assert all(row["tenant_id"] == "tenant-a" for row in manifest.rows)


def test_generate_evidence_manifest_applies_the_same_filter_as_the_audit_search(db_session):
    _seed_events(db_session)

    manifest = generate_evidence_manifest(
        db_session, tenant_id="tenant-a", generated_by_actor_user_id="reviewer-a", object_id="obj-1",
    )
    db_session.commit()

    assert manifest.row_count == 1
    assert manifest.rows[0]["object_id"] == "obj-1"
    assert manifest.filter_criteria == {"actor_user_id": None, "object_id": "obj-1", "action": None}


def test_generate_evidence_manifest_hash_matches_a_hand_computed_hash_of_the_same_rows(db_session):
    """Load-bearing: the manifest's own content_hash is reproducible from
    the exact rows it bundled, via the SAME hashing convention this
    platform already uses (`compute_payload_hash`), not a bespoke one."""
    _seed_events(db_session)

    manifest = generate_evidence_manifest(
        db_session, tenant_id="tenant-a", generated_by_actor_user_id="reviewer-a",
    )
    db_session.commit()

    hand_computed = compute_payload_hash({"rows": manifest.rows})
    assert manifest.content_hash == hand_computed
    assert manifest.content_hash == compute_manifest_content_hash(manifest.rows)


def test_manifest_hash_detects_a_single_tampered_row(db_session):
    """Load-bearing tamper-detection: corrupt one already-hashed row's
    content (simulating post-export tampering) and confirm the hash
    genuinely changes -- the hash function is sensitive to content, not
    merely present."""
    _seed_events(db_session)

    manifest = generate_evidence_manifest(
        db_session, tenant_id="tenant-a", generated_by_actor_user_id="reviewer-a",
    )
    db_session.commit()

    original_hash = manifest.content_hash

    tampered_rows = copy.deepcopy(manifest.rows)
    tampered_rows[0]["action"] = "invite_staff_member:owner"  # a real field, real change
    tampered_hash = compute_manifest_content_hash(tampered_rows)

    assert tampered_hash != original_hash


def test_manifest_hash_is_unchanged_when_rows_are_untouched(db_session):
    _seed_events(db_session)

    manifest = generate_evidence_manifest(
        db_session, tenant_id="tenant-a", generated_by_actor_user_id="reviewer-a",
    )
    db_session.commit()

    # Re-hashing the same, untouched rows must reproduce the same hash --
    # otherwise the hash could never be used to verify integrity later.
    assert compute_manifest_content_hash(copy.deepcopy(manifest.rows)) == manifest.content_hash


def test_generate_evidence_manifest_appends_a_real_audit_event_for_the_export_itself(db_session):
    _seed_events(db_session)

    generate_evidence_manifest(
        db_session, tenant_id="tenant-a", generated_by_actor_user_id="reviewer-a",
    )
    db_session.commit()

    events = list_audit_events(db_session, tenant_id="tenant-a", action=EVIDENCE_MANIFEST_EXPORT_ACTION)
    assert len(events) == 1
    assert events[0].actor_user_id == "reviewer-a"
