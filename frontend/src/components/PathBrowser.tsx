import { useCallback, useEffect, useState } from "react";
import { browse } from "../api";
import type { BrowseResult } from "../types";

interface Props {
  initialPath: string;
  onSelect: (path: string) => void;
  onClose: () => void;
}

/** Server-side directory picker: navigate drives/folders, pick a folder or a .py file. */
export function PathBrowser({ initialPath, onSelect, onClose }: Props) {
  const [result, setResult] = useState<BrowseResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const navigate = useCallback(async (path: string) => {
    setLoading(true);
    setError(null);
    try {
      setResult(await browse(path));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    navigate(initialPath);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const join = (dir: string) => {
    if (!result?.path) return dir; // drive roots come back absolute
    const sep = result.path.includes("\\") ? "\\" : "/";
    return result.path.endsWith(sep) ? result.path + dir : result.path + sep + dir;
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <button className="icon-btn" title="Up one level" onClick={() => result?.parent !== null && navigate(result?.parent ?? "")} disabled={result?.parent === null}>
            ↑
          </button>
          <button className="icon-btn" title="Home" onClick={() => result && navigate(result.home)}>
            ⌂
          </button>
          <div className="modal-path">{result?.path || "Drives"}</div>
        </div>
        <div className="modal-list">
          {loading && (
            <div className="modal-entry">
              <span className="spinner" /> loading…
            </div>
          )}
          {error && <div className="error-box">{error}</div>}
          {!loading &&
            result?.dirs.map((dir) => (
              <div key={dir} className="modal-entry" onDoubleClick={() => navigate(join(dir))} onClick={() => navigate(join(dir))}>
                <span className="glyph">🗀</span> {dir}
              </div>
            ))}
          {!loading &&
            result?.files.map((file) => (
              <div key={file} className="modal-entry" onClick={() => onSelect(join(file))}>
                <span className="glyph">🐍</span> {file}
              </div>
            ))}
          {!loading && result && result.dirs.length === 0 && result.files.length === 0 && (
            <div className="modal-entry">(empty)</div>
          )}
        </div>
        <div className="modal-footer">
          <button className="btn" onClick={onClose}>
            Cancel
          </button>
          <button className="btn btn-primary" disabled={!result?.path} onClick={() => result?.path && onSelect(result.path)}>
            Select this folder
          </button>
        </div>
      </div>
    </div>
  );
}
