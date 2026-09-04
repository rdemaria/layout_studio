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

Type conversion
---------------
The LDB mechanical middle becomes Layout Studio ``center``.  Two stored type
frames, ``mechanical_start`` and ``mechanical_end``, are generated.  LDB optic
middle/start/end map to Layout Studio's implicit ``magnetic_center``,
``magnetic_entry`` and ``magnetic_exit`` frames.  Box transverse dimensions are
configurable and default to 0.1 m.  The type curvature is the deflection angle
divided by optic length, matching ``Machine.get_ref_curve``.

Layout Studio requires positive shape and magnetic lengths.  A zero mechanical
length is displayed with ``point_length`` (default 0.1 m), while its generated
mechanical frames still coincide exactly.  A zero optic length is represented
by a tiny positive ``zero_magnetic_length`` (default 1e-9 m).

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
import importlib
import importlib.util
import json
import math
import pickle
import re
import sys
import types
from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping, Sequence


POINT_TO_LAYOUT_FRAME: dict[str, str] = {
    "MECHANICAL START": "mechanical_start",
    "MECHANICAL MIDDLE": "center",
    "MECHANICAL END": "mechanical_end",
    "OPTIC START": "magnetic_entry",
    "OPTIC MIDDLE": "magnetic_center",
    "OPTIC END": "magnetic_exit",
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
    "magnetic_center",
    "magnetic_entry",
    "magnetic_exit",
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
    optic_offset: float
    angle: float
    roll: float


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
    zero_magnetic_length_value: float = 1e-9
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


def infer_machine_length(machine: Any) -> tuple[float, str]:
    """Infer circumference/beamline length from top-level mechanical coverage.

    For SPS this uses the six ``TS*`` sextants attached directly to ``SPS`` and
    yields 6911.51818896 m.  The existing ``Machine.ref_curve`` is only a
    fallback because the supplied implementation creates it with an LHC-length
    default for every machine.
    """

    machine_name = str(machine.name)
    top_level = [
        tf
        for tf in machine.transformations.values()
        if str(tf.ref) == machine_name
        and _as_float(tf.ty, f"{tf.target}.ty") == 0.0
        and _as_float(tf.tz, f"{tf.target}.tz") == 0.0
        and _as_float(tf.rx, f"{tf.target}.rx") == 0.0
        and _as_float(tf.ry, f"{tf.target}.ry") == 0.0
        and _as_float(tf.rz, f"{tf.target}.rz") == 0.0
    ]

    ends: list[float] = []
    for tf in top_level:
        # The external machine root is treated as s=0.  This inference is used
        # only for direct, purely longitudinal children of that root.
        target_origin = _as_float(tf.tx, f"{tf.target}.tx") - _point_offset(
            tf, str(tf.target_point)
        )
        mechanical_end = target_origin + _as_float(
            tf.length, f"{tf.target}.length"
        ) / 2.0
        optic_end = (
            target_origin
            + _as_float(tf.optic_offset, f"{tf.target}.optic_offset")
            + _as_float(tf.optic_length, f"{tf.target}.optic_length") / 2.0
        )
        ends.append(max(mechanical_end, optic_end))

    positive_ends = [value for value in ends if value > 0.0]
    if positive_ends:
        inferred = max(positive_ends)
        return inferred, "top-level mechanical/optic coverage"

    ref_curve = getattr(machine, "ref_curve", None)
    dcum = getattr(ref_curve, "dcum", None)
    if dcum is not None and len(dcum):
        fallback = _as_float(dcum[-1], "machine.ref_curve length")
        if fallback > 0.0:
            return fallback, "existing machine.ref_curve"

    raise ConversionError(
        "cannot infer machine length; pass machine_length explicitly"
    )


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


def _layout_starting_frame_from_ldb(path: Any) -> dict[str, Any]:
    """Convert an LDBPath start frame through LDBPoint -> MADPoint."""

    start = getattr(path, "start", None)
    if start is None:
        return {"reference": {"kind": "world"}, "transformation": []}
    if not hasattr(start, "to_madpoint"):
        raise ConversionError("reference path start does not provide to_madpoint()")

    mad = start.to_madpoint()
    xyz = [_as_float(value, "curve start coordinate") for value in mad.xyz]
    theta, phi, psi = (
        _as_float(value, "curve start Euler angle")
        for value in mad.get_theta_phi_psi()
    )

    operations: list[list[Any]] = []
    # Translate in the fixed world axes before orienting the local frame.
    for name, value in zip(("tx", "ty", "tt"), xyz):
        if _nonzero(value):
            operations.append([name, value])

    # MADPoint stores R = Ry(theta) Rx(-phi) Rs(psi), while Layout Studio's
    # rx/ry/rs matrices are ordinary right-handed local rotations.
    for name, value in (("ry", theta), ("rx", -phi), ("rs", psi)):
        if _nonzero(value):
            operations.append([name, value])

    return {"reference": {"kind": "world"}, "transformation": operations}


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


def _type_key(object_name: str, tf: Any) -> TypeKey:
    return TypeKey(
        ldb_type=str(tf.target_type),
        color_initial=_object_initial(object_name),
        mechanical_length=_as_float(tf.length, f"{object_name}.length"),
        optic_length=_as_float(tf.optic_length, f"{object_name}.optic_length"),
        optic_offset=_as_float(tf.optic_offset, f"{object_name}.optic_offset"),
        angle=_as_float(tf.angle, f"{object_name}.angle"),
        roll=_as_float(tf.roll, f"{object_name}.roll"),
    )


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


def _make_layout_type(
    key: TypeKey,
    *,
    transverse_size: float,
    point_length: float,
    zero_magnetic_length: float,
) -> dict[str, Any]:
    if transverse_size <= 0.0:
        raise ConversionError("transverse_size must be positive")
    if point_length <= 0.0:
        raise ConversionError("point_length must be positive")
    if zero_magnetic_length <= 0.0:
        raise ConversionError("zero_magnetic_length must be positive")

    displayed_length = key.mechanical_length if key.mechanical_length > 0.0 else point_length
    magnetic_length = key.optic_length if key.optic_length > 0.0 else zero_magnetic_length

    if key.angle != 0.0:
        curvature_denominator = key.optic_length or key.mechanical_length
        if curvature_denominator <= 0.0:
            raise ConversionError(
                f"type {key.ldb_type!r} has a bend angle but no positive length"
            )
        curvature = key.angle / curvature_denominator
    else:
        curvature = 0.0

    magnetic_center_ops: list[list[Any]] = []
    if _nonzero(key.optic_offset):
        magnetic_center_ops.append(["ts", key.optic_offset])

    # These use the actual LDB mechanical length, even when point_length is used
    # merely to make a zero-length entity visible.
    start_shift = -key.mechanical_length / 2.0
    end_shift = key.mechanical_length / 2.0
    mechanical_start_ops = [["ts", start_shift]] if _nonzero(start_shift) else []
    mechanical_end_ops = [["ts", end_shift]] if _nonzero(end_shift) else []

    return {
        "shape": [
            "box",
            transverse_size,
            transverse_size,
            displayed_length,
            curvature,
            key.roll,
        ],
        "color": color_for_name(key.color_initial),
        "magnetic_center": {"transformation": magnetic_center_ops},
        "magnetic_length": magnetic_length,
        "frames": {
            "mechanical_start": {"transformation": mechanical_start_ops},
            "mechanical_end": {"transformation": mechanical_end_ops},
        },
    }


def _initial_dangling_objects(machine: Any) -> dict[str, str]:
    transformations = machine.transformations
    machine_name = str(machine.name)
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
    machine: Any,
    *,
    curve_name: str | None = None,
    machine_length: float | None = None,
    transverse_size: float = 0.1,
    point_length: float = 0.1,
    zero_magnetic_length: float = 1e-9,
    dangling: Literal["skip", "error"] = "skip",
) -> ConversionResult:
    """Convert an already-loaded LDB ``Machine`` to canonical Layout Studio JSON.

    Parameters
    ----------
    machine:
        A ``Machine``-like object exposing ``name``, ``version``,
        ``transformations`` and ``get_ref_curve``.
    curve_name:
        Output curve name.  Defaults to ``machine.name``.
    machine_length:
        Explicit finite curve domain.  When omitted, infer it from top-level
        mechanical/optic coverage and fall back to ``machine.ref_curve``.
    transverse_size:
        Box ``dx`` and ``dy`` in metres.
    point_length:
        Displayed ``dz`` for zero-mechanical-length entities.
    zero_magnetic_length:
        Positive approximation used where LDB optic length is zero.
    dangling:
        ``"skip"`` removes dangling objects and their descendants; ``"error"``
        aborts conversion.
    """

    for attribute in ("name", "version", "transformations", "get_ref_curve"):
        if not hasattr(machine, attribute):
            raise ConversionError(f"machine is missing required attribute {attribute!r}")

    if dangling not in {"skip", "error"}:
        raise ConversionError("dangling must be 'skip' or 'error'")

    machine_name = str(machine.name)
    output_curve_name = curve_name or machine_name
    if not output_curve_name:
        raise ConversionError("curve_name must be non-empty")

    if machine_length is None:
        resolved_length, length_source = infer_machine_length(machine)
    else:
        resolved_length = _as_float(machine_length, "machine_length")
        if resolved_length <= 0.0:
            raise ConversionError("machine_length must be positive")
        length_source = "explicit argument"

    transformations: Mapping[str, Any] = machine.transformations
    report = ConversionReport(
        machine=machine_name,
        version=str(machine.version),
        curve_name=output_curve_name,
        machine_length=resolved_length,
        machine_length_source=length_source,
        input_transformations=len(transformations),
        input_type_names=len({str(tf.target_type) for tf in transformations.values()}),
        zero_length_display_value=point_length,
        zero_magnetic_length_value=zero_magnetic_length,
    )

    skipped = _initial_dangling_objects(machine)
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

    keys_by_base: dict[str, set[TypeKey]] = defaultdict(set)
    key_for_object: dict[str, TypeKey] = {}
    for object_name, tf in transformations.items():
        object_name = str(object_name)
        if object_name in skipped:
            continue
        key = _type_key(object_name, tf)
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
            zero_magnetic_length=zero_magnetic_length,
        )

    output_objects: dict[str, Any] = {}
    for object_name, tf in transformations.items():
        object_name = str(object_name)
        if object_name in skipped:
            continue

        try:
            target_frame = POINT_TO_LAYOUT_FRAME[str(tf.target_point)]
            reference_frame = POINT_TO_LAYOUT_FRAME[str(tf.ref_point)]
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
            f"represented {report.zero_optic_length_objects} zero optic length(s) with "
            f"magnetic_length={zero_magnetic_length:g} m"
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
            referenced_type = types_[objects[object_name]["type"]]
            frame = value.get("frame")
            if frame not in IMPLICIT_FRAMES and frame not in referenced_type["frames"]:
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
        if not type_name or set(type_) != {
            "shape",
            "color",
            "magnetic_center",
            "magnetic_length",
            "frames",
        }:
            raise ConversionError(f"invalid type {type_name!r}")
        color(type_["color"], f"types.{type_name}.color")
        shape = type_["shape"]
        if not isinstance(shape, list) or len(shape) != 6 or shape[0] != "box":
            raise ConversionError(f"types.{type_name}.shape is not a Layout Studio box")
        for index in (1, 2, 3):
            if finite(shape[index], f"types.{type_name}.shape[{index}]") <= 0.0:
                raise ConversionError(f"types.{type_name} shape dimensions must be positive")
        finite(shape[4], f"types.{type_name}.shape curvature")
        finite(shape[5], f"types.{type_name}.shape roll")
        if finite(type_["magnetic_length"], f"types.{type_name}.magnetic_length") <= 0.0:
            raise ConversionError(f"types.{type_name}.magnetic_length must be positive")
        magnetic_center = type_["magnetic_center"]
        if not isinstance(magnetic_center, Mapping) or set(magnetic_center) != {"transformation"}:
            raise ConversionError(f"types.{type_name}.magnetic_center is invalid")
        operations(magnetic_center["transformation"], f"types.{type_name}.magnetic_center")
        frames = type_["frames"]
        if not isinstance(frames, Mapping):
            raise ConversionError(f"types.{type_name}.frames must be a dictionary")
        if IMPLICIT_FRAMES.intersection(frames):
            raise ConversionError(f"types.{type_name}.frames uses a reserved name")
        for frame_name, frame in frames.items():
            if not frame_name or not isinstance(frame, Mapping) or set(frame) != {"transformation"}:
                raise ConversionError(f"types.{type_name}.frames.{frame_name} is invalid")
            operations(frame["transformation"], f"types.{type_name}.frames.{frame_name}")

    dependencies: dict[str, list[str]] = {}
    for object_name, obj in objects.items():
        if not object_name or not isinstance(obj, Mapping) or set(obj) != {"type", "position"}:
            raise ConversionError(f"invalid object {object_name!r}")
        type_name = obj["type"]
        if type_name not in types_:
            raise ConversionError(f"object {object_name!r} references unknown type {type_name!r}")
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
        if target not in IMPLICIT_FRAMES and target not in types_[type_name]["frames"]:
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
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=indent, ensure_ascii=False, allow_nan=False)
        stream.write("\n")


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
    return input_path.with_name(f"{stem}.layout.json")


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Machine pickle")
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
    parser.add_argument(
        "--zero-magnetic-length",
        type=float,
        default=1e-9,
        help="positive approximation for zero optic length (default: 1e-9)",
    )
    parser.add_argument(
        "--dangling",
        choices=("skip", "error"),
        default="skip",
        help="policy for missing LDB ancestors (default: skip)",
    )
    parser.add_argument(
        "--indent",
        type=int,
        default=2,
        help="JSON indentation; use 0 for compact output (default: 2)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    output = args.output or _default_output_path(args.input)
    report_path = args.report or output.with_name(f"{output.stem}.report.json")
    indent = None if args.indent == 0 else args.indent

    try:
        machine = load_machine_pickle(args.input, module_dir=args.module_dir)
        result = machine_to_layout(
            machine,
            curve_name=args.curve_name,
            machine_length=args.machine_length,
            transverse_size=args.transverse_size,
            point_length=args.point_length,
            zero_magnetic_length=args.zero_magnetic_length,
            dangling=args.dangling,
        )
        write_json(output, result.layout, indent=indent)
        write_json(report_path, asdict(result.report), indent=2)
    except ConversionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    report = result.report
    print(
        f"wrote {output} with {report.output_objects} objects, "
        f"{report.output_types} types and {report.curve_segments} curve segments"
    )
    print(
        f"curve {report.curve_name!r}: {report.machine_length:.12g} m "
        f"({report.machine_length_source})"
    )
    if report.skipped_objects:
        print(
            f"warning: skipped {len(report.skipped_objects)} object(s); see {report_path}",
            file=sys.stderr,
        )
    print(f"wrote report {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
