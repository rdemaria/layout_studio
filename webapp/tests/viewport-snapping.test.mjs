import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test, { after } from "node:test";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";

const root = fileURLToPath(new URL("..", import.meta.url));
const vite = await createServer({configFile: false, appType: "custom", root,
  resolve: {alias: {"@": root}}, server: {middlewareMode: true, hmr: false}});
after(() => vite.close());
const {parseLayout} = await vite.ssrLoadModule("/app/layout-data.ts");
const {buildScene, frameAtCurvePath, dot, sub} = await vite.ssrLoadModule("/app/layout-geometry.ts");
const {featurePlaneCurveStations, projectFeatureFrame, cameraProjector} = await vite.ssrLoadModule("/app/layout-viewport.tsx");
const fixture = JSON.parse(await readFile(new URL("../public/layouts/xsuite-rbend-axes.json", import.meta.url), "utf8"));
const features = ["mechanical", "magnetic", "beam"];
const allVisible = {mechanical: true, magnetic: true, beam: true};
const finish = generator => {let step; do {step = generator.next();} while (!step.done); return step.value;};
const close = (a, b) => assert.ok(Math.abs(a - b) < 1e-9, `${a} != ${b}`);

test("plane markers coincide with exact axis start, center and end under perspective", () => {
  const layout = structuredClone(fixture);
  // This bend originally used an odd number of line segments, missing its center.
  layout.types.symmetric.shape[4] = 0.07;
  const scene = buildScene(parseLayout(layout));
  let largestOldOffset = 0;
  for (const axisScale of [[1, 1, 1], [0.3, 2, 1]]) {
    const project = cameraProjector({azimuth: -0.68, elevation: 0.42,
      distance: 6, target: [-1.2, 0, 0], axisScale}, 800, 600);
    for (const feature of features) {
      for (const plane of scene[`${feature}Frames`]) {
        const axis = scene[`${feature}Axes`].find(axis => axis.object === plane.object);
        const expected = plane.name.endsWith("_entry") ? axis.samples[0]
          : plane.name.endsWith("_exit") ? axis.samples.at(-1)
          : axis.samples.find(sample => sample.path === 0);
        assert.ok(expected, `${plane.name} must be a vertex of its axis polyline`);
        const rendered = projectFeatureFrame(plane, project);
        assert.ok(rendered);
        const axisPoint = project(expected.p);
        close(rendered.origin.x, axisPoint.x);
        close(rendered.origin.y, axisPoint.y);
        const average = rendered.polygon.reduce((p, v) => ({x: p.x + v.x / rendered.polygon.length,
          y: p.y + v.y / rendered.polygon.length}), {x: 0, y: 0});
        largestOldOffset = Math.max(largestOldOffset, Math.hypot(average.x - axisPoint.x, average.y - axisPoint.y));
      }
    }
  }
  assert.ok(largestOldOffset > 1, "fixture reproduces a visible perspective offset");
});

test("world-positioned RBends expose their visible axis plane crossings on a curved reference", () => {
  const scene = buildScene(parseLayout(fixture));
  for (const curve of scene.curves) {
    const object = curve.name.replace("_reference", "");
    const stations = finish(featurePlaneCurveStations(curve, scene, allVisible));
    // The asymmetric mechanical end plane does not reach the reference curve.
    assert.equal(stations.length, object === "symmetric" ? 9 : 8);
    for (const feature of features) {
      const matches = stations.filter(station => station.sources[0].feature === feature);
      const boundaries = object === "asymmetric" && feature === "mechanical"
        ? ["entry", "center"] : ["entry", "center", "exit"];
      assert.deepEqual(matches.map(station => station.sources[0].name),
        boundaries.map(boundary => `${feature}_${boundary}`));
      assert.ok(matches.every((station, index) => index === 0 || matches[index - 1].path < station.path));
      for (const station of matches) {
        assert.equal(station.sources[0].object, object);
        const plane = scene[`${feature}Frames`].find(frame => frame.object === object && frame.name === station.sources[0].name);
        close(dot(sub(station.frame.o, plane.frame.o), plane.frame.s), 0);
        assert.deepEqual(station.frame, frameAtCurvePath(curve, station.path));
      }
    }
  }
});

test("each axis layer controls only its own snap targets in every visibility combination", () => {
  const scene = buildScene(parseLayout(fixture));
  for (let mask = 0; mask < 8; mask++) {
    const visibility = Object.fromEntries(features.map((feature, index) => [feature, Boolean(mask & (1 << index))]));
    const stations = finish(featurePlaneCurveStations(scene.curves[0], scene, visibility));
    assert.equal(stations.length, features.filter(feature => visibility[feature]).length * 3);
    assert.ok(stations.every(station => visibility[station.sources[0].feature]));
  }
});

test("deferred feature layers include the same center planes and snap targets as full geometry", () => {
  const layout = parseLayout(fixture);
  const scene = buildScene(layout);
  const deferred = buildScene(layout, undefined, {deferred: true});
  const layers = Object.fromEntries(features.map(feature => [`${feature}Frames`,
    Object.keys(layout.objects).flatMap(object => deferred.deferred.feature(object, feature).frames)]));
  for (const curve of deferred.curves) {
    assert.deepEqual(finish(featurePlaneCurveStations(curve, layers, allVisible)),
      finish(featurePlaneCurveStations(scene.curves.find(candidate => candidate.name === curve.name), scene, allVisible)));
  }
});

test("finite planes retain multiple isolated crossings and reject misses and tangent or coincident curves", () => {
  const makeCurve = segments => buildScene(parseLayout({reference_curves: {main: {
    color: "#67b7ff", starting_frame: {reference: {kind: "world"}, transformation: []}, segments,
  }}, types: {}, objects: {}})).curves[0];
  const frame = {o: [-1, 0, 0], x: [0, 0, 1], y: [0, 1, 0], s: [1, 0, 0]};
  const plane = {object: "world_object", typeName: "test", kind: "mechanical", name: "mechanical_center", frame,
    vertices: [[-1, -2, -2], [-1, -2, 2], [-1, 2, 2], [-1, 2, -2]]};
  const stations = (curve, plane) => finish(featurePlaneCurveStations(curve,
    {mechanicalFrames: [plane], magneticFrames: [], beamFrames: []}, allVisible));
  const circle = makeCurve([[2 * Math.PI, 2 * Math.PI, 0]]);
  const crossings = stations(circle, plane);
  assert.equal(crossings.length, 2);
  close(crossings[0].path, Math.PI / 2);
  close(crossings[1].path, 3 * Math.PI / 2);
  assert.equal(stations(circle, {...plane, vertices: plane.vertices.map(([x,y,z]) => [x, y, z + 10])}).length, 0);
  const tangent = {...plane, frame: {...frame, o: [-2, 0, 0]}, vertices: plane.vertices.map(([,y,z]) => [-2, y, z])};
  assert.equal(stations(circle, tangent).length, 0);
  const coincident = {...plane, frame: {...frame, o: [0, 0, 0]}, vertices: plane.vertices.map(([,y,z]) => [0, y, z])};
  assert.equal(stations(makeCurve([[1, 0, 0]]), coincident).length, 0);
});
