"""E04 (bounded): Margin call detection and alert persistence.

Monitors account maintenance requirements vs available margin and persists
margin call alerts when equity approaches broker maintenance thresholds.
Implements fail-closed pattern: if margin state cannot be determined, alerts
are raised to prevent silent failures.

This module is designed to work with broker adapters that report:
- current_equity: float
- maintenance_requirement: float (or can be derived from account state)
- excess_margin: float (equity - maintenance_requirement)
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from app.db import SignalStore
from app.models import DestinationAccount

logger = logging.getLogger(__name__)


class MarginCallDetector:
    """Detects and persists margin call conditions."""

    def __init__(self, store: SignalStore):
        self.store = store

    def check_and_persist_margin_call(
        self,
        account: DestinationAccount,
        current_equity: Optional[float],
        maintenance_requirement: Optional[float],
        excess_margin: Optional[float],
        broker: str,
    ) -> Optional[str]:
        """
        Check for margin call conditions and persist alert if margin is breached.

        Args:
            account: The destination account
            current_equity: Current account equity (None = unknown)
            maintenance_requirement: Broker maintenance requirement (None = unknown)
            excess_margin: Available margin above requirement (None = unknown, can be derived)
            broker: Broker name for record-keeping

        Returns:
            Error message if unable to determine margin state or if margin call exists,
            None if margin is sufficient. Fail-closed: when in doubt, return error.
        """
        # Fail-closed: if we can't determine margin state with required precision, reject
        if current_equity is None or maintenance_requirement is None:
            return (
                f"Cannot determine margin state for {account.account_id}: "
                f"equity={current_equity}, maintenance={maintenance_requirement}"
            )

        # Calculate excess margin if not provided
        if excess_margin is None:
            excess_margin = current_equity - maintenance_requirement

        # Check if margin call exists (excess_margin <= 0 means margin call)
        if excess_margin <= 0:
            try:
                self.store.persist_margin_call_alert(
                    account_id=account.account_id,
                    current_equity=current_equity,
                    maintenance_requirement=maintenance_requirement,
                    excess_margin=excess_margin,
                    broker=broker,
                )
                logger.warning(
                    f"Margin call detected for {account.account_id}: "
                    f"equity={current_equity}, required={maintenance_requirement}, "
                    f"excess={excess_margin}"
                )
                return (
                    f"Margin call on {broker}: excess_margin={excess_margin:.2f} "
                    f"(equity {current_equity:.2f} < requirement {maintenance_requirement:.2f})"
                )
            except Exception as e:
                # Fail-closed: if we can't persist the alert, prevent trading
                logger.error(f"Failed to persist margin call alert: {e}")
                return f"Unable to persist margin call alert: {e}"

        # Check for warning threshold (e.g., excess_margin < 10% of requirement)
        warning_threshold = maintenance_requirement * 0.10
        if excess_margin < warning_threshold:
            logger.warning(
                f"Margin warning for {account.account_id}: "
                f"excess_margin={excess_margin:.2f} below threshold {warning_threshold:.2f}"
            )

        return None

    def get_unresolved_margin_calls(self, account_id: str) -> list[dict]:
        """Get all unresolved margin call alerts for an account."""
        return self.store.get_unresolved_margin_calls(account_id)

    def resolve_margin_call(self, alert_id: int) -> bool:
        """Mark a margin call alert as resolved."""
        try:
            self.store.resolve_margin_call_alert(alert_id)
            return True
        except Exception as e:
            logger.error(f"Failed to resolve margin call alert {alert_id}: {e}")
            return False
