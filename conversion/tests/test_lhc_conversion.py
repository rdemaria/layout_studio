from pathlib import Path
import sys

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "python_api" / "src"))

from conversion.lhc.convert import ConversionError, convert
from ldbpoint import LDBPath
from layout_studio import Layout, Resolver
from test_ldb_machine_to_layout import FakeMachine, transformation


def unknown_length(name="UNKNOWN"):
    tf = transformation(name, ref="LHC", tx=2, length=None,
                        target_point="MECHANICAL MIDDLE")
    tf.optic_length = 0
    return tf


def test_unknown_length_keeps_anchor_without_changing_known_type_or_source():
    unknown = unknown_length()
    known = transformation("KNOWN", ref="LHC", tx=5, length=1)
    machine = FakeMachine(name="LHC", transformations={"UNKNOWN": unknown, "KNOWN": known})
    result = convert(machine, machine_length=10)
    layout = Layout.from_dict(result.layout)
    obj = layout.objects["UNKNOWN"]
    assert obj.type.shape is None
    assert "mechanical_start" not in obj.type.frames
    assert "mechanical_end" not in obj.type.frames
    np.testing.assert_allclose(obj.get_frame("anchor").origin, [0, 0, 2])
    assert layout.objects["KNOWN"].type.shape is not None
    assert result.report.unavailable_mechanical_lengths == ["UNKNOWN"]
    assert result.report.zero_mechanical_length_objects == 0
    assert unknown.length is None and unknown.target_type == "TEST"


def test_unknown_length_rejects_placement_or_reference_requiring_an_endpoint():
    unknown = unknown_length()
    unknown.target_point = "MECHANICAL START"
    machine = FakeMachine(name="LHC", transformations={"UNKNOWN": unknown})
    with pytest.raises(ConversionError, match="cannot place"):
        convert(machine, machine_length=10)
    unknown.target_point = "MECHANICAL MIDDLE"
    machine.transformations["CHILD"] = transformation("CHILD", ref="UNKNOWN", tx=1, length=1)
    with pytest.raises(ConversionError, match="unknown mechanical endpoint"):
        convert(machine, machine_length=10)


class RingMachine(FakeMachine):
    def get_ref_curve(self, machine_length=100, start=None):
        return LDBPath([(machine_length, 2 * np.pi, 0)], start=start)


@pytest.mark.parametrize("reference_point, offset, station", [
    ("MECHANICAL START", 10, 10),
    ("MECHANICAL END", -10, 90),
])
def test_ring_seam_uses_source_station_without_inverse_ambiguity(reference_point, offset, station):
    sector = transformation("SECTOR", ref="LHC", tx=0, length=100, target_type="LHC SECTOR")
    child = transformation("CHILD", ref="SECTOR", tx=offset, length=1,
                           target_point="MECHANICAL MIDDLE")
    child.ref_point = reference_point
    machine = RingMachine(name="LHC", transformations={"SECTOR": sector, "CHILD": child})
    result = convert(machine)
    assert result.layout["objects"]["CHILD"]["position"]["reference"] == {"kind": "curve", "curve": "LHC"}
    assert result.report.seam_reference_stations["CHILD"] == (0 if offset > 0 else 100)
    assert child.ref == "SECTOR"
    with Resolver(Layout.from_dict(result.layout)) as resolver:
        np.testing.assert_allclose(resolver.object_frame("CHILD", "anchor").matrix,
                                   resolver.curve_frame("LHC", station).matrix, atol=1e-12)
