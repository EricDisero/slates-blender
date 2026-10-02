# Previs render rules

Why the blocking render in `previs.py` and the scene summary in `scene.py` are built the way they are, with the receipts. Read before changing `render_blocking`, `_PREVIS_SHADING`, video output selection, keyframe timing, or anything that reads `bpy` data a Blender release may have moved.

The rule for each is one line in [`CLAUDE.md`](../CLAUDE.md); the code comments in `previs.py` and `scene.py` carry the same reasoning beside the code.

---

## Render with `render.render`, invoked

Two mistakes both shipped in the first cut and were caught 2026-08-28 by reading upstream's own render tools plus the manual we bundle.

**Viewport render is viewport-anchored.** `data/manual/editors/3dview/viewport_render.rst`: it renders "from the current viewpoint (rather than from the active camera, as would be the case with a regular render)", and "if you are not in an active camera view, a virtual camera is used to match the current perspective." `view_context=False` does not buy the camera back, and it is a 3D-Viewport menu operator that needs an area and region the bridge's timer callback does not have. A blocking clip whose framing depends on where the user left their mouse is worthless. `render.render` goes through the scene camera and the scene render settings, which is what makes `_PREVIS_ENGINE = BLENDER_WORKBENCH` load-bearing rather than decorative.

**A synchronous animation render from a timer re-enters the main loop running it.** Upstream (`blmcp/tools/render_*_toolcode.py`) invokes with `INVOKE_DEFAULT` whenever `not bpy.app.background` and returns a `check_is_finished` that polls `bpy.app.is_job_running('RENDER')`. We do the same, and `bridge/deferred.py` (already vendored) holds the socket open and answers with the identical `{status, result}` envelope, so no caller can tell. The snapshot must not be restored in a `finally` on that path: the invoke returns before a frame exists, and upstream carries the same warning. Restore inside the checker, after the job ends.

"Not running" means "finished" only after the job has started. The bridge polls 50 ms after the invoke, before Blender has spun the job up, so the checker must see the job running once (or let a grace window lapse, `_JOB_START_GRACE_SECONDS`) before it believes the render is over.

## Pin the whole Workbench shading block

Workbench renders from `scene.display.shading`, so switching the engine inherits whatever the scene held. Found 2026-08-28 on the first real projectId render: `render.engine = BLENDER_WORKBENCH` was pinned, but everything that decides what the render looks like was not, so the clip rode on scene values the user may never have opened. `_PREVIS_SHADING` now pins all of them alongside the engine and snapshots them with everything else. Measured on real footage: an unpinned scene moved the floor 132.6 to 117.9 and the background from neutral grey to (212.9, 49.9, 20.9), red.

- **`background_type` is pinned away from Blender's default.** The default `THEME` reads the user's Blender theme preference, so the same .blend renders a different backdrop on a different machine. `VIEWPORT` plus an explicit colour is the only theme-independent option. It matters because `slates-blocking-to-prompt` tells the model a flat background is a placeholder to replace; a user's red viewport would arrive as content to dress instead of a hole to fill.
- **`_snapshot` copies array properties.** An RNA array (`background_color`, any colour or vector) hands back a live proxy into Blender's memory, not a value. Verified 2026-08-28: stash the reference, overwrite the property, and the stash reads back the new value, so `_restore` writes back what it was meant to undo and the snapshot silently restores nothing. `_snapshot` converts any non-str sequence to a tuple. Any future array-valued pin depends on this.
- **`MATERIAL`, not `OBJECT`.** In `MATERIAL` an object with a material renders its `diffuse_color` and an object with no material falls back to Workbench's own neutral grey, which is exactly the grey set previs wants. `OBJECT` renders every unpainted object pure white, so a floor or wall nobody assigned a colour blows out. `MATERIAL` is also the documented default, so pinning it changes no existing scene; it only decides what happens when `diffuse_color` and `object.color` disagree.
- **Workbench never evaluates a shader node tree**, so a procedural `TEX_CHECKER` renders as flat grey. The `slates-previs-blocking` skill says to build a checker as geometry with alternating `material_index`. The `TEXTURE` colour type is not the escape hatch either: the bundled `data/api/bpy.types.View3DShading.rst` says it draws "the texture from the active **image** texture node using the active UV map", a baked image and never a procedural.

## Never guess the render output filename

For video containers Blender appends the frame range to `filepath` and no setting turns that off. Render into an exclusive directory and discover what landed (`_newest_video_in`).

## Detect features, never Blender versions

**Video output selection moved in Blender 5.0.** 4.x selected it with `image_settings.file_format = 'FFMPEG'`. 5.x uses `image_settings.media_type = 'VIDEO'`, and `file_format` now enumerates image formats only: `'FFMPEG'` is not among them, so the 4.x line raises on the version this add-on pins. `_select_video_output` checks for the attribute, which is the thing we actually depend on. Encoding properties (`ffmpeg.format`, `.codec`, `.constant_rate_factor`, `.ffmpeg_preset`, `.gopsize`) go through `_apply_ffmpeg`, which skips whatever a build does not expose: a missing encoding option should cost a slightly bigger file, never a failed render.

How this was caught: the bundled `data/api/bpy.types.FFmpegSettings.rst` lists only the two audio attributes, while `data/manual/render/output/properties/output.rst` documents container, codec and CRF under `bpy.types.FFmpegSettings.*` anchors. The API corpus is incomplete for some structs; the manual is the tiebreak. When the API reference looks like a property vanished, check the manual before believing it.

**`Action.fcurves` is gone; walk the slotted layout.** Found on real footage 2026-08-28, against Blender 5.2.1: `hasattr(action, "fcurves")` is False, and the old line raised `AttributeError` the moment a camera actually had keyframes, so an unanimated default scene passed and every real previs scene failed. The curves now live at `action.layers[] -> strips[] -> strip.channelbag(slot) -> .fcurves`, with the slot from `animation_data.action_slot`. `scene._action_fcurves` handles both shapes by feature detection, the same rule `_select_video_output` follows. The bundled API page for `bpy.types.Action` does not list the attribute either way; the answer came from asking the running Blender through `slates_blender_execute`, the third source after the API reference and the manual, and the only one that is never out of date.

## Keyframe seconds start at `frame_start`

These timestamps are quoted straight back into a generation prompt, and in the rendered clip the first rendered frame is t=0. A plain `frame / fps` put frame 1 at 0.042 s, so every cut an agent wrote was one frame late, silently, and against the `slates-previs-blocking` skill's own stated rule (`frame = seconds x fps + 1`). Any new time field derives from `(frame - scene.frame_start) / fps`.
