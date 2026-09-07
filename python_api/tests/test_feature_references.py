import json
from pathlib import Path

import numpy as np
import pytest

from layout_studio import (
    Box, ForeignLayoutError, Frame, Layout, LocalFrameReference, Position, ReferenceCycleError,
    ReferenceInUseError, ValidationError,
)

FIXTURE = Path(__file__).resolve().parents[2] / "specifications/examples/anchored-features.json"


def example():
    return Layout.from_dict(json.loads(FIXTURE.read_text()))


def test_chained_features_align_target_and_inherit_resolved_magnetic_frames():
    layout = example()
    a, c = layout.objects["A"], layout.objects["C"]
    np.testing.assert_allclose(a.get_frame().origin, [9, -2, 0], atol=1e-13)
    np.testing.assert_allclose(a.get_frame("mechanical_center").origin, [10, 0, 0], atol=1e-13)
    np.testing.assert_allclose(a.get_frame("magnetic_center").origin, [10, 0, .5], atol=1e-13)
    np.testing.assert_allclose(a.get_frame("survey_world").origin, [100, 0, 0], atol=1e-13)
    np.testing.assert_allclose(a.get_frame("survey_other").origin, [20, 3.2, 0], atol=1e-13)
    for suffix in ("center", "entry", "exit"):
        np.testing.assert_allclose(c.get_frame(f"beam_{suffix}").matrix,
                                   c.get_frame(f"magnetic_{suffix}").matrix, atol=1e-13)
    assert layout.to_dict() == json.loads(FIXTURE.read_text())


def test_mechanical_sweep_uses_its_placement_and_edits_invalidate_caches():
    from layout_studio.resolver import swept_object_mesh, swept_type_mesh
    layout = Layout()
    type_ = layout.new_type("t", color="#123456", shape=Box(1, 1, 2),
                            mechanical_center=Frame().tx(3))
    obj = layout.new_object("A", type_, Position("world").ty(2))
    np.testing.assert_allclose(swept_object_mesh(obj)["vertices"].mean(axis=0), [3, 2, 0], atol=1e-13)
    np.testing.assert_allclose(swept_type_mesh(type_)["vertices"].mean(axis=0), [3, 0, 0], atol=1e-13)
    type_.mechanical_center.tx(1)
    np.testing.assert_allclose(obj.get_frame().origin, [0, 2, 0], atol=1e-13)
    np.testing.assert_allclose(obj.get_frame("mechanical_center").origin, [4, 2, 0], atol=1e-13)


def test_local_reference_ownership_clone_rename_and_removal():
    layout = example()
    type_ = layout.types["magnet"]
    mount = type_.frames["mount"]
    type_.mechanical_center.reference = mount
    assert isinstance(type_.mechanical_center.reference, LocalFrameReference)
    clone = type_.clone()
    assert clone.mechanical_center.owner is clone
    assert clone.mechanical_center.reference.frame_name == "mount"
    type_.rename_frame("mount", "support")
    assert type_.mechanical_center.reference.frame_name == "support"
    assert layout.objects["C"].position.target_name == "support"
    with pytest.raises(ReferenceInUseError):
        type_.pop_frame("support")
    with pytest.raises(ReferenceInUseError):
        type_.remove_shape()
    layout.validate()


def test_legacy_center_is_only_an_import_alias():
    document = json.loads(FIXTURE.read_text())
    document["objects"]["B"]["position"]["target"] = "center"
    document["objects"]["A"]["beam_center"]["reference"]["frame"] = "center"
    layout = Layout.from_dict(document)
    assert layout.objects["B"].position.target_name == "anchor"
    assert layout.objects["A"].beam_center.to_dict()["reference"]["frame"] == "anchor"
    assert "center" not in layout.objects["A"].implicit_frames


def test_precise_dependencies_allow_independently_placed_external_features():
    layout = example()
    layout.objects["B"].position.reference = layout.objects["A"].ref("survey_world")
    layout.validate()
    np.testing.assert_allclose(layout.objects["B"].get_frame().origin, [100, 0, 0])
    layout.objects["A"].get_frame("beam_exit")


@pytest.mark.parametrize("target", ["survey_world", "survey_curve", "survey_other", "beam_center"])
def test_external_target_cannot_determine_its_anchor(target):
    layout = example()
    layout.objects["A"].position.target = target
    with pytest.raises(ValidationError, match="own anchor"):
        layout.validate()


def test_local_and_inherited_alias_cycles_are_rejected():
    for inherited in (False, True):
        layout = example()
        if inherited:
            layout.types["magnet"].magnetic_center.reference = "local:beam_center"
        else:
            layout.types["magnet"].frames["mount"].reference = "local:mechanical_center"
        with pytest.raises(ReferenceCycleError):
            layout.validate()


def test_foreign_feature_links_are_rejected_before_attaching_or_replacing_frames():
    layout = example()
    foreign = example().objects["B"]
    type_, obj = layout.types["magnet"], layout.objects["A"]
    before = layout.to_dict()
    for attach in (
        lambda frame: type_.new_frame("bad", frame),
        lambda frame: type_.set(mechanical_center=frame),
        lambda frame: obj.set_beam_axis(center=frame),
    ):
        candidate = Frame(foreign.ref())
        with pytest.raises(ForeignLayoutError):
            attach(candidate)
        assert candidate.owner is None
        assert layout.to_dict() == before
