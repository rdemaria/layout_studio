import type { SelectedEntity } from "./layout-data";

export const PYTHON_BRIDGE_SOURCE = "layout-studio-python" as const;
export const PYTHON_BRIDGE_PROTOCOL = 1 as const;

export type PythonBridgeConfig = {
  nonce: string;
  parentOrigin: string;
};

export type PythonBridgeScope =
  | { kind: "layout" }
  | { kind: "curve" | "object"; name: string };

export type PythonBridgeVisibility = {
  curves?: boolean;
  objects?: boolean;
  frames?: boolean;
  beam_frames?: boolean;
};

export type PythonBridgeCommand =
  | {
      id: string;
      command: "set_layout";
      layout: unknown;
      scope?: PythonBridgeScope;
      visibility?: PythonBridgeVisibility;
      selection?: SelectedEntity;
      fit?: { kind: "layout" } | { kind: "curve" | "object"; name: string };
      mode?: "orbit" | "pan" | "select" | "zoom-region";
      view?: "+x" | "-x" | "+y" | "-y" | "+z" | "-z";
    }
  | {
      id: string;
      command: "get_layout";
    }
  | {
      id: string;
      command: "set_selection";
      selection: SelectedEntity;
    }
  | {
      id: string;
      command: "fit";
      target:
        | { kind: "layout" }
        | { kind: "curve" | "object"; name: string };
    }
  | {
      id: string;
      command: "set_mode";
      mode: "orbit" | "pan" | "select" | "zoom-region";
    }
  | {
      id: string;
      command: "set_view";
      view: "+x" | "-x" | "+y" | "-y" | "+z" | "-z";
    }
  | {
      id: string;
      command: "set_scope";
      scope: PythonBridgeScope;
    }
  | {
      id: string;
      command: "set_visibility";
      visibility: PythonBridgeVisibility;
    };

export type PythonBridgeHandlers = {
  execute: (command: PythonBridgeCommand) => unknown | Promise<unknown>;
  getSelection: () => SelectedEntity;
};

export type PythonBridgeController = {
  close: () => void;
  emitSelection: (selection: SelectedEntity) => void;
};

type BridgeEnvelope = {
  source: typeof PYTHON_BRIDGE_SOURCE;
  protocol: typeof PYTHON_BRIDGE_PROTOCOL;
};

type HandshakeEvent = Pick<
  MessageEvent,
  "data" | "origin" | "ports" | "source"
>;

const NONCE_PATTERN = /^[A-Za-z0-9_-]{22,128}$/;
const COMMAND_ID_PATTERN = /^[A-Za-z0-9._:-]{1,128}$/;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasOnlyKeys(
  value: Record<string, unknown>,
  allowed: readonly string[],
): boolean {
  return Object.keys(value).every((key) => allowed.includes(key));
}

function requireOnlyKeys(
  value: Record<string, unknown>,
  allowed: readonly string[],
  label: string,
) {
  const unsupported = Object.keys(value).filter(
    (key) => !allowed.includes(key),
  );
  if (unsupported.length) {
    throw new Error(
      `${label} contains unsupported fields: ${unsupported.join(", ")}`,
    );
  }
}

function requireName(value: unknown, label: string): string {
  if (typeof value !== "string" || !value || value.length > 1024) {
    throw new Error(`${label} must be a non-empty string`);
  }
  return value;
}

function parseSelection(value: unknown): SelectedEntity {
  if (value === null) return null;
  if (!isRecord(value) || typeof value.kind !== "string") {
    throw new Error("selection must be null or a selected entity");
  }
  if (value.kind === "curve") {
    requireOnlyKeys(value, ["kind", "name", "segmentIndex"], "curve selection");
    const segmentIndex = value.segmentIndex;
    if (
      segmentIndex !== undefined &&
      (!Number.isSafeInteger(segmentIndex) || Number(segmentIndex) < 0)
    ) {
      throw new Error("curve selection segmentIndex must be a non-negative integer");
    }
    return {
      kind: "curve",
      name: requireName(value.name, "curve selection name"),
      ...(segmentIndex === undefined
        ? {}
        : { segmentIndex: Number(segmentIndex) }),
    };
  }
  if (value.kind === "object") {
    requireOnlyKeys(value, ["kind", "name"], "object selection");
    return {
      kind: "object",
      name: requireName(value.name, "object selection name"),
    };
  }
  if (value.kind === "frame") {
    requireOnlyKeys(value, ["kind", "object", "name"], "frame selection");
    return {
      kind: "frame",
      object: requireName(value.object, "frame selection object"),
      name: requireName(value.name, "frame selection name"),
    };
  }
  throw new Error(`Unsupported selection kind: ${value.kind}`);
}

function parseFitTarget(value: unknown): Extract<
  PythonBridgeCommand,
  { command: "fit" }
>["target"] {
  if (!isRecord(value) || typeof value.kind !== "string") {
    throw new Error("fit target must be an entity descriptor");
  }
  if (value.kind === "layout") {
    requireOnlyKeys(value, ["kind"], "layout fit target");
    return { kind: "layout" };
  }
  if (value.kind === "curve" || value.kind === "object") {
    requireOnlyKeys(value, ["kind", "name"], `${value.kind} fit target`);
    return {
      kind: value.kind,
      name: requireName(value.name, `${value.kind} fit target name`),
    };
  }
  throw new Error(`Unsupported fit target kind: ${value.kind}`);
}

function parseVisibility(value: unknown): Extract<
  PythonBridgeCommand,
  { command: "set_visibility" }
>["visibility"] {
  if (!isRecord(value)) {
    throw new Error("visibility must be an object");
  }
  requireOnlyKeys(
    value,
    ["curves", "objects", "frames", "beam_frames"],
    "visibility",
  );
  if (!Object.keys(value).length) {
    throw new Error("visibility must set at least one layer");
  }
  const visibility: PythonBridgeVisibility = {};
  for (const key of ["curves", "objects", "frames", "beam_frames"] as const) {
    if (value[key] === undefined) continue;
    if (typeof value[key] !== "boolean") {
      throw new Error(`visibility.${key} must be boolean`);
    }
    visibility[key] = value[key];
  }
  return visibility;
}

function parseScope(value: unknown): PythonBridgeScope {
  if (!isRecord(value) || typeof value.kind !== "string") {
    throw new Error("scope must be an entity descriptor");
  }
  if (value.kind === "layout") {
    requireOnlyKeys(value, ["kind"], "layout scope");
    return { kind: "layout" };
  }
  if (value.kind === "curve" || value.kind === "object") {
    requireOnlyKeys(value, ["kind", "name"], `${value.kind} scope`);
    return {
      kind: value.kind,
      name: requireName(value.name, `${value.kind} scope name`),
    };
  }
  throw new Error(`Unsupported scope kind: ${value.kind}`);
}

export function parsePythonBridgeConfig(hash: string): PythonBridgeConfig | null {
  const raw = hash.startsWith("#") ? hash.slice(1) : hash;
  if (!raw) return null;
  const parameters = new URLSearchParams(raw);
  const nonceValues = parameters.getAll("python-bridge");
  const originValues = parameters.getAll("python-origin");
  if (!nonceValues.length && !originValues.length) return null;
  if (
    nonceValues.length !== 1 ||
    originValues.length !== 1 ||
    [...parameters.keys()].some(
      (key) => key !== "python-bridge" && key !== "python-origin",
    )
  ) {
    return null;
  }
  const nonce = nonceValues[0];
  if (!NONCE_PATTERN.test(nonce)) return null;
  try {
    const parsedOrigin = new URL(originValues[0]);
    if (
      (parsedOrigin.protocol !== "http:" && parsedOrigin.protocol !== "https:") ||
      parsedOrigin.origin !== originValues[0]
    ) {
      return null;
    }
    return { nonce, parentOrigin: parsedOrigin.origin };
  } catch {
    return null;
  }
}

export function isPythonBridgeHandshake(
  config: PythonBridgeConfig,
  event: HandshakeEvent,
  parent: MessageEventSource | null,
  opener: MessageEventSource | null,
): boolean {
  const hasAllowedSource =
    (parent !== null && event.source === parent) ||
    (opener !== null && event.source === opener);
  if (
    event.origin !== config.parentOrigin ||
    !hasAllowedSource ||
    event.ports.length !== 1 ||
    !isRecord(event.data)
  ) {
    return false;
  }
  return (
    hasOnlyKeys(event.data, ["source", "protocol", "type", "nonce"]) &&
    event.data.source === PYTHON_BRIDGE_SOURCE &&
    event.data.protocol === PYTHON_BRIDGE_PROTOCOL &&
    event.data.type === "connect" &&
    event.data.nonce === config.nonce
  );
}

export function parsePythonBridgeCommand(value: unknown): PythonBridgeCommand {
  if (!isRecord(value)) throw new Error("Bridge command must be an object");
  if (
    value.source !== PYTHON_BRIDGE_SOURCE ||
    value.protocol !== PYTHON_BRIDGE_PROTOCOL ||
    value.type !== "command"
  ) {
    throw new Error("Invalid bridge command envelope");
  }
  if (typeof value.id !== "string" || !COMMAND_ID_PATTERN.test(value.id)) {
    throw new Error("Bridge command id is invalid");
  }
  if (typeof value.command !== "string") {
    throw new Error("Bridge command name is missing");
  }
  const envelopeKeys = ["source", "protocol", "type", "id", "command"];
  const id = value.id;
  switch (value.command) {
    case "set_layout":
      requireOnlyKeys(
        value,
        [
          ...envelopeKeys,
          "layout",
          "scope",
          "visibility",
          "selection",
          "fit",
          "mode",
          "view",
        ],
        "set_layout command",
      );
      if (!Object.hasOwn(value, "layout") || !isRecord(value.layout)) {
        throw new Error("set_layout requires a layout object");
      }
      if (
        Object.hasOwn(value, "mode") &&
        value.mode !== "orbit" &&
        value.mode !== "pan" &&
        value.mode !== "select" &&
        value.mode !== "zoom-region"
      ) {
        throw new Error("mode must be orbit, pan, select, or zoom-region");
      }
      if (
        Object.hasOwn(value, "view") &&
        value.view !== "+x" &&
        value.view !== "-x" &&
        value.view !== "+y" &&
        value.view !== "-y" &&
        value.view !== "+z" &&
        value.view !== "-z"
      ) {
        throw new Error("view must be one of +x, -x, +y, -y, +z, -z");
      }
      return {
        id,
        command: "set_layout",
        layout: value.layout,
        ...(Object.hasOwn(value, "scope")
          ? { scope: parseScope(value.scope) }
          : {}),
        ...(Object.hasOwn(value, "visibility")
          ? { visibility: parseVisibility(value.visibility) }
          : {}),
        ...(Object.hasOwn(value, "selection")
          ? { selection: parseSelection(value.selection) }
          : {}),
        ...(Object.hasOwn(value, "fit")
          ? { fit: parseFitTarget(value.fit) }
          : {}),
        ...(Object.hasOwn(value, "mode")
          ? { mode: value.mode }
          : {}),
        ...(Object.hasOwn(value, "view")
          ? { view: value.view }
          : {}),
      };
    case "get_layout":
      requireOnlyKeys(value, envelopeKeys, "get_layout command");
      return { id, command: "get_layout" };
    case "set_selection":
      requireOnlyKeys(
        value,
        [...envelopeKeys, "selection"],
        "set_selection command",
      );
      if (!Object.hasOwn(value, "selection")) {
        throw new Error("set_selection requires selection");
      }
      return {
        id,
        command: "set_selection",
        selection: parseSelection(value.selection),
      };
    case "fit":
      requireOnlyKeys(value, [...envelopeKeys, "target"], "fit command");
      return { id, command: "fit", target: parseFitTarget(value.target) };
    case "set_mode":
      requireOnlyKeys(value, [...envelopeKeys, "mode"], "set_mode command");
      if (
        value.mode !== "orbit" &&
        value.mode !== "pan" &&
        value.mode !== "select" &&
        value.mode !== "zoom-region"
      ) {
        throw new Error("mode must be orbit, pan, select, or zoom-region");
      }
      return { id, command: "set_mode", mode: value.mode };
    case "set_view":
      requireOnlyKeys(value, [...envelopeKeys, "view"], "set_view command");
      if (
        value.view !== "+x" &&
        value.view !== "-x" &&
        value.view !== "+y" &&
        value.view !== "-y" &&
        value.view !== "+z" &&
        value.view !== "-z"
      ) {
        throw new Error("view must be one of +x, -x, +y, -y, +z, or -z");
      }
      return { id, command: "set_view", view: value.view };
    case "set_scope":
      requireOnlyKeys(value, [...envelopeKeys, "scope"], "set_scope command");
      return { id, command: "set_scope", scope: parseScope(value.scope) };
    case "set_visibility":
      requireOnlyKeys(
        value,
        [...envelopeKeys, "visibility"],
        "set_visibility command",
      );
      return {
        id,
        command: "set_visibility",
        visibility: parseVisibility(value.visibility),
      };
    default:
      throw new Error(`Unsupported bridge command: ${value.command}`);
  }
}

function envelope(): BridgeEnvelope {
  return {
    source: PYTHON_BRIDGE_SOURCE,
    protocol: PYTHON_BRIDGE_PROTOCOL,
  };
}

export function installPythonBridge(
  targetWindow: Window,
  handlers: () => PythonBridgeHandlers,
): PythonBridgeController | null {
  const config = parsePythonBridgeConfig(targetWindow.location.hash);
  if (!config) return null;

  let port: MessagePort | null = null;
  let portListener: ((event: MessageEvent) => void) | null = null;
  let closed = false;

  const postEvent = (event: "ready" | "selection", detail = {}) => {
    port?.postMessage({ ...envelope(), type: "event", event, ...detail });
  };

  const onPortMessage = async (
    sourcePort: MessagePort,
    event: MessageEvent,
  ) => {
    let id: string | null = null;
    try {
      if (isRecord(event.data) && typeof event.data.id === "string") {
        id = event.data.id;
      }
      const command = parsePythonBridgeCommand(event.data);
      id = command.id;
      const result = await handlers().execute(command);
      if (!closed && sourcePort === port) {
        sourcePort.postMessage({
          ...envelope(),
          type: "response",
          id,
          ok: true,
          ...(result === undefined ? {} : { result }),
        });
      }
    } catch (error) {
      if (
        !closed &&
        sourcePort === port &&
        id &&
        COMMAND_ID_PATTERN.test(id)
      ) {
        sourcePort.postMessage({
          ...envelope(),
          type: "response",
          id,
          ok: false,
          error: error instanceof Error ? error.message : "Bridge command failed",
        });
      }
    }
  };

  const disconnectPort = () => {
    if (!port) return;
    if (portListener) port.removeEventListener("message", portListener);
    port.close();
    port = null;
    portListener = null;
  };

  const onWindowMessage = (event: MessageEvent) => {
    if (
      closed ||
      !isPythonBridgeHandshake(
        config,
        event,
        targetWindow.parent === targetWindow ? null : targetWindow.parent,
        targetWindow.opener,
      )
    ) {
      return;
    }
    disconnectPort();
    const nextPort = event.ports[0];
    const nextPortListener = (portEvent: MessageEvent) => {
      void onPortMessage(nextPort, portEvent);
    };
    port = nextPort;
    portListener = nextPortListener;
    nextPort.addEventListener("message", nextPortListener);
    nextPort.start();
    postEvent("ready");
    postEvent("selection", { selection: handlers().getSelection() });
  };

  targetWindow.addEventListener("message", onWindowMessage);

  return {
    close() {
      if (closed) return;
      closed = true;
      targetWindow.removeEventListener("message", onWindowMessage);
      disconnectPort();
    },
    emitSelection(selection) {
      if (!closed) postEvent("selection", { selection });
    },
  };
}
