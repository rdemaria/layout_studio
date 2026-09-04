from __future__ import annotations

import os
import sys
import types

import numpy as np
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

    viewer = layout.plot3d(
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


def test_plot3d_is_the_only_public_spelling():
    layout, curve, object_ = populated_layout()

    for entity in (layout, curve, object_):
        assert callable(entity.plot3d)
        assert not hasattr(entity, "plot3D")


def test_curve_plot_is_limited_to_that_curve(viewer_spy):
    layout, curve, _ = populated_layout()

    viewer = curve.plot3d(show=False, beam_frames=True)

    assert viewer is viewer_spy[-1]
    assert viewer.layout is layout
    assert viewer.kwargs["curves"] == [curve]
    assert viewer.kwargs["objects"] == []
    assert viewer.kwargs["beam_frames"] is True
    assert viewer.kwargs["show"] is False


def test_object_plot_is_limited_to_that_object(viewer_spy):
    layout, _, object_ = populated_layout()

    viewer = object_.plot3d(show=False, beam_frames=True)

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
        detached_curve.plot3d(show=False)
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
        full = layout.plot3d(show=False, off_screen=True, beam_frames=True)
        viewers.append(full)
        curve_only = main.plot3d(show=False, off_screen=True)
        viewers.append(curve_only)
        object_only = q2.plot3d(show=False, off_screen=True, beam_frames=True)
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


def test_vtk_reference_axes_follow_camera_scale_without_rendering():
    pytest.importorskip("vtkmodules")

    layout, _curve, object_ = populated_layout()
    object_.type.new_frame("survey").tx(0.1)
    viewer = layout.plot3d(
        show=False,
        off_screen=True,
        frames=True,
        window_size=(800, 600),
    )
    try:
        viewer.select(object_)
        named = next(item for item in viewer._display_axes if not item.active)
        active = viewer._hover_display_axes
        assert active is not None
        assert active.pose is not None

        for display_axes in (named, active):
            length = float(display_axes.actor.GetTotalLength()[0])
            world_per_pixel = viewer._world_units_per_pixel(display_axes.pose.origin)
            assert length / world_per_pixel == pytest.approx(
                viewer._reference_arrow_pixels(active=display_axes.active)
            )

        perspective_length = float(active.actor.GetTotalLength()[0])
        viewer.camera.Dolly(4.0)
        viewer.renderer.InvokeEvent("StartEvent")
        dolly_length = float(active.actor.GetTotalLength()[0])
        assert dolly_length < perspective_length * 0.5
        assert dolly_length / viewer._world_units_per_pixel(
            active.pose.origin
        ) == pytest.approx(viewer._reference_arrow_pixels(active=True))

        viewer.camera.ParallelProjectionOn()
        viewer.camera.SetParallelScale(10.0)
        viewer.renderer.InvokeEvent("StartEvent")
        large_scale_length = float(active.actor.GetTotalLength()[0])
        viewer.camera.SetParallelScale(2.0)
        viewer.renderer.InvokeEvent("StartEvent")
        small_scale_length = float(active.actor.GetTotalLength()[0])
        assert small_scale_length / large_scale_length == pytest.approx(0.2)
    finally:
        viewer.close()

    assert viewer._display_axes == []
    assert viewer._hover_display_axes is None


def test_vtk_fit_entity_preserves_direction_and_home_restores_it():
    pytest.importorskip("vtkmodules")

    layout, _curve, object_ = populated_layout()
    viewer = layout.plot3d(show=False, off_screen=True)
    try:
        viewer.camera.Azimuth(31.0)
        viewer.camera.Elevation(-17.0)
        viewer.camera.Roll(23.0)

        def direction():
            position = np.asarray(viewer.camera.GetPosition())
            focal = np.asarray(viewer.camera.GetFocalPoint())
            value = position - focal
            return value / np.linalg.norm(value)

        before = direction()
        view_up_before = np.asarray(viewer.camera.GetViewUp())
        viewer.fit(object_)
        np.testing.assert_allclose(direction(), before, atol=1e-12, rtol=0.0)
        np.testing.assert_allclose(
            viewer.camera.GetViewUp(), view_up_before, atol=1e-12, rtol=0.0
        )

        viewer.home(object_)
        assert not np.allclose(direction(), before, atol=1e-6, rtol=0.0)
    finally:
        viewer.close()


def test_vtk_camera_interaction_temporarily_disables_depth_peeling():
    from layout_studio.viewer import LayoutViewer

    calls = []

    class Renderer:
        enabled = True

        def GetUseDepthPeeling(self):
            return self.enabled

        def SetUseDepthPeeling(self, enabled):
            self.enabled = bool(enabled)
            calls.append(self.enabled)

    viewer = LayoutViewer.__new__(LayoutViewer)
    viewer.renderer = Renderer()
    viewer._camera_interacting = False
    viewer._restore_depth_peeling = False
    viewer._last_hover_position = (1, 2)
    viewer._request_render = lambda: calls.append("render")

    viewer._on_interaction_start(None, "StartInteractionEvent")
    assert viewer._camera_interacting
    assert calls == [False]

    viewer._on_interaction_end(None, "EndInteractionEvent")
    assert not viewer._camera_interacting
    assert viewer._last_hover_position is None
    assert calls == [False, True, "render"]


def test_vtk_escape_uses_full_selection_cleanup():
    from layout_studio.viewer import LayoutViewer

    calls = []
    viewer = LayoutViewer.__new__(LayoutViewer)
    viewer.clear_selection = lambda: calls.append("clear")
    caller = types.SimpleNamespace(GetKeySym=lambda: "Escape")

    viewer._on_key_press(caller, "KeyPressEvent")

    assert calls == ["clear"]


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
    viewer = layout.plot3d(show=False, off_screen=True, window_size=(160, 120))
    try:
        viewer.render_window.Render()
        assert viewer.render_window.GetSize() == (160, 120)
    finally:
        viewer.close()


def test_native_show_start_return_always_closes_and_detaches_without_display():
    from layout_studio.viewer import LayoutViewer

    calls = []

    class StubWindow:
        def Render(self):
            calls.append("render")

    class StubInteractor:
        def __init__(self, window, failure=None):
            self.window = window
            self.failure = failure

        def Initialize(self):
            calls.append("initialize")

        def Start(self):
            calls.append("start")
            if self.failure is not None:
                raise self.failure

        def SetRenderWindow(self, window):
            self.window = window

    def stub_viewer(*, failure=None, close_failure=None):
        viewer = LayoutViewer.__new__(LayoutViewer)
        viewer.off_screen = False
        viewer._closed = False
        viewer._in_interactor = False
        viewer._interactor_initialised = False
        viewer._render_started = False
        viewer._exit_requested = False
        viewer.render_window = StubWindow()
        viewer.interactor = StubInteractor(viewer.render_window, failure)
        viewer._enable_orientation_widget = lambda: calls.append("orientation")

        def close():
            calls.append("close")
            viewer._closed = True
            viewer.interactor.SetRenderWindow(None)
            if close_failure is not None:
                raise close_failure

        viewer.close = close
        return viewer

    viewer = stub_viewer()
    assert viewer.show() is viewer
    assert calls == ["initialize", "orientation", "render", "start", "close"]
    assert viewer._closed
    assert viewer.interactor.window is None
    assert not viewer._in_interactor

    calls.clear()
    start_failure = RuntimeError("start failed")
    viewer = stub_viewer(
        failure=start_failure, close_failure=ValueError("teardown failed")
    )
    with pytest.raises(RuntimeError) as caught:
        viewer.show()
    assert caught.value is start_failure
    assert calls == ["initialize", "orientation", "render", "start", "close"]
    assert viewer._closed
    assert viewer.interactor.window is None
    assert not viewer._in_interactor

    calls.clear()
    viewer = stub_viewer()
    viewer._in_interactor = True
    with pytest.raises(RuntimeError, match="already running"):
        viewer.show()
    assert calls == []


def test_auto_object_lod_keeps_curves_but_explicit_one_is_honored():
    from layout_studio import Type
    from layout_studio.viewer import LayoutViewer

    straight = Type(
        shape=Box(1.0, 1.0, 10.0),
        color="#abcdef",
        magnetic_center=Frame(),
        magnetic_length=1.0,
    )
    curved = Type(
        shape=Box(1.0, 1.0, 10.0, curvature=0.01),
        color="#abcdef",
        magnetic_center=Frame(),
        magnetic_length=1.0,
    )
    viewer = LayoutViewer.__new__(LayoutViewer)
    viewer.object_resolution = 1
    viewer._requested_object_resolution = None

    assert viewer._object_mesh_resolution(straight) == 1
    assert viewer._object_mesh_resolution(curved) >= 2

    viewer._requested_object_resolution = 1
    assert viewer._object_mesh_resolution(curved) == 1


def test_real_vtk_close_releases_scene_and_model_without_rendering():
    pytest.importorskip("vtkmodules")

    layout, curve, object_ = populated_layout()
    viewer = layout.plot3d(show=False, off_screen=True)
    assert not viewer._render_started
    assert not viewer._interactor_initialised

    viewer.close()
    viewer.close()

    assert viewer._closed
    assert viewer.layout is None
    assert viewer.resolver is None
    assert viewer.curve_scope == ()
    assert viewer.object_scope == ()
    assert viewer._curve_items == []
    assert viewer._object_items == []
    assert viewer.curve_actors == {}
    assert viewer.object_actors == {}
    assert viewer._pick_targets == {}
    assert viewer._batched_pick_targets == {}
    assert viewer.renderer.GetViewProps().GetNumberOfItems() == 0
    assert viewer.interactor.GetRenderWindow() is None
    assert "curves=1" in repr(viewer)
    assert "objects=1" in repr(viewer)
    assert "state='closed'" in repr(viewer)

    # The test itself keeps these alive; the viewer no longer does.
    assert curve.layout is layout
    assert object_.layout is layout


def test_vtk_partial_constructor_failure_runs_best_effort_cleanup(monkeypatch):
    pytest.importorskip("vtkmodules")
    from layout_studio.viewer import LayoutViewer

    layout, _curve, _object = populated_layout()
    captured = {}

    def fail_after_window(self):
        captured["viewer"] = self
        raise RuntimeError("synthetic geometry failure")

    monkeypatch.setattr(LayoutViewer, "_build_entity_geometry", fail_after_window)
    with pytest.raises(RuntimeError, match="synthetic geometry failure"):
        LayoutViewer(layout, show=False, off_screen=True)

    viewer = captured["viewer"]
    assert viewer._closed
    assert viewer._resolver_context is None
    assert viewer.resolver is None
    assert viewer.interactor.GetRenderWindow() is None


def test_vtk_object_batches_obey_a_memory_budget(monkeypatch):
    import layout_studio.viewer as viewer_module
    from layout_studio.viewer import LayoutViewer

    monkeypatch.setattr(viewer_module, "_OBJECT_BATCH_BYTE_BUDGET", 100)
    visual = types.SimpleNamespace(
        vertices=np.empty((1, 3), dtype=np.float64),
        faces=np.empty((1, 3), dtype=np.int64),
    )
    viewer = LayoutViewer.__new__(LayoutViewer)
    viewer.object_batch_size = 4096
    viewer._pending_object_visuals = [visual, visual, visual]
    batches = []
    viewer._add_object_batch = lambda batch: batches.append(len(batch))

    viewer._finish_object_batches()

    assert batches == [1, 1, 1]
    assert viewer._pending_object_visuals == []
