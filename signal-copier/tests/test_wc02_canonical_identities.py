"""Tests for WC-02: canonical identities (PhysicalAccount, AccountBinding, CapabilityProfile).

Implements §1 and §5.1 of WORKFLOW_SPECIFICATION:
- Canonical entities and ownership
- Non-negotiable invariants I02 and I21
- Account eligibility

Tests:
- I02: Multiple credentials or matching route rules do not duplicate capital or execution
- I21: No unavailable route is labeled supported merely because a class, method, logo
  or mocked response exists
- Paper and live with the same display name are different accounts
"""
from __future__ import annotations

import pytest

from app.db import SignalStore
from app.workflow.identity import (
    AccountBinding,
    CapabilityProfile,
    PhysicalAccount,
    IdentityRegistry,
)


class TestPhysicalAccountDeduplication:
    """Test invariant I02: multiple bindings collapse to one physical account."""

    @pytest.mark.scenario("ROU-002")
    def test_two_bindings_same_physical_account(self, tmp_path):
        """Two bindings to the same broker account ID collapse to one PhysicalAccount."""
        store = SignalStore(tmp_path / "test.db")

        # Create a physical account (e.g., IB account DU123456)
        physical_id = "pa_ib_du123456_live"
        physical = PhysicalAccount(
            physical_account_id=physical_id,
            broker="interactive_brokers",
            broker_account_id="DU123456",
            environment="live",
            base_currency="USD",
            margin_type="margin",
            restriction_state="none",
        )

        # Insert the physical account (in real implementation, this would be via IdentityRegistry)
        with store._connect() as conn:
            conn.execute(
                "INSERT INTO physical_accounts (physical_account_id, broker, broker_account_id, "
                "environment, base_currency, margin_type, restriction_state) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    physical.physical_account_id,
                    physical.broker,
                    physical.broker_account_id,
                    physical.environment,
                    physical.base_currency,
                    physical.margin_type,
                    physical.restriction_state,
                ),
            )

            # Create two bindings to this same physical account
            # (e.g., two API keys, or a webhook + polling)
            binding1_id = "bind_ib_key1"
            binding2_id = "bind_ib_key2"
            config_id1 = "acc_001"
            config_id2 = "acc_002"

            # Insert dummy config_accounts rows
            for account_id in [config_id1, config_id2]:
                conn.execute(
                    "INSERT INTO config_accounts (account_id, broker) VALUES (?, ?)",
                    (account_id, "interactive_brokers"),
                )

            # Insert bindings
            binding1 = AccountBinding(
                binding_id=binding1_id,
                physical_account_id=physical_id,
                config_account_id=config_id1,
                version=1,
                revoked=False,
            )
            binding2 = AccountBinding(
                binding_id=binding2_id,
                physical_account_id=physical_id,
                config_account_id=config_id2,
                version=1,
                revoked=False,
            )

            for binding in [binding1, binding2]:
                conn.execute(
                    "INSERT INTO account_bindings (binding_id, physical_account_id, config_account_id, version, revoked) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        binding.binding_id,
                        binding.physical_account_id,
                        binding.config_account_id,
                        binding.version,
                        int(binding.revoked),
                    ),
                )

            conn.commit()

        # Query: collapse [config_id1, config_id2] to physical accounts
        registry = IdentityRegistry(store)
        candidates = registry.collapse_bindings([config_id1, config_id2])

        # Invariant I02: should return exactly one PhysicalAccount
        # (even though two config_accounts exist), because they both route
        # to the same broker account (DU123456)
        assert (
            len(candidates) == 1
        ), f"I02 violated: expected 1 physical account, got {len(candidates)}"
        assert candidates[0].physical_account_id == physical_id
        assert candidates[0].broker == "interactive_brokers"
        assert candidates[0].broker_account_id == "DU123456"

    def test_different_physical_accounts_not_collapsed(self, tmp_path):
        """Two config_accounts routing to different broker accounts are not collapsed."""
        store = SignalStore(tmp_path / "test.db")

        # Create two physical accounts
        physical1 = PhysicalAccount(
            physical_account_id="pa_ib_du123456_live",
            broker="interactive_brokers",
            broker_account_id="DU123456",
            environment="live",
            base_currency="USD",
            margin_type="margin",
            restriction_state="none",
        )

        physical2 = PhysicalAccount(
            physical_account_id="pa_ib_du654321_live",
            broker="interactive_brokers",
            broker_account_id="DU654321",
            environment="live",
            base_currency="USD",
            margin_type="margin",
            restriction_state="none",
        )

        with store._connect() as conn:
            for physical in [physical1, physical2]:
                conn.execute(
                    "INSERT INTO physical_accounts (physical_account_id, broker, broker_account_id, "
                    "environment, base_currency, margin_type, restriction_state) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        physical.physical_account_id,
                        physical.broker,
                        physical.broker_account_id,
                        physical.environment,
                        physical.base_currency,
                        physical.margin_type,
                        physical.restriction_state,
                    ),
                )

            # Two config accounts, one per physical account
            config_id1 = "acc_001"
            config_id2 = "acc_002"

            for account_id in [config_id1, config_id2]:
                conn.execute(
                    "INSERT INTO config_accounts (account_id, broker) VALUES (?, ?)",
                    (account_id, "interactive_brokers"),
                )

            # Bindings to different physical accounts
            conn.execute(
                "INSERT INTO account_bindings (binding_id, physical_account_id, config_account_id, version, revoked) "
                "VALUES (?, ?, ?, ?, ?)",
                ("bind_1", physical1.physical_account_id, config_id1, 1, 0),
            )
            conn.execute(
                "INSERT INTO account_bindings (binding_id, physical_account_id, config_account_id, version, revoked) "
                "VALUES (?, ?, ?, ?, ?)",
                ("bind_2", physical2.physical_account_id, config_id2, 1, 0),
            )

            conn.commit()

        registry = IdentityRegistry(store)
        candidates = registry.collapse_bindings([config_id1, config_id2])

        # Should return two distinct PhysicalAccounts (different broker account IDs)
        assert (
            len(candidates) == 2
        ), f"Expected 2 physical accounts, got {len(candidates)}"
        physical_ids = {c.physical_account_id for c in candidates}
        assert physical_ids == {physical1.physical_account_id, physical2.physical_account_id}


class TestCapabilityProfileEvidenceTier:
    """Test invariant I21: unknown capabilities are unsupported."""

    @pytest.mark.scenario("ROU-004")
    def test_unknown_capability_is_not_supported(self, tmp_path):
        """Unknown capability (evidence_tier='unknown') returns None, not mocked."""
        store = SignalStore(tmp_path / "test.db")

        physical_id = "pa_paper_001"
        physical = PhysicalAccount(
            physical_account_id=physical_id,
            broker="paper",
            broker_account_id="PAPER_001",
            environment="paper",
            base_currency="USD",
            margin_type="cash",
            restriction_state="none",
        )

        with store._connect() as conn:
            # Insert physical account
            conn.execute(
                "INSERT INTO physical_accounts (physical_account_id, broker, broker_account_id, "
                "environment, base_currency, margin_type, restriction_state) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    physical.physical_account_id,
                    physical.broker,
                    physical.broker_account_id,
                    physical.environment,
                    physical.base_currency,
                    physical.margin_type,
                    physical.restriction_state,
                ),
            )

            # Insert an UNKNOWN capability (not yet declared)
            cap_unknown = CapabilityProfile(
                physical_account_id=physical_id,
                instrument_family="option",
                session="regular",
                operation="entry_long",
                order_recipe="market",
                evidence_tier="unknown",  # Unknown = unsupported
            )

            # Also insert a DECLARED capability for comparison
            cap_declared = CapabilityProfile(
                physical_account_id=physical_id,
                instrument_family="stock",
                session="regular",
                operation="entry_long",
                order_recipe="limit",
                evidence_tier="declared",
            )

            for cap in [cap_unknown, cap_declared]:
                conn.execute(
                    "INSERT INTO capability_profiles (capability_id, physical_account_id, instrument_family, "
                    "session, operation, order_recipe, evidence_tier) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        f"cap_{cap.physical_account_id}_{cap.instrument_family}_{cap.operation}",
                        cap.physical_account_id,
                        cap.instrument_family,
                        cap.session,
                        cap.operation,
                        cap.order_recipe,
                        cap.evidence_tier,
                    ),
                )

            conn.commit()

        registry = IdentityRegistry(store)

        # Query the UNKNOWN capability
        unknown_result = registry.capability(
            physical_id,
            "option",
            "regular",
            "entry_long",
        )

        # Invariant I21: unknown capability must return None (genuinely unsupported)
        assert (
            unknown_result is None
        ), f"I21 violated: unknown capability should return None, got {unknown_result}"

        # Query the DECLARED capability (should exist)
        declared_result = registry.capability(
            physical_id,
            "stock",
            "regular",
            "entry_long",
        )

        # The declared one should be found
        assert (
            declared_result is not None
        ), "Declared capability should be found (not None)"
        assert declared_result.evidence_tier == "declared"


class TestPaperAndLiveDistinction:
    """Test that paper and live accounts with the same broker name are different."""

    @pytest.mark.scenario("ROU-001")
    def test_paper_and_live_same_broker_are_different_accounts(self, tmp_path):
        """Paper and live with same broker_account_id are different PhysicalAccounts."""
        store = SignalStore(tmp_path / "test.db")

        # Two physical accounts: same broker and account ID, but different environment
        paper_account = PhysicalAccount(
            physical_account_id="pa_ib_du123456_paper",
            broker="interactive_brokers",
            broker_account_id="DU123456",
            environment="paper",
            base_currency="USD",
            margin_type="cash",
            restriction_state="none",
        )

        live_account = PhysicalAccount(
            physical_account_id="pa_ib_du123456_live",
            broker="interactive_brokers",
            broker_account_id="DU123456",
            environment="live",
            base_currency="USD",
            margin_type="margin",
            restriction_state="none",
        )

        with store._connect() as conn:
            for account in [paper_account, live_account]:
                conn.execute(
                    "INSERT INTO physical_accounts (physical_account_id, broker, broker_account_id, "
                    "environment, base_currency, margin_type, restriction_state) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        account.physical_account_id,
                        account.broker,
                        account.broker_account_id,
                        account.environment,
                        account.base_currency,
                        account.margin_type,
                        account.restriction_state,
                    ),
                )

            conn.commit()

        # Verify both rows exist with different IDs
        with store._connect() as conn:
            rows = conn.execute(
                "SELECT physical_account_id, environment FROM physical_accounts "
                "WHERE broker = ? AND broker_account_id = ?",
                ("interactive_brokers", "DU123456"),
            ).fetchall()

        assert len(rows) == 2, f"Expected 2 rows (paper + live), got {len(rows)}"

        environments = {row[1] for row in rows}
        assert environments == {"paper", "live"}, f"Expected both paper and live, got {environments}"
