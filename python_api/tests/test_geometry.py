from __future__ import annotations

import math

import numpy as np
import pytest
from conftest import assert_pose

from layout_studio import (
    AmbiguousStationError,
    Box,
    Frame,
    Layout,
    NoStationSolutionError,
    Position,
    Segment,
    StationOutOfRangeError,
)
from layout_studio.resolver import Resolver, swept_type_mesh


def add_curve(layout, name, segments, *, starting_frame=None):
    return layout.new_curve(
        name,
        starting_frame=starting_frame or Frame("world"),
        color="#112233",
        segments=segments,
    )


def add_type(
    layout,
    name="element",
    *,
    curvature=0.0,
    roll=0.0,
    magnetic_center=None,
    magnetic_length=1.0,
):
    return layout.new_type(
        name,
        shape=Box(1.0, 1.0, 2.0, curvature=curvature, roll=roll),
        color="#445566",
        magnetic_center=magnetic_center or Frame(),
        magnetic_length=magnetic_length,
    )


def test_explicit_resolver_context_reuses_validation_and_geometry_caches():
    layout = Layout()
    curve = add_curve(layout, "line", [Segment(10.0)])
    type_ = add_type(layout)
    object_ = layout.new_object("Q1", type=type_, position=Position(curve).ts(2.0))
    resolver = layout.resolver()
    calls = 0
    validate = layout.validate

    def counted_validate():
        nonlocal calls
        calls += 1
        return validate()

    layout.validate = counted_validate  # type: ignore[method-assign]
    with resolver:
        first = resolver.object_frame(object_)
        with resolver:
            second = resolver.object_frame(object_)

    assert calls == 1
    np.testing.assert_allclose(first.matrix, second.matrix)
    assert resolver._object_centers == {}
    assert resolver._curve_data_cache == {}

    resolver.object_frame(object_)
    assert calls == 2


def test_viewers_can_request_a_lightweight_mesh_without_public_metadata():
    type_ = add_type(Layout())

    full = swept_type_mesh(type_, resolution=2)
    lean = swept_type_mesh(type_, resolution=2, include_metadata=False)
    lean_again = swept_type_mesh(type_, resolution=2, include_metadata=False)

    metadata = {
        "normals",
        "stations",
        "section_indices",
        "centerline_frames",
    }
    assert metadata <= full.keys()
    assert not metadata & lean.keys()
    assert lean["faces"] is lean_again["faces"]
    assert not lean["faces"].flags.writeable
    assert full["faces"].flags.writeable
    with pytest.raises(ValueError):
        lean["faces"].setflags(write=True)

    unpoisoned = swept_type_mesh(type_, resolution=2, include_metadata=False)
    np.testing.assert_array_equal(unpoisoned["faces"], lean["faces"])


def test_straight_curve_frame_and_pose_matrix():
    layout = Layout()
    curve = add_curve(
        layout,
        "line",
        [Segment(10.0)],
        starting_frame=Frame("world").tx(1.0).ty(-2.0),
    )

    pose = curve.get_frame(4.5)

    assert pose.space == "world"
    assert_pose(
        pose,
        origin=[1.0, -2.0, 4.5],
        x=[1.0, 0.0, 0.0],
        y=[0.0, 1.0, 0.0],
        tangent=[0.0, 0.0, 1.0],
    )
    expected = np.eye(4)
    expected[:3, 3] = [1.0, -2.0, 4.5]
    np.testing.assert_allclose(pose.matrix, expected, atol=1e-12, rtol=0.0)
    np.testing.assert_allclose(
        pose.transform_point([2.0, 3.0, 4.0]),
        [3.0, 1.0, 8.5],
        atol=1e-12,
        rtol=0.0,
    )
    assert not pose.matrix.flags.writeable


def test_positive_quarter_arc_bends_toward_local_minus_x():
    layout = Layout()
    curve = add_curve(layout, "arc", [Segment(math.pi / 2.0, math.pi / 2.0)])

    midpoint = curve.get_frame(math.pi / 4.0)
    end = curve.get_frame(math.pi / 2.0)

    root_half = math.sqrt(0.5)
    assert_pose(
        midpoint,
        origin=[root_half - 1.0, 0.0, root_half],
        x=[root_half, 0.0, root_half],
        y=[0.0, 1.0, 0.0],
        tangent=[-root_half, 0.0, root_half],
    )
    assert_pose(
        end,
        origin=[-1.0, 0.0, 1.0],
        x=[0.0, 0.0, 1.0],
        y=[0.0, 1.0, 0.0],
        tangent=[-1.0, 0.0, 0.0],
    )


def test_positive_roll_rotates_bend_direction_toward_minus_y():
    layout = Layout()
    curve = add_curve(
        layout,
        "rolled",
        [Segment(math.pi / 2.0, math.pi / 2.0, math.pi / 2.0)],
    )

    end = curve.get_frame(math.pi / 2.0)

    assert_pose(
        end,
        origin=[0.0, -1.0, 1.0],
        x=[1.0, 0.0, 0.0],
        y=[0.0, 0.0, 1.0],
        tangent=[0.0, -1.0, 0.0],
    )


def test_multisegment_curve_is_continuous_and_uses_exact_arc_geometry():
    layout = Layout()
    curve = add_curve(
        layout,
        "mixed",
        [Segment(2.0), Segment(math.pi / 2.0, math.pi / 2.0), Segment(3.0)],
    )

    at_first_joint = curve.get_frame(2.0)
    at_second_joint = curve.get_frame(2.0 + math.pi / 2.0)
    after_last = curve.get_frame(curve.length)

    assert_pose(at_first_joint, origin=[0.0, 0.0, 2.0], tangent=[0.0, 0.0, 1.0])
    assert_pose(
        at_second_joint,
        origin=[-1.0, 0.0, 3.0],
        tangent=[-1.0, 0.0, 0.0],
    )
    assert_pose(after_last, origin=[-4.0, 0.0, 3.0], tangent=[-1.0, 0.0, 0.0])


def test_curve_extrapolates_by_straight_tangent_continuation():
    layout = Layout()
    length = math.pi / 2.0
    curve = add_curve(layout, "arc", [Segment(length, math.pi / 2.0)])

    assert_pose(curve.get_frame(-2.0), origin=[0.0, 0.0, -2.0], tangent=[0.0, 0.0, 1.0])
    assert_pose(
        curve.get_frame(length + 2.0),
        origin=[-3.0, 0.0, 1.0],
        tangent=[-1.0, 0.0, 0.0],
    )

    with pytest.raises(StationOutOfRangeError):
        curve.get_frame(-0.1, extrapolate=False)
    with pytest.raises(StationOutOfRangeError):
        curve.get_frame(length + 0.1, extrapolate=False)


def test_curve_reference_hoists_and_sums_all_ts_operations():
    layout = Layout()
    length = math.pi / 2.0
    base = add_curve(layout, "base", [Segment(length, math.pi / 2.0)])

    before = add_curve(
        layout,
        "before",
        [Segment(1.0)],
        starting_frame=Frame(base).tx(2.0).ts(length / 3.0).ts(2.0 * length / 3.0),
    )
    after = add_curve(
        layout,
        "after",
        [Segment(1.0)],
        starting_frame=Frame(base).ts(length).tx(2.0),
    )

    assert_pose(
        before.get_frame(0.0), origin=[-1.0, 0.0, 3.0], tangent=[-1.0, 0.0, 0.0]
    )
    np.testing.assert_allclose(
        before.get_frame(0.0).matrix, after.get_frame(0.0).matrix, atol=1e-12
    )


def test_non_ts_operations_keep_their_relative_order_after_curve_ts_hoisting():
    layout = Layout()
    base = add_curve(layout, "base", [Segment(10.0)])
    rotate_then_translate = add_curve(
        layout,
        "rotate_then_translate",
        [Segment(1.0)],
        starting_frame=Frame(base).rx(math.pi / 2.0).ts(2.0).ty(1.0),
    )
    translate_then_rotate = add_curve(
        layout,
        "translate_then_rotate",
        [Segment(1.0)],
        starting_frame=Frame(base).ty(1.0).ts(2.0).rx(math.pi / 2.0),
    )

    assert_pose(rotate_then_translate.get_frame(0.0), origin=[0.0, 0.0, 3.0])
    assert_pose(translate_then_rotate.get_frame(0.0), origin=[0.0, 1.0, 2.0])


def test_type_local_ts_runs_sequentially_at_its_list_position():
    layout = Layout()
    type_ = add_type(layout, curvature=1.0)
    length = math.pi / 2.0
    pre = type_.new_frame("pre", frame=Frame().tx(2.0).ts(length))
    post = type_.new_frame("post", frame=Frame().ts(length).tx(2.0))

    assert pre.owner is type_
    assert post.owner is type_
    assert_pose(
        type_.get_frame("pre"), origin=[1.0, 0.0, 1.0], tangent=[-1.0, 0.0, 0.0]
    )
    assert_pose(
        type_.get_frame("post"), origin=[-1.0, 0.0, 3.0], tangent=[-1.0, 0.0, 0.0]
    )
    assert type_.get_frame("pre").space == "type_local"


def test_type_local_negative_ts_follows_curved_path_backwards():
    layout = Layout()
    type_ = add_type(layout, curvature=1.0)
    type_.new_frame("back").ts(-math.pi / 2.0)

    assert_pose(
        type_.get_frame("back"),
        origin=[-1.0, 0.0, -1.0],
        tangent=[1.0, 0.0, 0.0],
    )


def test_target_alignment_places_requested_local_frame_at_desired_world_frame():
    layout = Layout()
    curve = add_curve(layout, "main", [Segment(20.0)])
    type_ = add_type(layout)
    entry = type_.new_frame("entry").ts(-1.0)
    object_ = layout.new_object(
        "Q1",
        type=type_,
        position=Position(curve, target=entry).ts(10.0),
    )

    assert_pose(object_.get_frame("entry"), origin=[0.0, 0.0, 10.0])
    assert_pose(object_.get_frame("center"), origin=[0.0, 0.0, 11.0])


def test_curved_type_target_alignment_inverts_the_complete_local_pose():
    layout = Layout()
    curve = add_curve(layout, "main", [Segment(20.0)])
    anchor_type = add_type(layout, name="anchor")
    curved_type = add_type(layout, name="curved", curvature=0.7, roll=0.3)
    mount = curved_type.new_frame("mount").tx(0.2).ts(0.8).rs(0.15)
    anchor = layout.new_object(
        "anchor",
        type=anchor_type,
        position=Position(curve).ts(8.0).ry(0.2).tx(0.4),
    )
    aligned = layout.new_object(
        "aligned",
        type=curved_type,
        position=Position(anchor.ref(), target=mount),
    )

    np.testing.assert_allclose(
        aligned.get_frame(mount).matrix,
        anchor.get_frame().matrix,
        atol=1e-12,
        rtol=0.0,
    )


def test_reserved_magnetic_frames_are_derived_but_not_stored():
    layout = Layout()
    type_ = add_type(
        layout,
        magnetic_center=Frame().tx(0.2).ts(0.5),
        magnetic_length=2.0,
    )

    assert set(type_.frames) == set()
    assert_pose(type_.get_frame("magnetic_center"), origin=[0.2, 0.0, 0.5])
    assert_pose(type_.get_frame("magnetic_entry"), origin=[0.2, 0.0, -0.5])
    assert_pose(type_.get_frame("magnetic_exit"), origin=[0.2, 0.0, 1.5])


def test_object_reference_chain_aligns_each_target_and_preserves_orientation():
    layout = Layout()
    curve = add_curve(layout, "main", [Segment(30.0)])
    type_ = add_type(layout)
    start = type_.new_frame("start").ts(-1.0)
    end = type_.new_frame("end").ts(1.0)
    first = layout.new_object(
        "A",
        type=type_,
        position=Position(curve).ts(5.0).ry(0.2),
    )
    second = layout.new_object(
        "B",
        type=type_,
        position=Position(first.ref(end), target=start),
    )
    third = layout.new_object(
        "C",
        type=type_,
        position=Position(second.ref(end), target=start),
    )

    np.testing.assert_allclose(
        second.get_frame("start").matrix, first.get_frame("end").matrix, atol=1e-12
    )
    np.testing.assert_allclose(
        third.get_frame("start").matrix, second.get_frame("end").matrix, atol=1e-12
    )
    np.testing.assert_allclose(
        third.get_frame().tangent, first.get_frame().tangent, atol=1e-12
    )


def test_station_inference_uses_raw_reference_origin_and_replaces_orientation():
    layout = Layout()
    curve = add_curve(layout, "main", [Segment(20.0)])
    type_ = add_type(layout)
    anchor = layout.new_object(
        "A",
        type=type_,
        position=Position("world").tt(4.0).ry(math.pi / 2.0),
    )

    # Both tt and tx occur textually before ts.  They must not alter the point
    # P used for inference: P is A.center's raw origin at z=4.  After inferring
    # s=4, ts advances to s=5, then the non-ts operations run in list order.
    follower = layout.new_object(
        "B",
        type=type_,
        position=(
            Position(anchor.ref(), reference_curve=curve).tt(2.0).tx(3.0).ts(1.0)
        ),
    )

    assert_pose(
        follower.get_frame(),
        origin=[3.0, 0.0, 7.0],
        x=[1.0, 0.0, 0.0],
        y=[0.0, 1.0, 0.0],
        tangent=[0.0, 0.0, 1.0],
    )


def test_station_inference_searches_only_the_finite_curve_domain():
    layout = Layout()
    curve = add_curve(layout, "short", [Segment(2.0)])

    assert curve.infer_station([10.0, -3.0, 1.25]) == pytest.approx(1.25)
    with pytest.raises(NoStationSolutionError):
        curve.infer_station([0.0, 0.0, 3.0])


def test_station_inference_rejects_a_closest_continuous_solution():
    layout = Layout()
    circle = add_curve(layout, "circle", [Segment(2.0 * math.pi, 2.0 * math.pi)])

    # The arc center is (-1, 0, 0), which lies in every transverse plane.
    with pytest.raises(AmbiguousStationError):
        circle.infer_station([-1.0, 0.0, 0.0])


def test_station_inference_preserves_distant_equidistant_roots():
    layout = Layout()
    curve = add_curve(
        layout,
        "hairpin",
        [
            Segment(2.0),
            Segment(math.pi, math.pi),
            Segment(2.0),
        ],
    )

    # The two straight legs have distinct station roots at the same distance.
    # A proximity-ordered search must retain the second root for ambiguity.
    with pytest.raises(AmbiguousStationError):
        curve.infer_station([-1.0, 0.0, 1.0])


def test_station_inference_matches_known_frames_on_a_mixed_curve():
    layout = Layout()
    curve = add_curve(
        layout,
        "mixed",
        [
            Segment(2.0),
            Segment(math.pi / 2.0, math.pi / 2.0, 0.35),
            Segment(3.0),
        ],
    )

    for station in (0.4, 2.3, curve.length - 0.6):
        pose = curve.get_frame(station)
        point = pose.origin + 0.17 * pose.x + 0.31 * pose.y
        assert curve.infer_station(point) == pytest.approx(station, abs=1.0e-11)


def test_station_inference_cache_is_fresh_after_live_curve_edit():
    layout = Layout()
    curve = add_curve(layout, "editable", [Segment(3.0)])
    resolver = Resolver(layout)

    assert resolver.infer_station(curve, [0.0, 0.0, 2.5]) == pytest.approx(2.5)
    curve.segments[0] = Segment(2.0)

    with pytest.raises(NoStationSolutionError):
        resolver.infer_station(curve, [0.0, 0.0, 2.5])
