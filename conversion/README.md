# CERN Layout Database conversion

Convert the committed CERN LDB `Machine` snapshots into the current Layout
Studio model. Shared conversion logic lives in `ldb_machine_to_layout.py` and
`_ldb_machine_to_layout_core.py`. Short machine scripts select assumptions
that cannot reliably be inferred from a database length alone.

## Reproduce

From the repository root:

```bash
python -m pip install -r conversion/requirements.txt
python conversion/sps/convert.py
python conversion/m2/convert.py
```

Each script reads its adjacent `MACHINE--LS3.pickle`, writes
`MACHINE--LS3.json.gz`, and refreshes its report and SHA-256 manifest.
Gzip output has no timestamp or original filename, so repeated conversions
with the same inputs produce the same bytes. Plain `.json` output is supported
with `--output`; `--indent 2` enables pretty printing.

The supplied `machine.py` and `ldbpoint.py` support reading the snapshots without
a CERN database connection. The loader uses an installed `cernlayoutdb` package
when available and otherwise loads these bundled modules. Pickles are trusted
project inputs; the loader restricts the allowed classes.

## Coordinate and frame conventions

LDB uses longitudinal X, inward Y, and vertical Z; Layout Studio uses MADPoint
coordinates: horizontal x, vertical y, and longitudinal s. Conversion follows
the supplied `LDBPoint.to_madpoint()`, including rotation signs.

| Source operation | Layout Studio operation |
| --- | --- |
| `TX` | `ts` |
| `TY` | `-tx` |
| `TZ` | `ty` |
| `RX` | `-rs` |
| `RY` | `rx` |
| `RZ` | `-ry` |

Source transformation rotations are degrees and become radians. Deflection
angle and roll are already radians. The source operation list retains its
order. Every source positioning `TX` becomes `ts`, including when rotations
precede it. Layout Studio applies its normal path-coordinate semantics; this
remains an approximation wherever LDB intended a straight tangent displacement.
`reference_curve` is supplied for object-frame references using `ts`.

The reference path comes from `Machine.get_ref_curve()`. Its LDBPath roll is
negated when encoding Layout Studio segments, and its initial frame is
converted through `to_madpoint()`.

| LDB point | Output frame |
| --- | --- |
| Mechanical middle | `center` |
| Mechanical start/end | `mechanical_start` / `mechanical_end` |
| Optic middle/start/end with positive optic length on hardware | `beam_center` / `beam_entry` / `beam_exit` |
| Optic middle/start/end with zero optic length or on a span | Stored `optic_center` / `optic_start` / `optic_end` |

## Mechanical geometry and beam interfaces

Physical components use straight mechanical geometry by default, including
SPS dipoles. Boxes have 0.1 m transverse dimensions. Mechanical end frames use
true tangent offsets of ±mechanical_length/2; these derived geometry offsets
use `tt`, separately from the source positioning `TX → ts` rule. Color depends
on the first character of the object name. Aperture/profile data remain separate.

For positive optic lengths, the available magnetic length is the source optic
length and magnetic curvature is `deflection_angle / optic_length`. Source
mechanical-to-magnetic offsets define the magnetic center. Missing longitudinal
offsets fall back to `optic_offset`; missing radial/vertical offsets to zero.
Mechanical and magnetic paths are independent.

LDB optic points address the object's beam interface. It inherits the magnetic
axis when their centers agree. If the optic center differs, a complete beam
group is written on that object, preserving the shared magnetic type.
No beam fields are written on types.

A zero mechanical length retains the existing 0.1 m display box, while its
mechanical frames coincide exactly. Zero optic lengths produce coincident
stored optic frames, with no artificial magnetic length or beam axis.

## Machine policies for s-spans

SPS explicitly classifies `SPS SEXTANT`, `SPS HALF ARC`, `SPS LSS`, and
`SPS PERIOD` as longitudinal spans. M2 currently needs only its `M2-LINE` root
alias and uses straight mechanical lengths.

For a span, resolve its station interval from the longitudinal source hierarchy.
Sample its boundary and middle frames on the complete piecewise reference curve.
Store each boundary relative to the middle:

```text
local_boundary = inverse(curve_frame(s_middle)) @ curve_frame(s_boundary)
```

This preserves parent references and gives exact boundary origins and
orientations across multiple bends and drifts. Logical containers have no solid
shape or magnetic/beam axis. Type variants hold their different local frames.
A kilometre-long sextant is neither drawn as straight hardware nor approximated
with one constant-curvature arc.

Sampled local frames describe the converted snapshot. Rerun conversion after
changing its reference path or span lengths. Offset or rotated span ancestors
are rejected rather than interpreted as on-curve station intervals.

The generic module can be used directly:

```python
from conversion.ldb_machine_to_layout import machine_to_layout

result = machine_to_layout(machine, span_types={"MY CELL"}, curved_types={"CURVED PIPE"})
document = result.layout
```

`curved_types` explicitly selects hardware whose mechanical curvature and roll
should match the magnetic axis. Do not select it just because a magnet bends
the beam. CLI equivalents are repeatable `--span-type` and `--curved-type`
flags. Add a short machine script when additional choices are needed, following
`sps/convert.py` and `m2/convert.py`.

Root discovery prefers explicit `--root-name`, the machine name, a matching
alias, then a unique or clearly dominant external reference. Machine length
comes from direct root coverage, never cached `machine.ref_curve`, which the
source class may have built with the LHC default circumference. Override
ambiguous cases with `--machine-length`. Missing object ancestors are reported
and skipped with their descendants; `--dangling error` aborts instead.

## Validation

```bash
python conversion/validate.py conversion/sps/SPS--LS3.pickle conversion/sps/SPS--LS3.json.gz
python conversion/validate.py conversion/m2/M2--LS3.pickle conversion/m2/M2--LS3.json.gz
PYTHONPATH=python_api/src python -m pytest conversion/tests
```

The validator uses this checkout's Python API to check the canonical schema,
resolve every object and available frame, compare reference-curve boundaries
with `LDBPoint.to_madpoint()`, and compare span frames at their source stations.
Regression tests cover all six axis mappings, rotation signs, mixed curved
spans, independent straight mechanics, object beam overrides, exact zero-length
anchors, root aliases, missing parents, and reproducible gzip output.
