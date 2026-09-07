# Layout Studio Python API

`layout-studio` is the Python counterpart of Layout Studio: it describes
piecewise circular reference curves, reusable curved component types, and
objects positioned by aligning named frames. The Layout Studio browser viewer
provides the interactive view of the analytically evaluated geometry.

Distances are in metres, rotations are in radians, and curvature is in
metres⁻¹.  The homogeneous pose convention is `[x y tangent origin]`.

## Install for development

```bash
python -m pip install -e '.[test]'
```

The geometric model, JSON round trips, and Python side of the web bridge need
only NumPy. In a source checkout, build the bridge-enabled browser asset once with
`make -C ../webapp standalone` (or `make -C webapp standalone` from the
repository root). Generated web bundles are intentionally excluded from Python
wheels; an installed package can instead use an explicitly trusted protocol-1
URL via `viewer_url=` or a local file via `standalone_path=`.

## Quick start

```python
from layout_studio import Box, Frame, Layout, Position, Segment

layout = Layout()

main = layout.new_curve(
    "main",
    starting_frame=Frame("world").tx(1.0),
    color="#7aa2f7",
    segments=[Segment(10.0), Segment(5.0, angle=0.2, roll=0.01)],
)

quadrupole = layout.new_type(
    "quadrupole",
    shape=Box(1.1, 0.9, 1.6, curvature=0.22),
    color="#f0a84b",
    magnetic_center=Frame(),
    magnetic_length=1.4,
    magnetic_curvature=0.22,
    magnetic_roll=0.0,
)
quadrupole.new_frame("survey_mark").tx(0.4)

q1 = layout.new_object(
    "Q1",
    type=quadrupole,
    position=Position("curve:main", target="center").ts(3.1).tt(0.2),
)

q2 = layout.new_object(
    "Q2",
    type=quadrupole,
    position=(
        Position(
            "Q1->magnetic_exit",
            target="magnetic_entry",
            reference_curve=main,
        )
        .ts(0.3)
        .tx(0.001)
    ),
)

layout.validate()
print(q2.get_frame("magnetic_exit"))
layout.to_json(filename_or_url="layout.json")

# plot_web() returns immediately. Display it in a local notebook, call show(),
# or pass show=True to open the system browser during construction.
web = q1.plot_web(show=False)
web.show()
web.set_visibility(magnetic_axis=True, beam_axis=True)

# Model edits are published explicitly, so a series of edits is serialized
# only once.
q1.position.tx(0.05)
web.update()
web.close()
```

## JSON input and output

JSON text and locations are deliberately separate inputs. Pass literal JSON
with `text=...`; pass a local filename or an HTTP(S) URL with
`filename_or_url=...`:

```python
document = layout.to_json()  # Equivalent to layout.to_json(str).
copy = Layout.from_json(text=document)

layout.to_json(filename_or_url="layout.json")
loaded = Layout.from_json(filename_or_url="layout.json")
```

`to_json()` returns a string only when its destination is omitted or is the
built-in `str` object. Any actual string is a filename or URL. HTTP(S) reads
use GET and writes use PUT. A location ending in `.gz` is decompressed or
compressed automatically; gzip-compressed byte input is also accepted through
`text=...`.

## Optional geometry and object beam interfaces

Every type and object always has an implicit `center` frame. All other physical
features are optional:

- `shape` is the mechanical swept geometry centered on `center`; its existing
  `dz`, curvature, and roll define its mechanical axis.
- A magnetic axis exists only when `magnetic_center`, `magnetic_length`,
  `magnetic_curvature`, and `magnetic_roll` are all supplied.
- Each object may define `beam_center`, `beam_length`, `beam_curvature`, and
  `beam_roll` together. If omitted, the beam interface follows all four magnetic
  values dynamically. With neither an override nor a magnetic axis, beam frames
  do not exist. Objects sharing a type can have different beam interfaces.

Partial groups and JSON `null` values are invalid. Lengths are positive;
curvature and roll are finite. Entry and exit lie at half the effective length
along the corresponding axis. Local center transformations use the mechanical
path for `ts` (straight when no shape exists).

`Type.set_magnetic_axis()` / `remove_magnetic_axis()` edit the magnetic group.
`Object.set_beam_axis()` creates or edits an override, starting from inherited
values when available. `Object.remove_beam_axis()` clears the override and
restores inheritance. For example:

```python
# Initially q1 and q2 both inherit quadrupole's magnetic axis.
q1.set_beam_axis(length=1.2, center=Frame().tx(0.001))
assert q2.beam_center is None  # still inherited
q1.remove_beam_axis()         # follows magnetic edits again
print(q1.effective_beam_axis) # (center Frame, length, curvature, roll)
```

`Object.beam_*` properties contain explicit values or `None`; the effective
axis is available through `effective_beam_axis` and `get_frame("beam_entry")`,
`get_frame("beam_center")`, or `get_frame("beam_exit")`. Inheritance is preserved
in JSON by omitting the beam fields. All seven implicit names are reserved
from `Type.frames`, but beam frames resolve only on objects.

Older beam definitions under a type must be moved to each object using that
type and removed from the type. The reader rejects type-level beam fields.

## Reference shorthand

Generic references accept only unambiguous strings:

| Input | Reference |
| --- | --- |
| `"world"` | World frame |
| `"curve:main"` | Curve `main` |
| `"Q1->magnetic_exit"` | `Q1` magnetic-exit frame |
| `"Q1->beam_exit"` | `Q1` beam-interface exit frame |

Bare names are still accepted in namespace-specific fields such as
`type="quadrupole"`, `target="magnetic_entry"`, and
`reference_curve="main"`.  JSON always uses the canonical structured form.

For repeated geometric evaluation, keep one resolver session open so
validation, dependency results, and inferred curve stations are shared:

```python
with layout.resolver() as resolver:
    q1_pose = resolver.object_frame(q1)
    q2_pose = resolver.object_frame(q2)
```

Treat that context as a per-thread snapshot: finish model mutations before
entering it, and do not share one active resolver between threads.

## Web viewer

`plot_web()` drives the existing Layout Studio web application through a
capability-scoped loopback bridge and returns without blocking Python. Layout
snapshots are served as compact, gzip-capable HTTP resources; they are not
embedded in generated HTML or URL query strings.

With neither `standalone_path=` nor `viewer_url=` specified, a source checkout
uses its local `webapp/build/index.html` after the build command above. For
offline, sensitive, or reproducible work, pass that locally generated file
explicitly. A hosted URL downloads application code in the browser and receives
the layout in browser memory, so only use a host you trust.

`Layout.plot_web()` shows the complete scene. `Curve.plot_web()` and
`Object.plot_web()` load the complete document so dependencies can still be
resolved, but scene construction, picking, bounds, and fitting are limited to
that entity and its enabled axis/frame layers. This is a computational scope,
not just selection or visibility; it does not reduce snapshot transfer size.

The independent `magnetic_axis`, `beam_axis`, and `frames` visibility layers
default off. A feature-axis layer includes its curved axis, with entry/exit
frames shown as transverse planes. `frames` contains only the type's stored
named frames.
The curves and object-shape layers retain their own switches. For example:

```python
viewer.set_visibility(magnetic_axis=True, beam_axis=True, frames=True)
```

The returned `WebViewer` supports `select()`, `fit()`, `set_scope()`,
`set_mode()` (including `"zoom-region"`), signed-axis `set_view()`,
`set_visibility()`, `request_layout()`, `get_event()`, `wait_ready()`, and
`wait_response()`.
Changing the Python model is deliberately not live-bound: call `update()` to
publish a fresh snapshot. Command methods return an id only when an explicit
acknowledgement via `wait_response()` is useful.

After `wait_ready()`, `request_layout()` asks for the browser editor's current
document and returns a command id;
`wait_response(id)["result"]["layout"]` retrieves it. Initial
scope and visibility are applied atomically with the first snapshot, and
`update()` preserves the active camera, navigation mode, and layer state.
A readback still pending when the iframe reconnects returns an explicit error,
because the restored latest snapshot cannot reproduce its historical ordering.

Use `close()` or a context manager to stop the local server; closing a browser
tab alone is not Python-side cleanup, and repeated `close()` calls are safe:

```python
with layout.plot_web(show=False) as viewer:
    display(viewer)  # Local IPython/Jupyter
    viewer.set_view("+z")
```

A source checkout reuses `webapp/build/index.html`; rebuild it with
`make -C webapp standalone` before first use. Generated bundles are deliberately
excluded from Python wheels. `standalone_path=` can select a protocol-1 build,
and `viewer_url=` can select an embeddable compatible hosted application.
When the standalone directory contains `list.json`, the loopback viewer serves
that catalog and only the local JSON files explicitly listed by it, so the web
editor's URL dropdown and relative URL loading work without exposing unrelated
files from the directory.
Only use a hosted viewer you trust: it receives the full layout through the
authenticated message channel. Non-loopback hosted viewers must use HTTPS.
If the embedded application reloads, the bridge reinstalls the latest Python
snapshot and controls, then resumes unhandled one-shot requests without
replaying obsolete layouts. Open or display a `WebViewer` once at a time;
two browser tabs pointed at the same capability URL would both consume its
command stream.

The browser must be able to reach the kernel-side
`127.0.0.1:<viewer-port>`. Normal Python sessions and local notebooks work
directly. Remote notebooks require an explicit port-forward/proxy; setting
`viewer_url=` changes the embedded application only and does not expose the
loopback data bridge.
