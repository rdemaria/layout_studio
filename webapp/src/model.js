/* Layout Studio geometry model and resolver.
 *
 * This file deliberately has no imports so it can be used both as an ordinary
 * script and as part of the generated single-file index.html build.
 */
(() => {
  "use strict";

  const EPS = 1e-10;
  const DEG = Math.PI / 180;
  const RAD = 180 / Math.PI;
  const OP_NAMES = Object.freeze(["tx", "ty", "ts", "tt", "rx", "ry", "rs"]);
  const ROTATION_OPS = new Set(["rx", "ry", "rs"]);
  const IMPLICIT_FRAMES = Object.freeze([
    "center",
    "magnetic_center",
    "magnetic_entry",
    "magnetic_exit",
  ]);

  class LayoutError extends Error {
    constructor(message, path = "") {
      super(path ? `${path}: ${message}` : message);
      this.name = "LayoutError";
      this.path = path;
    }
  }

  const DEFAULT_LAYOUT = Object.freeze({
    reference_curves: {
      ring: {
        color: "#68d5c8",
        starting_frame: {
          reference: { kind: "world" },
          transformation: [["tx", -5]],
        },
        segments: [
          [5, 0, 0],
          [5, 60 * DEG, 0],
          [4, 0, 0],
          [4, -36 * DEG, 12.6050714929 * DEG],
        ],
      },
    },
    types: {
      quadrupole: {
        shape: ["box", 1.1, 0.9, 1.6, 0.22, 0],
        color: "#f0a84b",
        magnetic_center: { transformation: [] },
        magnetic_length: 1.4,
        frames: {
          survey_mark: {
            transformation: [
              ["tx", 0.45],
              ["ty", 0.35],
            ],
          },
        },
      },
      dipole: {
        shape: ["box", 1.35, 0.78, 2.6, 0.2, 0],
        color: "#d96f55",
        magnetic_center: { transformation: [] },
        magnetic_length: 2.4,
        frames: {
          mechanical_start: { transformation: [["ts", -1.3]] },
          mechanical_end: { transformation: [["ts", 1.3]] },
        },
      },
      monitor: {
        shape: ["cylinder", 0.45, 0.38, 0, 0],
        color: "#91b4dc",
        magnetic_center: { transformation: [] },
        magnetic_length: 0.2,
        frames: {},
      },
    },
    objects: {
      QF1: {
        type: "quadrupole",
        position: {
          target: "center",
          reference: { kind: "curve", curve: "ring" },
          transformation: [["ts", 3.5]],
        },
      },
      B1: {
        type: "dipole",
        position: {
          target: "center",
          reference: { kind: "curve", curve: "ring" },
          transformation: [["ts", 8]],
        },
      },
      BPM1: {
        type: "monitor",
        position: {
          target: "center",
          reference: { kind: "object_frame", object: "B1", frame: "magnetic_exit" },
          transformation: [["tt", 0.65]],
        },
      },
    },
  });

  function clone(value) {
    if (typeof structuredClone === "function") return structuredClone(value);
    return JSON.parse(JSON.stringify(value));
  }

  function isPlainObject(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value);
  }

  function finiteNumber(value, path) {
    if (typeof value !== "number" || !Number.isFinite(value)) {
      throw new LayoutError("expected a finite JSON number", path);
    }
    return value;
  }

  function nonEmptyName(value, path) {
    if (typeof value !== "string" || !value.trim()) {
      throw new LayoutError("expected a non-empty name", path);
    }
    return value;
  }

  function assertKeys(value, required, optional, path) {
    if (!isPlainObject(value)) throw new LayoutError("expected an object", path);
    const allowed = new Set([...required, ...optional]);
    for (const key of Object.keys(value)) {
      if (!allowed.has(key)) throw new LayoutError(`unexpected field ${JSON.stringify(key)}`, path);
    }
    for (const key of required) {
      if (!(key in value)) throw new LayoutError(`missing field ${JSON.stringify(key)}`, path);
    }
  }

  function validateColor(value, path) {
    if (typeof value !== "string" || !/^#[0-9a-fA-F]{6}$/.test(value)) {
      throw new LayoutError("expected a color in #RRGGBB form", path);
    }
  }

  function validateOperations(value, path) {
    if (!Array.isArray(value)) throw new LayoutError("expected an operation list", path);
    value.forEach((item, index) => {
      const itemPath = `${path}[${index}]`;
      if (!Array.isArray(item) || item.length !== 2) {
        throw new LayoutError("expected [operation, value]", itemPath);
      }
      if (!OP_NAMES.includes(item[0])) {
        throw new LayoutError(`unsupported operation ${JSON.stringify(item[0])}`, `${itemPath}[0]`);
      }
      finiteNumber(item[1], `${itemPath}[1]`);
    });
  }

  function validateReference(reference, layout, path) {
    if (!isPlainObject(reference)) throw new LayoutError("expected a reference object", path);
    const kind = reference.kind;
    if (kind === "world") {
      assertKeys(reference, ["kind"], [], path);
      return;
    }
    if (kind === "curve") {
      assertKeys(reference, ["kind", "curve"], [], path);
      const name = nonEmptyName(reference.curve, `${path}.curve`);
      if (!(name in layout.reference_curves)) {
        throw new LayoutError(`unknown curve ${JSON.stringify(name)}`, `${path}.curve`);
      }
      return;
    }
    if (kind === "object_frame") {
      assertKeys(reference, ["kind", "object", "frame"], [], path);
      const objectName = nonEmptyName(reference.object, `${path}.object`);
      if (!(objectName in layout.objects)) {
        throw new LayoutError(`unknown object ${JSON.stringify(objectName)}`, `${path}.object`);
      }
      const object = layout.objects[objectName];
      const type = layout.types[object.type];
      const frameName = nonEmptyName(reference.frame, `${path}.frame`);
      if (!IMPLICIT_FRAMES.includes(frameName) && !(frameName in (type?.frames ?? {}))) {
        throw new LayoutError(
          `unknown frame ${JSON.stringify(frameName)} on ${JSON.stringify(objectName)}`,
          `${path}.frame`,
        );
      }
      return;
    }
    throw new LayoutError(`unsupported reference kind ${JSON.stringify(kind)}`, `${path}.kind`);
  }

  function validateLayout(input, { resolve = true } = {}) {
    assertKeys(input, ["reference_curves", "types", "objects"], [], "layout");
    for (const key of ["reference_curves", "types", "objects"]) {
      if (!isPlainObject(input[key])) throw new LayoutError("expected a name-indexed object", `layout.${key}`);
    }

    const layout = input;
    for (const [name, curve] of Object.entries(layout.reference_curves)) {
      const path = `layout.reference_curves.${name}`;
      nonEmptyName(name, path);
      assertKeys(curve, ["color", "starting_frame", "segments"], [], path);
      validateColor(curve.color, `${path}.color`);
      assertKeys(curve.starting_frame, ["reference", "transformation"], [], `${path}.starting_frame`);
      if (!Array.isArray(curve.segments) || curve.segments.length === 0) {
        throw new LayoutError("expected at least one segment", `${path}.segments`);
      }
      curve.segments.forEach((segment, index) => {
        const segmentPath = `${path}.segments[${index}]`;
        if (!Array.isArray(segment) || segment.length !== 3) {
          throw new LayoutError("expected [length, angle, roll]", segmentPath);
        }
        if (finiteNumber(segment[0], `${segmentPath}[0]`) <= 0) {
          throw new LayoutError("segment length must be positive", `${segmentPath}[0]`);
        }
        finiteNumber(segment[1], `${segmentPath}[1]`);
        finiteNumber(segment[2], `${segmentPath}[2]`);
      });
    }

    for (const [name, type] of Object.entries(layout.types)) {
      const path = `layout.types.${name}`;
      nonEmptyName(name, path);
      assertKeys(type, ["shape", "color", "magnetic_center", "magnetic_length", "frames"], [], path);
      validateColor(type.color, `${path}.color`);
      if (!Array.isArray(type.shape)) throw new LayoutError("expected a shape array", `${path}.shape`);
      const primitive = type.shape[0];
      if (primitive === "box") {
        if (type.shape.length !== 6) {
          throw new LayoutError("box shape must be [\"box\", dx, dy, dz, curvature, roll]", `${path}.shape`);
        }
        for (let i = 1; i <= 3; i += 1) {
          if (finiteNumber(type.shape[i], `${path}.shape[${i}]`) <= 0) {
            throw new LayoutError("box dimensions must be positive", `${path}.shape[${i}]`);
          }
        }
        finiteNumber(type.shape[4], `${path}.shape[4]`);
        finiteNumber(type.shape[5], `${path}.shape[5]`);
      } else if (primitive === "cylinder") {
        if (type.shape.length !== 5) {
          throw new LayoutError(
            "cylinder shape must be [\"cylinder\", radius, dz, curvature, roll]",
            `${path}.shape`,
          );
        }
        if (finiteNumber(type.shape[1], `${path}.shape[1]`) <= 0) {
          throw new LayoutError("cylinder radius must be positive", `${path}.shape[1]`);
        }
        if (finiteNumber(type.shape[2], `${path}.shape[2]`) <= 0) {
          throw new LayoutError("cylinder length must be positive", `${path}.shape[2]`);
        }
        finiteNumber(type.shape[3], `${path}.shape[3]`);
        finiteNumber(type.shape[4], `${path}.shape[4]`);
      } else {
        throw new LayoutError(`unsupported primitive ${JSON.stringify(primitive)}`, `${path}.shape[0]`);
      }
      assertKeys(type.magnetic_center, ["transformation"], [], `${path}.magnetic_center`);
      validateOperations(type.magnetic_center.transformation, `${path}.magnetic_center.transformation`);
      if (finiteNumber(type.magnetic_length, `${path}.magnetic_length`) <= 0) {
        throw new LayoutError("magnetic length must be positive", `${path}.magnetic_length`);
      }
      if (!isPlainObject(type.frames)) throw new LayoutError("expected a frame dictionary", `${path}.frames`);
      for (const [frameName, frame] of Object.entries(type.frames)) {
        const framePath = `${path}.frames.${frameName}`;
        nonEmptyName(frameName, framePath);
        if (IMPLICIT_FRAMES.includes(frameName)) {
          throw new LayoutError("frame name is reserved", framePath);
        }
        assertKeys(frame, ["transformation"], [], framePath);
        validateOperations(frame.transformation, `${framePath}.transformation`);
      }
    }

    for (const [name, object] of Object.entries(layout.objects)) {
      const path = `layout.objects.${name}`;
      nonEmptyName(name, path);
      assertKeys(object, ["type", "position"], [], path);
      const typeName = nonEmptyName(object.type, `${path}.type`);
      if (!(typeName in layout.types)) {
        throw new LayoutError(`unknown type ${JSON.stringify(typeName)}`, `${path}.type`);
      }
      assertKeys(
        object.position,
        ["target", "reference", "transformation"],
        ["reference_curve"],
        `${path}.position`,
      );
      const target = nonEmptyName(object.position.target, `${path}.position.target`);
      if (!IMPLICIT_FRAMES.includes(target) && !(target in layout.types[typeName].frames)) {
        throw new LayoutError(`unknown target frame ${JSON.stringify(target)}`, `${path}.position.target`);
      }
      validateOperations(object.position.transformation, `${path}.position.transformation`);
    }

    // References are checked only after all dictionaries have been structurally validated.
    for (const [name, curve] of Object.entries(layout.reference_curves)) {
      const path = `layout.reference_curves.${name}.starting_frame`;
      validateReference(curve.starting_frame.reference, layout, `${path}.reference`);
      const hasTs = curve.starting_frame.transformation.some(([op]) => op === "ts");
      if (hasTs && curve.starting_frame.reference.kind !== "curve") {
        throw new LayoutError("ts requires a curve reference in a curve starting frame", `${path}.transformation`);
      }
    }

    for (const [name, object] of Object.entries(layout.objects)) {
      const path = `layout.objects.${name}.position`;
      const position = object.position;
      validateReference(position.reference, layout, `${path}.reference`);
      const hasTs = position.transformation.some(([op]) => op === "ts");
      if (position.reference.kind === "curve") {
        if ("reference_curve" in position) {
          throw new LayoutError("reference_curve is redundant with a curve reference", `${path}.reference_curve`);
        }
      } else if (hasTs) {
        const curveName = nonEmptyName(position.reference_curve, `${path}.reference_curve`);
        if (!(curveName in layout.reference_curves)) {
          throw new LayoutError(`unknown curve ${JSON.stringify(curveName)}`, `${path}.reference_curve`);
        }
      } else if ("reference_curve" in position) {
        throw new LayoutError("reference_curve is only valid when transformation contains ts", `${path}.reference_curve`);
      }
    }

    if (resolve) new Resolver(layout).resolveAll();
    return layout;
  }

  function v(x = 0, y = 0, z = 0) {
    return [x, y, z];
  }
  function add(a, b) {
    return [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
  }
  function sub(a, b) {
    return [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
  }
  function scale(a, q) {
    return [a[0] * q, a[1] * q, a[2] * q];
  }
  function dot(a, b) {
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  }
  function cross(a, b) {
    return [
      a[1] * b[2] - a[2] * b[1],
      a[2] * b[0] - a[0] * b[2],
      a[0] * b[1] - a[1] * b[0],
    ];
  }
  function norm(a) {
    return Math.hypot(a[0], a[1], a[2]);
  }
  function unit(a) {
    const n = norm(a);
    if (n < EPS) throw new LayoutError("cannot normalize a zero vector");
    return scale(a, 1 / n);
  }
  function lerp(a, b, t) {
    return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
  }

  function rotateVector(vector, axis, angle) {
    if (Math.abs(angle) < EPS) return vector.slice();
    const u = unit(axis);
    const c = Math.cos(angle);
    const s = Math.sin(angle);
    return add(add(scale(vector, c), scale(cross(u, vector), s)), scale(u, dot(u, vector) * (1 - c)));
  }

  function identityFrame() {
    return { o: [0, 0, 0], x: [1, 0, 0], y: [0, 1, 0], s: [0, 0, 1] };
  }

  function cloneFrame(frame) {
    return { o: frame.o.slice(), x: frame.x.slice(), y: frame.y.slice(), s: frame.s.slice() };
  }

  function mapLocalVector(frame, local) {
    return add(add(scale(frame.x, local[0]), scale(frame.y, local[1])), scale(frame.s, local[2]));
  }

  function mapLocalPoint(frame, local) {
    return add(frame.o, mapLocalVector(frame, local));
  }

  function composeFrames(parent, local) {
    return {
      o: mapLocalPoint(parent, local.o),
      x: mapLocalVector(parent, local.x),
      y: mapLocalVector(parent, local.y),
      s: mapLocalVector(parent, local.s),
    };
  }

  function inverseFrame(frame) {
    const x = [frame.x[0], frame.y[0], frame.s[0]];
    const y = [frame.x[1], frame.y[1], frame.s[1]];
    const s = [frame.x[2], frame.y[2], frame.s[2]];
    return {
      o: [-dot(frame.o, frame.x), -dot(frame.o, frame.y), -dot(frame.o, frame.s)],
      x,
      y,
      s,
    };
  }

  function rotateFrame(frame, axis, angle) {
    frame.x = rotateVector(frame.x, axis, angle);
    frame.y = rotateVector(frame.y, axis, angle);
    frame.s = rotateVector(frame.s, axis, angle);
    return frame;
  }

  function applyRigidOperation(frame, operation, amount) {
    switch (operation) {
      case "tx":
        frame.o = add(frame.o, scale(frame.x, amount));
        break;
      case "ty":
        frame.o = add(frame.o, scale(frame.y, amount));
        break;
      case "tt":
        frame.o = add(frame.o, scale(frame.s, amount));
        break;
      case "rx":
        rotateFrame(frame, frame.x.slice(), amount);
        break;
      case "ry":
        rotateFrame(frame, frame.y.slice(), amount);
        break;
      case "rs":
        rotateFrame(frame, frame.s.slice(), amount);
        break;
      default:
        throw new LayoutError(`operation ${operation} needs a path context`);
    }
    return frame;
  }

  function applyRigidOperations(inputFrame, operations) {
    const frame = cloneFrame(inputFrame);
    for (const [operation, amount] of operations) {
      if (operation === "ts") throw new LayoutError("ts requires a curve or type path context");
      applyRigidOperation(frame, operation, amount);
    }
    return frame;
  }

  function typeShape(type) {
    const shape = type.shape;
    if (shape[0] === "box") {
      return {
        primitive: "box",
        dx: shape[1],
        dy: shape[2],
        dz: shape[3],
        curvature: shape[4],
        roll: shape[5],
      };
    }
    return {
      primitive: "cylinder",
      radius: shape[1],
      dz: shape[2],
      curvature: shape[3],
      roll: shape[4],
    };
  }

  function pathFrameAt(curvature, roll, station) {
    const frame = identityFrame();
    if (Math.abs(curvature) < EPS) {
      frame.o = [0, 0, station];
      return frame;
    }
    const theta = curvature * station;
    const normal = [-Math.cos(roll), -Math.sin(roll), 0];
    const binormal = [Math.sin(roll), -Math.cos(roll), 0];
    frame.o = add(scale([0, 0, 1], Math.sin(theta) / curvature), scale(normal, (1 - Math.cos(theta)) / curvature));
    rotateFrame(frame, binormal, theta);
    return frame;
  }

  function typePathFrame(type, station) {
    const shape = typeShape(type);
    return pathFrameAt(shape.curvature, shape.roll, station);
  }

  function applyTypeOperations(type, operations) {
    let frame = identityFrame();
    let pathStation = 0;
    let stationIsReliable = true;
    for (const [operation, amount] of operations) {
      if (operation === "ts") {
        const before = typePathFrame(type, pathStation);
        const after = typePathFrame(type, pathStation + amount);
        const delta = composeFrames(inverseFrame(before), after);
        frame = composeFrames(frame, delta);
        pathStation += amount;
      } else {
        applyRigidOperation(frame, operation, amount);
        if (operation === "tt") stationIsReliable = false;
      }
    }
    return { frame, pathStation, stationIsReliable };
  }

  function typeLocalFrame(type, frameName) {
    if (frameName === "center") {
      return { frame: identityFrame(), pathStation: 0, stationIsReliable: true };
    }
    let definition;
    if (frameName === "magnetic_center" || frameName === "magnetic_entry" || frameName === "magnetic_exit") {
      definition = type.magnetic_center;
    } else {
      definition = type.frames[frameName];
    }
    if (!definition) throw new LayoutError(`unknown type-local frame ${JSON.stringify(frameName)}`);
    let state = applyTypeOperations(type, definition.transformation);
    if (frameName === "magnetic_entry" || frameName === "magnetic_exit") {
      const delta = frameName === "magnetic_entry" ? -type.magnetic_length / 2 : type.magnetic_length / 2;
      const next = applyTypeOperationsFromState(type, state, [["ts", delta]]);
      state = next;
    }
    return state;
  }

  function applyTypeOperationsFromState(type, initial, operations) {
    let frame = cloneFrame(initial.frame);
    let pathStation = initial.pathStation;
    let stationIsReliable = initial.stationIsReliable;
    for (const [operation, amount] of operations) {
      if (operation === "ts") {
        const before = typePathFrame(type, pathStation);
        const after = typePathFrame(type, pathStation + amount);
        const delta = composeFrames(inverseFrame(before), after);
        frame = composeFrames(frame, delta);
        pathStation += amount;
      } else {
        applyRigidOperation(frame, operation, amount);
        if (operation === "tt") stationIsReliable = false;
      }
    }
    return { frame, pathStation, stationIsReliable };
  }

  function stationMapCopy(stations) {
    return Object.assign(Object.create(null), stations ?? {});
  }

  function makePose(frame, stations = null) {
    return { frame, stations: stationMapCopy(stations) };
  }

  function bendData(frame, roll) {
    return {
      normal: add(scale(frame.x, -Math.cos(roll)), scale(frame.y, -Math.sin(roll))),
      binormal: add(scale(frame.x, Math.sin(roll)), scale(frame.y, -Math.cos(roll))),
    };
  }

  function advanceSegment(frame, length, angle, roll, distance = length) {
    const out = cloneFrame(frame);
    const u = distance;
    if (Math.abs(angle) < EPS) {
      out.o = add(out.o, scale(out.s, u));
      return out;
    }
    const curvature = angle / length;
    const theta = curvature * u;
    const { normal, binormal } = bendData(out, roll);
    out.o = add(
      out.o,
      add(scale(out.s, Math.sin(theta) / curvature), scale(normal, (1 - Math.cos(theta)) / curvature)),
    );
    rotateFrame(out, binormal, theta);
    return out;
  }

  class Resolver {
    constructor(layout) {
      this.layout = layout;
      this.curveCache = new Map();
      this.objectCenterCache = new Map();
      this.objectFrameCache = new Map();
      this.resolving = [];
    }

    enter(key) {
      const index = this.resolving.indexOf(key);
      if (index >= 0) {
        throw new LayoutError(`dependency cycle: ${[...this.resolving.slice(index), key].join(" → ")}`);
      }
      this.resolving.push(key);
    }

    leave(key) {
      const actual = this.resolving.pop();
      if (actual !== key) throw new Error("resolver stack corruption");
    }

    resolveAll() {
      for (const name of Object.keys(this.layout.reference_curves)) this.curveData(name);
      for (const name of Object.keys(this.layout.objects)) this.objectCenter(name);
      return this;
    }

    curveData(name) {
      if (this.curveCache.has(name)) return this.curveCache.get(name);
      const curve = this.layout.reference_curves[name];
      if (!curve) throw new LayoutError(`unknown curve ${JSON.stringify(name)}`);
      const key = `curve:${name}`;
      this.enter(key);
      try {
        const startPose = this.resolveTransformation(curve.starting_frame, `curve ${name} starting frame`);
        let frame = startPose.frame;
        let station = 0;
        const segments = [];
        const samples = [{ station: 0, frame: cloneFrame(frame) }];
        for (let index = 0; index < curve.segments.length; index += 1) {
          const [length, angle, roll] = curve.segments[index];
          const startFrame = cloneFrame(frame);
          const { normal, binormal } = bendData(startFrame, roll);
          const segment = {
            index,
            station,
            length,
            angle,
            roll,
            curvature: Math.abs(angle) < EPS ? 0 : angle / length,
            startFrame,
            normal,
            binormal,
          };
          segments.push(segment);
          const divisions = Math.max(1, Math.min(96, Math.ceil(Math.max(length / 1.5, Math.abs(angle) / (Math.PI / 36)))));
          for (let i = 1; i <= divisions; i += 1) {
            const u = (length * i) / divisions;
            samples.push({ station: station + u, frame: advanceSegment(startFrame, length, angle, roll, u) });
          }
          frame = advanceSegment(startFrame, length, angle, roll, length);
          station += length;
        }
        const stations = stationMapCopy(startPose.stations);
        stations[name] = 0;
        const data = {
          name,
          definition: curve,
          startPose: makePose(cloneFrame(startPose.frame), stations),
          segments,
          length: station,
          endFrame: cloneFrame(frame),
          samples,
        };
        this.curveCache.set(name, data);
        return data;
      } finally {
        this.leave(key);
      }
    }

    curveFrame(name, station) {
      const data = this.curveData(name);
      let frame;
      if (station < 0) {
        frame = cloneFrame(data.startPose.frame);
        frame.o = add(frame.o, scale(frame.s, station));
      } else if (station > data.length) {
        frame = cloneFrame(data.endFrame);
        frame.o = add(frame.o, scale(frame.s, station - data.length));
      } else if (station >= data.length - EPS) {
        frame = cloneFrame(data.endFrame);
      } else {
        let low = 0;
        let high = data.segments.length - 1;
        while (low < high) {
          const mid = Math.floor((low + high + 1) / 2);
          if (data.segments[mid].station <= station) low = mid;
          else high = mid - 1;
        }
        const segment = data.segments[low];
        frame = advanceSegment(segment.startFrame, segment.length, segment.angle, segment.roll, station - segment.station);
      }
      const stations = stationMapCopy(data.startPose.stations);
      stations[name] = station;
      return makePose(frame, stations);
    }

    referencePose(reference) {
      if (reference.kind === "world") return makePose(identityFrame());
      if (reference.kind === "curve") return this.curveFrame(reference.curve, 0);
      return this.objectFrame(reference.object, reference.frame);
    }

    resolveTransformation(spec, label = "transformation") {
      const operations = spec.transformation ?? [];
      const reference = spec.reference;
      const ts = operations.reduce((sum, [operation, amount]) => (operation === "ts" ? sum + amount : sum), 0);
      const nonTs = operations.filter(([operation]) => operation !== "ts");

      if (reference.kind === "curve") {
        const pose = this.curveFrame(reference.curve, ts);
        const frame = applyRigidOperations(pose.frame, nonTs);
        const stations = stationMapCopy(pose.stations);
        if (nonTs.some(([operation]) => operation === "tt")) delete stations[reference.curve];
        return makePose(frame, stations);
      }

      const referencePose = this.referencePose(reference);
      if (Math.abs(ts) > EPS || operations.some(([operation]) => operation === "ts")) {
        const curveName = spec.reference_curve;
        if (!curveName) throw new LayoutError(`${label} uses ts without reference_curve`);
        let baseStation = referencePose.stations[curveName];
        if (!Number.isFinite(baseStation)) baseStation = this.inferCurveStation(curveName, referencePose.frame.o);
        const pose = this.curveFrame(curveName, baseStation + ts);
        const frame = applyRigidOperations(pose.frame, nonTs);
        const stations = stationMapCopy(pose.stations);
        if (nonTs.some(([operation]) => operation === "tt")) delete stations[curveName];
        return makePose(frame, stations);
      }

      const frame = applyRigidOperations(referencePose.frame, operations);
      const stations = stationMapCopy(referencePose.stations);
      if (operations.some(([operation]) => operation === "tt")) {
        for (const curveName of Object.keys(stations)) delete stations[curveName];
      }
      return makePose(frame, stations);
    }

    objectCenter(name) {
      if (this.objectCenterCache.has(name)) return this.objectCenterCache.get(name);
      const object = this.layout.objects[name];
      if (!object) throw new LayoutError(`unknown object ${JSON.stringify(name)}`);
      const type = this.layout.types[object.type];
      if (!type) throw new LayoutError(`object ${name} has unknown type ${JSON.stringify(object.type)}`);
      const key = `object:${name}`;
      this.enter(key);
      try {
        const targetPose = this.resolveTransformation(object.position, `object ${name} position`);
        const targetLocal = typeLocalFrame(type, object.position.target);
        const centerFrame = composeFrames(targetPose.frame, inverseFrame(targetLocal.frame));
        const stations = stationMapCopy(targetPose.stations);
        if (targetLocal.stationIsReliable) {
          for (const curveName of Object.keys(stations)) stations[curveName] -= targetLocal.pathStation;
        } else {
          for (const curveName of Object.keys(stations)) delete stations[curveName];
        }
        const pose = makePose(centerFrame, stations);
        this.objectCenterCache.set(name, pose);
        return pose;
      } finally {
        this.leave(key);
      }
    }

    objectFrame(objectName, frameName) {
      const cacheKey = `${objectName}\u0000${frameName}`;
      if (this.objectFrameCache.has(cacheKey)) return this.objectFrameCache.get(cacheKey);
      const object = this.layout.objects[objectName];
      if (!object) throw new LayoutError(`unknown object ${JSON.stringify(objectName)}`);
      const type = this.layout.types[object.type];
      const center = this.objectCenter(objectName);
      const local = typeLocalFrame(type, frameName);
      const frame = composeFrames(center.frame, local.frame);
      const stations = stationMapCopy(center.stations);
      if (local.stationIsReliable) {
        for (const curveName of Object.keys(stations)) stations[curveName] += local.pathStation;
      } else {
        for (const curveName of Object.keys(stations)) delete stations[curveName];
      }
      const pose = makePose(frame, stations);
      this.objectFrameCache.set(cacheKey, pose);
      return pose;
    }

    inferCurveStation(curveName, point) {
      const data = this.curveData(curveName);
      const candidates = [];
      const addCandidate = (segment, u) => {
        if (u < -1e-8 || u > segment.length + 1e-8) return;
        const clamped = Math.max(0, Math.min(segment.length, u));
        const frame = advanceSegment(
          segment.startFrame,
          segment.length,
          segment.angle,
          segment.roll,
          clamped,
        );
        const distance = norm(sub(point, frame.o));
        candidates.push({ station: segment.station + clamped, distance });
      };

      for (const segment of data.segments) {
        const delta = sub(point, segment.startFrame.o);
        const along = dot(delta, segment.startFrame.s);
        if (Math.abs(segment.curvature) < EPS) {
          addCandidate(segment, along);
          continue;
        }
        const normalDistance = dot(delta, segment.normal);
        const k = segment.curvature;
        const d = normalDistance - 1 / k;
        const base = Math.atan2(-along, d);
        const theta0 = 0;
        const theta1 = segment.angle;
        const lo = Math.min(theta0, theta1) - 1e-10;
        const hi = Math.max(theta0, theta1) + 1e-10;
        const nMin = Math.ceil((lo - base) / Math.PI);
        const nMax = Math.floor((hi - base) / Math.PI);
        for (let n = nMin; n <= nMax; n += 1) {
          addCandidate(segment, (base + n * Math.PI) / k);
        }
      }

      // De-duplicate roots shared by adjacent segments.
      candidates.sort((a, b) => a.station - b.station || a.distance - b.distance);
      const unique = [];
      for (const candidate of candidates) {
        const previous = unique[unique.length - 1];
        if (previous && Math.abs(previous.station - candidate.station) < 1e-8) {
          if (candidate.distance < previous.distance) unique[unique.length - 1] = candidate;
        } else {
          unique.push(candidate);
        }
      }
      if (unique.length === 0) {
        throw new LayoutError(
          `no curve station on ${JSON.stringify(curveName)} has a normal plane containing ` +
            `[${point.map((q) => q.toPrecision(7)).join(", ")}]`,
        );
      }
      unique.sort((a, b) => a.distance - b.distance || a.station - b.station);
      const best = unique[0];
      const tolerance = Math.max(1e-8, best.distance * 1e-9, data.length * 1e-12);
      if (unique.length > 1 && Math.abs(unique[1].distance - best.distance) <= tolerance && Math.abs(unique[1].station - best.station) > 1e-8) {
        throw new LayoutError(
          `ambiguous closest stations ${best.station.toPrecision(9)} and ${unique[1].station.toPrecision(9)} ` +
            `on ${JSON.stringify(curveName)}`,
        );
      }
      return best.station;
    }

    objectGeometry(name) {
      const object = this.layout.objects[name];
      const type = this.layout.types[object.type];
      return { name, object, type, center: this.objectCenter(name), shape: typeShape(type) };
    }
  }

  function poseToMadEuler(frame) {
    const sinPhi = Math.max(-1, Math.min(1, frame.s[1]));
    const phi = Math.asin(sinPhi);
    const cosPhi = Math.cos(phi);
    let theta;
    let psi;
    if (Math.abs(cosPhi) > 1e-9) {
      theta = Math.atan2(frame.s[0], frame.s[2]);
      psi = Math.atan2(frame.x[1], frame.y[1]);
    } else {
      // Gimbal lock: preserve a stable equivalent with psi = 0.
      theta = Math.atan2(-frame.x[2], frame.x[0]);
      psi = 0;
    }
    return { theta, phi, psi };
  }

  function frameSummary(frame) {
    const { theta, phi, psi } = poseToMadEuler(frame);
    return {
      x: frame.o[0],
      y: frame.o[1],
      z: frame.o[2],
      theta,
      phi,
      psi,
    };
  }

  function sampleTypeAxis(type, divisions = null) {
    const shape = typeShape(type);
    const count = divisions ?? Math.max(2, Math.min(48, Math.ceil(Math.abs(shape.curvature * shape.dz) / (Math.PI / 36)) + 1));
    const points = [];
    for (let i = 0; i <= count; i += 1) {
      const station = -shape.dz / 2 + (shape.dz * i) / count;
      points.push({ station, frame: typePathFrame(type, station) });
    }
    return points;
  }

  function layoutBounds(layout, resolver = new Resolver(layout).resolveAll()) {
    const points = [[0, 0, 0]];
    for (const curveName of Object.keys(layout.reference_curves)) {
      for (const sample of resolver.curveData(curveName).samples) points.push(sample.frame.o);
    }
    for (const objectName of Object.keys(layout.objects)) {
      const geometry = resolver.objectGeometry(objectName);
      const shape = geometry.shape;
      const center = geometry.center.frame;
      const axis = sampleTypeAxis(geometry.type, 4);
      const radius = shape.primitive === "box" ? Math.hypot(shape.dx, shape.dy) / 2 : shape.radius;
      for (const sample of axis) {
        const world = composeFrames(center, sample.frame);
        points.push(world.o);
        points.push(add(world.o, scale(world.x, radius)));
        points.push(add(world.o, scale(world.x, -radius)));
        points.push(add(world.o, scale(world.y, radius)));
        points.push(add(world.o, scale(world.y, -radius)));
      }
    }
    const min = [Infinity, Infinity, Infinity];
    const max = [-Infinity, -Infinity, -Infinity];
    for (const point of points) {
      for (let i = 0; i < 3; i += 1) {
        min[i] = Math.min(min[i], point[i]);
        max[i] = Math.max(max[i], point[i]);
      }
    }
    return { min, max, center: scale(add(min, max), 0.5), size: sub(max, min), points };
  }

  function referenceAnchor(reference) {
    if (reference.kind === "world") return "world";
    if (reference.kind === "curve") return `curve:${reference.curve}`;
    return `object:${reference.object}`;
  }

  function buildDependencyGraph(layout) {
    const nodes = new Map();
    nodes.set("world", { id: "world", kind: "world", name: "World", label: "global frame" });
    for (const [name, curve] of Object.entries(layout.reference_curves)) {
      nodes.set(`curve:${name}`, {
        id: `curve:${name}`,
        kind: "curve",
        name,
        label: `${curve.segments.length} segment${curve.segments.length === 1 ? "" : "s"}`,
      });
    }
    for (const [name, object] of Object.entries(layout.objects)) {
      nodes.set(`object:${name}`, {
        id: `object:${name}`,
        kind: "object",
        name,
        type: object.type,
        label: object.type,
      });
    }
    const children = new Map();
    const add = (anchor, edge) => {
      if (!children.has(anchor)) children.set(anchor, []);
      children.get(anchor).push(edge);
    };
    for (const [name, curve] of Object.entries(layout.reference_curves)) {
      const reference = curve.starting_frame.reference;
      add(referenceAnchor(reference), {
        id: `curve:${name}`,
        relation:
          reference.kind === "world"
            ? "starting frame from World"
            : reference.kind === "curve"
              ? `starting frame on ${reference.curve}`
              : `starting frame from ${reference.object} → ${reference.frame}`,
      });
    }
    for (const [name, object] of Object.entries(layout.objects)) {
      const reference = object.position.reference;
      add(referenceAnchor(reference), {
        id: `object:${name}`,
        relation:
          reference.kind === "world"
            ? `placed from World → ${object.position.target}`
            : reference.kind === "curve"
              ? `placed on ${reference.curve} → ${object.position.target}`
              : `${reference.object} → ${reference.frame} places ${object.position.target}`,
      });
    }
    for (const edges of children.values()) {
      edges.sort((a, b) => {
        const na = nodes.get(a.id);
        const nb = nodes.get(b.id);
        return (na?.kind ?? "").localeCompare(nb?.kind ?? "") || (na?.name ?? "").localeCompare(nb?.name ?? "");
      });
    }
    return { nodes, children };
  }

  function uniqueName(dictionary, base) {
    if (!(base in dictionary)) return base;
    let index = 2;
    while (`${base}_${index}` in dictionary) index += 1;
    return `${base}_${index}`;
  }

  function renameKey(dictionary, oldName, newName) {
    if (oldName === newName) return;
    const entries = Object.entries(dictionary);
    const index = entries.findIndex(([name]) => name === oldName);
    if (index < 0) throw new LayoutError(`unknown name ${JSON.stringify(oldName)}`);
    entries[index][0] = newName;
    for (const key of Object.keys(dictionary)) delete dictionary[key];
    for (const [name, value] of entries) dictionary[name] = value;
  }

  function walkReferences(layout, callback) {
    for (const [curveName, curve] of Object.entries(layout.reference_curves)) {
      callback(curve.starting_frame.reference, { kind: "curve", name: curveName });
    }
    for (const [objectName, object] of Object.entries(layout.objects)) {
      callback(object.position.reference, { kind: "object", name: objectName });
    }
  }

  function operationUnit(operation) {
    return ROTATION_OPS.has(operation) ? "degree" : "metres";
  }

  function operationDisplayValue(operation, value) {
    return ROTATION_OPS.has(operation) ? value * RAD : value;
  }

  function operationJsonValue(operation, value) {
    return ROTATION_OPS.has(operation) ? value * DEG : value;
  }

  globalThis.LayoutStudioModel = Object.freeze({
    DEG,
    RAD,
    EPS,
    OP_NAMES,
    ROTATION_OPS,
    IMPLICIT_FRAMES,
    LayoutError,
    DEFAULT_LAYOUT,
    clone,
    validateLayout,
    Resolver,
    identityFrame,
    cloneFrame,
    composeFrames,
    inverseFrame,
    mapLocalPoint,
    mapLocalVector,
    add,
    sub,
    scale,
    dot,
    cross,
    norm,
    unit,
    lerp,
    rotateVector,
    typeShape,
    typePathFrame,
    typeLocalFrame,
    sampleTypeAxis,
    advanceSegment,
    poseToMadEuler,
    frameSummary,
    layoutBounds,
    buildDependencyGraph,
    uniqueName,
    renameKey,
    walkReferences,
    operationUnit,
    operationDisplayValue,
    operationJsonValue,
  });
})();
