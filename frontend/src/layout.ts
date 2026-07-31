import dagre from "dagre";
import type { Node } from "@xyflow/react";
import type { FunctionInfo, Graph } from "./types";

export interface Point {
  x: number;
  y: number;
}

/** Rough card height before the DOM has measured it (header + docstring + code lines). */
export function estimateNodeHeight(fn: FunctionInfo): number {
  return 58 + (fn.docstring ? 42 : 0) + fn.codeLines.length * 19 + 18;
}

/**
 * Real size of a node: React Flow's measurement when present, else the live
 * DOM element, else a conservative default. Rebuilt node objects can lose
 * `measured`, and laying out with the default causes overlapping cards.
 */
export function nodeSize(node: Node): { width: number; height: number } {
  if (node.measured?.width && node.measured?.height) {
    return { width: node.measured.width, height: node.measured.height };
  }
  const el = document.querySelector<HTMLElement>(`.react-flow__node[data-id="${CSS.escape(node.id)}"]`);
  if (el && el.offsetWidth > 0) {
    return { width: el.offsetWidth, height: el.offsetHeight };
  }
  return { width: 480, height: 300 };
}

/** Padding inside a folder frame around its grid of cards; reused by the frame overlay. */
export const FOLDER_PAD = 28;
/** Space reserved at the top of a folder frame for its file-name label; reused by the frame overlay. */
export const FOLDER_HEADER = 34;

const CELL_GAP = 40;
const FOLDER_GAP = 60;
const RANK_GAP = 160;

interface LocalEdge {
  source: string;
  target: string;
}

/**
 * Reorder each column to reduce edge crossings against its neighbor columns,
 * via the standard layered-graph barycenter heuristic: sweep forward (each
 * column reordered by the average position of its predecessors in the
 * column to its left) then backward (by successors to its right), a few
 * times. A node with no in-range neighbor keeps its existing spot instead of
 * being thrown to one end. Only `edges` local to this set of columns matter,
 * a wire's on-screen crossings are only meaningful between cards that are
 * actually near each other.
 */
function reduceCrossings(columns: string[][], edges: LocalEdge[]): string[][] {
  const cols = columns.map((c) => [...c]);
  const PASSES = 4;
  for (let pass = 0; pass < PASSES; pass++) {
    const forward = pass % 2 === 0;
    for (let i = forward ? 1 : cols.length - 2; forward ? i < cols.length : i >= 0; forward ? i++ : i--) {
      const neighbor = cols[forward ? i - 1 : i + 1];
      const neighborIndex = new Map(neighbor.map((id, idx) => [id, idx]));
      const currentIndex = new Map(cols[i].map((id, idx) => [id, idx]));
      const scored = cols[i].map((id) => {
        const neighborPositions = edges
          .filter((e) => (forward ? e.target === id : e.source === id))
          .map((e) => neighborIndex.get(forward ? e.source : e.target))
          .filter((idx): idx is number => idx !== undefined);
        const barycenter =
          neighborPositions.length > 0
            ? neighborPositions.reduce((a, b) => a + b, 0) / neighborPositions.length
            : currentIndex.get(id)!;
        return { id, barycenter };
      });
      scored.sort((a, b) => a.barycenter - b.barycenter || currentIndex.get(a.id)! - currentIndex.get(b.id)!);
      cols[i] = scored.map((s) => s.id);
    }
  }
  return cols;
}

/**
 * DFS over the call graph (source-order among a function's calls, restricted
 * to `present` ids), recording each function's first-discovered depth, its
 * global visitation order, and which edges are "back edges", pointing to a
 * node still on the current recursion stack, i.e. one of the caller's own
 * ancestors. Every root, a function nothing present calls, is walked
 * first, in stable file order; anything left over (e.g. a cycle with no
 * outside caller) is then walked too so every id gets a value.
 *
 * Depths are then relaxed so a caller always sits strictly left of its
 * callee: for every edge that ISN'T a back edge, the target's depth is
 * raised to at least source+1, repeatedly (pushing a node forward also
 * pushes whatever it itself leads to, cascading through the chain) until
 * nothing changes. Back edges are exempt, without that, a cycle would push
 * its members forward forever, chasing its own tail.
 */
function dfsDepths(present: ReadonlySet<string>, graph: Graph, order: Map<string, number>) {
  const depth = new Map<string, number>();
  const dfsIndex = new Map<string, number>();
  const hasIncoming = new Set<string>();
  for (const id of present) {
    for (const callee of graph.functions[id]?.calls ?? []) {
      if (callee !== id && present.has(callee)) hasIncoming.add(callee);
    }
  }

  let counter = 0;
  const onStack = new Set<string>();
  const backEdges = new Map<string, Set<string>>();
  const markBackEdge = (source: string, target: string) => {
    const targets = backEdges.get(source) ?? new Set<string>();
    targets.add(target);
    backEdges.set(source, targets);
  };
  const visit = (id: string, d: number) => {
    if (depth.has(id)) return;
    depth.set(id, d);
    dfsIndex.set(id, counter++);
    onStack.add(id);
    for (const callee of graph.functions[id]?.calls ?? []) {
      if (callee === id || !present.has(callee)) continue;
      if (onStack.has(callee)) {
        markBackEdge(id, callee); // callee is still an active ancestor: a cycle, not a forward call
        continue;
      }
      visit(callee, d + 1);
    }
    onStack.delete(id);
  };

  const byOrder = (a: string, b: string) => (order.get(a) ?? 0) - (order.get(b) ?? 0);
  for (const id of [...present].filter((id) => !hasIncoming.has(id)).sort(byOrder)) visit(id, 0);
  for (const id of [...present].sort(byOrder)) visit(id, 0); // leftover cycles, no outside caller

  let changed = true;
  for (let guard = 0; changed && guard <= present.size; guard++) {
    changed = false;
    for (const id of present) {
      for (const callee of graph.functions[id]?.calls ?? []) {
        if (callee === id || !present.has(callee) || backEdges.get(id)?.has(callee)) continue;
        const wanted = (depth.get(id) ?? 0) + 1;
        if ((depth.get(callee) ?? 0) < wanted) {
          depth.set(callee, wanted);
          changed = true;
        }
      }
    }
  }

  return { depth, dfsIndex };
}

/**
 * Assign each id a column (its DFS depth, compressed to remove gaps) and a
 * row within that column, ordered by DFS discovery order, so a caller's
 * callees default to the order they're actually called in source, and two
 * siblings never swap places. When `refine` is on, that order is then
 * adjusted by a barycenter sweep against `edges` to reduce crossings against
 * neighboring columns (used for the compact per-folder grid, where the
 * groups are small); flow mode leaves it off so a call's position always
 * matches where it's written, never reshuffled by something two columns
 * over. Cells are non-uniform: each column only as wide as its widest
 * member, each card only as tall as itself, so one oversized sibling never
 * forces empty space onto the rest of its column or the whole group.
 */
function layoutColumns(
  ids: string[],
  sizes: Map<string, { width: number; height: number }>,
  depth: Map<string, number>,
  dfsIndex: Map<string, number>,
  edges: LocalEdge[],
  colGap: number,
  origin: Point,
  refine: boolean,
): { local: Map<string, Point>; width: number; height: number } {
  const depths = [...new Set(ids.map((id) => depth.get(id) ?? 0))].sort((a, b) => a - b);
  const colIndex = new Map(depths.map((d, i) => [d, i]));
  const columns: string[][] = depths.map(() => []);
  for (const id of [...ids].sort((a, b) => (dfsIndex.get(a) ?? 0) - (dfsIndex.get(b) ?? 0))) {
    columns[colIndex.get(depth.get(id) ?? 0)!].push(id);
  }
  const orderedColumns = refine ? reduceCrossings(columns, edges) : columns;

  const local = new Map<string, Point>();
  let x = origin.x;
  let tallestColumn = 0;
  for (const col of orderedColumns) {
    const colWidth = Math.max(...col.map((id) => sizes.get(id)!.width));
    let y = origin.y;
    for (const id of col) {
      local.set(id, { x, y });
      y += sizes.get(id)!.height + CELL_GAP;
    }
    tallestColumn = Math.max(tallestColumn, y - CELL_GAP);
    x += colWidth + colGap;
  }
  return { local, width: x - colGap - origin.x, height: tallestColumn - origin.y };
}

/**
 * Left-to-right layered layout of every visible function (callers left of
 * callees): a function's column is its DFS depth, and its row is DFS
 * discovery order, so a caller's wires always stack top to bottom in the
 * exact order its calls are written in source, which both matches how
 * someone reading the code would expect the flow to look and keeps a
 * caller's own wires from crossing each other. Returns a position per node id.
 */
export function layoutGraph(nodes: Node[], graph: Graph): Map<string, Point> {
  const sizes = new Map(nodes.map((n) => [n.id, nodeSize(n)]));
  const present = new Set(nodes.map((n) => n.id));
  const order = new Map<string, number>();
  graph.files.forEach((file, fi) => file.functions.forEach((id, i) => order.set(id, fi * 100000 + i)));
  const { depth, dfsIndex } = dfsDepths(present, graph, order);

  const localEdges: LocalEdge[] = [];
  for (const edge of graph.edges) {
    if (edge.source !== edge.target && present.has(edge.source) && present.has(edge.target)) {
      localEdges.push({ source: edge.source, target: edge.target });
    }
  }

  return layoutColumns([...present], sizes, depth, dfsIndex, localEdges, RANK_GAP, { x: 0, y: 0 }, false).local;
}

/**
 * Group every function into a grid per file ("folder"): a function's column
 * is its DFS depth (compressed to remove gaps), and its row is chosen by a
 * barycenter sweep against edges within the same folder to minimize wires
 * crossing other cards. The folders themselves are then laid out with dagre
 * (callers left of callees, its own crossing minimization) so that most
 * wires flow left-to-right instead of doubling back behind a folder.
 * Returns a position per node id.
 */
export function layoutFolderGraph(nodes: Node[], graph: Graph): Map<string, Point> {
  const sizes = new Map(nodes.map((n) => [n.id, nodeSize(n)]));
  const present = new Set(nodes.map((n) => n.id));
  const order = new Map<string, number>();
  graph.files.forEach((file, fi) => file.functions.forEach((id, i) => order.set(id, fi * 100000 + i)));
  const { depth, dfsIndex } = dfsDepths(present, graph, order);

  const byFile = new Map<string, string[]>();
  for (const node of nodes) {
    const fn = graph.functions[node.id];
    if (!fn) continue;
    const list = byFile.get(fn.file);
    if (list) list.push(node.id);
    else byFile.set(fn.file, [node.id]);
  }

  interface Folder {
    file: string;
    width: number;
    height: number;
    local: Map<string, Point>;
  }
  const folders: Folder[] = [];
  for (const [file, ids] of [...byFile].sort(([a], [b]) => a.localeCompare(b))) {
    const idSet = new Set(ids);
    const localEdges: LocalEdge[] = ids.flatMap((id) =>
      (graph.functions[id]?.calls ?? [])
        .filter((callee) => callee !== id && idSet.has(callee))
        .map((callee) => ({ source: id, target: callee })),
    );

    const { local, width, height } = layoutColumns(
      ids,
      sizes,
      depth,
      dfsIndex,
      localEdges,
      CELL_GAP,
      { x: FOLDER_PAD, y: FOLDER_PAD + FOLDER_HEADER },
      true,
    );
    folders.push({ file, width: width + FOLDER_PAD * 2, height: height + FOLDER_PAD * 2 + FOLDER_HEADER, local });
  }

  // Fold every function-level call into a folder-level edge, then let dagre
  // rank the folders left-to-right: a folder that calls into another sits to
  // its left, so a wire leaving a function is never routed back behind it.
  const folderOf = new Map<string, string>();
  for (const folder of folders) for (const id of folder.local.keys()) folderOf.set(id, folder.file);

  const g = new dagre.graphlib.Graph();
  g.setGraph({
    rankdir: "LR",
    nodesep: FOLDER_GAP,
    ranksep: FOLDER_GAP * 2,
    marginx: 0,
    marginy: 0,
    // dagre's default cycle-breaking (plain DFS) ignores edge weight; only
    // "greedy" (weighted feedback-arc-set) actually favors keeping the
    // heavier direction of a folder-level cycle forward.
    acyclicer: "greedy",
  });
  g.setDefaultEdgeLabel(() => ({}));
  for (const folder of folders) g.setNode(folder.file, { width: folder.width, height: folder.height });

  // Weight each folder-pair direction by how many real calls support it, so
  // that when two folders call into each other (a cycle at folder scale),
  // dagre's cycle-breaking reverses the weaker direction and keeps the
  // majority of wires forward instead of an arbitrary pick.
  const folderEdgeWeights = new Map<string, Map<string, number>>();
  for (const folder of folders) {
    for (const id of folder.local.keys()) {
      for (const callee of graph.functions[id]?.calls ?? []) {
        const targetFile = folderOf.get(callee);
        if (!targetFile || targetFile === folder.file) continue;
        const targets = folderEdgeWeights.get(folder.file) ?? new Map<string, number>();
        targets.set(targetFile, (targets.get(targetFile) ?? 0) + 1);
        folderEdgeWeights.set(folder.file, targets);
      }
    }
  }
  for (const [source, targets] of folderEdgeWeights) {
    for (const [target, weight] of targets) {
      g.setEdge(source, target, { weight });
    }
  }

  dagre.layout(g);

  const positions = new Map<string, Point>();
  for (const folder of folders) {
    const placed = g.node(folder.file);
    const originX = placed.x - placed.width / 2;
    const originY = placed.y - placed.height / 2;
    for (const [id, local] of folder.local) {
      positions.set(id, { x: originX + local.x, y: originY + local.y });
    }
  }
  return positions;
}
