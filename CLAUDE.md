# slates-blender — Claude code notes

Blender add-on. Opens a localhost execution bridge, ships Blender's documentation, renders the blocking pass. Everything else lives in `slates-mcp`.

## Layout

```
slates-blender/
├── blender_manifest.toml     ← extension manifest (id, version, wheels, version floor)
├── wheels/docutils-*.whl     ← required by the RST parser
├── scripts/build.py          ← zips the extension (hoists slates_blender/* to root)
├── tests/port_fallback.py    ← asserts the bridge never binds an occupied port
├── tests/serve_stub.py       ← runs the real bridge against a stubbed bpy
└── slates_blender/
    ├── __init__.py           ← registration, panel, operators, port binding
    ├── previs.py             ← THE deliverable: scene-camera render → mp4
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
- **🚨 `render.render`, NEVER `render.opengl` — and INVOKE it, don't EXEC it.** Two mistakes that both shipped in the first cut and were caught 2026-08-28 by reading upstream's own render tools plus the manual we bundle.
  - *Viewport render is viewport-anchored.* `data/manual/editors/3dview/viewport_render.rst`: it renders "from the current viewpoint (rather than from the active camera, as would be the case with a regular render)", and "if you are not in an active camera view, a virtual camera is used to match the current perspective." `view_context=False` does not buy the camera back, and it is a 3D-Viewport menu operator needing an area/region the bridge's timer callback does not have. A blocking clip whose framing depends on where the user left their mouse is worthless. `render.render` goes through the scene camera and the scene render settings — which is what makes `_PREVIS_ENGINE = BLENDER_WORKBENCH` load-bearing rather than decorative.
  - *A synchronous animation render from a timer re-enters the main loop running it.* Upstream (`blmcp/tools/render_*_toolcode.py`) invokes with `INVOKE_DEFAULT` whenever `not bpy.app.background` and returns a `check_is_finished` that polls `bpy.app.is_job_running('RENDER')`. We do the same, and `bridge/deferred.py` — already vendored — holds the socket open and answers with the identical `{status, result}` envelope, so no caller can tell. **The snapshot must NOT be restored in a `finally` on that path**: the invoke returns before a frame exists, and upstream carries the same warning. Restore inside the checker, after the job ends.
  - *"Not running" means "finished" only after it has started.* The bridge polls 50ms after the invoke, before Blender has spun the job up, so the checker must see the job running once (or let a grace window lapse) before it believes the render is over.
- **🚨 Workbench renders from `scene.display.shading`, so PIN THE WHOLE BLOCK — switching the engine inherits whatever the scene held.** Found 2026-08-28 on the first real projectId render. `render.engine = BLENDER_WORKBENCH` was pinned; everything that decides what the render LOOKS like was not, so the clip rode on scene values the user may never have opened. `_PREVIS_SHADING` now pins all of them alongside the engine and snapshots them with everything else. Measured on real footage: an unpinned scene moved the floor 132.6 → 117.9 and the background from neutral grey to **(212.9, 49.9, 20.9) RED**.
  - **`background_type` is the dangerous one, and it is pinned AWAY from Blender's default.** The default `THEME` reads the user's *Blender theme preference*, so the same .blend renders a different backdrop on a different machine. `VIEWPORT` + an explicit colour is the only theme-independent option. It matters because `slates-blocking-to-prompt` tells the model a flat background is a PLACEHOLDER to replace — a user's red viewport would arrive as content to dress instead of a hole to fill.
  - **🚨 `_snapshot` must COPY array properties, and this is not theoretical.** An RNA array (`background_color`, any colour or vector) hands back a LIVE proxy into Blender's memory, not a value. Verified 2026-08-28: stash the reference, overwrite the property, and the stash reads back the NEW value — so `_restore` writes back what it was meant to undo and the snapshot silently restores nothing. `_snapshot` now converts any non-str sequence to a tuple. Any future array-valued pin depends on this.
  - **`MATERIAL`, not `OBJECT`, and the choice is load-bearing.** In `MATERIAL` an object with a material renders its `diffuse_color` and an object with NO material falls back to Workbench's own neutral grey — which is exactly the grey set previs wants. `OBJECT` renders every unpainted object pure WHITE, so a floor or wall nobody assigned a colour blows out. `MATERIAL` is also the documented default, so pinning it changes no existing scene; it only decides what happens when `diffuse_color` and `object.color` disagree.
  - **Workbench never evaluates a shader node tree**, so a procedural `TEX_CHECKER` renders as flat grey. The `slates-previs-blocking` skill used to recommend checkering a floor for scale/speed parallax and that advice silently did nothing — it now says to build the checker as geometry with alternating `material_index`. `TEXTURE` colour type is not the escape hatch either: the bundled `data/api/bpy.types.View3DShading.rst` says it draws "the texture from the active **image** texture node using the active UV map", i.e. a baked image, never a procedural.
- **Never guess the render output filename.** For video containers Blender appends the frame range to `filepath` and no setting turns that off. Render into an exclusive directory and discover what landed — see `_newest_video_in`.
- **🚨 Video output selection MOVED in Blender 5.0 — feature-detect, never branch on version.** 4.x selected it with `image_settings.file_format = 'FFMPEG'`. 5.x uses `image_settings.media_type = 'VIDEO'`, and `file_format` now enumerates image formats only — `'FFMPEG'` is not among them, so the 4.x line *raises* on the version this add-on pins. `_select_video_output` checks for the attribute, which is the thing we actually depend on. Encoding properties (`ffmpeg.format`, `.codec`, `.constant_rate_factor`, `.ffmpeg_preset`, `.gopsize`) go through `_apply_ffmpeg`, which skips whatever a build does not expose — a missing encoding option should cost a slightly bigger file, never a failed render.
  - **How this was caught, and the lesson:** the bundled `data/api/bpy.types.FFmpegSettings.rst` lists only the two audio attributes, while `data/manual/render/output/properties/output.rst` documents container/codec/CRF under `bpy.types.FFmpegSettings.*` anchors. **The API corpus is incomplete for some structs; the manual is the tiebreak.** When the API reference looks like a property vanished, check the manual before believing it.
- **🚨 `Action.fcurves` IS GONE. Walk the slotted layout.** Found on real footage 2026-08-28, against Blender 5.2.1: `hasattr(action, "fcurves")` is **False**, and the old line raised `AttributeError` the moment a camera actually had keyframes — so an unanimated default scene passed and every real previs scene failed. The curves now live at `action.layers[] -> strips[] -> strip.channelbag(slot) -> .fcurves`, with the slot from `animation_data.action_slot`. `scene._action_fcurves` handles both shapes by feature-detection, the same rule `_select_video_output` follows. **The bundled API page for `bpy.types.Action` does not list the attribute either way** — the answer came from asking the running Blender through `slates_blender_execute`, which is the third source after the API reference and the manual, and the only one that is never out of date.
- **🚨 Keyframe SECONDS are measured from `frame_start`, not from frame zero.** These timestamps get quoted straight back into a generation prompt, and in the rendered clip the first rendered frame is t=0. A plain `frame / fps` put frame 1 at 0.042s, so every cut an agent wrote was one frame late — silently, and against the `slates-previs-blocking` skill's own stated rule (`frame = seconds x fps + 1`). Any new time field derives from `(frame - scene.frame_start) / fps`.
- **One zip in `dist/`, always.** `scripts/build.py` deletes the previous build before writing the new one; the published bucket does the same via `slates-api/scripts/upload-web-asset.mjs --prune`. Two versions side by side is somebody installing the wrong one and losing a session to a fix that was already applied.
- **Main thread only.** `bpy` is not thread-safe. The bridge already marshals execution onto Blender's timer; never spawn a thread that touches `bpy.data` or `bpy.ops`.
- **🚨 `bind()` failing is NOT how you learn a port is taken — probe it first.** `bridge/server.py` sets `SO_REUSEADDR` (vendored, not ours to change). On Linux/macOS that only permits rebinding a `TIME_WAIT` socket, so a live listener still makes `bind` raise. **On Windows it lets a second process bind a port another process is already LISTENING on** — verified 2026-08-28, two listeners on 127.0.0.1:9876, no error. `start_bridge`'s walk-forward therefore never fired: a second Blender bound 9876 too, and the MCP client (which probes 9876 first) drove whichever one Windows routed to — a render into the wrong .blend, silently. `_port_is_free` binds a plain optionless socket first, which is refused everywhere. `tests/port_fallback.py` locks it, and the same asymmetry applies to any future listener here.

## Wire protocol

```
-> {"type":"execute","code":"...","strict_json":false}\0
<- {"status":"ok","result":{...},"stdout":"...","stderr":"..."}\0
<- {"status":"error","message":"<traceback>"}\0
```

Executed code must assign a dict to `result`. `strict_json:false` means a stray Blender object comes back as its repr rather than failing the call — the agent can correct itself from a repr, not from a serialization error.

**Deferred replies are part of the protocol, not an extension of it.** Code that starts a background job (a render) assigns a callable to `check_is_finished` INSTEAD of `result`. The bridge then keeps the socket open, polls that callable on its own timer, and finally sends the same `{"status":"ok","result":…}` envelope — so the client sees one request and one reply either way and needs no special case. Whatever the callable returns becomes `result`; returning `None` means "still going". This is upstream's convention (`bridge/deferred.py`), and `slates_blender_render_blocking` is the op that uses it.

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

## Releasing — the add-on auto-updates, so a build is not a release

Users install through a **static extension repository**: `index.json` at
`https://slates-web-assets.t3.tigrisfiles.io/blender/index.json`, generated by
Blender's own `extension server-generate` and sitting beside the zips it names.
Blender stores that URL in the user's preferences and checks it for updates.

🚨 **That URL can never move.** Every install that has ever registered it points
there forever; change the path and they silently stop seeing updates. It is why
both files live under a `blender/` prefix rather than the bucket root, and why
`archive_url` stays RELATIVE — Blender resolves it against the index's own URL,
so the listing and its zips must share a folder.

`python scripts/build.py` prints the exact remaining commands. The shape:

1. `blender --command extension server-generate --repo-dir=dist` — never
   hand-write `index.json`; the format is Blender's and we do not own it, the
   same rule that keeps `bridge/` vendored verbatim.
2. Upload both from `slates-api/` with `upload-web-asset.mjs --prefix blender`.
   The zip is content-addressed and immutable; `index.json` is a MUTABLE
   manifest and needs `--replace`.
3. Bump `BLENDER_ADDON_VERSION` in `slates-web/src/app/lib/blender.ts` and
   deploy, so the page's install link names the new zip.

**The install link carries query parameters and they are the feature, not
decoration** — `?repository=./index.json&blender_version_min=…&platforms=…`.
Dragged into Blender that URL installs the add-on AND subscribes the user to the
repository. A bare `.zip` installs once and is then frozen. Documented at
`data/manual/advanced/extensions/creating_repository/static_repository.rst`.

🚨 **SUBSCRIBED IS NOT AUTO-UPDATING, AND THERE IS NO PARAMETER THAT MAKES IT
SO.** `UserExtensionRepo.use_sync_on_startup` is **False by default** for any repo a
user adds (`data/api/bpy.types.UserExtensionRepo.rst`: "Allow Blender to check for
updates upon launch (default False)") — `extensions.blender.org` only checks on
launch because Blender ships it pre-enabled. The install URL accepts exactly
`repository`, `blender_version_min`, `blender_max` and `platforms`, so nothing we
put in the link can switch it on. **A subscribed user who restarts Blender still
sees the old version.** Verified 2026-08-28: restarted a Blender subscribed to this
repo with 0.1.4 published and it stayed on 0.1.3.

What the subscription actually buys is the in-Blender update PATH (Get Extensions →
Refresh Remote → Update) instead of hunting for a zip — real, just not automatic.
Turning it on is one tick the USER has to make, so it has to be *told* to them:
`slates-web/src/app/blender/page.tsx` carries those instructions and is the only
place a stuck user will look. **Never write "it updates itself" anywhere** — that
copy shipped and was wrong.

⚠️ `blender_version_max` is deliberately omitted: Blender's own manual spells it
two ways on one page (`blender_version_max` in the parameter list, `blender_max`
in the format example) and we set no maximum anyway.

Delete `__pycache__` before building; `scripts/build.py` excludes it, but it pollutes greps.
