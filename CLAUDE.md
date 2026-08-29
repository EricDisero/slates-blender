# slates-blender — Claude code notes

Blender add-on. Opens a localhost execution bridge, ships Blender's documentation, renders the blocking pass. Everything else lives in `slates-mcp`.

## Layout

```
slates-blender/
├── blender_manifest.toml     ← extension manifest (id, version, wheels, version floor)
├── wheels/docutils-*.whl     ← required by the RST parser
├── scripts/build.py          ← zips the extension (hoists slates_blender/* to root)
├── tests/serve_stub.py       ← runs the real bridge against a stubbed bpy
└── slates_blender/
    ├── __init__.py           ← registration, panel, operators, port binding
    ├── previs.py             ← THE deliverable: viewport render → mp4
    ├── scene.py              ← scene/camera/collection summary
    ├── docs.py               ← API lookup + full-text search over data/
    ├── bridge/               ← VENDORED (Blender Authors, GPL-3.0)
    │   ├── server.py         ← non-blocking TCP, NUL-delimited JSON
    │   ├── execute.py        ← bpy.app.timers poll callback
    │   ├── capture.py  sandbox.py  deferred.py
    ├── rst/                  ← VENDORED RST readers
    └── data/                 ← Blender 5.1 API reference + manual as .rst (25 MB)
```

## Hard rules

- **Never edit `bridge/` or `rst/` beyond import rewrites.** They are vendored from Blender Lab's `blender_mcp` at a pinned revision. Divergence means we own a socket protocol we did not write and cannot pull fixes for. Every change to them must be recorded in `NOTICE.md`.
- **GPL-3.0-or-later, and the boundary is the socket.** This package links `bpy` so it is GPL — that is true of every Blender add-on, not a consequence of what we adapted. The Slates API, MCP server and desktop are separate programs communicating over a documented protocol and stay proprietary. **Do not import Slates business logic into this package** — that is what would blur the line.
- **The add-on is a dumb executor and must stay one.** No model choice, no prompts, no credit logic, no project state. Anything that would need a plugin reinstall to change belongs in `slates-mcp` instead.
- **`blender_version_min` tracks the bundled docs.** The corpus under `data/` is Blender 5.1. If you refresh the docs, move the floor with them; mismatched docs are worse than none because wrong-but-plausible signatures read as authoritative.
- **Everything a `bpy` call touches gets snapshotted and restored.** `previs.py` runs inside the user's live .blend. Leaving the engine on Workbench or the format on FFMPEG corrupts their next hand render. `_snapshot`/`_restore` exist for this; use them.
- **Never guess the render output filename.** For video containers Blender appends the frame range to `filepath` and no setting turns that off. Render into an exclusive directory and discover what landed — see `_newest_video_in`.
- **🚨 Video output selection MOVED in Blender 5.0 — feature-detect, never branch on version.** 4.x selected it with `image_settings.file_format = 'FFMPEG'`. 5.x uses `image_settings.media_type = 'VIDEO'`, and `file_format` now enumerates image formats only — `'FFMPEG'` is not among them, so the 4.x line *raises* on the version this add-on pins. `_select_video_output` checks for the attribute, which is the thing we actually depend on. Encoding properties (`ffmpeg.format`, `.codec`, `.constant_rate_factor`, `.ffmpeg_preset`, `.gopsize`) go through `_apply_ffmpeg`, which skips whatever a build does not expose — a missing encoding option should cost a slightly bigger file, never a failed render.
  - **How this was caught, and the lesson:** the bundled `data/api/bpy.types.FFmpegSettings.rst` lists only the two audio attributes, while `data/manual/render/output/properties/output.rst` documents container/codec/CRF under `bpy.types.FFmpegSettings.*` anchors. **The API corpus is incomplete for some structs; the manual is the tiebreak.** When the API reference looks like a property vanished, check the manual before believing it.
- **Main thread only.** `bpy` is not thread-safe. The bridge already marshals execution onto Blender's timer; never spawn a thread that touches `bpy.data` or `bpy.ops`.
- **🚨 `bind()` failing is NOT how you learn a port is taken — probe it first.** `bridge/server.py` sets `SO_REUSEADDR` (vendored, not ours to change). On Linux/macOS that only permits rebinding a `TIME_WAIT` socket, so a live listener still makes `bind` raise. **On Windows it lets a second process bind a port another process is already LISTENING on** — verified 2026-08-28, two listeners on 127.0.0.1:9876, no error. `start_bridge`'s walk-forward therefore never fired: a second Blender bound 9876 too, and the MCP client (which probes 9876 first) drove whichever one Windows routed to — a render into the wrong .blend, silently. `_port_is_free` binds a plain optionless socket first, which is refused everywhere. `tests/port_fallback.py` locks it, and the same asymmetry applies to any future listener here.

## Wire protocol

```
-> {"type":"execute","code":"...","strict_json":false}\0
<- {"status":"ok","result":{...},"stdout":"...","stderr":"..."}\0
<- {"status":"error","message":"<traceback>"}\0
```

Executed code must assign a dict to `result`. `strict_json:false` means a stray Blender object comes back as its repr rather than failing the call — the agent can correct itself from a repr, not from a serialization error.

The client is `slates-mcp/packages/shared/src/clients/blender.ts`. **Protocol changes are a two-repo edit** and the client probes ports 9876-9879 in the same order the add-on binds them.

## Adding a helper the agent can call

Put it in `previs.py` / `scene.py` / `docs.py`, then reach it from an op with `_mod("previs").your_function(...)`. The client's prelude resolves the add-on package out of `sys.modules` by suffix, because extensions are imported as `bl_ext.<repo>.slates_blender` and the repo segment depends on where the user installed from — the name cannot be hardcoded.

## Build

```bash
python scripts/build.py                 # → dist/
python -m compileall -q slates_blender  # syntax check
python tests/port_fallback.py           # asserts; the one that must stay green
python tests/serve_stub.py              # serves the real bridge for 30s, no Blender needed
```

`serve_stub.py` is a HARNESS, not an assertion suite — it stands the real
`bridge/server.py` up against a stubbed `bpy` so you can drive it from the
Node client (`slates-mcp/packages/shared/dist/clients/blender.js`) and watch
the framing, the JSON envelope and the error paths for real. `port_fallback.py`
is the one that passes or fails on its own.

Delete `__pycache__` before building; `scripts/build.py` excludes it, but it pollutes greps.
