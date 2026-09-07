import { readFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import { performance } from "node:perf_hooks";
import { fileURLToPath } from "node:url";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

const root = fileURLToPath(new URL("..", import.meta.url));
const mode = process.argv[2] ?? "all";
if (!["all", "scene", "controls", "tree", "segments"].includes(mode)) {
  throw new Error("Usage: node scripts/profile-sps.mjs [all|scene|controls|tree|segments]");
}
const vite = await createServer({
  appType: "custom", configFile: false, root,
  resolve: { alias: { "@": root } },
  server: { middlewareMode: true, hmr: false },
});
function measure(label, run) {
  const started = performance.now();
  const value = run();
  console.log(JSON.stringify({ stage: label, milliseconds: performance.now() - started,
    heapMB: process.memoryUsage().heapUsed / 1024 / 1024 }));
  return value;
}
try {
  const text = await readFile(new URL("../public/layouts/SPS--LS3.json", import.meta.url), "utf8");
  const value = measure("JSON.parse", () => JSON.parse(text));
  const { parseLayout } = await vite.ssrLoadModule("/app/layout-data.ts");
  const layout = measure("parseLayout", () => parseLayout(value));
  console.log(JSON.stringify({ objects: Object.keys(layout.objects).length,
    types: Object.keys(layout.types).length,
    curves: Object.values(layout.reference_curves).map(curve => curve.segments.length) }));
  if (mode === "scene" || mode === "all") {
    const { buildScene } = await vite.ssrLoadModule("/app/layout-geometry.ts");
    const scene = measure("buildScene", () => buildScene(layout));
    const fingerprint = createHash("sha256");
    for (const [kind, values] of Object.entries(scene)) {
      fingerprint.update(kind);
      if (kind === "objects") {
        for (const object of values) {
          fingerprint.update(JSON.stringify([object.name, object.frame, object.vertices]));
        }
      } else fingerprint.update(JSON.stringify(values));
    }
    console.log(JSON.stringify({ objects: scene.objects.length, frames: scene.frames.length,
      vertices: scene.objects.reduce((sum, object) => sum + object.vertices.length, 0),
      bounds: scene.bounds,
      geometryHash: fingerprint.digest("hex"),
      curveSamples: scene.curves.reduce((sum, curve) => sum + curve.samples.length, 0) }));
  }
  if (mode === "controls" || mode === "all") {
    const { ReferenceEditor } = await vite.ssrLoadModule("/app/layout-controls.tsx");
    for (const [kind, definitions] of [["curve", layout.reference_curves], ["object", layout.objects]]) {
      const [name, definition] = Object.entries(definitions)[0];
      const html = measure(`${kind} reference editor`, () => renderToStaticMarkup(
        React.createElement(ReferenceEditor, { layout, owner: { kind, name },
          value: kind === "curve" ? definition.starting_frame : definition.position,
          onChange() {} }),
      ));
      console.log(JSON.stringify({ kind, name, htmlBytes: html.length }));
    }
  }
  if (mode === "tree" || mode === "all") {
    const { DependencyTree, buildLayoutDependencyHierarchy } = await vite.ssrLoadModule("/app/dependency-tree.tsx");
    const tree = measure("dependency graph", () => buildLayoutDependencyHierarchy(layout));
    console.log(JSON.stringify({ nodes: tree.graphNodes.size,
      edges: [...tree.dependentsByAnchor.values()].reduce((sum, edges) => sum + edges.length, 0) }));
    const html = measure("collapsed dependency tree", () => renderToStaticMarkup(
      React.createElement(DependencyTree, { layout, selection: null, onSelect() {} }),
    ));
    console.log(JSON.stringify({ htmlBytes: html.length }));
  }
  if (mode === "segments" || mode === "all") {
    const { CurveSegmentEditor } = await vite.ssrLoadModule("/app/curve-segment-editor.tsx");
    for (const open of [false, true]) {
      const html = measure(open ? "segment page" : "hidden segments", () => renderToStaticMarkup(
        React.createElement(CurveSegmentEditor, {
          open, curveName: "SPS", segments: layout.reference_curves.SPS.segments, page: 0,
          onPageChange() {}, onChange() {}, onRemove() {}, onAdd() {},
        }),
      ));
      console.log(JSON.stringify({ open, htmlBytes: html.length,
        numericInputs: (html.match(/role="spinbutton"/g) ?? []).length }));
    }
  }
} finally {
  await vite.close();
}
