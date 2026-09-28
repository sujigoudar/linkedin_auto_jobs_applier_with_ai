"""Sleeve<->source identity mapping -- S12 step 5 "Portfolio Lab source
feed", INTEGRATION_DECISION.md S5's own "No name-based joins and no
automatic linking solely because two user records have the same email"
applied to this specific case: a `SOURCE_RECEIPT` envelope's own
`SourceIdentity` (provider, analyst, parser_version) is matched against
`app/models/sleeve.py`'s own `Sleeve` rows by an EXPLICIT, fully-
specified identity tuple -- never a partial or fuzzy match, and never
"the sleeve for this provider" when more than one exists.

`Sleeve.provider`/`Sleeve.analyst`/`Sleeve.parser_version` are all
NOT NULL (spec/docs/03_portfolio_research_and_selection.md: a sleeve is
"one qualified provider/analyst/strategy/horizon/parser/policy
combination" -- an unattributed sleeve isn't a real one), so a
`SourceIdentity` with `analyst_id=None` can never match any sleeve --
this is correct, not a gap this module should paper over: an
unattributed source recommendation has no qualified sleeve to belong
to yet, by construction of what a sleeve IS.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.sleeve import Sleeve


def resolve_sleeve_for_source(
    session: Session, *, tenant_id: str, source_provider_id: str, analyst_id: str | None, parser_version: str
) -> Sleeve | None:
    """Returns the ONE sleeve matching this exact (provider, analyst,
    parser_version) tuple for this tenant, or `None` if none has been
    admitted yet. `analyst_id=None` always returns `None` (see this
    module's own docstring) without even querying -- there is no sleeve
    row that could ever match a NULL analyst, so this is a real,
    honest short-circuit, not an optimization that changes behavior."""
    if analyst_id is None:
        return None
    # Assumes the qualification/admission process (Phase 04, out of this
    # module's own scope) never admits two sleeves sharing the identical
    # (tenant, provider, analyst, parser_version) tuple -- there is no
    # DB-level uniqueness constraint enforcing that today. If it were
    # ever violated, `.first()` picks one arbitrarily rather than
    # erroring; this module does not attempt to detect or resolve that
    # ambiguity.
    return session.scalars(
        select(Sleeve).where(
            Sleeve.tenant_id == tenant_id,
            Sleeve.provider == source_provider_id,
            Sleeve.analyst == analyst_id,
            Sleeve.parser_version == parser_version,
        )
    ).first()
