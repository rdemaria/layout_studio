"""Public data model for curve-referenced three-dimensional layouts.

The classes in this module deliberately separate editable symbolic data from
evaluated geometry.  Importing :mod:`layout_studio` therefore has no VTK (or
other viewer) dependency; evaluation and plotting are loaded lazily by the
small delegation methods near the end of the entity classes.
"""

from __future__ import annotations

import json
import math
import os
import re
from collections import OrderedDict
from collections.abc import (
    Callable,
    Iterable,
    Iterator,
    Mapping,
    MutableMapping,
    MutableSequence,
    Sequence,
)
from dataclasses import dataclass
from html import escape
from pathlib import Path
from re import Pattern
from typing import Any, Generic, Literal, TextIO, TypeVar, overload

import numpy as np
from numpy.typing import ArrayLike, NDArray

from .errors import (
    AmbiguousNameError,
    AttachmentError,
    DanglingReferenceError,
    ForeignLayoutError,
    NameConflictError,
    ReferenceCycleError,
    ReferenceInUseError,
    UnknownEntityError,
    ValidationError,
)

OperationName = Literal["tx", "ty", "ts", "tt", "rx", "ry", "rs"]
RootKind = Literal["curve", "type", "object"]
SearchKind = Literal["curve", "type", "object", "frame"]

_OPERATION_NAMES = frozenset(("tx", "ty", "ts", "tt", "rx", "ry", "rs"))
_ROOT_KINDS = frozenset(("curve", "type", "object"))
_SEARCH_KINDS = frozenset((*_ROOT_KINDS, "frame"))
_IMPLICIT_FRAME_NAMES = frozenset(
    ("center", "magnetic_center", "magnetic_entry", "magnetic_exit")
)
_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


class _Unset:
    def __repr__(self) -> str:
        return "UNSET"


UNSET: Any = _Unset()


def _fail(message: str, *, path: str | None = None) -> ValidationError:
    return ValidationError(message, path=path)


def _finite(value: object, label: str, *, path: str | None = None) -> float:
    """Return *value* as a finite float, rejecting booleans."""

    if isinstance(value, bool) or not isinstance(value, (int, float, np.number)):
        raise _fail(f"{label} must be a finite number", path=path)
    result = float(value)
    if not math.isfinite(result):
        raise _fail(f"{label} must be a finite number", path=path)
    return result


def _positive(value: object, label: str, *, path: str | None = None) -> float:
    result = _finite(value, label, path=path)
    if result <= 0.0:
        raise _fail(f"{label} must be positive", path=path)
    return result


def _name(value: object, label: str = "name", *, path: str | None = None) -> str:
    if not isinstance(value, str) or value == "":
        raise _fail(f"{label} must be a non-empty string", path=path)
    return value


def _color(value: object, *, path: str | None = None) -> str:
    if not isinstance(value, str) or _COLOR_RE.fullmatch(value) is None:
        raise _fail("color must be a six-digit hexadecimal value", path=path)
    return value


def _mapping(
    value: object,
    *,
    required: Iterable[str],
    optional: Iterable[str] = (),
    path: str | None = None,
) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise _fail("expected a JSON object", path=path)
    required_set = set(required)
    optional_set = set(optional)
    keys = set(value)
    missing = required_set - keys
    if missing:
        missing_text = ", ".join(sorted(repr(key) for key in missing))
        raise _fail(f"missing required field(s): {missing_text}", path=path)
    unsupported = keys - required_set - optional_set
    if unsupported:
        unsupported_text = ", ".join(sorted(repr(key) for key in unsupported))
        raise _fail(f"unsupported field(s): {unsupported_text}", path=path)
    return value


def _sequence(
    value: object, length: int, label: str, *, path: str | None = None
) -> Sequence[object]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise _fail(f"{label} must be a {length}-item array", path=path)
    if len(value) != length:
        raise _fail(f"{label} must contain exactly {length} items", path=path)
    return value


class JsonValue:
    """Mixin implementing unambiguous JSON text and file I/O."""

    __slots__ = ()

    @classmethod
    def from_json(cls, text: str | bytes) -> Any:
        """Parse one JSON value from *text*.  Strings are never treated as paths."""

        if not isinstance(text, (str, bytes, bytearray)):
            raise TypeError("text must be str or bytes")
        try:
            value = json.loads(text)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValidationError(f"invalid JSON: {exc}") from exc
        return cls.from_dict(value)

    @classmethod
    def load(cls, filename: os.PathLike[str] | str | TextIO) -> Any:
        """Read and parse a JSON value from a path or open text stream."""

        if hasattr(filename, "read"):
            text = filename.read()  # type: ignore[union-attr]
        else:
            text = Path(filename).read_text(encoding="utf-8")
        return cls.from_json(text)

    @classmethod
    def from_dict(cls, dct: object) -> Any:
        raise NotImplementedError(f"{cls.__name__}.from_dict() is not implemented")

    def to_dict(self) -> object:
        raise NotImplementedError(f"{type(self).__name__}.to_dict() is not implemented")

    def to_json(self, *, indent: int | None = 2) -> str:
        """Return canonical JSON text for this value."""

        if indent is not None and (
            isinstance(indent, bool) or not isinstance(indent, int)
        ):
            raise TypeError("indent must be an int or None")
        return json.dumps(self.to_dict(), indent=indent, allow_nan=False)

    def save(
        self,
        filename: os.PathLike[str] | str | TextIO,
        *,
        indent: int | None = 2,
    ) -> None:
        """Serialize this value to a path or open text stream."""

        text = self.to_json(indent=indent)
        if hasattr(filename, "write"):
            filename.write(text)  # type: ignore[union-attr]
        else:
            Path(filename).write_text(text, encoding="utf-8")


@dataclass(frozen=True, slots=True)
class Operation(JsonValue):
    """One immutable symbolic frame operation."""

    name: OperationName
    value: float

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or self.name not in _OPERATION_NAMES:
            raise _fail(f"unsupported operation {self.name!r}")
        object.__setattr__(self, "value", _finite(self.value, "operation value"))

    @classmethod
    def from_dict(cls, dct: object) -> Operation:
        pair = _sequence(dct, 2, "operation")
        return cls(pair[0], pair[1])  # type: ignore[arg-type]

    def to_dict(self) -> list[object]:
        return [self.name, self.value]


@dataclass(frozen=True, slots=True)
class Segment(JsonValue):
    """One immutable straight or constant-curvature curve segment."""

    length: float
    angle: float = 0.0
    roll: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "length", _positive(self.length, "segment length"))
        object.__setattr__(self, "angle", _finite(self.angle, "segment angle"))
        object.__setattr__(self, "roll", _finite(self.roll, "segment roll"))

    @classmethod
    def from_dict(cls, dct: object) -> Segment:
        values = _sequence(dct, 3, "segment")
        return cls(values[0], values[1], values[2])

    def to_dict(self) -> list[float]:
        return [self.length, self.angle, self.roll]


@dataclass(frozen=True, slots=True)
class Box(JsonValue):
    """Immutable curved box primitive; ``dz`` is centerline arc length."""

    dx: float
    dy: float
    dz: float
    curvature: float = 0.0
    roll: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "dx", _positive(self.dx, "box dx"))
        object.__setattr__(self, "dy", _positive(self.dy, "box dy"))
        object.__setattr__(self, "dz", _positive(self.dz, "box dz"))
        object.__setattr__(self, "curvature", _finite(self.curvature, "box curvature"))
        object.__setattr__(self, "roll", _finite(self.roll, "box roll"))

    @classmethod
    def from_dict(cls, dct: object) -> Box:
        values = _sequence(dct, 6, "box shape")
        if values[0] != "box":
            raise _fail("box shape must start with 'box'")
        return cls(*values[1:])

    def to_dict(self) -> list[object]:
        return ["box", self.dx, self.dy, self.dz, self.curvature, self.roll]


@dataclass(frozen=True, slots=True)
class Cylinder(JsonValue):
    """Immutable curved cylinder primitive; ``dz`` is centerline arc length."""

    r: float
    dz: float
    curvature: float = 0.0
    roll: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "r", _positive(self.r, "cylinder radius"))
        object.__setattr__(self, "dz", _positive(self.dz, "cylinder dz"))
        object.__setattr__(
            self, "curvature", _finite(self.curvature, "cylinder curvature")
        )
        object.__setattr__(self, "roll", _finite(self.roll, "cylinder roll"))

    @classmethod
    def from_dict(cls, dct: object) -> Cylinder:
        values = _sequence(dct, 5, "cylinder shape")
        if values[0] != "cylinder":
            raise _fail("cylinder shape must start with 'cylinder'")
        return cls(*values[1:])

    def to_dict(self) -> list[object]:
        return ["cylinder", self.r, self.dz, self.curvature, self.roll]


class Pose:
    """An immutable evaluated homogeneous frame.

    Matrix columns are local ``x``, ``y`` and tangent/``s`` axes; the fourth
    column is the origin.  The owned NumPy array is copied and marked read-only.
    """

    __slots__ = ("_matrix", "_space")

    def __init__(
        self,
        matrix: ArrayLike,
        space: Literal["type_local", "world"],
    ) -> None:
        array = np.array(matrix, dtype=float, copy=True)
        if array.shape != (4, 4):
            raise _fail("pose matrix must have shape (4, 4)")
        if not np.isfinite(array).all():
            raise _fail("pose matrix must contain only finite values")
        if space not in ("type_local", "world"):
            raise _fail("pose space must be 'type_local' or 'world'")
        array.setflags(write=False)
        self._matrix = array
        self._space = space

    @property
    def matrix(self) -> NDArray[np.float64]:
        # Returning a view prevents callers from re-enabling writes with
        # ``setflags(write=True)`` on the array that owns the memory.
        result = self._matrix.view()
        result.setflags(write=False)
        return result

    @property
    def space(self) -> Literal["type_local", "world"]:
        return self._space  # type: ignore[return-value]

    @property
    def origin(self) -> NDArray[np.float64]:
        return self._matrix[:3, 3]

    @property
    def x(self) -> NDArray[np.float64]:
        return self._matrix[:3, 0]

    @property
    def y(self) -> NDArray[np.float64]:
        return self._matrix[:3, 1]

    @property
    def tangent(self) -> NDArray[np.float64]:
        return self._matrix[:3, 2]

    @property
    def euler(self) -> tuple[float, float, float]:
        """Return deterministic MAD-X ``(theta, phi, psi)`` survey angles."""

        tangent = np.asarray(self.tangent, dtype=float)
        tangent = tangent / np.linalg.norm(tangent)
        x_axis = np.asarray(self.x, dtype=float)
        x_axis = x_axis - np.dot(x_axis, tangent) * tangent
        x_norm = np.linalg.norm(x_axis)
        if x_norm <= np.finfo(float).eps:
            raise _fail("pose has a degenerate x axis")
        x_axis = x_axis / x_norm
        y_axis = np.cross(tangent, x_axis)
        horizontal = math.hypot(float(tangent[0]), float(tangent[2]))
        phi = math.atan2(float(tangent[1]), horizontal)
        if horizontal > 1e-12:
            theta = math.atan2(float(tangent[0]), float(tangent[2]))
            psi = math.atan2(float(x_axis[1]), float(y_axis[1]))
        else:
            theta = 0.0
            psi = math.atan2(float(-y_axis[0]), float(x_axis[0]))
        return theta, phi, psi

    def transform_point(self, xyz: ArrayLike) -> NDArray[np.float64]:
        """Transform one point or an array whose final dimension is three."""

        points = np.asarray(xyz, dtype=float)
        if points.ndim == 0 or points.shape[-1] != 3:
            raise ValueError("xyz must have a final dimension of length 3")
        return points @ self._matrix[:3, :3].T + self._matrix[:3, 3]

    def __repr__(self) -> str:
        origin = ", ".join(f"{value:.6g}" for value in self.origin)
        return f"Pose(space={self.space!r}, origin=({origin}))"


class OwnedValue(JsonValue):
    """Base for mutable values with one structural owner."""

    def __init__(self) -> None:
        self._owner: object | None = None
        self._name: str | None = None

    @property
    def name(self) -> str | None:
        return self._name

    @property
    def owner(self) -> object | None:
        return self._owner

    @property
    def layout(self) -> Layout | None:
        current: object | None = self._owner
        seen: set[int] = set()
        while current is not None:
            if isinstance(current, Layout):
                return current
            marker = id(current)
            if marker in seen:
                return None
            seen.add(marker)
            current = getattr(current, "owner", None)
        return None

    @property
    def is_owned(self) -> bool:
        return self._owner is not None

    @property
    def is_bound(self) -> bool:
        return self.layout is not None

    def _attach(self, owner: object, name: str | None = None) -> None:
        if self._owner is not None and self._owner is not owner:
            raise AttachmentError("value already has a structural owner")
        if self._owner is owner and self._name != name:
            raise AttachmentError("value is already attached in another registry slot")
        self._owner = owner
        self._name = name

    def _detach(self, owner: object) -> None:
        if self._owner is owner:
            self._owner = None
            self._name = None

    def clone(self) -> Any:
        """Return a deep, detached copy of this owned subtree."""

        return self._clone_detached()

    def _clone_detached(self) -> OwnedValue:
        raise NotImplementedError


class Reference(JsonValue):
    """Immutable symbolic anchor for a :class:`Frame`."""

    __slots__ = ()
    kind: str

    @classmethod
    def parse(cls, text: str) -> Reference:
        """Parse one exact, unambiguous reference shorthand."""

        if not isinstance(text, str):
            raise TypeError("reference shorthand must be a string")
        if text == "world":
            return WorldReference()
        if (
            text.startswith("curve:")
            and text[len("curve:") :] != ""
            and "@" not in text[len("curve:") :]
        ):
            return CurveReference(text[len("curve:") :])
        if text.count("->") == 1:
            object_name, frame_name = text.split("->")
            if object_name and frame_name:
                return ObjectReference(object_name, frame_name)
        raise _fail(
            "invalid reference shorthand; use 'world', 'curve:<name>', or '<object>-><frame>'"
        )

    @classmethod
    def from_dict(cls, dct: object) -> Reference:
        mapping = _mapping(
            dct, required=("kind",), optional=("curve", "object", "frame")
        )
        kind = mapping["kind"]
        if kind == "world":
            _mapping(dct, required=("kind",))
            result: Reference = WorldReference()
        elif kind == "curve":
            exact = _mapping(dct, required=("kind", "curve"))
            result = CurveReference(_name(exact["curve"], "curve name"))
        elif kind == "object_frame":
            exact = _mapping(dct, required=("kind", "object", "frame"))
            result = ObjectReference(
                _name(exact["object"], "object name"),
                _name(exact["frame"], "frame name"),
            )
        else:
            raise _fail(f"unsupported reference kind {kind!r}")
        if cls is not Reference and not isinstance(result, cls):
            raise _fail(f"expected a {cls.__name__} reference")
        return result

    def _copy_for(self, context: Frame | None) -> Reference:
        raise NotImplementedError

    def as_frame(self) -> Frame:
        return Frame(self)

    def _first(self, name: OperationName, value: float) -> Frame:
        return Frame(self, operations=(Operation(name, value),))

    def tx(self, distance: float) -> Frame:
        return self._first("tx", distance)

    def ty(self, distance: float) -> Frame:
        return self._first("ty", distance)

    def ts(self, distance: float) -> Frame:
        return self._first("ts", distance)

    def tt(self, distance: float) -> Frame:
        return self._first("tt", distance)

    def rx(self, angle: float) -> Frame:
        return self._first("rx", angle)

    def ry(self, angle: float) -> Frame:
        return self._first("ry", angle)

    def rs(self, angle: float) -> Frame:
        return self._first("rs", angle)


class WorldReference(Reference):
    """The world frame anchor."""

    __slots__ = ()
    kind: Literal["world"] = "world"

    def _copy_for(self, context: Frame | None) -> WorldReference:
        return WorldReference()

    def to_dict(self) -> dict[str, object]:
        return {"kind": "world"}

    def __eq__(self, other: object) -> bool:
        return isinstance(other, WorldReference)

    def __hash__(self) -> int:
        return hash("world")

    def __repr__(self) -> str:
        return "WorldReference()"


def _link_name(value: object) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, OwnedValue):
        return value.name
    return None


def _clone_link(value: object) -> object:
    if isinstance(value, OwnedValue) and value.name is not None:
        return value.name
    return value


class CurveReference(Reference):
    """Anchor on a named or live reference curve."""

    __slots__ = ("_context", "_curve")
    kind: Literal["curve"] = "curve"

    def __init__(self, curve: str | Curve) -> None:
        if isinstance(curve, str):
            _name(curve, "curve name")
        elif not isinstance(curve, Curve):
            raise TypeError("curve must be a curve name or Curve")
        self._curve = curve
        self._context: Frame | None = None

    @property
    def curve(self) -> str | Curve:
        if isinstance(self._curve, str) and self._context is not None:
            layout = self._context.layout
            if layout is not None and self._curve in layout.curves:
                return layout.curves[self._curve]
        return self._curve

    @property
    def curve_name(self) -> str | None:
        return _link_name(self._curve)

    def _copy_for(self, context: Frame | None) -> CurveReference:
        result = CurveReference(self._curve)
        result._context = context
        return result

    def to_dict(self) -> dict[str, object]:
        name = self.curve_name
        if name is None:
            raise DanglingReferenceError(
                "an unnamed curve reference cannot be serialized"
            )
        return {"kind": "curve", "curve": name}

    def __eq__(self, other: object) -> bool:
        return isinstance(other, CurveReference) and self._curve == other._curve

    def __hash__(self) -> int:
        return hash(
            ("curve", self._curve if isinstance(self._curve, str) else id(self._curve))
        )

    def __repr__(self) -> str:
        return f"CurveReference({self.curve_name!r})"


class ObjectReference(Reference):
    """Anchor on a complete frame belonging to an object instance."""

    __slots__ = ("_context", "_frame", "_object")
    kind: Literal["object_frame"] = "object_frame"

    def __init__(self, object: str | Object, frame: str | Frame = "center") -> None:
        if isinstance(object, str):
            _name(object, "object name")
        elif not isinstance(object, Object):
            raise TypeError("object must be an object name or Object")
        if isinstance(frame, str):
            _name(frame, "frame name")
        elif not isinstance(frame, Frame):
            raise TypeError("frame must be a frame name or Frame")
        self._object = object
        self._frame = frame
        self._context: Frame | None = None

    @property
    def object(self) -> str | Object:
        if isinstance(self._object, str) and self._context is not None:
            layout = self._context.layout
            if layout is not None and self._object in layout.objects:
                return layout.objects[self._object]
        return self._object

    @property
    def frame(self) -> str | Frame:
        if isinstance(self._frame, str):
            obj = self.object
            if isinstance(obj, Object):
                type_value = obj.type
                if isinstance(type_value, Type) and self._frame in type_value.frames:
                    return type_value.frames[self._frame]
        return self._frame

    @property
    def object_name(self) -> str | None:
        return _link_name(self._object)

    @property
    def frame_name(self) -> str | None:
        return _link_name(self._frame)

    def _copy_for(self, context: Frame | None) -> ObjectReference:
        result = ObjectReference(self._object, self._frame)
        result._context = context
        return result

    def to_dict(self) -> dict[str, object]:
        object_name = self.object_name
        frame_name = self.frame_name
        if object_name is None or frame_name is None:
            raise DanglingReferenceError(
                "unnamed object/frame references cannot be serialized"
            )
        return {"kind": "object_frame", "object": object_name, "frame": frame_name}

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, ObjectReference)
            and self._object == other._object
            and self._frame == other._frame
        )

    def __hash__(self) -> int:
        object_key = self._object if isinstance(self._object, str) else id(self._object)
        frame_key = self._frame if isinstance(self._frame, str) else id(self._frame)
        return hash(("object_frame", object_key, frame_key))

    def __repr__(self) -> str:
        return f"ObjectReference({self.object_name!r}, {self.frame_name!r})"


T = TypeVar("T")


class ManagedSequence(MutableSequence[T], Generic[T]):
    """A list-like sequence that validates each edit before committing it."""

    def __init__(
        self,
        values: Iterable[object],
        *,
        coerce: Callable[[object], T],
        validate: Callable[[list[T]], None] | None = None,
    ) -> None:
        self._coerce = coerce
        self._validate = validate
        prepared = [coerce(value) for value in values]
        self._check(prepared)
        self._values = prepared

    def _check(self, values: list[T]) -> None:
        if self._validate is not None:
            self._validate(values)

    def _commit(self, values: list[T]) -> None:
        self._check(values)
        self._values = values

    @overload
    def __getitem__(self, index: int) -> T: ...

    @overload
    def __getitem__(self, index: slice) -> list[T]: ...

    def __getitem__(self, index: int | slice) -> T | list[T]:
        return self._values[index]

    def __setitem__(self, index: int | slice, value: object) -> None:
        candidate = list(self._values)
        if isinstance(index, slice):
            if isinstance(value, (str, bytes, bytearray)) or not isinstance(
                value, Iterable
            ):
                raise TypeError("slice assignment requires an iterable")
            candidate[index] = [self._coerce(item) for item in value]
        else:
            candidate[index] = self._coerce(value)
        self._commit(candidate)

    def __delitem__(self, index: int | slice) -> None:
        candidate = list(self._values)
        del candidate[index]
        self._commit(candidate)

    def __len__(self) -> int:
        return len(self._values)

    def insert(self, index: int, value: object) -> None:
        candidate = list(self._values)
        candidate.insert(index, self._coerce(value))
        self._commit(candidate)

    def extend(self, values: Iterable[object]) -> None:
        additions = [self._coerce(value) for value in values]
        self._commit([*self._values, *additions])

    def clear(self) -> None:
        self._commit([])

    def reverse(self) -> None:
        self._commit(list(reversed(self._values)))

    def __iter__(self) -> Iterator[T]:
        return iter(self._values)

    def __repr__(self) -> str:
        return f"ManagedSequence({self._values!r})"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, ManagedSequence):
            return self._values == other._values
        if isinstance(other, Sequence):
            return self._values == list(other)
        return False


E = TypeVar("E", bound=OwnedValue)


class EntityMap(MutableMapping[str, E], Generic[E]):
    """Ordered mapping whose owner controls attachment and removal."""

    def __init__(self, owner: Layout | Type, kind: str) -> None:
        self._owner = owner
        self._kind = kind
        self._data: OrderedDict[str, E] = OrderedDict()

    def __getitem__(self, key: str) -> E:
        try:
            return self._data[key]
        except KeyError as exc:
            raise UnknownEntityError(
                f"unknown {self._kind} {key!r}", path=f"{self._kind}s.{key}"
            ) from exc

    def __setitem__(self, key: str, value: E) -> None:
        self._owner._entity_map_set(self._kind, key, value)

    def __delitem__(self, key: str) -> None:
        self._owner._entity_map_delete(self._kind, key)

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def get(self, key: str, default: Any = None) -> E | Any:
        """Return a value or *default* without changing domain lookup errors."""

        return self._data.get(key, default)

    def setdefault(self, key: str, default: E | None = None) -> E:
        if key in self._data:
            return self._data[key]
        if default is None:
            raise TypeError(f"a {self._kind} default value is required")
        self[key] = default
        return default

    def pop(self, key: str, default: Any = UNSET) -> E | Any:
        if key not in self._data:
            if default is not UNSET:
                return default
            return self[key]  # raises the domain-specific lookup exception
        value = self._data[key]
        del self[key]
        return value

    def popitem(self) -> tuple[str, E]:
        if not self._data:
            raise KeyError("popitem(): mapping is empty")
        key = next(reversed(self._data))
        value = self._data[key]
        del self[key]
        return key, value

    def update(self, *args: object, **kwargs: E) -> None:
        """Update transactionally, restoring attachment state on any failure."""

        incoming: OrderedDict[str, E] = OrderedDict()
        incoming.update(*args, **kwargs)  # type: ignore[arg-type]
        snapshot = OrderedDict(self._data)
        try:
            for key, value in incoming.items():
                self[key] = value
        except Exception:
            self._restore(snapshot)
            raise

    def clear(self) -> None:
        """Remove all entries atomically (and with normal in-use checks)."""

        snapshot = OrderedDict(self._data)
        try:
            for key in tuple(self._data):
                del self[key]
        except Exception:
            self._restore(snapshot)
            raise

    def _restore(self, snapshot: OrderedDict[str, E]) -> None:
        originals = {id(value) for value in snapshot.values()}
        for value in tuple(self._data.values()):
            if id(value) not in originals and value.owner is self._owner:
                value._detach(self._owner)
        for key, value in snapshot.items():
            if value.owner is None:
                value._attach(self._owner, key)
            elif value.owner is self._owner:
                value._name = key
        self._data = snapshot

    def _insert(self, key: str, value: E) -> None:
        self._data[key] = value

    def _remove(self, key: str) -> E:
        return self._data.pop(key)

    def _rename(self, old: str, new: str) -> None:
        rebuilt: OrderedDict[str, E] = OrderedDict()
        for key, value in self._data.items():
            rebuilt[new if key == old else key] = value
        self._data = rebuilt

    def __contains__(self, key: object) -> bool:
        return key in self._data

    def __repr__(self) -> str:
        keys = list(self._data)
        shown = keys[:6]
        suffix = ", ..." if len(keys) > 6 else ""
        return f"EntityMap({', '.join(repr(key) for key in shown)}{suffix})"

    def _ipython_key_completions_(self) -> list[str]:
        return list(self._data)

    def _repr_html_(self) -> str:
        keys = list(self._data)
        shown = keys[:20]
        items = "".join(f"<li><code>{escape(key)}</code></li>" for key in shown)
        more = (
            f"<li>… {len(keys) - len(shown)} more</li>"
            if len(keys) > len(shown)
            else ""
        )
        return f"<div><strong>{escape(self._kind.title())} map</strong><ul>{items}{more}</ul></div>"


def _coerce_operation(value: object) -> Operation:
    if isinstance(value, Operation):
        return value
    return Operation.from_dict(value)


def _coerce_segment(value: object) -> Segment:
    if isinstance(value, Segment):
        return value
    return Segment.from_dict(value)


def _coerce_reference(
    value: object | None, *, context: Frame | None = None
) -> Reference | None:
    if value is None:
        return None
    if isinstance(value, Reference):
        return value._copy_for(context)
    if isinstance(value, Curve):
        return CurveReference(value)._copy_for(context)
    if isinstance(value, Object):
        return ObjectReference(value)._copy_for(context)
    if isinstance(value, str):
        return Reference.parse(value)._copy_for(context)
    raise TypeError(
        "reference must be a Reference, Curve, Object, shorthand string, or None"
    )


class Frame(OwnedValue):
    """An editable symbolic transformation relative to an optional anchor."""

    def __init__(
        self,
        reference: Reference | Curve | Object | str | None = None,
        *,
        operations: Iterable[Operation | Sequence[object]] = (),
    ) -> None:
        super().__init__()
        self._reference = _coerce_reference(reference, context=self)
        self._operations = ManagedSequence(operations, coerce=_coerce_operation)

    @property
    def reference(self) -> Reference | None:
        return self._reference

    @reference.setter
    def reference(self, value: Reference | Curve | Object | str | None) -> None:
        candidate = _coerce_reference(value, context=self)
        _check_link_for_owner(candidate, self)
        self._reference = candidate

    @property
    def operations(self) -> ManagedSequence[Operation]:
        return self._operations

    @operations.setter
    def operations(self, values: Iterable[Operation | Sequence[object]]) -> None:
        self._operations = ManagedSequence(values, coerce=_coerce_operation)

    @classmethod
    def from_dict(cls, dct: object) -> Frame:
        if not isinstance(dct, Mapping):
            raise _fail("frame must be a JSON object")
        if "reference" in dct:
            mapping = _mapping(dct, required=("reference", "transformation"))
            reference = Reference.from_dict(mapping["reference"])
        else:
            mapping = _mapping(dct, required=("transformation",))
            reference = None
        operations = mapping["transformation"]
        if isinstance(operations, (str, bytes, bytearray)) or not isinstance(
            operations, Sequence
        ):
            raise _fail("transformation must be an array")
        return cls(
            reference, operations=(Operation.from_dict(value) for value in operations)
        )

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {}
        if self.reference is not None:
            result["reference"] = self.reference.to_dict()
        result["transformation"] = [
            operation.to_dict() for operation in self.operations
        ]
        return result

    def _append(self, name: OperationName, value: float) -> Frame:
        self.operations.append(Operation(name, value))
        return self

    def tx(self, distance: float) -> Frame:
        return self._append("tx", distance)

    def ty(self, distance: float) -> Frame:
        return self._append("ty", distance)

    def ts(self, distance: float) -> Frame:
        return self._append("ts", distance)

    def tt(self, distance: float) -> Frame:
        return self._append("tt", distance)

    def rx(self, angle: float) -> Frame:
        return self._append("rx", angle)

    def ry(self, angle: float) -> Frame:
        return self._append("ry", angle)

    def rs(self, angle: float) -> Frame:
        return self._append("rs", angle)

    def set_operation(
        self,
        index: int,
        *,
        name: OperationName | None = None,
        value: float | None = None,
    ) -> Frame:
        current = self.operations[index]
        self.operations[index] = Operation(
            current.name if name is None else name,
            current.value if value is None else value,
        )
        return self

    def insert_operation(self, index: int, name: OperationName, value: float) -> Frame:
        self.operations.insert(index, Operation(name, value))
        return self

    def move_operation(self, old_index: int, new_index: int) -> Frame:
        values = list(self.operations)
        operation = values.pop(old_index)
        values.insert(new_index, operation)
        self.operations = values
        return self

    def remove_operation(self, index: int) -> Operation:
        return self.operations.pop(index)

    def clear_operations(self) -> Frame:
        self.operations.clear()
        return self

    def as_position(
        self,
        *,
        target: str | Frame = "center",
        reference_curve: str | Curve | None = None,
    ) -> Position:
        if self.is_owned:
            raise AttachmentError(
                "an owned frame cannot be adopted by a Position; clone it first"
            )
        return Position(self, target=target, reference_curve=reference_curve)

    def _clone_detached(self) -> Frame:
        reference: object | None
        if isinstance(self.reference, CurveReference):
            reference = CurveReference(_clone_link(self.reference._curve))  # type: ignore[arg-type]
        elif isinstance(self.reference, ObjectReference):
            reference = ObjectReference(
                _clone_link(self.reference._object),  # type: ignore[arg-type]
                _clone_link(self.reference._frame),  # type: ignore[arg-type]
            )
        elif isinstance(self.reference, WorldReference):
            reference = WorldReference()
        else:
            reference = None
        return Frame(reference, operations=self.operations)

    def __repr__(self) -> str:
        ref = self.reference.kind if self.reference is not None else None
        return f"Frame(reference={ref!r}, operations={len(self.operations)})"


def _check_link_for_owner(link: object, owner: OwnedValue) -> None:
    layout = owner.layout
    if layout is not None:
        layout._check_foreign_links(link)


class Position(OwnedValue):
    """Placement of an object target at a transformed reference frame."""

    def __init__(
        self,
        reference: Frame | Reference | Curve | Object | str,
        *,
        target: str | Frame = "center",
        reference_curve: str | Curve | None = None,
    ) -> None:
        super().__init__()
        frame = self._prepare_reference(reference)
        target_value = self._prepare_target(target)
        curve_value = self._prepare_reference_curve(reference_curve)
        frame._attach(self)
        self._reference = frame
        self._target = target_value
        self._reference_curve = curve_value

    @staticmethod
    def _prepare_reference(value: Frame | Reference | Curve | Object | str) -> Frame:
        if isinstance(value, Frame):
            if value.is_owned:
                raise AttachmentError("position reference frame already has an owner")
            return value
        if isinstance(value, (Reference, Curve, Object, str)):
            return Frame(value)
        raise TypeError("position reference must be a Frame or ReferenceLike value")

    @staticmethod
    def _prepare_target(value: str | Frame) -> str | Frame:
        if isinstance(value, str):
            return _name(value, "target frame name")
        if isinstance(value, Frame):
            return value
        raise TypeError("target must be a frame name or Frame")

    @staticmethod
    def _prepare_reference_curve(value: str | Curve | None) -> str | Curve | None:
        if value is None:
            return None
        if isinstance(value, str):
            return _name(value, "reference curve name")
        if isinstance(value, Curve):
            return value
        raise TypeError("reference_curve must be a curve name, Curve, or None")

    @property
    def reference(self) -> Frame:
        return self._reference

    @reference.setter
    def reference(self, value: Frame | Reference | Curve | Object | str) -> None:
        self.set_reference(value)

    @property
    def target(self) -> str | Frame:
        if isinstance(self._target, str):
            obj = self.owner
            if isinstance(obj, Object):
                type_value = obj.type
                if isinstance(type_value, Type) and self._target in type_value.frames:
                    return type_value.frames[self._target]
        return self._target

    @target.setter
    def target(self, value: str | Frame) -> None:
        self.set_target(value)

    @property
    def target_name(self) -> str | None:
        return _link_name(self._target)

    @property
    def reference_curve(self) -> str | Curve | None:
        if isinstance(self._reference_curve, str):
            layout = self.layout
            if layout is not None and self._reference_curve in layout.curves:
                return layout.curves[self._reference_curve]
        return self._reference_curve

    @reference_curve.setter
    def reference_curve(self, value: str | Curve | None) -> None:
        candidate = self._prepare_reference_curve(value)
        _check_link_for_owner(candidate, self)
        self._reference_curve = candidate

    @property
    def reference_curve_name(self) -> str | None:
        return _link_name(self._reference_curve)

    @property
    def operations(self) -> ManagedSequence[Operation]:
        return self.reference.operations

    @operations.setter
    def operations(self, values: Iterable[Operation | Sequence[object]]) -> None:
        self.reference.operations = values

    @classmethod
    def from_dict(cls, dct: object) -> Position:
        mapping = _mapping(
            dct,
            required=("target", "reference", "transformation"),
            optional=("reference_curve",),
        )
        operations = mapping["transformation"]
        if isinstance(operations, (str, bytes, bytearray)) or not isinstance(
            operations, Sequence
        ):
            raise _fail("position transformation must be an array")
        frame = Frame(
            Reference.from_dict(mapping["reference"]),
            operations=(Operation.from_dict(value) for value in operations),
        )
        reference_curve = mapping.get("reference_curve")
        if reference_curve is not None:
            reference_curve = _name(reference_curve, "reference curve name")
        return cls(
            frame,
            target=_name(mapping["target"], "target frame name"),
            reference_curve=reference_curve,
        )

    def to_dict(self) -> dict[str, object]:
        target_name = self.target_name
        if target_name is None:
            raise DanglingReferenceError("an unnamed target frame cannot be serialized")
        if self.reference.reference is None:
            raise DanglingReferenceError("a position reference cannot be empty")
        result: dict[str, object] = {
            "target": target_name,
            "reference": self.reference.reference.to_dict(),
        }
        if self.reference_curve_name is not None and any(
            op.name == "ts" for op in self.operations
        ):
            result["reference_curve"] = self.reference_curve_name
        result["transformation"] = [
            operation.to_dict() for operation in self.operations
        ]
        return result

    def set(self, **changes: object) -> Position:
        allowed = {"reference", "target", "reference_curve", "operations"}
        unexpected = set(changes) - allowed
        if unexpected:
            raise TypeError(
                f"unexpected Position field(s): {', '.join(sorted(unexpected))}"
            )
        reference_change = changes.get("reference", self.reference)
        new_reference = (
            self.reference
            if reference_change is self.reference
            else self._prepare_reference(reference_change)  # type: ignore[arg-type]
        )
        new_target = (
            self._prepare_target(changes["target"])  # type: ignore[arg-type]
            if "target" in changes
            else self._target
        )
        new_curve = (
            self._prepare_reference_curve(changes["reference_curve"])  # type: ignore[arg-type]
            if "reference_curve" in changes
            else self._reference_curve
        )
        new_operations = (
            ManagedSequence(changes["operations"], coerce=_coerce_operation)  # type: ignore[arg-type]
            if "operations" in changes
            else None
        )
        for link in (new_reference.reference, new_target, new_curve):
            _check_link_for_owner(link, self)
        if new_reference is not self.reference:
            self.reference._detach(self)
            new_reference._attach(self)
            self._reference = new_reference
        self._target = new_target
        self._reference_curve = new_curve
        if new_operations is not None:
            self._reference._operations = new_operations
        return self

    def set_reference(
        self, reference: Frame | Reference | Curve | Object | str
    ) -> Position:
        return self.set(reference=reference)

    def set_target(self, target: str | Frame) -> Position:
        return self.set(target=target)

    def _append(self, name: OperationName, value: float) -> Position:
        self.operations.append(Operation(name, value))
        return self

    def tx(self, distance: float) -> Position:
        return self._append("tx", distance)

    def ty(self, distance: float) -> Position:
        return self._append("ty", distance)

    def ts(self, distance: float) -> Position:
        return self._append("ts", distance)

    def tt(self, distance: float) -> Position:
        return self._append("tt", distance)

    def rx(self, angle: float) -> Position:
        return self._append("rx", angle)

    def ry(self, angle: float) -> Position:
        return self._append("ry", angle)

    def rs(self, angle: float) -> Position:
        return self._append("rs", angle)

    def _clone_detached(self) -> Position:
        target = _clone_link(self._target)
        reference_curve = _clone_link(self._reference_curve)
        return Position(
            self.reference.clone(),
            target=target,  # type: ignore[arg-type]
            reference_curve=reference_curve,  # type: ignore[arg-type]
        )

    def __repr__(self) -> str:
        return (
            f"Position(target={self.target_name!r}, operations={len(self.operations)})"
        )


class Curve(OwnedValue):
    """A colored piecewise straight/circular reference curve."""

    def __init__(
        self,
        *,
        starting_frame: Frame | Reference | Curve | Object | str,
        color: str,
        segments: Iterable[Segment | Sequence[object]],
    ) -> None:
        super().__init__()
        frame = self._prepare_starting_frame(starting_frame)
        color_value = _color(color)
        segment_values = ManagedSequence(
            segments,
            coerce=_coerce_segment,
            validate=self._validate_segments,
        )
        frame._attach(self)
        self._starting_frame = frame
        self._color = color_value
        self._segments = segment_values

    @staticmethod
    def _prepare_starting_frame(
        value: Frame | Reference | Curve | Object | str,
    ) -> Frame:
        if isinstance(value, Frame):
            if value.is_owned:
                raise AttachmentError("starting frame already has an owner")
            return value
        if isinstance(value, (Reference, Curve, Object, str)):
            return Frame(value)
        raise TypeError("starting_frame must be a Frame or ReferenceLike value")

    @staticmethod
    def _validate_segments(values: list[Segment]) -> None:
        if not values:
            raise _fail("a curve must contain at least one segment")

    @property
    def starting_frame(self) -> Frame:
        return self._starting_frame

    @starting_frame.setter
    def starting_frame(self, value: Frame | Reference | Curve | Object | str) -> None:
        self.set_starting_frame(value)

    @property
    def color(self) -> str:
        return self._color

    @color.setter
    def color(self, value: str) -> None:
        self._color = _color(value)

    @property
    def segments(self) -> ManagedSequence[Segment]:
        return self._segments

    @segments.setter
    def segments(self, values: Iterable[Segment | Sequence[object]]) -> None:
        self._segments = ManagedSequence(
            values, coerce=_coerce_segment, validate=self._validate_segments
        )

    @property
    def length(self) -> float:
        return math.fsum(segment.length for segment in self.segments)

    @classmethod
    def from_dict(cls, dct: object) -> Curve:
        mapping = _mapping(dct, required=("color", "starting_frame", "segments"))
        segments = mapping["segments"]
        if isinstance(segments, (str, bytes, bytearray)) or not isinstance(
            segments, Sequence
        ):
            raise _fail("segments must be an array")
        return cls(
            starting_frame=Frame.from_dict(mapping["starting_frame"]),
            color=mapping["color"],  # type: ignore[arg-type]
            segments=(Segment.from_dict(value) for value in segments),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "color": self.color,
            "starting_frame": self.starting_frame.to_dict(),
            "segments": [segment.to_dict() for segment in self.segments],
        }

    def set(self, **changes: object) -> Curve:
        allowed = {"starting_frame", "color", "segments"}
        unexpected = set(changes) - allowed
        if unexpected:
            raise TypeError(
                f"unexpected Curve field(s): {', '.join(sorted(unexpected))}"
            )
        frame_change = changes.get("starting_frame", self.starting_frame)
        frame = (
            self.starting_frame
            if frame_change is self.starting_frame
            else self._prepare_starting_frame(frame_change)  # type: ignore[arg-type]
        )
        color_value = _color(changes["color"]) if "color" in changes else self.color
        segment_values = (
            ManagedSequence(
                changes["segments"],  # type: ignore[arg-type]
                coerce=_coerce_segment,
                validate=self._validate_segments,
            )
            if "segments" in changes
            else self.segments
        )
        _check_link_for_owner(frame.reference, self)
        if frame is not self.starting_frame:
            self.starting_frame._detach(self)
            frame._attach(self)
            self._starting_frame = frame
        self._color = color_value
        self._segments = segment_values
        return self

    def set_starting_frame(
        self, frame: Frame | Reference | Curve | Object | str
    ) -> Curve:
        return self.set(starting_frame=frame)

    def add_segment(
        self,
        length: float,
        angle: float = 0.0,
        roll: float = 0.0,
        *,
        index: int | None = None,
    ) -> Segment:
        segment = Segment(length, angle, roll)
        if index is None:
            self.segments.append(segment)
        else:
            self.segments.insert(index, segment)
        return segment

    def add_segments(
        self,
        segments: Iterable[Segment | tuple[float, float, float]],
        *,
        index: int | None = None,
    ) -> list[Segment]:
        prepared = [_coerce_segment(segment) for segment in segments]
        if index is None:
            self.segments.extend(prepared)
        else:
            candidate = list(self.segments)
            candidate[index:index] = prepared
            self.segments = candidate
        return prepared

    def set_segment(
        self,
        index: int,
        *,
        length: float | Any = UNSET,
        angle: float | Any = UNSET,
        roll: float | Any = UNSET,
    ) -> Curve:
        old = self.segments[index]
        self.segments[index] = Segment(
            old.length if length is UNSET else length,
            old.angle if angle is UNSET else angle,
            old.roll if roll is UNSET else roll,
        )
        return self

    def move_segment(self, old_index: int, new_index: int) -> Curve:
        values = list(self.segments)
        segment = values.pop(old_index)
        values.insert(new_index, segment)
        self.segments = values
        return self

    def remove_segment(self, index: int) -> Segment:
        return self.segments.pop(index)

    def get_frame(self, s: float, *, extrapolate: bool = True) -> Pose:
        layout = _require_bound(self)
        from .resolver import Resolver

        return Resolver(layout).curve_frame(
            self, _finite(s, "station"), extrapolate=extrapolate
        )

    def infer_station(self, point: ArrayLike | Pose) -> float:
        layout = _require_bound(self)
        from .resolver import Resolver

        return float(Resolver(layout).infer_station(self, point))

    def ref(self) -> CurveReference:
        return CurveReference(self)

    def plot3d(
        self,
        *,
        selection: object | None = None,
        show: bool = True,
        off_screen: bool = False,
        window_size: tuple[int, int] = (1000, 720),
        **viewer_kwargs: object,
    ) -> Any:
        layout = _require_bound(self)
        from .viewer import LayoutViewer

        return LayoutViewer(
            layout,
            curves=[self],
            objects=[],
            selection=selection,
            show=show,
            off_screen=off_screen,
            window_size=window_size,
            **viewer_kwargs,
        )

    plot3D = plot3d

    def plot2d(
        self,
        projection: str = "xy",
        *,
        selection: object | None = None,
        show: bool = True,
        figsize: tuple[float, float] = (10.0, 7.2),
        **viewer_kwargs: object,
    ) -> Any:
        layout = _require_bound(self)
        from .viewer2d import LayoutViewer2D

        return LayoutViewer2D(
            layout,
            projection=projection,
            curves=[self],
            objects=[],
            selection=selection,
            show=show,
            figsize=figsize,
            **viewer_kwargs,
        )

    def _clone_detached(self) -> Curve:
        return Curve(
            starting_frame=self.starting_frame.clone(),
            color=self.color,
            segments=self.segments,
        )

    def __repr__(self) -> str:
        name = f"name={self.name!r}, " if self.name is not None else ""
        return f"Curve({name}length={self.length:.6g}, segments={len(self.segments)})"


def _coerce_shape(value: object) -> Box | Cylinder:
    if isinstance(value, (Box, Cylinder)):
        return value
    if (
        isinstance(value, Sequence)
        and not isinstance(value, (str, bytes, bytearray))
        and value
    ):
        if value[0] == "box":
            return Box.from_dict(value)
        if value[0] == "cylinder":
            return Cylinder.from_dict(value)
    raise _fail("shape must be a Box, Cylinder, or canonical shape array")


class Type(OwnedValue):
    """Reusable object geometry and type-local frames."""

    implicit_frames = _IMPLICIT_FRAME_NAMES

    def __init__(
        self,
        *,
        shape: Box | Cylinder | Sequence[object],
        color: str,
        magnetic_center: Frame,
        magnetic_length: float,
        frames: Mapping[str, Frame] | None = None,
    ) -> None:
        super().__init__()
        shape_value = _coerce_shape(shape)
        color_value = _color(color)
        center = self._prepare_local_frame(magnetic_center, "magnetic_center")
        length_value = _positive(magnetic_length, "magnetic length")
        if frames is not None and not isinstance(frames, Mapping):
            raise TypeError("frames must be a mapping of names to Frame instances")
        frame_values = list((frames or {}).items())
        for frame_name, frame in frame_values:
            self._check_frame_name(frame_name)
            self._prepare_local_frame(frame, f"frame {frame_name!r}")
        all_frames = [center, *(frame for _, frame in frame_values)]
        if len({id(frame) for frame in all_frames}) != len(all_frames):
            raise AttachmentError(
                "each type-local frame must be a distinct detached instance"
            )
        center._attach(self)
        self._shape = shape_value
        self._color = color_value
        self._magnetic_center = center
        self._magnetic_length = length_value
        self._frames: EntityMap[Frame] = EntityMap(self, "frame")
        for frame_name, frame in frame_values:
            frame._attach(self, frame_name)
            self._frames._insert(frame_name, frame)

    @staticmethod
    def _prepare_local_frame(value: object, label: str) -> Frame:
        if not isinstance(value, Frame):
            raise TypeError(f"{label} must be a Frame")
        if value.is_owned:
            raise AttachmentError(f"{label} already has an owner")
        if value.reference is not None:
            raise _fail(f"{label} is type-local and cannot have an explicit reference")
        return value

    @staticmethod
    def _check_frame_name(value: object) -> str:
        result = _name(value, "frame name")
        if result in _IMPLICIT_FRAME_NAMES:
            raise NameConflictError(f"{result!r} is a reserved implicit frame name")
        return result

    @property
    def shape(self) -> Box | Cylinder:
        return self._shape

    @shape.setter
    def shape(self, value: Box | Cylinder | Sequence[object]) -> None:
        self._shape = _coerce_shape(value)

    @property
    def color(self) -> str:
        return self._color

    @color.setter
    def color(self, value: str) -> None:
        self._color = _color(value)

    @property
    def magnetic_center(self) -> Frame:
        return self._magnetic_center

    @magnetic_center.setter
    def magnetic_center(self, value: Frame) -> None:
        self.set(magnetic_center=value)

    @property
    def magnetic_length(self) -> float:
        return self._magnetic_length

    @magnetic_length.setter
    def magnetic_length(self, value: float) -> None:
        self._magnetic_length = _positive(value, "magnetic length")

    @property
    def frames(self) -> EntityMap[Frame]:
        return self._frames

    @classmethod
    def from_dict(cls, dct: object) -> Type:
        mapping = _mapping(
            dct,
            required=("shape", "color", "magnetic_center", "magnetic_length", "frames"),
        )
        frames = mapping["frames"]
        if not isinstance(frames, Mapping):
            raise _fail("frames must be a JSON object")
        return cls(
            shape=_coerce_shape(mapping["shape"]),
            color=mapping["color"],  # type: ignore[arg-type]
            magnetic_center=Frame.from_dict(mapping["magnetic_center"]),
            magnetic_length=mapping["magnetic_length"],  # type: ignore[arg-type]
            frames={
                _name(frame_name, "frame name"): Frame.from_dict(frame_value)
                for frame_name, frame_value in frames.items()
            },
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "shape": self.shape.to_dict(),
            "color": self.color,
            "magnetic_center": self.magnetic_center.to_dict(),
            "magnetic_length": self.magnetic_length,
            "frames": {name: frame.to_dict() for name, frame in self.frames.items()},
        }

    def set(self, **changes: object) -> Type:
        allowed = {"shape", "color", "magnetic_center", "magnetic_length"}
        unexpected = set(changes) - allowed
        if unexpected:
            raise TypeError(
                f"unexpected Type field(s): {', '.join(sorted(unexpected))}"
            )
        shape = _coerce_shape(changes["shape"]) if "shape" in changes else self.shape
        color = _color(changes["color"]) if "color" in changes else self.color
        center_change = changes.get("magnetic_center", self.magnetic_center)
        center = (
            self.magnetic_center
            if center_change is self.magnetic_center
            else self._prepare_local_frame(center_change, "magnetic_center")
        )
        length = (
            _positive(changes["magnetic_length"], "magnetic length")
            if "magnetic_length" in changes
            else self.magnetic_length
        )
        if center is not self.magnetic_center:
            self.magnetic_center._detach(self)
            center._attach(self)
            self._magnetic_center = center
        self._shape = shape
        self._color = color
        self._magnetic_length = length
        return self

    def set_shape(self, shape: Box | Cylinder) -> Type:
        return self.set(shape=shape)

    def set_magnetic_axis(
        self,
        *,
        center: Frame | None = None,
        length: float | None = None,
    ) -> Type:
        changes: dict[str, object] = {}
        if center is not None:
            changes["magnetic_center"] = center
        if length is not None:
            changes["magnetic_length"] = length
        return self.set(**changes)

    def new_frame(
        self,
        name: str,
        frame: Frame | None = None,
        *,
        operations: Iterable[Operation] = (),
    ) -> Frame:
        operation_values = tuple(operations)
        if frame is not None and operation_values:
            raise TypeError("frame and operations are mutually exclusive")
        value = Frame(operations=operation_values) if frame is None else frame
        return self.add_frame(name, value)

    def add_frame(self, name: str, frame: Frame) -> Frame:
        frame_name = self._check_frame_name(name)
        if frame_name in self.frames:
            raise NameConflictError(f"frame {frame_name!r} already exists")
        self._prepare_local_frame(frame, f"frame {frame_name!r}")
        frame._attach(self, frame_name)
        self.frames._insert(frame_name, frame)
        return frame

    def rename_frame(self, frame: Frame | str, new_name: str) -> Frame:
        new_name = self._check_frame_name(new_name)
        if isinstance(frame, str):
            old_name = frame
            value = self.frames[old_name]
        elif (
            isinstance(frame, Frame) and frame.owner is self and frame.name is not None
        ):
            old_name = frame.name
            value = frame
        else:
            raise UnknownEntityError("frame does not belong to this type")
        if new_name == old_name:
            return value
        if new_name in self.frames:
            raise NameConflictError(f"frame {new_name!r} already exists")
        layout = self.layout
        if layout is not None:
            layout._rewrite_frame_name(self, old_name, new_name)
        self.frames._rename(old_name, new_name)
        value._name = new_name
        return value

    def pop_frame(self, name: str) -> Frame:
        value = self.frames[name]
        layout = self.layout
        if layout is not None:
            layout._ensure_frame_not_in_use(self, value)
        self.frames._remove(name)
        value._detach(self)
        return value

    def _entity_map_set(self, kind: str, name: str, value: OwnedValue) -> None:
        if kind != "frame":
            raise TypeError("Type only owns frame maps")
        frame_name = self._check_frame_name(name)
        if not isinstance(value, Frame):
            raise TypeError("frame map values must be Frame instances")
        old = self.frames._data.get(frame_name)
        if old is value:
            return
        self._prepare_local_frame(value, f"frame {frame_name!r}")
        if old is not None and self.layout is not None:
            self.layout._ensure_frame_not_in_use(self, old)
        if old is not None:
            old._detach(self)
        value._attach(self, frame_name)
        self.frames._insert(frame_name, value)

    def _entity_map_delete(self, kind: str, name: str) -> None:
        if kind != "frame":
            raise TypeError("Type only owns frame maps")
        self.pop_frame(name)

    def get_frame(self, name: str | Frame = "center") -> Pose:
        layout = _require_bound(self)
        from .resolver import Resolver

        return Resolver(layout).type_frame(self, name)

    def _clone_detached(self) -> Type:
        return Type(
            shape=self.shape,
            color=self.color,
            magnetic_center=self.magnetic_center.clone(),
            magnetic_length=self.magnetic_length,
            frames={name: frame.clone() for name, frame in self.frames.items()},
        )

    def __repr__(self) -> str:
        name = f"name={self.name!r}, " if self.name is not None else ""
        return (
            f"Type({name}shape={type(self.shape).__name__}, frames={len(self.frames)})"
        )

    def _ipython_key_completions_(self) -> list[str]:
        return [*sorted(_IMPLICIT_FRAME_NAMES), *self.frames]


class Object(OwnedValue):
    """One positioned instance of a reusable :class:`Type`."""

    def __init__(self, *, type: str | Type, position: Position) -> None:
        super().__init__()
        type_value = self._prepare_type(type)
        if not isinstance(position, Position):
            raise TypeError("position must be a Position")
        if position.is_owned:
            raise AttachmentError("position already has an owner")
        position._attach(self)
        self._type = type_value
        self._position = position

    @staticmethod
    def _prepare_type(value: str | Type) -> str | Type:
        if isinstance(value, str):
            return _name(value, "type name")
        if isinstance(value, Type):
            return value
        raise TypeError("type must be a type name or Type")

    @property
    def type(self) -> str | Type:
        if isinstance(self._type, str):
            layout = self.layout
            if layout is not None and self._type in layout.types:
                return layout.types[self._type]
        return self._type

    @type.setter
    def type(self, value: str | Type) -> None:
        self.set_type(value)

    @property
    def type_name(self) -> str | None:
        return _link_name(self._type)

    @property
    def position(self) -> Position:
        return self._position

    @position.setter
    def position(self, value: Position) -> None:
        self.set_position(value)

    @classmethod
    def from_dict(cls, dct: object) -> Object:
        mapping = _mapping(dct, required=("type", "position"))
        return cls(
            type=_name(mapping["type"], "type name"),
            position=Position.from_dict(mapping["position"]),
        )

    def to_dict(self) -> dict[str, object]:
        type_name = self.type_name
        if type_name is None:
            raise DanglingReferenceError(
                "an unnamed type reference cannot be serialized"
            )
        return {"type": type_name, "position": self.position.to_dict()}

    def set(self, **changes: object) -> Object:
        allowed = {"type", "position"}
        unexpected = set(changes) - allowed
        if unexpected:
            raise TypeError(
                f"unexpected Object field(s): {', '.join(sorted(unexpected))}"
            )
        type_value = (
            self._prepare_type(changes["type"])  # type: ignore[arg-type]
            if "type" in changes
            else self._type
        )
        position = changes.get("position", self.position)
        if not isinstance(position, Position):
            raise TypeError("position must be a Position")
        if position is not self.position and position.is_owned:
            raise AttachmentError("position already has an owner")
        _check_link_for_owner(type_value, self)
        if position is not self.position:
            if self.layout is not None:
                self.layout._check_foreign_links(position)
            self.position._detach(self)
            position._attach(self)
            self._position = position
        self._type = type_value
        return self

    def set_type(self, type: str | Type) -> Object:
        return self.set(type=type)

    def set_position(self, position: Position) -> Object:
        return self.set(position=position)

    def ref(self, frame: str | Frame = "center") -> ObjectReference:
        return ObjectReference(self, frame)

    def get_frame(self, frame: str | Frame = "center") -> Pose:
        layout = _require_bound(self)
        from .resolver import Resolver

        return Resolver(layout).object_frame(self, frame)

    def plot3d(
        self,
        *,
        beam_frames: bool = True,
        frames: bool = True,
        selection: object | None = None,
        show: bool = True,
        off_screen: bool = False,
        window_size: tuple[int, int] = (1000, 720),
        **viewer_kwargs: object,
    ) -> Any:
        layout = _require_bound(self)
        from .viewer import LayoutViewer

        return LayoutViewer(
            layout,
            curves=[],
            objects=[self],
            beam_frames=beam_frames,
            frames=frames,
            selection=selection,
            show=show,
            off_screen=off_screen,
            window_size=window_size,
            **viewer_kwargs,
        )

    plot3D = plot3d

    def plot2d(
        self,
        projection: str = "xy",
        *,
        beam_frames: bool = True,
        frames: bool = True,
        selection: object | None = None,
        show: bool = True,
        figsize: tuple[float, float] = (10.0, 7.2),
        **viewer_kwargs: object,
    ) -> Any:
        layout = _require_bound(self)
        from .viewer2d import LayoutViewer2D

        return LayoutViewer2D(
            layout,
            projection=projection,
            curves=[],
            objects=[self],
            beam_frames=beam_frames,
            frames=frames,
            selection=selection,
            show=show,
            figsize=figsize,
            **viewer_kwargs,
        )

    def _clone_detached(self) -> Object:
        return Object(
            type=_clone_link(self._type),  # type: ignore[arg-type]
            position=self.position.clone(),
        )

    def __repr__(self) -> str:
        name = f"name={self.name!r}, " if self.name is not None else ""
        return f"Object({name}type={self.type_name!r})"


RootEntity = Curve | Type | Object
SearchEntity = RootEntity | Frame


def _require_bound(value: OwnedValue) -> Layout:
    layout = value.layout
    if layout is None:
        raise AttachmentError(
            f"{type(value).__name__} must belong to a Layout before it can be evaluated or plotted"
        )
    return layout


class Layout(JsonValue):
    """The root registry and validation boundary for a complete layout graph."""

    def __init__(
        self,
        *,
        curves: Mapping[str, Curve] | None = None,
        types: Mapping[str, Type] | None = None,
        objects: Mapping[str, Object] | None = None,
    ) -> None:
        self._curves: EntityMap[Curve] = EntityMap(self, "curve")
        self._types: EntityMap[Type] = EntityMap(self, "type")
        self._objects: EntityMap[Object] = EntityMap(self, "object")
        try:
            for name, curve in (curves or {}).items():
                self.add_curve(name, curve)
            for name, type_value in (types or {}).items():
                self.add_type(name, type_value)
            for name, object_value in (objects or {}).items():
                self.add_object(name, object_value)
        except Exception:
            for mapping in (self._curves, self._types, self._objects):
                for value in mapping._data.values():
                    value._detach(self)
                mapping._data.clear()
            raise

    @property
    def curves(self) -> EntityMap[Curve]:
        return self._curves

    @property
    def reference_curves(self) -> EntityMap[Curve]:
        """Canonical-JSON spelling of :attr:`curves` (the same live map)."""

        return self._curves

    @property
    def types(self) -> EntityMap[Type]:
        return self._types

    @property
    def objects(self) -> EntityMap[Object]:
        return self._objects

    @classmethod
    def from_dict(cls, dct: object) -> Layout:
        mapping = _mapping(dct, required=("reference_curves", "types", "objects"))
        curves = mapping["reference_curves"]
        types = mapping["types"]
        objects = mapping["objects"]
        for label, value in (
            ("reference_curves", curves),
            ("types", types),
            ("objects", objects),
        ):
            if not isinstance(value, Mapping):
                raise _fail(f"{label} must be a JSON object", path=label)
        result = cls(
            curves={
                _name(
                    name, "curve name", path=f"reference_curves.{name}"
                ): Curve.from_dict(value)
                for name, value in curves.items()  # type: ignore[union-attr]
            },
            types={
                _name(name, "type name", path=f"types.{name}"): Type.from_dict(value)
                for name, value in types.items()  # type: ignore[union-attr]
            },
            objects={
                _name(name, "object name", path=f"objects.{name}"): Object.from_dict(
                    value
                )
                for name, value in objects.items()  # type: ignore[union-attr]
            },
        )
        result.validate()
        return result

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {
            "reference_curves": {
                name: curve.to_dict() for name, curve in self.curves.items()
            },
            "types": {
                name: type_value.to_dict() for name, type_value in self.types.items()
            },
            "objects": {
                name: object_value.to_dict()
                for name, object_value in self.objects.items()
            },
        }

    def new_curve(self, name: str, **attributes: object) -> Curve:
        name = self._require_available_name("curve", name)
        return self.add_curve(name, Curve(**attributes))  # type: ignore[arg-type]

    def add_curve(self, name: str, curve: Curve) -> Curve:
        self._add_root("curve", name, curve)
        return curve

    def new_type(self, name: str, **attributes: object) -> Type:
        name = self._require_available_name("type", name)
        return self.add_type(name, Type(**attributes))  # type: ignore[arg-type]

    def add_type(self, name: str, type_: Type) -> Type:
        self._add_root("type", name, type_)
        return type_

    def new_object(self, name: str, type: str | Type, position: Position) -> Object:
        name = self._require_available_name("object", name)
        return self.add_object(name, Object(type=type, position=position))

    def add_object(self, name: str, object_: Object) -> Object:
        self._add_root("object", name, object_)
        return object_

    def _map_for(self, kind: RootKind) -> EntityMap[Any]:
        if kind == "curve":
            return self.curves
        if kind == "type":
            return self.types
        if kind == "object":
            return self.objects
        raise ValueError(f"unknown root kind {kind!r}")

    @staticmethod
    def _class_for(kind: RootKind) -> type[OwnedValue]:
        return {"curve": Curve, "type": Type, "object": Object}[kind]

    def _require_available_name(self, kind: RootKind, name: str) -> str:
        result = _name(name, f"{kind} name")
        if result in self._map_for(kind):
            raise NameConflictError(f"{kind} {result!r} already exists")
        return result

    def _add_root(self, kind: RootKind, name: str, value: OwnedValue) -> None:
        name = _name(name, f"{kind} name")
        expected = self._class_for(kind)
        if not isinstance(value, expected):
            raise TypeError(f"{kind} map values must be {expected.__name__} instances")
        mapping = self._map_for(kind)
        if name in mapping:
            raise NameConflictError(f"{kind} {name!r} already exists")
        if value.is_owned:
            if value.layout is not None and value.layout is not self:
                raise ForeignLayoutError(f"{kind} belongs to another layout")
            raise AttachmentError(f"{kind} already has a structural owner")
        self._check_foreign_links(value)
        value._attach(self, name)
        mapping._insert(name, value)

    def _entity_map_set(self, kind: str, name: str, value: OwnedValue) -> None:
        if kind not in _ROOT_KINDS:
            raise TypeError(f"unknown root map kind {kind!r}")
        root_kind: RootKind = kind  # type: ignore[assignment]
        name = _name(name, f"{kind} name")
        expected = self._class_for(root_kind)
        if not isinstance(value, expected):
            raise TypeError(f"{kind} map values must be {expected.__name__} instances")
        mapping = self._map_for(root_kind)
        old = mapping._data.get(name)
        if old is value:
            return
        if value.is_owned:
            if value.layout is not None and value.layout is not self:
                raise ForeignLayoutError(f"{kind} belongs to another layout")
            raise AttachmentError(f"{kind} already has a structural owner")
        self._check_foreign_links(value)
        if old is not None:
            self._ensure_root_not_in_use(root_kind, old)
            old._detach(self)
        value._attach(self, name)
        mapping._insert(name, value)

    def _entity_map_delete(self, kind: str, name: str) -> None:
        if kind not in _ROOT_KINDS:
            raise TypeError(f"unknown root map kind {kind!r}")
        self.pop(name, kind=kind)  # type: ignore[arg-type]

    def _select_root(
        self,
        entity: RootEntity | str,
        kind: RootKind | None,
    ) -> tuple[RootKind, str, RootEntity]:
        if kind is not None and kind not in _ROOT_KINDS:
            raise ValueError(f"invalid root kind {kind!r}")
        if isinstance(entity, (Curve, Type, Object)):
            inferred: RootKind = (
                "curve"
                if isinstance(entity, Curve)
                else "type"
                if isinstance(entity, Type)
                else "object"
            )
            if kind is not None and kind != inferred:
                raise UnknownEntityError(f"entity is not a {kind}")
            if entity.owner is not self or entity.name is None:
                raise UnknownEntityError("entity does not belong to this layout")
            return inferred, entity.name, entity
        if not isinstance(entity, str):
            raise TypeError("entity must be a root entity or name")
        if kind is not None:
            value = self._map_for(kind)[entity]
            return kind, entity, value
        matches: list[tuple[RootKind, RootEntity]] = []
        for candidate_kind in ("curve", "type", "object"):
            mapping = self._map_for(candidate_kind)
            if entity in mapping:
                matches.append((candidate_kind, mapping[entity]))
        if not matches:
            raise UnknownEntityError(f"unknown entity {entity!r}")
        if len(matches) > 1:
            raise AmbiguousNameError(
                f"name {entity!r} exists in multiple namespaces; specify kind"
            )
        found_kind, value = matches[0]
        return found_kind, entity, value

    def rename(
        self,
        entity: RootEntity | str,
        new_name: str,
        *,
        kind: RootKind | None = None,
    ) -> RootEntity:
        found_kind, old_name, value = self._select_root(entity, kind)
        new_name = _name(new_name, f"{found_kind} name")
        if new_name == old_name:
            return value
        mapping = self._map_for(found_kind)
        if new_name in mapping:
            raise NameConflictError(f"{found_kind} {new_name!r} already exists")
        self._rewrite_root_name(found_kind, value, old_name, new_name)
        mapping._rename(old_name, new_name)
        value._name = new_name
        return value

    def pop(self, name: str, *, kind: RootKind | None = None) -> RootEntity:
        found_kind, old_name, value = self._select_root(name, kind)
        self._ensure_root_not_in_use(found_kind, value)
        self._map_for(found_kind)._remove(old_name)
        value._detach(self)
        return value

    def search(
        self,
        regexp: str | Pattern[str],
        kind: SearchKind | Iterable[SearchKind] | None = None,
    ) -> list[SearchEntity]:
        pattern = re.compile(regexp) if isinstance(regexp, str) else regexp
        if not hasattr(pattern, "search"):
            raise TypeError("regexp must be a string or compiled regular expression")
        if kind is None:
            kinds: tuple[SearchKind, ...] = ("curve", "type", "object", "frame")
        elif isinstance(kind, str):
            kinds = (kind,)  # type: ignore[assignment]
        else:
            kinds = tuple(kind)
        invalid = [value for value in kinds if value not in _SEARCH_KINDS]
        if invalid:
            raise ValueError(f"invalid search kind {invalid[0]!r}")
        result: list[SearchEntity] = []
        for candidate_kind in kinds:
            if candidate_kind == "frame":
                for type_name, type_value in self.types.items():
                    for frame_name, frame in type_value.frames.items():
                        if pattern.search(f"{type_name}.{frame_name}"):
                            result.append(frame)
            else:
                for name, value in self._map_for(candidate_kind).items():  # type: ignore[arg-type]
                    if pattern.search(name):
                        result.append(value)
        return result

    @overload
    def __getitem__(self, key: str) -> RootEntity: ...

    @overload
    def __getitem__(self, key: tuple[RootKind, str]) -> RootEntity: ...

    def __getitem__(self, key: str | tuple[RootKind, str]) -> RootEntity:
        if isinstance(key, tuple):
            if len(key) != 2:
                raise KeyError(key)
            kind, name = key
            if kind not in _ROOT_KINDS:
                raise KeyError(key)
            return self._map_for(kind)[name]
        return self._select_root(key, None)[2]

    def reference(self, value: str | Curve | Object | Reference) -> Reference:
        if isinstance(value, str):
            reference = Reference.parse(value)
        elif isinstance(value, Curve):
            reference = CurveReference(value)
        elif isinstance(value, Object):
            reference = ObjectReference(value)
        elif isinstance(value, Reference):
            reference = value
        else:
            raise TypeError("value must be a shorthand, Curve, Object, or Reference")
        try:
            if isinstance(reference, WorldReference):
                return WorldReference()
            if isinstance(reference, CurveReference):
                curve = self._resolve_curve(reference._curve, "reference.curve")
                return CurveReference(curve)
            if isinstance(reference, ObjectReference):
                obj = self._resolve_object(reference._object, "reference.object")
                frame = self._resolve_object_frame(
                    obj, reference._frame, "reference.frame"
                )
                return ObjectReference(obj, frame)
        except DanglingReferenceError as exc:
            raise UnknownEntityError(str(exc), path=exc.path) from exc
        raise AssertionError("unknown reference subclass")

    def _resolve_curve(self, value: str | Curve, path: str) -> Curve:
        if isinstance(value, str):
            if value not in self.curves:
                raise DanglingReferenceError(f"unknown curve {value!r}", path=path)
            return self.curves[value]
        if value.layout is not None and value.layout is not self:
            raise ForeignLayoutError(
                "curve reference belongs to another layout", path=path
            )
        if (
            value.owner is not self
            or value.name is None
            or self.curves._data.get(value.name) is not value
        ):
            raise DanglingReferenceError(
                "curve reference is not registered in this layout", path=path
            )
        return value

    def _resolve_type(self, value: str | Type, path: str) -> Type:
        if isinstance(value, str):
            if value not in self.types:
                raise DanglingReferenceError(f"unknown type {value!r}", path=path)
            return self.types[value]
        if value.layout is not None and value.layout is not self:
            raise ForeignLayoutError(
                "type reference belongs to another layout", path=path
            )
        if (
            value.owner is not self
            or value.name is None
            or self.types._data.get(value.name) is not value
        ):
            raise DanglingReferenceError(
                "type reference is not registered in this layout", path=path
            )
        return value

    def _resolve_object(self, value: str | Object, path: str) -> Object:
        if isinstance(value, str):
            if value not in self.objects:
                raise DanglingReferenceError(f"unknown object {value!r}", path=path)
            return self.objects[value]
        if value.layout is not None and value.layout is not self:
            raise ForeignLayoutError(
                "object reference belongs to another layout", path=path
            )
        if (
            value.owner is not self
            or value.name is None
            or self.objects._data.get(value.name) is not value
        ):
            raise DanglingReferenceError(
                "object reference is not registered in this layout", path=path
            )
        return value

    def _resolve_object_frame(
        self, obj: Object, value: str | Frame, path: str
    ) -> str | Frame:
        type_value = self._resolve_type(obj._type, f"objects.{obj.name}.type")
        if isinstance(value, str):
            if value in _IMPLICIT_FRAME_NAMES:
                return value
            if value not in type_value.frames:
                raise DanglingReferenceError(
                    f"type {type_value.name!r} has no frame {value!r}", path=path
                )
            return type_value.frames[value]
        if value.layout is not None and value.layout is not self:
            raise ForeignLayoutError(
                "frame reference belongs to another layout", path=path
            )
        if value.owner is not type_value or value.name is None:
            raise DanglingReferenceError(
                "frame reference does not belong to the referenced object's type",
                path=path,
            )
        return value

    def _validate_reference(self, reference: Reference, path: str) -> RootEntity | None:
        if isinstance(reference, WorldReference):
            return None
        if isinstance(reference, CurveReference):
            return self._resolve_curve(reference._curve, f"{path}.curve")
        if isinstance(reference, ObjectReference):
            obj = self._resolve_object(reference._object, f"{path}.object")
            self._resolve_object_frame(obj, reference._frame, f"{path}.frame")
            return obj
        raise ValidationError("unsupported reference object", path=path)

    def _validate_frame(
        self,
        frame: Frame,
        path: str,
        *,
        require_reference: bool,
        type_local: bool = False,
    ) -> RootEntity | None:
        if type_local:
            if frame.reference is not None:
                raise ValidationError(
                    "type-local frame cannot have a reference", path=f"{path}.reference"
                )
            return None
        if frame.reference is None:
            if require_reference:
                raise DanglingReferenceError(
                    "frame requires a reference", path=f"{path}.reference"
                )
            return None
        dependency = self._validate_reference(frame.reference, f"{path}.reference")
        if any(
            operation.name == "ts" for operation in frame.operations
        ) and not isinstance(frame.reference, CurveReference):
            raise ValidationError(
                "ts in a general frame requires a curve reference",
                path=f"{path}.transformation",
            )
        return dependency

    def validate(self) -> None:
        """Validate names, references, positioning rules, and dependency cycles."""

        dependencies: dict[tuple[RootKind, str], list[tuple[RootKind, str]]] = {}
        for curve_name, curve in self.curves.items():
            node: tuple[RootKind, str] = ("curve", curve_name)
            dependencies[node] = []
            dependency = self._validate_frame(
                curve.starting_frame,
                f"reference_curves.{curve_name}.starting_frame",
                require_reference=True,
            )
            if isinstance(dependency, Curve):
                dependencies[node].append(("curve", dependency.name))  # type: ignore[arg-type]
            elif isinstance(dependency, Object):
                dependencies[node].append(("object", dependency.name))  # type: ignore[arg-type]
        for type_name, type_value in self.types.items():
            self._validate_frame(
                type_value.magnetic_center,
                f"types.{type_name}.magnetic_center",
                require_reference=False,
                type_local=True,
            )
            for frame_name, frame in type_value.frames.items():
                Type._check_frame_name(frame_name)
                self._validate_frame(
                    frame,
                    f"types.{type_name}.frames.{frame_name}",
                    require_reference=False,
                    type_local=True,
                )
        for object_name, object_value in self.objects.items():
            node = ("object", object_name)
            dependencies[node] = []
            type_value = self._resolve_type(
                object_value._type, f"objects.{object_name}.type"
            )
            position = object_value.position
            if position.reference.reference is None:
                raise DanglingReferenceError(
                    "position requires a reference",
                    path=f"objects.{object_name}.position.reference",
                )
            dependency = self._validate_reference(
                position.reference.reference,
                f"objects.{object_name}.position.reference",
            )
            if isinstance(dependency, Curve):
                dependencies[node].append(("curve", dependency.name))  # type: ignore[arg-type]
            elif isinstance(dependency, Object):
                dependencies[node].append(("object", dependency.name))  # type: ignore[arg-type]
            target_name = position.target_name
            if target_name is None:
                raise DanglingReferenceError(
                    "position target frame is unnamed",
                    path=f"objects.{object_name}.position.target",
                )
            if isinstance(position._target, Frame):
                if (
                    position._target.owner is not type_value
                    or position._target.name is None
                ):
                    raise ValidationError(
                        "target frame does not belong to the positioned object's type",
                        path=f"objects.{object_name}.position.target",
                    )
            elif (
                target_name not in _IMPLICIT_FRAME_NAMES
                and target_name not in type_value.frames
            ):
                raise DanglingReferenceError(
                    f"type {type_value.name!r} has no target frame {target_name!r}",
                    path=f"objects.{object_name}.position.target",
                )
            has_ts = any(operation.name == "ts" for operation in position.operations)
            primary = position.reference.reference
            if isinstance(primary, CurveReference):
                if position._reference_curve is not None:
                    raise ValidationError(
                        "reference_curve is forbidden with a primary curve reference",
                        path=f"objects.{object_name}.position.reference_curve",
                    )
            elif has_ts:
                if position._reference_curve is None:
                    raise ValidationError(
                        "ts with a world or object reference requires reference_curve",
                        path=f"objects.{object_name}.position.reference_curve",
                    )
                curve = self._resolve_curve(
                    position._reference_curve,
                    f"objects.{object_name}.position.reference_curve",
                )
                dependencies[node].append(("curve", curve.name))  # type: ignore[arg-type]
            elif position._reference_curve is not None:
                self._resolve_curve(
                    position._reference_curve,
                    f"objects.{object_name}.position.reference_curve",
                )

        state: dict[tuple[RootKind, str], int] = {}
        trail: list[tuple[RootKind, str]] = []

        def visit(node: tuple[RootKind, str]) -> None:
            marker = state.get(node, 0)
            if marker == 2:
                return
            if marker == 1:
                start = trail.index(node)
                cycle = trail[start:] + [node]
                rendered = " -> ".join(f"{kind}:{name}" for kind, name in cycle)
                raise ReferenceCycleError(f"reference dependency cycle: {rendered}")
            state[node] = 1
            trail.append(node)
            for dependency in dependencies.get(node, ()):
                visit(dependency)
            trail.pop()
            state[node] = 2

        for node in dependencies:
            visit(node)

    def _iter_frames(self) -> Iterator[tuple[RootEntity, Frame]]:
        for curve in self.curves.values():
            yield curve, curve.starting_frame
        for type_value in self.types.values():
            yield type_value, type_value.magnetic_center
            for frame in type_value.frames.values():
                yield type_value, frame
        for object_value in self.objects.values():
            yield object_value, object_value.position.reference

    def _check_foreign_links(self, value: object) -> None:
        def check_link(link: object) -> None:
            if isinstance(link, OwnedValue):
                linked_layout = link.layout
                if linked_layout is not None and linked_layout is not self:
                    raise ForeignLayoutError("link points into another layout")

        if isinstance(value, CurveReference):
            check_link(value._curve)
        elif isinstance(value, ObjectReference):
            check_link(value._object)
            check_link(value._frame)
        elif isinstance(value, Frame):
            self._check_foreign_links(value.reference)
        elif isinstance(value, Position):
            self._check_foreign_links(value.reference)
            check_link(value._target)
            check_link(value._reference_curve)
        elif isinstance(value, Curve):
            self._check_foreign_links(value.starting_frame)
        elif isinstance(value, Object):
            check_link(value._type)
            self._check_foreign_links(value.position)
        elif isinstance(value, Type):
            self._check_foreign_links(value.magnetic_center)
            for frame in value.frames.values():
                self._check_foreign_links(frame)
        else:
            check_link(value)

    def _reference_matches(
        self, reference: Reference | None, kind: RootKind, value: RootEntity
    ) -> bool:
        if kind == "curve" and isinstance(reference, CurveReference):
            return reference._curve is value or (
                isinstance(reference._curve, str) and reference._curve == value.name
            )
        if kind == "object" and isinstance(reference, ObjectReference):
            return reference._object is value or (
                isinstance(reference._object, str) and reference._object == value.name
            )
        return False

    def _ensure_root_not_in_use(self, kind: RootKind, value: RootEntity) -> None:
        if kind == "type":
            for object_name, object_value in self.objects.items():
                if object_value._type is value or (
                    isinstance(object_value._type, str)
                    and object_value._type == value.name
                ):
                    raise ReferenceInUseError(
                        f"type {value.name!r} is used by object {object_name!r}",
                        path=f"objects.{object_name}.type",
                    )
            return
        for structural_root, frame in self._iter_frames():
            if structural_root is value:
                continue
            if self._reference_matches(frame.reference, kind, value):
                raise ReferenceInUseError(f"{kind} {value.name!r} is still referenced")
        if kind == "curve":
            for object_name, object_value in self.objects.items():
                link = object_value.position._reference_curve
                if link is value or (isinstance(link, str) and link == value.name):
                    raise ReferenceInUseError(
                        f"curve {value.name!r} is used as a reference curve",
                        path=f"objects.{object_name}.position.reference_curve",
                    )

    def _ensure_frame_not_in_use(self, type_value: Type, frame: Frame) -> None:
        for structural_root, candidate in self._iter_frames():
            reference = candidate.reference
            if not isinstance(reference, ObjectReference):
                continue
            try:
                obj = self._resolve_object(reference._object, "reference.object")
                obj_type = self._resolve_type(obj._type, f"objects.{obj.name}.type")
            except (DanglingReferenceError, ForeignLayoutError):
                continue
            if obj_type is type_value and (
                reference._frame is frame
                or (
                    isinstance(reference._frame, str) and reference._frame == frame.name
                )
            ):
                raise ReferenceInUseError(f"frame {frame.name!r} is still referenced")
        for object_value in self.objects.values():
            try:
                obj_type = self._resolve_type(object_value._type, "object.type")
            except (DanglingReferenceError, ForeignLayoutError):
                continue
            target = object_value.position._target
            if obj_type is type_value and (
                target is frame or (isinstance(target, str) and target == frame.name)
            ):
                raise ReferenceInUseError(
                    f"frame {frame.name!r} is used as a position target"
                )

    def _rewrite_root_name(
        self,
        kind: RootKind,
        value: RootEntity,
        old_name: str,
        new_name: str,
    ) -> None:
        for _, frame in self._iter_frames():
            reference = frame.reference
            if kind == "curve" and isinstance(reference, CurveReference):
                if isinstance(reference._curve, str) and reference._curve == old_name:
                    reference._curve = new_name
            elif (
                kind == "object"
                and isinstance(reference, ObjectReference)
                and isinstance(reference._object, str)
                and reference._object == old_name
            ):
                reference._object = new_name
        if kind == "curve":
            for object_value in self.objects.values():
                if object_value.position._reference_curve == old_name:
                    object_value.position._reference_curve = new_name
        elif kind == "type":
            for object_value in self.objects.values():
                if object_value._type == old_name:
                    object_value._type = new_name

    def _rewrite_frame_name(
        self, type_value: Type, old_name: str, new_name: str
    ) -> None:
        for _, frame in self._iter_frames():
            reference = frame.reference
            if (
                not isinstance(reference, ObjectReference)
                or reference._frame != old_name
            ):
                continue
            try:
                obj = self._resolve_object(reference._object, "reference.object")
                obj_type = self._resolve_type(obj._type, f"objects.{obj.name}.type")
            except (DanglingReferenceError, ForeignLayoutError):
                continue
            if obj_type is type_value:
                reference._frame = new_name
        for object_value in self.objects.values():
            if object_value.position._target != old_name:
                continue
            try:
                obj_type = self._resolve_type(object_value._type, "object.type")
            except (DanglingReferenceError, ForeignLayoutError):
                continue
            if obj_type is type_value:
                object_value.position._target = new_name

    def plot3d(
        self,
        *,
        curves: bool = True,
        objects: bool = True,
        beam_frames: bool = False,
        selection: SearchEntity | None = None,
        show: bool = True,
        off_screen: bool = False,
        window_size: tuple[int, int] = (1000, 720),
        **viewer_kwargs: object,
    ) -> Any:
        self.validate()
        from .viewer import LayoutViewer

        return LayoutViewer(
            self,
            curves=curves,
            objects=objects,
            beam_frames=beam_frames,
            selection=selection,
            show=show,
            off_screen=off_screen,
            window_size=window_size,
            **viewer_kwargs,
        )

    plot3D = plot3d

    def plot2d(
        self,
        projection: str = "xy",
        *,
        curves: bool = True,
        objects: bool = True,
        beam_frames: bool = False,
        selection: SearchEntity | None = None,
        show: bool = True,
        figsize: tuple[float, float] = (10.0, 7.2),
        **viewer_kwargs: object,
    ) -> Any:
        self.validate()
        from .viewer2d import LayoutViewer2D

        return LayoutViewer2D(
            self,
            projection=projection,
            curves=curves,
            objects=objects,
            beam_frames=beam_frames,
            selection=selection,
            show=show,
            figsize=figsize,
            **viewer_kwargs,
        )

    def __repr__(self) -> str:
        return (
            f"Layout(curves={len(self.curves)}, types={len(self.types)}, "
            f"objects={len(self.objects)})"
        )

    def _repr_html_(self) -> str:
        return (
            "<table><caption><strong>Layout</strong></caption>"
            f"<tr><th>Curves</th><td>{len(self.curves)}</td></tr>"
            f"<tr><th>Types</th><td>{len(self.types)}</td></tr>"
            f"<tr><th>Objects</th><td>{len(self.objects)}</td></tr></table>"
        )

    def _ipython_key_completions_(self) -> list[str]:
        return list(dict.fromkeys((*self.curves, *self.types, *self.objects)))


ReferenceLike = Reference | Curve | Object | str
FrameLike = Frame | ReferenceLike


__all__ = [
    "UNSET",
    "ArrayLike",
    "Box",
    "Curve",
    "CurveReference",
    "Cylinder",
    "EntityMap",
    "Frame",
    "FrameLike",
    "JsonValue",
    "Layout",
    "ManagedSequence",
    "Object",
    "ObjectReference",
    "Operation",
    "OperationName",
    "OwnedValue",
    "Pose",
    "Position",
    "Reference",
    "ReferenceLike",
    "RootEntity",
    "RootKind",
    "SearchEntity",
    "SearchKind",
    "Segment",
    "Type",
    "WorldReference",
]
