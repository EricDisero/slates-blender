# Third-Party Notices

## Blender MCP

The execution bridge (`slates_blender/bridge/`), the RST documentation readers
(`slates_blender/rst/`) and the bundled documentation corpus
(`slates_blender/data/`) are adapted from Blender Lab's `blender_mcp` project:

- Source: https://projects.blender.org/lab/blender_mcp
- Copyright: 2026 Blender Authors
- License: GNU General Public License v3.0 or later

Obtained via the `blender_mcp-1.0.0` wheel distributed inside Higgsfield Inc.'s
GPL-3.0-or-later `higgsfield_blender` add-on (v1.5.47), which vendored upstream
revision `98b0e49d98321d321c7e631389200f513f765d59`. Upstream SPDX headers are
retained in every adapted file. Changes made by Slates:

- `mcp_bridge/` renamed to `bridge/`, module names shortened
  (`mcp_to_blender_server` → `server`, `capture_output` → `capture`,
  `weak_sandbox` → `sandbox`, `deferred_tool` → `deferred`), imports rewritten
  to match. Protocol, threading model and semantics are unchanged.
- `tools_helpers/rst_doc_search.py` and `rst_parse_docs.py` moved to
  `rst/doc_search.py` and `rst/parse_docs.py`; the only edit is the import
  rewrite from `blmcp.tools_helpers.rst_parse_docs` to `.parse_docs`.
- `data/prompts.yml` retained as `data/blender-instructions.yml`.

`slates_blender/previs.py`, `scene.py`, `docs.py` and `__init__.py` are original
work by Slates, also GPL-3.0-or-later.

## Blender documentation

`slates_blender/data/api/` and `slates_blender/data/manual/` are the Blender 5.1
Python API reference and user manual, redistributed as plain reStructuredText.

- Copyright: Blender Authors and the Blender Documentation contributors
- https://docs.blender.org

## docutils

`wheels/docutils-0.23-py3-none-any.whl` is bundled unmodified and is required by
the RST parser. Docutils is released into the public domain, with portions under
the BSD 2-Clause and Python licenses; see the wheel's own metadata.

---

## Why this add-on is GPL

Any Blender add-on links `bpy` and is therefore a derivative work of Blender,
which is GPL. The licence here is not a choice forced on us by the code we
adapted — it is the licence every Blender add-on carries.

The Slates API, the Slates MCP server and the Slates desktop application are
**not** derivative works of this add-on. They communicate with it over a
localhost socket using a documented JSON protocol. The GPL propagates through
linkage, not across a process boundary.
