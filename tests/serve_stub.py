#!/usr/bin/env python
# SPDX-FileCopyrightText: 2026 Slates
# SPDX-License-Identifier: GPL-3.0-or-later

"""Run the real bridge server outside Blender, against a stubbed ``bpy``.

The point is to exercise `bridge/server.py` **as shipped** — its framing, its
NUL delimiting, its JSON envelope and its error paths — without needing a
Blender install in CI. Nothing in `server.py` imports `bpy` at module scope;
only the weak sandbox reaches for `bpy.ops`, so a stub with the one attribute
it patches is enough to run the whole request path for real.

The DEFERRED path is testable here too: `bpy.app.is_job_running` is stubbed
against `bpy.app._jobs`, so test code can start a fake `RENDER` job, end it,
and watch a real client receive the reply on the socket it never let go of.

What this does NOT cover: anything that actually touches a scene. `previs.py`
and `scene.py` reach `bpy.context` and need a real Blender.

Usage:  python tests/serve_stub.py [--port 9876] [--seconds 30] [--background]
"""

from __future__ import annotations

import argparse
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def install_bpy_stub() -> None:
    """Enough `bpy` to import the add-on and run the weak sandbox.

    Deliberately shallow: the base classes are bare `object` and the property
    factories return sentinels, because nothing here registers with Blender.
    Getting through `slates_blender/__init__.py` at all is itself worth
    something — it proves the add-on has no import-time work beyond declaring
    its classes.
    """
    bpy = types.ModuleType("bpy")

    ops = types.ModuleType("bpy.ops")

    def _op_create_function(module, func):  # noqa: ANN001
        def _call(*args, **kwargs):
            return {"FINISHED"}

        _call.__name__ = f"{module}.{func}"
        return _call

    ops._op_create_function = _op_create_function

    class _OpNamespace:
        """Lazy `bpy.ops.<module>.<func>` the way Blender resolves it.

        The weak sandbox works by swapping `_op_create_function`, so this must
        look the attribute up on the module at ACCESS time — capturing it here
        would silently read past the patch and the sandbox test would pass
        while blocking nothing.
        """

        def __init__(self, module: str) -> None:
            self._module = module

        def __getattr__(self, func: str):  # noqa: ANN202
            return ops._op_create_function(self._module, func)

    def _ops_getattr(module: str):  # noqa: ANN202
        if module.startswith("_"):
            raise AttributeError(module)
        return _OpNamespace(module)

    ops.__getattr__ = _ops_getattr

    bpy_types = types.ModuleType("bpy.types")
    for name in ("AddonPreferences", "Operator", "Panel", "PropertyGroup"):
        setattr(bpy_types, name, type(name, (object,), {}))

    bpy_props = types.ModuleType("bpy.props")
    for name in ("BoolProperty", "StringProperty", "IntProperty", "FloatProperty", "EnumProperty"):
        setattr(bpy_props, name, lambda **kwargs: None)

    timers = types.SimpleNamespace(
        register=lambda *a, **k: None,
        unregister=lambda *a, **k: None,
        is_registered=lambda *a, **k: False,
    )

    # `is_job_running` is here so the DEFERRED path can be exercised without
    # Blender: `previs.render_blocking` polls it, and it is the one piece of
    # `bpy` that decides whether the bridge holds a socket open or answers now.
    # Test code drives it by setting `bpy.app._jobs`.
    jobs: set[str] = set()

    app = types.SimpleNamespace(
        background=True,
        tempdir="/tmp",
        timers=timers,
        is_job_running=lambda job_type: job_type in jobs,
    )
    app._jobs = jobs

    utils = types.SimpleNamespace(
        register_class=lambda cls: None,
        unregister_class=lambda cls: None,
    )

    bpy.ops = ops
    bpy.app = app
    bpy.types = bpy_types
    bpy.props = bpy_props
    bpy.utils = utils
    sys.modules["bpy"] = bpy
    sys.modules["bpy.ops"] = ops
    sys.modules["bpy.types"] = bpy_types
    sys.modules["bpy.props"] = bpy_props


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=9876)
    parser.add_argument("--seconds", type=float, default=30.0)
    parser.add_argument(
        "--background",
        action="store_true",
        help=(
            "Drive the blocking single-request path instead of the interactive "
            "poll loop. Deferred responses are REFUSED on this path (upstream "
            "does not support them headless), so it cannot test a render."
        ),
    )
    args = parser.parse_args()

    install_bpy_stub()
    sys.path.insert(0, ROOT)

    from slates_blender.bridge import server

    server.start("127.0.0.1", args.port)
    print(f"[stub] bridge listening on 127.0.0.1:{args.port}", flush=True)

    import time

    deadline = time.monotonic() + args.seconds
    try:
        while time.monotonic() < deadline:
            if args.background:
                # No Blender timer headless, so drive the blocking path.
                server.poll_blocking(timeout=0.25)
            else:
                # What `execute.run` does on Blender's timer — the path that
                # actually ships, and the only one that services deferred work.
                server.poll()
                time.sleep(server.TIMER_INTERVAL_ACTIVE)
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
        print("[stub] stopped", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
