# SPDX-FileCopyrightText: 2026 Blender Authors
# SPDX-FileCopyrightText: 2026 Slates
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Reading the scene back out, cheaply.

An agent that cannot see the scene writes code against a scene it imagined.
The collection/object tree here is adapted from ``blender_mcp``'s objects
summary; the camera and timing block is ours, because previs asks a different
question than general Blender work does — not *what is in the scene* but
*where does the camera go, for how long, and is any of it actually animated*.

**Cheap on purpose.** This gets called before nearly every edit, so it returns
names, types and transforms — never mesh data. A summary that dumps geometry
stops being callable on the scenes that most need it.
"""

from __future__ import annotations

from typing import Any

import bpy


def _object_info(obj) -> dict[str, Any]:
    info: dict[str, Any] = {
        "name": obj.name,
        "type": obj.type,
        "parent": obj.parent.name if obj.parent else None,
        "location": [round(v, 4) for v in obj.location],
        "rotationEuler": [round(v, 4) for v in obj.rotation_euler],
        "scale": [round(v, 4) for v in obj.scale],
        "visible": obj.visible_get(),
    }
    if obj.animation_data and obj.animation_data.action:
        info["action"] = obj.animation_data.action.name
    # Constraints are how a camera gets aimed, so an agent debugging a rig
    # needs them by name and target without a second round trip.
    if obj.constraints:
        info["constraints"] = [
            {
                "type": c.type,
                "target": getattr(getattr(c, "target", None), "name", None),
            }
            for c in obj.constraints
        ]
    if obj.type == "CAMERA":
        cam = obj.data
        info["lens"] = round(cam.lens, 3)
        info["sensorWidth"] = round(cam.sensor_width, 3)
        info["clip"] = [round(cam.clip_start, 4), round(cam.clip_end, 3)]
    return info


def _collection_tree(layer_collection) -> dict[str, Any]:
    collection = layer_collection.collection
    return {
        "name": collection.name,
        "excluded": layer_collection.exclude,
        "hidden": collection.hide_viewport,
        "objects": sorted(
            (_object_info(o) for o in collection.objects),
            key=lambda o: o["name"],
        ),
        "children": sorted(
            (_collection_tree(c) for c in layer_collection.children),
            key=lambda c: c["name"],
        ),
    }


def _action_fcurves(anim):
    """Every F-curve driving *anim*, across both Action layouts.

    🚨 `Action.fcurves` DOES NOT EXIST ANY MORE. Slotted Actions moved the
    curves to `action.layers[].strips[]` → `strip.channelbag(slot)` → `.fcurves`,
    with the slot coming off `animation_data.action_slot`. Verified against a
    live Blender 5.2.1 on 2026-08-28: `hasattr(action, "fcurves")` is False, and
    the old line raised `AttributeError: 'Action' object has no attribute
    'fcurves'` the moment a camera actually had keyframes on it — i.e. on every
    real previs scene, while an unanimated default scene passed happily.

    Feature-detected rather than version-branched, the same rule
    `_select_video_output` follows: legacy actions still expose `.fcurves`, and
    a build that has both should use the one it actually has.
    """
    action = getattr(anim, "action", None)
    if action is None:
        return

    legacy = getattr(action, "fcurves", None)
    if legacy is not None:
        yield from legacy
        return

    # A slot binds this datablock to its own channels inside the action; an
    # action assigned without one animates nothing, so there is nothing to walk.
    slot = getattr(anim, "action_slot", None)
    if slot is None:
        return

    for layer in getattr(action, "layers", ()):
        for strip in getattr(layer, "strips", ()):
            # Only keyframe strips carry channelbags. Anything else (a future
            # strip type) is skipped rather than guessed at.
            channelbag = getattr(strip, "channelbag", None)
            if channelbag is None:
                continue
            try:
                bag = channelbag(slot)
            except (RuntimeError, TypeError):
                continue
            if bag is not None:
                yield from bag.fcurves


def _camera_track(camera, scene) -> dict[str, Any]:
    """Keyframe times on the camera, so the agent can see the cut structure."""
    times: set[int] = set()
    for holder in (camera, camera.data):
        anim = holder.animation_data
        if anim:
            for fcurve in _action_fcurves(anim):
                for kp in fcurve.keyframe_points:
                    times.add(int(round(kp.co[0])))
    fps = scene.render.fps / max(scene.render.fps_base, 1e-6)
    # 🚨 SECONDS ARE MEASURED FROM `frame_start`, NOT FROM FRAME ZERO.
    # These timestamps exist to be quoted back in a generation prompt against
    # the blocking clip, and in that clip the FIRST RENDERED FRAME is t=0. A
    # plain `t / fps` made frame 1 land at 0.042s on a default scene, so every
    # cut an agent wrote was one frame late — consistently, invisibly, and in
    # direct contradiction of the `slates-previs-blocking` skill's own rule
    # (`frame = seconds x fps + 1`). Caught on real footage 2026-08-28.
    origin = scene.frame_start
    return {
        "name": camera.name,
        "keyframes": sorted(times),
        "keyframeSeconds": [round((t - origin) / fps, 3) for t in sorted(times)],
        "animated": bool(times),
    }


def summary() -> dict[str, Any]:
    """Scene, timing, camera and the full collection tree."""
    context = bpy.context
    scene = context.scene
    view_layer = context.view_layer
    render = scene.render

    fps = render.fps / max(render.fps_base, 1e-6)
    frames = scene.frame_end - scene.frame_start + 1
    active = view_layer.objects.active

    # ⚠️ `context.mode` IS GUARDED ON PURPOSE. This whole function runs inside a
    # `bpy.app.timers` callback, where the context is the window manager's and
    # not an operator's — upstream's own objects-summary tool reads it as
    # `context.mode if active else None` for the same reason. `summary()` is the
    # first call of every previs workflow (`slates_blender_status` runs it), so
    # one attribute being unavailable must not take the whole handshake down.
    try:
        mode = context.mode if active is not None else None
    except AttributeError:
        mode = None

    result: dict[str, Any] = {
        "scene": scene.name,
        "unitSystem": scene.unit_settings.system,
        "timing": {
            "frameStart": scene.frame_start,
            "frameEnd": scene.frame_end,
            "frames": frames,
            "fps": round(fps, 4),
            "durationSeconds": round(frames / fps, 3),
        },
        "render": {
            "engine": render.engine,
            "resolution": [render.resolution_x, render.resolution_y],
            "resolutionPercentage": render.resolution_percentage,
        },
        "activeObject": active.name if active else None,
        "mode": mode,
        "camera": None,
        "collections": _collection_tree(view_layer.layer_collection),
    }

    if scene.camera is not None:
        result["camera"] = _camera_track(scene.camera, scene)
    return result
