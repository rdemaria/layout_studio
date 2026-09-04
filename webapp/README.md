# Layout Studio web application

This directory contains the browser editor and interactive 3D viewer for the Layout
Studio JSON model. It has no runtime dependencies. The editable modules live under
`src/`. Two generated entry pages are checked in:

- `index.html` loads the local files under `src/` and is convenient for development.
- `build/index.html` is a fully self-contained single page with all CSS and JavaScript
  inlined. It can be copied or deployed on its own.

## Use the application

Open `index.html` or `build/index.html` in a current browser. Local JSON import and export
work without a web server. Use `build/index.html` when only one application file should
be distributed.

When the application is served over HTTP(S), it also looks for `/list.json` at the root
of the same web server. If that file exists, the top bar contains a dropdown for loading
the listed same-origin JSON layouts. A minimal catalogue is:

```json
[
  "M2--LS3.json",
  "SPS--LS3.json"
]
```

Entries can have separate display labels:

```json
{
  "files": [
    { "path": "M2--LS3.json", "label": "M2 — LS3" },
    { "path": "SPS--LS3.json", "label": "SPS — LS3" }
  ]
}
```

Paths are resolved from the web-server root and must remain on the same origin. A
missing `/list.json` is not an error; the dropdown is disabled and local file import
remains available.

## Develop locally

After changing a file in `src/`, rebuild both generated pages and run the checks:

```bash
cd webapp
python3 build.py
npm test
npm run check
```

`python3 build.py` refreshes both `index.html` and `build/index.html`. The GitHub Actions
workflow performs the same build, tests it, verifies that the standalone page has no
external script or stylesheet references, and commits changed generated pages.

To serve the generated application locally:

```bash
python3 -m http.server 8000
```

Then open `http://localhost:8000/` for the modular page or
`http://localhost:8000/build/` for the self-contained page. When `webapp/` itself is the
server root, put `list.json` and its JSON layouts in `webapp/`. When the repository root
is served, put them in the repository root instead.

## Viewer interaction

- **Orbit**, **Pan**, and **Select** retain the normal pointer interactions.
- **Zoom box** fits a rectangle drawn in the canvas while retaining the current
  orientation.
- **View** selects the canonical views from `+X`, `-X`, `+Y`, or `-Y`.
- **Axes** shrinks, expands, or hides the world axes without changing the geometry.
- The mouse wheel zooms about the pointer and does not scroll the editor pane.

## Files

- `index.html` — generated modular application; do not edit directly.
- `build/index.html` — generated fully self-contained single-page application.
- `build.py` — deterministic standard-library-only builder for both entry pages.
- `src/index.template.html` — document shell and model-help dialogs.
- `src/app.js` — editor state, cards, import/export, dependency tree, and interactions.
- `src/model.js` — canonical JSON validation, curved-frame mathematics, dependency
  resolution, and world-pose calculation.
- `src/viewer.js` — dependency-free interactive canvas renderer.
- `src/viewer-controls.js` — rectangle zoom, canonical views, and axis-size controls.
- `src/server-layouts.js` — optional `/list.json` catalogue and server-layout dropdown.
- `src/styles.css` — responsive application styling.
- `tests/model.test.mjs` — dependency-free model and geometry regression tests.
- `package.json` — lightweight `build`, `serve`, `test`, and `check` scripts.

## Layout JSON

The root object contains:

```json
{
  "reference_curves": {},
  "types": {},
  "objects": {}
}
```

Transformations are ordered `[operation, value]` pairs. Translation values are metres;
rotation values are radians. The supported operations are `tx`, `ty`, `ts`, `tt`, `rx`,
`ry`, and `rs`. Angle and roll fields are stored in radians in JSON and displayed in
degrees by the editor.

The app imports local JSON files, validates references, resolves curve/object dependency
chains, and exports the current canonical document. The reference graph is rooted at
World and starts collapsed; it can be expanded branch by branch or with the Expand all
control.

For large machines, the viewer switches distant or small objects to compact glyphs while
keeping detailed wireframes for selected or nearby geometry. This keeps SPS-sized
conversions practical without changing the JSON model.

## Browser support

The app uses Canvas 2D, `ResizeObserver`, `structuredClone` (with a JSON fallback), and
the native `<dialog>` element. Current Firefox, Chromium, and Safari releases are
supported.
