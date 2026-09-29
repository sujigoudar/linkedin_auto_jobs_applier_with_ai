#!/usr/bin/env python3
"""Cross-check docs/manifest.yaml against the real docs/ tree.

Standalone, no new dependencies (uses PyYAML, which this repo already
depends on transitively via other tooling; if it is not importable this
script falls back to a tiny hand-rolled parser for the one shape this
manifest actually uses -- see `_load_manifest_paths_fallback`).

What "missing" and "dangling" mean here:
  - missing:  a real file exists under docs/ (or is one of the other
    top-level docs this manifest also indexes, e.g. CHANGELOG.md,
    PROJECT_STATUS.yaml) but no category in docs/manifest.yaml lists it
    in its `paths` (or `path`).
  - dangling: a category in docs/manifest.yaml lists a path in `paths`
    (or `path`) that does not exist on disk.

This is the exact cross-check performed by hand earlier in this
initiative when a manifest gap was found and fixed; this script makes
that check repeatable via `make docs-check`.

Exit code 0 = no gaps. Exit code 1 = missing and/or dangling entries
found (listed on stdout).
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent  # signal-copier/
MANIFEST_PATH = REPO_ROOT / "docs" / "manifest.yaml"
DOCS_DIR = REPO_ROOT / "docs"

# Top-level, non-docs/ files the manifest also indexes (e.g. CHANGELOG.md,
# PROJECT_STATUS.yaml live at the repo root, not under docs/). These are
# included in the "real files" side of the comparison whenever the
# manifest references them, but are not walked automatically since they
# are not under DOCS_DIR.
KNOWN_ROOT_LEVEL_DOCS = {"CHANGELOG.md", "PROJECT_STATUS.yaml"}


def _load_manifest_paths() -> set[str]:
    """Return every path string the manifest's `documents` mapping lists.

    Tries PyYAML first (accurate for arbitrary YAML); falls back to a
    small line-oriented parser scoped to this manifest's actual shape
    (a `path: <str>` and/or `paths: [ - <str> ... ]` per category) if
    PyYAML is not installed, so this script has no hard new dependency.
    """
    text = MANIFEST_PATH.read_text()
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(text)
        documents = data.get("documents", {}) or {}
        paths: set[str] = set()
        for _category, entry in documents.items():
            if not isinstance(entry, dict):
                continue
            single = entry.get("path")
            if single:
                paths.add(single)
            for p in entry.get("paths", []) or []:
                paths.add(p)
        return paths
    except ImportError:
        return _load_manifest_paths_fallback(text)


def _load_manifest_paths_fallback(text: str) -> set[str]:
    """Minimal parser for this manifest's specific `path`/`paths` shape.

    Only used when PyYAML is unavailable. Walks lines looking for
    `path: <value>` and list items under a `paths:` key. Deliberately
    narrow -- not a general YAML parser.
    """
    paths: set[str] = set()
    in_paths_list = False
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("#") or not stripped:
            continue
        if stripped.startswith("path:"):
            value = stripped.split(":", 1)[1].strip()
            if value:
                paths.add(value)
            in_paths_list = False
            continue
        if stripped.startswith("paths:"):
            in_paths_list = True
            continue
        if in_paths_list and stripped.startswith("- "):
            paths.add(stripped[2:].strip())
            continue
        if in_paths_list and not stripped.startswith("- "):
            in_paths_list = False
    return paths


def _real_doc_files() -> set[str]:
    """Every real file under docs/, as repo-root-relative POSIX paths,
    plus the known root-level docs this manifest also indexes."""
    manifest_rel = str(MANIFEST_PATH.relative_to(REPO_ROOT)).replace("\\", "/")
    real: set[str] = set()
    for p in DOCS_DIR.rglob("*"):
        if p.is_file():
            rel = str(p.relative_to(REPO_ROOT)).replace("\\", "/")
            if rel == manifest_rel:
                continue  # the manifest doesn't index itself
            real.add(rel)
    for name in KNOWN_ROOT_LEVEL_DOCS:
        if (REPO_ROOT / name).is_file():
            real.add(name)
    return real


def main() -> int:
    if not MANIFEST_PATH.exists():
        print(f"ERROR: manifest not found at {MANIFEST_PATH}")
        return 1

    manifest_paths = _load_manifest_paths()
    real_files = _real_doc_files()

    # Only compare manifest paths that are within docs/ or in the known
    # root-level set -- the manifest may reference .agent/context-map.yaml
    # or CLAUDE.md in prose (read_when/update_when text), but those aren't
    # `path`/`paths` entries so they won't appear here anyway.
    missing = sorted(
        f for f in real_files
        if f not in manifest_paths
    )
    dangling = sorted(
        p for p in manifest_paths
        if not (REPO_ROOT / p).is_file()
    )

    if not missing and not dangling:
        print("docs/manifest.yaml: OK -- every real docs/ file is indexed, "
              "no dangling entries.")
        return 0

    if missing:
        print(f"MISSING from docs/manifest.yaml ({len(missing)}): real file(s) "
              "under docs/ (or a known root-level doc) not listed in any "
              "category's path/paths:")
        for f in missing:
            print(f"  - {f}")

    if dangling:
        print(f"DANGLING in docs/manifest.yaml ({len(dangling)}): path/paths "
              "entries that do not exist on disk:")
        for p in dangling:
            print(f"  - {p}")

    return 1


if __name__ == "__main__":
    sys.exit(main())
