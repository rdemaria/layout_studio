import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import test, {after} from "node:test";
import {fileURLToPath} from "node:url";
import {createServer} from "vite";

const root = fileURLToPath(new URL("..", import.meta.url));
const vite = await createServer({configFile: false, appType: "custom", root,
  resolve: {alias: {"@": root}}, server: {middlewareMode: true, hmr: false}});
after(() => vite.close());
const {buildLayoutDependencyHierarchy, dependencySelectionReveal, dependencySelectionNodeId} =
  await vite.ssrLoadModule("/app/dependency-tree.tsx");
const {parseLayout, SAMPLE_LAYOUT} = await vite.ssrLoadModule("/app/layout-data.ts");
const world = {kind: "world"};
const object = (reference, transformation = []) => ({type: "box",
  position: {target: "anchor", reference, transformation}});
const base = () => ({reference_curves: {}, types: {box: {color: "#abcdef", frames: {}}}, objects: {}});

test("selecting an object or its frame reveals only its placement ancestors", () => {
  const hierarchy = buildLayoutDependencyHierarchy(parseLayout(SAMPLE_LAYOUT));
  const selected = dependencySelectionReveal(hierarchy, dependencySelectionNodeId({kind: "object", name: "Detector"}));
  assert.deepEqual(selected.expanded, ["world", "world/starting_frame:curve:ring:",
    "world/starting_frame:curve:ring:/position_reference:object:QF1:"]);
  assert.ok(selected.branchId.endsWith("/position_reference:object:Detector:magnetic_exit"));
  assert.deepEqual([...selected.pages.values()], [0, 0, 0]);
  assert.deepEqual(dependencySelectionReveal(hierarchy,
    dependencySelectionNodeId({kind: "frame", object: "Detector", name: "anchor"})), selected);
  assert.equal(dependencySelectionReveal(hierarchy, null), null);
  assert.equal(dependencySelectionReveal(hierarchy, "object:missing"), null);
});

test("selection switches to the right page at every ancestor including World", () => {
  const layout = base();
  for (let index = 0; index < 125; index++) layout.objects[`Parent${index}`] = object(world);
  for (let index = 0; index < 125; index++) layout.objects[`Child${index}`] = object(
    {kind: "object_frame", object: "Parent104", frame: "anchor"});
  const hierarchy = buildLayoutDependencyHierarchy(parseLayout(layout));
  const selected = dependencySelectionReveal(hierarchy, "object:Child111");
  assert.deepEqual([...selected.pages], [["world", 2], ["world/position_reference:object:Parent104:", 2]]);
  assert.equal(selected.expanded.length, 2);
  assert.ok(selected.branchId.endsWith("/position_reference:object:Child111:anchor"));
  const previous = dependencySelectionReveal(hierarchy, "object:Child2");
  assert.deepEqual([...previous.pages.values()], [2, 0]);
  assert.deepEqual([...selected.pages.values()], [2, 2], "reselection does not mutate earlier reveals");
});

test("selection pages follow sorted curve stations rather than object insertion order", () => {
  const layout = base();
  layout.reference_curves.arc = {color: "#abcdef", starting_frame: {reference: world, transformation: []},
    segments: [[200, 0, 0]]};
  for (let index = 120; index >= 0; index--) layout.objects[`At${index}`] = object(
    {kind: "curve", curve: "arc"}, [["ts", index]]);
  const hierarchy = buildLayoutDependencyHierarchy(parseLayout(layout));
  assert.deepEqual([...dependencySelectionReveal(hierarchy, "object:At119").pages.values()], [0, 2]);
  assert.deepEqual([...dependencySelectionReveal(hierarchy, "object:At1").pages.values()], [0, 0]);
});

test("valid independent frames with cyclic object-level parents can still be revealed", () => {
  const layout = base();
  layout.types.external = {color: "#abcdef", frames: {survey: {
    reference: {kind: "object_frame", object: "Origin", frame: "anchor"}, transformation: [],
  }}};
  layout.objects.Origin = object(world);
  layout.objects.A = {...object({kind: "object_frame", object: "B", frame: "survey"}), type: "external"};
  layout.objects.B = {...object({kind: "object_frame", object: "A", frame: "survey"}), type: "external"};
  const hierarchy = buildLayoutDependencyHierarchy(parseLayout(layout));
  for (const name of ["A", "B"]) {
    const selected = dependencySelectionReveal(hierarchy, `object:${name}`);
    assert.ok(selected);
    assert.equal(selected.expanded.length, 2);
    assert.ok(selected.branchId.includes("object:Origin:"));
    assert.ok(selected.branchId.includes(`feature_reference:object:${name}:anchor`));
  }
});

test("LHC selection opens a short ancestor path without expanding the full hierarchy", async () => {
  const layout = JSON.parse(await readFile(new URL("../public/layouts/LHC--LS3.json", import.meta.url), "utf8"));
  const hierarchy = buildLayoutDependencyHierarchy(layout);
  const selected = dependencySelectionReveal(hierarchy, "object:MB.B11R1");
  assert.ok(selected);
  assert.ok(selected.expanded.length > 1 && selected.expanded.length < 20);
  assert.equal(selected.pages.size, selected.expanded.length);
  assert.ok(selected.branchId.includes("object:MB.B11R1:"));
  assert.ok(hierarchy.graphNodes.size > 160000);
});
