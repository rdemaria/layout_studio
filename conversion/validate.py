#!/usr/bin/env python3
"""Validate a conversion using the Python API and the original LDB reference path."""
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
import sys

import numpy as np

try:
    from .ldb_machine_to_layout import load_machine_pickle
except ImportError:
    from ldb_machine_to_layout import load_machine_pickle

# Use this checkout's matching positioning model when it is available.
api_source = Path(__file__).resolve().parents[1] / "python_api" / "src"
if api_source.is_dir():
    sys.path.insert(0, str(api_source))
from layout_studio import Layout, Resolver


def validate_conversion(input_path: Path, output_path: Path, report_path: Path) -> dict:
    machine = load_machine_pickle(input_path)
    report = json.loads(report_path.read_text())
    data = output_path.read_bytes()
    if output_path.suffix == ".gz":
        data = gzip.decompress(data)
    layout = Layout.from_dict(json.loads(data))
    layout.validate()
    source = machine.get_ref_curve(machine_length=report["machine_length"],
                                   start=machine.ref_curve.start)
    curve_name = report["curve_name"]
    result = {"objects_resolved": 0, "frames_resolved": 0,
              "curve_frames_checked": 0, "span_frames_checked": 0,
              "max_curve_position_error_m": 0.0, "max_curve_rotation_error": 0.0,
              "max_span_position_error_m": 0.0, "max_span_rotation_error": 0.0}

    def compare(actual, expected, feature):
        position_error = float(np.max(np.abs(actual[:3, 3] - expected[:3, 3])))
        rotation_error = float(np.max(np.abs(actual[:3, :3] - expected[:3, :3])))
        result[f"max_{feature}_position_error_m"] = max(result[f"max_{feature}_position_error_m"], position_error)
        result[f"max_{feature}_rotation_error"] = max(result[f"max_{feature}_rotation_error"], rotation_error)
        if position_error > 1e-8 or rotation_error > 1e-10:
            raise ValueError(f"{feature} frame mismatch: {position_error:g} m, {rotation_error:g} rotation")

    with Resolver(layout) as resolver:
        for station in source.dcum:
            actual = resolver.curve_frame(curve_name, float(station)).matrix
            compare(actual, source.get_point(float(station)).to_madpoint().matrix, "curve")
            result["curve_frames_checked"] += 1
        for name, obj in layout.objects.items():
            for frame in {*obj.implicit_frames, *obj.type.frames}:
                pose = resolver.object_frame(name, frame).matrix
                if not np.isfinite(pose).all():
                    raise ValueError(f"non-finite frame {name}.{frame}")
                result["frames_resolved"] += 1
            result["objects_resolved"] += 1
        for name, stations in report.get("span_objects", {}).items():
            for boundary, frame in (("start", "mechanical_start"), ("center", "anchor"), ("end", "mechanical_end")):
                station = min(float(source.dcum[-1]), max(0.0, stations[boundary]))
                compare(resolver.object_frame(name, frame).matrix,
                        source.get_point(station).to_madpoint().matrix, "span")
                result["span_frames_checked"] += 1
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    basename = args.output.name.removesuffix(".gz").removesuffix(".json")
    report = args.report or args.output.with_name(f"{basename}.layout-report.json")
    print(json.dumps(validate_conversion(args.input, args.output, report), indent=2), flush=True)


if __name__ == "__main__":
    main()
