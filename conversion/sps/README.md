# SPS LS3 conversion

This directory records the SPS LS3 test conversion from the CERN Layout
Database positioning model to Layout Studio.

## Reproduce

Place the trusted `SPS--LS3.pickle` snapshot in this directory, then run from
the repository root:

```bash
python conversion/ldb_machine_to_layout.py \
  conversion/sps/SPS--LS3.pickle \
  --output conversion/sps/SPS--LS3.layout.json \
  --report conversion/sps/SPS--LS3.layout-report.json
```

For compact output, add `--indent 0`. `SPS--LS3.files.json` records the exact
sizes and SHA-256 checksums of the input snapshot and prepared outputs.

## Conversion choices

- LDB local `tx` becomes Layout Studio `ts`.
- LDB `ty -> -tx`, `tz -> ty`, `rx -> -rs`, `ry -> rx`, and `rz -> -ry`.
- LDB transformation rotations are converted from degrees to radians; curve
  angle and roll are already radians in the snapshot.
- Mechanical middle becomes `center`; mechanical start/end become named frames.
- Optic middle/start/end become
  `magnetic_center`/`magnetic_entry`/`magnetic_exit`.
- The converter writes the magnetic curvature and roll explicitly; they match
  the converted shape path for this source model even though Layout Studio now
  treats the mechanical and magnetic axes independently.
- Shapes are boxes with 0.1 m transverse dimensions. Aperture/profile data is
  intentionally kept separate.
- Color is determined by the first character of each object name. Source types
  are split where instance geometry or required color differs.
- The LDB root is the exact machine-name match `SPS`.
- SPS circumference is inferred from direct root coverage by the six sextants as
  6911.51818896 m. The converter passes this explicitly to `get_ref_curve`;
  `--machine-length` overrides it.
- Zero mechanical lengths use a 0.1 m display box while their mechanical frames
  remain coincident. Zero optic lengths use `magnetic_length = 1e-9 m`.
- `VMFD.20902` is skipped because `BPCN.20902` is absent. Use
  `--dangling error` to abort instead.

## Result and validation

The generated model contains 12,339 objects, 712 output types and 1,489 curve
segments. The curve total angle is approximately `2*pi`.

Checks performed during preparation:

- Python syntax compilation.
- Strict canonical Layout Studio JSON structural validation.
- Recursive resolution of every emitted object without missing or ambiguous
  curve-station solutions.
- Reference-curve comparison at every segment boundary against
  `LDBPoint.to_madpoint()`: maximum position difference about `7.3e-12 m` and
  maximum orientation-matrix coefficient difference about `1.2e-15`.
