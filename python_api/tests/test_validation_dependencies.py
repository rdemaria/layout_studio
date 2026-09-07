from __future__ import annotations

import pytest

from layout_studio import (
    Box,
    Curve,
    DanglingReferenceError,
    Frame,
    Layout,
    Position,
    ReferenceCycleError,
    Segment,
    ValidationError,
)


def add_type(layout, name="kind"):
    return layout.new_type(
        name,
        shape=Box(1.0, 1.0, 1.0),
        color="#112233",
        magnetic_center=Frame(),
        magnetic_length=1.0,
        magnetic_curvature=0.0,
        magnetic_roll=0.0,
    )


def test_incremental_graph_can_be_dangling_but_validate_rejects_it():
    layout = Layout()
    layout.new_curve(
        "dependent",
        starting_frame=Frame("curve:missing"),
        color="#112233",
        segments=[Segment(1.0)],
    )

    with pytest.raises(DanglingReferenceError) as exc_info:
        layout.validate()
    assert exc_info.value.path is None or "dependent" in exc_info.value.path


def test_dangling_type_object_and_frame_references_are_rejected():
    layout = Layout()
    layout.new_object(
        "bad_type",
        type="missing",
        position=Position("world"),
    )

    with pytest.raises(DanglingReferenceError):
        layout.validate()

    layout = Layout()
    type_ = add_type(layout)
    layout.new_object(
        "bad_object",
        type=type_,
        position=Position("missing->center"),
    )
    with pytest.raises(DanglingReferenceError):
        layout.validate()

    layout = Layout()
    type_ = add_type(layout)
    anchor = layout.new_object("A", type=type_, position=Position("world"))
    layout.new_object(
        "bad_frame",
        type=type_,
        position=Position("A->missing"),
    )
    assert anchor.name == "A"
    with pytest.raises(DanglingReferenceError):
        layout.validate()


@pytest.mark.parametrize(
    "frame_name",
    [
        "magnetic_center",
        "magnetic_entry",
        "magnetic_exit",
        "beam_center",
        "beam_entry",
        "beam_exit",
    ],
)
def test_unconfigured_optional_axis_frames_are_dangling(frame_name):
    layout = Layout()
    type_ = layout.new_type("bare", color="#112233")
    layout.new_object("A", type=type_, position=Position("world"))
    layout.new_object(
        "B",
        type=type_,
        position=Position(f"A->{frame_name}"),
    )

    with pytest.raises(DanglingReferenceError):
        layout.validate()


@pytest.mark.parametrize("target", ["magnetic_entry", "beam_entry"])
def test_unconfigured_optional_axis_targets_are_dangling(target):
    layout = Layout()
    type_ = layout.new_type("bare", color="#112233")
    layout.new_object(
        "A",
        type=type_,
        position=Position("world", target=target),
    )

    with pytest.raises(DanglingReferenceError):
        layout.validate()


def test_curve_dependency_cycle_is_rejected():
    layout = Layout()
    layout.add_curve(
        "left",
        Curve(
            starting_frame=Frame("curve:right"),
            color="#112233",
            segments=[Segment(1.0)],
        ),
    )
    layout.add_curve(
        "right",
        Curve(
            starting_frame=Frame("curve:left"),
            color="#445566",
            segments=[Segment(1.0)],
        ),
    )

    with pytest.raises(ReferenceCycleError):
        layout.validate()


def test_object_dependency_cycle_is_rejected():
    layout = Layout()
    type_ = add_type(layout)
    layout.new_object("A", type=type_, position=Position("B->center"))
    layout.new_object("B", type=type_, position=Position("A->center"))

    with pytest.raises(ReferenceCycleError):
        layout.validate()


def test_station_inference_reference_curve_adds_a_cycle_edge():
    layout = Layout()
    type_ = add_type(layout)
    layout.new_object(
        "A",
        type=type_,
        position=Position("world", reference_curve="beam").ts(0.0),
    )
    layout.new_curve(
        "beam",
        starting_frame=Frame("A->center"),
        color="#112233",
        segments=[Segment(2.0)],
    )

    with pytest.raises(ReferenceCycleError):
        layout.validate()


def test_ts_context_requirements_are_validated():
    layout = Layout()
    type_ = add_type(layout)
    layout.new_object(
        "missing_curve",
        type=type_,
        position=Position("world").ts(1.0),
    )
    with pytest.raises(ValidationError):
        layout.validate()

    layout = Layout()
    type_ = add_type(layout)
    layout.new_curve(
        "main",
        starting_frame=Frame("world"),
        color="#112233",
        segments=[Segment(2.0)],
    )
    layout.new_object(
        "redundant_curve",
        type=type_,
        position=Position("curve:main", reference_curve="main").ts(1.0),
    )
    with pytest.raises(ValidationError):
        layout.validate()


def test_curve_starting_frame_cannot_use_world_ts():
    layout = Layout()
    layout.new_curve(
        "bad",
        starting_frame=Frame("world").ts(1.0),
        color="#112233",
        segments=[Segment(2.0)],
    )

    with pytest.raises(ValidationError):
        layout.validate()
