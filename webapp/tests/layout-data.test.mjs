import assert from "node:assert/strict";
import test, { after } from "node:test";
import { fileURLToPath } from "node:url";

import { createServer } from "vite";

const root = fileURLToPath(new URL("..", import.meta.url));
const vite = await createServer({
  appType: "custom",
  configFile: false,
  root,
  resolve: { alias: { "@": root } },
  server: { middlewareMode: true },
});

after(async () => {
  await vite.close();
});

const {
  createEmptyLayout,
  getLayoutDependencyGraph,
  parseLayout,
  SAMPLE_LAYOUT,
} = await vite.ssrLoadModule("/app/layout-data.ts");
const {
  buildScene,
  closestTransverseCurvePathForPoint,
  curveObjectSurfaceIntersectionPaths,
  curvePlaneIntersectionPaths,
  curveSegmentIndexAtPath,
  frameAtCurvePath,
  transverseCurvePathsForPoint,
} = await vite.ssrLoadModule("/app/layout-geometry.ts");

function canonicalLayout() {
  return {
    reference_curves: {
      main: {
        color: "#7d91ff",
        starting_frame: {
          reference: { kind: "world" },
          transformation: [],
        },
        segments: [[100, 0, 0]],
      },
    },
    types: {
      magnet: {
        shape: ["box", 2, 2, 2, 0, 0],
        color: "#f0a84b",
        magnetic_center: { transformation: [] },
        magnetic_length: 2,
        magnetic_curvature: 0,
        magnetic_roll: 0,
        frames: {
          entrance: { transformation: [["ts", -1]] },
          exit: { transformation: [["ts", 1]] },
        },
      },
      marker: {
        shape: ["cylinder", 0.25, 0.5, 0, 0],
        color: "#5fd6c7",
        magnetic_center: { transformation: [] },
        magnetic_length: 0.5,
        magnetic_curvature: 0,
        magnetic_roll: 0,
        frames: {},
      },
    },
    objects: {
      Q1: {
        type: "magnet",
        position: {
          target: "center",
          reference: { kind: "curve", curve: "main" },
          transformation: [["ts", 10]],
        },
      },
      Q2: {
        type: "magnet",
        position: {
          target: "center",
          reference: { kind: "curve", curve: "main" },
          transformation: [
            ["ts", 20],
            ["ry", Math.PI / 2],
          ],
        },
      },
      downstream: {
        type: "marker",
        position: {
          target: "center",
          reference: { kind: "object_frame", object: "Q1", frame: "exit" },
          transformation: [["tt", 2]],
        },
      },
    },
  };
}

test("creates a fresh valid empty layout", () => {
  const first = createEmptyLayout();
  const second = createEmptyLayout();

  assert.deepEqual(parseLayout(first), {
    reference_curves: {},
    types: {},
    objects: {},
  });
  assert.notEqual(first, second);
  assert.notEqual(first.reference_curves, second.reference_curves);
  assert.notEqual(first.types, second.types);
  assert.notEqual(first.objects, second.objects);

  const scene = buildScene(first);
  assert.equal(scene.curves.length, 0);
  assert.equal(scene.objects.length, 0);
  assert.equal(scene.frames.length, 0);
  assert.equal(Object.hasOwn(scene, "points"), false);
  assert.equal(scene.magneticAxes.length, 0);
  assert.equal(scene.magneticFrames.length, 0);
  assert.equal(scene.beamAxes.length, 0);
  assert.equal(scene.beamFrames.length, 0);
  assert.deepEqual(scene.bounds, { min: [-1, -1, -1], max: [1, 1, 1] });
});

test("keeps the built-in sample conformant", () => {
  assert.doesNotThrow(() => parseLayout(structuredClone(SAMPLE_LAYOUT)));
});

test("builds the positioning dependency graph used by cycle validation", () => {
  const base = parseLayout(canonicalLayout());
  assert.deepEqual(getLayoutDependencyGraph(base).edges, [
    {
      from: "object:Q1",
      to: "curve:main",
      relation: "position_reference",
    },
    {
      from: "object:Q2",
      to: "curve:main",
      relation: "position_reference",
    },
    {
      from: "object:downstream",
      to: "object:Q1",
      relation: "position_reference",
      frame: "exit",
    },
  ]);

  const projected = canonicalLayout();
  projected.objects.downstream.position.reference_curve = "main";
  projected.objects.downstream.position.transformation = [["ts", 2]];
  assert.deepEqual(
    getLayoutDependencyGraph(parseLayout(projected)).edges.filter(
      (edge) => edge.from === "object:downstream",
    ),
    [
      {
        from: "object:downstream",
        to: "object:Q1",
        relation: "position_reference",
        frame: "exit",
      },
      {
        from: "object:downstream",
        to: "curve:main",
        relation: "station_curve",
      },
    ],
  );

  const inertProjection = canonicalLayout();
  inertProjection.objects.downstream.position.reference_curve = "main";
  assert.equal(
    getLayoutDependencyGraph(parseLayout(inertProjection)).edges.some(
      (edge) => edge.relation === "station_curve",
    ),
    false,
  );

  const curveFromFrame = canonicalLayout();
  curveFromFrame.reference_curves.tail = {
    color: "#7d91ff",
    starting_frame: {
      reference: { kind: "object_frame", object: "Q1", frame: "exit" },
      transformation: [],
    },
    segments: [[1, 0, 0]],
  };
  assert.deepEqual(
    getLayoutDependencyGraph(parseLayout(curveFromFrame)).edges.find(
      (edge) => edge.from === "curve:tail",
    ),
    {
      from: "curve:tail",
      to: "object:Q1",
      relation: "starting_frame",
      frame: "exit",
    },
  );
});

function approximatelyEqual(actual, expected, tolerance = 1e-9) {
  assert.equal(actual.length, expected.length);
  for (let index = 0; index < actual.length; index += 1) {
    assert.ok(
      Math.abs(actual[index] - expected[index]) <= tolerance,
      `coordinate ${index}: expected ${expected[index]}, received ${actual[index]}`,
    );
  }
}

const coercibleNonNumbers = [null, "", true, false, "1"];

test("parses the canonical types schema and preserves shared type identity", () => {
  const input = canonicalLayout();
  const parsed = parseLayout(input);

  assert.deepEqual(parsed, input);
  assert.deepEqual(Object.keys(parsed.types), ["magnet", "marker"]);
  assert.deepEqual(Object.keys(parsed.objects.Q1), ["type", "position"]);
  assert.equal(parsed.objects.Q1.type, "magnet");
  assert.equal(parsed.objects.Q2.type, "magnet");
  assert.deepEqual(parseLayout(JSON.parse(JSON.stringify(parsed))), parsed);
});

test("keeps structurally identical types distinct when their names differ", () => {
  const input = canonicalLayout();
  input.types.magnet_copy = structuredClone(input.types.magnet);
  input.objects.Q3 = {
    type: "magnet_copy",
    position: {
      target: "center",
      reference: { kind: "curve", curve: "main" },
      transformation: [["ts", 30]],
    },
  };

  const parsed = parseLayout(input);
  assert.deepEqual(Object.keys(parsed.types), ["magnet", "marker", "magnet_copy"]);
  assert.equal(parsed.objects.Q1.type, "magnet");
  assert.equal(parsed.objects.Q3.type, "magnet_copy");
});

test("rejects layouts without the canonical top-level types dictionary", () => {
  const input = canonicalLayout();
  delete input.types;

  assert.throws(
    () => parseLayout(input),
    /Expected reference_curves, types and objects dictionaries/,
  );
});

test("requires every reference curve to have a six-digit hex color", () => {
  const missing = canonicalLayout();
  delete missing.reference_curves.main.color;
  assert.throws(() => parseLayout(missing), /reference_curves\.main\.color/);

  for (const color of [null, "", "blue", "#fff", "#12345g"]) {
    const input = canonicalLayout();
    input.reference_curves.main.color = color;
    assert.throws(
      () => parseLayout(input),
      /reference_curves\.main\.color must be a six-digit hex color/,
    );
  }

  const parsed = parseLayout(canonicalLayout());
  assert.equal(parsed.reference_curves.main.color, "#7d91ff");
});

test("rejects coercible non-numbers in every curve segment field", () => {
  for (const fieldIndex of [0, 1, 2]) {
    for (const value of coercibleNonNumbers) {
      const input = canonicalLayout();
      input.reference_curves.main.segments[0][fieldIndex] = value;
      assert.throws(
        () => parseLayout(input),
        /must be a finite number/,
        `segment field ${fieldIndex} accepted ${JSON.stringify(value)}`,
      );
    }
  }
});

test("rejects coercible non-numbers in referenced and local transformations", () => {
  const setters = [
    (input, value) => { input.objects.Q1.position.transformation[0][1] = value; },
    (input, value) => { input.types.magnet.frames.exit.transformation[0][1] = value; },
  ];

  for (const setValue of setters) {
    for (const value of coercibleNonNumbers) {
      const input = canonicalLayout();
      setValue(input, value);
      assert.throws(
        () => parseLayout(input),
        /must be a finite number/,
        `transformation accepted ${JSON.stringify(value)}`,
      );
    }
  }
});

test("rejects coercible non-numbers in all box and cylinder dimensions", () => {
  for (const [typeName, indexes] of [
    ["magnet", [1, 2, 3]],
    ["marker", [1, 2]],
  ]) {
    for (const fieldIndex of indexes) {
      for (const value of coercibleNonNumbers) {
        const input = canonicalLayout();
        input.types[typeName].shape[fieldIndex] = value;
        assert.throws(
          () => parseLayout(input),
          /must be a finite number/,
          `${typeName} dimension ${fieldIndex} accepted ${JSON.stringify(value)}`,
        );
      }
    }
  }
});

test("requires explicit finite shape curvature and roll", () => {
  for (const [typeName, indexes] of [
    ["magnet", [4, 5]],
    ["marker", [3, 4]],
  ]) {
    for (const fieldIndex of indexes) {
      for (const value of [...coercibleNonNumbers, NaN, Infinity, -Infinity]) {
        const input = canonicalLayout();
        input.types[typeName].shape[fieldIndex] = value;
        assert.throws(
          () => parseLayout(input),
          /must be a finite number/,
          `${typeName} path field ${fieldIndex} accepted ${String(value)}`,
        );
      }
    }
  }

  for (const shape of [
    ["box", 2, 2, 2],
    ["box", 2, 2, 2, 0],
    ["cylinder", 0.25, 0.5],
    ["cylinder", 0.25, 0.5, 0],
  ]) {
    const input = canonicalLayout();
    input.types.magnet.shape = shape;
    assert.throws(() => parseLayout(input), /curvature, roll/);
  }

  const signed = canonicalLayout();
  signed.types.magnet.shape = ["box", 2, 3, 4, -0.5, Math.PI / 6];
  signed.types.marker.shape = ["cylinder", 0.5, 4, 0.2, -Math.PI / 3];
  assert.deepEqual(parseLayout(signed).types, signed.types);
});

test("rejects legacy shape, color and points fields on objects", () => {
  for (const [field, value] of [
    ["shape", ["box", 1, 1, 1]],
    ["color", "#ffffff"],
    ["points", { exit: { transformation: [] } }],
  ]) {
    const input = canonicalLayout();
    input.objects.Q1[field] = value;
    assert.throws(
      () => parseLayout(input),
      new RegExp(`objects\\.Q1 contains unsupported fields: ${field}`),
    );
  }
});

test("rejects unsupported fields throughout the canonical schema", () => {
  const cases = [
    ["layout", (input) => { input.version = 1; }],
    ["reference_curves.main", (input) => { input.reference_curves.main.note = "legacy"; }],
    ["starting_frame", (input) => { input.reference_curves.main.starting_frame.matrix = []; }],
    ["reference", (input) => { input.objects.Q1.position.reference.name = "main"; }],
  ];

  for (const [label, change] of cases) {
    const input = canonicalLayout();
    change(input);
    assert.throws(() => parseLayout(input), /contains unsupported fields/, label);
  }
});

test("requires the canonical frames dictionary on every type", () => {
  const input = canonicalLayout();
  delete input.types.marker.frames;
  assert.throws(() => parseLayout(input), /types\.marker\.frames must be an object/);
});

test("rejects the former named-point vocabulary without migration", () => {
  const legacyDictionary = canonicalLayout();
  legacyDictionary.types.magnet.points = legacyDictionary.types.magnet.frames;
  delete legacyDictionary.types.magnet.frames;
  assert.throws(
    () => parseLayout(legacyDictionary),
    /types\.magnet contains unsupported fields: points/,
  );

  const legacyKind = canonicalLayout();
  legacyKind.objects.downstream.position.reference = {
    kind: "object_point",
    object: "Q1",
    point: "exit",
  };
  assert.throws(
    () => parseLayout(legacyKind),
    /objects\.downstream\.position\.reference has an invalid reference/,
  );

  const legacyField = canonicalLayout();
  legacyField.objects.downstream.position.reference = {
    kind: "object_frame",
    object: "Q1",
    point: "exit",
  };
  assert.throws(
    () => parseLayout(legacyField),
    /objects\.downstream\.position\.reference has an invalid reference/,
  );
});

test("accepts absent optional features and rejects partial axis features", () => {
  const absent = canonicalLayout();
  delete absent.types.magnet.shape;
  for (const field of [
    "magnetic_center",
    "magnetic_length",
    "magnetic_curvature",
    "magnetic_roll",
  ]) {
    delete absent.types.magnet[field];
  }
  assert.deepEqual(parseLayout(absent).types.magnet, {
    color: "#f0a84b",
    frames: absent.types.magnet.frames,
  });

  for (const field of [
    "magnetic_center",
    "magnetic_length",
    "magnetic_curvature",
    "magnetic_roll",
  ]) {
    const partial = canonicalLayout();
    delete partial.types.magnet[field];
    assert.throws(
      () => parseLayout(partial),
      new RegExp(`complete magnetic feature; missing .*${field}`),
    );
  }

  const partialBeam = canonicalLayout();
  partialBeam.types.magnet.beam_center = { transformation: [] };
  assert.throws(
    () => parseLayout(partialBeam),
    /complete beam feature; missing beam_length, beam_curvature, beam_roll/,
  );
});

test("validates complete magnetic and beam feature values", () => {
  const complete = canonicalLayout();
  Object.assign(complete.types.magnet, {
    beam_center: { transformation: [["tx", 0.1]] },
    beam_length: 1.7,
    beam_curvature: -0.2,
    beam_roll: 0.3,
  });
  assert.deepEqual(parseLayout(complete).types.magnet.beam_center, {
    transformation: [["tx", 0.1]],
  });

  for (const value of [...coercibleNonNumbers, NaN, Infinity, -Infinity]) {
    const input = canonicalLayout();
    input.types.magnet.magnetic_length = value;
    assert.throws(
      () => parseLayout(input),
      /types\.magnet\.magnetic_length must be a finite number/,
    );
  }
  for (const value of [0, -0.1]) {
    const input = canonicalLayout();
    input.types.magnet.magnetic_length = value;
    assert.throws(
      () => parseLayout(input),
      /types\.magnet\.magnetic_length must be positive/,
    );
  }
  for (const field of ["magnetic_curvature", "magnetic_roll"]) {
    for (const value of [...coercibleNonNumbers, NaN, Infinity, -Infinity]) {
      const input = canonicalLayout();
      input.types.magnet[field] = value;
      assert.throws(
        () => parseLayout(input),
        new RegExp(`types\\.magnet\\.${field} must be a finite number`),
      );
    }
  }

  const explicitReference = canonicalLayout();
  explicitReference.types.magnet.magnetic_center.reference = { kind: "world" };
  assert.throws(
    () => parseLayout(explicitReference),
    /types\.magnet\.magnetic_center contains unsupported fields: reference/,
  );

  const ordered = canonicalLayout();
  ordered.types.magnet.magnetic_center.transformation = [
    ["ts", 0.25],
    ["rs", Math.PI / 6],
  ];
  assert.deepEqual(
    parseLayout(ordered).types.magnet.magnetic_center.transformation,
    ordered.types.magnet.magnetic_center.transformation,
  );
});

test("reserves all conditional implicit frame names", () => {
  for (const name of [
    "center",
    "magnetic_center",
    "magnetic_entry",
    "magnetic_exit",
    "beam_center",
    "beam_entry",
    "beam_exit",
  ]) {
    const input = canonicalLayout();
    input.types.magnet.frames[name] = { transformation: [] };

    assert.throws(
      () => parseLayout(input),
      new RegExp(`types\\.magnet\\.frames\\.${name} is reserved`),
    );
  }
});

test("does not treat inherited property names as declared types or frames", () => {
  const unknownType = canonicalLayout();
  unknownType.objects.Q1.type = "toString";
  assert.throws(
    () => parseLayout(unknownType),
    /objects\.Q1 references unknown type toString/,
  );

  const unknownFrame = canonicalLayout();
  unknownFrame.objects.downstream.position.reference.frame = "valueOf";
  assert.throws(
    () => parseLayout(unknownFrame),
    /object downstream references unknown frame Q1\.valueOf/,
  );

  const reservedType = canonicalLayout();
  Object.defineProperty(reservedType.types, "__proto__", {
    configurable: true,
    enumerable: true,
    value: structuredClone(reservedType.types.magnet),
    writable: true,
  });
  reservedType.objects.Q1.type = "__proto__";
  const parsed = parseLayout(reservedType);
  assert.equal(parsed.objects.Q1.type, "__proto__");
  assert.equal(parsed.types.__proto__.color, "#f0a84b");
  assert.doesNotThrow(() => buildScene(parsed));
});

test("rejects object types and frame references that do not resolve", () => {
  const unknownType = canonicalLayout();
  unknownType.objects.Q1.type = "missing";
  assert.throws(
    () => parseLayout(unknownType),
    /objects\.Q1 references unknown type missing/,
  );

  const unknownObject = canonicalLayout();
  unknownObject.objects.downstream.position.reference.object = "missing";
  assert.throws(
    () => parseLayout(unknownObject),
    /object downstream references unknown object missing/,
  );

  const wrongTypeFrame = canonicalLayout();
  wrongTypeFrame.objects.downstream.position.reference.frame = "missing";
  assert.throws(
    () => parseLayout(wrongTypeFrame),
    /object downstream references unknown frame Q1\.missing/,
  );
});

test("accepts the implicit center as an object-frame reference for every type", () => {
  const input = canonicalLayout();
  input.objects.downstream.position.reference = {
    kind: "object_frame",
    object: "Q1",
    frame: "center",
  };

  const parsed = parseLayout(input);
  const scene = buildScene(parsed);
  const downstream = scene.objects.find((object) => object.name === "downstream");
  assert.ok(downstream);
  approximatelyEqual(downstream.frame.o, [0, 0, 12]);
  assert.equal(
    scene.frames.some(
      (namedFrame) => namedFrame.object === "Q1" && namedFrame.name === "center",
    ),
    false,
    "the implicit center must not be emitted as a stored named frame",
  );
});

test("exposes only the implicit frames supplied by optional features", () => {
  const withBeam = canonicalLayout();
  Object.assign(withBeam.types.magnet, {
    beam_center: { transformation: [] },
    beam_length: 4,
    beam_curvature: 0,
    beam_roll: 0,
  });
  const expectedReferenceZ = {
    center: 12,
    magnetic_center: 12,
    magnetic_entry: 11,
    magnetic_exit: 13,
    beam_center: 12,
    beam_entry: 10,
    beam_exit: 14,
  };
  for (const [name, expectedZ] of Object.entries(expectedReferenceZ)) {
    const input = structuredClone(withBeam);
    input.objects.downstream.position.reference = {
      kind: "object_frame",
      object: "Q1",
      frame: name,
    };
    const scene = buildScene(parseLayout(input));
    const downstream = scene.objects.find((object) => object.name === "downstream");
    assert.ok(downstream);
    approximatelyEqual(downstream.frame.o, [0, 0, expectedZ]);
  }

  for (const target of [
    "center",
    "magnetic_center",
    "magnetic_entry",
    "magnetic_exit",
    "beam_center",
    "beam_entry",
    "beam_exit",
  ]) {
    const input = structuredClone(withBeam);
    input.objects.Q1.position.target = target;
    assert.equal(parseLayout(input).objects.Q1.position.target, target);
  }

  const absent = canonicalLayout();
  for (const field of [
    "magnetic_center",
    "magnetic_length",
    "magnetic_curvature",
    "magnetic_roll",
  ]) {
    delete absent.types.magnet[field];
  }
  absent.objects.Q1.position.target = "magnetic_center";
  assert.throws(
    () => parseLayout(absent),
    /position\.target references unknown frame magnet\.magnetic_center/,
  );
});

test("requires each object position to target its center or a declared local frame", () => {
  const missing = canonicalLayout();
  delete missing.objects.Q1.position.target;
  assert.throws(() => parseLayout(missing), /objects\.Q1\.position\.target/);

  for (const target of [null, "", 42, "missing", "toString"]) {
    const input = canonicalLayout();
    input.objects.Q1.position.target = target;
    assert.throws(
      () => parseLayout(input),
      /objects\.Q1\.position\.target/,
    );
  }

  const named = canonicalLayout();
  named.objects.Q1.position.target = "entrance";
  assert.equal(parseLayout(named).objects.Q1.position.target, "entrance");

  // "center" is the built-in target and need not appear in the type's frames.
  const centered = canonicalLayout();
  assert.equal(parseLayout(centered).objects.Q1.position.target, "center");
});

test("keeps type frames reference-free and allows local curved-path ts", () => {
  const explicitReference = canonicalLayout();
  explicitReference.types.magnet.frames.exit.reference = { kind: "world" };
  assert.throws(
    () => parseLayout(explicitReference),
    /types\.magnet\.frames\.exit contains unsupported fields: reference/,
  );

  const pathShift = canonicalLayout();
  pathShift.types.magnet.frames.exit.transformation = [["ts", 1]];
  assert.deepEqual(
    parseLayout(pathShift).types.magnet.frames.exit.transformation,
    [["ts", 1]],
  );
});

test("requires an explicit reference curve for non-curve object-position ts", () => {
  for (const reference of [
    { kind: "world" },
    { kind: "object_frame", object: "Q1", frame: "exit" },
  ]) {
    const input = canonicalLayout();
    input.objects.downstream.position.reference = reference;
    input.objects.downstream.position.transformation = [["ts", 2]];
    assert.throws(
      () => parseLayout(input),
      /requires reference_curve when ts is used/,
    );
  }

  const frameReference = canonicalLayout();
  frameReference.objects.downstream.position.reference_curve = "main";
  frameReference.objects.downstream.position.transformation = [["ts", 2]];
  const parsedFrameReference = parseLayout(frameReference);
  assert.equal(
    parsedFrameReference.objects.downstream.position.reference_curve,
    "main",
  );
  const downstream = buildScene(parsedFrameReference).objects.find(
    (object) => object.name === "downstream",
  );
  assert.ok(downstream);
  approximatelyEqual(downstream.frame.o, [0, 0, 13]);

  const worldReference = canonicalLayout();
  worldReference.objects.downstream.position.reference = { kind: "world" };
  worldReference.objects.downstream.position.reference_curve = "main";
  worldReference.objects.downstream.position.transformation = [["ts", 2]];
  const worldObject = buildScene(parseLayout(worldReference)).objects.find(
    (object) => object.name === "downstream",
  );
  assert.ok(worldObject);
  approximatelyEqual(worldObject.frame.o, [0, 0, 2]);

  for (const referenceCurve of ["", null, 42]) {
    const input = canonicalLayout();
    input.objects.downstream.position.reference_curve = referenceCurve;
    assert.throws(
      () => parseLayout(input),
      /reference_curve must be a non-empty curve name/,
    );
  }

  const unknown = canonicalLayout();
  unknown.objects.downstream.position.reference_curve = "missing";
  assert.throws(
    () => parseLayout(unknown),
    /object downstream references unknown projection curve missing/,
  );

  const redundant = canonicalLayout();
  redundant.objects.Q1.position.reference_curve = "main";
  assert.throws(
    () => parseLayout(redundant),
    /reference_curve is only used with world or object-frame references/,
  );

  const curveStartingFrame = canonicalLayout();
  curveStartingFrame.reference_curves.main.starting_frame.transformation = [["ts", 1]];
  assert.throws(
    () => parseLayout(curveStartingFrame),
    /can use ts only with a curve reference; use tt for a tangent shift/,
  );
});

function transverseProjectionLayout(segments, anchorPosition) {
  return {
    reference_curves: {
      main: {
        color: "#7d91ff",
        starting_frame: {
          reference: { kind: "world" },
          transformation: [],
        },
        segments,
      },
    },
    types: {
      marker: {
        shape: ["box", 0.2, 0.2, 0.2, 0, 0],
        color: "#f0a84b",
        magnetic_center: { transformation: [] },
        magnetic_length: 0.1,
        magnetic_curvature: 0,
        magnetic_roll: 0,
        frames: {},
      },
    },
    objects: {
      anchor: {
        type: "marker",
        position: {
          target: "center",
          reference: { kind: "world" },
          transformation: anchorPosition,
        },
      },
      projected: {
        type: "marker",
        position: {
          target: "center",
          reference: {
            kind: "object_frame",
            object: "anchor",
            frame: "center",
          },
          reference_curve: "main",
          transformation: [["ts", 0]],
        },
      },
    },
  };
}

test("infers a unique transverse plane on an exact curved segment", () => {
  const station = Math.PI / 4;
  const shift = 0.1;
  const input = transverseProjectionLayout(
    [[Math.PI / 2, Math.PI / 2, 0]],
    [
      ["tx", -(1 - Math.cos(station))],
      ["tt", Math.sin(station)],
      ["ry", Math.PI / 3],
    ],
  );
  input.objects.projected.position.transformation = [["ts", shift]];
  input.objects.control = {
    type: "marker",
    position: {
      target: "center",
      reference: { kind: "curve", curve: "main" },
      transformation: [["ts", station + shift]],
    },
  };

  const scene = buildScene(parseLayout(input));
  const projected = scene.objects.find((object) => object.name === "projected");
  const control = scene.objects.find((object) => object.name === "control");
  assert.ok(projected);
  assert.ok(control);
  approximatelyEqual(projected.frame.o, control.frame.o, 1e-10);
  approximatelyEqual(projected.frame.x, control.frame.x, 1e-10);
  approximatelyEqual(projected.frame.y, control.frame.y, 1e-10);
  approximatelyEqual(projected.frame.s, control.frame.s, 1e-10);
});

test("infers s on a rolled negative-angle arc", () => {
  const station = Math.PI / 4;
  const shift = 0.05;
  const input = transverseProjectionLayout(
    [[Math.PI / 2, -Math.PI / 2, Math.PI / 3]],
    [],
  );
  input.objects.anchor.position = {
    target: "center",
    reference: { kind: "curve", curve: "main" },
    transformation: [["ts", station]],
  };
  input.objects.projected.position.transformation = [["ts", shift]];
  input.objects.control = {
    type: "marker",
    position: {
      target: "center",
      reference: { kind: "curve", curve: "main" },
      transformation: [["ts", station + shift]],
    },
  };

  const objects = Object.fromEntries(
    buildScene(parseLayout(input)).objects.map((object) => [object.name, object]),
  );
  approximatelyEqual(objects.projected.frame.o, objects.control.frame.o, 1e-10);
  approximatelyEqual(objects.projected.frame.x, objects.control.frame.x, 1e-10);
  approximatelyEqual(objects.projected.frame.y, objects.control.frame.y, 1e-10);
  approximatelyEqual(objects.projected.frame.s, objects.control.frame.s, 1e-10);
});

test("chooses the closest transverse-plane solution and rejects only missing or equal nearest solutions", () => {
  const none = transverseProjectionLayout(
    [[10, 0, 0]],
    [["tt", 20]],
  );
  assert.throws(
    () => buildScene(parseLayout(none)),
    /lies in no transverse plane within the curve domain/,
  );

  const multiple = transverseProjectionLayout(
    [[Math.PI, Math.PI, 0]],
    [],
  );
  const multipleScene = buildScene(parseLayout(multiple));
  approximatelyEqual(
    multipleScene.objects.find((object) => object.name === "projected").frame.o,
    [0, 0, 0],
  );

  const translatedMultiple = transverseProjectionLayout(
    [[Math.PI, Math.PI, 0]],
    [["tx", 1_000_000_000]],
  );
  translatedMultiple.reference_curves.main.starting_frame.transformation = [
    ["tx", 1_000_000_000],
  ];
  const translatedScene = buildScene(parseLayout(translatedMultiple));
  approximatelyEqual(
    translatedScene.objects.find((object) => object.name === "projected").frame.o,
    [1_000_000_000, 0, 0],
  );

  const equalNearest = transverseProjectionLayout(
    [[2, 0, 0], [Math.PI, Math.PI, 0], [2, 0, 0]],
    [["tx", -1], ["tt", 1]],
  );
  assert.throws(
    () => buildScene(parseLayout(equalNearest)),
    /multiple transverse-plane solutions are equally close/,
  );

  const infinite = transverseProjectionLayout(
    [[Math.PI, Math.PI, 0]],
    [["tx", -1]],
  );
  assert.throws(
    () => buildScene(parseLayout(infinite)),
    /multiple transverse-plane solutions are equally close/,
  );

  const intervalWithCloserRoot = transverseProjectionLayout(
    [
      [Math.PI * 2, Math.PI * 2, 0],
      [Math.PI / 2, Math.PI, 0],
    ],
    [["tx", -1]],
  );
  const intervalScene = buildScene(parseLayout(intervalWithCloserRoot));
  approximatelyEqual(
    intervalScene.objects.find((object) => object.name === "projected").frame.o,
    [-1, 0, 0],
    1e-8,
  );
});

test("deduplicates the same transverse plane at a segment junction", () => {
  const input = transverseProjectionLayout(
    [[5, 0, 0], [5, 0, 0]],
    [["tt", 5]],
  );
  input.objects.projected.position.transformation = [["ts", 1]];
  const projected = buildScene(parseLayout(input)).objects.find(
    (object) => object.name === "projected",
  );
  assert.ok(projected);
  approximatelyEqual(projected.frame.o, [0, 0, 6]);
});

test("instantiates shared type geometry and preserves the prior center-target behavior", () => {
  const layout = parseLayout(canonicalLayout());
  const scene = buildScene(layout);
  const objects = Object.fromEntries(scene.objects.map((object) => [object.name, object]));
  const frames = Object.fromEntries(
    scene.frames.map((namedFrame) => [
      `${namedFrame.object}.${namedFrame.name}`,
      namedFrame,
    ]),
  );

  assert.equal(objects.Q1.type, objects.Q2.type);
  assert.equal(objects.Q1.typeName, "magnet");
  assert.equal(objects.Q2.typeName, "magnet");
  assert.equal(objects.Q1.type.color, "#f0a84b");
  assert.equal(objects.Q1.vertices.length, 8);
  assert.equal(objects.Q2.vertices.length, 8);

  approximatelyEqual(objects.Q1.frame.o, [0, 0, 10]);
  approximatelyEqual(objects.Q2.frame.o, [0, 0, 20]);
  approximatelyEqual(objects.Q2.frame.x, [0, 0, -1]);
  approximatelyEqual(objects.Q2.frame.y, [0, 1, 0]);
  approximatelyEqual(objects.Q2.frame.s, [1, 0, 0]);
  approximatelyEqual(frames["Q1.entrance"].frame.o, [0, 0, 9]);
  approximatelyEqual(frames["Q1.exit"].frame.o, [0, 0, 11]);
  approximatelyEqual(frames["Q2.entrance"].frame.o, [-1, 0, 20]);
  approximatelyEqual(frames["Q2.exit"].frame.o, [1, 0, 20]);
  approximatelyEqual(objects.downstream.frame.o, [0, 0, 13]);
});

test("evaluates local ts in order and tt along the resolved tangent", () => {
  const input = {
    reference_curves: {},
    types: {
      curved: {
        shape: ["box", 1, 1, Math.PI, 1, 0],
        color: "#f0a84b",
        magnetic_center: { transformation: [] },
        magnetic_length: Math.PI,
        magnetic_curvature: 1,
        magnetic_roll: 0,
        frames: {
          station: { transformation: [["ts", Math.PI / 2]] },
          after: { transformation: [["ts", Math.PI / 2], ["tt", 2]] },
          before: { transformation: [["tt", 2], ["ts", Math.PI / 2]] },
          rotated: { transformation: [["rs", Math.PI / 2], ["ts", Math.PI / 2]] },
        },
      },
    },
    objects: {
      C: {
        type: "curved",
        position: {
          target: "center",
          reference: { kind: "world" },
          transformation: [],
        },
      },
    },
  };
  const scene = buildScene(parseLayout(input));
  const frames = Object.fromEntries(
    scene.frames.map((namedFrame) => [namedFrame.name, namedFrame.frame]),
  );

  approximatelyEqual(frames.station.o, [-1, 0, 1]);
  approximatelyEqual(frames.station.x, [0, 0, 1]);
  approximatelyEqual(frames.station.y, [0, 1, 0]);
  approximatelyEqual(frames.station.s, [-1, 0, 0]);
  approximatelyEqual(frames.after.o, [-3, 0, 1]);
  approximatelyEqual(frames.before.o, [-1, 0, 3]);
  approximatelyEqual(frames.rotated.o, [0, -1, 1]);
  approximatelyEqual(frames.rotated.x, [0, 0, 1]);
  approximatelyEqual(frames.rotated.y, [-1, 0, 0]);
  approximatelyEqual(frames.rotated.s, [0, -1, 0]);
});

test("derives curved magnetic entry and exit frames and aligns magnetic targets", () => {
  const input = {
    reference_curves: {},
    types: {
      sector: {
        shape: ["box", 1, 1, Math.PI, 1, 0],
        color: "#f0a84b",
        magnetic_center: { transformation: [] },
        magnetic_length: Math.PI,
        magnetic_curvature: 1,
        magnetic_roll: 0,
        frames: {},
      },
    },
    objects: {
      A: {
        type: "sector",
        position: {
          target: "center",
          reference: { kind: "world" },
          transformation: [],
        },
      },
      B: {
        type: "sector",
        position: {
          target: "magnetic_entry",
          reference: {
            kind: "object_frame",
            object: "A",
            frame: "magnetic_exit",
          },
          transformation: [],
        },
      },
    },
  };
  const scene = buildScene(parseLayout(input));
  const frames = Object.fromEntries(
    scene.magneticFrames.map((frame) => [`${frame.object}.${frame.name}`, frame]),
  );

  approximatelyEqual(frames["A.magnetic_entry"].frame.o, [-1, 0, -1]);
  approximatelyEqual(frames["A.magnetic_entry"].frame.s, [1, 0, 0]);
  approximatelyEqual(frames["A.magnetic_exit"].frame.o, [-1, 0, 1]);
  approximatelyEqual(frames["A.magnetic_exit"].frame.s, [-1, 0, 0]);
  approximatelyEqual(
    frames["B.magnetic_entry"].frame.o,
    frames["A.magnetic_exit"].frame.o,
    1e-8,
  );
  approximatelyEqual(
    frames["B.magnetic_entry"].frame.s,
    frames["A.magnetic_exit"].frame.s,
    1e-8,
  );

  assert.equal(scene.magneticFrames.length, 4);
  assert.equal(scene.magneticAxes.length, 2);
  assert.equal(scene.beamAxes.length, 0);
  assert.equal(scene.beamFrames.length, 0);
  assert.equal(frames["A.magnetic_entry"].vertices.length, 4);
  for (const magneticFrame of scene.magneticFrames) {
    for (const vertex of magneticFrame.vertices) {
      const delta = vertex.map(
        (coordinate, axis) => coordinate - magneticFrame.frame.o[axis],
      );
      assert.ok(Math.abs(
        delta.reduce(
          (sum, coordinate, axis) =>
            sum + coordinate * magneticFrame.frame.s[axis],
          0,
        ),
      ) < 1e-9, "magnetic plane must be normal to its tangent");
    }
  }
  assert.equal(
    scene.frames.some((namedFrame) => namedFrame.name.startsWith("magnetic_")),
    false,
    "derived magnetic frames must not become stored named frames",
  );
});

test("keeps mechanical, magnetic and beam paths independent", () => {
  const input = {
    reference_curves: {},
    types: {
      combined: {
        shape: ["box", 1, 1, Math.PI, 1, 0],
        color: "#f0a84b",
        magnetic_center: { transformation: [] },
        magnetic_length: 2,
        magnetic_curvature: 0,
        magnetic_roll: 0.4,
        beam_center: { transformation: [["tx", 1]] },
        beam_length: 4,
        beam_curvature: 0,
        beam_roll: -0.3,
        frames: {
          mechanical_station: { transformation: [["ts", Math.PI / 2]] },
        },
      },
    },
    objects: {
      A: {
        type: "combined",
        position: {
          target: "center",
          reference: { kind: "world" },
          transformation: [],
        },
      },
      B: {
        type: "combined",
        position: {
          target: "beam_entry",
          reference: {
            kind: "object_frame",
            object: "A",
            frame: "magnetic_exit",
          },
          transformation: [],
        },
      },
    },
  };
  const scene = buildScene(parseLayout(input));
  const named = scene.frames.find(
    (frame) => frame.object === "A" && frame.name === "mechanical_station",
  );
  assert.ok(named);
  approximatelyEqual(named.frame.o, [-1, 0, 1]);
  approximatelyEqual(named.frame.s, [-1, 0, 0]);

  const magnetic = Object.fromEntries(
    scene.magneticFrames
      .filter((frame) => frame.object === "A")
      .map((frame) => [frame.name, frame.frame]),
  );
  approximatelyEqual(magnetic.magnetic_entry.o, [0, 0, -1]);
  approximatelyEqual(magnetic.magnetic_exit.o, [0, 0, 1]);

  const beam = Object.fromEntries(
    scene.beamFrames
      .filter((frame) => frame.object === "A")
      .map((frame) => [frame.name, frame.frame]),
  );
  approximatelyEqual(beam.beam_entry.o, [1, 0, -2]);
  approximatelyEqual(beam.beam_exit.o, [1, 0, 2]);

  const objectB = scene.objects.find((object) => object.name === "B");
  assert.ok(objectB);
  approximatelyEqual(objectB.frame.o, [-1, 0, 3]);
  const beamEntryB = scene.beamFrames.find(
    (frame) => frame.object === "B" && frame.name === "beam_entry",
  );
  assert.ok(beamEntryB);
  approximatelyEqual(beamEntryB.frame.o, magnetic.magnetic_exit.o);

  assert.equal(scene.magneticAxes.length, 2);
  assert.equal(scene.beamAxes.length, 2);
  assert.equal(scene.magneticFrames.every((frame) => frame.kind === "magnetic"), true);
  assert.equal(scene.beamFrames.every((frame) => frame.kind === "beam"), true);
  approximatelyEqual(
    scene.magneticAxes[0].samples[0].p,
    magnetic.magnetic_entry.o,
  );
  approximatelyEqual(
    scene.beamAxes[0].samples.at(-1).p,
    beam.beam_exit.o,
  );
});

test("represents a shapeless object by its center frame", () => {
  const input = {
    reference_curves: {},
    types: {
      marker: {
        color: "#5fd6c7",
        frames: {
          offset: { transformation: [["ts", 2]] },
        },
      },
    },
    objects: {
      A: {
        type: "marker",
        position: {
          target: "center",
          reference: { kind: "world" },
          transformation: [["tx", 3]],
        },
      },
    },
  };
  const scene = buildScene(parseLayout(input));
  assert.equal(scene.objects.length, 1);
  assert.deepEqual(scene.objects[0].vertices, []);
  assert.deepEqual(scene.objects[0].faces, []);
  assert.deepEqual(scene.objects[0].edges, []);
  approximatelyEqual(scene.objects[0].frame.o, [3, 0, 0]);
  approximatelyEqual(scene.frames[0].frame.o, [3, 0, 2]);
  assert.deepEqual(scene.bounds, { min: [3, 0, 0], max: [3, 0, 2] });
});

test("uses shape roll for the local bend plane", () => {
  const input = {
    reference_curves: {},
    types: {
      rolled: {
        shape: ["cylinder", 0.2, Math.PI, 1, Math.PI / 2],
        color: "#5fd6c7",
        magnetic_center: { transformation: [] },
        magnetic_length: Math.PI,
        magnetic_curvature: 1,
        magnetic_roll: Math.PI / 2,
        frames: {
          station: { transformation: [["ts", Math.PI / 2]] },
        },
      },
      negative: {
        shape: ["box", 0.2, 0.2, Math.PI, -1, 0],
        color: "#8898ff",
        magnetic_center: { transformation: [] },
        magnetic_length: Math.PI,
        magnetic_curvature: -1,
        magnetic_roll: 0,
        frames: {
          station: { transformation: [["ts", Math.PI / 2]] },
        },
      },
    },
    objects: {
      C: {
        type: "rolled",
        position: {
          target: "center",
          reference: { kind: "world" },
          transformation: [],
        },
      },
      N: {
        type: "negative",
        position: {
          target: "center",
          reference: { kind: "world" },
          transformation: [],
        },
      },
    },
  };
  const frames = Object.fromEntries(
    buildScene(parseLayout(input)).frames.map(
      (namedFrame) => [namedFrame.object, namedFrame.frame],
    ),
  );
  approximatelyEqual(frames.C.o, [0, -1, 1]);
  approximatelyEqual(frames.C.x, [1, 0, 0]);
  approximatelyEqual(frames.C.y, [0, 0, 1]);
  approximatelyEqual(frames.C.s, [0, -1, 0]);
  approximatelyEqual(frames.N.o, [1, 0, 1]);
  approximatelyEqual(frames.N.s, [1, 0, 0]);
});

test("sweeps box and cylinder cross-sections along their curved centrelines", () => {
  const input = {
    reference_curves: {},
    types: {
      box: {
        shape: ["box", 1, 2, Math.PI, 1, 0],
        color: "#f0a84b",
        magnetic_center: { transformation: [] },
        magnetic_length: Math.PI,
        magnetic_curvature: 1,
        magnetic_roll: 0,
        frames: {},
      },
      cylinder: {
        shape: ["cylinder", 0.5, Math.PI, 1, 0],
        color: "#5fd6c7",
        magnetic_center: { transformation: [] },
        magnetic_length: Math.PI,
        magnetic_curvature: 1,
        magnetic_roll: 0,
        frames: {},
      },
    },
    objects: {
      Box: {
        type: "box",
        position: {
          target: "center",
          reference: { kind: "world" },
          transformation: [],
        },
      },
      Cylinder: {
        type: "cylinder",
        position: {
          target: "center",
          reference: { kind: "world" },
          transformation: [],
        },
      },
    },
  };
  const scene = buildScene(parseLayout(input));
  const box = scene.objects.find((object) => object.name === "Box");
  const cylinder = scene.objects.find((object) => object.name === "Cylinder");
  assert.ok(box);
  assert.ok(cylinder);
  assert.ok(box.vertices.length > 8);
  assert.ok(cylinder.vertices.length > 36);
  assert.equal(
    scene.magneticFrames.find(
      (frame) => frame.object === "Box" && frame.name === "magnetic_entry",
    )?.vertices.length,
    4,
  );
  assert.equal(
    scene.magneticFrames.find(
      (frame) => frame.object === "Cylinder" && frame.name === "magnetic_entry",
    )?.vertices.length,
    24,
  );

  const mean = (vertices) => vertices.reduce(
    (sum, vertex) => sum.map((value, axis) => value + vertex[axis]),
    [0, 0, 0],
  ).map((value) => value / vertices.length);
  approximatelyEqual(mean(box.vertices.slice(0, 4)), [-1, 0, -1], 1e-8);
  approximatelyEqual(mean(box.vertices.slice(-4)), [-1, 0, 1], 1e-8);
  approximatelyEqual(mean(cylinder.vertices.slice(0, 18)), [-1, 0, -1], 1e-8);
  approximatelyEqual(mean(cylinder.vertices.slice(-18)), [-1, 0, 1], 1e-8);

  for (const object of [box, cylinder]) {
    assert.ok(object.vertices.flat().every(Number.isFinite));
    for (const indexes of [...object.faces, ...object.edges]) {
      assert.ok(indexes.every((index) => index >= 0 && index < object.vertices.length));
    }
  }
});

test("shares immutable topology between compatible object meshes", () => {
  const scene = buildScene(parseLayout(canonicalLayout()));
  const q1 = scene.objects.find((object) => object.name === "Q1");
  const q2 = scene.objects.find((object) => object.name === "Q2");
  assert.ok(q1);
  assert.ok(q2);
  assert.strictEqual(q1.faces, q2.faces);
  assert.strictEqual(q1.edges, q2.edges);
  assert.ok(Object.isFrozen(q1.faces));
  assert.ok(Object.isFrozen(q1.edges));
  assert.ok(q1.faces.every(Object.isFrozen));
  assert.ok(q1.edges.every(Object.isFrozen));
});

test("aligns a curved target frame by inverting its local path", () => {
  const input = {
    reference_curves: {
      main: {
        color: "#7d91ff",
        starting_frame: { reference: { kind: "world" }, transformation: [] },
        segments: [[100, 0, 0]],
      },
    },
    types: {
      curved: {
        shape: ["box", 1, 1, Math.PI, 1, 0],
        color: "#f0a84b",
        magnetic_center: { transformation: [] },
        magnetic_length: Math.PI,
        magnetic_curvature: 1,
        magnetic_roll: 0,
        frames: {
          start: { transformation: [["ts", -Math.PI / 2]] },
          end: { transformation: [["ts", Math.PI / 2]] },
        },
      },
    },
    objects: {
      A: {
        type: "curved",
        position: {
          target: "center",
          reference: { kind: "curve", curve: "main" },
          transformation: [["ts", 10]],
        },
      },
      B: {
        type: "curved",
        position: {
          target: "start",
          reference: { kind: "object_frame", object: "A", frame: "end" },
          transformation: [["tt", 2]],
        },
      },
    },
  };
  const scene = buildScene(parseLayout(input));
  const objectB = scene.objects.find((object) => object.name === "B");
  const startB = scene.frames.find(
    (namedFrame) => namedFrame.object === "B" && namedFrame.name === "start",
  );
  assert.ok(objectB);
  assert.ok(startB);
  approximatelyEqual(startB.frame.o, [-3, 0, 11], 1e-8);
  approximatelyEqual(startB.frame.x, [0, 0, 1], 1e-8);
  approximatelyEqual(startB.frame.y, [0, 1, 0], 1e-8);
  approximatelyEqual(startB.frame.s, [-1, 0, 0], 1e-8);
  approximatelyEqual(objectB.frame.o, [-4, 0, 10], 1e-8);
  approximatelyEqual(objectB.frame.x, [-1, 0, 0], 1e-8);
  approximatelyEqual(objectB.frame.s, [0, 0, -1], 1e-8);
});

test("aligns B.start to a transformed A.end, including frame and placement rotations", () => {
  const input = canonicalLayout();
  input.types.link = {
    shape: ["box", 1, 1, 2, 0, 0],
    color: "#e66f86",
    magnetic_center: { transformation: [] },
    magnetic_length: 2,
    magnetic_curvature: 0,
    magnetic_roll: 0,
    frames: {
      start: {
        transformation: [
          ["tx", -0.4],
          ["ry", Math.PI / 4],
          ["tt", -1],
          ["rs", Math.PI / 6],
        ],
      },
      end: { transformation: [["tt", 1]] },
    },
  };
  input.objects.A = {
    type: "link",
    position: {
      target: "center",
      reference: { kind: "curve", curve: "main" },
      transformation: [["ts", 30]],
    },
  };
  input.objects.B = {
    type: "link",
    position: {
      target: "start",
      reference: { kind: "object_frame", object: "A", frame: "end" },
      transformation: [
        ["tx", 2],
        ["ry", Math.PI / 2],
        ["tt", 3],
        ["rs", Math.PI / 2],
      ],
    },
  };

  const scene = buildScene(parseLayout(input));
  const objectB = scene.objects.find((object) => object.name === "B");
  const frameBStart = scene.frames.find(
    (namedFrame) => namedFrame.object === "B" && namedFrame.name === "start",
  );
  assert.ok(objectB);
  assert.ok(frameBStart);

  // A.end is at [0, 0, 31]. The position operations move the desired target
  // frame to [5, 0, 31] and rotate its axes as below. B's non-trivial local
  // start transform must be inverted so that the instantiated frame lands here.
  approximatelyEqual(frameBStart.frame.o, [5, 0, 31]);
  approximatelyEqual(frameBStart.frame.x, [0, 1, 0]);
  approximatelyEqual(frameBStart.frame.y, [0, 0, 1]);
  approximatelyEqual(frameBStart.frame.s, [1, 0, 0]);
  assert.ok(
    Math.hypot(
      objectB.frame.o[0] - frameBStart.frame.o[0],
      objectB.frame.o[1] - frameBStart.frame.o[1],
      objectB.frame.o[2] - frameBStart.frame.o[2],
    ) > 0.1,
  );
});

test("rejects self and mutual object-frame dependency cycles", () => {
  const selfReference = canonicalLayout();
  selfReference.objects.Q1.position.reference = {
    kind: "object_frame",
    object: "Q1",
    frame: "exit",
  };
  selfReference.objects.Q1.position.transformation = [];
  assert.throws(
    () => parseLayout(selfReference),
    /Reference dependency cycle: object Q1 -> object Q1/,
  );

  const mutualReference = canonicalLayout();
  mutualReference.objects.Q1.position.reference = {
    kind: "object_frame",
    object: "Q2",
    frame: "exit",
  };
  mutualReference.objects.Q1.position.transformation = [];
  mutualReference.objects.Q2.position.reference = {
    kind: "object_frame",
    object: "Q1",
    frame: "exit",
  };
  mutualReference.objects.Q2.position.transformation = [];
  assert.throws(
    () => parseLayout(mutualReference),
    /Reference dependency cycle: object Q1 -> object Q2 -> object Q1/,
  );
});

test("rejects dependency cycles spanning a curve and an object", () => {
  const input = canonicalLayout();
  input.reference_curves.main.starting_frame.reference = {
    kind: "object_frame",
    object: "Q1",
    frame: "exit",
  };

  assert.throws(
    () => parseLayout(input),
    /Reference dependency cycle: curve main -> object Q1 -> curve main/,
  );
});

test("rejects cycles through an object position's projection curve", () => {
  const input = canonicalLayout();
  input.objects.Q1.position = {
    target: "center",
    reference: { kind: "world" },
    transformation: [["tt", 10]],
  };
  input.objects.downstream.position.reference_curve = "main";
  input.objects.downstream.position.transformation = [["ts", 0]];
  input.reference_curves.main.starting_frame = {
    reference: {
      kind: "object_frame",
      object: "downstream",
      frame: "center",
    },
    transformation: [],
  };

  assert.throws(
    () => parseLayout(input),
    /Reference dependency cycle: curve main -> object downstream -> curve main/,
  );
});

test("evaluates an exact frame at a continuous curve station", () => {
  const curve = buildScene(parseLayout(canonicalLayout())).curves[0];
  const frame = frameAtCurvePath(curve, 12.345678);

  approximatelyEqual(frame.o, [0, 0, 12.345678]);
  approximatelyEqual(frame.s, [0, 0, 1]);
});

test("uses the negative-x, positive-roll-toward-negative-y bend convention", () => {
  const input = canonicalLayout();
  input.reference_curves.main.segments = [[Math.PI / 2, Math.PI / 2, 0]];
  let curve = buildScene(parseLayout(input)).curves[0];
  let frame = frameAtCurvePath(curve, Math.PI / 2);
  approximatelyEqual(frame.o, [-1, 0, 1]);
  approximatelyEqual(frame.s, [-1, 0, 0]);

  input.reference_curves.main.segments = [
    [Math.PI / 2, Math.PI / 2, Math.PI / 2],
  ];
  curve = buildScene(parseLayout(input)).curves[0];
  frame = frameAtCurvePath(curve, Math.PI / 2);
  approximatelyEqual(frame.o, [0, -1, 1]);
  approximatelyEqual(frame.s, [0, -1, 0]);
});

test("assigns shared curve boundaries to the following segment", () => {
  const input = canonicalLayout();
  input.reference_curves.main.segments = [[3, 0, 0], [7, 0, 0]];
  const curve = buildScene(parseLayout(input)).curves[0];
  assert.equal(curveSegmentIndexAtPath(curve, 0), 0);
  assert.equal(curveSegmentIndexAtPath(curve, 2.999), 0);
  assert.equal(curveSegmentIndexAtPath(curve, 3), 1);
  assert.equal(curveSegmentIndexAtPath(curve, 10), 1);
});

test("classifies point transverse-plane stations without throwing", () => {
  const curve = buildScene(parseLayout(canonicalLayout())).curves[0];
  const offsetPoint = transverseCurvePathsForPoint(curve, [3, -2, 27.5]);
  assert.equal(offsetPoint.kind, "unique");
  approximatelyEqual(offsetPoint.paths, [27.5]);

  const circular = canonicalLayout();
  circular.reference_curves.main.segments = [[Math.PI * 2, Math.PI * 2, 0]];
  const circle = buildScene(parseLayout(circular)).curves[0];
  const arcCenter = transverseCurvePathsForPoint(circle, [-1, 0, 0]);
  assert.equal(arcCenter.kind, "infinite");

  const repeatedPlane = transverseCurvePathsForPoint(circle, [0, 0, 0]);
  assert.equal(repeatedPlane.kind, "multiple");
});

test("ranks all transverse-plane roots by distance to the curve frame", () => {
  const halfCircle = canonicalLayout();
  halfCircle.reference_curves.main.segments = [[Math.PI, Math.PI, 0]];
  const curve = buildScene(parseLayout(halfCircle)).curves[0];
  const closest = closestTransverseCurvePathForPoint(curve, [0, 0, 0]);
  assert.equal(closest.kind, "unique");
  assert.equal(closest.path, 0);

  const fullCircle = canonicalLayout();
  fullCircle.reference_curves.main.segments = [[Math.PI * 2, Math.PI * 2, 0]];
  const circle = buildScene(parseLayout(fullCircle)).curves[0];
  assert.equal(
    closestTransverseCurvePathForPoint(circle, [-1, 0, 0]).kind,
    "equidistant",
  );
});

test("finds and deduplicates curve crossings of displayed object surfaces", () => {
  const scene = buildScene(parseLayout(canonicalLayout()));
  const curve = scene.curves.find((candidate) => candidate.name === "main");
  const box = scene.objects.find((candidate) => candidate.name === "Q1");
  const cylinder = scene.objects.find(
    (candidate) => candidate.name === "downstream",
  );
  assert.ok(curve);
  assert.ok(box);
  assert.ok(cylinder);

  const crossings = curveObjectSurfaceIntersectionPaths(curve, [box, cylinder]);
  approximatelyEqual(crossings.get("Q1"), [9, 11], 1e-8);
  approximatelyEqual(crossings.get("downstream"), [12.75, 13.25], 1e-8);

  const missed = structuredClone(canonicalLayout());
  missed.objects.Q1.position.transformation.push(["tx", 20]);
  const missedScene = buildScene(parseLayout(missed));
  const missedCurve = missedScene.curves.find((candidate) => candidate.name === "main");
  const missedBox = missedScene.objects.find((candidate) => candidate.name === "Q1");
  assert.ok(missedCurve);
  assert.ok(missedBox);
  assert.equal(
    curveObjectSurfaceIntersectionPaths(missedCurve, [missedBox]).has("Q1"),
    false,
  );
});

test("finds entry and exit crossings of a matching curved swept shape", () => {
  const curvature = 0.2;
  const roll = Math.PI / 5;
  const input = canonicalLayout();
  input.reference_curves.main.segments = [[4, curvature * 4, roll]];
  input.types.magnet.shape = ["box", 0.8, 0.6, 2, curvature, roll];
  input.objects = {
    Q1: {
      type: "magnet",
      position: {
        target: "center",
        reference: { kind: "curve", curve: "main" },
        transformation: [["ts", 2]],
      },
    },
  };
  const scene = buildScene(parseLayout(input));
  const crossings = curveObjectSurfaceIntersectionPaths(
    scene.curves[0],
    [scene.objects[0]],
  );
  approximatelyEqual(crossings.get("Q1"), [1, 3], 2e-3);
});

test("finds unique object-plane crossings and rejects coincident intervals", () => {
  const straight = buildScene(parseLayout(canonicalLayout())).curves[0];
  const transversePlane = {
    o: [0, 0, 31.25],
    x: [1, 0, 0],
    y: [0, 1, 0],
    s: [0, 0, 1],
  };
  const crossing = curvePlaneIntersectionPaths(straight, transversePlane);
  assert.equal(crossing.kind, "unique");
  approximatelyEqual(crossing.paths, [31.25]);

  const coincidentPlane = {
    o: [0, 0, 0],
    x: [0, 1, 0],
    y: [0, 0, 1],
    s: [1, 0, 0],
  };
  assert.equal(
    curvePlaneIntersectionPaths(straight, coincidentPlane).kind,
    "infinite",
  );
});

test("solves a curved segment's object-plane crossing analytically", () => {
  const input = canonicalLayout();
  input.reference_curves.main.segments = [[Math.PI / 2, Math.PI / 2, 0]];
  const curve = buildScene(parseLayout(input)).curves[0];
  const plane = {
    o: [-0.5, 0, 0],
    x: [0, 1, 0],
    y: [0, 0, 1],
    s: [1, 0, 0],
  };
  const result = curvePlaneIntersectionPaths(curve, plane);

  assert.equal(result.kind, "unique");
  approximatelyEqual(result.paths, [Math.PI / 3], 1e-8);

  input.reference_curves.main.segments = [[Math.PI / 2, -Math.PI / 2, 0]];
  const negativeCurve = buildScene(parseLayout(input)).curves[0];
  const negativeResult = curvePlaneIntersectionPaths(negativeCurve, {
    ...plane,
    o: [0.5, 0, 0],
  });
  assert.equal(negativeResult.kind, "unique");
  approximatelyEqual(negativeResult.paths, [Math.PI / 3], 1e-8);

  input.reference_curves.main.segments = [[Math.PI / 2, Math.PI / 2, Math.PI / 2]];
  const rolledCurve = buildScene(parseLayout(input)).curves[0];
  const rolledResult = curvePlaneIntersectionPaths(rolledCurve, {
    o: [0, -0.5, 0],
    x: [1, 0, 0],
    y: [0, 0, 1],
    s: [0, 1, 0],
  });
  assert.equal(rolledResult.kind, "unique");
  approximatelyEqual(rolledResult.paths, [Math.PI / 3], 1e-8);
});
