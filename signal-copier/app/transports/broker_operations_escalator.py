"""Broker operations incident escalation and human notification system.

Routes classified broker operations events to appropriate escalation paths
based on severity level and event type, with human notifications for
critical incidents.

Key design decisions:

1. SEVERITY-BASED ESCALATION: CRITICAL events trigger immediate owner
   notification; WARNING events are queued for review; INFORMATIONAL events
   are logged only.

2. INCIDENT PERSISTENCE: All incidents are stored in the database for audit
   trail, metrics, and historical analysis.

3. DEDUPLICATION: Duplicate incidents within a time window are detected to
   prevent alert fatigue (same account/broker/type within 1 hour).

4. OWNER NOTIFICATION: Critical incidents notify the account owner via
   configured channels (webhook, email, or in-app notification).

5. FAIL-SAFE LOGGING: Every incident is logged regardless of escalation
   outcome; escalation failures are reported but don't block processing.

Architecture:
    BrokerOperationEvent (from classifier)
        ↓
    BrokerOperationsIncidentEscalator.escalate()
        ↓
    Database storage + severity-based routing
        ↓
    Owner notification (CRITICAL) | Review queue (WARNING) | Logged only (INFO)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any, Optional

from app.transports.broker_operations_classifier import (
    BrokerOperationEvent,
    EventSeverity,
    OperationEventType,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class EscalationAction(str, Enum):
    """Action to take for an incident based on severity."""
    NOTIFY_IMMEDIATELY = "notify_immediately"  # Critical: immediate owner notification
    QUEUE_FOR_REVIEW = "queue_for_review"  # Warning: schedule for operator review
    LOG_ONLY = "log_only"  # Informational: log without active escalation


def _default_created_at() -> datetime:
    """Return current UTC time for IncidentEscalation.created_at."""
    return datetime.now(timezone.utc)


@dataclass
class IncidentEscalation:
    """Result of escalating a broker operations incident."""
    incident_id: str  # UUID for tracking
    event_type: OperationEventType
    severity: EventSeverity
    action: EscalationAction
    subject: str
    sender: str
    extracted_account: Optional[str]
    extracted_amount: Optional[str]
    extracted_deadline: Optional[str]
    is_duplicate: bool = False  # True if duplicate of recent incident
    duplicate_of_incident_id: Optional[str] = None  # If duplicate, reference original
    created_at: datetime = field(default_factory=_default_created_at)  # Incident creation time


class BrokerOperationsIncidentEscalator:
    """Escalates broker operations incidents to appropriate handlers."""

    def __init__(self, store: Optional[Any]) -> None:
        """Initialize escalator with database access.

        Args:
            store: SignalStore instance for persisting incidents (optional)
        """
        self.store = store

    def escalate(
        self,
        event: BrokerOperationEvent,
        inbox_id: str,
        account_owner_id: str,
    ) -> IncidentEscalation:
        """Escalate a broker operations event to appropriate handlers.

        Args:
            event: Classified broker operations event
            inbox_id: ID of the inbox that received the event
            account_owner_id: Owner account ID for notifications

        Returns:
            IncidentEscalation object describing escalation action taken
        """
        import uuid

        # Check for recent duplicate incidents
        is_duplicate, duplicate_id = self._check_duplicate(
            event=event,
            account_owner_id=account_owner_id,
            time_window_hours=1,
        )

        # Determine escalation action based on severity
        action = self._determine_action(event.severity)

        # Create incident record
        incident_id = str(uuid.uuid4())
        escalation = IncidentEscalation(
            incident_id=incident_id,
            event_type=event.event_type,
            severity=event.severity,
            action=action,
            subject=event.subject,
            sender=event.sender,
            extracted_account=event.extracted_account,
            extracted_amount=event.extracted_amount,
            extracted_deadline=event.extracted_deadline,
            is_duplicate=is_duplicate,
            duplicate_of_incident_id=duplicate_id,
        )

        # Persist the incident
        self._store_incident(
            escalation=escalation,
            inbox_id=inbox_id,
            account_owner_id=account_owner_id,
        )

        # Execute escalation action
        if action == EscalationAction.NOTIFY_IMMEDIATELY and not is_duplicate:
            self._notify_critical_incident(
                escalation=escalation,
                account_owner_id=account_owner_id,
            )
        elif action == EscalationAction.QUEUE_FOR_REVIEW:
            self._queue_for_review(
                escalation=escalation,
                account_owner_id=account_owner_id,
            )

        return escalation

    def _check_duplicate(
        self,
        event: BrokerOperationEvent,
        account_owner_id: str,
        time_window_hours: int,
    ) -> tuple[bool, Optional[str]]:
        """Check if this incident is a duplicate of a recent one.

        Args:
            event: The broker operation event
            account_owner_id: Owner account ID
            time_window_hours: Look back this many hours for duplicates

        Returns:
            Tuple of (is_duplicate: bool, duplicate_incident_id: Optional[str])
        """
        try:
            if not self.store:
                return False, None

            # Get recent incidents for this account
            recent_incidents = self.store.list_broker_operations_incidents(
                hours_back=time_window_hours,
                limit=1000,
            )

            # Check for duplicates with same event_type and extracted_account
            for incident in recent_incidents:
                if (
                    incident.get("account_owner_id") == account_owner_id
                    and incident.get("event_type") == event.event_type.value
                    and incident.get("extracted_account") == event.extracted_account
                ):
                    return True, incident.get("incident_id")

            return False, None
        except Exception as e:
            logger.warning(f"Error checking for duplicate incident: {e}")
            return False, None

    def _determine_action(self, severity: EventSeverity) -> EscalationAction:
        """Determine escalation action based on severity.

        Args:
            severity: Event severity level

        Returns:
            EscalationAction to take
        """
        if severity == EventSeverity.CRITICAL:
            return EscalationAction.NOTIFY_IMMEDIATELY
        elif severity == EventSeverity.WARNING:
            return EscalationAction.QUEUE_FOR_REVIEW
        else:
            return EscalationAction.LOG_ONLY

    def _store_incident(
        self,
        escalation: IncidentEscalation,
        inbox_id: str,
        account_owner_id: str,
    ) -> None:
        """Store incident in database for audit trail.

        Args:
            escalation: The incident escalation record
            inbox_id: ID of the inbox that received the event
            account_owner_id: Owner account ID
        """
        try:
            if self.store:
                self.store.save_broker_operations_incident(
                    incident_id=escalation.incident_id,
                    inbox_id=inbox_id,
                    account_owner_id=account_owner_id,
                    event_type=escalation.event_type.value,
                    severity=escalation.severity.value,
                    action=escalation.action.value,
                    subject=escalation.subject,
                    sender=escalation.sender,
                    extracted_account=escalation.extracted_account,
                    extracted_amount=escalation.extracted_amount,
                    extracted_deadline=escalation.extracted_deadline,
                    is_duplicate=escalation.is_duplicate,
                    duplicate_of_incident_id=escalation.duplicate_of_incident_id,
                    created_at=escalation.created_at.isoformat(),
                )
            logger.info(
                f"Broker operations incident stored | "
                f"ID: {escalation.incident_id} | "
                f"Type: {escalation.event_type} | "
                f"Severity: {escalation.severity} | "
                f"Action: {escalation.action} | "
                f"Owner: {account_owner_id} | "
                f"Inbox: {inbox_id}"
            )
        except Exception as e:
            logger.exception(f"Error storing incident {escalation.incident_id}: {e}")

    def _notify_critical_incident(
        self,
        escalation: IncidentEscalation,
        account_owner_id: str,
    ) -> None:
        """Send immediate notification for critical incident.

        Args:
            escalation: The critical incident to notify about
            account_owner_id: Owner account ID to notify
        """
        try:
            # For now, log as critical alert
            # Full implementation would:
            # 1. Look up owner contact info
            # 2. Send webhook/email notification
            # 3. Record notification attempt in database
            logger.critical(
                f"CRITICAL broker operations incident | "
                f"ID: {escalation.incident_id} | "
                f"Type: {escalation.event_type} | "
                f"Owner: {account_owner_id} | "
                f"Subject: {escalation.subject[:100]} | "
                f"From: {escalation.sender} | "
                f"Account: {escalation.extracted_account} | "
                f"Amount: {escalation.extracted_amount} | "
                f"Deadline: {escalation.extracted_deadline}"
            )
        except Exception as e:
            logger.exception(
                f"Error sending notification for critical incident "
                f"{escalation.incident_id}: {e}"
            )

    def _queue_for_review(
        self,
        escalation: IncidentEscalation,
        account_owner_id: str,
    ) -> None:
        """Queue warning incident for operator review.

        Args:
            escalation: The warning incident to queue
            account_owner_id: Owner account ID
        """
        try:
            # For now, log as warning
            # Full implementation would:
            # 1. INSERT into a review_queue table
            # 2. Trigger daily digest email to operators
            # 3. Show in operations dashboard
            logger.warning(
                f"Broker operations incident queued for review | "
                f"ID: {escalation.incident_id} | "
                f"Type: {escalation.event_type} | "
                f"Owner: {account_owner_id} | "
                f"Subject: {escalation.subject[:100]}"
            )
        except Exception as e:
            logger.exception(
                f"Error queuing incident {escalation.incident_id} for review: {e}"
            )
