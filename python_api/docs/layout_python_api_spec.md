# Layout Python API — contract revision 0.7

Status: implemented public-interface contract. Geometry is evaluated
analytically and interactive views use the Layout Studio browser application.

Package release: `layout-studio` 0.1.0. The contract revision is versioned
independently from the installable package.

## Conventions

- Canonical JSON contains `reference_curves`, `types`, `objects`, type-local
  `frames`, and `{kind: "object_frame", object, frame}`. Type shape and magnetic
  axis are optional; each object's beam interface defaults to its magnetic axis.
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
- JSON text rejects duplicate member names, including names spelled with
  different equivalent escapes. Optional members must be omitted rather than
  set to `null`, including `reference_curve`.
- Geometry follows revision 1 of `specifications/layout_positioning_model.tex`
  and its schema and conformance corpus. `Layout.validate()` and
  `Resolver.validate()` check structure and references; full geometric
  resolution can additionally fail on missing/ambiguous stations or numeric
  range. A successfully solved layout has a unique set of world frames.

## Public API

The declarations below are signatures, not executable code.

```python
OperationName = Literal["tx", "ty", "ts", "tt", "rx", "ry", "rs"]
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


class Resolver:                             # analytic snapshot evaluator
    __enter__(self) -> Self
    __exit__(self, *exc_info) -> None
    curve_frame(self, curve, station, *, extrapolate=True) -> Pose
    infer_station(self, curve, point) -> float
    type_frame(self, type_, frame="anchor") -> Pose
    object_frame(self, object_, frame="anchor") -> Pose
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
        self, *, curves=None, objects=None, magnetic_axis=None,
        beam_axis=None, frames=None
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
        self, name: str, type: str | Type, position: Position, **attributes
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
    plot_web(
        self, *, curves=True, objects=True, magnetic_axis=False,
        beam_axis=False, frames=False, selection=None, fit=None, show=False,
        width="100%", height=720, visibility=None, **viewer_kwargs,
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
    plot_web(self, *, selection=None, fit=None, show=False, width="100%",
             height=720, visibility=None, **viewer_kwargs) -> WebViewer
        # builds browser geometry for this curve only


class Type(OwnedValue):
    reserved_frames: frozenset[str]          # all reserved implicit names
    color: str
    shape: Box | Cylinder | None
    mechanical_center: Frame | None
    magnetic_center: Frame | None
    magnetic_length: float | None
    magnetic_curvature: float | None
    magnetic_roll: float | None
    implicit_frames: frozenset[str]          # anchor plus present feature frames
    frames: EntityMap[Frame]                 # stored named frames only

    __init__(
        self,
        *,
        color,
        shape=None,
        mechanical_center=None,
        magnetic_center=None,
        magnetic_length=None,
        magnetic_curvature=None,
        magnetic_roll=None,
        frames=None,
    )
    set(self, **changes) -> Self
    set_shape(self, shape: Box | Cylinder | None) -> Self
    remove_shape(self) -> Self
    set_magnetic_axis(
        self, *, center: Frame | None = None, length: float | None = None,
        curvature: float | None = None, roll: float | None = None
    ) -> Self
    remove_magnetic_axis(self) -> Self
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
    get_frame(self, name: str = "anchor") -> Pose


class Object(OwnedValue):
    type: str | Type
    type_name: str | None                    # read-only serialized name
    position: Position
    beam_center: Frame | None
    beam_length: float | None
    beam_curvature: float | None
    beam_roll: float | None
    effective_beam_axis: tuple[Frame, float, float, float] | None
    implicit_frames: frozenset[str]          # includes inherited beam frames

    __init__(self, *, type: str | Type, position: Position,
             beam_center=None, beam_length=None, beam_curvature=None, beam_roll=None)
    set(self, **changes) -> Self
    set_beam_axis(
        self, *, center: Frame | None = None, length: float | None = None,
        curvature: float | None = None, roll: float | None = None
    ) -> Self
    remove_beam_axis(self) -> Self
    set_type(self, type: str | Type) -> Self
    set_position(self, position: Position) -> Self
    ref(self, frame: str | Frame = "anchor") -> ObjectReference
    get_frame(self, frame: str | Frame = "anchor") -> Pose
    plot_web(self, *, magnetic_axis=False, beam_axis=False, frames=False,
             selection=None, fit=None, show=False, width="100%", height=720,
             visibility=None, **viewer_kwargs) -> WebViewer
        # builds browser geometry for this object and enabled frames only


class Frame(OwnedValue):
    reference: Reference | None
    operations: ManagedSequence[Operation]

    __init__(self, reference: Reference | Curve | Object | Frame | str | None = None, *, operations=())
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
        target: str | Frame = "anchor",
        reference_curve: str | Curve | None = None,
    ) -> Position


class Position(OwnedValue):
    reference: Frame
    target: str | Frame = "anchor"
    reference_curve: str | Curve | None = None
    reference_curve_name: str | None         # read-only serialized name
    operations: ManagedSequence[Operation]   # alias of reference.operations

    __init__(self, reference: FrameLike, *, target="anchor", reference_curve=None)
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


class LocalFrameReference(Reference):
    frame: str | Frame
    frame_name: str | None
    kind: Literal["local_frame"] = "local_frame"

    __init__(self, frame: str | Frame = "anchor")


class ObjectReference(Reference):
    kind: Literal["object_frame"]
    object: str | Object
    frame: str | Frame = "anchor"
    object_name: str | None                  # read-only serialized names
    frame_name: str | None
    __init__(self, object: str | Object, frame: str | Frame = "anchor")
```

`EntityMap` is an ordered, controlled mapping of names to live values.
`ManagedSequence` is list-like but validates every edit atomically.

## Reference-string shortcuts

Every argument typed as `ReferenceLike` accepts these forms:

| String | Meaning |
| --- | --- |
| `"world"` | The world frame; this word is reserved in shorthand syntax. |
| `"anchor"` | The same-instance anchor, for feature placements. |
| `"curve:main"` | Curve `main`, explicitly namespaced. |
| `"Q1->magnetic_exit"` | Frame `magnetic_exit` of object `Q1`. |
| `"Q1->beam_exit"` | Frame `beam_exit` of object `Q1`. |
| `"local:mechanical_center"` | The mechanical-center frame on the same object instance. |
| `"Q1->anchor"` | The implicit anchor frame of object `Q1`. |

A bare string is not accepted in a generic reference position: `"main"` could
later denote either a curve or an object, and a detached value has no layout in
which to decide. Bare names remain valid where the parameter already fixes the
namespace, such as `type="quadrupole"`, `target="magnetic_entry"`,
`reference_curve="main"`, and `get_frame("exit")`.

Shortcut parsing recognizes exact `world` and `anchor` (also legacy `center`),
non-empty `local:` and `curve:` suffixes, and exactly one non-empty
`object->frame` pair. Local references are valid only for feature placements.
Parsing is case-sensitive and input-only;
serialization always emits the canonical structured reference object. Draft
0.2 defines no escaping: use an explicit class or instance when an object or
frame name contains `->`. A curve named `world` remains addressable as
`"curve:world"`; an object named `world` as `"world->anchor"`. Operations are
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
   object's type. Implicit frames are supplied by reserved string name, but a
   magnetic name requires the type's magnetic feature, while a beam-interface
   name requires an object override or an inherited magnetic feature.
8. Deserializing a `Layout` creates a bound graph. Deserializing an individual
   entity creates a detached value. An entity's `to_dict()` omits its registry
   name; `Layout.to_dict()` supplies the name-indexed canonical dictionaries.

Local field constraints are checked immediately. Full graph validation—names,
references, dependency cycles, and completeness—runs on `Layout.validate()`,
layout serialization, evaluation, and browser viewing. This permits incremental
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
  shape's mechanical path at its exact list position. If the type has no shape,
  this path is straight, so `ts` remains defined.
- A world- or object-referenced `Position` containing `ts` requires
  `reference_curve`. Station inference searches only its finite domain. The
  chosen curve frame replaces the original reference orientation; the summed
  `ts` offset is applied there, followed by non-`ts` operations in their
  original relative order.
- Curve frame evaluation may extrapolate beyond the finite domain by straight
  tangent continuation; station inference never extrapolates.
- Positioning aligns `Position.target` with the transformed reference frame and
  derives the object anchor using the inverse local target pose.
- Positive curve angle at zero roll bends toward local −x; positive roll turns
  that bend direction toward local −y.
- `shape` is optional. When present, its `dz`, curvature, and roll define the
  mechanical swept geometry centered on `mechanical_center`; without it the type has no
  rendered surface.
- The magnetic axis is an optional type-level four-field group. The beam
  interface is an optional object-level four-field group: center, positive
  length, finite curvature, and finite roll are supplied or omitted together.
  If the object omits its group, all four effective values follow the type's
  magnetic axis dynamically. Without either group, beam frames do not exist.
- Entry and exit are evaluated at `−length/2` and `+length/2` along the effective
  feature axis. Center placements use local mechanical-path `ts` semantics except when their reference is a curve.
- `Type.implicit_frames` contains `anchor`, `mechanical_center` when a shape exists, and the present magnetic triplet.
  `Object.implicit_frames` additionally contains `beam_center`, `beam_entry`,
  and `beam_exit` when explicit or inherited. All reserved names remain forbidden
  in `Type.frames`. Type-level queries do not resolve beam frames.
- `Type.set_magnetic_axis()` updates the magnetic definition;
  `Object.set_beam_axis()` creates or updates an explicit interface. Creating an
  override from an inherited axis starts with a detached clone of that center
  and the inherited numbers, then applies the supplied changes. With no axis
  to inherit, `length` is required; center defaults to `Frame()`, curvature and
  roll to zero. `Object.remove_beam_axis()` clears the override and restores the
  magnetic fallback. Removing a last available frame is rejected while in use.
- `Object.beam_*` stores the explicit values (`None` when omitted), while
  `Object.effective_beam_axis` returns `(center, length, curvature, roll)` using
  the current fallback, or `None` if neither axis exists. A fallback frame
  retains its type owner; it is never reparented to an object.
- Canonical JSON omits absent fields; no partial groups or explicit `null` values
  are accepted. Inherited beam values are not materialized on serialization.
  Older beam groups under `types` must be moved to their objects and removed
  from the type. Readers reject the old placement of these fields.
- Draft 0.1 deliberately omits `tz` and `rz`: `ts` is a path operation, `tt` is
  tangent translation, and longitudinal rotation is `rs`; a `tz` alias would
  hide this distinction.

### Anchors and referenced feature placements

`anchor` is the base frame determined by `Position`. `mechanical_center` is an
optional `Frame` on `Type` that places its shape. It requires a shape and defaults
to an empty transformation from the anchor. A magnetic center, object beam center,
or stored named frame can likewise have a reference. Omitting it means the anchor.

Use `Frame("local:<frame>")`, `Frame(LocalFrameReference("<frame>"))`, or
`Frame(type_.frames["<frame>"])` to reference a frame on the same instance. JSON
uses `{"kind": "local_frame", "frame": "<frame>"}`. World, curve, and other object
references use their existing forms. This includes local references to the
instance's explicit or inherited beam frames. Every reference must resolve for
each actual instance, and the complete frame graph must be acyclic.

On a curve reference, summed `ts` operations select the station. On all other
feature references, operations execute sequentially and `ts` uses the type's
mechanical curvature and roll. Feature placements do not use station inference;
`reference_curve` remains specific to `Position`.

```python
type_.mechanical_center = Frame().tx(0.1)
type_.magnetic_center.reference = "local:mechanical_center"
type_.new_frame("survey", Frame("local:magnetic_exit").ty(0.02))
```

A positioning target must follow same-instance references back to the anchor.
Its inverse local pose then determines that anchor. An externally rooted target
is rejected because it cannot determine the anchor. `Type.get_frame()` likewise
requires an anchor-relative chain; use `Object.get_frame()` for external references.
Mechanical meshes use the resolved mechanical center. An inherited beam interface
aliases the resolved magnetic frames, so references and edits propagate through it.

Readers accept the former base-frame spelling `center` and normalize it to
`anchor`; writers and frame lists use `anchor`. `mechanical_center` is reserved,
including when no shape is present, as are the other implicit feature names.

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
    magnetic_curvature=0.22,
    magnetic_roll=0.0,
)
quad.new_frame("survey_mark").tx(0.4)

q1 = layout.new_object(
    "Q1",
    type=quad,
    position=Position("curve:main", target="anchor").ts(3.1).tt(0.2),
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
web = layout.plot_web(show=False)
web.set_visibility(magnetic_axis=True, beam_axis=True)
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
views, and independent curve, object, magnetic-axis, beam-axis, and stored-frame
layer visibility. The magnetic toggle covers its axis and entry/exit frames;
the beam toggle does the same for the beam interface. Magnetic, beam,
and stored-frame layers default off. Calls return command ids;
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
