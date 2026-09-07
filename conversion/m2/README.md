# M2 LS3 conversion

Run `python conversion/m2/convert.py` from the repository root to regenerate
[`M2--LS3.json.gz`](M2--LS3.json.gz), its report, and checksum manifest from the
committed `M2--LS3.pickle`.

The M2 policy selects `M2-LINE` as the external LDB root. Direct root coverage
establishes a line length of **1185.5781 m**. This is supplied to
`get_ref_curve()`, ignoring the source class's cached LHC-length default.

Mechanical shapes are straight by default; magnetic curvature and roll are
independent. LDB optic anchors use each object's beam interface, inherited from
its magnetic axis unless an explicit center override is needed. Zero optic
lengths retain exact stored optic frames. There are no span type overrides for
this snapshot. See the [shared documentation](../README.md) for the complete
coordinate mapping and the `TX → ts` approximation.

The converted model has **428 objects, 115 types, and 29 curve segments**, with
no missing-parent omissions. All objects and 3,177 frames resolve through the
Python API. At all 30 curve boundaries, the source `LDBPoint.to_madpoint()` and
Layout Studio frames agree within `2.3e-13 m` and `5.6e-16` in rotation-matrix
coefficients.

```bash
python conversion/validate.py conversion/m2/M2--LS3.pickle conversion/m2/M2--LS3.json.gz
```
