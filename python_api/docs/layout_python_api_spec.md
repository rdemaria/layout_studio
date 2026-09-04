# Layout Python API — contract revision 0.4

Status: implemented public-interface contract. Geometry is evaluated
analytically; interactive 2D, native 3D, and browser views use Matplotlib, VTK,
and the Layout Studio web application, respectively.

Package release: `layout-studio` 0.1.0. The contract revision is versioned
independently from the installable package.

## Conventions

- Canonical JSON remains unchanged: `reference_curves`, `types`, `objects`,
  type-local `frames`, and `{kind: "object_frame", object, frame}`.
- Python exposes `Layout.curves` as the shorter name for `reference_curves`.
- Distances are in metres, rotations in radians, and curvature in m⁻¹.
- `Frame` means an editable symbolic transformation. `Pose` means an immutable
  evaluated 4×4 frame, either type-local or world-local.
- JSON text and locations are explicit: `from_json()` requires exactly one of
  `text=` or `filename_or_url=`. `to_json()` returns text when its destination
  is omitted (or is the built-in `str` object); an actual filename or HTTP(S)
  URL is written instead.
- References accept concise strings when their meaning is syntactically
  unambiguous; explicit reference classes remain the lossless fallback.

## Public API

The declarations below are signatures, not executable code.

```python
OperationName = Literal["tx", "ty", "ts", "tt", "rx", "ry", "rs"]
Projection2D = Literal["xy", "yx", "xz", "zx", "yz", "zy"]
ViewerMode = Literal["orbit", "pan", "select", "zoom-region"]
ViewerDirection = Literal["+x", "-x", "+y", "-y", "+z", "-z"]
RootKind = Literal["curve", "type", "object"]
SearchKind = Literal["curve", "type", "object", "frame"]
RootEntity = Curve | Type | Object
SearchEntity = RootEntity | Frame
ReferenceLike = Reference | Curve | Object | str
FrameLike = Frame | ReferenceLike


class JsonValue:
    from_json(
        cls,
        filename_or_url: PathLike[str] | str | None = None,
        text: str | bytes | bytearray | None = None,
    ) -> Self
    from_dict(cls, value: object) -> Self
    to_dict(self) -> object
    @overload
    to_json(
        self, filename_or_url: type[str] = str, *, indent: int | None = 2
    ) -> str
    @overload
    to_json(
        self,
        filename_or_url: PathLike[str] | str,
        *,
        indent: int | None = 2,
    ) -> None


class OwnedValue(JsonValue):
    name: str | None          # read-only registry name
    owner: object | None      # read-only structural owner
    layout: Layout | None     # enclosing layout, if any
    is_owned: bool
    is_bound: bool            # true when transitively attached to a Layout
    clone(self) -> Self       # deep, detached copy of the owned subtree


class Operation(JsonValue):                 # immutable
    name: OperationName
    value: float


class Segment(JsonValue):                   # immutable
    length: float
    angle: float = 0.0
    roll: float = 0.0
    __init__(self, length: float, angle: float = 0.0, roll: float = 0.0)
    # JSON: [length, angle, roll]


class Box(JsonValue):                       # immutable
    dx: float
    dy: float
    dz: float
    curvature: float = 0.0
    roll: float = 0.0
    __init__(self, dx, dy, dz, curvature=0.0, roll=0.0)
    # JSON: ["box", dx, dy, dz, curvature, roll]


class Cylinder(JsonValue):                  # immutable
    r: float
    dz: float
    curvature: float = 0.0
    roll: float = 0.0
    __init__(self, r, dz, curvature=0.0, roll=0.0)
    # JSON: ["cylinder", r, dz, curvature, roll]


class Pose:                                 # immutable
    matrix: NDArray[float]                  # read-only 4x4 matrix
    space: Literal["type_local", "world"]
    origin: NDArray[float]
    x: NDArray[float]
    y: NDArray[float]
    tangent: NDArray[float]                 # local s axis
    euler: tuple[float, float, float]        # MAD-X theta, phi, psi
    transform_point(self, xyz: ArrayLike) -> NDArray[float]


class LayoutViewer:                         # VTK-backed, returned by plot3d
    renderer: vtkRenderer
    render_window: vtkRenderWindow
    interactor: vtkRenderWindowInteractor
    selection: SearchEntity | None
    show(self) -> Self
    render(self) -> Self
    fit(self, entity=None, *, preserve_orientation=True) -> Self
    home(self, entity=None) -> Self
    reset_camera(self) -> Self
    select(self, entity=None, *, station=None) -> Self
    clear_selection(self) -> Self
    set_curves_visible(self, visible=True) -> Self
    set_objects_visible(self, visible=True) -> Self
    set_frames_visible(self, visible=True) -> Self
    set_beam_frames_visible(self, visible=True) -> Self
    screenshot(self, filename=None, *, scale=1, transparent=False)
    close(self) -> None

    # Constructor viewer kwargs include frames=None, curve_resolution=None,
    # object_resolution=None, radial_resolution=None, batch_objects=None,
    # and object_batch_size=4096. None selects adaptive behaviour. Batches are
    # also split at an internal memory budget.


class LayoutViewer2D:                       # Matplotlib-backed, returned by plot2d
    figure: matplotlib.figure.Figure
    ax: matplotlib.axes.Axes
    axes: matplotlib.axes.Axes
    canvas: matplotlib.backend_bases.FigureCanvasBase
    projection: Projection2D
    selection: SearchEntity | None
    selected: SearchEntity | None
    show(self, *, block: bool | None = None) -> Self
    draw(self) -> Self
    render(self) -> Self                    # alias of draw
    fit(self) -> Self
    reset_view(self) -> Self
    reset_camera(self) -> Self               # alias of reset_view
    select(self, entity=None, *, station=None) -> Self
    clear_selection(self) -> Self
    set_curves_visible(self, visible=True) -> Self
    set_objects_visible(self, visible=True) -> Self
    set_frames_visible(self, visible=True) -> Self
    set_beam_frames_visible(self, visible=True) -> Self
    set_grid_visible(self, visible=True) -> Self
    savefig(self, filename, **kwargs) -> Path
    screenshot(self, filename=None, **kwargs) -> Path | NDArray[np.uint8]
    close(self) -> None

    # Constructor viewer kwargs include frames=None, curve_resolution=None,
    # object_resolution=None, radial_resolution=None, batch_objects=None,
    # batch_threshold=128, and hover_interval=1/30. None selects adaptive
    # behaviour; the resolution kwargs also accept "auto".


class Resolver:                             # analytic snapshot evaluator
    __enter__(self) -> Self
    __exit__(self, *exc_info) -> None
    curve_frame(self, curve, station, *, extrapolate=True) -> Pose
    infer_station(self, curve, point) -> float
    type_frame(self, type_, frame="center") -> Pose
    object_frame(self, object_, frame="center") -> Pose
    sampled_curve(self, curve, resolution=128) -> Mapping[str, object]
    swept_object_mesh(
        self, object_, resolution=32, radial_resolution=24, *,
        include_metadata=True,
    ) -> Mapping[str, object]


class WebViewer:                            # nonblocking browser bridge
    __init__(
        self,
        layout,
        *,
        standalone_path=None,
        viewer_url=None,
        scope=None,
        selection=None,
        fit=None,
        mode: ViewerMode | None = None,
        visibility: Mapping[str, bool] | None = None,
        show=False,
        width="100%",
        height=720,
        poll_timeout=20.0,
    )
    layout: Layout
    url: str
    closed: bool
    update(self, layout=None) -> str
    set_scope(self, target=None) -> str
    select(self, target=None) -> str
    fit(self, target=None) -> str
    set_mode(self, mode: ViewerMode) -> str
    set_view(self, direction: ViewerDirection) -> str
    set_visibility(
        self, *, curves=None, objects=None, beam_frames=None, frames=None
    ) -> str
    request_layout(self) -> str
    get_event(self, timeout=0.0) -> dict[str, object] | None
    wait_ready(self, timeout=10.0) -> Self
    wait_response(self, command_id, timeout=10.0) -> dict[str, object]
    show(self) -> Self
    close(self) -> None
    __enter__(self) -> Self
    __exit__(self, *exc_info) -> None


class Layout(JsonValue):
    curves: EntityMap[Curve]                # JSON key: reference_curves
    types: EntityMap[Type]
    objects: EntityMap[Object]

    __init__(self, *, curves=None, types=None, objects=None)
    new_curve(self, name: str, **attributes) -> Curve
    add_curve(self, name: str, curve: Curve) -> Curve
    new_type(self, name: str, **attributes) -> Type
    add_type(self, name: str, type_: Type) -> Type
    new_object(
        self, name: str, type: str | Type, position: Position
    ) -> Object
    add_object(self, name: str, object_: Object) -> Object
    rename(
        self,
        entity: RootEntity | str,
        new_name: str,
        *,
        kind: RootKind | None = None,
    ) -> RootEntity
    pop(
        self, name: str, *, kind: RootKind | None = None
    ) -> RootEntity
    search(
        self,
        regexp: str | Pattern[str],
        kind: SearchKind | Iterable[SearchKind] | None = None,
    ) -> list[SearchEntity]
    __getitem__(self, key: str | tuple[RootKind, str]) -> RootEntity
    reference(self, value: str | Curve | Object | Reference) -> Reference
        # resolve a shorthand/reference in this Layout without transforming it
    validate(self) -> None
    resolver(self) -> Resolver
    plot2d(
        self,
        projection: Projection2D | str = "xy",
        *,
        curves: bool | Curve | str | Iterable[Curve | str] = True,
        objects: bool | Object | str | Iterable[Object | str] = True,
        beam_frames: bool = False,
        selection: SearchEntity | None = None,
        show: bool = True,
        figsize: tuple[float, float] = (10.0, 7.2),
        **viewer_kwargs,
    ) -> LayoutViewer2D
    plot3d(
        self,
        *,
        curves: bool | Curve | str | Iterable[Curve | str] = True,
        objects: bool | Object | str | Iterable[Object | str] = True,
        beam_frames: bool = False,
        selection: SearchEntity | None = None,
        show: bool = True,
        off_screen: bool = False,
        window_size: tuple[int, int] = (1000, 720),
        **viewer_kwargs,
    ) -> LayoutViewer
    plot_web(
        self, *, curves=True, objects=True, beam_frames=False, frames=False,
        selection=None, fit=None, show=False, width="100%", height=720,
        visibility=None, **viewer_kwargs,
    ) -> WebViewer


class Curve(OwnedValue):
    starting_frame: Frame
    color: str
    segments: ManagedSequence[Segment]
    length: float                            # read-only total length

    __init__(self, *, starting_frame: FrameLike, color, segments)
    set(self, **changes) -> Self             # atomic multi-field update
    set_starting_frame(self, frame: FrameLike) -> Self
    add_segment(
        self,
        length: float,
        angle: float = 0.0,
        roll: float = 0.0,
        *,
        index: int | None = None,
    ) -> Segment
    add_segments(
        self,
        segments: Iterable[Segment | tuple[float, float, float]],
        *,
        index: int | None = None,
    ) -> list[Segment]
    set_segment(self, index: int, *, length=UNSET, angle=UNSET, roll=UNSET)
    move_segment(self, old_index: int, new_index: int) -> Self
    remove_segment(self, index: int) -> Segment
    get_frame(self, s: float, *, extrapolate: bool = True) -> Pose
    infer_station(self, point: ArrayLike | Pose) -> float
    ref(self) -> CurveReference
    plot2d(self, projection: Projection2D | str = "xy", *, selection=None,
           show=True, figsize=(10.0, 7.2), **viewer_kwargs) -> LayoutViewer2D
        # displays this curve only; dependencies are resolved but not drawn
    plot3d(self, *, selection=None, show=True, off_screen=False,
           window_size=(1000, 720), **viewer_kwargs) -> LayoutViewer
        # displays this curve only; dependencies are resolved but not drawn
    plot_web(self, *, selection=None, fit=None, show=False, width="100%",
             height=720, visibility=None, **viewer_kwargs) -> WebViewer
        # builds browser geometry for this curve only


class Type(OwnedValue):
    color: str
    shape: Box | Cylinder
    magnetic_center: Frame
    magnetic_length: float
    frames: EntityMap[Frame]                 # stored named frames only

    __init__(
        self,
        *,
        shape,
        color,
        magnetic_center,
        magnetic_length,
        frames=None,
    )
    set(self, **changes) -> Self
    set_shape(self, shape: Box | Cylinder) -> Self
    set_magnetic_axis(
        self, *, center: Frame | None = None, length: float | None = None
    ) -> Self
    new_frame(
        self,
        name: str,
        frame: Frame | None = None,
        *,
        operations: Iterable[Operation] = (),
    ) -> Frame                              # frame and operations are exclusive
    add_frame(self, name: str, frame: Frame) -> Frame
    rename_frame(self, frame: Frame | str, new_name: str) -> Frame
    pop_frame(self, name: str) -> Frame
    get_frame(self, name: str = "center") -> Pose


class Object(OwnedValue):
    type: str | Type
    type_name: str | None                    # read-only serialized name
    position: Position

    __init__(self, *, type: str | Type, position: Position)
    set(self, **changes) -> Self
    set_type(self, type: str | Type) -> Self
    set_position(self, position: Position) -> Self
    ref(self, frame: str | Frame = "center") -> ObjectReference
    get_frame(self, frame: str | Frame = "center") -> Pose
    plot2d(self, projection: Projection2D | str = "xy", *, beam_frames=True,
           frames=True, selection=None, show=True, figsize=(10.0, 7.2),
           **viewer_kwargs) -> LayoutViewer2D
        # displays this object and its requested frames only
    plot3d(self, *, beam_frames=True, frames=True, selection=None,
           show=True, off_screen=False, window_size=(1000, 720),
           **viewer_kwargs) -> LayoutViewer
        # displays this object and its requested frames only
    plot_web(self, *, beam_frames=True, frames=True, selection=None, fit=None,
             show=False, width="100%", height=720, visibility=None,
             **viewer_kwargs) -> WebViewer
        # builds browser geometry for this object and enabled frames only


class Frame(OwnedValue):
    reference: Reference | None
    operations: ManagedSequence[Operation]

    __init__(self, reference: Reference | Curve | Object | str | None = None, *, operations=())
    tx(self, distance: float) -> Self
    ty(self, distance: float) -> Self
    ts(self, distance: float) -> Self
    tt(self, distance: float) -> Self
    rx(self, angle: float) -> Self
    ry(self, angle: float) -> Self
    rs(self, angle: float) -> Self
    set_operation(self, index: int, *, name=None, value=None) -> Self
    insert_operation(self, index: int, name: OperationName, value: float) -> Self
    move_operation(self, old_index: int, new_index: int) -> Self
    remove_operation(self, index: int) -> Operation
    clear_operations(self) -> Self
    as_position(
        self,
        *,
        target: str | Frame = "center",
        reference_curve: str | Curve | None = None,
    ) -> Position


class Position(OwnedValue):
    reference: Frame
    target: str | Frame = "center"
    reference_curve: str | Curve | None = None
    reference_curve_name: str | None         # read-only serialized name
    operations: ManagedSequence[Operation]   # alias of reference.operations

    __init__(self, reference: FrameLike, *, target="center", reference_curve=None)
    set(self, **changes) -> Self
    set_reference(self, reference: FrameLike) -> Self
    set_target(self, target: str | Frame) -> Self
    tx(self, distance: float) -> Self
    ty(self, distance: float) -> Self
    ts(self, distance: float) -> Self
    tt(self, distance: float) -> Self
    rx(self, angle: float) -> Self
    ry(self, angle: float) -> Self
    rs(self, angle: float) -> Self


class Reference(JsonValue):                 # immutable anchor
    parse(cls, text: str) -> Reference
    as_frame(self) -> Frame
    tx(self, distance: float) -> Frame
    ty(self, distance: float) -> Frame
    ts(self, distance: float) -> Frame
    tt(self, distance: float) -> Frame
    rx(self, angle: float) -> Frame
    ry(self, angle: float) -> Frame
    rs(self, angle: float) -> Frame


class WorldReference(Reference):
    kind: Literal["world"]


class CurveReference(Reference):
    kind: Literal["curve"]
    curve: str | Curve
    curve_name: str | None                   # read-only serialized name
    __init__(self, curve: str | Curve)


class ObjectReference(Reference):
    kind: Literal["object_frame"]
    object: str | Object
    frame: str | Frame = "center"
    object_name: str | None                  # read-only serialized names
    frame_name: str | None
    __init__(self, object: str | Object, frame: str | Frame = "center")
```

`EntityMap` is an ordered, controlled mapping of names to live values.
`ManagedSequence` is list-like but validates every edit atomically.

## Reference-string shortcuts

Every argument typed as `ReferenceLike` accepts these forms:

| String | Meaning |
| --- | --- |
| `"world"` | The world frame; this word is reserved in shorthand syntax. |
| `"curve:main"` | Curve `main`, explicitly namespaced. |
| `"Q1->magnetic_exit"` | Frame `magnetic_exit` of object `Q1`. |
| `"Q1->center"` | The implicit center frame of object `Q1`. |

A bare string is not accepted in a generic reference position: `"main"` could
later denote either a curve or an object, and a detached value has no layout in
which to decide. Bare names remain valid where the parameter already fixes the
namespace, such as `type="quadrupole"`, `target="magnetic_entry"`,
`reference_curve="main"`, and `get_frame("exit")`.

Shortcut parsing tests exact `world`, then a non-empty `curve:` suffix, then
exactly one non-empty `object->frame` pair. It is case-sensitive and input-only;
serialization always emits the canonical structured reference object. Draft
0.2 defines no escaping: use an explicit class or instance when an object or
frame name contains `->`. A curve named `world` remains addressable as
`"curve:world"`; an object named `world` as `"world->center"`. Operations are
not encoded in strings, so forms such as `"curve:main@3.1"` are unsupported.

## Behavioral contract

### Attachment and references

1. Directly constructed root entities are detached values. `new_*` constructs
   a bound entity; `add_*` adopts the same detached instance without copying.
2. Every mutable value has at most one structural owner. A frame in a detached
   type is owned and named but not yet bound to a layout. Reuse elsewhere
   requires `clone()`.
3. Public attributes are live validated properties. For example,
   `curve.color = "#112233"` immediately changes its enclosing layout. Every
   mutation is atomic, but no source file is rewritten until an explicit
   `to_json(filename_or_url=...)` call.
4. Names are read-only registry keys. `Layout.rename()` and
   `Type.rename_frame()` preserve identity-bound references. Removing a used
   entity raises `ReferenceInUseError`; this contract revision has no cascading
   removal.
5. Constructors accept names or instances. A same-layout instance resolves by
   identity and follows renames; a foreign-layout instance is rejected. Bound
   link properties return live instances and `*_name` exposes the JSON name.
   Detached string references may remain symbolic; unnamed instance references
   cannot be serialized.
6. `Frame.as_position()` adopts that same detached frame. It raises
   `AttachmentError` if the frame already has an owner.
7. A frame-instance `Position.target` must belong to the positioned object's
   type. A frame instance in `ObjectReference` must belong to the referenced
   object's type. Implicit frames are supplied by reserved string name.
8. Deserializing a `Layout` creates a bound graph. Deserializing an individual
   entity creates a detached value. An entity's `to_dict()` omits its registry
   name; `Layout.to_dict()` supplies the name-indexed canonical dictionaries.

Local field constraints are checked immediately. Full graph validation—names,
references, dependency cycles, and completeness—runs on `Layout.validate()`,
layout serialization, evaluation, and plotting. This permits incremental
construction in IPython.

`Layout.resolver()` creates an analytic evaluator. Each ordinary resolver
method uses a fresh session so later model edits are observed. Within
`with layout.resolver() as resolver:`, validation, dependency poses, sampled
curve data, and inferred stations are cached until the outermost context exits;
then all snapshot caches are released. Do not mutate the layout within that
snapshot or share one active resolver across threads.

### Geometry and operations

- Transform methods append in place and return `self`; methods on an immutable
  `Reference` create a new `Frame` containing the first operation.
- `tx`, `ty`, and `tt` are translations. `tt` is straight translation along
  the current tangent. `rx`, `ry`, and `rs` are rotations.
- On a curve reference, all `ts` values are summed to select the curve station
  before non-`ts` operations run. In a type-local frame, `ts` follows the
  curved type path at its exact list position.
- A world- or object-referenced `Position` containing `ts` requires
  `reference_curve`. Station inference searches only its finite domain. The
  chosen curve frame replaces the original reference orientation; the summed
  `ts` offset is applied there, followed by non-`ts` operations in their
  original relative order.
- Curve frame evaluation may extrapolate beyond the finite domain by straight
  tangent continuation; station inference never extrapolates.
- Positioning aligns `Position.target` with the transformed reference frame and
  derives the object center using the inverse local target pose.
- Positive curve angle at zero roll bends toward local −x; positive roll turns
  that bend direction toward local −y.
- Reserved implicit type frames are `center`, `magnetic_center`,
  `magnetic_entry`, and `magnetic_exit`. They are addressable but absent from
  `Type.frames`.
- Draft 0.1 deliberately omits `tz` and `rz`: `ts` is a path operation, `tt` is
  tangent translation, and longitudinal rotation is `rs`; a `tz` alias would
  hide this distinction.

### Lookup and IPython

- `layout["Q1"]` returns the live root entity when the name is unique across
  namespaces. Otherwise it raises `AmbiguousNameError`; use
  `layout["object", "Q1"]` or `layout.objects["Q1"]`.
- `search(..., kind="frame")` returns stored type frames using qualified names;
  it does not manufacture implicit frames for every object instance.
- `Layout`, `EntityMap`, and `Type.frames` provide bracket-key completion.
- `repr` and `_repr_html_` are bounded summaries and never enumerate thousands
  of segments or objects. Dynamic attributes such as `layout.Q1` are omitted.

## Example interaction

```python
layout = Layout()

main = layout.new_curve(
    "main",
    starting_frame=Frame("world").tx(1.0),
    color="#7aa2f7",
    segments=[Segment(10.0), Segment(5.0, angle=0.2, roll=0.01)],
)

quad = layout.new_type(
    "quadrupole",
    shape=Box(1.1, 0.9, 1.6, curvature=0.22),
    color="#f0a84b",
    magnetic_center=Frame(),
    magnetic_length=1.4,
)
quad.new_frame("survey_mark").tx(0.4)

q1 = layout.new_object(
    "Q1",
    type=quad,
    position=Position("curve:main", target="center").ts(3.1).tt(0.2),
)

q2 = layout.new_object(
    "Q2",
    type=quad,
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

layout.search(r"^Q", kind="object")
layout["Q1"].get_frame("magnetic_exit")
layout.plot2d("xy")
layout.plot3d()
web = layout.plot_web(show=False)
web.close()
layout.to_json(filename_or_url="layout.json")
```

## JSON I/O

`from_json()` accepts exactly one source. `text=` always means literal JSON
text and accepts `str`, `bytes`, or `bytearray`; `filename_or_url=` means a
local filesystem path or an HTTP(S) URL (read with GET). Supplying neither
source, or supplying both sources, raises `TypeError`, so a JSON-looking string
is never mistaken for a filename.

`to_json()` and `to_json(str)` return JSON text. Passing any actual `str` or
`PathLike[str]` supplies a destination and returns `None`; an HTTP(S)
destination is written with PUT. For example:

```python
text = layout.to_json()
copy = Layout.from_json(text=text)

layout.to_json(filename_or_url="layout.json")
copy = Layout.from_json(filename_or_url="layout.json")
```

For input and output, a filename or URL path ending in `.gz` selects gzip
decompression or compression automatically. Input bytes with the gzip magic
header are also decompressed, including `text=` bytes and URL responses whose
address does not end in `.gz`. Output is UTF-8 JSON; compressed HTTP PUTs set
`Content-Encoding: gzip`.

## Browser web viewer

`plot_web()` returns a nonblocking `WebViewer` controlling the existing Layout
Studio browser application. A tokenized loopback HTTP server exposes a small
wrapper, an ordered command channel, and compact/gzip-capable layout snapshots.
The wrapper establishes a nonce- and origin-validated `MessagePort`; layout
JSON is not placed in HTML, URL fragments, or query strings.

The browser protocol supports snapshot replacement/readback, selection, fit,
strict scene scope, orbit/pan/select/rectangle-zoom modes, six signed canonical
views, and curve/object/frame layer visibility. Calls return command ids;
`wait_response()` is optional when acknowledgement is required, while
after `wait_ready()`, `request_layout()` plus `wait_response()` reads back the
edited document, and
`get_event()` receives ready and selection events. Handshake retries cover a
late React listener without blocking Python. Layout, scope, and requested
visibility are installed atomically; later `update()` calls preserve viewport
camera, mode, and layer state.

`Curve.plot_web()` and `Object.plot_web()` load the complete document for
dependency resolution but enumerate, mesh, pick, bound, and fit only the
requested curve or object (including enabled frames). `set_scope()` changes
that strict computational scope. A selected `Type` or stored `Frame` maps to an
object only when its scoped instance is unique; use an explicit object or
`"object->frame"` otherwise.

Model mutation is not automatically mirrored. `update()` validates and
publishes one fresh snapshot, allowing many Python edits to serialize once.
`show()` opens a browser without blocking; inline IPython display embeds the
same wrapper. Use `close()` or a context manager to stop its server.

In a source checkout, the default asset is `webapp/build/index.html`; it must
be rebuilt with `make -C webapp standalone` after bridge source changes and
for source-checkout use. Generated bundles are not included in Python wheels.
`standalone_path=` selects a compatible local asset. `viewer_url=` selects a
protocol-1 hosted app but does not move the Python
wrapper/data server: the browser must still reach kernel-side
`127.0.0.1:<port>`. Remote kernels therefore need explicit forwarding/proxying.
The hosted application receives the complete layout over the authenticated
message channel, so it must be trusted; non-loopback hosted viewers must use
HTTPS. An iframe reconnect restores the latest Python-owned layout, scope,
visibility, mode, signed view, selection, and fit target before resuming
ordered commands. A readback that was still pending across a reconnect fails
explicitly because restoring the latest snapshot would make its historical
result ambiguous. A viewer capability should have one active browser client at
a time.

## Matplotlib 2D viewer

`plot2d(projection="xy")` returns a `LayoutViewer2D`. Projection values are
case-insensitive and comprise all six ordered pairs of distinct world axes:
`"xy"`, `"yx"`, `"xz"`, `"zx"`, `"yz"`, and `"zy"`. The first axis is
horizontal and the second vertical; labels are world X/Y/Z in metres and each
axis scales independently to fill the available plotting area. An invalid
projection raises `ValueError`.

With `show=True`, the figure is shown using the active Matplotlib backend.
With `show=False`, the complete figure and artists are built without opening a
GUI window, permitting headless inspection and export with `savefig()` or
`screenshot()`. Native Matplotlib toolbar pan and zoom remain available. An
existing Matplotlib axes can be supplied with `ax=...` for composed figures.
Where the backend supports it, hover/readout/local-axis updates use canvas
blitting with automatic full-redraw fallback. `LayoutViewer2D.savefig()`
temporarily includes those animated overlays in exported images.

For scopes of at least 128 objects, automatic mode represents each projected
object by its convex silhouette and places all objects in one collection. This
keeps drawing and interaction costs bounded while preserving object-level
selection through a vectorized bounds index. Straight extrusions use exactly
two longitudinal sections. Curved objects, cylinders, and reference curves use
adaptive tessellation budgets unless an explicit `curve_resolution`,
`object_resolution`, or `radial_resolution` is supplied. `batch_objects=False`
requests the detailed per-object representation.

Hovering shows the entity name and world pose. For curves, the nearest sampled
chord in the projection supplies a continuously interpolated station and its
pose. Left-click
selects and highlights an entity, shows its local axes and pose, and retains
the snapped curve station; clicking the same selection again clears it. The
keys `c`, `o`, and `b` toggle curve, object, and Beam-frame layers; `g` toggles
the grid; `f` and `r` fit the scoped geometry; and Escape clears selection.
Stored-frame arrows and the active local-axis triad use bounded,
viewport-relative lengths which refresh on pan, zoom, and resize. Beam planes
remain physical object geometry.

The layout view can toggle curves, objects, stored frames, and Beam entry/exit
frames. Frame layers are lazy; their adaptive default is on for small scopes
and off for large ones, and an explicit boolean is always honoured. Batched
scopes also batch enabled frames. `Curve.plot2d()` and `Object.plot2d()` use the
same strict entity scope as their 3D counterparts: upstream dependencies are
resolved, but unrelated geometry is neither drawn nor included in fitting.
For a full-layout viewer, boolean `curves=False` or `objects=False` controls
initial visibility but still constructs that layer; pass an explicit empty
scope (`curves=[]` or `objects=[]`) when the geometry should not be built.

## VTK viewer

`plot3d()` returns a `LayoutViewer`. With
`show=True` the viewer opens its native VTK interactor; `show=False` builds and
returns the scene without entering the event loop, which is convenient in
IPython and for programmatic inspection.

The VTK viewer automatically batches scopes of at least 128 objects into
actors capped at 4096 objects and an internal memory budget, with per-cell
colours and selection identities. It converts contiguous NumPy geometry to VTK
in bulk, uses static cell locators, restricts picking to interactive props, and
throttles hover work. Straight extrusions use an exact two-section mesh;
curved/radial tessellation follows a scene-size budget. Explicit
`curve_resolution`, `object_resolution`, `radial_resolution`, `batch_objects`,
and `object_batch_size` values override the count-based choices, while the
memory bound remains active.

The layout view can toggle reference curves, objects, stored frames, and Beam
entry/exit frames. Frame layers are lazy and default off only for a large
scope; explicitly enabled large frame layers are batched. It provides Y-up
trackball navigation, fit/reset, an X–Z ground grid, a world orientation triad,
hover labels, click selection, selection highlighting, a local x/y/s triad,
and a world-pose readout using MAD-X theta/phi/psi angles. `Curve.plot3d()` and
`Object.plot3d()` use strict entity scope: upstream dependencies are resolved
but unrelated geometry is not shown and does not affect camera fitting.
Stored-frame arrows and the active triad are resized from camera depth and the
renderer viewport before every frame, keeping them legible through dolly,
parallel zoom, and window resizing. Beam planes remain physical geometry.
`fit(entity=None, preserve_orientation=True)` preserves both viewing direction
and camera roll; `home(entity=None)` and `reset_camera()` restore the canonical
isometric view. Camera motion temporarily suspends depth peeling and restores
it at interaction end.

The native `show()` loop handles VTK `ExitEvent` explicitly. Closing the window
terminates the interactor, finalizes and detaches the render window, removes
observers and props, and releases scene-sized caches while a closed viewer
remains referenced by IPython. `close()` performs the same teardown and is
idempotent.

## Exceptions

```python
LayoutError
ValidationError(LayoutError, ValueError)
NameConflictError(LayoutError)
UnknownEntityError(LayoutError, LookupError)
AmbiguousNameError(LayoutError, LookupError)
AttachmentError(LayoutError)
ForeignLayoutError(AttachmentError)
DanglingReferenceError(LayoutError)
ReferenceInUseError(LayoutError)
ReferenceCycleError(LayoutError)
EvaluationError(LayoutError)
StationOutOfRangeError(EvaluationError)
NoStationSolutionError(EvaluationError)
AmbiguousStationError(EvaluationError)

WebViewerError(RuntimeError)
WebViewerAssetError(WebViewerError)
WebViewerTimeoutError(WebViewerError, TimeoutError)
```

Where applicable, exceptions carry a machine-readable `path`, for example
`objects.Q2.position.reference.frame`.
