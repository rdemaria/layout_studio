from __future__ import annotations

import sys
import types
from pathlib import Path

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
        magnetic_curvature=0.0,
        magnetic_roll=0.0,
        beam_center=Frame(),
        beam_length=0.6,
        beam_curvature=0.0,
        beam_roll=0.0,
    )
    object_ = layout.new_object(
        "Q1",
        type=type_,
        position=Position(curve).ts(2.0),
    )
    return layout, curve, object_


@pytest.fixture
def web_viewer_spy(monkeypatch):
    calls = []

    class WebViewerSpy:
        def __init__(self, layout, **kwargs):
            self.layout = layout
            self.kwargs = kwargs
            calls.append(self)

    fake_module = types.ModuleType("layout_studio.webviewer")
    fake_module.WebViewer = WebViewerSpy
    monkeypatch.setitem(sys.modules, "layout_studio.webviewer", fake_module)
    return calls


def test_layout_plot_web_forwards_browser_controls(web_viewer_spy):
    layout, _curve, object_ = populated_layout()

    viewer = layout.plot_web(
        selection=object_,
        fit=object_,
        show=False,
        width=840,
        height=560,
        mode="select",
        viewer_url="https://viewer.example/app",
    )

    assert viewer is web_viewer_spy[-1]
    assert viewer.layout is layout
    assert viewer.kwargs == {
        "scope": layout,
        "selection": object_,
        "fit": object_,
        "visibility": {
            "curves": True,
            "objects": True,
            "magnetic_axis": False,
            "beam_axis": False,
            "frames": False,
        },
        "show": False,
        "width": 840,
        "height": 560,
        "mode": "select",
        "viewer_url": "https://viewer.example/app",
    }


def test_curve_and_object_plot_web_use_strict_scope(web_viewer_spy):
    layout, curve, object_ = populated_layout()

    curve_viewer = curve.plot_web(show=False)
    object_viewer = object_.plot_web(show=False, frames=False)

    assert curve_viewer.layout is layout
    assert curve_viewer.kwargs["scope"] is curve
    assert curve_viewer.kwargs["show"] is False
    assert curve_viewer.kwargs["visibility"] is None
    assert object_viewer.layout is layout
    assert object_viewer.kwargs["scope"] is object_
    assert object_viewer.kwargs["visibility"] == {
        "curves": False,
        "objects": True,
        "magnetic_axis": False,
        "beam_axis": False,
        "frames": False,
    }


def test_plot_web_forwards_each_optional_type_layer_independently(web_viewer_spy):
    layout, _curve, object_ = populated_layout()

    layout.plot_web(
        show=False,
        magnetic_axis=True,
        beam_axis=False,
        frames=True,
    )
    assert web_viewer_spy[-1].kwargs["visibility"] == {
        "curves": True,
        "objects": True,
        "magnetic_axis": True,
        "beam_axis": False,
        "frames": True,
    }

    object_.plot_web(show=False, magnetic_axis=False, beam_axis=True, frames=False)
    assert web_viewer_spy[-1].kwargs["visibility"] == {
        "curves": False,
        "objects": True,
        "magnetic_axis": False,
        "beam_axis": True,
        "frames": False,
    }


def test_detached_entities_cannot_be_plotted_on_the_web(web_viewer_spy):
    _layout, curve, object_ = populated_layout()

    with pytest.raises(AttachmentError):
        curve.clone().plot_web(show=False)
    with pytest.raises(AttachmentError):
        object_.clone().plot_web(show=False)


def test_plot_web_has_no_camel_case_alias():
    layout, curve, object_ = populated_layout()

    for entity in (layout, curve, object_):
        assert callable(entity.plot_web)
        assert not hasattr(entity, "plotWeb")


def test_native_plotting_methods_are_not_part_of_the_python_api():
    layout, curve, object_ = populated_layout()

    for entity in (layout, curve, object_):
        assert not hasattr(entity, "plot2d")
        assert not hasattr(entity, "plot3d")


def test_object_plot_web_constructs_a_real_scoped_viewer(tmp_path: Path):
    _layout, _curve, object_ = populated_layout()
    asset = tmp_path / "index.html"
    asset.write_text(
        "<!doctype html><script>/* layout-studio-python protocol 1 */</script>",
        encoding="utf-8",
    )

    viewer = object_.plot_web(
        show=False,
        frames=False,
        standalone_path=asset,
        poll_timeout=0.01,
    )
    try:
        assert len(viewer._state.commands) == 1
        command = viewer._state.commands[0]
        assert command["command"] == "load_layout"
        assert command["scope"] == {"kind": "object", "name": "Q1"}
        assert command["visibility"] == {
            "curves": False,
            "objects": True,
            "magnetic_axis": False,
            "beam_axis": False,
            "frames": False,
        }
    finally:
        server_thread = viewer._thread
        viewer.close()

    assert not server_thread.is_alive()
