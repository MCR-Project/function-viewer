import { useState } from "react";
import { useViewer } from "../store";
import { FileTree } from "./FileTree";
import { PathBrowser } from "./PathBrowser";
import { SearchBar } from "./SearchBar";

export function Sidebar() {
  const graph = useViewer((s) => s.graph);
  const loading = useViewer((s) => s.loading);
  const error = useViewer((s) => s.error);
  const load = useViewer((s) => s.load);
  const enableAll = useViewer((s) => s.enableAll);
  const disableAll = useViewer((s) => s.disableAll);

  const [path, setPath] = useState("");
  const [browsing, setBrowsing] = useState(false);

  const submit = () => {
    if (path.trim()) load(path.trim());
  };

  return (
    <div className="sidebar">
      <div className="sidebar-brand">
        <div className="brand-marks">
          <a className="brand-github" href="https://github.com/MCR-Project/function-viewer" target="_blank" rel="noopener noreferrer" title="GitHub repository">
            <svg viewBox="0 0 16 16" width="32" height="32" fill="currentColor" aria-hidden="true">
              <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8z" />
            </svg>
          </a>
        </div>
        <h1>
          Function<span>Viewer</span>
        </h1>
      </div>

      <div className="sidebar-section">
        <div className="sidebar-section-label">Import</div>
        <div className="path-row">
          <input
            className="input"
            placeholder="C:\path\to\project"
            value={path}
            onChange={(e) => setPath(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submit()}
          />
          <button className="btn" onClick={() => setBrowsing(true)}>
            Browse…
          </button>
        </div>
        <div className="btn-row">
          <button className="btn btn-primary" onClick={submit} disabled={loading || !path.trim()}>
            {loading ? (
              <>
                <span className="spinner" />
                Analyzing…
              </>
            ) : (
              "Load"
            )}
          </button>
        </div>
        {error && <div className="error-box">{error}</div>}
        {!graph && !error && <div className="hint">Pick a Python file or a folder, every function inside is analyzed and its calls to other loaded functions become wires.</div>}
      </div>

      <SearchBar />

      {graph && (
        <div className="sidebar-section">
          <div className="sidebar-section-label">Global</div>
          <div className="btn-row" style={{ marginTop: 0 }}>
            <button className="btn" onClick={enableAll}>
              Enable all
            </button>
            <button className="btn btn-danger" onClick={disableAll}>
              Disable all
            </button>
          </div>
        </div>
      )}

      <FileTree />

      {browsing && (
        <PathBrowser
          initialPath={path.trim()}
          onClose={() => setBrowsing(false)}
          onSelect={(selected) => {
            setPath(selected);
            setBrowsing(false);
            load(selected);
          }}
        />
      )}
    </div>
  );
}
