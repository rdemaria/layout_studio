import type { Vec3 } from "./layout-data";

export type Camera = {
  azimuth: number;
  elevation: number;
  distance: number;
  target: Vec3;
  axisScale?: Vec3;
};

export type CameraHistory = {
  past: Camera[];
  present: Camera;
  future: Camera[];
  group?: string;
};

export type CameraHistoryAction =
  | {type: "change"; update: (camera: Camera) => Camera; group?: string; reset?: boolean}
  | {type: "back"}
  | {type: "forward"};

const HISTORY_LIMIT = 100;
const copyCamera = (camera: Camera): Camera => ({
  ...camera,
  target: [...camera.target],
  ...(camera.axisScale ? {axisScale: [...camera.axisScale] as Vec3} : {}),
});

export function initialCameraHistory(camera: Camera): CameraHistory {
  return {past: [], present: copyCamera(camera), future: []};
}

function sameCamera(a: Camera, b: Camera): boolean {
  return a.azimuth === b.azimuth && a.elevation === b.elevation &&
    a.distance === b.distance && a.target.every((v, i) => v === b.target[i]) &&
    [0, 1, 2].every(i => (a.axisScale?.[i] ?? 1) === (b.axisScale?.[i] ?? 1));
}

/** One entry per gesture; discrete actions and history navigation end a group. */
export function cameraHistoryReducer(state: CameraHistory, action: CameraHistoryAction): CameraHistory {
  if (action.type === "back") {
    if (!state.past.length) return state;
    return {past: state.past.slice(0, -1), present: state.past[state.past.length - 1],
      future: [state.present, ...state.future]};
  }
  if (action.type === "forward") {
    if (!state.future.length) return state;
    return {past: [...state.past, state.present], present: state.future[0],
      future: state.future.slice(1)};
  }
  const next = action.update(state.present);
  if (action.reset) return initialCameraHistory(next);
  if (sameCamera(state.present, next)) return state;
  const continuing = action.group !== undefined && action.group === state.group;
  return {
    past: continuing ? state.past : [...state.past, state.present].slice(-HISTORY_LIMIT),
    present: copyCamera(next),
    future: [],
    group: action.group,
  };
}
