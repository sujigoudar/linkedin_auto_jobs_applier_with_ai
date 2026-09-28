"""AD-19 "Content and disclosure publishing" -- the real save/list
service backing F-CONTENT. See dashboard_spec/screens/AD-19.md for the
full screen contract this implements a bounded slice of.

`_FORBIDDEN_MARKUP_CHARS` enforces "No raw HTML/script editor" (AD-19's
own acceptance text) literally: a body containing `<` or `>` is always
refused, never sanitized/stripped and silently accepted -- this is
plain text only, not a markup renderer to secure.

`_FACTUAL_CLAIM_DOCUMENT_TYPES` requires at least one source_evidence_id
-- "required for factual performance claims; No model invented
citations" (AD-19's own field help text) applied to the two document
types (methodology, status) whose content is inherently a factual claim
about performance or system state.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.content_document import ContentDocument, ContentDocumentState, ContentDocumentType

_MAX_BODY_LENGTH = 20_000
_MAX_TITLE_LENGTH = 120
_FORBIDDEN_MARKUP_CHARS = frozenset({"<", ">"})
_FACTUAL_CLAIM_DOCUMENT_TYPES: frozenset[ContentDocumentType] = frozenset(
    {ContentDocumentType.METHODOLOGY, ContentDocumentType.STATUS}
)


class InvalidContentDraftError(Exception):
    pass


class ContentNotEligibleForReviewError(Exception):
    pass


class ContentNotEligibleForPublicationError(Exception):
    pass


def list_content_documents(session: Session, *, tenant_id: str) -> list[ContentDocument]:
    return list(
        session.scalars(
            select(ContentDocument).where(ContentDocument.tenant_id == tenant_id).order_by(ContentDocument.created_at.desc())
        ).all()
    )


def get_content_document(session: Session, document_id: str, *, tenant_id: str) -> ContentDocument | None:
    document = session.get(ContentDocument, document_id)
    if document is None or document.tenant_id != tenant_id:
        return None
    return document


def save_content_draft(
    session: Session,
    *,
    tenant_id: str,
    document_type: str,
    locale: str,
    title: str,
    body: str,
    audience_policy_id: str,
    source_evidence_ids: list[str],
) -> ContentDocument:
    try:
        document_type_enum = ContentDocumentType(document_type)
    except ValueError as exc:
        raise InvalidContentDraftError(f"{document_type!r} is not a known structured template") from exc
    if not title or not (1 <= len(title) <= _MAX_TITLE_LENGTH):
        raise InvalidContentDraftError(f"title must be 1..{_MAX_TITLE_LENGTH} characters")
    if not body or not (1 <= len(body) <= _MAX_BODY_LENGTH):
        raise InvalidContentDraftError(f"body must be 1..{_MAX_BODY_LENGTH} characters")
    if any(char in body for char in _FORBIDDEN_MARKUP_CHARS):
        raise InvalidContentDraftError("body must be plain text -- no raw HTML/script markup is permitted")
    if not audience_policy_id or not audience_policy_id.strip():
        raise InvalidContentDraftError("audience_policy_id is required")
    if document_type_enum in _FACTUAL_CLAIM_DOCUMENT_TYPES and not source_evidence_ids:
        raise InvalidContentDraftError(
            f"{document_type_enum.value!r} makes a factual claim and requires at least one source_evidence_id"
        )

    document = ContentDocument(
        tenant_id=tenant_id,
        document_type=document_type_enum,
        locale=locale or "en-US",
        title=title,
        body=body,
        audience_policy_id=audience_policy_id,
        source_evidence_ids=list(source_evidence_ids),
    )
    session.add(document)
    session.flush()
    return document


def request_content_review(session: Session, document: ContentDocument) -> ContentDocument:
    """"Confirm: Submit for review; publishing distinct approved
    command" -- moves a DRAFT to SUBMITTED_FOR_REVIEW only. Actual
    publication is a separate, not-yet-built admission decision this
    function has no authority over, matching AD-07/AD-08's own
    established pattern."""
    if document.state != ContentDocumentState.DRAFT:
        raise ContentNotEligibleForReviewError(f"document is {document.state.value}, not DRAFT")
    document.state = ContentDocumentState.SUBMITTED_FOR_REVIEW
    document.updated_at = datetime.now(timezone.utc)
    session.flush()
    return document


def publish_content_document(session: Session, document: ContentDocument) -> ContentDocument:
    """PU-06's own missing admission decision -- this is the ONLY
    function anywhere in this build that ever sets PUBLISHED. Only a
    document already SUBMITTED_FOR_REVIEW is eligible; a DRAFT (never
    reviewed) or an already-PUBLISHED document both refuse, so this can
    never be used to skip the review step or double-publish."""
    if document.state != ContentDocumentState.SUBMITTED_FOR_REVIEW:
        raise ContentNotEligibleForPublicationError(f"document is {document.state.value}, not SUBMITTED_FOR_REVIEW")
    document.state = ContentDocumentState.PUBLISHED
    document.updated_at = datetime.now(timezone.utc)
    session.flush()
    return document


def list_public_content_documents(
    session: Session, *, document_type: str | None = None, locale: str | None = None
) -> list[ContentDocument]:
    """PU-06 "Methodology, risk and legal documents" -- anonymous, no
    tenant scope. Deliberately identical in shape to product_admin's own
    list_published_products: an explicit `state == PUBLISHED` filter is
    the one code path that decides what counts as published, so this is
    correct even where RLS is bypassed (e.g. the admin database
    connection this build's own tests use). The real
    `content_document_visibility` RLS policy (app/db.py) is defense in
    depth beyond this, not the only protection -- the same relationship
    AD-01's own slice already documented between an explicit tenant_id
    filter and RLS."""
    query = select(ContentDocument).where(ContentDocument.state == ContentDocumentState.PUBLISHED).order_by(
        ContentDocument.document_type, ContentDocument.locale
    )
    if document_type is not None:
        try:
            query = query.where(ContentDocument.document_type == ContentDocumentType(document_type))
        except ValueError:
            return []
    if locale is not None:
        query = query.where(ContentDocument.locale == locale)
    return list(session.scalars(query).all())
