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
