from __future__ import annotations

import os
import sys
import types

import pytest
from layout_studio import AttachmentError, Box, Frame, Layout, Position, Segment


def populated_layout():
    layout = Layout()
    curve = layout.new_curve(
        "main",
        starting_frame=Frame("world"),
        color="#112233",
        segments=[Segment(5.0)],
    )
    type_ = layout.new_type(
        "kind",
        shape=Box(1.0, 1.0, 1.0),
        color="#445566",
        magnetic_center=Frame(),
        magnetic_length=0.8,
    )
    object_ = layout.new_object(
        "Q1",
        type=type_,
        position=Position(curve).ts(2.0),
    )
    return layout, curve, object_


@pytest.fixture
def viewer_spy(monkeypatch):
    calls = []

    class ViewerSpy:
        def __init__(self, layout, **kwargs):
            self.layout = layout
            self.kwargs = kwargs
            calls.append(self)

    fake_module = types.ModuleType("layout_studio.viewer")
    fake_module.LayoutViewer = ViewerSpy
    monkeypatch.setitem(sys.modules, "layout_studio.viewer", fake_module)
    return calls


def test_layout_plot3d_forwards_viewer_controls(viewer_spy):
    layout, _curve, object_ = populated_layout()

    viewer = layout.plot3D(
        curves=False,
        objects=True,
        beam_frames=True,
        selection=object_,
        show=False,
        off_screen=True,
        window_size=(640, 480),
        background="#010203",
    )

    assert viewer is viewer_spy[-1]
    assert viewer.layout is layout
    assert viewer.kwargs == {
        "curves": False,
        "objects": True,
        "beam_frames": True,
        "selection": object_,
        "show": False,
        "off_screen": True,
        "window_size": (640, 480),
        "background": "#010203",
    }

    alias = layout.plot3d(show=False)
    assert alias is viewer_spy[-1]
    assert alias.layout is layout
    assert alias.kwargs["show"] is False


@pytest.mark.parametrize("spelling", ["plot3d", "plot3D"])
def test_curve_plot_is_limited_to_that_curve(viewer_spy, spelling):
    layout, curve, _ = populated_layout()

    viewer = getattr(curve, spelling)(show=False, beam_frames=True)

    assert viewer is viewer_spy[-1]
    assert viewer.layout is layout
    assert viewer.kwargs["curves"] == [curve]
    assert viewer.kwargs["objects"] == []
    assert viewer.kwargs["beam_frames"] is True
    assert viewer.kwargs["show"] is False


@pytest.mark.parametrize("spelling", ["plot3d", "plot3D"])
def test_object_plot_is_limited_to_that_object(viewer_spy, spelling):
    layout, _, object_ = populated_layout()

    viewer = getattr(object_, spelling)(show=False, beam_frames=True)

    assert viewer is viewer_spy[-1]
    assert viewer.layout is layout
    assert viewer.kwargs["curves"] == []
    assert viewer.kwargs["objects"] == [object_]
    assert viewer.kwargs["beam_frames"] is True
    assert viewer.kwargs["show"] is False


def test_detached_curve_or_object_cannot_be_plotted(viewer_spy):
    layout, curve, object_ = populated_layout()
    detached_curve = curve.clone()
    detached_object = object_.clone()

    assert layout is curve.layout is object_.layout
    with pytest.raises(AttachmentError):
        detached_curve.plot3D(show=False)
    with pytest.raises(AttachmentError):
        detached_object.plot3d(show=False)


def test_real_vtk_builds_full_and_entity_scoped_topologies_without_rendering():
    pytest.importorskip("vtkmodules")

    layout, main, q1 = populated_layout()
    auxiliary = layout.new_curve(
        "auxiliary",
        starting_frame=Frame("world").tx(2.0),
        color="#778899",
        segments=[Segment(3.0)],
    )
    type_ = q1.type
    type_.new_frame("survey").tx(0.1)
    q2 = layout.new_object(
        "Q2",
        type=type_,
        position=Position(auxiliary).ts(1.0),
    )

    viewers = []
    try:
        full = layout.plot3D(show=False, off_screen=True, beam_frames=True)
        viewers.append(full)
        curve_only = main.plot3d(show=False, off_screen=True)
        viewers.append(curve_only)
        object_only = q2.plot3D(show=False, off_screen=True, beam_frames=True)
        viewers.append(object_only)

        assert set(full.curve_actors) == {"main", "auxiliary"}
        assert set(full.object_actors) == {"Q1", "Q2"}
        assert full.curve_scope == (main, auxiliary)
        assert full.object_scope == (q1, q2)
        assert full._beam_frame_actors
        assert full._named_frame_actors

        assert set(curve_only.curve_actors) == {"main"}
        assert curve_only.object_actors == {}
        assert curve_only.curve_scope == (main,)
        assert curve_only.object_scope == ()
        assert curve_only._beam_frame_actors == []

        assert object_only.curve_actors == {}
        assert set(object_only.object_actors) == {"Q2"}
        assert object_only.curve_scope == ()
        assert object_only.object_scope == (q2,)
        assert object_only._beam_frame_actors
        assert object_only._named_frame_actors

        # Construction and topology checks must never accidentally contact the
        # display server. Rendering is isolated to the guarded smoke below.
        assert all(not viewer._render_started for viewer in viewers)
    finally:
        for viewer in reversed(viewers):
            viewer.close()


def test_real_vtk_off_screen_smoke_only_with_a_known_render_backend():
    pytest.importorskip("vtkmodules")

    backend = os.environ.get("VTK_DEFAULT_OPENGL_WINDOW", "")
    safe_headless_backend = backend in {"vtkEGLRenderWindow", "vtkOSOpenGLRenderWindow"}
    if not os.environ.get("DISPLAY") and not safe_headless_backend:
        pytest.skip(
            "real VTK Render() needs DISPLAY or an explicitly selected EGL/OSMesa backend"
        )

    # This is deliberately the only test that calls Render().  All dispatch
    # and scoping tests above replace the viewer at the lazy import boundary.
    layout, _, _ = populated_layout()
    viewer = layout.plot3D(show=False, off_screen=True, window_size=(160, 120))
    try:
        viewer.render_window.Render()
        assert viewer.render_window.GetSize() == (160, 120)
    finally:
        viewer.close()
