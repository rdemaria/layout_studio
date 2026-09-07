// Reuse the rendered projection for zoom picking; never resolve the model on
// wheel events. Reciprocal depth is linear on a projected segment or triangle.
export type ZoomPoint = { x: number; y: number; depth: number };
export type ZoomGeometry = {
  faces: { polygon: ZoomPoint[] }[];
  lines: [ZoomPoint, ZoomPoint][];
  points: ZoomPoint[];
};
type Rectangle = { startX: number; startY: number; endX: number; endY: number };

export function zoomFocusDepth(
  geometry: ZoomGeometry,
  x: number,
  y: number,
  rectangle?: Rectangle,
): number | undefined {
  const left = rectangle ? Math.min(rectangle.startX, rectangle.endX) : x - 12;
  const right = rectangle ? Math.max(rectangle.startX, rectangle.endX) : x + 12;
  const top = rectangle ? Math.min(rectangle.startY, rectangle.endY) : y - 12;
  const bottom = rectangle ? Math.max(rectangle.startY, rectangle.endY) : y + 12;
  let bestDistance = rectangle ? Infinity : 12 ** 2;
  let bestDepth: number | undefined;
  const consider = (px: number, py: number, depth: number) => {
    if (!(depth > 0) || !Number.isFinite(depth)) return;
    const distance = (px - x) ** 2 + (py - y) ** 2;
    if (distance < bestDistance - 1e-9 ||
        (Math.abs(distance - bestDistance) <= 1e-9 && depth < (bestDepth ?? Infinity))) {
      bestDistance = distance;
      bestDepth = depth;
    }
  };
  const segment = (a: ZoomPoint, b: ZoomPoint) => {
    // Clip the segment to the requested area before finding its nearest point.
    let low = 0, high = 1;
    const dx = b.x - a.x, dy = b.y - a.y;
    for (const [p, q] of [[-dx, a.x - left], [dx, right - a.x], [-dy, a.y - top], [dy, bottom - a.y]]) {
      if (p === 0) { if (q < 0) return; }
      else if (p < 0) low = Math.max(low, q / p);
      else high = Math.min(high, q / p);
    }
    if (low > high) return;
    const denominator = dx * dx + dy * dy;
    const t = Math.max(low, Math.min(high, denominator ? ((x - a.x) * dx + (y - a.y) * dy) / denominator : 0));
    consider(a.x + dx * t, a.y + dy * t, 1 / ((1 - t) / a.depth + t / b.depth));
  };

  for (const { polygon } of geometry.faces) {
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (const p of polygon) {
      minX = Math.min(minX, p.x); maxX = Math.max(maxX, p.x);
      minY = Math.min(minY, p.y); maxY = Math.max(maxY, p.y);
    }
    if (maxX < left || minX > right || maxY < top || minY > bottom) continue;
    const a = polygon[0];
    for (let i = 1; i + 1 < polygon.length; i += 1) {
      const b = polygon[i], c = polygon[i + 1];
      const determinant = (b.y - c.y) * (a.x - c.x) + (c.x - b.x) * (a.y - c.y);
      if (Math.abs(determinant) < 1e-12) continue;
      const u = ((b.y - c.y) * (x - c.x) + (c.x - b.x) * (y - c.y)) / determinant;
      const v = ((c.y - a.y) * (x - c.x) + (a.x - c.x) * (y - c.y)) / determinant;
      const w = 1 - u - v;
      if (Math.min(u, v, w) >= -1e-9) consider(x, y, 1 / (u / a.depth + v / b.depth + w / c.depth));
    }
    if (bestDistance > 0) {
      for (let i = 0; i < polygon.length; i += 1) segment(polygon[i], polygon[(i + 1) % polygon.length]);
    }
  }
  for (const [a, b] of geometry.lines) segment(a, b);
  for (const p of geometry.points) {
    if (p.x >= left && p.x <= right && p.y >= top && p.y <= bottom) consider(p.x, p.y, p.depth);
  }
  return bestDepth;
}
