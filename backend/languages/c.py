"""C language plugin: tree-sitter-based analysis of C source trees.

Owns `.c` and `.h`, except that the C++ plugin claims `.h` when the picked
root contains a C++-only extension; a C-only root is unaffected. Nothing is
preprocessed or compiled: the plugin reads the text of each file as written,
and resolves calls by name, following the way a C program's names are
actually visible.

Functions are definitions only, a function with a body. A prototype in a
header is not a Function, though its comment can document one (below).
`qualname` is the function name, `module` is the file path without its
extension, and the id is `<file>::<name>`, with `#2`, `#3` appended to
further same-named definitions in one file (the branches of an `#ifdef`).

The contiguous own-line comment block directly above a definition (`//`,
`///`, `/* */`, `/** */`) is its docstring, markers stripped, and is not part
of its code body. A definition with no such comment takes the one above its
prototype in a quoted-included header instead.

A call `foo()` resolves in this order, and stops at the first step that finds
any candidate: (1) a definition in the same file, static or not; (2) a
definition in a file reached through quoted `#include "..."` (transitively),
or in the `.c` file implementing one of those headers (same file stem, the
header's own directory first; non-static only); (3) the one non-static
definition of the name anywhere in the picked root. Two or more candidates at
the step that finds them mean no edge: the plugin never picks arbitrarily.
`<system.h>` includes are ignored. An include path is tried against the
including file's directory, then the picked root, then by a path suffix that
exactly one loaded file ends with (standing in for the unread `-I` paths).
A `static` function is visible only in its own file, except one defined in an
included header (`static inline`), which the including files can call. A call
whose name is a parameter or a local variable is an indirect call and is
never linked, even when a function of that name is loaded.

Real limits: nothing is preprocessed, so a function defined by a macro is not
a Function and a call hidden in a macro body is invisible; every `#ifdef`
branch is parsed, whether or not it would be compiled; function-pointer calls
(`cb(x)`, `s->handler(x)`, `tbl[i](x)`) are not linked; and a file tree-sitter
cannot fully parse (macro-heavy code such as zlib's) is flagged with an error
but keeps whatever functions were recovered from it.
"""

from __future__ import annotations

import posixpath
import tree_sitter_c
from dataclasses import dataclass, field
from tree_sitter import Language, Node, Parser

from .base import FunctionInfo, LanguagePlugin, build_edges, dedupe_calls
from .cfamily import (
    comment_block_above,
    declarator_name,
    declares_function,
    extract_params,
    function_declarator_of,
    is_static,
    local_names,
    make_code_lines,
    node_text,
    param_names,
    prepare_source,
    quoted_includes,
    reached_files,
    resolve_include,
    return_type,
    split_lines,
    suffix_index,
    walk_top_level,
)

_C_LANGUAGE = Language(tree_sitter_c.language())


@dataclass
class _CFile:
    rel_path: str  # forward slashes
    module: str  # the path without its extension
    source: bytes
    lines: list[str]
    include_paths: list[str] = field(default_factory=list)  # the quoted paths as written
    definitions: dict[str, list[str]] = field(default_factory=dict)  # name -> function ids, in file order
    prototype_docs: dict[str, str] = field(default_factory=dict)  # name -> the comment above its first documented prototype


@dataclass
class _Pending:
    node: Node
    file: _CFile
    ignored: set[str]  # parameters and locals: a call through one of these is indirect


class _CAnalyzer:
    def __init__(self, sources: dict[str, str]):
        self.sources = sources
        self.files: dict[str, _CFile] = {}
        self.functions: dict[str, FunctionInfo] = {}
        self.file_errors: dict[str, str] = {}
        self.file_functions: dict[str, list[str]] = {}
        self.static_ids: set[str] = set()
        self.global_defs: dict[str, list[str]] = {}  # name -> ids of its non-static definitions
        self._pending: dict[str, _Pending] = {}  # func id -> what pass 2 needs
        self._reached: dict[str, list[str]] = {}
        self._implementations: dict[str, list[str]] = {}  # header path -> the .c files implementing it
        self._resolved: dict[tuple[str, str], str | None] = {}

    def analyze(self) -> dict:
        self._parse_files()
        self._link_includes()
        self._fill_missing_docstrings()
        self._resolve_calls()
        return self._to_response()

    # -- pass 1: parse everything, register definitions --

    def _parse_files(self) -> None:
        parser = Parser(_C_LANGUAGE)
        for rel_path, raw in sorted(self.sources.items()):
            self.file_functions.setdefault(rel_path, [])
            source = prepare_source(raw)
            source_bytes = source.encode("utf-8")
            tree = parser.parse(source_bytes)
            if tree.root_node.has_error:
                self.file_errors[rel_path] = "SyntaxError: C source has one or more parse errors"

            module = posixpath.splitext(rel_path)[0]
            cfile = _CFile(rel_path, module, source_bytes, split_lines(source))
            cfile.include_paths = quoted_includes(tree.root_node, source_bytes)
            self.files[rel_path] = cfile
            for node in walk_top_level(tree.root_node):
                if node.type == "function_definition":
                    self._register_function(cfile, node)
                elif node.type == "declaration":
                    self._register_prototypes(cfile, node)

    def _register_function(self, cfile: _CFile, fn_node: Node) -> None:
        source = cfile.source
        declarator = fn_node.child_by_field_name("declarator")
        fn_declarator = function_declarator_of(declarator)
        name = declarator_name(declarator, source)
        if fn_declarator is None or name is None:
            return  # tree-sitter's recovery of something that is not a plain definition

        defined = cfile.definitions.setdefault(name, [])
        func_id = f"{cfile.rel_path}::{name}"
        if defined:
            func_id += f"#{len(defined) + 1}"
        defined.append(func_id)

        start_line = fn_node.start_point[0] + 1
        end_line = fn_node.end_point[0] + 1
        body = fn_node.child_by_field_name("body")
        info = FunctionInfo(
            id=func_id,
            name=name,
            qualname=name,
            file=cfile.rel_path,
            module=cfile.module,
            class_name=None,
            lineno=start_line,
            end_lineno=end_line,
            params=extract_params(fn_declarator, source),
            returns=return_type(fn_node, source),
            docstring=comment_block_above(fn_node, source, cfile.lines),
            is_async=False,
            code_lines=make_code_lines(cfile.lines, start_line, end_line),
        )
        self.functions[func_id] = info
        self.file_functions[cfile.rel_path].append(func_id)
        if is_static(fn_node, source):
            self.static_ids.add(func_id)
        else:
            self.global_defs.setdefault(name, []).append(func_id)

        ignored = param_names(fn_declarator, source)
        if body is not None:
            ignored |= local_names(body, source)
        self._pending[func_id] = _Pending(fn_node, cfile, ignored)

    def _register_prototypes(self, cfile: _CFile, node: Node) -> None:
        """Remembers the comment above each function prototype, to document a definition elsewhere."""
        doc = None
        for declarator in node.children_by_field_name("declarator"):
            if not declares_function(declarator):
                continue
            name = declarator_name(declarator, cfile.source)
            if name is None or name in cfile.prototype_docs:
                continue
            doc = doc or comment_block_above(node, cfile.source, cfile.lines)
            if doc:
                cfile.prototype_docs[name] = doc

    # -- between the passes: what each file can see --

    def _link_includes(self) -> None:
        loaded = set(self.files)
        by_suffix = suffix_index(loaded)
        direct: dict[str, list[str]] = {}
        for rel_path, cfile in self.files.items():
            resolved = (resolve_include(rel_path, path, loaded, by_suffix) for path in cfile.include_paths)
            direct[rel_path] = [target for target in resolved if target is not None and target != rel_path]
        self._reached = {rel_path: reached_files(rel_path, direct) for rel_path in self.files}

        stems = {rel_path: posixpath.splitext(posixpath.basename(rel_path)) for rel_path in self.files}
        by_stem: dict[str, list[str]] = {}
        for rel_path, (stem, ext) in stems.items():
            if ext == ".c":
                by_stem.setdefault(stem, []).append(rel_path)
        for rel_path, (stem, ext) in stems.items():
            if ext != ".h":
                continue
            candidates = by_stem.get(stem, [])
            beside = [c for c in candidates if posixpath.dirname(c) == posixpath.dirname(rel_path)]
            self._implementations[rel_path] = beside or candidates

    def _fill_missing_docstrings(self) -> None:
        for info in self.functions.values():
            if info.docstring is not None:
                continue
            for included in self._reached[info.file]:
                doc = self.files[included].prototype_docs.get(info.name)
                if doc:
                    info.docstring = doc
                    break

    # -- pass 2: resolve calls --

    def _resolve_calls(self) -> None:
        for func_id, info in self.functions.items():
            pending = self._pending.pop(func_id)
            body = pending.node.child_by_field_name("body")
            found: list[tuple[int, str]] = []
            if body is not None:
                stack = [body]
                while stack:
                    node = stack.pop()
                    if node.type == "call_expression":
                        target = self._resolve_call(pending, node)
                        if target:
                            found.append((node.start_point[0] + 1, target))
                    stack.extend(reversed(node.children))  # so calls come out in source order
            dedupe_calls(info, found)

    def _resolve_call(self, pending: _Pending, call: Node) -> str | None:
        func = call.child_by_field_name("function")
        if func is None or func.type != "identifier":
            return None  # s->handler(x), tbl[i](x), (*fp)(x): function-pointer calls are not linked
        name = node_text(func, pending.file.source)
        if name in pending.ignored:
            return None
        return self._resolve_name(pending.file.rel_path, name)

    def _resolve_name(self, rel_path: str, name: str) -> str | None:
        key = (rel_path, name)
        if key not in self._resolved:
            self._resolved[key] = self._lookup(rel_path, name)
        return self._resolved[key]

    def _lookup(self, rel_path: str, name: str) -> str | None:
        # (1) a definition in the same file, static or not
        candidates = self.files[rel_path].definitions.get(name, [])
        if candidates:
            return candidates[0] if len(candidates) == 1 else None

        # (2) a definition in an included file, or non-static in the .c file implementing an included header
        reached = self._reached[rel_path]
        found: set[str] = set()
        for included in reached:
            found.update(self.files[included].definitions.get(name, []))
            for implementation in self._implementations.get(included, []):
                if implementation != rel_path:
                    found.update(fid for fid in self.files[implementation].definitions.get(name, []) if fid not in self.static_ids)
        if found:
            return next(iter(found)) if len(found) == 1 else None

        # (3) the one non-static definition anywhere
        candidates = self.global_defs.get(name, [])
        return candidates[0] if len(candidates) == 1 else None

    # -- output --

    def _to_response(self) -> dict:
        edges = build_edges(self.functions)
        files = [
            {"path": rel_path, "functions": ids, "error": self.file_errors.get(rel_path)}
            for rel_path, ids in sorted(self.file_functions.items())
        ]
        return {
            "files": files,
            "functions": {fid: info.to_dict() for fid, info in self.functions.items()},
            "edges": edges,
        }


class CLanguage(LanguagePlugin):
    id = "c"
    label = "C"
    extensions = frozenset({".c", ".h"})

    def analyze(self, sources: dict[str, str]) -> dict:
        return _CAnalyzer(sources).analyze()
