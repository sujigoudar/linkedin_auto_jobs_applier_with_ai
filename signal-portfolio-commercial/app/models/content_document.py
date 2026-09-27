"""ContentDocument: AD-19 "Content and disclosure publishing" -- a
tenant's own draftable public-content document. See
dashboard_spec/screens/AD-19.md's F-CONTENT form for the full contract
this implements a bounded slice of.

Deliberately mutable while DRAFT, like Product's own precedent --
"Persist only scoped explicit drafts" (AD-19's own panel contract).
Submitting for review is this screen's own "Confirm" action;
PUBLISHED is a distinct, separately reviewed admission decision this
model has no authority over, matching AD-07/AD-08's own established
publish-is-not-release-is-not-draft separation.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import ARRAY, DateTime, Enum, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ContentDocumentType(str, enum.Enum):
    METHODOLOGY = "methodology"
    RISK = "risk"
    BILLING_TERMS = "billing_terms"
    PRIVACY = "privacy"
    HELP = "help"
    STATUS = "status"


class ContentDocumentState(str, enum.Enum):
    DRAFT = "DRAFT"
    SUBMITTED_FOR_REVIEW = "SUBMITTED_FOR_REVIEW"
    PUBLISHED = "PUBLISHED"


class ContentDocument(Base):
    __tablename__ = "content_documents"

    document_id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String, nullable=False, index=True)

    document_type: Mapped[ContentDocumentType] = mapped_column(
        Enum(ContentDocumentType, native_enum=False), nullable=False
    )
    locale: Mapped[str] = mapped_column(String, nullable=False, default="en-US")
    title: Mapped[str] = mapped_column(String, nullable=False)
    body: Mapped[str] = mapped_column(String, nullable=False)
    audience_policy_id: Mapped[str] = mapped_column(String, nullable=False)
    source_evidence_ids: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    state: Mapped[ContentDocumentState] = mapped_column(
        Enum(ContentDocumentState, native_enum=False), nullable=False, default=ContentDocumentState.DRAFT
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
