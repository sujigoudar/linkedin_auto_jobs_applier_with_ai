"""Daily loss limit enforcement with circuit breaker.

Prevents trading when daily losses exceed configured ceiling for an account.
Implements fail-closed: rejects new entries if daily loss limit breached.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from app.db import SignalStore
from app.models import DestinationAccount


class DailyLossLimiter:
    """Enforces daily loss limits per account with circuit breaker logic."""

    def __init__(self, store: SignalStore):
        self.store = store

    async def check_daily_loss_limit(
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
        get_daily_pnl = getattr(self.store, "get_daily_pnl", None)
        if get_daily_pnl is None:
            # A limit IS configured but this build has no daily-P&L source
            # (SignalStore.get_daily_pnl does not exist). Reject (fail
            # closed) rather than raise out of signal handling or silently
            # skip a configured circuit breaker.
            return "Daily loss limit check failed: no daily P&L source is implemented in this build (failing closed)"
        daily_pnl = get_daily_pnl(account.account_id, today)

        if daily_pnl is None:
            # No data yet for today
            return None

        # Get account balance to compute percentage
        if account.broker not in getattr(self.store, "_broker_adapters", {}):
            # No broker available, fail closed
            return "Daily loss limit check failed: no broker adapter available"

        broker = self.store._broker_adapters[account.broker]  # type: ignore[attr-defined]
        try:
            balance = await broker.get_account_balance(account)
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

    async def halt_trading_if_limit_exceeded(
        self, account: DestinationAccount, daily_loss_limit_percent: Optional[float]
    ) -> bool:
        """Check if trading should be halted due to daily loss limit.

        Args:
            account: The destination account
            daily_loss_limit_percent: Maximum acceptable daily loss as percentage of equity

        Returns:
            True if trading is halted (daily loss limit exceeded)
        """
        return await self.check_daily_loss_limit(account, daily_loss_limit_percent) is not None

    async def check_min_equity_threshold(
        self, account: DestinationAccount, min_equity_threshold: Optional[float]
    ) -> Optional[str]:
        """Check if account equity meets minimum threshold.

        Account liquidation circuit breaker: rejects new entries if account equity
        would fall below configured minimum. Implements fail-closed: if equity
        cannot be determined, rejects rather than guessing.

        Args:
            account: The destination account
            min_equity_threshold: Minimum acceptable equity (in account currency).
                                  None = check disabled (default).

        Returns:
            None if equity is above threshold, error message if below or cannot be determined
        """
        if min_equity_threshold is None or min_equity_threshold <= 0:
            return None  # Min equity threshold not configured

        # Get current account balance to check equity
        if account.broker not in getattr(self.store, "_broker_adapters", {}):
            # No broker available, fail closed
            return "Min equity check failed: no broker adapter available"

        broker = self.store._broker_adapters[account.broker]  # type: ignore[attr-defined]
        try:
            balance = await broker.get_account_balance(account)
            if balance is None or balance.equity is None:
                return "Min equity check failed: cannot determine current account equity"

            if balance.equity < min_equity_threshold:
                return (
                    f"Account liquidation: equity {balance.equity:.2f} "
                    f"below minimum threshold {min_equity_threshold:.2f}"
                )
        except Exception as e:
            # Fail closed on any error
            return f"Min equity check failed: {str(e)}"

        return None
