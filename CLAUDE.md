# slates-blender: Claude code notes

Blender 5.1+ add-on, GPL-3.0-or-later. Opens a localhost execution bridge, ships Blender's documentation, and renders the blocking pass. Everything else lives in `slates-mcp`. Status: live; users install it through a static extension repository. The workspace parent's `CLAUDE.md` loads with this file and holds the GPL-boundary, two-repo protocol-change, commit and `npm run dev` rules.

## Commands

```bash
python scripts/build.py                 # builds dist/ (one zip) and prints the remaining release commands
python -m compileall -q slates_blender  # syntax check
python tests/port_fallback.py           # asserts the bridge never binds an occupied port; must stay green
python tests/serve_stub.py              # harness, not assertions: serves the real bridge for 30s against a stubbed bpy
```

`serve_stub.py` stands `bridge/server.py` up with no Blender so you can drive it from the Node client (`slates-mcp/packages/shared/dist/clients/blender.js`) and watch the framing, the JSON envelope and the error paths for real.

## Layout

```
slates-blender/
├── blender_manifest.toml     extension manifest (id, version, wheels, version floor)
├── wheels/docutils-*.whl     required by the RST parser
├── scripts/build.py          zips the extension (hoists slates_blender/* to root)
├── tests/                    port_fallback.py, serve_stub.py
└── slates_blender/
    ├── __init__.py           registration, panel, operators, port binding
    ├── previs.py             the deliverable: scene-camera render to mp4
    ├── scene.py              scene, camera and collection summary
    ├── docs.py               API lookup and full-text search over data/
    ├── bridge/               VENDORED (Blender Authors, GPL-3.0)
    ├── rst/                  VENDORED RST readers
    └── data/                 Blender 5.1 API reference and manual as .rst
```

## Rules

- **Never edit `bridge/` or `rst/` beyond import rewrites.** They are vendored from Blender Lab's `blender_mcp` at a pinned revision; divergence means we own a socket protocol we did not write and cannot pull fixes for. Record every change to them in `NOTICE.md`.
- **The add-on is a dumb executor.** No model choice, no prompts, no credit logic, no project state, and no Slates business logic imported into this package. Anything that would need a plugin reinstall to change belongs in `slates-mcp`.
- **`blender_version_min` tracks the bundled docs.** The corpus under `data/` is Blender 5.1; if you refresh the docs, move the floor with them. Mismatched docs are worse than none, because wrong-but-plausible signatures read as authoritative.
- **Snapshot and restore everything a `bpy` call touches** (`_snapshot` and `_restore` in `previs.py`). `previs.py` runs inside the user's live .blend, and leaving the engine on Workbench or the format on FFMPEG corrupts their next hand render. `_snapshot` copies array properties, because an RNA array is a live proxy into Blender's memory.
- **Blocking render is `render.render`, never `render.opengl`, and it is invoked, not exec'd.** Restore the snapshot inside the render checker after the job ends, never in a `finally` on that path.
- **Pin the whole Workbench shading block** (`_PREVIS_SHADING`) alongside the engine, so the render never rides on scene values the user may never have opened.
- **Never guess the render output filename.** Render into an exclusive directory and discover what landed (`_newest_video_in`).
- **Feature-detect, never branch on Blender version** (`_select_video_output`, `_apply_ffmpeg`, `scene._action_fcurves`). When the API reference lacks a property, the manual settles it, then the running Blender through `slates_blender_execute`.
- **Keyframe seconds are `(frame - scene.frame_start) / fps`**: the first rendered frame is t=0.
- **Main thread only.** `bpy` is not thread-safe; the bridge marshals execution onto Blender's timer, and nothing here may spawn a thread that touches `bpy.data` or `bpy.ops`.
- **Probe a port before binding it** (`_port_is_free`). `bind()` failing does not tell you a port is taken, because on Windows `SO_REUSEADDR` lets a second listener bind.
- **A build is not a release, and a subscribed user is not auto-updated.** The extension repository URL can never move, and no copy anywhere may say the add-on updates itself.

## Read when

| Situation | Read |
|---|---|
| Changing `render_blocking`, the shading pins, video output selection, keyframe timing, or anything a Blender release may have moved | [`docs/previs-render-rules.md`](docs/previs-render-rules.md) |
| Changing what the bridge accepts or returns, adding a helper the agent can call, or touching port binding | [`docs/bridge-protocol.md`](docs/bridge-protocol.md) |
| Any release, or any copy about how the add-on updates | [`docs/releasing.md`](docs/releasing.md) |
