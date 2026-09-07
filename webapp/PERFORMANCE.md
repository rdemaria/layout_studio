# SPS loading performance

Analysis on 2026-09-07, using `public/layouts/SPS--LS3.json` from commit
`0775f2492252eb01d57ee73283f7c8150fd94608`:

- 2,900,133 bytes of plain JSON; 12,339 objects and 914 types.
- One reference curve with 1,489 segments and 19,267 display samples.
- 96,792 mechanical vertices, 55,902 named frames, and 1,931 magnetic axes
  with their inherited beam interfaces.

## Findings

The complete browser load stalled even though parsing and geometry generation
finished separately. Loading a large curve closes its segment section, but the
collapsible component retains its contents during the closing transition. The
new SPS contents could therefore mount 1,489 rows and 4,467 numeric inputs before
being hidden. Numeric inputs measure their underline position during layout,
making this transient mount particularly expensive. Small sample layouts and
parsing-only tests did not exercise this transition.

The reference editor also rendered a native option for each candidate object
and rebuilt its reverse dependency arrays by copying them for every edge. The
SPS object selector alone produced about 1.46 MB of HTML.

Geometry generation had a separate, measurable cost: 9,811 `ts` placements
performed transverse-plane searches, but they used only 2,762 distinct reference
frames. The CPU profile was dominated by `transverseCurvePathsForPoint`, its
tolerance computation, and vector operations. These searches implement the
specified positioning semantics; changing those semantics is unnecessary to fix
the loading stall.

## Changes and measurements

| Stage | Before | After |
| --- | ---: | ---: |
| JSON parsing | 21 ms | 20 ms |
| Model validation | 128 ms | 123 ms |
| Complete geometry generation | 1,795 ms | 845 ms |
| Object reference editor, server rendering | 350 ms | 31 ms |
| Object reference editor HTML | 1,458,004 bytes | 12,309 bytes |
| Hidden segment controls | Could mount 4,467 inputs during closing | 0 |
| Expanded segment controls | 4,467 inputs | At most 150 inputs |

The timings are representative measurements in the same Linux x64 environment
with Node 24.19.0, excluding module loading. They are stage measurements, not a
prediction of browser load times. The collapsed dependency tree took about
120 ms and did not cause the stall.

Hidden segment contents now return immediately, including during closing
transitions. Expanded segments are paged in groups of 50. Viewer and Python
segment selections open the corresponding page; editing and removing rows use
absolute segment indices. Adding a segment opens its page.

Object/frame references use the existing searchable picker. Reverse dependency
lists are built by appending entries and memoized for the current layout.
Cycle-producing references remain excluded.

Station inference is cached by curve and resolved reference origin for one scene
build. A new build after an edit gets a new cache. A SHA-256 comparison of the
generated curve geometry, object frames/vertices, named frames, and feature
geometry was identical before and after this optimization:
`9542d69405fb89f47d5b51c0e0127d5e8c44d697db380a85768a72fe621f39a6`.

Browser checks reproduced the original stall. With the changes, initial and
repeat SPS loads completed, including reloading with the segment editor open.
Observed load-to-status times were approximately 3–8 seconds in the cloud
development preview, including transfer and automation overhead. Opening the
first segment page took about 0.6 seconds; moving to the next page replaced rows
1–50 with rows 51–100.

## Reproducing the analysis

From `webapp/`, with dependencies installed:

```bash
node scripts/profile-sps.mjs
node scripts/profile-sps.mjs scene
node --cpu-prof --cpu-prof-dir=/tmp scripts/profile-sps.mjs scene
node --test tests/sps-performance.test.mjs
```

The profiler also accepts `controls`, `tree`, or `segments`. It reports time,
heap use, geometry counts/fingerprint, and rendered control counts. CPU profiles
are diagnostic artifacts and should not be committed.

The SPS regression tests run in `make test`/CI. They check complete geometry and
beam inheritance, recomputation after moving the curve, zero hidden segment
controls, bounded pages with correct indices, and bounded reference selector
markup. Geometry has a generous 10-second regression ceiling; UI counts are
checked directly so they do not depend on machine speed.
