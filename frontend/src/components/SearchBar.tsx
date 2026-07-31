import { useMemo, useState } from "react";
import { useViewer } from "../store";
import type { FunctionInfo } from "../types";

/** Search over loaded functions; a result can be enabled+focused or flow-expanded. */
export function SearchBar() {
  const graph = useViewer((s) => s.graph);
  const enable = useViewer((s) => s.enable);
  const focus = useViewer((s) => s.focus);
  const showFlow = useViewer((s) => s.showFlow);
  const [query, setQuery] = useState("");

  const results = useMemo<FunctionInfo[]>(() => {
    if (!graph || query.trim().length === 0) return [];
    const q = query.trim().toLowerCase();
    return Object.values(graph.functions)
      .map((fn) => {
        const name = fn.qualname.toLowerCase();
        const file = fn.file.toLowerCase();
        let score = -1;
        if (name.startsWith(q)) score = 0;
        else if (name.includes(q)) score = 1;
        else if (`${file}::${name}`.includes(q)) score = 2;
        return { fn, score };
      })
      .filter((r) => r.score >= 0)
      .sort((a, b) => a.score - b.score || a.fn.qualname.localeCompare(b.fn.qualname))
      .slice(0, 30)
      .map((r) => r.fn);
  }, [graph, query]);

  const pick = (fn: FunctionInfo) => {
    enable(fn.id);
    focus(fn.id);
  };

  return (
    <div className="sidebar-section">
      <div className="sidebar-section-label">Search</div>
      <input
        className="input"
        style={{ width: "100%" }}
        placeholder="Find a function…"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        disabled={!graph}
      />
      {results.length > 0 && (
        <div className="search-results">
          {results.map((fn) => (
            <div key={fn.id} className="search-result" onClick={() => pick(fn)} title={`${fn.file}, click to enable & focus`}>
              <span className="fn-name">{fn.qualname}</span>
              <span className="fn-file">{fn.file}</span>
              <button
                className="icon-btn"
                title="Show everything that leads to this function"
                onClick={(e) => {
                  e.stopPropagation();
                  showFlow(fn.id, "up");
                }}
              >
                ▲
              </button>
              <button
                className="icon-btn"
                title="Show everything this function leads to"
                onClick={(e) => {
                  e.stopPropagation();
                  showFlow(fn.id, "down");
                }}
              >
                ▼
              </button>
            </div>
          ))}
        </div>
      )}
      {graph && query.trim() && results.length === 0 && <div className="hint">No function matches “{query.trim()}”.</div>}
    </div>
  );
}
