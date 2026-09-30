"""Track 15: sample-driven parser tooling -- app/parser_tooling.py
(message-type classification, structured field extraction, parser-
profile lifecycle validation), `SignalStore`'s new `parser_samples`/
`parser_profiles` CRUD (app/db.py), and the new `/parser-tooling/...`
REST routes (app/main.py).
"""
import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app import config as app_config
from app.db import SignalStore
from app.parser_tooling import (
    ExtractedFields,
    MessageType,
    ParserProfileStatus,
    ParserToolingError,
    classify_message_type,
    extract_fields,
    validate_profile_registration,
    validate_profile_transition,
    validate_supported_message_types,
)


# --- app/parser_tooling.py: MessageType classification ---------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("BUY AAPL 10 @190 SL 185 TP 200", MessageType.ENTRY),
        ("LONG BTCUSDT 0.5 @65000", MessageType.ENTRY),
        ("SELL EURUSD 0.5 SL 1.10 TP 1.09", MessageType.ENTRY),
        ("close AAPL", MessageType.EXIT),
        ("exit BTCUSDT", MessageType.EXIT),
        ("move SL to 185 on AAPL", MessageType.STOP_UPDATE),
        ("trailing stop on BTCUSDT", MessageType.STOP_UPDATE),
        ("new target 210 for AAPL", MessageType.TARGET_UPDATE),
        ("move tp to 220", MessageType.TARGET_UPDATE),
        ("add to AAPL position", MessageType.ADD),
        ("scaling in on BTCUSDT", MessageType.ADD),
        ("trim half of AAPL", MessageType.TRIM),
        ("taking partial profit on BTCUSDT", MessageType.TRIM),
        ("cancel the AAPL order", MessageType.CANCEL),
        ("scratch this trade", MessageType.CANCEL),
        ("TP1 hit on AAPL, +5%", MessageType.RESULT),
        ("stopped out on BTCUSDT, -1.5%", MessageType.RESULT),
        ("Weekly recap: 5 wins, 2 losses", MessageType.RECAP),
        ("Monthly performance summary attached", MessageType.RECAP),
        ("good morning everyone", MessageType.NON_SIGNAL),
        ("gm", MessageType.NON_SIGNAL),
        ("", MessageType.UNKNOWN),
        ("   ", MessageType.UNKNOWN),
        (
            "The broader market has been choppy lately with a lot of sector rotation happening",
            MessageType.UNKNOWN,
        ),
    ],
)
def test_classify_message_type_representative_examples(text, expected):
    assert classify_message_type(text) is expected


def test_classify_message_type_never_raises_on_arbitrary_text():
    # No input this classifier can't produce SOME MessageType for --
    # UNKNOWN is the honest fallback, never an exception.
    for text in ["🚀🚀🚀", "12345", "a" * 500, "\n\n\t", "BUY", "buy buy buy"]:
        result = classify_message_type(text)
        assert isinstance(result, MessageType)


def test_classify_message_type_honest_unknown_is_not_overused_for_clear_cases():
    # A sanity check that UNKNOWN doesn't swallow the clear, keyword-rich
    # cases above -- every parametrized example that has a confident
    # answer got one (see the parametrized test); this only re-confirms
    # the entry/exit reuse of the real parser is doing real work, not
    # falling through to the word-count NON_SIGNAL/UNKNOWN heuristics.
    assert classify_message_type("BUY MSFT 5 @420") is MessageType.ENTRY
    assert classify_message_type("close MSFT") is MessageType.EXIT


# --- app/parser_tooling.py: structured field extraction ---------------


def test_extract_fields_reuses_text_parser_signal_for_entry():
    result = extract_fields("BUY AAPL 10 lots @190 SL 185 TP1 200 TP2 210")
    assert result.symbol == "AAPL"
    assert result.asset_class == "equity"
    assert result.side == "buy"
    assert result.quantity == 10.0
    assert result.quantity_type == "lots"
    assert result.entry_price == 190.0
    assert result.stop == 185.0
    assert result.targets == [200.0, 210.0]


def test_extract_fields_time_in_force_and_provider_position_id():
    result = extract_fields("BUY AAPL 5 @190 GTC ref: POS-99182")
    assert result.time_in_force == "GTC"
    assert result.provider_position_id == "POS-99182"


def test_extract_fields_entry_range():
    result = extract_fields("BUY AAPL 5 entry 150-155")
    assert result.entry_range == (150.0, 155.0)


def test_extract_fields_option_contract_from_occ_symbol():
    result = extract_fields("BUY AAPL260918C00200000 5 @1.20")
    assert result.asset_class == "option"
    assert result.strike == 200.0
    assert result.call_put == "call"
    assert result.expiration == "2026-09-18"


def test_extract_fields_option_strategy_keyword():
    result = extract_fields("Opening an iron condor on SPX this week")
    assert result.option_strategy == "iron condor"


def test_extract_fields_never_guesses_unparseable_fields():
    # A stop-update message has no side keyword for text_parser.py's
    # grammar to match at all -- every Signal-shaped field this function
    # would otherwise read off a PARSED Signal must stay honestly None,
    # never fabricated from thin air.
    result = extract_fields("move SL to 185 on AAPL")
    assert result.entry_price is None
    assert result.quantity is None
    assert result.targets == []
    assert result.action == "stop_update"


def test_extract_fields_symbol_fallback_only_when_unambiguous():
    # Exactly one plausible ticker-shaped token -> confident fallback.
    result = extract_fields("cancel the AAPL order")
    assert result.symbol == "AAPL"
    # Two candidates with no way to disambiguate -> stays None, never a
    # coin-flip guess between them.
    ambiguous = extract_fields("cancel AAPL or MSFT, whichever fills first")
    assert ambiguous.symbol is None


def test_extracted_fields_as_dict_serializes_entry_range_as_list():
    result = ExtractedFields(entry_range=(1.0, 2.0))
    assert result.as_dict()["entry_range"] == [1.0, 2.0]
    assert ExtractedFields().as_dict()["entry_range"] is None


# --- app/parser_tooling.py: parser-profile lifecycle validation -------


def test_validate_profile_registration_rejects_empty_fields():
    with pytest.raises(ParserToolingError):
        validate_profile_registration(parser_id="", provider_id="p1", version="v1")
    with pytest.raises(ParserToolingError):
        validate_profile_registration(parser_id="pp1", provider_id="", version="v1")
    with pytest.raises(ParserToolingError):
        validate_profile_registration(parser_id="pp1", provider_id="p1", version="")


def test_validate_supported_message_types_rejects_unknown_value():
    with pytest.raises(ParserToolingError):
        validate_supported_message_types(["not_a_real_type"])
    assert validate_supported_message_types(["entry", "exit"]) == ["entry", "exit"]
    assert validate_supported_message_types(None) == []


def test_validate_profile_transition_same_state_is_a_no_op():
    validate_profile_transition(ParserProfileStatus.DRAFT, ParserProfileStatus.DRAFT)


def test_validate_profile_transition_cannot_skip_states():
    with pytest.raises(ParserToolingError):
        validate_profile_transition(ParserProfileStatus.DRAFT, ParserProfileStatus.ACTIVE)
    with pytest.raises(ParserToolingError):
        validate_profile_transition(ParserProfileStatus.DRAFT, ParserProfileStatus.CERTIFIED)
    with pytest.raises(ParserToolingError):
        validate_profile_transition(ParserProfileStatus.TESTED, ParserProfileStatus.CERTIFIED)
    with pytest.raises(ParserToolingError):
        validate_profile_transition(ParserProfileStatus.SHADOW, ParserProfileStatus.ACTIVE)


def test_validate_profile_transition_allows_the_full_forward_chain():
    validate_profile_transition(ParserProfileStatus.DRAFT, ParserProfileStatus.TESTED)
    validate_profile_transition(ParserProfileStatus.TESTED, ParserProfileStatus.SHADOW)
    validate_profile_transition(ParserProfileStatus.SHADOW, ParserProfileStatus.CERTIFIED)
    validate_profile_transition(ParserProfileStatus.CERTIFIED, ParserProfileStatus.ACTIVE)


def test_validate_profile_transition_any_state_can_retire_directly():
    for state in (
        ParserProfileStatus.DRAFT,
        ParserProfileStatus.TESTED,
        ParserProfileStatus.SHADOW,
        ParserProfileStatus.CERTIFIED,
        ParserProfileStatus.ACTIVE,
    ):
        validate_profile_transition(state, ParserProfileStatus.RETIRED)


def test_validate_profile_transition_retired_is_terminal():
    with pytest.raises(ParserToolingError):
        validate_profile_transition(ParserProfileStatus.RETIRED, ParserProfileStatus.DRAFT)
    with pytest.raises(ParserToolingError):
        validate_profile_transition(ParserProfileStatus.RETIRED, ParserProfileStatus.ACTIVE)


# --- SignalStore CRUD: parser_samples / parser_profiles ---------------


@pytest.fixture
def store(tmp_path):
    s = SignalStore(tmp_path / "parser_tooling.db")
    s.register_provider(provider_id="acme", display_name="Acme")
    s.register_source(source_id="src-1", provider_id="acme", platform="telegram")
    return s


def test_save_get_list_parser_sample(store):
    created = store.save_parser_sample(
        sample_id="sample-1",
        source_id="src-1",
        provider_id="acme",
        raw_text="BUY AAPL 10 @190",
        message_type="entry",
        extracted_fields={"symbol": "AAPL", "quantity": 10.0},
        disposition_outcome="parsed",
    )
    assert created["id"] == "sample-1"
    assert created["is_corrected"] is False
    assert created["extracted_fields"]["symbol"] == "AAPL"

    fetched = store.get_parser_sample("sample-1")
    assert fetched == created
    assert store.get_parser_sample("does-not-exist") is None

    listed = store.list_parser_samples(source_id="src-1")
    assert [s["id"] for s in listed] == ["sample-1"]
    assert store.list_parser_samples(source_id="src-1", is_corrected=True) == []


def test_correct_parser_sample_preserves_original_classification(store):
    store.save_parser_sample(
        sample_id="sample-1",
        source_id="src-1",
        provider_id="acme",
        raw_text="BUY AAPL 10 @190",
        message_type="entry",
        extracted_fields={"symbol": "AAPL", "quantity": 10.0},
        disposition_outcome="parsed",
    )
    corrected = store.correct_parser_sample(
        "sample-1",
        corrected_message_type="entry",
        corrected_fields={"symbol": "AAPL", "quantity": 15.0},
        correction_note="quantity was actually 15, not 10",
    )
    assert corrected["is_corrected"] is True
    assert corrected["corrected_fields"]["quantity"] == 15.0
    # The ORIGINAL automatic extraction is untouched -- both are
    # inspectable side by side, per this store method's own docstring.
    assert corrected["extracted_fields"]["quantity"] == 10.0
    assert corrected["correction_note"] == "quantity was actually 15, not 10"

    assert store.list_parser_samples(source_id="src-1", is_corrected=True)[0]["id"] == "sample-1"


def test_correct_parser_sample_raises_for_unknown_sample(store):
    with pytest.raises(KeyError):
        store.correct_parser_sample("nope", corrected_message_type="entry", corrected_fields={})


def test_resaving_a_sample_never_discards_an_existing_correction(store):
    store.save_parser_sample(
        sample_id="sample-1",
        source_id="src-1",
        provider_id="acme",
        raw_text="BUY AAPL 10 @190",
        message_type="entry",
        extracted_fields={"symbol": "AAPL"},
        disposition_outcome="parsed",
    )
    store.correct_parser_sample(
        "sample-1", corrected_message_type="entry", corrected_fields={"symbol": "AAPL", "quantity": 15.0}
    )
    # A re-run of batch-classify over the same source (e.g. the operator
    # re-imports the same history) must not wipe out the correction.
    resaved = store.save_parser_sample(
        sample_id="sample-1",
        source_id="src-1",
        provider_id="acme",
        raw_text="BUY AAPL 10 @190",
        message_type="entry",
        extracted_fields={"symbol": "AAPL", "quantity": 10.0},
        disposition_outcome="parsed",
    )
    assert resaved["is_corrected"] is True
    assert resaved["corrected_fields"]["quantity"] == 15.0


def test_register_get_list_parser_profile(store):
    created = store.register_parser_profile(
        parser_id="pp-1",
        provider_id="acme",
        version="v1",
        supported_message_types=["entry", "exit"],
        fallback_model="none",
        prompt_version="p1",
        schema_version="s1",
    )
    assert created["status"] == "draft"
    assert created["supported_message_types"] == ["entry", "exit"]
    assert created["sample_count"] == 0
    assert created["fallback_model"] == "none"

    assert store.get_parser_profile("pp-1") == created
    assert store.get_parser_profile("nope") is None

    listed = store.list_parser_profiles(provider_id="acme")
    assert [p["id"] for p in listed] == ["pp-1"]
    assert store.list_parser_profiles(provider_id="acme", status="draft") == listed
    assert store.list_parser_profiles(provider_id="acme", status="active") == []


def test_register_parser_profile_requires_existing_provider(store):
    with pytest.raises(KeyError):
        store.register_parser_profile(parser_id="pp-1", provider_id="does-not-exist", version="v1")


def test_register_parser_profile_rejects_duplicate_id(store):
    store.register_parser_profile(parser_id="pp-1", provider_id="acme", version="v1")
    with pytest.raises(ParserToolingError):
        store.register_parser_profile(parser_id="pp-1", provider_id="acme", version="v2")


def test_register_parser_profile_rejects_bad_supported_message_type(store):
    with pytest.raises(ParserToolingError):
        store.register_parser_profile(
            parser_id="pp-1", provider_id="acme", version="v1", supported_message_types=["not_real"]
        )


def test_promote_parser_profile_cannot_skip_states(store):
    store.register_parser_profile(parser_id="pp-1", provider_id="acme", version="v1")
    with pytest.raises(ParserToolingError):
        store.promote_parser_profile("pp-1", "active")
    # Still draft -- the rejected attempt changed nothing.
    assert store.get_parser_profile("pp-1")["status"] == "draft"


def test_promote_parser_profile_raises_for_unknown_profile(store):
    with pytest.raises(KeyError):
        store.promote_parser_profile("nope", "tested")


def test_promote_parser_profile_full_lifecycle_reaches_active(store):
    store.register_parser_profile(parser_id="pp-1", provider_id="acme", version="v1")
    store.promote_parser_profile("pp-1", "tested")
    store.promote_parser_profile("pp-1", "shadow")
    store.promote_parser_profile("pp-1", "certified")
    active = store.promote_parser_profile("pp-1", "active")
    assert active["status"] == "active"
    assert active["activated_at"] is not None


def test_promote_parser_profile_only_one_active_per_provider(store):
    store.register_parser_profile(parser_id="pp-1", provider_id="acme", version="v1")
    for target in ("tested", "shadow", "certified", "active"):
        store.promote_parser_profile("pp-1", target)
    assert store.get_parser_profile("pp-1")["status"] == "active"

    store.register_parser_profile(parser_id="pp-2", provider_id="acme", version="v2")
    for target in ("tested", "shadow", "certified", "active"):
        store.promote_parser_profile("pp-2", target)

    # Promoting pp-2 to ACTIVE must atomically retire pp-1 -- never two
    # ACTIVE profiles for the same provider at once.
    pp1 = store.get_parser_profile("pp-1")
    pp2 = store.get_parser_profile("pp-2")
    assert pp1["status"] == "retired"
    assert pp1["retired_at"] is not None
    assert pp2["status"] == "active"

    active_profiles = [p for p in store.list_parser_profiles(provider_id="acme") if p["status"] == "active"]
    assert len(active_profiles) == 1
    assert active_profiles[0]["id"] == "pp-2"


def test_promote_parser_profile_retired_data_stays_inspectable(store):
    store.register_parser_profile(parser_id="pp-1", provider_id="acme", version="v1")
    for target in ("tested", "shadow", "certified", "active"):
        store.promote_parser_profile("pp-1", target)
    store.update_parser_profile_metrics("pp-1", sample_count=42, test_count=10, accuracy_metrics={"symbol": 0.95})

    store.register_parser_profile(parser_id="pp-2", provider_id="acme", version="v2")
    for target in ("tested", "shadow", "certified", "active"):
        store.promote_parser_profile("pp-2", target)

    retired = store.get_parser_profile("pp-1")
    assert retired["status"] == "retired"
    # The metrics this now-retired profile earned while it was active
    # are preserved exactly, not reset or discarded.
    assert retired["sample_count"] == 42
    assert retired["test_count"] == 10
    assert retired["accuracy_metrics"] == {"symbol": 0.95}


def test_get_active_parser_profile_for_source(store):
    assert store.get_active_parser_profile_for_source("src-1") is None

    store.register_parser_profile(parser_id="pp-1", provider_id="acme", version="v1")
    store.register_source(
        source_id="src-1", provider_id="acme", platform="telegram", parser_profile="pp-1"
    )
    # Assigned but not yet ACTIVE -- still None, an assignment alone
    # doesn't make a profile live.
    assert store.get_active_parser_profile_for_source("src-1") is None

    for target in ("tested", "shadow", "certified", "active"):
        store.promote_parser_profile("pp-1", target)
    resolved = store.get_active_parser_profile_for_source("src-1")
    assert resolved is not None
    assert resolved["id"] == "pp-1"

    assert store.get_active_parser_profile_for_source("does-not-exist") is None


def test_update_parser_profile_metrics_only_touches_given_fields(store):
    store.register_parser_profile(parser_id="pp-1", provider_id="acme", version="v1")
    store.update_parser_profile_metrics("pp-1", sample_count=5)
    profile = store.get_parser_profile("pp-1")
    assert profile["sample_count"] == 5
    assert profile["test_count"] == 0
    assert profile["accuracy_metrics"] == {}

    store.update_parser_profile_metrics("pp-1", test_count=3)
    profile = store.get_parser_profile("pp-1")
    assert profile["sample_count"] == 5  # unchanged by the second call
    assert profile["test_count"] == 3


def test_update_parser_profile_metrics_raises_for_unknown_profile(store):
    with pytest.raises(KeyError):
        store.update_parser_profile_metrics("nope", sample_count=1)


# --- REST routes: /parser-tooling/... ----------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_config, "OWNER_PASSWORD", "test-owner-password")
    monkeypatch.setattr(app_config, "SESSION_SECRET", "test-session-secret")

    test_store = SignalStore(tmp_path / "parser_tooling_api.db")
    test_store.register_provider(provider_id="acme", display_name="Acme")
    test_store.register_source(source_id="src-1", provider_id="acme", platform="telegram")
    monkeypatch.setattr(main_module, "store", test_store)
    monkeypatch.setattr(main_module.engine, "store", test_store)

    test_client = TestClient(main_module.app)
    login = test_client.post("/auth/login", json={"password": "test-owner-password"})
    assert login.status_code == 200
    test_client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    return test_client


def test_unauthenticated_parser_tooling_requests_are_rejected():
    with TestClient(main_module.app) as anon_client:
        resp = anon_client.post("/parser-tooling/classify-samples", json={"texts": ["BUY AAPL 5 @190"]})
        assert resp.status_code in (401, 403, 503)


def test_classify_samples_route_never_persists(client):
    with client:
        resp = client.post(
            "/parser-tooling/classify-samples",
            json={"texts": ["BUY AAPL 10 @190 SL 185 TP 200", "move SL to 185 on AAPL"]},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["samples"]) == 2
        assert body["samples"][0]["message_type"] == "entry"
        assert body["samples"][0]["extracted_fields"]["symbol"] == "AAPL"
        assert body["samples"][1]["message_type"] == "stop_update"

        # Nothing was saved -- the source's own sample listing is empty.
        listed = client.get("/parser-tooling/sources/src-1/samples")
        assert listed.json()["samples"] == []


def test_persist_and_list_and_correct_samples(client):
    with client:
        persisted = client.post(
            "/parser-tooling/sources/src-1/samples",
            json={"texts": ["BUY AAPL 10 @190 SL 185 TP 200"]},
        )
        assert persisted.status_code == 200
        samples = persisted.json()["samples"]
        assert len(samples) == 1
        sample_id = samples[0]["id"]
        assert samples[0]["message_type"] == "entry"

        listed = client.get("/parser-tooling/sources/src-1/samples")
        assert [s["id"] for s in listed.json()["samples"]] == [sample_id]

        corrected = client.post(
            f"/parser-tooling/samples/{sample_id}/correct",
            json={
                "corrected_message_type": "entry",
                "corrected_fields": {"symbol": "AAPL", "quantity": 12.0},
                "correction_note": "quantity misread",
            },
        )
        assert corrected.status_code == 200
        assert corrected.json()["is_corrected"] is True
        assert corrected.json()["corrected_fields"]["quantity"] == 12.0

        only_corrected = client.get("/parser-tooling/sources/src-1/samples", params={"is_corrected": True})
        assert [s["id"] for s in only_corrected.json()["samples"]] == [sample_id]


def test_persist_samples_requires_existing_source(client):
    with client:
        resp = client.post("/parser-tooling/sources/does-not-exist/samples", json={"texts": ["BUY AAPL 5 @190"]})
        assert resp.status_code == 404


def test_correct_sample_requires_existing_sample(client):
    with client:
        resp = client.post(
            "/parser-tooling/samples/does-not-exist/correct",
            json={"corrected_message_type": "entry", "corrected_fields": {}},
        )
        assert resp.status_code == 404


def test_create_list_and_promote_parser_profile(client):
    with client:
        created = client.post(
            "/parser-tooling/providers/acme/parser-profiles",
            json={"version": "v1", "supported_message_types": ["entry", "exit"]},
        )
        assert created.status_code == 200
        profile = created.json()
        assert profile["status"] == "draft"
        parser_id = profile["id"]

        listed = client.get("/parser-tooling/providers/acme/parser-profiles")
        assert [p["id"] for p in listed.json()["parser_profiles"]] == [parser_id]

        # Cannot skip straight to active.
        skip = client.post(f"/parser-tooling/parser-profiles/{parser_id}/promote", json={"target_status": "active"})
        assert skip.status_code == 400

        for target in ("tested", "shadow", "certified", "active"):
            resp = client.post(f"/parser-tooling/parser-profiles/{parser_id}/promote", json={"target_status": target})
            assert resp.status_code == 200
        assert resp.json()["status"] == "active"


def test_create_parser_profile_requires_existing_provider(client):
    with client:
        resp = client.post(
            "/parser-tooling/providers/does-not-exist/parser-profiles", json={"version": "v1"}
        )
        assert resp.status_code == 404


def test_promote_parser_profile_route_unknown_profile_is_404(client):
    with client:
        resp = client.post("/parser-tooling/parser-profiles/nope/promote", json={"target_status": "tested"})
        assert resp.status_code == 404


def test_only_one_active_parser_profile_per_provider_via_rest(client):
    with client:
        def create_and_activate(version):
            created = client.post(
                "/parser-tooling/providers/acme/parser-profiles", json={"version": version}
            ).json()
            parser_id = created["id"]
            for target in ("tested", "shadow", "certified", "active"):
                client.post(f"/parser-tooling/parser-profiles/{parser_id}/promote", json={"target_status": target})
            return parser_id

        first_id = create_and_activate("v1")
        second_id = create_and_activate("v2")

        profiles = client.get("/parser-tooling/providers/acme/parser-profiles").json()["parser_profiles"]
        by_id = {p["id"]: p for p in profiles}
        assert by_id[first_id]["status"] == "retired"
        assert by_id[second_id]["status"] == "active"
