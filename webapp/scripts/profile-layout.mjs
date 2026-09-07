import { readFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import { performance } from "node:perf_hooks";
import { fileURLToPath } from "node:url";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

const root = fileURLToPath(new URL("..", import.meta.url));
const input = process.argv[2] ?? new URL("../public/layouts/SPS--LS3.json", import.meta.url);
const mode = process.argv[3] ?? "all";
if (!["all", "scene", "controls", "tree", "segments", "view", "lod"].includes(mode)) {
  throw new Error("Usage: node scripts/profile-layout.mjs <layout.json> [all|scene|controls|tree|segments|view|lod]");
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
  const text = await readFile(input, "utf8");
  console.log(JSON.stringify({input: String(input), jsonBytes: Buffer.byteLength(text), node: process.version}));
  const value = measure("JSON.parse", () => JSON.parse(text));
  const { parseLayout } = await vite.ssrLoadModule("/app/layout-data.ts");
  const layout = measure("parseLayout", () => parseLayout(value));
  console.log(JSON.stringify({ objects: Object.keys(layout.objects).length,
    types: Object.keys(layout.types).length,
    curves: Object.values(layout.reference_curves).map(curve => curve.segments.length) }));
  if (mode === "lod") {
    const {buildSceneSteps} = await vite.ssrLoadModule("/app/layout-geometry.ts");
    const {buildSpatialIndex, selectSceneDetail, compactProxies, runCooperatively, visibleCurveSamples} = await vite.ssrLoadModule("/app/layout-lod.ts");
    const {cameraBoundsProjector, cameraProjector, fitCameraToPoints} = await vite.ssrLoadModule("/app/layout-viewport.tsx");
    const signal = new AbortController().signal;
    const started = performance.now();
    const scene = await runCooperatively(buildSceneSteps(layout, {kind: "layout"}, {deferred: true}), signal);
    console.log(JSON.stringify({stage: "deferred scene", milliseconds: performance.now()-started, heapMB: process.memoryUsage().heapUsed/1024/1024}));
    const indexed = performance.now();
    const index = await runCooperatively(buildSpatialIndex(scene.objects), signal);
    console.log(JSON.stringify({stage: "spatial index", milliseconds: performance.now()-indexed, heapMB: process.memoryUsage().heapUsed/1024/1024}));
    const corners = [];
    for (const x of [scene.bounds.min[0], scene.bounds.max[0]]) for (const y of [scene.bounds.min[1], scene.bounds.max[1]]) for (const z of [scene.bounds.min[2], scene.bounds.max[2]]) corners.push([x,y,z]);
    const object = scene.deferred.objectByName.get("MB.B11R1") ?? scene.objects.find(object => {
      const shape = object.type.shape;
      return shape && (shape[0] === "box" ? shape[3] : shape[2]) > 1;
    }) ?? scene.objects[0];
    for (const [width, height] of [[512,613],[1200,800]]) {
      const overview = fitCameraToPoints({azimuth:-0.68,elevation:0.42,distance:20,target:[0,0,4]}, corners,width,height);
      for (const [name,camera] of [["overview",overview],["detail",{...overview, target: object.mechanicalFrame?.o ?? object.frame.o,distance:40}], ["scaled",{...overview,axisScale:[0.01,1,1]}]]) {
        const selection = measure(`LOD ${name} ${width}`, () => selectSceneDetail(scene,index,cameraBoundsProjector(camera,width,height), name==="detail"?object.name:undefined));
        const objects = measure("visible solids",() => selection.objects.map(object=>scene.deferred.detail(object)));
        const samples = measure("visible curves", () => scene.curves.flatMap(curve=>visibleCurveSamples(curve,cameraProjector(camera,width,height),width,height)));
        console.log(JSON.stringify({stage:name,width,height,detailed:objects.length,proxies:compactProxies(selection.proxies).length,visited:selection.visited,curvePoints:samples.filter(Boolean).length,faces:objects.reduce((sum,o)=>sum+o.faces.length,0),cachedSolids:scene.deferred.cachedSolids()}));
      }
    }
  }
  if (["scene", "all", "view"].includes(mode)) {
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
      faces: scene.objects.reduce((sum, object) => sum + object.faces.length, 0),
      edges: scene.objects.reduce((sum, object) => sum + object.edges.length, 0),
      magneticAxes: scene.magneticAxes.length, beamAxes: scene.beamAxes.length,
      vertices: scene.objects.reduce((sum, object) => sum + object.vertices.length, 0),
      bounds: scene.bounds,
      geometryHash: fingerprint.digest("hex"),
      curveSamples: scene.curves.reduce((sum, curve) => sum + curve.samples.length, 0) }));
    if (mode === "view") {
      const { cameraProjector, fitCameraToPoints } = await vite.ssrLoadModule("/app/layout-viewport.tsx");
      const corners = [];
      for (const x of [scene.bounds.min[0], scene.bounds.max[0]])
        for (const y of [scene.bounds.min[1], scene.bounds.max[1]])
          for (const z of [scene.bounds.min[2], scene.bounds.max[2]]) corners.push([x, y, z]);
      for (const [width, height] of [[512, 613], [1200, 800]]) {
        const camera = fitCameraToPoints({azimuth: -0.68, elevation: 0.42, distance: 20, target: [0, 0, 4]}, corners, width, height);
        const project = cameraProjector(camera, width, height);
        const counts = {solids: 0, offscreen: 0, below1px: 0, below2px: 0, below5px: 0};
        for (const object of scene.objects) {
          if (!object.faces.length) continue;
          counts.solids++;
          const points = object.vertices.map(project).filter(Boolean);
          if (!points.length) { counts.offscreen++; continue; }
          const left = Math.min(...points.map(p => p.x)), right = Math.max(...points.map(p => p.x));
          const top = Math.min(...points.map(p => p.y)), bottom = Math.max(...points.map(p => p.y));
          if (right < 0 || left > width || bottom < 0 || top > height) { counts.offscreen++; continue; }
          const extent = Math.max(right - left, bottom - top);
          if (extent < 1) counts.below1px++;
          if (extent < 2) counts.below2px++;
          if (extent < 5) counts.below5px++;
        }
        console.log(JSON.stringify({stage: "whole-layout projected bounds", width, height, ...counts}));
      }
    }
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
    const [curveName, curve] = Object.entries(layout.reference_curves)[0];
    for (const open of [false, true]) {
      const html = measure(open ? "segment page" : "hidden segments", () => renderToStaticMarkup(
        React.createElement(CurveSegmentEditor, {
          open, curveName, segments: curve.segments, page: 0,
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
