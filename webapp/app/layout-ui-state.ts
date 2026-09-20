import {objectFrameNames, type LayoutData, type SelectedEntity, type Vec3} from "./layout-data";
import type {SceneScope} from "./layout-geometry";
import type {Camera} from "./viewport-history";

export const SEGMENT_PAGE_SIZE = 50;
export const LARGE_SEGMENT_COUNT = 200;
export const LARGE_FRAME_COUNT = 200;

export type ViewportUiState = {
  camera?: Camera;
  pastViews: Camera[];
  futureViews: Camera[];
  mode: "orbit" | "pan" | "select" | "zoom-region";
  showCurves: boolean;
  showObjects: boolean;
  showFrames: boolean;
  showMechanicalAxis: boolean;
  showMagneticAxis: boolean;
  showBeamAxis: boolean;
};

export type DependencyUiState = {expanded: string[]; pages: Record<string, number>; selection: string | null};
export type LayoutUiState = {
  version: 1;
  editor: {
    selectedCurve: string;
    selectedType: string;
    selectedObject: string;
    selectedTypeFrame: string;
    segmentPage: number;
    selection: SelectedEntity;
  };
  cards: {
    curves: boolean; types: boolean; objects: boolean; viewer: boolean;
    dependencies: boolean; segments: boolean; typeFrames: boolean;
  };
  scope: SceneScope;
  viewport: ViewportUiState;
  dependencies: DependencyUiState;
};

const record = (value: unknown): Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown> : {};
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const vec3 = (value: unknown): value is Vec3 => Array.isArray(value) && value.length === 3 && value.every(finite);
const boolean = (value: unknown, fallback: boolean) => typeof value === "boolean" ? value : fallback;
const named = (value: unknown, names: object, fallback: string) =>
  typeof value === "string" && Object.hasOwn(names, value) ? value : fallback;

function camera(value: unknown): Camera | undefined {
  const raw = record(value);
  if (!finite(raw.azimuth) || !finite(raw.elevation) || !finite(raw.distance) ||
      raw.distance <= 0 || !vec3(raw.target)) return;
  return {
    azimuth: raw.azimuth, elevation: raw.elevation, distance: raw.distance, target: [...raw.target],
    ...(vec3(raw.axisScale) && raw.axisScale.every(v => v >= 0.001 && v <= 1000)
      ? {axisScale: [...raw.axisScale] as Vec3} : {}),
  };
}

function views(value: unknown): Camera[] {
  return Array.isArray(value) ? value.slice(-100).flatMap(item => {
    const parsed = camera(item);
    return parsed ? [parsed] : [];
  }) : [];
}

/** UI metadata is advisory: stale names and malformed settings use safe defaults. */
export function restoreLayoutUiState(layout: LayoutData): LayoutUiState {
  const raw = layout.ui_state?.version === 1 ? layout.ui_state : {};
  const editor = record(raw.editor), cards = record(raw.cards), viewport = record(raw.viewport);
  const dependency = record(raw.dependencies), scope = record(raw.scope);
  const selectedCurve = named(editor.selectedCurve, layout.reference_curves, Object.keys(layout.reference_curves)[0] ?? "");
  const selectedObject = named(editor.selectedObject, layout.objects, Object.keys(layout.objects)[0] ?? "");
  const selectedType = named(editor.selectedType, layout.types,
    layout.objects[selectedObject]?.type ?? Object.keys(layout.types)[0] ?? "");
  const frames = layout.types[selectedType]?.frames ?? {};
  const selectedTypeFrame = named(editor.selectedTypeFrame, frames, Object.keys(frames)[0] ?? "");
  const segments = layout.reference_curves[selectedCurve]?.segments.length ?? 0;
  let restoredScope: SceneScope = {kind: "layout"};
  if (typeof scope.name === "string" && (
    (scope.kind === "curve" && Object.hasOwn(layout.reference_curves, scope.name)) ||
    (scope.kind === "object" && Object.hasOwn(layout.objects, scope.name))
  )) restoredScope = {kind: scope.kind, name: scope.name};
  const candidate = record(editor.selection);
  let selection: SelectedEntity = null;
  if (candidate.kind === "curve" && typeof candidate.name === "string" && Object.hasOwn(layout.reference_curves, candidate.name)) {
    const index = candidate.segmentIndex;
    selection = {kind: "curve", name: candidate.name,
      ...(Number.isInteger(index) && (index as number) >= 0 && (index as number) < layout.reference_curves[candidate.name].segments.length
        ? {segmentIndex: index as number} : {})};
  } else if (candidate.kind === "object" && typeof candidate.name === "string" && Object.hasOwn(layout.objects, candidate.name)) {
    selection = {kind: "object", name: candidate.name};
  } else if (candidate.kind === "frame" && typeof candidate.object === "string" &&
      Object.hasOwn(layout.objects, candidate.object) && typeof candidate.name === "string") {
    const object = layout.objects[candidate.object];
    if (objectFrameNames(layout.types[object.type], object).includes(candidate.name)) {
      selection = {kind: "frame", object: candidate.object, name: candidate.name};
    }
  }
  if (selection && restoredScope.kind !== "layout" && !(
    (selection.kind === restoredScope.kind && selection.name === restoredScope.name) ||
    (selection.kind === "frame" && restoredScope.kind === "object" && selection.object === restoredScope.name)
  )) selection = null;
  return {
    version: 1,
    editor: {selectedCurve, selectedObject, selectedType, selectedTypeFrame, selection,
      segmentPage: Number.isInteger(editor.segmentPage)
        ? Math.max(0, Math.min(editor.segmentPage as number, Math.max(0, Math.ceil(segments / SEGMENT_PAGE_SIZE) - 1))) : 0},
    cards: {
      curves: boolean(cards.curves, true), types: boolean(cards.types, true),
      objects: boolean(cards.objects, true), viewer: boolean(cards.viewer, true),
      dependencies: boolean(cards.dependencies, Object.keys(layout.objects).length < 20000),
      segments: boolean(cards.segments, segments <= LARGE_SEGMENT_COUNT),
      typeFrames: boolean(cards.typeFrames, Object.keys(frames).length <= LARGE_FRAME_COUNT),
    },
    scope: restoredScope,
    viewport: {
      camera: camera(viewport.camera), pastViews: views(viewport.pastViews), futureViews: views(viewport.futureViews),
      mode: viewport.mode === "pan" || viewport.mode === "select" || viewport.mode === "zoom-region" ? viewport.mode : "orbit",
      showCurves: boolean(viewport.showCurves, true), showObjects: boolean(viewport.showObjects, true),
      showFrames: boolean(viewport.showFrames, false), showMechanicalAxis: boolean(viewport.showMechanicalAxis, false),
      showMagneticAxis: boolean(viewport.showMagneticAxis, false), showBeamAxis: boolean(viewport.showBeamAxis, false),
    },
    dependencies: {
      selection: typeof dependency.selection === "string" ? dependency.selection : null,
      expanded: Array.isArray(dependency.expanded) ? dependency.expanded.filter((v): v is string => typeof v === "string") : [],
      pages: Object.fromEntries(Object.entries(record(dependency.pages)).filter((entry): entry is [string, number] => Number.isSafeInteger(entry[1]) && (entry[1] as number) >= 0)),
    },
  };
}
