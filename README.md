# Slates for Blender

Block your shot in 3D, then let the model dress it.

A Blender add-on that lets a Claude/Codex agent build a **blocking pass** — untextured grey boxes and a camera path — render it to mp4, and hand that clip to Slates as a reference video. The model then renders a world around a camera path it no longer has to invent.

**Why bother:** a text prompt makes the model invent the camera, so it invents differently every roll and you pay for each one. A reference video removes the invention. Iteration moves to the free half.

---

## Install

1. Download `slates_blender-<version>.zip` from <https://slates.video/blender>.
2. Blender → `Edit ▸ Preferences ▸ Add-ons ▸ Install from Disk`, pick the zip.
3. In the 3D viewport press **N**, open the **Slates** tab, click **Start Bridge**.

Requires **Blender 5.1+**. The floor is deliberate — the bundled API reference is the 5.1 set, and an agent grepping 4.x docs to write 5.x `bpy` is worse than one with no docs at all.

Then, wherever your agent runs:

```bash
npm i -g @slatesvideo/cli
slates login
```

The Slates MCP server finds this Blender automatically while the bridge is running. There is no second connector to configure.

---

## What the agent gets

Six tools, on top of the ~76 Slates ops it already has:

| Tool | Does |
|---|---|
| `slates_blender_status` | Is a bridge reachable, and the scene summary if so |
| `slates_blender_execute` | Run `bpy` Python; returns whatever the code assigns to `result` |
| `slates_blender_scene` | Frame range, fps, camera keyframe times, object tree |
| `slates_blender_docs` | Look up a dotted Blender API identifier |
| `slates_blender_search_docs` | Full-text search of the bundled API reference and manual |
| `slates_blender_render_blocking` | Render the scene camera to mp4 and import it into a Slates project |

There is deliberately **no camera-move library**. Camera work is written as `bpy` against the Blender documentation the add-on ships. A fixed menu of moves would cap the workflow at whatever we thought of.

The workflow itself lives in five agent skills — `slates-previs-blocking`, `slates-camera-language`, `slates-blocking-to-prompt`, `slates-dialogue-blocking`, `slates-restyle-from-blocking` — shipped in `@slatesvideo/shared` and readable via `slates_get_prompting_guide`.

---

## How it fits together

```
Claude ──stdio──> Slates MCP server ──TCP 127.0.0.1:9876──> this add-on ──> bpy
                        │
                        └──HTTPS──> Slates desktop + API (projects, credits, generation)
```

The add-on is a **dumb executor**. Model choice, prompts, credits and project state live in the MCP server, where they change without anyone reinstalling a Blender add-on.

That split is also the licence boundary: this package links `bpy` and is GPL-3.0-or-later; the MCP server talks to it over a socket and is not a derivative work.

---

## Development

```bash
python scripts/build.py          # → dist/slates_blender-<version>.zip
python -m compileall -q slates_blender
```

The build hoists `slates_blender/*` to the zip root, because Blender wants `blender_manifest.toml` and `__init__.py` at the top level.

### Testing without Blender

`tests/serve_stub.py` runs the real bridge server against a stubbed `bpy`, which exercises the wire protocol — framing, NUL delimiting, the JSON envelope, error paths, the weak sandbox — with no Blender install:

```bash
python tests/serve_stub.py --port 9876 --seconds 60
```

It does **not** cover anything that touches a scene. `previs.py` and `scene.py` need a real Blender.

---

## Licence

GPL-3.0-or-later. Every Blender add-on links `bpy` and carries this licence; it is not a choice forced by the code we adapted.

The bridge, the RST readers and the documentation corpus are adapted from Blender Lab's [`blender_mcp`](https://projects.blender.org/lab/blender_mcp). Full provenance and the list of changes: [`NOTICE.md`](NOTICE.md).
