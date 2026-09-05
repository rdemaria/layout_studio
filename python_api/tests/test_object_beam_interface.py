from __future__ import annotations

import copy

import numpy as np
import pytest

from layout_studio import (
    AttachmentError, Box, Frame, Layout, Object, Position,
    ReferenceInUseError, Type, UnknownEntityError, ValidationError,
)
from layout_studio.webviewer import _selection_catalog


def example():
    layout = Layout()
    type_ = layout.new_type(
        "magnet", color="#112233",
        shape=Box(1, 1, 4, curvature=-0.25, roll=0.4),
        magnetic_center=Frame().tx(0.2).ts(0.3).rs(0.1),
        magnetic_length=2, magnetic_curvature=0.7, magnetic_roll=-0.2,
    )
    first = layout.new_object("A", type_, Position("world").tx(3).ry(0.2))
    second = layout.new_object("B", type_, Position("world").tt(5))
    return layout, type_, first, second


def test_inheritance_matches_all_magnetic_poses_and_follows_live_edits():
    layout, type_, first, _ = example()
    for changed in (False, True):
        if changed:
            type_.set_magnetic_axis(length=3, curvature=-0.4, roll=0.8)
            type_.magnetic_center.ty(0.6)
        for suffix in ("center", "entry", "exit"):
            np.testing.assert_allclose(first.get_frame(f"beam_{suffix}").matrix,
                                       first.get_frame(f"magnetic_{suffix}").matrix,
                                       rtol=0, atol=1e-13)
        assert first.beam_center is None
        assert first.effective_beam_axis[0] is type_.magnetic_center
        assert not any(key.startswith("beam_") for key in first.to_dict())
        assert "beam_exit" in _selection_catalog(layout.to_dict()).object_frames["A"]
    with pytest.raises(UnknownEntityError):
        type_.get_frame("beam_entry")


def test_override_is_owned_by_one_object_and_reset_restores_inheritance():
    layout, type_, first, second = example()
    first.set_beam_axis(length=1.25)
    assert first.beam_center is not type_.magnetic_center
    assert first.beam_center.owner is first
    assert type_.magnetic_center.owner is type_
    assert first.beam_center.to_dict() == type_.magnetic_center.to_dict()
    assert first.beam_curvature == type_.magnetic_curvature
    before = first.get_frame("beam_exit").matrix.copy()
    type_.set_magnetic_axis(length=4, roll=1)
    np.testing.assert_allclose(first.get_frame("beam_exit").matrix, before)
    assert second.beam_length is None
    assert second.effective_beam_axis[1] == 4
    old_center = first.beam_center
    first.remove_beam_axis()
    assert old_center.owner is None
    np.testing.assert_allclose(first.get_frame("beam_exit").matrix,
                               first.get_frame("magnetic_exit").matrix)
    layout.validate()


def test_distinct_object_beam_targets_align_without_changing_the_shared_type():
    layout, type_, first, second = example()
    first.set_beam_axis(center=Frame().tx(0.8).ts(0.4), length=3, curvature=0.2, roll=0.6)
    second.set_beam_axis(center=Frame().ty(-0.3), length=1, curvature=-0.3, roll=-0.5)
    second.position = Position(first.ref("beam_exit"), target="beam_entry")
    layout.validate()
    np.testing.assert_allclose(second.get_frame("beam_entry").matrix,
                               first.get_frame("beam_exit").matrix, rtol=0, atol=1e-12)
    assert first.type is second.type is type_
    assert not any(key.startswith("beam_") for key in type_.to_dict())


def test_round_trip_and_clone_keep_explicit_and_inherited_interfaces_distinct():
    layout, _, first, second = example()
    first.set_beam_axis(center=Frame().ty(0.3), length=1.5, curvature=0, roll=0)
    document = layout.to_dict()
    restored = Layout.from_dict(document)
    assert restored.to_dict() == document
    assert restored.objects["A"].beam_center.owner is restored.objects["A"]
    assert restored.objects["B"].beam_center is None
    clone = first.clone()
    assert clone.beam_center is not first.beam_center
    assert clone.beam_center.owner is clone
    assert second.clone().beam_center is None
    for name in ("A", "B"):
        np.testing.assert_allclose(restored.objects[name].get_frame("beam_exit").matrix,
                                   layout.objects[name].get_frame("beam_exit").matrix)


def test_object_without_magnet_can_supply_a_beam_interface():
    layout = Layout()
    type_ = layout.new_type("bare", color="#112233")
    first = layout.new_object("A", type_, Position("world"))
    assert first.effective_beam_axis is None
    assert first.implicit_frames == frozenset({"center"})
    with pytest.raises(UnknownEntityError):
        first.get_frame("beam_center")
    first.set_beam_axis(center=Frame().ts(0.5), length=2)
    np.testing.assert_allclose(first.get_frame("beam_entry").origin, [0, 0, -0.5])
    first.remove_beam_axis()
    assert first.implicit_frames == frozenset({"center"})


def test_removing_magnetic_axis_protects_inherited_beam_references_only():
    layout, type_, first, second = example()
    second.position = Position(first.ref("beam_exit"))
    before = type_.to_dict()
    with pytest.raises(ReferenceInUseError):
        type_.remove_magnetic_axis()
    assert type_.to_dict() == before
    first.set_beam_axis()  # now the referenced interface survives type removal
    type_.remove_magnetic_axis()
    layout.validate()
    assert "beam_exit" in first.implicit_frames
    assert "beam_exit" not in second.implicit_frames
    assert second.effective_beam_axis is None


def test_removing_override_keeps_existing_references_when_fallback_exists():
    layout, _, first, second = example()
    first.set_beam_axis(length=1)
    second.position = Position(first.ref("beam_exit"))
    first.remove_beam_axis()
    layout.validate()
    np.testing.assert_allclose(second.get_frame().matrix,
                               first.get_frame("magnetic_exit").matrix)


def test_object_beam_edits_are_atomic_and_protect_last_frame():
    layout, type_, first, second = example()
    first.set_beam_axis(length=1)
    original = first.to_dict()
    old_center = first.beam_center
    new_center, new_position = Frame().tx(2), Position("world").tx(1)
    with pytest.raises(ValidationError):
        first.set(position=new_position, beam_center=new_center, beam_length=0)
    assert first.to_dict() == original
    assert new_center.owner is None and new_position.owner is None
    assert old_center.owner is first
    with pytest.raises(ValidationError):
        first.set(beam_center=None)
    assert first.beam_center is old_center
    second.position = Position(first.ref("beam_entry"))
    type_.remove_magnetic_axis()
    with pytest.raises(ReferenceInUseError):
        first.remove_beam_axis()
    assert old_center.owner is first


def test_rejects_type_beam_fields_partial_groups_and_nonlocal_centers():
    layout, _, first, _ = example()
    first.set_beam_axis(length=1)
    doc = layout.to_dict()
    legacy = copy.deepcopy(doc)
    for key in ("beam_center", "beam_length", "beam_curvature", "beam_roll"):
        legacy["types"]["magnet"][key] = legacy["objects"]["A"].pop(key)
    with pytest.raises(ValidationError, match="unsupported"):
        Layout.from_dict(legacy)
    with pytest.raises(TypeError):
        Type(color="#112233", beam_length=1)
    for key in ("beam_center", "beam_length", "beam_curvature", "beam_roll"):
        partial = copy.deepcopy(doc)
        del partial["objects"]["A"][key]
        with pytest.raises(ValidationError, match="all present"):
            Layout.from_dict(partial)
    position = Position("world")
    with pytest.raises(ValidationError):
        Object(type="magnet", position=position, beam_center=Frame("world"),
               beam_length=1, beam_curvature=0, beam_roll=0)
    assert position.owner is None
    with pytest.raises(AttachmentError):
        Object(type="magnet", position=position, beam_center=first.beam_center,
               beam_length=1, beam_curvature=0, beam_roll=0)
    assert position.owner is None
