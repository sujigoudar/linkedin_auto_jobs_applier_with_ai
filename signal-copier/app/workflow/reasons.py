"""Decision reason codes from WORKFLOW_SPECIFICATION.md §2.

WC-01 creates this module with all reason codes and the admission blocking order.
"""
import enum


class Reason(str, enum.Enum):
    """Decision reason codes from §2 of WORKFLOW_SPECIFICATION.

    Every decision (admission, sizing, selection, order execution) returns
    one reason code. The enum members are defined in §2 of the spec.
    """

    # Authorization and configuration
    AUTH_REJECTED = "AUTH_REJECTED"
    SOURCE_AUTHORITY = "SOURCE_AUTHORITY"
    POLICY_DISABLED = "POLICY_DISABLED"

    # Interpretation and routing
    UNKNOWN_PROVIDER = "UNKNOWN_PROVIDER"
    PARSE_AMBIGUOUS = "PARSE_AMBIGUOUS"
    NOT_CURRENT_ACTIONABLE_ENTRY = "NOT_CURRENT_ACTIONABLE_ENTRY"
    NONACTIONABLE = "NONACTIONABLE"
    NO_ELIGIBLE_ROUTE = "NO_ELIGIBLE_ROUTE"
    UNROUTABLE = "UNROUTABLE"

    # Duplicate and conflict
    DUPLICATE = "DUPLICATE"
    OWNERSHIP_CONFLICT = "OWNERSHIP_CONFLICT"

    # Instrument and product
    INSTRUMENT_UNRESOLVED = "INSTRUMENT_UNRESOLVED"

    # Timing and validity
    WAIT_TRIGGER = "WAIT_TRIGGER"
    EXPIRED = "EXPIRED"

    # Budget and capital
    CAPITAL_LIMITED = "CAPITAL_LIMITED"
    BUDGET_NOT_ADMISSIBLE = "BUDGET_NOT_ADMISSIBLE"

    # Risk and margin
    RISK_LIMITED = "RISK_LIMITED"
    MARGIN_UNKNOWN = "MARGIN_UNKNOWN"

    # Liquidity
    LIQUIDITY_REJECTED = "LIQUIDITY_REJECTED"

    # Regime and halt
    REGIME_UNKNOWN = "REGIME_UNKNOWN"
    HALTED = "HALTED"
    UNCERTAIN_EFFECT = "UNCERTAIN_EFFECT"

    # Order and execution
    SUBMISSION_UNKNOWN = "SUBMISSION_UNKNOWN"
    PARTIAL_UNPROTECTED = "PARTIAL_UNPROTECTED"

    # Stop and protection
    STOP_BREACHED = "STOP_BREACHED"

    # Lifecycle
    EXIT_ORPHAN = "EXIT_ORPHAN"
    CLOSED_PENDING_ACCOUNTING = "CLOSED_PENDING_ACCOUNTING"
    CLOSED = "CLOSED"

    # End state
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


# Admission gate blocking reasons in priority order (spec §5).
ADMISSION_BLOCKING_ORDER = [
    "SOURCE_AUTHORITY",
    "NOT_CURRENT_ACTIONABLE_ENTRY",
    "NO_ELIGIBLE_ROUTE",
    "BUDGET_NOT_ADMISSIBLE",
    "REGIME_UNKNOWN",
    "HALTED",
    "UNCERTAIN_EFFECT",
]
