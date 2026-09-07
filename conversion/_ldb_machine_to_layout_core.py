#!/usr/bin/env python3
"""Convert a CERN Layout Database ``Machine`` into Layout Studio JSON.

The converter is intentionally independent of the future Layout Studio Python
API: it emits the canonical JSON dictionaries directly.  It can be used as a
library with an already-loaded ``Machine`` or as a command-line program for the
pickles produced by ``cernlayoutdb.machine.Machine.to_pickle``.

Coordinate conversion
---------------------
Layout Studio uses the MAD-like local axes ``(x, y, s)`` while LDB uses
``(x, y, z) = (s, horizontal-inward, vertical)``.  The elementary LDB
operations therefore map as follows (LDB rotations are stored in degrees):

    LDB tx  -> Layout ts
    LDB ty  -> Layout tx with opposite sign
    LDB tz  -> Layout ty
    LDB rx  -> Layout rs with opposite sign
    LDB ry  -> Layout rx
    LDB rz  -> Layout ry with opposite sign

The signs and axis permutation are the elementary-operation form of
``LDBPoint.to_madpoint()``.  Ordered operations remain ordered.  As requested,
all LDB longitudinal ``tx`` operations become path-following ``ts`` operations;
no LDB ``tx`` is silently converted to the straight-tangent ``tt`` operation.

Geometry conversion
-------------------
Physical mechanical lengths are straight by default. Explicit machine policy
can identify curved hardware or logical s-span containers. Span frames are
sampled from the complete reference curve and expressed as local rigid frames;
spans have no solid shape or fictitious magnetic axis.

Positive optic lengths define the available magnetic axis. LDB optic points
address the object's beam interface, which inherits that axis unless the source
optic center differs from its mechanical-to-magnetic offset. Zero optic lengths
remain exact stored optic frames, without a tiny artificial magnetic length.

Security
--------
Python pickle is not a general interchange format.  The CLI loader uses a
restricted allow-list tailored to the supplied Machine/LDBPoint/numpy objects,
but input pickles should still be treated as trusted project data.
"""

from __future__ import annotations

import argparse
import colorsys
import hashlib
import gzip
import importlib
import importlib.util
import json
import math
import pickle
import re
import sys
import types
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping, Sequence

import numpy as np


POINT_TO_LAYOUT_FRAME: dict[str, str] = {
    "MECHANICAL START": "mechanical_start",
    "MECHANICAL MIDDLE": "anchor",
    "MECHANICAL END": "mechanical_end",
    "OPTIC START": "beam_entry",
    "OPTIC MIDDLE": "beam_center",
    "OPTIC END": "beam_exit",
}

# name, multiplicative factor, convert-degrees-to-radians
LDB_OPERATION_MAP: dict[str, tuple[str, float, bool]] = {
    "tx": ("ts", +1.0, False),
    "ty": ("tx", -1.0, False),
    "tz": ("ty", +1.0, False),
    "rx": ("rs", -1.0, True),
    "ry": ("rx", +1.0, True),
    "rz": ("ry", -1.0, True),
}

HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
IMPLICIT_FRAMES = {
    "center",
    "anchor",
    "mechanical_center",
    "magnetic_center",
    "magnetic_entry",
    "magnetic_exit",
    "beam_center",
    "beam_entry",
    "beam_exit",
}


class ConversionError(RuntimeError):
    """Raised when the source machine cannot be represented safely."""


@dataclass(frozen=True, order=True)
class TypeKey:
    """Per-instance data that Layout Studio stores on a reusable type."""

    ldb_type: str
    color_initial: str
    mechanical_length: float
    optic_length: float
    magnetic_long_offset: float
    magnetic_radial_offset: float
    magnetic_vertical_offset: float
    angle: float
    roll: float
    mechanical_model: str = "straight"
    span_frames: tuple = ()


@dataclass
class ConversionReport:
    machine: str
    version: str
    curve_name: str
    machine_length: float
    machine_length_source: str
    input_transformations: int
    output_objects: int = 0
    skipped_objects: dict[str, str] = field(default_factory=dict)
    input_type_names: int = 0
    output_types: int = 0
    split_type_names: dict[str, list[str]] = field(default_factory=dict)
    curve_segments: int = 0
    curve_total_angle: float = 0.0
    zero_mechanical_length_objects: int = 0
    zero_optic_length_objects: int = 0
    zero_length_display_value: float = 0.1
    root_name: str = ""
    root_name_source: str = ""
    mechanical_models: dict[str, str] = field(default_factory=dict)
    span_objects: dict[str, dict[str, float]] = field(default_factory=dict)
    explicit_beam_interfaces: int = 0
    warnings: list[str] = field(default_factory=list)


@dataclass
class ConversionResult:
    layout: dict[str, Any]
    report: ConversionReport


def _as_float(value: Any, label: str) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise ConversionError(f"{label} is not a real number: {value!r}") from exc
    if not math.isfinite(out):
        raise ConversionError(f"{label} must be finite, got {out!r}")
    # Avoid serializing negative zero, which makes reports unnecessarily noisy.
    return 0.0 if out == 0.0 else out


def _nonzero(value: float, tolerance: float = 1e-15) -> bool:
    return abs(value) > tolerance


def color_for_name(name: str) -> str:
    """Return a stable, reasonably separated color based on the first character."""

    key = (name.strip()[:1] or "?").upper()
    alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    if key in alphabet:
        index = alphabet.index(key)
    else:
        # Stable across Python processes, unlike the built-in hash().
        index = int.from_bytes(hashlib.sha256(key.encode("utf-8")).digest()[:2], "big")

    # Golden-ratio stepping gives useful separation even for adjacent letters.
    hue = (0.08 + index * 0.6180339887498949) % 1.0
    saturation = 0.58 + 0.08 * ((index // 12) % 2)
    value = 0.88 - 0.06 * ((index // 24) % 2)
    red, green, blue = colorsys.hsv_to_rgb(hue, saturation, value)
    return f"#{round(red * 255):02x}{round(green * 255):02x}{round(blue * 255):02x}"


def _object_initial(name: str) -> str:
    return (name.strip()[:1] or "?").upper()


def _point_offset(tf: Any, point_name: str) -> float:
    """Longitudinal LDB coordinate of a standard point from mechanical middle."""

    length = _as_float(tf.length, f"{tf.target}.length")
    optic_length = _as_float(tf.optic_length, f"{tf.target}.optic_length")
    optic_offset = _as_float(tf.optic_offset, f"{tf.target}.optic_offset")
    offsets = {
        "MECHANICAL START": -length / 2.0,
        "MECHANICAL MIDDLE": 0.0,
        "MECHANICAL END": length / 2.0,
        "OPTIC START": optic_offset - optic_length / 2.0,
        "OPTIC MIDDLE": optic_offset,
        "OPTIC END": optic_offset + optic_length / 2.0,
    }
    try:
        return offsets[point_name]
    except KeyError as exc:
        raise ConversionError(
            f"unsupported LDB point type {point_name!r} on {tf.target!r}"
        ) from exc


def convert_ldb_operations(tf: Any) -> list[list[Any]]:
    """Convert one ordered LDB transformation to Layout Studio operations."""

    operations: list[list[Any]] = []
    for source_name in tf.order:
        if source_name not in LDB_OPERATION_MAP:
            raise ConversionError(
                f"{tf.target!r} contains unsupported LDB operation {source_name!r}"
            )
        target_name, factor, degrees = LDB_OPERATION_MAP[source_name]
        value = _as_float(getattr(tf, source_name), f"{tf.target}.{source_name}")
        if not _nonzero(value):
            continue
        if degrees:
            value = math.radians(value)
        converted = factor * value
        operations.append([target_name, 0.0 if converted == 0.0 else converted])
    return operations


def _mad_frame_operations(mad: Any) -> list[list[Any]]:
    """Encode a rigid MADPoint with fixed translations followed by rotations."""
    xyz = [_as_float(value, "frame coordinate") for value in mad.xyz]
    theta, phi, psi = (
        _as_float(value, "frame Euler angle") for value in mad.get_theta_phi_psi()
    )
    # MADPoint R = Ry(theta) Rx(-phi) Rs(psi).
    values = [*zip(("tx", "ty", "tt"), xyz),
              ("ry", theta), ("rx", -phi), ("rs", psi)]
    return [[name, value] for name, value in values if _nonzero(value)]


def _layout_starting_frame_from_ldb(path: Any) -> dict[str, Any]:
    start = getattr(path, "start", None)
    if start is not None and not hasattr(start, "to_madpoint"):
        raise ConversionError("reference path start does not provide to_madpoint()")
    return {"reference": {"kind": "world"}, "transformation":
            [] if start is None else _mad_frame_operations(start.to_madpoint())}


def _make_reference_curve(
    machine: Any,
    curve_name: str,
    machine_length: float,
) -> tuple[dict[str, Any], Any]:
    existing_path = getattr(machine, "ref_curve", None)
    start = getattr(existing_path, "start", None)
    try:
        path = machine.get_ref_curve(machine_length=machine_length, start=start)
    except TypeError:
        # Compatibility with a potential older method without a start keyword.
        path = machine.get_ref_curve(machine_length=machine_length)

    segments: list[list[float]] = []
    for index, segment in enumerate(path.segments, start=1):
        if len(segment) != 3:
            raise ConversionError(f"curve segment {index} is not (length, angle, roll)")
        length, angle, ldb_path_roll = (
            _as_float(value, f"curve segment {index}") for value in segment
        )
        if length <= 0.0:
            raise ConversionError(
                f"curve segment {index} has non-positive length {length}"
            )
        # LDBPath positive roll turns an inward bend upward.  After conversion
        # to MAD/Layout axes, Layout positive roll turns it downward, hence the
        # sign reversal.  Machine.get_ref_curve itself stores -LDBTrans.roll in
        # LDBPath, so this recovers the original MAD-X roll convention.
        segments.append([length, angle, -ldb_path_roll])

    return (
        {
            "color": color_for_name(curve_name),
            "starting_frame": _layout_starting_frame_from_ldb(path),
            "segments": segments,
        },
        path,
    )


def _magnetic_offset(tf: Any, field: str, fallback: float = 0.0) -> float:
    value = getattr(tf, field, None)
    return fallback if value is None else _as_float(value, f"{tf.target}.{field}")


def _frame_name(tf: Any, point: str, *, span: bool = False) -> str:
    if point.startswith("OPTIC ") and (span or tf.optic_length == 0):
        return {"OPTIC START": "optic_start", "OPTIC MIDDLE": "optic_center",
                "OPTIC END": "optic_end"}[point]
    try:
        return POINT_TO_LAYOUT_FRAME[point]
    except KeyError as exc:
        raise ConversionError(f"{tf.target!r} uses unsupported point {point!r}") from exc


def _type_key(object_name: str, tf: Any, mechanical_model: str, span_frames=()) -> TypeKey:
    length = _as_float(tf.length, f"{object_name}.length")
    optic_length = _as_float(tf.optic_length, f"{object_name}.optic_length")
    if length < 0 or optic_length < 0:
        raise ConversionError(f"{object_name!r} has a negative length")
    optic_offset = _as_float(tf.optic_offset, f"{object_name}.optic_offset")
    return TypeKey(
        ldb_type=str(tf.target_type), color_initial=_object_initial(object_name),
        mechanical_length=length, optic_length=optic_length,
        magnetic_long_offset=(_magnetic_offset(tf, "mec_long_offset", optic_offset)
                              if optic_length else optic_offset),
        magnetic_radial_offset=_magnetic_offset(tf, "mec_radial_offset"),
        magnetic_vertical_offset=_magnetic_offset(tf, "mec_vertical_offset"),
        angle=_as_float(tf.angle, f"{object_name}.angle"),
        roll=_as_float(tf.roll, f"{object_name}.roll"),
        mechanical_model=mechanical_model, span_frames=span_frames,
    )


def _span_center_stations(machine: Any, root_name: str, names: Iterable[str]) -> dict[str, float]:
    """Resolve the source's longitudinal hierarchy before wrapping it onto a curve.

    A span is a station interval, not rotated/offset hardware. Refuse such
    source data rather than guessing how to transport a span away from the path.
    """
    cache: dict[str, float] = {}

    def station(name: str) -> float:
        if name in cache:
            return cache[name]
        tf = machine.transformations[name]
        if any(_nonzero(_as_float(getattr(tf, op), f"{name}.{op}"))
               for op in ("ty", "tz", "rx", "ry", "rz")):
            raise ConversionError(f"span ancestry {name!r} is not purely longitudinal")
        if str(tf.ref) == root_name:
            base = 0.0
        else:
            parent = machine.transformations[str(tf.ref)]
            base = station(str(tf.ref)) + _point_offset(parent, str(tf.ref_point))
        cache[name] = base + _as_float(tf.tx, f"{name}.tx") - _point_offset(tf, str(tf.target_point))
        return cache[name]

    return {name: station(name) for name in sorted(names)}


def _span_local_frames(tf: Any, center_station: float, path: Any) -> tuple:
    def at(station: float):
        length = float(path.dcum[-1])
        if station < -1e-8 or station > length + 1e-8:
            raise ConversionError(f"span {tf.target!r} extends beyond the reference curve")
        return path.get_point(min(length, max(0.0, station))).to_madpoint()

    center = at(center_station)
    inverse = np.linalg.inv(center.matrix)
    frames = []
    for point in POINT_TO_LAYOUT_FRAME:
        name = _frame_name(tf, point, span=True)
        if name == "anchor":
            continue
        offset = _point_offset(tf, point)
        if offset == 0:
            frames.append((name, ()))
        else:
            local = type(center)(inverse @ at(center_station + offset).matrix)
            frames.append((name, tuple(tuple(op) for op in _mad_frame_operations(local))))
    return tuple(frames)


def _allocate_type_names(
    keys_by_base: Mapping[str, set[TypeKey]],
) -> tuple[dict[TypeKey, str], dict[str, list[str]]]:
    """Assign stable names, splitting LDB type names only where necessary."""

    key_to_name: dict[TypeKey, str] = {}
    splits: dict[str, list[str]] = {}
    used: set[str] = set()

    def unique(candidate: str) -> str:
        if candidate not in used:
            used.add(candidate)
            return candidate
        suffix = 2
        while f"{candidate}__{suffix}" in used:
            suffix += 1
        out = f"{candidate}__{suffix}"
        used.add(out)
        return out

    for base in sorted(keys_by_base):
        keys = sorted(keys_by_base[base])
        if len(keys) == 1:
            key_to_name[keys[0]] = unique(base)
            continue

        names: list[str] = []
        for index, key in enumerate(keys, start=1):
            safe_initial = re.sub(r"[^0-9A-Za-z]+", "_", key.color_initial) or "X"
            candidate = f"{base}__{safe_initial}_{index:02d}"
            output_name = unique(candidate)
            key_to_name[key] = output_name
            names.append(output_name)
        splits[base] = names

    return key_to_name, splits


def _offset_operations(longitudinal: float, radial=0.0, vertical=0.0, *, curved=False):
    # These are local geometry offsets, not source positioning TX operations.
    return [[name, value] for name, value in
            (("ts" if curved else "tt", longitudinal), ("tx", -radial), ("ty", vertical))
            if _nonzero(value)]


def _make_layout_type(key: TypeKey, *, transverse_size: float, point_length: float) -> dict[str, Any]:
    result: dict[str, Any] = {"color": color_for_name(key.color_initial), "frames": {}}
    if key.mechanical_model == "span":
        result["frames"] = {name: {"transformation": [list(op) for op in ops]}
                            for name, ops in key.span_frames}
        return result

    if key.angle and key.optic_length <= 0:
        raise ConversionError(f"type {key.ldb_type!r} has a bend angle but no optic length")
    curvature = key.angle / key.optic_length if key.optic_length else 0.0
    curved = key.mechanical_model == "curved"
    result["shape"] = ["box", transverse_size, transverse_size,
                       key.mechanical_length or point_length,
                       curvature if curved else 0.0, key.roll if curved else 0.0]
    for name, direction in (("mechanical_start", -0.5), ("mechanical_end", 0.5)):
        result["frames"][name] = {"transformation":
                                  _offset_operations(direction * key.mechanical_length, curved=curved)}
    center = {"transformation": _offset_operations(
        key.magnetic_long_offset, key.magnetic_radial_offset, key.magnetic_vertical_offset,
        curved=curved,
    )}
    if key.optic_length:
        result.update(magnetic_center=center, magnetic_length=key.optic_length,
                      magnetic_curvature=curvature, magnetic_roll=key.roll)
    else:
        # A zero-length optic point is still a valid source anchor. It has no
        # finite axis and its start/middle/end coincide exactly.
        for name in ("optic_start", "optic_center", "optic_end"):
            result["frames"][name] = {"transformation": _offset_operations(key.magnetic_long_offset, curved=curved)}
    return result


def _initial_dangling_objects(machine: Any, root_name: str) -> dict[str, str]:
    transformations = machine.transformations
    machine_name = root_name
    dangling: dict[str, str] = {}
    for object_name, tf in transformations.items():
        reference_name = str(tf.ref)
        if reference_name != machine_name and reference_name not in transformations:
            dangling[str(object_name)] = (
                f"references missing LDB object {reference_name!r}"
            )
    return dangling


def _propagate_skips(machine: Any, skipped: dict[str, str]) -> dict[str, str]:
    children: dict[str, list[str]] = defaultdict(list)
    for object_name, tf in machine.transformations.items():
        children[str(tf.ref)].append(str(object_name))

    queue = deque(skipped)
    while queue:
        parent = queue.popleft()
        for child in children.get(parent, []):
            if child not in skipped:
                skipped[child] = f"depends on skipped object {parent!r}"
                queue.append(child)
    return skipped


def _check_input_cycles(machine: Any, kept_names: set[str]) -> None:
    state: dict[str, Literal["visiting", "visited"]] = {}
    stack: list[str] = []

    def visit(name: str) -> None:
        if state.get(name) == "visited":
            return
        if state.get(name) == "visiting":
            start = stack.index(name)
            cycle = " -> ".join([*stack[start:], name])
            raise ConversionError(f"LDB reference cycle: {cycle}")
        state[name] = "visiting"
        stack.append(name)
        parent = str(machine.transformations[name].ref)
        if parent in kept_names:
            visit(parent)
        stack.pop()
        state[name] = "visited"

    for name in kept_names:
        visit(name)


def machine_to_layout(
    machine: Any, *, curve_name: str, root_name: str, machine_length: float,
    transverse_size: float = 0.1, point_length: float = 0.1,
    span_types: Iterable[str] = (), curved_types: Iterable[str] = (),
    dangling: Literal["skip", "error"] = "skip",
) -> ConversionResult:
    """Shared conversion implementation; root/length discovery is in the public module."""
    for attribute in ("name", "version", "transformations", "get_ref_curve"):
        if not hasattr(machine, attribute):
            raise ConversionError(f"machine is missing required attribute {attribute!r}")

    if dangling not in {"skip", "error"}:
        raise ConversionError("dangling must be 'skip' or 'error'")

    machine_name = root_name
    output_curve_name = curve_name
    if not output_curve_name:
        raise ConversionError("curve_name must be non-empty")

    resolved_length = _as_float(machine_length, "machine_length")
    for name, value in (("machine_length", resolved_length),
                        ("transverse_size", transverse_size), ("point_length", point_length)):
        if _as_float(value, name) <= 0:
            raise ConversionError(f"{name} must be positive")
    span_types, curved_types = frozenset(span_types), frozenset(curved_types)
    if span_types & curved_types:
        raise ConversionError("a type cannot be both a span and curved hardware")
    transformations: Mapping[str, Any] = machine.transformations
    report = ConversionReport(
        machine=str(machine.name),
        version=str(machine.version),
        curve_name=output_curve_name,
        machine_length=resolved_length,
        machine_length_source="explicit argument",
        input_transformations=len(transformations),
        input_type_names=len({str(tf.target_type) for tf in transformations.values()}),
        zero_length_display_value=point_length,
    )

    skipped = _initial_dangling_objects(machine, root_name)
    if skipped and dangling == "error":
        details = "; ".join(f"{name}: {reason}" for name, reason in sorted(skipped.items()))
        raise ConversionError(f"dangling LDB references: {details}")
    if skipped:
        skipped = _propagate_skips(machine, skipped)
        report.skipped_objects.update(sorted(skipped.items()))
        report.warnings.append(
            f"skipped {len(skipped)} object(s) with missing ancestors"
        )

    kept_names = {str(name) for name in transformations if str(name) not in skipped}
    _check_input_cycles(machine, kept_names)

    reference_curve, source_path = _make_reference_curve(
        machine, output_curve_name, resolved_length
    )
    report.curve_segments = len(reference_curve["segments"])
    report.curve_total_angle = sum(segment[1] for segment in reference_curve["segments"])
    curve_length_from_segments = sum(segment[0] for segment in reference_curve["segments"])
    if not math.isclose(
        curve_length_from_segments,
        resolved_length,
        rel_tol=1e-12,
        abs_tol=1e-9,
    ):
        raise ConversionError(
            "generated reference curve length does not match requested machine length: "
            f"{curve_length_from_segments} versus {resolved_length}"
        )

    span_names = {name for name in kept_names if str(transformations[name].target_type) in span_types}
    span_stations = _span_center_stations(machine, root_name, span_names)
    report.mechanical_models = {name: "span" for name in sorted(span_types)}
    report.mechanical_models.update({name: "curved" for name in sorted(curved_types)})
    keys_by_base: dict[str, set[TypeKey]] = defaultdict(set)
    key_for_object: dict[str, TypeKey] = {}
    for object_name, tf in transformations.items():
        object_name = str(object_name)
        if object_name in skipped:
            continue
        model = "span" if object_name in span_names else "curved" if str(tf.target_type) in curved_types else "straight"
        span_frames = ()
        if model == "span":
            station = span_stations[object_name]
            span_frames = _span_local_frames(tf, station, source_path)
            report.span_objects[object_name] = {"start": station - tf.length / 2,
                                                "center": station, "end": station + tf.length / 2}
        key = _type_key(object_name, tf, model, span_frames)
        key_for_object[object_name] = key
        keys_by_base[key.ldb_type].add(key)
        if key.mechanical_length == 0.0:
            report.zero_mechanical_length_objects += 1
        if key.optic_length == 0.0:
            report.zero_optic_length_objects += 1

    type_name_for_key, split_names = _allocate_type_names(keys_by_base)
    report.split_type_names = split_names

    output_types: dict[str, Any] = {}
    # Sort by output name for deterministic JSON independent of source row order.
    for key, output_name in sorted(type_name_for_key.items(), key=lambda item: item[1]):
        output_types[output_name] = _make_layout_type(
            key,
            transverse_size=transverse_size,
            point_length=point_length,
        )

    output_objects: dict[str, Any] = {}
    for object_name, tf in transformations.items():
        object_name = str(object_name)
        if object_name in skipped:
            continue

        try:
            target_frame = _frame_name(tf, str(tf.target_point), span=object_name in span_names)
            reference_frame = ("anchor" if str(tf.ref) == root_name else _frame_name(
                transformations[str(tf.ref)], str(tf.ref_point), span=str(tf.ref) in span_names))
        except KeyError as exc:
            raise ConversionError(
                f"{object_name!r} uses unsupported point type {exc.args[0]!r}"
            ) from exc

        operations = convert_ldb_operations(tf)
        reference_name = str(tf.ref)
        if reference_name == machine_name:
            reference: dict[str, Any] = {
                "kind": "curve",
                "curve": output_curve_name,
            }
        elif reference_name in kept_names:
            reference = {
                "kind": "object_frame",
                "object": reference_name,
                "frame": reference_frame,
            }
        else:
            # This should have been caught by the dangling closure above.
            raise ConversionError(
                f"internal error: unresolved reference {reference_name!r} for {object_name!r}"
            )

        position: dict[str, Any] = {
            "target": target_frame,
            "reference": reference,
            "transformation": operations,
        }
        if reference["kind"] != "curve" and any(op[0] == "ts" for op in operations):
            position["reference_curve"] = output_curve_name

        output_objects[object_name] = {
            "type": type_name_for_key[key_for_object[object_name]],
            "position": position,
        }
        key = key_for_object[object_name]
        if key.optic_length > 0 and key.mechanical_model != "span":
            beam_center = {"transformation": _offset_operations(
                _as_float(tf.optic_offset, f"{object_name}.optic_offset"),
                curved=key.mechanical_model == "curved")}
            magnetic = output_types[type_name_for_key[key]]
            if beam_center != magnetic["magnetic_center"]:
                output_objects[object_name].update(
                    beam_center=beam_center, beam_length=key.optic_length,
                    beam_curvature=magnetic["magnetic_curvature"], beam_roll=key.roll,
                )
                report.explicit_beam_interfaces += 1

    layout = {
        "reference_curves": {output_curve_name: reference_curve},
        "types": output_types,
        "objects": output_objects,
    }

    report.output_objects = len(output_objects)
    report.output_types = len(output_types)
    if split_names:
        report.warnings.append(
            f"split {len(split_names)} LDB type name(s) because geometry and/or color initial varies"
        )
    if report.zero_mechanical_length_objects:
        report.warnings.append(
            f"displayed {report.zero_mechanical_length_objects} zero-length object(s) with "
            f"dz={point_length:g} m"
        )
    if report.zero_optic_length_objects:
        report.warnings.append(
            f"kept {report.zero_optic_length_objects} zero optic length(s) as exact stored frames; no magnetic/beam axis"
        )

    validate_layout_json(layout)
    return ConversionResult(layout=layout, report=report)


def validate_layout_json(layout: Mapping[str, Any]) -> None:
    """Validate the emitted subset against the strict Layout Studio schema."""

    if set(layout) != {"reference_curves", "types", "objects"}:
        raise ConversionError("layout root must contain exactly reference_curves, types and objects")
    curves = layout["reference_curves"]
    types_ = layout["types"]
    objects = layout["objects"]
    if not all(isinstance(value, Mapping) for value in (curves, types_, objects)):
        raise ConversionError("layout root fields must be dictionaries")

    def finite(value: Any, path: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConversionError(f"{path} must be a JSON number")
        out = float(value)
        if not math.isfinite(out):
            raise ConversionError(f"{path} must be finite")
        return out

    def color(value: Any, path: str) -> None:
        if not isinstance(value, str) or not HEX_COLOR_RE.fullmatch(value):
            raise ConversionError(f"{path} is not #RRGGBB")

    def operations(value: Any, path: str, *, allow_ts: bool = True) -> None:
        if not isinstance(value, list):
            raise ConversionError(f"{path} must be a list")
        allowed = {"tx", "ty", "ts", "tt", "rx", "ry", "rs"}
        for index, item in enumerate(value):
            if not isinstance(item, list) or len(item) != 2:
                raise ConversionError(f"{path}[{index}] must be [name, value]")
            name, amount = item
            if name not in allowed or (name == "ts" and not allow_ts):
                raise ConversionError(f"{path}[{index}] has invalid operation {name!r}")
            finite(amount, f"{path}[{index}][1]")

    def local_frame(value: Any, path: str) -> None:
        if not isinstance(value, Mapping) or set(value) != {"transformation"}:
            raise ConversionError(f"{path} must be a local transformation")
        operations(value["transformation"], path)

    def axis(value: Mapping, feature: str, path: str) -> None:
        fields = {f"{feature}_{name}" for name in ("center", "length", "curvature", "roll")}
        present = fields.intersection(value)
        if present and present != fields:
            raise ConversionError(f"{path}.{feature} fields must be all present or all absent")
        if not present:
            return
        local_frame(value[f"{feature}_center"], f"{path}.{feature}_center")
        if finite(value[f"{feature}_length"], f"{path}.{feature}_length") <= 0:
            raise ConversionError(f"{path}.{feature}_length must be positive")
        for name in ("curvature", "roll"):
            finite(value[f"{feature}_{name}"], f"{path}.{feature}_{name}")

    def frame_names(obj: Mapping) -> set[str]:
        type_ = types_[obj["type"]]
        names = {"anchor", *type_["frames"]}
        if "shape" in type_:
            names.add("mechanical_center")
        if "magnetic_center" in type_:
            names.update(("magnetic_center", "magnetic_entry", "magnetic_exit"))
        if "beam_center" in obj or "magnetic_center" in type_:
            names.update(("beam_center", "beam_entry", "beam_exit"))
        return names

    def reference(value: Any, path: str) -> None:
        if not isinstance(value, Mapping):
            raise ConversionError(f"{path} must be a reference object")
        kind = value.get("kind")
        if kind == "world":
            if set(value) != {"kind"}:
                raise ConversionError(f"{path} world reference has extra fields")
        elif kind == "curve":
            if set(value) != {"kind", "curve"} or value.get("curve") not in curves:
                raise ConversionError(f"{path} has an invalid curve reference")
        elif kind == "object_frame":
            if set(value) != {"kind", "object", "frame"}:
                raise ConversionError(f"{path} object-frame reference has wrong fields")
            object_name = value.get("object")
            if object_name not in objects:
                raise ConversionError(f"{path} references unknown object {object_name!r}")
            frame = value.get("frame")
            if frame not in frame_names(objects[object_name]):
                raise ConversionError(f"{path} references unknown frame {frame!r}")
        else:
            raise ConversionError(f"{path} has invalid reference kind {kind!r}")

    for curve_name, curve in curves.items():
        if not curve_name or set(curve) != {"color", "starting_frame", "segments"}:
            raise ConversionError(f"invalid curve {curve_name!r}")
        color(curve["color"], f"reference_curves.{curve_name}.color")
        start = curve["starting_frame"]
        if not isinstance(start, Mapping) or set(start) != {"reference", "transformation"}:
            raise ConversionError(f"invalid starting frame for curve {curve_name!r}")
        reference(start["reference"], f"reference_curves.{curve_name}.starting_frame.reference")
        operations(start["transformation"], f"reference_curves.{curve_name}.starting_frame.transformation")
        if start["reference"].get("kind") != "curve" and any(
            op[0] == "ts" for op in start["transformation"]
        ):
            raise ConversionError("curve starting-frame ts requires a curve reference")
        if not isinstance(curve["segments"], list) or not curve["segments"]:
            raise ConversionError(f"curve {curve_name!r} needs segments")
        for index, segment in enumerate(curve["segments"]):
            if not isinstance(segment, list) or len(segment) != 3:
                raise ConversionError(f"curve {curve_name!r} segment {index} is invalid")
            if finite(segment[0], "segment length") <= 0.0:
                raise ConversionError("segment length must be positive")
            finite(segment[1], "segment angle")
            finite(segment[2], "segment roll")

    for type_name, type_ in types_.items():
        allowed = {"color", "frames", "shape", "magnetic_center", "magnetic_length",
                   "magnetic_curvature", "magnetic_roll"}
        if not type_name or not isinstance(type_, Mapping) or not {"color", "frames"}.issubset(type_) or not set(type_).issubset(allowed):
            raise ConversionError(f"invalid type {type_name!r}")
        color(type_["color"], f"types.{type_name}.color")
        if "shape" in type_:
            shape = type_["shape"]
            if not isinstance(shape, list) or len(shape) != 6 or shape[0] != "box":
                raise ConversionError(f"types.{type_name}.shape is not a Layout Studio box")
            for index in (1, 2, 3):
                if finite(shape[index], f"types.{type_name}.shape[{index}]") <= 0:
                    raise ConversionError(f"types.{type_name} shape dimensions must be positive")
            finite(shape[4], f"types.{type_name}.shape curvature")
            finite(shape[5], f"types.{type_name}.shape roll")
        axis(type_, "magnetic", f"types.{type_name}")
        frames = type_["frames"]
        if not isinstance(frames, Mapping) or IMPLICIT_FRAMES.intersection(frames):
            raise ConversionError(f"types.{type_name}.frames must be a mapping with no reserved names")
        for name, frame in frames.items():
            if not name:
                raise ConversionError(f"types.{type_name}.frames contains an empty name")
            local_frame(frame, f"types.{type_name}.frames.{name}")

    dependencies: dict[str, list[str]] = {}
    for object_name, obj in objects.items():
        allowed = {"type", "position", "beam_center", "beam_length", "beam_curvature", "beam_roll"}
        if not object_name or not isinstance(obj, Mapping) or not {"type", "position"}.issubset(obj) or not set(obj).issubset(allowed):
            raise ConversionError(f"invalid object {object_name!r}")
        type_name = obj["type"]
        if type_name not in types_:
            raise ConversionError(f"object {object_name!r} references unknown type {type_name!r}")
        axis(obj, "beam", f"objects.{object_name}")
        position = obj["position"]
        if not isinstance(position, Mapping):
            raise ConversionError(f"object {object_name!r} position is invalid")
        allowed_fields = {"target", "reference", "reference_curve", "transformation"}
        if not set(position).issubset(allowed_fields) or not {
            "target",
            "reference",
            "transformation",
        }.issubset(position):
            raise ConversionError(f"object {object_name!r} position has wrong fields")
        target = position["target"]
        if target not in frame_names(obj):
            raise ConversionError(f"object {object_name!r} targets unknown frame {target!r}")
        reference(position["reference"], f"objects.{object_name}.position.reference")
        operations(position["transformation"], f"objects.{object_name}.position.transformation")
        has_ts = any(op[0] == "ts" for op in position["transformation"])
        kind = position["reference"]["kind"]
        projection = position.get("reference_curve")
        if kind == "curve":
            if projection is not None:
                raise ConversionError(f"object {object_name!r} must not set reference_curve")
        elif has_ts:
            if projection not in curves:
                raise ConversionError(
                    f"object {object_name!r} needs a valid reference_curve for ts"
                )
        elif projection is not None:
            raise ConversionError(
                f"object {object_name!r} has an unused reference_curve"
            )

        deps: list[str] = []
        if kind == "object_frame":
            deps.append(position["reference"]["object"])
        dependencies[object_name] = deps

    state: dict[str, Literal["visiting", "visited"]] = {}
    stack: list[str] = []

    def visit(object_name: str) -> None:
        if state.get(object_name) == "visited":
            return
        if state.get(object_name) == "visiting":
            start = stack.index(object_name)
            cycle = " -> ".join([*stack[start:], object_name])
            raise ConversionError(f"output object dependency cycle: {cycle}")
        state[object_name] = "visiting"
        stack.append(object_name)
        for dependency in dependencies.get(object_name, []):
            visit(dependency)
        stack.pop()
        state[object_name] = "visited"

    for object_name in objects:
        visit(object_name)


def write_json(path: Path, value: Any, *, indent: int | None = 2) -> None:
    """Write JSON, using reproducible gzip bytes for a .gz output path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(value, indent=indent, ensure_ascii=False, allow_nan=False,
                       separators=(",", ":") if indent is None else None) + "\n").encode("utf-8")
    if path.suffix == ".gz":
        with path.open("wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=9) as stream:
                stream.write(data)
    else:
        path.write_bytes(data)


def _bootstrap_legacy_modules(module_dir: Path | None) -> None:
    """Make supplied standalone modules available under pickle module names."""

    try:
        importlib.import_module("cernlayoutdb.machine")
        importlib.import_module("cernlayoutdb.ldbpoint")
        return
    except ImportError:
        pass

    if module_dir is None:
        module_dir = Path(__file__).resolve().parent
    module_dir = module_dir.resolve()
    ldbpoint_path = module_dir / "ldbpoint.py"
    machine_path = module_dir / "machine.py"
    if not ldbpoint_path.exists() or not machine_path.exists():
        raise ConversionError(
            "could not import cernlayoutdb and could not find machine.py plus "
            f"ldbpoint.py in {module_dir}"
        )

    package = sys.modules.get("cernlayoutdb")
    if package is None:
        package = types.ModuleType("cernlayoutdb")
        package.__path__ = [str(module_dir)]  # type: ignore[attr-defined]
        sys.modules["cernlayoutdb"] = package

    def load(name: str, filename: Path) -> types.ModuleType:
        spec = importlib.util.spec_from_file_location(name, filename)
        if spec is None or spec.loader is None:
            raise ConversionError(f"cannot load module {name!r} from {filename}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    ldbpoint = load("cernlayoutdb.ldbpoint", ldbpoint_path)
    # The supplied machine.py imports the historical top-level name.
    sys.modules.setdefault("ldbpoint", ldbpoint)
    machine_module = load("cernlayoutdb.machine", machine_path)
    sys.modules.setdefault("machine", machine_module)


class _RestrictedMachineUnpickler(pickle.Unpickler):
    _ALLOWED: set[tuple[str, str]] = {
        ("cernlayoutdb.machine", "Machine"),
        ("cernlayoutdb.machine", "LDBTrans"),
        ("cernlayoutdb.machine", "Type"),
        ("cernlayoutdb.machine", "Aperture"),
        ("cernlayoutdb.machine", "Profile"),
        ("machine", "Machine"),
        ("machine", "LDBTrans"),
        ("machine", "Type"),
        ("machine", "Aperture"),
        ("machine", "Profile"),
        ("cernlayoutdb.ldbpoint", "LDBPath"),
        ("cernlayoutdb.ldbpoint", "LDBPoint"),
        ("ldbpoint", "LDBPath"),
        ("ldbpoint", "LDBPoint"),
        ("numpy", "dtype"),
        ("numpy", "ndarray"),
        ("numpy.core.multiarray", "scalar"),
        ("numpy.core.multiarray", "_reconstruct"),
        ("numpy._core.multiarray", "scalar"),
        ("numpy._core.multiarray", "_reconstruct"),
        ("numpy.core.numeric", "_frombuffer"),
        ("numpy._core.numeric", "_frombuffer"),
    }

    def find_class(self, module: str, name: str) -> Any:
        if (module, name) not in self._ALLOWED:
            raise pickle.UnpicklingError(f"blocked pickle global {module}.{name}")
        return super().find_class(module, name)


def load_machine_pickle(path: Path, *, module_dir: Path | None = None) -> Any:
    _bootstrap_legacy_modules(module_dir)
    try:
        with path.open("rb") as stream:
            machine = _RestrictedMachineUnpickler(stream).load()
    except (OSError, pickle.PickleError, AttributeError, ImportError) as exc:
        raise ConversionError(f"cannot load Machine pickle {path}: {exc}") from exc
    return machine


def _default_output_path(input_path: Path) -> Path:
    stem = input_path.name
    for suffix in (".pickle", ".pkl"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    return input_path.with_name(f"{stem}.json.gz")


def build_argument_parser(*, default_input: Path | None = None) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, nargs="?" if default_input else None, default=default_input, help="Machine pickle")
    parser.add_argument("-o", "--output", type=Path, help="Layout Studio JSON path")
    parser.add_argument(
        "--report",
        type=Path,
        help="conversion report JSON (default: OUTPUT.report.json)",
    )
    parser.add_argument(
        "--module-dir",
        type=Path,
        help="directory containing standalone machine.py and ldbpoint.py",
    )
    parser.add_argument("--curve-name", help="output curve name; default machine.name")
    parser.add_argument(
        "--machine-length",
        type=float,
        help="explicit curve length/circumference in metres",
    )
    parser.add_argument(
        "--transverse-size",
        type=float,
        default=0.1,
        help="box dx and dy in metres (default: 0.1)",
    )
    parser.add_argument(
        "--point-length",
        type=float,
        default=0.1,
        help="display dz for zero-length objects in metres (default: 0.1)",
    )
    parser.add_argument("--span-type", action="append", default=[], help="LDB type whose length is a reference-curve span (repeatable)")
    parser.add_argument("--curved-type", action="append", default=[], help="LDB type with mechanical curvature matching its magnetic axis (repeatable)")
    parser.add_argument(
        "--dangling",
        choices=("skip", "error"),
        default="skip",
        help="policy for missing LDB ancestors (default: skip)",
    )
    parser.add_argument(
        "--indent",
        type=int,
        default=0,
        help="JSON indentation; use 0 for compact output (default: 0)",
    )
    return parser
