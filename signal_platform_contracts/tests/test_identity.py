"""build_subject's own tests -- INTEGRATION_DECISION.md S5: explicit
clusters, no name-based joins, no silent "None" leaking into a subject
key."""
import pytest
from pydantic import ValidationError

from signal_platform_contracts.identity import (
    CommercialIdentity,
    InstrumentIdentity,
    PrivateAccountIdentity,
    SourceIdentity,
    build_subject,
)


def test_build_subject_prefixes_each_field_by_its_cluster_name():
    subject = build_subject(
        source=SourceIdentity(
            source_provider_id="telegram", parser_version="v3", source_event_id="evt-1"
        ),
        account=PrivateAccountIdentity(account_id="acct1"),
    )
    assert subject == {
        "source.source_provider_id": "telegram",
        "source.parser_version": "v3",
        "source.source_event_id": "evt-1",
        "account.account_id": "acct1",
    }


def test_build_subject_omits_none_fields_rather_than_writing_the_string_none():
    subject = build_subject(source=SourceIdentity(source_provider_id="telegram", parser_version="v3", source_event_id="evt-1"))
    assert "source.analyst_id" not in subject
    assert "source.strategy_id" not in subject


def test_two_clusters_sharing_no_field_names_never_collide():
    subject = build_subject(
        source=SourceIdentity(source_provider_id="telegram", parser_version="v3", source_event_id="evt-1"),
        commercial=CommercialIdentity(tenant_id="tenant-a"),
    )
    assert subject["source.source_provider_id"] == "telegram"
    assert subject["commercial.tenant_id"] == "tenant-a"


def test_instrument_identity_requires_a_real_multiplier_not_a_float():
    instrument = InstrumentIdentity(
        instrument_id="AAPL", venue="NASDAQ", market_type="equity", currency="USD",
        multiplier="1", quantity_convention="shares",
    )
    assert str(instrument.multiplier) == "1"


def test_identity_models_are_frozen():
    identity = PrivateAccountIdentity(account_id="acct1")
    with pytest.raises(ValidationError):
        identity.account_id = "acct2"  # type: ignore[misc]


# -- Track 29: source_catalog_id, additive and optional ------------------


def test_source_catalog_id_defaults_to_none_and_is_omitted_from_the_subject():
    """Backward compatibility: a SourceIdentity built exactly as every
    pre-Track-29 caller always has (never setting source_catalog_id)
    must still construct and export identically -- no required field,
    no new key leaking into the subject when it's unset."""
    identity = SourceIdentity(source_provider_id="telegram", parser_version="v3", source_event_id="evt-1")
    assert identity.source_catalog_id is None
    subject = build_subject(source=identity)
    assert "source.source_catalog_id" not in subject


def test_source_catalog_id_round_trips_through_build_subject_and_model_dump():
    """A producer that DOES know a Track 14 catalog source (the new,
    additive case) carries it all the way through -- the subject binding
    and the model's own json dump both reflect the real value, and
    deserializing that dump reconstructs an identical SourceIdentity."""
    identity = SourceIdentity(
        source_provider_id="telegram",
        source_channel_id="chan-1",
        parser_version="v3",
        source_event_id="evt-1",
        source_catalog_id="catalog-source-42",
    )
    subject = build_subject(source=identity)
    assert subject["source.source_catalog_id"] == "catalog-source-42"

    dumped = identity.model_dump(mode="json")
    assert dumped["source_catalog_id"] == "catalog-source-42"
    reconstructed = SourceIdentity.model_validate(dumped)
    assert reconstructed == identity


def test_source_catalog_id_distinct_from_source_provider_id_and_source_channel_id():
    """The whole point of this field: one provider, reached via two
    different sources/transports, must be distinguishable -- same
    source_provider_id, different source_catalog_id."""
    source_a = SourceIdentity(
        source_provider_id="momentum_mike", source_channel_id="telegram-chan-1",
        parser_version="v3", source_event_id="evt-1", source_catalog_id="source-row-a",
    )
    source_b = SourceIdentity(
        source_provider_id="momentum_mike", source_channel_id="whop-webhook-1",
        parser_version="v3", source_event_id="evt-2", source_catalog_id="source-row-b",
    )
    assert source_a.source_provider_id == source_b.source_provider_id
    assert source_a.source_catalog_id != source_b.source_catalog_id
