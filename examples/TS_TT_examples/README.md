# TS and TT examples for Layout Studio

Ten loadable JSON layouts and eleven scientific plots for the accompanying
Google Slides presentation. All positions, orientations and plotted path points
are evaluated by Layout Studio's Python resolver. Matplotlib formats the plots.
The plots use `aspect='auto'`: horizontal and vertical scales differ.

The examples use the existing schema without changing Layout Studio.
They are based on repository commit
[`415b051e7ca97625ef920f587dbbd0c108a8422a`](https://github.com/rdemaria/layout_studio/tree/415b051e7ca97625ef920f587dbbd0c108a8422a).

## Files and cases

| File in `cases/` | Meaning |
|---|---|
| `01_mechanical_tangent.json` | Straight mechanical-axis TT compared with TS on a curved global reference. Local mechanical TS equals TT for this straight type. |
| `02_sbend_magnetic.json` | TS along an ideal sector bend's magnetic curve versus TT from its entrance. |
| `03_rbend_beam.json` | A straight-body rectangular bend with a straight magnetic axis and a curved beam reference. |
| `04_multiple_elements.json` | A schematic 60 m reference spanning bends and drifts. |
| `05_lhc_s12.json` | Exact 309-segment excerpt of the LHC reference over S12, 0–3332.3604 m. Equipment is omitted. |
| `06_offset_magnetic.json` | Constant offset from a sector bend's magnetic curve: same reference station versus same physical length. |
| `07_offset_beam.json` | The equivalent comparison for the rectangular bend's beam frame. |
| `08_offset_reference.json` | The equivalent comparison across a piecewise reference. |
| `09_offset_projection_trap.json` | Inferred object-position TS drops the old transverse offset unless it is reapplied. |
| `10_local_order_trap.json` | Local TS is sequential, whereas explicit curve TS collects station increments first. |

`plots/` contains PNG and vector SVG files. Plot 11 shows the physical-length
correction for a constant outward 100 mm offset over the actual S12 reference.
`results.json` records the input parameters and exact resolved marker frames.
Coordinates and lengths are in metres; angles are in radians.

## Viewing and regeneration

In the Layout Studio web app, use the file-load control and choose any case JSON.
Enable mechanical, magnetic and beam axes as needed. Names identify the endpoints:
`A` is the initial frame, `TS` and `TT` are the two displacement results,
`EqualPhysicalLength` preserves the requested physical length, and
`OffsetCurveTS` is an independent encoding of that same frame.

With a Layout Studio checkout and its built standalone viewer:

```bash
python open_case.py cases/03_rbend_beam.json --repo /path/to/layout_studio
```

To regenerate all cases and plots, install NumPy and Matplotlib in your Python
environment, then run:

```bash
python build_cases.py --repo /path/to/layout_studio
```

The builder reads the repository's LHC JSON and RBend example. Use the pinned
commit above to reproduce these results. `cases/list.json` is a compatible catalog
with paths relative to the `cases` directory.

## Geometric distinction

TT translates an origin by `L * current_tangent` and leaves the orientation fixed.
TS on a selected curve evaluates its frame at the requested station, so both
origin and orientation generally change. A magnetic field centerline, a
mechanical shape centerline and the reference used by beam dynamics are separate
geometric choices.

The RBend case uses the current project's symmetric straight-body construction:
straight magnetic length 4 m, bend angle 0.6 rad, zero asymmetry and shift, and
sagitta compensation enabled. Its beam arc length is 4.060636034188947 m.
It illustrates coordinate geometry, not particle tracking or field integration.
The large offsets and bend angles in the teaching cases are deliberately chosen
to make the differences visible; they do not represent alignment tolerances or
aperture limits.

## Offset path length

For a constant local transverse offset,

\[
q(s)=r(s)+x e_x(s)+y e_y(s),\qquad
\frac{d\ell}{ds}=1+h(s)[x\cos\rho(s)+y\sin\rho(s)].
\]

This uses Layout Studio's non-twisting segment frame, positive bend toward
negative local x at zero roll, and a regular offset for which the right-hand
side stays positive. Here rho is the bend-plane roll, not an independent twist.

For planar bends at constant x:

\[
\ell(s_1)-\ell(s_0)=s_1-s_0+x[\theta(s_1)-\theta(s_0)].
\]

Thus equal reference station and equal physical length are different conditions.
At constant curvature, equal physical length L requires `delta_s=L/(1+h*x)`.
Across multiple segments, integrate the local metric and invert it segment by
segment. A drift has factor one. An inward offset can shorten the path.

For varying x(s), y(s), the metric instead is

\[
\frac{d\ell}{ds}=\sqrt{[1+h(x\cos\rho+y\sin\rho)]^2+(x')^2+(y')^2}.
\]

The offset JSON cases provide an explicit `offset_path` as a second encoding.
Each segment retains its bend angle and roll, but its physical length becomes
`L_i * [1 + h_i*(x*cos(rho_i)+y*sin(rho_i))]`. For these planar examples y=0
and rho=0. Starting that curve at the offset origin makes its TS coordinate
the physical offset-path length. This is ordinary supported curve geometry,
not a new TS option.

| Offset example | Nominal L | Offset x | Physical length at station L | Station for physical length L |
|---|---:|---:|---:|---:|
| Magnetic path | 5 | 1 | 5.5 | 4.545454545 |
| RBend beam reference | 4.060636034 | 0.5 | 4.360636034 | 3.781275225 |
| Piecewise reference | 60 | 2 | 61.2 | 58.8 |

On the exact S12 reference, a constant outward 0.1 m offset adds
`0.1 * pi/4 = 0.07853981634 m` of physical length over the sector.

## Current TS evaluation rules

1. **Explicit curve reference:** all `ts` entries sum to an absolute station.
   The remaining operations then execute in their original order. A preceding
   `tx` does not alter the curve station or create an independently parametrized
   offset path.
2. **Object position referenced to world or another object frame:** any `ts`
   entry requires `reference_curve`. The resolver infers a station from the
   unmodified reference origin, advances it, replaces the origin and orientation
   by the curve frame, then applies the other operations. Reapply the required
   offsets and rotations explicitly. Even `ts=0` can change an off-curve frame.
3. **Local feature placement:** operations execute sequentially. Local `ts`
   uses the owning type's mechanical curvature, even when its reference is named
   `magnetic_center` or `beam_entry`. It does not automatically select that
   feature's own curvature. The supplied path-specific examples therefore declare
   explicit reference curves rooted at the corresponding axis entrance frame.

The current UI uses “start” and “end” labels for magnetic axes, while the JSON/API
frame identifiers remain `magnetic_entry` and `magnetic_exit`.

## Sources

- [Positioning specification](https://github.com/rdemaria/layout_studio/blob/415b051e7ca97625ef920f587dbbd0c108a8422a/specifications/layout_positioning_model.tex)
- [Python geometry resolver](https://github.com/rdemaria/layout_studio/blob/415b051e7ca97625ef920f587dbbd0c108a8422a/python_api/src/layout_studio/resolver.py)
- [Project RBend construction](https://github.com/rdemaria/layout_studio/blob/415b051e7ca97625ef920f587dbbd0c108a8422a/python_api/examples/xsuite_rbend_axes.py)
- [LHC LS3 source layout](https://github.com/rdemaria/layout_studio/blob/415b051e7ca97625ef920f587dbbd0c108a8422a/webapp/public/layouts/LHC--LS3.json.gz)
- [Xsuite RBend reference](https://xsuite.readthedocs.io/en/latest/apireference.html#rbend)
