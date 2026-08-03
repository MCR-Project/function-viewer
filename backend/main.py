"""FastAPI server for Function Viewer.

Dev: uvicorn backend.main:app --reload --port 8000  (frontend on Vite, port 5173)
Prod: builds of frontend/dist are served at / if present.
"""

from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .analyzer import analyze_sources

app = FastAPI(title="Function Viewer")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # frontend may be hosted separately (e.g. GitHub Pages) from this API
    allow_methods=["*"],
    allow_headers=["*"],
)


class FileUpload(BaseModel):
    path: str
    content: str


class AnalyzeRequest(BaseModel):
    root: str = ""
    files: list[FileUpload]


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/api/analyze")
def analyze(req: AnalyzeRequest) -> dict:
    """Analyze source files read client-side and uploaded as text (the server never touches the visitor's filesystem)."""
    if not req.files:
        raise HTTPException(status_code=400, detail="No supported source files were provided")
    sources = {f.path: f.content for f in req.files}
    try:
        return analyze_sources(sources, root=req.root)
    except Exception as exc:  # surface analysis failures to the UI
        raise HTTPException(status_code=500, detail=f"Analysis failed: {exc}")


_dist = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend", "dist")
if os.path.isdir(_dist):
    app.mount("/", StaticFiles(directory=_dist, html=True), name="frontend")
