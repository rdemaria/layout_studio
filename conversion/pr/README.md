# PR LS3 conversion

Run `python conversion/pr/convert.py` from the repository root to regenerate
[`PR--LS3.json.gz`](PR--LS3.json.gz), its report, and checksum manifest from the
committed `PR--LS3.pickle`.

The PR policy selects the external root `PS`. Direct sector coverage establishes
a circumference of **628.3185 m**. The output has **4,623 objects, 595 types, and
300 curve segments**, with a total bend angle within `2e-10 rad` of `2*pi`.

These 310 lattice containers are longitudinal spans without solid shapes:

| Source type | Objects |
| --- | ---: |
| `PS RING SECTOR` | 10 |
| `PS RING SECTION` | 100 |
| `PS RING STRAIGHT SECTION` | 100 |
| `PS RING UNIT ASSEMBLY SECTION` | 100 |

The 200 focusing/defocusing bends remain separate. Their touching boundaries
produce zero or rounding-sized drifts. The path builder omits 101 drifts with
absolute length at most `1e-10 m`, including the final drift, while rejecting
larger overlaps. It preserves the source bend lengths, angles, and rolls.

Hardware uses straight mechanical geometry and independent magnetic/beam axes.
The 17 span-boundary references at the ring seam are rebased onto their parent
span's center, preserving the hierarchy. See the [shared documentation](../README.md)
for the coordinate mapping and the `TX → ts` approximation.

**266 objects are omitted** because their ancestors are unavailable. The missing
external parents are `PE.BFA21S.MK`, `PE.BFA21S.RK`, `PE.BFA09P21P.RK`,
`PE.BFA21P.MK`, and `PE.BFA21P.RK`. Every omission is recorded in
`PR--LS3.layout-report.json`; `--dangling error` makes these missing parents
fatal. The source is unchanged.

All 4,623 objects and 42,147 available frames resolve through the Python API.
The 301 reference-curve boundary frames agree with the prepared source path
within `9e-14 m`; all 930 span frames agree within `1.3e-12 m` and `1.1e-14`
in rotation-matrix coefficients.

```bash
python conversion/validate.py conversion/pr/PR--LS3.pickle conversion/pr/PR--LS3.json.gz
python conversion/pr/convert.py --output /tmp/PR--LS3.json
```

Use the plain `/tmp/PR--LS3.json` with the viewer's **Import file** action.
