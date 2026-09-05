import assert from "node:assert/strict";
import { readdir, readFile } from "node:fs/promises";
import path from "node:path";
import test, { after } from "node:test";
import { fileURLToPath } from "node:url";

import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
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

async function readCssTree(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const contents = await Promise.all(
    entries.map(async (entry) => {
      const entryPath = path.join(directory, entry.name);
      if (entry.isDirectory()) {
        return readCssTree(entryPath);
      }
      return entry.name.endsWith(".css") ? readFile(entryPath, "utf8") : "";
    }),
  );
  return contents.join("\n");
}

test("emits the catalog's animation and scrolling utilities", async () => {
  const css = await readCssTree(path.join(root, "dist"));

  assert.match(css, /--tw-enter-opacity/);
  assert.match(css, /scrollbar-width:\s*thin/);
  assert.match(css, /scrollbar-width:\s*none/);
  assert.match(css, /scrollbar-gutter:\s*stable/);
  assert.match(css, /scroll-fade-reveal-b/);
  assert.match(css, /mask-image:/);
  assert.match(css, /tw-shimmer/);
  assert.match(css, /prefers-reduced-motion:\s*reduce/);
});

test("forwards progress semantics to the primitive", async () => {
  const { Progress } = await vite.ssrLoadModule("/components/ui/progress.tsx");
  const html = renderToStaticMarkup(React.createElement(Progress, { value: 37 }));

  assert.match(html, /aria-valuenow="37"/);
  assert.match(html, /aria-valuetext="37%"/);
  assert.match(html, /data-state="loading"/);
});

test("emits chart themes for the starter's media dark mode", async () => {
  const { ChartStyle } = await vite.ssrLoadModule("/components/ui/chart.tsx");
  const html = renderToStaticMarkup(
    React.createElement(ChartStyle, {
      id: "contract",
      config: {
        latency: { theme: { light: "#ffffff", dark: "#000000" } },
      },
    }),
  );

  assert.match(html, /\[data-chart=contract\]/);
  assert.match(html, /@media \(prefers-color-scheme: dark\)/);
  assert.doesNotMatch(html, /\.dark/);
});

test("renders sidebar skeletons deterministically", async () => {
  const { SidebarMenuSkeleton } = await vite.ssrLoadModule(
    "/components/ui/sidebar.tsx",
  );
  const first = renderToStaticMarkup(React.createElement(SidebarMenuSkeleton));
  const second = renderToStaticMarkup(React.createElement(SidebarMenuSkeleton));

  assert.equal(first, second);
  assert.match(first, /--skeleton-width:70%/);
});

test("builds URL suggestions from local paths in list.json", async () => {
  const { layoutCatalogUrl, parseLayoutUrlList, resolveLayoutUrl } =
    await vite.ssrLoadModule("/app/layout-url-catalog.ts");
  const catalogUrl = "https://layout.example/tools/studio/list.json";
  const suggestions = parseLayoutUrlList(
    {
      files: [
        "layouts/sps.json",
        { path: "/layouts/lhc.json", label: "LHC" },
        "layouts/sps.json",
        "https://elsewhere.example/layout.json",
        "data:application/json,{}",
        "  ",
      ],
    },
    catalogUrl,
  );

  assert.deepEqual(suggestions, [
    {
      href: "https://layout.example/tools/studio/layouts/sps.json",
      path: "layouts/sps.json",
    },
    {
      href: "https://layout.example/layouts/lhc.json",
      path: "/layouts/lhc.json",
      label: "LHC",
    },
  ]);
  assert.equal(
    resolveLayoutUrl("layouts/sps.json", catalogUrl),
    "https://layout.example/tools/studio/layouts/sps.json",
  );
  assert.equal(
    resolveLayoutUrl("https://external.example/layout.json", catalogUrl),
    "https://external.example/layout.json",
  );
  assert.equal(
    layoutCatalogUrl(
      "https://layout.example/tools/studio/index.html?preview=1#viewer",
    ).href,
    "https://layout.example/tools/studio/list.json",
  );
  assert.equal(
    layoutCatalogUrl("https://layout.example/tools/studio/").href,
    "https://layout.example/tools/studio/list.json",
  );

  const { default: Home, LayoutUrlPicker } = await vite.ssrLoadModule(
    "/app/page.tsx",
  );
  const html = renderToStaticMarkup(React.createElement(Home));
  assert.match(html, /list="layout-url-suggestions"/);
  assert.match(html, /<datalist id="layout-url-suggestions"><\/datalist>/);
  assert.match(html, /aria-label="Available layout JSON files"/);
  assert.match(html, /No JSON catalog/);
  assert.match(html, /inputMode="url"/);
  assert.match(html, /Load URL/);

  const pickerHtml = renderToStaticMarkup(
    React.createElement(LayoutUrlPicker, {
      suggestions,
      onSelect() {},
    }),
  );
  assert.match(pickerHtml, /Available JSON…/);
  assert.match(pickerHtml, /value="layouts\/sps.json"/);
  assert.match(pickerHtml, /value="\/layouts\/lhc.json"/);
  assert.match(pickerHtml, /LHC — \/layouts\/lhc.json/);
});

test("renders viewer layers, world axes, and combines curve station with World pose", async () => {
  const {
    fitCameraToPoints,
    LayoutViewport,
    toggleViewerSelection,
    worldAxisMarkerProjection,
    zoomedCameraDistance,
  } = await vite.ssrLoadModule(
    "/app/layout-viewport.tsx",
  );
  const { TooltipProvider } = await vite.ssrLoadModule(
    "/components/ui/tooltip.tsx",
  );
  const layout = {
    reference_curves: {
      main: {
        color: "#7d91ff",
        starting_frame: {
          reference: { kind: "world" },
          transformation: [],
        },
        segments: [[10, 0, 0]],
      },
    },
    types: {},
    objects: {},
  };
  const html = renderToStaticMarkup(
    React.createElement(
      TooltipProvider,
      null,
      React.createElement(LayoutViewport, {
        layout,
        selection: { kind: "curve", name: "main" },
        onSelect() {},
      }),
    ),
  );

  assert.match(
    html,
    /<button(?=[^>]*aria-label="Show reference curves")(?=[^>]*data-state="checked")[^>]*>/,
  );
  assert.match(
    html,
    /<button(?=[^>]*aria-label="Show objects")(?=[^>]*data-state="checked")[^>]*>/,
  );
  assert.match(
    html,
    /<button(?=[^>]*aria-label="Show Beam entry and exit frames")(?=[^>]*data-state="checked")[^>]*>/,
  );
  assert.match(
    html,
    /aria-label="Selected reference curve station, world coordinates, and MAD-X Euler angles"/,
  );
  assert.match(html, /aria-label="World axis orientation"/);
  assert.match(html, /aria-label="Zoom to rectangle"/);
  assert.match(html, /aria-label="Canonical views"/);
  assert.match(html, /data-axis="x"/);
  assert.match(html, /data-axis="y"/);
  assert.match(html, /data-axis="z"/);
  assert.doesNotMatch(html, /aria-label="Selected reference curve and path position"/);
  assert.equal((html.match(/class="coordinate-readout"/g) ?? []).length, 1);
  assert.match(html, /World pose · Curve station/);
  assert.match(html, /Curve = main    s = 0.000000 m/);
  assert.match(html, /X = 0.000000 m/);
  assert.match(html, /Snapped to Segment 1 start/);

  const front = worldAxisMarkerProjection({ azimuth: 0, elevation: 0 });
  const frontAxes = Object.fromEntries(front.axes.map((axis) => [axis.label, axis]));
  assert.ok(frontAxes.X.x > front.origin.x);
  assert.ok(frontAxes.Y.y < front.origin.y);
  assert.ok(Math.abs(frontAxes.Z.x - front.origin.x) < 1e-10);
  assert.ok(Math.abs(frontAxes.Z.y - front.origin.y) < 1e-10);

  const unselectedHtml = renderToStaticMarkup(
    React.createElement(
      TooltipProvider,
      null,
      React.createElement(LayoutViewport, {
        layout,
        selection: null,
        onSelect() {},
      }),
    ),
  );
  assert.match(unselectedHtml, /aria-label="World coordinates and MAD-X Euler angles"/);
  assert.doesNotMatch(unselectedHtml, /World pose · Curve station/);
  assert.doesNotMatch(
    unselectedHtml,
    /aria-label="Selected reference curve and path position"/,
  );

  assert.equal(
    toggleViewerSelection(
      { kind: "curve", name: "main" },
      { kind: "curve", name: "main" },
    ),
    null,
  );
  assert.deepEqual(
    toggleViewerSelection(
      { kind: "curve", name: "main" },
      { kind: "curve", name: "main", segmentIndex: 2 },
    ),
    { kind: "curve", name: "main", segmentIndex: 2 },
  );
  assert.equal(
    toggleViewerSelection(
      { kind: "curve", name: "main", segmentIndex: 2 },
      { kind: "curve", name: "main", segmentIndex: 2 },
    ),
    null,
  );
  assert.deepEqual(
    toggleViewerSelection(
      { kind: "curve", name: "main", segmentIndex: 2 },
      { kind: "curve", name: "main", segmentIndex: 3 },
    ),
    { kind: "curve", name: "main", segmentIndex: 3 },
  );
  assert.equal(
    toggleViewerSelection(
      { kind: "object", name: "Q1" },
      { kind: "object", name: "Q1" },
    ),
    null,
  );
  assert.deepEqual(
    toggleViewerSelection(
      { kind: "frame", object: "Q1", name: "entry" },
      { kind: "object", name: "Q1" },
    ),
    { kind: "object", name: "Q1" },
  );
  assert.equal(
    toggleViewerSelection(
      { kind: "frame", object: "Q1", name: "entry" },
      { kind: "frame", object: "Q1", name: "entry" },
    ),
    null,
  );
  assert.deepEqual(
    toggleViewerSelection(
      { kind: "frame", object: "Q1", name: "entry" },
      { kind: "frame", object: "Q1", name: "exit" },
    ),
    { kind: "frame", object: "Q1", name: "exit" },
  );

  const camera = {
    azimuth: 0,
    elevation: 0,
    distance: 100,
    target: [0, 0, 0],
  };
  const detailView = fitCameraToPoints(
    camera,
    [
      [999999.99, 1999999.99, 3000000],
      [1000000.01, 2000000.01, 3000000],
    ],
    800,
    600,
  );
  assert.deepEqual(detailView.target, [1000000, 2000000, 3000000]);
  assert.ok(detailView.distance < 0.1);
  assert.ok(zoomedCameraDistance(0.5, -120, [0, 0, 0]) < 0.5);
});

test("reports scene failures through Python render barriers", async () => {
  const { viewportCommandRenderError } = await vite.ssrLoadModule(
    "/app/layout-viewport.tsx",
  );
  const barrier = {
    id: 7,
    command: "set_visibility",
    visibility: {},
  };

  assert.equal(
    viewportCommandRenderError(barrier, "cyclic dependency"),
    "Cannot render viewport: cyclic dependency",
  );
  assert.equal(viewportCommandRenderError(barrier, ""), undefined);
  assert.equal(
    viewportCommandRenderError(
      { id: 8, command: "set_mode", mode: "orbit" },
      "cyclic dependency",
    ),
    undefined,
  );
  const source = await readFile(
    path.join(root, "app", "layout-viewport.tsx"),
    "utf8",
  );
  assert.match(
    source,
    /if \(command\.command === "set_visibility"\)[\s\S]*?finish\(viewportCommandRenderError\(command, geometryError\)\)/,
  );
});

test("supports canonical camera directions and rectangle zoom", async () => {
  const {
    cameraForCanonicalView,
    zoomCameraToRectangle,
  } = await vite.ssrLoadModule("/app/layout-viewport.tsx");
  const viewportSource = await readFile(
    path.join(root, "app/layout-viewport.tsx"),
    "utf8",
  );
  const camera = {
    azimuth: 0.37,
    elevation: -0.21,
    distance: 100,
    target: [10, 20, 30],
  };
  const expectedAngles = {
    "+x": [Math.PI / 2, 0],
    "-x": [-Math.PI / 2, 0],
    "+y": [0, Math.PI / 2 - 1e-6],
    "-y": [0, -Math.PI / 2 + 1e-6],
    "+z": [0, 0],
    "-z": [Math.PI, 0],
  };

  for (const [view, [azimuth, elevation]] of Object.entries(expectedAngles)) {
    const canonical = cameraForCanonicalView(camera, view);
    assert.ok(Math.abs(canonical.azimuth - azimuth) < 1e-12);
    assert.ok(Math.abs(canonical.elevation - elevation) < 1e-12);
    assert.equal(canonical.distance, camera.distance);
    assert.deepEqual(canonical.target, camera.target);
  }

  for (const label of [
    "View from +X",
    "View from −X",
    "View from +Y",
    "View from −Y",
    "View from +Z",
    "View from −Z",
  ]) {
    assert.match(viewportSource, new RegExp(label.replace("+", "\\+")));
  }

  const front = cameraForCanonicalView(
    { ...camera, target: [0, 0, 0] },
    "+z",
  );
  const centered = zoomCameraToRectangle(
    front,
    { startX: 300, startY: 225, endX: 500, endY: 375 },
    800,
    600,
  );
  assert.deepEqual(centered.target, front.target);
  assert.equal(centered.distance, 25);

  const reverseDrag = zoomCameraToRectangle(
    front,
    { startX: 500, startY: 375, endX: 300, endY: 225 },
    800,
    600,
  );
  assert.deepEqual(reverseDrag, centered);

  const offCenter = zoomCameraToRectangle(
    front,
    { startX: 500, startY: 225, endX: 700, endY: 375 },
    800,
    600,
  );
  assert.ok(Math.abs(offCenter.target[0] - 200 * 100 / (600 * 0.92)) < 1e-12);
  assert.equal(offCenter.target[1], 0);
  assert.equal(offCenter.target[2], 0);
  assert.equal(offCenter.distance, 25);

  assert.equal(
    zoomCameraToRectangle(
      front,
      { startX: 300, startY: 225, endX: 300, endY: 375 },
      800,
      600,
    ),
    front,
  );
});

test("keeps canvas redraws cheap and display annotations stable", async () => {
  const {
    sceneBoundsForVisibility,
    syncCanvasDimensions,
    traceProjectedPolyline,
    viewportRelativeArrowLength,
  } = await vite.ssrLoadModule("/app/layout-viewport.tsx");

  let writes = 0;
  let backingWidth = 0;
  let backingHeight = 0;
  let cssWidth = "";
  let cssHeight = "";
  const canvas = {
    get width() { return backingWidth; },
    set width(value) { writes += 1; backingWidth = value; },
    get height() { return backingHeight; },
    set height(value) { writes += 1; backingHeight = value; },
    style: {
      get width() { return cssWidth; },
      set width(value) { writes += 1; cssWidth = value; },
      get height() { return cssHeight; },
      set height(value) { writes += 1; cssHeight = value; },
    },
  };
  syncCanvasDimensions(canvas, 800, 600, 2);
  assert.equal(writes, 4);
  assert.deepEqual(
    [backingWidth, backingHeight, cssWidth, cssHeight],
    [1600, 1200, "800px", "600px"],
  );
  syncCanvasDimensions(canvas, 800, 600, 2);
  assert.equal(writes, 4, "an unchanged redraw must not reset the backing store");

  const traced = [];
  traceProjectedPolyline(
    {
      moveTo(x, y) { traced.push(["M", x, y]); },
      lineTo(x, y) { traced.push(["L", x, y]); },
    },
    [
      { x: 0, y: 0 },
      { x: 1, y: 1 },
      null,
      { x: 3, y: 3 },
      { x: 4, y: 4 },
    ],
  );
  assert.deepEqual(traced, [
    ["M", 0, 0],
    ["L", 1, 1],
    ["M", 3, 3],
    ["L", 4, 4],
  ]);

  assert.equal(viewportRelativeArrowLength(2, 800, 600) * 2, 45);
  assert.equal(viewportRelativeArrowLength(0.5, 800, 600) * 0.5, 45);
  assert.equal(viewportRelativeArrowLength(1, 200, 100), 24);
  assert.equal(viewportRelativeArrowLength(1, 2000, 2000), 64);
  assert.equal(viewportRelativeArrowLength(0, 800, 600), 0);

  const scene = {
    curves: [{ samples: [{ p: [0, 0, 0] }, { p: [1, 2, 3] }] }],
    objects: [{ vertices: [[10, 0, 0], [12, 1, 1]] }],
    frames: [{ frame: { o: [1000, 1000, 1000] } }],
    magneticFrames: [{ vertices: [[2000, 2000, 2000]] }],
  };
  assert.deepEqual(
    sceneBoundsForVisibility(scene, {
      curves: true,
      objects: false,
      frames: false,
      beamFrames: false,
    }),
    { min: [0, 0, 0], max: [1, 2, 3] },
  );
  assert.deepEqual(
    sceneBoundsForVisibility(scene, {
      curves: false,
      objects: true,
      frames: false,
      beamFrames: false,
    }),
    { min: [10, 0, 0], max: [12, 1, 1] },
  );
});

test("keeps viewer wheel zoom local and exposes entity fit controls", async () => {
  const { default: Home } = await vite.ssrLoadModule("/app/page.tsx");
  const html = renderToStaticMarkup(React.createElement(Home));
  const viewportSource = await readFile(
    path.join(root, "app/layout-viewport.tsx"),
    "utf8",
  );

  assert.match(html, /aria-label="Fit curve ring in 3D view"/);
  assert.match(html, /aria-label="Fit object QF1 in 3D view"/);
  assert.match(viewportSource, /addEventListener\("wheel", handleWheel, \{ passive: false \}\)/);
  assert.match(viewportSource, /event\.preventDefault\(\);\s*event\.stopPropagation\(\);/s);
  assert.doesNotMatch(viewportSource, /onWheel=\{onWheel\}/);
  assert.doesNotMatch(viewportSource, /Math\.max\(\s*0\.5,/s);
});

test("uses named-frame terminology throughout the editor", async () => {
  const { default: Home } = await vite.ssrLoadModule("/app/page.tsx");
  const html = renderToStaticMarkup(React.createElement(Home));

  for (const wording of [
    "Named frames",
    "Frame name",
    "Target frame",
    "Object frame",
  ]) {
    assert.match(html, new RegExp(wording));
  }

  const [pageSource, viewportSource] = await Promise.all([
    readFile(path.join(root, "app/page.tsx"), "utf8"),
    readFile(path.join(root, "app/layout-viewport.tsx"), "utf8"),
  ]);
  assert.match(pageSource, /Inferring s from a referenced frame origin/);
  assert.match(viewportSource, /Hover a named frame, Beam frame, object, or curve/);
  assert.doesNotMatch(html, /Named points|Object point|Target point|Point name/);
});

test("orients the dependency hierarchy outward from World", async () => {
  const { buildLayoutDependencyHierarchy } = await vite.ssrLoadModule(
    "/app/dependency-tree.tsx",
  );
  const { parseLayout, SAMPLE_LAYOUT } = await vite.ssrLoadModule(
    "/app/layout-data.ts",
  );
  const layout = structuredClone(SAMPLE_LAYOUT);
  layout.objects.Detector.position.reference_curve = "ring";
  layout.objects.Detector.position.transformation = [["ts", 0]];

  const { dependentsByAnchor } = buildLayoutDependencyHierarchy(
    parseLayout(layout),
  );
  assert.deepEqual(
    dependentsByAnchor.get("world").map((edge) => edge.from),
    ["curve:ring"],
  );
  assert.deepEqual(
    dependentsByAnchor.get("curve:ring").map((edge) => [
      edge.from,
      edge.relation,
    ]),
    [
      ["object:QF1", "position_reference"],
      ["object:BPM1", "position_reference"],
      ["object:Detector", "station_curve"],
    ],
  );
  assert.deepEqual(
    dependentsByAnchor.get("object:QF1").map((edge) => [
      edge.from,
      edge.relation,
      edge.frame,
    ]),
    [["object:Detector", "position_reference", "magnetic_exit"]],
  );
});

test("renders collapsible controls for every main card", async () => {
  const { default: Home } = await vite.ssrLoadModule("/app/page.tsx");
  const html = renderToStaticMarkup(React.createElement(Home));

  for (const label of [
    "Hide reference curves card",
    "Hide types card",
    "Hide objects card",
    "Hide 3D layout card",
    "Hide dependency tree card",
  ]) {
    assert.match(html, new RegExp(`aria-label="${label}"`));
  }
  assert.match(
    html,
    /<div(?=[^>]*id="curves-card")(?=[^>]*data-state="open")[^>]*>/,
  );
  assert.match(
    html,
    /<div(?=[^>]*class="[^"]*viewport-card[^"]*")(?=[^>]*data-state="open")[^>]*>/,
  );
  assert.match(
    html,
    /<div(?=[^>]*id="dependencies-card")(?=[^>]*data-state="open")[^>]*>/,
  );
  assert.match(html, /class="[^"]*main-card-count[^"]*">1</);
  assert.match(html, /aria-label="Segment 1 editor"/);
  assert.match(html, /Dependency tree/);
  assert.match(html, />Expand all</);
  assert.match(html, />Collapse all</);
  assert.match(html, /role="tree" aria-label="Layout dependencies from World"/);
  assert.match(
    html,
    /<button(?=[^>]*aria-label="Expand dependents from World")(?=[^>]*aria-expanded="false")[^>]*>/,
  );
  assert.match(html, /class="dependency-node-name">World</);
  assert.doesNotMatch(html, /aria-label="Select object BPM1 from dependency tree"/);
  assert.doesNotMatch(html, /aria-label="Select curve ring from dependency tree"/);

  const [pageSource, dependencySource, css] = await Promise.all([
    readFile(path.join(root, "app/page.tsx"), "utf8"),
    readFile(path.join(root, "app/dependency-tree.tsx"), "utf8"),
    readFile(path.join(root, "app/globals.css"), "utf8"),
  ]);
  assert.match(pageSource, /curve\.segments\.length > 4/);
  assert.match(pageSource, /key=\{`dependencies-\$\{viewerRevision\}`\}/);
  assert.match(dependencySource, /useState<Set<string>>\(\(\) => new Set\(\)\)/);
  assert.match(dependencySource, /dependentsByAnchor\.get\(edge\.to\)/);
  assert.match(dependencySource, /graphNodes\.get\(edge\.from\)/);
  assert.match(dependencySource, /expandableBranchIds\(dependentsByAnchor\)/);
  assert.match(dependencySource, /setExpanded\(new Set\(branchIds\)\)/);
  assert.match(dependencySource, /setExpanded\(new Set\(\)\)/);
  assert.match(css, /\.segment-list-scrollable\s*\{[^}]*max-height:\s*207px/s);
  assert.match(css, /\.workspace\s*\{[^}]*grid-template-columns:[^}]*minmax\(270px, 0\.45fr\)/s);
  assert.match(css, /\.dependency-tree-scroll\s*\{[^}]*overflow:\s*auto/s);
});

test("renders a guarded top-bar action for starting an empty layout", async () => {
  const { default: Home } = await vite.ssrLoadModule("/app/page.tsx");
  const html = renderToStaticMarkup(React.createElement(Home));

  assert.match(
    html,
    /<button(?=[^>]*data-slot="alert-dialog-trigger")(?=[^>]*aria-label="Clear layout")[^>]*>[^<]*(?:<svg[\s\S]*?<\/svg>)?\s*Clear<\/button>/,
  );

  const pageSource = await readFile(path.join(root, "app/page.tsx"), "utf8");
  assert.match(pageSource, /<AlertDialogTitle>Clear the layout\?<\/AlertDialogTitle>/);
  assert.match(pageSource, /<AlertDialogAction variant="destructive" onClick=\{clearLayout\}>/);
  assert.match(pageSource, /setLayout\(createEmptyLayout\(\)\)/);
  assert.match(pageSource, /setSelectedTypeFrame\(""\)/);
  assert.match(pageSource, /setTypeFramesOpen\(true\)/);
  assert.match(pageSource, /setSelection\(null\)/);
  assert.match(pageSource, /<LayoutViewport\s+key=\{viewerRevision\}/);
});
