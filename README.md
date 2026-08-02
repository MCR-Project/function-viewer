# Function Viewer

<div align="center">
  <img src="frontend/public/favicon.png" alt="Function Viewer logo" />
</div>

## Overview

Function Viewer visualizes how the functions of a codebase call each other, styled like an Unreal Engine Blueprint graph. Every function is a draggable card showing its signature, docstring, and full syntax highlighted source, connected by glowing wires that trace each real call in the code.

The goal is to make the flow of a codebase visible instead of jumping between files and grepping for callers. Core capabilities:

- Load any file or folder from disk and see every function and call resolved automatically, in any supported language, no per-language toggle needed.
- Toggle functions active or inactive to grow or shrink the graph around what matters right now.
- Follow a function's flow upward (its callers) or downward (what it calls) with one click.
- Auto arrange the graph two ways: by call order (flow mode) or grouped by file (folder mode, draggable as a group).
- Search and a VS Code style file explorer for quick navigation, with a small language icon next to each file.

### Supported languages

| Language | Extensions |
| --- | --- |
| Python | `.py` |
| Rust | `.rs` |

More languages can be added without touching the frontend - see [Backend](#backend) below.

## Quick start

Backend (Python 3.10+):

```sh
pip install -r backend/requirements.txt
python -m uvicorn backend.main:app --port 8000
```

Frontend:

```sh
cd frontend
npm install
npm run dev
```

Open http://localhost:5173 and load a folder or file from the sidebar. Try `sample_project` (Python) or `sample_rust_project` (Rust) for a quick demo.

## Codebase

```
backend/            FastAPI + a per-language plugin architecture, static analysis
frontend/src/        React 19 + React Flow + Zustand, the UI
sample_project/      Python demo fixture used for manual testing
sample_rust_project/ Rust demo fixture used for manual testing
```

### Backend

- `analyzer.py`: the dispatcher. Groups the files it's given by extension, hands each slice to its language plugin, and merges the results into one graph. Function and file ids are namespaced by path, so merging across languages can't collide.
- `languages/`: one module per language, each implementing the same `LanguagePlugin` contract (`languages/base.py`) - parse every file, extract each function's signature/docstring/source lines, and resolve which loaded functions it calls (direct calls, `module.func()`, `self.method()`, constructors/associated functions). Only calls between functions that were actually loaded get resolved. Adding a language means adding one new module here and registering it in `languages/__init__.py`; nothing else changes.
  - `python.py`: parses with the stdlib `ast`.
  - `rust.py`: parses with `tree-sitter` + `tree-sitter-rust` (functions, `impl` methods, `use`-based and bare-path module resolution). Calls made inside a macro invocation (`println!`, `format!`, ...) aren't visible to it - macro arguments are an opaque token stream, not parsed expressions.
- `main.py`: the FastAPI app. `GET /api/browse` powers the server-side folder picker, `POST /api/analyze` runs the analyzer and returns the graph as JSON. Also serves the built frontend in production.

### Frontend

- `store.ts`: the single Zustand store, holds the loaded graph, which functions are active, the layout mode, and every UI action.
- `flow.ts`: pure functions for visibility rules (active functions plus their direct neighbors shown as ghosts), reachability for the flow buttons, and picking `main` as the entry point on load.
- `layout.ts`: the two auto arrange algorithms, both built on a shared depth first search model. Flow mode lays out every function left to right by call depth, in call order. Folder mode groups functions by file first, then ranks the file groups left to right with dagre.
- `components/GraphCanvas.tsx`: the React Flow canvas, incremental card placement, both layout modes, folder drag to move a group, wire highlighting, and the topbar controls.
- `components/FunctionNode.tsx`: the blueprint style function card, with syntax highlighting and per line call handles.
- `components/FolderFrame.tsx`: the folder style group frame used in folder mode.
- `components/Sidebar.tsx`, `FileTree.tsx`, `SearchBar.tsx`, `PathBrowser.tsx`: import controls, a nested directory tree, search, and the server-side folder browser.
- `components/LanguageIcon.tsx`: the small per-file language badge shown in the Explorer.
- `languages.ts`: the frontend's mirror of the backend's language registry - id, label, extensions, icon color per language. Adding a language to the backend also means adding one entry here.
- `api.ts`, `types.ts`, `colors.ts`: fetch wrapper, wire format types, and per file color hashing.

The frontend only talks to the backend over HTTP, with no browser-only file APIs, so it can later be wrapped in an Electron or Tauri shell without changes.

## CI/CD

Every PR into `main` runs two required checks (`.github/workflows/pr-checks.yml`): a frontend build and a backend build-and-run (health check against `/api/health`).

On every push to `main`, two deployments happen automatically:

- **Backend**: a single Docker image (`Dockerfile`) that builds the frontend and bundles it with the backend, which serves it at `/`. `render.yaml` describes the Render web service; once connected to this repo, Render rebuilds and redeploys on every push. Live at https://function-viewer.onrender.com.
- **Frontend**: `.github/workflows/deploy-pages.yml` builds `frontend/` with `VITE_API_URL` pointed at the Render backend above and publishes it to GitHub Pages. Live at https://mcr-project.github.io/function-viewer/.

The two are independent: the Render deploy is self-contained (backend serving its own built frontend), while the Pages site is a separate static build of the same frontend wired to the same Render API.
