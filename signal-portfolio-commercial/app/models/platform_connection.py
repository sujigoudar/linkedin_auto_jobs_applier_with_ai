"""PlatformConnection: CU-07 "Platform connections" / CU-08 "Connection
wizard" -- a customer's own declared platform/broker connection
intent, per dashboard_spec/screens/CU-08.md's F-CONNECTION form. See
app/services/platform_connection.py for the full screen contract this
implements a bounded slice of.

CU-08's own "Begin authorization" / "Verify connection" steps need a
real hosted OAuth authorization flow and a real account-identity
readback this environment does not have -- only "Save verified
connection" is buildable, and even that only as a real, honestly
DECLARED (never VERIFIED) record: `environment` is restricted to
`local_simulation` at the service layer (the same honesty-gate
discipline as AD-09's publisher destinations/AD-17's integration
configurations), and `platform` is restricted to the same reviewed
adapter allowlist those two screens already established (Collective2,
eToro, MetaApi CopyFactory). "Connected does not mean authorized to
copy" (CU-07's own acceptance text) holds by construction: nothing
here grants trading/copying authority, and no live connectivity is
ever attempted.

The compound FK to `memberships` matches `ApiKey`/`SupportCase`'s own
precedent -- a customer can declare more than one connection, so
`connection_id` is its own primary key.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, ForeignKeyConstraint, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class PlatformConnectionState(str, enum.Enum):
    DECLARED = "declared"
    DISCONNECTED = "disconnected"


class PlatformConnection(Base):
    __tablename__ = "platform_connections"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["memberships.tenant_id", "memberships.user_id"],
            name="fk_platform_connection_membership",
        ),
    )

    connection_id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)

    platform: Mapped[str] = mapped_column(String, nullable=False)
    environment: Mapped[str] = mapped_column(String, nullable=False)
    #: A customer-supplied label only (e.g. "IBKR ****1234") -- never a
    #: real account number or credential; no real readback verifies it.
    masked_account_label: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[PlatformConnectionState] = mapped_column(
        Enum(PlatformConnectionState, native_enum=False), nullable=False, default=PlatformConnectionState.DECLARED
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
