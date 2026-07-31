import { useMemo, useState } from "react";
import { fileColor } from "../colors";
import { useViewer } from "../store";
import type { FileInfo } from "../types";

interface DirNode {
  name: string;
  path: string;
  dirs: Map<string, DirNode>;
  files: FileInfo[];
}

function buildTree(files: FileInfo[]): DirNode {
  const root: DirNode = { name: "", path: "", dirs: new Map(), files: [] };
  for (const file of files) {
    const parts = file.path.split("/");
    let node = root;
    let path = "";
    for (let i = 0; i < parts.length - 1; i++) {
      path = path ? `${path}/${parts[i]}` : parts[i];
      let child = node.dirs.get(parts[i]);
      if (!child) {
        child = { name: parts[i], path, dirs: new Map(), files: [] };
        node.dirs.set(parts[i], child);
      }
      node = child;
    }
    node.files.push(file);
  }
  return root;
}

const STEP = 18;
const BASE = 8;

/** VS-Code-like explorer: real nested folders, files inside them act as folders of functions. */
export function FileTree() {
  const graph = useViewer((s) => s.graph);
  const activeIds = useViewer((s) => s.activeIds);
  const toggle = useViewer((s) => s.toggle);
  const enable = useViewer((s) => s.enable);
  const focus = useViewer((s) => s.focus);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  const tree = useMemo(() => (graph ? buildTree(graph.files) : null), [graph]);

  if (!graph || !tree) return null;

  const flip = (path: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  };

  const renderFile = (file: FileInfo, depth: number) => {
    const open = !collapsed.has(file.path);
    const name = file.path.slice(file.path.lastIndexOf("/") + 1);
    return (
      <div key={file.path} className="tree-file">
        <div className="tree-row" style={{ paddingLeft: BASE + depth * STEP }} onClick={() => flip(file.path)}>
          <span className={`chevron ${open ? "open" : ""}`}>▶</span>
          <span className="file-dot" style={{ background: fileColor(file.path) }} />
          <span className="label">{name}</span>
          {file.error ? (
            <span className="tree-error" title={file.error}>
              parse error
            </span>
          ) : (
            <span className="count">{file.functions.length}</span>
          )}
        </div>
        {open &&
          file.functions.map((id) => {
            const fn = graph.functions[id];
            const isActive = activeIds.has(id);
            return (
              <div
                key={id}
                className="tree-row tree-fn"
                style={{ paddingLeft: BASE + (depth + 1) * STEP }}
                onClick={() => {
                  enable(id);
                  focus(id);
                }}
                title={`${fn.qualname}, click to enable & focus`}
              >
                <button
                  className={`status-dot ${isActive ? "on" : "off"}`}
                  title={isActive ? "Disable" : "Enable"}
                  onClick={(e) => {
                    e.stopPropagation();
                    toggle(id);
                  }}
                />
                <span className="label">{fn.qualname}</span>
              </div>
            );
          })}
      </div>
    );
  };

  const renderDir = (dir: DirNode, depth: number) => {
    const open = !collapsed.has(dir.path);
    const dirs = [...dir.dirs.values()].sort((a, b) => a.name.localeCompare(b.name));
    const files = [...dir.files].sort((a, b) => a.path.localeCompare(b.path));
    return (
      <div key={dir.path} className="tree-dir">
        <div className="tree-row" style={{ paddingLeft: BASE + depth * STEP }} onClick={() => flip(dir.path)}>
          <span className={`chevron ${open ? "open" : ""}`}>▶</span>
          <span className="folder-glyph">🗀</span>
          <span className="label">{dir.name}</span>
        </div>
        {open && (
          <>
            {dirs.map((child) => renderDir(child, depth + 1))}
            {files.map((file) => renderFile(file, depth + 1))}
          </>
        )}
      </div>
    );
  };

  const rootDirs = [...tree.dirs.values()].sort((a, b) => a.name.localeCompare(b.name));
  const rootFiles = [...tree.files].sort((a, b) => a.path.localeCompare(b.path));

  return (
    <div className="file-tree">
      <div className="sidebar-section-label" style={{ padding: "6px 8px 2px" }}>
        Explorer
      </div>
      {rootDirs.map((dir) => renderDir(dir, 0))}
      {rootFiles.map((file) => renderFile(file, 0))}
    </div>
  );
}
