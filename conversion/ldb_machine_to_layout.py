#!/usr/bin/env python3
"""Public LDB-to-Layout-Studio converter with generic root/length handling.

The conversion implementation lives in :mod:`_ldb_machine_to_layout_core`.
This module adds robust discovery of the external LDB root (for example
``M2-LINE`` for machine ``M2``) and derives the curve length from objects
positioned directly on that root.  It deliberately never trusts the cached
``machine.ref_curve`` length, because the supplied ``Machine`` constructs that
path with the LHC circumference by default.
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any, Literal, Sequence

try:  # Package import when conversion is imported as a namespace/package.
    from . import _ldb_machine_to_layout_core as _core
except ImportError:  # Direct execution: python conversion/ldb_machine_to_layout.py
    import _ldb_machine_to_layout_core as _core

# Preserve the original public surface, then replace the functions/classes whose
# semantics are extended below.
for _name in dir(_core):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_core, _name))


@dataclass
class ConversionReport(_core.ConversionReport):
    """Conversion report including the resolved external LDB root."""

    root_name: str = ""
    root_name_source: str = ""


# The core constructs this class by looking it up in its module globals.
_core.ConversionReport = ConversionReport


def _external_reference_counts(machine: Any) -> Counter[str]:
    """Count references that are not themselves present as transformations."""

    transformations = machine.transformations
    return Counter(
        str(tf.ref)
        for tf in transformations.values()
        if str(tf.ref) not in transformations
    )


def resolve_root_name(
    machine: Any,
    root_name: str | None = None,
) -> tuple[str, str]:
    """Resolve the external LDB frame represented by the output curve.

    Ring snapshots normally use the machine name itself (``SPS`` or ``LHC``),
    while transfer lines may use an alias such as ``M2-LINE``.  Unrelated
    missing parent assets must not all be accepted as roots, so automatic
    resolution favours an exact machine-name match, then a machine-name alias,
    then a unique or clearly dominant external reference.
    """

    counts = _external_reference_counts(machine)
    if not counts:
        raise ConversionError(
            "cannot find an external LDB root; pass root_name explicitly"
        )

    if root_name is not None:
        explicit = str(root_name).strip()
        if not explicit:
            raise ConversionError("root_name must be non-empty")
        if explicit not in counts:
            candidates = ", ".join(repr(name) for name in sorted(counts))
            raise ConversionError(
                f"root_name {explicit!r} is not an external reference; "
                f"available candidates: {candidates}"
            )
        return explicit, "explicit argument"

    machine_name = str(machine.name).strip()
    if machine_name in counts:
        return machine_name, "exact machine-name match"

    # Match M2-LINE, M2_LINE, M2.LINE, etc., but not an unrelated M20 name.
    if machine_name:
        alias_pattern = re.compile(
            rf"^{re.escape(machine_name)}(?:[-_. /].*)?$",
            flags=re.IGNORECASE,
        )
        aliases = [name for name in counts if alias_pattern.fullmatch(name)]
        if aliases:
            aliases.sort(key=lambda name: (-counts[name], name))
            return aliases[0], "machine-name alias"

    if len(counts) == 1:
        return next(iter(counts)), "only external reference"

    ranked = counts.most_common()
    if ranked[0][1] >= 2 * ranked[1][1]:
        return ranked[0][0], "dominant external reference"

    candidates = ", ".join(
        f"{name!r} ({count} direct children)" for name, count in ranked
    )
    raise ConversionError(
        "cannot identify the machine root unambiguously; pass root_name "
        f"explicitly. External references: {candidates}"
    )


def infer_machine_length(machine: Any, root_name: str) -> tuple[float, str]:
    """Infer a ring circumference or transfer-line length from root coverage.

    The inferred value is the largest positive mechanical or optic endpoint of
    a non-rotated object attached directly to ``root_name``.  Transverse offsets
    do not affect the longitudinal coordinate.  The cached ``machine.ref_curve``
    is intentionally not a fallback because it may have been built with the LHC
    default circumference even for another machine.
    """

    top_level = [
        tf
        for tf in machine.transformations.values()
        if str(tf.ref) == root_name
        and _core._as_float(tf.rx, f"{tf.target}.rx") == 0.0
        and _core._as_float(tf.ry, f"{tf.target}.ry") == 0.0
        and _core._as_float(tf.rz, f"{tf.target}.rz") == 0.0
    ]

    ends: list[float] = []
    for tf in top_level:
        target_origin = _core._as_float(tf.tx, f"{tf.target}.tx") - _core._point_offset(
            tf, str(tf.target_point)
        )
        mechanical_end = target_origin + _core._as_float(
            tf.length, f"{tf.target}.length"
        ) / 2.0
        optic_end = (
            target_origin
            + _core._as_float(tf.optic_offset, f"{tf.target}.optic_offset")
            + _core._as_float(tf.optic_length, f"{tf.target}.optic_length") / 2.0
        )
        ends.append(max(mechanical_end, optic_end))

    positive_ends = [value for value in ends if value > 0.0]
    if positive_ends:
        return max(positive_ends), f"direct coverage of LDB root {root_name!r}"

    raise ConversionError(
        f"cannot infer machine length from LDB root {root_name!r}; "
        "pass machine_length explicitly"
    )


def machine_to_layout(
    machine: Any,
    *,
    curve_name: str | None = None,
    root_name: str | None = None,
    machine_length: float | None = None,
    transverse_size: float = 0.1,
    point_length: float = 0.1,
    zero_magnetic_length: float = 1e-9,
    dangling: Literal["skip", "error"] = "skip",
) -> ConversionResult:
    """Convert an LDB ``Machine`` with automatic root and length discovery."""

    for attribute in ("name", "version", "transformations", "get_ref_curve"):
        if not hasattr(machine, attribute):
            raise ConversionError(f"machine is missing required attribute {attribute!r}")

    original_machine_name = str(machine.name)
    output_curve_name = curve_name or original_machine_name
    resolved_root, root_source = resolve_root_name(machine, root_name)

    if machine_length is None:
        resolved_length, length_source = infer_machine_length(machine, resolved_root)
    else:
        resolved_length = _core._as_float(machine_length, "machine_length")
        if resolved_length <= 0.0:
            raise ConversionError("machine_length must be positive")
        length_source = "explicit argument"

    # The conversion core consistently uses machine.name as the external root.
    # Substitute only for the duration of the call, while preserving the actual
    # machine name as the output curve name and in the report.
    machine.name = resolved_root
    try:
        result = _core.machine_to_layout(
            machine,
            curve_name=output_curve_name,
            machine_length=resolved_length,
            transverse_size=transverse_size,
            point_length=point_length,
            zero_magnetic_length=zero_magnetic_length,
            dangling=dangling,
        )
    finally:
        machine.name = original_machine_name

    result.report.machine = original_machine_name
    result.report.machine_length_source = length_source
    result.report.root_name = resolved_root
    result.report.root_name_source = root_source
    return result


def build_argument_parser():
    parser = _core.build_argument_parser()
    parser.add_argument(
        "--root-name",
        help=(
            "external LDB root mapped to the output curve; normally detected "
            "automatically (for example M2-LINE for machine M2)"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    output = args.output or _core._default_output_path(args.input)
    report_path = args.report or output.with_name(f"{output.stem}.report.json")
    indent = None if args.indent == 0 else args.indent

    try:
        machine = load_machine_pickle(args.input, module_dir=args.module_dir)
        result = machine_to_layout(
            machine,
            curve_name=args.curve_name,
            root_name=args.root_name,
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
    print(f"LDB root {report.root_name!r} ({report.root_name_source})")
    if report.skipped_objects:
        print(
            f"warning: skipped {len(report.skipped_objects)} object(s); see {report_path}",
            file=sys.stderr,
        )
    print(f"wrote report {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
