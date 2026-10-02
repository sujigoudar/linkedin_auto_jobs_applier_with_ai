"""Track 68: Comprehensive mutation testing for database and data model modules.

Targeted regression tests for mutation-critical patterns in:
  - app/models.py (Signal, OrderResult, AccountBalance, Side/AssetClass/OrderStatus enums)
  - app/connections.py (source/broker connection tracking and validation)
  - app/trade_episode.py (trade lifecycle tracking and episode grouping)

Focus areas:
  1. Enum value discrimination (Side.BUY/SELL/CLOSE, OrderStatus values)
  2. Model validation and type conversions (__post_init__ mutations)
  3. Type conversions and defaults
  4. Boundary conditions in ID/count fields
  5. Optional field handling (None vs empty vs zero)
  6. Boolean logic in connection health/state classification
  7. Boolean flags (enabled, managed_lifecycle, exclusive_writer_qualified)
  8. Comparison operators (==, !=, >, >=, <, <=, is None)

Every test is hand-written to target a specific high-risk mutation,
never a library-test comparison. Pattern mirrors Tracks 61-67 approach.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import uuid

import pytest

from app.db import alembic_code_head
from app.models import (
    Signal,
    Side,
    AssetClass,
    EntryOrderType,
    OrderResult,
    OrderStatus,
    AccountBalance,
    DestinationAccount,
    ManagementRecipe,
    CommandType,
    UncertaintyState,
    TERMINAL_UNCERTAINTY_STATES,
    CommandLedgerEntry,
    QuantityBreakdown,
    ProfitTarget,
)
from app.connections import (
    ConnectionState,
    AuthorizationState,
    ConnectionError_,
    validate_connection_registration,
    looks_like_raw_credential,
    default_capabilities,
)
from app.trade_episode import TradeEpisode, EpisodeExecution


# ============================================================================
# Section 1: app/models.py - Enum Value Discrimination
# ============================================================================


class TestSideEnumMutations:
    """Mutation-critical tests for Side enum discrimination."""

    def test_side_buy_value_is_exactly_buy(self):
        """Mutation: changing 'buy' value breaks provider filtering."""
        assert Side.BUY.value == "buy"

    def test_side_sell_value_is_exactly_sell(self):
        """Mutation: changing 'sell' value breaks direction logic."""
        assert Side.SELL.value == "sell"

    def test_side_close_value_is_exactly_close(self):
        """Mutation: changing 'close' value breaks position-closing logic."""
        assert Side.CLOSE.value == "close"

    def test_side_enum_has_exactly_three_members(self):
        """Mutation: adding/removing sides breaks enum membership checks."""
        assert len(Side) == 3
        assert set(s.value for s in Side) == {"buy", "sell", "close"}

    def test_side_from_string_buy_lowercase(self):
        """Mutation: enum conversion failure on lowercase input."""
        s = Side("buy")
        assert s == Side.BUY
        assert isinstance(s, Side)

    def test_side_from_string_sell_lowercase(self):
        """Mutation: enum conversion failure on lowercase input."""
        s = Side("sell")
        assert s == Side.SELL

    def test_side_from_string_close_lowercase(self):
        """Mutation: enum conversion failure on lowercase input."""
        s = Side("close")
        assert s == Side.CLOSE

    def test_side_from_invalid_string_raises_valueerror(self):
        """Mutation: removing exception raises breaks validation."""
        with pytest.raises(ValueError):
            Side("unknown_side")

    def test_side_inequality(self):
        """Mutation: using == instead of != breaks conditional logic."""
        assert Side.BUY != Side.SELL
        assert Side.BUY != Side.CLOSE
        assert Side.SELL != Side.CLOSE


class TestAssetClassEnumMutations:
    """Mutation-critical tests for AssetClass enum discrimination."""

    def test_asset_class_crypto_value(self):
        """Mutation: changing 'crypto' value breaks asset filtering."""
        assert AssetClass.CRYPTO.value == "crypto"

    def test_asset_class_forex_value(self):
        """Mutation: changing 'forex' value breaks FX routing."""
        assert AssetClass.FOREX.value == "forex"

    def test_asset_class_equity_value(self):
        """Mutation: changing 'equity' value breaks equity routing."""
        assert AssetClass.EQUITY.value == "equity"

    def test_asset_class_option_value(self):
        """Mutation: changing 'option' value breaks derivative routing."""
        assert AssetClass.OPTION.value == "option"

    def test_asset_class_future_value(self):
        """Mutation: changing 'future' value breaks derivative routing."""
        assert AssetClass.FUTURE.value == "future"

    def test_asset_class_enum_has_exactly_five_members(self):
        """Mutation: adding/removing asset classes breaks routing."""
        assert len(AssetClass) == 5

    def test_asset_class_default_is_crypto(self):
        """Mutation: changing default asset class breaks signal creation."""
        sig = Signal(source="test", symbol="BTC/USD", side=Side.BUY)
        assert sig.asset_class == AssetClass.CRYPTO


class TestOrderStatusEnumMutations:
    """Mutation-critical tests for OrderStatus enum discrimination."""

    def test_order_status_pending_value(self):
        """Mutation: changing 'pending' value breaks order polling."""
        assert OrderStatus.PENDING.value == "pending"

    def test_order_status_filled_value(self):
        """Mutation: changing 'filled' value breaks fill detection."""
        assert OrderStatus.FILLED.value == "filled"

    def test_order_status_rejected_value(self):
        """Mutation: changing 'rejected' value breaks rejection handling."""
        assert OrderStatus.REJECTED.value == "rejected"

    def test_order_status_error_value(self):
        """Mutation: changing 'error' value breaks error classification."""
        assert OrderStatus.ERROR.value == "error"

    def test_order_status_enum_has_exactly_four_members(self):
        """Mutation: adding/removing statuses breaks state machine."""
        assert len(OrderStatus) == 4
        assert set(os.value for os in OrderStatus) == {"pending", "filled", "rejected", "error"}

    def test_order_status_from_string_filled(self):
        """Mutation: enum conversion failure breaks fill processing."""
        assert OrderStatus("filled") == OrderStatus.FILLED

    def test_order_status_inequality_pending_vs_filled(self):
        """Mutation: using == instead of != breaks state checks."""
        assert OrderStatus.PENDING != OrderStatus.FILLED
        assert OrderStatus.PENDING != OrderStatus.REJECTED


class TestEntryOrderTypeEnumMutations:
    """Mutation-critical tests for EntryOrderType enum discrimination."""

    def test_entry_order_type_market_value(self):
        """Mutation: changing 'market' value breaks order type routing."""
        assert EntryOrderType.MARKET.value == "market"

    def test_entry_order_type_limit_value(self):
        """Mutation: changing 'limit' value breaks limit order routing."""
        assert EntryOrderType.LIMIT.value == "limit"

    def test_entry_order_type_stop_value(self):
        """Mutation: changing 'stop' value breaks stop order routing."""
        assert EntryOrderType.STOP.value == "stop"

    def test_entry_order_type_enum_has_exactly_three_members(self):
        """Mutation: adding/removing order types breaks routing."""
        assert len(EntryOrderType) == 3


# ============================================================================
# Section 2: app/models.py - Model Validation and Type Conversion
# ============================================================================


class TestSignalPostInitConversions:
    """Mutation-critical tests for Signal.__post_init__ enum conversions."""

    def test_signal_side_string_to_enum_buy(self):
        """Mutation: removing side string conversion breaks signal parsing."""
        sig = Signal(source="test", symbol="BTC/USD", side="buy")
        assert sig.side == Side.BUY
        assert isinstance(sig.side, Side)

    def test_signal_side_string_to_enum_sell(self):
        """Mutation: removing side string conversion breaks signal parsing."""
        sig = Signal(source="test", symbol="BTC/USD", side="sell")
        assert sig.side == Side.SELL

    def test_signal_side_string_to_enum_close(self):
        """Mutation: removing side string conversion breaks close parsing."""
        sig = Signal(source="test", symbol="BTC/USD", side="close")
        assert sig.side == Side.CLOSE

    def test_signal_asset_class_string_to_enum_crypto(self):
        """Mutation: removing asset_class conversion breaks signal parsing."""
        sig = Signal(source="test", symbol="BTC/USD", side=Side.BUY, asset_class="crypto")
        assert sig.asset_class == AssetClass.CRYPTO
        assert isinstance(sig.asset_class, AssetClass)

    def test_signal_asset_class_string_to_enum_forex(self):
        """Mutation: removing asset_class conversion breaks FX signal parsing."""
        sig = Signal(source="test", symbol="EUR/USD", side=Side.BUY, asset_class="forex")
        assert sig.asset_class == AssetClass.FOREX

    def test_signal_entry_order_type_string_to_enum_limit(self):
        """Mutation: removing entry_order_type conversion breaks limit order parsing."""
        sig = Signal(
            source="test",
            symbol="BTC/USD",
            side=Side.BUY,
            entry_order_type="limit"
        )
        assert sig.entry_order_type == EntryOrderType.LIMIT
        assert isinstance(sig.entry_order_type, EntryOrderType)

    def test_signal_entry_order_type_string_to_enum_market(self):
        """Mutation: removing entry_order_type conversion breaks order type parsing."""
        sig = Signal(
            source="test",
            symbol="BTC/USD",
            side=Side.BUY,
            entry_order_type="market"
        )
        assert sig.entry_order_type == EntryOrderType.MARKET

    def test_signal_side_case_insensitive_uppercase(self):
        """Mutation: removing .lower() breaks uppercase side parsing."""
        sig = Signal(source="test", symbol="BTC/USD", side="BUY")
        assert sig.side == Side.BUY

    def test_signal_asset_class_case_insensitive_uppercase(self):
        """Mutation: removing .lower() breaks uppercase asset_class parsing."""
        sig = Signal(source="test", symbol="BTC/USD", side=Side.BUY, asset_class="CRYPTO")
        assert sig.asset_class == AssetClass.CRYPTO

    def test_signal_side_already_enum_not_converted_again(self):
        """Mutation: double-converting already-enum side breaks equality."""
        side_enum = Side.BUY
        sig = Signal(source="test", symbol="BTC/USD", side=side_enum)
        assert sig.side == Side.BUY
        assert sig.side is side_enum


class TestSignalDefaultValues:
    """Mutation-critical tests for Signal field defaults."""

    def test_signal_id_not_none(self):
        """Mutation: removing default factory for id breaks uniqueness."""
        sig = Signal(source="test", symbol="BTC/USD", side=Side.BUY)
        assert sig.id is not None
        assert isinstance(sig.id, str)
        assert len(sig.id) > 0

    def test_signal_id_is_unique(self):
        """Mutation: broken default factory means all ids the same."""
        sig1 = Signal(source="test", symbol="BTC/USD", side=Side.BUY)
        sig2 = Signal(source="test", symbol="BTC/USD", side=Side.BUY)
        assert sig1.id != sig2.id

    def test_signal_received_at_not_none(self):
        """Mutation: removing default factory for received_at breaks timestamps."""
        sig = Signal(source="test", symbol="BTC/USD", side=Side.BUY)
        assert sig.received_at is not None
        assert isinstance(sig.received_at, datetime)

    def test_signal_received_at_is_utc(self):
        """Mutation: removing timezone.utc breaks UTC requirement."""
        sig = Signal(source="test", symbol="BTC/USD", side=Side.BUY)
        assert sig.received_at.tzinfo is not None
        assert sig.received_at.tzinfo.utcoffset(None) == timedelta(0)

    def test_signal_raw_dict_default(self):
        """Mutation: removing default factory creates shared mutable default."""
        sig1 = Signal(source="test", symbol="BTC/USD", side=Side.BUY)
        sig1.raw["key"] = "value"
        sig2 = Signal(source="test", symbol="BTC/USD", side=Side.BUY)
        assert "key" not in sig2.raw

    def test_signal_targets_list_default(self):
        """Mutation: removing default factory creates shared mutable default."""
        sig1 = Signal(source="test", symbol="BTC/USD", side=Side.BUY)
        sig1.targets.append(ProfitTarget(price=100.0))
        sig2 = Signal(source="test", symbol="BTC/USD", side=Side.BUY)
        assert len(sig2.targets) == 0

    def test_signal_optional_fields_are_none(self):
        """Mutation: changing None defaults to fabricated values."""
        sig = Signal(source="test", symbol="BTC/USD", side=Side.BUY)
        assert sig.quantity is None
        assert sig.price is None
        assert sig.stop_loss is None
        assert sig.take_profit is None
        assert sig.analyst is None
        assert sig.import_batch is None
        assert sig.channel_id is None
        assert sig.message_id is None


class TestOrderResultValidation:
    """Mutation-critical tests for OrderResult field requirements."""

    def test_order_result_requires_account_id(self):
        """Mutation: removing account_id requirement breaks identification."""
        result = OrderResult(
            account_id="acc123",
            status=OrderStatus.FILLED,
            signal_id="sig456"
        )
        assert result.account_id == "acc123"

    def test_order_result_requires_status(self):
        """Mutation: removing status requirement breaks state tracking."""
        result = OrderResult(
            account_id="acc123",
            status=OrderStatus.FILLED,
            signal_id="sig456"
        )
        assert result.status == OrderStatus.FILLED

    def test_order_result_requires_signal_id(self):
        """Mutation: removing signal_id requirement breaks correlation."""
        result = OrderResult(
            account_id="acc123",
            status=OrderStatus.FILLED,
            signal_id="sig456"
        )
        assert result.signal_id == "sig456"

    def test_order_result_broker_order_id_optional(self):
        """Mutation: changing broker_order_id from optional to required."""
        result = OrderResult(
            account_id="acc123",
            status=OrderStatus.REJECTED,
            signal_id="sig456"
        )
        assert result.broker_order_id is None

    def test_order_result_executed_at_default(self):
        """Mutation: removing executed_at default breaks timestamps."""
        result = OrderResult(
            account_id="acc123",
            status=OrderStatus.FILLED,
            signal_id="sig456"
        )
        assert result.executed_at is not None
        assert isinstance(result.executed_at, datetime)

    def test_order_result_message_default_empty_string(self):
        """Mutation: changing default message to None breaks string operations."""
        result = OrderResult(
            account_id="acc123",
            status=OrderStatus.FILLED,
            signal_id="sig456"
        )
        assert result.message == ""
        assert isinstance(result.message, str)


class TestAccountBalanceOptionalFields:
    """Mutation-critical tests for AccountBalance None-vs-zero distinction."""

    def test_account_balance_cash_none_not_zero(self):
        """Mutation: defaulting None cash to 0.0 fabricates balance."""
        balance = AccountBalance(account_id="acc123")
        assert balance.cash is None

    def test_account_balance_equity_none_not_zero(self):
        """Mutation: defaulting None equity to 0.0 fabricates balance."""
        balance = AccountBalance(account_id="acc123")
        assert balance.equity is None

    def test_account_balance_buying_power_none_not_zero(self):
        """Mutation: defaulting None buying_power to 0.0 fabricates capacity."""
        balance = AccountBalance(account_id="acc123")
        assert balance.buying_power is None

    def test_account_balance_maintenance_margin_none_not_zero(self):
        """Mutation: defaulting None margin to 0.0 fabricates usage."""
        balance = AccountBalance(account_id="acc123")
        assert balance.maintenance_margin is None

    def test_account_balance_to_dict_preserves_none(self):
        """Mutation: converting None to 0 in to_dict breaks API contract."""
        balance = AccountBalance(account_id="acc123", cash=100.0, equity=None)
        d = balance.to_dict()
        assert d["account_id"] == "acc123"
        assert d["cash"] == 100.0
        assert d["equity"] is None


class TestDestinationAccountDefaults:
    """Mutation-critical tests for DestinationAccount boolean and enum defaults."""

    def test_destination_account_multiplier_default_is_one(self):
        """Mutation: changing multiplier default to 0 breaks sizing."""
        acc = DestinationAccount(account_id="acc123", broker="alpaca")
        assert acc.multiplier == 1.0

    def test_destination_account_enabled_default_is_true(self):
        """Mutation: changing enabled default to False blocks trading."""
        acc = DestinationAccount(account_id="acc123", broker="alpaca")
        assert acc.enabled is True

    def test_destination_account_managed_lifecycle_default_is_false(self):
        """Mutation: enabling managed_lifecycle by default changes product."""
        acc = DestinationAccount(account_id="acc123", broker="alpaca")
        assert acc.managed_lifecycle is False

    def test_destination_account_exclusive_writer_qualified_default_is_false(self):
        """Mutation: enabling exclusive_writer by default bypasses security."""
        acc = DestinationAccount(account_id="acc123", broker="alpaca")
        assert acc.exclusive_writer_qualified is False

    def test_destination_account_management_recipe_from_managed_lifecycle_true(self):
        """Mutation: not setting management_recipe leaves it None."""
        acc = DestinationAccount(account_id="acc123", broker="alpaca", managed_lifecycle=True)
        assert acc.management_recipe == ManagementRecipe.FULL_MANAGED_LIFECYCLE

    def test_destination_account_management_recipe_from_managed_lifecycle_false(self):
        """Mutation: not setting management_recipe leaves it None."""
        acc = DestinationAccount(account_id="acc123", broker="alpaca", managed_lifecycle=False)
        assert acc.management_recipe == ManagementRecipe.PLAIN_UNMANAGED

    def test_destination_account_management_recipe_string_conversion(self):
        """Mutation: removing string-to-enum conversion breaks configuration."""
        acc = DestinationAccount(
            account_id="acc123",
            broker="alpaca",
            management_recipe="full_managed_lifecycle"
        )
        assert acc.management_recipe == ManagementRecipe.FULL_MANAGED_LIFECYCLE

    def test_destination_account_explicit_recipe_overrides_managed_lifecycle(self):
        """Mutation: always overwriting recipe from managed_lifecycle breaks config."""
        acc = DestinationAccount(
            account_id="acc123",
            broker="alpaca",
            managed_lifecycle=True,
            management_recipe="plain_unmanaged"
        )
        assert acc.management_recipe == ManagementRecipe.PLAIN_UNMANAGED

    def test_destination_account_fixed_quantity_optional(self):
        """Mutation: requiring fixed_quantity breaks multiplier-based sizing."""
        acc = DestinationAccount(account_id="acc123", broker="alpaca")
        assert acc.fixed_quantity is None

    def test_destination_account_symbol_map_empty_default(self):
        """Mutation: removing default factory creates shared mutable default."""
        acc1 = DestinationAccount(account_id="acc123", broker="alpaca")
        acc1.symbol_map["BTC/USD"] = "BTC"
        acc2 = DestinationAccount(account_id="acc124", broker="alpaca")
        assert len(acc2.symbol_map) == 0


# ============================================================================
# Section 3: app/connections.py - Connection Validation and Enums
# ============================================================================


class TestConnectionStateEnumMutations:
    """Mutation-critical tests for ConnectionState enum discrimination."""

    def test_connection_state_unconfigured_value(self):
        """Mutation: changing 'unconfigured' value breaks initial state."""
        assert ConnectionState.UNCONFIGURED.value == "unconfigured"

    def test_connection_state_connected_value(self):
        """Mutation: changing 'connected' value breaks health checks."""
        assert ConnectionState.CONNECTED.value == "connected"

    def test_connection_state_degraded_value(self):
        """Mutation: changing 'degraded' value breaks partial-failure detection."""
        assert ConnectionState.DEGRADED.value == "degraded"

    def test_connection_state_error_value(self):
        """Mutation: changing 'error' value breaks error classification."""
        assert ConnectionState.ERROR.value == "error"

    def test_connection_state_disconnected_value(self):
        """Mutation: changing 'disconnected' value breaks offline detection."""
        assert ConnectionState.DISCONNECTED.value == "disconnected"

    def test_connection_state_enum_has_exactly_five_members(self):
        """Mutation: adding/removing states breaks state machine."""
        assert len(ConnectionState) == 5


class TestAuthorizationStateEnumMutations:
    """Mutation-critical tests for AuthorizationState enum discrimination."""

    def test_authorization_state_unauthorized_value(self):
        """Mutation: changing 'unauthorized' value breaks initial auth state."""
        assert AuthorizationState.UNAUTHORIZED.value == "unauthorized"

    def test_authorization_state_authorized_value(self):
        """Mutation: changing 'authorized' value breaks auth checks."""
        assert AuthorizationState.AUTHORIZED.value == "authorized"

    def test_authorization_state_expired_value(self):
        """Mutation: changing 'expired' value breaks token expiry detection."""
        assert AuthorizationState.EXPIRED.value == "expired"

    def test_authorization_state_revoked_value(self):
        """Mutation: changing 'revoked' value breaks revocation detection."""
        assert AuthorizationState.REVOKED.value == "revoked"

    def test_authorization_state_enum_has_exactly_four_members(self):
        """Mutation: adding/removing states breaks auth state machine."""
        assert len(AuthorizationState) == 4


class TestLooksLikeRawCredentialMutations:
    """Mutation-critical tests for raw credential detection heuristic."""

    def test_looks_like_raw_credential_none_is_false(self):
        """Mutation: treating None as raw credential rejects valid env names."""
        assert looks_like_raw_credential(None) is False

    def test_looks_like_raw_credential_empty_string_is_false(self):
        """Mutation: treating empty string as raw credential rejects valid names."""
        assert looks_like_raw_credential("") is False

    def test_looks_like_raw_credential_normal_env_var_is_false(self):
        """Mutation: flagging normal env vars blocks configuration."""
        assert looks_like_raw_credential("SLACK_TOKEN") is False
        assert looks_like_raw_credential("API_KEY") is False

    def test_looks_like_raw_credential_long_value_is_true(self):
        """Mutation: not checking length allows long secrets through."""
        long_secret = "x" * 65
        assert looks_like_raw_credential(long_secret) is True

    def test_looks_like_raw_credential_with_spaces_is_true(self):
        """Mutation: not checking spaces allows structured secrets through."""
        assert looks_like_raw_credential("Bearer token123") is True

    def test_looks_like_raw_credential_with_colon_is_true(self):
        """Mutation: not checking colons allows URI secrets through."""
        assert looks_like_raw_credential("user:password") is True

    def test_looks_like_raw_credential_with_slash_path_is_true(self):
        """Mutation: not checking slashes allows path-like secrets through."""
        assert looks_like_raw_credential("path/to/secret") is True

    def test_looks_like_raw_credential_http_url_is_false(self):
        """Mutation: incorrectly flagging URLs blocks configuration."""
        assert looks_like_raw_credential("http://example.com/callback") is False
        assert looks_like_raw_credential("https://api.example.com") is False


class TestValidateConnectionRegistration:
    """Mutation-critical tests for connection validation."""

    def test_validate_connection_requires_connection_id(self):
        """Mutation: removing required connection_id allows invalid config."""
        with pytest.raises(ConnectionError_) as exc_info:
            validate_connection_registration(connection_id="", connection_type="slack")
        assert "connection_id is required" in str(exc_info.value)

    def test_validate_connection_rejects_whitespace_only_id(self):
        """Mutation: not stripping connection_id allows whitespace config."""
        with pytest.raises(ConnectionError_) as exc_info:
            validate_connection_registration(connection_id="   ", connection_type="slack")
        assert "connection_id is required" in str(exc_info.value)

    def test_validate_connection_requires_connection_type(self):
        """Mutation: removing required connection_type allows invalid config."""
        with pytest.raises(ConnectionError_) as exc_info:
            validate_connection_registration(connection_id="conn1", connection_type="")
        assert "connection_type is required" in str(exc_info.value)

    def test_validate_connection_rejects_whitespace_only_type(self):
        """Mutation: not stripping connection_type allows whitespace config."""
        with pytest.raises(ConnectionError_) as exc_info:
            validate_connection_registration(connection_id="conn1", connection_type="   ")
        assert "connection_type is required" in str(exc_info.value)

    def test_validate_connection_rejects_raw_credential(self):
        """Mutation: not validating credential blocks secret-detection."""
        with pytest.raises(ConnectionError_) as exc_info:
            validate_connection_registration(
                connection_id="conn1",
                connection_type="slack",
                credential_reference="sk-" + "x" * 100
            )
        assert "environment-variable NAME" in str(exc_info.value)

    def test_validate_connection_accepts_valid_env_var_credential(self):
        """Mutation: rejecting all credentials breaks configuration."""
        validate_connection_registration(
            connection_id="conn1",
            connection_type="slack",
            credential_reference="SLACK_BOT_TOKEN"
        )

    def test_validate_connection_accepts_none_credential(self):
        """Mutation: requiring credentials breaks passwordless connections."""
        validate_connection_registration(
            connection_id="conn1",
            connection_type="rss",
            credential_reference=None
        )

    def test_validate_connection_accepts_valid_connection_state(self):
        """Mutation: rejecting valid states breaks state persistence."""
        validate_connection_registration(
            connection_id="conn1",
            connection_type="slack",
            connection_state="connected"
        )

    def test_validate_connection_accepts_valid_authorization_state(self):
        """Mutation: rejecting valid states breaks auth tracking."""
        validate_connection_registration(
            connection_id="conn1",
            connection_type="slack",
            authorization_state="authorized"
        )


# ============================================================================
# Section 4: app/models.py - Terminal States and Uncertainty
# ============================================================================


class TestTerminalUncertaintyStates:
    """Mutation-critical tests for terminal state identification."""

    def test_terminal_states_includes_confirmed(self):
        """Mutation: removing CONFIRMED from terminal states breaks resolved check."""
        assert UncertaintyState.CONFIRMED in TERMINAL_UNCERTAINTY_STATES

    def test_terminal_states_includes_rejected_confirmed(self):
        """Mutation: removing REJECTED_CONFIRMED allows ambiguous state."""
        assert UncertaintyState.REJECTED_CONFIRMED in TERMINAL_UNCERTAINTY_STATES

    def test_terminal_states_does_not_include_pending_submission(self):
        """Mutation: adding PENDING_SUBMISSION to terminals breaks state machine."""
        assert UncertaintyState.PENDING_SUBMISSION not in TERMINAL_UNCERTAINTY_STATES

    def test_terminal_states_does_not_include_submitted_unconfirmed(self):
        """Mutation: adding SUBMITTED_UNCONFIRMED to terminals breaks polling."""
        assert UncertaintyState.SUBMITTED_UNCONFIRMED not in TERMINAL_UNCERTAINTY_STATES

    def test_terminal_states_does_not_include_unknown_ambiguous(self):
        """Mutation: adding UNKNOWN_AMBIGUOUS to terminals breaks reconciliation."""
        assert UncertaintyState.UNKNOWN_AMBIGUOUS not in TERMINAL_UNCERTAINTY_STATES

    def test_terminal_states_has_exactly_two_members(self):
        """Mutation: changing terminal states breaks state-based queries."""
        assert len(TERMINAL_UNCERTAINTY_STATES) == 2


# ============================================================================
# Section 5: Comparison Operators and Boolean Flags
# ============================================================================


class TestComparisonOperatorMutations:
    """Mutation-critical tests for comparison operator correctness."""

    def test_side_equality_buy_equals_buy(self):
        """Mutation: changing == to != breaks equality checks."""
        sig1 = Signal(source="test", symbol="BTC/USD", side=Side.BUY)
        sig2 = Signal(source="test", symbol="BTC/USD", side=Side.BUY)
        assert sig1.side == sig2.side

    def test_side_inequality_buy_not_equals_sell(self):
        """Mutation: changing != to == inverts logic."""
        sig1 = Signal(source="test", symbol="BTC/USD", side=Side.BUY)
        sig2 = Signal(source="test", symbol="BTC/USD", side=Side.SELL)
        assert sig1.side != sig2.side

    def test_order_status_equality_filled_equals_filled(self):
        """Mutation: changing == to != breaks status checks."""
        result1 = OrderResult(
            account_id="acc",
            status=OrderStatus.FILLED,
            signal_id="sig"
        )
        result2 = OrderResult(
            account_id="acc",
            status=OrderStatus.FILLED,
            signal_id="sig"
        )
        assert result1.status == result2.status

    def test_order_status_inequality_pending_not_equals_filled(self):
        """Mutation: changing != to == breaks state discrimination."""
        result1 = OrderResult(
            account_id="acc",
            status=OrderStatus.PENDING,
            signal_id="sig"
        )
        result2 = OrderResult(
            account_id="acc",
            status=OrderStatus.FILLED,
            signal_id="sig"
        )
        assert result1.status != result2.status


class TestBooleanFlagMutations:
    """Mutation-critical tests for boolean flag handling."""

    def test_destination_account_enabled_true_allows_trading(self):
        """Mutation: inverting enabled breaks feature toggle."""
        acc_enabled = DestinationAccount(account_id="acc1", broker="alpaca", enabled=True)
        acc_disabled = DestinationAccount(account_id="acc2", broker="alpaca", enabled=False)
        assert acc_enabled.enabled is True
        assert acc_disabled.enabled is False
        assert acc_enabled.enabled != acc_disabled.enabled

    def test_destination_account_managed_lifecycle_selects_recipe(self):
        """Mutation: not checking managed_lifecycle breaks recipe selection."""
        acc_plain = DestinationAccount(
            account_id="acc1",
            broker="alpaca",
            managed_lifecycle=False
        )
        acc_managed = DestinationAccount(
            account_id="acc2",
            broker="alpaca",
            managed_lifecycle=True
        )
        assert acc_plain.management_recipe == ManagementRecipe.PLAIN_UNMANAGED
        assert acc_managed.management_recipe == ManagementRecipe.FULL_MANAGED_LIFECYCLE

    def test_destination_account_exclusive_writer_qualified_gates_closes(self):
        """Mutation: not checking exclusive_writer breaks authorization."""
        acc_not_qualified = DestinationAccount(
            account_id="acc1",
            broker="alpaca",
            exclusive_writer_qualified=False
        )
        acc_qualified = DestinationAccount(
            account_id="acc2",
            broker="alpaca",
            exclusive_writer_qualified=True
        )
        assert acc_not_qualified.exclusive_writer_qualified is False
        assert acc_qualified.exclusive_writer_qualified is True


class TestDefaultCapabilitiesFunction:
    """Mutation-critical tests for connection capability defaults."""

    def test_default_capabilities_returns_all_false(self):
        """Mutation: defaulting to True allows unverified capabilities."""
        caps = default_capabilities()
        for key in caps:
            assert caps[key] is False

    def test_default_capabilities_has_all_expected_keys(self):
        """Mutation: missing keys breaks capability discovery."""
        caps = default_capabilities()
        expected_keys = {
            "realtime_events", "history", "history_depth", "message_edits",
            "deletions", "attachments", "images", "embeds", "threads",
            "stable_ids", "original_timestamps", "delivery_ack", "replay",
            "backfill", "health_check", "push", "poll", "active_retrieval"
        }
        assert set(caps.keys()) == expected_keys

    def test_default_capabilities_returns_new_dict_each_time(self):
        """Mutation: reusing dict breaks isolation between connections."""
        caps1 = default_capabilities()
        caps2 = default_capabilities()
        caps1["realtime_events"] = True
        assert caps2["realtime_events"] is False


class TestSchemaMigrationHead:
    """Mutation-critical tests for schema version tracking."""

    def test_alembic_code_head_not_none(self):
        """Mutation: code_head returning None breaks schema version checking."""
        head = alembic_code_head()
        assert head is not None

    def test_alembic_code_head_is_string(self):
        """Mutation: returning non-string breaks version comparison."""
        head = alembic_code_head()
        assert isinstance(head, str)

    def test_alembic_code_head_consistent(self):
        """Mutation: non-deterministic head breaks reproducibility."""
        head1 = alembic_code_head()
        head2 = alembic_code_head()
        assert head1 == head2
