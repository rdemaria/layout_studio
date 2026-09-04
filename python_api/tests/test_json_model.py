from __future__ import annotations

import gzip
import json
import math

import pytest

from layout_studio import (
    AttachmentError,
    Box,
    Curve,
    Cylinder,
    Frame,
    Layout,
    Position,
    Segment,
    Type,
    ValidationError,
)


def test_canonical_dict_and_json_round_trip(canonical_layout_dict):
    layout = Layout.from_dict(canonical_layout_dict)

    assert layout.to_dict() == canonical_layout_dict
    assert (
        Layout.from_json(text=layout.to_json(indent=None)).to_dict()
        == canonical_layout_dict
    )
    assert (
        Layout.from_json(text=layout.to_json(str).encode()).to_dict()
        == canonical_layout_dict
    )


def test_inert_reference_curve_is_preserved_in_json(canonical_layout_dict):
    position = canonical_layout_dict["objects"]["Q1"]["position"]
    position["reference"] = {"kind": "world"}
    position["reference_curve"] = "main"
    position["transformation"] = []

    layout = Layout.from_dict(canonical_layout_dict)
    layout.validate()

    serialized = layout.to_dict()
    assert serialized["objects"]["Q1"]["position"]["reference_curve"] == "main"


def test_json_local_paths_and_gzip(tmp_path, canonical_layout_dict):
    layout = Layout.from_dict(canonical_layout_dict)
    filename = tmp_path / "layout.json"
    compressed_filename = tmp_path / "layout.json.gz"

    assert layout.to_json(filename_or_url=filename, indent=None) is None
    assert Layout.from_json(filename_or_url=filename).to_dict() == canonical_layout_dict

    assert layout.to_json(filename_or_url=compressed_filename, indent=None) is None
    assert gzip.decompress(compressed_filename.read_bytes()) == filename.read_bytes()
    assert (
        Layout.from_json(filename_or_url=compressed_filename).to_dict()
        == canonical_layout_dict
    )


def test_from_json_requires_exactly_one_source(canonical_layout_dict):
    text = json.dumps(canonical_layout_dict)

    with pytest.raises(TypeError, match="exactly one"):
        Layout.from_json()
    with pytest.raises(TypeError, match="exactly one"):
        Layout.from_json(filename_or_url="layout.json", text=text)


def test_python_shortcuts_serialize_to_canonical_json():
    layout = Layout()
    layout.new_curve(
        "main",
        starting_frame=Frame("world").tx(1.0),
        color="#112233",
        segments=[Segment(2.0)],
    )
    type_ = layout.new_type(
        "kind",
        shape=Box(1.0, 2.0, 3.0),
        color="#abcdef",
        magnetic_center=Frame(),
        magnetic_length=1.0,
    )
    layout.new_object(
        "thing",
        type=type_,
        position=Position("curve:main").ts(0.5),
    )

    document = layout.to_dict()
    assert set(document) == {"reference_curves", "types", "objects"}
    assert document["reference_curves"]["main"]["starting_frame"] == {
        "reference": {"kind": "world"},
        "transformation": [["tx", 1.0]],
    }
    assert document["objects"]["thing"]["position"] == {
        "target": "center",
        "reference": {"kind": "curve", "curve": "main"},
        "transformation": [["ts", 0.5]],
    }


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(extra=True),
        lambda d: d.pop("objects"),
        lambda d: d["reference_curves"]["main"].update(extra=True),
        lambda d: d["reference_curves"]["main"].pop("color"),
        lambda d: d["reference_curves"]["main"].update(segments=[[1.0, 0.0]]),
        lambda d: d["types"]["magnet"].update(shape=["box", 1.0, 2.0, 3.0]),
        lambda d: d["types"]["magnet"]["frames"]["survey"].update(
            reference={"kind": "world"}
        ),
        lambda d: d["objects"]["Q1"]["position"].pop("target"),
        lambda d: d["objects"]["Q1"]["position"]["reference"].update(extra=True),
        lambda d: d["objects"]["Q1"]["position"].update(transformation=[["tz", 1.0]]),
    ],
    ids=[
        "top-level-extra",
        "top-level-missing",
        "curve-extra",
        "curve-missing",
        "segment-arity",
        "shape-arity",
        "local-frame-reference",
        "position-missing-target",
        "reference-extra",
        "unknown-operation",
    ],
)
def test_canonical_reader_is_strict(canonical_copy, mutate):
    document = canonical_copy()
    mutate(document)

    with pytest.raises((ValidationError, TypeError, ValueError)):
        Layout.from_dict(document)


@pytest.mark.parametrize(
    "factory",
    [
        lambda: Segment(0.0),
        lambda: Segment(-1.0),
        lambda: Box(0.0, 1.0, 1.0),
        lambda: Cylinder(1.0, -1.0),
        lambda: Frame().tx(math.nan),
        lambda: Frame().rs(math.inf),
        lambda: Type(
            shape=Box(1.0, 1.0, 1.0),
            color="#112233",
            magnetic_center=Frame(),
            magnetic_length=0.0,
        ),
        lambda: Curve(
            starting_frame=Frame("world"),
            color="red",
            segments=[Segment(1.0)],
        ),
    ],
    ids=[
        "zero-segment",
        "negative-segment",
        "zero-shape-dimension",
        "negative-shape-dimension",
        "nan-operation",
        "infinite-operation",
        "zero-magnetic-length",
        "noncanonical-color",
    ],
)
def test_local_constraints_are_checked_immediately(factory):
    with pytest.raises((ValidationError, TypeError, ValueError)):
        factory()


def test_root_adoption_preserves_identity_and_clone_detaches_subtree():
    starting_frame = Frame("world").tx(1.0)
    curve = Curve(
        starting_frame=starting_frame,
        color="#112233",
        segments=[Segment(2.0)],
    )
    assert curve.owner is None
    assert curve.layout is None
    assert curve.name is None
    assert starting_frame.owner is curve

    layout = Layout()
    assert layout.add_curve("main", curve) is curve
    assert curve.name == "main"
    assert curve.owner is layout
    assert curve.layout is layout
    assert curve.is_owned and curve.is_bound
    assert starting_frame.layout is layout

    clone = curve.clone()
    assert clone is not curve
    assert clone.owner is None
    assert clone.layout is None
    assert clone.name is None
    assert clone.starting_frame is not starting_frame
    assert clone.starting_frame.owner is clone
    assert clone.to_dict() == curve.to_dict()

    assert layout.add_curve("copy", clone) is clone
    assert clone.layout is layout


def test_owned_values_cannot_be_reused_without_clone():
    frame = Frame("world")
    first = Curve(
        starting_frame=frame,
        color="#112233",
        segments=[Segment(1.0)],
    )

    with pytest.raises(AttachmentError):
        Curve(
            starting_frame=frame,
            color="#445566",
            segments=[Segment(1.0)],
        )

    layout = Layout()
    layout.add_curve("first", first)
    with pytest.raises(AttachmentError):
        Layout().add_curve("again", first)

    second = Curve(
        starting_frame=frame.clone(),
        color="#445566",
        segments=[Segment(1.0)],
    )
    assert second.starting_frame is not frame


def test_frame_as_position_adopts_the_same_detached_frame():
    frame = Frame("world").tx(0.25)
    position = frame.as_position(target="center")

    assert position.reference is frame
    assert frame.owner is position
    assert position.operations is frame.operations

    with pytest.raises(AttachmentError):
        frame.as_position()


def test_individual_entity_roundtrip_stays_detached():
    curve = Curve(
        starting_frame=Frame("world"),
        color="#112233",
        segments=[Segment(1.0, 0.2, 0.3)],
    )

    loaded = Curve.from_json(text=json.dumps(curve.to_dict()))
    assert loaded.to_dict() == curve.to_dict()
    assert loaded.name is None
    assert loaded.owner is None
    assert loaded.layout is None
