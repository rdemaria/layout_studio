"""Interactive Matplotlib projections for :mod:`layout_studio`.

The modelling package intentionally has no mandatory plotting dependency.
Matplotlib is therefore imported only when :class:`LayoutViewer2D` is
constructed.  The viewer consumes evaluated geometry from
:class:`.resolver.Resolver`; scoped views may resolve dependencies, but never
draw them unless they are themselves in the requested scope.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from types import SimpleNamespace
from typing import Any

try:  # Python 3.10 compatibility
    from typing import Self
except ImportError:  # pragma: no cover - exercised only on Python 3.10
    from typing_extensions import Self

import numpy as np

_DEFAULT_BACKGROUND = "#090e16"
_CURVE_FALLBACK = "#68d5c8"
_OBJECT_FALLBACK = "#f0a84b"
_SELECTION_COLOR = "#ffbe5d"
_AXIS_COLORS = ("#f07178", "#8bd49c", "#6cb6ff")
_AXIS_NAMES = ("x", "y", "s")
_WORLD_AXES = {"x": 0, "y": 1, "z": 2}
_VALID_PROJECTIONS = ("xy", "xz", "yx", "yz", "zx", "zy")
_OBJECT_BATCH_THRESHOLD = 128
_CURVE_SAMPLE_BUDGET = 8192
_OBJECT_SECTION_BUDGET = 4096
_OBJECT_RADIAL_BUDGET = 16384
_NAMED_FRAME_ARROW_FRACTION = 0.05
_ACTIVE_FRAME_ARROW_FRACTION = 0.075


def _require_matplotlib() -> SimpleNamespace:
    """Import the optional Matplotlib pieces used by the viewer."""

    try:
        from matplotlib import colors, pyplot  # type: ignore[import-not-found]
        from matplotlib.collections import (  # type: ignore[import-not-found]
            LineCollection,
            PolyCollection,
        )
    except (ImportError, ModuleNotFoundError) as exc:
        raise ImportError(
            "The 2D viewer requires the optional Matplotlib dependency. "
            "Install it with `python -m pip install 'layout-studio[plot2d]'` "
            "or `python -m pip install matplotlib`."
        ) from exc
    return SimpleNamespace(
        pyplot=pyplot,
        colors=colors,
        LineCollection=LineCollection,
        PolyCollection=PolyCollection,
    )


def _normalise_projection(value: Any) -> str:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        if len(value) != 2:
            raise ValueError("projection must contain exactly two world axes")
        value = "".join(str(axis) for axis in value)
    text = str(value).strip().lower().replace("-", "").replace("_", "")
    if text not in _VALID_PROJECTIONS:
        valid = ", ".join(_VALID_PROJECTIONS)
        raise ValueError(f"projection must be one of: {valid}")
    return text


def _data_field(data: Any, name: str, default: Any = None) -> Any:
    if isinstance(data, Mapping):
        return data.get(name, default)
    return getattr(data, name, default)


def _mapping_items(value: Any) -> list[tuple[str, Any]]:
    if value is None:
        return []
    if hasattr(value, "items"):
        return [(str(name), entity) for name, entity in value.items()]
    result: list[tuple[str, Any]] = []
    for index, entity in enumerate(value):
        name = getattr(entity, "name", None)
        result.append((str(name) if name is not None else str(index), entity))
    return result


def _as_points(value: Any) -> np.ndarray:
    points = np.asarray(value, dtype=float)
    if points.size == 0:
        return np.empty((0, 3), dtype=float)
    points = np.reshape(points, (-1, 3))
    if not np.all(np.isfinite(points)):
        raise ValueError("viewer geometry contains a non-finite point")
    return points


def _pose_matrix(pose: Any) -> np.ndarray:
    """Return a homogeneous pose matrix with local axes in its columns."""

    matrix = getattr(pose, "matrix", None)
    if matrix is not None:
        result = np.asarray(matrix, dtype=float)
        if result.shape == (4, 4):
            return result
    origin = np.asarray(
        getattr(pose, "origin", getattr(pose, "o", (0, 0, 0))), dtype=float
    )
    x_axis = np.asarray(getattr(pose, "x", (1, 0, 0)), dtype=float)
    y_axis = np.asarray(getattr(pose, "y", (0, 1, 0)), dtype=float)
    tangent = np.asarray(
        getattr(pose, "tangent", getattr(pose, "s", (0, 0, 1))), dtype=float
    )
    result = np.eye(4, dtype=float)
    result[:3, 0] = x_axis
    result[:3, 1] = y_axis
    result[:3, 2] = tangent
    result[:3, 3] = origin
    return result


def _pose_origin(pose: Any) -> np.ndarray:
    return _pose_matrix(pose)[:3, 3]


def _pose_axes(pose: Any) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    matrix = _pose_matrix(pose)
    return matrix[:3, 0], matrix[:3, 1], matrix[:3, 2]


def _normalised(vector: np.ndarray, fallback: Sequence[float]) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    if norm <= 1e-14:
        return np.asarray(fallback, dtype=float)
    return vector / norm


def _madx_euler(pose: Any) -> tuple[float, float, float]:
    """Return the MAD-X ``theta, phi, psi`` convention in radians."""

    value = getattr(pose, "euler", None)
    if value is not None:
        try:
            result = tuple(float(item) for item in value)
        except (TypeError, ValueError):
            pass
        else:
            if len(result) == 3 and all(math.isfinite(item) for item in result):
                return result  # type: ignore[return-value]

    x_axis, _y_axis, tangent = _pose_axes(pose)
    tangent = _normalised(tangent, (0.0, 0.0, 1.0))
    x_axis = x_axis - tangent * float(np.dot(x_axis, tangent))
    x_axis = _normalised(x_axis, (1.0, 0.0, 0.0))
    y_axis = _normalised(np.cross(tangent, x_axis), (0.0, 1.0, 0.0))
    horizontal = math.hypot(float(tangent[0]), float(tangent[2]))
    phi = math.atan2(float(tangent[1]), horizontal)
    if horizontal > 1e-10:
        theta = math.atan2(float(tangent[0]), float(tangent[2]))
        psi = math.atan2(float(x_axis[1]), float(y_axis[1]))
    else:
        theta = 0.0
        psi = math.atan2(-float(y_axis[0]), float(x_axis[0]))
    return theta, phi, psi


def _safe_name(entity: Any, fallback: str) -> str:
    value = getattr(entity, "name", None)
    return fallback if value is None else str(value)


@dataclass
class _CurveVisual:
    name: str
    entity: Any
    points: np.ndarray
    projected: np.ndarray
    stations: np.ndarray
    segment_indices: np.ndarray
    line: Any
    halo: Any
    color: tuple[float, float, float]
    bounds: np.ndarray


@dataclass
class _ObjectVisual:
    name: str
    entity: Any
    vertices: np.ndarray
    projected: np.ndarray
    faces: np.ndarray
    feature_edges: np.ndarray
    fill: Any
    edges: Any
    color: tuple[float, float, float]
    pose: Any
    bounds: np.ndarray
    batch_index: int | None = None
    batch_indices: np.ndarray | None = None


@dataclass
class _PickTarget:
    kind: str
    name: str
    entity: Any
    pose: Any = None
    owner: Any = None
    frame_name: str | None = None


@dataclass
class _Selection:
    kind: str
    name: str
    entity: Any
    pose: Any
    owner: Any = None
    frame_name: str | None = None
    station: float | None = None
    segment_index: int | None = None


@dataclass
class _PickCandidate:
    selection: _Selection
    depth: float
    distance: float
    priority: int
    order: int


@dataclass
class _BatchedFrameVisual:
    target: _PickTarget
    projected: np.ndarray
    bounds: np.ndarray
    depth: float


@dataclass
class _DisplayAxes:
    artist: Any
    origins: np.ndarray
    directions: np.ndarray
    active: bool = False


class LayoutViewer2D:
    """Interactive orthographic projection of a layout or a scoped subset.

    The first character of ``projection`` is the horizontal world axis and
    the second is the vertical world axis.  The accepted ordered projections
    are ``xy``, ``xz``, ``yx``, ``yz``, ``zx`` and ``zy``.

    ``curves`` and ``objects`` can each be a visibility boolean or an exact
    entity/name (or iterable of them).  Thus ``curves=[curve], objects=[]``
    evaluates whatever ``curve`` depends on while drawing only that curve.
    Matplotlib is imported lazily, and ``show=False`` never opens a GUI or
    enters an event loop, which makes an Agg-backed viewer safe in headless
    tests and batch jobs.

    Geometry detail is automatic when ``curve_resolution``,
    ``object_resolution`` or ``radial_resolution`` is ``None`` (or ``"auto"``).
    Explicit positive integers always take precedence.  Large object scopes
    are rendered as one collection of projected silhouettes; set
    ``batch_objects=False`` to retain the face-by-face small-scene rendering.
    Named frames also default adaptively: visible for small scopes and lazy/off
    for a batched layout, while an explicit ``frames`` value is honoured.
    """

    def __init__(
        self,
        layout: Any,
        *,
        projection: str | Sequence[str] = "xy",
        scope: Any = None,
        curves: bool | Any | Iterable[Any] = True,
        objects: bool | Any | Iterable[Any] = True,
        beam_frames: bool = False,
        frames: bool | None = None,
        selection: Any = None,
        show: bool = True,
        figsize: tuple[float, float] = (10.0, 7.2),
        dpi: float = 100.0,
        background: Any = _DEFAULT_BACKGROUND,
        curves_visible: bool | None = None,
        objects_visible: bool | None = None,
        curve_resolution: int | str | None = None,
        object_resolution: int | str | None = None,
        radial_resolution: int | str | None = None,
        batch_objects: bool | None = None,
        batch_threshold: int = _OBJECT_BATCH_THRESHOLD,
        hover_interval: float = 1.0 / 30.0,
        ax: Any = None,
    ) -> None:
        self.layout = layout
        self.projection = _normalise_projection(projection)
        self.axis_indices = tuple(_WORLD_AXES[axis] for axis in self.projection)
        self.depth_axis = next(
            index for index in range(3) if index not in self.axis_indices
        )
        self.figsize = self._validate_figsize(figsize)
        self.dpi = self._validate_dpi(dpi)
        self.background = background
        self._requested_curve_resolution = self._validate_resolution(
            curve_resolution, "curve_resolution", minimum=1
        )
        self._requested_object_resolution = self._validate_resolution(
            object_resolution, "object_resolution", minimum=1
        )
        self._requested_radial_resolution = self._validate_resolution(
            radial_resolution, "radial_resolution", minimum=3
        )
        self.batch_threshold = self._validate_positive_integer(
            batch_threshold, "batch_threshold"
        )
        self.hover_interval = self._validate_nonnegative_number(
            hover_interval, "hover_interval"
        )
        self._mpl = _require_matplotlib()
        self._plt = self._mpl.pyplot

        from .resolver import Resolver

        self.resolver = Resolver(layout)

        all_curves = _mapping_items(getattr(layout, "curves", None))
        if not all_curves:
            all_curves = _mapping_items(getattr(layout, "reference_curves", None))
        all_objects = _mapping_items(getattr(layout, "objects", None))
        scope_curves, scope_objects = self._scope_filter(scope, all_curves, all_objects)
        self._curve_items, bool_curves = self._resolve_layer_scope(
            curves, scope_curves, "curve"
        )
        self._object_items, bool_objects = self._resolve_layer_scope(
            objects, scope_objects, "object"
        )
        self.curve_scope = tuple(entity for _, entity in self._curve_items)
        self.object_scope = tuple(entity for _, entity in self._object_items)
        self._curve_count = len(self._curve_items)
        self._object_count = len(self._object_items)
        curve_count = max(1, len(self._curve_items))
        object_count = max(1, len(self._object_items))
        self.curve_resolution = (
            self._requested_curve_resolution
            if self._requested_curve_resolution is not None
            else max(4, min(128, _CURVE_SAMPLE_BUDGET // curve_count))
        )
        self.object_resolution = (
            self._requested_object_resolution
            if self._requested_object_resolution is not None
            else max(1, min(32, _OBJECT_SECTION_BUDGET // object_count))
        )
        self.radial_resolution = (
            self._requested_radial_resolution
            if self._requested_radial_resolution is not None
            else max(8, min(24, _OBJECT_RADIAL_BUDGET // object_count))
        )
        self.curve_resolution_effective = self.curve_resolution
        self.object_resolution_effective = self.object_resolution
        self.radial_resolution_effective = self.radial_resolution
        if batch_objects is not None and not isinstance(
            batch_objects, (bool, np.bool_)
        ):
            raise TypeError("batch_objects must be a boolean or None")
        self.batch_objects = (
            len(self._object_items) >= self.batch_threshold
            if batch_objects is None
            else bool(batch_objects)
        )
        self.batched_objects = self.batch_objects
        self.curves_visible = (
            bool_curves if curves_visible is None else bool(curves_visible)
        )
        self.objects_visible = (
            bool_objects if objects_visible is None else bool(objects_visible)
        )
        self.beam_frames_visible = bool(beam_frames)
        # Named frames are useful by default for small scoped views, but tens of
        # thousands of axes, markers, and labels overwhelm interactive backends.
        # ``None`` therefore means an adaptive default; an explicit boolean is
        # always honoured.
        self.frames_visible = (
            len(self._object_items) < self.batch_threshold
            if frames is None
            else bool(frames)
        )
        self.grid_visible = True

        self._closed = False
        self._draw_started = False
        self._selection: _Selection | None = None
        self._hover: _Selection | None = None
        self._curve_visuals: dict[str, _CurveVisual] = {}
        self._object_visuals: dict[str, _ObjectVisual] = {}
        self._feature_edge_cache: dict[tuple[int, int, int], np.ndarray] = {}
        self._pick_targets: dict[Any, _PickTarget] = {}
        self._curve_artists: list[Any] = []
        self._object_artists: list[Any] = []
        self._beam_frame_artists: list[Any] = []
        self._named_frame_artists: list[Any] = []
        self._bounds_low = np.full(3, np.inf, dtype=float)
        self._bounds_high = np.full(3, -np.inf, dtype=float)
        self._callbacks: list[int] = []
        self._axes_callbacks: list[int] = []
        self._display_axes: list[_DisplayAxes] = []
        self._resolver_session: Any = None
        self._named_frames_built = False
        self._beam_frames_built = False
        self._batch_entries: list[
            tuple[str, np.ndarray, tuple[float, ...], tuple[float, ...], float]
        ] = []
        self._batch_object_collection: Any = None
        self._batch_base_facecolors = np.empty((0, 4), dtype=float)
        self._batch_base_edgecolors = np.empty((0, 4), dtype=float)
        self._batch_current_facecolors = np.empty((0, 4), dtype=float)
        self._batch_current_edgecolors = np.empty((0, 4), dtype=float)
        self._object_pick_visuals: list[_ObjectVisual] = []
        self._object_pick_bounds = np.empty((0, 4), dtype=float)
        self._batched_frame_visuals: list[_BatchedFrameVisual] = []
        self._batched_frame_bounds = np.empty((0, 4), dtype=float)
        self._last_hover_time = -math.inf
        self._last_hover_pixel: tuple[float, float] | None = None
        self._blit_enabled = False
        self._blit_suspended = False
        self._blit_background: Any = None
        self._hover_overlay_artists: tuple[Any, ...] = ()
        self._owns_figure = False
        self.figure = None
        self.ax = None
        self.axes = None
        self.canvas = None
        try:
            # Validate before styling caller-owned axes. Keep this resolver
            # session for the complete viewer snapshot after validation.
            session = self.resolver._session()
            session.__enter__()
            self._resolver_session = session
            self._build_figure(ax)
            self._blit_enabled = bool(getattr(self.canvas, "supports_blit", False))
            self._build_entity_geometry()
            self._finish_bounds()
            self._ensure_object_frames(
                named=self.frames_visible, beam=self.beam_frames_visible
            )
            self._finish_bounds()
            self._build_overlays()
            self._install_interaction()
            self._apply_layer_visibility()
            self.fit()

            self.curve_artists = {
                name: visual.line for name, visual in self._curve_visuals.items()
            }
            self.object_artists = {
                name: visual.fill for name, visual in self._object_visuals.items()
            }
            # Familiar aliases for code that also deals with the VTK viewer.
            self.curve_actors = self.curve_artists
            self.object_actors = self.object_artists

            if selection is not None:
                self.select(selection)
            if show:
                self.show()
        except BaseException:
            self._disable_blit()
            self._disconnect_callbacks()
            self._close_resolver_session()
            if self._owns_figure and self.figure is not None:
                self._plt.close(self.figure)
            self._closed = True
            self._release_scene_references(clear_figure=self._owns_figure)
            raise

    @staticmethod
    def _validate_resolution(value: Any, name: str, *, minimum: int) -> int | None:
        if value is None or (isinstance(value, str) and value.lower() == "auto"):
            return None
        if isinstance(value, (bool, np.bool_)):
            raise TypeError(f"{name} must be an integer or None")
        try:
            result = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} must be an integer or None") from exc
        if result != value or result < minimum:
            qualifier = "positive" if minimum == 1 else f"at least {minimum}"
            raise ValueError(f"{name} must be {qualifier} or None")
        return result

    @staticmethod
    def _validate_positive_integer(value: Any, name: str) -> int:
        if isinstance(value, (bool, np.bool_)):
            raise TypeError(f"{name} must be a positive integer")
        try:
            result = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} must be a positive integer") from exc
        if result != value or result < 1:
            raise ValueError(f"{name} must be a positive integer")
        return result

    @staticmethod
    def _validate_nonnegative_number(value: Any, name: str) -> float:
        try:
            result = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} must be a non-negative number") from exc
        if not math.isfinite(result) or result < 0.0:
            raise ValueError(f"{name} must be a non-negative number")
        return result

    @staticmethod
    def _validate_figsize(value: Any) -> tuple[float, float]:
        try:
            width, height = value
            width, height = float(width), float(height)
        except (TypeError, ValueError) as exc:
            raise ValueError("figsize must be a (width, height) pair") from exc
        if not math.isfinite(width + height) or width <= 0 or height <= 0:
            raise ValueError("figsize dimensions must be finite and positive")
        return width, height

    @staticmethod
    def _validate_dpi(value: Any) -> float:
        try:
            result = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("dpi must be a positive number") from exc
        if not math.isfinite(result) or result <= 0:
            raise ValueError("dpi must be a positive number")
        return result

    def _scope_filter(
        self,
        scope: Any,
        all_curves: list[tuple[str, Any]],
        all_objects: list[tuple[str, Any]],
    ) -> tuple[list[tuple[str, Any]], list[tuple[str, Any]]]:
        if scope is None or scope is self.layout:
            return all_curves, all_objects
        curve_by_name = dict(all_curves)
        object_by_name = dict(all_objects)
        curve_ids = {id(entity): name for name, entity in all_curves}
        object_ids = {id(entity): name for name, entity in all_objects}
        curves: list[tuple[str, Any]] = []
        objects: list[tuple[str, Any]] = []
        for value in self._as_scope_values(scope):
            if isinstance(value, str):
                if value.startswith("curve:"):
                    name = value[6:]
                    if name not in curve_by_name:
                        raise KeyError(f"unknown curve {name!r}")
                    curves.append((name, curve_by_name[name]))
                    continue
                if value.startswith("object:"):
                    name = value[7:]
                    if name not in object_by_name:
                        raise KeyError(f"unknown object {name!r}")
                    objects.append((name, object_by_name[name]))
                    continue
                in_curves = value in curve_by_name
                in_objects = value in object_by_name
                if in_curves and in_objects:
                    raise ValueError(
                        f"scope name {value!r} is ambiguous; use 'curve:' or 'object:'"
                    )
                if in_curves:
                    curves.append((value, curve_by_name[value]))
                elif in_objects:
                    objects.append((value, object_by_name[value]))
                else:
                    raise KeyError(f"unknown curve or object {value!r}")
                continue
            identity = id(value)
            if identity in curve_ids:
                curves.append((curve_ids[identity], value))
            elif identity in object_ids:
                objects.append((object_ids[identity], value))
            else:
                raise ValueError("scope entities must belong to this layout")
        return self._deduplicate(curves), self._deduplicate(objects)

    @staticmethod
    def _as_scope_values(value: Any) -> list[Any]:
        if isinstance(value, (str, bytes)):
            return [value]
        if isinstance(value, Mapping):
            return list(value.values())
        try:
            return list(iter(value))
        except TypeError:
            return [value]

    @staticmethod
    def _deduplicate(items: list[tuple[str, Any]]) -> list[tuple[str, Any]]:
        result: list[tuple[str, Any]] = []
        seen: set[int] = set()
        for name, entity in items:
            if id(entity) not in seen:
                seen.add(id(entity))
                result.append((name, entity))
        return result

    def _resolve_layer_scope(
        self,
        value: Any,
        available: list[tuple[str, Any]],
        kind: str,
    ) -> tuple[list[tuple[str, Any]], bool]:
        if isinstance(value, (bool, np.bool_)):
            return list(available), bool(value)
        if value is None:
            return list(available), True
        by_name = dict(available)
        by_identity = {id(entity): name for name, entity in available}
        result: list[tuple[str, Any]] = []
        for requested in self._as_scope_values(value):
            if isinstance(requested, str):
                name = requested.removeprefix(f"{kind}:")
                if name not in by_name:
                    raise KeyError(f"unknown {kind} {name!r} in viewer scope")
                result.append((name, by_name[name]))
            elif id(requested) in by_identity:
                result.append((by_identity[id(requested)], requested))
            else:
                raise ValueError(f"{kind} scope entities must belong to this layout")
        return self._deduplicate(result), True

    # ---------------------------------------------------------------- setup

    def _build_figure(self, supplied_axes: Any) -> None:
        if supplied_axes is None:
            self.figure, self.ax = self._plt.subplots(
                figsize=self.figsize, dpi=self.dpi
            )
            self._owns_figure = True
        else:
            figure = getattr(supplied_axes, "figure", None)
            if figure is None or getattr(figure, "canvas", None) is None:
                raise TypeError("ax must be a Matplotlib Axes")
            self.figure, self.ax = figure, supplied_axes
            self._owns_figure = False
        self.axes = self.ax
        self.canvas = self.figure.canvas
        background = self._color(self.background, _DEFAULT_BACKGROUND)
        self.figure.patch.set_facecolor(background)
        self.ax.set_facecolor(self._lighten(background, 0.035))
        self.ax.set_title(f"Layout Studio — {self.projection.upper()} projection")
        self.ax.set_xlabel(f"{self.projection[0].upper()} [m]")
        self.ax.set_ylabel(f"{self.projection[1].upper()} [m]")
        self.ax.tick_params(colors="#aab9c3")
        self.ax.xaxis.label.set_color("#d6e2e8")
        self.ax.yaxis.label.set_color("#d6e2e8")
        self.ax.title.set_color("#e2edf2")
        for spine in self.ax.spines.values():
            spine.set_color("#60758a")

    def _color(self, value: Any, fallback: Any) -> tuple[float, float, float]:
        try:
            return tuple(float(item) for item in self._mpl.colors.to_rgb(value))
        except (TypeError, ValueError):
            if value == fallback:
                return (1.0, 1.0, 1.0)
            return self._color(fallback, "#ffffff")

    @staticmethod
    def _lighten(color: Sequence[float], amount: float) -> tuple[float, float, float]:
        return tuple(
            min(1.0, float(channel) + (1.0 - float(channel)) * amount)
            for channel in color
        )

    def _project(self, points: Any) -> np.ndarray:
        values = _as_points(points)
        return values[:, self.axis_indices]

    def _reference_arrow_pixels(self, *, active: bool = False) -> float:
        """Return a bounded arrow length relative to the visible axes area."""

        width, height = (float(value) for value in self.ax.bbox.size)
        if not math.isfinite(width) or not math.isfinite(height):
            width = height = 1.0
        shortest = max(1.0, min(width, height))
        dpi_scale = max(0.5, float(self.figure.dpi) / 100.0)
        if active:
            fraction, minimum, maximum = _ACTIVE_FRAME_ARROW_FRACTION, 24.0, 72.0
        else:
            fraction, minimum, maximum = _NAMED_FRAME_ARROW_FRACTION, 18.0, 56.0
        return float(
            np.clip(fraction * shortest, minimum * dpi_scale, maximum * dpi_scale)
        )

    def _register_display_axes(
        self,
        artist: Any,
        origins: Any,
        directions: Any,
        *,
        active: bool = False,
    ) -> _DisplayAxes:
        display_axes = _DisplayAxes(
            artist,
            np.asarray(origins, dtype=float).reshape(-1, 2),
            np.asarray(directions, dtype=float).reshape(-1, 2),
            active,
        )
        self._display_axes.append(display_axes)
        self._update_display_axes(display_axes)
        return display_axes

    def _update_display_axes(self, display_axes: _DisplayAxes) -> None:
        """Map projected directions to a stable, viewport-relative length."""

        origins = display_axes.origins
        directions = display_axes.directions
        if len(origins) == 0:
            display_axes.artist.set_segments([])
            return
        transform = self.ax.transData
        start_pixels = transform.transform(origins)
        direction_pixels = transform.transform(origins + directions) - start_pixels
        norms = np.linalg.norm(direction_pixels, axis=1)
        endpoints_pixels = start_pixels.copy()
        visible = np.isfinite(norms) & (norms > 1e-9)
        if np.any(visible):
            length = self._reference_arrow_pixels(active=display_axes.active)
            endpoints_pixels[visible] += (
                direction_pixels[visible] / norms[visible, np.newaxis] * length
            )
        endpoints = transform.inverted().transform(endpoints_pixels)
        display_axes.artist.set_segments(np.stack((origins, endpoints), axis=1))

    def _refresh_reference_arrows(self) -> None:
        for display_axes in self._display_axes:
            self._update_display_axes(display_axes)
        self._position_local_axis_labels()

    def _position_local_axis_labels(self) -> None:
        local_axes = getattr(self, "local_axes", None)
        labels = getattr(self, "local_axis_labels", ())
        if local_axes is None:
            return
        for label, segment in zip(labels, local_axes.get_segments()):
            endpoint = segment[-1]
            label.set_position((float(endpoint[0]), float(endpoint[1])))

    def _build_entity_geometry(self) -> None:
        # Keep one resolver session for the lifetime of this geometry snapshot.
        # Besides making the initial build linear for dependency-linked layouts,
        # this prevents hover callbacks from validating a 10k-object model on
        # every call to ``curve_frame``.
        session = getattr(self.resolver, "_session", None)
        if callable(session) and self._resolver_session is None:
            self._resolver_session = session()
            self._resolver_session.__enter__()
        try:
            for fallback_name, curve in self._curve_items:
                self._add_curve(_safe_name(curve, fallback_name), curve)
            for fallback_name, obj in self._object_items:
                self._add_object(_safe_name(obj, fallback_name), obj)
            if self.batch_objects:
                self._finish_object_batch()
            self._object_pick_visuals = list(self._object_visuals.values())
            if self._object_pick_visuals:
                self._object_pick_bounds = np.stack(
                    [visual.bounds for visual in self._object_pick_visuals]
                )
        except BaseException:
            self._close_resolver_session()
            raise

    def _add_curve(self, name: str, curve: Any) -> None:
        data = self.resolver.sampled_curve(
            curve, resolution=self.curve_resolution_effective
        )
        points = _as_points(_data_field(data, "points", ()))
        if not len(points):
            return
        projected = self._project(points)
        stations = np.asarray(
            _data_field(data, "stations", np.arange(len(points))), dtype=float
        ).reshape(-1)
        if len(stations) != len(points):
            stations = np.linspace(
                0.0, float(getattr(curve, "length", 0.0)), len(points)
            )
        indices = np.asarray(
            _data_field(data, "segment_indices", np.zeros(len(points))), dtype=int
        ).reshape(-1)
        if len(indices) != len(points):
            indices = np.zeros(len(points), dtype=int)
        color = self._color(getattr(curve, "color", None), _CURVE_FALLBACK)
        (halo,) = self.ax.plot(
            projected[:, 0],
            projected[:, 1],
            color=color,
            alpha=0.18,
            linewidth=8.0,
            solid_capstyle="round",
            zorder=2,
        )
        (line,) = self.ax.plot(
            projected[:, 0],
            projected[:, 1],
            color=color,
            linewidth=2.6,
            solid_capstyle="round",
            picker=6,
            zorder=4,
        )
        line.set_gid(f"curve:{name}")
        low, high = np.min(projected, axis=0), np.max(projected, axis=0)
        visual = _CurveVisual(
            name,
            curve,
            points,
            projected,
            stations,
            indices,
            line,
            halo,
            color,
            np.asarray([low[0], high[0], low[1], high[1]], dtype=float),
        )
        self._curve_visuals[name] = visual
        self._curve_artists.extend((halo, line))
        self._pick_targets[line] = _PickTarget("curve", name, curve)
        self._accumulate_bounds(points)

    def _add_object(self, name: str, obj: Any) -> None:
        mesh_resolution = self._object_mesh_resolution(obj)
        data = self.resolver.swept_object_mesh(
            obj,
            resolution=mesh_resolution,
            radial_resolution=self.radial_resolution_effective,
            include_metadata=False,
        )
        vertices = _as_points(_data_field(data, "vertices", ()))
        faces = np.asarray(_data_field(data, "faces", ()), dtype=np.int64).reshape(
            (-1, 3)
        )
        if not len(vertices):
            return
        if len(faces) and (np.min(faces) < 0 or np.max(faces) >= len(vertices)):
            raise ValueError("object mesh face contains an invalid vertex index")
        projected = self._project(vertices)
        bend_angle = self._shape_bend_angle(obj) if self.batch_objects else 0.0
        straight_batch = self.batch_objects and bend_angle <= 1.0e-12
        if straight_batch:
            front_faces = np.empty((0, 3), dtype=int)
        else:
            triangles = vertices[faces]
            normals = np.cross(
                triangles[:, 1] - triangles[:, 0],
                triangles[:, 2] - triangles[:, 0],
            )
            # Draw only the front-facing skin. Painting both translucent sides
            # makes a projected solid spuriously opaque.
            front_faces = faces[normals[:, self.depth_axis] > 1e-14]
            if len(front_faces):
                order = np.argsort(
                    np.mean(vertices[front_faces, self.depth_axis], axis=1),
                    kind="stable",
                )
                front_faces = front_faces[order]
        type_ = self._object_type(obj)
        color_value = _data_field(data, "color", getattr(type_, "color", None))
        color = self._color(color_value, _OBJECT_FALLBACK)
        edge_key = (id(type_), mesh_resolution, self.radial_resolution_effective)
        edge_pairs = self._feature_edge_cache.get(edge_key)
        if edge_pairs is None:
            edge_pairs = np.asarray(
                self._feature_edges(vertices, faces), dtype=np.int64
            ).reshape((-1, 2))
            edge_pairs.setflags(write=False)
            self._feature_edge_cache[edge_key] = edge_pairs
        edge_color = self._lighten(color, 0.24)
        low, high = np.min(projected, axis=0), np.max(projected, axis=0)
        bounds = np.asarray([low[0], high[0], low[1], high[1]], dtype=float)

        fill = edges = None
        if self.batch_objects:
            if straight_batch:
                # A straight convex extrusion has one exact projected hull.
                render_faces = [self._convex_hull(projected)]
                render_depths = [float(np.mean(vertices[:, self.depth_axis]))]
            else:
                # A curved extrusion need not be convex (a semicircle is the
                # clearest counterexample).  Keep its true projected visible
                # skin inside the shared collection so empty interiors remain
                # empty while artist count stays constant.
                render_faces = [projected[face] for face in front_faces]
                render_depths = [
                    float(np.mean(vertices[face, self.depth_axis]))
                    for face in front_faces
                ]
                if not render_faces:
                    render_faces = [self._convex_hull(projected)]
                    render_depths = [float(np.mean(vertices[:, self.depth_axis]))]
            self._batch_entries.extend(
                (
                    name,
                    polygon,
                    (*color, 0.20),
                    (*edge_color, 0.68),
                    depth,
                )
                for polygon, depth in zip(render_faces, render_depths)
            )
        else:
            polygons = [projected[face] for face in front_faces]
            fill = self._mpl.PolyCollection(
                polygons,
                facecolors=[(*color, 0.20)],
                edgecolors="none",
                closed=True,
                zorder=3,
            )
            self.ax.add_collection(fill)
            segments = [
                [projected[first], projected[second]] for first, second in edge_pairs
            ]
            edges = self._mpl.LineCollection(
                segments,
                colors=[(*edge_color, 0.68)],
                linewidths=0.7,
                zorder=3.2,
            )
            self.ax.add_collection(edges)
            fill.set_gid(f"object:{name}")
            edges.set_gid(f"object:{name}:edges")
        pose = self.resolver.object_frame(obj)
        visual = _ObjectVisual(
            name,
            obj,
            vertices,
            projected,
            faces,
            edge_pairs,
            fill,
            edges,
            color,
            pose,
            bounds,
        )
        self._object_visuals[name] = visual
        if not self.batch_objects:
            self._object_artists.extend((fill, edges))
        self._accumulate_bounds(vertices)

    def _object_mesh_resolution(self, obj: Any) -> int:
        """Return exact requested detail or a bend-aware automatic LOD."""

        if self._requested_object_resolution is not None:
            return self.object_resolution
        bend_angle = self._shape_bend_angle(obj)
        if bend_angle <= 1.0e-12:
            # A straight extrusion is represented exactly by its two end rings.
            return 1
        desired = max(2, math.ceil(bend_angle / math.radians(5.0)))
        return max(2, min(self.object_resolution_effective, desired))

    def _shape_bend_angle(self, obj: Any) -> float:
        shape = getattr(self._object_type(obj), "shape", None)
        curvature = float(getattr(shape, "curvature", 0.0))
        length = float(getattr(shape, "dz", 0.0))
        if isinstance(shape, Sequence) and not isinstance(shape, (str, bytes)):
            try:
                if shape and str(shape[0]).lower() == "box":
                    length = float(shape[3])
                    curvature = float(shape[4]) if len(shape) > 4 else 0.0
                elif shape and str(shape[0]).lower() == "cylinder":
                    length = float(shape[2])
                    curvature = float(shape[3]) if len(shape) > 3 else 0.0
            except (TypeError, ValueError, IndexError):
                pass
        return abs(curvature * length)

    @staticmethod
    def _convex_hull(points: np.ndarray) -> np.ndarray:
        """Return the counter-clockwise hull of 2D points without SciPy."""

        unique = np.unique(np.asarray(points, dtype=float), axis=0)
        if len(unique) <= 2:
            return unique
        ordered = unique[np.lexsort((unique[:, 1], unique[:, 0]))]

        def cross(origin: np.ndarray, first: np.ndarray, second: np.ndarray) -> float:
            one, two = first - origin, second - origin
            return float(one[0] * two[1] - one[1] * two[0])

        lower: list[np.ndarray] = []
        for point in ordered:
            while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0.0:
                lower.pop()
            lower.append(point)
        upper: list[np.ndarray] = []
        for point in reversed(ordered):
            while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0.0:
                upper.pop()
            upper.append(point)
        return np.asarray([*lower[:-1], *upper[:-1]], dtype=float)

    def _finish_object_batch(self) -> None:
        if not self._batch_entries:
            return
        # Painter's algorithm for translucent projected silhouettes.
        entries = sorted(self._batch_entries, key=lambda entry: entry[4])
        polygons = [entry[1] for entry in entries]
        self._batch_base_facecolors = np.asarray(
            [entry[2] for entry in entries], dtype=float
        )
        self._batch_base_edgecolors = np.asarray(
            [entry[3] for entry in entries], dtype=float
        )
        collection = self._mpl.PolyCollection(
            polygons,
            facecolors=self._batch_base_facecolors,
            edgecolors=self._batch_base_edgecolors,
            linewidths=0.7,
            closed=True,
            zorder=3,
        )
        collection.set_gid("objects:batch")
        self.ax.add_collection(collection)
        self._batch_object_collection = collection
        self._object_artists.append(collection)
        self._batch_current_facecolors = self._batch_base_facecolors.copy()
        self._batch_current_edgecolors = self._batch_base_edgecolors.copy()
        indices_by_name: dict[str, list[int]] = {}
        for index, (name, _polygon, _face, _edge, _depth) in enumerate(entries):
            indices_by_name.setdefault(name, []).append(index)
        for name, indices in indices_by_name.items():
            visual = self._object_visuals[name]
            visual.fill = collection
            visual.edges = collection
            visual.batch_index = indices[0]
            visual.batch_indices = np.asarray(indices, dtype=np.int64)
        self._batch_entries.clear()

    @staticmethod
    def _feature_edges(
        vertices: np.ndarray, faces: np.ndarray
    ) -> list[tuple[int, int]]:
        """Return mesh boundary/ridge edges, omitting triangulation diagonals."""

        edge_faces: dict[tuple[int, int], list[int]] = {}
        for face_index, face in enumerate(faces):
            for first, second in zip(face, np.roll(face, -1)):
                edge = tuple(sorted((int(first), int(second))))
                edge_faces.setdefault(edge, []).append(face_index)
        if not len(faces):
            return []
        triangles = vertices[faces]
        normals = np.cross(
            triangles[:, 1] - triangles[:, 0],
            triangles[:, 2] - triangles[:, 0],
        )
        lengths = np.linalg.norm(normals, axis=1)
        valid = lengths > 1e-14
        normals[valid] /= lengths[valid, None]
        ridge_cosine = math.cos(math.radians(5.0))
        result: list[tuple[int, int]] = []
        for edge, owners in edge_faces.items():
            if len(owners) != 2:
                result.append(edge)
                continue
            first, second = owners
            if not valid[first] or not valid[second]:
                continue
            if float(np.dot(normals[first], normals[second])) < ridge_cosine:
                result.append(edge)
        return result

    def _object_type(self, obj: Any) -> Any:
        type_ = getattr(obj, "type", None)
        if type_ is not None and not isinstance(type_, str):
            return type_
        name = getattr(obj, "type_name", type_)
        types = getattr(self.layout, "types", None)
        if name is not None and types is not None:
            try:
                return types[name]
            except (KeyError, TypeError):
                pass
        return type_

    def _finish_bounds(self) -> None:
        if np.all(np.isfinite(self._bounds_low)):
            low = self._bounds_low
            high = self._bounds_high
        else:
            low = np.asarray([-1.0, -1.0, -1.0])
            high = np.asarray([1.0, 1.0, 1.0])
        self._world_bounds = np.asarray(
            [low[0], high[0], low[1], high[1], low[2], high[2]], dtype=float
        )
        projected_low = low[list(self.axis_indices)]
        projected_high = high[list(self.axis_indices)]
        self._bounds = np.asarray(
            [projected_low[0], projected_high[0], projected_low[1], projected_high[1]],
            dtype=float,
        )
        self._scene_scale = max(1.0, float(np.linalg.norm(high - low)))
        self._projected_scale = max(
            1.0, float(np.linalg.norm(projected_high - projected_low))
        )
        self.world_bounds = tuple(float(item) for item in self._world_bounds)
        self.bounds = tuple(float(item) for item in self._bounds)

    def _accumulate_bounds(self, points: Any) -> None:
        values = _as_points(points)
        if not len(values):
            return
        self._bounds_low = np.minimum(self._bounds_low, np.min(values, axis=0))
        self._bounds_high = np.maximum(self._bounds_high, np.max(values, axis=0))

    def _ensure_object_frames(self, *, named: bool = False, beam: bool = False) -> None:
        build_named = bool(named and not self._named_frames_built)
        build_beam = bool(beam and not self._beam_frames_built)
        if not build_named and not build_beam:
            return
        if self.batch_objects:
            if build_named:
                self._build_batched_named_frames()
            if build_beam:
                self._build_batched_beam_frames()
            self._refresh_batched_frame_index()
            self._named_frames_built = self._named_frames_built or build_named
            self._beam_frames_built = self._beam_frames_built or build_beam
            return

        from .model import Pose

        for object_name, visual in self._object_visuals.items():
            obj = visual.entity
            type_ = self._object_type(obj)
            center = _pose_matrix(visual.pose)
            if build_named:
                for fallback_name, _frame in _mapping_items(
                    getattr(type_, "frames", None)
                ):
                    frame_name = str(fallback_name)
                    local = self.resolver._type_frame_matrix(type_, frame_name)
                    pose = Pose(center @ local, space="world")
                    self._add_named_frame(object_name, obj, frame_name, pose)
                    self._accumulate_bounds(_pose_origin(pose).reshape(1, 3))
            if build_beam:
                for frame_name in ("magnetic_entry", "magnetic_exit"):
                    local = self.resolver._type_frame_matrix(type_, frame_name)
                    pose = Pose(center @ local, space="world")
                    vertices = self._beam_plane_vertices(type_, pose)
                    self._add_beam_frame(object_name, obj, frame_name, pose, vertices)
                    self._accumulate_bounds(vertices)
        self._named_frames_built = self._named_frames_built or build_named
        self._beam_frames_built = self._beam_frames_built or build_beam

    def _frame_pose(self, visual: _ObjectVisual, type_: Any, frame_name: str) -> Any:
        from .model import Pose

        local = self.resolver._type_frame_matrix(type_, frame_name)
        return Pose(_pose_matrix(visual.pose) @ local, space="world")

    def _build_batched_named_frames(self) -> None:
        origins: list[np.ndarray] = []
        directions: list[np.ndarray] = []
        colors: list[str] = []
        points: list[np.ndarray] = []
        for object_name, visual in self._object_visuals.items():
            obj = visual.entity
            type_ = self._object_type(obj)
            for fallback_name, _frame in _mapping_items(getattr(type_, "frames", None)):
                frame_name = str(fallback_name)
                pose = self._frame_pose(visual, type_, frame_name)
                origin = _pose_origin(pose)
                for axis, color in zip(_pose_axes(pose), _AXIS_COLORS):
                    origins.append(self._project(origin.reshape(1, 3))[0])
                    directions.append(self._project(np.asarray(axis).reshape(1, 3))[0])
                    colors.append(color)
                point = self._project(origin.reshape(1, 3))[0]
                points.append(point)
                target = _PickTarget(
                    "frame", f"{object_name}.{frame_name}", obj, pose, obj, frame_name
                )
                self._append_batched_frame_visual(target, point.reshape(1, 2))
                self._accumulate_bounds(origin.reshape(1, 3))
        if not points:
            return
        axes = self._mpl.LineCollection([], colors=colors, linewidths=0.7, zorder=7)
        self.ax.add_collection(axes)
        self._register_display_axes(axes, origins, directions)
        point_array = np.asarray(points, dtype=float)
        markers = self.ax.scatter(
            point_array[:, 0],
            point_array[:, 1],
            s=7.0,
            c="#ffca75",
            edgecolors="#4e3921",
            linewidths=0.25,
            zorder=8,
        )
        self._named_frame_artists.extend((axes, markers))

    def _build_batched_beam_frames(self) -> None:
        polygons: list[np.ndarray] = []
        facecolors: list[tuple[float, ...]] = []
        edgecolors: list[tuple[float, ...]] = []
        points: list[np.ndarray] = []
        marker_colors: list[tuple[float, float, float]] = []
        for object_name, visual in self._object_visuals.items():
            obj = visual.entity
            type_ = self._object_type(obj)
            for frame_name in ("magnetic_entry", "magnetic_exit"):
                pose = self._frame_pose(visual, type_, frame_name)
                vertices = self._beam_plane_vertices(type_, pose)
                projected = self._project(vertices)
                color_text = "#66c7ff" if frame_name == "magnetic_entry" else "#ff9b78"
                color = self._color(color_text, color_text)
                polygons.append(projected)
                facecolors.append((*color, 0.14))
                edgecolors.append((*color, 1.0))
                point = self._project(_pose_origin(pose).reshape(1, 3))[0]
                points.append(point)
                marker_colors.append(color)
                target = _PickTarget(
                    "beam_frame",
                    f"{object_name}.{frame_name}",
                    obj,
                    pose,
                    obj,
                    frame_name,
                )
                self._append_batched_frame_visual(target, projected)
                self._accumulate_bounds(vertices)
        if not points:
            return
        planes = self._mpl.PolyCollection(
            polygons,
            facecolors=facecolors,
            edgecolors=edgecolors,
            linewidths=0.65,
            closed=True,
            zorder=5,
        )
        self.ax.add_collection(planes)
        point_array = np.asarray(points, dtype=float)
        markers = self.ax.scatter(
            point_array[:, 0],
            point_array[:, 1],
            s=8.0,
            c=marker_colors,
            edgecolors="#0c1620",
            linewidths=0.25,
            marker="s",
            zorder=6,
        )
        self._beam_frame_artists.extend((planes, markers))

    def _append_batched_frame_visual(
        self, target: _PickTarget, projected: np.ndarray
    ) -> None:
        low, high = np.min(projected, axis=0), np.max(projected, axis=0)
        self._batched_frame_visuals.append(
            _BatchedFrameVisual(
                target,
                projected,
                np.asarray([low[0], high[0], low[1], high[1]], dtype=float),
                float(_pose_origin(target.pose)[self.depth_axis]),
            )
        )

    def _refresh_batched_frame_index(self) -> None:
        if self._batched_frame_visuals:
            self._batched_frame_bounds = np.stack(
                [visual.bounds for visual in self._batched_frame_visuals]
            )

    def _add_named_frame(
        self, object_name: str, obj: Any, frame_name: str, pose: Any
    ) -> None:
        origin = _pose_origin(pose)
        axis_lines = self._mpl.LineCollection(
            [],
            colors=_AXIS_COLORS,
            linewidths=1.2,
            zorder=7,
        )
        self.ax.add_collection(axis_lines)
        projected_origin = self._project(origin.reshape(1, 3))[0]
        self._register_display_axes(
            axis_lines,
            np.repeat(projected_origin.reshape(1, 2), 3, axis=0),
            np.asarray(_pose_axes(pose), dtype=float)[:, self.axis_indices],
        )
        point = projected_origin
        (marker,) = self.ax.plot(
            [point[0]],
            [point[1]],
            marker="o",
            markersize=5,
            markerfacecolor="#ffca75",
            markeredgecolor="#4e3921",
            linestyle="none",
            picker=7,
            zorder=8,
        )
        label = self.ax.annotate(
            frame_name,
            xy=point,
            xytext=(4, 4),
            textcoords="offset points",
            color="#ffca75",
            fontsize=7.5,
            zorder=8,
        )
        target = _PickTarget(
            "frame", f"{object_name}.{frame_name}", obj, pose, obj, frame_name
        )
        self._pick_targets[marker] = target
        self._named_frame_artists.extend((axis_lines, marker, label))

    def _beam_plane_vertices(self, type_: Any, pose: Any) -> np.ndarray:
        shape = getattr(type_, "shape", None)
        dx = dy = 1.0
        circular = False
        if hasattr(shape, "dx") and hasattr(shape, "dy"):
            dx, dy = float(shape.dx), float(shape.dy)
        elif hasattr(shape, "r"):
            dx = dy = 2.0 * float(shape.r)
            circular = True
        elif isinstance(shape, Sequence) and not isinstance(shape, (str, bytes)):
            if shape and str(shape[0]).lower() == "box" and len(shape) >= 3:
                dx, dy = float(shape[1]), float(shape[2])
            elif shape and str(shape[0]).lower() == "cylinder" and len(shape) >= 2:
                dx = dy = 2.0 * float(shape[1])
                circular = True
        hx = max(abs(dx) * 1.08 / 2.0, 0.02)
        hy = max(abs(dy) * 1.08 / 2.0, 0.02)
        origin = _pose_origin(pose)
        x_axis, y_axis, _ = _pose_axes(pose)
        if circular:
            return np.asarray(
                [
                    origin
                    + hx * math.cos(angle) * x_axis
                    + hy * math.sin(angle) * y_axis
                    for angle in np.linspace(
                        0.0,
                        2.0 * math.pi,
                        self.radial_resolution_effective,
                        endpoint=False,
                    )
                ]
            )
        return np.asarray(
            [
                origin - hx * x_axis - hy * y_axis,
                origin + hx * x_axis - hy * y_axis,
                origin + hx * x_axis + hy * y_axis,
                origin - hx * x_axis + hy * y_axis,
            ]
        )

    def _add_beam_frame(
        self,
        object_name: str,
        obj: Any,
        frame_name: str,
        pose: Any,
        vertices: np.ndarray,
    ) -> None:
        projected = self._project(vertices)
        color_text = "#66c7ff" if frame_name == "magnetic_entry" else "#ff9b78"
        color = self._color(color_text, color_text)
        fill = self._mpl.PolyCollection(
            [projected],
            facecolors=[(*color, 0.14)],
            edgecolors="none",
            closed=True,
            picker=True,
            zorder=5,
        )
        self.ax.add_collection(fill)
        closed = np.vstack((projected, projected[0]))
        outline = self._mpl.LineCollection(
            [[closed[index], closed[index + 1]] for index in range(len(projected))],
            colors=[color],
            linewidths=1.5,
            picker=6,
            zorder=5.5,
        )
        self.ax.add_collection(outline)
        point = self._project(_pose_origin(pose).reshape(1, 3))[0]
        (marker,) = self.ax.plot(
            [point[0]],
            [point[1]],
            marker="s",
            markersize=4.5,
            markerfacecolor=color,
            markeredgecolor="#0c1620",
            linestyle="none",
            picker=7,
            zorder=6,
        )
        short_label = "IN" if frame_name == "magnetic_entry" else "OUT"
        label = self.ax.annotate(
            short_label,
            xy=point,
            xytext=(4, 4),
            textcoords="offset points",
            color=color,
            fontsize=8,
            fontweight="bold",
            zorder=6,
        )
        target = _PickTarget(
            "beam_frame",
            f"{object_name}.{frame_name}",
            obj,
            pose,
            obj,
            frame_name,
        )
        for artist in (fill, outline, marker):
            self._pick_targets[artist] = target
        self._beam_frame_artists.extend((fill, outline, marker, label))

    def _build_overlays(self) -> None:
        self.pose_text = self.ax.text(
            0.012,
            0.012,
            self._empty_pose_message(),
            transform=self.ax.transAxes,
            ha="left",
            va="bottom",
            family="monospace",
            fontsize=8.5,
            color="#b9dce0",
            bbox={
                "boxstyle": "round,pad=0.42",
                "facecolor": "#080d14",
                "edgecolor": "#304052",
                "alpha": 0.88,
            },
            zorder=20,
        )
        self.tooltip = self.ax.annotate(
            "",
            xy=(0.0, 0.0),
            xytext=(10, 10),
            textcoords="offset points",
            family="monospace",
            fontsize=8.5,
            fontweight="bold",
            color="#f0fafc",
            bbox={
                "boxstyle": "round,pad=0.32",
                "facecolor": "#080f17",
                "edgecolor": "#657783",
                "alpha": 0.92,
            },
            zorder=22,
        )
        self.tooltip.set_visible(False)
        self.local_axes = self._mpl.LineCollection(
            [], colors=_AXIS_COLORS, linewidths=2.0, zorder=18
        )
        self.ax.add_collection(self.local_axes)
        self._local_display_axes = self._register_display_axes(
            self.local_axes,
            np.empty((0, 2), dtype=float),
            np.empty((0, 2), dtype=float),
            active=True,
        )
        (self.local_origin,) = self.ax.plot(
            [],
            [],
            marker="o",
            markersize=4,
            markerfacecolor="#f5f7f9",
            markeredgecolor="#24323e",
            linestyle="none",
            zorder=19,
        )
        self.local_axis_labels = [
            self.ax.text(0.0, 0.0, name, color=color, fontsize=8, zorder=19)
            for name, color in zip(_AXIS_NAMES, _AXIS_COLORS)
        ]
        self._set_local_axes_visible(False)
        self._hover_overlay_artists = (
            self.local_axes,
            self.local_origin,
            *self.local_axis_labels,
            self.pose_text,
            self.tooltip,
        )
        if self._blit_enabled:
            for artist in self._hover_overlay_artists:
                artist.set_animated(True)

    @staticmethod
    def _empty_pose_message() -> str:
        return (
            "Hover or click a named frame, Beam frame, object, or curve "
            "to inspect its world pose."
        )

    def _install_interaction(self) -> None:
        connect = self.canvas.mpl_connect
        self._callbacks.extend(
            [
                connect("button_press_event", self._on_click),
                connect("motion_notify_event", self._on_motion),
                connect("axes_leave_event", self._on_leave),
                connect("figure_leave_event", self._on_leave),
                connect("key_press_event", self._on_key_press),
                connect("resize_event", self._on_resize),
                connect("draw_event", self._on_draw),
                connect("close_event", self._on_close_event),
            ]
        )
        self._axes_callbacks.extend(
            (
                self.ax.callbacks.connect("xlim_changed", self._on_limits_changed),
                self.ax.callbacks.connect("ylim_changed", self._on_limits_changed),
            )
        )

    # --------------------------------------------------------------- layers

    @staticmethod
    def _set_artists_visible(artists: Iterable[Any], visible: bool) -> None:
        for artist in artists:
            artist.set_visible(bool(visible))

    def _apply_layer_visibility(self) -> None:
        self._set_artists_visible(self._curve_artists, self.curves_visible)
        self._set_artists_visible(self._object_artists, self.objects_visible)
        self._set_artists_visible(
            self._beam_frame_artists,
            self.objects_visible and self.beam_frames_visible,
        )
        self._set_artists_visible(
            self._named_frame_artists,
            self.objects_visible and self.frames_visible,
        )
        if self._hover is not None and not self._selection_is_visible(self._hover):
            self._hover = None
            self.tooltip.set_visible(False)
            if self._selection is None:
                self.pose_text.set_text(self._empty_pose_message())
            else:
                self._set_pose_text(self._selection)
        self._sync_selection_overlay()

    def set_curves_visible(self, visible: bool = True) -> Self:
        self._ensure_open()
        self.curves_visible = bool(visible)
        self._apply_layer_visibility()
        self._request_draw()
        return self

    def set_objects_visible(self, visible: bool = True) -> Self:
        self._ensure_open()
        self.objects_visible = bool(visible)
        self._apply_layer_visibility()
        self._request_draw()
        return self

    def set_beam_frames_visible(self, visible: bool = True) -> Self:
        self._ensure_open()
        self.beam_frames_visible = bool(visible)
        if self.beam_frames_visible:
            self._ensure_object_frames(beam=True)
            self._finish_bounds()
        self._apply_layer_visibility()
        self._request_draw()
        return self

    def set_frames_visible(self, visible: bool = True) -> Self:
        self._ensure_open()
        self.frames_visible = bool(visible)
        if self.frames_visible:
            self._ensure_object_frames(named=True)
            self._finish_bounds()
            self._refresh_reference_arrows()
        self._apply_layer_visibility()
        self._request_draw()
        return self

    def set_grid_visible(self, visible: bool = True) -> Self:
        self._ensure_open()
        self.grid_visible = bool(visible)
        if self.grid_visible:
            self.ax.grid(True, color="#7891a8", alpha=0.24, linewidth=0.65)
        else:
            self.ax.grid(False)
        self._request_draw()
        return self

    # ------------------------------------------------------------- view / IO

    def fit(self) -> Self:
        """Fit the axes to all geometry in the selected entity scope."""

        self._ensure_open()
        xmin, xmax, ymin, ymax = self._bounds
        xspan, yspan = xmax - xmin, ymax - ymin
        scale = max(self._projected_scale, xspan, yspan, 1.0)
        xpad = max(0.06 * xspan, 0.025 * scale, 1e-3)
        ypad = max(0.06 * yspan, 0.025 * scale, 1e-3)
        if xspan < 1e-12:
            xpad = max(xpad, 0.5)
        if yspan < 1e-12:
            ypad = max(ypad, 0.5)
        self.ax.set_xlim(float(xmin - xpad), float(xmax + xpad))
        self.ax.set_ylim(float(ymin - ypad), float(ymax + ypad))
        self.ax.set_aspect("auto")
        self._refresh_reference_arrows()
        self.set_grid_visible(self.grid_visible)
        self._request_draw()
        return self

    def reset_view(self) -> Self:
        return self.fit()

    reset_camera = reset_view

    def draw(self) -> Self:
        """Draw once without entering a GUI event loop."""

        self._ensure_open()
        self._draw_started = True
        self._invalidate_blit()
        self._refresh_reference_arrows()
        self.canvas.draw()
        return self

    render = draw

    def show(self, *, block: bool | None = None) -> Self:
        """Draw and ask Matplotlib's active backend to display the figure."""

        self._ensure_open()
        self._draw_started = True
        self._invalidate_blit()
        self._refresh_reference_arrows()
        self.canvas.draw_idle()
        self._plt.show(block=block)
        return self

    def savefig(
        self,
        filename: str | Path,
        *,
        dpi: float | str | None = None,
        transparent: bool = False,
        **kwargs: Any,
    ) -> Path:
        """Save the current projection and return the resulting path."""

        self._ensure_open()
        path = Path(filename)
        if not path.suffix:
            path = path.with_suffix(".png")
        self._draw_started = True
        self._refresh_reference_arrows()
        animated = [
            bool(artist.get_animated()) for artist in self._hover_overlay_artists
        ]
        self._blit_suspended = True
        try:
            # Animated artists are deliberately absent from a full canvas draw,
            # so include them explicitly in exported figures.
            for artist in self._hover_overlay_artists:
                artist.set_animated(False)
            self.figure.savefig(
                path,
                dpi=self.dpi if dpi is None else dpi,
                transparent=transparent,
                **kwargs,
            )
        finally:
            for artist, value in zip(self._hover_overlay_artists, animated):
                artist.set_animated(value)
            self._blit_suspended = False
            self._invalidate_blit()
        return path

    def screenshot(
        self,
        filename: str | Path | None = None,
        *,
        dpi: float | str | None = None,
        transparent: bool = False,
        **kwargs: Any,
    ) -> Path | np.ndarray:
        """Save an image, or return an RGBA array when no path is supplied."""

        if filename is not None:
            return self.savefig(filename, dpi=dpi, transparent=transparent, **kwargs)
        self.draw()
        return np.asarray(self.canvas.buffer_rgba()).copy()

    def close(self) -> None:
        """Disconnect callbacks and release the figure; repeated calls are safe."""

        if self._closed:
            self._disconnect_callbacks()
            self._close_resolver_session()
            return
        # A viewer can share caller-owned axes. Restore ordinary Matplotlib
        # drawing before disconnecting our draw callback so visible overlays
        # remain part of later canvas draws and exports.
        self._disable_blit()
        self._disconnect_callbacks()
        self._close_resolver_session()
        if self._owns_figure:
            self._plt.close(self.figure)
        self._closed = True
        self._release_scene_references(clear_figure=self._owns_figure)

    def _on_close_event(self, _event: Any) -> None:
        self._disable_blit()
        self._disconnect_callbacks()
        self._close_resolver_session()
        self._closed = True
        self._release_scene_references(clear_figure=False)

    def _disconnect_callbacks(self) -> None:
        canvas = getattr(self, "canvas", None)
        for callback in self._callbacks:
            if canvas is not None:
                canvas.mpl_disconnect(callback)
        self._callbacks.clear()
        axes = getattr(self, "ax", None)
        for callback in self._axes_callbacks:
            if axes is not None:
                axes.callbacks.disconnect(callback)
        self._axes_callbacks.clear()

    def _close_resolver_session(self) -> None:
        session, self._resolver_session = self._resolver_session, None
        if session is not None:
            session.__exit__(None, None, None)

    def _release_scene_references(self, *, clear_figure: bool) -> None:
        """Drop geometry retained by a closed viewer (notably in IPython Out)."""

        figure = getattr(self, "figure", None)
        if clear_figure and figure is not None:
            figure.clear()
        for name in (
            "_curve_visuals",
            "_object_visuals",
            "_feature_edge_cache",
            "_pick_targets",
            "curve_artists",
            "object_artists",
            "curve_actors",
            "object_actors",
        ):
            value = getattr(self, name, None)
            if hasattr(value, "clear"):
                value.clear()
        self._curve_items.clear()
        self._object_items.clear()
        self._selection = None
        self._hover = None
        for name in (
            "_curve_artists",
            "_object_artists",
            "_beam_frame_artists",
            "_named_frame_artists",
            "_batch_entries",
            "_object_pick_visuals",
            "_batched_frame_visuals",
            "_display_axes",
        ):
            value = getattr(self, name, None)
            if hasattr(value, "clear"):
                value.clear()
        empty4 = np.empty((0, 4), dtype=float)
        self._object_pick_bounds = empty4
        self._batched_frame_bounds = empty4
        self._batch_base_facecolors = empty4
        self._batch_base_edgecolors = empty4
        self._batch_current_facecolors = empty4
        self._batch_current_edgecolors = empty4
        self._batch_object_collection = None
        self._blit_background = None
        self._blit_enabled = False
        self._blit_suspended = False
        self._hover_overlay_artists = ()
        self.curve_scope = ()
        self.object_scope = ()
        self.layout = None
        self.resolver = None
        for name in ("pose_text", "tooltip", "local_axes", "local_origin"):
            setattr(self, name, None)
        self._local_display_axes = None
        labels = getattr(self, "local_axis_labels", None)
        if hasattr(labels, "clear"):
            labels.clear()
        self.ax = None
        self.axes = None
        self.canvas = None
        self.figure = None

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("the LayoutViewer2D is closed")

    def _request_draw(self) -> None:
        if self._draw_started and not self._closed:
            self._invalidate_blit()
            self.canvas.draw_idle()

    def _invalidate_blit(self) -> None:
        self._blit_background = None

    def _on_draw(self, event: Any) -> None:
        """Capture the static scene after a full draw, then paint overlays."""

        if (
            not self._blit_enabled
            or self._blit_suspended
            or self._closed
            or getattr(event, "canvas", self.canvas) is not self.canvas
        ):
            return
        try:
            self._blit_background = self.canvas.copy_from_bbox(self.figure.bbox)
            self._draw_hover_overlays()
        except (AttributeError, NotImplementedError, RuntimeError, ValueError):
            # Some third-party canvases advertise blitting but reject it at
            # runtime. A regular draw remains a safe backend-independent path.
            self._disable_blit()
            self.canvas.draw_idle()

    def _draw_hover_overlays(self) -> None:
        for artist in self._hover_overlay_artists:
            if artist.get_visible():
                self.ax.draw_artist(artist)
        self.canvas.blit(self.figure.bbox)

    def _disable_blit(self) -> None:
        self._blit_enabled = False
        self._blit_background = None
        for artist in self._hover_overlay_artists:
            artist.set_animated(False)

    def _request_overlay_draw(self) -> None:
        """Redraw only hover/readout artists when the backend supports it."""

        if not self._draw_started or self._closed:
            return
        if (
            self._blit_enabled
            and not self._blit_suspended
            and self._blit_background is not None
        ):
            try:
                self.canvas.restore_region(self._blit_background)
                self._draw_hover_overlays()
                return
            except (AttributeError, NotImplementedError, RuntimeError, ValueError):
                self._disable_blit()
        self.canvas.draw_idle()

    # -------------------------------------------------------------- selection

    @property
    def selected(self) -> Any:
        return None if self._selection is None else self._selection.entity

    @property
    def selection(self) -> Any:
        return self.selected

    def select(self, entity: Any = None, *, station: float | None = None) -> Self:
        """Highlight a scoped entity/frame, or clear selection with ``None``."""

        self._ensure_open()
        if entity is None:
            self._set_selection(None)
        else:
            self._set_selection(self._selection_from_value(entity, station=station))
        return self

    def clear_selection(self) -> Self:
        return self.select(None)

    def _selection_from_value(
        self, value: Any, *, station: float | None = None
    ) -> _Selection:
        if isinstance(value, tuple) and len(value) == 2:
            kind, value = value
            if str(kind) == "curve":
                value = f"curve:{value}"
            elif str(kind) == "object":
                value = f"object:{value}"
            elif str(kind) == "frame":
                value = str(value)
        if isinstance(value, str):
            text = value
            if "->" in text and not text.startswith("curve:"):
                object_name, frame_name = text.split("->", 1)
                return self._named_frame_selection(object_name, frame_name, text)
            if text.startswith("curve:"):
                name = text[6:]
                visual = self._curve_visuals.get(name)
                if visual is None:
                    raise KeyError(f"unknown scoped curve {name!r}")
                return self._curve_selection(visual, station)
            if text.startswith("object:"):
                name = text[7:]
                visual = self._object_visuals.get(name)
                if visual is None:
                    raise KeyError(f"unknown scoped object {name!r}")
                return _Selection("object", name, visual.entity, visual.pose)
            curve_visual = self._curve_visuals.get(text)
            object_visual = self._object_visuals.get(text)
            if curve_visual is not None and object_visual is not None:
                raise ValueError(
                    f"selection name {text!r} is ambiguous; use 'curve:' or 'object:'"
                )
            if curve_visual is not None:
                return self._curve_selection(curve_visual, station)
            if object_visual is not None:
                return _Selection(
                    "object", text, object_visual.entity, object_visual.pose
                )
            if "." in text:
                object_name, frame_name = text.split(".", 1)
                return self._named_frame_selection(object_name, frame_name, text)
            raise KeyError(f"unknown selectable entity {text!r} in viewer scope")

        for visual in self._curve_visuals.values():
            if value is visual.entity:
                return self._curve_selection(visual, station)
        for visual in self._object_visuals.values():
            if value is visual.entity:
                return _Selection("object", visual.name, visual.entity, visual.pose)
        matching = [
            visual
            for visual in self._object_visuals.values()
            if self._object_type(visual.entity) is value
        ]
        if matching:
            visual = matching[0]
            return _Selection("object", visual.name, visual.entity, visual.pose)
        owner = getattr(value, "owner", None)
        if owner is not None:
            for visual in self._object_visuals.values():
                if self._object_type(visual.entity) is owner:
                    name = getattr(value, "name", None)
                    if name is not None:
                        return self._named_frame_selection(
                            visual.name, str(name), f"{visual.name}.{name}"
                        )
        raise ValueError("selection is not represented in this viewer scope")

    def _named_frame_selection(
        self, object_name: str, frame_name: str, display_name: str
    ) -> _Selection:
        visual = self._object_visuals.get(object_name)
        if visual is None or not frame_name:
            raise KeyError(f"unknown scoped frame {display_name!r}")
        try:
            pose = self.resolver.object_named_frame(visual.entity, frame_name)
        except Exception as exc:
            raise KeyError(f"unknown scoped frame {display_name!r}") from exc
        kind = (
            "beam_frame"
            if frame_name in {"magnetic_entry", "magnetic_exit"}
            else "frame"
        )
        return _Selection(
            kind,
            f"{object_name}.{frame_name}",
            visual.entity,
            pose,
            visual.entity,
            frame_name,
        )

    def _curve_selection(
        self, visual: _CurveVisual, station: float | None
    ) -> _Selection:
        if station is None:
            station = float(visual.stations[0]) if len(visual.stations) else 0.0
        station = float(station)
        pose = self.resolver.curve_frame(visual.entity, station)
        segment = None
        if len(visual.stations):
            index = int(np.argmin(np.abs(visual.stations - station)))
            segment = int(visual.segment_indices[index])
        return _Selection(
            "curve",
            visual.name,
            visual.entity,
            pose,
            station=station,
            segment_index=segment,
        )

    def _set_selection(self, selection: _Selection | None) -> None:
        self._selection = selection
        self._reset_highlights()
        if selection is None:
            self.pose_text.set_text(self._empty_pose_message())
            if self._hover is None:
                self._set_local_axes_visible(False)
        else:
            self._apply_highlight(selection)
            self._set_pose_text(selection)
            self._show_local_axes(selection.pose)
        self._sync_selection_overlay()
        self._request_draw()

    def _reset_highlights(self) -> None:
        for visual in self._curve_visuals.values():
            visual.line.set_color(visual.color)
            visual.line.set_linewidth(2.6)
            visual.halo.set_color(visual.color)
            visual.halo.set_linewidth(8.0)
            visual.halo.set_alpha(0.18)
        if self._batch_object_collection is not None:
            self._batch_current_facecolors = self._batch_base_facecolors.copy()
            self._batch_current_edgecolors = self._batch_base_edgecolors.copy()
            self._batch_object_collection.set_facecolors(self._batch_current_facecolors)
            self._batch_object_collection.set_edgecolors(self._batch_current_edgecolors)
            self._batch_object_collection.set_linewidth(0.7)
            return
        for visual in self._object_visuals.values():
            visual.fill.set_facecolor([(*visual.color, 0.20)])
            visual.edges.set_color([(*self._lighten(visual.color, 0.24), 0.68)])
            visual.edges.set_linewidth(0.7)

    def _apply_highlight(self, selection: _Selection) -> None:
        highlight = self._color(_SELECTION_COLOR, _SELECTION_COLOR)
        if selection.kind == "curve":
            visual = self._curve_visuals.get(selection.name)
            if visual is not None:
                visual.line.set_color(self._lighten(visual.color, 0.18))
                visual.line.set_linewidth(3.5)
                visual.halo.set_color(highlight)
                visual.halo.set_linewidth(11.0)
                visual.halo.set_alpha(0.38)
            return
        owner = selection.owner if selection.owner is not None else selection.entity
        for visual in self._object_visuals.values():
            if visual.entity is owner:
                if (
                    self._batch_object_collection is not None
                    and visual.batch_indices is not None
                ):
                    indices = visual.batch_indices
                    self._batch_current_facecolors[indices] = (*visual.color, 0.38)
                    self._batch_current_edgecolors[indices] = (*highlight, 0.98)
                    self._batch_object_collection.set_facecolors(
                        self._batch_current_facecolors
                    )
                    self._batch_object_collection.set_edgecolors(
                        self._batch_current_edgecolors
                    )
                    continue
                visual.fill.set_facecolor([(*visual.color, 0.38)])
                visual.edges.set_color([(*highlight, 0.98)])
                visual.edges.set_linewidth(1.8)

    def _set_pose_text(self, selection: _Selection) -> None:
        origin = _pose_origin(selection.pose)
        angles = tuple(math.degrees(value) for value in _madx_euler(selection.pose))
        if selection.kind == "curve":
            heading = f"Curve {selection.name} · s = {selection.station:.6f} m"
        elif selection.kind == "beam_frame":
            heading = f"Beam frame {selection.name}"
        elif selection.kind == "frame":
            heading = f"Frame {selection.name}"
        else:
            type_ = self._object_type(selection.entity)
            type_name = getattr(
                type_, "name", getattr(selection.entity, "type_name", None)
            )
            suffix = f" · {type_name}" if type_name else ""
            heading = f"Object {selection.name}{suffix} · center"
        self.pose_text.set_text(
            f"{heading}\n"
            f"X = {origin[0]:.6f} m    Y = {origin[1]:.6f} m    Z = {origin[2]:.6f} m\n"
            f"theta = {angles[0]:.5f} deg    phi = {angles[1]:.5f} deg    "
            f"psi = {angles[2]:.5f} deg"
        )

    def _show_local_axes(self, pose: Any) -> None:
        origin = _pose_origin(pose)
        point = self._project(origin.reshape(1, 3))[0]
        display_axes = self._local_display_axes
        display_axes.origins = np.repeat(point.reshape(1, 2), 3, axis=0)
        display_axes.directions = np.asarray(_pose_axes(pose), dtype=float)[
            :, self.axis_indices
        ]
        self._update_display_axes(display_axes)
        self.local_origin.set_data([point[0]], [point[1]])
        self._position_local_axis_labels()
        self._set_local_axes_visible(True)

    def _set_local_axes_visible(self, visible: bool) -> None:
        self.local_axes.set_visible(visible)
        self.local_origin.set_visible(visible)
        self._set_artists_visible(self.local_axis_labels, visible)

    def _selection_is_visible(self, selection: _Selection) -> bool:
        if selection.kind == "curve":
            return self.curves_visible
        if not self.objects_visible:
            return False
        if selection.kind == "beam_frame":
            return self.beam_frames_visible
        if selection.kind == "frame":
            return self.frames_visible
        return True

    def _sync_selection_overlay(self) -> None:
        active = self._hover if self._hover is not None else self._selection
        if active is None or not self._selection_is_visible(active):
            self._set_local_axes_visible(False)
        else:
            self._show_local_axes(active.pose)

    # -------------------------------------------------------------- callbacks

    def _on_limits_changed(self, _axes: Any) -> None:
        self._refresh_reference_arrows()
        self._request_draw()

    def _on_resize(self, _event: Any) -> None:
        self._refresh_reference_arrows()
        self._request_draw()

    def _on_key_press(self, event: Any) -> None:
        key = str(getattr(event, "key", "") or "").lower()
        if key == "c":
            self.set_curves_visible(not self.curves_visible)
        elif key == "o":
            self.set_objects_visible(not self.objects_visible)
        elif key == "b":
            self.set_beam_frames_visible(not self.beam_frames_visible)
        elif key in {"f", "r"}:
            self.fit()
        elif key == "g":
            self.set_grid_visible(not self.grid_visible)
        elif key in {"escape", "esc"}:
            self.clear_selection()

    def _on_click(self, event: Any) -> None:
        if getattr(event, "button", 1) not in (1, None):
            return
        if getattr(event, "inaxes", self.ax) is not self.ax:
            return
        toolbar = getattr(getattr(self.canvas, "toolbar", None), "mode", "")
        if toolbar:
            return
        selection = self._pick_selection(event)
        if selection is not None and self._same_selection(selection, self._selection):
            selection = None
        self._set_selection(selection)

    def _on_motion(self, event: Any) -> None:
        if getattr(event, "inaxes", None) is not self.ax:
            self._clear_hover()
            return
        toolbar = getattr(getattr(self.canvas, "toolbar", None), "mode", "")
        if toolbar or getattr(event, "button", None) is not None:
            return
        x, y = getattr(event, "x", None), getattr(event, "y", None)
        pixel = (float(x), float(y)) if x is not None and y is not None else None
        if pixel is not None and pixel == self._last_hover_pixel:
            return
        now = monotonic()
        if now - self._last_hover_time < self.hover_interval:
            return
        self._last_hover_time = now
        self._last_hover_pixel = pixel
        selection = self._pick_selection(event)
        if selection is None and self._hover is None:
            return
        if (
            selection is not None
            and selection.kind != "curve"
            and self._same_selection(selection, self._hover)
        ):
            data = self._event_data(event)
            if data is not None:
                self.tooltip.xy = data
                self._request_overlay_draw()
            return
        self._hover = selection
        if selection is None:
            self.tooltip.set_visible(False)
            if self._selection is None:
                self.pose_text.set_text(self._empty_pose_message())
                self._set_local_axes_visible(False)
            else:
                self._set_pose_text(self._selection)
                self._sync_selection_overlay()
        else:
            label = selection.name
            if selection.kind == "curve" and selection.station is not None:
                label += (
                    f"\nsegment {(selection.segment_index or 0) + 1}"
                    f" · s = {selection.station:.3f} m"
                )
            data = self._event_data(event)
            if data is not None:
                self.tooltip.xy = data
            self.tooltip.set_text(label)
            self.tooltip.set_visible(True)
            self._set_pose_text(selection)
            self._show_local_axes(selection.pose)
        self._request_overlay_draw()

    def _on_leave(self, _event: Any) -> None:
        self._clear_hover()

    def _clear_hover(self) -> None:
        if self._hover is None:
            self._last_hover_pixel = None
            return
        self._hover = None
        self._last_hover_pixel = None
        self.tooltip.set_visible(False)
        if self._selection is None:
            self.pose_text.set_text(self._empty_pose_message())
            self._set_local_axes_visible(False)
        else:
            self._set_pose_text(self._selection)
            self._sync_selection_overlay()
        self._request_overlay_draw()

    def _event_data(self, event: Any) -> tuple[float, float] | None:
        xdata, ydata = getattr(event, "xdata", None), getattr(event, "ydata", None)
        if xdata is not None and ydata is not None:
            return float(xdata), float(ydata)
        x, y = getattr(event, "x", None), getattr(event, "y", None)
        if x is None or y is None:
            return None
        values = self.ax.transData.inverted().transform((float(x), float(y)))
        return float(values[0]), float(values[1])

    def _event_pixels(self, event: Any) -> np.ndarray | None:
        x, y = getattr(event, "x", None), getattr(event, "y", None)
        if x is not None and y is not None:
            return np.asarray([float(x), float(y)])
        data = self._event_data(event)
        if data is None:
            return None
        return np.asarray(self.ax.transData.transform(data), dtype=float)

    def _pick_selection(self, event: Any) -> _Selection | None:
        candidates: dict[tuple[Any, ...], _PickCandidate] = {}
        priorities = {"curve": 1, "object": 2, "beam_frame": 3, "frame": 4}
        data_point = self._event_data(event)
        tolerance = self._pick_data_tolerance(event, 7.0)
        for order, (artist, target) in enumerate(self._pick_targets.items()):
            if not artist.get_visible():
                continue
            if target.kind == "curve":
                visual = self._curve_visuals.get(target.name)
                if visual is None or not self._bounds_hit(
                    visual.bounds, data_point, tolerance
                ):
                    continue
            try:
                contains, _details = artist.contains(event)
            except (AttributeError, TypeError, ValueError):
                contains = False
            if not contains:
                continue
            if target.kind == "curve":
                visual = self._curve_visuals.get(target.name)
                if visual is None:
                    continue
                station, segment, depth, distance = self._nearest_curve_station(
                    visual, event
                )
                pose = self.resolver.curve_frame(visual.entity, station)
                selection = _Selection(
                    "curve",
                    visual.name,
                    visual.entity,
                    pose,
                    station=station,
                    segment_index=segment,
                )
            else:
                kind = "frame" if target.kind == "frame" else target.kind
                selection = _Selection(
                    kind,
                    target.name,
                    target.entity,
                    target.pose,
                    target.owner,
                    target.frame_name,
                )
                if target.kind == "object":
                    visual = self._object_visuals.get(target.name)
                    if visual is None:
                        continue
                    depth, distance = self._object_pick_metrics(visual, event)
                else:
                    depth = float(_pose_origin(target.pose)[self.depth_axis])
                    distance = 0.0
            key = (
                selection.kind,
                id(selection.entity),
                selection.name,
                selection.frame_name,
            )
            candidate = _PickCandidate(
                selection,
                depth,
                distance,
                priorities.get(selection.kind, 0),
                order,
            )
            old = candidates.get(key)
            if old is None or self._candidate_key(candidate) > self._candidate_key(old):
                candidates[key] = candidate

        # Object artists are deliberately not individually pickable.  A
        # vectorised projected-bounds query first reduces a 10k-object layout
        # to the handful of shapes near the pointer, after which the existing
        # face/edge metric retains depth-aware selection semantics.
        if (
            self.objects_visible
            and data_point is not None
            and len(self._object_pick_visuals)
        ):
            nearby = self._nearby_object_indices(data_point, max_distance=7.0)
            base_order = len(self._pick_targets)
            for index in nearby:
                visual = self._object_pick_visuals[int(index)]
                depth, distance = self._object_pick_metrics(visual, event)
                if distance > 7.0:
                    continue
                selection = _Selection(
                    "object", visual.name, visual.entity, visual.pose
                )
                candidate = _PickCandidate(
                    selection,
                    depth,
                    distance,
                    priorities["object"],
                    base_order + int(index),
                )
                key = ("object", id(visual.entity), visual.name, None)
                old = candidates.get(key)
                if old is None or self._candidate_key(candidate) > self._candidate_key(
                    old
                ):
                    candidates[key] = candidate

        if data_point is not None and len(self._batched_frame_visuals):
            x, y = data_point
            tx, ty = tolerance
            bounds = self._batched_frame_bounds
            nearby = np.flatnonzero(
                (bounds[:, 0] - tx <= x)
                & (x <= bounds[:, 1] + tx)
                & (bounds[:, 2] - ty <= y)
                & (y <= bounds[:, 3] + ty)
            )
            base_order = len(self._pick_targets) + len(self._object_pick_visuals)
            for index in nearby:
                visual = self._batched_frame_visuals[int(index)]
                target = visual.target
                if target.kind == "frame" and not (
                    self.objects_visible and self.frames_visible
                ):
                    continue
                if target.kind == "beam_frame" and not (
                    self.objects_visible and self.beam_frames_visible
                ):
                    continue
                distance = self._projected_pick_distance(visual.projected, event)
                if distance > 7.0:
                    continue
                selection = _Selection(
                    target.kind,
                    target.name,
                    target.entity,
                    target.pose,
                    target.owner,
                    target.frame_name,
                )
                candidate = _PickCandidate(
                    selection,
                    visual.depth,
                    distance,
                    priorities[target.kind],
                    base_order + int(index),
                )
                key = (
                    selection.kind,
                    id(selection.entity),
                    selection.name,
                    selection.frame_name,
                )
                old = candidates.get(key)
                if old is None or self._candidate_key(candidate) > self._candidate_key(
                    old
                ):
                    candidates[key] = candidate
        if not candidates:
            return None
        return max(candidates.values(), key=self._candidate_key).selection

    def _nearby_object_indices(
        self, point: tuple[float, float], *, max_distance: float
    ) -> np.ndarray:
        """Return only the closest projected object bounds within a pixel radius."""

        x, y = point
        bounds = self._object_pick_bounds
        dx = np.maximum.reduce(
            (bounds[:, 0] - x, np.zeros(len(bounds)), x - bounds[:, 1])
        )
        dy = np.maximum.reduce(
            (bounds[:, 2] - y, np.zeros(len(bounds)), y - bounds[:, 3])
        )
        transform = self.ax.transData
        origin = np.asarray(transform.transform((0.0, 0.0)), dtype=float)
        unit = np.asarray(transform.transform((1.0, 1.0)), dtype=float)
        scale = np.abs(unit - origin)
        distances = np.hypot(dx * scale[0], dy * scale[1])
        within = np.flatnonzero(distances <= max_distance)
        if not len(within):
            return within
        minimum = float(np.min(distances[within]))
        # At overview scale hundreds of sub-pixel accelerator elements can lie
        # inside a conventional seven-pixel pick aperture.  Exact hit testing
        # only the nearest sub-pixel band is both faster and more intuitive.
        return within[distances[within] <= minimum + 0.75]

    def _projected_pick_distance(self, projected: np.ndarray, event: Any) -> float:
        point = self._event_pixels(event)
        if point is None or not len(projected):
            return math.inf
        pixels = np.asarray(self.ax.transData.transform(projected), dtype=float)
        if len(pixels) == 1:
            return float(np.linalg.norm(point - pixels[0]))
        following = np.roll(pixels, -1, axis=0)
        chords = following - pixels
        denominators = np.einsum("ij,ij->i", chords, chords)
        numerators = np.einsum("ij,ij->i", point - pixels, chords)
        fractions = np.divide(
            numerators,
            denominators,
            out=np.zeros_like(numerators),
            where=denominators > 1.0e-20,
        )
        fractions = np.clip(fractions, 0.0, 1.0)
        nearest = pixels + fractions[:, None] * chords
        distance = float(np.min(np.linalg.norm(point - nearest, axis=1)))
        if len(pixels) < 3:
            return distance
        # Even/odd ray crossing determines whether the pointer is inside the
        # projected frame plane; its interior then has zero pick distance.
        x, y = float(point[0]), float(point[1])
        first_x, first_y = pixels[:, 0], pixels[:, 1]
        next_x, next_y = following[:, 0], following[:, 1]
        crosses = (first_y > y) != (next_y > y)
        denominator = next_y - first_y
        intersections = first_x + (y - first_y) * (next_x - first_x) / np.where(
            np.abs(denominator) > 1.0e-20, denominator, 1.0
        )
        if int(np.count_nonzero(crosses & (x < intersections))) % 2:
            return 0.0
        return distance

    def _pick_data_tolerance(self, event: Any, pixels: float) -> tuple[float, float]:
        point = self._event_pixels(event)
        if point is None:
            return 0.0, 0.0
        inverse = self.ax.transData.inverted()
        center = np.asarray(inverse.transform(point), dtype=float)
        offset = np.asarray(
            inverse.transform(point + np.asarray([pixels, pixels])), dtype=float
        )
        delta = np.abs(offset - center)
        return float(delta[0]), float(delta[1])

    @staticmethod
    def _bounds_hit(
        bounds: np.ndarray,
        point: tuple[float, float] | None,
        tolerance: tuple[float, float],
    ) -> bool:
        if point is None:
            return True
        x, y = point
        tx, ty = tolerance
        return bool(
            bounds[0] - tx <= x <= bounds[1] + tx
            and bounds[2] - ty <= y <= bounds[3] + ty
        )

    def _candidate_key(self, candidate: _PickCandidate) -> tuple[int, int, float, int]:
        # Values within numerical world-coordinate noise share a depth bucket;
        # the explicit kind priority and pixel distance then break the tie.
        tolerance = max(self._scene_scale * 1e-10, 1e-12)
        depth = candidate.depth if math.isfinite(candidate.depth) else -math.inf
        bucket = -(10**30) if depth == -math.inf else round(depth / tolerance)
        return bucket, candidate.priority, -candidate.distance, -candidate.order

    def _object_pick_metrics(
        self, visual: _ObjectVisual, event: Any
    ) -> tuple[float, float]:
        """Approximate a shell hit's front depth and pixel distance."""

        point = self._event_pixels(event)
        depths = visual.vertices[:, self.depth_axis]
        if point is None or not len(visual.vertices):
            return float(np.max(depths, initial=-math.inf)), 0.0
        pixels = np.asarray(self.ax.transData.transform(visual.projected), dtype=float)
        faces = visual.faces
        if len(faces):
            triangles = pixels[faces]
            first = triangles[:, 0]
            v0 = triangles[:, 1] - first
            v1 = triangles[:, 2] - first
            offset = point - first
            denominator = v0[:, 0] * v1[:, 1] - v0[:, 1] * v1[:, 0]
            valid = np.abs(denominator) > 1.0e-14
            second_weight = np.divide(
                offset[:, 0] * v1[:, 1] - offset[:, 1] * v1[:, 0],
                denominator,
                out=np.full(len(faces), -math.inf),
                where=valid,
            )
            third_weight = np.divide(
                v0[:, 0] * offset[:, 1] - v0[:, 1] * offset[:, 0],
                denominator,
                out=np.full(len(faces), -math.inf),
                where=valid,
            )
            first_weight = 1.0 - second_weight - third_weight
            inside = valid & (
                np.minimum.reduce((first_weight, second_weight, third_weight))
                >= -1.0e-9
            )
            if np.any(inside):
                face_depths = depths[faces[inside]]
                hit_depths = (
                    first_weight[inside] * face_depths[:, 0]
                    + second_weight[inside] * face_depths[:, 1]
                    + third_weight[inside] * face_depths[:, 2]
                )
                return float(np.max(hit_depths)), 0.0

        edges = np.asarray(visual.feature_edges, dtype=np.int64).reshape((-1, 2))
        if not len(edges):
            return float(np.max(depths, initial=-math.inf)), math.inf
        first_indices, second_indices = edges[:, 0], edges[:, 1]
        first, second = pixels[first_indices], pixels[second_indices]
        chords = second - first
        denominators = np.einsum("ij,ij->i", chords, chords)
        numerators = np.einsum("ij,ij->i", point - first, chords)
        fractions = np.divide(
            numerators,
            denominators,
            out=np.zeros_like(numerators),
            where=denominators > 1.0e-20,
        )
        fractions = np.clip(fractions, 0.0, 1.0)
        distances = np.linalg.norm(
            point - (first + fractions[:, None] * chords), axis=1
        )
        edge_depths = depths[first_indices] + fractions * (
            depths[second_indices] - depths[first_indices]
        )
        minimum = float(np.min(distances))
        ties = distances <= minimum + 1.0e-9
        return float(np.max(edge_depths[ties])), minimum

    def _nearest_curve_station(
        self, visual: _CurveVisual, event: Any
    ) -> tuple[float, int, float, float]:
        """Snap to a sampled chord in pixels and interpolate its station.

        At projected self-crossings equally near chords are resolved by the
        largest omitted world coordinate (the frontmost branch).  A chord
        collapsed by the projection represents a station interval; its
        frontmost endpoint is selected, with the lower station as the final
        deterministic tie-break.
        """

        if not len(visual.projected):
            return 0.0, 0, -math.inf, math.inf
        event_pixels = self._event_pixels(event)
        if event_pixels is None:
            station = float(visual.stations[0]) if len(visual.stations) else 0.0
            segment = (
                int(visual.segment_indices[0]) if len(visual.segment_indices) else 0
            )
            depth = float(visual.points[0, self.depth_axis])
            return station, segment, depth, math.inf
        screen_points = np.asarray(
            self.ax.transData.transform(visual.projected), dtype=float
        )
        depths = visual.points[:, self.depth_axis]
        if len(screen_points) == 1:
            distance = float(np.linalg.norm(screen_points[0] - event_pixels))
            station = float(visual.stations[0]) if len(visual.stations) else 0.0
            segment = (
                int(visual.segment_indices[0]) if len(visual.segment_indices) else 0
            )
            return station, segment, float(depths[0]), distance

        first = screen_points[:-1]
        chords = screen_points[1:] - first
        denominators = np.einsum("ij,ij->i", chords, chords)
        offsets = event_pixels - first
        numerators = np.einsum("ij,ij->i", offsets, chords)
        fractions = np.divide(
            numerators,
            denominators,
            out=np.zeros_like(numerators),
            where=denominators > 1.0e-20,
        )
        fractions = np.clip(fractions, 0.0, 1.0)
        collapsed = denominators <= 1.0e-20
        fractions[collapsed] = (depths[1:][collapsed] > depths[:-1][collapsed]).astype(
            float
        )
        nearest = first + fractions[:, None] * chords
        distances = np.linalg.norm(event_pixels - nearest, axis=1)
        stations = visual.stations[:-1] + fractions * np.diff(visual.stations)
        chord_depths = depths[:-1] + fractions * np.diff(depths)
        chord_indices = np.arange(len(fractions), dtype=np.int64)
        sample_indices = chord_indices + (fractions >= 1.0)
        segments = visual.segment_indices[sample_indices]

        minimum = float(np.min(distances))
        tie_tolerance = max(1e-7, minimum * 1e-9)
        near = np.flatnonzero(distances <= minimum + tie_tolerance)
        # The usually-single tie set stays tiny; retaining the tuple comparison
        # here exactly matches the deterministic frontmost/station/index rules.
        index = max(
            (int(value) for value in near),
            key=lambda value: (
                float(chord_depths[value]),
                -float(stations[value]),
                -value,
                -float(fractions[value]),
            ),
        )
        return (
            float(stations[index]),
            int(segments[index]),
            float(chord_depths[index]),
            float(distances[index]),
        )

    @staticmethod
    def _same_selection(left: _Selection, right: _Selection | None) -> bool:
        if right is None or left.kind != right.kind or left.entity is not right.entity:
            return False
        if left.kind == "curve":
            return left.segment_index == right.segment_index
        return left.frame_name == right.frame_name

    # ---------------------------------------------------------- context/repr

    def __enter__(self) -> Self:
        self._ensure_open()
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    def __repr__(self) -> str:
        state = "closed" if self._closed else "open"
        return (
            f"LayoutViewer2D(projection={self.projection!r}, "
            f"curves={self._curve_count}, objects={self._object_count}, "
            f"state={state!r})"
        )


__all__ = ["LayoutViewer2D"]
