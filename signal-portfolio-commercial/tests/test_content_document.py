"""AD-19 "Content and disclosure publishing" -- app/services/content_document.py's
own tests. Real Postgres, real tenant-scoped session."""
import pytest
from sqlalchemy import select

from app.db import set_tenant_scope
from app.models.content_document import ContentDocument, ContentDocumentState
from app.services.content_document import (
    ContentNotEligibleForPublicationError,
    ContentNotEligibleForReviewError,
    InvalidContentDraftError,
    get_content_document,
    list_content_documents,
    list_public_content_documents,
    publish_content_document,
    request_content_review,
    save_content_draft,
)


def test_list_content_documents_is_empty_before_any_are_saved(db_session):
    assert list_content_documents(db_session, tenant_id="tenant-a") == []


def test_save_content_draft_rejects_html_markup(db_session):
    with pytest.raises(InvalidContentDraftError, match="raw HTML"):
        save_content_draft(
            db_session,
            tenant_id="tenant-a",
            document_type="help",
            locale="en-US",
            title="Help page",
            body="<script>alert(1)</script>",
            audience_policy_id="audience-1",
            source_evidence_ids=[],
        )


def test_save_content_draft_rejects_angle_brackets_even_without_a_script_tag(db_session):
    with pytest.raises(InvalidContentDraftError, match="raw HTML"):
        save_content_draft(
            db_session,
            tenant_id="tenant-a",
            document_type="help",
            locale="en-US",
            title="Help page",
            body="See <a href='x'>link</a>",
            audience_policy_id="audience-1",
            source_evidence_ids=[],
        )


def test_save_content_draft_requires_evidence_for_methodology(db_session):
    with pytest.raises(InvalidContentDraftError, match="factual claim"):
        save_content_draft(
            db_session,
            tenant_id="tenant-a",
            document_type="methodology",
            locale="en-US",
            title="Methodology",
            body="Our approach is X.",
            audience_policy_id="audience-1",
            source_evidence_ids=[],
        )


def test_save_content_draft_does_not_require_evidence_for_help(db_session):
    document = save_content_draft(
        db_session,
        tenant_id="tenant-a",
        document_type="help",
        locale="en-US",
        title="Help page",
        body="Contact support for questions.",
        audience_policy_id="audience-1",
        source_evidence_ids=[],
    )
    assert document.title == "Help page"


def test_save_then_reload_persists_the_real_draft(db_session):
    save_content_draft(
        db_session,
        tenant_id="tenant-a",
        document_type="privacy",
        locale="en-US",
        title="Privacy policy",
        body="We do not sell your data.",
        audience_policy_id="audience-1",
        source_evidence_ids=[],
    )
    db_session.commit()

    documents = list_content_documents(db_session, tenant_id="tenant-a")
    assert len(documents) == 1
    assert documents[0].state == ContentDocumentState.DRAFT


def test_get_content_document_is_none_for_a_cross_tenant_document(db_session):
    document = save_content_draft(
        db_session,
        tenant_id="tenant-a",
        document_type="help",
        locale="en-US",
        title="Help page",
        body="Contact support.",
        audience_policy_id="audience-1",
        source_evidence_ids=[],
    )
    db_session.commit()

    assert get_content_document(db_session, document.document_id, tenant_id="tenant-b") is None


def test_request_content_review_moves_a_draft_to_submitted(db_session):
    document = save_content_draft(
        db_session,
        tenant_id="tenant-a",
        document_type="help",
        locale="en-US",
        title="Help page",
        body="Contact support.",
        audience_policy_id="audience-1",
        source_evidence_ids=[],
    )
    db_session.commit()

    request_content_review(db_session, document)
    db_session.commit()
    assert document.state == ContentDocumentState.SUBMITTED_FOR_REVIEW


def test_request_content_review_refuses_a_non_draft_document(db_session):
    document = save_content_draft(
        db_session,
        tenant_id="tenant-a",
        document_type="help",
        locale="en-US",
        title="Help page",
        body="Contact support.",
        audience_policy_id="audience-1",
        source_evidence_ids=[],
    )
    db_session.commit()
    request_content_review(db_session, document)
    db_session.commit()

    with pytest.raises(ContentNotEligibleForReviewError):
        request_content_review(db_session, document)


def test_publish_content_document_moves_a_submitted_document_to_published(db_session):
    document = save_content_draft(
        db_session,
        tenant_id="tenant-a",
        document_type="help",
        locale="en-US",
        title="Help page",
        body="Contact support.",
        audience_policy_id="audience-1",
        source_evidence_ids=[],
    )
    db_session.commit()
    request_content_review(db_session, document)
    db_session.commit()

    publish_content_document(db_session, document)
    db_session.commit()
    assert document.state == ContentDocumentState.PUBLISHED


def test_publish_content_document_refuses_a_draft_document(db_session):
    document = save_content_draft(
        db_session,
        tenant_id="tenant-a",
        document_type="help",
        locale="en-US",
        title="Help page",
        body="Contact support.",
        audience_policy_id="audience-1",
        source_evidence_ids=[],
    )
    db_session.commit()

    with pytest.raises(ContentNotEligibleForPublicationError):
        publish_content_document(db_session, document)


def test_publish_content_document_refuses_an_already_published_document(db_session):
    document = save_content_draft(
        db_session,
        tenant_id="tenant-a",
        document_type="help",
        locale="en-US",
        title="Help page",
        body="Contact support.",
        audience_policy_id="audience-1",
        source_evidence_ids=[],
    )
    db_session.commit()
    request_content_review(db_session, document)
    db_session.commit()
    publish_content_document(db_session, document)
    db_session.commit()

    with pytest.raises(ContentNotEligibleForPublicationError):
        publish_content_document(db_session, document)


def test_list_public_content_documents_is_empty_before_any_are_published(db_session):
    save_content_draft(
        db_session,
        tenant_id="tenant-a",
        document_type="help",
        locale="en-US",
        title="Help page",
        body="Contact support.",
        audience_policy_id="audience-1",
        source_evidence_ids=[],
    )
    db_session.commit()

    assert list_public_content_documents(db_session) == []


def test_list_public_content_documents_shows_a_published_document_across_tenants(db_session):
    document = save_content_draft(
        db_session,
        tenant_id="tenant-a",
        document_type="help",
        locale="en-US",
        title="Help page",
        body="Contact support.",
        audience_policy_id="audience-1",
        source_evidence_ids=[],
    )
    db_session.commit()
    request_content_review(db_session, document)
    db_session.commit()
    publish_content_document(db_session, document)
    db_session.commit()

    documents = list_public_content_documents(db_session)
    assert len(documents) == 1
    assert documents[0].title == "Help page"


def test_list_public_content_documents_filters_by_document_type(db_session):
    help_doc = save_content_draft(
        db_session,
        tenant_id="tenant-a",
        document_type="help",
        locale="en-US",
        title="Help page",
        body="Contact support.",
        audience_policy_id="audience-1",
        source_evidence_ids=[],
    )
    privacy_doc = save_content_draft(
        db_session,
        tenant_id="tenant-a",
        document_type="privacy",
        locale="en-US",
        title="Privacy policy",
        body="We do not sell your data.",
        audience_policy_id="audience-1",
        source_evidence_ids=[],
    )
    db_session.commit()
    for document in (help_doc, privacy_doc):
        request_content_review(db_session, document)
        publish_content_document(db_session, document)
    db_session.commit()

    documents = list_public_content_documents(db_session, document_type="privacy")
    assert len(documents) == 1
    assert documents[0].title == "Privacy policy"


def test_content_document_visibility_rls_hides_another_tenants_draft(db_session, tenant_session_factory):
    """The real Postgres policy itself (app/db.py's
    `content_document_visibility`), exercised with a bare `select` --
    not through content_document.py's own tenant_id filters, which
    would mask a broken policy underneath. A tenant-a draft must never
    come back for a tenant-b-scoped raw query, at the database layer,
    independent of any application-level filtering."""
    session_a = tenant_session_factory()
    try:
        set_tenant_scope(session_a, "tenant-a")
        save_content_draft(
            session_a,
            tenant_id="tenant-a",
            document_type="help",
            locale="en-US",
            title="RLS Hidden Draft",
            body="Contact support.",
            audience_policy_id="audience-1",
            source_evidence_ids=[],
        )
        session_a.commit()
    finally:
        session_a.close()

    session_b = tenant_session_factory()
    try:
        set_tenant_scope(session_b, "tenant-b")
        rows = session_b.scalars(select(ContentDocument)).all()
        assert rows == []
    finally:
        session_b.rollback()
        session_b.close()


def test_content_document_visibility_rls_shows_a_published_document_to_an_unscoped_session(
    db_session, tenant_session_factory
):
    """The mirror image of the hidden-draft test: an unscoped session
    (no `app.tenant_id` set at all, matching an anonymous PU-06
    request) must still see a genuinely PUBLISHED document from any
    tenant -- proving the policy's `OR state = 'PUBLISHED'` clause, not
    just its tenant-match clause."""
    session_a = tenant_session_factory()
    try:
        set_tenant_scope(session_a, "tenant-a")
        document = save_content_draft(
            session_a,
            tenant_id="tenant-a",
            document_type="help",
            locale="en-US",
            title="RLS Visible Published",
            body="Contact support.",
            audience_policy_id="audience-1",
            source_evidence_ids=[],
        )
        session_a.commit()
        set_tenant_scope(session_a, "tenant-a")
        request_content_review(session_a, document)
        session_a.commit()
        set_tenant_scope(session_a, "tenant-a")
        publish_content_document(session_a, document)
        session_a.commit()
    finally:
        session_a.close()

    unscoped_session = tenant_session_factory()
    try:
        rows = unscoped_session.scalars(select(ContentDocument)).all()
        assert len(rows) == 1
        assert rows[0].title == "RLS Visible Published"
    finally:
        unscoped_session.rollback()
        unscoped_session.close()
