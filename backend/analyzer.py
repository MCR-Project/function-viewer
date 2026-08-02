"""Multi-language dispatcher: groups sources by extension, hands each slice
to its language plugin (see backend/languages/), and merges the results.

Every plugin returns the same {"files", "functions", "edges"} shape (see
backend/languages/base.py), so merging across languages is just
concatenation - function/file ids are already namespaced by file path, so
two languages' ids can never collide.
"""

from __future__ import annotations

import json
import os
import sys

from .languages import EXTENSION_TO_LANGUAGE, LANGUAGES, SKIP_DIRS, SUPPORTED_EXTENSIONS


def _collect_source_files(root: str) -> list[str]:
    """Absolute paths of every supported source file under root (or root itself if a file)."""
    if os.path.isfile(root):
        return [root]
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(filenames):
            if os.path.splitext(name)[1] in SUPPORTED_EXTENSIONS:
                found.append(os.path.join(dirpath, name))
    return found


def analyze_sources(sources: dict[str, str], root: str = "") -> dict:
    """Analyze in-memory sources, e.g. files picked and read in the browser.

    sources: rel_path (forward slashes) -> file content, already read.
    """
    by_language: dict[str, dict[str, str]] = {}
    for rel_path, content in sources.items():
        ext = os.path.splitext(rel_path)[1]
        lang = EXTENSION_TO_LANGUAGE.get(ext)
        if lang is None:
            continue
        by_language.setdefault(lang.id, {})[rel_path] = content

    files: list[dict] = []
    functions: dict[str, dict] = {}
    edges: list[dict] = []

    for lang in LANGUAGES:
        lang_sources = by_language.get(lang.id)
        if not lang_sources:
            continue
        result = lang.analyze(lang_sources)
        for file_entry in result["files"]:
            file_entry["language"] = lang.id
            files.append(file_entry)
        for func_id, func in result["functions"].items():
            func["language"] = lang.id
            functions[func_id] = func
        edges.extend(result["edges"])

    files.sort(key=lambda f: f["path"])
    return {"root": root, "files": files, "functions": functions, "edges": edges}


def _collect_disk_sources(root: str) -> tuple[dict[str, str], dict[str, str]]:
    """rel_path (forward slashes) -> content for every supported file under root, plus rel_path -> read error."""
    abs_root = os.path.abspath(root)
    base_dir = os.path.dirname(abs_root) if os.path.isfile(abs_root) else abs_root
    sources: dict[str, str] = {}
    errors: dict[str, str] = {}
    for abs_path in _collect_source_files(abs_root):
        rel_path = os.path.relpath(abs_path, base_dir).replace(os.sep, "/")
        try:
            with open(abs_path, "r", encoding="utf-8", errors="replace") as fh:
                sources[rel_path] = fh.read()
        except OSError as exc:
            errors[rel_path] = f"{type(exc).__name__}: {exc}"
    return sources, errors


def analyze_path(path: str) -> dict:
    """Analyze a project living on this machine's filesystem (local script/CLI use)."""
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    sources, read_errors = _collect_disk_sources(path)
    result = analyze_sources(sources, root=os.path.abspath(path))
    if read_errors:
        known = {f["path"] for f in result["files"]}
        for rel_path, msg in read_errors.items():
            if rel_path not in known:
                result["files"].append({"path": rel_path, "functions": [], "error": msg, "language": None})
        result["files"].sort(key=lambda f: f["path"])
    return result


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "."
    print(json.dumps(analyze_path(target), indent=2))
