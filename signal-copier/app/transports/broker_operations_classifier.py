"""Broker operations email classifier.

Identifies and categorizes operational emails from brokers into event types
that trigger appropriate system responses: margin calls, trading restrictions,
position adjustments, security incidents, etc.

Key design decisions:

1. PATTERN-BASED CLASSIFICATION: Uses keywords and patterns from subject and body
   to identify event type reliably without ML/inference.

2. SEVERITY ASSESSMENT: Classifies severity (critical/warning/informational) to
   guide escalation decisions in the incident system.

3. STRUCTURED EXTRACTION: Extracts key data (account, amount, deadline) for
   downstream processing and alerts.

4. MULTIPLE SIGNALS: Combines subject, sender, and body analysis for robustness.

5. FAIL-CLOSED ON UNCERTAINTY: Unknown operation types return UNKNOWN_OPERATION
   rather than misclassifying to a wrong category.

Architecture:
    SourceReceipt (from email transport)
        ↓
    BrokerOperationsClassifier.classify()
        ↓
    BrokerOperationEvent (type, severity, extracted data)
        ↓
    Incident system receives event for escalation/action
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class OperationEventType(str, Enum):
    """Classification of operational events from brokers."""
    MARGIN_CALL = "margin_call"  # Margin requirement below threshold
    TRADING_RESTRICTION = "trading_restriction"  # Account locked/restricted
    POSITION_ADJUSTMENT = "position_adjustment"  # Forced liquidation, closeout
    FILL_CONFIRMATION = "fill_confirmation"  # Trade execution report
    ACCOUNT_STATEMENT = "account_statement"  # Daily/periodic account snapshot
    SECURITY_INCIDENT = "security_incident"  # Account compromise, password reset required
    SYSTEM_MAINTENANCE = "system_maintenance"  # Broker infrastructure event
    DIVIDEND_CORPORATE_ACTION = "dividend_corporate_action"  # Dividend, split, merger
    TRANSFER_DEPOSIT = "transfer_deposit"  # Fund transfer event
    UNKNOWN_OPERATION = "unknown_operation"  # Could not classify


class EventSeverity(str, Enum):
    """Severity of operational event."""
    CRITICAL = "critical"  # Immediate action required (margin call, restriction)
    WARNING = "warning"  # Should be reviewed soon (corporate action, maintenance)
    INFORMATIONAL = "informational"  # FYI (fill report, statement)


@dataclass
class BrokerOperationEvent:
    """Classified broker operations event with extracted data."""
    event_type: OperationEventType
    severity: EventSeverity
    subject: str
    sender: str
    extracted_account: Optional[str] = None  # Account number/ID
    extracted_amount: Optional[str] = None  # Dollar amount or value
    extracted_deadline: Optional[str] = None  # Action deadline if applicable
    raw_body_excerpt: Optional[str] = None  # First 500 chars of body for context
    classification_confidence: float = 1.0  # 0.0-1.0 confidence in classification


class BrokerOperationsClassifier:
    """Identifies and categorizes operational emails from brokers."""

    def __init__(self) -> None:
        """Initialize classifier with pattern definitions."""
        self._margin_call_patterns = [
            r"margin\s+(?:call|requirement|alert|notice)",
            r"margin\s+level.*below",
            r"insufficient\s+margin",
            r"margin\s+excess.*positive",
            r"(?:margin|buying\s+power).*deficit",
        ]

        self._trading_restriction_patterns = [
            r"trading\s+(?:restricted|disabled|locked|halted)",
            r"account\s+(?:suspended|locked|restricted)",
            r"pattern\s+day\s+trader",
            r"(?:you|your)\s+account.*(?:locked|suspended|restricted)",
            r"(?:cannot|unable|not allowed).*(?:trade|execute|place)",
        ]

        self._position_adjustment_patterns = [
            r"(?:forced\s+)?(?:liquidation|closeout|closure)",
            r"position.*(?:closed|liquidated|flattened)",
            r"(?:reduce|cover).*risk",
            r"position\s+(?:reduction|halt)",
        ]

        self._fill_confirmation_patterns = [
            r"(?:trade\s+)?execution.*(?:report|confirmation|filled)",
            r"(?:your\s+)?order.*(?:filled|executed|confirmed)",
            r"fill.*(?:confirmation|report|notice)",
            r"execution.*details",
        ]

        self._account_statement_patterns = [
            r"(?:daily|weekly|monthly).*statement",
            r"account\s+statement",
            r"statement\s+(?:of\s+)?(?:account|position|holdings)",
            r"end-of-day\s+report",
        ]

        self._security_incident_patterns = [
            r"(?:password|login|account).*(?:reset|change|verify)",
            r"(?:unauthorized|suspicious).*(?:activity|access|login)",
            r"security\s+(?:alert|notice|incident)",
            r"verify\s+(?:identity|account)",
        ]

        self._system_maintenance_patterns = [
            r"(?:system|platform|market).*(?:maintenance|upgrade|downtime)",
            r"maintenance.*(?:window|schedule)",
            r"(?:scheduled|planned).*downtime",
            r"market.*closed",
        ]

        self._corporate_action_patterns = [
            r"(?:dividend|distribution|payment)",
            r"stock\s+(?:split|dividend|bonus)",
            r"corporate\s+(?:action|event)",
            r"(?:merger|acquisition|reorganization)",
        ]

        self._transfer_deposit_patterns = [
            r"(?:deposit|withdrawal|transfer).*(?:confirmed|received|processed)",
            r"(?:wire|ach|check).*(?:received|deposited|processed)",
            r"(?:fund|cash).*(?:added|transferred|withdrawn)",
        ]

    def classify(
        self,
        subject: str,
        sender: str,
        body_excerpt: str,
    ) -> BrokerOperationEvent:
        """Classify broker operations email.

        Args:
            subject: Email subject line
            sender: Sender email address
            body_excerpt: First part of email body (plain text)

        Returns:
            BrokerOperationEvent with classified type and extracted data
        """
        combined_text = f"{subject} {body_excerpt}".lower()

        # Try to match against all event type patterns
        event_type = self._match_event_type(combined_text)
        severity = self._assess_severity(event_type, combined_text)

        # Extract relevant data from body
        account = self._extract_account(body_excerpt)
        amount = self._extract_amount(body_excerpt)
        deadline = self._extract_deadline(body_excerpt)

        return BrokerOperationEvent(
            event_type=event_type,
            severity=severity,
            subject=subject,
            sender=sender,
            extracted_account=account,
            extracted_amount=amount,
            extracted_deadline=deadline,
            raw_body_excerpt=body_excerpt[:500] if body_excerpt else None,
        )

    def _match_event_type(self, text: str) -> OperationEventType:
        """Match text against event type patterns."""
        patterns = [
            (self._margin_call_patterns, OperationEventType.MARGIN_CALL),
            (self._trading_restriction_patterns, OperationEventType.TRADING_RESTRICTION),
            (self._position_adjustment_patterns, OperationEventType.POSITION_ADJUSTMENT),
            (self._fill_confirmation_patterns, OperationEventType.FILL_CONFIRMATION),
            (self._account_statement_patterns, OperationEventType.ACCOUNT_STATEMENT),
            (self._security_incident_patterns, OperationEventType.SECURITY_INCIDENT),
            (self._system_maintenance_patterns, OperationEventType.SYSTEM_MAINTENANCE),
            (self._corporate_action_patterns, OperationEventType.DIVIDEND_CORPORATE_ACTION),
            (self._transfer_deposit_patterns, OperationEventType.TRANSFER_DEPOSIT),
        ]

        for pattern_list, event_type in patterns:
            for pattern in pattern_list:
                if re.search(pattern, text):
                    return event_type

        return OperationEventType.UNKNOWN_OPERATION

    def _assess_severity(self, event_type: OperationEventType, text: str) -> EventSeverity:
        """Assess severity of event."""
        critical_types = {
            OperationEventType.MARGIN_CALL,
            OperationEventType.TRADING_RESTRICTION,
            OperationEventType.POSITION_ADJUSTMENT,
            OperationEventType.SECURITY_INCIDENT,
        }

        if event_type in critical_types:
            return EventSeverity.CRITICAL

        warning_types = {
            OperationEventType.SYSTEM_MAINTENANCE,
            OperationEventType.DIVIDEND_CORPORATE_ACTION,
        }

        if event_type in warning_types:
            return EventSeverity.WARNING

        return EventSeverity.INFORMATIONAL

    def _extract_account(self, text: str) -> Optional[str]:
        """Extract account number/ID from body."""
        patterns = [
            r"account\s*[#:]?\s*([A-Z0-9\-]{4,20})",
            r"acct\s*[#:]?\s*([A-Z0-9\-]{4,20})",
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1)

        return None

    def _extract_amount(self, text: str) -> Optional[str]:
        """Extract dollar amount from body."""
        pattern = r"\$[\d,]+\.?\d*|\b\d+[\d,]*\.?\d*\s*(?:USD|dollars?)\b"
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(0)

        return None

    def _extract_deadline(self, text: str) -> Optional[str]:
        """Extract action deadline from body."""
        patterns = [
            r"by\s+([A-Za-z]+\s+\d{1,2},?\s+\d{4})",
            r"deadline.*?([A-Za-z]+\s+\d{1,2})",
            r"(?:please\s+)?(?:respond|act|comply)\s+by\s+([A-Za-z]+\s+\d{1,2})",
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1)

        return None
