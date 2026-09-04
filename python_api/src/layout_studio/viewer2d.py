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


@dataclass
class _ObjectVisual:
    name: str
    entity: Any
    vertices: np.ndarray
    projected: np.ndarray
    faces: np.ndarray
    feature_edges: list[tuple[int, int]]
    fill: Any
    edges: Any
    color: tuple[float, float, float]
    pose: Any


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
        frames: bool = True,
        selection: Any = None,
        show: bool = True,
        figsize: tuple[float, float] = (10.0, 7.2),
        dpi: float = 100.0,
        background: Any = _DEFAULT_BACKGROUND,
        curves_visible: bool | None = None,
        objects_visible: bool | None = None,
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
        self._mpl = _require_matplotlib()
        self._plt = self._mpl.pyplot

        from .resolver import Resolver

        self.resolver = Resolver(layout)
        validate = getattr(self.resolver, "validate", None)
        if callable(validate):
            validate()

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
        self.curves_visible = (
            bool_curves if curves_visible is None else bool(curves_visible)
        )
        self.objects_visible = (
            bool_objects if objects_visible is None else bool(objects_visible)
        )
        self.beam_frames_visible = bool(beam_frames)
        self.frames_visible = bool(frames)
        self.grid_visible = True

        self._closed = False
        self._draw_started = False
        self._selection: _Selection | None = None
        self._hover: _Selection | None = None
        self._curve_visuals: dict[str, _CurveVisual] = {}
        self._object_visuals: dict[str, _ObjectVisual] = {}
        self._pick_targets: dict[Any, _PickTarget] = {}
        self._curve_artists: list[Any] = []
        self._object_artists: list[Any] = []
        self._beam_frame_artists: list[Any] = []
        self._named_frame_artists: list[Any] = []
        self._bounds_points: list[np.ndarray] = []
        self._callbacks: list[int] = []

        self._build_figure(ax)
        self._build_entity_geometry()
        self._finish_bounds()
        self._build_object_frames()
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

    def _build_entity_geometry(self) -> None:
        for fallback_name, curve in self._curve_items:
            self._add_curve(_safe_name(curve, fallback_name), curve)
        for fallback_name, obj in self._object_items:
            self._add_object(_safe_name(obj, fallback_name), obj)

    def _add_curve(self, name: str, curve: Any) -> None:
        data = self.resolver.sampled_curve(curve)
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
        visual = _CurveVisual(
            name, curve, points, projected, stations, indices, line, halo, color
        )
        self._curve_visuals[name] = visual
        self._curve_artists.extend((halo, line))
        self._pick_targets[line] = _PickTarget("curve", name, curve)
        self._bounds_points.append(points)

    def _add_object(self, name: str, obj: Any) -> None:
        data = self.resolver.swept_object_mesh(obj)
        vertices = _as_points(_data_field(data, "vertices", ()))
        faces = np.asarray(_data_field(data, "faces", ()), dtype=int).reshape((-1, 3))
        if not len(vertices):
            return
        if len(faces) and (np.min(faces) < 0 or np.max(faces) >= len(vertices)):
            raise ValueError("object mesh face contains an invalid vertex index")
        projected = self._project(vertices)
        triangles = vertices[faces]
        normals = np.cross(
            triangles[:, 1] - triangles[:, 0],
            triangles[:, 2] - triangles[:, 0],
        )
        # Draw only the front-facing skin.  Painting every translucent triangle
        # (including the back surface) makes a projected solid spuriously
        # opaque because Matplotlib composites each triangle independently.
        front_faces = faces[normals[:, self.depth_axis] > 1e-14]
        if len(front_faces):
            order = np.argsort(
                np.mean(vertices[front_faces, self.depth_axis], axis=1),
                kind="stable",
            )
            front_faces = front_faces[order]
        polygons = [projected[face] for face in front_faces]
        type_ = self._object_type(obj)
        color_value = _data_field(data, "color", getattr(type_, "color", None))
        color = self._color(color_value, _OBJECT_FALLBACK)
        fill = self._mpl.PolyCollection(
            polygons,
            facecolors=[(*color, 0.20)],
            edgecolors="none",
            closed=True,
            picker=True,
            zorder=3,
        )
        self.ax.add_collection(fill)
        edge_pairs = self._feature_edges(vertices, faces)
        segments = [
            [projected[first], projected[second]] for first, second in edge_pairs
        ]
        edge_color = self._lighten(color, 0.24)
        edges = self._mpl.LineCollection(
            segments,
            colors=[(*edge_color, 0.68)],
            linewidths=0.7,
            picker=5,
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
        )
        self._object_visuals[name] = visual
        self._object_artists.extend((fill, edges))
        target = _PickTarget("object", name, obj, pose)
        self._pick_targets[fill] = target
        self._pick_targets[edges] = target
        self._bounds_points.append(vertices)

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
        if self._bounds_points:
            points = np.concatenate(self._bounds_points, axis=0)
        else:
            points = np.asarray([[-1.0, -1.0, -1.0], [1.0, 1.0, 1.0]])
        low, high = np.min(points, axis=0), np.max(points, axis=0)
        self._world_bounds = np.asarray(
            [low[0], high[0], low[1], high[1], low[2], high[2]], dtype=float
        )
        projected = points[:, self.axis_indices]
        projected_low = np.min(projected, axis=0)
        projected_high = np.max(projected, axis=0)
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

    def _build_object_frames(self) -> None:
        for object_name, visual in self._object_visuals.items():
            obj = visual.entity
            type_ = self._object_type(obj)
            for fallback_name, _frame in _mapping_items(getattr(type_, "frames", None)):
                frame_name = str(fallback_name)
                pose = self.resolver.object_named_frame(obj, frame_name)
                self._add_named_frame(object_name, obj, frame_name, pose)
                self._bounds_points.append(_pose_origin(pose).reshape(1, 3))
            for frame_name in ("magnetic_entry", "magnetic_exit"):
                pose = self.resolver.object_named_frame(obj, frame_name)
                vertices = self._beam_plane_vertices(type_, pose)
                self._add_beam_frame(object_name, obj, frame_name, pose, vertices)
                self._bounds_points.append(vertices)

    def _add_named_frame(
        self, object_name: str, obj: Any, frame_name: str, pose: Any
    ) -> None:
        origin = _pose_origin(pose)
        scale = max(0.035, self._scene_scale * 0.025)
        axes = _pose_axes(pose)
        segments = [
            self._project(np.asarray([origin, origin + scale * axis])) for axis in axes
        ]
        axis_lines = self._mpl.LineCollection(
            segments,
            colors=_AXIS_COLORS,
            linewidths=1.2,
            zorder=7,
        )
        self.ax.add_collection(axis_lines)
        point = self._project(origin.reshape(1, 3))[0]
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
                    for angle in np.linspace(0.0, 2.0 * math.pi, 32, endpoint=False)
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
                connect("close_event", self._on_close_event),
            ]
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
        self.curves_visible = bool(visible)
        self._apply_layer_visibility()
        self._request_draw()
        return self

    def set_objects_visible(self, visible: bool = True) -> Self:
        self.objects_visible = bool(visible)
        self._apply_layer_visibility()
        self._request_draw()
        return self

    def set_beam_frames_visible(self, visible: bool = True) -> Self:
        self.beam_frames_visible = bool(visible)
        self._apply_layer_visibility()
        self._request_draw()
        return self

    def set_frames_visible(self, visible: bool = True) -> Self:
        self.frames_visible = bool(visible)
        self._apply_layer_visibility()
        self._request_draw()
        return self

    def set_grid_visible(self, visible: bool = True) -> Self:
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
        self.ax.set_aspect("equal", adjustable="box")
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
        self.canvas.draw()
        return self

    render = draw

    def show(self, *, block: bool | None = None) -> Self:
        """Draw and ask Matplotlib's active backend to display the figure."""

        self._ensure_open()
        self._draw_started = True
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
        self.figure.savefig(
            path,
            dpi=self.dpi if dpi is None else dpi,
            transparent=transparent,
            **kwargs,
        )
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
            return
        self._disconnect_callbacks()
        if self._owns_figure:
            self._plt.close(self.figure)
        self._closed = True

    def _on_close_event(self, _event: Any) -> None:
        self._disconnect_callbacks()
        self._closed = True

    def _disconnect_callbacks(self) -> None:
        for callback in self._callbacks:
            self.canvas.mpl_disconnect(callback)
        self._callbacks.clear()

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("the LayoutViewer2D is closed")

    def _request_draw(self) -> None:
        if self._draw_started and not self._closed:
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
        scale = max(0.05, self._scene_scale * 0.045)
        endpoints = [origin + scale * axis for axis in _pose_axes(pose)]
        segments = [
            self._project(np.asarray([origin, endpoint])) for endpoint in endpoints
        ]
        self.local_axes.set_segments(segments)
        point = self._project(origin.reshape(1, 3))[0]
        self.local_origin.set_data([point[0]], [point[1]])
        for label, endpoint in zip(self.local_axis_labels, endpoints):
            projected = self._project(endpoint.reshape(1, 3))[0]
            label.set_position((float(projected[0]), float(projected[1])))
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
        selection = self._pick_selection(event)
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
        self._request_draw()

    def _on_leave(self, _event: Any) -> None:
        self._clear_hover()

    def _clear_hover(self) -> None:
        self._hover = None
        self.tooltip.set_visible(False)
        if self._selection is None:
            self.pose_text.set_text(self._empty_pose_message())
            self._set_local_axes_visible(False)
        else:
            self._set_pose_text(self._selection)
            self._sync_selection_overlay()
        self._request_draw()

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
        for order, (artist, target) in enumerate(self._pick_targets.items()):
            if not artist.get_visible():
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
        if not candidates:
            return None
        return max(candidates.values(), key=self._candidate_key).selection

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
        inside_depths: list[float] = []
        for face in visual.faces:
            first, second, third = (pixels[int(index)] for index in face)
            v0, v1, offset = second - first, third - first, point - first
            denominator = float(v0[0] * v1[1] - v0[1] * v1[0])
            if abs(denominator) <= 1e-14:
                continue
            second_weight = float((offset[0] * v1[1] - offset[1] * v1[0]) / denominator)
            third_weight = float((v0[0] * offset[1] - v0[1] * offset[0]) / denominator)
            first_weight = 1.0 - second_weight - third_weight
            if min(first_weight, second_weight, third_weight) >= -1e-9:
                face_depths = depths[face]
                inside_depths.append(
                    float(
                        first_weight * face_depths[0]
                        + second_weight * face_depths[1]
                        + third_weight * face_depths[2]
                    )
                )
        if inside_depths:
            return max(inside_depths), 0.0

        best_distance = math.inf
        best_depth = -math.inf
        for first_index, second_index in visual.feature_edges:
            first, second = pixels[first_index], pixels[second_index]
            chord = second - first
            denominator = float(np.dot(chord, chord))
            fraction = (
                0.0
                if denominator <= 1e-20
                else float(np.clip(np.dot(point - first, chord) / denominator, 0, 1))
            )
            distance = float(np.linalg.norm(point - (first + fraction * chord)))
            depth = float(
                depths[first_index]
                + fraction * (depths[second_index] - depths[first_index])
            )
            if distance < best_distance - 1e-9 or (
                abs(distance - best_distance) <= 1e-9 and depth > best_depth
            ):
                best_distance, best_depth = distance, depth
        if not math.isfinite(best_depth):
            best_depth = float(np.max(depths, initial=-math.inf))
        return best_depth, best_distance

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

        choices: list[tuple[float, float, float, int, int, float]] = []
        for index in range(len(screen_points) - 1):
            first, second = screen_points[index], screen_points[index + 1]
            chord = second - first
            denominator = float(np.dot(chord, chord))
            if denominator <= 1e-20:
                first_depth, second_depth = (
                    float(depths[index]),
                    float(depths[index + 1]),
                )
                if second_depth > first_depth:
                    fraction = 1.0
                else:
                    fraction = 0.0
            else:
                fraction = float(
                    np.clip(np.dot(event_pixels - first, chord) / denominator, 0, 1)
                )
            nearest = first + fraction * chord
            distance = float(np.linalg.norm(event_pixels - nearest))
            station = float(
                visual.stations[index]
                + fraction * (visual.stations[index + 1] - visual.stations[index])
            )
            depth = float(
                depths[index] + fraction * (depths[index + 1] - depths[index])
            )
            sample_index = index if fraction < 1.0 else index + 1
            segment = int(visual.segment_indices[sample_index])
            choices.append((distance, depth, station, segment, index, fraction))

        minimum = min(choice[0] for choice in choices)
        tie_tolerance = max(1e-7, minimum * 1e-9)
        near = [choice for choice in choices if choice[0] <= minimum + tie_tolerance]
        distance, depth, station, segment, _index, _fraction = max(
            near,
            key=lambda choice: (
                choice[1],
                -choice[2],
                -choice[4],
                -choice[5],
            ),
        )
        return station, segment, depth, distance

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
            f"curves={len(self._curve_visuals)}, objects={len(self._object_visuals)}, "
            f"state={state!r})"
        )


__all__ = ["LayoutViewer2D"]
