from __future__ import annotations

import math
import os
import sys
import types
from collections.abc import Iterable
from pathlib import Path

import numpy as np
import pytest

from layout_studio import (
    AttachmentError,
    Box,
    Cylinder,
    Frame,
    Layout,
    Position,
    Segment,
)

# Select a non-interactive backend before the lazily imported 2D viewer imports
# pyplot.  This keeps the complete test module safe on CI workers without a
# display server.
os.environ.setdefault("MPLBACKEND", "Agg")


def populated_layout():
    layout = Layout()
    curve = layout.new_curve(
        "main",
        starting_frame=Frame("world").tx(1.0).ty(2.0),
        color="#112233",
        segments=[
            Segment(2.0),
            Segment(math.pi / 2.0, math.pi / 2.0),
            Segment(math.pi / 2.0, math.pi / 2.0, math.pi / 2.0),
        ],
    )
    type_ = layout.new_type(
        "kind",
        shape=Box(1.0, 0.8, 1.6, curvature=0.3, roll=0.2),
        color="#445566",
        magnetic_center=Frame().tx(0.05),
        magnetic_length=1.2,
    )
    type_.new_frame("survey").tx(0.2).ty(0.1).ts(0.3)
    object_ = layout.new_object(
        "Q1",
        type=type_,
        position=Position(curve).ts(1.0),
    )
    return layout, curve, object_


def extended_layout():
    layout, main, q1 = populated_layout()
    auxiliary = layout.new_curve(
        "auxiliary",
        starting_frame=Frame("world").tx(-2.0).ty(-1.0),
        color="#778899",
        segments=[Segment(3.0)],
    )
    cylinder = layout.new_type(
        "cylinder",
        shape=Cylinder(0.4, 1.4, curvature=-0.2, roll=0.3),
        color="#a06030",
        magnetic_center=Frame(),
        magnetic_length=0.9,
    )
    cylinder.new_frame("fiducial").ty(0.15)
    q2 = layout.new_object(
        "Q2",
        type=cylinder,
        position=Position(auxiliary).ts(1.5),
    )
    return layout, main, auxiliary, q1, q2


@pytest.fixture
def viewer2d_spy(monkeypatch):
    calls = []

    class Viewer2DSpy:
        def __init__(self, layout, **kwargs):
            self.layout = layout
            self.kwargs = kwargs
            calls.append(self)

    fake_module = types.ModuleType("layout_studio.viewer2d")
    fake_module.LayoutViewer2D = Viewer2DSpy
    monkeypatch.setitem(sys.modules, "layout_studio.viewer2d", fake_module)
    return calls


def test_layout_plot2d_forwards_projection_and_viewer_controls(viewer2d_spy):
    layout, _curve, object_ = populated_layout()

    viewer = layout.plot2d(
        "xz",
        curves=False,
        objects=True,
        beam_frames=True,
        selection=object_,
        show=False,
        figsize=(6.0, 4.0),
        dpi=90,
        background="#010203",
        frames=False,
    )

    assert viewer is viewer2d_spy[-1]
    assert viewer.layout is layout
    assert viewer.kwargs == {
        "projection": "xz",
        "curves": False,
        "objects": True,
        "beam_frames": True,
        "selection": object_,
        "show": False,
        "figsize": (6.0, 4.0),
        "dpi": 90,
        "background": "#010203",
        "frames": False,
    }


def test_curve_plot2d_is_limited_to_that_curve(viewer2d_spy):
    layout, curve, _object = populated_layout()

    viewer = curve.plot2d(
        "zy",
        selection=curve,
        show=False,
        figsize=(5.0, 3.0),
        background="white",
    )

    assert viewer is viewer2d_spy[-1]
    assert viewer.layout is layout
    assert viewer.kwargs == {
        "projection": "zy",
        "curves": [curve],
        "objects": [],
        "selection": curve,
        "show": False,
        "figsize": (5.0, 3.0),
        "background": "white",
    }


def test_object_plot2d_is_limited_to_that_object(viewer2d_spy):
    layout, _curve, object_ = populated_layout()

    viewer = object_.plot2d(
        "yz",
        beam_frames=False,
        frames=False,
        selection=object_,
        show=False,
        figsize=(4.0, 4.0),
        dpi=120,
    )

    assert viewer is viewer2d_spy[-1]
    assert viewer.layout is layout
    assert viewer.kwargs == {
        "projection": "yz",
        "curves": [],
        "objects": [object_],
        "beam_frames": False,
        "frames": False,
        "selection": object_,
        "show": False,
        "figsize": (4.0, 4.0),
        "dpi": 120,
    }


def test_detached_curve_or_object_cannot_be_plotted(viewer2d_spy):
    layout, curve, object_ = populated_layout()
    detached_curve = curve.clone()
    detached_object = object_.clone()

    assert layout is curve.layout is object_.layout
    with pytest.raises(AttachmentError):
        detached_curve.plot2d(show=False)
    with pytest.raises(AttachmentError):
        detached_object.plot2d(show=False)
    assert viewer2d_spy == []


@pytest.mark.parametrize(
    ("requested", "projection"),
    [
        ("xy", "xy"),
        ("XZ", "xz"),
        ("yx", "yx"),
        ("Yz", "yz"),
        ("zx", "zx"),
        ("ZY", "zy"),
    ],
)
def test_ordered_projection_selects_and_orients_the_requested_coordinates(
    requested, projection
):
    pytest.importorskip("matplotlib")
    layout, main, _object = populated_layout()
    viewer = layout.plot2d(requested, objects=False, show=False)
    try:
        visual = viewer._curve_visuals["main"]
        indices = {"x": 0, "y": 1, "z": 2}

        np.testing.assert_allclose(
            visual.projected,
            visual.points[:, [indices[projection[0]], indices[projection[1]]]],
        )
        assert viewer.projection == projection
        assert viewer.ax.get_xlabel().lower().startswith(projection[0])
        assert viewer.ax.get_ylabel().lower().startswith(projection[1])
        assert viewer.ax.get_aspect() == "auto"
        assert viewer.curve_scope == (main,)
    finally:
        viewer.close()


def test_reference_axes_remain_viewport_relative_across_zoom_and_axis_scaling():
    pytest.importorskip("matplotlib")
    layout, _curve, object_ = populated_layout()
    viewer = layout.plot2d("xz", show=False, frames=True)

    def displayed_lengths(display_axes):
        segments = np.asarray(display_axes.artist.get_segments(), dtype=float)
        if not segments.size:
            return np.empty(0, dtype=float)
        pixels = viewer.ax.transData.transform(segments.reshape(-1, 2)).reshape(
            segments.shape
        )
        lengths = np.linalg.norm(pixels[:, 1] - pixels[:, 0], axis=1)
        return lengths[lengths > 1e-7]

    try:
        viewer.draw()
        viewer.select(object_)
        named = next(item for item in viewer._display_axes if not item.active)
        active = viewer._local_display_axes

        for display_axes in (named, active):
            np.testing.assert_allclose(
                displayed_lengths(display_axes),
                viewer._reference_arrow_pixels(active=display_axes.active),
            )

        named_data_length = np.linalg.norm(
            np.asarray(named.artist.get_segments()[0])[1]
            - np.asarray(named.artist.get_segments()[0])[0]
        )
        viewer.ax.set_xlim(-10_000.0, 10_000.0)
        viewer.ax.set_ylim(-0.01, 0.01)

        for display_axes in (named, active):
            np.testing.assert_allclose(
                displayed_lengths(display_axes),
                viewer._reference_arrow_pixels(active=display_axes.active),
            )
        zoomed_data_length = np.linalg.norm(
            np.asarray(named.artist.get_segments()[0])[1]
            - np.asarray(named.artist.get_segments()[0])[0]
        )
        assert zoomed_data_length != pytest.approx(named_data_length)
    finally:
        viewer.close()

    assert viewer._display_axes == []
    assert viewer._axes_callbacks == []


@pytest.mark.parametrize(
    "projection",
    ["", "x", "xx", "xX", "xyz", "ab", "xq", None, 12],
)
def test_projection_validation_rejects_unknown_or_malformed_values(projection):
    pytest.importorskip("matplotlib")
    layout, _curve, _object = populated_layout()

    with pytest.raises((TypeError, ValueError), match="projection"):
        layout.plot2d(projection, show=False)


def test_curve_along_omitted_axis_has_finite_degenerate_projection_and_limits():
    pytest.importorskip("matplotlib")
    layout = Layout()
    curve = layout.new_curve(
        "longitudinal",
        starting_frame=Frame("world").tx(1.5).ty(-0.5),
        color="#334455",
        segments=[Segment(4.0)],
    )
    viewer = curve.plot2d("xy", show=False)
    try:
        visual = viewer._curve_visuals["longitudinal"]

        assert np.isfinite(visual.projected).all()
        np.testing.assert_allclose(
            visual.projected,
            np.broadcast_to([1.5, -0.5], visual.projected.shape),
        )
        assert np.isfinite(viewer.ax.get_xlim()).all()
        assert np.isfinite(viewer.ax.get_ylim()).all()
        assert viewer.ax.get_xlim()[0] < viewer.ax.get_xlim()[1]
        assert viewer.ax.get_ylim()[0] < viewer.ax.get_ylim()[1]
    finally:
        viewer.close()


def test_real_matplotlib_builds_full_and_entity_scoped_topologies_without_showing(
    monkeypatch,
):
    pytest.importorskip("matplotlib")
    import matplotlib.pyplot as plt
    from matplotlib.axes import Axes
    from matplotlib.figure import Figure

    show_calls = []
    monkeypatch.setattr(
        plt, "show", lambda *args, **kwargs: show_calls.append((args, kwargs))
    )

    layout, main, auxiliary, q1, q2 = extended_layout()
    viewers = []
    try:
        full = layout.plot2d(
            "xz",
            show=False,
            beam_frames=True,
            figsize=(6.0, 4.0),
            dpi=90,
        )
        viewers.append(full)
        curve_only = main.plot2d("zx", show=False)
        viewers.append(curve_only)
        object_only = q2.plot2d("yz", show=False, beam_frames=True, frames=True)
        viewers.append(object_only)

        assert isinstance(full.figure, Figure)
        assert isinstance(full.ax, Axes)
        assert full.axes is full.ax
        assert full.canvas is full.figure.canvas
        np.testing.assert_allclose(full.figure.get_size_inches(), [6.0, 4.0])
        assert full.figure.dpi == pytest.approx(90.0)

        assert set(full.curve_artists) == {"main", "auxiliary"}
        assert set(full.object_artists) == {"Q1", "Q2"}
        assert full.curve_scope == (main, auxiliary)
        assert full.object_scope == (q1, q2)
        assert full._beam_frame_artists
        assert full._named_frame_artists
        for visual in full._object_visuals.values():
            assert len(visual.vertices) > 8
            np.testing.assert_allclose(
                visual.projected,
                visual.vertices[:, [0, 2]],
            )

        assert set(curve_only.curve_artists) == {"main"}
        assert curve_only.object_artists == {}
        assert curve_only.curve_scope == (main,)
        assert curve_only.object_scope == ()
        assert curve_only._beam_frame_artists == []
        assert curve_only._named_frame_artists == []

        assert object_only.curve_artists == {}
        assert set(object_only.object_artists) == {"Q2"}
        assert object_only.curve_scope == ()
        assert object_only.object_scope == (q2,)
        assert object_only._beam_frame_artists
        assert object_only._named_frame_artists

        # show=False is significant for notebooks, scripts, and headless CI.
        assert show_calls == []
    finally:
        for viewer in reversed(viewers):
            viewer.close()


def _flatten_artists(value) -> Iterable[object]:
    from matplotlib.artist import Artist

    if isinstance(value, Artist):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _flatten_artists(child)
    elif isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
        for child in value:
            yield from _flatten_artists(child)


def _assert_visible(value, expected: bool) -> None:
    artists = list(_flatten_artists(value))
    assert artists
    assert all(artist.get_visible() is expected for artist in artists)


def test_visibility_controls_update_every_layer_and_preserve_logical_selection():
    pytest.importorskip("matplotlib")
    layout, main, _auxiliary, q1, _q2 = extended_layout()
    viewer = layout.plot2d("xz", show=False, beam_frames=True, frames=True)
    try:
        assert viewer.select(main, station=1.0) is viewer
        assert viewer.selected is main
        assert viewer.selection is main

        assert viewer.set_curves_visible(False) is viewer
        assert viewer.curves_visible is False
        _assert_visible(viewer.curve_artists, False)
        assert viewer.selected is main

        viewer.set_curves_visible(True)
        _assert_visible(viewer.curve_artists, True)

        viewer.select(q1)
        assert viewer.set_objects_visible(False) is viewer
        assert viewer.objects_visible is False
        _assert_visible(viewer.object_artists, False)
        _assert_visible(viewer._beam_frame_artists, False)
        _assert_visible(viewer._named_frame_artists, False)
        assert viewer.selected is q1

        viewer.set_objects_visible(True)
        _assert_visible(viewer.object_artists, True)
        _assert_visible(viewer._beam_frame_artists, True)
        _assert_visible(viewer._named_frame_artists, True)

        assert viewer.set_beam_frames_visible(False) is viewer
        _assert_visible(viewer._beam_frame_artists, False)
        viewer.set_beam_frames_visible(True)
        _assert_visible(viewer._beam_frame_artists, True)

        assert viewer.set_frames_visible(False) is viewer
        _assert_visible(viewer._named_frame_artists, False)
        viewer.set_frames_visible(True)
        _assert_visible(viewer._named_frame_artists, True)

        assert viewer.clear_selection() is viewer
        assert viewer.selected is None
        assert viewer.selection is None
        assert viewer._selection is None
    finally:
        viewer.close()


def test_keyboard_shortcuts_toggle_layers_and_escape_clears_selection():
    pytest.importorskip("matplotlib")
    layout, main, _auxiliary, _q1, _q2 = extended_layout()
    viewer = layout.plot2d("xz", show=False, beam_frames=True)
    try:
        viewer.select(main, station=0.5)

        viewer._on_key_press(types.SimpleNamespace(key="c"))
        viewer._on_key_press(types.SimpleNamespace(key="o"))
        viewer._on_key_press(types.SimpleNamespace(key="b"))
        viewer._on_key_press(types.SimpleNamespace(key="g"))
        assert viewer.curves_visible is False
        assert viewer.objects_visible is False
        assert viewer.beam_frames_visible is False
        assert viewer.grid_visible is False

        viewer._on_key_press(types.SimpleNamespace(key="escape"))
        assert viewer.selected is None
    finally:
        viewer.close()


def test_programmatic_curve_object_and_named_frame_selection():
    pytest.importorskip("matplotlib")
    layout, main, _auxiliary, q1, _q2 = extended_layout()
    viewer = layout.plot2d("xz", show=False, beam_frames=True, frames=True)
    try:
        viewer.select("curve:main", station=2.25)
        assert viewer.selected is main
        assert viewer._selection.kind == "curve"
        assert viewer._selection.station == pytest.approx(2.25)
        np.testing.assert_allclose(
            viewer._selection.pose.origin,
            main.get_frame(2.25).origin,
        )

        viewer.select("object:Q1")
        assert viewer.selected is q1
        assert viewer._selection.kind == "object"

        viewer.select("Q1->survey")
        assert viewer.selected is q1
        assert viewer._selection.kind == "frame"
        assert viewer._selection.frame_name == "survey"

        viewer.select("Q1.survey")
        assert viewer._selection.kind == "frame"
        assert viewer._selection.frame_name == "survey"
    finally:
        viewer.close()


def test_synthetic_hover_and_click_interpolate_station_along_sampled_curve_chord():
    pytest.importorskip("matplotlib")
    from matplotlib.backend_bases import MouseButton, MouseEvent

    _layout, main, _object = populated_layout()
    viewer = main.plot2d("xz", show=False)
    try:
        viewer.draw()
        visual = viewer._curve_visuals["main"]
        differences = np.diff(visual.projected, axis=0)
        candidates = np.flatnonzero(
            (np.linalg.norm(differences, axis=1) > 1e-12)
            & (visual.segment_indices[:-1] == visual.segment_indices[1:])
        )
        assert len(candidates) > 5
        index = int(candidates[5])
        projected_midpoint = (
            visual.projected[index] + visual.projected[index + 1]
        ) / 2.0
        display_x, display_y = viewer.ax.transData.transform(projected_midpoint)

        hover = MouseEvent(
            "motion_notify_event",
            viewer.canvas,
            display_x,
            display_y,
        )
        screen_points = np.asarray(
            viewer.ax.transData.transform(visual.projected), dtype=float
        )
        event_point = np.asarray([hover.x, hover.y], dtype=float)
        chord = screen_points[index + 1] - screen_points[index]
        fraction = float(
            np.dot(event_point - screen_points[index], chord) / np.dot(chord, chord)
        )
        expected_station = float(
            visual.stations[index]
            + fraction * (visual.stations[index + 1] - visual.stations[index])
        )
        assert visual.stations[index] < expected_station < visual.stations[index + 1]

        viewer._on_motion(hover)
        assert viewer._hover is not None
        assert viewer._hover.entity is main
        assert viewer._hover.station == pytest.approx(expected_station)

        click = MouseEvent(
            "button_press_event",
            viewer.canvas,
            display_x,
            display_y,
            button=MouseButton.LEFT,
        )
        viewer._on_click(click)
        assert viewer.selected is main
        assert viewer._selection.station == pytest.approx(expected_station)
        assert viewer._selection.segment_index == int(visual.segment_indices[index])

        # Matching the VTK viewer, clicking the same curve segment toggles it.
        viewer._on_click(click)
        assert viewer.selected is None

        viewer.set_curves_visible(False)
        viewer._on_click(click)
        viewer._on_motion(hover)
        assert viewer.selected is None
        assert viewer._hover is None

        leave = MouseEvent("figure_leave_event", viewer.canvas, -1, -1)
        viewer._on_leave(leave)
        assert viewer._hover is None
    finally:
        viewer.close()


def test_hover_blits_overlays_and_full_scene_changes_invalidate_background(
    monkeypatch,
):
    pytest.importorskip("matplotlib")
    layout, _curve, object_ = populated_layout()
    viewer = layout.plot2d("xz", show=False, hover_interval=0)
    try:
        assert viewer.canvas.supports_blit is True
        assert viewer._blit_enabled is True
        assert all(artist.get_animated() for artist in viewer._hover_overlay_artists)

        viewer.draw()
        assert viewer._blit_background is not None

        selection = viewer._selection_from_value(object_)
        monkeypatch.setattr(viewer, "_pick_selection", lambda _event: selection)
        blits = []
        idle_draws = []
        monkeypatch.setattr(viewer.canvas, "blit", lambda bbox=None: blits.append(bbox))
        monkeypatch.setattr(viewer.canvas, "draw_idle", lambda: idle_draws.append(True))
        event = types.SimpleNamespace(
            inaxes=viewer.ax,
            button=None,
            x=120.0,
            y=160.0,
            xdata=0.2,
            ydata=0.3,
        )

        viewer._on_motion(event)

        assert viewer._hover is selection
        assert blits
        assert idle_draws == []

        # Layer/grid/camera changes affect the cached static scene and therefore
        # use a normal draw rather than restoring the now-stale background.
        viewer.set_grid_visible(False)
        assert viewer._blit_background is None
        assert idle_draws == [True]
    finally:
        viewer.close()


def test_runtime_blit_failure_falls_back_to_regular_draw(monkeypatch):
    pytest.importorskip("matplotlib")
    _layout, curve, _object = populated_layout()
    viewer = curve.plot2d("xz", show=False)
    try:
        viewer.draw()
        assert viewer._blit_enabled is True
        idle_draws = []
        monkeypatch.setattr(
            viewer.canvas,
            "restore_region",
            lambda _background: (_ for _ in ()).throw(NotImplementedError()),
        )
        monkeypatch.setattr(viewer.canvas, "draw_idle", lambda: idle_draws.append(True))

        viewer._request_overlay_draw()

        assert viewer._blit_enabled is False
        assert viewer._blit_background is None
        assert not any(
            artist.get_animated() for artist in viewer._hover_overlay_artists
        )
        assert idle_draws == [True]
    finally:
        viewer.close()


def test_savefig_writes_a_nonempty_png(tmp_path: Path):
    pytest.importorskip("matplotlib")
    layout, _curve, _object = populated_layout()
    viewer = layout.plot2d("xy", show=False, beam_frames=True)
    output = tmp_path / "layout-xy.png"
    try:
        result = viewer.savefig(output, dpi=110)

        assert result == output
        assert output.is_file()
        assert output.stat().st_size > 1_000
        assert output.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    finally:
        viewer.close()


def test_savefig_temporarily_includes_animated_hover_overlays(tmp_path, monkeypatch):
    pytest.importorskip("matplotlib")
    _layout, curve, _object = populated_layout()
    viewer = curve.plot2d("xz", show=False)
    output = tmp_path / "overlays.png"
    animated_during_save = []
    original_savefig = viewer.figure.savefig

    def savefig_spy(*args, **kwargs):
        animated_during_save.extend(
            artist.get_animated() for artist in viewer._hover_overlay_artists
        )
        return original_savefig(*args, **kwargs)

    monkeypatch.setattr(viewer.figure, "savefig", savefig_spy)
    try:
        assert all(artist.get_animated() for artist in viewer._hover_overlay_artists)

        viewer.savefig(output)

        assert animated_during_save
        assert not any(animated_during_save)
        assert all(artist.get_animated() for artist in viewer._hover_overlay_artists)
        assert output.is_file()
    finally:
        viewer.close()


def test_existing_axes_and_in_memory_screenshot_are_supported():
    pytest.importorskip("matplotlib")
    import matplotlib.pyplot as plt

    _layout, curve, _object = populated_layout()
    figure, axes = plt.subplots(figsize=(5.0, 3.0), dpi=80)
    viewer = curve.plot2d("xz", show=False, ax=axes)
    try:
        assert viewer.figure is figure
        assert viewer.ax is axes
        image = viewer.screenshot()
        assert image.ndim == 3
        assert image.shape[2] == 4
        assert image.dtype == np.uint8
    finally:
        viewer.close()

    # A viewer only owns figures it creates itself.
    assert plt.fignum_exists(figure.number)
    plt.close(figure)


def test_closing_shared_axes_restores_overlay_drawing():
    pytest.importorskip("matplotlib")
    import matplotlib.pyplot as plt

    _layout, curve, _object = populated_layout()
    figure, axes = plt.subplots()
    viewer = curve.plot2d("xz", show=False, ax=axes)
    overlays = viewer._hover_overlay_artists

    assert overlays
    assert all(artist.get_animated() for artist in overlays)
    viewer.close()

    assert all(not artist.get_animated() for artist in overlays)
    assert viewer._closed
    plt.close(figure)


def test_layoutviewer2d_is_public_and_matplotlib_is_lazily_available():
    pytest.importorskip("matplotlib")
    import layout_studio
    from layout_studio.viewer2d import LayoutViewer2D

    assert layout_studio.LayoutViewer2D is LayoutViewer2D


def test_close_disconnects_callbacks_and_is_idempotent():
    pytest.importorskip("matplotlib")
    _layout, curve, _object = populated_layout()
    viewer = curve.plot2d(show=False)

    assert viewer._callbacks
    viewer.close()
    assert viewer._closed is True
    assert viewer._callbacks == []
    viewer.close()
    with pytest.raises(RuntimeError, match="closed"):
        viewer.draw()
