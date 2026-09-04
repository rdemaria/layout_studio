# Layout Studio Python API

`layout-studio` is the Python counterpart of Layout Studio: it describes
piecewise circular reference curves, reusable curved component types, and
objects positioned by aligning named frames. Matplotlib, VTK, and browser
viewers provide interactive views of the same evaluated geometry.

Distances are in metres, rotations are in radians, and curvature is in
metres⁻¹.  The homogeneous pose convention is `[x y tangent origin]`.

## Install for development

```bash
python -m pip install -e '.[plot2d,viewer,test]'
```

Matplotlib and VTK are optional until a 2D or 3D view is requested. The
geometric model, JSON round trips, and Python side of the web bridge need only
NumPy. In a source checkout, build the bridge-enabled browser asset once with
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

# plot2d() returns a Matplotlib-backed LayoutViewer2D.  Projection names are
# ordered: the first letter is the horizontal axis and the second is vertical.
view2d = layout.plot2d("xz", beam_frames=True, show=False)
view2d.show()

# Curve and object plots deliberately keep the visible scene local to the
# entity; dependencies are resolved but not drawn.
main.plot2d("xy")
q1.plot2d("yz", beam_frames=True)

# plot3d() returns a LayoutViewer; show=False builds without starting the
# blocking native interactor.
viewer = layout.plot3d(beam_frames=True, show=False)
viewer.show()

main.plot3d()
q1.plot3d(beam_frames=True)

# plot_web() returns immediately. Display it in a local notebook, call show(),
# or pass show=True to open the system browser during construction.
web = q1.plot_web(show=False)
web.show()

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

## Reference shorthand

Generic references accept only unambiguous strings:

| Input | Reference |
| --- | --- |
| `"world"` | World frame |
| `"curve:main"` | Curve `main` |
| `"Q1->magnetic_exit"` | `Q1` Beam-exit frame |

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
that entity (plus the object's enabled frames). This is a computational scope,
not just selection or visibility; it does not reduce snapshot transfer size.

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

## 2D viewer controls

`plot2d(projection="xy")` accepts the case-insensitive ordered projections
`"xy"`, `"yx"`, `"xz"`, `"zx"`, `"yz"`, and `"zy"`. The first axis is
horizontal, the second is vertical, coordinates are world coordinates in
metres, and each plot axis scales independently to fill the available area.

Use the Matplotlib toolbar for native pan and zoom. Hovering reports the entity
and its pose; hovering a curve also snaps continuously along its nearest
projected chord and reports the interpolated station.
Left-click selects and highlights an entity, shows its local axes and pose, and
snaps curve selection to a station. Clicking the same selection again clears
it. Keyboard shortcuts control the layers and view:

Stored-frame arrows and the active local-axis triad remain a bounded fraction
of the viewport as the plot is zoomed or resized. Beam entry/exit planes retain
their physical dimensions.

| Key | Action |
| --- | --- |
| `c` | Reference curves |
| `o` | Objects |
| `b` | Beam entry/exit frames |
| `g` | Grid |
| `f` or `r` | Fit the scoped geometry |
| `Escape` | Clear selection |

`LayoutViewer2D` exposes its Matplotlib `figure`, `axes`/`ax`, and `canvas`,
together with `set_curves_visible()`, `set_objects_visible()`,
`set_beam_frames_visible()`, `set_frames_visible()`, `set_grid_visible()`,
`fit()`, `reset_view()`/`reset_camera()`,
`select()`, `clear_selection()`, `draw()`/`render()`, `show()`, `savefig()`,
`screenshot()`, and `close()`. Passing `show=False` creates the complete figure
without opening a GUI window, so it is suitable for notebooks, headless tests,
and programmatic export. Pass an existing Matplotlib axes as `ax=...` to draw
the projection inside a larger figure.

Large scopes use an adaptive level of detail automatically. Straight objects
keep their exact two-section extrusion, and scopes of 128 objects or more are
drawn as one collection of projected silhouettes instead of thousands of
per-object artists. Stored and Beam-frame layers are built lazily and default
off for a large scope; passing `frames=True` or `beam_frames=True` still builds
them in batched collections. `curve_resolution`, `object_resolution`, and
`radial_resolution` override automatic tessellation. `batch_objects` and
`batch_threshold` override automatic 2D batching, while `hover_interval`
controls pointer throttling.

On canvases that support it, hover labels, pose text, and local axes are
updated with blitting instead of redrawing the full scene. Backends without
working blit support fall back automatically. Repeated objects also share
immutable type-edge topology, reducing both memory and Python work. Use
`viewer.savefig()` rather than calling `viewer.figure.savefig()` directly when
animated overlays must be included.

## 3D viewer controls

The VTK viewer uses trackball-camera navigation.  Left drag orbits, middle
drag pans, and the wheel zooms.  Click an object or curve to highlight it and
show its world pose; hover shows its name.  Keyboard shortcuts toggle the
principal layers and restore the fitted camera:

| Key | Action |
| --- | --- |
| `c` | Reference curves |
| `o` | Objects |
| `b` | Beam entry/exit frames |
| `f` | Fit while preserving the current camera orientation |
| `r` | Return to the canonical isometric home view and fit |
| `Escape` | Clear selection |

The `LayoutViewer` also exposes `set_curves_visible()`,
`set_objects_visible()`, `set_frames_visible()`,
`set_beam_frames_visible()`, `fit(entity=None, preserve_orientation=True)`,
`home(entity=None)`, `select()`, `screenshot()`, and `close()` for interactive
Python use. Large scopes automatically use exact low-detail straight
extrusions, object actors capped at 4096 objects and a bounded memory budget,
bulk NumPy-to-VTK topology conversion, cell-aware picking, a scene-wide
tessellation budget, and lazy batched frame layers. Depth peeling is suspended
during camera motion and restored when the interaction ends. The automatic
choices can be overridden with
`curve_resolution`, `object_resolution`, `radial_resolution`, `batch_objects`,
and `object_batch_size`.

`show()` enters the native VTK interaction loop. Closing that window terminates
the loop, finalizes the render window, disconnects observers, and releases the
scene even if IPython retains the returned viewer in `Out[...]`. For notebook
construction or export without entering the loop, use `show=False` and call
`close()` when finished; repeated calls to `close()` are harmless.
