# BR LS3 conversion

Run `python conversion/br/convert.py` from the repository root to regenerate
[`BR--LS3.json.gz`](BR--LS3.json.gz), its report, and checksum manifest from the
committed `BR--LS3.pickle`.

The BR policy selects the external root `PSB` and treats the 16 `PSB PERIOD`
containers as longitudinal spans. Their coverage establishes a circumference
of **157.08 m**. The output has **2,070 objects, 196 types, and 65 curve
segments**, with a total bend angle of `-2*pi`.

The source lists 128 bends: four identical ring copies at each of 32 stations.
The reference path merges only bends with identical start/end stations, optic
lengths, angles, and rolls, removing 96 duplicates from the path. All four sets
of objects and their hierarchy remain in the model. The snapshot places their
ring bends at the same height; no vertical separation is invented.

The periods have no solid shape. Other hardware uses the common straight
mechanical model with independent magnetic and beam axes. At the ring seam,
86 references are rebased onto their parent period's center while retaining
the original parent. See the [shared documentation](../README.md) for the
coordinate mapping and the `TX → ts` approximation.

**295 objects are omitted** because their ancestors are unavailable. The missing
external parents are `BI.DIS10.CK`, `BI.DIS10.RK`, `BI.DIS10.FK`, and
`BI.DIS10.MK`. Every omission is recorded in `BR--LS3.layout-report.json`;
`--dangling error` makes these missing parents fatal. The source is unchanged.

All 2,070 objects and 19,998 available frames resolve through the Python API.
The 66 reference-curve boundary frames and 48 period frames agree with the
prepared source path within `2e-14 m` and `5e-16` in rotation-matrix coefficients.

```bash
python conversion/validate.py conversion/br/BR--LS3.pickle conversion/br/BR--LS3.json.gz
python conversion/br/convert.py --output /tmp/BR--LS3.json
```

Use the plain `/tmp/BR--LS3.json` with the viewer's **Import file** action.
