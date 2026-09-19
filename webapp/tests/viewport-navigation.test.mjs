import assert from "node:assert/strict";
import test, { after } from "node:test";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";

const root = fileURLToPath(new URL("..", import.meta.url));
const vite = await createServer({configFile: false, appType: "custom", root,
  resolve: {alias: {"@": root}}, server: {middlewareMode: true, hmr: false}});
after(() => vite.close());
const {cameraProjector, screenPointAtDepth, zoomCameraAtPoint, zoomCameraToRectangle,
  fitCameraToPoints, panCamera} = await vite.ssrLoadModule("/app/layout-viewport.tsx");
const {zoomFocusDepth} = await vite.ssrLoadModule("/app/viewport-zoom.ts");
const {cameraHistoryReducer, initialCameraHistory} = await vite.ssrLoadModule("/app/viewport-history.ts");
const width = 800, height = 600;
const base = {azimuth: 0, elevation: 0, distance: 10, target: [0, 0, 0]};
const close = (a, b, tolerance = 1e-8) => assert.ok(Math.abs(a - b) < tolerance, `${a} != ${b}`);

test("view history groups gestures and restores wheel, fit and region zoom exactly", () => {
  let history = initialCameraHistory({...base, axisScale: [0.1, 3, 0.4]});
  const initial = history.present;
  for (let i = 0; i < 20; i++) history = cameraHistoryReducer(history, {
    type: "change", group: "wheel-1",
    update: camera => zoomCameraAtPoint(camera, 420, 270, -12, width, height),
  });
  assert.equal(history.past.length, 1);
  const wheel = history.present;
  history = cameraHistoryReducer(history, {type: "change", update: camera =>
    fitCameraToPoints(camera, [[-3, -2, -100], [4, 2, 100]], width, height)});
  const fitted = history.present;
  history = cameraHistoryReducer(history, {type: "change", update: camera =>
    zoomCameraToRectangle(camera, {startX: 100, startY: 100, endX: 400, endY: 300}, width, height)});
  const region = history.present;
  for (const expected of [fitted, wheel, initial]) {
    history = cameraHistoryReducer(history, {type: "back"});
    assert.deepEqual(history.present, expected);
  }
  assert.equal(cameraHistoryReducer(history, {type: "back"}), history);
  for (const expected of [wheel, fitted, region]) {
    history = cameraHistoryReducer(history, {type: "forward"});
    assert.deepEqual(history.present, expected);
  }
  assert.equal(cameraHistoryReducer(history, {type: "forward"}), history);
});

test("view history ignores unchanged views and branches after going back during a gesture", () => {
  let history = initialCameraHistory(base);
  history = cameraHistoryReducer(history, {type: "change", group: "wheel-1",
    update: camera => ({...camera, distance: 5})});
  history = cameraHistoryReducer(history, {type: "back"});
  const unchanged = cameraHistoryReducer(history, {type: "change",
    update: camera => ({...camera, axisScale: [1, 1, 1]})});
  assert.equal(unchanged, history);
  assert.equal(unchanged.future.length, 1);
  history = cameraHistoryReducer(history, {type: "change", group: "wheel-1",
    update: camera => ({...camera, distance: 7})});
  assert.equal(history.past.length, 1);
  assert.equal(history.future.length, 0);
  assert.equal(history.present.distance, 7);
  history = cameraHistoryReducer(history, {type: "back"});
  assert.deepEqual(history.present, base);
});

test("view history keeps at most 100 steps and initial fitting creates no phantom step", () => {
  let history = initialCameraHistory(base);
  for (let i = 0; i < 150; i++) history = cameraHistoryReducer(history, {
    type: "change", update: camera => ({...camera, distance: camera.distance + 1}),
  });
  assert.equal(history.past.length, 100);
  assert.equal(history.past[0].distance, 60);
  history = cameraHistoryReducer(history, {type: "change", reset: true,
    update: camera => fitCameraToPoints(camera, [[0, 0, 0]], width, height)});
  assert.equal(history.past.length, 0);
  assert.equal(history.future.length, 0);
});

test("wheel zoom approaches distant off-center detail instead of stopping at the old target", () => {
  const detail = [20, -10, -500];
  for (const axisScale of [[1, 1, 1], [0.1, 3, 0.4]]) {
    let camera = {...base, axisScale};
    const initial = cameraProjector(camera, width, height)(detail);
    for (let i = 0; i < 70; i += 1) {
      const before = cameraProjector(camera, width, height)(detail);
      const next = zoomCameraAtPoint(camera, initial.x, initial.y, -120, width, height, before.depth);
      const after = cameraProjector(next, width, height)(detail);
      close(after.x, initial.x); close(after.y, initial.y);
      assert.ok(after.depth < before.depth);
      close(after.scale / before.scale, Math.exp(0.144));
      camera = next;
    }
    assert.ok(camera.distance < 0.03, "can reach centimetre detail from the whole-machine view");
    assert.ok(camera.target[2] < -499, "orbit target follows the actual detail");
  }
  assert.deepEqual(detail, [20, -10, -500]);
});

test("rectangle zoom centers and magnifies the actual geometry at its own depth", () => {
  const camera = {...base, axisScale: [0.2, 3, 0.5]};
  const detail = [80, 6, -500];
  const point = cameraProjector(camera, width, height)(detail);
  const rectangle = {startX: point.x - 100, endX: point.x + 100, startY: point.y - 75, endY: point.y + 75};
  const depth = zoomFocusDepth({faces: [], lines: [], points: [point]}, point.x, point.y, rectangle);
  const next = zoomCameraToRectangle(camera, rectangle, width, height, depth);
  next.target.forEach((value, i) => close(value, detail[i]));
  const after = cameraProjector(next, width, height)(detail);
  close(after.x, width / 2); close(after.y, height / 2);
  close(after.scale / point.scale, 4);
  assert.deepEqual(zoomCameraToRectangle(camera, {...rectangle,
    startX: rectangle.endX, endX: rectangle.startX, startY: rectangle.endY, endY: rectangle.startY}, width, height, depth), next);
});

test("zoom picking uses surface depth and perspective-correct line depth", () => {
  const polygon = [{x: 0, y: 0, depth: 100}, {x: 100, y: 0, depth: 200}, {x: 0, y: 100, depth: 400}];
  const geometry = {faces: [{polygon}], lines: [], points: []};
  close(zoomFocusDepth(geometry, 25, 50), 1 / (0.25 / 100 + 0.25 / 200 + 0.5 / 400));
  geometry.faces.push({polygon: polygon.map(p => ({...p, depth: 10}))});
  close(zoomFocusDepth(geometry, 25, 50), 10);
  assert.equal(zoomFocusDepth(geometry, 500, 500), undefined);
  const line = {faces: [], lines: [[{x: -100, y: 300, depth: 20}, {x: 900, y: 300, depth: 200}]], points: []};
  close(zoomFocusDepth(line, 400, 300, {startX: 300, startY: 200, endX: 500, endY: 400}), 1 / (0.5 / 20 + 0.5 / 200));
  // The center misses, but geometry elsewhere inside the box still supplies depth.
  close(zoomFocusDepth(geometry, 200, 200, {startX: -10, startY: -10, endX: 210, endY: 210}), 10);
});

test("axis scaling stays consistent with projection, inverse projection, panning and fit", () => {
  const camera = {...base, azimuth: -0.68, elevation: 0.42, axisScale: [0.001, 1000, 0.2], target: [1e6, -2e6, 3e6], distance: 300};
  const point = screenPointAtDepth(camera, 530, 220, 190, width, height);
  const projected = cameraProjector(camera, width, height)(point);
  close(projected.x, 530, 1e-4); close(projected.y, 220, 1e-4); close(projected.depth, 190, 1e-4);
  const center = cameraProjector(camera, width, height)(camera.target);
  const panned = cameraProjector(panCamera(camera, 50, -30, width, height), width, height)(camera.target);
  close(panned.x - center.x, 50, 1e-4); close(panned.y - center.y, -30, 1e-4);
  const points = [[-1, -1, -500], [1, 1, 500]];
  const perspective = {...base, azimuth: -0.68, elevation: 0.42};
  const normal = fitCameraToPoints(perspective, points, width, height);
  const compressed = fitCameraToPoints({...perspective, axisScale: [1, 1, 0.001]}, points, width, height);
  assert.ok(compressed.distance < normal.distance / 100);
  for (const p of points.map(cameraProjector(compressed, width, height))) {
    assert.ok(p.x >= width * 0.079 && p.x <= width * 0.921);
    assert.ok(p.y >= height * 0.079 && p.y <= height * 0.921);
  }
  assert.deepEqual(points, [[-1, -1, -500], [1, 1, 500]]);
});
