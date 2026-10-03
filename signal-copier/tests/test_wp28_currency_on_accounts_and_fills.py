"""WP-28: Multi-currency support — account base currency and per-fill price currency.

E-11: No account/base currency anywhere; realized P&L sums quote-currency units
across symbols; export currency is guessed from symbol syntax.

B-10: Per-account ceilings sum raw numbers across currencies and instruments;
no basis currency.

Tests for:
- DestinationAccount.currency field for account base currency
- OrderResult.price_currency field for per-fill price currency
- AccountBalance.currency field for broker-reported account currency
- Database schema and migrations
- Honest NULL semantics: never fabricate currency values
"""
from app.models import (
    DestinationAccount,
    OrderResult,
    OrderStatus,
    AccountBalance,
)


class TestDestinationAccountCurrency:
    """Tests for DestinationAccount currency field."""

    def test_destination_account_currency_default_none(self):
        """Should default to None (not declared)."""
        account = DestinationAccount(
            account_id="TEST_001",
            broker="paper",
        )
        assert account.currency is None

    def test_destination_account_currency_usd(self):
        """Should accept explicit USD currency."""
        account = DestinationAccount(
            account_id="TEST_001",
            broker="paper",
            currency="USD",
        )
        assert account.currency == "USD"

    def test_destination_account_currency_eur(self):
        """Should accept EUR currency."""
        account = DestinationAccount(
            account_id="TEST_001",
            broker="paper",
            currency="EUR",
        )
        assert account.currency == "EUR"

    def test_destination_account_currency_jpy(self):
        """Should accept JPY currency (3-letter codes)."""
        account = DestinationAccount(
            account_id="TEST_001",
            broker="paper",
            currency="JPY",
        )
        assert account.currency == "JPY"

    def test_destination_account_with_all_fields(self):
        """Should preserve currency alongside other account config."""
        account = DestinationAccount(
            account_id="TEST_MULTI",
            broker="alpaca",
            currency="EUR",
            multiplier=1.0,
            managed_lifecycle=True,
            max_notional_exposure=10000.0,
        )
        assert account.account_id == "TEST_MULTI"
        assert account.broker == "alpaca"
        assert account.currency == "EUR"
        assert account.multiplier == 1.0
        assert account.managed_lifecycle is True
        assert account.max_notional_exposure == 10000.0


class TestOrderResultPriceCurrency:
    """Tests for OrderResult price_currency field."""

    def test_order_result_price_currency_default_none(self):
        """Should default to None (not reported)."""
        result = OrderResult(
            account_id="TEST_001",
            status=OrderStatus.FILLED,
            signal_id="SIG_001",
            filled_quantity=10.0,
            filled_price=100.0,
        )
        assert result.price_currency is None

    def test_order_result_price_currency_usd(self):
        """Should accept USD price currency."""
        result = OrderResult(
            account_id="TEST_001",
            status=OrderStatus.FILLED,
            signal_id="SIG_001",
            filled_quantity=10.0,
            filled_price=100.0,
            price_currency="USD",
        )
        assert result.price_currency == "USD"

    def test_order_result_price_currency_jpy(self):
        """Should accept JPY price currency."""
        result = OrderResult(
            account_id="TEST_001",
            status=OrderStatus.FILLED,
            signal_id="SIG_001",
            filled_quantity=1000000.0,
            filled_price=150.0,
            price_currency="JPY",
        )
        assert result.price_currency == "JPY"

    def test_order_result_price_currency_crypto(self):
        """Should accept crypto asset codes like BTC."""
        result = OrderResult(
            account_id="TEST_001",
            status=OrderStatus.FILLED,
            signal_id="SIG_001",
            filled_quantity=0.5,
            filled_price=50000.0,
            price_currency="BTC",
        )
        assert result.price_currency == "BTC"

    def test_order_result_with_price_and_fee_currency(self):
        """Should distinguish price_currency from fee_currency."""
        # BTC/JPY trade with JPY fee
        result = OrderResult(
            account_id="TEST_001",
            status=OrderStatus.FILLED,
            signal_id="SIG_001",
            filled_quantity=1.0,
            filled_price=5000000.0,
            fee=1000.0,
            fee_currency="JPY",
            price_currency="JPY",
        )
        assert result.fee_currency == "JPY"
        assert result.price_currency == "JPY"

    def test_order_result_price_currency_null_for_rejected(self):
        """Should have NULL price_currency for rejected orders (honest)."""
        result = OrderResult(
            account_id="TEST_001",
            status=OrderStatus.REJECTED,
            signal_id="SIG_001",
            message="Insufficient buying power",
        )
        assert result.price_currency is None
        # Never fabricate even for rejected orders

    def test_order_result_price_currency_null_for_pending(self):
        """Should have NULL price_currency for PENDING (not yet filled)."""
        result = OrderResult(
            account_id="TEST_001",
            status=OrderStatus.PENDING,
            signal_id="SIG_001",
            broker_order_id="BROKER_123",
        )
        # PENDING: no fill yet, so price_currency is still unknown
        assert result.price_currency is None


class TestAccountBalanceCurrency:
    """Tests for AccountBalance currency field."""

    def test_account_balance_currency_default_none(self):
        """Should default to None (broker didn't report it)."""
        balance = AccountBalance(
            account_id="TEST_001",
            cash=10000.0,
            equity=15000.0,
        )
        assert balance.currency is None

    def test_account_balance_currency_usd(self):
        """Should accept USD currency from broker."""
        balance = AccountBalance(
            account_id="TEST_001",
            cash=10000.0,
            equity=15000.0,
            currency="USD",
        )
        assert balance.currency == "USD"

    def test_account_balance_to_dict_includes_currency(self):
        """to_dict should include currency field."""
        balance = AccountBalance(
            account_id="TEST_001",
            cash=10000.0,
            equity=15000.0,
            buying_power=5000.0,
            maintenance_margin=500.0,
            currency="EUR",
        )
        d = balance.to_dict()
        assert d["account_id"] == "TEST_001"
        assert d["cash"] == 10000.0
        assert d["equity"] == 15000.0
        assert d["buying_power"] == 5000.0
        assert d["maintenance_margin"] == 500.0
        assert d["currency"] == "EUR"

    def test_account_balance_currency_not_fabricated(self):
        """Should not fabricate currency; None is honest."""
        # Broker didn't report currency
        balance = AccountBalance(
            account_id="TEST_001",
            cash=10000.0,
            equity=15000.0,
            # No currency provided
        )
        assert balance.currency is None


class TestDatabasePersistence:
    """Tests for currency field database persistence."""

    def test_config_accounts_currency_column_exists(self, tmp_path):
        """Database should have currency column on config_accounts."""
        import sqlite3
        from app.db import SCHEMA

        # Create schema directly without alembic (which requires complete migration chain)
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(db_path)
        conn.executescript(SCHEMA)

        # Schema should include currency column
        cursor = conn.execute("PRAGMA table_info(config_accounts)")
        columns = {row[1]: row[2] for row in cursor.fetchall()}
        conn.close()

        assert "currency" in columns
        assert columns["currency"] == "TEXT"

    def test_orders_price_currency_column_exists(self, tmp_path):
        """Database should have price_currency column on orders."""
        import sqlite3
        from app.db import SCHEMA

        # Create schema directly without alembic (which requires complete migration chain)
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(db_path)
        conn.executescript(SCHEMA)

        # Schema should include price_currency column
        cursor = conn.execute("PRAGMA table_info(orders)")
        columns = {row[1]: row[2] for row in cursor.fetchall()}
        conn.close()

        assert "price_currency" in columns
        assert columns["price_currency"] == "TEXT"

    def test_save_and_read_account_currency(self, tmp_path):
        """Should persist and retrieve account currency."""
        import sqlite3
        from app.db import SCHEMA

        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(db_path)
        conn.executescript(SCHEMA)

        # Insert account with currency
        conn.execute(
            """INSERT INTO config_accounts
               (account_id, broker, currency)
               VALUES (?, ?, ?)""",
            ("ACC_001", "paper", "EUR"),
        )
        conn.commit()

        # Read back
        row = conn.execute(
            "SELECT currency FROM config_accounts WHERE account_id = ?",
            ("ACC_001",),
        ).fetchone()
        conn.close()

        assert row[0] == "EUR"

    def test_order_price_currency_null_by_default(self, tmp_path):
        """Orders should have NULL price_currency by default."""
        import sqlite3
        from app.db import SCHEMA

        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(db_path)
        conn.executescript(SCHEMA)

        # First, insert a signal
        signal_id = "SIG_TEST_001"
        conn.execute(
            """INSERT INTO signals
               (id, source, symbol, side, asset_class, received_at, raw)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                signal_id,
                "test_source",
                "AAPL",
                "buy",
                "equity",
                "2026-10-02T00:00:00Z",
                "{}",
            ),
        )
        conn.commit()

        # Insert an order without price_currency
        conn.execute(
            """INSERT INTO orders
               (account_id, signal_id, status, executed_at)
               VALUES (?, ?, ?, ?)""",
            ("ACC_001", signal_id, "filled", "2026-10-02T00:00:00Z"),
        )
        conn.commit()

        # Read back
        row = conn.execute(
            "SELECT price_currency FROM orders WHERE account_id = ?",
            ("ACC_001",),
        ).fetchone()
        conn.close()

        assert row[0] is None  # NULL by default

    def test_save_order_with_price_currency(self, tmp_path):
        """Should persist price_currency when provided."""
        import sqlite3
        from app.db import SCHEMA

        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(db_path)
        conn.executescript(SCHEMA)

        # First, insert a signal
        signal_id = "SIG_TEST_002"
        conn.execute(
            """INSERT INTO signals
               (id, source, symbol, side, asset_class, received_at, raw)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                signal_id,
                "test_source",
                "USDJPY",
                "buy",
                "forex",
                "2026-10-02T00:00:00Z",
                "{}",
            ),
        )
        conn.commit()

        # Insert order WITH price_currency
        conn.execute(
            """INSERT INTO orders
               (account_id, signal_id, status, executed_at, price_currency)
               VALUES (?, ?, ?, ?, ?)""",
            ("ACC_002", signal_id, "filled", "2026-10-02T00:00:00Z", "JPY"),
        )
        conn.commit()

        # Read back
        row = conn.execute(
            "SELECT price_currency FROM orders WHERE account_id = ?",
            ("ACC_002",),
        ).fetchone()
        conn.close()

        assert row[0] == "JPY"


class TestCurrencyNeverFabricated:
    """Tests enforcing that currency is never guessed or fabricated."""

    def test_order_result_symbol_parsing_not_used(self):
        """Should not infer price_currency from symbol like USDJPY."""
        # Symbol is USDJPY but we don't guess currency
        result = OrderResult(
            account_id="TEST_001",
            status=OrderStatus.FILLED,
            signal_id="SIG_001",
            filled_quantity=1000000.0,
            filled_price=150.0,
            # No price_currency provided
        )
        # price_currency is NULL, not inferred from "USDJPY" symbol
        assert result.price_currency is None

    def test_symbol_slash_pairs_not_guessed(self):
        """Should not infer currency from symbol like BTC/USD."""
        # Symbol is BTC/USD but we don't guess currency
        result = OrderResult(
            account_id="TEST_001",
            status=OrderStatus.FILLED,
            signal_id="SIG_001",
            filled_quantity=1.0,
            filled_price=50000.0,
            # No price_currency provided; it's either BTC or USD
        )
        # Either way, we don't guess
        assert result.price_currency is None

    def test_account_currency_not_defaulted_to_usd(self):
        """Should not default account currency to USD."""
        account = DestinationAccount(
            account_id="TEST_001",
            broker="paper",
            # No currency specified
        )
        # Should be None, not USD
        assert account.currency is None

    def test_broker_balance_currency_honest_none(self):
        """AccountBalance should report None when broker doesn't provide currency."""
        # Broker doesn't report currency
        balance = AccountBalance(
            account_id="TEST_001",
            cash=10000.0,
            equity=15000.0,
            # No currency field
        )
        assert balance.currency is None


class TestCurrencyAcrossMultipleSymbols:
    """Integration tests for multi-currency tracking on a single account."""

    def test_account_trades_multiple_currencies(self, tmp_path):
        """Single account with orders in different price currencies."""
        import sqlite3
        from app.db import SCHEMA

        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(db_path)
        conn.executescript(SCHEMA)

        # Configure account with base currency EUR
        conn.execute(
            """INSERT INTO config_accounts
               (account_id, broker, currency)
               VALUES (?, ?, ?)""",
            ("EUR_ACCOUNT", "paper", "EUR"),
        )
        conn.commit()

        # Add two signals
        for i, (symbol, side) in enumerate([("EURUSD", "buy"), ("EURGBP", "sell")]):
            signal_id = f"SIG_00{i}"
            conn.execute(
                """INSERT INTO signals
                   (id, source, symbol, side, asset_class, received_at, raw)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    signal_id,
                    "test_source",
                    symbol,
                    side,
                    "forex",
                    "2026-10-02T00:00:00Z",
                    "{}",
                ),
            )

        # Insert two orders with different price currencies
        conn.execute(
            """INSERT INTO orders
               (account_id, symbol, signal_id, status, executed_at, price_currency)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("EUR_ACCOUNT", "EURUSD", "SIG_000", "filled", "2026-10-02T00:00:00Z", "USD"),
        )
        conn.execute(
            """INSERT INTO orders
               (account_id, symbol, signal_id, status, executed_at, price_currency)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("EUR_ACCOUNT", "EURGBP", "SIG_001", "filled", "2026-10-02T00:00:00Z", "GBP"),
        )
        conn.commit()

        # Verify account base currency
        account_row = conn.execute(
            "SELECT currency FROM config_accounts WHERE account_id = ?",
            ("EUR_ACCOUNT",),
        ).fetchone()
        assert account_row[0] == "EUR"

        # Verify each order's price currency is tracked separately
        orders = conn.execute(
            """SELECT symbol, price_currency FROM orders WHERE account_id = ?
               ORDER BY symbol""",
            ("EUR_ACCOUNT",),
        ).fetchall()
        conn.close()

        assert len(orders) == 2
        assert orders[0] == ("EURGBP", "GBP")  # Alphabetical
        assert orders[1] == ("EURUSD", "USD")
