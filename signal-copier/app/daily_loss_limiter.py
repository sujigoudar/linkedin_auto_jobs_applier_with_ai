"""Daily loss limit enforcement with circuit breaker.

Prevents trading when daily losses exceed configured ceiling for an account.
Implements fail-closed: rejects new entries if daily loss limit breached.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

from app.db import SignalStore
from app.models import DestinationAccount


class DailyLossLimiter:
    """Enforces daily loss limits per account with circuit breaker logic."""

    def __init__(self, store: SignalStore):
        self.store = store

    def check_daily_loss_limit(
        self, account: DestinationAccount, daily_loss_limit_percent: Optional[float]
    ) -> Optional[str]:
        """Check if account has exceeded daily loss limit.

        Args:
            account: The destination account
            daily_loss_limit_percent: Maximum acceptable daily loss as percentage of equity (e.g., 5 for 5%)

        Returns:
            None if within limit, error message (rejection reason) if limit exceeded
        """
        if daily_loss_limit_percent is None or daily_loss_limit_percent <= 0:
            return None  # Daily loss limit not configured

        # Get today's P&L from account economics
        today = date.today()
        daily_pnl = self.store.get_daily_pnl(account.account_id, today)

        if daily_pnl is None:
            # No data yet for today
            return None

        # Get account balance to compute percentage
        if account.broker not in self.store._broker_adapters:
            # No broker available, fail closed
            return "Daily loss limit check failed: no broker adapter available"

        broker = self.store._broker_adapters[account.broker]
        try:
            balance = broker.get_account_balance(account)
            if balance is None or balance.equity is None:
                return "Daily loss limit check failed: cannot determine account equity"

            daily_loss_percent = abs(daily_pnl) / balance.equity * 100
            if daily_loss_percent >= daily_loss_limit_percent:
                return (
                    f"Daily loss limit breached: {daily_loss_percent:.2f}% loss "
                    f"(limit: {daily_loss_limit_percent:.2f}%, loss: {daily_pnl:.2f})"
                )
        except Exception as e:
            # Fail closed on any error
            return f"Daily loss limit check failed: {str(e)}"

        return None

    def halt_trading_if_limit_exceeded(
        self, account: DestinationAccount, daily_loss_limit_percent: Optional[float]
    ) -> bool:
        """Check if trading should be halted due to daily loss limit.

        Args:
            account: The destination account
            daily_loss_limit_percent: Maximum acceptable daily loss as percentage of equity

        Returns:
            True if trading is halted (daily loss limit exceeded)
        """
        return self.check_daily_loss_limit(account, daily_loss_limit_percent) is not None
