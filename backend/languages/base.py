"""Shared types and the plugin contract every backend/languages/*.py module implements.

Each language plugin gets a slice of sources (already filtered to its own
extensions) and returns the same {"files", "functions", "edges"} shape,
built from the same FunctionInfo schema, so the frontend contract stays
identical no matter which languages produced it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

# Directories skipped while walking a disk tree, shared across all languages
# (build/dependency/vcs dirs a source scan should never descend into).
SKIP_DIRS = {"__pycache__", "node_modules", ".git", ".venv", "venv", ".tox", ".mypy_cache", "target"}


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


class LanguagePlugin(ABC):
    """One entry in the language registry (see languages/__init__.py)."""

    id: str  # "python", "rust" - also stamped onto each file/function in the response
    label: str  # "Python", "Rust"
    extensions: frozenset[str]  # {".py"}, {".rs"} - dot-prefixed, lowercase

    @abstractmethod
    def analyze(self, sources: dict[str, str]) -> dict:
        """sources: rel_path (forward slashes) -> content, already filtered to `extensions`.

        Returns {"files": [...], "functions": {...}, "edges": [...]} - the
        same shape the top-level analyzer.py dispatcher returns, scoped to
        just this language's files.
        """
