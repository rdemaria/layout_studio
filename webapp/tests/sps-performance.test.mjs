import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { performance } from "node:perf_hooks";
import test, { after } from "node:test";
import { fileURLToPath } from "node:url";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

const root = fileURLToPath(new URL("..", import.meta.url));
const vite = await createServer({
  appType: "custom", configFile: false, root,
  resolve: { alias: { "@": root } },
  server: { middlewareMode: true, hmr: false },
});
after(() => vite.close());
const { parseLayout } = await vite.ssrLoadModule("/app/layout-data.ts");
const { buildScene } = await vite.ssrLoadModule("/app/layout-geometry.ts");
const { CurveSegmentEditor, SEGMENT_PAGE_SIZE } = await vite.ssrLoadModule("/app/curve-segment-editor.tsx");
const { ReferenceEditor } = await vite.ssrLoadModule("/app/layout-controls.tsx");
const layout = parseLayout(JSON.parse(await readFile(new URL("../public/layouts/SPS--LS3.json", import.meta.url), "utf8")));

test("SPS geometry completes with every object, named frame, and inherited beam axis", () => {
  const started = performance.now();
  const scene = buildScene(layout);
  // A generous ceiling catches severe regressions without treating CI timing
  // as a browser benchmark. The profiling script reports exact stage timings.
  assert.ok(performance.now() - started < 10_000, "SPS geometry exceeded 10 seconds");
  assert.equal(scene.objects.length, 12339);
  assert.equal(scene.frames.length, 55902);
  assert.equal(scene.magneticAxes.length, 1931);
  assert.equal(scene.beamAxes.length, 1931);
  for (const object of scene.objects) {
    assert.ok(object.frame.o.every(Number.isFinite), object.name);
  }
  for (let index = 0; index < scene.beamAxes.length; index++) {
    assert.deepEqual(scene.beamAxes[index].samples, scene.magneticAxes[index].samples);
  }

  const moved = structuredClone(layout);
  moved.reference_curves.SPS.starting_frame.transformation.push(["ty", 10]);
  const movedScene = buildScene(moved);
  assert.ok(Math.abs(movedScene.objects[0].frame.o[1] - scene.objects[0].frame.o[1] - 10) < 1e-9,
    "a new scene must recompute stations after editing a curve");
});

test("SPS hidden segments mount no controls while an enclosing collapsible is closing", () => {
  const html = renderToStaticMarkup(React.createElement(CurveSegmentEditor, {
    open: false, curveName: "SPS", segments: layout.reference_curves.SPS.segments,
    page: 0, onPageChange() {}, onChange() {}, onRemove() {}, onAdd() {},
  }));
  assert.equal(html, "");
});

test("SPS segment pages keep controls bounded and preserve absolute segment indices", () => {
  const segments = layout.reference_curves.SPS.segments;
  for (const [page, first, last] of [[0, 1, 50], [1, 51, 100], [29, 1451, 1489], [30, 1451, 1489]]) {
    const html = renderToStaticMarkup(React.createElement(CurveSegmentEditor, {
      open: true, curveName: "SPS", segments, page, selectedIndex: last - 1,
      onPageChange() {}, onChange() {}, onRemove() {}, onAdd() {},
    }));
    const inputs = (html.match(/role="spinbutton"/g) ?? []).length;
    assert.equal(inputs, (last - first + 1) * 3);
    assert.ok(inputs <= SEGMENT_PAGE_SIZE * 3);
    assert.ok(html.includes(`id="curve-segment-row-${first - 1}"`));
    assert.ok(html.includes(`aria-label="Segment ${last} editor"`));
    assert.match(html, /selected-segment-row/);
  }
});

test("SPS reference selectors do not mount thousands of native options", () => {
  const name = Object.keys(layout.objects)[0];
  const html = renderToStaticMarkup(React.createElement(ReferenceEditor, {
    layout, owner: { kind: "object", name }, value: layout.objects[name].position,
    onChange() {},
  }));
  assert.ok((html.match(/<option\b/g) ?? []).length < 100);
  assert.ok(html.length < 60_000, "reference controls grew with the entire SPS object list");
  assert.ok(html.includes(`value="${layout.objects[name].position.reference.object}"`));
});
