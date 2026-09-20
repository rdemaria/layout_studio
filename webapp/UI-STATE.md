# UI state in layout JSON

Drop one `.json` file anywhere on the page, or use **Import file**. Both use the
same asynchronous parser and validation. Invalid JSON leaves the current layout
intact; dropping multiple files reports an error without choosing one implicitly.

**Download JSON** includes an optional top-level `ui_state` object alongside
`reference_curves`, `types`, and `objects`. File imports, catalog loads, and the
`?url=` loader restore it. Files without this field open with the default UI.

Version 1 contains:

| Field | Saved values |
| --- | --- |
| `version` | `1` |
| `editor` | `selectedCurve`, `selectedType`, `selectedObject`, `selectedTypeFrame`, `segmentPage`, `selection` |
| `cards` | Boolean `curves`, `types`, `objects`, `viewer`, `dependencies`, `segments`, `typeFrames` |
| `scope` | `{kind: "layout"}`, or `{kind: "curve" \| "object", name: "…"}` |
| `viewport` | `camera`, `pastViews`, `futureViews`, `mode`, `showCurves`, `showObjects`, `showFrames`, `showMechanicalAxis`, `showMagneticAxis`, `showBeamAxis` |
| `dependencies` | Expanded branch IDs in `expanded`, branch page numbers in `pages`, and the last selected node ID or null in `selection` |

`camera` stores `azimuth`, `elevation` (radians), `distance`, `target` (three world
coordinates), and optional three-component `axisScale`. It is absent until a
viewport has been created. `pastViews` and `futureViews` hold up to 100 cameras
each, so the previous/next zoom buttons continue to work after loading.
`mode` is `orbit`, `pan`, `select`, or `zoom-region`. Pages are zero-based.
Editor `selection` uses the existing curve/object/frame selection structure,
or null. Display choices and cameras survive hiding and reopening their cards.

UI state is advisory and has no effect on positioning. Stale entity names and
invalid individual UI values fall back to valid defaults; unknown versions
open with defaults. The canonical parser, schema, and Python API accept and
preserve the opaque object, including unknown members. A new download writes
the current version and current settings. Python exposes it as `Layout.ui_state`
(`None` when omitted) and preserves it through `from_json` / `to_json`.

Transient interactions (hover, a drag in progress, open menus, unfinished search
text, and status messages), source URLs, and Python bridge connections are not
stored. Python live updates with `preserveViewport` keep the current viewer state.
