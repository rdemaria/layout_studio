from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

CONVERSION_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CONVERSION_DIR))

import ldb_machine_to_layout as converter
from ldbpoint import LDBPath


class FakeMachine:
    def __init__(self, *, name: str, transformations: dict[str, object]):
        self.name = name
        self.version = "LS3"
        self.transformations = transformations
        # Reproduce the stale eager cache in the supplied Machine class.
        self.ref_curve = LDBPath([(26658.8832, 0.0, 0.0)])
        self.requested_lengths: list[float] = []

    def get_ref_curve(self, machine_length: float = 26658.8832, start=None):
        self.requested_lengths.append(float(machine_length))
        return LDBPath([(float(machine_length), 0.0, 0.0)], start=start)


def transformation(
    target: str,
    *,
    ref: str,
    tx: float,
    length: float,
    target_point: str = "MECHANICAL START",
    target_type: str = "TEST",
) -> object:
    return SimpleNamespace(
        target=target,
        target_type=target_type,
        target_point=target_point,
        ref=ref,
        ref_point="MECHANICAL START",
        tx=tx,
        ty=0.0,
        tz=0.0,
        rx=0.0,
        ry=0.0,
        rz=0.0,
        order=["tx", "ty", "tz", "rx", "ry", "rz"],
        length=length,
        optic_length=length,
        optic_offset=0.0,
        angle=0.0,
        roll=0.0,
    )


def test_transfer_line_alias_is_root_and_cached_lhc_length_is_ignored():
    transformations = {
        "A": transformation("A", ref="M2-LINE", tx=0.0, length=10.0),
        "B": transformation(
            "B",
            ref="M2-LINE",
            tx=11.0,
            length=2.0,
            target_point="MECHANICAL MIDDLE",
        ),
        "A.child": transformation("A.child", ref="A", tx=1.0, length=0.5),
    }
    machine = FakeMachine(name="M2", transformations=transformations)

    result = converter.machine_to_layout(machine)

    assert result.report.root_name == "M2-LINE"
    assert result.report.root_name_source == "machine-name alias"
    assert result.report.machine_length == pytest.approx(12.0)
    assert result.report.output_objects == 3
    assert result.report.skipped_objects == {}
    assert machine.requested_lengths == [pytest.approx(12.0)]
    assert set(result.layout["objects"]) == set(transformations)
    for type_ in result.layout["types"].values():
        assert type_["magnetic_curvature"] == pytest.approx(type_["shape"][4])
        assert type_["magnetic_roll"] == pytest.approx(type_["shape"][5])


def test_only_unrelated_missing_reference_branch_is_skipped():
    transformations = {
        "A": transformation("A", ref="M2-LINE", tx=0.0, length=10.0),
        "B": transformation("B", ref="M2-LINE", tx=10.0, length=1.0),
        "orphan": transformation("orphan", ref="MISSING-ASSET", tx=0.0, length=1.0),
        "orphan.child": transformation(
            "orphan.child", ref="orphan", tx=1.0, length=1.0
        ),
    }
    machine = FakeMachine(name="M2", transformations=transformations)

    result = converter.machine_to_layout(machine)

    assert set(result.layout["objects"]) == {"A", "B"}
    assert result.report.skipped_objects == {
        "orphan": "references missing LDB object 'MISSING-ASSET'",
        "orphan.child": "depends on skipped object 'orphan'",
    }


def test_explicit_root_override_is_validated():
    machine = FakeMachine(
        name="UNKNOWN",
        transformations={
            "A": transformation("A", ref="BEAM-LINE", tx=0.0, length=3.0),
        },
    )

    result = converter.machine_to_layout(machine, root_name="BEAM-LINE")
    assert result.report.root_name == "BEAM-LINE"
    assert result.report.root_name_source == "explicit argument"

    with pytest.raises(converter.ConversionError, match="not an external reference"):
        converter.machine_to_layout(machine, root_name="WRONG")
