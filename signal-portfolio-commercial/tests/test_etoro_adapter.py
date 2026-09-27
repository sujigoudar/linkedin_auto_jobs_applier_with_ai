"""app/services/etoro_adapter.py -- builds (never sends) an eToro trade
request. Pure function tests, no database needed."""
from datetime import datetime, timedelta, timezone

import pytest

from app.models.publication import (
    Environment,
    PublicationAction,
    PublicationIntent,
    QuantityBasis,
)
from app.services.etoro_adapter import (
    MissingPositionIdError,
    UnsupportedAccountModeError,
    UnsupportedActionError,
    UnsupportedQuantityError,
    build_trade_request,
)


def _intent(**overrides) -> PublicationIntent:
    now = datetime.now(timezone.utc)
    defaults = dict(
        environment=Environment.LOCAL_SIM,
        portfolio_version_id="pv-1",
        episode_id="ep-1",
        revision=1,
        action=PublicationAction.OPEN,
        channel="etoro",
        external_strategy_id="strategy-1",
        instrument_id="AAPL",
        quantity="100",
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


def test_a_real_account_mode_is_refused_outright():
    with pytest.raises(UnsupportedAccountModeError):
        build_trade_request(_intent(), instrument_symbol="AAPL", account_mode="real")


def test_an_open_builds_a_buy_request():
    request = build_trade_request(_intent(action=PublicationAction.OPEN), instrument_symbol="AAPL")
    assert request == {"instrument": "AAPL", "direction": "BUY", "amount": "100"}


def test_a_reduce_without_a_position_id_is_rejected():
    with pytest.raises(MissingPositionIdError):
        build_trade_request(_intent(action=PublicationAction.REDUCE), instrument_symbol="AAPL")


def test_a_close_without_a_position_id_is_rejected():
    with pytest.raises(MissingPositionIdError):
        build_trade_request(_intent(action=PublicationAction.CLOSE, quantity=None), instrument_symbol="AAPL")


def test_a_close_with_a_position_id_is_a_full_close_not_a_generic_sell():
    request = build_trade_request(
        _intent(action=PublicationAction.CLOSE, quantity=None), instrument_symbol="AAPL", position_id="pos-1"
    )
    assert request == {"positionId": "pos-1", "fullClose": True}
    assert "instrument" not in request


def test_a_reduce_with_a_position_id_is_a_partial_close():
    request = build_trade_request(
        _intent(action=PublicationAction.REDUCE, quantity="30"), instrument_symbol="AAPL", position_id="pos-1"
    )
    assert request == {"positionId": "pos-1", "fullClose": False, "amount": "30"}


def test_an_unmapped_action_is_rejected():
    with pytest.raises(UnsupportedActionError):
        build_trade_request(_intent(action=PublicationAction.STOP_UPDATE, quantity=None), instrument_symbol="AAPL")


def test_a_non_positive_open_amount_is_rejected():
    with pytest.raises(UnsupportedQuantityError):
        build_trade_request(_intent(quantity="0"), instrument_symbol="AAPL")
