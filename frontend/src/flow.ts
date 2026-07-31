import type { Graph } from "./types";

/**
 * Visible = active functions, plus every direct callee of an active function
 * ("led to"), plus every direct caller of an active function, both rendered
 * as inactive ghost cards.
 */
export function computeVisible(graph: Graph, activeIds: ReadonlySet<string>): Set<string> {
  const visible = new Set<string>();
  for (const id of activeIds) {
    if (!graph.functions[id]) continue;
    visible.add(id);
    for (const callee of graph.functions[id].calls) visible.add(callee);
  }
  for (const [id, fn] of Object.entries(graph.functions)) {
    if (!visible.has(id) && fn.calls.some((callee) => activeIds.has(callee))) visible.add(id);
  }
  return visible;
}

/** Every function reachable from `start` through call edges, including `start` (cycle-safe DFS). */
export function reachableFrom(graph: Graph, start: string): Set<string> {
  const seen = new Set<string>();
  const stack = [start];
  while (stack.length > 0) {
    const id = stack.pop()!;
    if (seen.has(id) || !graph.functions[id]) continue;
    seen.add(id);
    for (const callee of graph.functions[id].calls) {
      if (!seen.has(callee)) stack.push(callee);
    }
  }
  return seen;
}

/** Every function that leads to `start` (its callers, transitively), including `start`. */
export function reachableUp(graph: Graph, start: string): Set<string> {
  const callers = new Map<string, string[]>();
  for (const [id, fn] of Object.entries(graph.functions)) {
    for (const callee of fn.calls) {
      const list = callers.get(callee);
      if (list) list.push(id);
      else callers.set(callee, [id]);
    }
  }
  const seen = new Set<string>();
  const stack = [start];
  while (stack.length > 0) {
    const id = stack.pop()!;
    if (seen.has(id) || !graph.functions[id]) continue;
    seen.add(id);
    for (const caller of callers.get(id) ?? []) {
      if (!seen.has(caller)) stack.push(caller);
    }
  }
  return seen;
}

/**
 * The function to auto-activate on load: prefer a top-level `main`,
 * shallowest path first, then alphabetical.
 */
export function findEntryPoint(graph: Graph): string | null {
  const mains = Object.values(graph.functions)
    .filter((fn) => fn.name === "main" && fn.className === null)
    .sort((a, b) => {
      const depth = a.file.split("/").length - b.file.split("/").length;
      return depth !== 0 ? depth : a.file.localeCompare(b.file);
    });
  return mains.length > 0 ? mains[0].id : null;
}
