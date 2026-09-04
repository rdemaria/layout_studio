"use client";

import { useMemo, useState } from "react";
import {
  Box as BoxIcon,
  ChevronRight,
  Globe2,
  Route,
  Waypoints,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
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
};

function relationLabel(edge: LayoutDependencyEdge): string {
  if (edge.relation === "station_curve") return "ts station curve";
  const relation = edge.relation === "starting_frame"
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

export function buildLayoutDependencyHierarchy(layout: LayoutData) {
  const graph = getLayoutDependencyGraph(layout);
  const graphNodes = new Map(graph.nodes.map((node) => [node.id, node]));
  const dependentsByAnchor = new Map<string, LayoutDependencyEdge[]>();

  for (const edge of graph.edges) {
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

  return { graphNodes, dependentsByAnchor };
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
  ancestors,
  onToggle,
  onSelect,
}: DependencyBranchProps) {
  const edges = dependentsByAnchor.get(node.id) ?? [];
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
          {edges.map((edge) => {
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
                      <span className="dependency-relation">cycle</span>
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
                ancestors={nextAncestors}
                onToggle={onToggle}
                onSelect={onSelect}
              />
            );
          })}
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
  onToggle: (id: string) => void;
  onSelect: DependencyTreeProps["onSelect"];
};

function WorldRoot({
  graphNodes,
  dependentsByAnchor,
  selection,
  expanded,
  onToggle,
  onSelect,
}: WorldRootProps) {
  const edges = dependentsByAnchor.get("world") ?? [];
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
          {edges.map((edge) => {
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
                ancestors={new Set(["world"])}
                onToggle={onToggle}
                onSelect={onSelect}
              />
            );
          })}
        </ul>
      ) : null}
    </li>
  );
}

export function DependencyTree({
  layout,
  selection,
  onSelect,
}: DependencyTreeProps) {
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const { graphNodes, dependentsByAnchor } = useMemo(
    () => buildLayoutDependencyHierarchy(layout),
    [layout],
  );
  const branchIds = useMemo(
    () => expandableBranchIds(dependentsByAnchor),
    [dependentsByAnchor],
  );
  const allExpanded = Boolean(
    branchIds.length && branchIds.every((id) => expanded.has(id)),
  );
  const anyExpanded = branchIds.some((id) => expanded.has(id));

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
            Expand all
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

      <div className="dependency-tree-scroll">
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
            onToggle={toggle}
            onSelect={onSelect}
          />
        </ul>
      </div>
    </div>
  );
}
