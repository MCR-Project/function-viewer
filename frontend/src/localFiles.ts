import type { FileUpload } from "./api";

// Mirrors backend/analyzer.py's SKIP_DIRS so junk trees (venvs, caches) never get uploaded.
const SKIP_DIRS = new Set(["__pycache__", "node_modules", ".git", ".venv", "venv", ".tox", ".mypy_cache"]);

function relativePath(file: File): string {
  const raw = (file.webkitRelativePath || file.name).replace(/\\/g, "/");
  // webkitRelativePath includes the picked folder's own name as its first
  // segment (e.g. "backend/services/x.py"). Strip it so paths - and the
  // module names the backend derives from them - are relative to the
  // folder's contents, matching Python's own import root and how the old
  // server-side disk walk computed them.
  const slash = raw.indexOf("/");
  return slash === -1 ? raw : raw.slice(slash + 1);
}

function isSkipped(path: string): boolean {
  const parts = path.split("/");
  return parts.slice(0, -1).some((part) => SKIP_DIRS.has(part) || part.startsWith("."));
}

/** Reads every selected .py file's text, keyed by its path relative to the picked root. */
export async function collectPyFiles(fileList: FileList): Promise<{ root: string; files: FileUpload[] }> {
  // Snapshot everything from the FileList synchronously: callers reset the
  // input's value right after handing it off (so re-picking the same folder
  // still fires a change event), which clears this same live FileList once
  // we hit the first `await` below.
  const picked = Array.from(fileList).filter((f) => f.name.endsWith(".py") && !isSkipped(relativePath(f)));
  const first = picked[0];
  const root = first?.webkitRelativePath ? first.webkitRelativePath.split("/")[0] : (first?.name ?? "");

  const files = await Promise.all(
    picked.map(async (file) => ({ path: relativePath(file), content: await file.text() })),
  );
  files.sort((a, b) => a.path.localeCompare(b.path));

  return { root, files };
}
