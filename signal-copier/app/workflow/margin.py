"""Margin regimes and settlement calendar logic (WC-09).

Spec §9: Margin is a capacity mechanism, not the risk denominator. When margin
is permitted, check compliance with owner ceilings, broker limits, maintenance
headroom, stress scenarios, and overnight settlement. Unknown regimes block
affected new exposure (I17); broker approval overrides owner permission only
within released precedence.

FINRA replacement intraday-margin standards (effective 2026-06-04, phase-in
through 2027-10-20) are stored per account as LEGACY_PDT, NEW_INTRADAY, or
UNKNOWN with dated evidence. T+1 settlement applies to most U.S. securities;
other assets and balance fields may differ. Spec §22 S02/S03.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime, timezone


class MarginRegime(str, enum.Enum):
    """Per-account margin regime classification.

    Spec §9: Store the regime with broker evidence and date; unknown blocks
    affected new exposure, not protective exits.
    """

    LEGACY_PDT = "legacy_pdt_verified"  # Pre-2026 FINRA PDT rules ($25k minimum)
    NEW_INTRADAY = "new_intraday_verified"  # 2026-06-04+ replacement standards
    UNKNOWN = "unknown"  # Regime unknown (default); blocks new exposure (I17)


@dataclass(frozen=True)
class MarginRegimeRecord:
    """Persisted margin regime for a physical account with evidence.

    Attributes:
        physical_account_id: The account this regime applies to.
        regime: One of LEGACY_PDT, NEW_INTRADAY, or UNKNOWN.
        evidence: Broker-provided evidence string or description (≥3 chars,
                  required by owner API PUT; persisted even if empty in DB).
        verified_at: Timestamp when regime was confirmed (UTC).
    """

    physical_account_id: str
    regime: MarginRegime | str  # Enum or string
    evidence: str  # Description of evidence source/date (≥3 chars for new via API)
    verified_at: datetime  # UTC timestamp


def validate_evidence(evidence: str) -> bool:
    """Check if evidence string is valid for owner API PUT.

    Args:
        evidence: Evidence/description string provided by owner.

    Returns:
        True if valid (≥3 chars), False otherwise.
    """
    return len(evidence) >= 3


def current_utc() -> datetime:
    """Current time in UTC."""
    return datetime.now(timezone.utc)


def regime_blocks_new_exposure(regime: MarginRegime | str | None) -> bool:
    """Check if a regime blocks new exposure (spec I17).

    Args:
        regime: The margin regime, or None.

    Returns:
        True if regime is UNKNOWN (blocks new exposure), False otherwise.
        None or any valid regime does not block.
    """
    if regime is None:
        return False
    regime_str = regime if isinstance(regime, str) else regime.value
    return regime_str == MarginRegime.UNKNOWN.value


def is_valid_regime(regime: str) -> bool:
    """Check if a regime string is valid.

    Args:
        regime: Candidate regime string.

    Returns:
        True if regime is a valid enum value.
    """
    return regime in (r.value for r in MarginRegime)
