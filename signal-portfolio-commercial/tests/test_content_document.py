"""AD-19 "Content and disclosure publishing" -- app/services/content_document.py's
own tests. Real Postgres, real tenant-scoped session."""
import pytest

from app.models.content_document import ContentDocumentState
from app.services.content_document import (
    ContentNotEligibleForReviewError,
    InvalidContentDraftError,
    get_content_document,
    list_content_documents,
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
