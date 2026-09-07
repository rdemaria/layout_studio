# Layout Studio web application

This directory contains the complete React/Vinext source for the Layout Studio editor
and its interactive 3D viewer.

## Development

Requirements: Node.js 22.13 or newer on Linux.

```bash
npm ci
npm run dev
```

Useful checks:

```bash
npm run lint
npm test
```

`npm test` builds the Sites application and runs the model, geometry, rendering, and
interface tests.

For the SPS performance analysis and profiling commands, see [PERFORMANCE.md](PERFORMANCE.md).

## Standalone build

Running the standalone build produces `build/index.html` with its JavaScript and
CSS fully inlined. It also copies the optional URL catalog and its listed local
JSON files beside the page. Rebuild after bridge/source or catalog changes before
using it from Python:

```bash
npm run build:standalone
```

The small build harness lives under `standalone/`; the application source remains in
`app/` and the shared UI primitives in `components/`.

The Python API can serve this generated file with `standalone_path=` or find it
automatically in a source checkout. Python wheels intentionally do not embed
the generated bundle; use an explicitly trusted `viewer_url=` with an installed
wheel. Do not add a second generated HTML copy under `python_api`.

## Source map

- `app/page.tsx` — editor state and top-level workspace.
- `app/layout-data.ts` — canonical JSON model and validation.
- `app/layout-geometry.ts` — curve, frame, object, and snapping mathematics.
- `app/layout-viewport.tsx` — interactive 3D projection and viewer controls.
- `app/viewport-zoom.ts` — depth picking from the rendered geometry for zoom.
- `app/python-bridge.ts` — validated external-control protocol for Python.
- `app/layout-url-catalog.ts` — catalog validation and debug URL resolution.
- `app/layout-import.ts` — JSON loading from files and URLs.
- `app/layout-controls.tsx` — reusable model-editing controls.
- `app/curve-segment-editor.tsx` — lazily mounted segment editor with 50-row pages.
- `app/dependency-tree.tsx` — World-rooted dependency view.
- `app/globals.css` — responsive application styling.
- `tests/` — model, geometry, rendering, and UI regression checks.
- `tests/python-bridge.test.mjs` — bridge protocol and security regressions.
- `standalone/` — repository-relative single-file bundler.
- `build/index.html` — generated standalone application.

## Zoom and display proportions

Wheel zoom moves the camera toward the geometry under the pointer while keeping
that detail at the same screen position. Rectangle zoom uses the depth of the
geometry inside the box, preferring its center, and approaches that plane. Empty
space uses the current camera target plane. Both keep a fixed field of view.

Open **Axis scale** at the lower left of the viewer to compress or stretch global
X, Y, and Z independently, from 0.001× to 1000×. The sliders use a logarithmic scale;
numeric fields allow exact factors. Use **Fit layout** after changing proportions
if needed, and **Reset to 1×** to restore physical proportions. Active factors
remain visible when the panel is closed.

Scaling affects only the display, including picking, pan, zoom, and fitting.
World-coordinate readouts, positioning calculations, and exported JSON remain
physical. It is useful for long straight sections near a global axis (for example,
compress Z to 0.01×); it does not unroll a ring or compress a diagonal section along
its own direction. In those cases an axis aligned with the section or a longitudinal
schematic view would be more suitable.

## Anchors and feature references

Objects are positioned by their `anchor` frame. A type's optional
`mechanical_center` places its shape; magnetic centers, named frames, and object
beam centers use the same reference-and-operations editor. The default reference
is the anchor. Choose a local frame, world, a curve, or a frame on another object.
Local frame references use `{"kind":"local_frame","frame":"name"}` in JSON.
Targets must be rooted in their own anchor, and reference cycles are rejected.
The reader normalizes old `center` links to `anchor`.

The common [positioning specification](../specifications/layout_positioning_model.tex)
and [reference example](../specifications/examples/anchored-features.json) define
these semantics for both JavaScript and Python.

## Loading layouts

The dropdown lists layouts from the optional same-origin `list.json` next to the
served page. Selecting an entry loads it immediately; there is no separate Load
button or URL text field. In a source development server this file comes from
`public/list.json`. The compact form is:

```json
["layouts/SPS--LS3.json", "layouts/sample-layout.json"]
```

Entries may also provide labels, as in the checked-in sample:

```json
[{"path": "layouts/sample-layout.json", "label": "Sample layout"}]
```

Only same-origin HTTP(S) paths from the catalog are offered. Prefer relative paths
so a layout continues to work when the app is mounted below an origin root. The
bundled catalog includes the sample layout and the SPS LS3 and M2 LS3 conversions
as plain JSON files.

**Import file** and URL loading use plain `.json`. Files are parsed directly from
text and URLs use the browser's JSON response reader. Files can still be imported
when the catalog is absent or the standalone page is opened directly from disk.

For debugging, add `?url=...` to the page address to load an arbitrary HTTP(S) URL
on startup, independently of the catalog. Relative URLs resolve beside the page;
cross-origin URLs require the remote server to allow CORS. URL-encode the value,
especially when it contains its own query parameters, for example:

```text
index.html?url=https%3A%2F%2Fexample.org%2Flayout.json
```

Matching quotes around the value, as in `?url="layouts/SPS--LS3.json"`, are also
accepted. The editor has no free-text URL input.

## Viewer navigation

Numeric fields underline the digit adjusted by their up/down buttons, the keyboard
Up/Down keys, or the mouse wheel while the field has focus. The left/right buttons
beside the spinner select a coarser/finer decimal place (also Alt+Left/Alt+Right).
Extra zeros expose places beyond the displayed digits without changing the value.
Each adjustment adds or subtracts one unit of the selected place and preserves
the finer digits. Direct typing and pasted scientific notation remain supported.

The viewer toolbar supports orbit, pan, selection, whole-layout fit, signed
canonical views (`+X`, `-X`, `+Y`, `-Y`, `+Z`, `-Z`), and rectangle zoom. Rectangle
zoom reframes the selected screen region without changing the current orientation.
Curves and mechanical object shapes are visible initially. Named frames, the
magnetic axis with its entry/exit frames, and the Beam-interface axis with its
entry/exit frames are independent layers and start hidden.

## Python bridge

The bridge is dormant during ordinary web-app use. It is enabled only by an
exact nonce/origin URL fragment followed by a matching transferred
`MessagePort`; window source, origin, protocol envelope, command fields, and
values are validated before dispatch.

Protocol 1 supports layout replacement/readback, selection, fit, strict scene
scope, orbit/pan/select/rectangle-zoom modes, signed canonical views, and layer
visibility. It emits ready and selection events plus a response for every
command. A curve or object scope enumerates only that entity's scene geometry,
while recursive resolution retains access to the complete layout for hidden
dependencies.

A hosted bridge asset must implement protocol 1, use HTTPS unless it runs on
literal loopback, and permit iframe embedding.
It changes only which app Python embeds; it does not make Python's loopback
wrapper and layout endpoints reachable from a remote notebook browser.

## Layout JSON

The root document contains `reference_curves`, `types`, and `objects`. Transformations
are ordered `[operation, value]` pairs; translations are metres and rotations are
radians. Named local coordinate systems are called frames. The application validates
references, resolves curve/object dependency chains, and evaluates world poses before
rendering or export.

A type always defines the implicit `center` frame. Its mechanical shape is optional;
when present, the shape tuple contains its centerline length, curvature, and roll.
The magnetic group belongs to the type. The optional Beam-interface group belongs
to each object and is edited in the Objects card. Each group contains a local center
transformation plus its own length, curvature, and roll. An omitted object group
inherits all four magnetic values dynamically, including its derived entry/exit
frames. Without either group, no beam frames exist. JSON preserves inheritance by
omitting the object beam fields. Type-level beam fields from older documents must
be moved to their objects. A center-only object remains selectable in the viewer.
