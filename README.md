# Function Viewer

<div align="center">
  <img src="frontend/public/favicon.png" alt="Function Viewer logo" />
</div>

## Overview

Function Viewer visualizes how the functions of a Python project call each other, styled like an Unreal Engine Blueprint graph. Every function is a draggable card showing its signature, docstring, and full syntax highlighted source, connected by glowing wires that trace each real call in the code.

The goal is to make the flow of a codebase visible instead of jumping between files and grepping for callers. Core capabilities:

- Load any Python file or folder from disk and see every function and call resolved automatically.
- Toggle functions active or inactive to grow or shrink the graph around what matters right now.
- Follow a function's flow upward (its callers) or downward (what it calls) with one click.
- Auto arrange the graph two ways: by call order (flow mode) or grouped by file (folder mode, draggable as a group).
- Search and a VS Code style file explorer for quick navigation.

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

Open http://localhost:5173 and load a folder or file from the sidebar. Try `sample_project` for a quick demo.

## Codebase

```
backend/          FastAPI + Python ast, static analysis
frontend/src/      React 19 + React Flow + Zustand, the UI
sample_project/    demo fixture used for manual testing
```

### Backend

- `analyzer.py`: walks a file or folder, parses each `.py` file with `ast`, and for every top-level function and class method extracts its signature, docstring, source lines, and which loaded functions it calls (direct calls, `module.func()`, `from x import y`, `self.method()`, constructors). Only calls between functions that were actually loaded get resolved.
- `main.py`: the FastAPI app. `GET /api/browse` powers the server-side folder picker, `POST /api/analyze` runs the analyzer and returns the graph as JSON. Also serves the built frontend in production.

### Frontend

- `store.ts`: the single Zustand store, holds the loaded graph, which functions are active, the layout mode, and every UI action.
- `flow.ts`: pure functions for visibility rules (active functions plus their direct neighbors shown as ghosts), reachability for the flow buttons, and picking `main` as the entry point on load.
- `layout.ts`: the two auto arrange algorithms, both built on a shared depth first search model. Flow mode lays out every function left to right by call depth, in call order. Folder mode groups functions by file first, then ranks the file groups left to right with dagre.
- `components/GraphCanvas.tsx`: the React Flow canvas, incremental card placement, both layout modes, folder drag to move a group, wire highlighting, and the topbar controls.
- `components/FunctionNode.tsx`: the blueprint style function card, with syntax highlighting and per line call handles.
- `components/FolderFrame.tsx`: the folder style group frame used in folder mode.
- `components/Sidebar.tsx`, `FileTree.tsx`, `SearchBar.tsx`, `PathBrowser.tsx`: import controls, a nested directory tree, search, and the server-side folder browser.
- `api.ts`, `types.ts`, `colors.ts`: fetch wrapper, wire format types, and per file color hashing.

The frontend only talks to the backend over HTTP, with no browser-only file APIs, so it can later be wrapped in an Electron or Tauri shell without changes.

## CI/CD

Every PR into `main` runs two required checks (`.github/workflows/pr-checks.yml`): a frontend build and a backend build-and-run (health check against `/api/health`).

Deployment is a single Docker image (`Dockerfile`) that builds the frontend and bundles it with the backend, which serves it at `/`. `render.yaml` describes the Render web service; once connected to this repo, Render rebuilds and redeploys automatically on every push to `main`.
