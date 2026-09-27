"""Pydantic mirror of spec/contracts/RightsGrant.schema.json -- used to
validate anything crossing an API boundary (never trust an ORM row alone
for that; a schema drift between the model and the contract must fail
loud here, not silently accept a malformed record)."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.rights import RightsStatus, RightsUse


class RightsGrantSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    grant_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    grantee_entity: str = Field(min_length=1)
    contract_hash: str = Field(min_length=1)
    status: RightsStatus
    uses: list[RightsUse]
    channels: list[str]
    jurisdictions: list[str]
    assets: list[str]
    effective_at: datetime
    expires_at: datetime
    attribution_policy_id: str = Field(min_length=1)
    wind_down_policy_id: str = Field(min_length=1)
    review_id: str = Field(min_length=1)
