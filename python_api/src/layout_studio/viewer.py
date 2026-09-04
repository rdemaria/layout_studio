"""Native VTK viewer for :mod:`layout_studio`.

The modelling package intentionally does not depend on VTK.  This module is
safe to import without it installed; :class:`LayoutViewer` imports VTK only
when a viewer is constructed and then reports an actionable error.

The viewer consumes evaluated geometry from :class:`.resolver.Resolver`.  It
does not walk references itself.  In particular, a scoped view asks the
resolver for the scoped entities (which may evaluate their dependencies), but
never adds those dependencies to the scene.
"""

from __future__ import annotations

import math
import time
from collections.abc import Iterable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
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
_AUTO_CURVE_RESOLUTION = 64
_AUTO_OBJECT_RESOLUTION = 8
_AUTO_RADIAL_RESOLUTION = 12
_OBJECT_BATCH_THRESHOLD = 128
_OBJECT_BATCH_SIZE = 4096
_OBJECT_BATCH_BYTE_BUDGET = 32 * 1024 * 1024
_OBJECT_TRIANGLE_BUDGET = 200_000
_NAMED_FRAME_ARROW_FRACTION = 0.05
_ACTIVE_FRAME_ARROW_FRACTION = 0.08


def _require_vtk() -> Any:
    """Import and return VTK, retaining it as a genuinely optional extra."""

    try:
        import vtk  # type: ignore[import-not-found]
    except (ImportError, ModuleNotFoundError) as exc:
        raise ImportError(
            "The 3D viewer requires the optional VTK dependency. "
            "Install it with `python -m pip install 'layout-studio[viewer]'` "
            "or `python -m pip install vtk`."
        ) from exc
    return vtk


def _hex_color(value: Any, fallback: str) -> tuple[float, float, float]:
    """Turn a common colour representation into a VTK RGB triple."""

    if value is None:
        value = fallback
    if isinstance(value, str):
        text = value.strip()
        text = text.removeprefix("#")
        if len(text) == 3 and all(ch in "0123456789abcdefABCDEF" for ch in text):
            text = "".join(ch * 2 for ch in text)
        if len(text) == 6 and all(ch in "0123456789abcdefABCDEF" for ch in text):
            return tuple(int(text[i : i + 2], 16) / 255.0 for i in (0, 2, 4))
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        try:
            rgb = tuple(float(value[i]) for i in range(3))
        except (IndexError, TypeError, ValueError):
            pass
        else:
            if max(rgb, default=0.0) > 1.0:
                rgb = tuple(channel / 255.0 for channel in rgb)
            return tuple(max(0.0, min(1.0, channel)) for channel in rgb)
    return _hex_color(fallback, "#ffffff") if value != fallback else (1.0, 1.0, 1.0)


def _darken(rgb: Sequence[float], amount: float) -> tuple[float, float, float]:
    return tuple(max(0.0, float(channel) * (1.0 - amount)) for channel in rgb)


def _lighten(rgb: Sequence[float], amount: float) -> tuple[float, float, float]:
    return tuple(
        min(1.0, float(channel) + (1.0 - float(channel)) * amount) for channel in rgb
    )


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
    """Return a pose as a homogeneous matrix with axes in its columns."""

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
    """Return MAD-X theta/phi/psi in radians.

    This is the same decomposition used by the web viewer.  Prefer the model's
    own evaluated value when present, since it is part of the public ``Pose``
    contract.
    """

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


def _nice_step(value: float) -> float:
    if not math.isfinite(value) or value <= 0:
        return 1.0
    power = 10.0 ** math.floor(math.log10(value))
    fraction = value / power
    if fraction <= 1.0:
        nice = 1.0
    elif fraction <= 2.0:
        nice = 2.0
    elif fraction <= 5.0:
        nice = 5.0
    else:
        nice = 10.0
    return nice * power


def _safe_name(entity: Any, fallback: str) -> str:
    value = getattr(entity, "name", None)
    return fallback if value is None else str(value)


@dataclass
class _CurveVisual:
    name: str
    entity: Any
    points: np.ndarray
    stations: np.ndarray
    segment_indices: np.ndarray
    actor: Any
    halo: Any
    color: tuple[float, float, float]


@dataclass
class _ObjectVisual:
    name: str
    entity: Any
    vertices: np.ndarray
    faces: np.ndarray
    actor: Any
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
class _DisplayAxes:
    actor: Any
    pose: Any = None
    active: bool = False
    marker_source: Any = None
    label: Any = None


class LayoutViewer:
    """Interactive, raw-VTK view of a layout or an explicitly scoped subset.

    ``curves`` and ``objects`` deliberately accept two useful forms.  A bool is
    a layer-visibility switch over all entities in the current ``scope``; an
    entity/name or iterable of entities/names is the exact display scope for
    that namespace.  Consequently ``curves=[curve], objects=[]`` evaluates any
    dependencies needed by ``curve`` but displays only that curve.

    Parameters are keyword-only after ``layout`` to make the bool-versus-scope
    distinction apparent at call sites.
    """

    def __init__(
        self,
        layout: Any,
        *,
        scope: Any = None,
        curves: bool | Any | Iterable[Any] = True,
        objects: bool | Any | Iterable[Any] = True,
        beam_frames: bool = False,
        frames: bool | None = None,
        selection: Any = None,
        show: bool = True,
        off_screen: bool = False,
        window_size: tuple[int, int] = (1000, 720),
        background: Any = _DEFAULT_BACKGROUND,
        curve_resolution: int | None = None,
        object_resolution: int | None = None,
        radial_resolution: int | None = None,
        batch_objects: bool | None = None,
        object_batch_size: int = _OBJECT_BATCH_SIZE,
        curves_visible: bool | None = None,
        objects_visible: bool | None = None,
    ) -> None:
        self.layout = layout
        self.off_screen = bool(off_screen)
        self.window_size = self._validate_window_size(window_size)
        self.background = background
        self._vtk = _require_vtk()

        # Import here as well: importing the public model remains VTK-free and
        # circular imports from model plotting methods cannot trap this module.
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

        object_count = len(self._object_items)
        self._requested_curve_resolution = curve_resolution
        self._requested_object_resolution = object_resolution
        self._requested_radial_resolution = radial_resolution
        self.radial_resolution = self._viewer_resolution(
            radial_resolution,
            "radial_resolution",
            self._auto_radial_resolution(object_count),
            minimum=3,
        )
        self.curve_resolution = self._viewer_resolution(
            curve_resolution,
            "curve_resolution",
            self._auto_curve_resolution(len(self._curve_items)),
        )
        self.object_resolution = self._viewer_resolution(
            object_resolution,
            "object_resolution",
            self._auto_object_resolution(object_count, self.radial_resolution),
        )
        self.curve_resolution_effective = self.curve_resolution
        self.object_resolution_effective = self.object_resolution
        self.radial_resolution_effective = self.radial_resolution
        if batch_objects is not None and not isinstance(
            batch_objects, (bool, np.bool_)
        ):
            raise TypeError("batch_objects must be a bool or None")
        self.batch_objects = batch_objects
        self.batched_objects = (
            object_count >= _OBJECT_BATCH_THRESHOLD
            if batch_objects is None
            else bool(batch_objects)
        )
        self.object_batch_size = self._viewer_resolution(
            object_batch_size,
            "object_batch_size",
            _OBJECT_BATCH_SIZE,
        )

        self.curves_visible = (
            bool_curves if curves_visible is None else bool(curves_visible)
        )
        self.objects_visible = (
            bool_objects if objects_visible is None else bool(objects_visible)
        )
        self.beam_frames_visible = bool(beam_frames)
        self.frames_visible = (
            object_count < _OBJECT_BATCH_THRESHOLD if frames is None else bool(frames)
        )

        self._closed = False
        self._closing = False
        self._exit_requested = False
        self._in_interactor = False
        self._render_started = False
        self._interactor_initialised = False
        self._orientation_enabled = False
        self._press_position: tuple[int, int] | None = None
        self._camera_interacting = False
        self._restore_depth_peeling = False
        self._selection: _Selection | None = None
        self._hover: _Selection | None = None
        self._last_hover_pick = 0.0
        self._last_hover_position: tuple[int, int] | None = None
        target_rate = 20.0 if object_count >= _OBJECT_BATCH_THRESHOLD else 30.0
        self._hover_interval = 1.0 / target_rate
        self._observer_tags: list[tuple[Any, int]] = []

        self._curve_visuals: dict[str, _CurveVisual] = {}
        self._object_visuals: dict[str, _ObjectVisual] = {}
        self._object_visual_by_identity: dict[int, _ObjectVisual] = {}
        self._pick_targets: dict[Any, _PickTarget] = {}
        self._batched_pick_targets: dict[Any, tuple[np.ndarray, list[_PickTarget]]] = {}
        self._pending_object_visuals: list[_ObjectVisual] = []
        self._pick_locators: list[Any] = []
        self._curve_actors: list[Any] = []
        self._object_actors: list[Any] = []
        self._beam_frame_actors: list[Any] = []
        self._named_frame_actors: list[Any] = []
        self._decoration_actors: list[Any] = []
        self._display_axes: list[_DisplayAxes] = []
        self._hover_display_axes: _DisplayAxes | None = None
        self._bounds_low = np.full(3, np.inf, dtype=float)
        self._bounds_high = np.full(3, -np.inf, dtype=float)
        self._named_frames_built = False
        self._beam_frames_built = False
        self._object_highlight_actor: Any = None
        self._resolver_context = self.resolver._session()
        self._resolver_context.__enter__()

        try:
            self._build_window()
            # The viewer represents a geometry snapshot.  Keep one resolver
            # session for its lifetime so hover, selection, and lazy layers do
            # not revalidate a large layout or discard resolved world poses.
            self._build_entity_geometry()
            self._finish_bounds()
            self._build_ground_grid()
            if self.frames_visible:
                self._ensure_named_frames()
            if self.beam_frames_visible:
                self._ensure_beam_frames()
            self._build_readouts()
            self._build_orientation_widget()
            self._install_interaction()
            self._apply_layer_visibility()
            self.home()
        except BaseException:
            self.close()
            raise

        try:
            # Public conveniences used in notebooks and lightweight smoke tests.
            self.curve_actors = {
                name: visual.actor for name, visual in self._curve_visuals.items()
            }
            self.object_actors = {
                name: visual.actor for name, visual in self._object_visuals.items()
            }

            if selection is not None:
                self.select(selection)
            if show:
                self.show()
        except BaseException:
            self.close()
            raise

    @staticmethod
    def _validate_window_size(value: Any) -> tuple[int, int]:
        try:
            width, height = value
            width, height = int(width), int(height)
        except (TypeError, ValueError) as exc:
            raise ValueError("window_size must be a (width, height) pair") from exc
        if width <= 0 or height <= 0:
            raise ValueError("window_size dimensions must be positive")
        return width, height

    @staticmethod
    def _viewer_resolution(
        value: Any, name: str, automatic: int, *, minimum: int = 1
    ) -> int:
        """Validate an explicit tessellation value or return its auto value."""

        if value is None:
            return int(automatic)
        if isinstance(value, (bool, np.bool_)):
            raise TypeError(f"{name} must be an integer of at least {minimum}")
        try:
            result = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"{name} must be an integer of at least {minimum}"
            ) from exc
        if result != value or result < minimum:
            raise ValueError(f"{name} must be an integer of at least {minimum}")
        return result

    @staticmethod
    def _auto_curve_resolution(curve_count: int) -> int:
        # Segment boundaries are inserted by Resolver, so even the floor keeps
        # every element junction while limiting regular samples in huge scenes.
        if curve_count <= 0:
            return _AUTO_CURVE_RESOLUTION
        return max(8, min(_AUTO_CURVE_RESOLUTION, 32_768 // curve_count))

    @staticmethod
    def _auto_radial_resolution(object_count: int) -> int:
        if object_count >= 2_000:
            return 6
        if object_count >= _OBJECT_BATCH_THRESHOLD:
            return 8
        return _AUTO_RADIAL_RESOLUTION

    @staticmethod
    def _auto_object_resolution(object_count: int, radial_resolution: int) -> int:
        if object_count <= 0:
            return _AUTO_OBJECT_RESOLUTION
        cross_section_sides = max(4, radial_resolution)
        budget_value = _OBJECT_TRIANGLE_BUDGET // (
            2 * object_count * cross_section_sides
        )
        return max(1, min(_AUTO_OBJECT_RESOLUTION, budget_value))

    def _validate_resolver(self) -> None:
        validate = getattr(self.resolver, "validate", None)
        if callable(validate):
            validate()

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
        values = self._as_scope_values(scope)
        curves: list[tuple[str, Any]] = []
        objects: list[tuple[str, Any]] = []
        for value in values:
            if isinstance(value, str):
                text = value
                if text.startswith("curve:"):
                    name = text[6:]
                    if name not in curve_by_name:
                        raise KeyError(f"unknown curve {name!r}")
                    curves.append((name, curve_by_name[name]))
                    continue
                if text.startswith("object:"):
                    name = text[7:]
                    if name not in object_by_name:
                        raise KeyError(f"unknown object {name!r}")
                    objects.append((name, object_by_name[name]))
                    continue
                in_curves, in_objects = text in curve_by_name, text in object_by_name
                if in_curves and in_objects:
                    raise ValueError(
                        f"scope name {text!r} is ambiguous; use 'curve:' or 'object:'"
                    )
                if in_curves:
                    curves.append((text, curve_by_name[text]))
                elif in_objects:
                    objects.append((text, object_by_name[text]))
                else:
                    raise KeyError(f"unknown curve or object {text!r}")
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
            iterator = iter(value)
        except TypeError:
            return [value]
        return list(iterator)

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
                name = requested
                prefix = f"{kind}:"
                name = name.removeprefix(prefix)
                if name not in by_name:
                    raise KeyError(f"unknown {kind} {name!r} in viewer scope")
                result.append((name, by_name[name]))
            elif id(requested) in by_identity:
                result.append((by_identity[id(requested)], requested))
            else:
                raise ValueError(f"{kind} scope entities must belong to this layout")
        return self._deduplicate(result), True

    # ------------------------------------------------------------------ setup

    def _build_window(self) -> None:
        vtk = self._vtk
        self.renderer = vtk.vtkRenderer()
        background = _hex_color(self.background, _DEFAULT_BACKGROUND)
        self.renderer.SetBackground(*background)
        if hasattr(self.renderer, "SetBackground2"):
            self.renderer.SetBackground2(*_lighten(background, 0.10))
        if hasattr(self.renderer, "GradientBackgroundOn"):
            self.renderer.GradientBackgroundOn()
        if hasattr(self.renderer, "SetUseDepthPeeling"):
            self.renderer.SetUseDepthPeeling(True)
            # A handful of peels is enough for an overview.  The former 100
            # passes made translucent accelerator-scale scenes needlessly
            # expensive, especially while the camera was moving.
            peels = 8 if self.batched_objects else 32
            self.renderer.SetMaximumNumberOfPeels(peels)
            self.renderer.SetOcclusionRatio(0.2 if self.batched_objects else 0.1)

        self.render_window = vtk.vtkRenderWindow()
        self.render_window.AddRenderer(self.renderer)
        self.render_window.SetSize(*self.window_size)
        self.render_window.SetWindowName("Layout Studio — plot3d")
        if hasattr(self.render_window, "SetAlphaBitPlanes"):
            self.render_window.SetAlphaBitPlanes(1)
        if hasattr(self.render_window, "SetMultiSamples"):
            # VTK depth peeling (used for the translucent object shells and
            # Beam planes) requires multisampling to be disabled.
            self.render_window.SetMultiSamples(0)
        if self.off_screen:
            self.render_window.SetOffScreenRendering(1)

        self.interactor = vtk.vtkRenderWindowInteractor()
        self.interactor.SetRenderWindow(self.render_window)
        self.style = vtk.vtkInteractorStyleTrackballCamera()
        self.interactor.SetInteractorStyle(self.style)
        self.camera = self.renderer.GetActiveCamera()
        self.camera.SetViewUp(0.0, 1.0, 0.0)

        # Common aliases make the raw VTK objects discoverable in notebooks.
        self.window = self.render_window

    def _build_entity_geometry(self) -> None:
        for fallback_name, curve in self._curve_items:
            self._add_curve(_safe_name(curve, fallback_name), curve)
        for fallback_name, obj in self._object_items:
            self._add_object(_safe_name(obj, fallback_name), obj)
        if self.batched_objects:
            self._finish_object_batches()

    def _add_curve(self, name: str, curve: Any) -> None:
        data = self.resolver.sampled_curve(curve, self.curve_resolution)
        points = _as_points(_data_field(data, "points", ()))
        if len(points) == 0:
            return
        stations = np.asarray(
            _data_field(data, "stations", np.arange(len(points), dtype=float)),
            dtype=float,
        ).reshape(-1)
        if len(stations) != len(points):
            stations = np.linspace(
                0.0, float(getattr(curve, "length", 0.0)), len(points)
            )
        segment_indices = np.asarray(
            _data_field(data, "segment_indices", np.zeros(len(points), dtype=int)),
            dtype=int,
        ).reshape(-1)
        if len(segment_indices) != len(points):
            segment_indices = np.zeros(len(points), dtype=int)

        polydata = self._polyline_data(points)
        color = _hex_color(getattr(curve, "color", None), _CURVE_FALLBACK)

        halo_mapper = self._vtk.vtkPolyDataMapper()
        halo_mapper.SetInputData(polydata)
        halo = self._vtk.vtkActor()
        halo.SetMapper(halo_mapper)
        halo.GetProperty().SetColor(*color)
        halo.GetProperty().SetOpacity(0.18)
        halo.GetProperty().SetLineWidth(9.0)
        self._render_lines_as_tubes(halo)
        halo.PickableOff()
        self.renderer.AddActor(halo)

        mapper = self._vtk.vtkPolyDataMapper()
        mapper.SetInputData(polydata)
        actor = self._vtk.vtkActor()
        actor.SetMapper(mapper)
        actor.GetProperty().SetColor(*color)
        actor.GetProperty().SetOpacity(1.0)
        actor.GetProperty().SetLineWidth(3.0)
        self._render_lines_as_tubes(actor)
        self.renderer.AddActor(actor)

        visual = _CurveVisual(
            name, curve, points, stations, segment_indices, actor, halo, color
        )
        self._curve_visuals[name] = visual
        self._curve_actors.extend((halo, actor))
        self._register_pick_target(actor, _PickTarget("curve", name, curve))
        self._accumulate_bounds(points)

    def _polyline_data(self, points: np.ndarray) -> Any:
        vtk = self._vtk
        from vtk.util.numpy_support import (  # type: ignore[import-not-found]
            numpy_to_vtk,
            numpy_to_vtkIdTypeArray,
        )

        values = np.ascontiguousarray(points, dtype=np.float64)
        vtk_points = vtk.vtkPoints()
        vtk_points.SetData(numpy_to_vtk(values, deep=True))
        polydata = vtk.vtkPolyData()
        polydata.SetPoints(vtk_points)
        cells = vtk.vtkCellArray()
        if len(points) == 1:
            offsets = np.asarray([0, 1], dtype=np.int64)
            connectivity = np.asarray([0], dtype=np.int64)
            cells.SetData(
                numpy_to_vtkIdTypeArray(offsets, deep=True),
                numpy_to_vtkIdTypeArray(connectivity, deep=True),
            )
            polydata.SetVerts(cells)
        else:
            offsets = np.asarray([0, len(points)], dtype=np.int64)
            connectivity = np.arange(len(points), dtype=np.int64)
            cells.SetData(
                numpy_to_vtkIdTypeArray(offsets, deep=True),
                numpy_to_vtkIdTypeArray(connectivity, deep=True),
            )
            polydata.SetLines(cells)
        return polydata

    @staticmethod
    def _render_lines_as_tubes(actor: Any) -> None:
        prop = actor.GetProperty()
        method = getattr(prop, "RenderLinesAsTubesOn", None)
        if callable(method):
            method()

    def _add_object(self, name: str, obj: Any) -> None:
        type_ = self._object_type(obj)
        mesh_resolution = self._object_mesh_resolution(type_)
        data = self.resolver.swept_object_mesh(
            obj,
            resolution=mesh_resolution,
            radial_resolution=self.radial_resolution,
            include_metadata=False,
        )
        vertices = _as_points(_data_field(data, "vertices", ()))
        faces = self._triangulated_faces(vertices, _data_field(data, "faces", ()))
        if len(vertices) == 0:
            return

        pose = self.resolver.object_frame(obj)
        color_value = _data_field(data, "color", getattr(type_, "color", None))
        color = _hex_color(color_value, _OBJECT_FALLBACK)
        visual = _ObjectVisual(name, obj, vertices, faces, None, color, pose)
        self._object_visuals[name] = visual
        self._object_visual_by_identity[id(obj)] = visual
        self._accumulate_bounds(vertices)

        if self.batched_objects:
            self._pending_object_visuals.append(visual)
            return

        polydata = self._surface_data(vertices, faces)
        normals = self._vtk.vtkPolyDataNormals()
        normals.SetInputData(polydata)
        normals.ConsistencyOn()
        normals.AutoOrientNormalsOn()
        normals.SplittingOff()

        mapper = self._vtk.vtkPolyDataMapper()
        mapper.SetInputConnection(normals.GetOutputPort())
        actor = self._vtk.vtkActor()
        actor.SetMapper(mapper)
        prop = actor.GetProperty()
        prop.SetColor(*color)
        prop.SetOpacity(0.34)
        prop.EdgeVisibilityOn()
        prop.SetEdgeColor(*_lighten(color, 0.22))
        prop.SetLineWidth(1.15)
        prop.SetInterpolationToPhong()
        prop.SetAmbient(0.22)
        prop.SetDiffuse(0.72)
        prop.SetSpecular(0.18)
        prop.SetSpecularPower(20.0)
        self.renderer.AddActor(actor)

        visual.actor = actor
        self._object_actors.append(actor)
        self._register_pick_target(actor, _PickTarget("object", name, obj, pose))

    def _object_mesh_resolution(self, type_: Any) -> int:
        """Use the exact two-end-section skin for every straight primitive."""

        shape = getattr(type_, "shape", None)
        curvature = getattr(shape, "curvature", None)
        length = getattr(shape, "dz", None)
        if (
            curvature is None
            and isinstance(shape, Sequence)
            and not isinstance(shape, (str, bytes))
        ):
            values = list(shape)
            if values:
                kind = str(values[0]).lower()
                index = 4 if kind == "box" else 3 if kind == "cylinder" else -1
                if index >= 0 and len(values) > index:
                    curvature = values[index]
                    length = values[3 if kind == "box" else 2]
        try:
            curvature_value = abs(float(curvature or 0.0))
            length_value = abs(float(length or 0.0))
        except (TypeError, ValueError):
            return self.object_resolution
        if curvature_value <= 1.0e-14:
            return 1
        if self._requested_object_resolution is not None:
            return self.object_resolution
        # Retain large arcs, but keep automatic detail inside the scene-wide
        # triangle budget.  Without this cap thousands of curved objects could
        # each force 64 sections and defeat the large-layout LOD entirely.
        angle_resolution = math.ceil(curvature_value * length_value / math.radians(7.5))
        sides = max(4, getattr(self, "radial_resolution", _AUTO_RADIAL_RESOLUTION))
        scene_cap = max(
            2,
            _OBJECT_TRIANGLE_BUDGET
            // (2 * max(1, getattr(self, "_object_count", 1)) * sides),
        )
        return max(
            2,
            self.object_resolution,
            min(64, scene_cap, angle_resolution),
        )

    @staticmethod
    def _triangulated_faces(vertices: np.ndarray, faces: Any) -> np.ndarray:
        try:
            array = np.asarray(faces)
        except (TypeError, ValueError):
            # NumPy 1.24+ rejects ragged polygon lists instead of producing an
            # object array.  The general fan-triangulation path supports them.
            array = np.asarray((), dtype=np.int64)
        if array.ndim == 2 and array.shape[1:] == (3,):
            try:
                triangles = np.ascontiguousarray(array, dtype=np.int64)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError(
                    "object mesh faces must contain integer indices"
                ) from exc
            if len(triangles) and (
                np.min(triangles) < 0 or np.max(triangles) >= len(vertices)
            ):
                raise ValueError("object mesh face contains an invalid vertex index")
            return triangles
        triangles: list[tuple[int, int, int]] = []
        for face in faces:
            ids = [int(index) for index in face]
            if len(ids) < 3:
                continue
            if any(index < 0 or index >= len(vertices) for index in ids):
                raise ValueError("object mesh face contains an invalid vertex index")
            triangles.extend(
                (ids[0], ids[index], ids[index + 1]) for index in range(1, len(ids) - 1)
            )
        return np.asarray(triangles, dtype=np.int64).reshape((-1, 3))

    def _finish_object_batches(self) -> None:
        """Build chunked, directly-coloured object actors for large layouts."""

        visuals = self._pending_object_visuals
        batch: list[_ObjectVisual] = []
        batch_bytes = 0
        for visual in visuals:
            # Batching duplicates offset connectivity and then deep-copies the
            # arrays into VTK. Bound raw NumPy intermediates as well as object
            # count so an explicit high-resolution mesh cannot create a
            # multi-gigabyte 4096-object concatenation.
            visual_bytes = (
                int(visual.vertices.nbytes)
                + 2 * int(visual.faces.nbytes)
                + 3 * len(visual.faces)
            )
            if batch and (
                len(batch) >= self.object_batch_size
                or batch_bytes + visual_bytes > _OBJECT_BATCH_BYTE_BUDGET
            ):
                self._add_object_batch(batch)
                batch = []
                batch_bytes = 0
            batch.append(visual)
            batch_bytes += visual_bytes
        self._add_object_batch(batch)
        visuals.clear()

    def _add_object_batch(self, visuals: list[_ObjectVisual]) -> None:
        if not visuals:
            return
        vertices_parts: list[np.ndarray] = []
        face_parts: list[np.ndarray] = []
        color_parts: list[np.ndarray] = []
        targets: list[_PickTarget] = []
        cell_ends: list[int] = []
        vertex_offset = 0
        cell_offset = 0
        for visual in visuals:
            vertices_parts.append(visual.vertices)
            face_parts.append(visual.faces + vertex_offset)
            rgb = np.rint(np.asarray(visual.color) * 255.0).astype(np.uint8)
            color_parts.append(np.repeat(rgb[None, :], len(visual.faces), axis=0))
            vertex_offset += len(visual.vertices)
            if len(visual.faces):
                cell_offset += len(visual.faces)
                cell_ends.append(cell_offset)
                targets.append(
                    _PickTarget("object", visual.name, visual.entity, visual.pose)
                )

        vertices = np.concatenate(vertices_parts, axis=0)
        faces = np.concatenate(face_parts, axis=0)
        colors = np.concatenate(color_parts, axis=0)
        polydata = self._surface_data(vertices, faces)

        from vtk.util.numpy_support import (  # type: ignore[import-not-found]
            numpy_to_vtk,
        )

        vtk_colors = numpy_to_vtk(
            np.ascontiguousarray(colors),
            deep=True,
            array_type=self._vtk.VTK_UNSIGNED_CHAR,
        )
        vtk_colors.SetName("LayoutObjectColor")
        polydata.GetCellData().SetScalars(vtk_colors)

        mapper = self._vtk.vtkPolyDataMapper()
        mapper.SetInputData(polydata)
        mapper.SetScalarModeToUseCellData()
        mapper.SetColorModeToDirectScalars()
        mapper.ScalarVisibilityOn()
        actor = self._vtk.vtkActor()
        actor.SetMapper(mapper)
        prop = actor.GetProperty()
        prop.SetOpacity(0.34)
        # Overview scenes trade per-triangle outlines and Phong normals for a
        # vastly smaller render pipeline.  Selection still gets a crisp overlay.
        if len(self._object_items) < 2_000:
            prop.EdgeVisibilityOn()
            prop.SetEdgeColor(*_hex_color("#9eb0bb", "#9eb0bb"))
            prop.SetLineWidth(1.0)
        else:
            prop.EdgeVisibilityOff()
        prop.SetInterpolationToFlat()
        prop.SetAmbient(0.28)
        prop.SetDiffuse(0.72)
        self.renderer.AddActor(actor)
        self._object_actors.append(actor)

        for visual in visuals:
            visual.actor = actor
        if targets:
            self._batched_pick_targets[actor] = (
                np.asarray(cell_ends, dtype=np.int64),
                targets,
            )

        locator = self._vtk.vtkStaticCellLocator()
        locator.SetDataSet(polydata)
        locator.BuildLocator()
        self._pick_locators.append(locator)

    def _surface_data(self, vertices: np.ndarray, faces: Any) -> Any:
        vtk = self._vtk
        from vtk.util.numpy_support import (  # type: ignore[import-not-found]
            numpy_to_vtk,
            numpy_to_vtkIdTypeArray,
        )

        vertex_array = np.ascontiguousarray(vertices, dtype=np.float64)
        points = vtk.vtkPoints()
        points.SetData(numpy_to_vtk(vertex_array, deep=True))

        try:
            face_array = np.asarray(faces)
        except ValueError:
            face_array = np.asarray((), dtype=np.int64)
        if face_array.ndim == 2 and face_array.dtype != object:
            cell_width = face_array.shape[1]
            if cell_width < 3:
                connectivity = np.empty(0, dtype=np.int64)
                offsets = np.asarray([0], dtype=np.int64)
            else:
                connectivity = np.ascontiguousarray(face_array, dtype=np.int64).reshape(
                    -1
                )
                offsets = np.arange(
                    0,
                    (len(face_array) + 1) * cell_width,
                    cell_width,
                    dtype=np.int64,
                )
        else:
            rows: list[np.ndarray] = []
            for face in faces:
                ids = np.asarray(tuple(face), dtype=np.int64).reshape(-1)
                if len(ids) >= 3:
                    rows.append(ids)
            counts = np.fromiter((len(row) for row in rows), dtype=np.int64)
            offsets = np.empty(len(rows) + 1, dtype=np.int64)
            offsets[0] = 0
            np.cumsum(counts, out=offsets[1:])
            connectivity = np.concatenate(rows) if rows else np.empty(0, dtype=np.int64)

        if len(connectivity) and (
            np.min(connectivity) < 0 or np.max(connectivity) >= len(vertex_array)
        ):
            raise ValueError("object mesh face contains an invalid vertex index")
        polygons = vtk.vtkCellArray()
        polygons.SetData(
            numpy_to_vtkIdTypeArray(np.ascontiguousarray(offsets), deep=True),
            numpy_to_vtkIdTypeArray(np.ascontiguousarray(connectivity), deep=True),
        )
        polydata = vtk.vtkPolyData()
        polydata.SetPoints(points)
        polydata.SetPolys(polygons)
        return polydata

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
        self._bounds = np.asarray(
            [low[0], high[0], low[1], high[1], low[2], high[2]], dtype=float
        )
        extent = high - low
        self._scene_scale = max(1.0, float(np.linalg.norm(extent)))
        self.bounds = tuple(float(value) for value in self._bounds)

    def _accumulate_bounds(self, points: Any) -> None:
        values = _as_points(points)
        if not len(values):
            return
        self._bounds_low = np.minimum(self._bounds_low, np.min(values, axis=0))
        self._bounds_high = np.maximum(self._bounds_high, np.max(values, axis=0))

    def _extend_bounds(self, points: Any) -> None:
        self._accumulate_bounds(points)
        self._bounds[[0, 2, 4]] = self._bounds_low
        self._bounds[[1, 3, 5]] = self._bounds_high
        self.bounds = tuple(float(value) for value in self._bounds)

    def _build_ground_grid(self) -> None:
        xmin, xmax, _ymin, _ymax, zmin, zmax = self._bounds
        horizontal_span = max(float(xmax - xmin), float(zmax - zmin), 2.0)
        major = _nice_step(horizontal_span / 8.0)
        minor = major / 5.0
        reach = max(
            abs(float(xmin)), abs(float(xmax)), abs(float(zmin)), abs(float(zmax))
        )
        reach = max(major * 5.0, math.ceil((reach + major) / major) * major)
        count = min(100, max(5, math.ceil(reach / minor)))

        minor_lines: list[tuple[Sequence[float], Sequence[float]]] = []
        major_lines: list[tuple[Sequence[float], Sequence[float]]] = []
        axis_lines: list[tuple[Sequence[float], Sequence[float]]] = []
        for index in range(-count, count + 1):
            value = index * minor
            if abs(value) > reach + minor * 0.5:
                continue
            lines = [
                ((-reach, 0.0, value), (reach, 0.0, value)),
                ((value, 0.0, -reach), (value, 0.0, reach)),
            ]
            if index == 0:
                axis_lines.extend(lines)
            elif index % 5 == 0:
                major_lines.extend(lines)
            else:
                minor_lines.extend(lines)

        self._add_line_segments(minor_lines, "#60758a", 0.13, 1.0)
        self._add_line_segments(major_lines, "#7891a8", 0.24, 1.0)
        self._add_line_segments(axis_lines, "#86a8c3", 0.42, 1.35)

    def _add_line_segments(
        self,
        lines: Iterable[tuple[Sequence[float], Sequence[float]]],
        color: Any,
        opacity: float,
        width: float,
    ) -> Any:
        vtk = self._vtk
        points = vtk.vtkPoints()
        cells = vtk.vtkCellArray()
        index = 0
        for start, end in lines:
            points.InsertNextPoint(*(float(value) for value in start))
            points.InsertNextPoint(*(float(value) for value in end))
            cells.InsertNextCell(2)
            cells.InsertCellPoint(index)
            cells.InsertCellPoint(index + 1)
            index += 2
        polydata = vtk.vtkPolyData()
        polydata.SetPoints(points)
        polydata.SetLines(cells)
        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputData(polydata)
        actor = vtk.vtkActor()
        actor.SetMapper(mapper)
        actor.GetProperty().SetColor(*_hex_color(color, "#60758a"))
        actor.GetProperty().SetOpacity(float(opacity))
        actor.GetProperty().SetLineWidth(float(width))
        actor.PickableOff()
        self.renderer.AddActor(actor)
        self._decoration_actors.append(actor)
        return actor

    def _ensure_named_frames(self) -> None:
        """Build stored frame markers only when their layer is first shown."""

        if self._named_frames_built:
            return
        records: list[tuple[str, Any, str, Any]] = []
        with self.resolver._session():
            for name, visual in self._object_visuals.items():
                obj = visual.entity
                type_ = self._object_type(obj)
                for fallback_frame_name, _frame in _mapping_items(
                    getattr(type_, "frames", None)
                ):
                    frame_name = str(fallback_frame_name)
                    pose = self.resolver.object_named_frame(obj, frame_name)
                    records.append((name, obj, frame_name, pose))

        if self.batched_objects:
            self._add_batched_named_frames(records)
        else:
            for name, obj, frame_name, pose in records:
                self._add_named_frame(name, obj, frame_name, pose)
                self._extend_bounds([_pose_origin(pose)])
        self._named_frames_built = True
        self._refresh_scene_scale()

    def _ensure_beam_frames(self) -> None:
        """Build Beam entry/exit planes only when their layer is first shown."""

        if self._beam_frames_built:
            return
        records: list[tuple[str, Any, str, Any, np.ndarray]] = []
        with self.resolver._session():
            for name, visual in self._object_visuals.items():
                obj = visual.entity
                type_ = self._object_type(obj)
                for frame_name in ("magnetic_entry", "magnetic_exit"):
                    pose = self.resolver.object_named_frame(obj, frame_name)
                    vertices = self._beam_plane_vertices(type_, pose)
                    records.append((name, obj, frame_name, pose, vertices))

        if self.batched_objects:
            self._add_batched_beam_frames(records)
        else:
            for name, obj, frame_name, pose, vertices in records:
                self._add_beam_frame(name, obj, frame_name, pose, vertices)
                self._extend_bounds(vertices)
        self._beam_frames_built = True
        self._refresh_scene_scale()

    def _refresh_scene_scale(self) -> None:
        # Frame extents can be farther from the object's shell than its center.
        extent = np.asarray(
            [
                self._bounds[1] - self._bounds[0],
                self._bounds[3] - self._bounds[2],
                self._bounds[5] - self._bounds[4],
            ]
        )
        self._scene_scale = max(1.0, float(np.linalg.norm(extent)))

    def _add_batched_named_frames(
        self, records: list[tuple[str, Any, str, Any]]
    ) -> None:
        if not records:
            return
        vtk = self._vtk
        from vtk.util.numpy_support import (  # type: ignore[import-not-found]
            numpy_to_vtk,
            numpy_to_vtkIdTypeArray,
        )

        origins = np.ascontiguousarray(
            [_pose_origin(pose) for _name, _obj, _frame, pose in records],
            dtype=np.float64,
        )
        points = vtk.vtkPoints()
        points.SetData(numpy_to_vtk(origins, deep=True))
        cells = vtk.vtkCellArray()
        cells.SetData(
            numpy_to_vtkIdTypeArray(
                np.arange(len(records) + 1, dtype=np.int64), deep=True
            ),
            numpy_to_vtkIdTypeArray(np.arange(len(records), dtype=np.int64), deep=True),
        )
        targets: list[_PickTarget] = []
        for object_name, obj, frame_name, pose in records:
            targets.append(
                _PickTarget(
                    "frame", f"{object_name}.{frame_name}", obj, pose, obj, frame_name
                )
            )
        polydata = vtk.vtkPolyData()
        polydata.SetPoints(points)
        polydata.SetVerts(cells)
        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputData(polydata)
        actor = vtk.vtkActor()
        actor.SetMapper(mapper)
        prop = actor.GetProperty()
        prop.SetColor(*_hex_color("#ffca75", "#ffca75"))
        prop.SetPointSize(7.0)
        render_as_spheres = getattr(prop, "RenderPointsAsSpheresOn", None)
        if callable(render_as_spheres):
            render_as_spheres()
        self.renderer.AddActor(actor)
        self._named_frame_actors.append(actor)
        self._register_batched_targets(actor, targets)
        self._extend_bounds(origins)

    def _add_batched_beam_frames(
        self, records: list[tuple[str, Any, str, Any, np.ndarray]]
    ) -> None:
        if not records:
            return
        vtk = self._vtk
        targets: list[_PickTarget] = []
        colors = np.empty((len(records), 3), dtype=np.uint8)
        all_vertices = [
            np.ascontiguousarray(vertices, dtype=np.float64)
            for _name, _obj, _frame, _pose, vertices in records
        ]
        counts = np.fromiter(
            (len(vertices) for vertices in all_vertices),
            dtype=np.int64,
            count=len(all_vertices),
        )
        offsets = np.empty(len(records) + 1, dtype=np.int64)
        offsets[0] = 0
        np.cumsum(counts, out=offsets[1:])
        vertex_array = np.ascontiguousarray(np.concatenate(all_vertices, axis=0))
        connectivity = np.arange(len(vertex_array), dtype=np.int64)
        if np.all(counts == counts[0]):
            faces: Any = connectivity.reshape(len(records), int(counts[0]))
        else:
            faces = np.split(connectivity, offsets[1:-1])
        polydata = self._surface_data(vertex_array, faces)

        for cell_index, (object_name, obj, frame_name, pose, vertices) in enumerate(
            records
        ):
            color_text = "#66c7ff" if frame_name == "magnetic_entry" else "#ff9b78"
            colors[cell_index] = np.rint(
                np.asarray(_hex_color(color_text, color_text)) * 255.0
            ).astype(np.uint8)
            targets.append(
                _PickTarget(
                    "beam_frame",
                    f"{object_name}.{frame_name}",
                    obj,
                    pose,
                    obj,
                    frame_name,
                )
            )

        from vtk.util.numpy_support import (  # type: ignore[import-not-found]
            numpy_to_vtk,
        )

        vtk_colors = numpy_to_vtk(
            np.ascontiguousarray(colors),
            deep=True,
            array_type=vtk.VTK_UNSIGNED_CHAR,
        )
        vtk_colors.SetName("LayoutBeamFrameColor")
        polydata.GetCellData().SetScalars(vtk_colors)

        actors = []
        for wireframe in (False, True):
            mapper = vtk.vtkPolyDataMapper()
            mapper.SetInputData(polydata)
            mapper.SetScalarModeToUseCellData()
            mapper.SetColorModeToDirectScalars()
            mapper.ScalarVisibilityOn()
            actor = vtk.vtkActor()
            actor.SetMapper(mapper)
            prop = actor.GetProperty()
            if wireframe:
                prop.SetRepresentationToWireframe()
                prop.SetOpacity(0.9)
                prop.SetLineWidth(1.5)
                self._render_lines_as_tubes(actor)
            else:
                prop.SetOpacity(0.15)
            self.renderer.AddActor(actor)
            self._register_batched_targets(actor, targets)
            actors.append(actor)
        self._beam_frame_actors.extend(actors)
        self._extend_bounds(vertex_array)

    def _add_named_frame(
        self, object_name: str, obj: Any, frame_name: str, pose: Any
    ) -> None:
        vtk = self._vtk
        axes = vtk.vtkAxesActor()
        axes.SetUserMatrix(self._vtk_matrix(_pose_matrix(pose)))
        axes.SetTotalLength(1.0, 1.0, 1.0)
        axes.SetShaftTypeToLine()
        axes.SetCylinderRadius(0.025)
        axes.SetConeRadius(0.20)
        axes.SetSphereRadius(0.20)
        axes.SetXAxisLabelText("x")
        axes.SetYAxisLabelText("y")
        axes.SetZAxisLabelText("s")
        axes.PickableOff()
        self.renderer.AddActor(axes)

        sphere_source = vtk.vtkSphereSource()
        sphere_source.SetCenter(*(float(value) for value in _pose_origin(pose)))
        sphere_source.SetRadius(1.0)
        sphere_source.SetThetaResolution(14)
        sphere_source.SetPhiResolution(10)
        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputConnection(sphere_source.GetOutputPort())
        marker = vtk.vtkActor()
        marker.SetMapper(mapper)
        marker.GetProperty().SetColor(*_hex_color("#ffca75", "#ffca75"))
        marker.GetProperty().SetAmbient(0.5)
        self.renderer.AddActor(marker)
        self._register_pick_target(
            marker,
            _PickTarget(
                "frame", f"{object_name}.{frame_name}", obj, pose, obj, frame_name
            ),
        )

        label = self._billboard_label(
            frame_name,
            _pose_origin(pose),
            "#ffca75",
            11,
        )
        self._named_frame_actors.extend((axes, marker, label))
        display_axes = _DisplayAxes(
            axes,
            pose,
            marker_source=sphere_source,
            label=label,
        )
        self._display_axes.append(display_axes)
        self._update_display_axes(display_axes)

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
        factor = 1.08
        hx, hy = max(abs(dx) * factor / 2.0, 0.02), max(abs(dy) * factor / 2.0, 0.02)
        origin = _pose_origin(pose)
        x_axis, y_axis, _ = _pose_axes(pose)
        if circular:
            return np.asarray(
                [
                    origin
                    + hx * math.cos(angle) * x_axis
                    + hy * math.sin(angle) * y_axis
                    for angle in np.linspace(0.0, 2.0 * math.pi, 24, endpoint=False)
                ],
                dtype=float,
            )
        return np.asarray(
            [
                origin - hx * x_axis - hy * y_axis,
                origin + hx * x_axis - hy * y_axis,
                origin + hx * x_axis + hy * y_axis,
                origin - hx * x_axis + hy * y_axis,
            ],
            dtype=float,
        )

    def _add_beam_frame(
        self,
        object_name: str,
        obj: Any,
        frame_name: str,
        pose: Any,
        vertices: np.ndarray,
    ) -> None:
        vtk = self._vtk
        color_text = "#66c7ff" if frame_name == "magnetic_entry" else "#ff9b78"
        color = _hex_color(color_text, color_text)
        polydata = self._surface_data(vertices, [range(len(vertices))])

        fill_mapper = vtk.vtkPolyDataMapper()
        fill_mapper.SetInputData(polydata)
        fill = vtk.vtkActor()
        fill.SetMapper(fill_mapper)
        fill.GetProperty().SetColor(*color)
        fill.GetProperty().SetOpacity(0.15)
        self.renderer.AddActor(fill)

        outline_mapper = vtk.vtkPolyDataMapper()
        outline_mapper.SetInputData(polydata)
        outline = vtk.vtkActor()
        outline.SetMapper(outline_mapper)
        outline.GetProperty().SetRepresentationToWireframe()
        outline.GetProperty().SetColor(*color)
        outline.GetProperty().SetOpacity(0.9)
        outline.GetProperty().SetLineWidth(2.0)
        self._render_lines_as_tubes(outline)
        self.renderer.AddActor(outline)

        target = _PickTarget(
            "beam_frame",
            f"{object_name}.{frame_name}",
            obj,
            pose,
            obj,
            frame_name,
        )
        self._register_pick_target(fill, target)
        self._register_pick_target(outline, target)
        short_label = "IN" if frame_name == "magnetic_entry" else "OUT"
        scale = max(0.035, self._scene_scale * 0.018)
        label = self._billboard_label(
            short_label,
            _pose_origin(pose) + _pose_axes(pose)[1] * scale * 0.4,
            color_text,
            12,
        )
        self._beam_frame_actors.extend((fill, outline, label))

    def _billboard_label(
        self, text: str, position: Sequence[float], color: Any, font_size: int
    ) -> Any:
        actor = self._vtk.vtkBillboardTextActor3D()
        actor.SetInput(str(text))
        actor.SetPosition(*(float(value) for value in position))
        prop = actor.GetTextProperty()
        prop.SetColor(*_hex_color(color, "#ffffff"))
        prop.SetFontSize(int(font_size))
        prop.SetBold(True)
        prop.SetJustificationToLeft()
        prop.SetVerticalJustificationToCentered()
        actor.PickableOff()
        self.renderer.AddActor(actor)
        return actor

    def _vtk_matrix(self, matrix: np.ndarray) -> Any:
        result = self._vtk.vtkMatrix4x4()
        for row in range(4):
            for column in range(4):
                result.SetElement(row, column, float(matrix[row, column]))
        return result

    def _reference_arrow_pixels(self, *, active: bool = False) -> float:
        """Return a bounded arrow length relative to the renderer viewport."""

        width, height = (float(value) for value in self.renderer.GetSize())
        if width <= 0.0 or height <= 0.0:
            width, height = (float(value) for value in self.render_window.GetSize())
        shortest = max(1.0, min(width, height))
        if active:
            fraction, minimum, maximum = _ACTIVE_FRAME_ARROW_FRACTION, 36.0, 96.0
        else:
            fraction, minimum, maximum = _NAMED_FRAME_ARROW_FRACTION, 24.0, 64.0
        return max(minimum, min(maximum, fraction * shortest))

    def _world_units_per_pixel(self, origin: Any) -> float:
        """Return the camera-plane world scale at *origin*'s depth."""

        width, height = (float(value) for value in self.renderer.GetSize())
        if width <= 0.0 or height <= 0.0:
            width, height = (float(value) for value in self.render_window.GetSize())
        width, height = max(width, 1.0), max(height, 1.0)
        camera = self.camera
        if bool(camera.GetParallelProjection()):
            scale = 2.0 * abs(float(camera.GetParallelScale())) / height
            return max(scale, np.finfo(float).eps)

        position = np.asarray(camera.GetPosition(), dtype=float)
        direction = _normalised(
            np.asarray(camera.GetDirectionOfProjection(), dtype=float),
            (0.0, 0.0, -1.0),
        )
        depth = float(np.dot(np.asarray(origin, dtype=float) - position, direction))
        if not math.isfinite(depth) or depth <= np.finfo(float).eps:
            depth = max(abs(float(camera.GetDistance())), 1.0)
        angle = math.radians(float(camera.GetViewAngle()))
        angle = min(max(angle, math.radians(0.1)), math.radians(179.0))
        horizontal_angle = getattr(camera, "GetUseHorizontalViewAngle", None)
        use_horizontal = (
            bool(horizontal_angle()) if callable(horizontal_angle) else False
        )
        pixels = width if use_horizontal else height
        scale = 2.0 * depth * math.tan(0.5 * angle) / pixels
        return max(scale, np.finfo(float).eps)

    def _update_display_axes(self, display_axes: _DisplayAxes) -> None:
        if display_axes.pose is None:
            return
        origin = _pose_origin(display_axes.pose)
        world_per_pixel = self._world_units_per_pixel(origin)
        pixels = self._reference_arrow_pixels(active=display_axes.active)
        length = pixels * world_per_pixel
        display_axes.actor.SetTotalLength(length, length, length)
        if display_axes.marker_source is not None:
            display_axes.marker_source.SetRadius(
                world_per_pixel * max(4.0, pixels * 0.1)
            )
        if display_axes.label is not None:
            label_offset = world_per_pixel * max(10.0, pixels * 0.28)
            position = origin + _pose_axes(display_axes.pose)[1] * label_offset
            display_axes.label.SetPosition(*(float(value) for value in position))

    def _update_reference_arrow_sizes(self) -> None:
        for display_axes in self._display_axes:
            self._update_display_axes(display_axes)

    def _on_render_start(self, _caller: Any, _event: str) -> None:
        self._update_reference_arrow_sizes()

    def _build_readouts(self) -> None:
        vtk = self._vtk
        self.pose_text = vtk.vtkTextActor()
        self.pose_text.SetInput(self._empty_pose_message())
        self.pose_text.SetDisplayPosition(14, 14)
        prop = self.pose_text.GetTextProperty()
        prop.SetFontFamilyToCourier()
        prop.SetFontSize(14)
        prop.SetColor(*_hex_color("#b9dce0", "#b9dce0"))
        prop.SetBackgroundColor(*_hex_color("#080d14", "#080d14"))
        prop.SetBackgroundOpacity(0.82)
        prop.SetFrame(True)
        prop.SetFrameColor(*_hex_color("#304052", "#304052"))
        # ``AddActor2D`` was removed from the Python surface in VTK 9.7;
        # ``AddViewProp`` accepts both 2-D overlays and 3-D props across the
        # supported VTK releases.
        self.renderer.AddViewProp(self.pose_text)

        self.tooltip = vtk.vtkTextActor()
        tooltip_prop = self.tooltip.GetTextProperty()
        tooltip_prop.SetFontFamilyToCourier()
        tooltip_prop.SetFontSize(13)
        tooltip_prop.SetBold(True)
        tooltip_prop.SetColor(0.94, 0.98, 0.99)
        tooltip_prop.SetBackgroundColor(*_hex_color("#080f17", "#080f17"))
        tooltip_prop.SetBackgroundOpacity(0.90)
        tooltip_prop.SetFrame(True)
        tooltip_prop.SetFrameColor(*_hex_color("#657783", "#657783"))
        self.tooltip.SetVisibility(False)
        self.renderer.AddViewProp(self.tooltip)

        self.hover_axes = vtk.vtkAxesActor()
        self.hover_axes.SetTotalLength(1.0, 1.0, 1.0)
        self.hover_axes.SetShaftTypeToLine()
        self.hover_axes.SetXAxisLabelText("x")
        self.hover_axes.SetYAxisLabelText("y")
        self.hover_axes.SetZAxisLabelText("s")
        self.hover_axes.PickableOff()
        self.hover_axes.SetVisibility(False)
        self.renderer.AddActor(self.hover_axes)
        self._hover_display_axes = _DisplayAxes(self.hover_axes, active=True)
        self._display_axes.append(self._hover_display_axes)

    @staticmethod
    def _empty_pose_message() -> str:
        return (
            "Hover or click a named frame, Beam frame, object, or curve "
            "to inspect its world pose."
        )

    def _build_orientation_widget(self) -> None:
        vtk = self._vtk
        axes = vtk.vtkAxesActor()
        axes.SetXAxisLabelText("X")
        axes.SetYAxisLabelText("Y")
        axes.SetZAxisLabelText("Z")
        axes.SetShaftTypeToCylinder()
        self.orientation_axes = axes
        widget = vtk.vtkOrientationMarkerWidget()
        widget.SetOrientationMarker(axes)
        widget.SetInteractor(self.interactor)
        widget.SetViewport(0.82, 0.77, 0.99, 0.99)
        self.orientation_widget = widget

    def _install_interaction(self) -> None:
        vtk = self._vtk
        self.picker = vtk.vtkCellPicker()
        self.picker.SetTolerance(0.006)
        self.picker.PickFromListOn()
        for actor in (*self._pick_targets, *self._batched_pick_targets):
            self.picker.AddPickList(actor)
        add_locator = getattr(self.picker, "AddLocator", None)
        if callable(add_locator):
            for locator in self._pick_locators:
                add_locator(locator)
        self.interactor.SetPicker(self.picker)
        self._observe(self.interactor, "KeyPressEvent", self._on_key_press)
        self._observe(self.interactor, "LeftButtonPressEvent", self._on_left_press)
        self._observe(self.interactor, "LeftButtonReleaseEvent", self._on_left_release)
        self._observe(self.interactor, "MouseMoveEvent", self._on_mouse_move)
        self._observe(self.interactor, "LeaveEvent", self._on_mouse_leave)
        self._observe(self.interactor, "ExitEvent", self._on_exit)
        self._observe(self.render_window, "DeleteEvent", self._on_window_delete)
        self._observe(self.renderer, "StartEvent", self._on_render_start)
        self._observe(self.style, "StartInteractionEvent", self._on_interaction_start)
        self._observe(self.style, "EndInteractionEvent", self._on_interaction_end)

    def _observe(self, subject: Any, event: str, callback: Any) -> None:
        tag = subject.AddObserver(event, callback)
        self._observer_tags.append((subject, int(tag)))

    def _add_to_pick_list(self, actor: Any) -> None:
        picker = getattr(self, "picker", None)
        if picker is not None:
            picker.AddPickList(actor)

    def _register_pick_target(self, actor: Any, target: _PickTarget) -> None:
        self._pick_targets[actor] = target
        self._add_to_pick_list(actor)

    def _register_batched_targets(
        self, actor: Any, targets: Sequence[_PickTarget]
    ) -> None:
        if targets:
            self._batched_pick_targets[actor] = (
                np.arange(1, len(targets) + 1, dtype=np.int64),
                list(targets),
            )
        self._add_to_pick_list(actor)

    # --------------------------------------------------------------- layers

    def _apply_layer_visibility(self) -> None:
        for actor in self._curve_actors:
            actor.SetVisibility(self.curves_visible)
        for actor in self._object_actors:
            actor.SetVisibility(self.objects_visible)
        for actor in self._beam_frame_actors:
            actor.SetVisibility(self.objects_visible and self.beam_frames_visible)
        for actor in self._named_frame_actors:
            actor.SetVisibility(self.objects_visible and self.frames_visible)
        if self._object_highlight_actor is not None:
            self._object_highlight_actor.SetVisibility(
                self.objects_visible
                and self._selection is not None
                and self._selection.kind != "curve"
            )

    def set_curves_visible(self, visible: bool = True) -> LayoutViewer:
        self.curves_visible = bool(visible)
        self._apply_layer_visibility()
        self._request_render()
        return self

    def set_objects_visible(self, visible: bool = True) -> LayoutViewer:
        self.objects_visible = bool(visible)
        self._apply_layer_visibility()
        self._request_render()
        return self

    def set_beam_frames_visible(self, visible: bool = True) -> LayoutViewer:
        self.beam_frames_visible = bool(visible)
        if self.beam_frames_visible:
            self._ensure_beam_frames()
        self._apply_layer_visibility()
        self._request_render()
        return self

    def set_frames_visible(self, visible: bool = True) -> LayoutViewer:
        """Show or hide stored, named type-frame markers."""

        self.frames_visible = bool(visible)
        if self.frames_visible:
            self._ensure_named_frames()
            self._update_reference_arrow_sizes()
        self._apply_layer_visibility()
        self._request_render()
        return self

    # ------------------------------------------------------------- camera/io

    def fit(
        self,
        entity: Any = None,
        *,
        preserve_orientation: bool = True,
    ) -> LayoutViewer:
        """Fit the camera to the scope or one curve/object.

        By default the current viewing direction is preserved, matching the
        web viewer's fit interaction.  Pass ``preserve_orientation=False`` or
        call :meth:`home` to restore the canonical isometric direction.
        """

        self._ensure_open()
        original_view_up = tuple(float(value) for value in self.camera.GetViewUp())
        bounds = self._padded_bounds(self._entity_bounds(entity))
        center = np.asarray(
            [
                (bounds[0] + bounds[1]) / 2.0,
                (bounds[2] + bounds[3]) / 2.0,
                (bounds[4] + bounds[5]) / 2.0,
            ],
            dtype=float,
        )
        if not preserve_orientation:
            azimuth, elevation = -0.68, 0.42
            direction = np.asarray(
                [
                    math.sin(azimuth) * math.cos(elevation),
                    math.sin(elevation),
                    math.cos(azimuth) * math.cos(elevation),
                ],
                dtype=float,
            )
            diagonal = math.sqrt(
                (bounds[1] - bounds[0]) ** 2
                + (bounds[3] - bounds[2]) ** 2
                + (bounds[5] - bounds[4]) ** 2
            )
            distance = max(2.0, diagonal * 1.35)
            self.camera.SetFocalPoint(*(float(value) for value in center))
            self.camera.SetPosition(
                *(float(value) for value in center + direction * distance)
            )
            self.camera.SetViewUp(0.0, 1.0, 0.0)
        try:
            self.renderer.ResetCamera(*bounds)
        except TypeError:
            self.renderer.ResetCamera(bounds)
        if preserve_orientation:
            self.camera.SetViewUp(*original_view_up)
        else:
            self.camera.SetViewUp(0.0, 1.0, 0.0)
        try:
            self.renderer.ResetCameraClippingRange(*bounds)
        except TypeError:
            self.renderer.ResetCameraClippingRange(bounds)
        self._update_reference_arrow_sizes()
        self._request_render()
        return self

    def _entity_bounds(self, entity: Any) -> np.ndarray:
        if entity is None or entity is self.layout:
            return self._bounds
        curve_visual: _CurveVisual | None = None
        object_visual: _ObjectVisual | None = None
        if isinstance(entity, str):
            text = entity.strip()
            if text.startswith("curve:"):
                curve_visual = self._curve_visuals.get(text[6:])
            elif text.startswith("object:"):
                object_visual = self._object_visuals.get(text[7:])
            else:
                curve_visual = self._curve_visuals.get(text)
                object_visual = self._object_visuals.get(text)
                if curve_visual is not None and object_visual is not None:
                    raise ValueError(
                        f"fit name {text!r} is ambiguous; use 'curve:' or 'object:'"
                    )
        else:
            curve_visual = next(
                (
                    item
                    for item in self._curve_visuals.values()
                    if item.entity is entity
                ),
                None,
            )
            object_visual = next(
                (
                    item
                    for item in self._object_visuals.values()
                    if item.entity is entity
                ),
                None,
            )
        points = (
            curve_visual.points
            if curve_visual is not None
            else object_visual.vertices
            if object_visual is not None
            else None
        )
        if points is None or not len(points):
            raise ValueError("fit entity is not represented in this viewer scope")
        low, high = np.min(points, axis=0), np.max(points, axis=0)
        return np.asarray(
            [low[0], high[0], low[1], high[1], low[2], high[2]], dtype=float
        )

    def _padded_bounds(
        self, source: Sequence[float] | None = None
    ) -> tuple[float, float, float, float, float, float]:
        result = np.asarray(
            self._bounds if source is None else source, dtype=float
        ).copy()
        local_scale = math.sqrt(
            (result[1] - result[0]) ** 2
            + (result[3] - result[2]) ** 2
            + (result[5] - result[4]) ** 2
        )
        for low_index, high_index in ((0, 1), (2, 3), (4, 5)):
            span = result[high_index] - result[low_index]
            padding = max(local_scale * 0.025, span * 0.06, 1e-3)
            if span < 1e-10:
                padding = max(padding, 0.5)
            result[low_index] -= padding
            result[high_index] += padding
        return tuple(float(value) for value in result)

    def home(self, entity: Any = None) -> LayoutViewer:
        """Fit the scope or entity using the canonical isometric direction."""

        return self.fit(entity, preserve_orientation=False)

    def reset_camera(self) -> LayoutViewer:
        """Restore the canonical isometric view and fit the scoped geometry."""

        return self.home()

    def show(self) -> LayoutViewer:
        """Render the scene and, for native windows, start the interactor."""

        self._ensure_open()
        if self._in_interactor:
            raise RuntimeError("the LayoutViewer interactor is already running")
        if not self.off_screen and not self._interactor_initialised:
            self.interactor.Initialize()
            self._interactor_initialised = True
        self._enable_orientation_widget()
        self._render_started = True
        self.render_window.Render()
        if not self.off_screen:
            self._exit_requested = False
            self._in_interactor = True
            try:
                self.interactor.Start()
            except BaseException:
                self._in_interactor = False
                # Teardown must not replace the exception raised by Start().
                with suppress(Exception):
                    self.close()
                raise
            else:
                self._in_interactor = False
                # Native Start() is blocking. Its return means the event loop
                # ended even on backends which report neither ExitEvent nor a
                # reliable Done flag, so always detach before IPython resumes.
                if not self._closed:
                    self.close()
        return self

    def render(self) -> LayoutViewer:
        """Render once without entering the native event loop."""

        self._ensure_open()
        self._enable_orientation_widget()
        self._render_started = True
        self.render_window.Render()
        return self

    def _enable_orientation_widget(self) -> None:
        if not self._orientation_enabled:
            self.orientation_widget.SetEnabled(True)
            self.orientation_widget.SetInteractive(False)
            self._orientation_enabled = True

    def screenshot(
        self,
        filename: str | Path | None = None,
        *,
        scale: int = 1,
        transparent: bool = False,
    ) -> Path | np.ndarray:
        """Capture the current view.

        With ``filename`` this writes PNG, JPEG, TIFF, or BMP based on its
        suffix and returns the path.  With no filename it returns a top-down
        RGB(A) NumPy image.
        """

        self._ensure_open()
        scale = int(scale)
        if scale < 1:
            raise ValueError("screenshot scale must be at least 1")
        self._enable_orientation_widget()
        self._render_started = True
        self.render_window.Render()

        capture = self._vtk.vtkWindowToImageFilter()
        capture.SetInput(self.render_window)
        capture.SetScale(scale)
        if transparent:
            capture.SetInputBufferTypeToRGBA()
        else:
            capture.SetInputBufferTypeToRGB()
        capture.ReadFrontBufferOff()
        capture.Update()

        if filename is None:
            from vtk.util.numpy_support import (
                vtk_to_numpy,  # type: ignore[import-not-found]
            )

            image = capture.GetOutput()
            width, height, _ = image.GetDimensions()
            components = image.GetNumberOfScalarComponents()
            array = vtk_to_numpy(image.GetPointData().GetScalars())
            return np.flipud(array.reshape(height, width, components)).copy()

        path = Path(filename)
        suffix = path.suffix.lower()
        writers = {
            ".png": self._vtk.vtkPNGWriter,
            ".jpg": self._vtk.vtkJPEGWriter,
            ".jpeg": self._vtk.vtkJPEGWriter,
            ".tif": self._vtk.vtkTIFFWriter,
            ".tiff": self._vtk.vtkTIFFWriter,
            ".bmp": self._vtk.vtkBMPWriter,
        }
        if not suffix:
            path = path.with_suffix(".png")
            suffix = ".png"
        if suffix not in writers:
            raise ValueError("screenshot format must be PNG, JPEG, TIFF, or BMP")
        writer = writers[suffix]()
        writer.SetFileName(str(path))
        writer.SetInputConnection(capture.GetOutputPort())
        writer.Write()
        return path

    def close(self) -> None:
        """Release VTK window resources.  Calling this twice is harmless."""

        if self._closed or self._closing:
            return
        self._closing = True
        # Mark the Python facade closed before invoking VTK teardown: some
        # backends dispatch events synchronously from TerminateApp/Finalize.
        self._closed = True
        self._exit_requested = True
        self._render_started = False
        self._press_position = None
        self._hover = None

        def cleanup(subject: Any, method_name: str, *args: Any) -> None:
            if subject is None:
                return
            with suppress(Exception):
                method = getattr(subject, method_name, None)
                if callable(method):
                    method(*args)

        try:
            orientation_widget = getattr(self, "orientation_widget", None)
            if self._orientation_enabled:
                cleanup(orientation_widget, "SetEnabled", False)
                self._orientation_enabled = False
            cleanup(orientation_widget, "SetInteractor", None)

            for subject, tag in reversed(self._observer_tags):
                cleanup(subject, "RemoveObserver", tag)
            self._observer_tags.clear()

            picker = getattr(self, "picker", None)
            cleanup(picker, "RemoveAllLocators")
            cleanup(picker, "InitializePickList")
            self._pick_locators.clear()
            interactor = getattr(self, "interactor", None)
            cleanup(interactor, "SetPicker", None)
            cleanup(interactor, "TerminateApp")
            if self._interactor_initialised:
                cleanup(interactor, "Disable")

            render_window = getattr(self, "render_window", None)
            cleanup(render_window, "Finalize")
            cleanup(interactor, "SetRenderWindow", None)
            cleanup(interactor, "SetInteractorStyle", None)
            renderer = getattr(self, "renderer", None)
            cleanup(renderer, "RemoveAllViewProps")
            cleanup(render_window, "RemoveRenderer", renderer)

            # IPython retains the value of the last expression in Out[n].
            # Release scene-sized Python/VTK references even while the closed
            # viewer facade itself remains reachable there.
            self._curve_visuals.clear()
            self._object_visuals.clear()
            self._object_visual_by_identity.clear()
            self._pick_targets.clear()
            self._batched_pick_targets.clear()
            self._pending_object_visuals.clear()
            self._curve_actors.clear()
            self._object_actors.clear()
            self._beam_frame_actors.clear()
            self._named_frame_actors.clear()
            self._decoration_actors.clear()
            self._display_axes.clear()
            self._hover_display_axes = None
            self._bounds_low = np.full(3, np.inf, dtype=float)
            self._bounds_high = np.full(3, -np.inf, dtype=float)
            public_curve_actors = getattr(self, "curve_actors", None)
            if hasattr(public_curve_actors, "clear"):
                public_curve_actors.clear()
            public_object_actors = getattr(self, "object_actors", None)
            if hasattr(public_object_actors, "clear"):
                public_object_actors.clear()
            self._object_highlight_actor = None
            self._selection = None
            self._hover = None
            self._curve_items.clear()
            self._object_items.clear()
            self.curve_scope = ()
            self.object_scope = ()
            self.layout = None
        finally:
            resolver_context = self._resolver_context
            self._resolver_context = None
            if resolver_context is not None:
                with suppress(Exception):
                    resolver_context.__exit__(None, None, None)
            self.resolver = None
            self._closing = False

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("the LayoutViewer is closed")

    def _request_render(self) -> None:
        # Merely constructing a topology (notably in headless notebooks/tests)
        # must not contact a display server.  Once the caller has explicitly
        # rendered or shown the window, property changes update it immediately.
        if self._render_started and not self._closed:
            self.render_window.Render()

    # -------------------------------------------------------------- selection

    @property
    def selected(self) -> Any:
        return None if self._selection is None else self._selection.entity

    @property
    def selection(self) -> Any:
        return self.selected

    def select(
        self, entity: Any = None, *, station: float | None = None
    ) -> LayoutViewer:
        """Highlight a scoped entity/frame, or clear selection with ``None``."""

        self._ensure_open()
        if entity is None:
            self._set_selection(None)
            return self
        selection = self._selection_from_value(entity, station=station)
        self._set_selection(selection)
        return self

    def clear_selection(self) -> LayoutViewer:
        return self.select(None)

    def _selection_from_value(
        self, value: Any, *, station: float | None = None
    ) -> _Selection:
        frame_name: str | None = None
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
                visual = self._object_visuals.get(object_name)
                if visual is None or not frame_name:
                    raise KeyError(f"unknown scoped frame {text!r}")
                pose = self.resolver.object_named_frame(visual.entity, frame_name)
                return _Selection(
                    "frame",
                    f"{object_name}.{frame_name}",
                    visual.entity,
                    pose,
                    visual.entity,
                    frame_name,
                )
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
            # Dot is accepted as the display-oriented spelling of object frame.
            if "." in text:
                object_name, possible_frame = text.split(".", 1)
                visual = self._object_visuals.get(object_name)
                if visual is not None and possible_frame:
                    pose = self.resolver.object_named_frame(
                        visual.entity, possible_frame
                    )
                    return _Selection(
                        "frame",
                        text,
                        visual.entity,
                        pose,
                        visual.entity,
                        possible_frame,
                    )
            raise KeyError(f"unknown selectable entity {text!r} in viewer scope")

        for visual in self._curve_visuals.values():
            if value is visual.entity:
                return self._curve_selection(visual, station)
        for visual in self._object_visuals.values():
            if value is visual.entity:
                return _Selection("object", visual.name, visual.entity, visual.pose)

        # A selected Type highlights its scoped instances; a selected stored
        # Frame highlights the first scoped object using its owning type.
        matching_objects = [
            visual
            for visual in self._object_visuals.values()
            if self._object_type(visual.entity) is value
        ]
        if matching_objects:
            visual = matching_objects[0]
            return _Selection("object", visual.name, visual.entity, visual.pose)
        owner = getattr(value, "owner", None)
        if owner is not None:
            for visual in self._object_visuals.values():
                if self._object_type(visual.entity) is owner:
                    name = getattr(value, "name", None)
                    if name is None:
                        break
                    pose = self.resolver.object_named_frame(visual.entity, str(name))
                    return _Selection(
                        "frame",
                        f"{visual.name}.{name}",
                        visual.entity,
                        pose,
                        visual.entity,
                        str(name),
                    )
        raise ValueError("selection is not represented in this viewer scope")

    def _curve_selection(
        self, visual: _CurveVisual, station: float | None
    ) -> _Selection:
        if station is None:
            station = float(visual.stations[0]) if len(visual.stations) else 0.0
        station = float(station)
        pose = self.resolver.curve_frame(visual.entity, station)
        if len(visual.stations):
            index = int(np.argmin(np.abs(visual.stations - station)))
            segment = int(visual.segment_indices[index])
        else:
            segment = None
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
        if selection is not None:
            self._apply_highlight(selection)
            self._set_pose_text(selection)
            self._show_local_axes(selection.pose)
        else:
            self.pose_text.SetInput(self._empty_pose_message())
            if self._hover is None:
                self.hover_axes.SetVisibility(False)
        self._request_render()

    def _reset_highlights(self) -> None:
        for visual in self._curve_visuals.values():
            visual.actor.GetProperty().SetColor(*visual.color)
            visual.actor.GetProperty().SetLineWidth(3.0)
            visual.halo.GetProperty().SetColor(*visual.color)
            visual.halo.GetProperty().SetLineWidth(9.0)
            visual.halo.GetProperty().SetOpacity(0.18)
        if self.batched_objects:
            if self._object_highlight_actor is not None:
                self._object_highlight_actor.SetVisibility(False)
        else:
            for visual in self._object_visuals.values():
                prop = visual.actor.GetProperty()
                prop.SetColor(*visual.color)
                prop.SetOpacity(0.34)
                prop.SetEdgeColor(*_lighten(visual.color, 0.22))
                prop.SetLineWidth(1.15)

    def _apply_highlight(self, selection: _Selection) -> None:
        highlight = _hex_color(_SELECTION_COLOR, _SELECTION_COLOR)
        if selection.kind == "curve":
            visual = self._curve_visuals.get(selection.name)
            if visual is not None:
                visual.actor.GetProperty().SetColor(*_lighten(visual.color, 0.15))
                visual.actor.GetProperty().SetLineWidth(4.6)
                visual.halo.GetProperty().SetColor(*highlight)
                visual.halo.GetProperty().SetLineWidth(13.0)
                visual.halo.GetProperty().SetOpacity(0.38)
            return
        owner = selection.owner if selection.owner is not None else selection.entity
        visual = self._object_visual_by_identity.get(id(owner))
        if visual is None:
            return
        if self.batched_objects:
            self._show_batched_object_highlight(visual, highlight)
            return
        prop = visual.actor.GetProperty()
        prop.SetOpacity(0.62)
        prop.SetEdgeColor(*highlight)
        prop.SetLineWidth(2.8)

    def _show_batched_object_highlight(
        self, visual: _ObjectVisual, color: Sequence[float]
    ) -> None:
        polydata = self._surface_data(visual.vertices, visual.faces)
        if self._object_highlight_actor is None:
            mapper = self._vtk.vtkPolyDataMapper()
            set_offset = getattr(
                mapper, "SetResolveCoincidentTopologyToPolygonOffset", None
            )
            if callable(set_offset):
                set_offset()
            actor = self._vtk.vtkActor()
            actor.SetMapper(mapper)
            actor.PickableOff()
            self.renderer.AddActor(actor)
            self._object_highlight_actor = actor
        actor = self._object_highlight_actor
        actor.GetMapper().SetInputData(polydata)
        prop = actor.GetProperty()
        prop.SetRepresentationToWireframe()
        prop.SetColor(*(float(value) for value in color))
        prop.SetOpacity(1.0)
        prop.SetLineWidth(3.0)
        self._render_lines_as_tubes(actor)
        actor.SetVisibility(self.objects_visible)

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
        text = (
            f"{heading}\n"
            f"X = {origin[0]:.6f} m    Y = {origin[1]:.6f} m    Z = {origin[2]:.6f} m\n"
            f"theta = {angles[0]:.5f} deg    phi = {angles[1]:.5f} deg    "
            f"psi = {angles[2]:.5f} deg"
        )
        self.pose_text.SetInput(text)

    def _show_local_axes(self, pose: Any) -> None:
        self.hover_axes.SetUserMatrix(self._vtk_matrix(_pose_matrix(pose)))
        if self._hover_display_axes is not None:
            self._hover_display_axes.pose = pose
            self._update_display_axes(self._hover_display_axes)
        self.hover_axes.SetVisibility(True)

    # --------------------------------------------------------------- callbacks

    def _on_key_press(self, caller: Any, _event: str) -> None:
        key = str(caller.GetKeySym() or "").lower()
        if key == "c":
            self.set_curves_visible(not self.curves_visible)
        elif key == "o":
            self.set_objects_visible(not self.objects_visible)
        elif key == "b":
            self.set_beam_frames_visible(not self.beam_frames_visible)
        elif key == "f":
            self.fit()
        elif key == "r":
            self.home()
        elif key in {"escape", "esc"}:
            self.clear_selection()

    def _on_left_press(self, caller: Any, _event: str) -> None:
        self._press_position = tuple(int(value) for value in caller.GetEventPosition())

    def _on_left_release(self, caller: Any, _event: str) -> None:
        position = tuple(int(value) for value in caller.GetEventPosition())
        press = self._press_position
        self._press_position = None
        if (
            press is None
            or math.hypot(position[0] - press[0], position[1] - press[1]) > 3.0
        ):
            return
        selection = self._pick_selection(*position)
        if selection is not None and self._same_selection(selection, self._selection):
            selection = None
        self._set_selection(selection)

    def _on_mouse_move(self, caller: Any, _event: str) -> None:
        if self._press_position is not None or self._camera_interacting:
            return
        x, y = (int(value) for value in caller.GetEventPosition())
        position = (x, y)
        now = time.monotonic()
        if position == self._last_hover_position:
            return
        if now - self._last_hover_pick < self._hover_interval:
            return
        self._last_hover_position = position
        self._last_hover_pick = now
        selection = self._pick_selection(x, y)
        if selection is None and self._hover is None:
            return
        if (
            selection is not None
            and selection.kind != "curve"
            and self._same_selection(selection, self._hover)
        ):
            self.tooltip.SetDisplayPosition(x + 14, y + 14)
            self._request_render()
            return
        self._hover = selection
        if selection is None:
            self.tooltip.SetVisibility(False)
            if self._selection is None:
                self.pose_text.SetInput(self._empty_pose_message())
                self.hover_axes.SetVisibility(False)
            else:
                self._set_pose_text(self._selection)
                self._show_local_axes(self._selection.pose)
        else:
            label = selection.name
            if selection.kind == "curve" and selection.station is not None:
                label += f"\nsegment {(selection.segment_index or 0) + 1} · s = {selection.station:.3f}"
            self.tooltip.SetInput(label)
            self.tooltip.SetDisplayPosition(x + 14, y + 14)
            self.tooltip.SetVisibility(True)
            self._set_pose_text(selection)
            self._show_local_axes(selection.pose)
        self._request_render()

    def _on_mouse_leave(self, _caller: Any, _event: str) -> None:
        if self._hover is None:
            self._last_hover_position = None
            return
        self._hover = None
        self._last_hover_position = None
        self.tooltip.SetVisibility(False)
        if self._selection is None:
            self.pose_text.SetInput(self._empty_pose_message())
            self.hover_axes.SetVisibility(False)
        else:
            self._set_pose_text(self._selection)
            self._show_local_axes(self._selection.pose)
        self._request_render()

    def _on_interaction_start(self, _caller: Any, _event: str) -> None:
        self._camera_interacting = True
        get_depth_peeling = getattr(self.renderer, "GetUseDepthPeeling", None)
        set_depth_peeling = getattr(self.renderer, "SetUseDepthPeeling", None)
        if (
            callable(get_depth_peeling)
            and callable(set_depth_peeling)
            and bool(get_depth_peeling())
        ):
            self._restore_depth_peeling = True
            set_depth_peeling(False)

    def _on_interaction_end(self, _caller: Any, _event: str) -> None:
        self._camera_interacting = False
        self._last_hover_position = None
        if self._restore_depth_peeling:
            self._restore_depth_peeling = False
            set_depth_peeling = getattr(self.renderer, "SetUseDepthPeeling", None)
            if callable(set_depth_peeling):
                set_depth_peeling(True)
            self._request_render()

    def _on_exit(self, caller: Any, _event: str) -> None:
        # Installing an ExitEvent observer suppresses VTK's default exit
        # callback.  Explicit termination is therefore required or Start()
        # remains stuck after q/e or a window-manager close.
        self._exit_requested = True
        self._render_started = False
        terminate = getattr(caller, "TerminateApp", None)
        if callable(terminate):
            terminate()

    def _on_window_delete(self, _caller: Any, _event: str) -> None:
        """Ensure a backend-driven window deletion also releases Start()."""

        self._exit_requested = True
        self._render_started = False
        terminate = getattr(self.interactor, "TerminateApp", None)
        if callable(terminate):
            terminate()

    def _pick_selection(self, x: int, y: int) -> _Selection | None:
        if not self.picker.Pick(int(x), int(y), 0.0, self.renderer):
            return None
        prop = self.picker.GetViewProp()
        if prop is None:
            prop = self.picker.GetActor()
        target = self._target_for_prop(prop, int(self.picker.GetCellId()))
        if target is None:
            return None
        if target.kind == "curve":
            visual = self._curve_visuals.get(target.name)
            if visual is None:
                return None
            station, segment = self._nearest_curve_station(
                visual, np.asarray(self.picker.GetPickPosition(), dtype=float)
            )
            pose = self.resolver.curve_frame(visual.entity, station)
            return _Selection(
                "curve",
                visual.name,
                visual.entity,
                pose,
                station=station,
                segment_index=segment,
            )
        kind = "frame" if target.kind == "frame" else target.kind
        return _Selection(
            kind,
            target.name,
            target.entity,
            target.pose,
            target.owner,
            target.frame_name,
        )

    def _target_for_prop(
        self, prop: Any, cell_id: int | None = None
    ) -> _PickTarget | None:
        try:
            batch = self._batched_pick_targets.get(prop)
        except TypeError:
            batch = None
        if batch is not None and cell_id is not None and cell_id >= 0:
            cell_ends, targets = batch
            index = int(np.searchsorted(cell_ends, cell_id, side="right"))
            if index < len(targets):
                return targets[index]
        try:
            result = self._pick_targets.get(prop)
        except TypeError:
            result = None
        if result is not None:
            return result
        for actor, (cell_ends, targets) in self._batched_pick_targets.items():
            if prop is actor or prop == actor:
                if cell_id is None or cell_id < 0:
                    return None
                index = int(np.searchsorted(cell_ends, cell_id, side="right"))
                return targets[index] if index < len(targets) else None
        for actor, target in self._pick_targets.items():
            if prop is actor or prop == actor:
                return target
        return None

    @staticmethod
    def _nearest_curve_station(
        visual: _CurveVisual, point: np.ndarray
    ) -> tuple[float, int]:
        points = visual.points
        if len(points) == 1:
            station = float(visual.stations[0]) if len(visual.stations) else 0.0
            segment = (
                int(visual.segment_indices[0]) if len(visual.segment_indices) else 0
            )
            return station, segment
        starts = points[:-1]
        chords = points[1:] - starts
        offsets = point[None, :] - starts
        denominators = np.einsum("ij,ij->i", chords, chords)
        fractions = np.zeros(len(chords), dtype=float)
        nonzero = denominators > 1.0e-20
        fractions[nonzero] = (
            np.einsum("ij,ij->i", offsets[nonzero], chords[nonzero])
            / denominators[nonzero]
        )
        np.clip(fractions, 0.0, 1.0, out=fractions)
        projected = starts + fractions[:, None] * chords
        residual = point[None, :] - projected
        index = int(np.argmin(np.einsum("ij,ij->i", residual, residual)))
        fraction = float(fractions[index])
        station = float(
            visual.stations[index]
            + fraction * (visual.stations[index + 1] - visual.stations[index])
        )
        segment_index = index if fraction < 1.0 else index + 1
        segment = int(visual.segment_indices[segment_index])
        return station, segment

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
            f"LayoutViewer(curves={self._curve_count}, "
            f"objects={self._object_count}, off_screen={self.off_screen}, "
            f"state={state!r})"
        )


__all__ = ["LayoutViewer"]
