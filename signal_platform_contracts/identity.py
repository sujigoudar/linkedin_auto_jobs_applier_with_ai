"""INTEGRATION_DECISION.md S5's own shared identity contract: "Create
explicit mappings ... No name-based joins and no automatic linking solely
because two user records have the same email."

Each `*Identity` model below is one of S5's named clusters. A payload
never inlines these fields loose -- it holds one of these frozen models
per cluster, and `build_subject` flattens the clusters it's given into
the envelope's own `subject: dict[str, str]`, prefixed by cluster name so
two clusters can never collide on a field name.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from signal_platform_contracts.money import Money


class SourceIdentity(BaseModel):
    """S5: "source_provider_id, source_channel_id, analyst_id, strategy_id,
    parser_version" plus "Original source_event_id, revision ID,
    parent/reply relation".

    `source_channel_id` + `source_event_id` together ARE this event's
    native provider message identity (e.g. a Discord/Telegram/Slack
    channel id + message id, or a webhook source's own idempotency key)
    -- true deduplication and edit/delete/reply correlation both key off
    this pair, never off re-parsed message text. `revision_id` is this
    SPECIFIC revision's own native id (e.g. Telegram's edited-message
    update carries the same message id but a new edit_date -- a source
    adapter that has a real distinct revision identifier puts it here;
    one that doesn't may reuse `source_event_id` as its own revision
    marker). `original_source_event_id` is the additional, distinct
    thing a revision chain needs: the FIRST message's own
    `source_event_id` this revision traces back to -- `None` when this
    identity describes the original message itself (no revision chain
    exists yet) or when this event isn't part of a revision chain at
    all. Never fabricated -- an adapter that hasn't wired real message
    identity yet (see app/sources/base.py's own docstring on this) simply
    leaves these `None`, same as it always could before this field
    existed.

    `source_catalog_id` is a SEPARATE, later (Track 29) addition: a
    signal-copier `Provider`/`Source`/`Connection` catalog's own
    `sources.id` (see signal-copier's `app/provider_catalog.py` module
    docstring for the full model) -- the specific TRANSPORT a provider's
    alert arrived through, when that provider has more than one (e.g.
    the same real-world seller reached via both a Telegram channel and a
    Whop webhook: two distinct `sources` rows sharing one `providers`
    row). This is NOT the same axis as `source_provider_id` (which, pre-
    Track-14, already identifies the provider/collector by itself and is
    never renamed or removed here) nor `source_channel_id` (that
    provider's own native channel identity at the transport level, e.g.
    a Telegram channel id -- which can legitimately be the FULL identity
    for a single-source provider with no catalog entry at all). Purely
    additive and optional: `None` for every event this contract has ever
    exported before this field existed, and `None` still today for any
    producer whose adapter hasn't been wired to the Track 14 catalog --
    never fabricated or backfilled from `source_channel_id`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_provider_id: str
    source_channel_id: str | None = None
    analyst_id: str | None = None
    strategy_id: str | None = None
    parser_version: str
    source_event_id: str
    revision_id: str | None = None
    parent_event_id: str | None = None
    original_source_event_id: str | None = None
    source_catalog_id: str | None = None


class InstrumentIdentity(BaseModel):
    """S5: "Canonical instrument ID, venue, market type, contract version,
    currency, multiplier and quantity convention"."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    instrument_id: str
    venue: str
    market_type: str
    contract_version: str | None = None
    currency: str
    multiplier: Money
    quantity_convention: str


class PrivateAccountIdentity(BaseModel):
    """S5: "Private account binding, allocation ID, order/intent/family ID
    and lifecycle ID"."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    account_id: str
    allocation_id: str | None = None
    order_family_id: str | None = None
    intent_id: str | None = None
    lifecycle_id: str | None = None


class PortfolioIdentity(BaseModel):
    """S5: "Sleeve ID, portfolio ID/version, product ID and policy/
    calculation version"."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sleeve_id: str | None = None
    portfolio_id: str | None = None
    portfolio_version_id: str | None = None
    product_id: str | None = None
    policy_version_id: str | None = None


class CommercialIdentity(BaseModel):
    """S5: "Commercial tenant, verified user membership, platform
    connection, actual platform account/strategy, selection and mandate
    IDs"."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str
    user_id: str | None = None
    platform_connection_id: str | None = None
    platform_account_id: str | None = None
    selection_id: str | None = None
    mandate_id: str | None = None


class PublicationIdentity(BaseModel):
    """S5: "Publication ID, target cohort, external event/order identity
    and causation chain"."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    publication_id: str | None = None
    target_cohort_id: str | None = None
    external_event_id: str | None = None
    causation_chain_id: str | None = None


def build_subject(**clusters: BaseModel) -> dict[str, str]:
    """Flattens one or more `*Identity` clusters into the envelope's own
    `subject: dict[str, str]`, keyed `<cluster_name>.<field_name>` so two
    clusters sharing a field name (e.g. both carrying no `tenant_id`)
    never collide. A field left `None` on a cluster is omitted, never
    written as the literal string `"None"`.

    >>> build_subject(
    ...     source=SourceIdentity(source_provider_id="telegram", parser_version="v3", source_event_id="evt-1"),
    ...     account=PrivateAccountIdentity(account_id="acct1"),
    ... )
    {'source.source_provider_id': 'telegram', 'source.parser_version': 'v3', 'source.source_event_id': 'evt-1', 'account.account_id': 'acct1'}
    """
    subject: dict[str, str] = {}
    for cluster_name, identity in clusters.items():
        for field_name, value in identity.model_dump(mode="json", exclude_none=True).items():
            subject[f"{cluster_name}.{field_name}"] = str(value)
    return subject
