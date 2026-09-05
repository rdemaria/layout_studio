"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  Box as BoxIcon,
  ChevronDown,
  Circle,
  CircleHelp,
  Download,
  FileJson,
  Focus,
  Link,
  Plus,
  Shapes,
  Trash2,
  Upload,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  NativeSelect,
  NativeSelectOption,
} from "@/components/ui/native-select";
import { TooltipProvider } from "@/components/ui/tooltip";
import {
  Field,
  NamePicker,
  NumberInput,
  OperationsEditor,
  ReferenceEditor,
} from "./layout-controls";
import {
  createEmptyLayout,
  forEachTransformation,
  isImplicitTypeFrameName,
  LOCAL_TRANSFORM_NAMES,
  parseLayout,
  SAMPLE_LAYOUT,
  shapePath,
  objectFrameNames,
  effectiveBeamFeature,
  uniqueName,
  type BoxShape,
  type CylinderShape,
  type LayoutData,
  type Reference,
  type SelectedEntity,
} from "./layout-data";
import { DependencyTree } from "./dependency-tree";
import {
  LayoutViewport,
  toggleViewerSelection,
  type ViewportCommand,
  type ViewportFitRequest,
} from "./layout-viewport";
import type { SceneScope } from "./layout-geometry";
import {
  installPythonBridge,
  type PythonBridgeCommand,
  type PythonBridgeController,
  type PythonBridgeHandlers,
} from "./python-bridge";
import {
  layoutCatalogUrl,
  parseLayoutUrlList,
  resolveLayoutUrl,
  type LayoutUrlSuggestion,
} from "./layout-url-catalog";

type Status = {
  kind: "idle" | "loading" | "success" | "error";
  message: string;
};

const LARGE_SEGMENT_COUNT = 200;
const LARGE_FRAME_COUNT = 200;

type ViewportCommandBody = ViewportCommand extends infer Command
  ? Command extends ViewportCommand
    ? Omit<Command, "id">
    : never
  : never;

type PendingViewportCommand = {
  command: ViewportCommand;
  resolve: () => void;
  reject: (error: Error) => void;
};

type LoadValueOptions = {
  preserveViewport?: boolean;
  scope?: SceneScope;
};

function sameSelection(a: SelectedEntity, b: SelectedEntity): boolean {
  if (!a || !b) return a === b;
  if (a.kind !== b.kind) return false;
  if (a.kind === "frame" && b.kind === "frame") {
    return a.object === b.object && a.name === b.name;
  }
  if (a.kind === "curve" && b.kind === "curve") {
    return a.name === b.name && a.segmentIndex === b.segmentIndex;
  }
  return a.kind === "object" && b.kind === "object" && a.name === b.name;
}

function sameScope(a: SceneScope, b: SceneScope): boolean {
  return a.kind === b.kind &&
    (a.kind === "layout" || (b.kind !== "layout" && a.name === b.name));
}

function selectionIsInScope(
  selection: SelectedEntity,
  scope: SceneScope,
): boolean {
  if (!selection || scope.kind === "layout") return true;
  if (scope.kind === "curve") {
    return selection.kind === "curve" && selection.name === scope.name;
  }
  return selection.kind === "object"
    ? selection.name === scope.name
    : selection.kind === "frame" && selection.object === scope.name;
}

function validateScope(layout: LayoutData, scope: SceneScope): void {
  if (
    scope.kind === "curve" &&
    !Object.hasOwn(layout.reference_curves, scope.name)
  ) {
    throw new Error(`Unknown reference curve: ${scope.name}`);
  }
  if (scope.kind === "object" && !Object.hasOwn(layout.objects, scope.name)) {
    throw new Error(`Unknown object: ${scope.name}`);
  }
}

function selectionExistsInLayout(
  selection: SelectedEntity,
  layout: LayoutData,
): boolean {
  if (!selection) return true;
  if (selection.kind === "curve") {
    const curve = layout.reference_curves[selection.name];
    return Boolean(
      curve &&
      (selection.segmentIndex === undefined ||
        selection.segmentIndex < curve.segments.length),
    );
  }
  const objectName =
    selection.kind === "object" ? selection.name : selection.object;
  const object = layout.objects[objectName];
  if (!object) return false;
  if (selection.kind === "object") return true;
  const type = layout.types[object.type];
  return Boolean(type && objectFrameNames(type, object).includes(selection.name));
}

function fitTargetIsInScope(
  target: Extract<PythonBridgeCommand, { command: "fit" }>["target"],
  scope: SceneScope,
): boolean {
  if (target.kind === "layout" || scope.kind === "layout") return true;
  return target.kind === scope.kind && target.name === scope.name;
}

type LayoutUrlPickerProps = {
  suggestions: LayoutUrlSuggestion[];
  onSelect: (path: string) => void;
};

export function LayoutUrlPicker({
  suggestions,
  onSelect,
}: LayoutUrlPickerProps) {
  return (
    <NativeSelect
      aria-label="Available layout JSON files"
      className="url-suggestion-select"
      size="sm"
      value=""
      disabled={suggestions.length === 0}
      onChange={(event) => {
        if (event.target.value) onSelect(event.target.value);
      }}
    >
      <NativeSelectOption value="">
        {suggestions.length > 0 ? "Available JSON…" : "No JSON catalog"}
      </NativeSelectOption>
      {suggestions.map((suggestion) => (
        <NativeSelectOption key={suggestion.href} value={suggestion.path}>
          {suggestion.label
            ? `${suggestion.label} — ${suggestion.path}`
            : suggestion.path}
        </NativeSelectOption>
      ))}
    </NativeSelect>
  );
}

export default function Home() {
  const [layout, setLayout] = useState<LayoutData>(() =>
    structuredClone(SAMPLE_LAYOUT),
  );
  const [selectedCurve, setSelectedCurve] = useState("ring");
  const [selectedType, setSelectedType] = useState("quadrupole");
  const [selectedObject, setSelectedObject] = useState("QF1");
  const [selectedTypeFrame, setSelectedTypeFrame] = useState("survey_mark");
  const [curvesCardOpen, setCurvesCardOpen] = useState(true);
  const [typesCardOpen, setTypesCardOpen] = useState(true);
  const [objectsCardOpen, setObjectsCardOpen] = useState(true);
  const [viewerCardOpen, setViewerCardOpen] = useState(true);
  const [dependenciesCardOpen, setDependenciesCardOpen] = useState(true);
  const [segmentsOpen, setSegmentsOpen] = useState(true);
  const [typeFramesOpen, setTypeFramesOpen] = useState(true);
  const [viewerRevision, setViewerRevision] = useState(0);
  const [viewportFitRequest, setViewportFitRequest] =
    useState<ViewportFitRequest | null>(null);
  const [viewportCommand, setViewportCommand] =
    useState<ViewportCommand | null>(null);
  const [viewportScope, setViewportScope] =
    useState<SceneScope>({ kind: "layout" });
  const [selection, setSelection] = useState<SelectedEntity>({
    kind: "object",
    name: "QF1",
  });
  const [url, setUrl] = useState("");
  const [urlSuggestions, setUrlSuggestions] = useState<
    LayoutUrlSuggestion[]
  >([]);
  const [status, setStatus] = useState<Status>({
    kind: "idle",
    message: "Ready",
  });
  const fileInputRef = useRef<HTMLInputElement>(null);
  const viewportFitIdRef = useRef(0);
  const viewportCommandIdRef = useRef(0);
  const viewportCommandQueueRef = useRef<PendingViewportCommand[]>([]);
  const viewportScopeRef = useRef<SceneScope>(viewportScope);
  const layoutRef = useRef(layout);
  const pythonBridgeRef = useRef<PythonBridgeController | null>(null);
  const pythonBridgeHandlersRef = useRef<PythonBridgeHandlers | null>(null);

  useEffect(() => {
    if (window.location.protocol !== "http:" && window.location.protocol !== "https:") {
      return;
    }

    const controller = new AbortController();
    const catalogUrl = layoutCatalogUrl(document.baseURI);

    void (async () => {
      try {
        const response = await fetch(catalogUrl, {
          cache: "no-cache",
          credentials: "same-origin",
          signal: controller.signal,
        });
        if (!response.ok) return;
        setUrlSuggestions(
          parseLayoutUrlList(await response.json(), catalogUrl),
        );
      } catch {
        // The catalog is optional; free-form URLs remain available without it.
      }
    })();

    return () => controller.abort();
  }, []);

  const update = (mutate: (draft: LayoutData) => void) => {
    const draft = structuredClone(layoutRef.current);
    mutate(draft);
    layoutRef.current = draft;
    setLayout(draft);
    setStatus({ kind: "idle", message: "Edited locally" });
  };

  const updateValidated = (mutate: (draft: LayoutData) => void) => {
    const draft = structuredClone(layoutRef.current);
    mutate(draft);
    try {
      const parsed = parseLayout(draft);
      layoutRef.current = parsed;
      setLayout(parsed);
      setStatus({ kind: "idle", message: "Edited locally" });
    } catch (error) {
      setStatus({
        kind: "error",
        message: error instanceof Error ? error.message : "Invalid layout edit",
      });
    }
  };

  const loadValue = (
    value: unknown,
    source: string,
    options: LoadValueOptions = {},
  ) => {
    const parsed = parseLayout(value);
    const preserveViewport = options.preserveViewport ?? false;
    const nextScope =
      options.scope ??
      (preserveViewport
        ? viewportScopeRef.current
        : { kind: "layout" as const });
    validateScope(parsed, nextScope);

    layoutRef.current = parsed;
    viewportScopeRef.current = nextScope;
    const firstCurve = Object.keys(parsed.reference_curves)[0] ?? "";
    const firstType = Object.keys(parsed.types)[0] ?? "";
    const firstObject = Object.keys(parsed.objects)[0] ?? "";
    const activeType = parsed.objects[firstObject]?.type ?? firstType;
    const firstFrame = Object.keys(parsed.types[activeType]?.frames ?? {})[0] ?? "";
    setLayout(parsed);
    setSelectedCurve(firstCurve);
    setSelectedType(activeType);
    setSelectedObject(firstObject);
    setSelectedTypeFrame(firstFrame);
    setSegmentsOpen(
      (parsed.reference_curves[firstCurve]?.segments.length ?? 0) <=
        LARGE_SEGMENT_COUNT,
    );
    setTypeFramesOpen(
      Object.keys(parsed.types[activeType]?.frames ?? {}).length <=
        LARGE_FRAME_COUNT,
    );
    setViewportFitRequest(null);
    setViewportScope((current) =>
      sameScope(current, nextScope) ? current : nextScope,
    );
    if (preserveViewport) {
      setSelection((current) =>
        selectionExistsInLayout(current, parsed) &&
        selectionIsInScope(current, nextScope)
          ? current
          : null,
      );
    } else {
      setViewerRevision((current) => current + 1);
      setSelection(null);
    }
    setStatus({ kind: "success", message: `Loaded ${source}` });
  };

  const importUrl = async () => {
    if (!url.trim()) return;
    setStatus({ kind: "loading", message: "Loading URL…" });
    try {
      const catalogUrl = layoutCatalogUrl(document.baseURI);
      const response = await fetch(resolveLayoutUrl(url, catalogUrl));
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      loadValue(await response.json(), "URL");
    } catch (error) {
      setStatus({
        kind: "error",
        message:
          error instanceof Error
            ? `Could not load URL: ${error.message}`
            : "Could not load URL",
      });
    }
  };

  const importFile = async (file: File | undefined) => {
    if (!file) return;
    setStatus({ kind: "loading", message: `Reading ${file.name}…` });
    try {
      loadValue(JSON.parse(await file.text()), file.name);
    } catch (error) {
      setStatus({
        kind: "error",
        message: error instanceof Error ? error.message : "Invalid JSON file",
      });
    } finally {
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  const downloadLayout = () => {
    const blob = new Blob([`${JSON.stringify(layout, null, 2)}\n`], {
      type: "application/json",
    });
    const href = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = href;
    link.download = "layout.json";
    link.click();
    URL.revokeObjectURL(href);
    setStatus({ kind: "success", message: "Downloaded layout.json" });
  };

  const clearLayout = () => {
    const fullLayoutScope: SceneScope = { kind: "layout" };
    viewportScopeRef.current = fullLayoutScope;
    setViewportScope(fullLayoutScope);
    layoutRef.current = createEmptyLayout();
    setLayout(createEmptyLayout());
    setSelectedCurve("");
    setSelectedType("");
    setSelectedObject("");
    setSelectedTypeFrame("");
    setSegmentsOpen(true);
    setTypeFramesOpen(true);
    setViewportFitRequest(null);
    setViewerRevision((current) => current + 1);
    setSelection(null);
    setUrl("");
    if (fileInputRef.current) fileInputRef.current.value = "";
    setStatus({ kind: "success", message: "Started an empty layout" });
  };

  const selectCurve = (name: string) => {
    if (!layout.reference_curves[name]) return;
    setSelectedCurve(name);
    setSelection({ kind: "curve", name });
  };

  const selectType = (name: string) => {
    const nextType = layout.types[name];
    if (!nextType) return;
    const nextFrameNames = Object.keys(nextType.frames);
    setSelectedType(name);
    setSelectedTypeFrame((current) =>
      current && nextFrameNames.includes(current)
        ? current
        : nextFrameNames[0] ?? "",
    );
  };

  const selectObject = (name: string) => {
    const nextObject = layout.objects[name];
    if (!nextObject) return;
    const nextType = layout.types[nextObject.type];
    const nextFrameNames = Object.keys(nextType?.frames ?? {});
    setSelectedObject(name);
    setSelectedType(nextObject.type);
    setSelectedTypeFrame((current) =>
      current && nextFrameNames.includes(current)
        ? current
        : nextFrameNames[0] ?? "",
    );
    setSelection({ kind: "object", name });
  };

  const fitEntityInViewport = (
    kind: ViewportFitRequest["kind"],
    name: string,
  ) => {
    const target = { kind, name } as const;
    if (!fitTargetIsInScope(target, viewportScopeRef.current)) {
      setStatus({
        kind: "error",
        message: `${kind === "curve" ? "Curve" : "Object"} ${name} is outside the current viewport scope`,
      });
      return;
    }
    viewportFitIdRef.current += 1;
    setViewerCardOpen(true);
    setSelection(target);
    setViewportFitRequest({ id: viewportFitIdRef.current, kind, name });
  };

  const selectFrame = (objectName: string, frameName: string) => {
    const selected = layout.objects[objectName];
    const objectType = selected?.type;
    if (
      !objectType ||
      !objectFrameNames(layout.types[objectType], selected).includes(frameName)
    ) return;
    setSelectedObject(objectName);
    setSelectedType(objectType);
    if (!isImplicitTypeFrameName(frameName)) {
      setSelectedTypeFrame(frameName);
      setTypeFramesOpen(true);
    }
    setSelection({ kind: "frame", object: objectName, name: frameName });
  };

  const selectTypeFrame = (frameName: string) => {
    if (!frameNames.includes(frameName)) return;
    setSelectedTypeFrame(frameName);
    if (layout.objects[selectedObject]?.type === selectedType) {
      setSelection({ kind: "frame", object: selectedObject, name: frameName });
    }
  };

  const selectFromViewport = (next: SelectedEntity) => {
    const toggled = toggleViewerSelection(selection, next);
    if (toggled?.kind === "curve") {
      setCurvesCardOpen(true);
      setSegmentsOpen(true);
      setSelectedCurve(toggled.name);
      setSelection(toggled);
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          const segmentRow = toggled.segmentIndex === undefined
            ? null
            : document.getElementById(
                `curve-segment-row-${toggled.segmentIndex}`,
              );
          const target = segmentRow ?? document.getElementById("curves-card");
          segmentRow?.focus({ preventScroll: true });
          target?.scrollIntoView({ behavior: "smooth", block: "nearest" });
        });
      });
    } else if (toggled?.kind === "object") {
      setObjectsCardOpen(true);
      selectObject(toggled.name);
      requestAnimationFrame(() => {
        document
          .getElementById("objects-card")
          ?.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    } else if (toggled?.kind === "frame") {
      const isBeam = toggled.name.startsWith("beam_");
      if (isBeam) setObjectsCardOpen(true);
      else setTypesCardOpen(true);
      selectFrame(toggled.object, toggled.name);
      requestAnimationFrame(() => {
        document
          .getElementById(isBeam ? "objects-card" : "types-card")
          ?.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    } else {
      setSelection(null);
    }
  };

  const issueViewportCommand = useCallback(
    (body: ViewportCommandBody): Promise<void> => {
      viewportCommandIdRef.current += 1;
      const command = {
        id: viewportCommandIdRef.current,
        ...body,
      } as ViewportCommand;
      setViewerCardOpen(true);
      return new Promise<void>((resolve, reject) => {
        const queue = viewportCommandQueueRef.current;
        queue.push({ command, resolve, reject });
        if (queue.length === 1) setViewportCommand(command);
      });
    },
    [],
  );

  const handleViewportCommandApplied = useCallback(
    (id: number, error?: string) => {
      const queue = viewportCommandQueueRef.current;
      const completed = queue[0];
      if (!completed || completed.command.id !== id) return;
      queue.shift();
      setViewportCommand(queue[0]?.command ?? null);
      if (error) completed.reject(new Error(error));
      else completed.resolve();
    },
    [],
  );

  const applyExternalSelection = (next: SelectedEntity) => {
    if (!next) {
      setSelection((current) =>
        sameSelection(current, null) ? current : null,
      );
      return;
    }
    const currentLayout = layoutRef.current;
    if (next.kind === "curve") {
      if (!Object.hasOwn(currentLayout.reference_curves, next.name)) {
        throw new Error(`Unknown reference curve: ${next.name}`);
      }
      const nextCurve = currentLayout.reference_curves[next.name];
      if (
        next.segmentIndex !== undefined &&
        next.segmentIndex >= nextCurve.segments.length
      ) {
        throw new Error(
          `Curve ${next.name} has no segment ${next.segmentIndex}`,
        );
      }
    } else {
      const objectName = next.kind === "object" ? next.name : next.object;
      if (!Object.hasOwn(currentLayout.objects, objectName)) {
        throw new Error(`Unknown object: ${objectName}`);
      }
      const nextObject = currentLayout.objects[objectName];
      const nextType = currentLayout.types[nextObject.type];
      if (!nextType) {
        throw new Error(
          `Object ${objectName} has unknown type ${nextObject.type}`,
        );
      }
      if (
        next.kind === "frame" &&
        !objectFrameNames(nextType, nextObject).includes(next.name)
      ) {
        throw new Error(`Object ${objectName} has no frame ${next.name}`);
      }
    }
    if (!selectionIsInScope(next, viewportScopeRef.current)) {
      throw new Error("Selection is outside the current viewport scope");
    }
    if (next.kind === "curve") {
      setCurvesCardOpen(true);
      setSegmentsOpen(true);
      setSelectedCurve(next.name);
    } else {
      const objectName = next.kind === "object" ? next.name : next.object;
      const nextObject = currentLayout.objects[objectName];
      setObjectsCardOpen(true);
      setSelectedObject(objectName);
      setSelectedType(nextObject.type);
      if (next.kind === "frame" && !next.name.startsWith("beam_")) {
        setTypesCardOpen(true);
        setTypeFramesOpen(true);
        if (!isImplicitTypeFrameName(next.name)) {
          setSelectedTypeFrame(next.name);
        }
      }
    }
    setSelection((current) => (sameSelection(current, next) ? current : next));
  };

  const executePythonBridgeCommand = (command: PythonBridgeCommand) => {
    switch (command.command) {
      case "set_layout": {
        loadValue(command.layout, "Python", {
          preserveViewport: true,
          ...(command.scope ? { scope: command.scope } : {}),
        });
        if (Object.hasOwn(command, "selection")) {
          applyExternalSelection(command.selection ?? null);
        }
        if (
          command.fit &&
          !fitTargetIsInScope(command.fit, viewportScopeRef.current)
        ) {
          throw new Error("Fit target is outside the current viewport scope");
        }
        // Even an empty visibility update acts as the render barrier for the
        // new layout/scope, so Python is acknowledged only after the viewport
        // has observed this transaction.
        return (async () => {
          await issueViewportCommand({
            command: "set_visibility",
            visibility: command.visibility ?? {},
          });
          if (command.mode) {
            await issueViewportCommand({ command: "set_mode", mode: command.mode });
          }
          if (command.view) {
            await issueViewportCommand({ command: "set_view", view: command.view });
          }
          if (command.fit) {
            await issueViewportCommand({ command: "fit", target: command.fit });
          }
        })();
      }
      case "get_layout":
        return { layout: layoutRef.current };
      case "set_selection":
        applyExternalSelection(command.selection);
        return issueViewportCommand({
          command: "set_visibility",
          visibility: {},
        });
      case "fit":
        if (
          command.target.kind === "curve" &&
          !Object.hasOwn(
            layoutRef.current.reference_curves,
            command.target.name,
          )
        ) {
          throw new Error(`Unknown reference curve: ${command.target.name}`);
        }
        if (
          command.target.kind === "object" &&
          !Object.hasOwn(layoutRef.current.objects, command.target.name)
        ) {
          throw new Error(`Unknown object: ${command.target.name}`);
        }
        if (!fitTargetIsInScope(command.target, viewportScopeRef.current)) {
          throw new Error("Fit target is outside the current viewport scope");
        }
        return issueViewportCommand({ command: "fit", target: command.target });
      case "set_mode":
        return issueViewportCommand({
          command: "set_mode",
          mode: command.mode,
        });
      case "set_view":
        return issueViewportCommand({
          command: "set_view",
          view: command.view,
        });
      case "set_scope":
        validateScope(layoutRef.current, command.scope);
        viewportScopeRef.current = command.scope;
        setViewerCardOpen(true);
        setViewportScope((current) =>
          sameScope(current, command.scope) ? current : command.scope,
        );
        setSelection((current) =>
          selectionIsInScope(current, command.scope) ? current : null,
        );
        return issueViewportCommand({
          command: "set_visibility",
          visibility: {},
        });
      case "set_visibility":
        return issueViewportCommand({
          command: "set_visibility",
          visibility: command.visibility,
        });
    }
  };

  useEffect(() => {
    pythonBridgeHandlersRef.current = {
      execute: executePythonBridgeCommand,
      getSelection: () => selection,
    };
  });

  useEffect(() => {
    const controller = installPythonBridge(window, () => {
      const handlers = pythonBridgeHandlersRef.current;
      if (!handlers) throw new Error("Python bridge is not ready");
      return handlers;
    });
    pythonBridgeRef.current = controller;
    return () => {
      controller?.close();
      if (pythonBridgeRef.current === controller) pythonBridgeRef.current = null;
    };
  }, []);

  useEffect(() => {
    pythonBridgeRef.current?.emitSelection(selection);
  }, [selection]);

  const curveNames = Object.keys(layout.reference_curves);
  const typeNames = Object.keys(layout.types);
  const objectNames = Object.keys(layout.objects);
  const hasLayoutContent = Boolean(
    curveNames.length || typeNames.length || objectNames.length,
  );
  const curve = layout.reference_curves[selectedCurve];
  const typeDefinition = layout.types[selectedType];
  const typePath = typeDefinition?.shape
    ? shapePath(typeDefinition.shape)
    : null;
  const object = layout.objects[selectedObject];
  const frameNames = Object.keys(typeDefinition?.frames ?? {});
  const frameDefinition = typeDefinition?.frames[selectedTypeFrame];
  const objectTargetNames = object
    ? objectFrameNames(layout.types[object.type], object)
    : ["center"];
  const typeInstances = objectNames.filter(
    (name) => layout.objects[name].type === selectedType,
  );

  const renameCurve = (from: string, requested: string) => {
    const to = requested.trim();
    if (!to || (to !== from && curveNames.includes(to))) return;
    update((draft) => {
      draft.reference_curves = Object.fromEntries(
        Object.entries(draft.reference_curves).map(([name, value]) => [
          name === from ? to : name,
          value,
        ]),
      );
      forEachTransformation(draft, (transformation) => {
        if (
          transformation.reference.kind === "curve" &&
          transformation.reference.curve === from
        ) {
          transformation.reference.curve = to;
        }
      });
      for (const object of Object.values(draft.objects)) {
        if (object.position.reference_curve === from) {
          object.position.reference_curve = to;
        }
      }
    });
    setSelectedCurve(to);
    if (selection?.kind === "curve" && selection.name === from) {
      setSelection({ ...selection, name: to });
    }
  };

  const renameType = (from: string, requested: string) => {
    const to = requested.trim();
    if (!to || (to !== from && typeNames.includes(to))) return;
    update((draft) => {
      draft.types = Object.fromEntries(
        Object.entries(draft.types).map(([name, value]) => [
          name === from ? to : name,
          value,
        ]),
      );
      for (const object of Object.values(draft.objects)) {
        if (object.type === from) object.type = to;
      }
    });
    setSelectedType(to);
  };

  const renameObject = (from: string, requested: string) => {
    const to = requested.trim();
    if (!to || (to !== from && objectNames.includes(to))) return;
    update((draft) => {
      draft.objects = Object.fromEntries(
        Object.entries(draft.objects).map(([name, value]) => [
          name === from ? to : name,
          value,
        ]),
      );
      forEachTransformation(draft, (transformation) => {
        if (
          transformation.reference.kind === "object_frame" &&
          transformation.reference.object === from
        ) {
          transformation.reference.object = to;
        }
      });
    });
    setSelectedObject(to);
    if (selection?.kind === "object" && selection.name === from) {
      setSelection({ kind: "object", name: to });
    } else if (selection?.kind === "frame" && selection.object === from) {
      setSelection({ ...selection, object: to });
    }
  };

  const renameTypeFrame = (
    typeName: string,
    from: string,
    requested: string,
  ) => {
    const to = requested.trim();
    if (isImplicitTypeFrameName(to)) {
      setStatus({
        kind: "error",
        message: `${to} is reserved for an implicit type frame`,
      });
      return;
    }
    if (
      !to ||
      (to !== from && frameNames.includes(to))
    ) return;
    update((draft) => {
      const target = draft.types[typeName];
      target.frames = Object.fromEntries(
        Object.entries(target.frames).map(([name, value]) => [
          name === from ? to : name,
          value,
        ]),
      );
      forEachTransformation(draft, (transformation) => {
        const reference = transformation.reference;
        if (
          reference.kind === "object_frame" &&
          draft.objects[reference.object]?.type === typeName &&
          reference.frame === from
        ) {
          reference.frame = to;
        }
      });
      if (from !== "center") {
        for (const object of Object.values(draft.objects)) {
          if (object.type === typeName && object.position.target === from) {
            object.position.target = to;
          }
        }
      }
    });
    setSelectedTypeFrame((current) => (current === from ? to : current));
    if (
      selection?.kind === "frame" &&
      layout.objects[selection.object]?.type === typeName &&
      selection.name === from
    ) {
      setSelection({ ...selection, name: to });
    }
  };

  const addCurve = () => {
    const name = uniqueName("curve", curveNames);
    update((draft) => {
      draft.reference_curves[name] = {
        color: "#5b8f9d",
        starting_frame: {
          reference: { kind: "world" },
          transformation: [],
        },
        segments: [[1, 0, 0]],
      };
    });
    setSelectedCurve(name);
    setCurvesCardOpen(true);
    setSegmentsOpen(true);
    setSelection({ kind: "curve", name });
  };

  const removeCurve = () => {
    if (!curve) return;
    let used = false;
    forEachTransformation(layout, (transformation, label) => {
      if (
        label !== `curve ${selectedCurve}` &&
        transformation.reference.kind === "curve" &&
        transformation.reference.curve === selectedCurve
      ) {
        used = true;
      }
    });
    if (
      Object.values(layout.objects).some(
        (candidate) => candidate.position.reference_curve === selectedCurve,
      )
    ) {
      used = true;
    }
    if (used) {
      setStatus({
        kind: "error",
        message: `Curve ${selectedCurve} is still referenced`,
      });
      return;
    }
    const index = curveNames.indexOf(selectedCurve);
    const next = curveNames[index + 1] ?? curveNames[index - 1] ?? "";
    update((draft) => {
      delete draft.reference_curves[selectedCurve];
    });
    setSelectedCurve(next);
    if (selection?.kind === "curve" && selection.name === selectedCurve) {
      setSelection(next ? { kind: "curve", name: next } : null);
    }
  };

  const removeCurveSegment = (index: number) => {
    update((draft) => {
      draft.reference_curves[selectedCurve].segments.splice(index, 1);
    });
    if (
      selection?.kind === "curve" &&
      selection.name === selectedCurve &&
      selection.segmentIndex !== undefined
    ) {
      if (selection.segmentIndex === index) {
        setSelection({ kind: "curve", name: selectedCurve });
      } else if (selection.segmentIndex > index) {
        setSelection({
          ...selection,
          segmentIndex: selection.segmentIndex - 1,
        });
      }
    }
  };

  const addType = () => {
    const name = uniqueName("type", typeNames);
    update((draft) => {
      draft.types[name] = {
        color: "#f0a84b",
        frames: {},
      };
    });
    setSelectedType(name);
    setTypesCardOpen(true);
    setSelectedTypeFrame("");
    setTypeFramesOpen(true);
  };

  const removeType = () => {
    if (!typeDefinition) return;
    if (typeInstances.length) {
      const preview = typeInstances.slice(0, 3).join(", ");
      setStatus({
        kind: "error",
        message: `Type ${selectedType} is used by ${typeInstances.length} object${typeInstances.length === 1 ? "" : "s"}: ${preview}${typeInstances.length > 3 ? "…" : ""}`,
      });
      return;
    }
    const index = typeNames.indexOf(selectedType);
    const next = typeNames[index + 1] ?? typeNames[index - 1] ?? "";
    const nextFrame = Object.keys(layout.types[next]?.frames ?? {})[0] ?? "";
    update((draft) => {
      delete draft.types[selectedType];
    });
    setSelectedType(next);
    setSelectedTypeFrame(nextFrame);
  };

  const addObject = () => {
    if (!typeNames.length) {
      setStatus({ kind: "error", message: "Create a type before adding an object" });
      return;
    }
    const name = uniqueName("object", objectNames);
    const type = layout.types[selectedType] ? selectedType : typeNames[0];
    const reference: Reference = curveNames.length
      ? { kind: "curve", curve: curveNames[0] }
      : { kind: "world" };
    update((draft) => {
      draft.objects[name] = {
        type,
        position: { target: "center", reference, transformation: [] },
      };
    });
    setSelectedObject(name);
    setObjectsCardOpen(true);
    setSelectedType(type);
    setSelectedTypeFrame(Object.keys(layout.types[type].frames)[0] ?? "");
    setSelection({ kind: "object", name });
  };

  const removeObject = () => {
    if (!object) return;
    let used = false;
    forEachTransformation(layout, (transformation, label) => {
      if (
        label !== `object ${selectedObject}` &&
        transformation.reference.kind === "object_frame" &&
        transformation.reference.object === selectedObject
      ) {
        used = true;
      }
    });
    if (used) {
      setStatus({
        kind: "error",
        message: `Object ${selectedObject} supplies a referenced frame`,
      });
      return;
    }
    const index = objectNames.indexOf(selectedObject);
    const next = objectNames[index + 1] ?? objectNames[index - 1] ?? "";
    const nextType = layout.objects[next]?.type;
    const nextFrame = nextType
      ? Object.keys(layout.types[nextType]?.frames ?? {})[0] ?? ""
      : "";
    update((draft) => {
      delete draft.objects[selectedObject];
    });
    setSelectedObject(next);
    if (nextType) {
      setSelectedType(nextType);
      setSelectedTypeFrame(nextFrame);
    }
    if (
      (selection?.kind === "object" && selection.name === selectedObject) ||
      (selection?.kind === "frame" && selection.object === selectedObject)
    ) {
      setSelection(next ? { kind: "object", name: next } : null);
    }
  };

  const changeObjectType = (nextType: string) => {
    if (!object || !layout.types[nextType]) return;
    const nextTypeFrameNames = Object.keys(layout.types[nextType].frames);
    const nextReferenceFrameNames = objectFrameNames(layout.types[nextType], object);
    const missing = new Set<string>();
    forEachTransformation(layout, (transformation) => {
      const reference = transformation.reference;
      if (
        reference.kind === "object_frame" &&
        reference.object === selectedObject &&
        !nextReferenceFrameNames.includes(reference.frame)
      ) {
        missing.add(reference.frame);
      }
    });
    if (missing.size) {
      setStatus({
        kind: "error",
        message: `Cannot use type ${nextType}: missing referenced frame${missing.size === 1 ? "" : "s"} ${[...missing].join(", ")}`,
      });
      return;
    }
    if (!nextReferenceFrameNames.includes(object.position.target)) {
      setStatus({
        kind: "error",
        message: `Cannot use type ${nextType}: target frame ${object.position.target} is not defined`,
      });
      return;
    }
    update((draft) => {
      draft.objects[selectedObject].type = nextType;
    });
    setSelectedType(nextType);
    const nextFrame = nextTypeFrameNames.includes(selectedTypeFrame)
      ? selectedTypeFrame
      : nextTypeFrameNames[0] ?? "";
    setSelectedTypeFrame(nextFrame);
    if (
      selection?.kind === "frame" &&
      selection.object === selectedObject &&
      !nextTypeFrameNames.includes(selection.name)
    ) {
      setSelection({ kind: "object", name: selectedObject });
    }
  };

  const removeTypeFrame = (frameName: string) => {
    const instanceSet = new Set(typeInstances);
    const targetUsers = typeInstances.filter(
      (name) => layout.objects[name].position.target === frameName,
    );
    if (targetUsers.length) {
      const preview = targetUsers.slice(0, 3).join(", ");
      setStatus({
        kind: "error",
        message: `Frame ${selectedType}.${frameName} positions ${targetUsers.length} object${targetUsers.length === 1 ? "" : "s"}: ${preview}${targetUsers.length > 3 ? "…" : ""}`,
      });
      return;
    }
    let usedBy = "";
    forEachTransformation(layout, (transformation, label) => {
      const reference = transformation.reference;
      if (
        reference.kind === "object_frame" &&
        instanceSet.has(reference.object) &&
        reference.frame === frameName
      ) {
        usedBy = label;
      }
    });
    if (usedBy) {
      setStatus({
        kind: "error",
        message: `Frame ${selectedType}.${frameName} is still referenced by ${usedBy}`,
      });
      return;
    }
    const index = frameNames.indexOf(frameName);
    const next = frameNames[index + 1] ?? frameNames[index - 1] ?? "";
    update((draft) => {
      delete draft.types[selectedType].frames[frameName];
    });
    if (selectedTypeFrame === frameName) setSelectedTypeFrame(next);
    if (
      selection?.kind === "frame" &&
      instanceSet.has(selection.object) &&
      selection.name === frameName
    ) {
      setSelection(
        next
          ? { kind: "frame", object: selection.object, name: next }
          : { kind: "object", name: selection.object },
      );
    }
  };

  return (
    <TooltipProvider>
      <main className="app-shell">
        <header className="topbar">
          <div className="brand">
            <div className="brand-mark" aria-hidden="true">
              <span /><span /><span />
            </div>
            <div>
              <h1>Layout Studio</h1>
              <p>Curve-referenced geometry editor</p>
            </div>
            <Dialog>
              <DialogTrigger asChild>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-sm"
                  className="model-help-trigger"
                  aria-label="About the layout model"
                  title="About the layout model"
                >
                  <CircleHelp />
                </Button>
              </DialogTrigger>
              <DialogContent className="model-help-dialog sm:max-w-2xl">
                <DialogHeader>
                  <DialogTitle>Layout model</DialogTitle>
                  <DialogDescription>
                    How curves, reusable types and positioned objects form a layout.
                  </DialogDescription>
                </DialogHeader>
                <div className="model-help-content">
                  <section>
                    <h2>Entities and relationships</h2>
                    <dl>
                      <div>
                        <dt>Reference curves</dt>
                        <dd>
                          Named spatial paths with a color, a starting frame and ordered{" "}
                          <code>[length, angle, roll]</code> segments. Length is in metres;{" "}
                          angle and roll are radians in JSON and degrees in the editor. A
                          positive angle bends toward −x at zero roll; positive roll rotates
                          that direction toward −y.
                        </dd>
                      </div>
                      <div>
                        <dt>Types</dt>
                        <dd>
                          Reusable definitions with a color and optional mechanical geometry,
                          magnetic axis and named local frames. Mechanical and magnetic
                          paths have independent lengths, curvatures and rolls. Every
                          instance has a center frame.
                        </dd>
                      </div>
                      <div>
                        <dt>Objects</dt>
                        <dd>
                          Instances of a type. A position says which target frame on the
                          instance is placed at a transformed world, curve or object-frame
                          reference. Many objects can reuse one type. Each object owns
                          its beam interface, which uses the type’s magnetic axis unless
                          customized. Its center, entry and exit frames exist when a
                          custom interface or magnetic axis is available.
                        </dd>
                      </div>
                    </dl>
                  </section>
                  <section>
                    <h2>Transformations</h2>
                    <p>
                      Operations are stored as ordered{" "}<code>[name, value]</code> pairs
                      and act on the current local x, y and s axes. <code>tx</code>{" "}
                      and <code>ty</code> translate in x and y; <code>tt</code> translates
                      in a straight line along the current tangent s. These distances are
                      metres. <code>rx</code>, <code>ry</code> and <code>rs</code> rotate about the
                      current axes; JSON uses radians while the editor shows degrees.
                    </p>
                    <p>
                      <code>ts</code> is a path coordinate. With a curve reference, all ts
                      values are summed to select the curve frame before the remaining
                      operations run. In a local frame, including a beam center, ts follows the mechanical axis
                      when present and a straight local axis otherwise. <code>tt</code> never
                      follows a curve. Magnetic and Beam entry/exit frames follow their own
                      axes from their respective center frames.
                    </p>
                  </section>
                  <section>
                    <h2>Inferring s from a referenced frame origin</h2>
                    <p>
                      An object position may pair a world or object-frame reference with{" "}
                      <code>reference_curve</code>. When that position contains ts, the
                      referenced frame origin P is mapped to the curve value s satisfying{" "}
                      <code>(P − r(s)) · t(s) = 0</code>: the curve frame&apos;s x-y plane at s
                      contains P. The solution is searched only on the defined curve domain.
                    </p>
                    <p>
                      If several s values satisfy the plane equation, the one whose curve
                      frame origin r(s) is closest to P is used. An error is raised only
                      when no solution exists or when multiple closest solutions are
                      equidistant (including a closest continuous interval). Once s is
                      known, the summed ts offset selects a new curve frame; the referenced
                      frame&apos;s orientation is not retained, and the remaining operations
                      are applied from that frame.
                    </p>
                  </section>
                </div>
              </DialogContent>
            </Dialog>
          </div>

          <div className="import-cluster">
            <AlertDialog>
              <AlertDialogTrigger asChild>
                <Button
                  type="button"
                  variant="outline"
                  aria-label="Clear layout"
                  disabled={status.kind === "loading" || !hasLayoutContent}
                >
                  <Trash2 /> Clear
                </Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>Clear the layout?</AlertDialogTitle>
                  <AlertDialogDescription>
                    This removes every reference curve, type and object from the
                    editor. Download the current layout first if you want to keep it.
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>Cancel</AlertDialogCancel>
                  <AlertDialogAction variant="destructive" onClick={clearLayout}>
                    Clear layout
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
            <div className="url-loader">
              <Link aria-hidden="true" />
              <Input
                aria-label="Layout JSON URL"
                type="text"
                inputMode="url"
                autoComplete="off"
                list="layout-url-suggestions"
                placeholder="layouts/example.json or https://…"
                value={url}
                onChange={(event) => setUrl(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") void importUrl();
                }}
              />
              <datalist id="layout-url-suggestions">
                {urlSuggestions.map((suggestion) => (
                  <option
                    key={suggestion.href}
                    value={suggestion.path}
                    label={suggestion.label}
                  />
                ))}
              </datalist>
              <LayoutUrlPicker
                suggestions={urlSuggestions}
                onSelect={setUrl}
              />
              <Button
                type="button"
                variant="secondary"
                size="sm"
                disabled={!url.trim() || status.kind === "loading"}
                onClick={() => void importUrl()}
              >
                Load URL
              </Button>
            </div>
            <Input
              ref={fileInputRef}
              className="sr-only"
              type="file"
              accept="application/json,.json"
              onChange={(event) => void importFile(event.target.files?.[0])}
            />
            <Button
              type="button"
              variant="outline"
              onClick={() => fileInputRef.current?.click()}
            >
              <Upload /> Import file
            </Button>
            <Button type="button" onClick={downloadLayout}>
              <Download /> Download JSON
            </Button>
          </div>

          <div className={`status-pill status-${status.kind}`} role="status">
            <span />{status.message}
          </div>
        </header>

        <div className="workspace">
          <section className="editor-column" aria-label="Layout editors">
            <Collapsible
              asChild
              open={curvesCardOpen}
              onOpenChange={setCurvesCardOpen}
            >
              <Card
                id="curves-card"
                className={`editor-card ${selection?.kind === "curve" ? "selected-card" : ""}`}
              >
              <CardHeader>
                <div className="eyebrow">
                  <span className="index">01</span> Geometry backbone
                </div>
                <CardTitle>
                  Reference curves
                  <span className="main-card-count">{curveNames.length}</span>
                </CardTitle>
                <CardDescription>
                  Ordered straight and arc segments in path length.
                </CardDescription>
                <CardAction className="main-card-actions">
                  <Button type="button" variant="outline" size="sm" onClick={addCurve}>
                    <Plus /> Curve
                  </Button>
                  <CollapsibleTrigger asChild>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      className="main-card-toggle"
                      aria-label={`${curvesCardOpen ? "Hide" : "Show"} reference curves card`}
                    >
                      <ChevronDown /> {curvesCardOpen ? "Hide" : "Show"}
                    </Button>
                  </CollapsibleTrigger>
                </CardAction>
              </CardHeader>
              <CollapsibleContent className="main-card-content">
                <CardContent>
                {!curveNames.length ? (
                  <div className="empty-state">
                    <FileJson /><p>No reference curves</p>
                    <Button type="button" size="sm" onClick={addCurve}>Add curve</Button>
                  </div>
                ) : curve ? (
                  <>
                    <div className="entity-heading">
                      <NamePicker
                        key={selectedCurve}
                        label="Curve name"
                        names={curveNames}
                        value={selectedCurve}
                        onSelect={selectCurve}
                        onRename={renameCurve}
                      />
                      <div className="entity-actions">
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          aria-label={`Fit curve ${selectedCurve} in 3D view`}
                          onClick={() => fitEntityInViewport("curve", selectedCurve)}
                        >
                          <Focus /> Fit view
                        </Button>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          aria-label={`Remove ${selectedCurve}`}
                          onClick={removeCurve}
                        >
                          <Trash2 />
                        </Button>
                      </div>
                    </div>

                    <div className="object-basics">
                      <Field label="Curve color">
                        <div className="color-field">
                          <Input
                            aria-label="Curve color picker"
                            type="color"
                            value={curve.color}
                            onChange={(event) =>
                              update((draft) => {
                                draft.reference_curves[selectedCurve].color =
                                  event.target.value;
                              })
                            }
                          />
                          <Input
                            aria-label="Curve color"
                            key={`${selectedCurve}:${curve.color}`}
                            defaultValue={curve.color}
                            maxLength={7}
                            onKeyDown={(event) => {
                              if (event.key === "Enter") {
                                event.currentTarget.blur();
                              }
                            }}
                            onBlur={(event) => {
                              const next = event.currentTarget.value.trim();
                              if (/^#[0-9a-f]{6}$/i.test(next)) {
                                update((draft) => {
                                  draft.reference_curves[selectedCurve].color =
                                    next;
                                });
                              } else {
                                event.currentTarget.value = curve.color;
                                setStatus({
                                  kind: "error",
                                  message:
                                    "Color must be a six-digit hex value",
                                });
                              }
                            }}
                          />
                        </div>
                      </Field>
                    </div>

                    <div className="subsection">
                      <div className="subsection-title">
                        <h3>Starting frame</h3>
                        <span>(reference, transformation)</span>
                      </div>
                      <ReferenceEditor
                        value={curve.starting_frame}
                        layout={layout}
                        owner={{ kind: "curve", name: selectedCurve }}
                        onChange={(value) =>
                          updateValidated((draft) => {
                            draft.reference_curves[selectedCurve].starting_frame = value;
                          })
                        }
                      />
                    </div>

                    <Collapsible
                      className="subsection collapsible-subsection"
                      open={segmentsOpen}
                      onOpenChange={setSegmentsOpen}
                    >
                      <div className="subsection-title">
                        <div className="subsection-title-copy">
                          <h3>
                            Segments
                            <span className="section-count">
                              {curve.segments.length.toLocaleString("en-US")}
                            </span>
                          </h3>
                          <span>length [m] · angle (°) · roll (°)</span>
                        </div>
                        <CollapsibleTrigger asChild>
                          <Button
                            type="button"
                            variant="ghost"
                            size="xs"
                            className="collapse-trigger"
                            aria-label={`${segmentsOpen ? "Hide" : "Show"} ${curve.segments.length} segments`}
                          >
                            <ChevronDown /> {segmentsOpen ? "Hide" : "Show"}
                          </Button>
                        </CollapsibleTrigger>
                      </div>
                      <CollapsibleContent>
                        <div className="segment-head">
                          <span>#</span><span>Length [m]</span><span>Angle (°)</span><span>Roll (°)</span><span />
                        </div>
                        <div
                          aria-label={`${selectedCurve} segments`}
                          className={`segment-list ${
                            curve.segments.length > 4
                              ? "segment-list-scrollable"
                              : ""
                          }`}
                        >
                          {curve.segments.map((segment, index) => (
                            <div
                              aria-label={`Segment ${index + 1} editor`}
                              className={`segment-row ${
                                selection?.kind === "curve" &&
                                selection.name === selectedCurve &&
                                selection.segmentIndex === index
                                  ? "selected-segment-row"
                                  : ""
                              }`}
                              id={`curve-segment-row-${index}`}
                              key={index}
                              role="group"
                              tabIndex={-1}
                            >
                              <span className="row-index">
                                {String(index + 1).padStart(2, "0")}
                              </span>
                              {segment.map((value, axis) => (
                                <NumberInput
                                  key={axis}
                                  value={
                                    axis === 0
                                      ? value
                                      : Math.round((value * 180 / Math.PI) * 1e10) / 1e10
                                  }
                                  min={axis === 0 ? 0 : undefined}
                                  step={axis === 0 ? 0.1 : 5}
                                  label={`Segment ${index + 1} ${["length", "angle", "roll"][axis]}`}
                                  onChange={(next) =>
                                    update((draft) => {
                                      draft.reference_curves[selectedCurve].segments[index][axis] =
                                        axis === 0
                                          ? Math.max(0.000001, next)
                                          : next * Math.PI / 180;
                                    })
                                  }
                                />
                              ))}
                              <Button
                                type="button"
                                variant="ghost"
                                size="icon-sm"
                                aria-label={`Remove segment ${index + 1}`}
                                disabled={curve.segments.length === 1}
                                onClick={() => removeCurveSegment(index)}
                              >
                                <Trash2 />
                              </Button>
                            </div>
                          ))}
                        </div>
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          className="wide-add"
                          onClick={() =>
                            update((draft) => {
                              draft.reference_curves[selectedCurve].segments.push([1, 0, 0]);
                            })
                          }
                        >
                          <Plus /> Add segment
                        </Button>
                      </CollapsibleContent>
                    </Collapsible>
                  </>
                ) : null}
                </CardContent>
              </CollapsibleContent>
              </Card>
            </Collapsible>

            <Collapsible
              asChild
              open={typesCardOpen}
              onOpenChange={setTypesCardOpen}
            >
              <Card
                id="types-card"
                className={`editor-card ${
                  selection?.kind === "frame" &&
                  layout.objects[selection.object]?.type === selectedType
                    ? "selected-card"
                    : ""
                }`}
              >
              <CardHeader>
                <div className="eyebrow">
                  <span className="index">02</span> Reusable geometry
                </div>
                <CardTitle>
                  Types
                  <span className="main-card-count">{typeNames.length}</span>
                </CardTitle>
                <CardDescription>
                  Optional mechanical and magnetic geometry with local frames.
                </CardDescription>
                <CardAction className="main-card-actions">
                  <Button type="button" variant="outline" size="sm" onClick={addType}>
                    <Plus /> Type
                  </Button>
                  <CollapsibleTrigger asChild>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      className="main-card-toggle"
                      aria-label={`${typesCardOpen ? "Hide" : "Show"} types card`}
                    >
                      <ChevronDown /> {typesCardOpen ? "Hide" : "Show"}
                    </Button>
                  </CollapsibleTrigger>
                </CardAction>
              </CardHeader>
              <CollapsibleContent className="main-card-content">
                <CardContent>
                {!typeNames.length ? (
                  <div className="empty-state">
                    <Shapes /><p>No types</p>
                    <Button type="button" size="sm" onClick={addType}>Add type</Button>
                  </div>
                ) : typeDefinition ? (
                  <>
                    <div className="entity-heading">
                      <NamePicker
                        key={selectedType}
                        label="Type name"
                        names={typeNames}
                        value={selectedType}
                        onSelect={selectType}
                        onRename={renameType}
                      />
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        aria-label={`Remove type ${selectedType}`}
                        onClick={removeType}
                      >
                        <Trash2 />
                      </Button>
                    </div>

                    <div className="type-overview">
                      <div className="type-usage">
                        <span>Instances</span>
                        <strong>{typeInstances.length.toLocaleString("en-US")}</strong>
                        <p>
                          {typeInstances.length
                            ? `${typeInstances.slice(0, 3).join(", ")}${typeInstances.length > 3 ? "…" : ""}`
                            : "Not used by any object"}
                        </p>
                      </div>
                      <Field label="Color">
                        <div className="color-field">
                          <Input
                            aria-label="Type color picker"
                            type="color"
                            value={typeDefinition.color}
                            onChange={(event) =>
                              update((draft) => {
                                draft.types[selectedType].color = event.target.value;
                              })
                            }
                          />
                          <Input
                            aria-label="Type color"
                            key={`${selectedType}:${typeDefinition.color}`}
                            defaultValue={typeDefinition.color}
                            maxLength={7}
                            onKeyDown={(event) => {
                              if (event.key === "Enter") event.currentTarget.blur();
                            }}
                            onBlur={(event) => {
                              const next = event.currentTarget.value.trim();
                              if (/^#[0-9a-f]{6}$/i.test(next)) {
                                update((draft) => {
                                  draft.types[selectedType].color = next;
                                });
                              } else {
                                event.currentTarget.value = typeDefinition.color;
                                setStatus({
                                  kind: "error",
                                  message: "Color must be a six-digit hex value",
                                });
                              }
                            }}
                          />
                        </div>
                      </Field>
                    </div>

                    <div className="subsection">
                      <div className="subsection-title">
                        <div className="subsection-title-copy">
                          <h3>Mechanical geometry</h3>
                          <span>shape and axis centered on the object center</span>
                        </div>
                        <Button
                          type="button"
                          variant="outline"
                          size="xs"
                          onClick={() =>
                            update((draft) => {
                              if (draft.types[selectedType].shape) {
                                delete draft.types[selectedType].shape;
                              } else {
                                draft.types[selectedType].shape = ["box", 1, 1, 1, 0, 0];
                              }
                            })
                          }
                        >
                          {typeDefinition.shape ? <Trash2 /> : <Plus />}
                          {typeDefinition.shape ? "Remove" : "Add geometry"}
                        </Button>
                      </div>
                      {typeDefinition.shape ? (
                        <>
                          <div className="shape-row">
                            <Field label="Primitive">
                              <NativeSelect
                                value={typeDefinition.shape[0]}
                                onChange={(event) =>
                                  update((draft) => {
                                    const current = draft.types[selectedType].shape;
                                    if (!current) return;
                                    const { curvature, roll } = shapePath(current);
                                    draft.types[selectedType].shape =
                                      event.target.value === "box"
                                        ? ["box", 1, 1, 1, curvature, roll]
                                        : ["cylinder", 0.5, 1, curvature, roll];
                                  })
                                }
                              >
                                <NativeSelectOption value="box">Box</NativeSelectOption>
                                <NativeSelectOption value="cylinder">Cylinder</NativeSelectOption>
                              </NativeSelect>
                            </Field>
                            {typeDefinition.shape[0] === "box" ? (
                              <>
                                {([1, 2, 3] as const).map((axis) => (
                                  <Field key={axis} label={`${["", "dx", "dy", "dz"][axis]} [m]`}>
                                    <NumberInput
                                      value={(typeDefinition.shape as BoxShape)[axis]}
                                      min={0}
                                      step={0.1}
                                      label={`Box ${["", "dx", "dy", "dz"][axis]}`}
                                      onChange={(value) =>
                                        update((draft) => {
                                          const shape = draft.types[selectedType].shape;
                                          if (shape?.[0] === "box") {
                                            shape[axis] = Math.max(0.000001, value);
                                          }
                                        })
                                      }
                                    />
                                  </Field>
                                ))}
                              </>
                            ) : (
                              <>
                                {([1, 2] as const).map((axis) => (
                                  <Field key={axis} label={`${axis === 1 ? "r" : "dz"} [m]`}>
                                    <NumberInput
                                      value={(typeDefinition.shape as CylinderShape)[axis]}
                                      min={0}
                                      step={0.1}
                                      label={axis === 1 ? "Cylinder radius" : "Cylinder dz"}
                                      onChange={(value) =>
                                        update((draft) => {
                                          const shape = draft.types[selectedType].shape;
                                          if (shape?.[0] === "cylinder") {
                                            shape[axis] = Math.max(0.000001, value);
                                          }
                                        })
                                      }
                                    />
                                  </Field>
                                ))}
                              </>
                            )}
                          </div>
                          {typePath && (
                            <>
                              <div className="shape-path-row">
                                <Field label="Curvature [1/m]">
                                  <NumberInput
                                    value={typePath.curvature}
                                    step={0.01}
                                    label="Mechanical curvature"
                                    onChange={(value) =>
                                      update((draft) => {
                                        const shape = draft.types[selectedType].shape;
                                        if (!shape) return;
                                        if (shape[0] === "box") shape[4] = value;
                                        else shape[3] = value;
                                      })
                                    }
                                  />
                                </Field>
                                <Field label="Roll [degree]">
                                  <NumberInput
                                    value={typePath.roll * 180 / Math.PI}
                                    step={5}
                                    label="Mechanical roll in degrees"
                                    onChange={(value) =>
                                      update((draft) => {
                                        const shape = draft.types[selectedType].shape;
                                        if (!shape) return;
                                        const radians = value * Math.PI / 180;
                                        if (shape[0] === "box") shape[5] = radians;
                                        else shape[4] = radians;
                                      })
                                    }
                                  />
                                </Field>
                              </div>
                              <p className="shape-help">
                                dz is the mechanical centerline length. Positive curvature
                                at roll 0° bends toward −x; positive roll rotates the bend
                                toward −y.
                              </p>
                            </>
                          )}
                        </>
                      ) : (
                        <p className="inline-empty">
                          No mechanical shape. Instances remain selectable at their center.
                        </p>
                      )}
                    </div>

                    <div className="subsection">
                      <div className="subsection-title">
                        <div className="subsection-title-copy">
                          <h3>Magnetic axis</h3>
                          <span>magnetic center, entry and exit</span>
                        </div>
                        <Button
                          type="button"
                          variant="outline"
                          size="xs"
                          onClick={() =>
                            updateValidated((draft) => {
                              const type = draft.types[selectedType];
                              if (type.magnetic_center) {
                                delete type.magnetic_center;
                                delete type.magnetic_length;
                                delete type.magnetic_curvature;
                                delete type.magnetic_roll;
                              } else {
                                type.magnetic_center = { transformation: [] };
                                type.magnetic_length = 1;
                                type.magnetic_curvature = 0;
                                type.magnetic_roll = 0;
                              }
                            })
                          }
                        >
                          {typeDefinition.magnetic_center ? <Trash2 /> : <Plus />}
                          {typeDefinition.magnetic_center ? "Remove" : "Add axis"}
                        </Button>
                      </div>
                      {typeDefinition.magnetic_center ? (
                        <>
                          <div className="shape-path-row">
                            <Field label="Length [m]">
                              <NumberInput
                                value={typeDefinition.magnetic_length ?? 1}
                                min={0}
                                step={0.1}
                                label="Magnetic length"
                                onChange={(value) =>
                                  update((draft) => {
                                    draft.types[selectedType].magnetic_length =
                                      Math.max(0.000001, value);
                                  })
                                }
                              />
                            </Field>
                            <Field label="Curvature [1/m]">
                              <NumberInput
                                value={typeDefinition.magnetic_curvature ?? 0}
                                step={0.01}
                                label="Magnetic curvature"
                                onChange={(value) =>
                                  update((draft) => {
                                    draft.types[selectedType].magnetic_curvature = value;
                                  })
                                }
                              />
                            </Field>
                            <Field label="Roll [degree]">
                              <NumberInput
                                value={(typeDefinition.magnetic_roll ?? 0) * 180 / Math.PI}
                                step={5}
                                label="Magnetic roll in degrees"
                                onChange={(value) =>
                                  update((draft) => {
                                    draft.types[selectedType].magnetic_roll =
                                      value * Math.PI / 180;
                                  })
                                }
                              />
                            </Field>
                          </div>
                          <div className="implicit-reference">
                            magnetic_center is relative to object center. Entry and exit
                            are derived at −Lmag/2 and +Lmag/2 along the magnetic axis.
                          </div>
                          <OperationsEditor
                            value={typeDefinition.magnetic_center.transformation}
                            allowedNames={LOCAL_TRANSFORM_NAMES}
                            onChange={(transformation) =>
                              update((draft) => {
                                const center = draft.types[selectedType].magnetic_center;
                                if (center) center.transformation = transformation;
                              })
                            }
                          />
                        </>
                      ) : (
                        <p className="inline-empty">No magnetic axis or magnetic frames.</p>
                      )}
                    </div>

                    <Collapsible
                      className="subsection collapsible-subsection"
                      open={typeFramesOpen}
                      onOpenChange={setTypeFramesOpen}
                    >
                      <div className="subsection-title">
                        <div className="subsection-title-copy">
                          <h3>
                            Named frames
                            <span className="section-count">
                              {frameNames.length.toLocaleString("en-US")}
                            </span>
                          </h3>
                          <span>implicit reference: object center</span>
                        </div>
                        <div className="section-actions">
                          <Button
                            type="button"
                            variant="outline"
                            size="xs"
                            onClick={() => {
                              const name = uniqueName("frame", frameNames);
                              update((draft) => {
                                draft.types[selectedType].frames[name] = {
                                  transformation: [],
                                };
                              });
                              setSelectedTypeFrame(name);
                              setTypeFramesOpen(true);
                            }}
                          >
                            <Plus /> Frame
                          </Button>
                          <CollapsibleTrigger asChild>
                            <Button
                              type="button"
                              variant="ghost"
                              size="xs"
                              className="collapse-trigger"
                              aria-label={`${typeFramesOpen ? "Hide" : "Show"} ${frameNames.length} named frames`}
                            >
                              <ChevronDown /> {typeFramesOpen ? "Hide" : "Show"}
                            </Button>
                          </CollapsibleTrigger>
                        </div>
                      </div>
                      <CollapsibleContent>
                        {!frameNames.length ? (
                          <p className="inline-empty">No named frames.</p>
                        ) : frameDefinition ? (
                          <div className="frames-list">
                            <div className="frame-editor" key={selectedTypeFrame}>
                              <div className="frame-heading">
                                <Circle aria-hidden="true" />
                                <NamePicker
                                  label="Frame name"
                                  names={frameNames}
                                  value={selectedTypeFrame}
                                  onSelect={selectTypeFrame}
                                  onRename={(from, to) =>
                                    renameTypeFrame(selectedType, from, to)
                                  }
                                />
                                <Button
                                  type="button"
                                  variant="ghost"
                                  size="icon-sm"
                                  aria-label={`Remove frame ${selectedTypeFrame}`}
                                  onClick={() => removeTypeFrame(selectedTypeFrame)}
                                >
                                  <Trash2 />
                                </Button>
                              </div>
                              <div className="implicit-reference">
                                Relative to the center frame · ts follows the type
                                curve · tt moves straight along the current tangent
                              </div>
                              <OperationsEditor
                                value={frameDefinition.transformation}
                                allowedNames={LOCAL_TRANSFORM_NAMES}
                                onChange={(transformation) =>
                                  update((draft) => {
                                    draft.types[selectedType].frames[
                                      selectedTypeFrame
                                    ].transformation = transformation;
                                  })
                                }
                              />
                            </div>
                          </div>
                        ) : null}
                      </CollapsibleContent>
                    </Collapsible>
                  </>
                ) : null}
                </CardContent>
              </CollapsibleContent>
              </Card>
            </Collapsible>

            <Collapsible
              asChild
              open={objectsCardOpen}
              onOpenChange={setObjectsCardOpen}
            >
              <Card
                id="objects-card"
                className={`editor-card ${selection?.kind === "object" || selection?.kind === "frame" ? "selected-card" : ""}`}
              >
              <CardHeader>
                <div className="eyebrow">
                  <span className="index">03</span> Positioned instances
                </div>
                <CardTitle>
                  Objects
                  <span className="main-card-count">{objectNames.length}</span>
                </CardTitle>
                <CardDescription>
                  Reusable type, beam interface and reference-based position.
                </CardDescription>
                <CardAction className="main-card-actions">
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={!typeNames.length}
                    title={typeNames.length ? undefined : "Create a type first"}
                    onClick={addObject}
                  >
                    <Plus /> Object
                  </Button>
                  <CollapsibleTrigger asChild>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      className="main-card-toggle"
                      aria-label={`${objectsCardOpen ? "Hide" : "Show"} objects card`}
                    >
                      <ChevronDown /> {objectsCardOpen ? "Hide" : "Show"}
                    </Button>
                  </CollapsibleTrigger>
                </CardAction>
              </CardHeader>
              <CollapsibleContent className="main-card-content">
                <CardContent>
                {!objectNames.length ? (
                  <div className="empty-state">
                    <BoxIcon /><p>No objects</p>
                    <Button
                      type="button"
                      size="sm"
                      disabled={!typeNames.length}
                      onClick={addObject}
                    >
                      {typeNames.length ? "Add object" : "Create a type first"}
                    </Button>
                  </div>
                ) : object ? (
                  <>
                    <div className="entity-heading">
                      <NamePicker
                        key={selectedObject}
                        label="Object name"
                        names={objectNames}
                        value={selectedObject}
                        onSelect={selectObject}
                        onRename={renameObject}
                      />
                      <div className="entity-actions">
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          aria-label={`Fit object ${selectedObject} in 3D view`}
                          onClick={() => fitEntityInViewport("object", selectedObject)}
                        >
                          <Focus /> Fit view
                        </Button>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          aria-label={`Remove ${selectedObject}`}
                          onClick={removeObject}
                        >
                          <Trash2 />
                        </Button>
                      </div>
                    </div>

                    <div className="object-type-row">
                      <NamePicker
                        key={`${selectedObject}:${object.type}`}
                        label="Type"
                        names={typeNames}
                        value={object.type}
                        onSelect={changeObjectType}
                      />
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() => {
                          setTypesCardOpen(true);
                          selectType(object.type);
                          document
                            .getElementById("types-card")
                            ?.scrollIntoView({ behavior: "smooth", block: "start" });
                        }}
                      >
                        Edit type
                      </Button>
                    </div>

                    <div className="subsection">
                      <div className="subsection-title">
                        <div className="subsection-title-copy">
                          <h3>Beam interface</h3>
                          <span>object beam center, entry and exit</span>
                        </div>
                        <Button
                          type="button"
                          variant="outline"
                          size="xs"
                          onClick={() =>
                            updateValidated((draft) => {
                              const object = draft.objects[selectedObject];
                              if (object.beam_center) {
                                delete object.beam_center;
                                delete object.beam_length;
                                delete object.beam_curvature;
                                delete object.beam_roll;
                              } else {
                                const inherited = effectiveBeamFeature(draft.types[object.type], object);
                                object.beam_center = structuredClone(inherited?.center ?? { transformation: [] });
                                object.beam_length = inherited?.length ?? 1;
                                object.beam_curvature = inherited?.curvature ?? 0;
                                object.beam_roll = inherited?.roll ?? 0;
                              }
                            })
                          }
                        >
                          {object.beam_center ? <Trash2 /> : <Plus />}
                          {object.beam_center
                            ? layout.types[object.type].magnetic_center ? "Use magnetic axis" : "Remove interface"
                            : "Customize interface"}
                        </Button>
                      </div>
                      {object.beam_center ? (
                        <>
                          <div className="shape-path-row">
                            <Field label="Length [m]">
                              <NumberInput
                                value={object.beam_length ?? 1}
                                min={0}
                                step={0.1}
                                label="Beam-interface length"
                                onChange={(value) =>
                                  update((draft) => {
                                    draft.objects[selectedObject].beam_length =
                                      Math.max(0.000001, value);
                                  })
                                }
                              />
                            </Field>
                            <Field label="Curvature [1/m]">
                              <NumberInput
                                value={object.beam_curvature ?? 0}
                                step={0.01}
                                label="Beam-interface curvature"
                                onChange={(value) =>
                                  update((draft) => {
                                    draft.objects[selectedObject].beam_curvature = value;
                                  })
                                }
                              />
                            </Field>
                            <Field label="Roll [degree]">
                              <NumberInput
                                value={(object.beam_roll ?? 0) * 180 / Math.PI}
                                step={5}
                                label="Beam-interface roll in degrees"
                                onChange={(value) =>
                                  update((draft) => {
                                    draft.objects[selectedObject].beam_roll =
                                      value * Math.PI / 180;
                                  })
                                }
                              />
                            </Field>
                          </div>
                          <div className="implicit-reference">
                            beam_center is relative to object center. Beam entry and exit
                            are derived at −Lbeam/2 and +Lbeam/2 along the Beam axis.
                          </div>
                          <OperationsEditor
                            value={object.beam_center.transformation}
                            allowedNames={LOCAL_TRANSFORM_NAMES}
                            onChange={(transformation) =>
                              update((draft) => {
                                const center = draft.objects[selectedObject].beam_center;
                                if (center) center.transformation = transformation;
                              })
                            }
                          />
                        </>
                      ) : (
                        <p className="inline-empty">
                          {effectiveBeamFeature(layout.types[object.type], object)
                            ? "Uses the type’s magnetic axis, including its center, length, curvature and roll."
                            : "No magnetic axis to inherit. Customize the interface to define beam frames."}
                        </p>
                      )}
                    </div>

                    <div className="subsection">
                      <div className="subsection-title">
                        <h3>Position</h3>
                        <span>place target at (reference, transformation)</span>
                      </div>
                      <div className="transformation-editor">
                        <NamePicker
                          key={`${selectedObject}:${object.type}:${object.position.target}`}
                          label="Target frame"
                          names={objectTargetNames}
                          value={object.position.target}
                          onSelect={(target) =>
                            updateValidated((draft) => {
                              draft.objects[selectedObject].position.target =
                                target;
                            })
                          }
                        />
                        <ReferenceEditor
                          value={object.position}
                          layout={layout}
                          owner={{ kind: "object", name: selectedObject }}
                          onChange={(value) =>
                            updateValidated((draft) => {
                              draft.objects[selectedObject].position.reference =
                                value.reference;
                              if (value.reference_curve) {
                                draft.objects[selectedObject].position.reference_curve =
                                  value.reference_curve;
                              } else {
                                delete draft.objects[selectedObject].position.reference_curve;
                              }
                              draft.objects[
                                selectedObject
                              ].position.transformation = value.transformation;
                            })
                          }
                        />
                      </div>
                    </div>
                  </>
                ) : null}
                </CardContent>
              </CollapsibleContent>
              </Card>
            </Collapsible>
          </section>

          <Collapsible
            asChild
            open={viewerCardOpen}
            onOpenChange={setViewerCardOpen}
          >
            <Card className="viewport-card">
            <CardHeader>
              <div className="eyebrow">
                <span className="index">04</span> Spatial view
              </div>
              <CardTitle>3D layout</CardTitle>
              <CardDescription>
                {curveNames.length} curves · {typeNames.length} types ·{" "}
                {objectNames.length} objects ·{" "}
                {Object.values(layout.objects).reduce(
                  (sum, item) =>
                    sum + Object.keys(layout.types[item.type]?.frames ?? {}).length,
                  0,
                )} frames
              </CardDescription>
              <CardAction className="main-card-actions">
                <CollapsibleTrigger asChild>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="main-card-toggle"
                    aria-label={`${viewerCardOpen ? "Hide" : "Show"} 3D layout card`}
                  >
                    <ChevronDown /> {viewerCardOpen ? "Hide" : "Show"}
                  </Button>
                </CollapsibleTrigger>
              </CardAction>
            </CardHeader>
            <CollapsibleContent className="main-card-content">
              <CardContent>
                <LayoutViewport
                  key={viewerRevision}
                  layout={layout}
                  selection={selection}
                  onSelect={selectFromViewport}
                  fitRequest={viewportFitRequest}
                  command={viewportCommand}
                  onCommandApplied={handleViewportCommandApplied}
                  scope={viewportScope}
                />
              </CardContent>
            </CollapsibleContent>
            </Card>
          </Collapsible>

          <Collapsible
            asChild
            open={dependenciesCardOpen}
            onOpenChange={setDependenciesCardOpen}
          >
            <Card id="dependencies-card" className="dependency-card">
              <CardHeader>
                <div className="eyebrow">
                  <span className="index">05</span> Reference graph
                </div>
                <CardTitle>
                  Dependency tree
                  <span className="main-card-count">
                    {curveNames.length + objectNames.length}
                  </span>
                </CardTitle>
                <CardDescription>
                  Placement hierarchy from World to dependent entities.
                </CardDescription>
                <CardAction className="main-card-actions">
                  <CollapsibleTrigger asChild>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      className="main-card-toggle"
                      aria-label={`${dependenciesCardOpen ? "Hide" : "Show"} dependency tree card`}
                    >
                      <ChevronDown /> {dependenciesCardOpen ? "Hide" : "Show"}
                    </Button>
                  </CollapsibleTrigger>
                </CardAction>
              </CardHeader>
              <CollapsibleContent className="main-card-content">
                <CardContent>
                  <DependencyTree
                    key={`dependencies-${viewerRevision}`}
                    layout={layout}
                    selection={selection}
                    onSelect={selectFromViewport}
                  />
                </CardContent>
              </CollapsibleContent>
            </Card>
          </Collapsible>
        </div>
      </main>
    </TooltipProvider>
  );
}
