# Bridge protocol

The wire protocol the add-on speaks, how deferred replies work, how to add a helper the agent can call, and how the port is bound. Read before changing what the bridge accepts or returns, adding a helper, or touching port binding.

The client is `slates-mcp/packages/shared/src/clients/blender.ts`, and it probes ports 9876-9879 in the same order the add-on binds them. A protocol change is a two-repo edit (the workspace parent's `CLAUDE.md` states the rule).

---

## Vendored bridge files

`bridge/server.py` is the non-blocking TCP server (NUL-delimited JSON). `bridge/execute.py` is the `bpy.app.timers` poll callback that runs the code on Blender's main thread. `capture.py`, `sandbox.py` and `deferred.py` are the stdout/stderr capture, the execution sandbox and the deferred-reply holder. All of `bridge/` is vendored; the rule against editing it is in `CLAUDE.md`.

## Wire format

```
-> {"type":"execute","code":"...","strict_json":false}\0
<- {"status":"ok","result":{...},"stdout":"...","stderr":"..."}\0
<- {"status":"error","message":"<traceback>"}\0
```

Executed code must assign a dict to `result`. `strict_json:false` means a stray Blender object comes back as its repr rather than failing the call; the agent can correct itself from a repr, not from a serialization error.

## Deferred replies

Deferred replies are part of the protocol, not an extension of it. Code that starts a background job (a render) assigns a callable to `check_is_finished` instead of `result`. The bridge then keeps the socket open, polls that callable on its own timer, and finally sends the same `{"status":"ok","result":...}` envelope, so the client sees one request and one reply either way and needs no special case. Whatever the callable returns becomes `result`; returning `None` means "still going". This is upstream's convention (`bridge/deferred.py`), and `slates_blender_render_blocking` is the op that uses it.

## Adding a helper the agent can call

Put it in `previs.py`, `scene.py` or `docs.py`, then reach it from an op with `_mod("previs").your_function(...)`. The client's prelude resolves the add-on package out of `sys.modules` by suffix, because extensions are imported as `bl_ext.<repo>.slates_blender` and the repo segment depends on where the user installed from, so the name cannot be hardcoded.

## Binding the port

`bind()` failing is not how you learn a port is taken: probe it first. `bridge/server.py` sets `SO_REUSEADDR` (vendored, not ours to change). On Linux and macOS that only permits rebinding a `TIME_WAIT` socket, so a live listener still makes `bind` raise. On Windows it lets a second process bind a port another process is already listening on: verified 2026-08-28, two listeners on 127.0.0.1:9876, no error. `start_bridge`'s walk-forward therefore never fired: a second Blender bound 9876 too, and the MCP client (which probes 9876 first) drove whichever one Windows routed to, a render into the wrong .blend, silently. `_port_is_free` binds a plain optionless socket first, which is refused everywhere. `tests/port_fallback.py` locks it, and the same asymmetry applies to any future listener here.
