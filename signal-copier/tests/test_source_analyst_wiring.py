"""Each pull-based text source's parse() must accept and pass through an
analyst identifier -- this is what makes app/providers.py's per-analyst
overrides reachable from a real signal instead of only from a
hand-constructed Signal in a unit test. Only the parse() wrapper is
exercised here (not the bot-library message handlers themselves, which
need discord.py/python-telegram-bot/slack-bolt/tweepy installed and
aren't covered by any existing test either)."""
from app.sources.discord import DiscordSource
from app.sources.slack import SlackSource
from app.sources.telegram import TelegramSource
from app.sources.twitter import TwitterSource


def test_discord_source_parse_passes_analyst_through():
    source = DiscordSource(on_signal=None, bot_token="x", channel_id=1)
    signal = source.parse("BUY BTCUSDT", analyst="SomeTrader#1234")
    assert signal.analyst == "SomeTrader#1234"


def test_telegram_source_parse_passes_analyst_through():
    source = TelegramSource(on_signal=None, bot_token="x", chat_id=1)
    signal = source.parse("BUY BTCUSDT", analyst="alice")
    assert signal.analyst == "alice"


def test_slack_source_parse_passes_analyst_through():
    source = SlackSource(on_signal=None, bot_token="x", app_token="y", channel_id="C1")
    signal = source.parse("BUY BTCUSDT", analyst="U123ABC")
    assert signal.analyst == "U123ABC"


def test_twitter_source_parse_passes_analyst_through():
    source = TwitterSource(on_signal=None, bearer_token="x")
    signal = source.parse("BUY BTCUSDT", analyst="9876543210")
    assert signal.analyst == "9876543210"
