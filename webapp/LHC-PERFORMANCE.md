# LHC LS3 conversion and viewer performance

Measured on 2026-09-07 against the source snapshot added in `abfe848`.
The viewer now uses automatic detail levels, spatial culling, background loading
and progressive scene construction. The same complete LHC model is interactive
in the overview and tested close-ups; the original eager rendering baseline is
retained below for comparison.

Publication status: the converter, report and profiling tools are committed.
The 35.6 MB JSON upload failed through the available GitHub connection, so the
dataset and its catalog entry are not included in that publication. Generate
the JSON with the command below, or import the separately supplied JSON file.

## Converted data and validation

- Plain JSON: 35,611,379 bytes; 161,941 objects and 4,764 types.
- Circumference: 26,658.8832 m; 2,465 analytic reference-curve segments.
- 578 logical containers use path spans. Hardware uses the shared straight
  mechanical default and approximate 0.1 m transverse boxes.
- 817 objects are omitted because their ancestors are absent from the snapshot.
- 840 objects with unavailable mechanical lengths remain as shapeless anchors.
- 82,701 explicitly zero-length objects retain the converter's small display box.
- 37 seam references use the parent span's midpoint plus the known boundary
  offset to avoid ambiguous closed-ring station inference while preserving the
  object hierarchy. The earlier direct-to-curve workaround left S12 without
  children in the hierarchy; the midpoint reference restores its branch.
  Comparing all 322,464 anchor and mechanical-center frames with that output
  gives a maximum position difference of 3.19e-12 m and direction-component
  difference of 1.12e-15. Types and reference curves are unchanged.

The Python validator resolved all 161,941 objects and 1,177,217 frames. It
compared 2,466 curve boundaries and 1,734 span frames with the source converted
through `LDBPoint.to_madpoint()`. Maximum positional differences were
1.45e-11 m for the curve and 3.19e-11 m for spans. These checks validate coordinate
conversion and internal resolution; they do not establish surveyed mechanical
envelopes or recover the missing ancestors. The conversion report and SHA-256
manifest are in `conversion/lhc/`.

## Original eager-rendering baseline

Browser measurements used the cloud Chrome development preview, default view,
all objects enabled, and other optional object layers disabled. No artificial
network or CPU throttling was applied. The settled canvas was approximately
512 × 613 CSS pixels at device pixel ratio 1.

| Browser stage | Observed time |
| --- | ---: |
| Fetch and JSON decode | 0.64 s |
| Model validation | 3.93 s |
| Scene construction | 7.07 s |
| First load through final redraw | 51.7 s |
| Fitted redraw before canvas resize | 9.35 s |
| Redraw after canvas resize | 19.60 s |
| Redraw with objects hidden, curve visible | 0.079 s |

The first load includes React/control work and three draws: initial camera,
fit, and resize. It is measured from the fetch start to completion of the final
draw, using console timestamps and stage durations. Drawing measurements time
Canvas command submission, not GPU presentation or sustained animation FPS.
These are diagnostic observations from one environment, not production SLAs.

Separate Linux/Node 24.19.0 runs, excluding module loading, gave:

| Node stage | Observed time |
| --- | ---: |
| JSON parsing | 0.38–0.58 s |
| Model validation | 4.70–5.70 s |
| Scene construction | 6.84–6.89 s |
| Curve reference editor, server rendering | 5.36 s |
| Object reference editor, server rendering | 2.98 s |
| Collapsed dependency tree | 0.94 s |

The scene contains 1,284,184 mechanical vertices, 963,138 faces, 1,926,276
edges, 761,297 stored frames, 15,576 magnetic axes and their inherited beam
interfaces, plus 65,285 curve samples. Heap use immediately after scene
construction was about 1,478 MiB, including the retained input and validated
model; this is neither browser memory nor a peak-memory measurement.

The viewer projects every solid, allocates and sorts its faces, and draws its
edges. Subpixel and offscreen solids still incur this work. Hit testing and
zoom also retain the projected geometry. Hidden frames and axes are built
eagerly. Each reference editor separately builds a complete frame dependency
index and filters every object's available frames, even for a closed picker.

At the default whole-layout fit, projected bounding-box measurements give:

| Canvas | Solids | Smaller than 1 px | Smaller than 2 px |
| --- | ---: | ---: | ---: |
| 512 × 613 | 160,523 | 160,512 (99.993%) | 160,519 |
| 1200 × 800 | 160,523 | 158,257 (98.59%) | 160,513 |

Both dimensions must be below the threshold. These counts estimate the
opportunity for simplification; they are not measured speedups. Simply hiding
these solids would remove most hardware from the overview, so a coarse
representation should replace them.

## Implemented changes and new measurements

The viewer retains all 161,941 objects and the original analytic positioning model.
It builds conservative object bounds and a spatial hierarchy before creating
solid meshes. Offscreen branches are rejected; small branches become grouped
marks, and individual small objects use centerlines. Full solids enter at an
8 CSS pixel footprint and remain until below 5 pixels. Selected onscreen objects
always get full detail. Thresholds apply after the existing global-axis scaling.
A bounded cache retains up to 2,048 solid meshes. Exact eager geometry remains
available through `buildScene()` for API consumers and regression comparisons.

Reference curves use screen-error sampling. Magnetic axes, beam interfaces and
named frames are generated only when enabled, in cancellable batches, and have
their own bounds so externally referenced frames can lie far from their object's
anchor. Picking and zoom use the representation actually drawn. A group mark
selects a representative object; the full searchable object list remains the
way to choose an exact name at ring scale. Curve snapping considers detailed
objects and layers currently in view and computes those targets in background
batches; exact analytic station readouts and segment boundaries are retained.

URL/file decoding and model validation run in a bundled worker, including in
the standalone page. The curve appears before object placement completes.
Placement, indexing, optional layers and snap calculations yield to the UI and
cancel obsolete work on a newer load, edit or camera change. Numeric edits copy
changed branches instead of deep-cloning the model; structural edits are checked
in a worker before adoption. Existing Python bridge commands wait for the new
scene and requested layers before reporting success.

Closed reference pickers no longer build/filter a complete reverse dependency
index. Open pickers display at most 50 matching names and evaluate candidate
frame dependencies on demand. The dependency card starts closed on very large
layouts; branches use 50-child pages, and large trees expand one branch at a time.

The same cloud Chrome development preview and 512 × 613 CSS pixel viewport gave:

| Measurement | Eager baseline | With automatic detail |
| --- | ---: | ---: |
| First settled LHC load | 51.7 s | about 12.3 s |
| First reference-curve preview | after synchronous startup | about 6.2 s |
| Whole-layout canvas draw | 9.35–19.60 s | 7–13 ms |
| Close-up canvas draw, MB.B11R1 | not measured | 10–15 ms |
| Close-up with beam interfaces | not measured | about 14 ms |
| Curve reference editor, Node server rendering | 5.36 s | 45 ms |
| Object reference editor, Node server rendering | 2.98 s | 58 ms |

The new browser load included about 5.3 seconds for the worker and model handoff
(0.40 s decode, 3.64 s validation inside the worker) and 6.2 seconds of cooperative
scene/index construction. The overview drew roughly 220 grouped marks and one
solid instead of 963,138 faces. The fitted MB.B11R1 view drew about 69 detailed
objects and 101 coarse marks. Wheel zoom and rectangle zoom continued to approach
the visible geometry; beam-layer toggling completed without restarting the load.
The rebuilt standalone page also loaded the full LHC successfully, in about
10.3 seconds, with a 7.6 ms overview draw. A repeat development load completed in
12.1 seconds with a 3.5 ms settled draw.

A separate Node run resolved the deferred scene in 3.35 seconds and built its
spatial index in 0.41 seconds. At 1200 × 800 pixels, visibility selection took
2.1 ms for the overview and 5.5 ms for a 40 m camera distance around MB.B11R1.
The latter created 153 detailed objects (918 faces), with 521 coarse marks.
Compressing global X to 0.01× reduced selection to about 1 ms in that overview.

Drawing times measure Canvas command submission, not presentation or sustained
FPS. Visibility selection adds a few milliseconds, and optional layers add their
own selection/projection work. These are observations from one development
browser, not hardware-independent guarantees. Startup still includes a full-model
validation and worker structured-clone handoff. Enabling all 761,297 named frames
requires additional background work and memory; it is not instantaneous. The
solid cache is bounded, but the complete model, resolved placement frames and
spatial indexes remain in memory. No browser peak-memory reduction is claimed.

Regression checks compare eager and deferred meshes and feature coordinates,
including externally referenced frames; retain selected detail and aggregate
counts; check culling under axis scaling and near-plane intersections; verify
cancellation, bounded mesh caching, adaptive curves, candidate-reference rules
and copy-on-write edits. The existing SPS and camera-navigation checks remain.
If dense close-ups still exceed the desired frame budget on target hardware,
instanced WebGL rendering is the next candidate. It is not necessary for the
measured LHC overview and close-up cases and is not included in this change.

## Reproduce

From the repository root, install `conversion/requirements.txt`, then:

```bash
python conversion/lhc/convert.py --output conversion/lhc/LHC--LS3.json
python conversion/validate.py conversion/lhc/LHC--LS3.pickle conversion/lhc/LHC--LS3.json
```

From `webapp/`, with dependencies installed:

```bash
node scripts/profile-layout.mjs ../conversion/lhc/LHC--LS3.json
node scripts/profile-layout.mjs ../conversion/lhc/LHC--LS3.json view
node scripts/profile-layout.mjs ../conversion/lhc/LHC--LS3.json lod
```

Modes `scene`, `controls`, `tree`, and `segments` isolate the corresponding
stages; `lod` measures deferred construction and visibility at overview, close-up
and compressed-axis views. The existing `profile-sps.mjs` commands remain supported. Add
`?profile=1` to the app URL, import the generated LHC JSON file, and inspect `[layout-profile]`
console entries for worker decode/validation, scene, visibility and redraw timings.
Normal use does not emit these diagnostics.
