# Fixture Conventions

## The `store` fixture: fresh `tmp_path` `SignalStore` per test

The default, preferred pattern across most test files is a small local
fixture that constructs a brand-new `SignalStore` against a `tmp_path`
scratch file, giving every test function its own isolated SQLite database:

```python
# tests/test_engine.py
@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")
```

Any component that takes a `store=` constructor argument
(`SignalCopierEngine`, `PositionLifecycleManager`, `CapitalAllocator`) is
then built directly against this fresh store in the test body, with no
dependency on anything `app.main` constructed at import time:

```python
async def test_signal_routes_and_sizes_to_multiple_accounts(store):
    paper = PaperBroker()
    ...
```

This is the safest, simplest, and most common isolation pattern in this
suite — prefer it for any new unit/integration test that doesn't need a
real running HTTP server. It needs no `monkeypatch`, no cleanup, and
`pytest`'s own `tmp_path` handles teardown.

## The sharp edge: `app.main`'s module-level singletons are NOT isolated by patching `store` alone

`app/main.py` constructs several objects **once, at import time**, as
module-level globals:

```python
store = SignalStore(config.DATABASE_PATH)
brokers = { ... }
writer_lease_guard = WriterLeaseGuard(store, ...)
lifecycle_manager = PositionLifecycleManager(brokers=brokers, store=store)
engine = SignalCopierEngine(...)
```

Because `app.main` is imported once per test process (not once per
test), **these are the same Python objects, shared across every test in
the suite** that goes through `TestClient(app)` or a real HTTP call,
unless a fixture explicitly rewires them. A test that only does:

```python
monkeypatch.setattr(main_module, "store", store)
```

(the `_authed_client` helper in `tests/test_tr01_tr04_trading_screens.py`
is a real example of exactly this) has **only** redirected
`main_module.store` — every HTTP route that reads `main_module.store`
directly now sees the fresh test store. But `main_module.engine.store`,
`main_module.lifecycle_manager`, `main_module.engine.lifecycle_manager`,
and `main_module.writer_lease_guard` are **untouched** — they still point
at whatever store/lifecycle state a previous test in the same process
left behind, or at a live SQLite file at `config.DATABASE_PATH` if that's
what `app.main` was imported against.

This is fine for a test that only exercises read endpoints backed
directly by `store` (as `_authed_client`'s own callers do — `GET
/signals`, `GET /positions` reads). It is **not** fine, and will produce
confusing cross-test contamination or false passes/failures, for any test
that exercises a code path going through `engine` or `lifecycle_manager`
— entry/exit submission, lifecycle state transitions, capital
reservation — while only patching `main_module.store`.

**The full, correct rewiring**, when a test genuinely needs to isolate
the whole `app.main` object graph (not just reads through `store`), is
the pattern `tests/test_tr03_mae_mfe_chart.py`'s
`live_server_inprocess` fixture uses:

```python
fresh_store = SignalStore(str(tmp_path / "some_test.db"))
fresh_lifecycle_manager = PositionLifecycleManager(brokers=main_module.brokers, store=fresh_store)
fresh_lifecycle_manager.capital_allocator = main_module.engine.capital_allocator

monkeypatch.setattr(main_module, "store", fresh_store)
monkeypatch.setattr(main_module, "lifecycle_manager", fresh_lifecycle_manager)
monkeypatch.setattr(main_module.engine, "store", fresh_store)
monkeypatch.setattr(main_module.engine, "lifecycle_manager", fresh_lifecycle_manager)
```

Every one of those four `setattr` calls is load-bearing on its own —
missing any one of them leaves that specific attribute pointing at the
shared, cross-test singleton. When writing a new test against
`TestClient(app)`/a real server that touches more than a pure read
through `store`, check which of `store`, `engine.store`,
`lifecycle_manager`, `engine.lifecycle_manager`, and
`writer_lease_guard` your code path actually reaches, and patch all of
them — not just `store`.

## A real bug this exact sharp edge caused: `WriterLeaseGuard` thread-safety

This is not a hypothetical warning — it is how a genuine bug was found
during this project's own P0-6 work. `app/writer_lease.py::WriterLeaseGuard`
is constructed once, at `app.main` import time
(`writer_lease_guard = WriterLeaseGuard(store, ...)`), and this codebase's
test suite opens *many* overlapping `TestClient` context managers across
different test files in the same process — each one re-entering
`lifespan()` (and therefore `WriterLeaseGuard.acquire()`) on its own
worker thread, against that **one process-wide singleton guard
instance**. That is not how a real deployment runs (one process, one
`lifespan()` invocation, no concurrent re-entry) — it is purely an
artifact of how this test suite exercises the one shared `app.main`
singleton across many tests.

Without a lock, two threads could both observe `WriterLeaseGuard._token
is None` (or both observe the same stale token), both perform a real
acquisition/renewal against the store (bumping the DB's fencing token
twice), and then race to write `self._token` — whichever write landed
last won, and it could be the stale, lower value, leaving that process
fenced out by its own second acquisition on the very next
`require_active()` call — not by any genuine second writer. This was
fixed by adding `self._lock = threading.Lock()` (a plain
`threading.Lock`, deliberately **not** `asyncio.Lock`, which only
excludes coroutines on one event loop, not OS threads — FastAPI's
`lifespan()` runs on anyio's thread-based portal) around the
check-then-act read of `_token` plus the acquisition/renewal call plus
the write of `_token`. See `app/writer_lease.py::WriterLeaseGuard.__init__`'s
own comment on `self._lock` for the full account, and
`tests/test_writer_lease_fencing.py::test_concurrent_acquire_from_multiple_threads_is_race_free`
for the regression test (8 real OS threads, a `threading.Barrier` to
maximize simultaneity, and an artificially widened race window between
the guard's `_token is None` check and the store call actually landing).

**The lesson to generalize**: any module-level singleton in `app/main.py`
that this test suite exercises through concurrent/overlapping
`TestClient`/server instances needs to be safe against re-entry from
multiple OS threads against the *same* object — not just multiple
coroutines on one event loop — precisely because the test suite's own
sharing of that singleton across many tests creates exactly that
concurrency, even though production never does. When adding a new
module-level singleton with mutable state to `app/main.py`, ask whether
this suite's pattern of re-entering `lifespan()`/`TestClient` many times
against the one shared object could race it, not just whether a single
real deployment could.
