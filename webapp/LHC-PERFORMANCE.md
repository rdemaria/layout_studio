# LHC LS3 conversion and viewer performance

Measured on 2026-09-07 against the source snapshot added in `abfe848`.
The LHC loads successfully, but displaying every object is too slow for
interactive use. The main bottleneck is geometry processing and Canvas 2D
drawing, with additional startup cost in model validation and reference controls.

Publication status: the converter, report and profiling tools are committed.
The 35.6 MB JSON upload failed through the available GitHub connection, so the
dataset and its catalog entry are not included in that publication. Generate
the JSON with the command below, or import the separately supplied JSON file.

## Converted data and validation

- Plain JSON: 35,608,954 bytes; 161,941 objects and 4,764 types.
- Circumference: 26,658.8832 m; 2,465 analytic reference-curve segments.
- 578 logical containers use path spans. Hardware uses the shared straight
  mechanical default and approximate 0.1 m transverse boxes.
- 817 objects are omitted because their ancestors are absent from the snapshot.
- 840 objects with unavailable mechanical lengths remain as shapeless anchors.
- 82,701 explicitly zero-length objects retain the converter's small display box.
- 37 seam references use known source stations to avoid ambiguous closed-ring
  station inference. The full browser scene fingerprint is identical before
  and after this correction; Python now also resolves the complete layout.

The Python validator resolved all 161,941 objects and 1,177,217 frames. It
compared 2,466 curve boundaries and 1,734 span frames with the source converted
through `LDBPoint.to_madpoint()`. Maximum positional differences were
1.45e-11 m for the curve and 3.19e-11 m for spans. These checks validate coordinate
conversion and internal resolution; they do not establish surveyed mechanical
envelopes or recover the missing ancestors. The conversion report and SHA-256
manifest are in `conversion/lhc/`.

## Measurements

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

## Proposed next changes

1. **Add automatic detail levels based on projected size.** At ring scale,
   show an adaptively sampled reference curve and sector/cell spans. At an
   intermediate scale, use simple lines or boxes for visible assemblies. Draw
   complete solids and small components at close range. Keep the selected
   object visible and detailed. Use hysteresis around thresholds to avoid
   flicker, and calculate size after the existing global X/Y/Z display scaling.
   This can be a viewer policy without changing physical coordinates or JSON.
2. **Cull and build only what is needed.** Cache object or assembly bounds,
   reject offscreen geometry before face allocation, and create optional axes
   and named frames when enabled or selected. Keep the object tree and search
   complete. Picking and wheel/rectangle depth targeting must use the displayed
   representation and resolve selections back to the underlying objects.
3. **Reduce startup work independently of drawing.** Share dependency/frame
   indexes across editors and calculate candidate frames when needed. Show the
   curve first, then generate visible geometry progressively. A worker could
   preserve UI responsiveness, but does not by itself reduce memory or work.
4. **Consider a GPU renderer if these changes remain insufficient.** Repeated
   boxes suit instanced rendering and depth-buffer visibility. First measure
   the simpler culling/LOD approach; moving all current allocations to WebGL
   would leave the startup and editor costs unresolved.

For immediate inspection, turning off **Show objects** leaves the reference
curve usable. LOD is a proposal in this change, not an implemented performance
fix. No physical objects were removed merely to make the benchmark faster.

## Reproduce

From the repository root, install `conversion/requirements.txt`, then:

```bash
python conversion/lhc/convert.py --output conversion/lhc/LHC--LS3.json
python conversion/validate.py conversion/lhc/LHC--LS3.pickle conversion/lhc/LHC--LS3.json
```

From `webapp/`, with dependencies installed:

```bash
node scripts/profile-layout.mjs public/layouts/LHC--LS3.json
node scripts/profile-layout.mjs public/layouts/LHC--LS3.json view
```

Modes `scene`, `controls`, `tree`, and `segments` isolate the corresponding
stages. The existing `profile-sps.mjs` commands remain supported. Add
`?profile=1` to the app URL, import the generated LHC JSON file, and inspect `[layout-profile]`
console entries for browser fetch, validation, scene, and redraw timings.
Normal use does not emit these diagnostics.
