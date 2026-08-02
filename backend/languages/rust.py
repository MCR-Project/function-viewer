"""Rust language plugin: tree-sitter-based analysis of Rust source trees.

Mirrors python.py's two-pass shape (register everything, then resolve
calls) but walks a tree-sitter concrete syntax tree instead of Python's
`ast`. Modules are namespaced by crate: a file's crate is whatever
directory sits directly above its nearest `src/` segment (Cargo convention),
so `crate::` inside a crate and `use that_crate::...` from a sibling crate
in the same workspace resolve to the same module names. If no `src/`
segment exists at all (a flat picked folder with no Cargo layout), every
file is treated as one crate named "crate", matching a single-crate project.

Calls are resolved when the callee is: a same-crate/`use`-imported plain
call, `self.method()`, `Type::method()` / `module::func()`, or
`variable.method()` when the variable's type can be inferred from its
function-parameter annotation or its `let` binding (explicit type, a
`Type::new(...)`-shaped call, or a `Type { ... }` struct literal). Deeper
inference (e.g. through a struct field, or a reassignment) is out of
scope, same as python.py's own resolver only handling direct references.

Two further real limits: `use` paths are resolved by name, not by
following actual re-exports, so a type imported through a `pub use` chain
is found via a crate-wide fallback search rather than the exact
re-exporting module. And calls made inside a macro invocation
(`println!(...)`, `format!(...)`, etc.) are invisible: tree-sitter parses a
macro's arguments as an opaque token stream, not as expressions.
"""

from __future__ import annotations

import tree_sitter_rust
from dataclasses import dataclass, field
from tree_sitter import Language, Node, Parser

from .base import FunctionInfo, LanguagePlugin

_RUST_LANGUAGE = Language(tree_sitter_rust.language())

# Leading `#[...]` attributes stay part of a function's displayed code (the
# Rust analog of a Python decorator); leading `///` doc comments become the
# docstring and are excluded from it (the Rust analog of a docstring).
_ATTR_TYPE = "attribute_item"
_DOC_COMMENT_TYPE = "line_comment"


@dataclass
class RustModuleInfo:
    rel_path: str  # forward slashes
    module: str  # "<crate>" or "<crate>::a::b", derived from the file's path
    crate_name: str  # this file's own crate - what "crate::..." resolves to from here
    top_level: dict[str, str] = field(default_factory=dict)  # fn name -> function id
    # impl target type name -> {method name -> function id}, merged across
    # every `impl Type` / `impl Trait for Type` block for that type.
    classes: dict[str, dict[str, str]] = field(default_factory=dict)
    # local name -> (module_hint, original_name), from `use` declarations
    from_imports: dict[str, tuple[str, str]] = field(default_factory=dict)


def _crate_and_relpath(rel_path: str) -> tuple[str, str]:
    """Splits a path into (crate name, path relative to that crate's src/).

    "crates/foo/src/db/mod.rs" -> ("foo", "db/mod.rs"); "src/main.rs" ->
    ("crate", "main.rs"); "main.rs" (no src/ at all) -> ("crate", "main.rs").
    """
    parts = rel_path.split("/")
    if "src" in parts:
        idx = parts.index("src")
        before, after = parts[:idx], parts[idx + 1 :]
        crate_name = before[-1] if before else "crate"
        return crate_name, "/".join(after)
    return "crate", rel_path


def _module_name(crate_name: str, within_src: str) -> str:
    stem = within_src[: -len(".rs")] if within_src.endswith(".rs") else within_src
    parts = stem.split("/") if stem else []
    if parts and parts[-1] in ("main", "lib", "mod"):
        parts = parts[:-1]
    return crate_name if not parts else f"{crate_name}::" + "::".join(parts)


def _join_module(hint: str, name: str) -> str:
    return hint if not name else f"{hint}::{name}"


def _text(node: Node | None, source: bytes) -> str:
    if node is None:
        return ""
    return source[node.start_byte : node.end_byte].decode("utf-8", "replace")


def _flatten_path(node: Node, source: bytes) -> list[str]:
    """crate::helpers::helper_fn -> ["crate", "helpers", "helper_fn"]; a bare identifier -> [name]."""
    if node.type in ("scoped_identifier", "scoped_type_identifier"):
        path = node.child_by_field_name("path")
        name = node.child_by_field_name("name")
        segments = _flatten_path(path, source) if path else []
        segments.append(_text(name, source))
        return segments
    return [_text(node, source)]


def _base_type_name(node: Node, source: bytes) -> str:
    """Unwraps &T / &mut T / Generic<T> down to the plain type name text."""
    while node.type in ("reference_type", "generic_type"):
        inner = node.child_by_field_name("type")
        if inner is None:
            break
        node = inner
    if node.type == "scoped_type_identifier":
        return _text(node.child_by_field_name("name"), source) or _text(node, source)
    return _text(node, source)


def _is_async(fn_node: Node) -> bool:
    for child in fn_node.children:
        if child.type == "function_modifiers" and b"async" in child.text:
            return True
    return False


def _extract_params(fn_node: Node, source: bytes) -> list[dict]:
    params_node = fn_node.child_by_field_name("parameters")
    params: list[dict] = []
    if params_node is None:
        return params
    for child in params_node.children:
        if child.type == "self_parameter":
            params.append({"name": _text(child, source), "annotation": None, "default": None})
        elif child.type in ("parameter", "variadic_parameter"):
            pattern = child.child_by_field_name("pattern")
            type_node = child.child_by_field_name("type")
            name = _text(pattern, source) if pattern else _text(child, source)
            params.append({"name": name, "annotation": _text(type_node, source) or None, "default": None})
    return params


def _extract_param_types(fn_node: Node, source: bytes) -> dict[str, str]:
    """param name -> base type name, for parameters with a plain identifier pattern and a type."""
    params_node = fn_node.child_by_field_name("parameters")
    types: dict[str, str] = {}
    if params_node is None:
        return types
    for child in params_node.children:
        if child.type != "parameter":
            continue
        pattern = child.child_by_field_name("pattern")
        type_node = child.child_by_field_name("type")
        if pattern is not None and pattern.type == "identifier" and type_node is not None:
            types[_text(pattern, source)] = _base_type_name(type_node, source)
    return types


def _infer_value_type(node: Node, source: bytes) -> str | None:
    """Best-effort type of a `let` initializer: `Type::new(...)` or `Type { ... }`."""
    if node.type == "call_expression":
        func = node.child_by_field_name("function")
        if func is not None and func.type == "scoped_identifier":
            path = func.child_by_field_name("path")
            if path is not None and path.type not in ("scoped_identifier", "scoped_type_identifier"):
                return _text(path, source)
        return None
    if node.type == "struct_expression":
        name = node.child_by_field_name("name")
        return _base_type_name(name, source) if name is not None else None
    return None


def _collect_let_types(node: Node, source: bytes) -> dict[str, str]:
    """Local variable -> inferred type, from `let` bindings anywhere in a function body."""
    types: dict[str, str] = {}

    def walk(n: Node) -> None:
        if n.type in ("function_item", "impl_item", "mod_item"):
            return
        if n.type == "let_declaration":
            pattern = n.child_by_field_name("pattern")
            if pattern is not None and pattern.type == "identifier":
                name = _text(pattern, source)
                type_node = n.child_by_field_name("type")
                if type_node is not None:
                    types[name] = _base_type_name(type_node, source)
                else:
                    value = n.child_by_field_name("value")
                    inferred = _infer_value_type(value, source) if value is not None else None
                    if inferred:
                        types[name] = inferred
        for child in n.children:
            walk(child)

    walk(node)
    return types


def _leading_doc_and_attrs(fn_node: Node, source_lines: list[str]) -> tuple[int, str | None]:
    """Walks contiguous preceding `#[...]`/`///` siblings.

    Returns (code start line, docstring): attributes push the code's start
    line up to include them; doc comment lines are collected as the
    docstring and never appear in the code body.
    """
    collected: list[Node] = []
    cursor_line = fn_node.start_point[0] + 1
    node = fn_node.prev_sibling
    while node is not None:
        if cursor_line - (node.end_point[0] + 1) > 1:
            break
        if node.type == _ATTR_TYPE:
            collected.append(node)
        elif node.type == _DOC_COMMENT_TYPE and source_lines[node.start_point[0]].strip().startswith("///"):
            collected.append(node)
        else:
            break
        cursor_line = node.start_point[0] + 1
        node = node.prev_sibling
    collected.reverse()

    attr_start: int | None = None
    doc_lines: list[str] = []
    for node in collected:
        if node.type == _ATTR_TYPE:
            line = node.start_point[0] + 1
            attr_start = line if attr_start is None else min(attr_start, line)
        else:
            stripped = source_lines[node.start_point[0]].strip()
            doc_lines.append(stripped[3:].strip() if stripped.startswith("///") else stripped)

    start_line = attr_start if attr_start is not None else fn_node.start_point[0] + 1
    docstring = "\n".join(doc_lines).strip() if doc_lines else None
    return start_line, docstring


class _CallCollector:
    """Collects (lineno, callee-expr) pairs inside one function body, not descending into nested items."""

    _SKIP = {"function_item", "impl_item", "mod_item"}

    def __init__(self, resolver, class_name: str | None):
        self.resolver = resolver
        self.class_name = class_name
        self.found: list[tuple[int, str]] = []

    def visit(self, node: Node) -> None:
        if node.type in self._SKIP:
            return
        if node.type == "call_expression":
            func = node.child_by_field_name("function")
            if func is not None:
                target = self.resolver(func, self.class_name)
                if target:
                    self.found.append((node.start_point[0] + 1, target))
        for child in node.children:
            self.visit(child)


class _RustAnalyzer:
    def __init__(self, sources: dict[str, str]):
        self.sources = sources
        self.sources_bytes: dict[str, bytes] = {}
        self.modules: dict[str, RustModuleInfo] = {}
        self.crate_names: set[str] = set()
        self.functions: dict[str, FunctionInfo] = {}
        self.file_errors: dict[str, str] = {}
        self.file_functions: dict[str, list[str]] = {}

    def analyze(self) -> dict:
        self._parse_files()
        self._resolve_calls()
        return self._to_response()

    # -- pass 1: parse everything, register functions/impls/imports --

    def _parse_files(self) -> None:
        parser = Parser(_RUST_LANGUAGE)

        # Every crate name must be known before any file's `use` declarations
        # are processed (a `use` can reference a sibling crate parsed later).
        crate_by_path = {rel_path: _crate_and_relpath(rel_path) for rel_path in self.sources}
        self.crate_names = {crate_name for crate_name, _ in crate_by_path.values()}

        for rel_path, source in sorted(self.sources.items()):
            self.file_functions.setdefault(rel_path, [])
            source_bytes = source.encode("utf-8")
            self.sources_bytes[rel_path] = source_bytes
            tree = parser.parse(source_bytes)
            if tree.root_node.has_error:
                self.file_errors[rel_path] = "SyntaxError: Rust source has one or more parse errors"

            crate_name, within_src = crate_by_path[rel_path]
            mod = RustModuleInfo(rel_path=rel_path, module=_module_name(crate_name, within_src), crate_name=crate_name)
            self.modules[mod.module] = mod
            source_lines = source.splitlines()
            self._walk_module_body(mod, tree.root_node, source_bytes, source_lines)

    def _walk_module_body(self, mod: RustModuleInfo, node: Node, source: bytes, source_lines: list[str]) -> None:
        for child in node.children:
            if child.type == "function_item":
                info = self._register_function(mod, child, source, source_lines, class_name=None)
                mod.top_level[info.name] = info.id
            elif child.type == "impl_item":
                self._register_impl(mod, child, source, source_lines)
            elif child.type == "use_declaration":
                arg = child.child_by_field_name("argument")
                if arg is not None:
                    self._register_use(mod, arg, source)
            elif child.type == "mod_item":
                body = child.child_by_field_name("body")
                if body is not None:
                    self._walk_module_body(mod, body, source, source_lines)

    def _register_impl(self, mod: RustModuleInfo, node: Node, source: bytes, source_lines: list[str]) -> None:
        type_node = node.child_by_field_name("type")
        body = node.child_by_field_name("body")
        if type_node is None or body is None:
            return
        type_name = _base_type_name(type_node, source)
        methods = mod.classes.setdefault(type_name, {})
        for child in body.children:
            if child.type == "function_item":
                info = self._register_function(mod, child, source, source_lines, class_name=type_name)
                methods[info.name] = info.id

    def _register_use(self, mod: RustModuleInfo, node: Node, source: bytes) -> None:
        if node.type == "use_list":
            for item in node.children:
                if item.type not in ("{", "}", ","):
                    self._register_use(mod, item, source)
            return
        if node.type == "scoped_use_list":
            path = node.child_by_field_name("path")
            lst = node.child_by_field_name("list")
            prefix = _flatten_path(path, source) if path else []
            if lst is not None:
                for item in lst.children:
                    if item.type not in ("{", "}", ","):
                        self._bind_use(mod, prefix + _flatten_path(item, source), None)
            return
        if node.type == "use_wildcard":
            return  # glob imports: out of scope
        if node.type == "use_as_clause":
            path = node.child_by_field_name("path")
            alias = node.child_by_field_name("alias")
            segments = _flatten_path(path, source) if path else []
            self._bind_use(mod, segments, _text(alias, source) or None)
            return
        self._bind_use(mod, _flatten_path(node, source), None)

    def _bind_use(self, mod: RustModuleInfo, segments: list[str], alias: str | None) -> None:
        if not segments or not segments[-1]:
            return
        original = segments[-1]
        local = alias or original
        prefix = segments[:-1]

        if not prefix:
            module_hint = mod.crate_name
        elif prefix[0] == "super":
            return  # relative-to-parent-module paths: out of scope
        elif prefix[0] == "crate":
            module_hint = "::".join([mod.crate_name] + prefix[1:]) if len(prefix) > 1 else mod.crate_name
        elif prefix[0] in self.crate_names:
            module_hint = "::".join(prefix)  # absolute path into another crate in the workspace
        else:
            # 2018-edition-style path implicitly relative to this file's own crate
            # (e.g. `use helpers::helper_fn;` for a local sibling module)
            module_hint = "::".join([mod.crate_name] + prefix)

        mod.from_imports[local] = (module_hint, original)

    def _register_function(
        self,
        mod: RustModuleInfo,
        fn_node: Node,
        source: bytes,
        source_lines: list[str],
        class_name: str | None,
    ) -> FunctionInfo:
        name = _text(fn_node.child_by_field_name("name"), source) or "<anonymous>"
        qualname = f"{class_name}.{name}" if class_name else name
        func_id = f"{mod.rel_path}::{qualname}"

        start_line, docstring = _leading_doc_and_attrs(fn_node, source_lines)
        end_line = fn_node.end_point[0] + 1
        raw = source_lines[start_line - 1 : end_line]
        indent = len(raw[0]) - len(raw[0].lstrip()) if raw else 0
        code_lines = []
        for i, line in enumerate(raw):
            lineno = start_line + i
            text = line[indent:] if line[:indent].strip() == "" else line
            code_lines.append({"lineno": lineno, "text": text, "calls": []})

        return_node = fn_node.child_by_field_name("return_type")

        info = FunctionInfo(
            id=func_id,
            name=name,
            qualname=qualname,
            file=mod.rel_path,
            module=mod.module,
            class_name=class_name,
            lineno=fn_node.start_point[0] + 1,
            end_lineno=end_line,
            params=_extract_params(fn_node, source),
            returns=_text(return_node, source) or None,
            docstring=docstring,
            is_async=_is_async(fn_node),
            code_lines=code_lines,
        )
        self.functions[func_id] = info
        self.file_functions[mod.rel_path].append(func_id)
        info._node = fn_node  # type: ignore[attr-defined]
        info._param_types = _extract_param_types(fn_node, source)  # type: ignore[attr-defined]
        return info

    # -- pass 2: resolve calls --

    def _resolve_calls(self) -> None:
        for info in self.functions.values():
            mod = self.modules[info.module]
            node = info._node  # type: ignore[attr-defined]
            source = self.sources_bytes[mod.rel_path]
            body = node.child_by_field_name("body")

            local_types = dict(info._param_types)  # type: ignore[attr-defined]
            if body is not None:
                local_types.update(_collect_let_types(body, source))

            resolver = self._make_resolver(mod, local_types)
            collector = _CallCollector(resolver, info.class_name)
            if body is not None:
                for child in body.children:
                    collector.visit(child)

            line_index = {cl["lineno"]: cl for cl in info.code_lines}
            seen: set[str] = set()
            for lineno, target in collector.found:
                if lineno in line_index and target not in line_index[lineno]["calls"]:
                    line_index[lineno]["calls"].append(target)
                if target not in seen:
                    seen.add(target)
                    info.calls.append(target)
            del info._node  # type: ignore[attr-defined]
            del info._param_types  # type: ignore[attr-defined]

    def _resolve_method_on_type(self, mod: RustModuleInfo, type_name: str, method: str) -> str | None:
        """Resolves `<a value or type of type_name>.method()` / `TypeName::method()`."""
        if type_name in mod.classes:
            return mod.classes[type_name].get(method)
        if type_name in mod.from_imports:
            module_hint, original = mod.from_imports[type_name]
            imported_mod = self.modules.get(module_hint)
            if imported_mod and original in imported_mod.classes:
                return imported_mod.classes[original].get(method)
            # the exact submodule wasn't it directly - the type may reach here
            # through a `pub use` re-export elsewhere in the same crate
            fallback_mod = self._find_impl_in_crate(module_hint.split("::")[0], original)
            if fallback_mod:
                return fallback_mod.classes[original].get(method)
        return None

    def _find_impl_in_crate(self, crate_name: str, type_name: str) -> RustModuleInfo | None:
        for candidate in self.modules.values():
            if candidate.module == crate_name or candidate.module.startswith(crate_name + "::"):
                if type_name in candidate.classes:
                    return candidate
        return None

    def _find_fn_in_crate(self, crate_name: str, fn_name: str) -> RustModuleInfo | None:
        for candidate in self.modules.values():
            if candidate.module == crate_name or candidate.module.startswith(crate_name + "::"):
                if fn_name in candidate.top_level:
                    return candidate
        return None

    def _make_resolver(self, mod: RustModuleInfo, local_types: dict[str, str]):
        def resolve(func_node: Node, class_name: str | None) -> str | None:
            source = self.sources_bytes[mod.rel_path]

            # foo(...)
            if func_node.type == "identifier":
                name = _text(func_node, source)
                if name in mod.top_level:
                    return mod.top_level[name]
                if name in mod.from_imports:
                    module_hint, original = mod.from_imports[name]
                    target_mod = self.modules.get(module_hint)
                    if target_mod and original in target_mod.top_level:
                        return target_mod.top_level[original]
                    fallback_mod = self._find_fn_in_crate(module_hint.split("::")[0], original)
                    if fallback_mod:
                        return fallback_mod.top_level[original]
                return None

            # Type::method(...) / module::func(...) - single path segment only,
            # same "no deep chains" limit python.py's own resolver has.
            if func_node.type == "scoped_identifier":
                path = func_node.child_by_field_name("path")
                name_node = func_node.child_by_field_name("name")
                if path is None or name_node is None or path.type in ("scoped_identifier", "scoped_type_identifier"):
                    return None
                method = _text(name_node, source)
                base = _text(path, source)

                if base in ("self", "Self") and class_name:
                    result = self._resolve_method_on_type(mod, class_name, method)
                    if result:
                        return result
                result = self._resolve_method_on_type(mod, base, method)
                if result:
                    return result

                # not a type - maybe `base` is a module alias: module::func()
                if base in mod.from_imports:
                    module_hint, original = mod.from_imports[base]
                    target_mod = self.modules.get(_join_module(module_hint, original))
                    if target_mod and method in target_mod.top_level:
                        return target_mod.top_level[method]
                    fallback_mod = self._find_fn_in_crate(module_hint.split("::")[0], method)
                    if fallback_mod:
                        return fallback_mod.top_level[method]

                # bare module path with no `use` needed, e.g. `helpers::helper_fn()`
                if base in self.crate_names:
                    direct_mod = self.modules.get(base)
                else:
                    direct_mod = self.modules.get(mod.crate_name if base == "crate" else f"{mod.crate_name}::{base}")
                if direct_mod and method in direct_mod.top_level:
                    return direct_mod.top_level[method]
                return None

            # self.method(...) / variable.method() when the variable's type is known
            if func_node.type == "field_expression":
                value = func_node.child_by_field_name("value")
                field_node = func_node.child_by_field_name("field")
                if value is None or field_node is None:
                    return None
                method = _text(field_node, source)
                if value.type == "self":
                    return self._resolve_method_on_type(mod, class_name, method) if class_name else None
                if value.type == "identifier":
                    type_name = local_types.get(_text(value, source))
                    if type_name:
                        return self._resolve_method_on_type(mod, type_name, method)
                return None

            return None

        return resolve

    # -- output --

    def _to_response(self) -> dict:
        edges = []
        for info in self.functions.values():
            for cl in info.code_lines:
                for target in cl["calls"]:
                    edges.append({"source": info.id, "target": target, "line": cl["lineno"]})
        files = [
            {"path": rel_path, "functions": ids, "error": self.file_errors.get(rel_path)}
            for rel_path, ids in sorted(self.file_functions.items())
        ]
        return {
            "files": files,
            "functions": {fid: info.to_dict() for fid, info in self.functions.items()},
            "edges": edges,
        }


class RustLanguage(LanguagePlugin):
    id = "rust"
    label = "Rust"
    extensions = frozenset({".rs"})

    def analyze(self, sources: dict[str, str]) -> dict:
        return _RustAnalyzer(sources).analyze()
