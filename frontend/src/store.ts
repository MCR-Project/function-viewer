import { create } from "zustand";
import { analyze } from "./api";
import { findEntryPoint, reachableFrom, reachableUp } from "./flow";
import type { Graph } from "./types";

type FlowDirection = "down" | "up";
export type OrgMode = "flow" | "folder";

interface ViewerState {
  graph: Graph | null;
  activeIds: Set<string>;
  loading: boolean;
  error: string | null;
  /** Bumped whenever the canvas should focus a node. */
  focusRequest: { id: string; nonce: number } | null;
  /** Bumped to request a full re-layout (initial load and the Reorganize button only). */
  layoutEpoch: number;
  /** Which auto-arrange algorithm the next Reorganize (or initial load) should use. */
  layoutMode: OrgMode;
  /** When on, inactive ("led-to") functions are not rendered at all. */
  hideInactive: boolean;

  load: (path: string) => Promise<void>;
  toggle: (id: string) => void;
  enable: (id: string) => void;
  enableAll: () => void;
  disableAll: () => void;
  showFlow: (id: string, dir: FlowDirection) => void;
  focus: (id: string) => void;
  reorganize: (mode?: OrgMode) => void;
  toggleHideInactive: () => void;
}

export const useViewer = create<ViewerState>((set, get) => ({
  graph: null,
  activeIds: new Set(),
  loading: false,
  error: null,
  focusRequest: null,
  layoutEpoch: 0,
  layoutMode: "flow",
  hideInactive: false,

  load: async (path: string) => {
    set({ loading: true, error: null });
    try {
      const graph = await analyze(path);
      const entry = findEntryPoint(graph);
      set({
        graph,
        activeIds: entry ? new Set([entry]) : new Set(),
        loading: false,
        focusRequest: entry ? { id: entry, nonce: Date.now() } : null,
        layoutEpoch: get().layoutEpoch + 1,
        layoutMode: "flow",
      });
    } catch (err) {
      set({ loading: false, error: err instanceof Error ? err.message : String(err) });
    }
  },

  toggle: (id: string) => {
    const { graph, activeIds } = get();
    if (!graph) return;
    const next = new Set(activeIds);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    set({ activeIds: next });
  },

  enable: (id: string) => {
    const next = new Set(get().activeIds);
    next.add(id);
    set({ activeIds: next });
  },

  enableAll: () => {
    const { graph } = get();
    if (!graph) return;
    set({ activeIds: new Set(Object.keys(graph.functions)) });
  },

  disableAll: () => set({ activeIds: new Set() }),

  showFlow: (id: string, dir: FlowDirection) => {
    const { graph, activeIds } = get();
    if (!graph) return;
    const next = new Set(activeIds);
    const members = dir === "down" ? reachableFrom(graph, id) : reachableUp(graph, id);
    for (const member of members) next.add(member);
    set({ activeIds: next, focusRequest: { id, nonce: Date.now() } });
  },

  focus: (id: string) => set({ focusRequest: { id, nonce: Date.now() } }),

  reorganize: (mode?: OrgMode) =>
    set((s) => ({ layoutMode: mode ?? s.layoutMode, layoutEpoch: s.layoutEpoch + 1 })),

  toggleHideInactive: () => set((s) => ({ hideInactive: !s.hideInactive })),
}));

// Console access for debugging in dev.
if (import.meta.env.DEV) {
  (window as unknown as Record<string, unknown>).__viewer = useViewer;
}
