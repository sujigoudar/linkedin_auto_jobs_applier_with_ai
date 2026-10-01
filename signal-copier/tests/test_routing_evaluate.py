"""Direct unit coverage for app/routing.py's `RoutingConfig.evaluate`/
`destinations_for` -- the real matching/precedence/dedup/pause algorithm
every signal is routed through.

Added as part of a mutation-testing pass (track39). Before this file,
`RoutingConfig` was exercised only INCIDENTALLY across dozens of other
test files (every engine/integration test needs a routing config to set
up accounts), never directly, rule-trace-first, the way SIG-01's own
`tests/test_sig01_duplicate_submission_protection.py` and TR-11's
`tests/test_tr11_routing_simulator.py` partially do. Mutation testing
scoped to those two files found several real, severe survivors in
`evaluate`'s own per-rule loop -- most critically, two `continue` ->
`break` mutations that would silently stop evaluating every rule AFTER
the first one whose source/symbol_filter doesn't match the current
signal, which the narrower existing test configs (every rule in them
happens to match, or the non-matching rule is always last) never
exercised. These tests close that gap directly, independent of any
other test file's own config shape.
"""
from __future__ import annotations

from app.models import DestinationAccount
from app.routing import RoutingConfig, RoutingRule, load_routing_config


def _account(account_id: str, **overrides) -> DestinationAccount:
    return DestinationAccount(account_id=account_id, broker="paper", **overrides)


def test_destinations_for_default_include_disabled_is_false():
    """Kills the `include_disabled: bool = True` default mutant on
    `destinations_for` itself. A plain call with no `include_disabled`
    must not admit a disabled/paused account."""
    account = _account("acct1", enabled=False)
    config = RoutingConfig(
        rules=[RoutingRule(source="tv", destinations=["acct1"])], accounts={"acct1": account}
    )
    assert config.destinations_for("tv", "BTCUSDT") == []


def test_destinations_for_passes_include_disabled_true_through_to_evaluate():
    """Kills the `include_disabled=None` and dropped-kwarg mutants on
    `destinations_for`: passing `include_disabled=True` explicitly (the
    real EXE-10 close-signal path) must actually reach `evaluate` and
    admit the disabled account -- not be silently swallowed into a
    falsy `None`/omitted default."""
    account = _account("acct1", enabled=False)
    config = RoutingConfig(
        rules=[RoutingRule(source="tv", destinations=["acct1"])], accounts={"acct1": account}
    )
    assert config.destinations_for("tv", "BTCUSDT", include_disabled=True) == [account]


def test_evaluate_default_include_disabled_is_false():
    """Kills the `include_disabled: bool = True` default mutant on
    `evaluate` itself (distinct from `destinations_for`'s own default,
    which has its own, already-tested default). Calling `evaluate`
    with NO `include_disabled` argument at all must behave as a normal
    (non-CLOSE) entry -- a disabled account must be paused, not
    admitted, by default."""
    account = _account("acct1", enabled=False)
    config = RoutingConfig(
        rules=[RoutingRule(source="tv", destinations=["acct1"])], accounts={"acct1": account}
    )
    accounts, trace = config.evaluate("tv", "BTCUSDT")
    assert accounts == []
    assert trace[0]["paused"] == ["acct1"]
    assert trace[0]["admitted"] == []


def test_evaluate_continues_past_a_non_matching_source_rule_to_later_rules():
    """Kills the `continue` -> `break` mutant on the non-matching-SOURCE
    branch. A rule for an unrelated source appearing BEFORE a rule that
    actually matches this signal's source must not stop evaluation --
    every rule in `self.rules` must still be considered."""
    acct1 = _account("acct1")
    acct2 = _account("acct2")
    config = RoutingConfig(
        rules=[
            RoutingRule(source="other_source", destinations=["acct1"]),
            RoutingRule(source="tv", destinations=["acct2"]),
        ],
        accounts={"acct1": acct1, "acct2": acct2},
    )
    assert config.destinations_for("tv", "BTCUSDT") == [acct2]


def test_evaluate_continues_past_a_non_matching_symbol_filter_rule_to_later_rules():
    """Kills the `continue` -> `break` mutant on the non-matching
    `symbol_filter` branch, mirroring the source-mismatch case above."""
    acct1 = _account("acct1")
    acct2 = _account("acct2")
    config = RoutingConfig(
        rules=[
            RoutingRule(source="tv", destinations=["acct1"], symbol_filter=["ETHUSDT"]),
            RoutingRule(source="tv", destinations=["acct2"]),
        ],
        accounts={"acct1": acct1, "acct2": acct2},
    )
    assert config.destinations_for("tv", "BTCUSDT") == [acct2]


def test_trace_for_a_non_matching_source_reports_matched_false_with_rule_and_reason():
    """Kills the string-key mutants ("rule"/"matched"/"reason" renamed or
    `matched` flipped to True, or the whole trace entry replaced with
    `None`) on the non-matching-SOURCE branch specifically -- no existing
    test asserts the trace SHAPE for this exact branch (only the
    symbol_filter non-match branch is asserted elsewhere)."""
    acct1 = _account("acct1")
    rule = RoutingRule(source="other_source", destinations=["acct1"])
    config = RoutingConfig(rules=[rule], accounts={"acct1": acct1})
    _, trace = config.evaluate("tv", "BTCUSDT")
    assert trace == [
        {
            "rule": rule,
            "matched": False,
            "reason": "rule source 'other_source' does not match signal source 'tv'",
        }
    ]


def test_deduped_bucket_is_an_empty_list_not_none_when_nothing_was_deduped():
    """Kills the `deduped: list[str] = None` mutant: a rule with no
    duplicate destinations must report `deduped: []`, not `None` --
    callers (TR-11's simulator JSON response) rely on it always being a
    real, iterable list."""
    acct1 = _account("acct1")
    config = RoutingConfig(
        rules=[RoutingRule(source="tv", destinations=["acct1"])], accounts={"acct1": acct1}
    )
    _, trace = config.evaluate("tv", "BTCUSDT")
    assert trace[0]["deduped"] == []


def test_deduped_bucket_contains_the_real_account_id_not_none():
    """Kills the `deduped.append(None)` mutant: a rule naming the same
    destination account twice (or a later rule re-naming an account a
    prior rule already claimed) must report the REAL account id in
    `deduped`, never a fabricated `None`."""
    acct1 = _account("acct1")
    rule = RoutingRule(source="tv", destinations=["acct1", "acct1"])
    config = RoutingConfig(rules=[rule], accounts={"acct1": acct1})
    _, trace = config.evaluate("tv", "BTCUSDT")
    assert trace[0]["deduped"] == ["acct1"]
    assert trace[0]["admitted"] == ["acct1"]


def test_evaluate_continues_past_a_duplicate_destination_within_one_rule():
    """Kills the `continue` -> `break` mutant in the per-destination
    dedup check: a rule listing a duplicate account FIRST, followed by a
    genuinely new account, must still admit the new one -- the dedup
    `continue` must not abort the rest of that rule's destinations."""
    acct1 = _account("acct1")
    acct2 = _account("acct2")
    rule = RoutingRule(source="tv", destinations=["acct1", "acct1", "acct2"])
    config = RoutingConfig(rules=[rule], accounts={"acct1": acct1, "acct2": acct2})
    assert config.destinations_for("tv", "BTCUSDT") == [acct1, acct2]


def test_evaluate_continues_past_an_unconfigured_account_id_within_one_rule():
    """Kills the `continue` -> `break` mutant in the "account id isn't
    configured at all" branch: a rule naming an unknown account id
    FIRST, followed by a real, configured one, must still admit the
    real one -- a config error on one destination must not silently
    drop every destination listed after it in the same rule."""
    acct2 = _account("acct2")
    rule = RoutingRule(source="tv", destinations=["does-not-exist", "acct2"])
    config = RoutingConfig(rules=[rule], accounts={"acct2": acct2})
    assert config.destinations_for("tv", "BTCUSDT") == [acct2]


# -- load_routing_config (the static YAML bootstrap loader) -----------------
#
# Before this test, `load_routing_config` had ZERO direct test coverage
# anywhere in this suite (confirmed by grepping the whole tests/ tree) --
# only its live, DB-backed sibling `load_routing_config_from_store` was
# exercised, via app/main.py's config CRUD endpoints. A mutation-testing
# pass against this file found all ~120 of its mutants surviving for
# exactly that reason: no test ever calls it at all. This closes that
# gap for the fields that actually drive money -- `multiplier`,
# `fixed_quantity`, `max_notional_exposure`, `risk_percent_of_equity`,
# `symbol_map`, `enabled` -- plus the two YAML files' basic shape.


def test_load_routing_config_parses_accounts_and_rules_from_yaml(tmp_path):
    accounts_path = tmp_path / "accounts.yaml"
    routing_path = tmp_path / "routing.yaml"
    accounts_path.write_text(
        """
accounts:
  acct1:
    broker: alpaca
    multiplier: 2.5
    fixed_quantity: 10.0
    symbol_map:
      BTCUSD: BTC/USDT
    enabled: false
    max_notional_exposure: 5000.0
    risk_percent_of_equity: 0.02
    managed_lifecycle: true
    management_recipe: full_managed_lifecycle
    qualification_level: account_entitled
    exclusive_writer_qualified: true
"""
    )
    routing_path.write_text(
        """
rules:
  - source: tradingview
    destinations: [acct1]
    symbol_filter: [BTCUSDT]
"""
    )
    config = load_routing_config(routing_path, accounts_path)

    account = config.accounts["acct1"]
    assert account.account_id == "acct1"
    assert account.broker == "alpaca"
    assert account.multiplier == 2.5
    assert account.fixed_quantity == 10.0
    assert account.symbol_map == {"BTCUSD": "BTC/USDT"}
    assert account.enabled is False
    assert account.max_notional_exposure == 5000.0
    assert account.risk_percent_of_equity == 0.02
    assert account.managed_lifecycle is True
    from app.models import ManagementRecipe

    assert account.management_recipe == ManagementRecipe.FULL_MANAGED_LIFECYCLE
    assert account.qualification_level == "account_entitled"
    assert account.exclusive_writer_qualified is True

    assert len(config.rules) == 1
    rule = config.rules[0]
    assert rule.source == "tradingview"
    assert rule.destinations == ["acct1"]
    assert rule.symbol_filter == ["BTCUSDT"]


def test_load_routing_config_honors_an_explicit_management_recipe_mismatched_against_managed_lifecycle(tmp_path):
    """P0-5: an explicit `management_recipe` in accounts.yaml is honored
    AS-IS, even when it deliberately mismatches `managed_lifecycle` --
    this is the one scenario that distinguishes "the loader actually
    parsed `management_recipe` from the YAML" from "`DestinationAccount
    .__post_init__` just backfilled it from `managed_lifecycle` anyway"
    (which is what every OTHER test in this file would still pass under
    even if `management_recipe` parsing were silently broken, since they
    all set compatible values for both fields)."""
    accounts_path = tmp_path / "accounts.yaml"
    routing_path = tmp_path / "routing.yaml"
    accounts_path.write_text(
        """
accounts:
  acct1:
    broker: paper
    managed_lifecycle: false
    management_recipe: full_managed_lifecycle
"""
    )
    routing_path.write_text("rules: []\n")
    config = load_routing_config(routing_path, accounts_path)

    from app.models import ManagementRecipe

    account = config.accounts["acct1"]
    assert account.managed_lifecycle is False
    assert account.management_recipe == ManagementRecipe.FULL_MANAGED_LIFECYCLE


def test_load_routing_config_with_a_yaml_file_present_but_no_accounts_or_rules_key(tmp_path):
    """Kills the `raw_accounts.get("accounts", {})`/`raw_routing.get(
    "rules", [])` default-value mutants: a YAML file that EXISTS but
    doesn't have an "accounts"/"rules" top-level key at all (e.g. an
    empty or unrelated-content file) must still produce an empty,
    valid config -- never a KeyError or a `None`-related crash."""
    accounts_path = tmp_path / "accounts.yaml"
    routing_path = tmp_path / "routing.yaml"
    accounts_path.write_text("some_other_key: true\n")
    routing_path.write_text("some_other_key: true\n")
    config = load_routing_config(routing_path, accounts_path)
    assert config.accounts == {}
    assert config.rules == []


def test_load_routing_config_applies_real_defaults_for_omitted_fields(tmp_path):
    """A minimal account spec (only the required `broker`) must get the
    REAL documented defaults -- multiplier 1.0, enabled True, no fixed
    quantity, no gates -- never a fabricated or zeroed value."""
    accounts_path = tmp_path / "accounts.yaml"
    routing_path = tmp_path / "routing.yaml"
    accounts_path.write_text(
        """
accounts:
  acct1:
    broker: paper
"""
    )
    routing_path.write_text("rules: []\n")
    config = load_routing_config(routing_path, accounts_path)

    account = config.accounts["acct1"]
    assert account.multiplier == 1.0
    assert account.fixed_quantity is None
    assert account.symbol_map == {}
    assert account.enabled is True
    assert account.max_notional_exposure is None
    assert account.risk_percent_of_equity is None
    # managed_lifecycle defaults False; qualification_level/
    # exclusive_writer_qualified/management_recipe follow the same "no
    # fabricated value" discipline.
    assert account.managed_lifecycle is False
    assert account.qualification_level is None
    assert account.exclusive_writer_qualified is False


def test_load_routing_config_with_missing_files_returns_an_empty_config(tmp_path):
    """Neither file existing yet (e.g. a fresh deployment before any
    account/rule has been configured) must not raise -- an empty,
    valid `RoutingConfig`, never a crash on startup."""
    config = load_routing_config(tmp_path / "missing_routing.yaml", tmp_path / "missing_accounts.yaml")
    assert config.accounts == {}
    assert config.rules == []


# -- load_routing_config_from_store (the live, DB-backed loader) ------------
#
# A duck-typed fake store (no real database needed -- this function only
# ever reads dict-like rows) exercising the same four fields
# (`management_recipe`/`qualification_level`/`exclusive_writer_qualified`/
# `managed_lifecycle`) that `load_routing_config`'s own tests above
# needed to kill their YAML-side mutants. `tests/test_config_crud.py`
# exercises this function end-to-end through real HTTP/DB round trips
# but has its own pre-existing, unrelated intermittent flakiness (a
# PaperBroker price-dependent rejection) that makes it unsuitable to
# include directly in a mutation-testing test selection -- this is a
# fast, deterministic substitute for the fields that matter here.


class _FakeStoreForRoutingFromStore:
    def __init__(self, account_rows, rule_rows):
        self._account_rows = account_rows
        self._rule_rows = rule_rows

    def list_config_accounts(self):
        return self._account_rows

    def list_config_routing_rules(self):
        return self._rule_rows


def test_load_routing_config_from_store_parses_the_real_governance_fields():
    from app.models import ManagementRecipe

    store = _FakeStoreForRoutingFromStore(
        account_rows=[
            {
                "account_id": "acct1",
                "broker": "alpaca",
                "multiplier": 1.0,
                "fixed_quantity": None,
                "symbol_map": {},
                "enabled": True,
                "managed_lifecycle": True,
                "max_notional_exposure": None,
                "risk_percent_of_equity": None,
                "management_recipe": "full_managed_lifecycle",
                "qualification_level": "account_entitled",
                "exclusive_writer_qualified": True,
            }
        ],
        rule_rows=[],
    )
    from app.routing import load_routing_config_from_store

    config = load_routing_config_from_store(store)
    account = config.accounts["acct1"]
    assert account.managed_lifecycle is True
    assert account.management_recipe == ManagementRecipe.FULL_MANAGED_LIFECYCLE
    assert account.qualification_level == "account_entitled"
    assert account.exclusive_writer_qualified is True


def test_load_routing_config_from_store_honors_an_explicit_management_recipe_mismatch():
    """Mirrors the YAML-loader mismatch test above, for the DB-backed
    loader: a row with `managed_lifecycle=False` but an explicit
    `management_recipe="full_managed_lifecycle"` must report the real
    parsed recipe, not let `DestinationAccount.__post_init__`'s
    managed_lifecycle-derived backfill mask a broken parse."""
    from app.models import ManagementRecipe
    from app.routing import load_routing_config_from_store

    store = _FakeStoreForRoutingFromStore(
        account_rows=[
            {
                "account_id": "acct1",
                "broker": "paper",
                "multiplier": 1.0,
                "fixed_quantity": None,
                "symbol_map": {},
                "enabled": True,
                "managed_lifecycle": False,
                "max_notional_exposure": None,
                "risk_percent_of_equity": None,
                "management_recipe": "full_managed_lifecycle",
                "qualification_level": None,
                "exclusive_writer_qualified": False,
            }
        ],
        rule_rows=[],
    )
    config = load_routing_config_from_store(store)
    account = config.accounts["acct1"]
    assert account.managed_lifecycle is False
    assert account.management_recipe == ManagementRecipe.FULL_MANAGED_LIFECYCLE


def test_load_routing_config_from_store_applies_real_defaults_when_governance_fields_absent():
    store = _FakeStoreForRoutingFromStore(
        account_rows=[
            {
                "account_id": "acct1",
                "broker": "paper",
                "multiplier": 1.0,
                "fixed_quantity": None,
                "symbol_map": {},
                "enabled": True,
                "managed_lifecycle": False,
                "max_notional_exposure": None,
                "risk_percent_of_equity": None,
                # management_recipe/qualification_level/exclusive_writer_qualified omitted entirely
            }
        ],
        rule_rows=[],
    )
    from app.routing import load_routing_config_from_store

    config = load_routing_config_from_store(store)
    account = config.accounts["acct1"]
    # DestinationAccount.__post_init__ fills an omitted management_recipe
    # from managed_lifecycle (False here) -- PLAIN_UNMANAGED, not a raw
    # None, is the correct, documented default for this path.
    from app.models import ManagementRecipe

    assert account.management_recipe == ManagementRecipe.PLAIN_UNMANAGED
    assert account.qualification_level is None
    assert account.exclusive_writer_qualified is False
