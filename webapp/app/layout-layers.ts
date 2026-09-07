import { type SceneGeometry, type ObjectGeometry, type NamedFrameGeometry,
  type FeatureAxisGeometry, type FeatureBoundaryFrameGeometry, type Bounds } from "./layout-geometry";

export type ObjectLayers = {frames: NamedFrameGeometry[]; magneticAxes: FeatureAxisGeometry[];
  magneticFrames: FeatureBoundaryFrameGeometry[]; beamAxes: FeatureAxisGeometry[]; beamFrames: FeatureBoundaryFrameGeometry[]};
export type SceneLayers = {objects: ObjectGeometry[]; byObject: Map<string, ObjectLayers>};

/** Optional layers get their own bounds: external feature references may be far from the anchor. */
export function* buildSceneLayers(scene: SceneGeometry, visibility: {frames: boolean; magnetic: boolean; beam: boolean}): Generator<void, SceneLayers> {
  const result: SceneLayers = {objects: [], byObject: new Map()};
  if (!scene.deferred || !(visibility.frames || visibility.magnetic || visibility.beam)) return result;
  for (const object of scene.objects) {
    const frames = visibility.frames ? scene.deferred.namedFrames(object.name) : [];
    const magnetic = visibility.magnetic ? scene.deferred.feature(object.name, "magnetic") : {axes: [], frames: []};
    const beam = visibility.beam ? scene.deferred.feature(object.name, "beam") : {axes: [], frames: []};
    const bounds: Bounds = {min: [Infinity, Infinity, Infinity], max: [-Infinity, -Infinity, -Infinity]};
    const include = (p: number[]) => {for (let i = 0; i < 3; i++) {bounds.min[i] = Math.min(bounds.min[i], p[i]); bounds.max[i] = Math.max(bounds.max[i], p[i]);}};
    for (const frame of frames) include(frame.frame.o);
    for (const axis of [...magnetic.axes, ...beam.axes]) for (const sample of axis.samples) include(sample.p);
    for (const frame of [...magnetic.frames, ...beam.frames]) for (const point of frame.vertices) include(point);
    if (Number.isFinite(bounds.min[0])) {
      result.objects.push({...object, bounds});
      result.byObject.set(object.name, {frames, magneticAxes: magnetic.axes, magneticFrames: magnetic.frames,
        beamAxes: beam.axes, beamFrames: beam.frames});
    }
    yield;
  }
  return result;
}
