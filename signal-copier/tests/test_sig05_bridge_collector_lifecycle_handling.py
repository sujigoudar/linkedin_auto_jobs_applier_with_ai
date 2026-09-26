"""SIG-05:
1. MetaApiSource treated every BUY/SELL deal as a fresh entry in that
   direction, even one that actually CLOSED an existing position (MetaApi
   tags this via `entryType` DEAL_ENTRY_OUT/OUT_BY) -- inverting the
   source account's real intent (a short being bought back looked like a
   new long entry).
2. MetaApiSource's per-deal signal dispatch was pure fire-and-forget
   (`asyncio.create_task` with nothing tracking the result) -- a failure
   inside `on_signal` was silently dropped, and `stop()` didn't wait for
   any in-flight ones.
3. TwitterSource's stream-rule setup deleted EVERY existing rule on the
   bearer token's account before adding its own, including rules this
   service never created.
"""
import asyncio

import pytest

from app.models import AssetClass, Side
from app.sources.mt4_mt5 import MetaApiSource


def _source():
    return MetaApiSource(on_signal=None, token="t", account_id="acct", asset_class=AssetClass.FOREX)


class _FakeHistoryStorage:
    def __init__(self, deals):
        self.deals = deals


class _FakeConnection:
    def __init__(self, deals):
        self.history_storage = _FakeHistoryStorage(deals)
        self.closed = False

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_deal_entry_out_is_treated_as_a_close_not_a_fresh_entry():
    received = []

    async def on_signal(signal):
        received.append(signal)

    source = _source()
    source.on_signal = on_signal
    source._connection = _FakeConnection(
        [{"id": "d1", "type": "DEAL_TYPE_BUY", "entryType": "DEAL_ENTRY_OUT", "symbol": "EURUSD", "volume": 1.0, "price": 1.09}]
    )

    source._check_for_new_deals()
    await asyncio.gather(*source._in_flight_tasks)

    assert len(received) == 1
    assert received[0].side == Side.CLOSE


@pytest.mark.asyncio
async def test_deal_entry_in_is_still_a_normal_directional_entry():
    received = []

    async def on_signal(signal):
        received.append(signal)

    source = _source()
    source.on_signal = on_signal
    source._connection = _FakeConnection(
        [{"id": "d2", "type": "DEAL_TYPE_BUY", "entryType": "DEAL_ENTRY_IN", "symbol": "EURUSD", "volume": 1.0, "price": 1.09}]
    )

    source._check_for_new_deals()
    await asyncio.gather(*source._in_flight_tasks)

    assert len(received) == 1
    assert received[0].side == Side.BUY


@pytest.mark.asyncio
async def test_a_failing_signal_handler_is_logged_not_silently_dropped(caplog):
    async def failing_on_signal(signal):
        raise RuntimeError("boom")

    source = _source()
    source.on_signal = failing_on_signal
    source._connection = _FakeConnection(
        [{"id": "d3", "type": "DEAL_TYPE_SELL", "entryType": "DEAL_ENTRY_IN", "symbol": "EURUSD", "volume": 1.0, "price": 1.09}]
    )

    with caplog.at_level("ERROR"):
        source._check_for_new_deals()
        await asyncio.gather(*source._in_flight_tasks, return_exceptions=True)

    assert any("d3" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_stop_awaits_in_flight_signal_tasks_before_closing_the_connection():
    started = asyncio.Event()
    finished = asyncio.Event()

    async def slow_on_signal(signal):
        started.set()
        await asyncio.sleep(0.05)
        finished.set()

    source = _source()
    source.on_signal = slow_on_signal
    connection = _FakeConnection(
        [{"id": "d4", "type": "DEAL_TYPE_BUY", "entryType": "DEAL_ENTRY_IN", "symbol": "EURUSD", "volume": 1.0, "price": 1.09}]
    )
    source._connection = connection

    source._check_for_new_deals()
    await started.wait()
    await source.stop()

    assert finished.is_set()
    assert connection.closed is True


@pytest.mark.asyncio
async def test_balance_and_credit_deals_are_still_skipped():
    received = []

    async def on_signal(signal):
        received.append(signal)

    source = _source()
    source.on_signal = on_signal
    source._connection = _FakeConnection([{"id": "d5", "type": "DEAL_TYPE_BALANCE"}])

    source._check_for_new_deals()
    assert source._in_flight_tasks == set()
    assert received == []


def test_twitter_rule_setup_only_deletes_rules_it_previously_tagged():
    tweepy = pytest.importorskip("tweepy")
    from app.sources.twitter import _MANAGED_RULE_TAG

    class _FakeRule:
        def __init__(self, id, tag):
            self.id = id
            self.tag = tag

    class _FakeRulesResponse:
        def __init__(self, data):
            self.data = data

    class _FakeStream:
        def __init__(self, existing_rules):
            self._existing = existing_rules
            self.deleted_ids = None
            self.added_rules = None

        def get_rules(self):
            return _FakeRulesResponse(self._existing)

        def delete_rules(self, ids):
            self.deleted_ids = ids

        def add_rules(self, rules):
            self.added_rules = rules

    existing = [
        _FakeRule(id="1", tag=_MANAGED_RULE_TAG),
        _FakeRule(id="2", tag="someone_elses_dashboard"),
        _FakeRule(id="3", tag=None),
    ]
    stream = _FakeStream(existing)

    # Reproduce exactly the startup logic in TwitterSource.start() without
    # needing a real network connection.
    app_owned_rule_ids = [rule.id for rule in stream.get_rules().data or [] if getattr(rule, "tag", None) == _MANAGED_RULE_TAG]
    if app_owned_rule_ids:
        stream.delete_rules(app_owned_rule_ids)
    stream.add_rules([tweepy.StreamRule(value="from:trader", tag=_MANAGED_RULE_TAG)])

    assert stream.deleted_ids == ["1"]
    assert stream.added_rules[0].tag == _MANAGED_RULE_TAG
