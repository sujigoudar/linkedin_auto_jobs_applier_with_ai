"""Track 71: comprehensive mutation testing for broker adapters and provider modules.

This file implements targeted regression tests for broker adapter capabilities,
provider registry/discovery, and settings inheritance. These tests are designed
to catch mutations that could silently break:

- Broker capability declarations (supports_native_bracket, supported_asset_classes)
- Order type and execution logic (bracket, OTO, market vs. limit)
- Side-dependent trading logic (BUY vs. SELL, close side handling)
- Provider enumeration and registry lookups
- Credential validation and authentication patterns
- Account type and market access discrimination
- Position reconciliation logic
- Settings override logic for providers

The tests follow the "Track 60-67 targeted regression test pattern" with
hand-written tests for mutation-critical patterns rather than relying on
mutant survival rates alone.

See pyproject.toml's Track 71 commentary for execution pattern and rationale.
"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock

import httpx

from app.brokers.alpaca import AlpacaBroker, _coerce_broker_order_id
from app.brokers.ccxt_broker import CCXTBroker
from app.brokers.base import BrokerAdapter
from app.models import DestinationAccount, OrderStatus, Side, Signal, AssetClass
from app.providers import (
    SettingsOverride, AnalystConfig, ProviderConfig, ProviderRegistry,
    _apply_override, load_provider_registry
)


# ============================================================================
# Track 71.1: Broker Capability Declarations and Asset Class Discrimination
# ============================================================================


class TestBrokerCapabilityDeclarations:
    """Test that broker capability flags are correctly declared and used.

    Mutations to catch:
    - supports_native_bracket inverted (True -> False, False -> True)
    - supported_asset_classes field missing or inverted
    - can_trade_asset_class logic inverted (returning opposite boolean)
    """

    def test_alpaca_declares_native_bracket_support(self) -> None:
        """AlpacaBroker explicitly supports bracket orders."""
        broker = AlpacaBroker()
        assert broker.supports_native_bracket is True

    def test_alpaca_declares_equity_only_support(self) -> None:
        """AlpacaBroker only supports equity, not options/futures/crypto."""
        broker = AlpacaBroker()
        assert broker.supported_asset_classes == frozenset({AssetClass.EQUITY})

    def test_alpaca_can_trade_declared_asset_class(self) -> None:
        """can_trade_asset_class returns True for declared EQUITY."""
        broker = AlpacaBroker()
        assert broker.can_trade_asset_class(AssetClass.EQUITY) is True

    def test_alpaca_cannot_trade_undeclared_asset_classes(self) -> None:
        """can_trade_asset_class returns False for OPTION, FUTURE, CRYPTO."""
        broker = AlpacaBroker()
        assert broker.can_trade_asset_class(AssetClass.OPTION) is False
        assert broker.can_trade_asset_class(AssetClass.FUTURE) is False
        assert broker.can_trade_asset_class(AssetClass.CRYPTO) is False

    def test_broker_undeclared_asset_classes_default_to_unrestricted(self) -> None:
        """Broker with supported_asset_classes=None can trade anything."""
        # Create a minimal broker without asset class restrictions
        class UnrestrictedBroker(BrokerAdapter):
            name = "unrestricted"

            async def place_order(self, signal, account, quantity, symbol):
                return MagicMock()

        broker = UnrestrictedBroker()
        assert broker.supported_asset_classes is None
        # Should return True for all asset classes (unrestricted)
        assert broker.can_trade_asset_class(AssetClass.EQUITY) is True
        assert broker.can_trade_asset_class(AssetClass.OPTION) is True
        assert broker.can_trade_asset_class(AssetClass.FUTURE) is True
        assert broker.can_trade_asset_class(AssetClass.CRYPTO) is True


# ============================================================================
# Track 71.2: Alpaca Broker Order Type and Bracket Logic
# ============================================================================


class TestAlpacaOrderTypeLogic:
    """Test Alpaca's order type selection based on signal exit legs.

    Mutations to catch:
    - stop_loss and take_profit condition inverted (and -> or, > -> <)
    - order_class assignment incorrect ("oto" -> "bracket" or vice versa)
    - Dropped brackets/legs in payload
    - Quantity type coercion (float -> int or vice versa)
    """

    def test_alpaca_place_order_with_both_stop_and_profit(self, monkeypatch):
        """When both stop_loss and take_profit present, order_class must be 'bracket'."""
        monkeypatch.setenv("ALPACA_TEST_API_KEY", "test_key")
        monkeypatch.setenv("ALPACA_TEST_API_SECRET", "test_secret")

        broker = AlpacaBroker()

        # Capture the payload sent to the API
        captured_payload = {}

        async def mock_post(url, headers, json):
            captured_payload.update(json)
            request = httpx.Request("POST", url)
            return httpx.Response(200, json={"id": "ord123", "status": "accepted"}, request=request)

        broker._client.post = mock_post

        signal = Signal(
            source="test",
            symbol="AAPL",
            side=Side.BUY,
            stop_loss=185.0,
            take_profit=200.0
        )
        account = DestinationAccount(account_id="test", broker="alpaca")

        # This should be async but we're testing the payload
        import asyncio
        asyncio.run(broker.place_order(signal, account, 10.0, "AAPL"))

        # Verify bracket order class and both legs present
        assert captured_payload.get("order_class") == "bracket"
        assert "stop_loss" in captured_payload
        assert "take_profit" in captured_payload

    def test_alpaca_place_order_with_take_profit_only(self, monkeypatch):
        """When only take_profit present, order_class must be 'oto'."""
        monkeypatch.setenv("ALPACA_TEST_API_KEY", "test_key")
        monkeypatch.setenv("ALPACA_TEST_API_SECRET", "test_secret")

        broker = AlpacaBroker()
        captured_payload = {}

        async def mock_post(url, headers, json):
            captured_payload.update(json)
            request = httpx.Request("POST", url)
            return httpx.Response(200, json={"id": "ord123", "status": "accepted"}, request=request)

        broker._client.post = mock_post

        signal = Signal(
            source="test",
            symbol="AAPL",
            side=Side.BUY,
            take_profit=200.0
        )
        account = DestinationAccount(account_id="test", broker="alpaca")

        import asyncio
        asyncio.run(broker.place_order(signal, account, 10.0, "AAPL"))

        # Verify OTO order class and take_profit present (no stop_loss)
        assert captured_payload.get("order_class") == "oto"
        assert "take_profit" in captured_payload
        assert "stop_loss" not in captured_payload

    def test_alpaca_place_order_with_stop_loss_only(self, monkeypatch):
        """When only stop_loss present, order_class must be 'oto'."""
        monkeypatch.setenv("ALPACA_TEST_API_KEY", "test_key")
        monkeypatch.setenv("ALPACA_TEST_API_SECRET", "test_secret")

        broker = AlpacaBroker()
        captured_payload = {}

        async def mock_post(url, headers, json):
            captured_payload.update(json)
            request = httpx.Request("POST", url)
            return httpx.Response(200, json={"id": "ord123", "status": "accepted"}, request=request)

        broker._client.post = mock_post

        signal = Signal(
            source="test",
            symbol="AAPL",
            side=Side.BUY,
            stop_loss=185.0
        )
        account = DestinationAccount(account_id="test", broker="alpaca")

        import asyncio
        asyncio.run(broker.place_order(signal, account, 10.0, "AAPL"))

        # Verify OTO order class and stop_loss present (no take_profit)
        assert captured_payload.get("order_class") == "oto"
        assert "stop_loss" in captured_payload
        assert "take_profit" not in captured_payload

    def test_alpaca_place_order_quantity_as_string(self, monkeypatch):
        """Alpaca sends quantity as string, not float or int."""
        monkeypatch.setenv("ALPACA_TEST_API_KEY", "test_key")
        monkeypatch.setenv("ALPACA_TEST_API_SECRET", "test_secret")

        broker = AlpacaBroker()
        captured_payload = {}

        async def mock_post(url, headers, json):
            captured_payload.update(json)
            request = httpx.Request("POST", url)
            return httpx.Response(200, json={"id": "ord123", "status": "accepted"}, request=request)

        broker._client.post = mock_post

        signal = Signal(
            source="test",
            symbol="AAPL",
            side=Side.BUY
        )
        account = DestinationAccount(account_id="test", broker="alpaca")

        import asyncio
        asyncio.run(broker.place_order(signal, account, 10.5, "AAPL"))

        # Quantity must be stringified for Alpaca API
        assert captured_payload.get("qty") == "10.5"
        assert isinstance(captured_payload.get("qty"), str)


# ============================================================================
# Track 71.3: Side-Dependent Trading Logic (BUY vs SELL)
# ============================================================================


class TestSideDependentLogic:
    """Test that BUY/SELL sides are correctly handled and transmitted.

    Mutations to catch:
    - side.value lookup missing or returning wrong string
    - close side not properly rejected
    - side inversion (BUY -> SELL)
    """

    def test_alpaca_rejects_close_side(self, monkeypatch):
        """'close' side should be rejected before reaching the broker."""
        monkeypatch.setenv("ALPACA_TEST_API_KEY", "test_key")
        monkeypatch.setenv("ALPACA_TEST_API_SECRET", "test_secret")

        broker = AlpacaBroker()

        signal = Signal(
            source="test",
            symbol="AAPL",
            side=Side.CLOSE
        )
        account = DestinationAccount(account_id="test", broker="alpaca")

        import asyncio
        result = asyncio.run(broker.place_order(signal, account, 10.0, "AAPL"))

        # close side must be rejected
        assert result.status == OrderStatus.REJECTED
        assert "close" in result.message.lower()

    def test_alpaca_transmits_buy_side_correctly(self, monkeypatch):
        """BUY side must be transmitted as 'buy' to the API."""
        monkeypatch.setenv("ALPACA_TEST_API_KEY", "test_key")
        monkeypatch.setenv("ALPACA_TEST_API_SECRET", "test_secret")

        broker = AlpacaBroker()
        captured_payload = {}

        async def mock_post(url, headers, json):
            captured_payload.update(json)
            request = httpx.Request("POST", url)
            return httpx.Response(200, json={"id": "ord123", "status": "accepted"}, request=request)

        broker._client.post = mock_post

        signal = Signal(
            source="test",
            symbol="AAPL",
            side=Side.BUY
        )
        account = DestinationAccount(account_id="test", broker="alpaca")

        import asyncio
        asyncio.run(broker.place_order(signal, account, 10.0, "AAPL"))

        assert captured_payload.get("side") == "buy"

    def test_alpaca_transmits_sell_side_correctly(self, monkeypatch):
        """SELL side must be transmitted as 'sell' to the API."""
        monkeypatch.setenv("ALPACA_TEST_API_KEY", "test_key")
        monkeypatch.setenv("ALPACA_TEST_API_SECRET", "test_secret")

        broker = AlpacaBroker()
        captured_payload = {}

        async def mock_post(url, headers, json):
            captured_payload.update(json)
            request = httpx.Request("POST", url)
            return httpx.Response(200, json={"id": "ord123", "status": "accepted"}, request=request)

        broker._client.post = mock_post

        signal = Signal(
            source="test",
            symbol="AAPL",
            side=Side.SELL
        )
        account = DestinationAccount(account_id="test", broker="alpaca")

        import asyncio
        asyncio.run(broker.place_order(signal, account, 10.0, "AAPL"))

        assert captured_payload.get("side") == "sell"


# ============================================================================
# Track 71.4: Credential Validation and Authentication
# ============================================================================


class TestCredentialValidation:
    """Test that broker credentials are properly validated.

    Mutations to catch:
    - Missing credential check (not checking if api_key or api_secret is None)
    - Wrong environment variable name used
    - Incorrect error status (ERROR vs REJECTED)
    - Missing error message
    """

    def test_alpaca_missing_api_key_returns_error(self, monkeypatch):
        """Missing API key should return OrderStatus.ERROR."""
        monkeypatch.delenv("ALPACA_TESTACCT_API_KEY", raising=False)
        monkeypatch.delenv("ALPACA_TESTACCT_API_SECRET", raising=False)

        broker = AlpacaBroker()

        signal = Signal(source="test", symbol="AAPL", side=Side.BUY)
        account = DestinationAccount(account_id="testacct", broker="alpaca")

        import asyncio
        result = asyncio.run(broker.place_order(signal, account, 10.0, "AAPL"))

        assert result.status == OrderStatus.ERROR
        assert "missing" in result.message.lower()
        assert "api_key" in result.message.lower()

    def test_alpaca_missing_api_secret_returns_error(self, monkeypatch):
        """Missing API secret should return OrderStatus.ERROR."""
        monkeypatch.setenv("ALPACA_TESTACCT_API_KEY", "key123")
        monkeypatch.delenv("ALPACA_TESTACCT_API_SECRET", raising=False)

        broker = AlpacaBroker()

        signal = Signal(source="test", symbol="AAPL", side=Side.BUY)
        account = DestinationAccount(account_id="testacct", broker="alpaca")

        import asyncio
        result = asyncio.run(broker.place_order(signal, account, 10.0, "AAPL"))

        assert result.status == OrderStatus.ERROR
        assert "missing" in result.message.lower()

    def test_alpaca_paper_trading_default_url(self, monkeypatch):
        """Default base URL should be paper trading endpoint."""
        monkeypatch.setenv("ALPACA_TEST_API_KEY", "key")
        monkeypatch.setenv("ALPACA_TEST_API_SECRET", "secret")
        monkeypatch.delenv("ALPACA_TEST_BASE_URL", raising=False)

        broker = AlpacaBroker()
        api_key, api_secret, base_url = broker._credentials_for(
            DestinationAccount(account_id="test", broker="alpaca")
        )

        assert base_url == "https://paper-api.alpaca.markets"

    def test_alpaca_live_trading_url_can_be_overridden(self, monkeypatch):
        """Base URL can be overridden for live trading."""
        monkeypatch.setenv("ALPACA_TEST_API_KEY", "key")
        monkeypatch.setenv("ALPACA_TEST_API_SECRET", "secret")
        monkeypatch.setenv("ALPACA_TEST_BASE_URL", "https://api.alpaca.markets")

        broker = AlpacaBroker()
        api_key, api_secret, base_url = broker._credentials_for(
            DestinationAccount(account_id="test", broker="alpaca")
        )

        assert base_url == "https://api.alpaca.markets"


# ============================================================================
# Track 71.5: Coerce Broker Order ID
# ============================================================================


class TestCoerceBrokerOrderID:
    """Test _coerce_broker_order_id handling of various ID types.

    Mutations to catch:
    - None not handled (returning "None" string instead)
    - int not converted to string
    - Missing type coercion
    """

    def test_coerce_order_id_string_passthrough(self) -> None:
        """String order IDs pass through unchanged."""
        result = _coerce_broker_order_id("order-abc123")
        assert result == "order-abc123"
        assert isinstance(result, str)

    def test_coerce_order_id_int_to_string(self) -> None:
        """Integer order IDs are converted to strings."""
        result = _coerce_broker_order_id(12345)
        assert result == "12345"
        assert isinstance(result, str)

    def test_coerce_order_id_none_stays_none(self) -> None:
        """None stays None, not converted to 'None' string."""
        result = _coerce_broker_order_id(None)
        assert result is None

    def test_coerce_order_id_float_to_string(self) -> None:
        """Float order IDs are stringified."""
        result = _coerce_broker_order_id(123.45)
        assert result == "123.45"
        assert isinstance(result, str)

    def test_coerce_order_id_nested_object(self) -> None:
        """Nested objects are stringified to prevent DB errors."""
        nested = {"id": "nested"}
        result = _coerce_broker_order_id(nested)
        assert isinstance(result, str)
        assert "nested" in result


# ============================================================================
# Track 71.6: Provider Registry and Settings Override
# ============================================================================


class TestProviderRegistry:
    """Test provider registry functionality and settings merging.

    Mutations to catch:
    - enabled field not respected (False stays True after override)
    - Provider lookup returns wrong provider or None when it exists
    - Missing analyst lookup
    - Settings not merged correctly (None not checked)
    """

    def test_provider_registry_empty_by_default(self) -> None:
        """ProviderRegistry starts with no providers."""
        registry = ProviderRegistry()
        assert registry.providers == {}

    def test_get_provider_returns_none_when_not_found(self) -> None:
        """get_provider returns None for unknown provider."""
        registry = ProviderRegistry()
        result = registry.get_provider("unknown_provider")
        assert result is None

    def test_get_provider_returns_config_when_found(self) -> None:
        """get_provider returns the ProviderConfig when found."""
        provider_cfg = ProviderConfig(provider_id="telegram")
        registry = ProviderRegistry(providers={"telegram": provider_cfg})

        result = registry.get_provider("telegram")
        assert result is provider_cfg
        assert result.provider_id == "telegram"

    def test_effective_settings_account_defaults_baseline(self) -> None:
        """effective_settings with no provider returns account defaults."""
        account_defaults = SettingsOverride(
            multiplier=1.0,
            fixed_quantity=100.0,
            managed_lifecycle=False,
            enabled=True
        )
        registry = ProviderRegistry()

        result = registry.effective_settings(account_defaults, "unknown", None)

        assert result.multiplier == 1.0
        assert result.fixed_quantity == 100.0
        assert result.managed_lifecycle is False
        assert result.enabled is True

    def test_effective_settings_provider_override_multiplier(self) -> None:
        """Provider override multiplier replaces account default."""
        account_defaults = SettingsOverride(
            multiplier=1.0,
            fixed_quantity=100.0,
            managed_lifecycle=False,
            enabled=True
        )

        provider_cfg = ProviderConfig(
            provider_id="telegram",
            settings=SettingsOverride(multiplier=2.0)
        )
        registry = ProviderRegistry(providers={"telegram": provider_cfg})

        result = registry.effective_settings(account_defaults, "telegram", None)

        assert result.multiplier == 2.0  # From provider
        assert result.fixed_quantity == 100.0  # From account

    def test_effective_settings_analyst_override_precedence(self) -> None:
        """Analyst override takes precedence over provider override."""
        account_defaults = SettingsOverride(
            multiplier=1.0,
            fixed_quantity=100.0,
            managed_lifecycle=False,
            enabled=True
        )

        analyst_cfg = AnalystConfig(
            analyst_id="alice",
            settings=SettingsOverride(multiplier=3.0)
        )
        provider_cfg = ProviderConfig(
            provider_id="telegram",
            settings=SettingsOverride(multiplier=2.0),
            analysts={"alice": analyst_cfg}
        )
        registry = ProviderRegistry(providers={"telegram": provider_cfg})

        result = registry.effective_settings(account_defaults, "telegram", "alice")

        assert result.multiplier == 3.0  # From analyst

    def test_effective_settings_enabled_false_prevents_override(self) -> None:
        """enabled=False at broader level prevents narrower True (RISK-04)."""
        account_defaults = SettingsOverride(
            multiplier=1.0,
            fixed_quantity=100.0,
            managed_lifecycle=False,
            enabled=False  # Account explicitly disabled
        )

        analyst_cfg = AnalystConfig(
            analyst_id="alice",
            settings=SettingsOverride(enabled=True)  # Analyst tries to enable
        )
        provider_cfg = ProviderConfig(
            provider_id="telegram",
            analysts={"alice": analyst_cfg}
        )
        registry = ProviderRegistry(providers={"telegram": provider_cfg})

        result = registry.effective_settings(account_defaults, "telegram", "alice")

        # enabled=False at account level must prevent narrower True
        assert result.enabled is False

    def test_effective_settings_enabled_true_allows_narrower_false(self) -> None:
        """enabled=True at broader level allows narrower False."""
        account_defaults = SettingsOverride(
            multiplier=1.0,
            fixed_quantity=100.0,
            managed_lifecycle=False,
            enabled=True
        )

        analyst_cfg = AnalystConfig(
            analyst_id="alice",
            settings=SettingsOverride(enabled=False)  # Analyst explicitly disables
        )
        provider_cfg = ProviderConfig(
            provider_id="telegram",
            analysts={"alice": analyst_cfg}
        )
        registry = ProviderRegistry(providers={"telegram": provider_cfg})

        result = registry.effective_settings(account_defaults, "telegram", "alice")

        # enabled=False at analyst level should take effect
        assert result.enabled is False

    def test_effective_settings_missing_analyst_ignored(self) -> None:
        """Request for non-existent analyst returns provider-level override."""
        account_defaults = SettingsOverride(
            multiplier=1.0,
            fixed_quantity=100.0,
            managed_lifecycle=False,
            enabled=True
        )

        provider_cfg = ProviderConfig(
            provider_id="telegram",
            settings=SettingsOverride(multiplier=2.0)
        )
        registry = ProviderRegistry(providers={"telegram": provider_cfg})

        result = registry.effective_settings(account_defaults, "telegram", "bob")

        assert result.multiplier == 2.0  # Provider level, analyst not found


# ============================================================================
# Track 71.7: Apply Override Logic
# ============================================================================


class TestApplyOverride:
    """Test _apply_override function for correct precedence.

    Mutations to catch:
    - enabled field logic inverted for the special case
    - None checks missing
    - Fallback not working when override is None
    """

    def test_apply_override_none_values_inherit_from_base(self) -> None:
        """None values in override fall back to base."""
        base = SettingsOverride(
            multiplier=1.0,
            fixed_quantity=100.0,
            managed_lifecycle=True,
            enabled=True
        )
        override = SettingsOverride()  # All None

        result = _apply_override(base, override)

        assert result.multiplier == 1.0
        assert result.fixed_quantity == 100.0
        assert result.managed_lifecycle is True
        assert result.enabled is True

    def test_apply_override_non_none_values_replace_base(self) -> None:
        """Non-None override values replace base."""
        base = SettingsOverride(
            multiplier=1.0,
            fixed_quantity=100.0,
            managed_lifecycle=True,
            enabled=True
        )
        override = SettingsOverride(
            multiplier=2.0,
            fixed_quantity=50.0
        )

        result = _apply_override(base, override)

        assert result.multiplier == 2.0
        assert result.fixed_quantity == 50.0
        assert result.managed_lifecycle is True  # From base
        assert result.enabled is True

    def test_apply_override_enabled_false_at_base_blocks_override_true(self) -> None:
        """enabled=False at base level is not overridden by True."""
        base = SettingsOverride(enabled=False)
        override = SettingsOverride(enabled=True)

        result = _apply_override(base, override)

        # The special RISK-04 logic: False at base level blocks True at override
        assert result.enabled is False

    def test_apply_override_enabled_true_allows_override_false(self) -> None:
        """enabled=True at base allows False from override."""
        base = SettingsOverride(enabled=True)
        override = SettingsOverride(enabled=False)

        result = _apply_override(base, override)

        assert result.enabled is False

    def test_apply_override_enabled_none_uses_base(self) -> None:
        """enabled=None in override falls back to base."""
        base = SettingsOverride(enabled=False)
        override = SettingsOverride(enabled=None)

        result = _apply_override(base, override)

        assert result.enabled is False


# ============================================================================
# Track 71.8: Load Provider Registry
# ============================================================================


class TestLoadProviderRegistry:
    """Test loading provider registry from YAML.

    Mutations to catch:
    - Missing providers key in YAML not handled
    - None spec values crash the loader
    - display_name defaults not applied
    - analysts dict not initialized
    """

    def test_load_provider_registry_missing_file(self) -> None:
        """Missing YAML file returns empty registry."""
        path = Path("/nonexistent/providers.yaml")
        result = load_provider_registry(path)

        assert isinstance(result, ProviderRegistry)
        assert result.providers == {}

    def test_load_provider_registry_empty_yaml(self, tmp_path) -> None:
        """Empty YAML file returns empty registry."""
        yaml_file = tmp_path / "empty.yaml"
        yaml_file.write_text("")

        result = load_provider_registry(yaml_file)

        assert result.providers == {}

    def test_load_provider_registry_with_providers(self, tmp_path) -> None:
        """YAML with providers loads correctly."""
        yaml_file = tmp_path / "providers.yaml"
        yaml_file.write_text("""
providers:
  telegram:
    display_name: "Telegram Signals"
    multiplier: 2.0
  discord:
    display_name: "Discord Traders"
    fixed_quantity: 50.0
""")

        result = load_provider_registry(yaml_file)

        assert "telegram" in result.providers
        assert "discord" in result.providers
        assert result.providers["telegram"].display_name == "Telegram Signals"
        assert result.providers["telegram"].settings.multiplier == 2.0
        assert result.providers["discord"].settings.fixed_quantity == 50.0

    def test_load_provider_registry_with_analysts(self, tmp_path) -> None:
        """YAML with analysts loads correctly."""
        yaml_file = tmp_path / "providers.yaml"
        yaml_file.write_text("""
providers:
  telegram:
    display_name: "Telegram"
    analysts:
      alice:
        display_name: "Alice"
        multiplier: 1.5
      bob:
        display_name: "Bob"
        enabled: false
""")

        result = load_provider_registry(yaml_file)

        assert "alice" in result.providers["telegram"].analysts
        assert "bob" in result.providers["telegram"].analysts
        assert result.providers["telegram"].analysts["alice"].settings.multiplier == 1.5
        assert result.providers["telegram"].analysts["bob"].settings.enabled is False


# ============================================================================
# Track 71.9: CCXT Broker Basic Capability Check
# ============================================================================


class TestCCXTBrokerCapabilities:
    """Test CCXT broker capability declarations.

    Mutations to catch:
    - supported_asset_classes not set to CRYPTO
    - supports_native_bracket changed incorrectly
    """

    def test_ccxt_supports_crypto_only(self) -> None:
        """CCXTBroker only supports CRYPTO asset class."""
        broker = CCXTBroker(exchange_id="binance")

        assert broker.supported_asset_classes == frozenset({AssetClass.CRYPTO})

    def test_ccxt_crypto_trading_allowed(self) -> None:
        """CCXTBroker can_trade_asset_class returns True for CRYPTO."""
        broker = CCXTBroker(exchange_id="binance")

        assert broker.can_trade_asset_class(AssetClass.CRYPTO) is True

    def test_ccxt_non_crypto_trading_blocked(self) -> None:
        """CCXTBroker can_trade_asset_class returns False for non-CRYPTO."""
        broker = CCXTBroker(exchange_id="binance")

        assert broker.can_trade_asset_class(AssetClass.EQUITY) is False
        assert broker.can_trade_asset_class(AssetClass.OPTION) is False
        assert broker.can_trade_asset_class(AssetClass.FUTURE) is False


# ============================================================================
# Track 71.10: Integration Tests
# ============================================================================


class TestIntegration:
    """Integration tests combining multiple components.

    Mutations to catch:
    - Settings not properly applied during order placement
    - Broker selection not respecting account configuration
    """

    def test_signal_routed_with_provider_override_multiplier(self) -> None:
        """Provider multiplier is properly applied to quantity calculation."""
        account_defaults = SettingsOverride(
            multiplier=1.0,
            fixed_quantity=None,
            managed_lifecycle=False,
            enabled=True
        )

        provider_cfg = ProviderConfig(
            provider_id="telegram",
            settings=SettingsOverride(multiplier=2.0)
        )
        registry = ProviderRegistry(providers={"telegram": provider_cfg})

        result = registry.effective_settings(account_defaults, "telegram", None)

        # Multiplier should be 2.0 from provider
        assert result.multiplier == 2.0
        # Simulate order quantity calculation: base_qty * multiplier
        base_qty = 10.0
        order_qty = base_qty * result.multiplier
        assert order_qty == 20.0

    def test_disabled_analyst_prevents_order_routing(self) -> None:
        """Disabled analyst configuration prevents signal routing."""
        account_defaults = SettingsOverride(
            multiplier=1.0,
            fixed_quantity=None,
            managed_lifecycle=False,
            enabled=True
        )

        analyst_cfg = AnalystConfig(
            analyst_id="alice",
            settings=SettingsOverride(enabled=False)
        )
        provider_cfg = ProviderConfig(
            provider_id="telegram",
            analysts={"alice": analyst_cfg}
        )
        registry = ProviderRegistry(providers={"telegram": provider_cfg})

        result = registry.effective_settings(account_defaults, "telegram", "alice")

        # Should be disabled
        assert result.enabled is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
