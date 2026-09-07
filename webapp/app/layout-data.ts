export type Vec3 = [number, number, number];
export type Frame = { o: Vec3; x: Vec3; y: Vec3; s: Vec3 };
export type TransformName = "tx" | "ty" | "ts" | "tt" | "rx" | "ry" | "rs";
export type TransformOperation = [TransformName, number];
export type NonCurveTransformName = Exclude<TransformName, "ts">;
export type LocalTransformName = TransformName;
export type LocalTransformOperation = [LocalTransformName, number];

export type WorldReference = { kind: "world" };
export type CurveReference = { kind: "curve"; curve: string };
export type ObjectFrameReference = {
  kind: "object_frame";
  object: string;
  frame: string;
};
export type Reference = WorldReference | CurveReference | ObjectFrameReference;
export type LocalFrameReference = { kind: "local_frame"; frame: string };
export type PlacementReference = Reference | LocalFrameReference;

export type Transformation = {
  reference: Reference;
  transformation: TransformOperation[];
};

export type ObjectPosition = Transformation & {
  target: string;
  reference_curve?: string;
};

export type CurveSegment = [number, number, number];
export type ReferenceCurve = {
  color: string;
  starting_frame: Transformation;
  segments: CurveSegment[];
};

// Shape dimensions are followed by the signed centreline curvature [1/m] and
// bend-plane roll [rad].  The longitudinal dimension is arc length.
export type BoxShape = ["box", number, number, number, number, number];
export type CylinderShape = ["cylinder", number, number, number, number];
export type Shape = BoxShape | CylinderShape;

export type LocalTransformation = {
  reference?: PlacementReference;
  transformation: LocalTransformOperation[];
};

export type LayoutType = {
  shape?: Shape;
  mechanical_center?: LocalTransformation;
  color: string;
  magnetic_center?: LocalTransformation;
  magnetic_length?: number;
  magnetic_curvature?: number;
  magnetic_roll?: number;
  frames: Record<string, LocalTransformation>;
};

export type LayoutObject = {
  beam_center?: LocalTransformation;
  beam_length?: number;
  beam_curvature?: number;
  beam_roll?: number;
  type: string;
  position: ObjectPosition;
};

export type LayoutData = {
  reference_curves: Record<string, ReferenceCurve>;
  types: Record<string, LayoutType>;
  objects: Record<string, LayoutObject>;
};

export type LayoutDependencyKind = "curve" | "object";
export type LayoutDependencyRelation =
  | "starting_frame"
  | "position_reference"
  | "feature_reference"
  | "station_curve";
export type LayoutDependencyNode = {
  id: string;
  kind: LayoutDependencyKind;
  name: string;
};
export type LayoutDependencyEdge = {
  from: string;
  to: string;
  relation: LayoutDependencyRelation;
  frame?: string;
};
export type LayoutDependencyGraph = {
  nodes: LayoutDependencyNode[];
  edges: LayoutDependencyEdge[];
};

export type SelectedEntity =
  | { kind: "curve"; name: string; segmentIndex?: number }
  | { kind: "object"; name: string }
  | { kind: "frame"; object: string; name: string }
  | null;

export function createEmptyLayout(): LayoutData {
  return {
    reference_curves: {},
    types: {},
    objects: {},
  };
}

export const TRANSFORM_NAMES: TransformName[] = [
  "tx",
  "ty",
  "ts",
  "tt",
  "rx",
  "ry",
  "rs",
];

export const LOCAL_TRANSFORM_NAMES: LocalTransformName[] = [
  "tx",
  "ty",
  "ts",
  "tt",
  "rx",
  "ry",
  "rs",
];

export const NON_CURVE_TRANSFORM_NAMES: NonCurveTransformName[] = [
  "tx",
  "ty",
  "tt",
  "rx",
  "ry",
  "rs",
];

// Anchor is present for every object. The magnetic and beam frames are derived
// when defined explicitly or (for objects) inherited from the magnetic axis. These names may not
// be stored in the type frames mapping, even when the corresponding axis is absent.
export const IMPLICIT_TYPE_FRAME_NAMES = [
  "anchor",
  "mechanical_center",
  "magnetic_center",
  "magnetic_entry",
  "magnetic_exit",
  "beam_center",
  "beam_entry",
  "beam_exit",
] as const;
export type ImplicitTypeFrameName =
  (typeof IMPLICIT_TYPE_FRAME_NAMES)[number];
export const MAGNETIC_BOUNDARY_FRAME_NAMES = [
  "magnetic_entry",
  "magnetic_exit",
] as const;
export type MagneticBoundaryFrameName =
  (typeof MAGNETIC_BOUNDARY_FRAME_NAMES)[number];
export const BEAM_BOUNDARY_FRAME_NAMES = [
  "beam_entry",
  "beam_exit",
] as const;
export type BeamBoundaryFrameName =
  (typeof BEAM_BOUNDARY_FRAME_NAMES)[number];
export type FeatureBoundaryFrameName =
  | MagneticBoundaryFrameName
  | BeamBoundaryFrameName;

export const SAMPLE_LAYOUT: LayoutData = {
  reference_curves: {
    ring: {
      color: "#68d5c8",
      starting_frame: {
        reference: { kind: "world" },
        transformation: [["tx", -5]],
      },
      segments: [
        [5, 0, 0],
        [5, Math.PI / 3, 0],
        [4, 0, 0],
        [4, -Math.PI / 5, 0.22],
      ],
    },
  },
  types: {
    quadrupole: {
      shape: ["box", 1.1, 0.9, 1.6, 0.22, 0],
      color: "#f0a84b",
      magnetic_center: { transformation: [] },
      magnetic_length: 1.4,
      magnetic_curvature: 0.22,
      magnetic_roll: 0,
      frames: {
        survey_mark: {
          transformation: [["tx", 0.45], ["ty", 0.35]],
        },
      },
    },
    monitor: {
      shape: ["cylinder", 0.62, 0.7, 0, 0],
      color: "#5fd6c7",
      magnetic_center: { transformation: [] },
      magnetic_length: 0.5,
      magnetic_curvature: 0,
      magnetic_roll: 0,
      frames: {},
    },
    detector: {
      shape: ["box", 2.2, 1.5, 0.55, 0, 0],
      color: "#8898ff",
      magnetic_center: { transformation: [] },
      magnetic_length: 0.4,
      magnetic_curvature: 0,
      magnetic_roll: 0,
      frames: {
        sensor_origin: {
          transformation: [["tx", 0.9], ["ty", 0.55]],
        },
      },
    },
  },
  objects: {
    QF1: {
      type: "quadrupole",
      beam_center: { transformation: [] },
      beam_length: 1.4,
      beam_curvature: 0.22,
      beam_roll: 0,
      position: {
        target: "anchor",
        reference: { kind: "curve", curve: "ring" },
        transformation: [["ts", 3.5]],
      },
    },
    BPM1: {
      type: "monitor",
      position: {
        target: "anchor",
        reference: { kind: "curve", curve: "ring" },
        transformation: [["ts", 8.4]],
      },
    },
    Detector: {
      type: "detector",
      position: {
        target: "magnetic_entry",
        reference: {
          kind: "object_frame",
          object: "QF1",
          frame: "magnetic_exit",
        },
        transformation: [["tt", 2.2], ["rs", 0.18]],
      },
    },
  },
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function assertOnlyKeys(
  value: Record<string, unknown>,
  label: string,
  allowed: readonly string[],
) {
  const extraKeys = Object.keys(value).filter((key) => !allowed.includes(key));
  if (extraKeys.length) {
    throw new Error(`${label} contains unsupported fields: ${extraKeys.join(", ")}`);
  }
}

function defineDictionaryEntry<T>(
  dictionary: Record<string, T>,
  name: string,
  value: T,
) {
  Object.defineProperty(dictionary, name, {
    configurable: true,
    enumerable: true,
    value,
    writable: true,
  });
}

function hasOwn(dictionary: object, name: PropertyKey): boolean {
  return Object.prototype.hasOwnProperty.call(dictionary, name);
}

export function isImplicitTypeFrameName(
  name: string,
): name is ImplicitTypeFrameName {
  return name === "center" || (IMPLICIT_TYPE_FRAME_NAMES as readonly string[]).includes(name);
}

/** Import the former base-frame spelling without exporting it again. */
export function canonicalFrameName(name: string): string {
  return name === "center" ? "anchor" : name;
}

export function hasMagneticFeature(type: LayoutType): boolean {
  return type.magnetic_center !== undefined &&
    type.magnetic_length !== undefined &&
    type.magnetic_curvature !== undefined &&
    type.magnetic_roll !== undefined;
}

export function hasBeamFeature(object: LayoutObject): boolean {
  return object.beam_center !== undefined &&
    object.beam_length !== undefined &&
    object.beam_curvature !== undefined &&
    object.beam_roll !== undefined;
}

export function effectiveBeamFeature(type: LayoutType, object: LayoutObject) {
  if (hasBeamFeature(object)) return {
    center: object.beam_center!, length: object.beam_length!,
    curvature: object.beam_curvature!, roll: object.beam_roll!,
  };
  if (hasMagneticFeature(type)) return {
    center: type.magnetic_center!, length: type.magnetic_length!,
    curvature: type.magnetic_curvature!, roll: type.magnetic_roll!,
  };
  return undefined;
}

export function typeFrameNames(type: LayoutType): string[] {
  const implicit = ["anchor"];
  if (type.shape) implicit.push("mechanical_center");
  if (hasMagneticFeature(type)) {
    implicit.push("magnetic_center", ...MAGNETIC_BOUNDARY_FRAME_NAMES);
  }
  return [...implicit, ...Object.keys(type.frames)];
}

export function objectFrameNames(type: LayoutType, object: LayoutObject): string[] {
  const names = typeFrameNames(type);
  if (effectiveBeamFeature(type, object)) {
    names.push("beam_center", ...BEAM_BOUNDARY_FRAME_NAMES);
  }
  return names;
}

export function hasTypeFrame(type: LayoutType, name: string): boolean {
  return typeFrameNames(type).includes(canonicalFrameName(name));
}

export type ObjectFrameDefinition = {
  placement: LocalTransformation;
  advance?: { length: number; curvature: number; roll: number };
};

/** The same recipe drives validation, target inversion, and world evaluation. */
export function objectFrameDefinition(
  type: LayoutType, object: LayoutObject, name: string,
): ObjectFrameDefinition | undefined {
  if (name === "mechanical_center" && type.shape) {
    return { placement: type.mechanical_center ?? { transformation: [] } };
  }
  if (name === "magnetic_center" && hasMagneticFeature(type)) {
    return { placement: type.magnetic_center! };
  }
  if (name === "beam_center" && effectiveBeamFeature(type, object)) {
    return { placement: object.beam_center ?? {
      reference: { kind: "local_frame", frame: "magnetic_center" }, transformation: [],
    } };
  }
  for (const kind of ["magnetic", "beam"] as const) {
    if (name !== `${kind}_entry` && name !== `${kind}_exit`) continue;
    const axis = kind === "beam" ? effectiveBeamFeature(type, object)
      : hasMagneticFeature(type) ? {
        length: type.magnetic_length!, curvature: type.magnetic_curvature!, roll: type.magnetic_roll!,
      } : undefined;
    if (!axis) return undefined;
    return {
      placement: { reference: { kind: "local_frame", frame: `${kind}_center` }, transformation: [] },
      advance: { length: (name.endsWith("_entry") ? -0.5 : 0.5) * axis.length,
        curvature: axis.curvature, roll: axis.roll },
    };
  }
  return hasOwn(type.frames, name) ? { placement: type.frames[name] } : undefined;
}

/** Only frames rigidly rooted in this anchor can determine its placement. */
export function anchorRelativeFrameNames(type: LayoutType, object: LayoutObject, objectName?: string): string[] {
  const state = new Map<string, boolean | "visiting">([["anchor", true]]);
  const anchored = (name: string): boolean => {
    if (state.has(name)) return state.get(name) === true;
    state.set(name, "visiting");
    const definition = objectFrameDefinition(type, object, name);
    const reference = definition?.placement.reference;
    const result = Boolean(definition) && (!reference ||
      (reference.kind === "local_frame" && anchored(reference.frame)) ||
      (reference.kind === "object_frame" && reference.object === objectName && anchored(reference.frame)));
    state.set(name, result);
    return result;
  };
  return objectFrameNames(type, object).filter(anchored);
}

export function forEachPlacement(
  layout: LayoutData,
  callback: (placement: LocalTransformation, label: string, owner: {kind: "type" | "object"; name: string}) => void,
) {
  for (const [name, type] of Object.entries(layout.types)) {
    const owner = { kind: "type" as const, name };
    if (type.mechanical_center) callback(type.mechanical_center, `type ${name} mechanical_center`, owner);
    if (type.magnetic_center) callback(type.magnetic_center, `type ${name} magnetic_center`, owner);
    for (const [frameName, placement] of Object.entries(type.frames)) callback(placement, `type ${name} frame ${frameName}`, owner);
  }
  for (const [name, object] of Object.entries(layout.objects)) {
    if (object.beam_center) callback(object.beam_center, `object ${name} beam_center`, {kind: "object", name});
  }
}

function finite(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new Error(`${label} must be a finite number`);
  }
  return value;
}

function parseReference(value: unknown, label: string): Reference {
  if (!isRecord(value)) throw new Error(`${label} must be an object`);
  if (value.kind === "world") {
    assertOnlyKeys(value, label, ["kind"]);
    return { kind: "world" };
  }
  if (value.kind === "curve" && typeof value.curve === "string" && value.curve) {
    assertOnlyKeys(value, label, ["kind", "curve"]);
    return { kind: "curve", curve: value.curve };
  }
  if (
    value.kind === "object_frame" &&
    typeof value.object === "string" && value.object &&
    typeof value.frame === "string" && value.frame
  ) {
    assertOnlyKeys(value, label, ["kind", "object", "frame"]);
    return { kind: "object_frame", object: value.object, frame: canonicalFrameName(value.frame) };
  }
  throw new Error(`${label} has an invalid reference`);
}

function parseTransformation(value: unknown, label: string): Transformation {
  if (!isRecord(value)) throw new Error(`${label} must be an object`);
  assertOnlyKeys(value, label, ["reference", "transformation"]);
  const transformation = parseTransformOperations(value.transformation, label);
  const reference = parseReference(value.reference, `${label}.reference`);
  if (
    reference.kind !== "curve" &&
    transformation.some(([name]) => name === "ts")
  ) {
    throw new Error(`${label} can use ts only with a curve reference; use tt for a tangent shift`);
  }
  return { reference, transformation };
}

function parseTransformOperations(
  value: unknown,
  label: string,
): TransformOperation[] {
  if (!Array.isArray(value)) {
    throw new Error(`${label}.transformation must be an array`);
  }
  return value.map((operation, index): TransformOperation => {
    if (
      !Array.isArray(operation) || operation.length !== 2 ||
      !TRANSFORM_NAMES.includes(operation[0] as TransformName)
    ) {
      throw new Error(`${label}.transformation[${index}] must be [name, value]`);
    }
    return [
      operation[0] as TransformName,
      finite(operation[1], `${label}.transformation[${index}][1]`),
    ];
  });
}

function parseObjectPosition(value: unknown, label: string): ObjectPosition {
  if (!isRecord(value)) throw new Error(`${label} must be an object`);
  assertOnlyKeys(value, label, [
    "target",
    "reference",
    "reference_curve",
    "transformation",
  ]);
  if (typeof value.target !== "string" || !value.target) {
    throw new Error(`${label}.target must be anchor or a named frame`);
  }
  const reference = parseReference(value.reference, `${label}.reference`);
  const transformation = parseTransformOperations(value.transformation, label);
  const hasPathLookup = transformation.some(([name]) => name === "ts");
  let reference_curve: string | undefined;
  if (value.reference_curve !== undefined) {
    if (typeof value.reference_curve !== "string" || !value.reference_curve) {
      throw new Error(`${label}.reference_curve must be a non-empty curve name`);
    }
    reference_curve = value.reference_curve;
  }
  if (reference.kind === "curve" && reference_curve !== undefined) {
    throw new Error(
      `${label}.reference_curve is only used with world or object-frame references`,
    );
  }
  if (reference.kind !== "curve" && hasPathLookup && !reference_curve) {
    throw new Error(
      `${label} requires reference_curve when ts is used with a ${reference.kind.replace("_", "-")} reference`,
    );
  }
  return {
    target: canonicalFrameName(value.target),
    reference,
    ...(reference_curve ? { reference_curve } : {}),
    transformation,
  };
}

function parseLocalTransformation(value: unknown, label: string): LocalTransformation {
  if (!isRecord(value)) throw new Error(`${label} must be an object`);
  assertOnlyKeys(value, label, ["reference", "transformation"]);
  let reference: PlacementReference | undefined;
  if (value.reference !== undefined) {
    const raw = value.reference;
    if (isRecord(raw) && raw.kind === "local_frame") {
      assertOnlyKeys(raw, `${label}.reference`, ["kind", "frame"]);
      if (typeof raw.frame !== "string" || !raw.frame) {
        throw new Error(`${label}.reference.frame must be a non-empty frame name`);
      }
      reference = { kind: "local_frame", frame: canonicalFrameName(raw.frame) };
    } else reference = parseReference(raw, `${label}.reference`);
  }
  if (!Array.isArray(value.transformation)) {
    throw new Error(`${label}.transformation must be an array`);
  }
  const transformation = value.transformation.map(
    (operation, index): LocalTransformOperation => {
      if (
        !Array.isArray(operation) || operation.length !== 2 ||
        !LOCAL_TRANSFORM_NAMES.includes(operation[0] as LocalTransformName)
      ) {
        throw new Error(
          `${label}.transformation[${index}] must use tx, ty, ts, tt, rx, ry or rs`,
        );
      }
      return [
        operation[0] as LocalTransformName,
        finite(operation[1], `${label}.transformation[${index}][1]`),
      ];
    },
  );
  return { ...(reference ? { reference } : {}), transformation };
}

function parseShape(value: unknown, label: string): Shape {
  if (!Array.isArray(value)) throw new Error(`${label} must be an array`);
  let shape: Shape;
  if (value[0] === "box" && value.length === 6) {
    shape = [
      "box",
      finite(value[1], `${label}[1]`),
      finite(value[2], `${label}[2]`),
      finite(value[3], `${label}[3]`),
      finite(value[4], `${label}[4]`),
      finite(value[5], `${label}[5]`),
    ];
  } else if (value[0] === "cylinder" && value.length === 5) {
    shape = [
      "cylinder",
      finite(value[1], `${label}[1]`),
      finite(value[2], `${label}[2]`),
      finite(value[3], `${label}[3]`),
      finite(value[4], `${label}[4]`),
    ];
  } else {
    throw new Error(
      `${label} must be ["box", dx, dy, dz, curvature, roll] or ["cylinder", r, dz, curvature, roll]`,
    );
  }
  const dimensions = shape[0] === "box"
    ? [shape[1], shape[2], shape[3]]
    : [shape[1], shape[2]];
  if (dimensions.some((dimension) => dimension <= 0)) {
    throw new Error(`${label} dimensions must be positive`);
  }
  return shape;
}

type OptionalAxisFeature = {
  center: LocalTransformation;
  length: number;
  curvature: number;
  roll: number;
};

function parseOptionalAxisFeature(
  value: Record<string, unknown>,
  label: string,
  prefix: "magnetic" | "beam",
): OptionalAxisFeature | undefined {
  const fields = [
    `${prefix}_center`,
    `${prefix}_length`,
    `${prefix}_curvature`,
    `${prefix}_roll`,
  ] as const;
  const present = fields.filter((field) => value[field] !== undefined);
  if (!present.length) return undefined;
  if (present.length !== fields.length) {
    const missing = fields.filter((field) => value[field] === undefined);
    throw new Error(
      `${label} must define the complete ${prefix} feature; missing ${missing.join(", ")}`,
    );
  }
  const length = finite(value[`${prefix}_length`], `${label}.${prefix}_length`);
  if (length <= 0) {
    throw new Error(`${label}.${prefix}_length must be positive`);
  }
  return {
    center: parseLocalTransformation(
      value[`${prefix}_center`],
      `${label}.${prefix}_center`,
    ),
    length,
    curvature: finite(
      value[`${prefix}_curvature`],
      `${label}.${prefix}_curvature`,
    ),
    roll: finite(value[`${prefix}_roll`], `${label}.${prefix}_roll`),
  };
}

export function shapePath(shape?: Shape): {
  length: number;
  curvature: number;
  roll: number;
} {
  if (!shape) return { length: 0, curvature: 0, roll: 0 };
  return shape[0] === "box"
    ? { length: shape[3], curvature: shape[4], roll: shape[5] }
    : { length: shape[2], curvature: shape[3], roll: shape[4] };
}

function parseColor(value: unknown, label: string): string {
  if (typeof value !== "string" || value.length !== 7 || !/^#[0-9a-f]{6}$/i.test(value)) {
    throw new Error(`${label} must be a six-digit hex color`);
  }
  return value;
}

export function forEachTransformation(
  layout: LayoutData,
  callback: (transformation: Transformation, label: string) => void,
) {
  for (const [name, curve] of Object.entries(layout.reference_curves)) {
    callback(curve.starting_frame, `curve ${name}`);
  }
  for (const [name, object] of Object.entries(layout.objects)) {
    callback(object.position, `object ${name}`);
  }
  forEachPlacement(layout, (placement, label) => {
    if (placement.reference && placement.reference.kind !== "local_frame") {
      callback(placement as Transformation, label);
    }
  });
}

function dependencyNodeId(kind: LayoutDependencyKind, name: string): string {
  return `${kind}:${name}`;
}

export function getLayoutDependencyGraph(
  layout: LayoutData,
): LayoutDependencyGraph {
  const nodes: LayoutDependencyNode[] = [
    ...Object.keys(layout.reference_curves).map((name) => ({
      id: dependencyNodeId("curve", name),
      kind: "curve" as const,
      name,
    })),
    ...Object.keys(layout.objects).map((name) => ({
      id: dependencyNodeId("object", name),
      kind: "object" as const,
      name,
    })),
  ];
  const edges: LayoutDependencyEdge[] = [];

  const addReferenceEdge = (
    from: string,
    reference: Reference,
    relation: Exclude<LayoutDependencyRelation, "station_curve">,
  ) => {
    if (reference.kind === "curve") {
      edges.push({
        from,
        to: dependencyNodeId("curve", reference.curve),
        relation,
      });
    } else if (reference.kind === "object_frame") {
      edges.push({
        from,
        to: dependencyNodeId("object", reference.object),
        relation,
        frame: reference.frame,
      });
    }
  };

  for (const [name, curve] of Object.entries(layout.reference_curves)) {
    addReferenceEdge(
      dependencyNodeId("curve", name),
      curve.starting_frame.reference,
      "starting_frame",
    );
  }
  for (const [name, object] of Object.entries(layout.objects)) {
    const from = dependencyNodeId("object", name);
    addReferenceEdge(from, object.position.reference, "position_reference");
    const type = layout.types[object.type];
    for (const frame of objectFrameNames(type, object)) {
      const reference = objectFrameDefinition(type, object, frame)?.placement.reference;
      if (reference && reference.kind !== "local_frame" &&
          !(reference.kind === "object_frame" && reference.object === name)) {
        addReferenceEdge(from, reference, "feature_reference");
      }
    }
    if (
      object.position.reference.kind !== "curve" &&
      object.position.reference_curve &&
      object.position.transformation.some(([operation]) => operation === "ts")
    ) {
      edges.push({
        from,
        to: dependencyNodeId("curve", object.position.reference_curve),
        relation: "station_curve",
      });
    }
  }

  return { nodes, edges };
}

export function layoutFrameNodeId(object: string, frame: string): string {
  return canonicalFrameName(frame) === "anchor" ? `object:${object}` : `frame:${JSON.stringify([object, canonicalFrameName(frame)])}`;
}

export function getLayoutFrameDependencies(layout: LayoutData): Map<string, string[]> {
  // Track individual frames: an externally placed feature need not depend on
  // its object's anchor, so collapsing everything to objects creates false cycles.
  const dependencies = new Map<string, string[]>();
  const frameId = layoutFrameNodeId;
  const referenceId = (reference: PlacementReference | undefined, owner?: string): string | undefined => {
    if (!reference || reference.kind === "local_frame") {
      return owner === undefined ? undefined : frameId(owner, reference?.frame ?? "anchor");
    }
    if (reference.kind === "world") return undefined;
    return reference.kind === "curve" ? `curve:${reference.curve}` : frameId(reference.object, reference.frame);
  };
  for (const [name, curve] of Object.entries(layout.reference_curves)) {
    const dependency = referenceId(curve.starting_frame.reference);
    dependencies.set(`curve:${name}`, dependency ? [dependency] : []);
  }
  for (const [name, object] of Object.entries(layout.objects)) {
    const type = layout.types[object.type];
    const available = objectFrameNames(type, object);
    const positionDependency = referenceId(object.position.reference);
    const anchorDependencies = positionDependency ? [positionDependency] : [];
    if (object.position.reference_curve && object.position.transformation.some(([op]) => op === "ts")) {
      anchorDependencies.push(`curve:${object.position.reference_curve}`);
    }
    dependencies.set(frameId(name, "anchor"), anchorDependencies);
    for (const frame of available) {
      if (frame === "anchor") continue;
      const reference = objectFrameDefinition(type, object, frame)!.placement.reference;
      if (reference?.kind === "local_frame" && !available.includes(reference.frame)) {
        throw new Error(`Object ${name} frame ${frame} references unknown local frame ${reference.frame}`);
      }
      const dependency = referenceId(reference, name);
      dependencies.set(frameId(name, frame), dependency ? [dependency] : []);
    }
  }
  // Also reject broken local chains on uninstantiated types. Beam frames may
  // be supplied by future instances and are checked against each actual object.
  for (const [name, type] of Object.entries(layout.types)) {
    const object: LayoutObject = {type: name, position: {target: "anchor", reference: {kind: "world"}, transformation: []}};
    const available = new Set([...objectFrameNames(type, object), "beam_center", "beam_entry", "beam_exit"]);
    for (const frame of typeFrameNames(type)) {
      if (frame === "anchor") continue;
      const reference = objectFrameDefinition(type, object, frame)!.placement.reference;
      if (reference?.kind === "local_frame") {
        if (!available.has(reference.frame)) throw new Error(`Type ${name} frame ${frame} references unknown local frame ${reference.frame}`);
        dependencies.set(`type-frame:${JSON.stringify([name, frame])}`, [`type-frame:${JSON.stringify([name, reference.frame])}`]);
      }
    }
  }

  return dependencies;
}

function validateDependencyCycles(layout: LayoutData) {
  const dependencies = getLayoutFrameDependencies(layout);
  const state = new Map<string, "visiting" | "visited">();
  const stack: string[] = [];
  const label = (node: string) => {
    const separator = node.indexOf(":");
    return `${node.slice(0, separator)} ${node.slice(separator + 1)}`;
  };
  const visit = (node: string) => {
    if (state.get(node) === "visited") return;
    if (state.get(node) === "visiting") {
      const cycleStart = stack.indexOf(node);
      const cycle = [...stack.slice(cycleStart), node].map(label).join(" -> ");
      throw new Error(`Reference dependency cycle: ${cycle}`);
    }
    state.set(node, "visiting");
    stack.push(node);
    for (const dependency of dependencies.get(node) ?? []) visit(dependency);
    stack.pop();
    state.set(node, "visited");
  };

  for (const node of dependencies.keys()) visit(node);
}

export function parseLayout(value: unknown): LayoutData {
  if (
    !isRecord(value) || !isRecord(value.reference_curves) ||
    !isRecord(value.types) || !isRecord(value.objects)
  ) {
    throw new Error("Expected reference_curves, types and objects dictionaries");
  }
  assertOnlyKeys(value, "layout", ["reference_curves", "types", "objects"]);

  const reference_curves: Record<string, ReferenceCurve> = {};
  for (const [name, raw] of Object.entries(value.reference_curves)) {
    if (!name || !isRecord(raw) || !Array.isArray(raw.segments)) {
      throw new Error(`Invalid curve ${name || "<unnamed>"}`);
    }
    assertOnlyKeys(raw, `reference_curves.${name}`, ["color", "starting_frame", "segments"]);
    const segments = raw.segments.map((segment, index): CurveSegment => {
      if (!Array.isArray(segment) || segment.length !== 3) {
        throw new Error(`Curve ${name}, segment ${index + 1} must have length, angle and roll`);
      }
      const result: CurveSegment = [
        finite(segment[0], "length"),
        finite(segment[1], "angle"),
        finite(segment[2], "roll"),
      ];
      if (result[0] <= 0) throw new Error(`Curve ${name}, segment ${index + 1} must have positive length`);
      return result;
    });
    if (!segments.length) throw new Error(`Curve ${name} needs at least one segment`);
    defineDictionaryEntry(reference_curves, name, {
      color: parseColor(raw.color, `reference_curves.${name}.color`),
      starting_frame: parseTransformation(raw.starting_frame, `reference_curves.${name}.starting_frame`),
      segments,
    });
  }

  const types: Record<string, LayoutType> = {};
  for (const [name, raw] of Object.entries(value.types)) {
    if (!name || !isRecord(raw)) {
      throw new Error(`Invalid type ${name || "<unnamed>"}`);
    }
    assertOnlyKeys(raw, `types.${name}`, [
      "shape",
      "mechanical_center",
      "color",
      "magnetic_center",
      "magnetic_length",
      "magnetic_curvature",
      "magnetic_roll",
      "frames",
    ]);
    const label = `types.${name}`;
    if (raw.mechanical_center !== undefined && raw.shape === undefined) {
      throw new Error(`${label}.mechanical_center requires a shape`);
    }
    const magnetic = parseOptionalAxisFeature(raw, label, "magnetic");
    const frames: Record<string, LocalTransformation> = {};
    if (!isRecord(raw.frames)) {
      throw new Error(`types.${name}.frames must be an object`);
    }
    for (const [frameName, frameDefinition] of Object.entries(
      (raw.frames as Record<string, unknown>) ?? {},
    )) {
      if (!frameName) throw new Error(`types.${name} has an unnamed frame`);
      if (isImplicitTypeFrameName(frameName)) {
        throw new Error(
          `types.${name}.frames.${frameName} is reserved for an implicit type frame`,
        );
      }
      defineDictionaryEntry(frames, frameName, parseLocalTransformation(
        frameDefinition,
        `types.${name}.frames.${frameName}`,
      ));
    }
    defineDictionaryEntry(types, name, {
      ...(raw.shape === undefined
        ? {}
        : { shape: parseShape(raw.shape, `types.${name}.shape`) }),
      color: parseColor(raw.color, `types.${name}.color`),
      ...(raw.mechanical_center === undefined ? {} : {
        mechanical_center: parseLocalTransformation(raw.mechanical_center, `${label}.mechanical_center`),
      }),
      ...(magnetic
        ? {
            magnetic_center: magnetic.center,
            magnetic_length: magnetic.length,
            magnetic_curvature: magnetic.curvature,
            magnetic_roll: magnetic.roll,
          }
        : {}),
      frames,
    });
  }

  const objects: Record<string, LayoutObject> = {};
  for (const [name, raw] of Object.entries(value.objects)) {
    if (!name || !isRecord(raw) || typeof raw.type !== "string" || !raw.type) {
      throw new Error(`Invalid object ${name || "<unnamed>"}`);
    }
    assertOnlyKeys(raw, `objects.${name}`, ["type", "position", "beam_center", "beam_length", "beam_curvature", "beam_roll"]);
    const beam = parseOptionalAxisFeature(raw, `objects.${name}`, "beam");
    if (!hasOwn(types, raw.type)) {
      throw new Error(`objects.${name} references unknown type ${raw.type}`);
    }
    defineDictionaryEntry(objects, name, {
      type: raw.type,
      ...(beam
        ? {
            beam_center: beam.center,
            beam_length: beam.length,
            beam_curvature: beam.curvature,
            beam_roll: beam.roll,
          }
        : {}),
      position: parseObjectPosition(raw.position, `objects.${name}.position`),
    });
  }


  for (const [name, object] of Object.entries(objects)) {
    const target = object.position.target;
    if (!objectFrameNames(types[object.type], object).includes(target)) {
      throw new Error(
        `objects.${name}.position.target references unknown frame ${object.type}.${target}`,
      );
    }
  }

  const result = { reference_curves, types, objects };
  forEachTransformation(result, (transformation, label) => {
    const reference = transformation.reference;
    if (
      reference.kind === "curve" &&
      !hasOwn(reference_curves, reference.curve)
    ) {
      throw new Error(`${label} references unknown curve ${reference.curve}`);
    }
    if (reference.kind === "object_frame") {
      if (!hasOwn(objects, reference.object)) {
        throw new Error(`${label} references unknown object ${reference.object}`);
      }
      const targetType = types[objects[reference.object].type];
      if (
        !objectFrameNames(targetType, objects[reference.object]).includes(reference.frame)
      ) {
        throw new Error(`${label} references unknown frame ${reference.object}.${reference.frame}`);
      }
    }
    const referenceCurve = (transformation as Partial<ObjectPosition>).reference_curve;
    if (referenceCurve && !hasOwn(reference_curves, referenceCurve)) {
      throw new Error(`${label} references unknown projection curve ${referenceCurve}`);
    }
  });
  validateDependencyCycles(result);
  for (const [name, object] of Object.entries(objects)) {
    if (!anchorRelativeFrameNames(types[object.type], object, name).includes(object.position.target)) {
      throw new Error(`objects.${name}.position.target must be rooted in its own anchor through local frame references`);
    }
  }
  return result;
}

export function uniqueName(base: string, names: string[]): string {
  if (!names.includes(base)) return base;
  let index = 2;
  while (names.includes(`${base}${index}`)) index += 1;
  return `${base}${index}`;
}
