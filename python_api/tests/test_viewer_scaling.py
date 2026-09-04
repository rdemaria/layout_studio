from __future__ import annotations

import math
import os
from types import SimpleNamespace

import numpy as np
import pytest

from layout_studio import Box, Frame, Layout, Position, Segment
from layout_studio.viewer import LayoutViewer

# Select a non-interactive backend before LayoutViewer2D lazily imports pyplot.
os.environ.setdefault("MPLBACKEND", "Agg")

_LARGE_OBJECT_COUNT = 128


def test_vtk_batch_triangulation_accepts_ragged_polygons():
    vertices = np.zeros((4, 3), dtype=float)
    triangles = LayoutViewer._triangulated_faces(
        vertices,
        [[0, 1, 2], [0, 1, 2, 3]],
    )

    np.testing.assert_array_equal(
        triangles,
        [[0, 1, 2], [0, 1, 2], [0, 2, 3]],
    )


@pytest.fixture(scope="module")
def large_straight_layout():
    layout = Layout()
    curve = layout.new_curve(
        "main",
        starting_frame=Frame("world"),
        color="#406080",
        segments=[Segment(float(_LARGE_OBJECT_COUNT + 1))],
    )
    type_ = layout.new_type(
        "box",
        shape=Box(0.4, 0.3, 0.5),
        color="#4878a8",
        magnetic_center=Frame(),
        magnetic_length=0.4,
    )
    type_.new_frame("survey").tx(0.1)
    objects = [
        layout.new_object(
            f"B{index:03d}",
            type=type_,
            position=Position(curve).ts(index + 0.5),
        )
        for index in range(_LARGE_OBJECT_COUNT)
    ]
    return layout, curve, objects


def small_straight_layout():
    layout = Layout()
    curve = layout.new_curve(
        "main",
        starting_frame=Frame("world"),
        color="#406080",
        segments=[Segment(2.0)],
    )
    type_ = layout.new_type(
        "box",
        shape=Box(0.4, 0.3, 0.5),
        color="#4878a8",
        magnetic_center=Frame(),
        magnetic_length=0.4,
    )
    layout.new_object("B000", type=type_, position=Position(curve).ts(1.0))
    return layout


def test_matplotlib_large_straight_layout_batches_meshes_frames_and_selection(
    large_straight_layout,
):
    pytest.importorskip("matplotlib")
    import matplotlib.pyplot as plt

    layout, _curve, objects = large_straight_layout
    viewer = layout.plot2d("xz", show=False)
    figure = viewer.figure
    try:
        assert viewer.batched_objects is True
        assert viewer._object_artists == [viewer._batch_object_collection]
        assert len(viewer._batch_object_collection.get_paths()) == len(objects)
        assert all(
            visual.faces.shape == (12, 3) for visual in viewer._object_visuals.values()
        )

        # The large-layout default does not allocate either frame layer.
        assert viewer.frames_visible is False
        assert viewer.beam_frames_visible is False
        assert viewer._named_frames_built is False
        assert viewer._beam_frames_built is False
        assert viewer._named_frame_artists == []
        assert viewer._beam_frame_artists == []

        # Object identity is retained even though every silhouette is in one
        # PolyCollection.  Exercise an actual projected hit and its highlight.
        target_index = 73
        target = objects[target_index]
        visual = viewer._object_visuals[target.name]
        viewer.draw()
        pixel = viewer.ax.transData.transform((0.0, target_index + 0.5))
        selection = viewer._pick_selection(
            SimpleNamespace(
                x=float(pixel[0]),
                y=float(pixel[1]),
                xdata=0.0,
                ydata=target_index + 0.5,
                inaxes=viewer.ax,
                button=None,
            )
        )
        assert selection is not None
        assert selection.kind == "object"
        assert selection.entity is target

        base_edges = viewer._batch_base_edgecolors.copy()
        viewer.select(target)
        assert viewer.selected is target
        assert visual.batch_index is not None
        changed = np.flatnonzero(
            np.any(viewer._batch_current_edgecolors != base_edges, axis=1)
        )
        np.testing.assert_array_equal(changed, [visual.batch_index])

        # Enabling the lazy layers adds a constant number of batched artists,
        # rather than several artists for every object.
        viewer.set_frames_visible(True)
        assert viewer._named_frames_built is True
        assert len(viewer._named_frame_artists) == 2
        assert len(viewer._batched_frame_visuals) == len(objects)

        viewer.set_beam_frames_visible(True)
        assert viewer._beam_frames_built is True
        assert len(viewer._beam_frame_artists) == 2
        assert len(viewer._batched_frame_visuals) == 3 * len(objects)
    finally:
        viewer.close()

    # A closed viewer may remain in IPython's Out cache without retaining its
    # figure, resolver, object maps, or scene-sized colour arrays.
    viewer.close()
    assert not plt.fignum_exists(figure.number)
    assert viewer.figure is None
    assert viewer.resolver is None
    assert viewer._object_visuals == {}
    assert viewer._object_pick_visuals == []
    assert viewer._object_pick_bounds.shape == (0, 4)
    assert viewer._batched_frame_visuals == []
    assert viewer._batch_object_collection is None
    assert viewer.object_artists == {}


def test_matplotlib_curved_batch_preserves_empty_projected_interior():
    pytest.importorskip("matplotlib")

    radius = 4.0
    layout = Layout()
    curve = layout.new_curve(
        "main",
        starting_frame=Frame("world"),
        color="#406080",
        segments=[Segment(20.0)],
    )
    type_ = layout.new_type(
        "semicircle",
        shape=Box(
            0.3,
            0.3,
            math.pi * radius,
            curvature=1.0 / radius,
        ),
        color="#4878a8",
        magnetic_center=Frame(),
        magnetic_length=1.0,
    )
    object_ = layout.new_object(
        "arc",
        type=type_,
        position=Position(curve).ts(5.0),
    )
    viewer = layout.plot2d(
        "xz",
        curves=False,
        frames=False,
        batch_objects=True,
        object_resolution=48,
        show=False,
    )
    try:
        visual = viewer._object_visuals["arc"]
        assert visual.batch_indices is not None
        assert len(visual.batch_indices) > 1

        # This point is inside the old convex hull but well inside the empty
        # region between the semicircular body and its diameter.
        empty_point = (-radius / 2.0, 5.0)
        paths = viewer._batch_object_collection.get_paths()
        assert not any(
            paths[int(index)].contains_point(empty_point)
            for index in visual.batch_indices
        )

        viewer.draw()
        pixel = viewer.ax.transData.transform(empty_point)
        selection = viewer._pick_selection(
            SimpleNamespace(
                x=float(pixel[0]),
                y=float(pixel[1]),
                xdata=empty_point[0],
                ydata=empty_point[1],
                inaxes=viewer.ax,
                button=None,
            )
        )
        assert selection is None

        base_edges = viewer._batch_base_edgecolors.copy()
        viewer.select(object_)
        changed = np.flatnonzero(
            np.any(viewer._batch_current_edgecolors != base_edges, axis=1)
        )
        np.testing.assert_array_equal(changed, visual.batch_indices)
    finally:
        viewer.close()


def test_vtk_large_straight_layout_batches_cells_frames_and_selection(
    large_straight_layout,
):
    pytest.importorskip("vtkmodules")

    layout, _curve, objects = large_straight_layout
    viewer = layout.plot3d(show=False, off_screen=True)
    render_window = viewer.render_window
    renderer = viewer.renderer
    interactor = viewer.interactor
    try:
        assert render_window.GetNeverRendered() == 1
        assert viewer.batched_objects is True
        assert len(viewer._object_actors) == math.ceil(
            len(objects) / viewer.object_batch_size
        )
        assert len({id(actor) for actor in viewer.object_actors.values()}) == 1
        assert all(
            visual.faces.shape == (12, 3) for visual in viewer._object_visuals.values()
        )

        actor = viewer._object_actors[0]
        assert actor.GetMapper().GetInput().GetNumberOfCells() == 12 * len(objects)
        cell_ends, targets = viewer._batched_pick_targets[actor]
        np.testing.assert_array_equal(cell_ends, 12 * np.arange(1, len(objects) + 1))
        assert len(targets) == len(objects)

        # Cell ids on both sides of a batch-internal object boundary must map
        # to the correct object, including the last cell in the actor.
        for cell_id, object_index in (
            (0, 0),
            (11, 0),
            (12, 1),
            (23, 1),
            (12 * len(objects) - 1, len(objects) - 1),
        ):
            target = viewer._target_for_prop(actor, cell_id)
            assert target is not None
            assert target.kind == "object"
            assert target.entity is objects[object_index]

        selected = objects[73]
        viewer.select(selected)
        assert viewer.selected is selected
        highlight = viewer._object_highlight_actor
        assert highlight is not None
        assert highlight.GetVisibility() == 1
        assert highlight.GetMapper().GetInput().GetNumberOfCells() == 12

        assert viewer.frames_visible is False
        assert viewer.beam_frames_visible is False
        assert viewer._named_frames_built is False
        assert viewer._beam_frames_built is False
        assert viewer._named_frame_actors == []
        assert viewer._beam_frame_actors == []

        viewer.set_frames_visible(True)
        assert viewer._named_frames_built is True
        assert len(viewer._named_frame_actors) == 1
        frame_actor = viewer._named_frame_actors[0]
        assert len(viewer._batched_pick_targets[frame_actor][1]) == len(objects)

        viewer.set_beam_frames_visible(True)
        assert viewer._beam_frames_built is True
        assert len(viewer._beam_frame_actors) == 2
        for beam_actor in viewer._beam_frame_actors:
            assert len(viewer._batched_pick_targets[beam_actor][1]) == 2 * len(objects)
    finally:
        viewer.close()

    # close() must be idempotent and must not trigger a render on a headless
    # show=False viewer while it tears down every heavyweight scene reference.
    viewer.close()
    assert render_window.GetNeverRendered() == 1
    assert renderer.GetViewProps().GetNumberOfItems() == 0
    assert render_window.GetRenderers().GetNumberOfItems() == 0
    assert interactor.GetRenderWindow() is None
    assert viewer.resolver is None
    assert viewer._resolver_context is None
    assert viewer._object_visuals == {}
    assert viewer._object_visual_by_identity == {}
    assert viewer._pick_targets == {}
    assert viewer._batched_pick_targets == {}
    assert viewer._pick_locators == []
    assert viewer._object_actors == []
    assert viewer._named_frame_actors == []
    assert viewer._beam_frame_actors == []
    assert viewer._object_highlight_actor is None
    assert viewer.object_actors == {}


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"curve_resolution": True}, TypeError),
        ({"curve_resolution": 0}, ValueError),
        ({"object_resolution": 1.5}, ValueError),
        ({"radial_resolution": 2}, ValueError),
        ({"radial_resolution": "coarse"}, ValueError),
        ({"batch_objects": "yes"}, TypeError),
    ],
)
def test_matplotlib_resolution_options_reject_invalid_values(kwargs, error):
    pytest.importorskip("matplotlib")
    layout = small_straight_layout()

    with pytest.raises(error, match=next(iter(kwargs))):
        layout.plot2d(show=False, **kwargs)


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"curve_resolution": True}, TypeError),
        ({"curve_resolution": 0}, ValueError),
        ({"object_resolution": 1.5}, ValueError),
        ({"radial_resolution": 2}, ValueError),
        ({"radial_resolution": "coarse"}, ValueError),
        ({"object_batch_size": 0}, ValueError),
        ({"batch_objects": "yes"}, TypeError),
    ],
)
def test_vtk_resolution_and_batch_options_reject_invalid_values(kwargs, error):
    pytest.importorskip("vtkmodules")
    layout = small_straight_layout()

    with pytest.raises(error, match=next(iter(kwargs))):
        layout.plot3d(show=False, off_screen=True, **kwargs)
