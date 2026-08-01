import type { BrowseResult, Graph } from "./types";

// Configurable so a packaged (Electron/Tauri) build, or a static frontend
// pointed at a separately hosted backend, can point elsewhere. Falls back to
// localhost in dev (frontend and backend run on different ports) and to a
// same-origin relative path in production (backend serves the built
// frontend itself, e.g. the Render deploy).
const API_BASE: string =
  import.meta.env.VITE_API_URL ?? (import.meta.env.DEV ? "http://127.0.0.1:8000" : "");

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, init);
  } catch {
    throw new Error("Cannot reach the backend. Is it running on " + API_BASE + "?");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* non-JSON error body */
    }
    throw new Error(detail);
  }
  return res.json();
}

export function browse(path: string): Promise<BrowseResult> {
  return request<BrowseResult>(`/api/browse?path=${encodeURIComponent(path)}`);
}

export function analyze(path: string): Promise<Graph> {
  return request<Graph>("/api/analyze", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
}
