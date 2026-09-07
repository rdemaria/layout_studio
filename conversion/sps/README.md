# SPS LS3 conversion

Run `python conversion/sps/convert.py` from the repository root. It reads the
committed `SPS--LS3.pickle` using the shared converter and writes
[`SPS--LS3.json.gz`](SPS--LS3.json.gz), a report, and a checksum manifest.

The SPS policy in `convert.py` classifies these source types as s-spans:

| Source type | Objects |
| --- | ---: |
| `SPS SEXTANT` | 6 |
| `SPS HALF ARC` | 12 |
| `SPS LSS` | 6 |
| `SPS PERIOD` | 216 |

Their start/middle/end frames follow the actual piecewise reference curve.
They retain the source hierarchy, with no misleading solid box. All other
mechanical shapes are straight, including the `SPMBA__` and `SPMBB__` dipoles;
their magnetic and inherited beam axes carry the bend curvature independently.
See the [shared documentation](../README.md) for axis signs, units,
optic frame naming, and the `TX → ts` approximation.

The six sextants establish a circumference of **6911.51818896 m**. The converter
passes this explicitly to `Machine.get_ref_curve()` instead of using its cached
LHC-length default. The rebuilt path has 1,489 segments and a total angle close
to `2*pi`.

The output contains **12,339 objects**. Type variants preserve source
geometry/color differences and sampled local span geometry. Only the compressed
model is committed; exact sizes and checksums are in `SPS--LS3.files.json`.

`VMFD.20902` is omitted because its parent `BPCN.20902` is missing from the
snapshot. This is recorded in `SPS--LS3.layout-report.json`; use `--dangling error`
to make it fatal. The original snapshot is unchanged.

Validation:

- All 12,339 objects and 79,827 available frames resolve through the Python API.
- All 1,490 reference-curve boundary frames agree with the source
  `LDBPoint.to_madpoint()` frames within `7e-12 m`.
- All 720 mechanical start/middle/end frames of the 240 spans agree with their
  source curve stations within `8e-12 m` and `5e-15` in rotation-matrix coefficients.

```bash
python conversion/validate.py conversion/sps/SPS--LS3.pickle conversion/sps/SPS--LS3.json.gz
```

Span frames are sampled for this snapshot. Regenerate the conversion if its
reference path or span lengths change.
