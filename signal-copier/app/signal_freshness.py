"""Track 16: per-provider/per-source signal FRESHNESS configuration,
actually wired into `app/engine.py`'s signal-admission path.

The user's own spec (verbatim): "Per provider/source: Maximum entry age,
Maximum add age, Exit stale behavior, Adjustment stale behavior, Use
provider timestamp, Use message timestamp, Use article publication time,
Clock-skew tolerance, Recovered-event behavior. Do not reduce freshness
to received_at."

Track 14 (`app/provider_catalog.py`) already reserved `providers.
max_entry_age_seconds`/`stale_exit_policy`/`correlation_window_seconds`
and `sources.freshness_policy` (a JSON blob), explicitly documented there
as "recorded honestly ... left for the next phase". This module IS that
next phase's wiring: it reads whatever a provider/source row already
has, resolves a single effective `FreshnessConfig` for one `Signal`, and
evaluates it against that signal's own best-available timestamp --
NEVER against `received_at` alone (see `resolve_effective_timestamp`).

Strict-superset discipline (mirrors `app/signal_correlation.py`'s own,
`app/provider_catalog.py`'s own): a `Signal` whose `source` names no
registered `providers` row -- true for every signal this codebase has
ever produced before Track 14, and for most of this codebase's own
adapters and test fixtures today -- resolves to `FreshnessConfig.
disabled()`, whose `evaluate` always returns `ok=True` with `action=None`
("nothing configured, nothing enforced"). `app/engine.py`'s own call site
never even runs the age computation for that case, so there is nothing
for a missing/legacy provider to regress against.

No LLM/AI judgment call anywhere in this module -- every decision below
is a closed-enum comparison against a stored, operator-set configuration
value, never a model inference."""
from __future__ import annotations

import enum
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from app.models import Signal

logger = logging.getLogger(__name__)


class StalenessAction(str, enum.Enum):
    """What to do with a signal this module has determined is stale --
    shared shape for `exit_stale_behavior` and `adjustment_stale_behavior`
    (the user's own spec: "same enum shape" for both). Never a silent
    default: a provider/source that hasn't set one explicitly resolves to
    `HOLD` (see `FreshnessConfig`'s own field docstring), the conservative
    floor, not `ACT_ANYWAY`."""

    #: Proceed to routing/submission despite the staleness -- an explicit,
    #: operator-chosen opt-in, never the unset default.
    ACT_ANYWAY = "ACT_ANYWAY"
    #: Hold the signal out of live routing, recorded for human review --
    #: same "recorded, never silently dropped, never silently routed"
    #: convention as Track 10's `stale_backlog_import_only`/Track 12's
    #: `CONFLICTING_SOURCE_DATA`. The conservative default.
    HOLD = "HOLD"
    #: Definitively refuse this signal (a REJECTED order-shaped result,
    #: not a "come back to this later" hold).
    REJECT = "REJECT"


class TimestampSourcePreference(str, enum.Enum):
    """Which of a `Signal`'s several honestly-distinct timestamps to
    treat as "when this really happened" for freshness math -- the user's
    own spec: "Do not reduce freshness to received_at." See
    `resolve_effective_timestamp` for the explicit, logged fallback when
    the preferred field is `None` (never a silent substitution)."""

    PROVIDER_TIMESTAMP = "PROVIDER_TIMESTAMP"  # Signal.source_created_at
    MESSAGE_TIMESTAMP = "MESSAGE_TIMESTAMP"  # Signal.source_modified_at
    PUBLICATION_TIMESTAMP = "PUBLICATION_TIMESTAMP"  # Signal.first_observed_at
    RECEIVED_AT = "RECEIVED_AT"  # Signal.received_at -- always present


class RecoveredEventBehavior(str, enum.Enum):
    """How to treat an event that arrives very late (e.g. a collector's
    reconnect-and-replay surfacing something long past) -- distinct from
    ordinary staleness, since a "recovered" event may be legitimately
    old evidence rather than a late-arriving live alert."""

    #: Evaluate it exactly like any other signal (no special-casing).
    PROCESS_NORMALLY = "PROCESS_NORMALLY"
    #: Hold it out of live routing for a human to look at -- the
    #: conservative default.
    HOLD_FOR_REVIEW = "HOLD_FOR_REVIEW"
    #: Record it (never silently dropped from the audit trail -- still
    #: `save_signal`'d) but never route it live, and never surface it on
    #: the HOLD review queue either (distinct disposition from
    #: `HOLD_FOR_REVIEW` -- an operator who's configured this has already
    #: decided a recovered replay this old is never actionable).
    DISCARD = "DISCARD"


#: A "recovered" event is one whose effective age is at least this many
#: TIMES `max_entry_age_seconds` -- e.g. a reconnect replaying something
#: 10x older than what this provider would ever consider a fresh entry.
#: Only evaluated when `max_entry_age_seconds` is actually configured
#: (no invented age ceiling for a provider that never set one).
RECOVERED_EVENT_AGE_MULTIPLIER = 10.0


@dataclass(frozen=True)
class FreshnessConfig:
    """The effective, already-merged (provider defaults + any source-level
    override) freshness configuration for one signal. `resolved_from`
    records exactly where this came from (`"none"` when nothing was
    configured at all -- the strict-superset, do-nothing case) for
    logging/audit, never silently unattributed."""

    max_entry_age_seconds: Optional[float] = None
    max_add_age_seconds: Optional[float] = None
    exit_stale_behavior: Optional[StalenessAction] = None
    adjustment_stale_behavior: Optional[StalenessAction] = None
    timestamp_source_preference: Optional[TimestampSourcePreference] = None
    clock_skew_tolerance_seconds: float = 0.0
    recovered_event_behavior: Optional[RecoveredEventBehavior] = None
    resolved_from: str = "none"

    @staticmethod
    def disabled() -> "FreshnessConfig":
        """The strict-superset default: a signal whose provider isn't
        registered (or is registered but never set any of these fields)
        resolves to this -- `evaluate` always returns `ok=True` for it,
        identical to this codebase's behavior before this module existed."""
        return FreshnessConfig()

    @property
    def is_configured(self) -> bool:
        return self.resolved_from != "none"


@dataclass(frozen=True)
class TimestampResolution:
    timestamp: datetime
    field_used: str
    fallback_used: bool
    preferred_field_was: Optional[str]


@dataclass(frozen=True)
class FreshnessDecision:
    """The result of evaluating one signal against its effective
    `FreshnessConfig`. `ok=True` means "nothing this module found reason
    to hold/reject" -- the caller (`app/engine.py`) proceeds exactly as
    before. `ok=False` carries `action` (REJECT or HOLD -- `ACT_ANYWAY`
    never produces `ok=False`, see `evaluate_signal_freshness`) and a
    human-readable `reason`."""

    ok: bool
    action: Optional[StalenessAction] = None
    reason: Optional[str] = None
    age_seconds: Optional[float] = None
    timestamp_field_used: Optional[str] = None
    fallback_used: bool = False
    recovered_event: bool = False


def resolve_effective_timestamp(signal: Signal, config: FreshnessConfig) -> TimestampResolution:
    """The user's own spec: "Do not reduce freshness to received_at." --
    picks whichever of `Signal.source_created_at`/`source_modified_at`/
    `first_observed_at`/`received_at` `config.timestamp_source_preference`
    names, falling back to `received_at` (always present) WITH AN
    EXPLICIT, LOGGED fallback flag whenever the preferred field is `None`
    -- never a silent substitution. `config.timestamp_source_preference
    is None` (nothing configured) also resolves to `received_at`, but
    `fallback_used=False` in that case -- there was no preference to have
    fallen back FROM."""
    preferred = config.timestamp_source_preference
    field_map = {
        TimestampSourcePreference.PROVIDER_TIMESTAMP: ("source_created_at", signal.source_created_at),
        TimestampSourcePreference.MESSAGE_TIMESTAMP: ("source_modified_at", signal.source_modified_at),
        TimestampSourcePreference.PUBLICATION_TIMESTAMP: ("first_observed_at", signal.first_observed_at),
        TimestampSourcePreference.RECEIVED_AT: ("received_at", signal.received_at),
    }
    if preferred is None:
        return TimestampResolution(
            timestamp=signal.received_at, field_used="received_at", fallback_used=False, preferred_field_was=None
        )
    field_name, value = field_map[preferred]
    if value is not None:
        return TimestampResolution(
            timestamp=value, field_used=field_name, fallback_used=False, preferred_field_was=field_name
        )
    logger.info(
        "signal id=%s source=%s: freshness timestamp_source_preference=%s but Signal.%s is None -- "
        "falling back to received_at (never silently substituted without this log line)",
        signal.id,
        signal.source,
        preferred.value,
        field_name,
    )
    return TimestampResolution(
        timestamp=signal.received_at, field_used="received_at", fallback_used=True, preferred_field_was=field_name
    )


def _as_aware_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def evaluate_signal_freshness(
    signal: Signal, config: FreshnessConfig, *, now: Optional[datetime] = None
) -> FreshnessDecision:
    """Evaluates `signal` against `config`. Returns `ok=True` (nothing to
    do -- the strict-superset case for `config.disabled()`, or a
    genuinely fresh signal, or a stale one whose configured behavior is
    `ACT_ANYWAY`) or `ok=False` with `action` set to `REJECT` or `HOLD`
    (the caller must hold this signal out of live routing).

    - An ENTRY/ADD signal (`is_exit=False`) exceeding `max_entry_age_seconds`
      (or `max_add_age_seconds` for `is_add=True`) always resolves to
      `REJECT` -- there is no separate behavior enum for entry/add
      staleness in the user's own spec (only EXIT and ADJUSTMENT have
      one); a stale entry is refused outright, the conservative floor,
      never silently acted on.
    - An EXIT signal (`is_exit=True`) exceeding `max_entry_age_seconds`
      (reused as the one configured age ceiling -- there is no separate
      "max exit age" field in the spec) is resolved by
      `config.exit_stale_behavior`, defaulting to `HOLD` when unset.
    - An ADJUSTMENT signal (`is_adjustment=True`) exceeding that same
      ceiling is resolved by `config.adjustment_stale_behavior`,
      defaulting to `HOLD` when unset.
    - A "recovered" event (age >= `RECOVERED_EVENT_AGE_MULTIPLIER *
      max_entry_age_seconds`, only evaluated when that ceiling is
      configured) is resolved by `config.recovered_event_behavior`
      instead of the ordinary exit/adjustment/entry behavior above,
      defaulting to `HOLD_FOR_REVIEW` when unset -- checked FIRST, since
      a recovered replay is a different phenomenon from ordinary lag.
    """
    if not config.is_configured:
        return FreshnessDecision(ok=True)

    resolution = resolve_effective_timestamp(signal, config)
    now = now or datetime.now(timezone.utc)
    raw_age = (now - _as_aware_utc(resolution.timestamp)).total_seconds()
    age_seconds = max(0.0, raw_age - config.clock_skew_tolerance_seconds)

    ceiling = config.max_entry_age_seconds
    is_exit = signal.side.value == "close"
    # NOTE: this codebase's `Signal.side` (BUY/SELL/CLOSE) has no distinct
    # "add to an existing position" value -- `max_add_age_seconds`/
    # `adjustment_stale_behavior` are recorded and resolved (see
    # `freshness_config_from_rows`) but there is currently no signal kind
    # this evaluator can distinguish from a plain entry to gate against
    # them separately. Honest limitation, not silently pretended away --
    # see this task's own final report. `is_add` stays `False` until a
    # future phase adds that distinction to `Signal`.
    is_add = False

    if ceiling is not None and age_seconds >= ceiling * RECOVERED_EVENT_AGE_MULTIPLIER:
        recovered_behavior = config.recovered_event_behavior or RecoveredEventBehavior.HOLD_FOR_REVIEW
        if recovered_behavior is RecoveredEventBehavior.PROCESS_NORMALLY:
            pass  # fall through to ordinary evaluation below
        else:
            action = (
                StalenessAction.HOLD if recovered_behavior is RecoveredEventBehavior.HOLD_FOR_REVIEW else StalenessAction.REJECT
            )
            return FreshnessDecision(
                ok=False,
                action=action,
                reason=(
                    f"recovered_event_behavior={recovered_behavior.value}: signal age={age_seconds:.0f}s is >= "
                    f"{RECOVERED_EVENT_AGE_MULTIPLIER:g}x max_entry_age_seconds={ceiling} -- treated as a "
                    "late-arriving replay, not an ordinary stale signal"
                ),
                age_seconds=age_seconds,
                timestamp_field_used=resolution.field_used,
                fallback_used=resolution.fallback_used,
                recovered_event=True,
            )

    effective_ceiling = config.max_add_age_seconds if is_add else ceiling
    if effective_ceiling is None or age_seconds < effective_ceiling:
        return FreshnessDecision(
            ok=True, age_seconds=age_seconds, timestamp_field_used=resolution.field_used, fallback_used=resolution.fallback_used
        )

    if is_exit:
        behavior = config.exit_stale_behavior or StalenessAction.HOLD
        label = "exit_stale_behavior"
    else:
        # A stale ENTRY (this codebase has no distinct "add"/"adjustment"
        # Signal kind yet -- see `is_add`'s own note above): no behavior
        # enum applies per the spec -- refuse outright, the conservative
        # floor, never silently acted on.
        behavior = StalenessAction.REJECT
        label = "max_entry_age_seconds (no behavior enum for entry staleness)"

    if behavior is StalenessAction.ACT_ANYWAY:
        return FreshnessDecision(
            ok=True, age_seconds=age_seconds, timestamp_field_used=resolution.field_used, fallback_used=resolution.fallback_used
        )
    return FreshnessDecision(
        ok=False,
        action=behavior,
        reason=(
            f"{label}={behavior.value}: signal age={age_seconds:.0f}s >= effective ceiling "
            f"{effective_ceiling}s (timestamp field used={resolution.field_used!r}, "
            f"fallback_used={resolution.fallback_used})"
        ),
        age_seconds=age_seconds,
        timestamp_field_used=resolution.field_used,
        fallback_used=resolution.fallback_used,
    )


def _enum_or_none(value: Any, enum_cls: type[enum.Enum]) -> Any:
    if value is None:
        return None
    try:
        return enum_cls(value)
    except ValueError:
        logger.warning("unrecognized %s value %r -- treated as unset", enum_cls.__name__, value)
        return None


def freshness_config_from_rows(provider_row: dict | None, source_row: dict | None) -> FreshnessConfig:
    """Merges a `providers` row (defaults) with a `sources` row's
    `freshness_policy` JSON (a per-source OVERRIDE -- any key it sets
    wins over the provider-level default; a key it doesn't set falls
    through to the provider's own column). `provider_row is None` (no
    registered provider for this signal's `source` -- true for every
    pre-Track-14 signal) returns `FreshnessConfig.disabled()`, the
    strict-superset case -- see this module's own docstring."""
    if provider_row is None:
        return FreshnessConfig.disabled()

    policy: dict = (source_row or {}).get("freshness_policy") or {}

    def pick(key: str, provider_key: str | None = None) -> Any:
        if key in policy and policy[key] is not None:
            return policy[key]
        return provider_row.get(provider_key or key)

    max_entry_age = pick("max_entry_age_seconds")
    max_add_age = pick("max_add_age_seconds")
    exit_behavior = _enum_or_none(pick("exit_stale_behavior", "stale_exit_policy"), StalenessAction)
    adjustment_behavior = _enum_or_none(pick("adjustment_stale_behavior"), StalenessAction)
    ts_pref = _enum_or_none(pick("timestamp_source_preference"), TimestampSourcePreference)
    skew = pick("clock_skew_tolerance_seconds") or 0.0
    recovered = _enum_or_none(pick("recovered_event_behavior"), RecoveredEventBehavior)

    if (
        max_entry_age is None
        and max_add_age is None
        and exit_behavior is None
        and adjustment_behavior is None
        and ts_pref is None
        and not skew
        and recovered is None
    ):
        return FreshnessConfig.disabled()

    has_source_override = bool(source_row) and any(v is not None for v in policy.values())
    source = "source" if has_source_override else "provider"
    return FreshnessConfig(
        max_entry_age_seconds=max_entry_age,
        max_add_age_seconds=max_add_age,
        exit_stale_behavior=exit_behavior,
        adjustment_stale_behavior=adjustment_behavior,
        timestamp_source_preference=ts_pref,
        clock_skew_tolerance_seconds=float(skew or 0.0),
        recovered_event_behavior=recovered,
        resolved_from=f"{source}:{provider_row['id']}",
    )
