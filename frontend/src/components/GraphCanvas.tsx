import { useEffect, useMemo, useRef, useState } from "react";
import {
  Background,
  BackgroundVariant,
  MiniMap,
  ReactFlow,
  useReactFlow,
  useUpdateNodeInternals,
  type Edge,
  type Node,
  type NodeChange,
  type OnNodeDrag,
  applyNodeChanges,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { fileColor } from "../colors";
import { computeVisible } from "../flow";
import {
  estimateNodeHeight,
  FOLDER_HEADER,
  FOLDER_PAD,
  layoutFolderGraph,
  layoutGraph,
  nodeSize,
  type Point,
} from "../layout";
import { useViewer, type OrgMode } from "../store";
import { FolderFrame, type FolderFrameType } from "./FolderFrame";
import { FunctionNode, type FunctionNodeType } from "./FunctionNode";

const nodeTypes = { function: FunctionNode, folder: FolderFrame };

function visKey(visible: Set<string>): string {
  return [...visible].sort().join("|");
}

export function GraphCanvas() {
  const graph = useViewer((s) => s.graph);
  const activeIds = useViewer((s) => s.activeIds);
  const focusRequest = useViewer((s) => s.focusRequest);
  const layoutEpoch = useViewer((s) => s.layoutEpoch);
  const layoutMode = useViewer((s) => s.layoutMode);

  const [nodes, setNodes] = useState<Node[]>([]);
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const { fitView, setCenter, getNode, screenToFlowPosition } = useReactFlow();
  const updateNodeInternals = useUpdateNodeInternals();
  const wrapRef = useRef<HTMLDivElement | null>(null);

  const hideInactive = useViewer((s) => s.hideInactive);

  const visible = useMemo(() => {
    if (!graph) return new Set<string>();
    if (hideInactive) return new Set([...activeIds].filter((id) => graph.functions[id]));
    return computeVisible(graph, activeIds);
  }, [graph, activeIds, hideInactive]);
  const needsLayout = useRef(false);
  const pendingLayoutMode = useRef<OrgMode>("flow");
  const lastVisKey = useRef("");
  const lastRoot = useRef("");
  const lastEpoch = useRef(0);

  // Rebuild the node list whenever the visible set changes.
  useEffect(() => {
    if (!graph) {
      setNodes([]);
      lastVisKey.current = "";
      return;
    }
    const key = visKey(visible);
    const newRoot = graph.root !== lastRoot.current;
    const epochChanged = layoutEpoch !== lastEpoch.current;
    if (key === lastVisKey.current && !newRoot && !epochChanged) return;
    lastVisKey.current = key;
    lastRoot.current = graph.root;
    lastEpoch.current = layoutEpoch;

    // A fresh full layout only happens on load and on the Reorganize button.
    // Everything else keeps every survivor in place and tucks each new card in
    // next to the active caller that leads to it. A fresh load always uses the
    // flow layout; an explicit Reorganize honors whichever mode was chosen.
    const fullLayout = newRoot || epochChanged;
    needsLayout.current = fullLayout;
    if (fullLayout) pendingLayoutMode.current = newRoot ? "flow" : layoutMode;

    setNodes((prev) => {
      if (fullLayout) {
        const prevById = new Map(newRoot ? [] : prev.map((n) => [n.id, n]));
        return [...visible]
          .filter((id) => graph.functions[id])
          .map((id) => {
            const existing = prevById.get(id);
            return {
              id,
              type: "function",
              position: existing?.position ?? { x: 0, y: 0 },
              data: { fn: graph.functions[id] },
              // keep the measured size so dagre lays out with real card dimensions
              measured: existing?.measured,
              // hide fresh nodes until dagre has placed them
              style: existing ? existing.style : { opacity: 0 },
            } satisfies FunctionNodeType as Node;
          });
      }

      const result = prev.filter((n) => visible.has(n.id));
      const placed = new Map(result.map((n) => [n.id, n]));
      const sizes = new Map(result.map((n) => [n.id, nodeSize(n)]));
      let pending = [...visible].filter((id) => !placed.has(id) && graph.functions[id]);

      const GAP = 40;
      // Slide a candidate spot downward until it no longer intersects any card.
      const findFreeY = (x: number, y: number, w: number, h: number): number => {
        for (let guard = 0; guard < 60; guard++) {
          let bumped = false;
          for (const [pid, pnode] of placed) {
            const s = sizes.get(pid)!;
            const px = pnode.position.x;
            const py = pnode.position.y;
            if (x < px + s.width + GAP && px < x + w + GAP && y < py + s.height + GAP && py < y + h + GAP) {
              y = py + s.height + GAP;
              bumped = true;
            }
          }
          if (!bumped) break;
        }
        return y;
      };

      const place = (id: string, at: Point) => {
        const size = { width: 480, height: estimateNodeHeight(graph.functions[id]) };
        const node = {
          id,
          type: "function",
          position: { x: at.x, y: findFreeY(at.x, at.y, size.width, size.height) },
          data: { fn: graph.functions[id] },
          style: { opacity: 1 },
        } satisfies FunctionNodeType as Node;
        placed.set(id, node);
        sizes.set(id, size);
        result.push(node);
      };

      while (pending.length > 0) {
        const later: string[] = [];
        for (const id of pending) {
          // Pop in right next to the active caller leading here…
          const wire = graph.edges.find(
            (e) => e.target === id && e.source !== id && activeIds.has(e.source) && placed.has(e.source),
          );
          if (wire) {
            const anchor = placed.get(wire.source)!;
            const x = anchor.position.x + sizes.get(wire.source)!.width + 160;
            place(id, { x, y: anchor.position.y });
            continue;
          }
          // …or, for upward flows, left of the placed function this one calls.
          const revWire = graph.edges.find((e) => e.source === id && e.target !== id && placed.has(e.target));
          if (revWire) {
            const anchor = placed.get(revWire.target)!;
            place(id, { x: anchor.position.x - 480 - 160, y: anchor.position.y });
            continue;
          }
          later.push(id);
        }
        if (later.length === pending.length && later.length > 0) {
          // No active caller on the canvas leads here: this is the function the
          // user just enabled. Drop it at the viewport center; its callees then
          // anchor to it on the next pass.
          const id = later.shift()!;
          const rect = wrapRef.current?.getBoundingClientRect();
          const center = rect
            ? screenToFlowPosition({ x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 })
            : { x: 0, y: 0 };
          place(id, { x: center.x - 240, y: center.y - estimateNodeHeight(graph.functions[id]) / 2 });
        }
        pending = later;
      }
      return result;
    });

    // ResizeObserver measurements only arrive on rendered frames; force a
    // synchronous DOM measure so layout also works in hidden/throttled tabs.
    const ids = [...visible];
    const timer = setTimeout(() => updateNodeInternals(ids), 30);
    return () => clearTimeout(timer);
  }, [graph, visible, activeIds, layoutEpoch, layoutMode, screenToFlowPosition, updateNodeInternals]);

  // Ready once every one of OUR nodes has a real measured size. Deliberately
  // not React Flow's own useNodesInitialized(): that hook waits on handle
  // measurements for every rendered node, and the decorative folder-frame
  // nodes below have no handles, which would leave it stuck false forever.
  const ownNodesMeasured =
    nodes.length > 0 && nodes.every((n) => (n.measured?.width ?? 0) > 0 && (n.measured?.height ?? 0) > 0);

  // Once every node has measured, lay them out (dagre or folder grid).
  useEffect(() => {
    if (!needsLayout.current || !ownNodesMeasured || !graph || nodes.length === 0) return;
    needsLayout.current = false;
    const positions =
      pendingLayoutMode.current === "folder" ? layoutFolderGraph(nodes, graph) : layoutGraph(nodes, graph);
    setNodes((prev) =>
      prev.map((n) => ({
        ...n,
        position: positions.get(n.id) ?? n.position,
        style: { opacity: 1 },
      })),
    );
    // rAF stalls in hidden tabs; setTimeout always runs, and animations are skipped there.
    const duration = document.hidden ? 0 : 400;
    setTimeout(() => fitView({ padding: 0.15, duration, maxZoom: 0.9 }), 0);
  }, [ownNodesMeasured, nodes, graph, fitView]);

  // Focus requests from the sidebar / show-flow.
  useEffect(() => {
    if (!focusRequest) return;
    const timer = setTimeout(() => {
      const node = getNode(focusRequest.id);
      if (node) {
        const { width: w, height: h } = nodeSize(node);
        setCenter(node.position.x + w / 2, node.position.y + h / 2, {
          zoom: 0.8,
          duration: document.hidden ? 0 : 500,
        });
      }
    }, 120); // let layout settle first
    return () => clearTimeout(timer);
  }, [focusRequest, getNode, setCenter]);

  // A card is "engaged" while hovered or selected; its wires (in both
  // directions) then get the same highlight as directly hovering a wire.
  const selectedIds = useMemo(() => new Set(nodes.filter((n) => n.selected).map((n) => n.id)), [nodes]);
  const engagedIds = useMemo(
    () => (hoveredId ? new Set(selectedIds).add(hoveredId) : selectedIds),
    [hoveredId, selectedIds],
  );

  // Edges: one wire per calling line, when at least one endpoint is active,
  // either active caller -> (ghost) callee, or ghost caller -> active callee.
  const edges = useMemo<Edge[]>(() => {
    if (!graph) return [];
    const result: Edge[] = [];
    for (const edge of graph.edges) {
      if (!activeIds.has(edge.source) && !activeIds.has(edge.target)) continue;
      if (!visible.has(edge.source) || !visible.has(edge.target)) continue;
      if (edge.source === edge.target) continue; // recursion: line is tinted instead
      const color = fileColor(graph.functions[edge.source].file);
      const engaged = engagedIds.has(edge.source) || engagedIds.has(edge.target);
      result.push({
        id: `${edge.source}@${edge.line}->${edge.target}`,
        source: edge.source,
        target: edge.target,
        sourceHandle: `L${edge.line}`,
        targetHandle: "in",
        type: "default",
        className: engaged ? "edge-active" : undefined,
        style: { stroke: color, color },
      });
    }
    return result;
  }, [graph, activeIds, visible, engagedIds]);

  const onNodesChange = (changes: NodeChange[]) => setNodes((prev) => applyNodeChanges(changes, prev));

  // Dragging a folder (by its tab) drags every function inside it. The folder
  // node itself isn't stored in `nodes` state, it's derived each render from
  // the real cards' positions, so instead of moving the folder, we relay the
  // drag delta onto its member cards; the folder frame then follows them.
  const folderDrag = useRef<{ file: string; last: Point } | null>(null);
  const onNodeDragStart: OnNodeDrag = (_event, node) => {
    if (node.type !== "folder") return;
    folderDrag.current = { file: (node.data as { file: string }).file, last: { ...node.position } };
  };
  const onNodeDrag: OnNodeDrag = (_event, node) => {
    const drag = folderDrag.current;
    if (node.type !== "folder" || !drag || (node.data as { file: string }).file !== drag.file) return;
    const dx = node.position.x - drag.last.x;
    const dy = node.position.y - drag.last.y;
    drag.last = { ...node.position };
    if (dx === 0 && dy === 0) return;
    setNodes((prev) =>
      prev.map((n) =>
        graph?.functions[n.id]?.file === drag.file
          ? { ...n, position: { x: n.position.x + dx, y: n.position.y + dy } }
          : n,
      ),
    );
  };
  const onNodeDragStop: OnNodeDrag = () => {
    folderDrag.current = null;
  };

  const reorganize = useViewer((s) => s.reorganize);
  const toggleHideInactive = useViewer((s) => s.toggleHideInactive);

  // In folder mode, draw a labeled frame around each file's cards, derived
  // from their live positions so it stays correct after drags or incremental
  // additions. Skipped while nodes are still fading in from a pending layout.
  const isSettled = nodes.length > 0 && nodes.every((n) => n.style?.opacity !== 0);
  const folderFrames = useMemo<Node[]>(() => {
    if (!graph || layoutMode !== "folder" || !isSettled) return [];
    const groups = new Map<string, Node[]>();
    for (const n of nodes) {
      const fn = graph.functions[n.id];
      if (!fn) continue;
      const list = groups.get(fn.file);
      if (list) list.push(n);
      else groups.set(fn.file, [n]);
    }
    const frames: Node[] = [];
    for (const [file, members] of groups) {
      let minX = Infinity;
      let minY = Infinity;
      let maxX = -Infinity;
      let maxY = -Infinity;
      for (const member of members) {
        const size = nodeSize(member);
        minX = Math.min(minX, member.position.x);
        minY = Math.min(minY, member.position.y);
        maxX = Math.max(maxX, member.position.x + size.width);
        maxY = Math.max(maxY, member.position.y + size.height);
      }
      frames.push({
        id: `folder:${file}`,
        type: "folder",
        position: { x: minX - FOLDER_PAD, y: minY - FOLDER_PAD - FOLDER_HEADER },
        data: { file, width: maxX - minX + FOLDER_PAD * 2, height: maxY - minY + FOLDER_PAD * 2 + FOLDER_HEADER },
        // Draggable only via its tab (see .folder-tab pointer-events in
        // index.css), grabbing the name drags every function inside along
        // with it, via the onNodeDrag relay below.
        draggable: true,
        dragHandle: ".folder-tab",
        selectable: false,
        connectable: false,
        zIndex: -1,
        // React Flow inline-hides a node's wrapper (visibility: hidden) until it
        // considers the node "measured", which, for a handle-less decorative
        // node like this one, never happens. We already know its exact size and
        // position ourselves, so force it visible via this class (see index.css).
        className: "folder-node-wrapper",
        style: { pointerEvents: "none" },
      } satisfies FolderFrameType as Node);
    }
    return frames;
  }, [graph, layoutMode, isSettled, nodes]);

  const renderNodes = useMemo(() => [...folderFrames, ...nodes], [folderFrames, nodes]);

  return (
    <div className="canvas-wrap" ref={wrapRef}>
      <div className="canvas-topbar">
        <span className="topbar-root">{graph ? graph.root : ""}</span>
        <div className="topbar-actions">
          <button
            className={`btn ${hideInactive ? "toggled" : ""}`}
            disabled={!graph}
            aria-pressed={hideInactive}
            title="Hide inactive (led-to) cards entirely; layout and reorganize then only consider active functions"
            onClick={toggleHideInactive}
          >
            ◍ Hide inactive
          </button>
          <div className="reorg-group">
            <button
              className="btn"
              disabled={!graph || visible.size === 0}
              title={`Auto-arrange all visible cards (${layoutMode === "folder" ? "folder-based" : "flow-based"})`}
              onClick={() => reorganize()}
            >
              ⌗ Reorganize
            </button>
            <div className={`mode-slider ${layoutMode}`}>
              <div className="mode-slider-thumb" />
              <button
                className={layoutMode === "flow" ? "active" : ""}
                disabled={!graph || visible.size === 0}
                title="Flow-based: callers left of callees"
                onClick={() => reorganize("flow")}
              >
                flow
              </button>
              <button
                className={layoutMode === "folder" ? "active" : ""}
                disabled={!graph || visible.size === 0}
                title="Folder-based: grouped by file"
                onClick={() => reorganize("folder")}
              >
                files
              </button>
            </div>
          </div>
          <button
            className="btn"
            disabled={!graph || visible.size === 0}
            title="Fit every visible card in view"
            onClick={() => fitView({ padding: 0.15, duration: document.hidden ? 0 : 400, maxZoom: 0.9 })}
          >
            ⛶ Fit view
          </button>
        </div>
      </div>
      {graph && visible.size === 0 && (
        <div className="empty-state">
          <div className="glyph">◇</div>
          <div className="title">No active functions</div>
          <div className="sub">
            No `main` function was found, so nothing is enabled yet. Use the search or the file explorer on the left to
            activate a function.
          </div>
        </div>
      )}
      {!graph && (
        <div className="empty-state">
          <div className="glyph">⬡</div>
          <div className="title">Function Viewer</div>
          <div className="sub">Load a Python or Rust file or folder from the sidebar to draw its call graph.</div>
        </div>
      )}
      <ReactFlow
        nodes={renderNodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodesChange={onNodesChange}
        onNodeMouseEnter={(_, node) => setHoveredId(node.id)}
        onNodeMouseLeave={() => setHoveredId(null)}
        onNodeDragStart={onNodeDragStart}
        onNodeDrag={onNodeDrag}
        onNodeDragStop={onNodeDragStop}
        minZoom={0.08}
        maxZoom={1.75}
        proOptions={{ hideAttribution: true }}
        nodesConnectable={false}
        deleteKeyCode={null}
      >
        <Background variant={BackgroundVariant.Dots} gap={26} size={1.6} color="#1b2634" />
        <MiniMap
          pannable
          zoomable
          nodeColor={(n) =>
            n.type === "folder"
              ? "transparent"
              : fileColor((n as FunctionNodeType).data.fn.file)
          }
          maskColor="rgba(6, 10, 15, 0.75)"
          bgColor="#0d1219"
        />
      </ReactFlow>
    </div>
  );
}
