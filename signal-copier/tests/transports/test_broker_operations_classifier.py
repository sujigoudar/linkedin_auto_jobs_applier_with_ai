"""Tests for broker operations email classifier."""
from app.transports.broker_operations_classifier import (
    BrokerOperationEvent,
    BrokerOperationsClassifier,
    EventSeverity,
    OperationEventType,
)


class TestBrokerOperationsClassifier:
    """Test suite for BrokerOperationsClassifier."""

    def setup_method(self) -> None:
        """Initialize classifier for each test."""
        self.classifier = BrokerOperationsClassifier()

    def test_margin_call_detection(self) -> None:
        """Test detection of margin call events."""
        event = self.classifier.classify(
            subject="Margin Call Notice",
            sender="alerts@broker.com",
            body_excerpt="Your account margin requirement has fallen below minimum. Please deposit additional funds.",
        )
        assert event.event_type == OperationEventType.MARGIN_CALL
        assert event.severity == EventSeverity.CRITICAL

    def test_margin_call_pattern_variants(self) -> None:
        """Test multiple margin call pattern variations."""
        patterns = [
            "margin call alert",
            "margin requirement below threshold",
            "insufficient margin",
            "buying power deficit",
        ]
        for pattern in patterns:
            event = self.classifier.classify(
                subject=pattern,
                sender="alerts@broker.com",
                body_excerpt=pattern,
            )
            assert event.event_type == OperationEventType.MARGIN_CALL
            assert event.severity == EventSeverity.CRITICAL

    def test_trading_restriction_detection(self) -> None:
        """Test detection of trading restriction events."""
        event = self.classifier.classify(
            subject="Account Trading Restricted",
            sender="operations@broker.com",
            body_excerpt="Your account has been locked due to suspicious activity.",
        )
        assert event.event_type == OperationEventType.TRADING_RESTRICTION
        assert event.severity == EventSeverity.CRITICAL

    def test_trading_restriction_variants(self) -> None:
        """Test multiple trading restriction pattern variations."""
        patterns = [
            "trading disabled",
            "account suspended",
            "pattern day trader restriction",
            "cannot place new trades",
        ]
        for pattern in patterns:
            event = self.classifier.classify(
                subject=pattern,
                sender="operations@broker.com",
                body_excerpt=pattern,
            )
            assert event.event_type == OperationEventType.TRADING_RESTRICTION
            assert event.severity == EventSeverity.CRITICAL

    def test_position_adjustment_detection(self) -> None:
        """Test detection of position adjustment/liquidation events."""
        event = self.classifier.classify(
            subject="Forced Liquidation Notice",
            sender="risk@broker.com",
            body_excerpt="Your position has been closed due to risk management rules.",
        )
        assert event.event_type == OperationEventType.POSITION_ADJUSTMENT
        assert event.severity == EventSeverity.CRITICAL

    def test_fill_confirmation_detection(self) -> None:
        """Test detection of fill confirmation events."""
        event = self.classifier.classify(
            subject="Trade Execution Report",
            sender="fills@broker.com",
            body_excerpt="Your order has been filled. 100 shares @ $50.00",
        )
        assert event.event_type == OperationEventType.FILL_CONFIRMATION
        assert event.severity == EventSeverity.INFORMATIONAL

    def test_account_statement_detection(self) -> None:
        """Test detection of account statement events."""
        event = self.classifier.classify(
            subject="Daily Account Statement",
            sender="statements@broker.com",
            body_excerpt="Your daily statement is ready. Account balance: $100,000",
        )
        assert event.event_type == OperationEventType.ACCOUNT_STATEMENT
        assert event.severity == EventSeverity.INFORMATIONAL

    def test_security_incident_detection(self) -> None:
        """Test detection of security incident events."""
        event = self.classifier.classify(
            subject="Security Alert: Password Reset Required",
            sender="security@broker.com",
            body_excerpt="For your account safety, please reset your password within 24 hours.",
        )
        assert event.event_type == OperationEventType.SECURITY_INCIDENT
        assert event.severity == EventSeverity.CRITICAL

    def test_system_maintenance_detection(self) -> None:
        """Test detection of system maintenance events."""
        event = self.classifier.classify(
            subject="Platform Maintenance Scheduled",
            sender="operations@broker.com",
            body_excerpt="System maintenance window: Sunday 2am-4am EST. Market closed.",
        )
        assert event.event_type == OperationEventType.SYSTEM_MAINTENANCE
        assert event.severity == EventSeverity.WARNING

    def test_dividend_corporate_action_detection(self) -> None:
        """Test detection of dividend and corporate action events."""
        event = self.classifier.classify(
            subject="Dividend Payment Notice",
            sender="corporate@broker.com",
            body_excerpt="A dividend distribution has been credited to your account.",
        )
        assert event.event_type == OperationEventType.DIVIDEND_CORPORATE_ACTION
        assert event.severity == EventSeverity.WARNING

    def test_corporate_action_variants(self) -> None:
        """Test corporate action pattern variations."""
        patterns = [
            "stock split notice",
            "merger announcement",
            "corporate action event",
        ]
        for pattern in patterns:
            event = self.classifier.classify(
                subject=pattern,
                sender="corporate@broker.com",
                body_excerpt=pattern,
            )
            assert event.event_type == OperationEventType.DIVIDEND_CORPORATE_ACTION
            assert event.severity == EventSeverity.WARNING

    def test_transfer_deposit_detection(self) -> None:
        """Test detection of transfer/deposit events."""
        event = self.classifier.classify(
            subject="Deposit Confirmed",
            sender="operations@broker.com",
            body_excerpt="Wire transfer received and processed. Amount: $10,000",
        )
        assert event.event_type == OperationEventType.TRANSFER_DEPOSIT
        assert event.severity == EventSeverity.INFORMATIONAL

    def test_transfer_variants(self) -> None:
        """Test transfer pattern variations."""
        patterns = [
            "ach received",
            "check deposited",
            "fund transfer processed",
            "cash withdrawal confirmed",
        ]
        for pattern in patterns:
            event = self.classifier.classify(
                subject=pattern,
                sender="operations@broker.com",
                body_excerpt=pattern,
            )
            assert event.event_type == OperationEventType.TRANSFER_DEPOSIT
            assert event.severity == EventSeverity.INFORMATIONAL

    def test_unknown_operation_fallback(self) -> None:
        """Test that unknown operations return UNKNOWN_OPERATION type."""
        event = self.classifier.classify(
            subject="Random Message About Something Else",
            sender="random@broker.com",
            body_excerpt="This email doesn't match any known operational pattern.",
        )
        assert event.event_type == OperationEventType.UNKNOWN_OPERATION
        assert event.severity == EventSeverity.INFORMATIONAL

    def test_account_extraction(self) -> None:
        """Test extraction of account number from email body."""
        event = self.classifier.classify(
            subject="Margin Call",
            sender="alerts@broker.com",
            body_excerpt="Account: ABC-12345-XYZ. Your margin has been exceeded.",
        )
        assert event.extracted_account == "ABC-12345-XYZ"

    def test_account_extraction_with_hash(self) -> None:
        """Test account extraction with # format."""
        event = self.classifier.classify(
            subject="Account Alert",
            sender="alerts@broker.com",
            body_excerpt="Account #12345-ABCD requires action.",
        )
        assert event.extracted_account == "12345-ABCD"

    def test_amount_extraction_with_dollar_sign(self) -> None:
        """Test extraction of dollar amounts."""
        event = self.classifier.classify(
            subject="Margin Call",
            sender="alerts@broker.com",
            body_excerpt="You need to deposit $5,500.50 to meet margin requirements.",
        )
        assert event.extracted_amount == "$5,500.50"

    def test_amount_extraction_without_dollar_sign(self) -> None:
        """Test extraction of amounts without dollar sign."""
        event = self.classifier.classify(
            subject="Deposit Notice",
            sender="operations@broker.com",
            body_excerpt="Amount deposited: 2500 USD",
        )
        assert event.extracted_amount == "2500 USD"

    def test_deadline_extraction(self) -> None:
        """Test extraction of action deadline."""
        event = self.classifier.classify(
            subject="Action Required",
            sender="alerts@broker.com",
            body_excerpt="Please respond by January 15, 2024 or your account will be closed.",
        )
        assert event.extracted_deadline is not None
        assert "January 15" in event.extracted_deadline

    def test_deadline_extraction_simple_format(self) -> None:
        """Test deadline extraction with simple date format."""
        event = self.classifier.classify(
            subject="Deadline",
            sender="alerts@broker.com",
            body_excerpt="Deadline: December 31. You must act by this date.",
        )
        assert event.extracted_deadline is not None
        assert "December 31" in event.extracted_deadline

    def test_event_data_completeness(self) -> None:
        """Test that returned event contains all expected fields."""
        event = self.classifier.classify(
            subject="Test Subject",
            sender="test@broker.com",
            body_excerpt="Test body content",
        )
        assert isinstance(event, BrokerOperationEvent)
        assert event.event_type is not None
        assert event.severity is not None
        assert event.subject == "Test Subject"
        assert event.sender == "test@broker.com"
        assert event.classification_confidence == 1.0

    def test_body_excerpt_truncation(self) -> None:
        """Test that body excerpt is truncated to 500 chars."""
        long_body = "x" * 1000
        event = self.classifier.classify(
            subject="Margin Call",
            sender="alerts@broker.com",
            body_excerpt=long_body,
        )
        assert event.raw_body_excerpt is not None
        assert len(event.raw_body_excerpt) <= 500

    def test_case_insensitive_matching(self) -> None:
        """Test that pattern matching is case-insensitive."""
        event = self.classifier.classify(
            subject="MARGIN CALL ALERT",
            sender="alerts@broker.com",
            body_excerpt="YOUR ACCOUNT MARGIN LEVEL IS BELOW THRESHOLD",
        )
        assert event.event_type == OperationEventType.MARGIN_CALL

    def test_empty_body_handling(self) -> None:
        """Test handling of empty or minimal body content."""
        event = self.classifier.classify(
            subject="Margin Call Alert",
            sender="alerts@broker.com",
            body_excerpt="",
        )
        # Should still classify based on subject
        assert event.event_type == OperationEventType.MARGIN_CALL

    def test_subject_and_body_combined_matching(self) -> None:
        """Test that classification uses both subject and body."""
        # Pattern in body only
        event = self.classifier.classify(
            subject="Important Notice",
            sender="alerts@broker.com",
            body_excerpt="Your account has a margin call requirement",
        )
        assert event.event_type == OperationEventType.MARGIN_CALL

    def test_no_false_positives_on_similar_words(self) -> None:
        """Test that similar words don't trigger false positives."""
        event = self.classifier.classify(
            subject="Discussion about margins in trading",
            sender="support@broker.com",
            body_excerpt="Let's discuss the margin concept in trading strategies.",
        )
        # "margin" alone without "call" shouldn't trigger margin call
        assert event.event_type == OperationEventType.UNKNOWN_OPERATION
