import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
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

const {
  installPythonBridge,
  isPythonBridgeHandshake,
  parsePythonBridgeCommand,
  parsePythonBridgeConfig,
  PYTHON_BRIDGE_PROTOCOL,
  PYTHON_BRIDGE_SOURCE,
} = await vite.ssrLoadModule("/app/python-bridge.ts");

const nonce = "abcdefghijklmnopqrstuvwxyzABCDEFGH";
const origin = "http://127.0.0.1:43210";
const envelope = {
  source: PYTHON_BRIDGE_SOURCE,
  protocol: PYTHON_BRIDGE_PROTOCOL,
  type: "command",
};

test("parses only nonce-bearing bridge fragments with an exact HTTP origin", () => {
  assert.deepEqual(
    parsePythonBridgeConfig(
      `#python-bridge=${nonce}&python-origin=${encodeURIComponent(origin)}`,
    ),
    { nonce, parentOrigin: origin },
  );
  assert.equal(parsePythonBridgeConfig("#ordinary-anchor"), null);
  assert.equal(
    parsePythonBridgeConfig(
      `#python-bridge=short&python-origin=${encodeURIComponent(origin)}`,
    ),
    null,
  );
  assert.equal(
    parsePythonBridgeConfig(
      `#python-bridge=${nonce}&python-origin=${encodeURIComponent(`${origin}/path`)}`,
    ),
    null,
  );
  assert.equal(
    parsePythonBridgeConfig(
      `#python-bridge=${nonce}&python-origin=${encodeURIComponent(origin)}&extra=1`,
    ),
    null,
  );
});

test("requires matching origin, source window, nonce, and one transferred port", () => {
  const parent = {};
  const opener = {};
  const port = {};
  const config = { nonce, parentOrigin: origin };
  const event = {
    origin,
    source: parent,
    ports: [port],
    data: {
      source: PYTHON_BRIDGE_SOURCE,
      protocol: PYTHON_BRIDGE_PROTOCOL,
      type: "connect",
      nonce,
    },
  };

  assert.equal(isPythonBridgeHandshake(config, event, parent, opener), true);
  assert.equal(
    isPythonBridgeHandshake(config, { ...event, origin: "https://attacker.invalid" }, parent, opener),
    false,
  );
  assert.equal(
    isPythonBridgeHandshake(config, { ...event, source: {} }, parent, opener),
    false,
  );
  assert.equal(
    isPythonBridgeHandshake(config, { ...event, source: null }, null, null),
    false,
  );
  assert.equal(
    isPythonBridgeHandshake(
      config,
      { ...event, data: { ...event.data, nonce: `${nonce}x` } },
      parent,
      opener,
    ),
    false,
  );
  assert.equal(
    isPythonBridgeHandshake(config, { ...event, ports: [] }, parent, opener),
    false,
  );
});

test("validates the versioned command schema without DOM or URL escape hatches", () => {
  const layout = { curves: {}, objects: {} };
  assert.deepEqual(
    parsePythonBridgeCommand({
      ...envelope,
      id: "load-1",
      command: "set_layout",
      layout,
      scope: { kind: "curve", name: "ring" },
      visibility: { curves: true, objects: false, frames: false },
      selection: { kind: "curve", name: "ring" },
      fit: { kind: "curve", name: "ring" },
      mode: "pan",
      view: "+z",
    }),
    {
      id: "load-1",
      command: "set_layout",
      layout,
      scope: { kind: "curve", name: "ring" },
      visibility: { curves: true, objects: false, frames: false },
      selection: { kind: "curve", name: "ring" },
      fit: { kind: "curve", name: "ring" },
      mode: "pan",
      view: "+z",
    },
  );
  assert.deepEqual(
    parsePythonBridgeCommand({
      ...envelope,
      id: "1",
      command: "set_selection",
      selection: { kind: "frame", object: "Q1", name: "survey_mark" },
    }),
    {
      id: "1",
      command: "set_selection",
      selection: { kind: "frame", object: "Q1", name: "survey_mark" },
    },
  );
  assert.deepEqual(
    parsePythonBridgeCommand({
      ...envelope,
      id: "scope-1",
      command: "set_scope",
      scope: { kind: "object", name: "Q1" },
    }),
    {
      id: "scope-1",
      command: "set_scope",
      scope: { kind: "object", name: "Q1" },
    },
  );
  assert.deepEqual(
    parsePythonBridgeCommand({
      ...envelope,
      id: "2",
      command: "fit",
      target: { kind: "layout" },
    }),
    { id: "2", command: "fit", target: { kind: "layout" } },
  );
  assert.deepEqual(
    parsePythonBridgeCommand({
      ...envelope,
      id: "3",
      command: "set_visibility",
      visibility: { curves: false, frames: false, beam_frames: true },
    }),
    {
      id: "3",
      command: "set_visibility",
      visibility: { curves: false, frames: false, beam_frames: true },
    },
  );
  assert.deepEqual(
    parsePythonBridgeCommand({
      ...envelope,
      id: "4",
      command: "set_view",
      view: "-z",
    }),
    { id: "4", command: "set_view", view: "-z" },
  );

  assert.throws(
    () => parsePythonBridgeCommand({
      ...envelope,
      id: "5",
      command: "load_url",
      url: "https://attacker.invalid/layout.json",
    }),
    /Unsupported bridge command/,
  );
  assert.throws(
    () => parsePythonBridgeCommand({
      ...envelope,
      id: "6",
      command: "get_layout",
      selector: "body",
    }),
    /unsupported fields/,
  );
  assert.throws(
    () => parsePythonBridgeCommand({
      ...envelope,
      id: "7",
      command: "set_selection",
      selection: { kind: "curve", name: "ring", segmentIndex: -1 },
    }),
    /non-negative integer/,
  );
  assert.throws(
    () => parsePythonBridgeCommand({
      ...envelope,
      id: "load-2",
      command: "set_layout",
      layout,
      scope: { kind: "object" },
    }),
    /scope name must be a non-empty string/,
  );
  assert.throws(
    () => parsePythonBridgeCommand({
      ...envelope,
      id: "load-3",
      command: "set_layout",
      layout,
      visibility: { objects: "yes" },
    }),
    /visibility\.objects must be boolean/,
  );
});

test("scopes emitted geometry while resolving against the full layout", async () => {
  const { parseLayout, SAMPLE_LAYOUT } = await vite.ssrLoadModule(
    "/app/layout-data.ts",
  );
  const { buildScene } = await vite.ssrLoadModule("/app/layout-geometry.ts");
  const layout = parseLayout(structuredClone(SAMPLE_LAYOUT));
  const full = buildScene(layout);
  const detector = buildScene(layout, { kind: "object", name: "Detector" });
  const ring = buildScene(layout, { kind: "curve", name: "ring" });

  assert.deepEqual(detector.curves, []);
  assert.deepEqual(detector.objects.map((item) => item.name), ["Detector"]);
  assert.deepEqual(detector.frames.map((item) => item.object), ["Detector"]);
  assert.deepEqual(
    detector.objects[0].frame,
    full.objects.find((item) => item.name === "Detector").frame,
  );
  const detectorPoints = [
    ...detector.objects.flatMap((item) => item.vertices),
    ...detector.frames.map((item) => item.frame.o),
    ...detector.magneticFrames.flatMap((item) => item.vertices),
  ];
  assert.deepEqual(detector.bounds, {
    min: [0, 1, 2].map((axis) =>
      Math.min(...detectorPoints.map((point) => point[axis]))
    ),
    max: [0, 1, 2].map((axis) =>
      Math.max(...detectorPoints.map((point) => point[axis]))
    ),
  });
  assert.deepEqual(ring.curves.map((item) => item.name), ["ring"]);
  assert.deepEqual(ring.objects, []);
  assert.deepEqual(ring.frames, []);
  assert.deepEqual(ring.magneticFrames, []);
  assert.throws(
    () => buildScene(layout, { kind: "object", name: "toString" }),
    /Unknown object/,
  );
});

class FakePort {
  constructor() {
    this.messages = [];
    this.listeners = new Set();
    this.started = false;
    this.closed = false;
  }

  addEventListener(type, listener) {
    if (type === "message") this.listeners.add(listener);
  }

  removeEventListener(type, listener) {
    if (type === "message") this.listeners.delete(listener);
  }

  postMessage(message) {
    this.messages.push(message);
  }

  start() {
    this.started = true;
  }

  close() {
    this.closed = true;
  }

  dispatch(data) {
    for (const listener of this.listeners) listener({ data });
  }
}

class FakeWindow {
  constructor(hash) {
    this.location = { hash };
    this.parent = {};
    this.opener = null;
    this.listeners = new Set();
  }

  addEventListener(type, listener) {
    if (type === "message") this.listeners.add(listener);
  }

  removeEventListener(type, listener) {
    if (type === "message") this.listeners.delete(listener);
  }

  dispatch(event) {
    for (const listener of this.listeners) listener(event);
  }
}

test("connects an authenticated MessagePort and emits stable events and replies", async () => {
  const target = new FakeWindow(
    `#python-bridge=${nonce}&python-origin=${encodeURIComponent(origin)}`,
  );
  const port = new FakePort();
  const calls = [];
  const controller = installPythonBridge(target, () => ({
    getSelection: () => ({ kind: "object", name: "Q1" }),
    execute(command) {
      calls.push(command);
      if (command.command === "get_layout") return { layout: { objects: {} } };
    },
  }));
  assert.ok(controller);

  target.dispatch({
    origin,
    source: target.parent,
    ports: [port],
    data: {
      source: PYTHON_BRIDGE_SOURCE,
      protocol: PYTHON_BRIDGE_PROTOCOL,
      type: "connect",
      nonce,
    },
  });
  assert.equal(port.started, true);
  assert.deepEqual(port.messages, [
    {
      source: PYTHON_BRIDGE_SOURCE,
      protocol: PYTHON_BRIDGE_PROTOCOL,
      type: "event",
      event: "ready",
    },
    {
      source: PYTHON_BRIDGE_SOURCE,
      protocol: PYTHON_BRIDGE_PROTOCOL,
      type: "event",
      event: "selection",
      selection: { kind: "object", name: "Q1" },
    },
  ]);

  port.dispatch({ ...envelope, id: "8", command: "get_layout" });
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(calls, [{ id: "8", command: "get_layout" }]);
  assert.deepEqual(port.messages.at(-1), {
    source: PYTHON_BRIDGE_SOURCE,
    protocol: PYTHON_BRIDGE_PROTOCOL,
    type: "response",
    id: "8",
    ok: true,
    result: { layout: { objects: {} } },
  });

  controller.emitSelection(null);
  assert.deepEqual(port.messages.at(-1), {
    source: PYTHON_BRIDGE_SOURCE,
    protocol: PYTHON_BRIDGE_PROTOCOL,
    type: "event",
    event: "selection",
    selection: null,
  });
  controller.close();
  assert.equal(port.closed, true);
});

test("replaces an authenticated MessagePort and keeps rejecting invalid reconnects", async () => {
  const target = new FakeWindow(
    `#python-bridge=${nonce}&python-origin=${encodeURIComponent(origin)}`,
  );
  const firstPort = new FakePort();
  const secondPort = new FakePort();
  const rejectedPort = new FakePort();
  const calls = [];
  const controller = installPythonBridge(target, () => ({
    getSelection: () => null,
    execute(command) {
      calls.push(command);
      return "applied";
    },
  }));

  const connect = (port, overrides = {}) => target.dispatch({
    origin,
    source: target.parent,
    ports: [port],
    data: {
      source: PYTHON_BRIDGE_SOURCE,
      protocol: PYTHON_BRIDGE_PROTOCOL,
      type: "connect",
      nonce,
    },
    ...overrides,
  });

  connect(firstPort);
  assert.equal(target.listeners.size, 1);
  assert.equal(firstPort.started, true);

  connect(rejectedPort, { origin: "https://attacker.invalid" });
  assert.equal(firstPort.closed, false);
  assert.equal(rejectedPort.started, false);

  connect(secondPort);
  assert.equal(firstPort.closed, true);
  assert.equal(firstPort.listeners.size, 0);
  assert.equal(secondPort.started, true);
  assert.deepEqual(secondPort.messages.map((message) => message.event), [
    "ready",
    "selection",
  ]);

  firstPort.dispatch({ ...envelope, id: "old", command: "get_layout" });
  secondPort.dispatch({ ...envelope, id: "new", command: "get_layout" });
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(calls, [{ id: "new", command: "get_layout" }]);
  assert.deepEqual(secondPort.messages.at(-1), {
    source: PYTHON_BRIDGE_SOURCE,
    protocol: PYTHON_BRIDGE_PROTOCOL,
    type: "response",
    id: "new",
    ok: true,
    result: "applied",
  });

  controller.close();
  assert.equal(secondPort.closed, true);
  assert.equal(target.listeners.size, 0);
});

test("awaits asynchronous command application before acknowledging it", async () => {
  const target = new FakeWindow(
    `#python-bridge=${nonce}&python-origin=${encodeURIComponent(origin)}`,
  );
  const port = new FakePort();
  let resolveExecution;
  const controller = installPythonBridge(target, () => ({
    getSelection: () => null,
    execute() {
      return new Promise((resolve) => {
        resolveExecution = resolve;
      });
    },
  }));

  target.dispatch({
    origin,
    source: target.parent,
    ports: [port],
    data: {
      source: PYTHON_BRIDGE_SOURCE,
      protocol: PYTHON_BRIDGE_PROTOCOL,
      type: "connect",
      nonce,
    },
  });
  port.dispatch({ ...envelope, id: "async-1", command: "get_layout" });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(
    port.messages.some((message) => message.type === "response"),
    false,
  );

  resolveExecution({ applied: true });
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(port.messages.at(-1), {
    source: PYTHON_BRIDGE_SOURCE,
    protocol: PYTHON_BRIDGE_PROTOCOL,
    type: "response",
    id: "async-1",
    ok: true,
    result: { applied: true },
  });

  port.dispatch({ ...envelope, id: "async-2", command: "get_layout" });
  await new Promise((resolve) => setImmediate(resolve));
  const replacementPort = new FakePort();
  target.dispatch({
    origin,
    source: target.parent,
    ports: [replacementPort],
    data: {
      source: PYTHON_BRIDGE_SOURCE,
      protocol: PYTHON_BRIDGE_PROTOCOL,
      type: "connect",
      nonce,
    },
  });
  resolveExecution({ stale: true });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(
    replacementPort.messages.some((message) => message.type === "response"),
    false,
  );

  controller.close();
  assert.equal(replacementPort.closed, true);
});

test("keeps normal SSR output and drives React state without DOM automation", async () => {
  const { default: Home } = await vite.ssrLoadModule("/app/page.tsx");
  const html = renderToStaticMarkup(React.createElement(Home));
  const [pageSource, bridgeSource, geometrySource, viewportSource] =
    await Promise.all([
      readFile(path.join(root, "app/page.tsx"), "utf8"),
      readFile(path.join(root, "app/python-bridge.ts"), "utf8"),
      readFile(path.join(root, "app/layout-geometry.ts"), "utf8"),
      readFile(path.join(root, "app/layout-viewport.tsx"), "utf8"),
    ]);

  assert.match(html, /Interactive three-dimensional layout view/);
  assert.match(html, /aria-label="Show named frames"/);
  assert.doesNotMatch(html, /python-bridge|layout-studio-python/);
  assert.match(pageSource, /installPythonBridge\(window/);
  assert.match(pageSource, /viewportCommandQueueRef/);
  assert.match(pageSource, /return new Promise<void>/);
  assert.match(pageSource, /command=\{viewportCommand\}/);
  assert.match(
    pageSource,
    /onCommandApplied=\{handleViewportCommandApplied\}/,
  );
  assert.match(pageSource, /preserveViewport: true/);
  assert.match(pageSource, /validateScope\(layoutRef\.current, command\.scope\)/);
  assert.match(
    pageSource,
    /case "set_scope":[\s\S]*?return issueViewportCommand\(\{[\s\S]*?command: "set_visibility"/,
  );
  assert.match(
    pageSource,
    /case "set_selection":[\s\S]*?return issueViewportCommand\(\{[\s\S]*?command: "set_visibility"/,
  );
  assert.doesNotMatch(bridgeSource, /querySelector|\.click\(|load_url|fetch\(/);
  assert.doesNotMatch(geometrySource, /const positions: Vec3\[\]/);
  assert.match(viewportSource, /const objectProjections =/);
  assert.match(viewportSource, /if \(hasVisibleEdge\) context\.stroke\(\)/);
});
