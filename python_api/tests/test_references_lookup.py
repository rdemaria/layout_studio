from __future__ import annotations

import re

import pytest
from layout_studio import (
    AmbiguousNameError,
    AttachmentError,
    Box,
    CurveReference,
    ForeignLayoutError,
    Frame,
    Layout,
    ObjectReference,
    Position,
    Reference,
    ReferenceInUseError,
    Segment,
    UnknownEntityError,
    ValidationError,
    WorldReference,
)


def add_straight_curve(layout: Layout, name: str = "main"):
    return layout.new_curve(
        name,
        starting_frame=Frame("world"),
        color="#112233",
        segments=[Segment(20.0)],
    )


def add_type(layout: Layout, name: str = "magnet"):
    return layout.new_type(
        name,
        shape=Box(1.0, 1.0, 2.0),
        color="#445566",
        magnetic_center=Frame(),
        magnetic_length=1.0,
    )


@pytest.mark.parametrize(
    ("text", "reference_type", "canonical"),
    [
        ("world", WorldReference, {"kind": "world"}),
        ("curve:main", CurveReference, {"kind": "curve", "curve": "main"}),
        (
            "Q1->magnetic_exit",
            ObjectReference,
            {"kind": "object_frame", "object": "Q1", "frame": "magnetic_exit"},
        ),
        (
            "world->center",
            ObjectReference,
            {"kind": "object_frame", "object": "world", "frame": "center"},
        ),
        ("curve:world", CurveReference, {"kind": "curve", "curve": "world"}),
    ],
)
def test_reference_shorthands_have_canonical_serialization(
    text, reference_type, canonical
):
    reference = Reference.parse(text)

    assert isinstance(reference, reference_type)
    assert reference.to_dict() == canonical


@pytest.mark.parametrize(
    "text",
    [
        "main",
        "World",
        " world",
        "world ",
        "curve:",
        "->center",
        "Q1->",
        "Q1->exit->extra",
        "curve:main@1.0",
        "",
    ],
)
def test_ambiguous_or_malformed_reference_shorthands_are_rejected(text):
    with pytest.raises((ValidationError, TypeError, ValueError)):
        Reference.parse(text)


def test_layout_reference_resolves_strings_to_live_instances():
    layout = Layout()
    curve = add_straight_curve(layout)
    type_ = add_type(layout)
    frame = type_.new_frame("survey").ts(0.25)
    object_ = layout.new_object(
        "Q1",
        type=type_,
        position=Position(curve).ts(3.0),
    )

    curve_ref = layout.reference("curve:main")
    object_ref = layout.reference("Q1->survey")

    assert curve_ref.curve is curve
    assert curve_ref.curve_name == "main"
    assert object_ref.object is object_
    assert object_ref.object_name == "Q1"
    assert object_ref.frame is frame
    assert object_ref.frame_name == "survey"
    assert isinstance(layout.reference("world"), WorldReference)

    with pytest.raises(UnknownEntityError):
        layout.reference("curve:missing")
    with pytest.raises(UnknownEntityError):
        layout.reference("missing->center")


def test_instance_bound_references_follow_root_and_frame_renames():
    layout = Layout()
    curve = add_straight_curve(layout)
    type_ = add_type(layout)
    tail = type_.new_frame("tail").ts(1.0)
    first = layout.new_object(
        "Q1",
        type=type_,
        position=Position(curve).ts(2.0),
    )
    second = layout.new_object(
        "Q2",
        type=type_,
        position=Position(first.ref(tail), target="center"),
    )

    assert layout.rename(curve, "beam") is curve
    assert layout.rename(type_, "renamed_type") is type_
    assert layout.rename(first, "A") is first
    assert type_.rename_frame(tail, "exit") is tail

    document = layout.to_dict()
    assert second.type is type_
    assert second.type_name == "renamed_type"
    assert document["objects"]["A"]["position"]["reference"] == {
        "kind": "curve",
        "curve": "beam",
    }
    assert document["objects"]["Q2"]["position"]["reference"] == {
        "kind": "object_frame",
        "object": "A",
        "frame": "exit",
    }


def test_symbolic_references_also_serialize_after_rename():
    layout = Layout()
    curve = add_straight_curve(layout)
    type_ = add_type(layout)
    object_ = layout.new_object(
        "Q1",
        type="magnet",
        position=Position("curve:main").ts(2.0),
    )
    follower = layout.new_object(
        "Q2",
        type="magnet",
        position=Position("Q1->center"),
    )

    layout.rename(curve, "beam")
    layout.rename(type_, "kind")
    layout.rename(object_, "A")

    assert follower.type_name == "kind"
    document = layout.to_dict()
    assert document["objects"]["A"]["position"]["reference"]["curve"] == "beam"
    assert document["objects"]["Q2"]["position"]["reference"]["object"] == "A"


def test_removal_of_referenced_entities_or_frames_is_rejected():
    layout = Layout()
    curve = add_straight_curve(layout)
    type_ = add_type(layout)
    exit_frame = type_.new_frame("exit").ts(1.0)
    first = layout.new_object(
        "Q1",
        type=type_,
        position=Position(curve).ts(2.0),
    )
    layout.new_object(
        "Q2",
        type=type_,
        position=Position(first.ref(exit_frame)),
    )

    with pytest.raises(ReferenceInUseError):
        layout.pop("main", kind="curve")
    with pytest.raises(ReferenceInUseError):
        layout.pop("magnet", kind="type")
    with pytest.raises(ReferenceInUseError):
        layout.pop("Q1", kind="object")
    with pytest.raises(ReferenceInUseError):
        type_.pop_frame("exit")


def test_foreign_layout_instances_are_rejected():
    left = Layout()
    foreign_curve = add_straight_curve(left)

    right = Layout()
    type_ = add_type(right)
    with pytest.raises((ForeignLayoutError, AttachmentError)):
        right.new_object(
            "Q1",
            type=type_,
            position=Position(foreign_curve).ts(1.0),
        )


def test_unqualified_lookup_requires_a_unique_root_name():
    layout = Layout()
    curve = add_straight_curve(layout, "same")
    type_ = add_type(layout, "same")
    object_ = layout.new_object(
        "same",
        type=type_,
        position=Position(curve).ts(1.0),
    )

    with pytest.raises(AmbiguousNameError):
        layout["same"]

    assert layout["curve", "same"] is curve
    assert layout["type", "same"] is type_
    assert layout["object", "same"] is object_
    with pytest.raises(UnknownEntityError):
        layout["missing"]


def test_search_supports_kinds_compiled_regex_and_stored_frames_only():
    layout = Layout()
    main = add_straight_curve(layout, "main")
    spare = add_straight_curve(layout, "spare")
    magnet = add_type(layout, "magnet")
    survey = magnet.new_frame("survey_mark").tx(0.1)
    q1 = layout.new_object(
        "Q1",
        type=magnet,
        position=Position(main).ts(1.0),
    )

    assert layout.search(re.compile(r"^ma"), kind=("curve", "type")) == [main, magnet]
    assert layout.search(r"^Q", kind="object") == [q1]
    assert layout.search(r"survey_mark", kind="frame") == [survey]
    assert layout.search(r"^center$", kind="frame") == []
    assert layout.search(r"spare", kind="curve") == [spare]
