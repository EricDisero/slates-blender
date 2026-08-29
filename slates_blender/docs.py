# SPDX-FileCopyrightText: 2026 Blender Authors
# SPDX-FileCopyrightText: 2026 Slates
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Blender's own documentation, on disk, greppable.

**This is the anti-hallucination layer and it is the highest-value part of the
add-on.** An agent writing `bpy` from memory invents operator names, invents
enum values, and gets keyword arguments subtly wrong in ways that raise no
error until the scene is silently malformed. An agent that can read the real
signature does not. The corpus under ``data/`` is Blender 5.1's Python API
reference and user manual as plain RST — 4,200-odd files — which is why the
manifest pins ``blender_version_min`` to 5.1: docs from a different series are
worse than none, because wrong-but-plausible reads as authoritative.

``lookup`` resolves a dotted identifier the way an agent asks for one
(``bpy.ops.mesh``, ``bpy.types.Camera.lens``, ``bpy.ops.*``); ``search`` is the
full-text fallback for when it does not know the name yet. Structure adapted
from ``blender_mcp``'s doc tools.
"""

from __future__ import annotations

import os
from typing import Any

from .rst import doc_search
from .rst.parse_docs import (
    data_dir,
    doctree_for_path,
    list_doctree_definitions,
)

_EXT = ".rst"

# Past this, returning the file body costs more context than it is worth, so we
# return the definition list and let the agent ask for one member instead.
_INLINE_LIMIT_BYTES = 32 * 1024


def _api_root() -> str:
    return os.path.realpath(os.path.join(data_dir(), "api"))


def _resolve(stem: str) -> str | None:
    """Path for a dotted identifier, refusing anything outside the corpus."""
    root = _api_root()
    candidate = os.path.realpath(os.path.join(root, stem + _EXT))
    if candidate.startswith(root + os.sep) and os.path.isfile(candidate):
        return candidate
    return None


def _children(identifier: str) -> list[str]:
    """Immediate submodules of *identifier* — one dot deeper, no further."""
    root = _api_root()
    prefix = identifier + "."
    depth = prefix.count(".")
    return sorted(
        name[: -len(_EXT)]
        for name in os.listdir(root)
        if name.endswith(_EXT)
        and name.startswith(prefix)
        and name[: -len(_EXT)].count(".") == depth
    )


def lookup(identifier: str) -> dict[str, Any]:
    """Look up a dotted Blender API identifier.

    ``"*"`` lists top-level modules; ``"bpy.ops.*"`` lists that namespace's
    submodules; an exact match returns the file; a miss walks the dotted name
    backwards looking for the member inside its parent's page.
    """
    root = _api_root()

    if identifier == "*" or identifier.endswith(".*"):
        if identifier == "*":
            names = sorted(
                {
                    name[: -len(_EXT)].split(".", 1)[0]
                    for name in os.listdir(root)
                    if name.endswith(_EXT)
                }
            )
        else:
            names = _children(identifier[:-2])
        return {"kind": "namespace", "found": True, "identifier": identifier, "submodules": names}

    path = _resolve(identifier)
    if path is not None:
        with open(path, encoding="utf-8", errors="replace") as handle:
            content = handle.read()
        if len(content) > _INLINE_LIMIT_BYTES:
            definitions = list_doctree_definitions(doctree_for_path(path))
            return {
                "kind": "index",
                "found": True,
                "identifier": identifier,
                "note": "Page is large; query a member directly for its body.",
                "definitions": definitions,
            }
        return {"kind": "exact", "found": True, "identifier": identifier, "content": content}

    submodules = _children(identifier)
    if submodules:
        return {
            "kind": "namespace",
            "found": True,
            "identifier": identifier,
            "submodules": submodules,
        }

    # `bpy.types.Camera.lens` has no page of its own — it lives inside
    # `bpy.types.Camera`. Strip trailing components until a page resolves.
    parts = identifier.split(".")
    for strip in range(1, len(parts)):
        parent = ".".join(parts[:-strip])
        parent_path = _resolve(parent)
        if parent_path is None:
            continue
        definitions = list_doctree_definitions(doctree_for_path(parent_path))
        member = ".".join(parts[-strip:])
        matches = [d for d in definitions if d == member or d.endswith("." + member)]
        return {
            "kind": "member",
            "found": bool(matches),
            "identifier": identifier,
            "definedIn": parent,
            "matches": matches,
            "siblings": definitions if not matches else [],
        }

    return {
        "kind": "missing",
        "found": False,
        "identifier": identifier,
        "hint": 'Try `search` instead, or list a namespace with e.g. "bpy.ops.*".',
    }


def search(query: str, scope: str = "api", max_results: int = 8, context: int = 1) -> dict[str, Any]:
    """Full-text search over the bundled docs. *scope* is ``api`` or ``manual``."""
    if scope not in ("api", "manual"):
        raise ValueError('scope must be "api" or "manual"')
    return doc_search.search(
        query=query,
        scope=scope,
        max_results=max_results,
        context=context,
    )
