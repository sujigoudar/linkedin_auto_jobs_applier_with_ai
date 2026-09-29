#!/usr/bin/env python3
"""Check docs/manifest.yaml for completeness against the real docs/ tree.

This app's manifest schema (verified against the real file, NOT assumed
to match signal-copier's `documents`/`paths` shape) is:

    version: <int>
    repo: <str>
    categories:
      <category_name>:
        owner: <str>
        path: <str>                # informational; not used for the walk
        docs: [<repo-relative path>, ...]
        read_when: [...]
    root_docs:
      - path: <repo-relative path>
        read_when: [...]

Two directions of drift are checked, both against the on-disk docs/ tree
plus the fixed root-level files this manifest tracks:

  1. LISTED BUT MISSING: a path in some category's `docs` list, or in
     `root_docs`, that does not exist on disk.
  2. PRESENT BUT UNLISTED: a real *.md file under docs/ (recursively)
     that is not listed in any category's `docs` list.

Exit code is 0 iff there are zero gaps in either direction; non-zero
otherwise. Intended to be run from signal-portfolio-commercial/ (e.g.
via `make docs-check`), but resolves paths relative to this script's
location so it also works if invoked from elsewhere.

PyYAML is not a declared dependency of this app's requirements.txt (it
happens to be present transitively in most dev environments, but this
script has no hard new dependency on it): if `import yaml` fails, a
small line-oriented fallback parser handles this manifest's specific
shape (`docs:` / `path:` list items under `categories.*` and
`root_docs`) -- deliberately narrow, not a general YAML parser.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = REPO_ROOT / "docs" / "manifest.yaml"
DOCS_DIR = REPO_ROOT / "docs"


def load_manifest() -> dict | None:
    """Returns the parsed manifest via PyYAML, or None if PyYAML isn't
    importable (caller then falls back to listed_paths_fallback)."""
    try:
        import yaml  # type: ignore
    except ImportError:
        return None
    with open(MANIFEST_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def listed_paths_fallback(text: str) -> set[str]:
    """Minimal parser for this manifest's `docs:`/`path:` list shape,
    used only when PyYAML is unavailable. Walks lines looking for
    `- <path>` items following a `docs:` key (inside `categories.*`),
    and `- path: <value>` list-item lines (inside `root_docs` entries).

    Deliberately does NOT match a bare `path: <value>` line -- that key
    also appears, unrelated, as each category's own directory path
    (e.g. `path: docs/architecture/`), which is informational only and
    must not be treated as a tracked file."""
    paths: set[str] = set()
    in_docs_list = False
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped == "docs:":
            in_docs_list = True
            continue
        if in_docs_list and stripped.startswith("- "):
            paths.add(stripped[2:].strip())
            continue
        if in_docs_list and not stripped.startswith("- "):
            in_docs_list = False
        if stripped.startswith("- path:"):
            value = stripped.split(":", 1)[1].strip()
            if value:
                paths.add(value)
    return paths


def listed_paths(manifest: dict) -> set[str]:
    """Every path this manifest claims exists, across categories + root_docs."""
    paths: set[str] = set()

    for _cat_name, cat in (manifest.get("categories") or {}).items():
        for doc in cat.get("docs") or []:
            paths.add(doc)

    for entry in manifest.get("root_docs") or []:
        # root_docs entries are {path, read_when}; be tolerant of a bare
        # string too, in case the manifest is hand-edited that way.
        if isinstance(entry, dict):
            path = entry.get("path")
        else:
            path = entry
        if path:
            paths.add(path)

    return paths


def real_docs_md_files() -> set[str]:
    """Every real *.md file under docs/, as repo-relative POSIX paths."""
    found: set[str] = set()
    if not DOCS_DIR.is_dir():
        return found
    for md_path in DOCS_DIR.rglob("*.md"):
        rel = md_path.relative_to(REPO_ROOT).as_posix()
        found.add(rel)
    return found


def main() -> int:
    if not MANIFEST_PATH.is_file():
        print(f"FAIL: manifest not found at {MANIFEST_PATH}")
        return 1

    manifest = load_manifest()
    if manifest is not None:
        listed = listed_paths(manifest)
    else:
        text = MANIFEST_PATH.read_text(encoding="utf-8")
        listed = listed_paths_fallback(text)
    on_disk_docs_md = real_docs_md_files()

    # 1) Listed but missing (checked against ALL listed paths, including
    # root_docs entries that live outside docs/, e.g. CLAUDE.md).
    missing = sorted(p for p in listed if not (REPO_ROOT / p).is_file())

    # 2) Present but unlisted -- only meaningful for docs/ *.md files,
    # since root_docs intentionally covers a handful of specific
    # non-docs/ files (CLAUDE.md, PROJECT_STATUS.yaml, etc.) rather than
    # an exhaustive repo-wide file walk.
    docs_prefixed_listed = {p for p in listed if p.startswith("docs/")}
    unlisted = sorted(on_disk_docs_md - docs_prefixed_listed)

    if not missing and not unlisted:
        print(f"OK: docs/manifest.yaml is complete ({len(listed)} paths tracked).")
        return 0

    if missing:
        print(f"MISSING ({len(missing)}): listed in manifest.yaml but not found on disk:")
        for p in missing:
            print(f"  - {p}")

    if unlisted:
        print(f"UNLISTED ({len(unlisted)}): real docs/*.md files not in any category's docs list:")
        for p in unlisted:
            print(f"  - {p}")

    return 1


if __name__ == "__main__":
    sys.exit(main())
