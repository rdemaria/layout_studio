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
- `app/python-bridge.ts` — validated external-control protocol for Python.
- `app/layout-url-catalog.ts` — validation and resolution for URL suggestions.
- `app/layout-controls.tsx` — reusable model-editing controls.
- `app/dependency-tree.tsx` — World-rooted dependency view.
- `app/globals.css` — responsive application styling.
- `tests/` — model, geometry, rendering, and UI regression checks.
- `tests/python-bridge.test.mjs` — bridge protocol and security regressions.
- `standalone/` — repository-relative single-file bundler.
- `build/index.html` — generated standalone application.

## URL suggestions

The editor reads the optional same-origin `list.json` next to the served page and
shows its local paths in a dropdown beside the free-form URL field. In a source
development server this file comes from `public/list.json`. The compact form is:

```json
["layouts/sps.json", "layouts/lhc.json"]
```

Entries may also provide labels, as in the checked-in sample:

```json
[{"path": "layouts/sample-layout.json", "label": "Sample layout"}]
```

Only same-origin HTTP(S) paths from the catalog are offered. Prefer relative paths
so a layout continues to work when the app is mounted below an origin root. A
manually entered URL may still point elsewhere. The catalog is optional, so the
editor keeps working when it is absent.

## Viewer navigation

The viewer toolbar supports orbit, pan, selection, whole-layout fit, signed
canonical views (`+X`, `-X`, `+Y`, `-Y`, `+Z`, `-Z`), and rectangle zoom. Rectangle
zoom reframes the selected screen region without changing the current orientation.

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
