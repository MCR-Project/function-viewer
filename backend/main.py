"""FastAPI server for Function Viewer.

Dev: uvicorn backend.main:app --reload --port 8000  (frontend on Vite, port 5173)
Prod: builds of frontend/dist are served at / if present.
"""

from __future__ import annotations

import os
import string

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .analyzer import analyze_path

app = FastAPI(title="Function Viewer")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # local tool; a future Electron shell talks to localhost too
    allow_methods=["*"],
    allow_headers=["*"],
)


class AnalyzeRequest(BaseModel):
    path: str


@app.get("/api/browse")
def browse(path: str = "") -> dict:
    """List directories and .py files at a path. Empty path lists Windows drives (or / on POSIX)."""
    if not path:
        if os.name == "nt":
            drives = [f"{letter}:\\" for letter in string.ascii_uppercase if os.path.exists(f"{letter}:\\")]
            return {"path": "", "parent": None, "dirs": drives, "files": [], "home": os.path.expanduser("~")}
        path = "/"

    path = os.path.abspath(path)
    if not os.path.isdir(path):
        raise HTTPException(status_code=404, detail=f"Not a directory: {path}")

    dirs: list[str] = []
    files: list[str] = []
    try:
        for entry in sorted(os.scandir(path), key=lambda e: e.name.lower()):
            if entry.name.startswith(".") or entry.name == "__pycache__":
                continue
            try:
                if entry.is_dir():
                    dirs.append(entry.name)
                elif entry.name.endswith(".py"):
                    files.append(entry.name)
            except OSError:
                continue
    except PermissionError:
        raise HTTPException(status_code=403, detail=f"Permission denied: {path}")

    parent = os.path.dirname(path)
    if parent == path:  # drive root -> back to drive list
        parent = ""
    return {"path": path, "parent": parent, "dirs": dirs, "files": files, "home": os.path.expanduser("~")}


@app.post("/api/analyze")
def analyze(req: AnalyzeRequest) -> dict:
    target = os.path.abspath(os.path.expanduser(req.path))
    if not os.path.exists(target):
        raise HTTPException(status_code=404, detail=f"Path not found: {target}")
    if os.path.isfile(target) and not target.endswith(".py"):
        raise HTTPException(status_code=400, detail="Only .py files can be analyzed")
    try:
        return analyze_path(target)
    except Exception as exc:  # surface analysis failures to the UI
        raise HTTPException(status_code=500, detail=f"Analysis failed: {exc}")


_dist = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend", "dist")
if os.path.isdir(_dist):
    app.mount("/", StaticFiles(directory=_dist, html=True), name="frontend")
