# SPDX-FileCopyrightText: 2026 Slates
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Slates for Blender — block the shot in 3D, then let the model dress it.

The add-on is deliberately thin. It opens a localhost execution bridge, ships
Blender's own documentation so an agent can write correct ``bpy``, and renders
a grey-box blocking pass. **Everything else — model choice, prompts, credits,
project state — lives in the Slates MCP server**, which is where it can change
without anyone reinstalling a Blender add-on.

That split is also the licence boundary. This package links ``bpy`` and is
GPL-3.0-or-later; the MCP server talks to it over a socket and is not a
derivative work.
"""

from __future__ import annotations

import bpy

from . import bridge

BRIDGE_HOST = "127.0.0.1"
DEFAULT_PORT = 9876
# If the port is taken (a second Blender, a stale process), walk forward rather
# than failing — the MCP server probes the same short range.
PORT_FALLBACKS = 3

_active_port: int | None = None
_last_error: str = ""


def active_port() -> int | None:
    return _active_port


def status() -> tuple[str, str]:
    """``(state, detail)`` where state is running / stopped / error."""
    if bridge.is_running() and _active_port is not None:
        return ("running", f"{BRIDGE_HOST}:{_active_port}")
    if _last_error:
        return ("error", _last_error)
    return ("stopped", "")


def start_bridge() -> int:
    """Bind the first free port in the range. Returns the port."""
    global _active_port, _last_error

    if bridge.is_running() and _active_port is not None:
        return _active_port

    _last_error = ""
    last_os_error: OSError | None = None
    for candidate in range(DEFAULT_PORT, DEFAULT_PORT + PORT_FALLBACKS + 1):
        try:
            bridge.start(BRIDGE_HOST, candidate)
        except OSError as error:
            last_os_error = error
            continue
        _active_port = candidate
        return candidate

    _active_port = None
    _last_error = (
        f"No free port in {DEFAULT_PORT}-{DEFAULT_PORT + PORT_FALLBACKS} ({last_os_error}). "
        "Another Blender may already be serving the bridge."
    )
    raise RuntimeError(_last_error)


def stop_bridge() -> None:
    global _active_port
    bridge.stop()
    _active_port = None


class SlatesPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    autostart: bpy.props.BoolProperty(
        name="Start bridge automatically",
        description="Open the local execution bridge when Blender loads this add-on",
        default=True,
    )


class SLATES_OT_start_bridge(bpy.types.Operator):
    bl_idname = "slates.start_bridge"
    bl_label = "Start Bridge"
    bl_description = "Open the local execution bridge so the Slates MCP server can reach this Blender"

    def execute(self, context):
        try:
            port = start_bridge()
        except Exception as error:  # noqa: BLE001 - surfaced in the UI, not raised at Blender
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Slates bridge listening on {BRIDGE_HOST}:{port}")
        return {"FINISHED"}


class SLATES_OT_stop_bridge(bpy.types.Operator):
    bl_idname = "slates.stop_bridge"
    bl_label = "Stop Bridge"
    bl_description = "Close the local execution bridge"

    def execute(self, context):
        stop_bridge()
        self.report({"INFO"}, "Slates bridge stopped.")
        return {"FINISHED"}


class SLATES_OT_render_blocking(bpy.types.Operator):
    bl_idname = "slates.render_blocking"
    bl_label = "Render Blocking Pass"
    bl_description = (
        "Render the scene camera to a grey-box mp4 — the reference clip that locks "
        "camera motion for generation"
    )

    def execute(self, context):
        from . import previs

        try:
            result = previs.render_blocking()
        except Exception as error:  # noqa: BLE001
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        context.window_manager.clipboard = str(result["filePath"])
        self.report(
            {"INFO"},
            f"Blocking rendered ({result['durationSeconds']}s) — path copied to clipboard.",
        )
        return {"FINISHED"}


class SLATES_PT_panel(bpy.types.Panel):
    bl_idname = "SLATES_PT_panel"
    bl_label = "Slates"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Slates"

    def draw(self, context):
        layout = self.layout
        state, detail = status()

        box = layout.box()
        if state == "running":
            box.label(text="Bridge connected", icon="LINKED")
            box.label(text=detail)
            box.operator(SLATES_OT_stop_bridge.bl_idname, icon="CANCEL")
        else:
            box.label(
                text="Bridge stopped" if state == "stopped" else "Bridge error",
                icon="UNLINKED" if state == "stopped" else "ERROR",
            )
            if detail:
                box.label(text=detail[:60])
            box.operator(SLATES_OT_start_bridge.bl_idname, icon="PLAY")

        guide = layout.box()
        guide.label(text="Connect your agent")
        guide.label(text="1. Install the Slates CLI:")
        guide.label(text="   npm i -g @slatesvideo/cli")
        guide.label(text="2. Sign in:  slates login")
        guide.label(text="3. Add the MCP server to Claude.")
        guide.label(text="It finds this Blender automatically")
        guide.label(text="while the bridge is running.")

        shot = layout.box()
        shot.label(text="Blocking")
        shot.operator(SLATES_OT_render_blocking.bl_idname, icon="RENDER_ANIMATION")


_CLASSES = (
    SlatesPreferences,
    SLATES_OT_start_bridge,
    SLATES_OT_stop_bridge,
    SLATES_OT_render_blocking,
    SLATES_PT_panel,
)


def _autostart() -> None:
    """Deferred one-shot: preferences are not readable during register()."""
    try:
        prefs = bpy.context.preferences.addons[__package__].preferences
        if prefs.autostart:
            start_bridge()
    except Exception as error:  # noqa: BLE001 - never block add-on load
        print(f"[slates] bridge autostart skipped: {error}")
    return None


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    if not bpy.app.background:
        bpy.app.timers.register(_autostart, first_interval=0.1)


def unregister() -> None:
    stop_bridge()
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
