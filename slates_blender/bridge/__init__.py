# SPDX-FileCopyrightText: 2026 Blender Authors
# SPDX-FileCopyrightText: 2026 Slates
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Localhost execution bridge.

A non-blocking TCP server inside Blender that accepts null-delimited JSON
requests and executes Python on Blender's main thread. Adapted from Blender
Lab's ``blender_mcp`` (see ``../../NOTICE.md``); the socket protocol, the
main-thread timer, output capture, the weak sandbox and deferred responses are
upstream's and are kept intact.

**Why a socket and not an MCP server in-process.** ``bpy`` is main-thread only
and Blender owns the main loop, so anything that wants to touch the scene has
to hand work to a timer and wait. The socket is what lets a separate process
(our MCP server) do that without embedding an event loop in Blender.
"""

from . import execute, server

DEFAULT_HOST = server.DEFAULT_HOST
DEFAULT_PORT = server.DEFAULT_PORT


def is_running() -> bool:
    return server.is_running()


def start(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    """Start the listener and register the main-thread polling timer."""
    if is_running():
        return
    server.start(host, port)

    import bpy

    if not bpy.app.timers.is_registered(execute.run):
        bpy.app.timers.register(
            execute.run,
            first_interval=server.TIMER_INTERVAL_ACTIVE,
            persistent=True,
        )


def stop() -> None:
    """Stop the listener, drop clients and deferred work, unregister the timer."""
    import bpy

    server.stop()
    if bpy.app.timers.is_registered(execute.run):
        bpy.app.timers.unregister(execute.run)
