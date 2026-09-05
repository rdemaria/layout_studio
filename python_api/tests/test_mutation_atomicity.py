from __future__ import annotations

import pytest

from layout_studio import (
    AmbiguousNameError,
    AttachmentError,
    Box,
    Curve,
    Frame,
    Layout,
    NameConflictError,
    Operation,
    Position,
    ReferenceInUseError,
    Segment,
    Type,
    ValidationError,
)


def detached_curve(color="#112233"):
    return Curve(
        starting_frame=Frame("world"),
        color=color,
        segments=[Segment(1.0), Segment(2.0, 0.1, 0.2)],
    )


def detached_type(color="#445566"):
    return Type(
        shape=Box(1.0, 2.0, 3.0),
        color=color,
        magnetic_center=Frame().tx(0.1),
        magnetic_length=1.5,
        magnetic_curvature=0.2,
        magnetic_roll=-0.1,
        beam_center=Frame().ty(0.2),
        beam_length=1.25,
        beam_curvature=-0.3,
        beam_roll=0.4,
    )


def operation_pairs(frame):
    return [(operation.name, operation.value) for operation in frame.operations]


def test_curve_set_and_segment_slice_fail_atomically():
    curve = detached_curve()
    original_dict = curve.to_dict()
    original_segments = list(curve.segments)
    original_frame = curve.starting_frame

    replacement_frame = Frame("world").tx(5.0)
    with pytest.raises(ValidationError):
        curve.set(
            starting_frame=replacement_frame,
            color="#abcdef",
            segments=[],
        )

    assert curve.to_dict() == original_dict
    assert list(curve.segments) == original_segments
    assert curve.starting_frame is original_frame
    assert original_frame.owner is curve
    assert replacement_frame.owner is None

    with pytest.raises(ValidationError):
        curve.segments[:] = []
    assert list(curve.segments) == original_segments

    with pytest.raises(ValidationError):
        curve.segments[0] = [0.0, 0.0, 0.0]
    assert list(curve.segments) == original_segments


def test_curve_batch_add_segments_is_all_or_nothing():
    curve = detached_curve()
    original_segments = list(curve.segments)

    with pytest.raises(ValidationError):
        curve.add_segments(
            [
                (3.0, 0.0, 0.0),
                (0.0, 0.0, 0.0),
                (4.0, 0.0, 0.0),
            ],
            index=1,
        )

    assert list(curve.segments) == original_segments
    assert curve.length == pytest.approx(3.0)


def test_frame_operation_edit_insert_move_remove_and_clear():
    frame = Frame("world").tx(1.0).ty(2.0).rs(3.0)

    assert frame.set_operation(1, name="tt", value=4.0) is frame
    assert operation_pairs(frame) == [("tx", 1.0), ("tt", 4.0), ("rs", 3.0)]

    assert frame.insert_operation(1, "rx", 0.5) is frame
    assert operation_pairs(frame) == [
        ("tx", 1.0),
        ("rx", 0.5),
        ("tt", 4.0),
        ("rs", 3.0),
    ]

    assert frame.move_operation(0, 3) is frame
    assert operation_pairs(frame) == [
        ("rx", 0.5),
        ("tt", 4.0),
        ("rs", 3.0),
        ("tx", 1.0),
    ]

    removed = frame.remove_operation(1)
    assert removed == Operation("tt", 4.0)
    assert operation_pairs(frame) == [("rx", 0.5), ("rs", 3.0), ("tx", 1.0)]

    before_invalid_edit = operation_pairs(frame)
    with pytest.raises(ValidationError):
        frame.set_operation(0, name="tz")
    assert operation_pairs(frame) == before_invalid_edit

    assert frame.clear_operations() is frame
    assert operation_pairs(frame) == []


def test_type_set_validates_every_change_before_committing_any():
    type_ = detached_type()
    original_shape = type_.shape
    original_color = type_.color
    original_center = type_.magnetic_center
    original_length = type_.magnetic_length
    original_curvature = type_.magnetic_curvature
    original_roll = type_.magnetic_roll
    original_beam_center = type_.beam_center
    original_beam_length = type_.beam_length
    original_beam_curvature = type_.beam_curvature
    original_beam_roll = type_.beam_roll
    replacement_center = Frame().ty(0.4)

    with pytest.raises(ValidationError):
        type_.set(
            shape=Box(4.0, 5.0, 6.0),
            color="#abcdef",
            magnetic_center=replacement_center,
            magnetic_length=0.0,
            magnetic_curvature=0.5,
            magnetic_roll=0.6,
        )

    assert type_.shape is original_shape
    assert type_.color == original_color
    assert type_.magnetic_center is original_center
    assert type_.magnetic_length == original_length
    assert type_.magnetic_curvature == original_curvature
    assert type_.magnetic_roll == original_roll
    assert type_.beam_center is original_beam_center
    assert type_.beam_length == original_beam_length
    assert type_.beam_curvature == original_beam_curvature
    assert type_.beam_roll == original_beam_roll
    assert original_center.owner is type_
    assert original_beam_center.owner is type_
    assert replacement_center.owner is None


def test_type_axis_feature_can_be_removed_as_one_atomic_group():
    type_ = detached_type()
    old_center = type_.magnetic_center

    type_.set(
        magnetic_center=None,
        magnetic_length=None,
        magnetic_curvature=None,
        magnetic_roll=None,
    )

    assert old_center.owner is None
    assert type_.magnetic_center is None
    assert type_.magnetic_length is None
    assert type_.magnetic_curvature is None
    assert type_.magnetic_roll is None
    assert "magnetic_center" not in type_.to_dict()
    assert "beam_center" in type_.to_dict()


def test_type_axis_partial_removal_rolls_back_without_detaching_center():
    type_ = detached_type()
    original = type_.to_dict()
    center = type_.beam_center

    with pytest.raises(ValidationError):
        type_.set(beam_center=None)

    assert type_.to_dict() == original
    assert type_.beam_center is center
    assert center.owner is type_


def test_type_mechanical_shape_can_be_removed_independently():
    type_ = detached_type()

    type_.set_shape(None)

    assert type_.shape is None
    assert "shape" not in type_.to_dict()
    assert type_.magnetic_center is not None
    assert type_.beam_center is not None


def test_axis_helpers_create_update_and_remove_optional_features():
    type_ = Type(color="#112233")

    assert type_.set_magnetic_axis(length=2.0, curvature=0.3, roll=-0.2) is type_
    assert type_.magnetic_center is not None
    assert type_.magnetic_center.owner is type_
    assert type_.magnetic_length == 2.0
    assert type_.magnetic_curvature == 0.3
    assert type_.magnetic_roll == -0.2

    replacement = Frame().tx(0.4)
    assert type_.set_beam_axis(center=replacement, length=1.5) is type_
    assert type_.beam_center is replacement
    assert type_.beam_length == 1.5
    assert type_.beam_curvature == 0.0
    assert type_.beam_roll == 0.0

    assert type_.remove_magnetic_axis() is type_
    assert type_.remove_beam_axis() is type_
    assert type_.implicit_frames == frozenset({"center"})


def test_position_set_rolls_back_and_operations_remain_a_live_alias():
    position = Position("world", target="center").tx(1.0)
    original_reference = position.reference
    original_target = position.target_name
    candidate_reference = Frame("world").tt(5.0)

    with pytest.raises(ValidationError):
        position.set(
            reference=candidate_reference,
            target="other",
            reference_curve="main",
            operations=[["ty", 2.0], ["bad", 3.0]],
        )

    assert position.reference is original_reference
    assert original_reference.owner is position
    assert candidate_reference.owner is None
    assert position.target_name == original_target
    assert position.reference_curve is None
    assert operation_pairs(position.reference) == [("tx", 1.0)]
    assert position.operations is position.reference.operations

    position.operations.append(Operation("tt", 2.0))
    assert operation_pairs(position.reference) == [("tx", 1.0), ("tt", 2.0)]
    position.reference.operations[0] = Operation("ty", 4.0)
    assert operation_pairs(position) == [("ty", 4.0), ("tt", 2.0)]


def test_entity_map_direct_replacement_transfers_ownership_atomically():
    layout = Layout()
    first = layout.add_curve("main", detached_curve())
    replacement = detached_curve("#778899")

    layout.curves["main"] = replacement
    assert layout.curves["main"] is replacement
    assert replacement.owner is layout
    assert replacement.name == "main"
    assert first.owner is None
    assert first.name is None

    already_bound = layout.add_curve("other", detached_curve("#aabbcc"))
    with pytest.raises(AttachmentError):
        layout.curves["main"] = already_bound
    assert layout.curves["main"] is replacement
    assert already_bound.owner is layout
    assert already_bound.name == "other"


def test_replacing_a_referenced_map_value_is_rejected_without_detaching_either_value():
    layout = Layout()
    curve = layout.add_curve("main", detached_curve())
    type_ = layout.add_type("kind", detached_type())
    layout.new_object("Q1", type=type_, position=Position(curve).ts(0.5))
    candidate = detached_curve("#abcdef")

    with pytest.raises(ReferenceInUseError):
        layout.curves["main"] = candidate

    assert layout.curves["main"] is curve
    assert curve.owner is layout
    assert curve.name == "main"
    assert candidate.owner is None


def test_ambiguous_rename_and_pop_are_non_mutating_and_explicit_forms_work():
    layout = Layout()
    curve = layout.add_curve("same", detached_curve())
    type_ = layout.add_type("kind", detached_type())
    object_ = layout.new_object("same", type=type_, position=Position("world"))

    with pytest.raises(AmbiguousNameError):
        layout.rename("same", "renamed")
    with pytest.raises(AmbiguousNameError):
        layout.pop("same")

    assert layout.curves["same"] is curve
    assert layout.objects["same"] is object_
    assert "renamed" not in layout.curves
    assert "renamed" not in layout.objects

    assert layout.rename("same", "beam", kind="curve") is curve
    assert layout.curves["beam"] is curve
    assert layout["same"] is object_
    assert layout.pop("same", kind="object") is object_
    assert object_.owner is None


def test_conflicting_rename_leaves_both_registry_entries_unchanged():
    layout = Layout()
    first = layout.add_curve("first", detached_curve())
    second = layout.add_curve("second", detached_curve("#abcdef"))

    with pytest.raises(NameConflictError):
        layout.rename(first, "second")

    assert layout.curves["first"] is first
    assert layout.curves["second"] is second
    assert first.name == "first"
    assert second.name == "second"
