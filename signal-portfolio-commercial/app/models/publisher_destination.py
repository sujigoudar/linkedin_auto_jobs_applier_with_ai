"""PublisherDestination: AD-09 "Publisher channels and strategies" --
a tenant's own declared, inactive mapping of one external
platform/strategy to a publication mode. See
dashboard_spec/screens/AD-09.md's F-PUBLISHER form for the full
contract this implements a bounded slice of.

Saving a destination never sends a signal or transmits anything --
nothing in the publication pipeline (app/services/publication.py,
collective2_publisher.py) reads this table at all yet. "Save inactive
destination" (AD-09's own action label) is therefore literally true: a
row here has no live effect.

`credential_ref` is an opaque reference, never a real secret -- "Never
disclose key" (AD-09's own field help text). No real credential is ever
stored, accepted, or transmitted by this module.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Platform(str, enum.Enum):
    COLLECTIVE2 = "collective2"
    ETORO = "etoro"
    COPYFACTORY = "copyfactory"
    BROKER_NATIVE = "broker_native"


class PublisherEnvironment(str, enum.Enum):
    LOCAL_SIMULATION = "local_simulation"
    EXTERNAL_TEST = "external_test"
    DEMO = "demo"
    LIVE = "live"


class PublicationMode(str, enum.Enum):
    API_STRATEGY_PUBLISHER = "api_strategy_publisher"
    APPROVED_MASTER_COPY = "approved_master_copy"


class PublisherDestination(Base):
    __tablename__ = "publisher_destinations"

    destination_id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String, nullable=False, index=True)

    platform: Mapped[Platform] = mapped_column(Enum(Platform, native_enum=False), nullable=False)
    external_strategy_id: Mapped[str] = mapped_column(String, nullable=False)
    environment: Mapped[PublisherEnvironment] = mapped_column(
        Enum(PublisherEnvironment, native_enum=False), nullable=False, default=PublisherEnvironment.LOCAL_SIMULATION
    )
    credential_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    capability_manifest_id: Mapped[str | None] = mapped_column(String, nullable=True)
    publication_mode: Mapped[PublicationMode] = mapped_column(
        Enum(PublicationMode, native_enum=False), nullable=False, default=PublicationMode.API_STRATEGY_PUBLISHER
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
