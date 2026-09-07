from __future__ import annotations

import gzip
import json
from pathlib import Path
import sys

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "conversion"))
sys.path.insert(0, str(ROOT / "python_api" / "src"))

from ldb_machine_to_layout import ConversionError, convert_ldb_operations, machine_to_layout, write_json
from ldbpoint import LDBPath, LDBPoint
from layout_studio import Layout, Resolver
from test_ldb_machine_to_layout import FakeMachine, transformation


@pytest.mark.parametrize("operation", ["tx", "ty", "tz", "rx", "ry", "rz", "all"])
def test_axis_permutation_and_rotation_signs_match_ldbpoint(operation):
    tf = transformation("Q", ref="TEST", tx=0, length=0, target_point="MECHANICAL MIDDLE")
    values = {"tx": 2.5, "ty": -0.8, "tz": 1.2, "rx": 17, "ry": -11, "rz": 23}
    source = LDBPoint()
    for name, value in values.items():
        if operation in (name, "all"):
            setattr(tf, name, value)
            getattr(source, name)(value)
    result = machine_to_layout(FakeMachine(name="TEST", transformations={"Q": tf}), machine_length=100)
    actual = Layout.from_dict(result.layout).objects["Q"].get_frame().matrix
    np.testing.assert_allclose(actual, source.to_madpoint().matrix, atol=1e-13, rtol=0)
    assert all(name != "tt" for name, _ in convert_ldb_operations(tf))


def test_order_is_preserved_when_tx_follows_rotations():
    tf = transformation("Q", ref="TEST", tx=2, length=1)
    tf.order = ["rz", "ry", "rx", "tz", "ty", "tx"]
    tf.rz, tf.rx, tf.ty = 10, 15, 0.3
    assert [name for name, _ in convert_ldb_operations(tf)] == ["ry", "rs", "tx", "ts"]


class CurvedMachine(FakeMachine):
    def get_ref_curve(self, machine_length=20, start=None):
        return LDBPath([(5, 0, 0), (6, 0.4, -0.7), (4, 0, 0), (5, -0.2, 1)], start=start)


def test_nested_spans_follow_piecewise_curve_instead_of_straight_lengths():
    outer = transformation("SEXTANT", ref="RING", tx=0, length=20, target_type="SPAN")
    inner = transformation("CELL", ref="SEXTANT", tx=5, length=7, target_type="CELL")
    marker = transformation("Q", ref="CELL", tx=2, length=1, target_point="MECHANICAL MIDDLE")
    machine = CurvedMachine(name="RING", transformations={"SEXTANT": outer, "CELL": inner, "Q": marker})
    machine.ref_curve.start = LDBPoint(x=1, y=2, z=3, rx=8, ry=-4, rz=7)
    result = machine_to_layout(machine, machine_length=20, span_types={"SPAN", "CELL"})
    layout = Layout.from_dict(result.layout)
    source = machine.get_ref_curve(start=machine.ref_curve.start)
    with Resolver(layout) as resolver:
        for name, stations in {"SEXTANT": (0, 10, 20), "CELL": (5, 8.5, 12)}.items():
            assert layout.objects[name].type.shape is None
            assert layout.objects[name].type.magnetic_center is None
            for frame, station in zip(("mechanical_start", "center", "mechanical_end"), stations):
                np.testing.assert_allclose(resolver.object_frame(name, frame).matrix,
                                           source.get_point(station).to_madpoint().matrix,
                                           atol=1e-11, rtol=0)
        np.testing.assert_allclose(resolver.object_frame("Q").matrix,
                                   source.get_point(7).to_madpoint().matrix, atol=1e-11, rtol=0)


def test_bending_magnet_mechanical_length_is_straight_unless_policy_overrides_it():
    tf = transformation("B", ref="RING", tx=10, length=4, target_point="MECHANICAL MIDDLE")
    tf.optic_length, tf.angle, tf.roll = 4.2, 0.3, 0.7
    machine = FakeMachine(name="RING", transformations={"B": tf})
    for curved in (False, True):
        result = machine_to_layout(machine, curved_types={"TEST"} if curved else ())
        obj = Layout.from_dict(result.layout).objects["B"]
        assert obj.type.shape.curvature == pytest.approx(tf.angle / tf.optic_length if curved else 0)
        assert obj.type.magnetic_curvature == pytest.approx(tf.angle / tf.optic_length)
        np.testing.assert_allclose(obj.get_frame("beam_exit").matrix,
                                   obj.get_frame("magnetic_exit").matrix)
        if not curved:
            start, end = obj.get_frame("mechanical_start"), obj.get_frame("mechanical_end")
            assert np.linalg.norm(end.origin - start.origin) == pytest.approx(4)
            np.testing.assert_allclose(start.matrix[:3, :3], end.matrix[:3, :3])


def test_optic_center_overrides_belong_to_objects_without_splitting_magnetic_types():
    first = transformation("Q1", ref="LINE", tx=2, length=1, target_point="OPTIC MIDDLE")
    second = transformation("Q2", ref="LINE", tx=4, length=1, target_point="OPTIC MIDDLE")
    first.optic_offset, second.optic_offset = 0.1, 0.2
    first.mec_long_offset = second.mec_long_offset = 0
    first.mec_radial_offset = second.mec_radial_offset = 0.03
    result = machine_to_layout(FakeMachine(name="LINE", transformations={"Q1": first, "Q2": second}))
    assert result.report.explicit_beam_interfaces == 2
    layout = Layout.from_dict(result.layout)
    assert layout.objects["Q1"].type is layout.objects["Q2"].type
    for name, station, offset in (("Q1", 2, 0.1), ("Q2", 4, 0.2)):
        obj = layout.objects[name]
        assert obj.beam_center is not None
        np.testing.assert_allclose(obj.get_frame("beam_center").origin, [0, 0, station], atol=1e-13)
        np.testing.assert_allclose(obj.get_frame("magnetic_center").origin, [-0.03, 0, station-offset], atol=1e-13)
        assert not any(field.startswith("beam_") for field in obj.type.to_dict())


def test_zero_optic_length_keeps_exact_anchor_without_artificial_axis():
    tf = transformation("Q", ref="LINE", tx=2, length=0, target_point="OPTIC END")
    tf.optic_offset = 0.1
    tf.mec_long_offset = 0.3
    result = machine_to_layout(FakeMachine(name="LINE", transformations={"Q": tf}), machine_length=10)
    obj = Layout.from_dict(result.layout).objects["Q"]
    assert obj.effective_beam_axis is None
    assert obj.type.magnetic_center is None
    for name in ("optic_start", "optic_center", "optic_end"):
        np.testing.assert_allclose(obj.get_frame(name).origin, [0, 0, 2], atol=1e-13)


def test_span_policy_rejects_offset_ancestors_and_conflicting_geometry():
    tf = transformation("S", ref="LINE", tx=0, length=10, target_type="SPAN")
    tf.ty = 0.1
    machine = FakeMachine(name="LINE", transformations={"S": tf})
    with pytest.raises(ConversionError, match="purely longitudinal"):
        machine_to_layout(machine, span_types={"SPAN"})
    with pytest.raises(ConversionError, match="both a span and curved"):
        machine_to_layout(machine, span_types={"SPAN"}, curved_types={"SPAN"})


def test_gzip_output_is_reproducible_and_contains_json(tmp_path):
    first, second = tmp_path / "one.json.gz", tmp_path / "two.json.gz"
    value = {"label": "SPS", "values": [1, 2, 3]}
    write_json(first, value, indent=None)
    write_json(second, value, indent=None)
    assert first.read_bytes() == second.read_bytes()
    assert json.loads(gzip.decompress(first.read_bytes())) == value
