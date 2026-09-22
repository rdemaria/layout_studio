#!/usr/bin/env python3
"""LHC policy: lattice containers are s-spans; hardware lengths stay straight."""
from pathlib import Path
import sys
from copy import copy
from dataclasses import asdict, dataclass, field

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from ldb_machine_to_layout import ConversionError, ConversionReport, machine_to_layout, validate_layout_json, main as run
    from _ring_conversion import rebase_seam_references
else:
    from ..ldb_machine_to_layout import ConversionError, ConversionReport, machine_to_layout, validate_layout_json, main as run
    from .._ring_conversion import rebase_seam_references


SPAN_TYPES = frozenset({
    "LHC SECTOR", "LHC ARC", "LHC HALF-ARC", "LHC DISPERSION SUPPRESSOR",
    "LHC HALF-LSS", "LHC HALF-CELL",
})


@dataclass
class LHCConversionReport(ConversionReport):
    unavailable_mechanical_lengths: list[str] = field(default_factory=list)
    seam_reference_stations: dict[str, float] = field(default_factory=dict)


def convert(machine, *, span_types=(), **options):
    if machine.name != "LHC":
        raise ConversionError("the LHC policy requires an LHC snapshot")
    unknown = {name for name, tf in machine.transformations.items() if tf.length is None}
    # The LS3 snapshot contains center-positioned instruments with no length.
    # Keep their anchors, without inventing a solid or mechanical endpoints.
    for name in unknown:
        tf = machine.transformations[name]
        if tf.target_point != "MECHANICAL MIDDLE" or any((tf.optic_length, tf.optic_offset, tf.angle)):
            raise ConversionError(f"cannot place {name!r} without its mechanical length")
    for tf in machine.transformations.values():
        if tf.ref in unknown and tf.ref_point != "MECHANICAL MIDDLE":
            raise ConversionError(f"{tf.target!r} needs an unknown mechanical endpoint of {tf.ref!r}")
    prepared = copy(machine)
    prepared.transformations = dict(machine.transformations)
    for name in unknown:
        tf = prepared.transformations[name] = copy(machine.transformations[name])
        tf.length = 0.0  # Internal placeholder; geometry and endpoints removed below.
        tf.target_type += " (length unavailable)"
    result = machine_to_layout(prepared, span_types=SPAN_TYPES | set(span_types), **options)
    seam_stations = rebase_seam_references(machine, result)
    kept_unknown = sorted(unknown & result.layout["objects"].keys())
    for name in kept_unknown:
        type_ = result.layout["types"][result.layout["objects"][name]["type"]]
        type_.pop("shape", None)
        for frame in ("mechanical_start", "mechanical_end"):
            type_["frames"].pop(frame, None)
    report = result.report
    report.input_type_names = len({tf.target_type for tf in machine.transformations.values()})
    report.zero_mechanical_length_objects -= len(kept_unknown)
    report.warnings = [warning for warning in report.warnings if not warning.startswith("displayed ")]
    if report.zero_mechanical_length_objects:
        report.warnings.append(f"displayed {report.zero_mechanical_length_objects} zero-length objects with dz={report.zero_length_display_value:g} m")
    if kept_unknown:
        report.warnings.append(f"kept {len(kept_unknown)} objects with unavailable mechanical lengths as anchors without solids or mechanical endpoints")
    if seam_stations:
        report.warnings.append(f"rebased {len(seam_stations)} closed-ring seam references onto parent span centers, preserving the object hierarchy")
    result.report = LHCConversionReport(**asdict(report), unavailable_mechanical_lengths=kept_unknown,
                                      seam_reference_stations=seam_stations)
    validate_layout_json(result.layout)
    return result


if __name__ == "__main__":
    raise SystemExit(run(convert=convert, default_input=Path(__file__).with_name("LHC--LS3.pickle")))
