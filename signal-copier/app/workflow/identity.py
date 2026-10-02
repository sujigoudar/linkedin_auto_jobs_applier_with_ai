"""Canonical identities for broker accounts, bindings, and capabilities.

Implements §1 and §5.1 of the WORKFLOW_SPECIFICATION: the immutable,
deduplicated representation of real broker accounts (PhysicalAccount),
the credentials and integrations that reach them (AccountBinding), and
the exact capabilities available on each account for each instrument
family and operation (CapabilityProfile). The IdentityRegistry provides
lookup and deduplication logic backed by SignalStore tables.

Invariants (§1.1):
- I02: Multiple credentials or matching route rules do not duplicate
  capital or execution. Multiple AccountBindings to the same broker
  account are collapsed to one PhysicalAccount during queries.
- I21: No unavailable route is labeled supported merely because a class,
  method, logo or mocked response exists. An unknown CapabilityProfile
  is genuinely unsupported, not a default or placeholder.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class PhysicalAccount:
    """One real broker account, immutable and deduplicated.

    A PhysicalAccount represents a single real, legal account at a broker.
    Even if multiple integrations or credentials reach the same account,
    there is exactly one PhysicalAccount row for it. The combination of
    broker + broker_account_id is globally unique.

    Invariant I02 enforces that duplicate bindings collapse to one physical
    account, never duplicating capital or execution.
    """

    physical_account_id: str
    """Immutable internal identifier for this physical account."""

    broker: str
    """Broker name (e.g., 'paper', 'interactive_brokers', 'robinhood')."""

    broker_account_id: str
    """The broker's own immutable account identifier.

    This is the account ID the broker reports (e.g., DU123456 for IB,
    or the account number from the broker's API). If the broker does not
    expose an account ID in its API, this is set to config_account_id
    with evidence_tier="declared".
    """

    environment: str
    """Trading environment: 'paper', 'live', 'sandbox', 'unknown'.

    Paper and live with the same broker name and ID are different physical
    accounts (invariant I02 still requires deduplication within an
    environment, but not across environments).
    """

    base_currency: str
    """Account base currency (ISO 4217 code: 'USD', 'EUR', 'JPY', etc)."""

    margin_type: str
    """Account margin/settlement type: 'cash', 'margin', 'retirement', 'unknown'.

    'unknown' means the type is not yet determined; cash and margin
    accounts cannot be substituted for each other (§5.1 account eligibility).
    """

    restriction_state: str
    """Account trading restriction state: 'none', 'pdt_restricted', 'closing_only', 'unknown'.

    'pdt_restricted' = Pattern Day Trader rule active (US equities).
    'closing_only' = account can only close positions, no new entries.
    'unknown' = restriction state is not yet determined; treated conservatively
    (blocking new exposure when required).
    """


@dataclass(frozen=True)
class AccountBinding:
    """A credential or integration path to a PhysicalAccount, versioned and revocable.

    An AccountBinding represents one way to reach a physical account:
    a credential, API key, connection, or integration. Multiple bindings
    can reach the same physical account (e.g., two API keys or a webhook
    plus a polling connection). Bindings are versioned for configuration
    changes and can be revoked independently without affecting the
    underlying physical account.

    A binding has no independent capital; it is only a route to the
    physical account. Capital checks and routing always collapse
    duplicate bindings (invariant I02).
    """

    binding_id: str
    """Immutable unique identifier for this binding."""

    physical_account_id: str
    """FK to PhysicalAccount: the real account this binding reaches."""

    config_account_id: str
    """FK to config_accounts table: the destination account config row."""

    version: int
    """Configuration version of this binding.

    Incremented when the binding's configuration changes (e.g., API key,
    webhook URL, polling interval). Used to detect stale cached references.
    """

    revoked: bool
    """True if this binding has been revoked and should not be used.

    Revoked bindings remain in the database for audit/history but are
    excluded from active routing and capability queries.
    """


@dataclass(frozen=True)
class CapabilityProfile:
    """Exact capability of an account for a specific instrument and operation.

    A CapabilityProfile declares what operations are supported on a specific
    account for a specific instrument family (stock, option, future, fx, crypto)
    under a specific session/recipe/operation combination.

    Invariant I21 enforces that an unknown CapabilityProfile is genuinely
    unsupported, not a default or mocked placeholder.
    """

    physical_account_id: str
    """FK to PhysicalAccount: which account has this capability."""

    instrument_family: str
    """Asset class: 'stock', 'option', 'future', 'fx', 'crypto', etc."""

    session: str
    """Trading session (broker/venue specific): 'regular', 'pre', 'after', 'etf', etc."""

    operation: str
    """Operation type: 'entry_long', 'entry_short', 'exit', 'stop', 'target', etc."""

    order_recipe: str
    """Qualified order recipe/method: 'limit', 'market', 'stop_limit', 'algo', etc.

    The exact mechanism by which orders will be placed on this account for
    this operation. Determines validation, execution quality, and protection
    mechanics.
    """

    evidence_tier: str
    """Source of truth for this capability: 'unknown', 'declared', 'simulator', 'paper', 'live'.

    - 'unknown': Not yet determined; treated as unsupported (I21).
    - 'declared': Operator explicitly declared this capability in config.
        Stronger than 'simulator' but weaker than proven evidence.
        Used for broker-documented but not yet tested capabilities.
    - 'simulator': Tested and working in the simulator/backtest.
    - 'paper': Tested and working on paper broker.
    - 'live': Tested and working on live broker.

    Only capabilities with evidence_tier != 'unknown' can route new entries.
    The tier is used to rank candidates when multiple accounts can handle
    an opportunity (lower tier = weaker confidence).
    """


class IdentityRegistry:
    """Lookup and deduplication for PhysicalAccounts, AccountBindings, and CapabilityProfiles.

    Backed by three tables in SignalStore: physical_accounts, account_bindings,
    capability_profiles. Provides the core router logic for I02 (collapse duplicate
    bindings) and I21 (unknown capabilities are unsupported).
    """

    def __init__(self, store):
        """Initialize with a SignalStore instance.

        Args:
            store: SignalStore instance providing access to the three tables.
        """
        self.store = store

    def physical_for_config_account(
        self, config_account_id: str
    ) -> Optional[PhysicalAccount]:
        """Look up the PhysicalAccount for a given config_account_id.

        Args:
            config_account_id: The config_accounts row ID.

        Returns:
            PhysicalAccount if one exists for this config_account_id, else None.
        """
        with self.store._connect() as conn:
            row = conn.execute(
                """
                SELECT pa.physical_account_id, pa.broker, pa.broker_account_id,
                       pa.environment, pa.base_currency, pa.margin_type, pa.restriction_state
                FROM physical_accounts pa
                JOIN account_bindings ab ON pa.physical_account_id = ab.physical_account_id
                WHERE ab.config_account_id = ? AND ab.revoked = 0
                LIMIT 1
                """,
                (config_account_id,),
            ).fetchone()

        if not row:
            return None

        return PhysicalAccount(
            physical_account_id=row[0],
            broker=row[1],
            broker_account_id=row[2],
            environment=row[3],
            base_currency=row[4],
            margin_type=row[5],
            restriction_state=row[6],
        )

    def collapse_bindings(self, config_account_ids: list[str]) -> list[PhysicalAccount]:
        """Collapse multiple config_account_ids to their unique PhysicalAccounts.

        Implements invariant I02: if two or more config_account_ids route to the
        same broker account ID, return only one PhysicalAccount for that broker
        account. This prevents capital reservation from being counted twice.

        Args:
            config_account_ids: List of config_accounts row IDs.

        Returns:
            List of unique PhysicalAccounts, deduplicated by broker + broker_account_id.
            No duplicates in the result even if multiple config_account_ids
            reference the same physical account.
        """
        if not config_account_ids:
            return []

        with self.store._connect() as conn:
            # Query all physical accounts for the given config_account_ids
            # and deduplicate by (broker, broker_account_id, environment)
            # which together form the unique physical account identity
            placeholders = ",".join("?" * len(config_account_ids))
            rows = conn.execute(
                f"""
                SELECT DISTINCT pa.physical_account_id, pa.broker, pa.broker_account_id,
                                pa.environment, pa.base_currency, pa.margin_type, pa.restriction_state
                FROM physical_accounts pa
                JOIN account_bindings ab ON pa.physical_account_id = ab.physical_account_id
                WHERE ab.config_account_id IN ({placeholders})
                  AND ab.revoked = 0
                ORDER BY pa.physical_account_id
                """,
                config_account_ids,
            ).fetchall()

        # Build PhysicalAccount objects from rows
        # The DISTINCT already ensures no duplicates by physical_account_id,
        # which is the natural unique key
        result = []
        for row in rows:
            result.append(
                PhysicalAccount(
                    physical_account_id=row[0],
                    broker=row[1],
                    broker_account_id=row[2],
                    environment=row[3],
                    base_currency=row[4],
                    margin_type=row[5],
                    restriction_state=row[6],
                )
            )

        return result

    def capability(
        self,
        physical_account_id: str,
        instrument_family: str,
        session: str,
        operation: str,
    ) -> Optional[CapabilityProfile]:
        """Look up an exact capability for an account/instrument/operation.

        Implements invariant I21: an unknown (not-yet-declared) capability
        returns None, not a mocked or default profile. This enforces the
        conservative policy that unknown capabilities must be explicitly
        declared before they can be used.

        Args:
            physical_account_id: The PhysicalAccount ID.
            instrument_family: Instrument family (e.g., 'stock', 'option').
            session: Trading session.
            operation: Operation type (e.g., 'entry_long', 'exit').

        Returns:
            CapabilityProfile if one exists with evidence_tier != 'unknown',
            else None (genuinely unsupported, not a default).
        """
        with self.store._connect() as conn:
            row = conn.execute(
                """
                SELECT capability_id, physical_account_id, instrument_family,
                       session, operation, order_recipe, evidence_tier
                FROM capability_profiles
                WHERE physical_account_id = ?
                  AND instrument_family = ?
                  AND session = ?
                  AND operation = ?
                  AND evidence_tier != 'unknown'
                LIMIT 1
                """,
                (physical_account_id, instrument_family, session, operation),
            ).fetchone()

        if not row:
            return None

        return CapabilityProfile(
            physical_account_id=row[1],
            instrument_family=row[2],
            session=row[3],
            operation=row[4],
            order_recipe=row[5],
            evidence_tier=row[6],
        )
