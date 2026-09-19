export type TouchPoint = {x: number; y: number};
export type TouchPair = TouchPoint & {distance: number};

/** Keep a multi-touch session consumed until every finger has been lifted. */
export class TouchNavigation {
  private points = new Map<number, TouchPoint>();
  private consumed = false;

  get suppressSinglePointer(): boolean { return this.consumed; }

  start(id: number, point: TouchPoint): TouchPair | null {
    this.points.set(id, point);
    if (this.points.size >= 2) this.consumed = true;
    return this.pair();
  }

  move(id: number, point: TouchPoint): TouchPair | null {
    if (!this.points.has(id)) return null;
    this.points.set(id, point);
    return this.pair();
  }

  end(id: number): boolean {
    const consumed = this.consumed;
    this.points.delete(id);
    if (!this.points.size) this.consumed = false;
    return consumed;
  }

  reset(): void {
    this.points.clear();
    this.consumed = false;
  }

  private pair(): TouchPair | null {
    const [first, second] = this.points.values();
    if (!first || !second) return null;
    const distance = Math.hypot(second.x - first.x, second.y - first.y);
    if (distance < 2) return null;
    return {x: (first.x + second.x) / 2, y: (first.y + second.y) / 2, distance};
  }
}
