import { type Bounds, type ObjectGeometry, type SceneGeometry, type CurveGeometry,
  type CurveSample, advanceFrame } from "./layout-geometry";

export type ScreenBounds = {left: number; right: number; top: number; bottom: number; depth: number};
export type BoundsProjector = (bounds: Bounds) => ScreenBounds | null;
export type SpatialNode = {bounds: Bounds; start: number; end: number; left?: SpatialNode; right?: SpatialNode};
export type SpatialIndex = {root: SpatialNode | null; order: ObjectGeometry[]};

/** Partition bounds in place. Yield between nodes to keep construction interruptible. */
export function* buildSpatialIndex(objects: ObjectGeometry[]): Generator<void, SpatialIndex> {
  const order = objects.slice();
  if (!order.length) return {root: null, order};
  const makeNode = (start: number, end: number): SpatialNode => {
    const bounds: Bounds = {min: [Infinity, Infinity, Infinity], max: [-Infinity, -Infinity, -Infinity]};
    for (let n = start; n < end; n++) {
      const b = order[n].bounds!;
      for (let a = 0; a < 3; a++) {bounds.min[a] = Math.min(bounds.min[a], b.min[a]); bounds.max[a] = Math.max(bounds.max[a], b.max[a]);}
    }
    return {bounds, start, end};
  };
  const root = makeNode(0, order.length);
  const pending = [root];
  while (pending.length) {
    const node = pending.pop()!;
    if (node.end - node.start > 16) {
      const spans = node.bounds.max.map((v, a) => v - node.bounds.min[a]);
      const axis = spans.indexOf(Math.max(...spans));
      const split = (node.bounds.min[axis] + node.bounds.max[axis]) / 2;
      let lo = node.start, hi = node.end - 1;
      while (lo <= hi) {
        const b = order[lo].bounds!;
        if ((b.min[axis] + b.max[axis]) / 2 < split) lo++;
        else { [order[lo], order[hi]] = [order[hi], order[lo]]; hi--; }
      }
      // Identical/overlapping bounds must still terminate with balanced leaves.
      if (lo === node.start || lo === node.end) lo = (node.start + node.end) >>> 1;
      node.left = makeNode(node.start, lo); node.right = makeNode(lo, node.end);
      pending.push(node.right, node.left);
    }
    yield;
  }
  return {root, order};
}

export type DetailSelection = {
  objects: ObjectGeometry[];
  proxies: {object: ObjectGeometry; bounds: ScreenBounds; count: number}[];
  visited: number;
};

/** Size thresholds are CSS pixels *after* display-axis scaling. */
export function selectSceneDetail(scene: SceneGeometry, index: SpatialIndex, projectBounds: BoundsProjector,
  selectedName?: string, history = new Set<string>()): DetailSelection {
  const result: DetailSelection = {objects: [], proxies: [], visited: 0};
  const pending = index.root ? [index.root] : [];
  const selected = selectedName ? scene.deferred?.objectByName.get(selectedName) : undefined;
  const selectedBounds = selected ? projectBounds(selected.bounds!) : null;
  const selectedContains = (node: SpatialNode) => selected && selected.bounds!.min.every((v, a) =>
    v >= node.bounds.min[a] && selected.bounds!.max[a] <= node.bounds.max[a]);
  const nextHistory = new Set<string>();
  while (pending.length) {
    const node = pending.pop()!;
    result.visited++;
    const box = projectBounds(node.bounds);
    if (!box) continue;
    const pixels = Math.max(box.right - box.left, box.bottom - box.top);
    if (pixels < 5 && !selectedContains(node)) {
      result.proxies.push({object: index.order[node.start], bounds: box, count: node.end - node.start});
      continue;
    }
    if (node.left && node.right) {pending.push(node.right, node.left); continue;}
    for (let n = node.start; n < node.end; n++) {
      const object = index.order[n];
      if (object === selected) continue;
      const bounds = projectBounds(object.bounds!);
      if (!bounds) continue;
      const extent = Math.max(bounds.right - bounds.left, bounds.bottom - bounds.top);
      const threshold = history.has(object.name) ? 5 : 8;
      if (extent >= threshold) {
        result.objects.push(object); nextHistory.add(object.name);
      } else result.proxies.push({object, bounds, count: 1});
    }
  }
  if (selected && selectedBounds) result.objects.push(selected);
  history.clear(); for (const name of nextHistory) history.add(name);
  return result;
}

/** Coalesce nearby subpixel marks without drawing over the same pixel repeatedly. */
export function compactProxies(proxies: DetailSelection["proxies"]): DetailSelection["proxies"] {
  const cells = new Map<string, DetailSelection["proxies"][number]>();
  for (const proxy of proxies) {
    const b = proxy.bounds;
    if (Math.max(b.right - b.left, b.bottom - b.top) > 5) {cells.set(proxy.object.name, proxy); continue;}
    const key = `${Math.floor((b.left + b.right) / 6)},${Math.floor((b.top + b.bottom) / 6)}`;
    const previous = cells.get(key);
    if (!previous) cells.set(key, {...proxy});
    else if (b.depth < previous.bounds.depth) cells.set(key, {...proxy, count: previous.count + proxy.count});
    else previous.count += proxy.count;
  }
  return [...cells.values()];
}

/** Analytic curve sampling adapts to screen error, independently of physical length. */
export function visibleCurveSamples(curve: CurveGeometry, project: (point: CurveSample["p"]) => {x: number; y: number} | null,
  width: number, height: number): (CurveSample | null)[] {
  const result: (CurveSample | null)[] = [];
  for (const segment of curve.segments) {
    const at = (fraction: number): CurveSample => {
      const frame = advanceFrame(segment.startFrame, segment.length * fraction, segment.angle * fraction, segment.roll);
      return {p: frame.o, frame, path: segment.path + segment.length * fraction};
    };
    const visit = (a: CurveSample, b: CurveSample, lo: number, hi: number, level: number) => {
      const middle = at((lo + hi) / 2), p = project(a.p), q = project(b.p), m = project(middle.p);
      const error = p && q && m ? Math.hypot(m.x - (p.x + q.x) / 2, m.y - (p.y + q.y) / 2) : Infinity;
      // Small angular pieces keep the midpoint error conservative even for loops.
      const needsSplit = Math.abs(segment.angle * (hi - lo)) > 0.15 || (segment.angle !== 0 && error > 0.6);
      if (needsSplit && level < 12 && (p || q || m || Math.abs(segment.angle * (hi - lo)) > 0.15)) {
        const half = (lo + hi) / 2;
        visit(a, middle, lo, half, level + 1); visit(middle, b, half, hi, level + 1); return;
      }
      if (!p || !q || (p.x < -10 && q.x < -10) || (p.x > width + 10 && q.x > width + 10)
        || (p.y < -10 && q.y < -10) || (p.y > height + 10 && q.y > height + 10)) {
        result.push(null); return;
      }
      const last = result[result.length - 1];
      if (!last || last.path !== a.path) result.push(a);
      result.push(b);
    };
    visit(at(0), at(1), 0, 1, 0);
  }
  return result;
}

/** Run a generator in small tasks; aborting a load releases its caches promptly. */
export async function runCooperatively<T, P>(iterator: Generator<P, T>, signal: AbortSignal,
  progress?: (value: P) => void): Promise<T> {
  try {
    while (true) {
      if (signal.aborted) throw new DOMException("Aborted", "AbortError");
      const until = performance.now() + 8;
      let latest: P | undefined;
      do {
        const next = iterator.next();
        if (next.done) return next.value;
        latest = next.value;
        if (latest && typeof latest === "object" && "preview" in latest) progress?.(latest);
      } while (performance.now() < until);
      if (latest !== undefined) progress?.(latest);
      await new Promise<void>(resolve => setTimeout(resolve, 0));
    }
  } finally { iterator.return(undefined as T); }
}
