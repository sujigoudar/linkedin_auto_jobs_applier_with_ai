"""app/services/collective2_publisher.py -- builds (never sends) the API4
Order envelope for a PublicationIntent. Pure function tests, no database
needed."""
from datetime import datetime, timedelta, timezone

import pytest

from app.models.publication import (
    Environment,
    PublicationAction,
    PublicationIntent,
    QuantityBasis,
)
from app.services.collective2_publisher import (
    Side,
    Tif,
    UnresolvedInstrumentError,
    UnsupportedQuantityBasisError,
    UnsupportedTifError,
    build_order,
)


def _intent(**overrides) -> PublicationIntent:
    now = datetime.now(timezone.utc)
    defaults = dict(
        environment=Environment.LOCAL_SIM,
        portfolio_version_id="pv-1",
        episode_id="ep-1",
        revision=1,
        action=PublicationAction.OPEN,
        channel="collective2",
        external_strategy_id="strategy-1",
        instrument_id="AAPL",
        quantity="10",
        quantity_basis=QuantityBasis.UNITS,
        price_basis="market",
        policy_hash="policy-hash-1",
        audience_snapshot_hash="audience-hash-1",
        source_revision_ids=["src-rev-1"],
        rights_grant_ids=["grant-1"],
        body_hash="body-hash-1",
        idempotency_key="idem-1",
        valid_from=now,
        expires_at=now + timedelta(hours=1),
    )
    defaults.update(overrides)
    return PublicationIntent(**defaults)


def test_an_open_builds_a_buy_market_order():
    order = build_order(_intent(action=PublicationAction.OPEN), c2_symbol="AAPL")
    assert order["Side1"] == int(Side.BUY)
    assert order["OrderQuantity"] == 10
    assert order["TIF"] == int(Tif.DAY)
    assert order["C2Symbol"] == "AAPL"


def test_an_add_also_builds_a_buy_order():
    order = build_order(_intent(action=PublicationAction.ADD), c2_symbol="AAPL")
    assert order["Side1"] == int(Side.BUY)


def test_a_reduce_builds_a_sell_order():
    order = build_order(_intent(action=PublicationAction.REDUCE), c2_symbol="AAPL")
    assert order["Side1"] == int(Side.SELL)


def test_a_stop_update_builds_a_price_only_modification():
    order = build_order(_intent(action=PublicationAction.STOP_UPDATE, quantity=None), c2_symbol="AAPL")
    assert order["PriceOnlyModification"] is True
    assert "OrderQuantity" not in order


def test_a_target_upsert_builds_a_price_only_modification():
    order = build_order(_intent(action=PublicationAction.TARGET_UPSERT, quantity=None), c2_symbol="AAPL")
    assert order["PriceOnlyModification"] is True


def test_a_missing_c2_symbol_is_rejected_not_guessed():
    with pytest.raises(UnresolvedInstrumentError):
        build_order(_intent(), c2_symbol="")


def test_a_non_units_quantity_basis_is_rejected():
    with pytest.raises(UnsupportedQuantityBasisError):
        build_order(_intent(quantity_basis=QuantityBasis.TARGET_POSITION), c2_symbol="AAPL")


def test_a_fractional_quantity_is_rejected_not_rounded():
    with pytest.raises(UnsupportedQuantityBasisError):
        build_order(_intent(quantity="10.5"), c2_symbol="AAPL")


def test_a_missing_quantity_for_an_open_is_rejected():
    with pytest.raises(UnsupportedQuantityBasisError):
        build_order(_intent(quantity=None), c2_symbol="AAPL")


def test_an_action_with_no_mapping_yet_is_rejected_not_guessed():
    """CLOSE/ENTRY_CANCEL/etc. are not yet mapped -- an unmapped action
    must raise, never silently fall through to some default order shape."""
    with pytest.raises(UnsupportedQuantityBasisError):
        build_order(_intent(action=PublicationAction.CLOSE, quantity=None), c2_symbol="AAPL")


def test_tif_only_ever_produces_a_vendor_confirmed_value():
    order = build_order(_intent(), c2_symbol="AAPL")
    assert order["TIF"] in (int(Tif.DAY), int(Tif.GTC))


def test_tif_value_two_is_not_a_defined_enum_member():
    """The spec's own flagged documentation conflict (docs/06: "It
    documents TIF0 day/1 GTC, but one conditional example uses 2... not
    permission to infer 2") -- this build never emits or accepts it until
    real vendor confirmation exists."""
    with pytest.raises(ValueError):
        Tif(2)


def test_unsupported_tif_error_is_raised_for_an_out_of_range_tif(monkeypatch):
    import app.services.collective2_publisher as publisher_module

    monkeypatch.setattr(publisher_module, "_resolve_tif", lambda intent: 2)
    with pytest.raises(UnsupportedTifError):
        build_order(_intent(), c2_symbol="AAPL")
