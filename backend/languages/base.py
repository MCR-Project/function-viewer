"""Shared types and the plugin contract every backend/languages/*.py module implements.

Each language plugin gets a slice of sources (already filtered to the
extensions it owns or claimed for this load) and returns the same
{"files", "functions", "edges"} shape, built from the same FunctionInfo
schema, so the frontend contract stays identical no matter which languages
produced it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Collection
from dataclasses import dataclass, field

# Directories skipped while walking a disk tree, shared across all languages
# (build/dependency/vcs dirs a source scan should never descend into).
SKIP_DIRS = {"__pycache__", "node_modules", ".git", ".venv", "venv", ".tox", ".mypy_cache", "target", "dist", "build"}


@dataclass
class FunctionInfo:
    id: str
    name: str
    qualname: str
    file: str  # relative path, forward slashes
    module: str
    class_name: str | None
    lineno: int
    end_lineno: int
    params: list[dict]
    returns: str | None
    docstring: str | None
    is_async: bool
    code_lines: list[dict] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)  # unique target ids

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "qualname": self.qualname,
            "file": self.file,
            "module": self.module,
            "className": self.class_name,
            "lineno": self.lineno,
            "endLineno": self.end_lineno,
            "params": self.params,
            "returns": self.returns,
            "docstring": self.docstring,
            "isAsync": self.is_async,
            "codeLines": self.code_lines,
            "calls": self.calls,
        }


def dedupe_calls(info: FunctionInfo, found: list[tuple[int, str]]) -> None:
    """Folds a function's raw (lineno, callee-id) hits into its code lines and its own
    unique `calls` list. Shared by every plugin's pass-2 call resolution: each callee
    is recorded once per calling line and once overall, regardless of how many lines
    or expressions on that line call it.
    """
    line_index = {cl["lineno"]: cl for cl in info.code_lines}
    seen: set[str] = set()
    for lineno, target in found:
        if lineno in line_index and target not in line_index[lineno]["calls"]:
            line_index[lineno]["calls"].append(target)
        if target not in seen:
            seen.add(target)
            info.calls.append(target)


def build_edges(functions: dict[str, FunctionInfo]) -> list[dict]:
    """One {"source", "target", "line"} entry per resolved call, read back off
    each function's own code lines. Shared by every plugin's response building.
    """
    edges = []
    for info in functions.values():
        for cl in info.code_lines:
            for target in cl["calls"]:
                edges.append({"source": info.id, "target": target, "line": cl["lineno"]})
    return edges


class LanguagePlugin(ABC):
    """One entry in the language registry (see languages/__init__.py)."""

    id: str  # "python", "rust" - stamped onto each file/function unless the plugin sets its own
    label: str  # "Python", "Rust"
    extensions: frozenset[str]  # {".py"}, {".rs"} - dot-prefixed, lowercase
    # Extensions this plugin MAY claim, in addition to `extensions` - dot-prefixed,
    # lowercase. `extensions` are owned unconditionally (C keeps `.h` as the
    # fallback); a conditional extension goes to this plugin only for a load where
    # `claims()` says so (C++ takes `.h` when the root holds a C++-only file).
    conditional_extensions: frozenset[str] = frozenset()

    def claims(self, ext: str, paths: Collection[str]) -> bool:
        """Whether this plugin takes files with the extension `ext` (one of its
        `conditional_extensions`) in this load.

        `paths` is every relative path (forward slashes) handed to the analyzer,
        of any language. The dispatcher asks once per distinct extension per
        load, in registry order; the first plugin to claim it wins, and when none
        does the unconditional owner (`extensions`) gets the files. Default: False.
        """
        return False

    @abstractmethod
    def analyze(self, sources: dict[str, str]) -> dict:
        """sources: rel_path (forward slashes) -> content, already filtered to
        `extensions` plus any `conditional_extensions` this plugin claimed.

        Returns {"files": [...], "functions": {...}, "edges": [...]} - the
        same shape the top-level analyzer.py dispatcher returns, scoped to
        just this language's files.

        A plugin that reads a family of related languages (TypeScript and
        JavaScript) sets "language" on each file and function entry itself;
        the dispatcher only fills it in, with `id`, where it is missing.
        """
