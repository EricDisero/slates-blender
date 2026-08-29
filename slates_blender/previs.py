# SPDX-FileCopyrightText: 2026 Slates
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Render the blocking pass — the one thing this add-on exists to produce.

A *blocking* render is an untextured grey-box playblast of the scene camera's
path. It is not a picture of the finished shot; it is the **structural log** a
video model consumes as a reference so it no longer has to invent camera
motion. Grey boxes are the point, not a limitation: the model dresses the
world, and anything we spend on materials here is spent twice.

Four decisions in here are load-bearing:

- **``bpy.ops.render.render``, NOT ``render.opengl``.** 🚨 This was the other
  way round until 2026-08-28 and it was wrong. The bundled manual
  (``data/manual/editors/3dview/viewport_render.rst``) is explicit: viewport
  render produces "quick preview renders **from the current viewpoint** (rather
  than from the active camera, as would be the case with a regular render)",
  and "if you are not in an active camera view, a virtual camera is used to
  match the current perspective." `view_context=False` does not buy back the
  camera — it is a 3D-Viewport menu operator, so it also needs an area/region
  this add-on does not have when the bridge calls it from a timer callback.
  Output that depends on where the user left their mouse is exactly what a
  blocking clip must never be. `render.render` goes through the SCENE CAMERA
  and the SCENE RENDER SETTINGS with no viewport involved, which is what makes
  ``_PREVIS_ENGINE`` load-bearing rather than decorative: Workbench is what
  makes the regular render come out grey-box and fast.
- **Interactive renders are DEFERRED; only background renders block.** Upstream
  Blender Lab's own render tools do exactly this, and the reason is that a
  synchronous animation render driven from `bpy.app.timers` re-enters the main
  loop that is already running it. So in a normal Blender session the operator
  is invoked with ``INVOKE_DEFAULT`` and a checker polls
  ``bpy.app.is_job_running('RENDER')``; the bridge holds the socket open and
  answers when it finishes (see ``bridge/deferred.py``). Blender stays
  responsive and the user can watch the render or cancel it.
- **The output directory is exclusive, and we discover the filename after.**
  For video containers Blender does not write the path you hand it — it appends
  the frame range (``0001-0720.mp4``). There is no setting that turns this off,
  so guessing the name is a guaranteed-stale bug. We render into an empty
  directory and return whatever landed.
- **Every property is restored — but on the DEFERRED path, only once the job
  is done.** This runs inside the user's live .blend, and a previs render that
  silently leaves the engine on Workbench or the format on FFMPEG corrupts the
  next real render they do by hand. ⚠️ Restoring in a `finally` is wrong when
  the render was invoked rather than executed: `INVOKE_DEFAULT` returns before
  a single frame exists, so a `finally` would put `filepath`, `engine` and the
  frame range back *while the render is still reading them*. Upstream carries
  the same warning against the same mistake.
"""

from __future__ import annotations

import os
import tempfile
import time
from collections.abc import Callable

import bpy

# 🚨 LOAD-BEARING, not a preference. `render.render` renders with the SCENE's
# engine, so this is the whole reason a regular camera render comes out as flat
# untextured solids instead of a lit EEVEE frame — and it is roughly two orders
# of magnitude faster on the same frames. The manual confirms Workbench is
# selectable as a final Render Engine, which is the mode being used here.
_PREVIS_ENGINE = "BLENDER_WORKBENCH"

# Properties saved and restored around a render, as (owner_path, attr) pairs
# resolved against the scene.
_SCENE_RENDER_ATTRS = (
    "engine",
    "filepath",
    "fps",
    "fps_base",
    "resolution_x",
    "resolution_y",
    "resolution_percentage",
    "use_file_extension",
    "use_overwrite",
)
_SCENE_ATTRS = ("frame_start", "frame_end")
# `media_type` is 5.x; `file_format` is how 4.x selected FFmpeg. Snapshot both
# and let `_select_video_output` decide which one to drive.
_IMAGE_SETTINGS_ATTRS = ("media_type", "file_format")
_FFMPEG_ATTRS = ("format", "codec", "constant_rate_factor", "ffmpeg_preset", "gopsize")

_VIDEO_SUFFIXES = (".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".ogv")

# How long a deferred render may take to appear in `bpy.app.is_job_running`
# before we stop waiting for it to start. Generous: it only bounds the gap
# between `INVOKE_DEFAULT` returning and the job registering, never the render.
_JOB_START_GRACE_SECONDS = 5.0


class PrevisError(RuntimeError):
    """A blocking render could not be produced. The message is agent-facing."""


def _snapshot(owner, attrs) -> list[tuple[object, str, object]]:
    return [(owner, a, getattr(owner, a)) for a in attrs if hasattr(owner, a)]


def _restore(saved) -> None:
    for owner, attr, value in saved:
        try:
            setattr(owner, attr, value)
        except Exception:  # noqa: BLE001 - restoring must never mask the real error
            pass


def _select_video_output(image_settings) -> str:
    """Switch the render output to video, across the 4.x/5.x API change.

    Blender 5.0 split media selection out of `file_format`: video is chosen
    with `media_type = 'VIDEO'`, and `file_format` now enumerates image
    formats only — `'FFMPEG'` is not among them, so the 4.x line raises on 5.x.
    Feature-detect rather than branching on `bpy.app.version`, because the
    attribute is the thing we actually depend on.

    Returns which path was taken, for the caller's diagnostics.
    """
    if hasattr(image_settings, "media_type"):
        image_settings.media_type = "VIDEO"
        return "media_type"
    image_settings.file_format = "FFMPEG"
    return "file_format"


def _apply_ffmpeg(ffmpeg, settings: dict[str, object]) -> list[str]:
    """Set what this build actually exposes; report what it did not.

    Encoding properties have moved between releases and are not uniformly
    present. A missing one should cost a slightly larger file, never a failed
    render, so skip it and say so.
    """
    skipped: list[str] = []
    for name, value in settings.items():
        if not hasattr(ffmpeg, name):
            skipped.append(name)
            continue
        try:
            setattr(ffmpeg, name, value)
        except (AttributeError, TypeError, ValueError):
            # An enum this build spells differently. Not worth failing over.
            skipped.append(name)
    return skipped


def _newest_video_in(directory: str) -> str | None:
    candidates = [
        os.path.join(directory, name)
        for name in os.listdir(directory)
        if name.lower().endswith(_VIDEO_SUFFIXES)
    ]
    if not candidates:
        return None
    return max(candidates, key=os.path.getmtime)


def render_blocking(
    *,
    output_dir: str | None = None,
    basename: str = "blocking",
    resolution_x: int = 1920,
    resolution_y: int = 1080,
    fps: int = 24,
    frame_start: int | None = None,
    frame_end: int | None = None,
    crf: str = "HIGH",
    defer: bool | None = None,
) -> dict[str, object] | Callable[[], dict[str, object] | None]:
    """Render the scene camera's animation to an H.264 mp4 and return its path.

    Returns a dict describing the clip — path, dimensions, fps, frame range and
    duration in seconds. ``durationSeconds`` is what the caller passes to
    Slates as ``videoReferenceSecondsEach``, so it is computed here rather than
    left for someone downstream to re-derive off a frame count.

    🚨 IN AN INTERACTIVE BLENDER THIS RETURNS A CALLABLE, NOT THE DICT.
    The render is invoked rather than executed (see the module docstring), so
    the clip does not exist yet when this returns. The callable is the bridge's
    ``check_is_finished`` convention: it yields ``None`` while the render runs
    and the result dict once it lands, and ``bridge/deferred.py`` wraps that in
    the same ``{"status": "ok", "result": …}`` envelope a synchronous call
    produces — so a bridge CALLER sees no difference at all. Only an in-process
    caller does, which is why the panel operator passes ``defer=False``.

    ``defer`` defaults to "yes unless Blender is running headless", because
    background Blender has no job system to poll and no UI to keep responsive.
    """
    if defer is None:
        defer = not bpy.app.background
    scene = bpy.context.scene

    if scene.camera is None:
        raise PrevisError(
            "The scene has no active camera, so there is no path to render. "
            "Create one and assign it: `bpy.context.scene.camera = cam_object`."
        )

    start = scene.frame_start if frame_start is None else int(frame_start)
    end = scene.frame_end if frame_end is None else int(frame_end)
    if end < start:
        raise PrevisError(
            f"frame_end ({end}) is before frame_start ({start}) — nothing to render."
        )

    # An exclusive directory is what makes filename discovery reliable.
    if output_dir is None:
        output_dir = tempfile.mkdtemp(prefix="slates_blocking_")
    else:
        output_dir = bpy.path.abspath(output_dir)
        output_dir = os.path.join(output_dir, f"{basename}_{int(time.time())}")
    os.makedirs(output_dir, exist_ok=True)

    render = scene.render
    image_settings = render.image_settings
    ffmpeg = render.ffmpeg

    saved = (
        _snapshot(render, _SCENE_RENDER_ATTRS)
        + _snapshot(scene, _SCENE_ATTRS)
        + _snapshot(image_settings, _IMAGE_SETTINGS_ATTRS)
        + _snapshot(ffmpeg, _FFMPEG_ATTRS)
    )

    def finish() -> dict[str, object]:
        """Discover what landed and describe it. Runs after the render, always."""
        filepath = _newest_video_in(output_dir)
        if filepath is None:
            raise PrevisError(
                f"The render reported success but no video file appeared in {output_dir}. "
                "This usually means Blender was built without FFmpeg support."
            )
        frames = end - start + 1
        out: dict[str, object] = {
            "filePath": filepath,
            "frameStart": start,
            "frameEnd": end,
            "frames": frames,
            "fps": int(fps),
            "durationSeconds": round(frames / float(fps), 3),
            "resolution": [int(resolution_x), int(resolution_y)],
            "camera": camera_name,
            "sizeBytes": os.path.getsize(filepath),
            "mediaSelector": media_path,
        }
        if skipped:
            out["encodingSettingsSkipped"] = skipped
        return out

    # Read BEFORE the render: on the deferred path `finish` runs minutes later,
    # by which time the user may have renamed or reassigned the camera.
    camera_name = scene.camera.name
    media_path = ""
    skipped: list[str] = []

    try:
        render.engine = _PREVIS_ENGINE
        render.resolution_x = int(resolution_x)
        render.resolution_y = int(resolution_y)
        render.resolution_percentage = 100
        render.fps = int(fps)
        render.fps_base = 1.0
        render.use_file_extension = True
        render.use_overwrite = True
        render.filepath = os.path.join(output_dir, basename)

        scene.frame_start = start
        scene.frame_end = end

        media_path = _select_video_output(image_settings)
        skipped = _apply_ffmpeg(
            ffmpeg,
            {
                "format": "MPEG4",
                "codec": "H264",
                "constant_rate_factor": crf,
                "ffmpeg_preset": "GOOD",
                # A short GOP keeps the reference seekable; models sample it
                # frame-wise and a 250-frame GOP makes early frames expensive
                # to reach.
                "gopsize": int(fps),
            },
        )

        # 🚨 THE EXECUTION CONTEXT IS THE WHOLE DIFFERENCE.
        # No context string means EXEC_DEFAULT: Blender renders every frame
        # before this line returns. That is right in background mode and wrong
        # in a session, where this call arrives from a `bpy.app.timers`
        # callback — a synchronous animation render there re-enters the main
        # loop that is already running it. INVOKE_DEFAULT hands the render to
        # Blender's job system and returns immediately instead.
        render_args = ("INVOKE_DEFAULT",) if defer else ()
        try:
            bpy.ops.render.render(*render_args, animation=True)
        except RuntimeError as error:
            raise PrevisError(f"Render failed: {error}") from error
    except Exception:
        # Only a FAILED start restores here. A successful invoke has a render
        # reading these properties for the next several minutes.
        _restore(saved)
        raise

    if not defer:
        _restore(saved)
        return finish()

    # 🚨 "NOT RUNNING" MEANS "FINISHED" ONLY AFTER IT HAS STARTED.
    # `INVOKE_DEFAULT` returns before Blender has spun the job up, and the
    # bridge polls the checker 50ms later — so a naive `if not is_job_running:
    # we're done` reports failure on the very first tick, every time, because
    # no frame has been written yet. The job must be SEEN running once (or the
    # grace window must lapse, which is the honest signal that the invoke did
    # nothing at all).
    state = {"seen_running": False, "deadline": time.monotonic() + _JOB_START_GRACE_SECONDS}

    def check_is_finished() -> dict[str, object] | None:
        """Bridge deferred-response convention: None until the render lands.

        Polled on the bridge's own timer (`bridge/deferred.py`), so it must stay
        cheap — one job-state query, then one directory listing exactly once.
        """
        if bpy.app.is_job_running("RENDER"):
            state["seen_running"] = True
            return None
        if not state["seen_running"] and time.monotonic() < state["deadline"]:
            return None
        # The render is done with the scene now, so the user's settings go back
        # BEFORE anything else can raise. `_restore` never throws.
        _restore(saved)
        return finish()

    return check_is_finished
