#!/usr/bin/env python
# SPDX-FileCopyrightText: 2026 Slates
# SPDX-License-Identifier: GPL-3.0-or-later

"""Prove the port fallback actually walks forward when a port is held.

🚨 THIS EXISTS BECAUSE THE BUG IS INVISIBLE WHERE IT LIVES. `bridge.start`
sets `SO_REUSEADDR`, which on Linux and macOS only permits rebinding a socket
in `TIME_WAIT` — but on **Windows** lets a second process bind a port another
process is already LISTENING on. Without the probe in `start_bridge`, a second
Blender binds 9876 as well, the loop never advances, and the MCP client (which
tries 9876 first) drives whichever of the two Windows happens to route to. The
render lands in the wrong .blend and nothing reports it.

A developer on macOS would never see it, and neither would a Windows developer
running one Blender. So it gets a test rather than a note.

Usage:  python tests/port_fallback.py
"""

from __future__ import annotations

import os
import socket
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from serve_stub import install_bpy_stub  # noqa: E402


def _incumbent(port: int) -> socket.socket:
    """A listener holding *port* exactly the way `bridge.start` holds one."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setblocking(False)
    sock.bind(("127.0.0.1", port))
    sock.listen(5)
    return sock


def main() -> int:
    install_bpy_stub()
    import slates_blender as addon

    failures: list[str] = []

    def check(condition: bool, message: str) -> None:
        if condition:
            print(f"  [ok] {message}")
        else:
            failures.append(message)
            print(f"  [FAIL] {message}")

    # The premise, asserted rather than assumed: on this platform, does
    # SO_REUSEADDR let two listeners share a port? Either answer is fine —
    # what must not happen is start_bridge handing back an occupied port.
    held = _incumbent(9876)
    shareable = False
    try:
        second = _incumbent(9876)
        second.close()
        shareable = True
    except OSError:
        pass
    print(
        f"  (this platform {'DOES' if shareable else 'does not'} allow two "
        "SO_REUSEADDR listeners on one port)"
    )

    try:
        port = addon.start_bridge()
        check(port == 9877, f"a held base port is skipped (got {port}, want 9877)")
        addon.stop_bridge()
    finally:
        held.close()

    port = addon.start_bridge()
    check(port == 9876, f"a free base port is still taken first (got {port})")
    addon.stop_bridge()

    # Every port held: fail loudly rather than returning a port nobody owns.
    holders = [_incumbent(p) for p in range(9876, 9880)]
    try:
        try:
            addon.start_bridge()
            check(False, "a fully occupied range raises instead of returning")
        except RuntimeError:
            check(True, "a fully occupied range raises instead of returning")
    finally:
        for sock in holders:
            sock.close()

    print("")
    if failures:
        print(f"{len(failures)} FAILURE(S) - the bridge can bind an occupied port")
        return 1
    print("PORT FALLBACK HOLDS - the bridge never binds a port another process is serving")
    return 0


if __name__ == "__main__":
    sys.exit(main())
