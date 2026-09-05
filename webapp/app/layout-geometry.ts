import {
  BEAM_BOUNDARY_FRAME_NAMES,
  effectiveBeamFeature,
  hasMagneticFeature,
  MAGNETIC_BOUNDARY_FRAME_NAMES,
  shapePath,
} from "./layout-data";
import type {
  BeamBoundaryFrameName,
  Frame,
  FeatureBoundaryFrameName,
  LayoutData,
  LayoutObject,
  LayoutType,
  MagneticBoundaryFrameName,
  ObjectPosition,
  TransformOperation,
  Transformation,
  Vec3,
} from "./layout-data";

export const IDENTITY: Frame = {
  o: [0, 0, 0],
  x: [1, 0, 0],
  y: [0, 1, 0],
  s: [0, 0, 1],
};

export function add(a: Vec3, b: Vec3): Vec3 {
  return [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
}

export function sub(a: Vec3, b: Vec3): Vec3 {
  return [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
}

export function scale(v: Vec3, amount: number): Vec3 {
  return [v[0] * amount, v[1] * amount, v[2] * amount];
}

export function dot(a: Vec3, b: Vec3): number {
  return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
}

export function cross(a: Vec3, b: Vec3): Vec3 {
  return [
    a[1] * b[2] - a[2] * b[1],
    a[2] * b[0] - a[0] * b[2],
    a[0] * b[1] - a[1] * b[0],
  ];
}

export function length(v: Vec3): number {
  return Math.hypot(v[0], v[1], v[2]);
}

export function normalize(v: Vec3): Vec3 {
  const size = length(v);
  return size > 1e-12 ? scale(v, 1 / size) : [0, 0, 0];
}

function rotate(v: Vec3, axis: Vec3, angle: number): Vec3 {
  const unit = normalize(axis);
  const c = Math.cos(angle);
  const s = Math.sin(angle);
  return add(
    add(scale(v, c), scale(cross(unit, v), s)),
    scale(unit, dot(unit, v) * (1 - c)),
  );
}

function cloneFrame(frame: Frame): Frame {
  return {
    o: [...frame.o],
    x: [...frame.x],
    y: [...frame.y],
    s: [...frame.s],
  } as Frame;
}

function applyOperations(frame: Frame, operations: TransformOperation[]): Frame {
  const next = cloneFrame(frame);
  for (const [name, value] of operations) {
    if (name === "tx") next.o = add(next.o, scale(next.x, value));
    else if (name === "ty") next.o = add(next.o, scale(next.y, value));
    else if (name === "tt") {
      next.o = add(next.o, scale(next.s, value));
    }
    else if (name === "rx" || name === "ry" || name === "rs") {
      const axis = name === "rx" ? next.x : name === "ry" ? next.y : next.s;
      next.x = normalize(rotate(next.x, axis, value));
      next.y = normalize(rotate(next.y, axis, value));
      next.s = normalize(rotate(next.s, axis, value));
    }
  }
  return next;
}

function curvatureNormal(frame: Frame, roll: number): Vec3 {
  // A positive bend angle points toward local -x at zero roll. Positive roll
  // rotates that curvature direction from -x toward local -y.
  return normalize(
    add(
      scale(frame.x, -Math.cos(roll)),
      scale(frame.y, -Math.sin(roll)),
    ),
  );
}

function advanceFrame(
  frame: Frame,
  segmentLength: number,
  angle: number,
  roll: number,
): Frame {
  if (Math.abs(angle) < 1e-10) {
    return { ...cloneFrame(frame), o: add(frame.o, scale(frame.s, segmentLength)) };
  }
  const normal = curvatureNormal(frame, roll);
  const bendAxis = normalize(cross(frame.s, normal));
  const radius = segmentLength / angle;
  const displacement = add(
    scale(frame.s, radius * Math.sin(angle)),
    scale(normal, radius * (1 - Math.cos(angle))),
  );
  return {
    o: add(frame.o, displacement),
    x: normalize(rotate(frame.x, bendAxis, angle)),
    y: normalize(rotate(frame.y, bendAxis, angle)),
    s: normalize(rotate(frame.s, bendAxis, angle)),
  };
}

type LocalPath = { curvature: number; roll: number };

function mechanicalPath(type: LayoutType): LocalPath {
  const { curvature, roll } = shapePath(type.shape);
  return { curvature, roll };
}

function advanceLocalPath(
  frame: Frame,
  distance: number,
  path: LocalPath,
): Frame {
  return advanceFrame(
    frame,
    distance,
    path.curvature * distance,
    path.roll,
  );
}

function applyLocalOperations(
  frame: Frame,
  operations: TransformOperation[],
  path: LocalPath,
): Frame {
  let next = cloneFrame(frame);
  for (const operation of operations) {
    next = operation[0] === "ts"
      ? advanceLocalPath(next, operation[1], path)
      : applyOperations(next, [operation]);
  }
  return next;
}

function transformVector(frame: Frame, vector: Vec3): Vec3 {
  return add(
    scale(frame.x, vector[0]),
    add(scale(frame.y, vector[1]), scale(frame.s, vector[2])),
  );
}

function composeFrames(parent: Frame, local: Frame): Frame {
  return {
    o: add(parent.o, transformVector(parent, local.o)),
    x: normalize(transformVector(parent, local.x)),
    y: normalize(transformVector(parent, local.y)),
    s: normalize(transformVector(parent, local.s)),
  };
}

function invertFrame(frame: Frame): Frame {
  return {
    o: [
      -dot(frame.x, frame.o),
      -dot(frame.y, frame.o),
      -dot(frame.s, frame.o),
    ],
    x: [frame.x[0], frame.y[0], frame.s[0]],
    y: [frame.x[1], frame.y[1], frame.s[1]],
    s: [frame.x[2], frame.y[2], frame.s[2]],
  };
}

function localFrameForName(
  type: LayoutType,
  frameName: string,
  object: LayoutObject,
): Frame | undefined {
  if (frameName === "center") return cloneFrame(IDENTITY);
  const path = mechanicalPath(type);
  if (
    frameName === "magnetic_center" ||
    frameName === "magnetic_entry" ||
    frameName === "magnetic_exit"
  ) {
    if (!hasMagneticFeature(type)) return undefined;
    const center = applyLocalOperations(
      IDENTITY,
      type.magnetic_center!.transformation,
      path,
    );
    if (frameName === "magnetic_center") return center;
    const direction = frameName === "magnetic_entry" ? -1 : 1;
    return advanceLocalPath(center, direction * type.magnetic_length! / 2, {
      curvature: type.magnetic_curvature!,
      roll: type.magnetic_roll!,
    });
  }
  if (
    frameName === "beam_center" ||
    frameName === "beam_entry" ||
    frameName === "beam_exit"
  ) {
    const beam = effectiveBeamFeature(type, object);
    if (!beam) return undefined;
    const center = applyLocalOperations(
      IDENTITY,
      beam.center.transformation,
      path,
    );
    if (frameName === "beam_center") return center;
    const direction = frameName === "beam_entry" ? -1 : 1;
    return advanceLocalPath(center, direction * beam.length / 2, {
      curvature: beam.curvature,
      roll: beam.roll,
    });
  }
  const definition = Object.prototype.hasOwnProperty.call(type.frames, frameName)
    ? type.frames[frameName]
    : undefined;
  return definition
    ? applyLocalOperations(IDENTITY, definition.transformation, path)
    : undefined;
}

function localToWorld(frame: Frame, local: Vec3): Vec3 {
  return add(
    frame.o,
    add(
      scale(frame.x, local[0]),
      add(scale(frame.y, local[1]), scale(frame.s, local[2])),
    ),
  );
}

function sweepStepCount(type: LayoutType): number {
  const { length: pathLength, curvature } = shapePath(type.shape);
  const totalAngle = Math.abs(curvature * pathLength);
  if (totalAngle < 1e-10) return 1;
  return Math.max(3, Math.min(48, Math.ceil(totalAngle / (Math.PI / 24))));
}

export type CurveSample = { p: Vec3; frame: Frame; path: number };
export type CurveSegmentGeometry = {
  startFrame: Frame;
  path: number;
  length: number;
  angle: number;
  roll: number;
};
export type CurveGeometry = {
  name: string;
  samples: CurveSample[];
  segments: CurveSegmentGeometry[];
  totalLength: number;
};
export type CurvePathSolutions = {
  kind: "none" | "unique" | "multiple" | "infinite";
  paths: number[];
  intervals: { start: number; end: number }[];
};
export type ClosestCurvePathSolution = {
  kind: "none" | "unique" | "equidistant";
  path?: number;
  paths: number[];
  distance?: number;
};
export type ObjectGeometry = {
  name: string;
  object: LayoutObject;
  typeName: string;
  type: LayoutType;
  frame: Frame;
  vertices: Vec3[];
  faces: readonly (readonly number[])[];
  edges: readonly (readonly [number, number])[];
};
export type NamedFrameGeometry = {
  object: string;
  name: string;
  typeName: string;
  frame: Frame;
};
export type FeatureAxisGeometry = {
  object: string;
  typeName: string;
  kind: "magnetic" | "beam";
  centerFrame: Frame;
  samples: CurveSample[];
};
export type FeatureBoundaryFrameGeometry<
  Name extends FeatureBoundaryFrameName = FeatureBoundaryFrameName,
> = {
  object: string;
  name: Name;
  typeName: string;
  kind: "magnetic" | "beam";
  frame: Frame;
  vertices: Vec3[];
};
export type MagneticFrameGeometry =
  FeatureBoundaryFrameGeometry<MagneticBoundaryFrameName>;
export type BeamFrameGeometry =
  FeatureBoundaryFrameGeometry<BeamBoundaryFrameName>;
export type SceneGeometry = {
  curves: CurveGeometry[];
  objects: ObjectGeometry[];
  frames: NamedFrameGeometry[];
  magneticAxes: FeatureAxisGeometry[];
  magneticFrames: MagneticFrameGeometry[];
  beamAxes: FeatureAxisGeometry[];
  beamFrames: BeamFrameGeometry[];
  bounds: { min: Vec3; max: Vec3 };
};

export type SceneScope =
  | { kind: "layout" }
  | { kind: "curve" | "object"; name: string };

function curveTolerances(curve: CurveGeometry, point?: Vec3) {
  let geometryScale = Math.max(1, curve.totalLength);
  let absoluteScale = Math.max(
    1,
    Math.abs(point?.[0] ?? 0),
    Math.abs(point?.[1] ?? 0),
    Math.abs(point?.[2] ?? 0),
  );
  for (const segment of curve.segments) {
    geometryScale = Math.max(geometryScale, segment.length);
    if (Math.abs(segment.angle) >= 1e-10) {
      geometryScale = Math.max(
        geometryScale,
        Math.abs(segment.length / segment.angle),
      );
    }
    absoluteScale = Math.max(
      absoluteScale,
      Math.abs(segment.startFrame.o[0]),
      Math.abs(segment.startFrame.o[1]),
      Math.abs(segment.startFrame.o[2]),
    );
  }
  return {
    distance: Math.max(
      1e-10,
      1e-12 * geometryScale,
      32 * Number.EPSILON * absoluteScale,
    ),
    path: 1e-9 * Math.max(1, curve.totalLength),
  };
}

function classifyCurvePaths(
  paths: number[],
  intervals: { start: number; end: number }[] = [],
): CurvePathSolutions {
  if (intervals.length) return { kind: "infinite", paths, intervals };
  if (paths.length === 0) return { kind: "none", paths, intervals };
  if (paths.length === 1) return { kind: "unique", paths, intervals };
  return { kind: "multiple", paths, intervals };
}

function enumeratePeriodicRoots(
  base: number,
  minimum: number,
  maximum: number,
  tolerance: number,
): number[] {
  const firstIndex = Math.ceil(
    (minimum - base - tolerance) / (Math.PI * 2),
  );
  const lastIndex = Math.floor(
    (maximum - base + tolerance) / (Math.PI * 2),
  );
  const roots: number[] = [];
  for (let index = firstIndex; index <= lastIndex; index += 1) {
    roots.push(base + index * Math.PI * 2);
  }
  return roots;
}

export function frameAtCurvePath(
  curve: CurveGeometry,
  requestedPath: number,
): Frame {
  if (!curve.samples.length) return cloneFrame(IDENTITY);
  if (requestedPath <= 0) {
    const first = curve.samples[0].frame;
    return requestedPath < 0
      ? applyOperations(first, [["tt", requestedPath]])
      : cloneFrame(first);
  }
  const last = curve.samples[curve.samples.length - 1];
  if (requestedPath >= curve.totalLength) {
    return applyOperations(last.frame, [["tt", requestedPath - curve.totalLength]]);
  }
  if (!curve.segments.length) return cloneFrame(last.frame);

  let low = 0;
  let high = curve.segments.length - 1;
  while (high - low > 1) {
    const middle = Math.floor((low + high) / 2);
    if (curve.segments[middle].path <= requestedPath) low = middle;
    else high = middle;
  }
  const segment = curve.segments[
    requestedPath >= curve.segments[high].path ? high : low
  ];
  const distance = Math.max(
    0,
    Math.min(segment.length, requestedPath - segment.path),
  );
  return advanceFrame(
    segment.startFrame,
    distance,
    segment.angle * distance / segment.length,
    segment.roll,
  );
}

/** Return the segment owning a path; a shared boundary belongs to its next segment. */
export function curveSegmentIndexAtPath(
  curve: CurveGeometry,
  requestedPath: number,
): number {
  if (!curve.segments.length) return -1;
  if (requestedPath >= curve.totalLength) return curve.segments.length - 1;
  const path = Math.max(0, requestedPath);
  let low = 0;
  let high = curve.segments.length;
  while (low < high) {
    const middle = Math.floor((low + high) / 2);
    if (curve.segments[middle].path <= path) low = middle + 1;
    else high = middle;
  }
  return Math.max(0, low - 1);
}

/**
 * Find every in-domain station whose curve transverse plane contains `point`.
 * This raw, non-throwing classification is shared by the viewer and by the
 * closest-station selector used for object positioning.
 */
export function transverseCurvePathsForPoint(
  curve: CurveGeometry,
  point: Vec3,
): CurvePathSolutions {
  const tolerance = curveTolerances(curve, point);
  const candidates: number[] = [];
  const intervals: { start: number; end: number }[] = [];

  const addCandidate = (path: number) => {
    const clamped = Math.max(0, Math.min(curve.totalLength, path));
    if (
      !candidates.some((candidate) =>
        Math.abs(candidate - clamped) <= tolerance.path
      )
    ) {
      candidates.push(clamped);
    }
  };

  for (const segment of curve.segments) {
    const offset = sub(point, segment.startFrame.o);
    if (Math.abs(segment.angle) < 1e-10) {
      const distance = dot(offset, segment.startFrame.s);
      if (
        distance >= -tolerance.path &&
        distance <= segment.length + tolerance.path
      ) {
        addCandidate(segment.path + Math.max(0, Math.min(segment.length, distance)));
      }
      continue;
    }

    const curvature = segment.angle / segment.length;
    const radius = 1 / curvature;
    const normal = curvatureNormal(segment.startFrame, segment.roll);
    const a = dot(offset, segment.startFrame.s);
    const b = dot(offset, normal) - radius;
    if (Math.hypot(a, b) <= tolerance.distance) {
      intervals.push({
        start: segment.path,
        end: segment.path + segment.length,
      });
      continue;
    }

    const firstRoot = Math.atan2(b, a) + Math.PI / 2;
    const minimum = Math.min(0, segment.angle);
    const maximum = Math.max(0, segment.angle);
    const angleTolerance = Math.max(
      1e-12,
      tolerance.path * Math.abs(curvature),
    );
    const firstIndex = Math.ceil(
      (minimum - firstRoot - angleTolerance) / Math.PI,
    );
    const lastIndex = Math.floor(
      (maximum - firstRoot + angleTolerance) / Math.PI,
    );
    for (let index = firstIndex; index <= lastIndex; index += 1) {
      const theta = firstRoot + index * Math.PI;
      const distance = theta / curvature;
      if (
        distance >= -tolerance.path &&
        distance <= segment.length + tolerance.path
      ) {
        addCandidate(
          segment.path + Math.max(0, Math.min(segment.length, distance)),
        );
      }
    }
  }

  candidates.sort((a, b) => a - b);
  return classifyCurvePaths(candidates, intervals);
}

/**
 * Select the transverse-plane solution whose curve-frame origin is closest to
 * `point`. An isolated solution wins over a farther continuous interval; a
 * nearest interval or equal nearest isolated solutions remain ambiguous.
 */
export function closestTransverseCurvePathForPoint(
  curve: CurveGeometry,
  point: Vec3,
): ClosestCurvePathSolution {
  const solutions = transverseCurvePathsForPoint(curve, point);
  if (!solutions.paths.length && !solutions.intervals.length) {
    return { kind: "none", paths: [] };
  }

  const ranked = [
    ...solutions.paths.map((path) => ({
      path,
      interval: false,
      distance: length(sub(point, frameAtCurvePath(curve, path).o)),
    })),
    ...solutions.intervals.map(({ start, end }) => {
      const path = (start + end) / 2;
      return {
        path,
        interval: true,
        distance: length(sub(point, frameAtCurvePath(curve, path).o)),
      };
    }),
  ];
  const minimum = Math.min(...ranked.map((candidate) => candidate.distance));
  const tolerance = curveTolerances(curve, point).distance;
  const nearest = ranked.filter(
    (candidate) => Math.abs(candidate.distance - minimum) <= tolerance,
  );
  const paths = nearest.map((candidate) => candidate.path).sort((a, b) => a - b);
  if (nearest.length !== 1 || nearest[0].interval) {
    return { kind: "equidistant", paths, distance: minimum };
  }
  return {
    kind: "unique",
    path: nearest[0].path,
    paths,
    distance: minimum,
  };
}

/** Find isolated intersections between a curve and an infinite frame x-y plane. */
export function curvePlaneIntersectionPaths(
  curve: CurveGeometry,
  plane: Frame,
): CurvePathSolutions {
  const planeNormal = normalize(plane.s);
  const tolerance = curveTolerances(curve, plane.o);
  const candidates: number[] = [];
  const intervals: { start: number; end: number }[] = [];

  const addCandidate = (path: number) => {
    const clamped = Math.max(0, Math.min(curve.totalLength, path));
    const curveFrame = frameAtCurvePath(curve, clamped);
    // A tangent touch does not designate a transverse object plane station.
    if (Math.abs(dot(planeNormal, curveFrame.s)) <= 1e-8) return;
    if (
      !candidates.some((candidate) =>
        Math.abs(candidate - clamped) <= tolerance.path
      )
    ) {
      candidates.push(clamped);
    }
  };

  for (const segment of curve.segments) {
    const offset = sub(segment.startFrame.o, plane.o);
    if (Math.abs(segment.angle) < 1e-10) {
      const a = dot(planeNormal, offset);
      const b = dot(planeNormal, segment.startFrame.s);
      if (Math.abs(b) <= 1e-10) {
        if (Math.abs(a) <= tolerance.distance) {
          intervals.push({
            start: segment.path,
            end: segment.path + segment.length,
          });
        }
        continue;
      }
      const distance = -a / b;
      if (
        distance >= -tolerance.path &&
        distance <= segment.length + tolerance.path
      ) {
        addCandidate(segment.path + Math.max(0, Math.min(segment.length, distance)));
      }
      continue;
    }

    const curvature = segment.angle / segment.length;
    const radius = 1 / curvature;
    const bendNormal = curvatureNormal(segment.startFrame, segment.roll);
    const tangentTerm = radius * dot(planeNormal, segment.startFrame.s);
    const bendTerm = radius * dot(planeNormal, bendNormal);
    const constant = dot(planeNormal, offset) + bendTerm;
    const amplitude = Math.hypot(tangentTerm, -bendTerm);
    if (amplitude <= tolerance.distance) {
      if (Math.abs(constant) <= tolerance.distance) {
        intervals.push({
          start: segment.path,
          end: segment.path + segment.length,
        });
      }
      continue;
    }
    const ratio = -constant / amplitude;
    if (ratio < -1 - 1e-12 || ratio > 1 + 1e-12) continue;
    const clampedRatio = Math.max(-1, Math.min(1, ratio));
    const phase = Math.atan2(-bendTerm, tangentTerm);
    const principal = Math.asin(clampedRatio);
    const minimum = Math.min(0, segment.angle);
    const maximum = Math.max(0, segment.angle);
    const angleTolerance = Math.max(
      1e-12,
      tolerance.path * Math.abs(curvature),
    );
    const roots = [
      ...enumeratePeriodicRoots(
        principal - phase,
        minimum,
        maximum,
        angleTolerance,
      ),
      ...enumeratePeriodicRoots(
        Math.PI - principal - phase,
        minimum,
        maximum,
        angleTolerance,
      ),
    ];
    for (const theta of roots) {
      const distance = theta / curvature;
      if (
        distance >= -tolerance.path &&
        distance <= segment.length + tolerance.path
      ) {
        addCandidate(
          segment.path + Math.max(0, Math.min(segment.length, distance)),
        );
      }
    }
  }

  candidates.sort((a, b) => a - b);
  return classifyCurvePaths(candidates, intervals);
}

type Bounds3 = { min: Vec3; max: Vec3 };
type CurveChord = {
  a: Vec3;
  b: Vec3;
  startPath: number;
  endPath: number;
  bounds: Bounds3;
};
type CurveChordNode = {
  bounds: Bounds3;
  left?: CurveChordNode;
  right?: CurveChordNode;
  chords?: CurveChord[];
};

function boundsForPoints(points: Vec3[]): Bounds3 {
  const min: Vec3 = [...points[0]];
  const max: Vec3 = [...points[0]];
  for (const point of points.slice(1)) {
    for (let axis = 0; axis < 3; axis += 1) {
      min[axis] = Math.min(min[axis], point[axis]);
      max[axis] = Math.max(max[axis], point[axis]);
    }
  }
  return { min, max };
}

function mergeBounds(a: Bounds3, b: Bounds3): Bounds3 {
  return {
    min: [
      Math.min(a.min[0], b.min[0]),
      Math.min(a.min[1], b.min[1]),
      Math.min(a.min[2], b.min[2]),
    ],
    max: [
      Math.max(a.max[0], b.max[0]),
      Math.max(a.max[1], b.max[1]),
      Math.max(a.max[2], b.max[2]),
    ],
  };
}

function boundsOverlap(a: Bounds3, b: Bounds3, tolerance: number): boolean {
  return [0, 1, 2].every(
    (axis) =>
      a.max[axis] + tolerance >= b.min[axis] &&
      b.max[axis] + tolerance >= a.min[axis],
  );
}

function buildCurveChordTree(chords: CurveChord[]): CurveChordNode | null {
  if (!chords.length) return null;
  const bounds = chords
    .slice(1)
    .reduce((result, chord) => mergeBounds(result, chord.bounds), chords[0].bounds);
  if (chords.length <= 12) return { bounds, chords };
  const centroidBounds = boundsForPoints(
    chords.map((chord) => scale(add(chord.a, chord.b), 0.5)),
  );
  const extents = sub(centroidBounds.max, centroidBounds.min);
  const axis = extents[1] > extents[0]
    ? extents[2] > extents[1] ? 2 : 1
    : extents[2] > extents[0] ? 2 : 0;
  const ordered = chords.slice().sort(
    (a, b) =>
      (a.a[axis] + a.b[axis]) / 2 - (b.a[axis] + b.b[axis]) / 2,
  );
  const middle = Math.floor(ordered.length / 2);
  const left = buildCurveChordTree(ordered.slice(0, middle));
  const right = buildCurveChordTree(ordered.slice(middle));
  return {
    bounds,
    ...(left ? { left } : {}),
    ...(right ? { right } : {}),
  };
}

function queryCurveChordTree(
  node: CurveChordNode | null,
  bounds: Bounds3,
  tolerance: number,
  result: CurveChord[],
) {
  if (!node || !boundsOverlap(node.bounds, bounds, tolerance)) return;
  if (node.chords) {
    for (const chord of node.chords) {
      if (boundsOverlap(chord.bounds, bounds, tolerance)) result.push(chord);
    }
    return;
  }
  queryCurveChordTree(node.left ?? null, bounds, tolerance, result);
  queryCurveChordTree(node.right ?? null, bounds, tolerance, result);
}

function segmentTriangleFraction(
  start: Vec3,
  end: Vec3,
  a: Vec3,
  b: Vec3,
  c: Vec3,
  distanceTolerance: number,
): number | null {
  const direction = sub(end, start);
  const segmentLength = length(direction);
  const edge1 = sub(b, a);
  const edge2 = sub(c, a);
  const normalLength = length(cross(edge1, edge2));
  if (segmentLength <= distanceTolerance || normalLength <= distanceTolerance ** 2) {
    return null;
  }
  const p = cross(direction, edge2);
  const determinant = dot(edge1, p);
  if (Math.abs(determinant) <= 1e-12 * segmentLength * normalLength) {
    // Coplanar/tangent overlap does not define an isolated surface station.
    return null;
  }
  const inverse = 1 / determinant;
  const fromA = sub(start, a);
  const triangleScale = Math.max(length(edge1), length(edge2), distanceTolerance);
  const barycentricTolerance = Math.max(1e-9, distanceTolerance / triangleScale);
  const u = dot(fromA, p) * inverse;
  if (u < -barycentricTolerance || u > 1 + barycentricTolerance) return null;
  const q = cross(fromA, edge1);
  const v = dot(direction, q) * inverse;
  if (v < -barycentricTolerance || u + v > 1 + barycentricTolerance) return null;
  const fraction = dot(edge2, q) * inverse;
  const endpointTolerance = Math.max(1e-9, distanceTolerance / segmentLength);
  if (fraction < -endpointTolerance || fraction > 1 + endpointTolerance) {
    return null;
  }
  return Math.max(0, Math.min(1, fraction));
}

/**
 * Intersect a sampled curve with the same triangulated shape surfaces drawn by
 * the viewer. The curve-chord BVH keeps this practical for very long layouts.
 */
export function curveObjectSurfaceIntersectionPaths(
  curve: CurveGeometry,
  objects: ObjectGeometry[],
): Map<string, number[]> {
  const result = new Map<string, number[]>();
  if (curve.samples.length < 2 || !objects.length) return result;
  const chords: CurveChord[] = [];
  for (let index = 1; index < curve.samples.length; index += 1) {
    const previous = curve.samples[index - 1];
    const current = curve.samples[index];
    chords.push({
      a: previous.p,
      b: current.p,
      startPath: previous.path,
      endPath: current.path,
      bounds: boundsForPoints([previous.p, current.p]),
    });
  }
  const tree = buildCurveChordTree(chords);
  const pathTolerance = curveTolerances(curve).path;

  for (const object of objects) {
    if (!object.vertices.length) continue;
    const objectBounds = boundsForPoints(object.vertices);
    const absoluteScale = Math.max(
      1,
      ...objectBounds.min.map(Math.abs),
      ...objectBounds.max.map(Math.abs),
    );
    const objectExtent = length(sub(objectBounds.max, objectBounds.min));
    const distanceTolerance = Math.max(
      1e-10,
      1e-12 * Math.max(1, objectExtent),
      32 * Number.EPSILON * absoluteScale,
    );
    const candidateChords: CurveChord[] = [];
    queryCurveChordTree(tree, objectBounds, distanceTolerance, candidateChords);
    if (!candidateChords.length) continue;

    const triangles: { a: Vec3; b: Vec3; c: Vec3; bounds: Bounds3 }[] = [];
    for (const face of object.faces) {
      if (face.length < 3) continue;
      const a = object.vertices[face[0]];
      for (let index = 1; index < face.length - 1; index += 1) {
        const b = object.vertices[face[index]];
        const c = object.vertices[face[index + 1]];
        triangles.push({ a, b, c, bounds: boundsForPoints([a, b, c]) });
      }
    }

    const paths: number[] = [];
    for (const chord of candidateChords) {
      for (const triangle of triangles) {
        if (!boundsOverlap(chord.bounds, triangle.bounds, distanceTolerance)) {
          continue;
        }
        const fraction = segmentTriangleFraction(
          chord.a,
          chord.b,
          triangle.a,
          triangle.b,
          triangle.c,
          distanceTolerance,
        );
        if (fraction === null) continue;
        const path = chord.startPath +
          (chord.endPath - chord.startPath) * fraction;
        if (!paths.some((candidate) => Math.abs(candidate - path) <= pathTolerance)) {
          paths.push(path);
        }
      }
    }
    if (paths.length) result.set(object.name, paths.sort((a, b) => a - b));
  }
  return result;
}

type SweepTopology = Pick<ObjectGeometry, "faces" | "edges">;

// Face and edge indexes depend only on the number of vertices in each ring and
// the number of sweep steps. Layouts commonly contain thousands of instances
// of the same few shapes, so keep one immutable topology instead of allocating
// identical nested arrays for every object.
const sweepTopologyCache = new Map<string, SweepTopology>();

function sweepTopology(
  ringSize: number,
  steps: number,
  endCapBeforeSides = false,
): SweepTopology {
  const key = `${ringSize}:${steps}:${endCapBeforeSides ? 1 : 0}`;
  const cached = sweepTopologyCache.get(key);
  if (cached) return cached;

  const faces: number[][] = [
    Array.from({ length: ringSize }, (_, index) => ringSize - 1 - index),
  ];
  const endOffset = steps * ringSize;
  const endCap = Array.from(
    { length: ringSize },
    (_, index) => endOffset + index,
  );
  if (endCapBeforeSides) faces.push(endCap);
  const edges: [number, number][] = [];
  for (let layer = 0; layer <= steps; layer += 1) {
    const offset = layer * ringSize;
    for (let index = 0; index < ringSize; index += 1) {
      edges.push([offset + index, offset + (index + 1) % ringSize]);
    }
  }
  for (let layer = 0; layer < steps; layer += 1) {
    const offset = layer * ringSize;
    const nextOffset = (layer + 1) * ringSize;
    for (let index = 0; index < ringSize; index += 1) {
      const next = (index + 1) % ringSize;
      faces.push([
        offset + index,
        offset + next,
        nextOffset + next,
        nextOffset + index,
      ]);
      edges.push([offset + index, nextOffset + index]);
    }
  }
  if (!endCapBeforeSides) faces.push(endCap);

  const topology: SweepTopology = Object.freeze({
    faces: Object.freeze(
      faces.map((face) => Object.freeze(face)),
    ),
    edges: Object.freeze(
      edges.map((edge) => Object.freeze(edge)),
    ),
  });
  sweepTopologyCache.set(key, topology);
  return topology;
}

function featureStepCount(axisLength: number, curvature: number): number {
  const totalAngle = Math.abs(axisLength * curvature);
  if (totalAngle < 1e-10) return 1;
  return Math.max(3, Math.min(48, Math.ceil(totalAngle / (Math.PI / 24))));
}

function buildFeatureAxisGeometry(
  object: string,
  typeName: string,
  kind: "magnetic" | "beam",
  centerFrame: Frame,
  axisLength: number,
  curvature: number,
  roll: number,
): FeatureAxisGeometry {
  const steps = featureStepCount(axisLength, curvature);
  const path = { curvature, roll };
  const samples = Array.from({ length: steps + 1 }, (_, index): CurveSample => {
    const station = -axisLength / 2 + axisLength * index / steps;
    const frame = advanceLocalPath(centerFrame, station, path);
    return { p: frame.o, frame, path: station };
  });
  return { object, typeName, kind, centerFrame, samples };
}

function featurePlaneVertices(
  type: LayoutType,
  frame: Frame,
  axisLength: number,
): Vec3[] {
  const planeScale = 1.08;
  let localVertices: Vec3[];
  if (type.shape?.[0] === "box") {
    localVertices = [
      [-type.shape[1] * planeScale / 2, -type.shape[2] * planeScale / 2, 0],
      [type.shape[1] * planeScale / 2, -type.shape[2] * planeScale / 2, 0],
      [type.shape[1] * planeScale / 2, type.shape[2] * planeScale / 2, 0],
      [-type.shape[1] * planeScale / 2, type.shape[2] * planeScale / 2, 0],
    ];
  } else if (type.shape?.[0] === "cylinder") {
    localVertices = Array.from({ length: 24 }, (_, index): Vec3 => {
      const angle = index / 24 * Math.PI * 2;
      const radius = type.shape![1] * planeScale;
      return [Math.cos(angle) * radius, Math.sin(angle) * radius, 0];
    });
  } else {
    const halfExtent = Math.max(0.05, axisLength * 0.08);
    localVertices = [
      [-halfExtent, -halfExtent, 0],
      [halfExtent, -halfExtent, 0],
      [halfExtent, halfExtent, 0],
      [-halfExtent, halfExtent, 0],
    ];
  }
  return localVertices.map((vertex) => localToWorld(frame, vertex));
}

export function buildScene(
  layout: LayoutData,
  scope: SceneScope = { kind: "layout" },
): SceneGeometry {
  if (
    scope.kind === "curve" &&
    !Object.hasOwn(layout.reference_curves, scope.name)
  ) {
    throw new Error(`Unknown reference curve: ${scope.name}`);
  }
  if (scope.kind === "object" && !Object.hasOwn(layout.objects, scope.name)) {
    throw new Error(`Unknown object: ${scope.name}`);
  }
  const curveCache = new Map<string, CurveGeometry>();
  const objectCache = new Map<string, Frame>();
  const namedFrameCache = new Map<string, Frame>();

  const resolveTransformation = (value: Transformation, stack: string[]): Frame => {
    let base = cloneFrame(IDENTITY);
    let operations = value.transformation;
    if (value.reference.kind === "curve") {
      const path = operations
        .filter(([name]) => name === "ts")
        .reduce((sum, [, amount]) => sum + amount, 0);
      base = frameOnCurve(value.reference.curve, path, stack);
      operations = operations.filter(([name]) => name !== "ts");
    } else if (value.reference.kind === "object_frame") {
      base = resolveFrame(value.reference.object, value.reference.frame, stack);
    }
    return applyOperations(base, operations);
  };

  const resolveCurve = (name: string, stack: string[]): CurveGeometry => {
    const cached = curveCache.get(name);
    if (cached) return cached;
    if (stack.includes(`curve:${name}`)) {
      return {
        name,
        samples: [{ p: [0, 0, 0], frame: IDENTITY, path: 0 }],
        segments: [],
        totalLength: 0,
      };
    }
    const definition = layout.reference_curves[name];
    if (!definition) {
      return {
        name,
        samples: [{ p: [0, 0, 0], frame: IDENTITY, path: 0 }],
        segments: [],
        totalLength: 0,
      };
    }
    let frame = resolveTransformation(
      definition.starting_frame,
      [...stack, `curve:${name}`],
    );
    let path = 0;
    const samples: CurveSample[] = [
      { p: frame.o, frame: cloneFrame(frame), path },
    ];
    const segments: CurveSegmentGeometry[] = [];
    for (const [segmentLength, angle, roll] of definition.segments) {
      segments.push({
        startFrame: cloneFrame(frame),
        path,
        length: segmentLength,
        angle,
        roll,
      });
      const steps = Math.max(
        3,
        Math.min(
          100,
          Math.ceil(Math.max(segmentLength / 0.35, Math.abs(angle) / 0.035)),
        ),
      );
      for (let index = 1; index <= steps; index += 1) {
        const fraction = index / steps;
        const sampleFrame = advanceFrame(
          frame,
          segmentLength * fraction,
          angle * fraction,
          roll,
        );
        samples.push({
          p: sampleFrame.o,
          frame: sampleFrame,
          path: path + segmentLength * fraction,
        });
      }
      frame = advanceFrame(frame, segmentLength, angle, roll);
      path += segmentLength;
    }
    const geometry = { name, samples, segments, totalLength: path };
    curveCache.set(name, geometry);
    return geometry;
  };

  const frameOnCurve = (name: string, requestedPath: number, stack: string[]): Frame =>
    frameAtCurvePath(resolveCurve(name, stack), requestedPath);

  const closestTransversePath = (
    curveName: string,
    point: Vec3,
    stack: string[],
    label: string,
  ): number => {
    const result = closestTransverseCurvePathForPoint(
      resolveCurve(curveName, stack),
      point,
    );
    if (result.kind === "none") {
      throw new Error(
        `${label}: cannot infer s on curve ${curveName}; the referenced frame origin lies in no transverse plane within the curve domain`,
      );
    }
    if (result.kind === "equidistant") {
      throw new Error(
        `${label}: cannot infer s on curve ${curveName}; multiple transverse-plane solutions are equally close to the referenced frame origin`,
      );
    }
    return result.path as number;
  };

  const resolveObjectPosition = (
    value: ObjectPosition,
    stack: string[],
    label: string,
  ): Frame => {
    const pathOperations = value.transformation.filter(([name]) => name === "ts");
    if (value.reference.kind === "curve" || pathOperations.length === 0) {
      return resolveTransformation(value, stack);
    }
    if (!value.reference_curve) {
      throw new Error(`${label}: reference_curve is required for ts`);
    }
    const referenceFrame = value.reference.kind === "object_frame"
      ? resolveFrame(value.reference.object, value.reference.frame, stack)
      : cloneFrame(IDENTITY);
    const inferredPath = closestTransversePath(
      value.reference_curve,
      referenceFrame.o,
      stack,
      label,
    );
    const pathShift = pathOperations.reduce((sum, [, amount]) => sum + amount, 0);
    const base = frameOnCurve(
      value.reference_curve,
      inferredPath + pathShift,
      stack,
    );
    return applyOperations(
      base,
      value.transformation.filter(([name]) => name !== "ts"),
    );
  };

  const resolveObject = (name: string, stack: string[]): Frame => {
    const cached = objectCache.get(name);
    if (cached) return cached;
    if (stack.includes(`object:${name}`)) return cloneFrame(IDENTITY);
    const object = layout.objects[name];
    if (!object) return cloneFrame(IDENTITY);
    const targetFrame = resolveObjectPosition(
      object.position,
      [...stack, `object:${name}`],
      `Object ${name} position`,
    );
    const type = layout.types[object.type];
    const targetLocalFrame = localFrameForName(
      type,
      object.position.target,
      object,
    );
    const frame = targetLocalFrame !== undefined
      ? composeFrames(targetFrame, invertFrame(targetLocalFrame))
      : targetFrame;
    objectCache.set(name, frame);
    return frame;
  };

  const resolveFrame = (
    objectName: string,
    frameName: string,
    stack: string[],
  ): Frame => {
    const key = JSON.stringify([objectName, frameName]);
    const cached = namedFrameCache.get(key);
    if (cached) return cached;
    if (stack.includes(`frame:${key}`)) return cloneFrame(IDENTITY);
    const object = layout.objects[objectName];
    const type = layout.types[object?.type];
    if (!type) return resolveObject(objectName, stack);
    const localFrame = localFrameForName(type, frameName, object);
    if (localFrame === undefined) return resolveObject(objectName, stack);
    const frame = composeFrames(
      resolveObject(objectName, [...stack, `frame:${key}`]),
      localFrame,
    );
    namedFrameCache.set(key, frame);
    return frame;
  };

  const curveNames = scope.kind === "layout"
    ? Object.keys(layout.reference_curves)
    : scope.kind === "curve"
      ? [scope.name]
      : [];
  const objectEntries = scope.kind === "layout"
    ? Object.entries(layout.objects)
    : scope.kind === "object" && Object.hasOwn(layout.objects, scope.name)
      ? [[scope.name, layout.objects[scope.name]] as const]
      : [];

  const curves = curveNames.map((name) =>
    resolveCurve(name, []),
  );
  const objects: ObjectGeometry[] = objectEntries.map(
    ([name, object]) => {
      const frame = resolveObject(name, []);
      const type = layout.types[object.type];
      if (!type.shape) {
        return {
          name,
          object,
          typeName: object.type,
          type,
          frame,
          vertices: [],
          faces: [],
          edges: [],
        };
      }
      const steps = sweepStepCount(type);
      if (type.shape[0] === "box") {
        const [, dx, dy, dz] = type.shape;
        const crossSection: [number, number][] = [
          [-dx / 2, -dy / 2],
          [dx / 2, -dy / 2],
          [dx / 2, dy / 2],
          [-dx / 2, dy / 2],
        ];
        const vertices: Vec3[] = [];
        for (let layer = 0; layer <= steps; layer += 1) {
          const path = -dz / 2 + (dz * layer) / steps;
          const sectionFrame = advanceLocalPath(
            frame,
            path,
            mechanicalPath(type),
          );
          for (const [x, y] of crossSection) {
            vertices.push(localToWorld(sectionFrame, [x, y, 0]));
          }
        }
        return {
          name,
          object,
          typeName: object.type,
          type,
          frame,
          vertices,
          ...sweepTopology(4, steps),
        };
      }

      const [, radius, dz] = type.shape;
      const sides = 18;
      const vertices: Vec3[] = [];
      for (let layer = 0; layer <= steps; layer += 1) {
        const path = -dz / 2 + (dz * layer) / steps;
        const sectionFrame = advanceLocalPath(
          frame,
          path,
          mechanicalPath(type),
        );
        for (let index = 0; index < sides; index += 1) {
          const angle = (index / sides) * Math.PI * 2;
          vertices.push(localToWorld(sectionFrame, [
            Math.cos(angle) * radius,
            Math.sin(angle) * radius,
            0,
          ]));
        }
      }
      return {
        name,
        object,
        typeName: object.type,
        type,
        frame,
        vertices,
        ...sweepTopology(sides, steps, true),
      };
    },
  );

  const frames = objectEntries.flatMap(([objectName, object]) =>
    Object.keys(layout.types[object.type].frames).map((frameName) => ({
      object: objectName,
      name: frameName,
      typeName: object.type,
      frame: resolveFrame(objectName, frameName, []),
    })),
  );

  const magneticAxes: FeatureAxisGeometry[] = [];
  const magneticFrames: MagneticFrameGeometry[] = [];
  const beamAxes: FeatureAxisGeometry[] = [];
  const beamFrames: BeamFrameGeometry[] = [];
  for (const [objectName, object] of objectEntries) {
    const type = layout.types[object.type];
    if (hasMagneticFeature(type)) {
      const centerFrame = resolveFrame(objectName, "magnetic_center", []);
      magneticAxes.push(buildFeatureAxisGeometry(
        objectName,
        object.type,
        "magnetic",
        centerFrame,
        type.magnetic_length!,
        type.magnetic_curvature!,
        type.magnetic_roll!,
      ));
      for (const name of MAGNETIC_BOUNDARY_FRAME_NAMES) {
        const frame = resolveFrame(objectName, name, []);
        magneticFrames.push({
          object: objectName,
          name,
          typeName: object.type,
          kind: "magnetic",
          frame,
          vertices: featurePlaneVertices(type, frame, type.magnetic_length!),
        });
      }
    }
    const beam = effectiveBeamFeature(type, object);
    if (beam) {
      const centerFrame = resolveFrame(objectName, "beam_center", []);
      beamAxes.push(buildFeatureAxisGeometry(
        objectName,
        object.type,
        "beam",
        centerFrame,
        beam.length,
        beam.curvature,
        beam.roll,
      ));
      for (const name of BEAM_BOUNDARY_FRAME_NAMES) {
        const frame = resolveFrame(objectName, name, []);
        beamFrames.push({
          object: objectName,
          name,
          typeName: object.type,
          kind: "beam",
          frame,
          vertices: featurePlaneVertices(type, frame, beam.length),
        });
      }
    }
  }

  let hasPosition = false;
  const min: Vec3 = [
    Number.POSITIVE_INFINITY,
    Number.POSITIVE_INFINITY,
    Number.POSITIVE_INFINITY,
  ];
  const max: Vec3 = [
    Number.NEGATIVE_INFINITY,
    Number.NEGATIVE_INFINITY,
    Number.NEGATIVE_INFINITY,
  ];
  const includePosition = (position: Vec3) => {
    hasPosition = true;
    for (let axis = 0; axis < 3; axis += 1) {
      min[axis] = Math.min(min[axis], position[axis]);
      max[axis] = Math.max(max[axis], position[axis]);
    }
  };
  for (const curve of curves) {
    for (const sample of curve.samples) includePosition(sample.p);
  }
  for (const object of objects) {
    includePosition(object.frame.o);
    for (const vertex of object.vertices) includePosition(vertex);
  }
  for (const frame of frames) includePosition(frame.frame.o);
  for (const axis of [...magneticAxes, ...beamAxes]) {
    for (const sample of axis.samples) includePosition(sample.p);
  }
  for (const boundary of [...magneticFrames, ...beamFrames]) {
    for (const vertex of boundary.vertices) includePosition(vertex);
  }
  if (!hasPosition) {
    min[0] = -1;
    min[1] = -1;
    min[2] = -1;
    max[0] = 1;
    max[1] = 1;
    max[2] = 1;
  }
  return {
    curves,
    objects,
    frames,
    magneticAxes,
    magneticFrames,
    beamAxes,
    beamFrames,
    bounds: { min, max },
  };
}
