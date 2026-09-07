import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test, { after } from "node:test";
import { fileURLToPath } from "node:url";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

const root = fileURLToPath(new URL("..", import.meta.url));
const vite = await createServer({configFile: false, appType: "custom", root,
  resolve: {alias: {"@": root}}, server: {middlewareMode: true, hmr: false}});
after(() => vite.close());
const {parseLayout, anchorRelativeFrameNames, objectFrameNames} = await vite.ssrLoadModule("/app/layout-data.ts");
const {buildScene} = await vite.ssrLoadModule("/app/layout-geometry.ts");
const fixture = JSON.parse(await readFile(new URL("../../specifications/examples/anchored-features.json", import.meta.url), "utf8"));
const close = (a,b) => a.forEach((n,i) => assert.ok(Math.abs(n-b[i]) < 1e-11, `${a} != ${b}`));

test("referenced features resolve independently and align a chained target", () => {
  const layout = parseLayout(fixture);
  assert.deepEqual(layout, fixture);
  const scene = buildScene(layout);
  const a = scene.objects.find(o => o.name === "A");
  close(a.frame.o, [9,-2,0]);
  close(a.mechanicalFrame.o, [10,0,0]);
  close(scene.magneticAxes.find(o => o.object === "A").centerFrame.o, [10,0,0.5]);
  close(scene.frames.find(f => f.object === "A" && f.name === "survey_world").frame.o, [100,0,0]);
  close(scene.frames.find(f => f.object === "A" && f.name === "survey_other").frame.o, [20,3.2,0]);
  const magnetic = scene.magneticAxes.find(o => o.object === "C");
  const beam = scene.beamAxes.find(o => o.object === "C");
  assert.deepEqual(beam.samples, magnetic.samples);
  const targets = anchorRelativeFrameNames(layout.types.magnet, layout.objects.A, "A");
  assert.ok(targets.includes("after_magnet"));
  for (const name of ["beam_center", "survey_world", "survey_other", "survey_curve"]) assert.ok(!targets.includes(name));
});

test("legacy center links import as anchor and geometry edits move only dependent features", () => {
  const legacy = structuredClone(fixture);
  legacy.objects.B.position.target = "center";
  legacy.objects.A.beam_center.reference.frame = "center";
  const layout = parseLayout(legacy);
  assert.equal(layout.objects.B.position.target, "anchor");
  assert.equal(layout.objects.A.beam_center.reference.frame, "anchor");
  const before = buildScene(layout);
  layout.objects.A.position.target = "anchor";
  layout.types.magnet.mechanical_center.transformation.push(["tx", 2]);
  const after = buildScene(layout);
  close(after.objects.find(o => o.name === "A").frame.o, [10,0,0]);
  close(after.objects.find(o => o.name === "A").mechanicalFrame.o, [13,2,0]);
  assert.deepEqual(after.beamAxes.find(o => o.object === "A"), before.beamAxes.find(o => o.object === "A"));
});

test("frame validation rejects dangling, cyclic, and externally rooted targets", () => {
  for (const mutate of [
    d => {d.types.magnet.mechanical_center.reference.frame = "missing";},
    d => {d.types.magnet.frames.mount.reference = {kind:"local_frame",frame:"mechanical_center"};},
    d => {d.types.magnet.magnetic_center.reference = {kind:"local_frame",frame:"beam_center"};},
    d => {d.objects.A.position.target = "survey_world";},
    d => {d.reference_curves.arc.starting_frame.reference = {kind:"local_frame",frame:"anchor"};},
    d => {delete d.types.magnet.shape;},
  ]) {
    const input = structuredClone(fixture); mutate(input);
    assert.throws(() => parseLayout(input), /unknown|cycle|anchor|reference|shape/i);
  }
});

test("acyclic external features may refer back to an object positioned from their owner", () => {
  const input = structuredClone(fixture);
  input.objects.B.position.reference = {kind:"object_frame",object:"A",frame:"survey_world"};
  assert.doesNotThrow(() => buildScene(parseLayout(input)));
});

test("every feature placement exposes anchor, local and external reference choices", async () => {
  const {FeaturePlacementEditor} = await vite.ssrLoadModule("/app/feature-placement-editor.tsx");
  const layout = parseLayout(fixture);
  for (const name of ["mechanical_center", "magnetic_center", "beam_center", "mount"]) {
    const html = renderToStaticMarkup(React.createElement(FeaturePlacementEditor, {
      layout, value:{transformation:[]}, frameName:name,
      frameNames:objectFrameNames(layout.types.magnet,layout.objects.A), onChange() {},
    }));
    assert.match(html, /Anchor \(default\)/);
    assert.match(html, /Local frame/);
    assert.match(html, /Object frame/);
  }
});
