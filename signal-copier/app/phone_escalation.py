"""Track 13: escalation-only active phone-control retrieval.

The user's own approved design (docs/adr/ — record an ADR alongside this
module; see this module's own docstring for the exact wording it
implements): a hybrid, escalation-based architecture. Official APIs/
webhooks/email/RSS remain the primary source. Passive Android
notification capture (`app/notification_bridge.py`, Track 10) is the
secondary source. Active AI-driven phone control is invoked ONLY when
ALL of the following hold for one captured notification event:

  1. No official direct source already delivered this same signal (Track
     12's cross-transport correlation layer is the intended source of
     truth for this -- see `EscalationCandidate`'s own docstring below
     for the exact interface this module expects from it, and why it is
     not yet wired in).
  2. The passively-captured notification's
     `app.notification_bridge.ContentCompleteness` means the OS
     notification text alone is not enough to safely parse a signal --
     today that is anything other than `ContentCompleteness.COMPLETE`
     (`TRUNCATED`/`TITLE_ONLY`); see `needs_escalation` below.
  3. Active retrieval is explicitly enabled for that specific
     provider/app (`ProviderEscalationConfig.capability_state` is
     `ENABLED`, or `SHADOW` for a non-live dry run) -- `DISABLED` by
     default, never auto-promoted (see `CapabilityState` below).

Safety rails (structurally enforced, not just documented -- see each
class/function's own docstring for exactly where):

  - Active phone control is READ-ONLY for signal acquisition. It may
    open and navigate an AUTHORIZED app and read what's on screen; it
    may never submit/confirm/send/buy/sell, type into a compose box, or
    touch a broker/banking app at all. `PhoneControlAdapter`'s action
    surface (`open_app`/`read_screen_text`/`get_accessibility_tree`/
    `tap`/`scroll`/`go_back`) is a hardcoded allow-list with NO generic
    "tap anywhere"/"type anything"/"submit" primitive -- see that class's
    own docstring for why this is a structural guarantee, not a
    documented convention, and `tests/test_t13_phone_escalation.py::
    test_adapter_public_surface_has_no_action_capable_of_submitting_or_typing`
    for the AST-based proof.
  - `open_app` refuses (raises `DeniedAppPackageError`, never just logs)
    on anything in `DENIED_APP_PACKAGES` -- every broker this codebase
    integrates (`app/brokers/*.py`) plus a banking/payment catch-all
    pattern. Enforced inside `PhoneControlAdapter.open_app` itself, so
    every real or mock subclass inherits the refusal for free; a
    subclass cannot bypass it without overriding `open_app` and
    deliberately skipping the base-class call.
  - `CapabilityState` starts `DISABLED` for every fresh
    `ProviderEscalationConfig` row (`SignalStore.
    register_phone_escalation_config` — see app/db.py) and only an
    owner-gated route (`POST /phone-escalation/configs/{provider}/
    promote` in app/main.py, behind `Depends(require_owner)`, the same
    gate every other owner-action-card in this codebase uses) can move
    it to `SHADOW` or `ENABLED`.
  - `SHADOW` mode NEVER feeds its result into the live pipeline --
    `evaluate_escalation` below returns a result tagged
    `EscalationDisposition.SHADOW_LOGGED_ONLY` for that state and the
    caller (`app/main.py`'s `_process_notification_bridge_event`) must
    never call `engine.handle_signal` for it; see that dispositon's own
    docstring and `test_shadow_mode_result_never_reaches_live_pipeline`.
  - Only `ENABLED` retrieval that produces a real candidate re-enters the
    SAME pipeline (dedup/freshness/validation/risk/execution) every
    other source goes through -- it becomes an ordinary `Signal` with
    `channel_id`/`message_id` set from the SAME notification_key/device
    identity as the triggering event, so the engine's own
    `find_signal_id_by_provider_identity` dedup and SIG-01 replay guard
    apply exactly as they do for every other source. There is no
    parallel/side execution path.

The LLM extraction boundary (`extract_signal_candidate`) is a bounded,
permission-limited call: raw accessibility-tree/screen text goes in, a
structured `ExtractionResult` (a candidate `Signal`'s raw fields, or
`UNKNOWN`) comes out. It has no tool access of its own — no ability to
call `PhoneControlAdapter` again, no ability to take any action --
mirroring the "permission boundary only, no execution authority" shape
`signal-portfolio-commercial`'s `model_gateway.py` documents (no
equivalent module exists yet in signal-copier; this is the first one).
`MockPhoneControlAdapter`/`mock_extract_signal_candidate` are TEST-ONLY
fixtures with deterministic canned data -- NOT a working device backend.
See `AdbPhoneControlAdapter` below for the recommended real backend
design (a stub that raises `NotImplementedError`; actual device
integration is out of scope for this environment, see this module's own
module-level docstring continuation in docs/adr/).
"""
from __future__ import annotations

import abc
import enum
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from app.notification_bridge import ContentCompleteness


class PhoneEscalationError(ValueError):
    """Raised for a registration/config update that fails this module's
    own validation -- never a silent best-effort accept."""


class DeniedAppPackageError(PermissionError):
    """Raised by `PhoneControlAdapter.open_app` (and never caught/
    swallowed by this module) when asked to open a package on
    `DENIED_APP_PACKAGES` -- a structural refusal, not a logged
    warning. See this module's own docstring."""


# ---------------------------------------------------------------------------
# Content-completeness -> escalation-eligibility
# ---------------------------------------------------------------------------

#: Which `app.notification_bridge.ContentCompleteness` values mean "the OS
#: notification text alone is not enough to safely parse a trade signal
#: from" -- i.e. escalation-eligible per point 2 of this module's own
#: docstring. Track 12/a future revision of `ContentCompleteness` may
#: introduce a finer-grained vocabulary (the design brief's own language:
#: `PARTIAL`/`POINTER_ONLY`/`TRUNCATED`) -- see `needs_escalation`'s own
#: docstring for exactly how that reconciles with what exists today.
_ESCALATION_ELIGIBLE_COMPLETENESS = frozenset(
    {ContentCompleteness.TRUNCATED, ContentCompleteness.TITLE_ONLY}
)


def needs_escalation(completeness: ContentCompleteness) -> bool:
    """Point 2 of this module's own docstring, in code: `True` only for a
    completeness value that means the passively-captured text alone
    cannot be safely parsed. `ContentCompleteness.COMPLETE` is never
    escalation-eligible -- the whole point of the hybrid design is that a
    complete passive capture needs no active retrieval at all.

    INTERFACE-RECONCILIATION NOTE (Track 12): the design brief this
    module implements names `PARTIAL`/`POINTER_ONLY`/`TRUNCATED` as the
    three escalation-triggering states, but
    `app.notification_bridge.ContentCompleteness` (as it exists on this
    branch today, unchanged by this task per its own hard rule 3 --
    public contracts are not modified without updating callers) only has
    `COMPLETE`/`TRUNCATED`/`TITLE_ONLY`. This function currently treats
    `TRUNCATED` and `TITLE_ONLY` (the closest existing equivalents of
    `PARTIAL`/`POINTER_ONLY`) as escalation-eligible. If Track 12 lands a
    finer-grained enum (splitting `TITLE_ONLY` into `POINTER_ONLY` vs a
    genuinely-empty case, or adding `PARTIAL` as distinct from
    `TRUNCATED`), this function's `_ESCALATION_ELIGIBLE_COMPLETENESS` set
    is the ONLY place that needs to change to reconcile with it -- no
    other caller in this module hardcodes the enum's members."""
    return completeness in _ESCALATION_ELIGIBLE_COMPLETENESS


# ---------------------------------------------------------------------------
# Track 12 consumption interface (documented, not yet wired -- see module
# docstring point 1)
# ---------------------------------------------------------------------------


@dataclass
class EscalationCandidate:
    """The shape this module expects Track 12's cross-transport
    correlation layer to hand it for point 1 of this module's own
    docstring ("no official API/webhook/email source already delivered
    this same signal"). Track 12's branch (`agent-track12-whop-
    correlation`) had not diverged from this task's own base commit at
    the time this module was written (verified: `git merge-base HEAD
    agent-track12-whop-correlation` == both branches' own HEAD), so
    nothing from it could be read or imported here -- this is this
    module's OWN best-effort design of the consumption interface,
    documented here so it can be reconciled (renamed/adjusted, never
    silently duplicated) at merge time rather than blocking this task on
    Track 12 landing first.

    Expected shape of "a query for events needing escalation":
    a function (or `SignalStore` method) returning `list[EscalationCandidate]`
    for events where `covered_by_direct_source` is `False` -- i.e. Track 12
    checked every other live source (webhook/email/RSS/bot) for this same
    signal by provider-identity/content-fingerprint correlation and found
    none. `evaluate_escalation` below takes `covered_by_direct_source` as
    a plain keyword argument today (the caller -- `app/main.py`'s
    `_process_notification_bridge_event` -- is expected to obtain it from
    Track 12's real query once merged); this dataclass documents the
    fuller shape that call is expected to eventually return."""

    device_id: str
    app_package: str
    notification_key: str
    content_hash: str
    covered_by_direct_source: bool
    direct_source_name: Optional[str] = None


# ---------------------------------------------------------------------------
# Capability state (point 3 -- disabled by default, per-provider, owner-gated)
# ---------------------------------------------------------------------------


class CapabilityState(str, enum.Enum):
    """Same CapabilityState/honest-unsupported convention CLAUDE.md's
    hard rule 11 asks for, applied to active phone-control retrieval.
    `DISABLED` is the only value a fresh config row may ever start at
    (enforced in `validate_config_registration` below, never left to the
    caller to remember) -- promotion to `SHADOW`/`ENABLED` is always a
    separate, explicit, owner-gated action (`POST /phone-escalation/
    configs/{provider}/promote`, behind `Depends(require_owner)`)."""

    #: The honest default. No retrieval attempt is ever made for a
    #: provider/app in this state -- `evaluate_escalation` returns
    #: `EscalationDisposition.CAPABILITY_DISABLED` immediately.
    DISABLED = "disabled"
    #: Retrieval runs (a real `PhoneControlAdapter` call, if one is
    #: configured), the attempt and its extraction result are recorded
    #: for the operator to review accuracy against -- but the result is
    #: NEVER passed to `engine.handle_signal`. See
    #: `EscalationDisposition.SHADOW_LOGGED_ONLY`.
    SHADOW = "shadow"
    #: Retrieval runs and, if extraction produces a real candidate, that
    #: candidate is fed into the same pipeline every other source goes
    #: through (point 5 of this module's own docstring). Reached only by
    #: explicit operator promotion FROM `SHADOW` (never directly from
    #: `DISABLED` -- see `validate_state_transition`), i.e. only after
    #: the operator has reviewed shadow-mode accuracy.
    ENABLED = "enabled"


#: The only transitions `validate_state_transition` accepts -- promotion
#: is always one step at a time (DISABLED -> SHADOW -> ENABLED), and any
#: state may be demoted straight back to DISABLED (an owner's emergency
#: off-switch, never blocked by "you must demote through SHADOW first").
_ALLOWED_TRANSITIONS: dict[CapabilityState, frozenset[CapabilityState]] = {
    CapabilityState.DISABLED: frozenset({CapabilityState.SHADOW}),
    CapabilityState.SHADOW: frozenset({CapabilityState.ENABLED, CapabilityState.DISABLED}),
    CapabilityState.ENABLED: frozenset({CapabilityState.DISABLED}),
}


def validate_state_transition(current: CapabilityState, target: CapabilityState) -> None:
    """Raises `PhoneEscalationError` (never silently accepts) for a
    transition this module's own promotion policy doesn't allow -- e.g.
    `DISABLED` straight to `ENABLED`, skipping shadow validation
    entirely, which the user's own instruction ("keep active retrieval
    disabled until that route passes paper/shadow validation") forbids
    structurally, not just by convention."""
    if target == current:
        return
    allowed = _ALLOWED_TRANSITIONS.get(current, frozenset())
    if target not in allowed:
        raise PhoneEscalationError(
            f"cannot transition capability_state from {current.value!r} to {target.value!r} -- "
            f"allowed transitions from {current.value!r} are {sorted(s.value for s in allowed)}"
        )


@dataclass
class ProviderEscalationConfig:
    """One row of the `phone_escalation_configs` registry -- per
    provider/app_package active-retrieval configuration. See
    `app/db.py`'s table comment for the persisted shape this mirrors."""

    id: str
    app_package: str
    provider_name: str
    #: Which `PhoneControlAdapter` backend to use for this provider --
    #: a free-form label (e.g. `"adb"`) resolved by the caller, never
    #: interpreted by this module. `None` (the default) means no real
    #: backend is wired -- `evaluate_escalation` treats that the same as
    #: `CapabilityState.DISABLED` regardless of the stored state (fail
    #: closed: a state without a usable backend must never silently
    #: behave as if retrieval ran).
    adapter_backend: Optional[str] = None
    capability_state: CapabilityState = CapabilityState.DISABLED
    #: Non-secret notes an operator leaves for themselves (e.g. why this
    #: provider was promoted, what shadow accuracy looked like) --
    #: cosmetic, never interpreted.
    notes: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        if isinstance(self.capability_state, str):
            self.capability_state = CapabilityState(self.capability_state)


def validate_config_registration(*, app_package: str, provider_name: str) -> None:
    """Shared validation for a new registry row -- raises
    `PhoneEscalationError` (never silently accepts). A fresh row is
    ALWAYS created with `capability_state=CapabilityState.DISABLED` --
    this function has no `capability_state` parameter at all, so a
    caller cannot even accidentally register a config that starts
    anywhere else (see `test_fresh_provider_config_always_starts_disabled`)."""
    if not app_package or not app_package.strip() or " " in app_package:
        raise PhoneEscalationError(f"app_package must be a bare Android package name (no spaces), got {app_package!r}")
    if not provider_name or not provider_name.strip():
        raise PhoneEscalationError("provider_name is required")
    if is_denied_app_package(app_package):
        raise PhoneEscalationError(
            f"app_package {app_package!r} matches DENIED_APP_PACKAGES (a broker/banking/payment app) -- "
            "active phone-control retrieval may never be configured for a broker or financial-account app, "
            "even at DISABLED state"
        )


# ---------------------------------------------------------------------------
# Broker / banking deny-list (point 2 of the hard safety rails)
# ---------------------------------------------------------------------------

#: Every broker this codebase already integrates (app/brokers/*.py),
#: keyed by their real Android package names where officially published.
#: This is a DEFENSE-IN-DEPTH structural refusal -- `open_app` raises
#: `DeniedAppPackageError` for ANY of these, never logs-and-continues.
#: Sourced from each broker's own official Android app listing package
#: name (Google Play Store), current as of this task's writing --
#: verify/update alongside `app/brokers/*.py` if a broker integration is
#: added or a listing changes.
BROKER_APP_PACKAGES: frozenset[str] = frozenset(
    {
        # app/brokers/alpaca.py
        "com.alpaca.app",
        # app/brokers/ibkr.py
        "com.ibkr.mtrader",
        "atws.app",
        # app/brokers/mt4_mt5.py
        "net.metaquotes.metatrader4",
        "net.metaquotes.metatrader5",
        # app/brokers/oanda.py
        "com.oanda.mobile.trade",
        "com.oanda.fxtrade.trade",
        # app/brokers/tradestation.py
        "com.tradestation.mobile",
        # app/brokers/tastytrade.py
        "com.tastyworks.tastytrade",
        # app/brokers/schwab.py
        "com.schwab.mobile",
        "com.schwab.thinkorswim",
        # app/brokers/robinhood.py
        "com.robinhood.android",
        # app/brokers/tradovate.py
        "com.tradovate.mobileapp",
        # app/brokers/rithmic.py -- Rithmic has no single official
        # consumer Android app of its own (it's an execution API most
        # front-ends white-label); the most common Rithmic-connected
        # retail front-ends are denied as a defensive catch-all below.
        "com.rtraderpro.android",
        # ccxt exchanges (app/brokers/ccxt_broker.py) -- the exchanges
        # this codebase's ccxt integration is actually verified against
        # (see that module's own docstring), plus the broader top
        # exchanges ccxt itself supports, as a defensive catch-all.
        "com.binance.dev",
        "com.coinbase.android",
        "com.coinbase.pro",
        "com.kraken.trade",
        "com.bybit.app",
        "com.okinc.okex.market",
        "com.kucoin.trade",
    }
)

#: Banking/payment package-NAME PATTERNS (not just exact package names)
#: as a defensive catch-all per the brief's point 2 -- a substring/regex
#: match against a candidate package name, since new banking apps appear
#: far faster than this list could be hand-maintained exhaustively.
_BANKING_PAYMENT_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"bank",
        r"paypal",
        r"venmo",
        r"zelle",
        r"cashapp",
        r"\bcash\.app\b",
        r"wise\.app",
        r"\bwallet\b",
        r"\bpay\b",  # com.google.android.apps.walletnfcrel-style / *pay* apps
        r"chase\.sig",
        r"wellsfargo",
        r"bankofamerica",
        r"citibank",
        r"capitalone",
        r"amex",
        r"americanexpress",
        r"coinbase",  # also a broker (ccxt) -- covered twice deliberately
        r"binance",
        r"kraken",
        r"crypto\.com",
    )
)


def is_denied_app_package(app_package: str) -> bool:
    """`True` for any package on `BROKER_APP_PACKAGES` (exact match) or
    matching `_BANKING_PAYMENT_PATTERNS` (substring/regex match) -- the
    single function both `validate_config_registration` (config-time)
    and `PhoneControlAdapter.open_app` (call-time, the real structural
    enforcement) consult, so the two can never drift apart."""
    if not app_package:
        return False
    normalized = app_package.strip().lower()
    if normalized in BROKER_APP_PACKAGES:
        return True
    return any(pattern.search(normalized) for pattern in _BANKING_PAYMENT_PATTERNS)


# ---------------------------------------------------------------------------
# The read-only phone-control action surface (point 1 of the hard safety
# rails)
# ---------------------------------------------------------------------------


@dataclass
class AccessibilityNode:
    """One node of a simplified accessibility-tree read -- exactly what
    `get_accessibility_tree` returns, and the only shape `tap` accepts a
    target from (see that method's own docstring for why this makes
    tapping a send/trade button structurally unreachable)."""

    node_id: str
    text: Optional[str]
    content_description: Optional[str]
    #: This module's own closed classification of what KIND of UI element
    #: this node is, assigned by the adapter backend from Android's real
    #: accessibility metadata (class name, resource-id naming, common
    #: action sets) -- never trusted from raw/unclassified input. Only
    #: `"navigation"` nodes may ever be passed to `tap` -- see that
    #: method's own docstring.
    role: str
    children: list["AccessibilityNode"] = field(default_factory=list)


class PhoneControlAdapter(abc.ABC):
    """The narrow, read-only action surface active phone-control
    retrieval is allowed to use. This is a HARDCODED ALLOW-LIST: the
    class defines exactly six public methods
    (`open_app`/`read_screen_text`/`get_accessibility_tree`/`tap`/
    `scroll`/`go_back`) and no others -- there is no generic "tap
    anywhere (x, y)" or "type text into field" primitive at all, on this
    class or any subclass that respects the interface (see
    `tests/test_t13_phone_escalation.py::
    test_adapter_public_surface_has_no_action_capable_of_submitting_or_typing`,
    an AST-based static check of this exact class, so an accidental
    future addition of a send/type/submit-shaped method fails CI, not
    just code review).

    Why this is a STRUCTURAL guarantee, not merely a documented one:
      - There is no `type_text`/`send_text`/`input_text` method at all --
        text entry literally cannot be requested through this interface,
        for ANY field, navigation or otherwise. A provider whose
        navigation would require typing (e.g. a search box) is out of
        scope for this adapter, not an allowed exception.
      - `tap` takes an `AccessibilityNode` (from THIS SAME adapter's own
        prior `get_accessibility_tree` call) and REQUIRES `node.role ==
        "navigation"` -- it raises `PermissionError` for anything else,
        so a caller cannot tap a node classified as a "submit"/"send"/
        "confirm"/"buy"/"sell" action even if it wanted to, because the
        backend's own classification (never the caller's) decides the
        role, and only `"navigation"`-classified nodes are tappable.
      - `open_app` refuses (raises `DeniedAppPackageError`) any package
        on `DENIED_APP_PACKAGES` before a subclass's own implementation
        (`_do_open_app`) ever runs -- enforced in this base class, so a
        subclass cannot open a broker/banking app just by implementing
        `_do_open_app` without also reimplementing (and thereby
        removing) this check.

    Subclasses implement the six `_do_*` protected methods; the public
    methods here are `final` in spirit (not reassigned) and are what
    every caller in this module uses."""

    #: A short, stable identifier for this backend (e.g. "adb", "mock")
    #: -- what `ProviderEscalationConfig.adapter_backend` is expected to
    #: name.
    backend_name: str = "unset"

    async def open_app(self, app_package: str) -> None:
        """Open (bring to foreground) an authorized app. Structurally
        refuses (raises `DeniedAppPackageError`, never a log-and-
        continue) any package on `DENIED_APP_PACKAGES` -- see this
        class's own docstring for why a subclass cannot route around
        this check."""
        if is_denied_app_package(app_package):
            raise DeniedAppPackageError(
                f"refusing to open_app({app_package!r}) -- this package is on the broker/banking/payment "
                "deny-list; active phone-control retrieval may never open a broker or financial-account app"
            )
        await self._do_open_app(app_package)

    async def read_screen_text(self) -> str:
        """The currently visible screen's plain text, best-effort
        (whatever the OS's own text-extraction gives, same honesty
        convention as `app.notification_bridge.ContentCompleteness` --
        never fabricated when unavailable)."""
        return await self._do_read_screen_text()

    async def get_accessibility_tree(self) -> AccessibilityNode:
        """The current screen's accessibility tree, as `AccessibilityNode`
        -- the structured read the extraction boundary
        (`extract_signal_candidate`) is meant to consume, and the ONLY
        source of `tap`-able targets."""
        return await self._do_get_accessibility_tree()

    async def tap(self, node: AccessibilityNode) -> None:
        """Tap ONE node from a prior `get_accessibility_tree()` call --
        restricted to `role == "navigation"` (see this class's own
        docstring for why this makes a send/trade/confirm tap
        structurally unreachable, not merely discouraged)."""
        if node.role != "navigation":
            raise PermissionError(
                f"refusing to tap node {node.node_id!r} with role={node.role!r} -- only role='navigation' "
                "nodes may be tapped; this adapter has no way to tap a submit/send/confirm/buy/sell-classified "
                "element even if asked"
            )
        await self._do_tap(node)

    async def scroll(self, direction: str) -> None:
        """Scroll the current screen -- `direction` is `"up"`/`"down"`/
        `"left"`/`"right"` only (validated by each backend's own
        `_do_scroll`); navigation-only by construction (there is nothing
        to scroll a value/confirmation with)."""
        await self._do_scroll(direction)

    async def go_back(self) -> None:
        """The OS back action -- always available, never blocked."""
        await self._do_go_back()

    # -- backend hooks (subclasses implement these, never the public API) --

    @abc.abstractmethod
    async def _do_open_app(self, app_package: str) -> None: ...

    @abc.abstractmethod
    async def _do_read_screen_text(self) -> str: ...

    @abc.abstractmethod
    async def _do_get_accessibility_tree(self) -> AccessibilityNode: ...

    @abc.abstractmethod
    async def _do_tap(self, node: AccessibilityNode) -> None: ...

    @abc.abstractmethod
    async def _do_scroll(self, direction: str) -> None: ...

    @abc.abstractmethod
    async def _do_go_back(self) -> None: ...


class MockPhoneControlAdapter(PhoneControlAdapter):
    """TEST-ONLY fixture adapter -- returns deterministic canned data,
    never talks to any real device. NOT a working device backend; do not
    use this outside `tests/`. Construct it with a fixed
    `accessibility_tree`/`screen_text` to control what a test's
    subsequent `extract_signal_candidate` call sees."""

    backend_name = "mock"

    def __init__(self, *, screen_text: str = "", accessibility_tree: AccessibilityNode | None = None) -> None:
        self._screen_text = screen_text
        self._tree = accessibility_tree or AccessibilityNode(
            node_id="root", text=None, content_description=None, role="navigation"
        )
        self.opened_packages: list[str] = []
        self.tapped_node_ids: list[str] = []

    async def _do_open_app(self, app_package: str) -> None:
        self.opened_packages.append(app_package)

    async def _do_read_screen_text(self) -> str:
        return self._screen_text

    async def _do_get_accessibility_tree(self) -> AccessibilityNode:
        return self._tree

    async def _do_tap(self, node: AccessibilityNode) -> None:
        self.tapped_node_ids.append(node.node_id)

    async def _do_scroll(self, direction: str) -> None:
        return None

    async def _do_go_back(self) -> None:
        return None


class AdbPhoneControlAdapter(PhoneControlAdapter):
    """RECOMMENDED real-backend design -- NOT implemented against a real
    device in this environment (no physical Android device, ADB
    connection, or emulator is available here; see this module's own
    top-level docstring and the task's own hard rule 4). Every `_do_*`
    method below raises `NotImplementedError` -- this class exists to
    document the intended real implementation shape, not to be used.

    Why ADB shell (`platform-tools`), not a third-party phone-automation
    framework: Android's own `adb shell screencap` (screenshot),
    `adb shell uiautomator dump` (the accessibility-tree XML dump --
    directly maps to `get_accessibility_tree`'s `AccessibilityNode`
    shape), and `adb shell input tap/swipe/keyevent` (navigation-only
    here -- `input text` is deliberately never called by this design,
    since this interface has no text-entry primitive at all) are
    official, already on any Android SDK install, and are the actual
    underlying mechanism most open-source phone-automation projects
    (DroidMind, PhoneAgent, phone-harness) wrap. A thin subprocess/adb-
    shell wrapper here is a natural, minimal-dependency real backend --
    consistent with this session's stated preference for a direct/self-
    hosted implementation over adding a large third-party automation
    framework as a dependency. Follow-up (needs a real device, out of
    this task's scope):
      - `_do_open_app`: `adb shell monkey -p {app_package} -c
        android.intent.category.LAUNCHER 1` (still gated by the base
        class's deny-list check before this ever runs).
      - `_do_read_screen_text` / `_do_get_accessibility_tree`:
        `adb shell uiautomator dump /sdcard/window_dump.xml` then
        `adb pull`, parsed into `AccessibilityNode` (an element's
        `clickable`/`class` attributes and resource-id naming heuristics
        classify `role`; only elements the classifier is confident are
        pure navigation -- a tab, a channel/thread list item, a back
        chevron -- are ever marked `"navigation"`; anything ambiguous or
        matching a send/submit/buy/sell-shaped resource-id or text is
        classified as a non-navigation role and therefore untappable by
        construction, never merely trusted).
      - `_do_tap` / `_do_scroll` / `_do_go_back`:
        `adb shell input tap {x} {y}` / `adb shell input swipe ...` /
        `adb shell input keyevent KEYCODE_BACK`, computed from the
        already-validated node's on-screen bounds.
    """

    backend_name = "adb"

    def __init__(self, *, adb_serial: str) -> None:
        self._adb_serial = adb_serial

    async def _do_open_app(self, app_package: str) -> None:
        raise NotImplementedError(
            "AdbPhoneControlAdapter is a documented real-backend DESIGN ONLY -- no physical Android device, "
            "ADB connection, or emulator is available in this environment. See this class's own docstring "
            "for the intended `adb shell` implementation; wiring it up is a follow-up that needs a real device."
        )

    async def _do_read_screen_text(self) -> str:
        raise NotImplementedError(self._do_open_app.__doc__ or "")

    async def _do_get_accessibility_tree(self) -> AccessibilityNode:
        raise NotImplementedError(self._do_open_app.__doc__ or "")

    async def _do_tap(self, node: AccessibilityNode) -> None:
        raise NotImplementedError(self._do_open_app.__doc__ or "")

    async def _do_scroll(self, direction: str) -> None:
        raise NotImplementedError(self._do_open_app.__doc__ or "")

    async def _do_go_back(self) -> None:
        raise NotImplementedError(self._do_open_app.__doc__ or "")


# ---------------------------------------------------------------------------
# The bounded extraction boundary: text-in, structured-JSON-out, no tool
# access of its own
# ---------------------------------------------------------------------------


class ExtractionStatus(str, enum.Enum):
    CANDIDATE = "candidate"
    #: Honest "could not confidently extract a signal" outcome -- never
    #: fabricated data. Same CapabilityIntrospection convention (fail
    #: closed / honest unsupported) as the rest of this codebase.
    UNKNOWN = "unknown"


@dataclass
class ExtractionResult:
    """What `extract_signal_candidate` returns. `status=UNKNOWN` means
    `fields` is empty and this candidate must never be routed anywhere
    -- the caller's only valid actions for `UNKNOWN` are recording the
    escalation attempt and stopping."""

    status: ExtractionStatus
    #: A raw field dict shaped like a subset of `app.models.Signal`'s own
    #: constructor kwargs (e.g. `{"symbol": ..., "side": ..., "price":
    #: ...}`) -- deliberately NOT a `Signal` instance itself, so this
    #: boundary can never accidentally carry a `Signal`'s own identity/
    #: dedup fields (`channel_id`/`message_id`/`id`) across from
    #: whatever internal state the extraction step had; the CALLER
    #: (`evaluate_escalation`) is solely responsible for constructing
    #: the real `Signal` and setting its identity fields from the
    #: triggering notification event.
    fields: dict[str, Any] = field(default_factory=dict)
    detail: Optional[str] = None


class SignalExtractor:
    """The permission boundary for the LLM extraction step -- text/tree
    in, `ExtractionResult` out, and NOTHING else. An implementation of
    `_do_extract` must not (and structurally cannot, via this class's
    own signature) call `PhoneControlAdapter`, reach any other tool, or
    take any action of its own; it only ever sees the plain string this
    class hands it and returns a plain dict/status. This mirrors
    `signal-portfolio-commercial`'s `model_gateway.py`-style boundary
    (permission boundary only, no execution authority) -- no equivalent
    module existed yet in signal-copier, so this class is the first one
    here.

    The base class's `extract` is what every caller uses; `_do_extract`
    is the one method a real implementation overrides (e.g. a call to an
    LLM API with a fixed, narrow system prompt and a JSON-schema-
    constrained response, and NO tool/function-calling capability
    granted to that call at all -- granting tools there would defeat the
    entire point of this boundary)."""

    async def extract(self, raw_text: str) -> ExtractionResult:
        if not raw_text or not raw_text.strip():
            return ExtractionResult(status=ExtractionStatus.UNKNOWN, detail="no text/tree content to extract from")
        return await self._do_extract(raw_text)

    async def _do_extract(self, raw_text: str) -> ExtractionResult:
        raise NotImplementedError(
            "SignalExtractor is the permission-boundary interface only -- no real LLM-backed implementation "
            "is wired in this environment. A real implementation calls an LLM with a fixed extraction prompt "
            "and a JSON-schema-constrained response, with NO tool/function-calling access granted to that "
            "call (text-in, structured-JSON-out only) -- see this class's own docstring."
        )


class MockSignalExtractor(SignalExtractor):
    """TEST-ONLY deterministic extractor -- returns a fixed
    `ExtractionResult` regardless of input (or one chosen at
    construction time), never a real LLM call. NOT a working extraction
    backend."""

    def __init__(self, result: ExtractionResult | None = None) -> None:
        self._result = result or ExtractionResult(status=ExtractionStatus.UNKNOWN, detail="mock: no result configured")

    async def _do_extract(self, raw_text: str) -> ExtractionResult:
        return self._result


# ---------------------------------------------------------------------------
# Escalation attempts (audit trail)
# ---------------------------------------------------------------------------


class EscalationDisposition(str, enum.Enum):
    """The real, honest outcome of one `evaluate_escalation` call --
    never a silent no-op indistinguishable from "nothing needed doing"."""

    #: Point 1: a direct source already covered this event -- retrieval
    #: was never attempted.
    COVERED_BY_DIRECT_SOURCE = "covered_by_direct_source"
    #: Point 2: the notification's own content_completeness didn't need
    #: escalation (it was COMPLETE).
    CONTENT_ALREADY_COMPLETE = "content_already_complete"
    #: Point 3: no config row for this app_package, or its
    #: capability_state is DISABLED (or it has no adapter_backend wired
    #: -- see `ProviderEscalationConfig.adapter_backend`'s own docstring
    #: for why that's treated the same as DISABLED).
    CAPABILITY_DISABLED = "capability_disabled"
    #: Retrieval ran, extraction produced no usable candidate.
    EXTRACTION_UNKNOWN = "extraction_unknown"
    #: SHADOW state: retrieval ran and produced a candidate, but it is
    #: recorded ONLY -- never passed to `engine.handle_signal`. See this
    #: module's own docstring.
    SHADOW_LOGGED_ONLY = "shadow_logged_only"
    #: ENABLED state: retrieval ran, extraction produced a candidate,
    #: and the caller is expected to route it through the normal
    #: pipeline (`engine.handle_signal`) exactly as any other source.
    ENABLED_CANDIDATE_READY = "enabled_candidate_ready"


@dataclass
class EscalationAttempt:
    """One row of the `phone_escalation_attempts` audit ledger -- see
    `app/db.py`'s table comment for the persisted shape."""

    id: str
    device_id: str
    app_package: str
    notification_key: str
    content_hash: str
    capability_state_at_attempt: CapabilityState
    disposition: EscalationDisposition
    extraction_status: Optional[ExtractionStatus] = None
    extraction_detail: Optional[str] = None
    signal_id: Optional[str] = None
    created_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        if isinstance(self.capability_state_at_attempt, str):
            self.capability_state_at_attempt = CapabilityState(self.capability_state_at_attempt)
        if isinstance(self.disposition, str):
            self.disposition = EscalationDisposition(self.disposition)
        if isinstance(self.extraction_status, str):
            self.extraction_status = ExtractionStatus(self.extraction_status)


def new_attempt_id() -> str:
    return str(uuid.uuid4())


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# The escalation trigger itself
# ---------------------------------------------------------------------------


async def evaluate_escalation(
    *,
    device_id: str,
    app_package: str,
    notification_key: str,
    content_hash: str,
    completeness: ContentCompleteness,
    covered_by_direct_source: bool,
    config: ProviderEscalationConfig | None,
    adapter: PhoneControlAdapter | None,
    extractor: SignalExtractor | None,
) -> tuple[EscalationAttempt, ExtractionResult | None]:
    """The one function that decides whether, and how, active retrieval
    runs for one captured notification event -- implements points 1-3 of
    this module's own docstring in order, short-circuiting (and
    returning an honest disposition, never attempting retrieval) the
    moment any gate fails.

    `covered_by_direct_source` is the caller's own best-effort answer to
    point 1 today (Track 12 not yet landed -- see `EscalationCandidate`'s
    own docstring); `config`/`adapter`/`extractor` being `None` are all
    valid, honest "not configured yet" inputs that this function fails
    CLOSED on (never treated as "assume yes/proceed").

    Returns the `EscalationAttempt` record (always -- the caller is
    expected to persist it via `SignalStore.record_phone_escalation_attempt`
    regardless of outcome, same "always record, never silently skip" audit
    convention as `app/notification_bridge.py`'s own event ledger) and,
    only for `EscalationDisposition.ENABLED_CANDIDATE_READY`, the
    `ExtractionResult` whose `fields` the caller should build a `Signal`
    from and route through `engine.handle_signal`."""
    attempt_id = new_attempt_id()

    if covered_by_direct_source:
        return (
            EscalationAttempt(
                id=attempt_id,
                device_id=device_id,
                app_package=app_package,
                notification_key=notification_key,
                content_hash=content_hash,
                capability_state_at_attempt=CapabilityState.DISABLED,
                disposition=EscalationDisposition.COVERED_BY_DIRECT_SOURCE,
                created_at=now_utc(),
            ),
            None,
        )

    if not needs_escalation(completeness):
        return (
            EscalationAttempt(
                id=attempt_id,
                device_id=device_id,
                app_package=app_package,
                notification_key=notification_key,
                content_hash=content_hash,
                capability_state_at_attempt=CapabilityState.DISABLED,
                disposition=EscalationDisposition.CONTENT_ALREADY_COMPLETE,
                created_at=now_utc(),
            ),
            None,
        )

    state = config.capability_state if config is not None else CapabilityState.DISABLED
    # Fail closed: DISABLED, no config row at all, or no real adapter
    # backend wired -- all treated identically as "retrieval must not
    # run," never distinguished in a way that could be mistaken for
    # partial readiness.
    if state is CapabilityState.DISABLED or adapter is None or extractor is None:
        return (
            EscalationAttempt(
                id=attempt_id,
                device_id=device_id,
                app_package=app_package,
                notification_key=notification_key,
                content_hash=content_hash,
                capability_state_at_attempt=state,
                disposition=EscalationDisposition.CAPABILITY_DISABLED,
                created_at=now_utc(),
            ),
            None,
        )

    # Point 1 (read-only navigation): open the authorized app, read what
    # it shows, hand ONLY that text to the extraction boundary. No write
    # action of any kind occurs on this path -- `adapter` exposes none.
    await adapter.open_app(app_package)
    tree = await adapter.get_accessibility_tree()
    screen_text = await adapter.read_screen_text()
    raw_text = screen_text or (tree.text or "")
    extraction = await extractor.extract(raw_text)

    if extraction.status is not ExtractionStatus.CANDIDATE:
        return (
            EscalationAttempt(
                id=attempt_id,
                device_id=device_id,
                app_package=app_package,
                notification_key=notification_key,
                content_hash=content_hash,
                capability_state_at_attempt=state,
                disposition=EscalationDisposition.EXTRACTION_UNKNOWN,
                extraction_status=extraction.status,
                extraction_detail=extraction.detail,
                created_at=now_utc(),
            ),
            None,
        )

    if state is CapabilityState.SHADOW:
        return (
            EscalationAttempt(
                id=attempt_id,
                device_id=device_id,
                app_package=app_package,
                notification_key=notification_key,
                content_hash=content_hash,
                capability_state_at_attempt=state,
                disposition=EscalationDisposition.SHADOW_LOGGED_ONLY,
                extraction_status=extraction.status,
                extraction_detail=extraction.detail,
                created_at=now_utc(),
            ),
            None,
        )

    # state is ENABLED and extraction produced a real candidate -- the
    # caller feeds `extraction.fields` into the normal pipeline.
    return (
        EscalationAttempt(
            id=attempt_id,
            device_id=device_id,
            app_package=app_package,
            notification_key=notification_key,
            content_hash=content_hash,
            capability_state_at_attempt=state,
            disposition=EscalationDisposition.ENABLED_CANDIDATE_READY,
            extraction_status=extraction.status,
            extraction_detail=extraction.detail,
            created_at=now_utc(),
        ),
        extraction,
    )
