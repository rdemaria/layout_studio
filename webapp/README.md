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

The checked-in `build/index.html` is a fully self-contained browser application with
its JavaScript and CSS inlined. Rebuild it from the same application source with:

```bash
npm run build:standalone
```

The small build harness lives under `standalone/`; the application source remains in
`app/` and the shared UI primitives in `components/`.

## Source map

- `app/page.tsx` — editor state and top-level workspace.
- `app/layout-data.ts` — canonical JSON model and validation.
- `app/layout-geometry.ts` — curve, frame, object, and snapping mathematics.
- `app/layout-viewport.tsx` — interactive 3D projection and viewer controls.
- `app/layout-controls.tsx` — reusable model-editing controls.
- `app/dependency-tree.tsx` — World-rooted dependency view.
- `app/globals.css` — responsive application styling.
- `tests/` — model, geometry, rendering, and UI regression checks.
- `standalone/` — repository-relative single-file bundler.
- `build/index.html` — generated standalone application.

## Layout JSON

The root document contains `reference_curves`, `types`, and `objects`. Transformations
are ordered `[operation, value]` pairs; translations are metres and rotations are
radians. Named local coordinate systems are called frames. The application validates
references, resolves curve/object dependency chains, and evaluates world poses before
rendering or export.
