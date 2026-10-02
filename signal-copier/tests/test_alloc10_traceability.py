"""ALLOC-10: the traceability registry may not rot. Every scenario row has a
valid status, ids are unique, and every referenced test function exists."""
import pathlib
import re

import yaml

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_REGISTRY = _ROOT / "docs" / "testing" / "ALLOCATION_TRACEABILITY.yaml"
_STATUSES = {"covered", "partial", "unit-only", "gap"}
_IMPL = {"implemented", "partial", "missing"}


def _rows():
    return yaml.safe_load(_REGISTRY.read_text())


def test_registry_rows_are_well_formed():
    rows = _rows()
    assert len(rows) >= 150
    ids = [r["id"] for r in rows]
    assert len(ids) == len(set(ids))
    for r in rows:
        assert r["status"] in _STATUSES, r["id"]
        assert r["impl_status"] in _IMPL, r["id"]
        if r["status"] == "covered":
            assert r["tests"], f"{r['id']} is covered but names no test"


def test_every_referenced_test_exists():
    missing = []
    for r in _rows():
        for ref in r.get("tests") or []:
            file_part, _, name = ref.partition("::")
            name = name.split("::")[-1].split("[")[0]
            path = _ROOT / file_part
            if not path.exists() or not re.search(rf"def {re.escape(name)}\b", path.read_text()):
                missing.append((r["id"], ref))
    assert not missing, missing
