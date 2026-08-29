#!/usr/bin/env python
# SPDX-FileCopyrightText: 2026 Slates
# SPDX-License-Identifier: GPL-3.0-or-later

"""Package the add-on as an installable Blender extension zip.

Blender expects `blender_manifest.toml` and `__init__.py` at the **root** of the
zip, so the contents of `slates_blender/` are hoisted one level rather than
nested. That is the single non-obvious thing this script does.

Usage:  python scripts/build.py [--out dist]
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = os.path.join(ROOT, "slates_blender")

# Mirrors `paths_exclude_pattern` in the manifest. Kept as a literal set here
# because the manifest's globs are Blender's to interpret, not ours.
EXCLUDE_DIRS = {"__pycache__", ".git", ".mypy_cache", ".ruff_cache"}
EXCLUDE_NAMES = {".DS_Store", "AGENTS.md"}


def version_from_manifest(manifest_path: str) -> str:
    with open(manifest_path, encoding="utf-8") as handle:
        text = handle.read()
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
    if match is None:
        raise SystemExit("blender_manifest.toml has no version field")
    return match.group(1)


def iter_files(base: str):
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDE_DIRS)
        for name in sorted(filenames):
            if name in EXCLUDE_NAMES or name.endswith(".pyc"):
                continue
            yield os.path.join(dirpath, name)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="dist", help="output directory (default: dist)")
    args = parser.parse_args()

    manifest = os.path.join(ROOT, "blender_manifest.toml")
    if not os.path.isfile(manifest):
        raise SystemExit(f"missing {manifest}")
    if not os.path.isfile(os.path.join(PACKAGE, "__init__.py")):
        raise SystemExit(f"missing {PACKAGE}/__init__.py")

    version = version_from_manifest(manifest)
    out_dir = os.path.join(ROOT, args.out)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"slates_blender-{version}.zip")

    count = 0
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        zf.write(manifest, "blender_manifest.toml")
        count += 1
        for name in ("LICENSE", "NOTICE.md"):
            path = os.path.join(ROOT, name)
            if os.path.isfile(path):
                zf.write(path, name)
                count += 1

        wheels = os.path.join(ROOT, "wheels")
        if os.path.isdir(wheels):
            for path in iter_files(wheels):
                zf.write(path, os.path.join("wheels", os.path.relpath(path, wheels)).replace("\\", "/"))
                count += 1

        # Hoisted: `slates_blender/foo.py` lands at `foo.py`.
        for path in iter_files(PACKAGE):
            zf.write(path, os.path.relpath(path, PACKAGE).replace("\\", "/"))
            count += 1

    size_mb = os.path.getsize(out_path) / (1024 * 1024)
    print(f"{out_path}\n{count} files, {size_mb:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
