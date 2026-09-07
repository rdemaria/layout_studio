"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from "react";
import {
  ChevronDown,
  Focus,
  MousePointer2,
  Move,
  RotateCcw,
  ScanSearch,
  View,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Switch } from "@/components/ui/switch";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import type {
  FeatureBoundaryFrameName,
  Frame,
  LayoutData,
  SelectedEntity,
  Vec3,
} from "./layout-data";
import {
  add,
  buildScene,
  closestTransverseCurvePathForPoint,
  curveObjectSurfaceIntersectionPaths,
  cross,
  curvePlaneIntersectionPaths,
  curveSegmentIndexAtPath,
  dot,
  frameAtCurvePath,
  length,
  normalize,
  scale,
  sub,
  transverseCurvePathsForPoint,
  type CurveGeometry,
  type CurveSample,
  type FeatureAxisGeometry,
  type FeatureBoundaryFrameGeometry,
  type SceneGeometry,
  type SceneScope,
} from "./layout-geometry";

type NavigationMode = "orbit" | "pan" | "select" | "zoom-region";
type Camera = { azimuth: number; elevation: number; distance: number; target: Vec3 };
type Projection = { x: number; y: number; depth: number; scale: number };
type Projector = (point: Vec3) => Projection | null;
export type CanonicalView = "+x" | "-x" | "+y" | "-y" | "+z" | "-z";
const DEFAULT_SCENE_SCOPE: SceneScope = { kind: "layout" };
export type ScreenRectangle = {
  startX: number;
  startY: number;
  endX: number;
  endY: number;
};

export type ViewportFitRequest = {
  id: number;
  kind: "curve" | "object";
  name: string;
};

export type ViewportCommand =
  | {
      id: number;
      command: "fit";
      target:
        | { kind: "layout" }
        | { kind: "curve" | "object"; name: string };
    }
  | { id: number; command: "set_mode"; mode: NavigationMode }
  | { id: number; command: "set_view"; view: CanonicalView }
  | {
      id: number;
      command: "set_visibility";
      visibility: {
        curves?: boolean;
        objects?: boolean;
        frames?: boolean;
        magnetic_axis?: boolean;
        beam_axis?: boolean;
      };
    };

export type ViewportCommandApplied = (
  id: number,
  error?: string,
) => void;

export function viewportCommandRenderError(
  command: ViewportCommand,
  geometryError: string,
): string | undefined {
  return command.command === "set_visibility" && geometryError
    ? `Cannot render viewport: ${geometryError}`
    : undefined;
}

type HoverTarget =
  | {
      kind: "curve";
      name: string;
      sample: CurveSample;
      segmentIndex: number;
      x: number;
      y: number;
      snappedTo?: string;
    }
  | {
      kind: "object";
      name: string;
      x: number;
      y: number;
      ax: number;
      ay: number;
      bx: number;
      by: number;
      radius: number;
    }
  | { kind: "frame"; object: string; name: string; x: number; y: number }
  | {
      kind: "feature_frame";
      feature: "magnetic" | "beam";
      object: string;
      name: FeatureBoundaryFrameName;
      frame: Frame;
      x: number;
      y: number;
      polygon: { x: number; y: number }[];
    }
  | {
      kind: "feature_axis";
      feature: "magnetic" | "beam";
      object: string;
      sample: CurveSample;
      x: number;
      y: number;
    }
  | null;
type CurveHitTarget = {
  kind: "curve_hit";
  name: string;
  ax: number;
  ay: number;
  bx: number;
  by: number;
  startDepth: number;
  endDepth: number;
  startPath: number;
  endPath: number;
  segmentIndex: number;
};
type FeatureAxisHitTarget = {
  kind: "feature_axis_hit";
  feature: "magnetic" | "beam";
  object: string;
  ax: number;
  ay: number;
  bx: number;
  by: number;
  startSample: CurveSample;
  endSample: CurveSample;
};
type HitTarget = Exclude<
  Exclude<HoverTarget, null>,
  { kind: "curve" | "feature_axis" }
> | CurveHitTarget | FeatureAxisHitTarget;
type FrameHitTarget = Extract<HitTarget, { kind: "frame" }>;
type FeatureFrameHitTarget = Extract<HitTarget, { kind: "feature_frame" }>;
type ObjectHitTarget = Extract<HitTarget, { kind: "object" }>;

type PoseReadout = { label: string; frame: Frame };
type CurveStationSource =
  | { kind: "frame"; object: string; name: string; label: string }
  | {
      kind: "plane";
      feature: "magnetic" | "beam";
      object: string;
      name: FeatureBoundaryFrameName;
      label: string;
    }
  | { kind: "surface"; object: string; name: "shape"; label: string }
  | {
      kind: "segment";
      segmentIndex: number;
      boundary: "start" | "end";
      label: string;
    };
type CurveStation = {
  path: number;
  frame: Frame;
  sources: CurveStationSource[];
};
type CurveProbe = {
  curve: string;
  sample: CurveSample;
  sources: CurveStationSource[];
};

export function syncCanvasDimensions(
  canvas: Pick<HTMLCanvasElement, "width" | "height" | "style">,
  width: number,
  height: number,
  ratio: number,
): void {
  const pixelWidth = Math.floor(width * ratio);
  const pixelHeight = Math.floor(height * ratio);
  if (canvas.width !== pixelWidth) canvas.width = pixelWidth;
  if (canvas.height !== pixelHeight) canvas.height = pixelHeight;
  const cssWidth = `${width}px`;
  const cssHeight = `${height}px`;
  if (canvas.style.width !== cssWidth) canvas.style.width = cssWidth;
  if (canvas.style.height !== cssHeight) canvas.style.height = cssHeight;
}

export function traceProjectedPolyline(
  context: Pick<CanvasRenderingContext2D, "moveTo" | "lineTo">,
  points: ({ x: number; y: number } | null)[],
): void {
  let started = false;
  for (const point of points) {
    if (!point) {
      started = false;
      continue;
    }
    if (started) context.lineTo(point.x, point.y);
    else context.moveTo(point.x, point.y);
    started = true;
  }
}

export function viewportRelativeArrowLength(
  projectedScale: number,
  width: number,
  height: number,
): number {
  if (
    !Number.isFinite(projectedScale) || projectedScale <= 0 ||
    !Number.isFinite(width) || width <= 0 ||
    !Number.isFinite(height) || height <= 0
  ) return 0;
  const desiredPixels = Math.max(24, Math.min(64, Math.min(width, height) * 0.075));
  return desiredPixels / projectedScale;
}

export function toggleViewerSelection(
  current: SelectedEntity,
  candidate: SelectedEntity,
): SelectedEntity {
  if (!candidate) return null;
  if (!current || current.kind !== candidate.kind) return candidate;
  if (candidate.kind === "frame" && current.kind === "frame") {
    return current.object === candidate.object && current.name === candidate.name
      ? null
      : candidate;
  }
  if (candidate.kind === "curve" && current.kind === "curve") {
    return current.name === candidate.name &&
        current.segmentIndex === candidate.segmentIndex
      ? null
      : candidate;
  }
  if (candidate.kind === "object" && current.kind === "object") {
    return current.name === candidate.name ? null : candidate;
  }
  return candidate;
}

const EMPTY_SCENE: SceneGeometry = {
  curves: [],
  objects: [],
  frames: [],
  magneticAxes: [],
  magneticFrames: [],
  beamAxes: [],
  beamFrames: [],
  bounds: { min: [-1, -1, -1], max: [1, 1, 1] },
};

export function sceneBoundsForVisibility(
  scene: SceneGeometry,
  visibility: {
    curves: boolean;
    objects: boolean;
    frames: boolean;
    magneticAxis: boolean;
    beamAxis: boolean;
  },
): { min: Vec3; max: Vec3 } | null {
  let min: Vec3 | null = null;
  let max: Vec3 | null = null;
  const include = (point: Vec3) => {
    if (!point.every(Number.isFinite)) return;
    if (!min || !max) {
      min = [...point];
      max = [...point];
      return;
    }
    for (let axis = 0; axis < 3; axis += 1) {
      min[axis] = Math.min(min[axis], point[axis]);
      max[axis] = Math.max(max[axis], point[axis]);
    }
  };

  if (visibility.curves) {
    for (const curve of scene.curves) {
      for (const sample of curve.samples) include(sample.p);
    }
  }
  if (visibility.objects) {
    for (const object of scene.objects) {
      if (object.vertices.length) {
        for (const vertex of object.vertices) include(vertex);
      } else {
        include(object.frame.o);
      }
    }
  }
  const includeFeature = (
    axes: FeatureAxisGeometry[],
    frames: FeatureBoundaryFrameGeometry[],
  ) => {
    for (const axis of axes) {
      for (const sample of axis.samples) include(sample.p);
    }
    for (const frame of frames) {
      for (const vertex of frame.vertices) include(vertex);
    }
  };
  if (visibility.magneticAxis) {
    includeFeature(scene.magneticAxes, scene.magneticFrames);
  }
  if (visibility.beamAxis) includeFeature(scene.beamAxes, scene.beamFrames);
  if (visibility.frames) {
    for (const frame of scene.frames) include(frame.frame.o);
  }
  return min && max ? { min, max } : null;
}

function sameHoverTarget(a: HoverTarget, b: HoverTarget): boolean {
  if (!a || !b) return a === b;
  if (a.kind !== b.kind) return false;
  if (a.kind === "frame" && b.kind === "frame") {
    return a.object === b.object && a.name === b.name;
  }
  if (a.kind === "curve" && b.kind === "curve") {
    return a.name === b.name &&
      a.segmentIndex === b.segmentIndex &&
      a.sample.path === b.sample.path &&
      a.snappedTo === b.snappedTo;
  }
  if (a.kind === "feature_frame" && b.kind === "feature_frame") {
    return a.feature === b.feature && a.object === b.object && a.name === b.name;
  }
  if (a.kind === "feature_axis" && b.kind === "feature_axis") {
    return a.feature === b.feature && a.object === b.object &&
      a.sample.path === b.sample.path;
  }
  return a.kind === "object" && b.kind === "object" && a.name === b.name;
}

function distanceToSegment(
  x: number,
  y: number,
  ax: number,
  ay: number,
  bx: number,
  by: number,
): number {
  return closestPointOnSegment(x, y, ax, ay, bx, by).distance;
}

function closestPointOnSegment(
  x: number,
  y: number,
  ax: number,
  ay: number,
  bx: number,
  by: number,
) {
  const dx = bx - ax;
  const dy = by - ay;
  const denominator = dx * dx + dy * dy;
  const fraction = denominator > 1e-12
    ? Math.max(0, Math.min(1, ((x - ax) * dx + (y - ay) * dy) / denominator))
    : 0;
  const closestX = ax + fraction * dx;
  const closestY = ay + fraction * dy;
  return {
    distance: Math.hypot(x - closestX, y - closestY),
    fraction,
    x: closestX,
    y: closestY,
  };
}

function perspectiveCorrectFraction(
  screenFraction: number,
  startDepth: number,
  endDepth: number,
): number {
  const denominator =
    (1 - screenFraction) * endDepth + screenFraction * startDepth;
  return denominator > 1e-12
    ? screenFraction * startDepth / denominator
    : screenFraction;
}

function pointInPolygon(
  x: number,
  y: number,
  polygon: { x: number; y: number }[],
): boolean {
  let inside = false;
  for (let index = 0, previous = polygon.length - 1; index < polygon.length; previous = index++) {
    const a = polygon[index];
    const b = polygon[previous];
    const crosses = (a.y > y) !== (b.y > y) &&
      x < ((b.x - a.x) * (y - a.y)) / (b.y - a.y) + a.x;
    if (crosses) inside = !inside;
  }
  return inside;
}

function distanceToPolygon(
  x: number,
  y: number,
  polygon: { x: number; y: number }[],
): number {
  if (pointInPolygon(x, y, polygon)) return 0;
  let closest = Number.POSITIVE_INFINITY;
  for (let index = 0; index < polygon.length; index += 1) {
    const a = polygon[index];
    const b = polygon[(index + 1) % polygon.length];
    closest = Math.min(closest, distanceToSegment(x, y, a.x, a.y, b.x, b.y));
  }
  return closest;
}

function pointInsideFeaturePlane(
  point: Vec3,
  featureFrame: FeatureBoundaryFrameGeometry,
): boolean {
  const localPoint = sub(point, featureFrame.frame.o);
  const x = dot(localPoint, featureFrame.frame.x);
  const y = dot(localPoint, featureFrame.frame.y);
  const polygon = featureFrame.vertices.map((vertex) => {
    const local = sub(vertex, featureFrame.frame.o);
    return {
      x: dot(local, featureFrame.frame.x),
      y: dot(local, featureFrame.frame.y),
    };
  });
  const extent = Math.max(
    1,
    ...polygon.map((vertex) => Math.hypot(vertex.x, vertex.y)),
  );
  return pointInPolygon(x, y, polygon) ||
    distanceToPolygon(x, y, polygon) <= extent * 1e-8;
}

function objectCurveAffiliation(
  layout: LayoutData,
  objectName: string,
  cache: Map<string, string | null>,
  stack: Set<string> = new Set(),
): string | null {
  if (cache.has(objectName)) return cache.get(objectName) ?? null;
  if (stack.has(objectName)) return null;
  const object = layout.objects[objectName];
  if (!object) return null;
  const nextStack = new Set(stack).add(objectName);
  let curve: string | null = null;
  if (object.position.reference.kind === "curve") {
    curve = object.position.reference.curve;
  } else if (object.position.reference_curve) {
    curve = object.position.reference_curve;
  } else if (object.position.reference.kind === "object_frame") {
    curve = objectCurveAffiliation(
      layout,
      object.position.reference.object,
      cache,
      nextStack,
    );
  }
  cache.set(objectName, curve);
  return curve;
}

function stationSourceLabel(sources: CurveStationSource[]): string {
  if (!sources.length) return "";
  if (sources.length === 1) return sources[0].label;
  return `${sources[0].label} +${sources.length - 1}`;
}

function stationSourceKey(source: CurveStationSource): string {
  return source.kind === "segment"
    ? `${source.kind}:${source.segmentIndex}:${source.boundary}`
    : `${source.kind}:${source.object}:${source.name}`;
}

function lowerBoundStation(stations: CurveStation[], path: number): number {
  let low = 0;
  let high = stations.length;
  while (low < high) {
    const middle = Math.floor((low + high) / 2);
    if (stations[middle].path < path) low = middle + 1;
    else high = middle;
  }
  return low;
}

function frameToMadxAngles(frame: Frame) {
  // MAD-X SURVEY convention: theta is horizontal azimuth, phi is vertical
  // elevation, and psi is roll around the local s axis.
  const s = normalize(frame.s);
  const x = normalize(sub(frame.x, scale(s, dot(frame.x, s))));
  const y = normalize(cross(s, x));
  const horizontal = Math.hypot(s[0], s[2]);
  const phi = Math.atan2(s[1], horizontal);
  const theta = horizontal > 1e-10 ? Math.atan2(s[0], s[2]) : 0;
  const psi =
    horizontal > 1e-10
      ? Math.atan2(x[1], y[1])
      : Math.atan2(-y[0], x[0]);
  return { theta, phi, psi };
}

function cleanFixed(value: number, digits: number) {
  const threshold = 0.5 * 10 ** -digits;
  return (Math.abs(value) < threshold ? 0 : value).toFixed(digits);
}

function cameraOrientation(
  camera: Pick<Camera, "azimuth" | "elevation">,
) {
  const cosElevation = Math.cos(camera.elevation);
  const eyeDirection: Vec3 = [
    Math.sin(camera.azimuth) * cosElevation,
    Math.sin(camera.elevation),
    Math.cos(camera.azimuth) * cosElevation,
  ];
  const forward = normalize(scale(eyeDirection, -1));
  let right = normalize(cross(forward, [0, 1, 0]));
  if (length(right) < 0.01) right = [1, 0, 0];
  const up = normalize(cross(right, forward));
  return { eyeDirection, forward, right, up };
}

function minimumCameraDistance(target: Vec3): number {
  const absoluteScale = Math.max(1, ...target.map(Math.abs));
  return Math.max(1e-9, absoluteScale * Number.EPSILON * 64);
}

const CANONICAL_POLE_EPSILON = 1e-6;

const CANONICAL_VIEWS: { value: CanonicalView; label: string }[] = [
  { value: "+x", label: "View from +X" },
  { value: "-x", label: "View from −X" },
  { value: "+y", label: "View from +Y" },
  { value: "-y", label: "View from −Y" },
  { value: "+z", label: "View from +Z" },
  { value: "-z", label: "View from −Z" },
];

export function cameraForCanonicalView(
  camera: Camera,
  view: CanonicalView,
): Camera {
  const orientation: Record<CanonicalView, [number, number]> = {
    "+x": [Math.PI / 2, 0],
    "-x": [-Math.PI / 2, 0],
    "+y": [0, Math.PI / 2 - CANONICAL_POLE_EPSILON],
    "-y": [0, -Math.PI / 2 + CANONICAL_POLE_EPSILON],
    "+z": [0, 0],
    "-z": [Math.PI, 0],
  };
  const [azimuth, elevation] = orientation[view];
  return { ...camera, azimuth, elevation };
}

export function zoomCameraToRectangle(
  camera: Camera,
  rectangle: ScreenRectangle,
  width: number,
  height: number,
): Camera {
  const safeWidth = Math.max(1, width);
  const safeHeight = Math.max(1, height);
  const left = Math.max(0, Math.min(safeWidth, rectangle.startX, rectangle.endX));
  const rightEdge = Math.max(0, Math.min(safeWidth, Math.max(rectangle.startX, rectangle.endX)));
  const top = Math.max(0, Math.min(safeHeight, rectangle.startY, rectangle.endY));
  const bottom = Math.max(0, Math.min(safeHeight, Math.max(rectangle.startY, rectangle.endY)));
  const rectangleWidth = rightEdge - left;
  const rectangleHeight = bottom - top;
  if (rectangleWidth <= 0 || rectangleHeight <= 0) return camera;

  const focal = Math.min(safeWidth, safeHeight) * 0.92;
  const centerX = (left + rightEdge) / 2;
  const centerY = (top + bottom) / 2;
  const { right, up } = cameraOrientation(camera);
  const target = add(
    camera.target,
    add(
      scale(right, (centerX - safeWidth / 2) * camera.distance / focal),
      scale(up, -(centerY - safeHeight / 2) * camera.distance / focal),
    ),
  );
  const scaleFactor = Math.max(
    rectangleWidth / safeWidth,
    rectangleHeight / safeHeight,
  );
  return {
    ...camera,
    target,
    distance: Math.max(
      minimumCameraDistance(target),
      camera.distance * scaleFactor,
    ),
  };
}

export function zoomedCameraDistance(
  distance: number,
  deltaY: number,
  target: Vec3,
): number {
  const exponent = Math.max(-5, Math.min(5, deltaY * 0.0012));
  const next = distance * Math.exp(exponent);
  return Number.isFinite(next)
    ? Math.max(minimumCameraDistance(target), next)
    : distance;
}

function boundsCorners(bounds: { min: Vec3; max: Vec3 }): Vec3[] {
  const corners: Vec3[] = [];
  for (const x of [bounds.min[0], bounds.max[0]]) {
    for (const y of [bounds.min[1], bounds.max[1]]) {
      for (const z of [bounds.min[2], bounds.max[2]]) {
        corners.push([x, y, z]);
      }
    }
  }
  return corners;
}

export function fitCameraToPoints(
  camera: Camera,
  points: Vec3[],
  width: number,
  height: number,
): Camera {
  const finitePoints = points.filter((point) => point.every(Number.isFinite));
  if (!finitePoints.length) return camera;

  const min: Vec3 = [...finitePoints[0]];
  const max: Vec3 = [...finitePoints[0]];
  for (const point of finitePoints.slice(1)) {
    for (let axis = 0; axis < 3; axis += 1) {
      min[axis] = Math.min(min[axis], point[axis]);
      max[axis] = Math.max(max[axis], point[axis]);
    }
  }

  const target = add(min, scale(sub(max, min), 0.5));
  const { forward, right, up } = cameraOrientation(camera);
  const safeWidth = Math.max(1, width);
  const safeHeight = Math.max(1, height);
  const focal = Math.min(safeWidth, safeHeight) * 0.92;
  const halfWidth = safeWidth * 0.42;
  const halfHeight = safeHeight * 0.42;
  const span = length(sub(max, min));
  const precisionFloor = minimumCameraDistance(target);
  const depthMargin = Math.max(precisionFloor, span * 1e-6);
  let distance = precisionFloor;

  for (const point of finitePoints) {
    const offset = sub(point, target);
    const alongView = dot(offset, forward);
    distance = Math.max(
      distance,
      Math.abs(dot(offset, right)) * focal / halfWidth - alongView,
      Math.abs(dot(offset, up)) * focal / halfHeight - alongView,
      depthMargin - alongView,
    );
  }

  return {
    ...camera,
    target,
    distance: Math.max(precisionFloor, distance),
  };
}

export function worldAxisMarkerProjection(
  camera: Pick<Camera, "azimuth" | "elevation">,
) {
  const { forward, right, up } = cameraOrientation(camera);
  const origin = { x: 38, y: 38 };
  const axisLength = 23;
  const axes: { label: "X" | "Y" | "Z"; color: string; vector: Vec3 }[] = [
    { label: "X", color: "#ff7185", vector: [1, 0, 0] },
    { label: "Y", color: "#83e28d", vector: [0, 1, 0] },
    { label: "Z", color: "#66c7ff", vector: [0, 0, 1] },
  ];
  return {
    origin,
    axes: axes
      .map((axis) => ({
        ...axis,
        x: origin.x + axisLength * dot(axis.vector, right),
        y: origin.y - axisLength * dot(axis.vector, up),
        depth: dot(axis.vector, forward),
      }))
      .sort((a, b) => b.depth - a.depth),
  };
}

function cameraProjector(camera: Camera, width: number, height: number): Projector {
  const { forward, right, up } = cameraOrientation(camera);
  const focal = Math.min(width, height) * 0.92;
  const nearDepth = Math.max(
    minimumCameraDistance(camera.target) * 0.25,
    camera.distance * 1e-6,
  );
  return (point: Vec3) => {
    // Work relative to the camera target so a millimetre-scale detail remains
    // resolvable even when the layout uses large world coordinates.
    const offset = sub(point, camera.target);
    const depth = camera.distance + dot(offset, forward);
    if (depth <= nearDepth) return null;
    return {
      x: width / 2 + (dot(offset, right) * focal) / depth,
      y: height / 2 - (dot(offset, up) * focal) / depth,
      depth,
      scale: focal / depth,
    };
  };
}

function rgba(hex: string, alpha: number): string {
  const normalized = /^#[0-9a-f]{6}$/i.test(hex) ? hex.slice(1) : "f0a84b";
  const value = Number.parseInt(normalized, 16);
  return `rgba(${(value >> 16) & 255}, ${(value >> 8) & 255}, ${value & 255}, ${alpha})`;
}

function ToolButton({
  active = false,
  label,
  onClick,
  children,
}: {
  active?: boolean;
  label: string;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          type="button"
          variant={active ? "default" : "ghost"}
          size="icon-sm"
          aria-label={label}
          onClick={onClick}
        >
          {children}
        </Button>
      </TooltipTrigger>
      <TooltipContent side="bottom">{label}</TooltipContent>
    </Tooltip>
  );
}

export function LayoutViewport({
  layout,
  selection,
  onSelect,
  fitRequest = null,
  command = null,
  onCommandApplied,
  scope = DEFAULT_SCENE_SCOPE,
}: {
  layout: LayoutData;
  selection: SelectedEntity;
  onSelect: (selection: SelectedEntity) => void;
  fitRequest?: ViewportFitRequest | null;
  command?: ViewportCommand | null;
  onCommandApplied?: ViewportCommandApplied;
  scope?: SceneScope;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const overlayRef = useRef<HTMLCanvasElement>(null);
  const wrapperRef = useRef<HTMLDivElement>(null);
  const hitTargetsRef = useRef<HitTarget[]>([]);
  const dragRef = useRef<{
    startX: number;
    startY: number;
    x: number;
    y: number;
    localStartX: number;
    localStartY: number;
    button: number;
    moved: boolean;
    zooming: boolean;
  } | null>(null);
  const fittedOnceRef = useRef(false);
  const handledFitRequestRef = useRef(0);
  const handledCommandRef = useRef(0);
  const reportedCommandRef = useRef(0);
  const handledScopeRef = useRef(
    scope.kind === "layout" ? "layout" : `${scope.kind}:${scope.name}`,
  );
  const sceneResult = useMemo(() => {
    try {
      return { scene: buildScene(layout, scope), error: "" };
    } catch (error) {
      return {
        scene: EMPTY_SCENE,
        error: error instanceof Error ? error.message : "Unknown geometry error",
      };
    }
  }, [layout, scope]);
  const { scene } = sceneResult;
  const geometryError = sceneResult.error;
  const [mode, setMode] = useState<NavigationMode>("orbit");
  const [hovered, setHovered] = useState<HoverTarget>(null);
  const [showCurves, setShowCurves] = useState(true);
  const [showObjects, setShowObjects] = useState(true);
  const [showFrames, setShowFrames] = useState(false);
  const [showMagneticAxis, setShowMagneticAxis] = useState(false);
  const [showBeamAxis, setShowBeamAxis] = useState(false);
  const [curveProbe, setCurveProbe] = useState<CurveProbe | null>(null);
  const [zoomRectangle, setZoomRectangle] =
    useState<ScreenRectangle | null>(null);
  const [commandResult, setCommandResult] = useState<{
    id: number;
    error?: string;
  } | null>(null);
  const [camera, setCamera] = useState<Camera>({
    azimuth: -0.68,
    elevation: 0.42,
    distance: 20,
    target: [0, 0, 4],
  });
  const [size, setSize] = useState({ width: 900, height: 650 });
  const worldAxes = useMemo(
    () => worldAxisMarkerProjection({
      azimuth: camera.azimuth,
      elevation: camera.elevation,
    }),
    [camera.azimuth, camera.elevation],
  );
  const hoverStyleKey = !hovered
    ? ""
    : hovered.kind === "curve" || hovered.kind === "object"
      ? `${hovered.kind}:${hovered.name}`
      : hovered.kind === "feature_axis"
        ? `${hovered.kind}:${hovered.feature}:${hovered.object}`
      : `${hovered.kind}:${hovered.object}:${hovered.name}`;

  const selectedCurve = useMemo<CurveGeometry | null>(() => {
    if (selection?.kind !== "curve") return null;
    return scene.curves.find((curve) => curve.name === selection.name) ?? null;
  }, [scene.curves, selection]);

  const selectedCurveStations = useMemo<CurveStation[]>(() => {
    if (!selectedCurve || geometryError) return [];
    const pathTolerance = 1e-9 * Math.max(1, selectedCurve.totalLength);
    const worldScale = Math.max(
      1,
      selectedCurve.totalLength,
      ...scene.bounds.min.map(Math.abs),
      ...scene.bounds.max.map(Math.abs),
    );
    const onCurveTolerance = Math.max(1e-6, 1e-10 * worldScale);
    const stations: CurveStation[] = [];
    const affiliationCache = new Map<string, string | null>();

    const addStation = (path: number, source: CurveStationSource) => {
      stations.push({
        path,
        frame: frameAtCurvePath(selectedCurve, path),
        sources: [source],
      });
    };

    const addFrameStation = (
      frameOrigin: Vec3,
      object: string,
      name: string,
      label: string,
    ) => {
      const affiliation = objectCurveAffiliation(
        layout,
        object,
        affiliationCache,
      );
      if (affiliation && affiliation !== selectedCurve.name) return;
      const solutions = transverseCurvePathsForPoint(selectedCurve, frameOrigin);
      const paths = affiliation === selectedCurve.name
        ? (() => {
            const closest = closestTransverseCurvePathForPoint(
              selectedCurve,
              frameOrigin,
            );
            return closest.kind === "unique" && closest.path !== undefined
              ? [closest.path]
              : [];
          })()
        : solutions.paths.filter((path) =>
            length(sub(frameOrigin, frameAtCurvePath(selectedCurve, path).o)) <=
              onCurveTolerance
          );
      for (const path of paths) {
        addStation(path, { kind: "frame", object, name, label });
      }
    };

    for (const [segmentIndex, segment] of selectedCurve.segments.entries()) {
      addStation(segment.path, {
        kind: "segment",
        segmentIndex,
        boundary: "start",
        label: `Segment ${segmentIndex + 1} start`,
      });
      addStation(segment.path + segment.length, {
        kind: "segment",
        segmentIndex,
        boundary: "end",
        label: `Segment ${segmentIndex + 1} end`,
      });
    }

    if (showFrames) {
      for (const namedFrame of scene.frames) {
        addFrameStation(
          namedFrame.frame.o,
          namedFrame.object,
          namedFrame.name,
          `${namedFrame.object}.${namedFrame.name}`,
        );
      }
    }

    if (showObjects) {
      for (const object of scene.objects) {
        addFrameStation(
          object.frame.o,
          object.name,
          "center",
          `${object.name}.center`,
        );
      }
      const surfacePaths = curveObjectSurfaceIntersectionPaths(
        selectedCurve,
        scene.objects,
      );
      for (const [object, paths] of surfacePaths) {
        for (const path of paths) {
          addStation(path, {
            kind: "surface",
            object,
            name: "shape",
            label: `${object} shape surface`,
          });
        }
      }
    }

    const addFeaturePlaneStations = (
      feature: "magnetic" | "beam",
      featureFrames: FeatureBoundaryFrameGeometry[],
    ) => {
      for (const featureFrame of featureFrames) {
        if (
          objectCurveAffiliation(
            layout,
            featureFrame.object,
            affiliationCache,
          ) !== selectedCurve.name
        ) {
          continue;
        }
        const intersections = curvePlaneIntersectionPaths(
          selectedCurve,
          featureFrame.frame,
        );
        if (intersections.kind === "none" || intersections.kind === "infinite") {
          continue;
        }
        const paths = intersections.paths.filter((path) => {
          const curveFrame = frameAtCurvePath(selectedCurve, path);
          return pointInsideFeaturePlane(curveFrame.o, featureFrame) &&
            length(cross(
              normalize(featureFrame.frame.s),
              normalize(curveFrame.s),
            )) <= 1e-6;
        });
        if (paths.length === 1) {
          const boundary = featureFrame.name.endsWith("_entry")
            ? "entry"
            : "exit";
          addStation(paths[0], {
            kind: "plane",
            feature,
            object: featureFrame.object,
            name: featureFrame.name,
            label: `${featureFrame.object} ${feature === "magnetic" ? "magnetic" : "beam"} ${boundary} plane`,
          });
        }
      }
    };
    if (showMagneticAxis) {
      addFeaturePlaneStations("magnetic", scene.magneticFrames);
    }
    if (showBeamAxis) addFeaturePlaneStations("beam", scene.beamFrames);

    stations.sort((a, b) => a.path - b.path);
    const grouped: CurveStation[] = [];
    for (const station of stations) {
      const previous = grouped[grouped.length - 1];
      if (!previous || Math.abs(previous.path - station.path) > pathTolerance) {
        grouped.push(station);
        continue;
      }
      for (const source of station.sources) {
        if (
          !previous.sources.some((candidate) =>
            stationSourceKey(candidate) === stationSourceKey(source)
          )
        ) {
          previous.sources.push(source);
        }
      }
    }
    return grouped;
  }, [
    geometryError,
    layout,
    scene,
    selectedCurve,
    showBeamAxis,
    showFrames,
    showMagneticAxis,
    showObjects,
  ]);

  const activeCurveProbe = useMemo<CurveProbe | null>(() => {
    if (!selectedCurve || geometryError) return null;
    const retainsProbe = curveProbe?.curve === selectedCurve.name;
    const path = retainsProbe
      ? Math.max(0, Math.min(selectedCurve.totalLength, curveProbe.sample.path))
      : 0;
    const station = selectedCurveStations.find(
      (candidate) =>
        Math.abs(candidate.path - path) <=
        1e-9 * Math.max(1, selectedCurve.totalLength),
    );
    const frame = frameAtCurvePath(selectedCurve, path);
    return {
      curve: selectedCurve.name,
      sample: { p: frame.o, frame, path },
      sources: station?.sources ?? [],
    };
  }, [curveProbe, geometryError, selectedCurve, selectedCurveStations]);

  const fitPoints = useCallback((points: Vec3[]) => {
    const rect = wrapperRef.current?.getBoundingClientRect();
    const width = rect?.width || size.width;
    const height = rect?.height || size.height;
    setCamera((current) =>
      fitCameraToPoints(current, points, width, height)
    );
  }, [size.height, size.width]);

  const visibleBounds = useMemo(
    () => sceneBoundsForVisibility(scene, {
      curves: showCurves,
      objects: showObjects,
      frames: showFrames,
      magneticAxis: showMagneticAxis,
      beamAxis: showBeamAxis,
    }),
    [
      scene,
      showBeamAxis,
      showCurves,
      showFrames,
      showMagneticAxis,
      showObjects,
    ],
  );

  const fit = useCallback(() => {
    setZoomRectangle(null);
    fitPoints(visibleBounds ? boundsCorners(visibleBounds) : []);
  }, [fitPoints, visibleBounds]);

  useEffect(() => {
    if (!fittedOnceRef.current) {
      fittedOnceRef.current = true;
      fit();
    }
  }, [fit]);

  /* eslint-disable react-hooks/set-state-in-effect -- The command prop is an
     external command stream. Applying a committed command here is the
     synchronization boundary, and the follow-up effect acknowledges its
     resulting render. */
  useEffect(() => {
    const scopeKey = scope.kind === "layout"
      ? "layout"
      : `${scope.kind}:${scope.name}`;
    if (scopeKey === handledScopeRef.current) return;
    handledScopeRef.current = scopeKey;
    const timeout = window.setTimeout(fit, 0);
    return () => window.clearTimeout(timeout);
  }, [fit, scope]);

  useEffect(() => {
    if (
      !fitRequest ||
      fitRequest.id === handledFitRequestRef.current ||
      geometryError
    ) return;
    handledFitRequestRef.current = fitRequest.id;
    if (fitRequest.kind === "curve") {
      const curve = scene.curves.find(
        (candidate) => candidate.name === fitRequest.name,
      );
      if (!curve) return;
      fitPoints(curve.samples.map((sample) => sample.p));
      return;
    }
    const object = scene.objects.find(
      (candidate) => candidate.name === fitRequest.name,
    );
    if (!object) return;
    fitPoints(object.vertices.length ? object.vertices : [object.frame.o]);
  }, [fitPoints, fitRequest, geometryError, scene.curves, scene.objects]);

  useEffect(() => {
    const wrapper = wrapperRef.current;
    if (!wrapper) return;
    const observer = new ResizeObserver(([entry]) => {
      setSize({
        width: Math.max(320, entry.contentRect.width),
        height: Math.max(280, entry.contentRect.height),
      });
    });
    observer.observe(wrapper);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const wrapper = wrapperRef.current;
    if (!wrapper) return;
    const handleWheel = (event: WheelEvent) => {
      event.preventDefault();
      event.stopPropagation();
      setZoomRectangle(null);
      const deltaY = event.deltaMode === WheelEvent.DOM_DELTA_LINE
        ? event.deltaY * 16
        : event.deltaMode === WheelEvent.DOM_DELTA_PAGE
          ? event.deltaY * Math.max(1, wrapper.clientHeight)
          : event.deltaY;
      setCamera((current) => ({
        ...current,
        distance: zoomedCameraDistance(
          current.distance,
          deltaY,
          current.target,
        ),
      }));
    };
    wrapper.addEventListener("wheel", handleWheel, { passive: false });
    return () => wrapper.removeEventListener("wheel", handleWheel);
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ratio = Math.min(2, window.devicePixelRatio || 1);
    syncCanvasDimensions(canvas, size.width, size.height, ratio);
    const context = canvas.getContext("2d");
    if (!context) return;
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    const { width, height } = size;
    const project = cameraProjector(camera, width, height);
    context.clearRect(0, 0, width, height);

    const gradient = context.createRadialGradient(
      width * 0.55,
      height * 0.45,
      10,
      width * 0.5,
      height * 0.5,
      Math.max(width, height),
    );
    gradient.addColorStop(0, "#172235");
    gradient.addColorStop(1, "#070b12");
    context.fillStyle = gradient;
    context.fillRect(0, 0, width, height);

    const requestedGridStep = Math.max(camera.distance / 35, 1e-12);
    const gridPower = 10 ** Math.floor(Math.log10(requestedGridStep));
    const gridRatio = requestedGridStep / gridPower;
    const gridStep = gridPower * (gridRatio <= 1 ? 1 : gridRatio <= 2 ? 2 : gridRatio <= 5 ? 5 : 10);
    const gridExtent = Math.max(gridStep * 5, camera.distance * 0.9);
    const gridMinX = Math.floor((camera.target[0] - gridExtent) / gridStep) * gridStep;
    const gridMaxX = Math.ceil((camera.target[0] + gridExtent) / gridStep) * gridStep;
    const gridMinZ = Math.floor((camera.target[2] - gridExtent) / gridStep) * gridStep;
    const gridMaxZ = Math.ceil((camera.target[2] + gridExtent) / gridStep) * gridStep;
    context.lineWidth = 1;
    const drawGridLine = (value: number, axis: "x" | "z") => {
      const lines = axis === "x"
        ? [[project([value, 0, gridMinZ]), project([value, 0, gridMaxZ])]]
        : [[project([gridMinX, 0, value]), project([gridMaxX, 0, value])]];
      const gridIndex = Math.round(value / gridStep);
      context.strokeStyle =
        Math.abs(value) <= gridStep * 1e-8
          ? "rgba(127, 166, 191, .26)"
          : Math.abs(gridIndex) % 5 === 0
            ? "rgba(127, 166, 191, .13)"
            : "rgba(127, 166, 191, .055)";
      for (const [a, b] of lines) {
        if (!a || !b) continue;
        context.beginPath();
        context.moveTo(a.x, a.y);
        context.lineTo(b.x, b.y);
        context.stroke();
      }
    };
    for (let value = gridMinX; value <= gridMaxX + gridStep * 0.5; value += gridStep) {
      drawGridLine(value, "x");
    }
    for (let value = gridMinZ; value <= gridMaxZ + gridStep * 0.5; value += gridStep) {
      drawGridLine(value, "z");
    }

    type FaceDraw = {
      polygon: Projection[];
      depth: number;
      color: string;
      selected: boolean;
    };
    const objectProjections = showObjects
      ? scene.objects.map((object) => object.vertices.map(project))
      : [];
    const faces: FaceDraw[] = [];
    if (showObjects) {
      for (const [objectIndex, object] of scene.objects.entries()) {
        const projected = objectProjections[objectIndex];
        for (const face of object.faces) {
          const polygon = face
            .map((index) => projected[index])
            .filter(Boolean) as Projection[];
          if (polygon.length !== face.length) continue;
          faces.push({
            polygon,
            depth:
              polygon.reduce((sum, point) => sum + point.depth, 0) /
              polygon.length,
            color: object.type.color,
            selected:
              (selection?.kind === "object" && selection.name === object.name) ||
              (selection?.kind === "frame" && selection.object === object.name),
          });
        }
      }
    }
    faces.sort((a, b) => b.depth - a.depth);
    for (const face of faces) {
      context.beginPath();
      context.moveTo(face.polygon[0].x, face.polygon[0].y);
      for (const point of face.polygon.slice(1)) {
        context.lineTo(point.x, point.y);
      }
      context.closePath();
      context.fillStyle = rgba(face.color, face.selected ? 0.12 : 0.065);
      context.fill();
    }

    const hits: HitTarget[] = [];
    const drawFeature = (
      feature: "magnetic" | "beam",
      axes: FeatureAxisGeometry[],
      boundaryFrames: FeatureBoundaryFrameGeometry[],
    ) => {
      const axisColor = feature === "magnetic" ? "#ffd166" : "#66c7ff";
      for (const axis of axes) {
        const projected = axis.samples.map((sample) => project(sample.p));
        const active = selection?.kind === "object" && selection.name === axis.object;
        const hovering = hoverStyleKey ===
          `feature_axis:${feature}:${axis.object}`;
        context.save();
        context.setLineDash(feature === "magnetic" ? [8, 4] : [3, 3]);
        context.lineCap = "round";
        context.lineJoin = "round";
        context.beginPath();
        traceProjectedPolyline(context, projected);
        context.lineWidth = hovering ? 4.2 : active ? 3.6 : 2.6;
        context.strokeStyle = rgba(axisColor, hovering ? 1 : active ? 0.95 : 0.86);
        context.stroke();
        context.restore();
        for (let index = 1; index < projected.length; index += 1) {
          const a = projected[index - 1];
          const b = projected[index];
          if (!a || !b) continue;
          hits.push({
            kind: "feature_axis_hit",
            feature,
            object: axis.object,
            ax: a.x,
            ay: a.y,
            bx: b.x,
            by: b.y,
            startSample: axis.samples[index - 1],
            endSample: axis.samples[index],
          });
        }
      }

      for (const featureFrame of boundaryFrames) {
        const polygon = featureFrame.vertices
          .map(project)
          .filter(Boolean) as Projection[];
        if (polygon.length !== featureFrame.vertices.length) continue;
        const active = selection?.kind === "object" &&
          selection.name === featureFrame.object;
        const hovering = hoverStyleKey ===
          `feature_frame:${featureFrame.object}:${featureFrame.name}`;
        const isEntry = featureFrame.name.endsWith("_entry");
        const color = feature === "magnetic"
          ? (isEntry ? "#ffe29a" : "#f5a742")
          : (isEntry ? "#7ee7ff" : "#659cff");
        context.beginPath();
        context.moveTo(polygon[0].x, polygon[0].y);
        for (const point of polygon.slice(1)) context.lineTo(point.x, point.y);
        context.closePath();
        context.fillStyle = rgba(color, hovering ? 0.28 : active ? 0.2 : 0.14);
        context.fill();
        context.save();
        context.setLineDash(feature === "magnetic" ? [7, 4] : [3, 3]);
        context.lineWidth = hovering ? 2.2 : 1.25;
        context.strokeStyle = rgba(color, hovering ? 1 : 0.82);
        context.stroke();
        context.restore();
        const x = polygon.reduce((sum, point) => sum + point.x, 0) /
          polygon.length;
        const y = polygon.reduce((sum, point) => sum + point.y, 0) /
          polygon.length;
        context.font = "650 9px ui-monospace, SFMono-Regular, monospace";
        context.fillStyle = rgba(color, 0.95);
        context.fillText(isEntry ? "IN" : "OUT", x + 5, y - 5);
        hits.push({
          kind: "feature_frame",
          feature,
          object: featureFrame.object,
          name: featureFrame.name,
          frame: featureFrame.frame,
          x,
          y,
          polygon,
        });
      }
    };
    if (showMagneticAxis) {
      drawFeature("magnetic", scene.magneticAxes, scene.magneticFrames);
    }
    if (showBeamAxis) drawFeature("beam", scene.beamAxes, scene.beamFrames);

    if (showCurves) for (const curve of scene.curves) {
      const projected = curve.samples.map((sample) => project(sample.p));
      const active = selection?.kind === "curve" && selection.name === curve.name;
      const hovering = hoverStyleKey === `curve:${curve.name}`;
      const curveColor = layout.reference_curves[curve.name].color;
      context.lineCap = "round";
      context.lineJoin = "round";
      context.beginPath();
      traceProjectedPolyline(context, projected);
      context.strokeStyle = active
        ? "rgba(255, 190, 93, .32)"
        : rgba(curveColor, hovering ? 0.3 : 0.14);
      context.lineWidth = active ? 11 : hovering ? 10 : 8;
      context.stroke();
      context.strokeStyle = rgba(curveColor, 1);
      context.lineWidth = active ? 3.2 : hovering ? 2.8 : 2.2;
      context.stroke();
      const focusedSegment = active ? selection.segmentIndex : undefined;
      if (focusedSegment !== undefined) {
        context.beginPath();
        let focusedStarted = false;
        for (let index = 1; index < projected.length; index += 1) {
          const a = projected[index - 1];
          const b = projected[index];
          if (!a || !b) {
            focusedStarted = false;
            continue;
          }
          const segmentIndex = curveSegmentIndexAtPath(
            curve,
            (curve.samples[index - 1].path + curve.samples[index].path) / 2,
          );
          if (segmentIndex !== focusedSegment) {
            focusedStarted = false;
            continue;
          }
          if (!focusedStarted) context.moveTo(a.x, a.y);
          context.lineTo(b.x, b.y);
          focusedStarted = true;
        }
        context.strokeStyle = "rgba(255, 244, 213, .95)";
        context.lineWidth = 5.2;
        context.stroke();
        context.strokeStyle = rgba(curveColor, 1);
        context.lineWidth = 2.4;
        context.stroke();
      }
      for (let index = 1; index < projected.length; index += 1) {
        const a = projected[index - 1];
        const b = projected[index];
        if (!a || !b) continue;
        hits.push({
          kind: "curve_hit",
          name: curve.name,
          ax: a.x,
          ay: a.y,
          bx: b.x,
          by: b.y,
          startDepth: a.depth,
          endDepth: b.depth,
          startPath: curve.samples[index - 1].path,
          endPath: curve.samples[index].path,
          segmentIndex: curveSegmentIndexAtPath(
            curve,
            (curve.samples[index - 1].path + curve.samples[index].path) / 2,
          ),
        });
      }
    }

    if (showObjects) for (const [objectIndex, object] of scene.objects.entries()) {
      const projected = objectProjections[objectIndex];
      const active =
        (selection?.kind === "object" && selection.name === object.name) ||
        (selection?.kind === "frame" && selection.object === object.name);
      const hovering = hoverStyleKey === `object:${object.name}` ||
        hoverStyleKey.startsWith(`feature_frame:${object.name}:`) ||
        hoverStyleKey.endsWith(`:${object.name}`);
      context.lineWidth = active ? 2.5 : hovering ? 2.1 : 1.25;
      context.strokeStyle =
        active || hovering
          ? rgba(object.type.color, 1)
          : rgba(object.type.color, 0.76);
      context.beginPath();
      let hasVisibleEdge = false;
      for (const [aIndex, bIndex] of object.edges) {
        const a = projected[aIndex];
        const b = projected[bIndex];
        if (!a || !b) continue;
        context.moveTo(a.x, a.y);
        context.lineTo(b.x, b.y);
        hasVisibleEdge = true;
      }
      if (hasVisibleEdge) context.stroke();
      if (!projected.length) {
        const center = project(object.frame.o);
        if (center) {
          const radius = active || hovering ? 6 : 4.5;
          context.beginPath();
          context.moveTo(center.x, center.y - radius);
          context.lineTo(center.x + radius, center.y);
          context.lineTo(center.x, center.y + radius);
          context.lineTo(center.x - radius, center.y);
          context.closePath();
          context.fillStyle = rgba(object.type.color, active ? 0.92 : 0.72);
          context.fill();
          context.stroke();
          hits.push({
            kind: "object",
            name: object.name,
            x: center.x,
            y: center.y,
            ax: center.x,
            ay: center.y,
            bx: center.x,
            by: center.y,
            radius: radius + 3,
          });
        }
        continue;
      }
      const ringSize = object.type.shape?.[0] === "box" ? 4 : 18;
      const rings: ({ x: number; y: number; radius: number } | null)[] = [];
      for (let offset = 0; offset < projected.length; offset += ringSize) {
        const visible = projected.slice(offset, offset + ringSize).filter(Boolean) as Projection[];
        if (!visible.length) {
          rings.push(null);
          continue;
        }
        const x = visible.reduce((sum, point) => sum + point.x, 0) / visible.length;
        const y = visible.reduce((sum, point) => sum + point.y, 0) / visible.length;
        const radius = Math.max(
          5,
          Math.min(
            90,
            Math.max(...visible.map((point) => Math.hypot(point.x - x, point.y - y))),
          ),
        );
        rings.push({ x, y, radius });
      }
      for (let index = 1; index < rings.length; index += 1) {
        const a = rings[index - 1];
        const b = rings[index];
        if (!a || !b) continue;
        hits.push({
          kind: "object",
          name: object.name,
          x: (a.x + b.x) / 2,
          y: (a.y + b.y) / 2,
          ax: a.x,
          ay: a.y,
          bx: b.x,
          by: b.y,
          radius: Math.max(a.radius, b.radius),
        });
      }
    }

    if (showFrames) for (const namedFrame of scene.frames) {
      const projected = project(namedFrame.frame.o);
      if (!projected) continue;
      const active =
        hoverStyleKey === `frame:${namedFrame.object}:${namedFrame.name}` ||
        (selection?.kind === "frame" &&
          selection.object === namedFrame.object &&
          selection.name === namedFrame.name);
      context.beginPath();
      context.arc(projected.x, projected.y, active ? 5.5 : 4, 0, Math.PI * 2);
      context.fillStyle = active ? "#ffffff" : "#ffca75";
      context.fill();
      context.lineWidth = 1.5;
      context.strokeStyle = "#1a2130";
      context.stroke();
      hits.push({
        kind: "frame",
        object: namedFrame.object,
        name: namedFrame.name,
        x: projected.x,
        y: projected.y,
      });
    }

    if (showCurves && selectedCurve) {
      for (const station of selectedCurveStations) {
        const marker = project(station.frame.o);
        if (!marker) continue;
        const isBoundary = station.sources.some((source) => source.kind === "segment");
        const isSurface = station.sources.some((source) => source.kind === "surface");
        context.beginPath();
        if (isBoundary) {
          context.moveTo(marker.x, marker.y - 4.2);
          context.lineTo(marker.x + 4.2, marker.y);
          context.lineTo(marker.x, marker.y + 4.2);
          context.lineTo(marker.x - 4.2, marker.y);
          context.closePath();
        } else if (isSurface) {
          context.rect(marker.x - 3.2, marker.y - 3.2, 6.4, 6.4);
        } else {
          context.arc(marker.x, marker.y, 2.8, 0, Math.PI * 2);
        }
        context.fillStyle = isBoundary
          ? "rgba(224, 250, 255, .96)"
          : isSurface
            ? "rgba(255, 159, 112, .92)"
            : "rgba(255, 204, 124, .86)";
        context.fill();
        context.lineWidth = isBoundary ? 1.5 : 1;
        context.strokeStyle = isBoundary
          ? "rgba(42, 139, 160, .96)"
          : "rgba(22, 28, 39, .92)";
        context.stroke();
      }
    }

    hitTargetsRef.current = hits;
  }, [
    camera,
    hoverStyleKey,
    layout.reference_curves,
    scene,
    selectedCurve,
    selectedCurveStations,
    selection,
    showBeamAxis,
    showCurves,
    showFrames,
    showMagneticAxis,
    showObjects,
    size,
  ]);

  useEffect(() => {
    const canvas = overlayRef.current;
    if (!canvas) return;
    const ratio = Math.min(2, window.devicePixelRatio || 1);
    syncCanvasDimensions(canvas, size.width, size.height, ratio);
    const context = canvas.getContext("2d");
    if (!context) return;
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.clearRect(0, 0, size.width, size.height);
    const project = cameraProjector(camera, size.width, size.height);

    const hoveredFrame =
      hovered?.kind === "curve" && showCurves
        ? hovered.sample.frame
        : hovered?.kind === "feature_axis" &&
            (hovered.feature === "magnetic" ? showMagneticAxis : showBeamAxis)
          ? hovered.sample.frame
        : hovered?.kind === "feature_frame" &&
            (hovered.feature === "magnetic" ? showMagneticAxis : showBeamAxis)
          ? hovered.frame
        : hovered?.kind === "frame" && showFrames
          ? scene.frames.find(
              (namedFrame) =>
                namedFrame.object === hovered.object &&
                namedFrame.name === hovered.name,
            )?.frame
          : undefined;
    if (hoveredFrame) {
      const origin = project(hoveredFrame.o);
      if (origin) {
        const axisSize = viewportRelativeArrowLength(
          origin.scale,
          size.width,
          size.height,
        );
        const axes = [
          { vector: hoveredFrame.x, color: "#ff7185", label: "x" },
          { vector: hoveredFrame.y, color: "#83e28d", label: "y" },
          { vector: hoveredFrame.s, color: "#66c7ff", label: "s" },
        ];
        for (const axis of axes) {
          const endpoint = project(
            add(hoveredFrame.o, scale(axis.vector, axisSize)),
          );
          if (!endpoint) continue;
          context.beginPath();
          context.moveTo(origin.x, origin.y);
          context.lineTo(endpoint.x, endpoint.y);
          context.lineWidth = 2.2;
          context.strokeStyle = axis.color;
          context.stroke();
          context.font = "600 12px ui-monospace, SFMono-Regular, monospace";
          context.fillStyle = axis.color;
          context.fillText(axis.label, endpoint.x + 4, endpoint.y - 3);
        }
      }
    }

    if (
      activeCurveProbe &&
      selection?.kind === "curve" &&
      selection.name === activeCurveProbe.curve
    ) {
      const marker = project(activeCurveProbe.sample.p);
      if (marker) {
        const snapped = activeCurveProbe.sources.length > 0;
        context.beginPath();
        context.arc(marker.x, marker.y, snapped ? 6 : 4.5, 0, Math.PI * 2);
        context.fillStyle = snapped ? "#ffcc7c" : "#f7fbfc";
        context.fill();
        context.lineWidth = 2;
        context.strokeStyle = snapped ? "#8b5723" : "#317c82";
        context.stroke();
      }
    }

    if (zoomRectangle) {
      const left = Math.min(zoomRectangle.startX, zoomRectangle.endX);
      const top = Math.min(zoomRectangle.startY, zoomRectangle.endY);
      const width = Math.abs(zoomRectangle.endX - zoomRectangle.startX);
      const height = Math.abs(zoomRectangle.endY - zoomRectangle.startY);
      context.save();
      context.fillStyle = "rgba(102, 199, 255, 0.13)";
      context.fillRect(left, top, width, height);
      context.setLineDash([6, 4]);
      context.lineWidth = 1.5;
      context.strokeStyle = "rgba(190, 232, 255, 0.96)";
      context.strokeRect(
        left + 0.75,
        top + 0.75,
        Math.max(0, width - 1.5),
        Math.max(0, height - 1.5),
      );
      context.restore();
    }
  }, [
    activeCurveProbe,
    camera,
    hovered,
    scene.frames,
    selection,
    showBeamAxis,
    showCurves,
    showFrames,
    showMagneticAxis,
    showObjects,
    size,
    zoomRectangle,
  ]);

  const pick = useCallback((x: number, y: number): HoverTarget => {
    let closestFrame: { distance: number; target: FrameHitTarget } | null = null;
    for (const target of hitTargetsRef.current) {
      if (target.kind !== "frame") continue;
      if (!showFrames) continue;
      const distance = Math.hypot(x - target.x, y - target.y);
      if (distance <= 11 && (!closestFrame || distance < closestFrame.distance)) {
        closestFrame = { distance, target };
      }
    }
    if (closestFrame) return closestFrame.target;

    let closestFeatureFrame: {
      distance: number;
      target: FeatureFrameHitTarget;
    } | null = null;
    for (const target of hitTargetsRef.current) {
      if (target.kind !== "feature_frame") continue;
      if (target.feature === "magnetic" ? !showMagneticAxis : !showBeamAxis) {
        continue;
      }
      const distance = distanceToPolygon(x, y, target.polygon);
      if (
        distance <= 7 &&
        (!closestFeatureFrame || distance < closestFeatureFrame.distance)
      ) {
        closestFeatureFrame = { distance, target };
      }
    }
    if (closestFeatureFrame) return closestFeatureFrame.target;

    let closestFeatureAxis: {
      distance: number;
      target: FeatureAxisHitTarget;
      fraction: number;
      x: number;
      y: number;
    } | null = null;
    for (const target of hitTargetsRef.current) {
      if (target.kind !== "feature_axis_hit") continue;
      if (target.feature === "magnetic" ? !showMagneticAxis : !showBeamAxis) {
        continue;
      }
      const closest = closestPointOnSegment(
        x,
        y,
        target.ax,
        target.ay,
        target.bx,
        target.by,
      );
      if (
        closest.distance <= 9 &&
        (!closestFeatureAxis || closest.distance < closestFeatureAxis.distance)
      ) {
        closestFeatureAxis = {
          distance: closest.distance,
          target,
          fraction: closest.fraction,
          x: closest.x,
          y: closest.y,
        };
      }
    }
    if (closestFeatureAxis) {
      const sample = closestFeatureAxis.fraction < 0.5
        ? closestFeatureAxis.target.startSample
        : closestFeatureAxis.target.endSample;
      return {
        kind: "feature_axis",
        feature: closestFeatureAxis.target.feature,
        object: closestFeatureAxis.target.object,
        sample,
        x: closestFeatureAxis.x,
        y: closestFeatureAxis.y,
      };
    }

    let best: {
      distance: number;
      target: CurveHitTarget | ObjectHitTarget;
    } | null = null;
    for (const target of hitTargetsRef.current) {
      if (target.kind !== "curve_hit" && target.kind !== "object") continue;
      if (target.kind === "curve_hit" && !showCurves) continue;
      if (target.kind === "object" && !showObjects) continue;
      const distance = distanceToSegment(
        x,
        y,
        target.ax,
        target.ay,
        target.bx,
        target.by,
      );
      const threshold = target.kind === "curve_hit" ? 12 : target.radius + 5;
      if (distance <= threshold && (!best || distance < best.distance)) {
        best = { distance, target };
      }
    }
    if (!best) return null;
    if (best.target.kind !== "curve_hit") return best.target;
    const curve = scene.curves.find(
      (candidate) => candidate.name === best.target.name,
    );
    if (!curve) return null;
    const closest = closestPointOnSegment(
      x,
      y,
      best.target.ax,
      best.target.ay,
      best.target.bx,
      best.target.by,
    );
    const fraction = perspectiveCorrectFraction(
      closest.fraction,
      best.target.startDepth,
      best.target.endDepth,
    );
    const path = best.target.startPath +
      (best.target.endPath - best.target.startPath) * fraction;
    const frame = frameAtCurvePath(curve, path);
    return {
      kind: "curve",
      name: curve.name,
      segmentIndex: best.target.segmentIndex,
      sample: { p: frame.o, frame, path },
      x: closest.x,
      y: closest.y,
    };
  }, [
    scene.curves,
    showBeamAxis,
    showCurves,
    showFrames,
    showMagneticAxis,
    showObjects,
  ]);

  const curveProbeAtPointer = useCallback((
    x: number,
    y: number,
    hover: HoverTarget,
  ): CurveProbe | null => {
    if (!showCurves || !selectedCurve) return null;

    const hoveredStation = hover && hover.kind !== "curve"
      ? selectedCurveStations.find((station) =>
          station.sources.some((source) => {
            if (hover.kind === "object") {
              return source.kind === "frame" &&
                source.object === hover.name &&
                source.name === "center";
            }
            if (hover.kind === "frame") {
              return source.kind === "frame" &&
                source.object === hover.object &&
                source.name === hover.name;
            }
            if (hover.kind === "feature_frame") {
              return source.kind === "plane" &&
                source.feature === hover.feature &&
                source.object === hover.object &&
                source.name === hover.name;
            }
            return false;
          })
        )
      : undefined;
    if (hoveredStation) {
      return {
        curve: selectedCurve.name,
        sample: {
          p: hoveredStation.frame.o,
          frame: hoveredStation.frame,
          path: hoveredStation.path,
        },
        sources: hoveredStation.sources,
      };
    }

    let closestHit: {
      target: CurveHitTarget;
      distance: number;
      fraction: number;
      screenLength: number;
    } | null = null;
    for (const target of hitTargetsRef.current) {
      if (target.kind !== "curve_hit" || target.name !== selectedCurve.name) {
        continue;
      }
      const closest = closestPointOnSegment(
        x,
        y,
        target.ax,
        target.ay,
        target.bx,
        target.by,
      );
      if (
        closest.distance <= 12 &&
        (!closestHit || closest.distance < closestHit.distance)
      ) {
        closestHit = {
          target,
          distance: closest.distance,
          fraction: closest.fraction,
          screenLength: Math.hypot(
            target.bx - target.ax,
            target.by - target.ay,
          ),
        };
      }
    }
    if (!closestHit) return null;

    const fraction = perspectiveCorrectFraction(
      closestHit.fraction,
      closestHit.target.startDepth,
      closestHit.target.endDepth,
    );
    const rawPath = closestHit.target.startPath +
      (closestHit.target.endPath - closestHit.target.startPath) * fraction;
    const segmentPath = Math.abs(
      closestHit.target.endPath - closestHit.target.startPath,
    );
    const pathWindow = segmentPath *
      (1.5 + 12 / Math.max(1, closestHit.screenLength));
    const project = cameraProjector(camera, size.width, size.height);
    let snapped: { station: CurveStation; distance: number } | null = null;
    const firstStation = lowerBoundStation(
      selectedCurveStations,
      rawPath - pathWindow,
    );
    for (let index = firstStation; index < selectedCurveStations.length; index += 1) {
      const station = selectedCurveStations[index];
      if (station.path > rawPath + pathWindow) break;
      const projected = project(station.frame.o);
      if (!projected) continue;
      const distance = Math.hypot(x - projected.x, y - projected.y);
      if (distance <= 12 && (!snapped || distance < snapped.distance)) {
        snapped = { station, distance };
      }
    }
    if (snapped) {
      return {
        curve: selectedCurve.name,
        sample: {
          p: snapped.station.frame.o,
          frame: snapped.station.frame,
          path: snapped.station.path,
        },
        sources: snapped.station.sources,
      };
    }
    const frame = frameAtCurvePath(selectedCurve, rawPath);
    return {
      curve: selectedCurve.name,
      sample: { p: frame.o, frame, path: rawPath },
      sources: [],
    };
  }, [camera, selectedCurve, selectedCurveStations, showCurves, size]);

  const pointerCoordinates = (event: ReactPointerEvent<HTMLCanvasElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  };

  const onPointerDown = (event: ReactPointerEvent<HTMLCanvasElement>) => {
    event.currentTarget.setPointerCapture(event.pointerId);
    const point = pointerCoordinates(event);
    dragRef.current = {
      startX: event.clientX,
      startY: event.clientY,
      x: event.clientX,
      y: event.clientY,
      localStartX: point.x,
      localStartY: point.y,
      button: event.button,
      moved: false,
      zooming: mode === "zoom-region" && event.button === 0 && !event.shiftKey,
    };
    if (mode === "zoom-region" && event.button === 0 && !event.shiftKey) {
      setZoomRectangle({
        startX: point.x,
        startY: point.y,
        endX: point.x,
        endY: point.y,
      });
    }
  };

  const onPointerMove = (event: ReactPointerEvent<HTMLCanvasElement>) => {
    const drag = dragRef.current;
    if (drag && event.buttons) {
      const dx = event.clientX - drag.x;
      const dy = event.clientY - drag.y;
      const activeMode = drag.button === 2 || event.shiftKey ? "pan" : mode;
      dragRef.current = {
        startX: drag.startX,
        startY: drag.startY,
        x: event.clientX,
        y: event.clientY,
        localStartX: drag.localStartX,
        localStartY: drag.localStartY,
        button: drag.button,
        moved:
          drag.moved ||
          Math.hypot(
            event.clientX - drag.startX,
            event.clientY - drag.startY,
          ) > 2,
        zooming: drag.zooming && activeMode === "zoom-region",
      };
      if (activeMode === "orbit") {
        setCamera((current) => ({
          ...current,
          azimuth: current.azimuth - dx * 0.008,
          elevation: Math.max(
            -Math.PI / 2 + CANONICAL_POLE_EPSILON,
            Math.min(
              Math.PI / 2 - CANONICAL_POLE_EPSILON,
              current.elevation + dy * 0.008,
            ),
          ),
        }));
      } else if (activeMode === "pan") {
        setZoomRectangle(null);
        const panScale =
          (camera.distance / Math.max(size.width, size.height)) * 1.45;
        const cos = Math.cos(camera.azimuth);
        const sin = Math.sin(camera.azimuth);
        setCamera((current) => ({
          ...current,
          target: add(current.target, [
            (-dx * cos - dy * sin * Math.sin(camera.elevation)) * panScale,
            dy * Math.cos(camera.elevation) * panScale,
            (dx * sin - dy * cos * Math.sin(camera.elevation)) * panScale,
          ]),
        }));
      } else if (activeMode === "zoom-region" && drag.zooming) {
        const point = pointerCoordinates(event);
        setZoomRectangle({
          startX: drag.localStartX,
          startY: drag.localStartY,
          endX: Math.max(0, Math.min(size.width, point.x)),
          endY: Math.max(0, Math.min(size.height, point.y)),
        });
      }
      return;
    }
    const point = pointerCoordinates(event);
    let next = pick(point.x, point.y);
    const nextProbe = curveProbeAtPointer(point.x, point.y, next);
    if (nextProbe) {
      setCurveProbe(nextProbe);
      if (next?.kind === "curve" && next.name === nextProbe.curve) {
        next = {
          ...next,
          sample: nextProbe.sample,
          snappedTo: stationSourceLabel(nextProbe.sources) || undefined,
        };
      }
    }
    setHovered((current) => sameHoverTarget(current, next) ? current : next);
  };

  const onPointerUp = (event: ReactPointerEvent<HTMLCanvasElement>) => {
    const drag = dragRef.current;
    const point = pointerCoordinates(event);
    if (drag?.zooming) {
      const rectangle = {
        startX: drag.localStartX,
        startY: drag.localStartY,
        endX: point.x,
        endY: point.y,
      };
      if (
        drag.moved &&
        Math.abs(rectangle.endX - rectangle.startX) >= 6 &&
        Math.abs(rectangle.endY - rectangle.startY) >= 6
      ) {
        setCamera((current) =>
          zoomCameraToRectangle(current, rectangle, size.width, size.height)
        );
      }
      setZoomRectangle(null);
      dragRef.current = null;
      return;
    }
    if (drag && !drag.moved && drag.button === 0) {
      const target = pick(point.x, point.y);
      if (target?.kind === "curve") {
        const snappedProbe = curveProbeAtPointer(point.x, point.y, target);
        setCurveProbe(
          snappedProbe?.curve === target.name
            ? snappedProbe
            : { curve: target.name, sample: target.sample, sources: [] },
        );
        onSelect({
          kind: "curve",
          name: target.name,
          segmentIndex: target.segmentIndex,
        });
      } else if (target?.kind === "object") {
        onSelect({ kind: "object", name: target.name });
      } else if (target?.kind === "frame") {
        onSelect({ kind: "frame", object: target.object, name: target.name });
      } else if (
        target?.kind === "feature_frame" ||
        target?.kind === "feature_axis"
      ) {
        onSelect({ kind: "object", name: target.object });
      } else {
        onSelect(null);
      }
    }
    dragRef.current = null;
  };

  const cancelPointerInteraction = () => {
    dragRef.current = null;
    setZoomRectangle(null);
  };

  const selectNavigationMode = useCallback((nextMode: NavigationMode) => {
    setMode(nextMode);
    setZoomRectangle(null);
  }, []);

  const hoverLabel =
    hovered?.kind === "frame" || hovered?.kind === "feature_frame"
      ? `${hovered.object}.${hovered.name}`
      : hovered?.kind === "feature_axis"
        ? `${hovered.object} ${hovered.feature} axis`
      : hovered?.name;

  const poseReadout = useMemo<PoseReadout | null>(() => {
    if (geometryError) return null;
    if (selection?.kind === "curve" && activeCurveProbe) {
      return {
        label: `Curve ${selection.name} · s = ${activeCurveProbe.sample.path.toFixed(3)} m`,
        frame: activeCurveProbe.sample.frame,
      };
    }
    if (hovered?.kind === "frame" && showFrames) {
      const namedFrame = scene.frames.find(
        (candidate) =>
          candidate.object === hovered.object && candidate.name === hovered.name,
      );
      return namedFrame
        ? {
            label: `Frame ${namedFrame.object}.${namedFrame.name} · ${namedFrame.typeName}`,
            frame: namedFrame.frame,
          }
        : null;
    }
    if (hovered?.kind === "object" && showObjects) {
      const object = scene.objects.find((candidate) => candidate.name === hovered.name);
      return object
        ? {
            label: `Object ${object.name} · ${object.typeName} · center`,
            frame: object.frame,
          }
        : null;
    }
    if (
      hovered?.kind === "feature_frame" &&
      (hovered.feature === "magnetic" ? showMagneticAxis : showBeamAxis)
    ) {
      return {
        label: `${hovered.feature === "magnetic" ? "Magnetic" : "Beam"} ${hovered.name.endsWith("_entry") ? "entry" : "exit"} frame · ${hovered.object}`,
        frame: hovered.frame,
      };
    }
    if (
      hovered?.kind === "feature_axis" &&
      (hovered.feature === "magnetic" ? showMagneticAxis : showBeamAxis)
    ) {
      return {
        label: `${hovered.feature === "magnetic" ? "Magnetic" : "Beam"} axis · ${hovered.object}`,
        frame: hovered.sample.frame,
      };
    }
    if (hovered?.kind === "curve" && showCurves) {
      return {
        label: `Curve ${hovered.name} · s = ${hovered.sample.path.toFixed(3)} m`,
        frame: hovered.sample.frame,
      };
    }
    if (selection?.kind === "object" && showObjects) {
      const object = scene.objects.find((candidate) => candidate.name === selection.name);
      return object
        ? {
            label: `Object ${object.name} · ${object.typeName} · center`,
            frame: object.frame,
          }
        : null;
    }
    if (selection?.kind === "frame" && showFrames) {
      const namedFrame = scene.frames.find(
        (candidate) =>
          candidate.object === selection.object &&
          candidate.name === selection.name,
      );
      return namedFrame
        ? {
            label: `Frame ${namedFrame.object}.${namedFrame.name} · ${namedFrame.typeName}`,
            frame: namedFrame.frame,
          }
        : null;
    }
    return null;
  }, [
    activeCurveProbe,
    geometryError,
    hovered,
    scene,
    selection,
    showBeamAxis,
    showCurves,
    showFrames,
    showMagneticAxis,
    showObjects,
  ]);

  const poseText = useMemo(() => {
    if (geometryError) return geometryError;
    if (!poseReadout) return "Hover a named frame, feature axis or boundary frame, object, or curve to inspect its world pose.";
    const { frame } = poseReadout;
    const angles = frameToMadxAngles(frame);
    const degrees = 180 / Math.PI;
    const lines = [
      `X = ${cleanFixed(frame.o[0], 6)} m    Y = ${cleanFixed(frame.o[1], 6)} m    Z = ${cleanFixed(frame.o[2], 6)} m`,
      `theta = ${cleanFixed(angles.theta * degrees, 5)} deg    phi = ${cleanFixed(angles.phi * degrees, 5)} deg    psi = ${cleanFixed(angles.psi * degrees, 5)} deg`,
    ];
    if (selection?.kind === "curve") {
      lines.unshift(
        activeCurveProbe
          ? `Curve = ${activeCurveProbe.curve}    s = ${cleanFixed(activeCurveProbe.sample.path, 6)} m`
          : `Curve = ${selection.name}    s = unavailable`,
      );
    }
    return lines.join("\n");
  }, [activeCurveProbe, geometryError, poseReadout, selection]);

  const stationHeading = geometryError
    ? "Geometry unresolved"
    : !selectedCurve
      ? "No reference curve selected"
      : !showCurves
        ? "Reference curves are hidden"
        : activeCurveProbe?.sources.length
          ? `Snapped to ${stationSourceLabel(activeCurveProbe.sources)}`
          : "Free curve position";
  const setCurveLayerVisible = useCallback((checked: boolean) => {
    setShowCurves(checked);
    if (!checked) {
      hitTargetsRef.current = hitTargetsRef.current.filter(
        (target) => target.kind !== "curve_hit",
      );
      setHovered((current) => current?.kind === "curve" ? null : current);
    }
  }, []);

  const setObjectLayerVisible = useCallback((checked: boolean) => {
    setShowObjects(checked);
    if (!checked) {
      hitTargetsRef.current = hitTargetsRef.current.filter(
        (target) => target.kind !== "object",
      );
      setHovered((current) =>
        current?.kind === "object" ? null : current
      );
    }
  }, []);

  const setFrameLayerVisible = useCallback((checked: boolean) => {
    setShowFrames(checked);
    if (!checked) {
      hitTargetsRef.current = hitTargetsRef.current.filter(
        (target) => target.kind !== "frame",
      );
      setHovered((current) => current?.kind === "frame" ? null : current);
    }
  }, []);

  const setFeatureLayerVisible = useCallback((
    feature: "magnetic" | "beam",
    checked: boolean,
  ) => {
    if (feature === "magnetic") setShowMagneticAxis(checked);
    else setShowBeamAxis(checked);
    if (!checked) {
      hitTargetsRef.current = hitTargetsRef.current.filter(
        (target) =>
          !(
            (target.kind === "feature_frame" ||
              target.kind === "feature_axis_hit") &&
            target.feature === feature
          ),
      );
      setHovered((current) =>
        current &&
          (current.kind === "feature_frame" ||
            current.kind === "feature_axis") &&
          current.feature === feature
          ? null
          : current
      );
    }
  }, []);

  const setMagneticAxisVisible = useCallback((checked: boolean) => {
    setFeatureLayerVisible("magnetic", checked);
  }, [setFeatureLayerVisible]);

  const setBeamAxisVisible = useCallback((checked: boolean) => {
    setFeatureLayerVisible("beam", checked);
  }, [setFeatureLayerVisible]);

  useEffect(() => {
    if (!command || command.id <= handledCommandRef.current) return;
    handledCommandRef.current = command.id;
    const finish = (error?: string) => {
      setCommandResult(error ? { id: command.id, error } : { id: command.id });
    };

    if (command.command === "set_mode") {
      selectNavigationMode(command.mode);
      finish();
      return;
    }
    if (command.command === "set_view") {
      setZoomRectangle(null);
      setCamera((current) => cameraForCanonicalView(current, command.view));
      finish();
      return;
    }
    if (command.command === "set_visibility") {
      if (command.visibility.curves !== undefined) {
        setCurveLayerVisible(command.visibility.curves);
      }
      if (command.visibility.objects !== undefined) {
        setObjectLayerVisible(command.visibility.objects);
      }
      if (command.visibility.frames !== undefined) {
        setFrameLayerVisible(command.visibility.frames);
      }
      if (command.visibility.magnetic_axis !== undefined) {
        setMagneticAxisVisible(command.visibility.magnetic_axis);
      }
      if (command.visibility.beam_axis !== undefined) {
        setBeamAxisVisible(command.visibility.beam_axis);
      }
      finish(viewportCommandRenderError(command, geometryError));
      return;
    }
    if (geometryError) {
      finish(`Cannot fit viewport: ${geometryError}`);
      return;
    }
    if (command.target.kind === "layout") {
      fit();
      finish();
      return;
    }
    if (command.target.kind === "curve") {
      const curveName = command.target.name;
      const curve = scene.curves.find(
        (candidate) => candidate.name === curveName,
      );
      if (!curve) {
        finish(`Cannot fit curve "${curveName}": target is not in the current scene.`);
        return;
      }
      fitPoints(curve.samples.map((sample) => sample.p));
      finish();
      return;
    }
    const objectName = command.target.name;
    const object = scene.objects.find(
      (candidate) => candidate.name === objectName,
    );
    if (!object) {
      finish(`Cannot fit object "${objectName}": target is not in the current scene.`);
      return;
    }
    fitPoints(object.vertices.length ? object.vertices : [object.frame.o]);
    finish();
  }, [
    command,
    fit,
    fitPoints,
    geometryError,
    scene.curves,
    scene.objects,
    selectNavigationMode,
    setBeamAxisVisible,
    setCurveLayerVisible,
    setFrameLayerVisible,
    setMagneticAxisVisible,
    setObjectLayerVisible,
  ]);

  useEffect(() => {
    if (
      !commandResult ||
      commandResult.id <= reportedCommandRef.current ||
      !onCommandApplied
    ) return;
    reportedCommandRef.current = commandResult.id;
    onCommandApplied(commandResult.id, commandResult.error);
  }, [commandResult, onCommandApplied]);
  /* eslint-enable react-hooks/set-state-in-effect */

  return (
    <div className="viewport-shell">
      <div ref={wrapperRef} className="viewport-stage">
        <canvas
          ref={canvasRef}
          aria-label="Interactive three-dimensional layout view"
          className={`viewport-canvas mode-${mode}`}
          onContextMenu={(event) => event.preventDefault()}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerLeave={() => {
            setHovered(null);
          }}
          onPointerCancel={cancelPointerInteraction}
          onLostPointerCapture={() => {
            if (dragRef.current) cancelPointerInteraction();
          }}
          onPointerUp={onPointerUp}
        />
        <canvas
          ref={overlayRef}
          aria-hidden="true"
          className="viewport-overlay"
        />
        <div className="viewport-toolbar" aria-label="3D navigation controls">
          <ToolButton
            active={mode === "orbit"}
            label="Orbit"
            onClick={() => selectNavigationMode("orbit")}
          >
            <RotateCcw />
          </ToolButton>
          <ToolButton
            active={mode === "pan"}
            label="Pan"
            onClick={() => selectNavigationMode("pan")}
          >
            <Move />
          </ToolButton>
          <ToolButton
            active={mode === "select"}
            label="Select"
            onClick={() => selectNavigationMode("select")}
          >
            <MousePointer2 />
          </ToolButton>
          <ToolButton
            active={mode === "zoom-region"}
            label="Zoom to rectangle"
            onClick={() => selectNavigationMode("zoom-region")}
          >
            <ScanSearch />
          </ToolButton>
          <span className="toolbar-separator" />
          <ToolButton label="Fit layout" onClick={fit}>
            <Focus />
          </ToolButton>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                aria-label="Canonical views"
                className="canonical-view-trigger"
                size="sm"
                type="button"
                variant="ghost"
              >
                <View />
                <span>Views</span>
                <ChevronDown className="canonical-view-chevron" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start" className="canonical-view-menu">
              {CANONICAL_VIEWS.map((view) => (
                <DropdownMenuItem
                  key={view.value}
                  onSelect={() => {
                    setZoomRectangle(null);
                    setCamera((current) =>
                      cameraForCanonicalView(current, view.value)
                    );
                  }}
                >
                  {view.label}
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
        <div className="viewport-layers" aria-label="Viewer layers">
          <div className="viewport-layer-toggle">
            <Switch
              aria-label="Show reference curves"
              checked={showCurves}
              id="viewer-curves-visible"
              onCheckedChange={setCurveLayerVisible}
              size="sm"
            />
            <label htmlFor="viewer-curves-visible">Curves</label>
          </div>
          <div className="viewport-layer-toggle">
            <Switch
              aria-label="Show objects"
              checked={showObjects}
              id="viewer-objects-visible"
              onCheckedChange={setObjectLayerVisible}
              size="sm"
            />
            <label htmlFor="viewer-objects-visible">Objects</label>
          </div>
          <div className="viewport-layer-toggle">
            <Switch
              aria-label="Show magnetic axis and entry and exit frames"
              checked={showMagneticAxis}
              id="viewer-magnetic-axis-visible"
              onCheckedChange={setMagneticAxisVisible}
              size="sm"
            />
            <label htmlFor="viewer-magnetic-axis-visible">Magnetic axis</label>
          </div>
          <div className="viewport-layer-toggle">
            <Switch
              aria-label="Show beam interface axis and entry and exit frames"
              checked={showBeamAxis}
              id="viewer-beam-axis-visible"
              onCheckedChange={setBeamAxisVisible}
              size="sm"
            />
            <label htmlFor="viewer-beam-axis-visible">Beam interface</label>
          </div>
          <div className="viewport-layer-toggle">
            <Switch
              aria-label="Show named frames"
              checked={showFrames}
              id="viewer-frames-visible"
              onCheckedChange={setFrameLayerVisible}
              size="sm"
            />
            <label htmlFor="viewer-frames-visible">Named frames</label>
          </div>
        </div>
        <svg
          aria-label="World axis orientation"
          className="viewport-axis-marker"
          role="img"
          viewBox="0 0 76 76"
        >
          <circle className="axis-marker-backdrop" cx="38" cy="38" r="34" />
          {worldAxes.axes.map((axis) => {
            const projectedLength = Math.hypot(
              axis.x - worldAxes.origin.x,
              axis.y - worldAxes.origin.y,
            );
            return (
              <g
                data-axis={axis.label.toLowerCase()}
                key={axis.label}
                opacity={axis.depth > 0.15 ? 0.72 : 1}
                style={{ color: axis.color }}
              >
                <line
                  x1={worldAxes.origin.x}
                  y1={worldAxes.origin.y}
                  x2={axis.x}
                  y2={axis.y}
                />
                {projectedLength < 4 ? (
                  <circle className="axis-marker-end-on" cx={axis.x} cy={axis.y} r="4" />
                ) : (
                  <circle cx={axis.x} cy={axis.y} r="2.4" />
                )}
                <text
                  x={axis.x + (axis.x >= worldAxes.origin.x ? 5 : -5)}
                  y={axis.y + (axis.y >= worldAxes.origin.y ? 10 : -5)}
                  textAnchor={axis.x >= worldAxes.origin.x ? "start" : "end"}
                >
                  {axis.label}
                </text>
              </g>
            );
          })}
          <circle className="axis-marker-origin" cx="38" cy="38" r="2.7" />
        </svg>
        <div className="viewport-hint">
          {mode === "zoom-region"
            ? "Draw a rectangle to zoom · Shift-drag or right-drag to pan"
            : `Drag to ${mode} · wheel to zoom · click again or empty space to clear`}
        </div>
        {geometryError && (
          <div className="viewport-error" role="alert">
            <strong>Cannot resolve layout geometry</strong>
            <span>{geometryError}</span>
          </div>
        )}
        {!geometryError && hovered && (
          <div
            className="viewport-label"
            style={{
              left: Math.min(size.width - 170, hovered.x + 14),
              top: Math.max(12, hovered.y - 16),
            }}
          >
            <span>{hovered.kind.replace("_", " ")}</span>
            <strong>{hoverLabel}</strong>
            {hovered.kind === "curve" && (
              <small>
                segment {hovered.segmentIndex + 1} · s = {hovered.sample.path.toFixed(3)}
              </small>
            )}
            {hovered.kind === "curve" && hovered.snappedTo && (
              <small>snap · {hovered.snappedTo}</small>
            )}
          </div>
        )}
      </div>
      <div className="viewport-readouts">
        <div
          aria-atomic="true"
          aria-live="polite"
          className="coordinate-readout"
        >
          <div className="coordinate-readout-heading">
            <span>
              {selection?.kind === "curve"
                ? "World pose · Curve station"
                : "World pose"}
            </span>
            <strong
              title={selection?.kind === "curve" ? stationHeading : poseReadout?.label}
            >
              {geometryError
                ? "Geometry unresolved"
                : selection?.kind === "curve"
                  ? stationHeading
                  : poseReadout?.label ?? "Nothing highlighted"}
            </strong>
          </div>
          <textarea
            aria-label={selection?.kind === "curve"
              ? "Selected reference curve station, world coordinates, and MAD-X Euler angles"
              : "World coordinates and MAD-X Euler angles"}
            className="coordinate-readout-text"
            readOnly
            rows={selection?.kind === "curve" ? 3 : 2}
            value={poseText}
          />
        </div>
      </div>
    </div>
  );
}
