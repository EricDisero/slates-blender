# SPDX-FileCopyrightText: 2026 Slates
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Render the blocking pass — the one thing this add-on exists to produce.

A *blocking* render is an untextured grey-box playblast of the scene camera's
path. It is not a picture of the finished shot; it is the **structural log** a
video model consumes as a reference so it no longer has to invent camera
motion. Grey boxes are the point, not a limitation: the model dresses the
world, and anything we spend on materials here is spent twice.

Three decisions in here are load-bearing:

- **``view_context=False``.** ``render.opengl`` defaults to rendering whatever
  the user's viewport happens to be looking at, including their current
  shading, overlays and orbit position. That makes the output depend on where
  someone left their mouse. With ``view_context=False`` it renders the *scene
  camera* through the *scene render settings*, which is the only form that is
  reproducible across machines and sessions.
- **The output directory is exclusive, and we discover the filename after.**
  For video containers Blender does not write the path you hand it — it appends
  the frame range (``0001-0720.mp4``). There is no setting that turns this off,
  so guessing the name is a guaranteed-stale bug. We render into an empty
  directory and return whatever landed.
- **Every property is restored.** This runs inside the user's live .blend. A
  previs render that silently leaves the engine on Workbench, or the format on
  FFMPEG, corrupts the next real render they do by hand.
"""

from __future__ import annotations

import os
import tempfile
import time

import bpy

# Workbench draws flat-shaded solids with no light transport, which is both
# what previs wants to look like and roughly two orders of magnitude faster
# than EEVEE on the same frames.
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
) -> dict[str, object]:
    """Render the scene camera's animation to an H.264 mp4 and return its path.

    Returns a dict describing the clip — path, dimensions, fps, frame range and
    duration in seconds. ``durationSeconds`` is what the caller passes to
    Slates as ``videoReferenceSecondsEach``, so it is computed here rather than
    left for someone downstream to re-derive off a frame count.
    """
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

        try:
            bpy.ops.render.opengl(animation=True, view_context=False)
        except RuntimeError as error:
            raise PrevisError(f"Viewport render failed: {error}") from error
    finally:
        _restore(saved)

    filepath = _newest_video_in(output_dir)
    if filepath is None:
        raise PrevisError(
            f"The render reported success but no video file appeared in {output_dir}. "
            "This usually means Blender was built without FFmpeg support."
        )

    frames = end - start + 1
    result: dict[str, object] = {
        "filePath": filepath,
        "frameStart": start,
        "frameEnd": end,
        "frames": frames,
        "fps": int(fps),
        "durationSeconds": round(frames / float(fps), 3),
        "resolution": [int(resolution_x), int(resolution_y)],
        "camera": scene.camera.name,
        "sizeBytes": os.path.getsize(filepath),
        "mediaSelector": media_path,
    }
    if skipped:
        result["encodingSettingsSkipped"] = skipped
    return result
