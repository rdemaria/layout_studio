import assert from "node:assert/strict";
import test, {after} from "node:test";
import {fileURLToPath} from "node:url";
import {createServer} from "vite";
import Ajv from "ajv/dist/2020.js";
import {readFile} from "node:fs/promises";
import React from "react";
import {renderToStaticMarkup} from "react-dom/server";

const root = fileURLToPath(new URL("..", import.meta.url));
const vite = await createServer({appType: "custom", configFile: false, root,
  resolve: {alias: {"@": root}}, server: {middlewareMode: true, hmr: false}});
after(() => vite.close());
const {parseLayout, SAMPLE_LAYOUT} = await vite.ssrLoadModule("/app/layout-data.ts");
const {restoreLayoutUiState} = await vite.ssrLoadModule("/app/layout-ui-state.ts");
const {installLayoutFileDrop} = await vite.ssrLoadModule("/app/layout-file-drop.ts");
const {loadLayoutAsync} = await vite.ssrLoadModule("/app/layout-load.ts");
const schema = JSON.parse(await readFile(new URL("../../specifications/layout.schema.json", import.meta.url)));
const validate = new Ajv({strict: false}).compile(schema);

test("optional UI state round-trips through file loading and the canonical schema", async () => {
  const ui = restoreLayoutUiState(SAMPLE_LAYOUT);
  ui.editor.selection = {kind: "object", name: "QF1"};
  ui.cards.dependencies = false;
  ui.cards.viewer = false;
  ui.viewport.camera = {azimuth: 1.2, elevation: 0.3, distance: 123, target: [4, 5, 6], axisScale: [2, 1, 0.5]};
  ui.viewport.pastViews = [{...ui.viewport.camera, distance: 246}];
  ui.viewport.showMechanicalAxis = true;
  ui.viewport.mode = "pan";
  ui.dependencies = {expanded: ["world"], pages: {world: 2}, selection: "object:QF1"};
  const value = {...SAMPLE_LAYOUT, ui_state: {...ui, extension: {future: null}}};
  assert.equal(validate(value), true, JSON.stringify(validate.errors));
  const parsed = await loadLayoutAsync({file: new File([JSON.stringify(value)], "saved.json")});
  assert.deepEqual(restoreLayoutUiState(parsed), ui);
  assert.deepEqual(parsed.ui_state.extension, {future: null});
  assert.notEqual(parsed.ui_state, value.ui_state);
  assert.deepEqual(parsed.objects, parseLayout(SAMPLE_LAYOUT).objects);
});

test("legacy layouts and unsupported UI versions use defaults", () => {
  const plain = parseLayout(SAMPLE_LAYOUT);
  assert.equal(Object.hasOwn(plain, "ui_state"), false);
  const defaults = restoreLayoutUiState(plain);
  assert.equal(defaults.viewport.camera, undefined);
  assert.equal(defaults.cards.viewer, true);
  const future = parseLayout({...SAMPLE_LAYOUT, ui_state: {version: 99, cards: {viewer: false}}});
  assert.deepEqual(restoreLayoutUiState(future), defaults);
  assert.equal(future.ui_state.version, 99);
});

test("stale selections, bad camera values, and invalid settings cannot break geometry loading", () => {
  const parsed = parseLayout({...SAMPLE_LAYOUT, ui_state: {version: 1,
    editor: {selectedObject: "missing", selectedCurve: "__proto__", selectedType: "constructor", segmentPage: 1e12,
      selection: {kind: "object", name: "missing"}},
    scope: {kind: "object", name: "missing"}, cards: {viewer: "false"},
    viewport: {camera: {azimuth: 0, elevation: 0, distance: -1, target: [0, 0, 0]},
      pastViews: [null, {}], showObjects: "false", mode: "invalid"},
    dependencies: {expanded: [null, "world"], pages: {world: -1, missing: 0.5}},
  }});
  const ui = restoreLayoutUiState(parsed);
  assert.equal(ui.editor.selection, null);
  assert.equal(ui.editor.selectedCurve, "ring");
  assert.notEqual(ui.editor.selectedType, "constructor");
  assert.deepEqual(ui.scope, {kind: "layout"});
  assert.equal(ui.viewport.camera, undefined);
  assert.equal(ui.viewport.showObjects, true);
  assert.equal(ui.cards.viewer, true);
  assert.deepEqual(ui.viewport.pastViews, []);
  assert.deepEqual(ui.dependencies.pages, {});
  assert.deepEqual(ui.dependencies.expanded, ["world"]);
  assert.equal(ui.editor.segmentPage, 0);
  for (const invalid of [null, [], "state"]) {
    assert.throws(() => parseLayout({...SAMPLE_LAYOUT, ui_state: invalid}), /ui_state must be a JSON object/);
  }
  assert.throws(() => parseLayout({...SAMPLE_LAYOUT, ui_state: {distance: Infinity}}), /finite numbers/);
});

test("saved viewport controls and zoom history are initialized before the first render", async () => {
  const {LayoutViewport} = await vite.ssrLoadModule("/app/layout-viewport.tsx");
  const {TooltipProvider} = await vite.ssrLoadModule("/components/ui/tooltip.tsx");
  const state = restoreLayoutUiState(SAMPLE_LAYOUT).viewport;
  state.camera = {azimuth: 1, elevation: 0, distance: 20, target: [1, 2, 3]};
  state.pastViews = [{...state.camera, distance: 40}];
  state.mode = "pan";
  state.showCurves = false;
  state.showMechanicalAxis = true;
  const html = renderToStaticMarkup(React.createElement(TooltipProvider, null, React.createElement(LayoutViewport, {
    layout: SAMPLE_LAYOUT, selection: null, onSelect() {}, initialState: state,
  })));
  const button = label => html.match(new RegExp(`<button(?=[^>]*aria-label="${label}")[^>]*>`))?.[0];
  assert.match(button("Pan"), /data-variant="default"/);
  assert.match(button("Show reference curves"), /aria-checked="false"/);
  assert.match(button("Show mechanical axis and start, center, and end frames"), /aria-checked="true"/);
  assert.doesNotMatch(button("Previous zoom view"), /\sdisabled=""/);
  assert.match(button("Next zoom view"), /\sdisabled=""/);
});

test("file drop handles nested targets, rejects multiple files, and leaves text drags alone", () => {
  const target = new EventTarget(), imported = [], active = [], errors = [];
  const remove = installLayoutFileDrop(target, f => imported.push(f), v => active.push(v), e => errors.push(e));
  const dispatch = (type, files = [], types = ["Files"]) => {
    const event = new Event(type, {cancelable: true});
    event.dataTransfer = {files, types};
    target.dispatchEvent(event);
    return event;
  };
  const file = new File(["{}"], "layout.JSON");
  assert.equal(dispatch("dragenter").defaultPrevented, true);
  dispatch("dragenter"); dispatch("dragleave");
  assert.equal(active.at(-1), true);
  assert.equal(dispatch("drop", [file]).defaultPrevented, true);
  assert.deepEqual(imported, [file]);
  assert.equal(active.at(-1), false);
  dispatch("drop", [file, file]);
  dispatch("drop", [new File(["text"], "notes.txt")]);
  assert.equal(errors.length, 2);
  assert.equal(imported.length, 1);
  assert.equal(dispatch("drop", [], ["text/plain"]).defaultPrevented, false);
  remove();
  assert.equal(dispatch("drop", [file]).defaultPrevented, false);
  assert.equal(imported.length, 1);
});
