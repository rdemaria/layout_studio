import assert from "node:assert/strict";
import test from "node:test";

await import("../src/model.js");
const M = globalThis.LayoutStudioModel;

function close(actual, expected, tolerance = 1e-9, message = "") {
  assert.ok(
    Math.abs(actual - expected) <= tolerance,
    `${message || "values differ"}: ${actual} != ${expected} (tol ${tolerance})`,
  );
}

function closeVector(actual, expected, tolerance = 1e-9) {
  assert.equal(actual.length, expected.length);
  actual.forEach((value, index) => close(value, expected[index], tolerance, `component ${index}`));
}

function emptyLayout() {
  return { reference_curves: {}, types: {}, objects: {} };
}

function basicType(overrides = {}) {
  return {
    shape: ["box", 1, 1, 1, 0, 0],
    color: "#abcdef",
    magnetic_center: { transformation: [] },
    magnetic_length: 0.8,
    frames: {},
    ...overrides,
  };
}

test("default layout validates and resolves every entity", () => {
  const layout = M.clone(M.DEFAULT_LAYOUT);
  assert.equal(M.validateLayout(layout), layout);
  const resolver = new M.Resolver(layout).resolveAll();
  close(resolver.curveData("ring").length, 18);
  assert.deepEqual(Object.keys(layout.objects), ["QF1", "B1", "BPM1"]);
  closeVector(resolver.objectCenter("QF1").frame.o, [-5, 0, 3.5]);
});

test("frame composition and inversion produce identity", () => {
  const frame = {
    o: [2, -3, 5],
    x: [0, 1, 0],
    y: [-1, 0, 0],
    s: [0, 0, 1],
  };
  const identity = M.composeFrames(frame, M.inverseFrame(frame));
  closeVector(identity.o, [0, 0, 0]);
  closeVector(identity.x, [1, 0, 0]);
  closeVector(identity.y, [0, 1, 0]);
  closeVector(identity.s, [0, 0, 1]);
});

test("positive curve angle bends toward negative x at zero roll", () => {
  const layout = emptyLayout();
  layout.reference_curves.arc = {
    color: "#123456",
    starting_frame: { reference: { kind: "world" }, transformation: [] },
    segments: [[1, Math.PI / 2, 0]],
  };
  M.validateLayout(layout);
  const frame = new M.Resolver(layout).curveFrame("arc", 1).frame;
  const radius = 2 / Math.PI;
  closeVector(frame.o, [-radius, 0, radius]);
  closeVector(frame.s, [-1, 0, 0], 1e-8);
});

test("all ts operations on a direct curve reference select the station before rigid operations", () => {
  const layout = emptyLayout();
  layout.reference_curves.line = {
    color: "#123456",
    starting_frame: { reference: { kind: "world" }, transformation: [["tx", 4]] },
    segments: [[20, 0, 0]],
  };
  layout.types.marker = basicType();
  layout.objects.M = {
    type: "marker",
    position: {
      target: "center",
      reference: { kind: "curve", curve: "line" },
      transformation: [["ts", 2], ["tx", 0.5], ["ts", 3]],
    },
  };
  const resolver = new M.Resolver(M.validateLayout(layout)).resolveAll();
  closeVector(resolver.objectCenter("M").frame.o, [4.5, 0, 5]);
  close(resolver.objectCenter("M").stations.line, 5);
});

test("placing a non-center target solves the object center by inverse local frame", () => {
  const layout = emptyLayout();
  layout.types.device = basicType({
    frames: { mechanical_end: { transformation: [["ts", 1.25]] } },
  });
  layout.objects.D = {
    type: "device",
    position: {
      target: "mechanical_end",
      reference: { kind: "world" },
      transformation: [],
    },
  };
  const resolver = new M.Resolver(M.validateLayout(layout)).resolveAll();
  closeVector(resolver.objectCenter("D").frame.o, [0, 0, -1.25]);
  closeVector(resolver.objectFrame("D", "mechanical_end").frame.o, [0, 0, 0]);
});

test("object-frame ts can inherit and advance a known reference-curve station", () => {
  const layout = emptyLayout();
  layout.reference_curves.line = {
    color: "#123456",
    starting_frame: { reference: { kind: "world" }, transformation: [] },
    segments: [[20, 0, 0]],
  };
  layout.types.marker = basicType();
  layout.objects.A = {
    type: "marker",
    position: {
      target: "center",
      reference: { kind: "curve", curve: "line" },
      transformation: [["ts", 2]],
    },
  };
  layout.objects.B = {
    type: "marker",
    position: {
      target: "center",
      reference: { kind: "object_frame", object: "A", frame: "center" },
      transformation: [["ts", 3]],
      reference_curve: "line",
    },
  };
  const resolver = new M.Resolver(M.validateLayout(layout)).resolveAll();
  closeVector(resolver.objectCenter("B").frame.o, [0, 0, 5]);
  close(resolver.objectCenter("B").stations.line, 5);
});

test("normal-plane station inference handles a displaced world reference", () => {
  const layout = emptyLayout();
  layout.reference_curves.line = {
    color: "#123456",
    starting_frame: { reference: { kind: "world" }, transformation: [] },
    segments: [[20, 0, 0]],
  };
  layout.types.marker = basicType();
  layout.objects.M = {
    type: "marker",
    position: {
      target: "center",
      reference: { kind: "world" },
      transformation: [["ts", 2], ["tx", 1]],
      reference_curve: "line",
    },
  };
  // World is in the normal plane at s=0, so ts=2 selects s=2.
  const resolver = new M.Resolver(M.validateLayout(layout)).resolveAll();
  closeVector(resolver.objectCenter("M").frame.o, [1, 0, 2]);
});

test("magnetic entry and exit follow the curved type axis", () => {
  const type = basicType({
    shape: ["box", 1, 1, 2, 0.5, 0],
    magnetic_length: 2,
  });
  const entry = M.typeLocalFrame(type, "magnetic_entry").frame;
  const exit = M.typeLocalFrame(type, "magnetic_exit").frame;
  close(entry.o[0], exit.o[0]);
  close(entry.o[2], -exit.o[2]);
  assert.ok(entry.s[0] > 0);
  assert.ok(exit.s[0] < 0);
});

test("dependency graph is rooted at World and follows references", () => {
  const layout = M.clone(M.DEFAULT_LAYOUT);
  const graph = M.buildDependencyGraph(layout);
  const worldChildren = graph.children.get("world").map((edge) => edge.id);
  assert.deepEqual(worldChildren, ["curve:ring"]);
  const curveChildren = graph.children.get("curve:ring").map((edge) => edge.id);
  assert.deepEqual(curveChildren, ["object:B1", "object:QF1"]);
  assert.deepEqual(graph.children.get("object:B1").map((edge) => edge.id), ["object:BPM1"]);
});

test("cycles and stale reference_curve fields are rejected", () => {
  const layout = emptyLayout();
  layout.types.marker = basicType();
  layout.objects.A = {
    type: "marker",
    position: { target: "center", reference: { kind: "object_frame", object: "B", frame: "center" }, transformation: [] },
  };
  layout.objects.B = {
    type: "marker",
    position: { target: "center", reference: { kind: "object_frame", object: "A", frame: "center" }, transformation: [] },
  };
  assert.throws(() => M.validateLayout(layout), /dependency cycle/);

  const stale = emptyLayout();
  stale.reference_curves.line = {
    color: "#123456",
    starting_frame: { reference: { kind: "world" }, transformation: [] },
    segments: [[1, 0, 0]],
  };
  stale.types.marker = basicType();
  stale.objects.A = {
    type: "marker",
    position: { target: "center", reference: { kind: "world" }, transformation: [], reference_curve: "line" },
  };
  assert.throws(() => M.validateLayout(stale), /only valid when transformation contains ts/);
});
