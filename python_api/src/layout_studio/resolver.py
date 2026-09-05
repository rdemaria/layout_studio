"""Analytic frame resolution and mesh sampling for :mod:`layout_studio`.

This module is deliberately independent of the concrete model classes.  The
model owns attachment, editing, and JSON concerns; the resolver only needs the
small attribute protocol documented in ``docs/layout_python_api_spec.md``.
Keeping the dependency one-way avoids a model/resolver import cycle.  The only
runtime model import is the lazy construction of :class:`model.Pose` objects.

Matrices use columns ``[x, y, tangent, origin]``.  Ordinary local operations
are therefore post-multiplied.  ``ts`` is contextual: it is hoisted when a
frame is referenced to a curve, but is executed in place for a type-local
operation list.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from math import atan2, ceil, cos, floor, hypot, isfinite, pi, sin
from typing import TYPE_CHECKING, Any

try:  # Python 3.10 compatibility
    from typing import Self
except ImportError:  # pragma: no cover - exercised only on Python 3.10
    from typing_extensions import Self

import numpy as np
from numpy.typing import ArrayLike, NDArray

from .errors import (
    AmbiguousStationError,
    DanglingReferenceError,
    EvaluationError,
    ForeignLayoutError,
    NoStationSolutionError,
    ReferenceCycleError,
    StationOutOfRangeError,
    UnknownEntityError,
    ValidationError,
)

if TYPE_CHECKING:  # pragma: no cover - imports are solely for type checkers.
    from .model import Curve, Layout, Object, Pose, Type


FloatMatrix = NDArray[np.float64]
FloatVector = NDArray[np.float64]

OPERATION_NAMES = frozenset({"tx", "ty", "ts", "tt", "rx", "ry", "rs"})
RESERVED_TYPE_FRAMES = frozenset(
    {
        "center",
        "magnetic_center",
        "magnetic_entry",
        "magnetic_exit",
        "beam_center",
        "beam_entry",
        "beam_exit",
    }
)
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_EPS = np.finfo(float).eps

__all__ = [
    "OPERATION_NAMES",
    "RESERVED_TYPE_FRAMES",
    "Resolver",
    "advance",
    "apply_operations",
    "apply_ordinary_operations",
    "apply_type_operations",
    "elementary_matrix",
    "identity_matrix",
    "inverse_operations",
    "madx_euler",
    "operation_matrix",
    "rodrigues",
    "rotation_matrix",
    "rs_matrix",
    "rx_matrix",
    "ry_matrix",
    "sampled_curve",
    "swept_object_mesh",
    "swept_type_mesh",
    "translation_matrix",
    "tt_matrix",
    "tx_matrix",
    "ty_matrix",
]


# ---------------------------------------------------------------------------
# Elementary rigid transformations


def identity_matrix() -> FloatMatrix:
    """Return a fresh 4 x 4 identity frame."""

    return np.eye(4, dtype=float)


def _finite(value: Any, *, what: str = "value", path: str | None = None) -> float:
    """Convert *value* to a finite float or raise an evaluation error."""

    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise EvaluationError(f"{what} must be a finite number", path=path) from exc
    if not isfinite(result):
        raise EvaluationError(f"{what} must be a finite number", path=path)
    return result


def _matrix4(matrix: ArrayLike, *, copy: bool = True) -> FloatMatrix:
    """Return *matrix* as a finite homogeneous 4 x 4 float array."""

    result = np.array(matrix, dtype=float, copy=copy)
    if result.shape != (4, 4):
        raise EvaluationError(
            f"frame matrix must have shape (4, 4), got {result.shape}"
        )
    if not np.all(np.isfinite(result)):
        raise EvaluationError("frame matrix must contain only finite numbers")
    return result


def translation_matrix(axis: str | int, distance: float) -> FloatMatrix:
    """Return a local-axis translation matrix.

    ``axis`` accepts ``"x"``, ``"y"``, ``"s"``/``"t"``/``"z"`` or the
    corresponding integer column 0, 1, or 2.  ``z`` is accepted only as a
    low-level matrix convenience; it is not a public layout operation name.
    """

    distance = _finite(distance, what="translation distance")
    if isinstance(axis, str):
        key = axis.lower()
        try:
            index = {"x": 0, "y": 1, "s": 2, "t": 2, "z": 2}[key]
        except KeyError as exc:
            raise EvaluationError(f"unknown translation axis {axis!r}") from exc
    else:
        index = int(axis)
        if index not in (0, 1, 2):
            raise EvaluationError(
                f"translation axis index must be 0, 1, or 2; got {axis!r}"
            )
    result = identity_matrix()
    result[index, 3] = distance
    return result


def rotation_matrix(axis: str | int, angle: float) -> FloatMatrix:
    """Return a right-handed local-axis rotation matrix."""

    angle = _finite(angle, what="rotation angle")
    if isinstance(axis, str):
        key = axis.lower()
        try:
            index = {"x": 0, "y": 1, "s": 2, "t": 2, "z": 2}[key]
        except KeyError as exc:
            raise EvaluationError(f"unknown rotation axis {axis!r}") from exc
    else:
        index = int(axis)
        if index not in (0, 1, 2):
            raise EvaluationError(
                f"rotation axis index must be 0, 1, or 2; got {axis!r}"
            )

    c, s = cos(angle), sin(angle)
    result = identity_matrix()
    if index == 0:
        result[:3, :3] = ((1.0, 0.0, 0.0), (0.0, c, -s), (0.0, s, c))
    elif index == 1:
        result[:3, :3] = ((c, 0.0, s), (0.0, 1.0, 0.0), (-s, 0.0, c))
    else:
        result[:3, :3] = ((c, -s, 0.0), (s, c, 0.0), (0.0, 0.0, 1.0))
    return result


def tx_matrix(distance: float) -> FloatMatrix:
    """Return translation along local x."""

    return translation_matrix(0, distance)


def ty_matrix(distance: float) -> FloatMatrix:
    """Return translation along local y."""

    return translation_matrix(1, distance)


def tt_matrix(distance: float) -> FloatMatrix:
    """Return a straight translation along the local tangent."""

    return translation_matrix(2, distance)


def rx_matrix(angle: float) -> FloatMatrix:
    """Return right-handed rotation about local x."""

    return rotation_matrix(0, angle)


def ry_matrix(angle: float) -> FloatMatrix:
    """Return right-handed rotation about local y."""

    return rotation_matrix(1, angle)


def rs_matrix(angle: float) -> FloatMatrix:
    """Return right-handed rotation about the local tangent."""

    return rotation_matrix(2, angle)


def rodrigues(axis: ArrayLike, angle: float) -> NDArray[np.float64]:
    """Return the 3 x 3 Rodrigues rotation about *axis* by *angle*.

    The axis need not already be normalized.  A zero-length axis is rejected
    because silently returning identity tends to hide malformed poses.
    """

    vector = np.asarray(axis, dtype=float)
    if vector.shape != (3,) or not np.all(np.isfinite(vector)):
        raise EvaluationError("rotation axis must be a finite three-vector")
    norm = float(np.linalg.norm(vector))
    if norm <= 32.0 * _EPS:
        raise EvaluationError("rotation axis must be non-zero")
    x, y, z = vector / norm
    angle = _finite(angle, what="rotation angle")
    c, s = cos(angle), sin(angle)
    one_c = 1.0 - c
    return np.array(
        [
            [c + x * x * one_c, x * y * one_c - z * s, x * z * one_c + y * s],
            [y * x * one_c + z * s, c + y * y * one_c, y * z * one_c - x * s],
            [z * x * one_c - y * s, z * y * one_c + x * s, c + z * z * one_c],
        ],
        dtype=float,
    )


def elementary_matrix(name: str, value: float) -> FloatMatrix:
    """Return the rigid matrix for one ordinary operation.

    ``ts`` has no context-free elementary matrix and raises
    :class:`EvaluationError`.
    """

    if name == "tx":
        return tx_matrix(value)
    if name == "ty":
        return ty_matrix(value)
    if name == "tt":
        return tt_matrix(value)
    if name == "rx":
        return rx_matrix(value)
    if name == "ry":
        return ry_matrix(value)
    if name == "rs":
        return rs_matrix(value)
    if name == "ts":
        raise EvaluationError("ts requires a curve or type-path context")
    raise EvaluationError(f"unknown operation {name!r}")


# A second, discoverable name is useful to callers that think in operations.
operation_matrix = elementary_matrix


def _operation_parts(operation: Any, *, path: str | None = None) -> tuple[str, float]:
    """Read an operation from the model object, mapping, or two-item sequence."""

    if hasattr(operation, "name") and hasattr(operation, "value"):
        name, value = operation.name, operation.value
    elif isinstance(operation, Mapping):
        try:
            name, value = operation["name"], operation["value"]
        except KeyError as exc:
            raise EvaluationError(
                "operation mapping requires name and value", path=path
            ) from exc
    else:
        try:
            name, value = operation
        except (TypeError, ValueError) as exc:
            raise EvaluationError(
                "operation must be a (name, value) pair", path=path
            ) from exc
    if not isinstance(name, str) or name not in OPERATION_NAMES:
        raise EvaluationError(f"unknown operation {name!r}", path=path)
    return name, _finite(value, what=f"{name} value", path=path)


def advance(
    matrix: ArrayLike,
    distance: float,
    curvature: float = 0.0,
    roll: float = 0.0,
) -> FloatMatrix:
    """Advance a frame exactly along a straight line or circular arc.

    Parameters
    ----------
    matrix:
        Initial homogeneous frame with columns ``x, y, tangent, origin``.
    distance:
        Signed centreline distance.
    curvature:
        Signed constant curvature in inverse metres.  Positive curvature at
        zero roll bends toward local ``-x``.
    roll:
        Bend-plane roll.  Positive roll turns the positive-curvature direction
        from ``-x`` toward ``-y``.

    The implementation uses stable small-angle quotients and is valid for
    negative distance.  No independent twist is introduced along the arc.
    """

    frame = _matrix4(matrix)
    distance = _finite(distance, what="advance distance")
    curvature = _finite(curvature, what="curvature")
    roll = _finite(roll, what="bend-plane roll")

    if distance == 0.0:
        return frame
    theta = curvature * distance
    if curvature == 0.0 or abs(theta) < 1.0e-14:
        # Retain the first curvature-dependent displacement term for a tiny but
        # non-zero theta; this makes advance continuous down to machine scale.
        tangent_distance = distance * (1.0 - theta * theta / 6.0)
        normal_distance = curvature * distance * distance * (0.5 - theta * theta / 24.0)
    else:
        tangent_distance = sin(theta) / curvature
        # 2 sin(theta/2)^2 avoids cancellation in 1-cos(theta).
        normal_distance = 2.0 * sin(theta / 2.0) ** 2 / curvature

    c_roll, s_roll = cos(roll), sin(roll)
    normal_local = np.array((-c_roll, -s_roll, 0.0), dtype=float)
    translation_local = tangent_distance * np.array((0.0, 0.0, 1.0))
    translation_local += normal_distance * normal_local

    relative = identity_matrix()
    relative[:3, 3] = translation_local
    if theta != 0.0:
        bend_axis_local = np.array((s_roll, -c_roll, 0.0), dtype=float)
        relative[:3, :3] = rodrigues(bend_axis_local, theta)
    return frame @ relative


def apply_operations(matrix: ArrayLike, operations: Iterable[Any]) -> FloatMatrix:
    """Apply ordinary operations in stored order.

    All operations act on the current local axes.  A ``ts`` entry is rejected;
    use :func:`apply_type_operations` or curve-reference resolution when a path
    context exists.
    """

    result = _matrix4(matrix)
    for index, operation in enumerate(operations):
        name, value = _operation_parts(operation, path=f"operations.{index}")
        if name == "ts":
            raise EvaluationError(
                "ts cannot be applied without a path context",
                path=f"operations.{index}",
            )
        result = result @ elementary_matrix(name, value)
    return result


apply_ordinary_operations = apply_operations


def apply_type_operations(
    matrix: ArrayLike,
    operations: Iterable[Any],
    curvature: float = 0.0,
    roll: float = 0.0,
) -> FloatMatrix:
    """Apply a type-local operation list sequentially.

    Unlike a curve reference, every ``ts`` executes exactly where it appears
    and follows the type's constant-curvature centreline from the *current*
    frame.  A preceding rotation therefore changes the subsequent bend plane.
    """

    result = _matrix4(matrix)
    curvature = _finite(curvature, what="type curvature")
    roll = _finite(roll, what="type bend-plane roll")
    for index, operation in enumerate(operations):
        name, value = _operation_parts(operation, path=f"operations.{index}")
        if name == "ts":
            result = advance(result, value, curvature, roll)
        else:
            result = result @ elementary_matrix(name, value)
    return result


def inverse_operations(operations: Iterable[Any]) -> list[tuple[str, float]]:
    """Return the inverse type-local operation list.

    Reversing and negating is valid for both ordinary operations and the
    constant-curvature type-path ``ts`` operator.
    """

    parsed = [_operation_parts(operation) for operation in operations]
    return [(name, -value) for name, value in reversed(parsed)]


def _rigid_inverse(matrix: ArrayLike) -> FloatMatrix:
    """Return the inverse of a rigid homogeneous matrix."""

    frame = _matrix4(matrix, copy=False)
    result = identity_matrix()
    rotation = frame[:3, :3]
    result[:3, :3] = rotation.T
    result[:3, 3] = -(rotation.T @ frame[:3, 3])
    return result


def madx_euler(matrix: ArrayLike) -> tuple[float, float, float]:
    """Return MAD-X-style ``(theta, phi, psi)`` survey angles.

    At the vertical tangent singularity the convention fixes ``theta = 0`` and
    uses the specified deterministic roll branch.  The matrix remains the
    authoritative representation of the pose.
    """

    frame = _matrix4(matrix, copy=False)
    tangent = frame[:3, 2]
    tangent_norm = float(np.linalg.norm(tangent))
    if tangent_norm <= 32.0 * _EPS:
        raise EvaluationError("pose tangent is zero")
    tangent = tangent / tangent_norm
    x_axis = frame[:3, 0] - float(np.dot(frame[:3, 0], tangent)) * tangent
    x_norm = float(np.linalg.norm(x_axis))
    if x_norm <= 32.0 * _EPS:
        raise EvaluationError("pose x axis is parallel to its tangent")
    x_axis /= x_norm
    y_axis = np.cross(tangent, x_axis)

    h = hypot(float(tangent[0]), float(tangent[2]))
    phi = atan2(float(tangent[1]), h)
    if h > 1.0e-12:
        theta = atan2(float(tangent[0]), float(tangent[2]))
        psi = atan2(float(x_axis[1]), float(y_axis[1]))
    else:
        theta = 0.0
        psi = atan2(float(-y_axis[0]), float(x_axis[0]))
    return theta, phi, psi


def _make_pose(matrix: ArrayLike, space: str) -> Pose:
    """Construct ``model.Pose`` lazily to avoid a module import cycle."""

    from .model import Pose

    return Pose(_matrix4(matrix), space=space)


# ---------------------------------------------------------------------------
# Duck-typed model access


def _mapping_items(mapping: Any) -> list[tuple[str, Any]]:
    if mapping is None:
        return []
    try:
        return list(mapping.items())
    except AttributeError as exc:
        raise EvaluationError("entity registry must be mapping-like") from exc


def _mapping_get(mapping: Any, name: str) -> Any | None:
    if mapping is None:
        return None
    try:
        return mapping[name]
    except (KeyError, TypeError):
        return None


def _entity_name(entity: Any, mapping: Any) -> str | None:
    name = getattr(entity, "name", None)
    if isinstance(name, str) and _mapping_get(mapping, name) is entity:
        return name
    for candidate, value in _mapping_items(mapping):
        if value is entity:
            return candidate
    return name if isinstance(name, str) and name else None


def _operations(value: Any) -> Sequence[Any]:
    if value is None:
        return ()
    operations = getattr(value, "operations", None)
    if operations is not None:
        return operations
    transformation = getattr(value, "transformation", None)
    if transformation is not None:
        return transformation
    if isinstance(value, Mapping):
        return value.get("operations", value.get("transformation", ()))  # type: ignore[return-value]
    return ()


def _transform_parts(value: Any) -> tuple[Any, Sequence[Any]]:
    """Return the anchor reference and operations of a Frame/Position value."""

    if value is None:
        return None, ()
    if isinstance(value, Mapping):
        reference = value.get("reference")
        return reference, value.get("operations", value.get("transformation", ()))  # type: ignore[return-value]
    if hasattr(value, "kind"):
        return value, ()
    reference = getattr(value, "reference", value)
    operations = _operations(value)
    # Position.reference is itself a Frame in the Python API.  Its operations
    # are also exposed as Position.operations, so use the nested list once.
    if (
        reference is not value
        and not hasattr(reference, "kind")
        and hasattr(reference, "reference")
    ):
        return reference.reference, _operations(reference)
    return reference, operations


def _segment_values(segment: Any) -> tuple[float, float, float]:
    if hasattr(segment, "length"):
        return float(segment.length), float(segment.angle), float(segment.roll)
    try:
        length, angle, roll = segment
    except (TypeError, ValueError) as exc:
        raise EvaluationError(
            "curve segment must contain length, angle, and roll"
        ) from exc
    return float(length), float(angle), float(roll)


def _shape_values(shape: Any) -> tuple[str, dict[str, float]]:
    """Normalize a model shape or canonical shape tuple."""

    if shape is None:
        raise EvaluationError("type has no shape")
    class_name = type(shape).__name__.lower()
    kind = getattr(shape, "kind", None)
    if isinstance(kind, str):
        class_name = kind.lower()
    if class_name == "box" or all(
        hasattr(shape, field) for field in ("dx", "dy", "dz")
    ):
        return "box", {
            "dx": float(shape.dx),
            "dy": float(shape.dy),
            "dz": float(shape.dz),
            "curvature": float(getattr(shape, "curvature", 0.0)),
            "roll": float(getattr(shape, "roll", 0.0)),
        }
    if class_name == "cylinder" or all(hasattr(shape, field) for field in ("r", "dz")):
        return "cylinder", {
            "r": float(shape.r),
            "dz": float(shape.dz),
            "curvature": float(getattr(shape, "curvature", 0.0)),
            "roll": float(getattr(shape, "roll", 0.0)),
        }
    if isinstance(shape, Sequence) and not isinstance(shape, (str, bytes)):
        if len(shape) == 6 and shape[0] == "box":
            return "box", dict(
                zip(
                    ("dx", "dy", "dz", "curvature", "roll"),
                    (float(value) for value in shape[1:]),
                )
            )
        if len(shape) == 5 and shape[0] == "cylinder":
            return "cylinder", dict(
                zip(
                    ("r", "dz", "curvature", "roll"),
                    (float(value) for value in shape[1:]),
                )
            )
    raise EvaluationError("unsupported type shape")


def _axis_feature_values(
    type_: Any, feature: str
) -> tuple[Any, float, float, float] | None:
    """Return one optional type axis as ``(center, length, curvature, roll)``."""

    fields = tuple(f"{feature}_{name}" for name in ("center", "length", "curvature", "roll"))
    values = tuple(getattr(type_, field, None) for field in fields)
    if all(value is None for value in values):
        return None
    if any(value is None for value in values):
        raise EvaluationError(
            f"{', '.join(fields)} must be all present or all absent"
        )
    center, length, curvature, roll = values
    length_value = _finite(length, what=f"{feature} length")
    if length_value <= 0.0:
        raise EvaluationError(f"{feature} length must be positive")
    return (
        center,
        length_value,
        _finite(curvature, what=f"{feature} curvature"),
        _finite(roll, what=f"{feature} roll"),
    )


def _type_path_values(type_: Any) -> tuple[float, float]:
    """Return the optional mechanical path's curvature and roll.

    A type without a shape still has its local centre frame.  Its local ``ts``
    operations follow the straight tangent through that frame.
    """

    shape_value = getattr(type_, "shape", None)
    if shape_value is None:
        return 0.0, 0.0
    _, shape = _shape_values(shape_value)
    return shape["curvature"], shape["roll"]


def _reference_kind_value(kind: Any) -> str:
    if hasattr(kind, "value"):
        kind = kind.value
    return str(kind).lower()


def _reference_info(reference: Any) -> tuple[str, Any, Any]:
    """Return ``(kind, entity, frame)`` for any supported reference form."""

    if reference is None:
        raise EvaluationError("missing frame reference")
    if isinstance(reference, str):
        if reference == "world":
            return "world", None, None
        if reference.startswith("curve:") and reference[6:]:
            return "curve", reference[6:], None
        if reference.count("->") == 1:
            object_name, frame_name = reference.split("->", 1)
            if object_name and frame_name:
                return "object_frame", object_name, frame_name
        # A bare string is intentionally not guessed between namespaces.
        raise EvaluationError(f"ambiguous or malformed reference {reference!r}")
    if isinstance(reference, Mapping):
        kind = _reference_kind_value(reference.get("kind", ""))
        if kind == "world":
            return kind, None, None
        if kind == "curve":
            return kind, reference.get("curve"), None
        if kind in {"object_frame", "object"}:
            return (
                "object_frame",
                reference.get("object"),
                reference.get("frame", "center"),
            )
        raise EvaluationError(f"unknown reference kind {kind!r}")

    kind = getattr(reference, "kind", None)
    if kind is not None:
        kind = _reference_kind_value(kind)
        if kind == "world":
            return kind, None, None
        if kind == "curve":
            curve = getattr(reference, "curve", None)
            if curve is None:
                curve = getattr(reference, "curve_name", None)
            return kind, curve, None
        if kind in {"object_frame", "object"}:
            object_ = getattr(reference, "object", None)
            if object_ is None:
                object_ = getattr(reference, "object_name", None)
            frame = getattr(reference, "frame", None)
            if frame is None:
                frame = getattr(reference, "frame_name", "center")
            return "object_frame", object_, frame
        raise EvaluationError(f"unknown reference kind {kind!r}")

    # ReferenceLike also accepts live Curve and Object instances.
    if hasattr(reference, "segments") and hasattr(reference, "starting_frame"):
        return "curve", reference, None
    if hasattr(reference, "position") and hasattr(reference, "type"):
        return "object_frame", reference, "center"
    raise EvaluationError(f"unsupported reference {reference!r}")


def _point3(point: Any) -> FloatVector:
    if hasattr(point, "origin"):
        point = point.origin
    result = np.asarray(point, dtype=float)
    if result.shape == (4,):
        if result[3] == 0.0:
            raise EvaluationError("homogeneous point has zero w coordinate")
        result = result[:3] / result[3]
    if result.shape != (3,) or not np.all(np.isfinite(result)):
        raise EvaluationError("point must be a finite three-vector or Pose")
    return np.array(result, dtype=float, copy=True)


@dataclass(frozen=True, slots=True)
class _CurveStationGeometry:
    """Session-local values reused by exact station inference.

    ``midpoints`` and ``sphere_radii`` define conservative bounding spheres
    for the centreline segments.  A point on a segment is at most half that
    segment's path length from its midpoint, so the spheres also enclose
    circular arcs.  This lets inference discard distant segments only after a
    closer transverse-plane solution has been found, without changing the
    exact root, tie, or ambiguity rules.
    """

    boundaries: NDArray[np.float64]
    starts: NDArray[np.float64]
    lengths: NDArray[np.float64]
    angles: NDArray[np.float64]
    rolls: NDArray[np.float64]
    curvatures: NDArray[np.float64]
    midpoints: NDArray[np.float64]
    sphere_radii: NDArray[np.float64]
    base_origin_scale: float
    path_tolerance: float
    geometry_scale_tolerance: float


class Resolver:
    """Evaluate a layout's symbolic frame graph analytically.

    A resolver accepts root names or live model instances.  Each outer public
    call uses a fresh memoization session, so edits made through the live model
    are immediately visible while recursive dependencies are still evaluated
    only once.  Both explicit graph validation and an active recursion stack
    protect against reference cycles.
    """

    def __init__(self, layout: Layout | None) -> None:
        self.layout = layout
        self._depth = 0
        self._curve_starts: dict[int, FloatMatrix] = {}
        self._curve_data_cache: dict[
            int, tuple[list[Any], list[float], list[FloatMatrix]]
        ] = {}
        self._curve_station_geometry_cache: dict[int, _CurveStationGeometry] = {}
        self._station_inference_cache: dict[tuple[int, bytes], float] = {}
        self._object_centers: dict[int, FloatMatrix] = {}
        self._active: list[tuple[str, str]] = []
        self._explicit_sessions: list[Any] = []

    @property
    def _curves(self) -> Any:
        if self.layout is None:
            return None
        curves = getattr(self.layout, "curves", None)
        return (
            curves
            if curves is not None
            else getattr(self.layout, "reference_curves", None)
        )

    @property
    def _types(self) -> Any:
        return None if self.layout is None else getattr(self.layout, "types", None)

    @property
    def _objects(self) -> Any:
        return None if self.layout is None else getattr(self.layout, "objects", None)

    @contextmanager
    def _session(self) -> Iterator[None]:
        outermost = self._depth == 0
        if outermost:
            self._curve_starts = {}
            self._curve_data_cache = {}
            self._curve_station_geometry_cache = {}
            self._station_inference_cache = {}
            self._object_centers = {}
            self._active = []
            if self.layout is not None:
                layout_validate = getattr(self.layout, "validate", None)
                if callable(layout_validate):
                    # Honour the model's public validation contract (including
                    # subclass overrides) without validating twice in plots.
                    layout_validate()
                else:
                    self.validate()
        self._depth += 1
        try:
            yield
        finally:
            self._depth -= 1
            if outermost:
                # An explicit Resolver may outlive a very large snapshot.
                # Its session caches are useful only while the context is
                # active, so release model and geometry references promptly.
                self._curve_starts.clear()
                self._curve_data_cache.clear()
                self._curve_station_geometry_cache.clear()
                self._station_inference_cache.clear()
                self._object_centers.clear()
                self._active.clear()

    def __enter__(self) -> Self:
        """Keep validation and memoized geometry alive across public calls.

        A resolver normally starts a fresh evaluation session for each method,
        ensuring model edits are observed immediately.  An explicit context is
        the efficient option for evaluating many frames from one snapshot::

            with layout.resolver() as resolver:
                poses = [resolver.object_frame(obj) for obj in layout.objects.values()]
        """

        session = self._session()
        session.__enter__()
        self._explicit_sessions.append(session)
        return self

    def __exit__(self, *exc_info: object) -> None:
        if not self._explicit_sessions:
            raise RuntimeError("resolver context exit without a matching enter")
        self._explicit_sessions.pop().__exit__(*exc_info)

    @contextmanager
    def _resolving(
        self, key: tuple[str, str], *, path: str | None = None
    ) -> Iterator[None]:
        if key in self._active:
            start = self._active.index(key)
            cycle = self._active[start:] + [key]
            description = " -> ".join(f"{kind}:{name}" for kind, name in cycle)
            raise ReferenceCycleError(
                f"reference cycle detected: {description}", path=path
            )
        self._active.append(key)
        try:
            yield
        finally:
            if self._active and self._active[-1] == key:
                self._active.pop()
            elif key in self._active:  # defensive cleanup after a nested exception
                self._active.remove(key)

    def _resolve_entity(
        self,
        kind: str,
        value: Any,
        *,
        path: str | None = None,
        reference: bool = False,
    ) -> Any:
        mapping = {"curve": self._curves, "type": self._types, "object": self._objects}[
            kind
        ]
        if isinstance(value, str):
            if kind == "curve" and value.startswith("curve:"):
                value = value[6:]
            entity = _mapping_get(mapping, value)
            if entity is None:
                error = DanglingReferenceError if reference else UnknownEntityError
                raise error(f"unknown {kind} {value!r}", path=path)
            return entity
        if value is None:
            error = DanglingReferenceError if reference else UnknownEntityError
            raise error(f"missing {kind} reference", path=path)

        entity_layout = getattr(value, "layout", None)
        if (
            self.layout is not None
            and entity_layout is not None
            and entity_layout is not self.layout
        ):
            raise ForeignLayoutError(f"{kind} belongs to another layout", path=path)
        if self.layout is not None:
            name = _entity_name(value, mapping)
            if name is None or _mapping_get(mapping, name) is not value:
                error = DanglingReferenceError if reference else UnknownEntityError
                raise error(f"{kind} is not attached to this layout", path=path)
        return value

    def _resolve_curve(self, value: Any, **kwargs: Any) -> Any:
        return self._resolve_entity("curve", value, **kwargs)

    def _resolve_type(self, value: Any, **kwargs: Any) -> Any:
        return self._resolve_entity("type", value, **kwargs)

    def _resolve_object(self, value: Any, **kwargs: Any) -> Any:
        return self._resolve_entity("object", value, **kwargs)

    def _name_for(self, kind: str, entity: Any) -> str:
        mapping = {"curve": self._curves, "type": self._types, "object": self._objects}[
            kind
        ]
        return _entity_name(entity, mapping) or f"<detached-{kind}-{id(entity):x}>"

    # ------------------------------------------------------------------
    # Validation and dependency cycles

    def _validation_operations(
        self, operations: Iterable[Any], path: str
    ) -> list[tuple[str, float]]:
        parsed: list[tuple[str, float]] = []
        for index, operation in enumerate(operations):
            item_path = f"{path}.{index}"
            try:
                name, value = _operation_parts(operation, path=item_path)
            except EvaluationError as exc:
                raise ValidationError(str(exc), path=exc.path or item_path) from exc
            parsed.append((name, value))
        return parsed

    def _validate_reference(self, reference: Any, path: str) -> tuple[str, Any, Any]:
        try:
            kind, entity, frame = _reference_info(reference)
        except EvaluationError as exc:
            raise ValidationError(str(exc), path=path) from exc
        if kind == "curve":
            entity = self._resolve_curve(entity, path=f"{path}.curve", reference=True)
        elif kind == "object_frame":
            entity = self._resolve_object(entity, path=f"{path}.object", reference=True)
            type_ = self._object_type(entity, path=f"{path}.object")
            try:
                self._type_frame_operations(type_, frame)
            except UnknownEntityError as exc:
                raise DanglingReferenceError(str(exc), path=f"{path}.frame") from exc
        elif kind != "world":
            raise ValidationError(f"unknown reference kind {kind!r}", path=path)
        return kind, entity, frame

    def validate(self) -> None:
        """Validate evaluator-visible structure, references, and graph cycles.

        Model constructors validate local fields eagerly; this pass intentionally
        repeats the inexpensive numeric checks so duck-typed inputs and mutated
        graphs fail with domain exceptions and useful canonical paths.
        """

        if self.layout is None:
            return

        edges: dict[tuple[str, str], list[tuple[str, str]]] = {}

        for kind, mapping in (
            ("curve", self._curves),
            ("type", self._types),
            ("object", self._objects),
        ):
            for name, _ in _mapping_items(mapping):
                if not isinstance(name, str) or not name:
                    raise ValidationError(
                        f"{kind} names must be non-empty strings", path=f"{kind}s"
                    )

        for type_name, type_ in _mapping_items(self._types):
            base = f"types.{type_name}"
            color = getattr(type_, "color", None)
            if color is not None and (
                not isinstance(color, str) or not _COLOR_RE.fullmatch(color)
            ):
                raise ValidationError(
                    "color must be a six-digit hexadecimal value", path=f"{base}.color"
                )
            shape_value = getattr(type_, "shape", None)
            if shape_value is not None:
                try:
                    shape_kind, shape = _shape_values(shape_value)
                except (EvaluationError, TypeError, ValueError) as exc:
                    raise ValidationError(str(exc), path=f"{base}.shape") from exc
                dimensions = (
                    ("dx", "dy", "dz") if shape_kind == "box" else ("r", "dz")
                )
                for dimension in dimensions:
                    value = shape[dimension]
                    if not isfinite(value) or value <= 0.0:
                        raise ValidationError(
                            f"shape {dimension} must be positive and finite",
                            path=f"{base}.shape",
                        )
                for field in ("curvature", "roll"):
                    if not isfinite(shape[field]):
                        raise ValidationError(
                            f"shape {field} must be finite", path=f"{base}.shape"
                        )
            for feature in ("magnetic", "beam"):
                try:
                    axis = _axis_feature_values(type_, feature)
                except (EvaluationError, TypeError, ValueError) as exc:
                    raise ValidationError(str(exc), path=f"{base}.{feature}_center") from exc
                if axis is None:
                    continue
                center, _, _, _ = axis
                if getattr(center, "reference", None) is not None:
                    raise ValidationError(
                        f"type-local {feature}_center cannot have an explicit reference",
                        path=f"{base}.{feature}_center.reference",
                    )
                self._validation_operations(
                    _operations(center), f"{base}.{feature}_center.transformation"
                )
            for frame_name, frame in _mapping_items(getattr(type_, "frames", None)):
                frame_path = f"{base}.frames.{frame_name}"
                if not isinstance(frame_name, str) or not frame_name:
                    raise ValidationError(
                        "frame names must be non-empty strings", path=f"{base}.frames"
                    )
                if frame_name in RESERVED_TYPE_FRAMES:
                    raise ValidationError(
                        f"{frame_name!r} is a reserved frame name", path=frame_path
                    )
                if getattr(frame, "reference", None) is not None:
                    raise ValidationError(
                        "type-local frames cannot have an explicit reference",
                        path=f"{frame_path}.reference",
                    )
                self._validation_operations(
                    _operations(frame), f"{frame_path}.transformation"
                )

        for curve_name, curve in _mapping_items(self._curves):
            base = f"reference_curves.{curve_name}"
            color = getattr(curve, "color", None)
            if color is not None and (
                not isinstance(color, str) or not _COLOR_RE.fullmatch(color)
            ):
                raise ValidationError(
                    "color must be a six-digit hexadecimal value", path=f"{base}.color"
                )
            segments = list(getattr(curve, "segments", ()))
            if not segments:
                raise ValidationError(
                    "curve must contain at least one segment", path=f"{base}.segments"
                )
            for index, segment in enumerate(segments):
                try:
                    length, angle, roll = _segment_values(segment)
                except (EvaluationError, TypeError, ValueError) as exc:
                    raise ValidationError(
                        str(exc), path=f"{base}.segments.{index}"
                    ) from exc
                if not isfinite(length) or length <= 0.0:
                    raise ValidationError(
                        "segment length must be positive and finite",
                        path=f"{base}.segments.{index}.0",
                    )
                if not isfinite(angle) or not isfinite(roll):
                    raise ValidationError(
                        "segment angle and roll must be finite",
                        path=f"{base}.segments.{index}",
                    )
            reference, operations = _transform_parts(
                getattr(curve, "starting_frame", None)
            )
            parsed = self._validation_operations(
                operations, f"{base}.starting_frame.transformation"
            )
            ref_kind, ref_entity, _ = self._validate_reference(
                reference, f"{base}.starting_frame.reference"
            )
            if any(name == "ts" for name, _ in parsed) and ref_kind not in {"curve"}:
                raise ValidationError(
                    "ts in a curve starting frame requires a curve reference",
                    path=f"{base}.starting_frame.transformation",
                )
            node = ("curve", curve_name)
            edges[node] = []
            if ref_kind == "curve":
                edges[node].append(("curve", self._name_for("curve", ref_entity)))
            elif ref_kind == "object_frame":
                edges[node].append(("object", self._name_for("object", ref_entity)))

        for object_name, object_ in _mapping_items(self._objects):
            base = f"objects.{object_name}"
            self._object_type(object_, path=f"{base}.type")
            position = getattr(object_, "position", None)
            if position is None:
                raise ValidationError(
                    "object requires a position", path=f"{base}.position"
                )
            try:
                self._type_frame_operations(
                    self._object_type(object_, path=f"{base}.type"),
                    getattr(position, "target", "center"),
                )
            except UnknownEntityError as exc:
                raise DanglingReferenceError(
                    str(exc), path=f"{base}.position.target"
                ) from exc
            reference, operations = _transform_parts(position)
            parsed = self._validation_operations(
                operations, f"{base}.position.transformation"
            )
            ref_kind, ref_entity, _ = self._validate_reference(
                reference, f"{base}.position.reference"
            )
            has_ts = any(name == "ts" for name, _ in parsed)
            reference_curve = self._position_reference_curve(position)
            if ref_kind == "curve" and reference_curve is not None:
                raise ValidationError(
                    "reference_curve is forbidden when the primary reference is a curve",
                    path=f"{base}.position.reference_curve",
                )
            inferred_curve = None
            if has_ts and ref_kind != "curve":
                if reference_curve is None:
                    raise ValidationError(
                        "world/object-referenced ts requires reference_curve",
                        path=f"{base}.position.reference_curve",
                    )
                inferred_curve = self._resolve_curve(
                    reference_curve,
                    path=f"{base}.position.reference_curve",
                    reference=True,
                )
            elif reference_curve is not None:
                # It has no geometric effect without ts, but still must resolve.
                inferred_curve = self._resolve_curve(
                    reference_curve,
                    path=f"{base}.position.reference_curve",
                    reference=True,
                )
            node = ("object", object_name)
            edges[node] = []
            if ref_kind == "curve":
                edges[node].append(("curve", self._name_for("curve", ref_entity)))
            elif ref_kind == "object_frame":
                edges[node].append(("object", self._name_for("object", ref_entity)))
            if has_ts and ref_kind != "curve" and inferred_curve is not None:
                edges[node].append(("curve", self._name_for("curve", inferred_curve)))

        state: dict[tuple[str, str], int] = {}
        stack: list[tuple[str, str]] = []

        def visit(node: tuple[str, str]) -> None:
            mark = state.get(node, 0)
            if mark == 2:
                return
            if mark == 1:
                start = stack.index(node)
                cycle = stack[start:] + [node]
                text = " -> ".join(f"{kind}:{name}" for kind, name in cycle)
                raise ReferenceCycleError(
                    f"reference cycle detected: {text}", path=self._node_path(node)
                )
            state[node] = 1
            stack.append(node)
            for dependency in edges.get(node, ()):
                visit(dependency)
            stack.pop()
            state[node] = 2

        for node in edges:
            visit(node)

    @staticmethod
    def _node_path(node: tuple[str, str]) -> str:
        kind, name = node
        return f"reference_curves.{name}" if kind == "curve" else f"objects.{name}"

    # ------------------------------------------------------------------
    # Curve frames and referenced transformations

    def _curve_start_matrix(self, curve: Any) -> FloatMatrix:
        key_id = id(curve)
        cached = self._curve_starts.get(key_id)
        if cached is not None:
            return cached
        name = self._name_for("curve", curve)
        path = f"reference_curves.{name}.starting_frame"
        with self._resolving(("curve", name), path=path):
            matrix = self._resolve_transformation(
                getattr(curve, "starting_frame", None),
                allow_inference=False,
                reference_curve=None,
                path=path,
            )
        self._curve_starts[key_id] = matrix
        return matrix

    def _curve_data(
        self, curve: Any
    ) -> tuple[list[Any], list[float], list[FloatMatrix]]:
        cached = self._curve_data_cache.get(id(curve))
        if cached is not None:
            return cached
        segments = list(getattr(curve, "segments", ()))
        if not segments:
            raise EvaluationError(
                "curve has no segments",
                path=f"reference_curves.{self._name_for('curve', curve)}.segments",
            )
        stations = [0.0]
        starts = [self._curve_start_matrix(curve)]
        current = starts[0]
        for index, segment in enumerate(segments):
            length, angle, roll = _segment_values(segment)
            if (
                not isfinite(length)
                or length <= 0.0
                or not isfinite(angle)
                or not isfinite(roll)
            ):
                raise EvaluationError(
                    "invalid curve segment",
                    path=f"reference_curves.{self._name_for('curve', curve)}.segments.{index}",
                )
            current = advance(current, length, angle / length, roll)
            stations.append(stations[-1] + length)
            starts.append(current)
        # starts contains the frame at every boundary; frame i starts segment i.
        result = segments, stations, starts
        self._curve_data_cache[id(curve)] = result
        return result

    def _curve_station_geometry(self, curve: Any) -> _CurveStationGeometry:
        """Return immutable per-segment station data for the active session."""

        key_id = id(curve)
        cached = self._curve_station_geometry_cache.get(key_id)
        if cached is not None:
            return cached

        segments, boundary_values, start_values = self._curve_data(curve)
        values = np.asarray([_segment_values(segment) for segment in segments])
        lengths = values[:, 0]
        angles = values[:, 1]
        rolls = values[:, 2]
        curvatures = angles / lengths
        boundaries = np.asarray(boundary_values, dtype=float)
        starts = np.stack(start_values[:-1])
        midpoints = np.stack(
            [
                advance(start, 0.5 * length, curvature, roll)[:3, 3]
                for start, length, curvature, roll in zip(
                    starts, lengths, curvatures, rolls
                )
            ]
        )

        total = float(boundaries[-1])
        bent = np.abs(angles) >= 1.0e-10
        bend_radii = np.abs(lengths[bent] / angles[bent])
        geometry_scale = max(
            1.0,
            total,
            float(np.max(lengths)),
            float(np.max(bend_radii)) if bend_radii.size else 1.0,
        )
        result = _CurveStationGeometry(
            boundaries=boundaries,
            starts=starts,
            lengths=lengths,
            angles=angles,
            rolls=rolls,
            curvatures=curvatures,
            midpoints=midpoints,
            sphere_radii=0.5 * lengths,
            base_origin_scale=max(
                1.0,
                total,
                float(np.max(np.abs(np.stack(start_values)[:, :3, 3]))),
            ),
            path_tolerance=max(1.0e-12, 1.0e-9 * max(1.0, total)),
            geometry_scale_tolerance=max(1.0e-10, 1.0e-12 * geometry_scale),
        )
        self._curve_station_geometry_cache[key_id] = result
        return result

    def _curve_frame_matrix(
        self, curve: Any, station: float, extrapolate: bool = True
    ) -> FloatMatrix:
        station = _finite(station, what="curve station")
        segments, boundaries, starts = self._curve_data(curve)
        total = boundaries[-1]
        path = f"reference_curves.{self._name_for('curve', curve)}"
        if station < 0.0:
            if not extrapolate:
                raise StationOutOfRangeError(
                    f"station {station:g} is before curve domain [0, {total:g}]",
                    path=path,
                )
            return starts[0] @ tt_matrix(station)
        if station > total:
            if not extrapolate:
                raise StationOutOfRangeError(
                    f"station {station:g} is after curve domain [0, {total:g}]",
                    path=path,
                )
            return starts[-1] @ tt_matrix(station - total)
        if station <= 0.0:
            return starts[0].copy()
        if station >= total:
            return starts[-1].copy()
        index = bisect_right(boundaries, station) - 1
        index = min(max(index, 0), len(segments) - 1)
        local = station - boundaries[index]
        length, angle, roll = _segment_values(segments[index])
        return advance(starts[index], local, angle / length, roll)

    def _reference_base_matrix(
        self, reference: Any, *, path: str
    ) -> tuple[str, FloatMatrix]:
        kind, entity, frame = _reference_info(reference)
        if kind == "world":
            return kind, identity_matrix()
        if kind == "curve":
            curve = self._resolve_curve(entity, path=f"{path}.curve", reference=True)
            return kind, self._curve_frame_matrix(curve, 0.0, True)
        if kind == "object_frame":
            object_ = self._resolve_object(
                entity, path=f"{path}.object", reference=True
            )
            return kind, self._object_named_frame_matrix(object_, frame)
        raise EvaluationError(f"unknown reference kind {kind!r}", path=path)

    def _resolve_transformation(
        self,
        transformation: Any,
        *,
        allow_inference: bool,
        reference_curve: Any,
        path: str,
    ) -> FloatMatrix:
        reference, operations = _transform_parts(transformation)
        parsed = [
            _operation_parts(operation, path=f"{path}.transformation.{i}")
            for i, operation in enumerate(operations)
        ]
        ts_total = sum(value for name, value in parsed if name == "ts")
        ordinary = [(name, value) for name, value in parsed if name != "ts"]
        kind, entity, frame = _reference_info(reference)

        if kind == "curve":
            if reference_curve is not None:
                raise EvaluationError(
                    "reference_curve is forbidden with a primary curve reference",
                    path=f"{path}.reference_curve",
                )
            curve = self._resolve_curve(
                entity, path=f"{path}.reference.curve", reference=True
            )
            return apply_operations(
                self._curve_frame_matrix(curve, ts_total, True), ordinary
            )

        if kind == "world":
            primary = identity_matrix()
        elif kind == "object_frame":
            object_ = self._resolve_object(
                entity, path=f"{path}.reference.object", reference=True
            )
            primary = self._object_named_frame_matrix(object_, frame)
        else:
            raise EvaluationError(
                f"unsupported reference kind {kind!r}", path=f"{path}.reference"
            )

        has_ts = any(name == "ts" for name, _ in parsed)
        if not has_ts:
            return apply_operations(primary, ordinary)
        if not allow_inference:
            raise EvaluationError(
                "ts requires a curve reference in this transformation",
                path=f"{path}.transformation",
            )
        if reference_curve is None:
            raise EvaluationError(
                "world/object-referenced ts requires reference_curve",
                path=f"{path}.reference_curve",
            )
        curve = self._resolve_curve(
            reference_curve, path=f"{path}.reference_curve", reference=True
        )
        inferred_station = self._infer_station(curve, primary[:3, 3])
        desired = self._curve_frame_matrix(curve, inferred_station + ts_total, True)
        return apply_operations(desired, ordinary)

    def curve_frame(
        self,
        curve: Curve | str,
        station: float,
        extrapolate: bool = True,
    ) -> Pose:
        """Return the world pose on *curve* at path coordinate *station*.

        Outside the finite curve domain, the default is straight tangent
        continuation.  Set ``extrapolate=False`` to raise
        :class:`StationOutOfRangeError` instead.
        """

        with self._session():
            resolved = self._resolve_curve(curve)
            return _make_pose(
                self._curve_frame_matrix(resolved, station, extrapolate), "world"
            )

    # ------------------------------------------------------------------
    # Type-local and object frames

    def _object_type(self, object_: Any, *, path: str | None = None) -> Any:
        type_value = getattr(object_, "type", None)
        if type_value is None:
            type_value = getattr(object_, "type_name", None)
        return self._resolve_type(type_value, path=path, reference=True)

    @staticmethod
    def _position_reference_curve(position: Any) -> Any:
        value = getattr(position, "reference_curve", None)
        if value is None:
            value = getattr(position, "reference_curve_name", None)
        if isinstance(position, Mapping):
            value = position.get("reference_curve", value)
        return value

    def _type_frame_operations(self, type_: Any, frame: Any) -> list[tuple[str, float]]:
        frames = getattr(type_, "frames", None)
        if frame is None:
            frame = "center"
        if not isinstance(frame, str):
            for name, candidate in _mapping_items(frames):
                if candidate is frame:
                    frame = name
                    break
            else:
                owner = getattr(frame, "owner", None)
                if owner is not None and owner is not type_:
                    raise UnknownEntityError("frame belongs to a different type")
                raise UnknownEntityError("frame is not a stored frame of this type")

        if frame == "center":
            return []
        for feature in ("magnetic", "beam"):
            if frame not in {
                f"{feature}_center",
                f"{feature}_entry",
                f"{feature}_exit",
            }:
                continue
            axis = _axis_feature_values(type_, feature)
            if axis is None:
                raise UnknownEntityError(f"type has no {feature} axis")
            center, _, _, _ = axis
            return [
                _operation_parts(operation) for operation in _operations(center)
            ]
        stored = _mapping_get(frames, frame)
        if stored is None:
            raise UnknownEntityError(f"unknown type frame {frame!r}")
        return [_operation_parts(operation) for operation in _operations(stored)]

    def _type_frame_matrix(self, type_: Any, frame: Any = "center") -> FloatMatrix:
        frame_name = frame
        if not isinstance(frame_name, str):
            frames = getattr(type_, "frames", None)
            for name, candidate in _mapping_items(frames):
                if candidate is frame_name:
                    frame_name = name
                    break
        curvature, roll = _type_path_values(type_)
        operations = self._type_frame_operations(type_, frame)
        center = apply_type_operations(identity_matrix(), operations, curvature, roll)
        for feature in ("magnetic", "beam"):
            if frame_name not in {f"{feature}_entry", f"{feature}_exit"}:
                continue
            axis = _axis_feature_values(type_, feature)
            if axis is None:  # _type_frame_operations already reports this clearly.
                raise UnknownEntityError(f"type has no {feature} axis")
            _, length, feature_curvature, feature_roll = axis
            direction = -0.5 if frame_name == f"{feature}_entry" else 0.5
            return advance(
                center,
                direction * length,
                feature_curvature,
                feature_roll,
            )
        return center

    def type_frame(self, type_: Type | str, frame: Any = "center") -> Pose:
        """Return a named or implicit frame in type-local coordinates."""

        with self._session():
            resolved = self._resolve_type(type_)
            return _make_pose(self._type_frame_matrix(resolved, frame), "type_local")

    def _object_center_matrix(self, object_: Any) -> FloatMatrix:
        cached = self._object_centers.get(id(object_))
        if cached is not None:
            return cached
        name = self._name_for("object", object_)
        path = f"objects.{name}.position"
        with self._resolving(("object", name), path=path):
            type_ = self._object_type(object_, path=f"objects.{name}.type")
            position = getattr(object_, "position", None)
            if position is None:
                raise EvaluationError("object has no position", path=path)
            desired = self._resolve_transformation(
                position,
                allow_inference=True,
                reference_curve=self._position_reference_curve(position),
                path=path,
            )
            target = getattr(position, "target", "center")
            target_local = self._type_frame_matrix(type_, target)
            center = desired @ _rigid_inverse(target_local)
        self._object_centers[id(object_)] = center
        return center

    def _object_named_frame_matrix(
        self, object_: Any, frame: Any = "center"
    ) -> FloatMatrix:
        center = self._object_center_matrix(object_)
        if frame is None or frame == "center":
            return center.copy()
        type_ = self._object_type(
            object_, path=f"objects.{self._name_for('object', object_)}.type"
        )
        return center @ self._type_frame_matrix(type_, frame)

    def object_frame(self, object_: Object | str, frame: Any = "center") -> Pose:
        """Return an object's world center or another named/implicit frame."""

        with self._session():
            resolved = self._resolve_object(object_)
            return _make_pose(self._object_named_frame_matrix(resolved, frame), "world")

    def object_named_frame(self, object_: Object | str, frame: Any) -> Pose:
        """Explicit alias for resolving a non-centre object frame."""

        return self.object_frame(object_, frame)

    # ------------------------------------------------------------------
    # Exact station inference

    @staticmethod
    def _deduplicate_candidates(
        candidates: list[tuple[float, float]], path_tolerance: float
    ) -> list[tuple[float, float]]:
        if not candidates:
            return []
        candidates.sort(key=lambda item: item[0])
        result = [candidates[0]]
        for station, distance in candidates[1:]:
            previous_station, previous_distance = result[-1]
            if abs(station - previous_station) <= path_tolerance:
                # Junction roots describe the same frame; average the tiny
                # station discrepancy and retain the more accurate distance.
                result[-1] = (
                    0.5 * (station + previous_station),
                    min(distance, previous_distance),
                )
            else:
                result.append((station, distance))
        return result

    def _infer_station(self, curve: Any, point: Any) -> float:
        point = _point3(point)
        cache_key = (id(curve), np.ascontiguousarray(point).tobytes())
        cached = self._station_inference_cache.get(cache_key)
        if cached is not None:
            return cached
        geometry = self._curve_station_geometry(curve)
        boundaries = geometry.boundaries
        total = float(boundaries[-1])
        origin_scale = max(geometry.base_origin_scale, float(np.max(np.abs(point))))
        path_tolerance = geometry.path_tolerance
        geometry_tolerance = max(
            geometry.geometry_scale_tolerance,
            32.0 * _EPS * origin_scale,
        )
        isolated: list[tuple[float, float]] = []
        intervals: list[float] = []

        # Every centreline point in a segment is within half its path length
        # of that segment's midpoint.  The resulting sphere lower bounds are
        # conservative for both lines and arcs.  Visit nearby segments first,
        # and stop only once every remaining segment is too distant to affect
        # either the closest root or its ambiguity tolerance.
        lower_bounds = np.maximum(
            0.0,
            np.linalg.norm(geometry.midpoints - point, axis=1)
            - geometry.sphere_radii
            - geometry_tolerance,
        )
        order = np.argsort(lower_bounds, kind="stable")
        closest_seen = float("inf")

        for index_value in order:
            index = int(index_value)
            if isfinite(closest_seen):
                distance_tolerance = max(
                    geometry_tolerance,
                    1.0e-10 * max(1.0, closest_seen, origin_scale),
                )
                if float(lower_bounds[index]) > closest_seen + distance_tolerance:
                    break

            length = float(geometry.lengths[index])
            angle = float(geometry.angles[index])
            roll = float(geometry.rolls[index])
            start = geometry.starts[index]
            origin = start[:3, 3]
            x_axis, y_axis, tangent = start[:3, 0], start[:3, 1], start[:3, 2]
            q = point - origin
            curvature = float(geometry.curvatures[index])

            # The web implementation and reference conformance notes treat
            # vanishingly small bend angles as straight to avoid enormous arc
            # radii dominating otherwise scale-aware tolerances.
            if abs(angle) < 1.0e-10:
                local = float(np.dot(q, tangent))
                if -path_tolerance <= local <= length + path_tolerance:
                    local = min(max(local, 0.0), length)
                    candidate_origin = advance(start, local, 0.0, roll)[:3, 3]
                    distance = float(np.linalg.norm(point - candidate_origin))
                    isolated.append((float(boundaries[index]) + local, distance))
                    closest_seen = min(closest_seen, distance)
                continue

            normal = -cos(roll) * x_axis - sin(roll) * y_axis
            a = float(np.dot(q, tangent))
            b = float(np.dot(q, normal)) - 1.0 / curvature
            local_scale = max(
                origin_scale, abs(1.0 / curvature), float(np.linalg.norm(q))
            )
            degeneracy_tolerance = max(
                geometry_tolerance,
                32.0 * _EPS * local_scale,
            )
            if abs(a) <= degeneracy_tolerance and abs(b) <= degeneracy_tolerance:
                distance = float(np.linalg.norm(point - geometry.midpoints[index]))
                intervals.append(distance)
                closest_seen = min(closest_seen, distance)
                continue

            base = atan2(b, a) + 0.5 * pi
            low, high = sorted((0.0, angle))
            theta_tolerance = max(1.0e-13, abs(curvature) * path_tolerance)
            k_min = ceil((low - base - theta_tolerance) / pi)
            k_max = floor((high - base + theta_tolerance) / pi)
            for integer in range(k_min, k_max + 1):
                theta = base + integer * pi
                if theta < low and low - theta <= theta_tolerance:
                    theta = low
                elif theta > high and theta - high <= theta_tolerance:
                    theta = high
                local = theta / curvature
                if local < -path_tolerance or local > length + path_tolerance:
                    continue
                local = min(max(local, 0.0), length)
                candidate_origin = advance(start, local, curvature, roll)[:3, 3]
                distance = float(np.linalg.norm(point - candidate_origin))
                isolated.append((float(boundaries[index]) + local, distance))
                closest_seen = min(closest_seen, distance)

        isolated = self._deduplicate_candidates(isolated, path_tolerance)
        all_distances = [distance for _, distance in isolated] + intervals
        curve_name = self._name_for("curve", curve)
        path = f"reference_curves.{curve_name}"
        if not all_distances:
            raise NoStationSolutionError(
                "point has no transverse-plane station on the finite curve", path=path
            )

        closest_distance = min(all_distances)
        distance_tolerance = max(
            geometry_tolerance,
            1.0e-10 * max(1.0, closest_distance, origin_scale),
        )
        if any(
            abs(distance - closest_distance) <= distance_tolerance
            for distance in intervals
        ):
            raise AmbiguousStationError(
                "closest station solution is a continuous interval", path=path
            )
        closest = [
            station
            for station, distance in isolated
            if abs(distance - closest_distance) <= distance_tolerance
        ]
        if len(closest) != 1:
            raise AmbiguousStationError(
                "multiple equidistant closest station solutions", path=path
            )
        station = closest[0]
        if abs(station) <= path_tolerance:
            station = 0.0
        elif abs(station - total) <= path_tolerance:
            station = total
        self._station_inference_cache[cache_key] = station
        return station

    def infer_station(self, curve: Curve | str, point: ArrayLike | Pose) -> float:
        """Infer the unique closest transverse-plane station for *point*.

        Only the finite curve domain is searched.  Analytic roots are generated
        for each straight or circular-arc segment; junction roots are
        de-duplicated before the closest-solution and ambiguity rules are
        applied.
        """

        with self._session():
            resolved = self._resolve_curve(curve)
            return self._infer_station(resolved, point)

    # ------------------------------------------------------------------
    # Viewer-oriented samples

    def sampled_curve(
        self, curve: Curve | str, resolution: int = 128
    ) -> dict[str, Any]:
        """Sample a curve while retaining stations, segment indices, and frames.

        ``resolution`` is the minimum number of intervals over the complete
        finite curve.  Exact segment boundaries are inserted as additional
        samples, which prevents visual shortcuts across small segments.
        """

        try:
            resolution = int(resolution)
        except (TypeError, ValueError) as exc:
            raise EvaluationError(
                "curve resolution must be a positive integer"
            ) from exc
        if resolution < 1:
            raise EvaluationError("curve resolution must be a positive integer")
        with self._session():
            resolved = self._resolve_curve(curve)
            segments, boundaries, _ = self._curve_data(resolved)
            total = boundaries[-1]
            regular = np.linspace(0.0, total, resolution + 1)
            stations = np.unique(
                np.concatenate((regular, np.asarray(boundaries, dtype=float)))
            )
            frames = np.stack(
                [
                    self._curve_frame_matrix(resolved, float(station), False)
                    for station in stations
                ]
            )
            indices = np.searchsorted(
                np.asarray(boundaries[1:]), stations, side="right"
            )
            indices = np.minimum(indices, len(segments) - 1).astype(np.int64)
            return {
                "points": np.array(frames[:, :3, 3], copy=True),
                "stations": stations,
                "segment_indices": indices,
                "frames": frames,
                "curve": self._name_for("curve", resolved),
                "color": getattr(resolved, "color", None),
            }

    def swept_object_mesh(
        self,
        object_: Object | str,
        resolution: int = 32,
        radial_resolution: int = 24,
        *,
        include_metadata: bool = True,
    ) -> dict[str, Any]:
        """Return a triangulated world-space skin for an object's swept shape.

        Set ``include_metadata=False`` when only vertices and faces are needed.
        This avoids calculating vertex normals and retaining sampling arrays,
        which is useful for large viewer scenes.  The default preserves the
        complete historical result.
        """

        with self._session():
            resolved = self._resolve_object(object_)
            type_ = self._object_type(
                resolved, path=f"objects.{self._name_for('object', resolved)}.type"
            )
            mesh = _swept_mesh(
                getattr(type_, "shape", None),
                self._object_center_matrix(resolved),
                resolution=resolution,
                radial_resolution=radial_resolution,
                include_metadata=include_metadata,
            )
            mesh.update(
                {
                    "object": self._name_for("object", resolved),
                    "type": self._name_for("type", type_),
                    "color": getattr(type_, "color", None),
                }
            )
            return mesh


# ---------------------------------------------------------------------------
# Standalone viewer data helpers


def _mesh_normals(vertices: FloatMatrix, faces: NDArray[np.int64]) -> FloatMatrix:
    normals = np.zeros_like(vertices)
    if len(faces):
        triangles = vertices[faces]
        face_normals = np.cross(
            triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]
        )
        for corner in range(3):
            np.add.at(normals, faces[:, corner], face_normals)
    lengths = np.linalg.norm(normals, axis=1)
    nonzero = lengths > 64.0 * _EPS
    normals[nonzero] /= lengths[nonzero, None]
    return normals


def _swept_mesh(
    shape_value: Any,
    center_matrix: ArrayLike,
    *,
    resolution: int,
    radial_resolution: int,
    include_metadata: bool = True,
) -> dict[str, Any]:
    try:
        resolution = int(resolution)
        radial_resolution = int(radial_resolution)
    except (TypeError, ValueError) as exc:
        raise EvaluationError("mesh resolutions must be integers") from exc
    if resolution < 1:
        raise EvaluationError("longitudinal mesh resolution must be positive")
    kind, shape = _shape_values(shape_value)
    if kind == "cylinder" and radial_resolution < 3:
        raise EvaluationError("cylinder radial resolution must be at least 3")
    center = _matrix4(center_matrix)
    dz, curvature, roll = shape["dz"], shape["curvature"], shape["roll"]
    stations = np.linspace(-0.5 * dz, 0.5 * dz, resolution + 1)
    frames = np.stack(
        [advance(center, float(station), curvature, roll) for station in stations]
    )

    if kind == "box":
        half_x, half_y = 0.5 * shape["dx"], 0.5 * shape["dy"]
        cross_section = np.asarray(
            (
                (-half_x, -half_y),
                (half_x, -half_y),
                (half_x, half_y),
                (-half_x, half_y),
            ),
            dtype=float,
        )
        ring_size = 4
        vertex_array = (
            frames[:, None, :3, 3]
            + cross_section[None, :, 0, None] * frames[:, None, :3, 0]
            + cross_section[None, :, 1, None] * frames[:, None, :3, 1]
        ).reshape((-1, 3))
    else:
        radius = shape["r"]
        ring_size = radial_resolution
        angles = np.linspace(0.0, 2.0 * pi, ring_size, endpoint=False)
        cosines = radius * np.cos(angles)
        sines = radius * np.sin(angles)
        rings = (
            frames[:, None, :3, 3]
            + cosines[None, :, None] * frames[:, None, :3, 0]
            + sines[None, :, None] * frames[:, None, :3, 1]
        ).reshape((-1, 3))
        vertex_array = np.concatenate(
            (rings, frames[[0, -1], :3, 3]),
            axis=0,
        )

    cached_faces = _swept_mesh_faces(kind, resolution, ring_size)
    # Public mesh results historically expose mutable arrays. Viewers opt out
    # of metadata and can safely share the immutable topology cache.
    face_array = cached_faces.copy() if include_metadata else cached_faces
    result: dict[str, Any] = {
        "vertices": vertex_array,
        "faces": face_array,
        "kind": kind,
    }
    if include_metadata:
        vertex_stations = np.repeat(stations, ring_size)
        section_indices = np.repeat(
            np.arange(resolution + 1, dtype=np.int64), ring_size
        )
        if kind == "cylinder":
            vertex_stations = np.concatenate((vertex_stations, stations[[0, -1]]))
            section_indices = np.concatenate(
                (section_indices, np.asarray([0, resolution], dtype=np.int64))
            )
        result.update(
            {
                "normals": _mesh_normals(vertex_array, face_array),
                "stations": vertex_stations,
                "section_indices": section_indices,
                "centerline_stations": stations,
                "centerline_frames": frames,
            }
        )
    return result


def _swept_mesh_faces(kind: str, resolution: int, ring_size: int) -> NDArray[np.int64]:
    """Return immutable topology, caching only bounded viewer-sized meshes."""

    triangle_count = 2 * resolution * ring_size + 2 * ring_size
    if triangle_count > 100_000:
        return _build_swept_mesh_faces(kind, resolution, ring_size)
    return _cached_swept_mesh_faces(kind, resolution, ring_size)


@lru_cache(maxsize=64)
def _cached_swept_mesh_faces(
    kind: str, resolution: int, ring_size: int
) -> NDArray[np.int64]:
    return _build_swept_mesh_faces(kind, resolution, ring_size)


def _build_swept_mesh_faces(
    kind: str, resolution: int, ring_size: int
) -> NDArray[np.int64]:
    """Build topology whose ultimate buffer is immutable Python bytes."""

    faces: list[tuple[int, int, int]] = []
    for section in range(resolution):
        first, second = section * ring_size, (section + 1) * ring_size
        for side in range(ring_size):
            nxt = (side + 1) % ring_size
            faces.append((first + side, first + nxt, second + nxt))
            faces.append((first + side, second + nxt, second + side))
    if kind == "box":
        faces.extend(((0, 2, 1), (0, 3, 2)))
        end = resolution * ring_size
        faces.extend(((end, end + 1, end + 2), (end, end + 2, end + 3)))
    else:
        start_center = (resolution + 1) * ring_size
        end_center = start_center + 1
        end_ring = resolution * ring_size
        for side in range(ring_size):
            nxt = (side + 1) % ring_size
            faces.append((start_center, nxt, side))
            faces.append((end_center, end_ring + side, end_ring + nxt))
    result = np.asarray(faces, dtype=np.int64).reshape((-1, 3))
    # A readonly ndarray that owns its buffer can be made writable again.
    # A bytes-backed array keeps shared cache entries immutable to callers.
    return np.frombuffer(result.tobytes(), dtype=np.int64).reshape((-1, 3))


def _resolver_for(entity: Any, resolver: Resolver | None) -> Resolver:
    if resolver is not None:
        return resolver
    return Resolver(getattr(entity, "layout", None))


def sampled_curve(
    curve: Curve,
    resolution: int = 128,
    *,
    resolver: Resolver | None = None,
) -> dict[str, Any]:
    """Standalone wrapper for :meth:`Resolver.sampled_curve`.

    The curve's enclosing layout is used automatically.  Supplying ``resolver``
    is useful when a viewer samples several entities in one explicit context.
    """

    return _resolver_for(curve, resolver).sampled_curve(curve, resolution)


def swept_type_mesh(
    type_: Type,
    resolution: int = 32,
    radial_resolution: int = 24,
    *,
    matrix: ArrayLike | None = None,
    include_metadata: bool = True,
) -> dict[str, Any]:
    """Triangulate a type's swept shape in type-local or supplied coordinates."""

    mesh = _swept_mesh(
        getattr(type_, "shape", None),
        identity_matrix() if matrix is None else matrix,
        resolution=resolution,
        radial_resolution=radial_resolution,
        include_metadata=include_metadata,
    )
    mesh.update(
        {
            "type": getattr(type_, "name", None),
            "color": getattr(type_, "color", None),
            "space": "type_local" if matrix is None else "world",
        }
    )
    return mesh


def swept_object_mesh(
    object_: Object,
    resolution: int = 32,
    radial_resolution: int = 24,
    *,
    resolver: Resolver | None = None,
    include_metadata: bool = True,
) -> dict[str, Any]:
    """Standalone wrapper for :meth:`Resolver.swept_object_mesh`."""

    return _resolver_for(object_, resolver).swept_object_mesh(
        object_,
        resolution=resolution,
        radial_resolution=radial_resolution,
        include_metadata=include_metadata,
    )
