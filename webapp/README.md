# Layout Studio web application

This directory contains the browser editor and interactive 3D viewer for the Layout
Studio JSON model. It has no runtime dependencies. The editable modules live under `src/`, while the
checked-in `index.html` is the generated entry page and can be opened directly or hosted
as a static site.

## Use the standalone app

Open `index.html` in a current browser. No installation or server is required.

A layout can also be loaded at startup by URL when the page is served over HTTP(S):

```text
https://example.org/layout-studio/?layout=https://example.org/machine.layout.json
```

The remote JSON server must allow browser cross-origin requests.

## Develop locally

After changing a file in `src/`, rebuild the standalone page and run the checks:

```bash
cd webapp
python3 build.py
npm test
npm run check
```

To serve the generated application locally:

```bash
python3 -m http.server 8000
```

Then open `http://localhost:8000/`. The generated `index.html` loads the dependency-free
assets directly from `src/`; `build.py` keeps the checked-in page synchronized with its
template.

## Files

- `index.html` — generated standalone application; do not edit directly.
- `build.py` — deterministic standard-library-only standalone builder.
- `src/index.template.html` — document shell and model-help dialogs.
- `src/app.js` — editor state, cards, import/export, dependency tree, and interactions.
- `src/model.js` — canonical JSON validation, curved-frame mathematics, dependency
  resolution, and world-pose calculation.
- `src/viewer.js` — dependency-free interactive canvas renderer with orbit, pan,
  selection, fitting, and beam-frame overlays.
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

The app imports local JSON files, loads JSON over HTTP(S), validates references, resolves
curve/object dependency chains, and exports the current canonical document. The
reference graph is rooted at World and starts collapsed; it can be expanded branch by
branch or with the Expand all control.

For large machines, the viewer switches distant/small objects to compact glyphs while
keeping detailed wireframes for selected or nearby geometry. This keeps the SPS-sized
conversion practical without changing the JSON model.

## Browser support

The app uses Canvas 2D, `ResizeObserver`, `structuredClone` (with a JSON fallback), and
the native `<dialog>` element. Current Firefox, Chromium, and Safari releases are
supported.
