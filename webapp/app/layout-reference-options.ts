import { layoutFrameNodeId, objectFrameDefinition, objectFrameNames,
  type LayoutData, type PlacementReference } from "./layout-data";

/** Follow only the candidate's ancestors, preserving the frame-level graph. */
export function referenceCandidates(layout: LayoutData, owner: {kind: "curve" | "object"; name: string}) {
  const forbidden = `${owner.kind}:${owner.name}`;
  const safe = new Map<string, boolean>();
  const referenceNode = (ref: PlacementReference | undefined, object?: string): string | undefined => {
    if (!ref || ref.kind === "local_frame") return object === undefined ? undefined
      : layoutFrameNodeId(object, ref?.frame ?? "anchor");
    if (ref.kind === "world") return undefined;
    return ref.kind === "curve" ? `curve:${ref.curve}` : layoutFrameNodeId(ref.object, ref.frame);
  };
  const allowed = (node: string, visiting = new Set<string>()): boolean => {
    if (node === forbidden || visiting.has(node)) return false;
    if (safe.has(node)) return safe.get(node)!;
    visiting.add(node);
    let references: (string | undefined)[] = [];
    if (node.startsWith("curve:")) {
      references = [referenceNode(layout.reference_curves[node.slice(6)]?.starting_frame.reference)];
    } else if (node.startsWith("object:")) {
      const p = layout.objects[node.slice(7)]?.position;
      if (p) references = [referenceNode(p.reference),
        p.reference_curve && p.transformation.some(([op]) => op === "ts") ? `curve:${p.reference_curve}` : undefined];
    } else if (node.startsWith("frame:")) {
      const [object, frame] = JSON.parse(node.slice(6)) as [string, string];
      const instance = layout.objects[object];
      if (instance) references = [referenceNode(objectFrameDefinition(layout.types[instance.type], instance, frame)?.placement.reference, object)];
    }
    const result = references.every(ref => !ref || allowed(ref, visiting));
    visiting.delete(node);
    safe.set(node, result);
    return result;
  };
  return {
    curveAllowed: (name: string) => allowed(`curve:${name}`),
    frames: (name: string) => {
      const object = layout.objects[name];
      return object ? objectFrameNames(layout.types[object.type], object)
        .filter(frame => allowed(layoutFrameNodeId(name, frame))) : [];
    },
  };
}

export function matchingNames(names: string[], query: string, allowed?: (name: string) => boolean, limit = 50): string[] {
  const result: string[] = [];
  const needle = query.toLocaleLowerCase();
  for (const name of names) {
    if (name.toLocaleLowerCase().startsWith(needle) && (!allowed || allowed(name))) {
      result.push(name);
      if (result.length >= limit) break;
    }
  }
  return result;
}
