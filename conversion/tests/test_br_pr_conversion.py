from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "python_api" / "src"))

from conversion._ring_conversion import RingMachine
from conversion.br.convert import convert as convert_br
from conversion.pr.convert import convert as convert_pr
from conversion.ldb_machine_to_layout import ConversionError, load_machine_pickle, write_json
from conversion.validate import validate_conversion
from conversion.ldbpoint import LDBPath


def source_with_bends(bends):
    return SimpleNamespace(name="TEST", get_bends=lambda: bends,
                           ref_curve=LDBPath([(26658.8832, 0, 0)]))


def test_coincident_bends_are_merged_only_when_requested_without_mutating_source():
    bends = [(f"BR{ring}.B", 1, 3, 2, -0.2, 0.3) for ring in range(1, 5)]
    source = source_with_bends(bends)
    prepared = RingMachine(source, merge_coincident_bends=True)
    path = prepared.get_ref_curve(4)
    np.testing.assert_allclose(path.segments, [(1, 0, 0), (2, -0.2, -0.3), (1, 0, 0)])
    assert prepared.coincident_bends == {f"BR{ring}.B": "BR1.B" for ring in range(2, 5)}
    assert len(source.get_bends()) == 4
    assert source.ref_curve.dcum[-1] == 26658.8832
    with pytest.raises(ConversionError, match="overlap"):
        RingMachine(source).get_ref_curve(4)
    bends[-1] = ("DIFFERENT", 1, 3, 2, -0.3, 0.3)
    with pytest.raises(ConversionError, match="overlap"):
        prepared.get_ref_curve(4)


@pytest.mark.parametrize("gap", [0, -2e-14, 2e-14])
def test_adjacent_bends_drop_roundoff_drifts_and_keep_their_angles(gap):
    source = source_with_bends([("F", 0, 2, 2, 0.1, 0),
                                ("D", 2 + gap, 5 + gap, 3, 0.2, 0)])
    prepared = RingMachine(source)
    path = prepared.get_ref_curve(5)
    np.testing.assert_allclose(path.segments, [(2, 0.1, 0), (3, 0.2, 0)])
    assert prepared.omitted_zero_drifts == 3


@pytest.mark.parametrize("bend, circumference", [
    (("B", -0.01, 1.99, 2, 0.1, 0), 4),
    (("B", 1, 3, 2, 0.1, 0), 2.99),
    (("B", 1, 1, 0, 0.1, 0), 4),
])
def test_invalid_bend_geometry_is_not_hidden_by_drift_normalization(bend, circumference):
    with pytest.raises(ConversionError):
        RingMachine(source_with_bends([bend])).get_ref_curve(circumference)


@pytest.mark.parametrize("convert, name", [(convert_br, "PR"), (convert_pr, "BR")])
def test_machine_policy_rejects_the_wrong_snapshot(convert, name):
    with pytest.raises(ConversionError, match="policy requires"):
        convert(SimpleNamespace(name=name))


@pytest.mark.parametrize("name, convert, root, length, count, spans, segments, turn", [
    ("BR", convert_br, "PSB", 157.08, 2070, 16, 65, -1),
    ("PR", convert_pr, "PS", 628.3185, 4623, 310, 300, 1),
])
def test_ls3_snapshot_roundtrip_and_all_frames(tmp_path, name, convert, root, length,
                                              count, spans, segments, turn):
    directory = ROOT / "conversion" / name.lower()
    snapshot = directory / f"{name}--LS3.pickle"
    machine = load_machine_pickle(snapshot)
    original_bends = machine.get_bends()
    result = convert(machine)
    report = result.report
    assert report.root_name == root
    assert report.machine_length == pytest.approx(length)
    assert report.output_objects == count
    assert len(report.span_objects) == spans
    assert report.curve_segments == segments
    assert report.curve_total_angle == pytest.approx(turn * 2 * np.pi, abs=2e-10, rel=0)
    assert set(result.layout["objects"]) | set(report.skipped_objects) == set(machine.transformations)
    assert set(result.layout["objects"]).isdisjoint(report.skipped_objects)
    assert machine.get_bends() == original_bends
    for obj_name in report.span_objects:
        obj = result.layout["objects"][obj_name]
        assert "shape" not in result.layout["types"][obj["type"]]
    if name == "BR":
        assert len(report.coincident_bends) == 96
        assert all(f"BR{ring}.BHZ11" in result.layout["objects"] for ring in range(1, 5))
    else:
        assert report.omitted_zero_drifts == 101
    for obj_name in report.seam_reference_stations:
        position = result.layout["objects"][obj_name]["position"]
        assert position["reference"]["object"] == machine.transformations[obj_name].ref
        assert position["reference"]["frame"] == "anchor"
    output = tmp_path / f"{name}.json.gz"
    report_path = tmp_path / f"{name}.layout-report.json"
    write_json(output, result.layout, indent=None)
    write_json(report_path, asdict(report), indent=2)
    assert output.read_bytes() == (directory / f"{name}--LS3.json.gz").read_bytes()
    assert json.loads(report_path.read_text()) == json.loads((directory / f"{name}--LS3.layout-report.json").read_text())
    manifest = json.loads((directory / f"{name}--LS3.files.json").read_text())
    for filename, info in manifest["artifacts"].items():
        content = (directory / filename).read_bytes()
        assert len(content) == info["bytes"]
        assert hashlib.sha256(content).hexdigest() == info["sha256"]
    validation = validate_conversion(snapshot, output, report_path)
    assert validation["objects_resolved"] == count
    assert validation["span_frames_checked"] == 3 * spans
    assert validation["curve_frames_checked"] == segments + 1
