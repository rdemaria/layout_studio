"""Shared BR/PR path preparation and closed-ring span references."""
from dataclasses import asdict, dataclass, field
import math

try:
    from .ldb_machine_to_layout import ConversionError, ConversionReport, machine_to_layout, validate_layout_json
except ImportError:
    from ldb_machine_to_layout import ConversionError, ConversionReport, machine_to_layout, validate_layout_json


class RingMachine:
    """Read-only adapter rebuilding a path from the source's ordered bends.

    BR repeats each bend for its four rings. Only identical path geometry is
    merged; every source transformation remains available to the converter.
    PR has adjacent bends whose intervening drift is zero up to roundoff.
    """

    def __init__(self, machine, *, merge_coincident_bends=False):
        self.source = machine
        self.merge_coincident_bends = merge_coincident_bends
        self.coincident_bends = {}
        self.omitted_zero_drifts = 0

    def __getattr__(self, name):
        return getattr(self.source, name)

    def get_ref_curve(self, machine_length, start=None):
        segments = []
        seen = {}
        self.coincident_bends = {}
        self.omitted_zero_drifts = 0
        last = 0.0

        def drift(length):
            if not math.isfinite(length) or length < -1e-10:
                raise ConversionError(f"{self.name} reference bends overlap or exceed the ring length: drift {length:g} m")
            if abs(length) <= 1e-10:
                self.omitted_zero_drifts += 1
            else:
                segments.append((length, 0.0, 0.0))

        for name, begin, end, length, angle, roll in self.source.get_bends():
            geometry = (begin, end, length, angle, roll)
            if self.merge_coincident_bends and geometry in seen:
                self.coincident_bends[name] = seen[geometry]
                continue
            seen[geometry] = name
            if not all(math.isfinite(value) for value in geometry) or length <= 0:
                raise ConversionError(f"{name!r} has invalid reference bend geometry")
            drift(begin - last)
            segments.append((length, angle, -roll))
            last = end
        drift(machine_length - last)
        return type(self.source.ref_curve)(segments, start=start)


def rebase_seam_references(machine, result):
    """Retain parents while avoiding inverse-station ambiguity at a ring seam."""
    stations = {}
    for name, obj in result.layout["objects"].items():
        source = machine.transformations[name]
        parent_span = result.report.span_objects.get(source.ref)
        if parent_span is None:
            continue
        parent = machine.transformations[source.ref]
        offsets = {
            "MECHANICAL START": -parent.length / 2,
            "MECHANICAL MIDDLE": 0,
            "MECHANICAL END": parent.length / 2,
            "OPTIC START": parent.optic_offset - parent.optic_length / 2,
            "OPTIC MIDDLE": parent.optic_offset,
            "OPTIC END": parent.optic_offset + parent.optic_length / 2,
        }
        station = parent_span["center"] + offsets[source.ref_point]
        if min(abs(station), abs(station - result.report.machine_length)) > 1e-7:
            continue
        position = obj["position"]
        position["reference"] = {"kind": "object_frame", "object": source.ref, "frame": "anchor"}
        position["reference_curve"] = result.report.curve_name
        position["transformation"].insert(0, ["ts", station - parent_span["center"]])
        stations[name] = station
    return stations


@dataclass
class RingConversionReport(ConversionReport):
    coincident_bends: dict[str, str] = field(default_factory=dict)
    omitted_zero_drifts: int = 0
    seam_reference_stations: dict[str, float] = field(default_factory=dict)


def convert_ring(machine, *, merge_coincident_bends=False, **options):
    prepared = RingMachine(machine, merge_coincident_bends=merge_coincident_bends)
    result = machine_to_layout(prepared, **options)
    seam_stations = rebase_seam_references(machine, result)
    result.report = RingConversionReport(
        **asdict(result.report), coincident_bends=prepared.coincident_bends,
        omitted_zero_drifts=prepared.omitted_zero_drifts,
        seam_reference_stations=seam_stations,
    )
    if prepared.coincident_bends:
        result.report.warnings.append(
            f"merged {len(prepared.coincident_bends)} coincident bends in the reference path only; all objects retain their source placements")
    if prepared.omitted_zero_drifts:
        result.report.warnings.append(
            f"omitted {prepared.omitted_zero_drifts} reference drifts with absolute length <= 1e-10 m")
    if seam_stations:
        result.report.warnings.append(
            f"rebased {len(seam_stations)} closed-ring seam references onto parent span centers, preserving the object hierarchy")
    validate_layout_json(result.layout)
    return result
