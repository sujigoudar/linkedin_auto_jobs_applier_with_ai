"""Tests for broker operations incident escalation system."""
from app.transports.broker_operations_classifier import (
    BrokerOperationEvent,
    EventSeverity,
    OperationEventType,
)
from app.transports.broker_operations_escalator import (
    BrokerOperationsIncidentEscalator,
    EscalationAction,
)


class TestBrokerOperationsEscalator:
    """Test suite for BrokerOperationsIncidentEscalator."""

    def setup_method(self) -> None:
        """Initialize escalator for each test (no store yet)."""
        self.escalator = BrokerOperationsIncidentEscalator(store=None)

    def test_critical_severity_escalates_immediately(self) -> None:
        """Test that CRITICAL events trigger immediate escalation."""
        event = BrokerOperationEvent(
            event_type=OperationEventType.MARGIN_CALL,
            severity=EventSeverity.CRITICAL,
            subject="Margin Call Alert",
            sender="alerts@broker.com",
            extracted_account="ACC123",
            extracted_amount="$5000",
        )

        escalation = self.escalator.escalate(
            event=event,
            inbox_id="agentmail:operations:owner123",
            account_owner_id="owner123",
        )

        assert escalation.action == EscalationAction.NOTIFY_IMMEDIATELY
        assert escalation.severity == EventSeverity.CRITICAL
        assert escalation.event_type == OperationEventType.MARGIN_CALL

    def test_warning_severity_queues_for_review(self) -> None:
        """Test that WARNING events are queued for review."""
        event = BrokerOperationEvent(
            event_type=OperationEventType.SYSTEM_MAINTENANCE,
            severity=EventSeverity.WARNING,
            subject="Platform Maintenance Scheduled",
            sender="operations@broker.com",
        )

        escalation = self.escalator.escalate(
            event=event,
            inbox_id="agentmail:operations:owner123",
            account_owner_id="owner123",
        )

        assert escalation.action == EscalationAction.QUEUE_FOR_REVIEW
        assert escalation.severity == EventSeverity.WARNING

    def test_informational_severity_logs_only(self) -> None:
        """Test that INFORMATIONAL events are logged only."""
        event = BrokerOperationEvent(
            event_type=OperationEventType.FILL_CONFIRMATION,
            severity=EventSeverity.INFORMATIONAL,
            subject="Trade Execution Report",
            sender="fills@broker.com",
        )

        escalation = self.escalator.escalate(
            event=event,
            inbox_id="agentmail:operations:owner123",
            account_owner_id="owner123",
        )

        assert escalation.action == EscalationAction.LOG_ONLY
        assert escalation.severity == EventSeverity.INFORMATIONAL

    def test_escalation_preserves_event_data(self) -> None:
        """Test that escalation preserves all event data."""
        event = BrokerOperationEvent(
            event_type=OperationEventType.TRADING_RESTRICTION,
            severity=EventSeverity.CRITICAL,
            subject="Account Trading Restricted",
            sender="operations@broker.com",
            extracted_account="ACC456",
            extracted_amount="$10000",
            extracted_deadline="January 15",
        )

        escalation = self.escalator.escalate(
            event=event,
            inbox_id="agentmail:operations:owner456",
            account_owner_id="owner456",
        )

        assert escalation.event_type == OperationEventType.TRADING_RESTRICTION
        assert escalation.subject == "Account Trading Restricted"
        assert escalation.sender == "operations@broker.com"
        assert escalation.extracted_account == "ACC456"
        assert escalation.extracted_amount == "$10000"
        assert escalation.extracted_deadline == "January 15"

    def test_escalation_creates_unique_incident_ids(self) -> None:
        """Test that each escalation gets a unique incident ID."""
        event = BrokerOperationEvent(
            event_type=OperationEventType.MARGIN_CALL,
            severity=EventSeverity.CRITICAL,
            subject="Margin Call Alert",
            sender="alerts@broker.com",
        )

        escalation1 = self.escalator.escalate(
            event=event,
            inbox_id="agentmail:operations:owner1",
            account_owner_id="owner1",
        )

        escalation2 = self.escalator.escalate(
            event=event,
            inbox_id="agentmail:operations:owner2",
            account_owner_id="owner2",
        )

        assert escalation1.incident_id != escalation2.incident_id
        assert len(escalation1.incident_id) > 0
        assert len(escalation2.incident_id) > 0

    def test_escalation_has_created_at_timestamp(self) -> None:
        """Test that escalation records have creation timestamp."""
        event = BrokerOperationEvent(
            event_type=OperationEventType.MARGIN_CALL,
            severity=EventSeverity.CRITICAL,
            subject="Margin Call Alert",
            sender="alerts@broker.com",
        )

        escalation = self.escalator.escalate(
            event=event,
            inbox_id="agentmail:operations:owner123",
            account_owner_id="owner123",
        )

        assert escalation.created_at is not None
        assert escalation.created_at.tzinfo is not None  # UTC aware

    def test_all_critical_event_types_escalate_immediately(self) -> None:
        """Test that all CRITICAL event types are handled correctly."""
        critical_types = [
            OperationEventType.MARGIN_CALL,
            OperationEventType.TRADING_RESTRICTION,
            OperationEventType.POSITION_ADJUSTMENT,
            OperationEventType.SECURITY_INCIDENT,
        ]

        for event_type in critical_types:
            event = BrokerOperationEvent(
                event_type=event_type,
                severity=EventSeverity.CRITICAL,
                subject="Test Event",
                sender="test@broker.com",
            )

            escalation = self.escalator.escalate(
                event=event,
                inbox_id="agentmail:operations:owner123",
                account_owner_id="owner123",
            )

            assert escalation.action == EscalationAction.NOTIFY_IMMEDIATELY
            assert escalation.event_type == event_type

    def test_all_warning_event_types_queue_for_review(self) -> None:
        """Test that all WARNING event types queue for review."""
        warning_types = [
            OperationEventType.SYSTEM_MAINTENANCE,
            OperationEventType.DIVIDEND_CORPORATE_ACTION,
        ]

        for event_type in warning_types:
            event = BrokerOperationEvent(
                event_type=event_type,
                severity=EventSeverity.WARNING,
                subject="Test Event",
                sender="test@broker.com",
            )

            escalation = self.escalator.escalate(
                event=event,
                inbox_id="agentmail:operations:owner123",
                account_owner_id="owner123",
            )

            assert escalation.action == EscalationAction.QUEUE_FOR_REVIEW
            assert escalation.event_type == event_type

    def test_all_informational_event_types_log_only(self) -> None:
        """Test that all INFORMATIONAL event types log only."""
        info_types = [
            OperationEventType.FILL_CONFIRMATION,
            OperationEventType.ACCOUNT_STATEMENT,
            OperationEventType.TRANSFER_DEPOSIT,
            OperationEventType.UNKNOWN_OPERATION,
        ]

        for event_type in info_types:
            event = BrokerOperationEvent(
                event_type=event_type,
                severity=EventSeverity.INFORMATIONAL,
                subject="Test Event",
                sender="test@broker.com",
            )

            escalation = self.escalator.escalate(
                event=event,
                inbox_id="agentmail:operations:owner123",
                account_owner_id="owner123",
            )

            assert escalation.action == EscalationAction.LOG_ONLY
            assert escalation.event_type == event_type

    def test_duplicate_check_returns_false_by_default(self) -> None:
        """Test that duplicate checking returns False when not implemented."""
        event = BrokerOperationEvent(
            event_type=OperationEventType.MARGIN_CALL,
            severity=EventSeverity.CRITICAL,
            subject="Margin Call Alert",
            sender="alerts@broker.com",
        )

        escalation = self.escalator.escalate(
            event=event,
            inbox_id="agentmail:operations:owner123",
            account_owner_id="owner123",
        )

        # Default implementation returns False (no duplicate tracking yet)
        assert escalation.is_duplicate is False
        assert escalation.duplicate_of_incident_id is None

    def test_escalation_with_extracted_data(self) -> None:
        """Test escalation with complete extracted data."""
        event = BrokerOperationEvent(
            event_type=OperationEventType.MARGIN_CALL,
            severity=EventSeverity.CRITICAL,
            subject="Urgent: Margin Call",
            sender="margin@broker.com",
            extracted_account="ACC-789",
            extracted_amount="$25000.50",
            extracted_deadline="December 31, 2024",
            raw_body_excerpt="Your account margin has fallen below minimum...",
            classification_confidence=0.95,
        )

        escalation = self.escalator.escalate(
            event=event,
            inbox_id="agentmail:operations:owner789",
            account_owner_id="owner789",
        )

        assert escalation.extracted_account == "ACC-789"
        assert escalation.extracted_amount == "$25000.50"
        assert escalation.extracted_deadline == "December 31, 2024"
        assert escalation.action == EscalationAction.NOTIFY_IMMEDIATELY

    def test_escalation_without_store_still_works(self) -> None:
        """Test that escalation works even without a SignalStore."""
        escalator = BrokerOperationsIncidentEscalator(store=None)

        event = BrokerOperationEvent(
            event_type=OperationEventType.MARGIN_CALL,
            severity=EventSeverity.CRITICAL,
            subject="Margin Call Alert",
            sender="alerts@broker.com",
        )

        escalation = escalator.escalate(
            event=event,
            inbox_id="agentmail:operations:owner123",
            account_owner_id="owner123",
        )

        assert escalation.incident_id is not None
        assert escalation.action == EscalationAction.NOTIFY_IMMEDIATELY
