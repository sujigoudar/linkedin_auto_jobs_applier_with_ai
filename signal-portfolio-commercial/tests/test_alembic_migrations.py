"""Sanity checks on the Alembic migration scripts themselves -- these
don't run a live migration (that needs `alembic upgrade head` against a
real database, verified manually/in CI via `alembic -x ...` or a
deployment step, not this test suite's own disposable-per-test-function
schema), but they do catch the two mistakes that have already happened
once each while writing these: a docstring placed after `from __future__
import annotations` (a SyntaxError) and a revision whose upgrade/
downgrade path doesn't at least import and call correctly."""
import importlib.util
from pathlib import Path

_VERSIONS_DIR = Path(__file__).resolve().parent.parent / "alembic" / "versions"


def _load_migration_modules():
    modules = {}
    for path in sorted(_VERSIONS_DIR.glob("*.py")):
        spec = importlib.util.spec_from_file_location(path.stem, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        modules[path.stem] = module
    return modules


def test_every_migration_script_imports_without_error():
    modules = _load_migration_modules()
    assert len(modules) >= 2


def test_every_migration_has_a_revision_id_and_upgrade_downgrade_functions():
    for name, module in _load_migration_modules().items():
        assert isinstance(module.revision, str) and module.revision, name
        assert callable(module.upgrade), name
        assert callable(module.downgrade), name


def test_the_revision_chain_has_exactly_one_root_and_one_head():
    modules = _load_migration_modules()
    revisions = {module.revision: module.down_revision for module in modules.values()}

    roots = [rev for rev, down in revisions.items() if down is None]
    heads = [rev for rev in revisions if rev not in revisions.values()]

    assert len(roots) == 1, f"expected exactly one root revision, got {roots}"
    assert len(heads) == 1, f"expected exactly one head revision, got {heads}"


def test_the_rls_and_append_only_migration_refuses_to_downgrade():
    modules = _load_migration_modules()
    rls_module = next(m for m in modules.values() if "row_level_security" in m.__name__)

    try:
        rls_module.downgrade()
        raise AssertionError("expected downgrade() to refuse rather than silently weaken isolation")
    except NotImplementedError:
        pass
