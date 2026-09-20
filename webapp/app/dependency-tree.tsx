"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  Box as BoxIcon,
  ChevronRight,
  Globe2,
  Route,
  Waypoints,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import type {DependencyUiState} from "./layout-ui-state";
import {
  canonicalFrameName,
  getLayoutDependencyGraph,
  type LayoutData,
  type LayoutDependencyEdge,
  type LayoutDependencyNode,
  type SelectedEntity,
} from "./layout-data";

type DependencyTreeProps = {
  layout: LayoutData;
  selection: SelectedEntity;
  onSelect: (entity: Exclude<SelectedEntity, null | { kind: "frame" }>) => void;
  initialState?: DependencyUiState;
  onStateChange?: (state: DependencyUiState) => void;
};

const PAGE_SIZE = 50;
type ParentBranch = { edge: LayoutDependencyEdge; index: number };

export function dependencySelectionNodeId(selection: SelectedEntity): string | null {
  if (!selection) return null;
  return selection.kind === "frame" ? `object:${selection.object}`
    : `${selection.kind}:${selection.name}`;
}

function relationLabel(edge: LayoutDependencyEdge): string {
  if (edge.relation === "station_curve") return "ts station curve";
  const relation = edge.relation === "feature_reference" ? "feature reference" : edge.relation === "starting_frame"
    ? "starting frame"
    : "position reference";
  return edge.frame ? `${relation} · ${edge.frame}` : relation;
}

function dependencyBranchId(
  parentBranchId: string,
  edge: LayoutDependencyEdge,
): string {
  return `${parentBranchId}/${edge.relation}:${edge.from}:${edge.frame ?? ""}`;
}

function expandableBranchIds(
  dependentsByAnchor: Map<string, LayoutDependencyEdge[]>,
): string[] {
  const result: string[] = [];
  const visit = (
    anchorId: string,
    branchId: string,
    ancestors: Set<string>,
  ) => {
    const edges = dependentsByAnchor.get(anchorId) ?? [];
    if (!edges.length) return;
    result.push(branchId);
    for (const edge of edges) {
      if (ancestors.has(edge.from)) continue;
      const nextAncestors = new Set(ancestors).add(edge.from);
      visit(
        edge.from,
        dependencyBranchId(branchId, edge),
        nextAncestors,
      );
    }
  };

  visit("world", "world", new Set());
  return result;
}

function sortChildrenByPath(
  edges: LayoutDependencyEdge[],
  graphNodes: Map<string, LayoutDependencyNode>,
  layout: LayoutData,
) {
  const groups = new Map<string, { edge: LayoutDependencyEdge; index: number; path: number }[]>();
  edges.forEach((edge, index) => {
    if (edge.relation === "feature_reference") return;
    const node = graphNodes.get(edge.from);
    if (!node) return;
    const position = node.kind === "object" ? layout.objects[node.name]?.position : undefined;
    const placement = node.kind === "curve"
      ? layout.reference_curves[node.name]?.starting_frame : position;
    if (!placement) return;
    const reference = placement.reference;
    const curve = reference.kind === "curve" ? reference.curve : position?.reference_curve;
    const shifts = placement.transformation.filter(([operation]) => operation === "ts");
    if (!curve || (reference.kind !== "curve" && shifts.length === 0)) return;
    const path = shifts.reduce((sum, [, amount]) => sum + amount, 0);
    if (!Number.isFinite(path)) return;
    const origin = JSON.stringify([
      curve, reference.kind,
      reference.kind === "object_frame" ? reference.object : "",
      reference.kind === "object_frame" ? canonicalFrameName(reference.frame) : "",
    ]);
    const group = groups.get(origin) ?? [];
    group.push({ edge, index, path });
    groups.set(origin, group);
  });

  // A common origin makes ts offsets comparable without resolving geometry.
  // Keep other origins and unknown stations in their existing slots.
  for (const group of groups.values()) {
    const sorted = [...group].sort((a, b) => a.path - b.path);
    group.forEach(({ index }, rank) => { edges[index] = sorted[rank].edge; });
  }
}

export function buildLayoutDependencyHierarchy(layout: LayoutData) {
  const graph = getLayoutDependencyGraph(layout);
  const graphNodes = new Map(graph.nodes.map((node) => [node.id, node]));
  const dependentsByAnchor = new Map<string, LayoutDependencyEdge[]>();

  for (const edge of graph.edges) {
    if (edge.relation === "station_curve") {
      const node = graphNodes.get(edge.from);
      // The referenced object is the hierarchy parent; the station curve
      // remains a dependency in the graph used for positioning.
      if (node?.kind === "object" &&
          layout.objects[node.name]?.position.reference.kind === "object_frame") {
        continue;
      }
    }
    const edges = dependentsByAnchor.get(edge.to) ?? [];
    edges.push(edge);
    dependentsByAnchor.set(edge.to, edges);
  }

  for (const node of graph.nodes) {
    const reference = node.kind === "curve"
      ? layout.reference_curves[node.name]?.starting_frame.reference
      : layout.objects[node.name]?.position.reference;
    if (reference?.kind !== "world") continue;
    const edges = dependentsByAnchor.get("world") ?? [];
    edges.push({
      from: node.id,
      to: "world",
      relation: node.kind === "curve"
        ? "starting_frame"
        : "position_reference",
    });
    dependentsByAnchor.set("world", edges);
  }

  const placementParents = new Map<string, ParentBranch>();
  for (const edges of dependentsByAnchor.values()) {
    sortChildrenByPath(edges, graphNodes, layout);
    edges.forEach((edge, index) => {
      if (edge.relation === "position_reference" || edge.relation === "starting_frame") {
        placementParents.set(edge.from, {edge, index});
      }
    });
  }

  return { graphNodes, dependentsByAnchor, placementParents };
}

/** Locate one occurrence without expanding every branch of a large layout. */
export function dependencySelectionReveal(
  hierarchy: ReturnType<typeof buildLayoutDependencyHierarchy>,
  nodeId: string | null,
) {
  if (!nodeId || !hierarchy.graphNodes.has(nodeId)) return null;
  const trace = (parents: Map<string, ParentBranch>) => {
    const path: ParentBranch[] = [];
    const visited = new Set<string>();
    let current = nodeId;
    while (current !== "world") {
      const parent = parents.get(current);
      if (!parent || visited.has(current)) return null;
      visited.add(current);
      path.push(parent);
      current = parent.edge.to;
    }
    return path.reverse();
  };
  let path = trace(hierarchy.placementParents);
  if (!path) {
    // Independent feature references can create an object-level cycle even
    // when all frame placements are valid. Find a reachable occurrence safely.
    const parents = new Map<string, ParentBranch>();
    const visited = new Set(["world"]);
    const queue = ["world"];
    for (let cursor = 0; cursor < queue.length && !parents.has(nodeId); cursor++) {
      const edges = hierarchy.dependentsByAnchor.get(queue[cursor]) ?? [];
      edges.forEach((edge, index) => {
        if (visited.has(edge.from)) return;
        visited.add(edge.from);
        parents.set(edge.from, {edge, index});
        queue.push(edge.from);
      });
    }
    path = trace(parents);
  }
  if (!path) return null;
  let branchId = "world";
  const expanded: string[] = [];
  const pages = new Map<string, number>();
  for (const {edge, index} of path) {
    expanded.push(branchId);
    pages.set(branchId, Math.floor(index / PAGE_SIZE));
    branchId = dependencyBranchId(branchId, edge);
  }
  return {branchId, expanded, pages};
}

function isSelected(node: LayoutDependencyNode, selection: SelectedEntity) {
  if (node.kind === "curve") {
    return selection?.kind === "curve" && selection.name === node.name;
  }
  return (
    (selection?.kind === "object" && selection.name === node.name) ||
    (selection?.kind === "frame" && selection.object === node.name)
  );
}

type DependencyBranchProps = {
  branchId: string;
  node: LayoutDependencyNode;
  relation?: LayoutDependencyEdge;
  graphNodes: Map<string, LayoutDependencyNode>;
  dependentsByAnchor: Map<string, LayoutDependencyEdge[]>;
  selection: SelectedEntity;
  expanded: Set<string>;
  pages: Map<string, number>;
  onPage: (id: string, page: number) => void;
  ancestors: Set<string>;
  onToggle: (id: string) => void;
  onSelect: DependencyTreeProps["onSelect"];
};

function DependencyBranch({
  branchId,
  node,
  relation,
  graphNodes,
  dependentsByAnchor,
  selection,
  expanded,
  pages,
  onPage,
  ancestors,
  onToggle,
  onSelect,
}: DependencyBranchProps) {
  const edges = dependentsByAnchor.get(node.id) ?? [];
  const page = Math.min(pages.get(branchId) ?? 0, Math.max(0, Math.ceil(edges.length / PAGE_SIZE) - 1));
  const setPage = (value: number) => onPage(branchId, value);
  const hasChildren = Boolean(edges.length);
  const isOpen = hasChildren && expanded.has(branchId);
  const selected = isSelected(node, selection);
  const nextAncestors = new Set(ancestors).add(node.id);
  const Icon = node.kind === "curve" ? Route : BoxIcon;

  return (
    <li
      className="dependency-tree-item"
      role="treeitem"
      aria-expanded={hasChildren ? isOpen : undefined}
      aria-selected={selected}
    >
      <div className={`dependency-tree-row ${selected ? "selected" : ""}`}>
        {hasChildren ? (
          <button
            type="button"
            className="dependency-disclosure"
            aria-label={`${isOpen ? "Collapse" : "Expand"} dependents of ${node.kind} ${node.name}`}
            aria-expanded={isOpen}
            onClick={() => onToggle(branchId)}
          >
            <ChevronRight aria-hidden="true" />
          </button>
        ) : (
          <span className="dependency-disclosure-spacer" aria-hidden="true" />
        )}
        <Icon className="dependency-kind-icon" aria-hidden="true" />
        <button
          type="button"
          className="dependency-node-button"
          aria-label={`Select ${node.kind} ${node.name} from dependency tree`}
          aria-current={selected ? "true" : undefined}
          data-dependency-branch={branchId}
          onClick={() => onSelect({ kind: node.kind, name: node.name })}
        >
          <span className="dependency-node-name">{node.name}</span>
          <span className="dependency-node-kind">{node.kind}</span>
          {relation ? (
            <span className="dependency-relation">{relationLabel(relation)}</span>
          ) : null}
        </button>
      </div>

      {isOpen ? (
        <ul className="dependency-tree-group" role="group">
          {edges.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE).map((edge) => {
            const child = graphNodes.get(edge.from);
            if (!child) return null;
            const childBranchId = dependencyBranchId(branchId, edge);
            if (nextAncestors.has(child.id)) {
              return (
                <li
                  className="dependency-tree-item dependency-cycle"
                  role="treeitem"
                  aria-selected={false}
                  key={childBranchId}
                >
                  <div className="dependency-tree-row">
                    <span className="dependency-disclosure-spacer" aria-hidden="true" />
                    <Waypoints className="dependency-kind-icon" aria-hidden="true" />
                    <span className="dependency-node-copy">
                      <span className="dependency-node-name">{child.name}</span>
                      <span className="dependency-relation">already shown</span>
                    </span>
                  </div>
                </li>
              );
            }
            return (
              <DependencyBranch
                key={childBranchId}
                branchId={childBranchId}
                node={child}
                relation={edge}
                graphNodes={graphNodes}
                dependentsByAnchor={dependentsByAnchor}
                selection={selection}
                expanded={expanded}
                pages={pages}
                onPage={onPage}
                ancestors={nextAncestors}
                onToggle={onToggle}
                onSelect={onSelect}
              />
            );
          })}
          {edges.length > PAGE_SIZE && <li role="none" className="dependency-pages">
            <Button size="xs" variant="ghost" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</Button>
            <span>{page * PAGE_SIZE + 1}–{Math.min(edges.length, (page + 1) * PAGE_SIZE)} of {edges.length}</span>
            <Button size="xs" variant="ghost" disabled={(page + 1) * PAGE_SIZE >= edges.length} onClick={() => setPage(page + 1)}>Next</Button>
          </li>}
        </ul>
      ) : null}
    </li>
  );
}

type WorldRootProps = {
  graphNodes: Map<string, LayoutDependencyNode>;
  dependentsByAnchor: Map<string, LayoutDependencyEdge[]>;
  selection: SelectedEntity;
  expanded: Set<string>;
  pages: Map<string, number>;
  onPage: (id: string, page: number) => void;
  onToggle: (id: string) => void;
  onSelect: DependencyTreeProps["onSelect"];
};

function WorldRoot({
  graphNodes,
  dependentsByAnchor,
  selection,
  expanded,
  pages,
  onPage,
  onToggle,
  onSelect,
}: WorldRootProps) {
  const edges = dependentsByAnchor.get("world") ?? [];
  const page = Math.min(pages.get("world") ?? 0, Math.max(0, Math.ceil(edges.length / PAGE_SIZE) - 1));
  const setPage = (value: number) => onPage("world", value);
  const hasChildren = Boolean(edges.length);
  const isOpen = hasChildren && expanded.has("world");

  return (
    <li
      className="dependency-tree-item dependency-world dependency-root"
      role="treeitem"
      aria-expanded={hasChildren ? isOpen : undefined}
      aria-selected={false}
    >
      <div className="dependency-tree-row">
        {hasChildren ? (
          <button
            type="button"
            className="dependency-disclosure"
            aria-label={`${isOpen ? "Collapse" : "Expand"} dependents from World`}
            aria-expanded={isOpen}
            onClick={() => onToggle("world")}
          >
            <ChevronRight aria-hidden="true" />
          </button>
        ) : (
          <span className="dependency-disclosure-spacer" aria-hidden="true" />
        )}
        <Globe2 className="dependency-kind-icon" aria-hidden="true" />
        <span className="dependency-node-copy">
          <span className="dependency-node-name">World</span>
          <span className="dependency-node-kind">root</span>
          <span className="dependency-relation">global frame</span>
        </span>
      </div>

      {isOpen ? (
        <ul className="dependency-tree-group" role="group">
          {edges.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE).map((edge) => {
            const child = graphNodes.get(edge.from);
            if (!child) return null;
            const childBranchId = dependencyBranchId("world", edge);
            return (
              <DependencyBranch
                key={childBranchId}
                branchId={childBranchId}
                node={child}
                relation={edge}
                graphNodes={graphNodes}
                dependentsByAnchor={dependentsByAnchor}
                selection={selection}
                expanded={expanded}
                pages={pages}
                onPage={onPage}
                ancestors={new Set(["world"])}
                onToggle={onToggle}
                onSelect={onSelect}
              />
            );
          })}
          {edges.length > PAGE_SIZE && <li role="none" className="dependency-pages">
            <Button size="xs" variant="ghost" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</Button>
            <span>{page * PAGE_SIZE + 1}–{Math.min(edges.length, (page + 1) * PAGE_SIZE)} of {edges.length}</span>
            <Button size="xs" variant="ghost" disabled={(page + 1) * PAGE_SIZE >= edges.length} onClick={() => setPage(page + 1)}>Next</Button>
          </li>}
        </ul>
      ) : null}
    </li>
  );
}

export function DependencyTree({
  layout,
  selection,
  onSelect,
  initialState,
  onStateChange,
}: DependencyTreeProps) {
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set(initialState?.expanded));
  const [pages, setPages] = useState(() => new Map<string, number>(Object.entries(initialState?.pages ?? {})));
  const scrollRef = useRef<HTMLDivElement>(null);
  const hierarchy = useMemo(
    () => buildLayoutDependencyHierarchy(layout),
    [layout],
  );
  const {graphNodes, dependentsByAnchor} = hierarchy;
  const selectedNodeId = dependencySelectionNodeId(selection);
  useEffect(() => {
    onStateChange?.({expanded: [...expanded], pages: Object.fromEntries(pages), selection: selectedNodeId});
  }, [expanded, pages, selectedNodeId, onStateChange]);
  const reveal = useMemo(() => dependencySelectionReveal(hierarchy, selectedNodeId),
    [hierarchy, selectedNodeId]);
  const scrolledReveal = useRef<typeof reveal>(null);
  const restoredReveal = useRef(initialState !== undefined && initialState.selection === selectedNodeId ? reveal : undefined);
  useEffect(() => {
    if (reveal === restoredReveal.current) return;
    restoredReveal.current = undefined;
    if (!reveal) return;
    let cancelled = false;
    queueMicrotask(() => {
      if (cancelled) return;
      setExpanded(current => new Set([...current, ...reveal.expanded]));
      setPages(current => new Map([...current, ...reveal.pages]));
    });
    return () => {cancelled = true;};
  }, [reveal]);
  useEffect(() => {
    const container = scrollRef.current;
    if (!reveal || !container || scrolledReveal.current === reveal) return;
    const selected = Array.from(container.querySelectorAll<HTMLElement>('[aria-current="true"]'))
      .find(row => row.dataset.dependencyBranch === reveal.branchId);
    if (!selected) return;
    const bounds = selected.getBoundingClientRect();
    const viewport = container.getBoundingClientRect();
    // Scroll only the tree, leaving the viewer and object editor in place.
    if (bounds.top < viewport.top) container.scrollTop += bounds.top - viewport.top;
    else if (bounds.bottom > viewport.bottom) container.scrollTop += bounds.bottom - viewport.bottom;
    if (bounds.left < viewport.left) container.scrollLeft += bounds.left - viewport.left;
    else if (bounds.right > viewport.right) container.scrollLeft += bounds.right - viewport.right;
    scrolledReveal.current = reveal;
  }, [reveal, expanded, pages]);
  const changePage = (id: string, page: number) => {
    setPages(current => new Map(current).set(id, page));
  };
  const largeTree = graphNodes.size > 2000;
  const branchIds = useMemo(
    () => largeTree ? ["world"] : expandableBranchIds(dependentsByAnchor),
    [dependentsByAnchor, largeTree],
  );
  const allExpanded = Boolean(
    branchIds.length && branchIds.every((id) => expanded.has(id)),
  );
  const anyExpanded = expanded.size > 0;

  const toggle = (id: string) => {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  return (
    <div className="dependency-tree-shell">
      <div className="dependency-tree-toolbar">
        <span>World root</span>
        <div>
          <Button
            type="button"
            variant="outline"
            size="xs"
            disabled={!branchIds.length || allExpanded}
            onClick={() => setExpanded(new Set(branchIds))}
          >
            {largeTree ? "Expand root" : "Expand all"}
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="xs"
            disabled={!anyExpanded}
            onClick={() => setExpanded(new Set())}
          >
            Collapse all
          </Button>
        </div>
      </div>

      <div className="dependency-tree-scroll" ref={scrollRef}>
        <ul
          className="dependency-tree"
          role="tree"
          aria-label="Layout dependencies from World"
        >
          <WorldRoot
            graphNodes={graphNodes}
            dependentsByAnchor={dependentsByAnchor}
            selection={selection}
            expanded={expanded}
            pages={pages}
            onPage={changePage}
            onToggle={toggle}
            onSelect={onSelect}
          />
        </ul>
      </div>
    </div>
  );
}
