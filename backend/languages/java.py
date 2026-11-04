"""Java language plugin: tree-sitter-based analysis of Java source trees.

Mirrors python.py / rust.py's two-pass shape (register everything, then
resolve calls). A Java file states its own package directly
(`package com.example;`), so - unlike Rust, which has to guess its crate
root from a `src/` directory convention - no directory-layout convention is
needed: `module` is exactly the declared package (the empty string for a
file with none).

A Function is a `method_declaration` or `constructor_declaration` *with a
body*: an abstract method, an interface method signature with no `default`/
`static`, and an `@interface` member are never Functions. Nested classes
(static or inner) are supported as a second level of `className`
(`"Outer.Inner"`, so `qualname` is `"Outer.Inner.method"`); local classes
(declared inside a method body) and anonymous class bodies (`new X() {...}`)
are not - pass 1 only ever walks into a type's own body, never into a
method's. A constructor's name is the class's own simple name, so it gets a
qualname like any other method (`Foo.Foo`).

Two methods or constructors sharing a name in one class are overloads:
merging them under one id would silently drop one body, so each keeps its
own id, with its parameter types as a suffix in `name`/`qualname`/the id
(`process(int)`, `process(String)`) - but only when that class actually has
more than one method of that name; the common, non-overloaded case keeps
the plain name. A call is matched against a class's own overloads of that
name by argument count alone; when more than one remains (two overloads
with the same arity but different types - argument *type* is not
considered), the call is left unresolved rather than guessing.

A call resolves when the callee is: a bare `method()` or `this.method()` in
the exact same class (no superclass/interface fallback - a redeclared or
inherited-only method is invisible from here), `new Type(...)`,
`Type.method()` (static), or `value.method()` / `this.field.method()` when
`value`'s type is known from its parameter, local-variable or field
declaration. A call inside a lambda body is attributed to the method the
lambda is written in, not dropped the way a nested function's calls are in
python.py - lambdas are Java's idiomatic callback/stream shape, not a rare
local-helper pattern.

`Type` in any of the above is looked up: first as a nested type of the
enclosing class itself (real Java lexical scoping - this wins even when
another, unrelated class elsewhere in the package has a nested type of the
same simple name), then among same-package classes (no import needed, a
nested class reachable by its own simple name or by `Outer.Inner`), then
through this file's own named imports (`import pkg.Type;`), then as a last
resort by simple name project-wide (mirroring the Rust plugin's crate-wide
fallback) - which also covers a wildcard import (`import pkg.*;`) or one
that only brings in a type's static members, without a dedicated table for
either. A fully package-qualified reference (`new com.example.Thing()`)
matches an exact same `fqn` outright; one that doesn't is still tried by its
last segment alone, the same precision-for-completeness tradeoff as the
simple-name fallback. A step that finds more than one same-named candidate
does not guess; static-member imports (`import static pkg.Type.member;`)
are not resolved at all. Nothing is preprocessed: a record's implicit
components, accessors and canonical
constructor are not Functions unless written out by hand, and a compact
constructor (`public Point { ... }`, no parameter list) is not recognised.
"""

from __future__ import annotations

import math
import tree_sitter_java
from dataclasses import dataclass, field
from tree_sitter import Language, Node, Parser

from .base import FunctionInfo, LanguagePlugin, build_edges, dedupe_calls

_JAVA_LANGUAGE = Language(tree_sitter_java.language())

_TYPE_DECL = {"class_declaration", "interface_declaration", "enum_declaration", "record_declaration"}
_METHODLIKE = {"method_declaration", "constructor_declaration"}
_COMMENT = {"line_comment", "block_comment"}


@dataclass
class _Overload:
    func_id: str
    min_args: int
    max_args: float  # math.inf for a varargs parameter


@dataclass
class _ClassInfo:
    fqn: str  # package-qualified: "com.example.Outer.Inner" ("Outer.Inner" with no package)
    local_name: str  # without the package: "Outer.Inner"
    simple_name: str  # "Inner"
    package: str
    methods: dict[str, list[_Overload]] = field(default_factory=dict)  # constructors live under the class's own simple name
    fields: dict[str, str] = field(default_factory=dict)  # field name -> declared base type name


@dataclass
class _ModuleInfo:
    rel_path: str
    package: str
    imports: dict[str, str] = field(default_factory=dict)  # simple name -> "pkg.Type", named imports only


def _text(node: Node | None, source: bytes) -> str:
    if node is None:
        return ""
    return source[node.start_byte : node.end_byte].decode("utf-8", "replace")


def _flatten_scoped(node: Node, source: bytes) -> str:
    """`com.example.Foo` -> that dotted text; a bare identifier -> itself.

    `scoped_identifier` (a package/import path, or a qualifier used as an
    expression) exposes `scope`/`name` fields; `scoped_type_identifier` (the
    same dotted shape used in *type* position - a declared type, `new
    Foo.Bar(...)`, `Foo.Bar.staticCall()`) does not - tree-sitter-java gives
    it only positional children, so its last two are its own prefix and name.
    """
    if node.type == "scoped_identifier":
        scope = node.child_by_field_name("scope")
        name = node.child_by_field_name("name")
        prefix = _flatten_scoped(scope, source) if scope is not None else ""
        suffix = _text(name, source)
        return f"{prefix}.{suffix}" if prefix else suffix
    if node.type == "scoped_type_identifier":
        parts = [c for c in node.children if c.type in ("scoped_type_identifier", "type_identifier")]
        if len(parts) == 2:
            return f"{_flatten_scoped(parts[0], source)}.{_text(parts[1], source)}"
        return _text(node, source)
    return _text(node, source)


def _base_type_name(node: Node | None, source: bytes) -> str:
    """Unwraps `Generic<T>` / `T[]` down to the plain type name text (dotted, if qualified)."""
    if node is None:
        return ""
    while node.type in ("generic_type", "array_type"):
        inner = node.child_by_field_name("element") if node.type == "array_type" else (node.children[0] if node.children else None)
        if inner is None:
            break
        node = inner
    if node.type in ("scoped_type_identifier", "scoped_identifier"):
        return _flatten_scoped(node, source)
    return _text(node, source)


def _extract_package(root: Node, source: bytes) -> str:
    for child in root.children:
        if child.type == "package_declaration":
            for c in child.children:
                if c.type in ("scoped_identifier", "identifier"):
                    return _flatten_scoped(c, source)
    return ""


def _extract_imports(root: Node, source: bytes) -> dict[str, str]:
    """Named imports only: `import pkg.Type;` -> {"Type": "pkg.Type"}. `static` and `*` imports are out of scope."""
    imports: dict[str, str] = {}
    for child in root.children:
        if child.type != "import_declaration":
            continue
        if any(c.type in ("static", "asterisk") for c in child.children):
            continue
        for c in child.children:
            if c.type in ("scoped_identifier", "identifier"):
                fqn = _flatten_scoped(c, source)
                imports[fqn.rsplit(".", 1)[-1]] = fqn
    return imports


def _extract_params(fn_node: Node, source: bytes) -> list[dict]:
    params_node = fn_node.child_by_field_name("parameters")
    params: list[dict] = []
    if params_node is None:
        return params
    for child in params_node.children:
        if child.type not in ("formal_parameter", "spread_parameter"):
            continue
        type_node = child.child_by_field_name("type")
        name_node = child.child_by_field_name("name")
        name = _text(name_node, source) if name_node is not None else _text(child, source)
        annotation = _text(type_node, source) if type_node is not None else None
        if child.type == "spread_parameter" and annotation:
            annotation += "..."
        params.append({"name": name, "annotation": annotation, "default": None})
    return params


def _extract_param_types(fn_node: Node, source: bytes) -> dict[str, str]:
    """param name -> base type name, for every formal parameter with a type."""
    params_node = fn_node.child_by_field_name("parameters")
    types: dict[str, str] = {}
    if params_node is None:
        return types
    for child in params_node.children:
        if child.type not in ("formal_parameter", "spread_parameter"):
            continue
        type_node = child.child_by_field_name("type")
        name_node = child.child_by_field_name("name")
        if name_node is not None and type_node is not None:
            types[_text(name_node, source)] = _base_type_name(type_node, source)
    return types


def _signature(params: list[dict]) -> str:
    return "(" + ", ".join(p["annotation"] or "?" for p in params) + ")"


def _arity(fn_node: Node) -> tuple[int, float]:
    params_node = fn_node.child_by_field_name("parameters")
    if params_node is None:
        return 0, 0
    total = 0
    variadic = False
    for child in params_node.children:
        if child.type == "spread_parameter":
            variadic = True
        elif child.type == "formal_parameter":
            total += 1
    return total, math.inf if variadic else total


def _count_args(args_node: Node | None) -> int:
    if args_node is None:
        return 0
    return sum(1 for c in args_node.named_children if c.type not in _COMMENT)


def _infer_value_type(node: Node, source: bytes) -> str | None:
    """Best-effort type of a local-variable initializer: `new Type(...)`."""
    if node.type == "object_creation_expression":
        type_node = node.child_by_field_name("type")
        return _base_type_name(type_node, source) if type_node is not None else None
    return None


def _leading_javadoc(fn_node: Node, source_lines: list[str]) -> str | None:
    """The `/** ... */` block comment directly above, if any - excluded from the code body (it's a separate sibling node, not part of `fn_node`'s own span)."""
    prev = fn_node.prev_sibling
    if prev is None or prev.type != "block_comment":
        return None
    if fn_node.start_point[0] - prev.end_point[0] > 1:
        return None  # a blank line in between: not this function's doc comment
    raw = "\n".join(source_lines[prev.start_point[0] : prev.end_point[0] + 1]).strip()
    if not raw.startswith("/**"):
        return None
    inner = raw[3:]
    if inner.endswith("*/"):
        inner = inner[:-2]
    lines = [ln.strip()[1:].strip() if ln.strip().startswith("*") else ln.strip() for ln in inner.split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines) if lines else None


def _make_code_lines(source_lines: list[str], start_line: int, end_line: int) -> list[dict]:
    raw = source_lines[start_line - 1 : end_line]
    indent = len(raw[0]) - len(raw[0].lstrip()) if raw else 0
    code_lines = []
    for i, line in enumerate(raw):
        text = line[indent:] if line[:indent].strip() == "" else line
        code_lines.append({"lineno": start_line + i, "text": text, "calls": []})
    return code_lines


class _CallCollector:
    """Collects (lineno, callee-id) pairs inside one function body.

    Does not descend into a nested type or method/constructor (their calls
    belong to their own Function, or - for a local/anonymous class, never
    registered as one - nowhere); does descend into a lambda body, so a call
    made inside one is attributed to the enclosing method.
    """

    _SKIP = _METHODLIKE | _TYPE_DECL

    def __init__(self, resolver):
        self._resolver = resolver
        self.found: list[tuple[int, str]] = []

    def visit(self, node: Node) -> None:
        if node.type in self._SKIP:
            return
        if node.type == "method_invocation":
            target = self._resolver.resolve_invocation(node)
            if target:
                self.found.append((node.start_point[0] + 1, target))
        elif node.type == "object_creation_expression":
            target = self._resolver.resolve_construction(node)
            if target:
                self.found.append((node.start_point[0] + 1, target))
        for child in node.children:
            self.visit(child)


class _Resolver:
    def __init__(self, analyzer: "_JavaAnalyzer", mod: _ModuleInfo, class_info: _ClassInfo, local_types: dict[str, str]):
        self._analyzer = analyzer
        self._mod = mod
        self._class_info = class_info
        self._local_types = local_types
        self._source = analyzer.sources_bytes[mod.rel_path]

    def _resolve_type(self, name: str) -> _ClassInfo | None:
        """A nested type of the enclosing class itself (`Inner` from inside `Outer`, any depth of
        `name`) wins over every other candidate - that's what real Java lexical scoping does, and
        it sidesteps a same-simple-name nested class elsewhere in the package being ambiguous."""
        if name:
            direct = self._analyzer.classes_by_fqn.get(f"{self._class_info.fqn}.{name.rsplit('.', 1)[-1]}")
            if direct is not None:
                return direct
        return self._analyzer.resolve_type(self._mod, name)

    def _receiver_type(self, node: Node) -> _ClassInfo | None:
        source = self._source
        if node.type == "this":
            return self._class_info
        if node.type == "identifier":
            name = _text(node, source)
            declared = self._local_types.get(name) or self._class_info.fields.get(name)
            return self._resolve_type(declared) if declared else self._resolve_type(name)
        if node.type == "field_access":
            obj = node.child_by_field_name("object")
            fld = node.child_by_field_name("field")
            if obj is not None and obj.type == "this" and fld is not None:
                declared = self._class_info.fields.get(_text(fld, source))
                return self._resolve_type(declared) if declared else None
            return None
        if node.type == "object_creation_expression":
            type_node = node.child_by_field_name("type")
            return self._resolve_type(_base_type_name(type_node, source)) if type_node is not None else None
        if node.type in ("scoped_identifier", "scoped_type_identifier"):
            return self._resolve_type(_flatten_scoped(node, source))
        return None

    @staticmethod
    def _match_overload(overloads: list[_Overload] | None, arg_count: int) -> str | None:
        if not overloads:
            return None
        candidates = [o.func_id for o in overloads if o.min_args <= arg_count <= o.max_args]
        return candidates[0] if len(candidates) == 1 else None

    def resolve_invocation(self, node: Node) -> str | None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return None
        method_name = _text(name_node, self._source)
        arg_count = _count_args(node.child_by_field_name("arguments"))
        object_node = node.child_by_field_name("object")
        target = self._class_info if object_node is None else self._receiver_type(object_node)
        if target is None:
            return None
        return self._match_overload(target.methods.get(method_name), arg_count)

    def resolve_construction(self, node: Node) -> str | None:
        type_node = node.child_by_field_name("type")
        if type_node is None:
            return None
        target = self._resolve_type(_base_type_name(type_node, self._source))
        if target is None:
            return None
        arg_count = _count_args(node.child_by_field_name("arguments"))
        return self._match_overload(target.methods.get(target.simple_name), arg_count)


class _JavaAnalyzer:
    def __init__(self, sources: dict[str, str]):
        self.sources = sources
        self.sources_bytes: dict[str, bytes] = {}
        self.modules: dict[str, _ModuleInfo] = {}  # keyed by rel_path: a package holds many files
        self.classes_by_fqn: dict[str, _ClassInfo] = {}
        self.classes_by_simple: dict[str, list[str]] = {}  # every suffix of a dotted local name -> fqns sharing it
        self.functions: dict[str, FunctionInfo] = {}
        self.file_errors: dict[str, str] = {}
        self.file_functions: dict[str, list[str]] = {}
        self._nodes: dict[str, Node] = {}  # func id -> its AST node, pass 1 to pass 2
        self._param_types: dict[str, dict[str, str]] = {}  # func id -> its params' inferred types, pass 1 to pass 2
        self._class_of: dict[str, str] = {}  # func id -> owning class fqn, pass 1 to pass 2

    def analyze(self) -> dict:
        self._parse_files()
        self._resolve_calls()
        return self._to_response()

    # -- pass 1: parse everything, register classes/methods/imports --

    def _parse_files(self) -> None:
        parser = Parser(_JAVA_LANGUAGE)
        for rel_path, source in sorted(self.sources.items()):
            self.file_functions.setdefault(rel_path, [])
            source_bytes = source.encode("utf-8")
            self.sources_bytes[rel_path] = source_bytes
            tree = parser.parse(source_bytes)
            if tree.root_node.has_error:
                self.file_errors[rel_path] = "SyntaxError: Java source has one or more parse errors"

            package = _extract_package(tree.root_node, source_bytes)
            mod = _ModuleInfo(rel_path=rel_path, package=package, imports=_extract_imports(tree.root_node, source_bytes))
            self.modules[rel_path] = mod
            source_lines = source.splitlines()
            for child in tree.root_node.children:
                if child.type in _TYPE_DECL:
                    self._register_type(mod, child, source_bytes, source_lines, [])

    def _register_type(self, mod: _ModuleInfo, node: Node, source: bytes, source_lines: list[str], parent_path: list[str]) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        path_parts = parent_path + [_text(name_node, source)]
        local_name = ".".join(path_parts)
        fqn = f"{mod.package}.{local_name}" if mod.package else local_name
        class_info = _ClassInfo(fqn=fqn, local_name=local_name, simple_name=path_parts[-1], package=mod.package)
        self.classes_by_fqn[fqn] = class_info
        for i in range(len(path_parts)):
            self.classes_by_simple.setdefault(".".join(path_parts[i:]), []).append(fqn)

        raw_methods: list[Node] = []
        body = node.child_by_field_name("body")
        if body is not None:
            self._walk_type_body(mod, body, source, source_lines, class_info, raw_methods, path_parts)
        self._finalize_methods(mod, class_info, raw_methods, source, source_lines)

    def _walk_type_body(
        self,
        mod: _ModuleInfo,
        body: Node,
        source: bytes,
        source_lines: list[str],
        class_info: _ClassInfo,
        raw_methods: list[Node],
        path_parts: list[str],
    ) -> None:
        for child in body.children:
            if child.type in _METHODLIKE:
                if child.child_by_field_name("body") is not None:
                    raw_methods.append(child)
            elif child.type == "field_declaration":
                self._register_field(class_info, child, source)
            elif child.type in _TYPE_DECL:
                self._register_type(mod, child, source, source_lines, path_parts)
            elif child.type == "enum_body_declarations":
                self._walk_type_body(mod, child, source, source_lines, class_info, raw_methods, path_parts)

    @staticmethod
    def _register_field(class_info: _ClassInfo, node: Node, source: bytes) -> None:
        type_node = node.child_by_field_name("type")
        if type_node is None:
            return
        base = _base_type_name(type_node, source)
        for child in node.children:
            if child.type == "variable_declarator":
                name_node = child.child_by_field_name("name")
                if name_node is not None:
                    class_info.fields[_text(name_node, source)] = base

    def _finalize_methods(
        self, mod: _ModuleInfo, class_info: _ClassInfo, raw_methods: list[Node], source: bytes, source_lines: list[str]
    ) -> None:
        by_name: dict[str, list[Node]] = {}
        for node in raw_methods:
            name_node = node.child_by_field_name("name")
            by_name.setdefault(_text(name_node, source) if name_node else "<anonymous>", []).append(node)

        for name, nodes in by_name.items():
            params_by_node = {n: _extract_params(n, source) for n in nodes}
            ambiguous = len({_signature(p) for p in params_by_node.values()}) > 1
            taken: dict[str, int] = {}
            for node in nodes:
                params = params_by_node[node]
                display_name = f"{name}{_signature(params)}" if ambiguous else name
                qualname = f"{class_info.local_name}.{display_name}"
                func_id = f"{mod.rel_path}::{qualname}"
                taken[func_id] = taken.get(func_id, 0) + 1
                if taken[func_id] > 1:
                    func_id += f"#{taken[func_id]}"

                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                is_ctor = node.type == "constructor_declaration"
                return_node = None if is_ctor else node.child_by_field_name("type")

                info = FunctionInfo(
                    id=func_id,
                    name=display_name,
                    qualname=qualname,
                    file=mod.rel_path,
                    module=mod.package,
                    class_name=class_info.local_name,
                    lineno=start_line,
                    end_lineno=end_line,
                    params=params,
                    returns=_text(return_node, source) if return_node is not None else None,
                    docstring=_leading_javadoc(node, source_lines),
                    is_async=False,
                    code_lines=_make_code_lines(source_lines, start_line, end_line),
                )
                self.functions[func_id] = info
                self.file_functions[mod.rel_path].append(func_id)
                self._nodes[func_id] = node
                self._param_types[func_id] = _extract_param_types(node, source)
                self._class_of[func_id] = class_info.fqn

                min_args, max_args = _arity(node)
                class_info.methods.setdefault(name, []).append(_Overload(func_id, min_args, max_args))

    # -- pass 2: resolve calls --

    def resolve_type(self, mod: _ModuleInfo, name: str) -> _ClassInfo | None:
        """Same package (no import needed) -> this file's named imports -> project-wide by simple name, if unique.

        `name` may be a bare simple name, a same-file qualified nested path ("Outer.Inner"), or a
        fully package-qualified reference ("com.example.Thing"): an exact fqn match wins outright,
        then the dotted or simple suffix is looked up in `classes_by_simple` (a package-qualified
        reference that isn't exactly one of our classes falls back to matching by its last segment
        alone, the same "give up precision before giving up entirely" tradeoff the Rust plugin makes
        for a `use` path that isn't exactly one level below its crate root).
        """
        if not name:
            return None
        if name in self.classes_by_fqn:
            return self.classes_by_fqn[name]
        simple = name.rsplit(".", 1)[-1]
        candidates = self.classes_by_simple.get(name) or self.classes_by_simple.get(simple) or []
        if not candidates:
            return None
        same_package = [c for c in candidates if self.classes_by_fqn[c].package == mod.package]
        if len(same_package) == 1:
            return self.classes_by_fqn[same_package[0]]
        imported = mod.imports.get(simple)
        if imported in self.classes_by_fqn:
            return self.classes_by_fqn[imported]
        if len(candidates) == 1:
            return self.classes_by_fqn[candidates[0]]
        return None

    def _resolve_calls(self) -> None:
        for info in self.functions.values():
            mod = self.modules[info.file]
            node = self._nodes.pop(info.id)
            class_info = self.classes_by_fqn[self._class_of.pop(info.id)]
            source = self.sources_bytes[mod.rel_path]
            body = node.child_by_field_name("body")

            local_types = dict(self._param_types.pop(info.id))
            if body is not None:
                local_types.update(self._collect_local_types(body, source))

            resolver = _Resolver(self, mod, class_info, local_types)
            collector = _CallCollector(resolver)
            if body is not None:
                collector.visit(body)

            dedupe_calls(info, collector.found)

    def _collect_local_types(self, body: Node, source: bytes) -> dict[str, str]:
        """Local variable -> declared or best-effort inferred type, from anywhere in a function's own body
        (including inside a lambda, since calls there are attributed back to this function)."""
        types: dict[str, str] = {}

        def walk(n: Node) -> None:
            if n.type in _CallCollector._SKIP:
                return
            if n.type == "local_variable_declaration":
                type_node = n.child_by_field_name("type")
                declared = _base_type_name(type_node, source) if type_node is not None else ""
                for child in n.children:
                    if child.type != "variable_declarator":
                        continue
                    name_node = child.child_by_field_name("name")
                    if name_node is None:
                        continue
                    name = _text(name_node, source)
                    if declared and declared != "var":
                        types[name] = declared
                    else:
                        value = child.child_by_field_name("value")
                        inferred = _infer_value_type(value, source) if value is not None else None
                        if inferred:
                            types[name] = inferred
            for child in n.children:
                walk(child)

        walk(body)
        return types

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


class JavaLanguage(LanguagePlugin):
    id = "java"
    label = "Java"
    extensions = frozenset({".java"})

    def analyze(self, sources: dict[str, str]) -> dict:
        return _JavaAnalyzer(sources).analyze()
