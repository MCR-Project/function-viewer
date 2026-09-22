"""TypeScript / JavaScript language plugin: tree-sitter-based analysis.

One plugin reads both languages. TypeScript's syntax is a superset of
JavaScript's and the two import each other freely, so splitting them would
lose every call edge that crosses a `.ts`/`.js` boundary (the dispatcher
hands a plugin only its own extensions). Each file is still stamped with its
own Language, `typescript` or `javascript`, by extension, which drives its
badge and highlighting.

"Untyped" is not a mode: a parameter without an annotation simply has
`annotation: None`, exactly as an unannotated TypeScript parameter does.

Functions are module-level declarations: functions; class methods,
constructors, static methods, getters and setters (named `get x` / `set x`);
arrow and function expressions bound to a name, class-field arrows included;
object-literal methods (`obj.method`); and anonymous default exports, named
`<file>.default` so two files' defaults stay apart. Bodiless overload,
abstract and interface signatures are not Functions. Anonymous callbacks and
nested closures aren't Functions either: their calls are attributed to the
enclosing function. A leading `/** */` block is the docstring (its types are
not read), and decorators stay part of a member's code.

Imports: ESM (relative, named, default, namespace, `export { } from`,
`export *`) and CommonJS (`require`, `module.exports`, `exports.x`). A
relative specifier resolves against the importing file, leaving off the
extension or naming a directory's `index`, and `./x.js` finds `x.ts`. Nothing
else is read (no tsconfig or package.json), so any other specifier resolves
only when exactly one loaded file's path ends with it; packages never do.

Calls resolved: `foo()`, `new Foo()`, `this.m()`, `Class.staticM()`, `ns.f()`
and `variable.m()` when the variable's class is known from a parameter
annotation, `const x: Foo` or `const x = new Foo()` - so annotated code
yields more edges than untyped code, but through the same path. `<Foo />`,
`<Foo>...</Foo>` and `<Foo.Bar />` resolve the same way, anchored to the
opening tag's name - JSX desugars to a call, so mounting a component is a
call from the function that renders it. A lowercase tag (`<div />`) is a DOM
element, not a call, by JSX's own capitalization convention; a fragment
(`<>...</>`) has no name and is never a call. A tag naming a class resolves
to nothing (see the member-resolution limit below: a class has no single id
in this plugin, only its methods do).

Real limits, in the spirit of the other plugins' resolvers: one object and
one member only (no `this.a.b()`, no `<Ns.Deep.Thing />`); no inheritance
(`super`, inherited methods); no dynamic calls (`obj[key]()`, `.call`,
`.apply`); a function passed as a value (`items.map(fn)`) isn't a call; a
getter or setter runs on property access, which isn't a call; `export * as
ns` isn't followed; hand-written `React.createElement(...)` isn't recognized
as a JSX-shaped call; and top-level statements belong to no Function.
"""

from __future__ import annotations

import posixpath
import tree_sitter_typescript
from dataclasses import dataclass, field
from tree_sitter import Language, Node, Parser

from .base import FunctionInfo, LanguagePlugin

_TS_GRAMMAR = Language(tree_sitter_typescript.language_typescript())
_TSX_GRAMMAR = Language(tree_sitter_typescript.language_tsx())

# `.ts`/`.mts`/`.cts` use the plain TypeScript grammar (angle-bracket casts are
# legal there, JSX is not). Everything else, plain JavaScript included, uses
# TSX: a superset that also parses JSX, and JS never needs the casts.
_TS_GRAMMAR_EXTENSIONS = frozenset({".ts", ".mts", ".cts"})
_TYPESCRIPT_EXTENSIONS = frozenset({".ts", ".tsx", ".mts", ".cts"})
_JAVASCRIPT_EXTENSIONS = frozenset({".js", ".jsx", ".mjs", ".cjs"})

# Overload, abstract and interface signatures have no body and are separate
# node types (function_signature, method_signature, ...), so they never match.
_FUNCTION_NODE_TYPES = frozenset(
    {
        "function_declaration",
        "generator_function_declaration",
        "function_expression",
        "function",  # the same node under older grammar versions
        "generator_function",
        "arrow_function",
    }
)
_CLASS_NODE_TYPES = frozenset({"class_declaration", "abstract_class_declaration", "class"})
_VARIABLE_STATEMENT_TYPES = frozenset({"lexical_declaration", "variable_declaration"})

# What a specifier may leave off, tried in this order (TypeScript wins over a
# compiled `.js` sitting beside it).
_RESOLVE_EXTENSIONS = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs")
# `import "./x.js"` in TypeScript ESM code means the source file x.ts.
_TS_SOURCE_FOR_JS_SPECIFIER = {".js": (".ts", ".tsx"), ".jsx": (".tsx",), ".mjs": (".mts",), ".cjs": (".cts",)}


@dataclass
class _Import:
    specifier: str  # the module string exactly as written
    original: str | None  # the exported name imported; "default" for a default import; None for a namespace import


@dataclass
class _ModuleInfo:
    rel_path: str  # forward slashes
    module: str  # rel_path without its extension
    top_level: dict[str, str] = field(default_factory=dict)  # fn name -> function id
    # class (or object literal) name -> {member name -> function id}
    classes: dict[str, dict[str, str]] = field(default_factory=dict)
    imports: dict[str, _Import] = field(default_factory=dict)  # local name -> import
    export_aliases: dict[str, str] = field(default_factory=dict)  # `export { local as name }`: name -> local
    default_export: str | None = None  # local name behind `export default`
    reexports: dict[str, tuple[str, str]] = field(default_factory=dict)  # `export { o as n } from`: n -> (specifier, o)
    star_reexports: list[str] = field(default_factory=list)  # specifiers of `export * from`


def _is_skipped_file(rel_path: str) -> bool:
    """Type declaration files (no function bodies) and minified bundles (build output).

    Mirrors SKIP_FILE in frontend/src/localFiles.ts; here it also covers the CLI's disk walk.
    """
    name = posixpath.basename(rel_path)
    return name.endswith((".d.ts", ".d.mts", ".d.cts", ".min.js", ".min.mjs", ".min.cjs"))


def _extension(rel_path: str) -> str:
    return posixpath.splitext(rel_path)[1]


def _language_id(rel_path: str) -> str:
    return "typescript" if _extension(rel_path) in _TYPESCRIPT_EXTENSIONS else "javascript"


def _module_name(rel_path: str) -> str:
    return posixpath.splitext(rel_path)[0]


def _text(node: Node | None, source: bytes) -> str:
    if node is None:
        return ""
    return source[node.start_byte : node.end_byte].decode("utf-8", "replace")


def _annotation_text(node: Node | None, source: bytes) -> str | None:
    """`: string` / `: Promise<void>` -> the type text without its leading colon."""
    text = _text(node, source).lstrip(":").strip()
    return text or None


def _string_value(node: Node | None, source: bytes) -> str:
    return _text(node, source).strip("'\"`")


def _clean_jsdoc(comment: str) -> str | None:
    """`/** ... */` -> its text, without the delimiters and the leading `*` of each line."""
    body = comment[3:]
    if body.endswith("*/"):
        body = body[:-2]
    lines = []
    for raw in body.split("\n"):
        line = raw.strip()
        if line.startswith("*"):
            line = line[1:]
            if line.startswith(" "):
                line = line[1:]
        lines.append(line.rstrip())
    return "\n".join(lines).strip() or None


def _leading_decorators_and_jsdoc(span_node: Node, source: bytes) -> tuple[int, str | None]:
    """Returns (code start line, docstring).

    Decorators directly above a member stay part of its code (the analog of a
    Python decorator) and pull the start line up to them. A `/** */` block on
    the line above that becomes the docstring and is never part of the code.
    """
    start_line = span_node.start_point[0] + 1
    node = span_node.prev_sibling
    while node is not None and node.type == "decorator" and 0 <= start_line - (node.end_point[0] + 1) <= 1:
        start_line = node.start_point[0] + 1
        node = node.prev_sibling
    if node is not None and node.type == "comment" and start_line - (node.end_point[0] + 1) == 1:
        text = _text(node, source)
        if text.startswith("/**"):
            return start_line, _clean_jsdoc(text)
    return start_line, None


def _require_specifier(node: Node | None, source: bytes) -> str | None:
    """`require("./x")` -> "./x"; anything else -> None."""
    if node is None or node.type != "call_expression":
        return None
    func = node.child_by_field_name("function")
    args = node.child_by_field_name("arguments")
    first = next((c for c in args.children if c.is_named), None) if args is not None else None
    if func is None or func.type != "identifier" or _text(func, source) != "require":
        return None
    return _string_value(first, source) if first is not None and first.type == "string" else None


def _is_module_exports(node: Node | None, source: bytes) -> bool:
    if node is None or node.type != "member_expression":
        return False
    obj = node.child_by_field_name("object")
    return (
        obj is not None
        and obj.type == "identifier"
        and _text(obj, source) == "module"
        and _text(node.child_by_field_name("property"), source) == "exports"
    )


def _member_name(node: Node | None, source: bytes) -> str | None:
    """A class member / object key as a plain name; computed keys (`[expr]`) have none."""
    if node is None:
        return None
    if node.type in ("property_identifier", "private_property_identifier", "identifier", "number"):
        return _text(node, source)
    if node.type == "string":
        return _text(node, source).strip("'\"`")
    return None


def _type_name(node: Node | None, source: bytes) -> str | None:
    """`Foo`, `Foo<T>` and `Foo | null` -> "Foo". Anything less plain has no single class to resolve against."""
    if node is None:
        return None
    if node.type == "type_annotation":
        inner = next((c for c in node.children if c.is_named), None)
        return _type_name(inner, source)
    if node.type == "type_identifier":
        return _text(node, source)
    if node.type == "generic_type":
        return _type_name(node.child_by_field_name("name"), source)
    if node.type == "union_type":
        names = {_type_name(c, source) for c in node.children if c.is_named} - {None}
        return next(iter(names)) if len(names) == 1 else None
    return None


def _collect_local_types(fn_node: Node, source: bytes) -> dict[str, str]:
    """Variable -> class name, from parameter annotations, `const x: Foo`, and `const x = new Foo()`.

    Walks the whole function, nested callbacks included, and ignores shadowing.
    """
    types: dict[str, str] = {}

    def walk(node: Node) -> None:
        if node.type in ("required_parameter", "optional_parameter"):
            pattern = node.child_by_field_name("pattern")
            type_name = _type_name(node.child_by_field_name("type"), source)
            if pattern is not None and pattern.type == "identifier" and type_name:
                types[_text(pattern, source)] = type_name
        elif node.type == "variable_declarator":
            name = node.child_by_field_name("name")
            if name is not None and name.type == "identifier":
                type_name = _type_name(node.child_by_field_name("type"), source)
                value = node.child_by_field_name("value")
                if type_name is None and value is not None and value.type == "new_expression":
                    ctor = value.child_by_field_name("constructor")
                    type_name = _text(ctor, source) if ctor is not None and ctor.type == "identifier" else None
                if type_name:
                    types[_text(name, source)] = type_name
        for child in node.children:
            walk(child)

    walk(fn_node)
    return types


def _is_async(fn_node: Node) -> bool:
    return any(child.type == "async" for child in fn_node.children)


def _extract_params(fn_node: Node, source: bytes) -> list[dict]:
    params_node = fn_node.child_by_field_name("parameters")
    if params_node is None:
        # `x => ...`: a lone unparenthesized arrow parameter
        single = fn_node.child_by_field_name("parameter")
        return [{"name": _text(single, source), "annotation": None, "default": None}] if single is not None else []
    params: list[dict] = []
    for child in params_node.children:
        if child.type not in ("required_parameter", "optional_parameter"):
            continue
        pattern = child.child_by_field_name("pattern")
        name = _text(pattern, source) if pattern is not None else _text(child, source)
        if child.type == "optional_parameter":
            name += "?"
        value = child.child_by_field_name("value")
        params.append(
            {
                "name": name,
                "annotation": _annotation_text(child.child_by_field_name("type"), source),
                "default": _text(value, source) or None,
            }
        )
    return params


# jsx_opening_element (the `<Foo>` half of a paired `<Foo>...</Foo>`) and
# jsx_self_closing_element (`<Foo />`) both carry a `name` field; a fragment's
# opening tag (`<>`) has none.
_JSX_TAG_NODE_TYPES = frozenset({"jsx_opening_element", "jsx_self_closing_element"})


def _is_intrinsic_jsx_tag(name_node: Node) -> bool:
    """`<div />` (lowercase first letter) is a DOM element, not a call; `<Foo />` / `<_Foo />` are."""
    first = (name_node.text or b"")[:1]
    return first.islower()


def _anchor_line(node: Node) -> int:
    """A method name on a later line than its object (a chain) is where the wire should leave from."""
    anchor = node.child_by_field_name("property") if node.type == "member_expression" else None
    return (anchor or node).start_point[0] + 1


class _CallCollector:
    """Collects (lineno, callee-id) pairs inside one function body.

    Descends into everything, nested functions and callbacks included: their
    calls are folded into the enclosing function's flow.
    """

    def __init__(self, resolver):
        self.resolver = resolver
        self.found: list[tuple[int, str]] = []

    def visit(self, node: Node) -> None:
        if node.type in ("call_expression", "new_expression"):
            is_new = node.type == "new_expression"
            func = node.child_by_field_name("constructor" if is_new else "function")
            if func is not None:
                target = self.resolver(func, is_new)
                if target:
                    self.found.append((_anchor_line(func), target))
        elif node.type in _JSX_TAG_NODE_TYPES:
            name = node.child_by_field_name("name")
            if name is not None and not (name.type == "identifier" and _is_intrinsic_jsx_tag(name)):
                target = self.resolver(name, False)
                if target:
                    self.found.append((_anchor_line(name), target))
        for child in node.children:
            self.visit(child)


class _TypeScriptAnalyzer:
    def __init__(self, sources: dict[str, str]):
        self.sources = sources
        self.sources_bytes: dict[str, bytes] = {}
        self.modules: dict[str, _ModuleInfo] = {}  # rel_path -> module
        self.functions: dict[str, FunctionInfo] = {}
        self.file_errors: dict[str, str] = {}
        self.file_functions: dict[str, list[str]] = {}
        self._specifier_cache: dict[tuple[str, str], _ModuleInfo | None] = {}

    def analyze(self) -> dict:
        self._parse_files()
        self._resolve_calls()
        return self._to_response()

    # -- pass 1: parse everything, register functions --

    def _parse_files(self) -> None:
        ts_parser = Parser(_TS_GRAMMAR)
        tsx_parser = Parser(_TSX_GRAMMAR)
        for rel_path, source in sorted(self.sources.items()):
            self.file_functions.setdefault(rel_path, [])
            source_bytes = source.encode("utf-8")
            self.sources_bytes[rel_path] = source_bytes
            parser = ts_parser if _extension(rel_path) in _TS_GRAMMAR_EXTENSIONS else tsx_parser
            tree = parser.parse(source_bytes)
            if tree.root_node.has_error:
                self.file_errors[rel_path] = "SyntaxError: source has one or more parse errors"

            mod = _ModuleInfo(rel_path=rel_path, module=_module_name(rel_path))
            self.modules[rel_path] = mod
            # tree-sitter rows count only "\n", so split on exactly that (not str.splitlines)
            source_lines = [line.rstrip("\r") for line in source.split("\n")]
            self._walk_module_body(mod, tree.root_node, source_bytes, source_lines)

    def _walk_module_body(self, mod: _ModuleInfo, node: Node, source: bytes, source_lines: list[str]) -> None:
        for stmt in node.children:
            if stmt.type == "import_statement":
                self._register_import(mod, stmt, source)
                continue
            if stmt.type == "export_statement":
                decl = stmt.child_by_field_name("declaration") or stmt.child_by_field_name("value")
                self._register_export(mod, stmt, decl, source)
            else:
                decl = stmt
            if decl is not None:
                self._register_declaration(mod, decl, stmt, source, source_lines)
            if stmt.type == "expression_statement":
                self._register_commonjs_export(mod, stmt, source, source_lines)

    def _register_require(self, mod: _ModuleInfo, name_node: Node, value: Node, source: bytes) -> bool:
        """`const x = require(..)`, `const { a, b: c } = require(..)`, `const f = require(..).f`. False if it isn't one."""
        prop = None
        call = value
        if value.type == "member_expression":
            call = value.child_by_field_name("object")
            prop = _member_name(value.child_by_field_name("property"), source)
        specifier = _require_specifier(call, source)
        if specifier is None:
            return False
        if name_node.type == "identifier":
            mod.imports[_text(name_node, source)] = _Import(specifier, prop)  # no prop: the whole module
        elif name_node.type == "object_pattern" and prop is None:
            for item in name_node.children:
                if item.type == "shorthand_property_identifier_pattern":
                    name = _text(item, source)
                    mod.imports[name] = _Import(specifier, name)
                elif item.type == "pair_pattern":
                    key = _member_name(item.child_by_field_name("key"), source)
                    local = item.child_by_field_name("value")
                    if key and local is not None and local.type == "identifier":
                        mod.imports[_text(local, source)] = _Import(specifier, key)
        return True

    def _register_commonjs_export(self, mod: _ModuleInfo, stmt: Node, source: bytes, source_lines: list[str]) -> None:
        """`module.exports = ...`, `exports.x = ...` and `module.exports.x = ...`."""
        assign = next((c for c in stmt.children if c.type == "assignment_expression"), None)
        left = assign.child_by_field_name("left") if assign is not None else None
        right = assign.child_by_field_name("right") if assign is not None else None
        if left is None or right is None or left.type != "member_expression":
            return
        obj = left.child_by_field_name("object")
        prop = _member_name(left.child_by_field_name("property"), source)

        if _is_module_exports(left, source):  # module.exports = <value>
            if right.type == "object":
                self._register_exported_object(mod, right, source, source_lines)
            elif right.type == "identifier":
                mod.default_export = _text(right, source)
            elif right.type in _FUNCTION_NODE_TYPES or right.type in _CLASS_NODE_TYPES:
                mod.default_export = self._register_exported_value(mod, None, right, stmt, source, source_lines)
        elif prop and obj is not None and (
            (obj.type == "identifier" and _text(obj, source) == "exports") or _is_module_exports(obj, source)
        ):  # exports.prop = <value>
            if right.type == "identifier":
                mod.export_aliases[prop] = _text(right, source)
            elif right.type in _FUNCTION_NODE_TYPES or right.type in _CLASS_NODE_TYPES:
                self._register_exported_value(mod, prop, right, stmt, source, source_lines)

    def _register_exported_value(
        self, mod: _ModuleInfo, name: str | None, value: Node, span: Node, source: bytes, source_lines: list[str]
    ) -> str:
        """Registers a function or class assigned to module.exports / exports.name; returns the name it is known by."""
        name = name or _text(value.child_by_field_name("name"), source) or self._default_name(mod)
        if value.type in _CLASS_NODE_TYPES:
            self._register_members(mod, name, value.child_by_field_name("body"), source, source_lines)
        else:
            info = self._register_function(mod, value, span, name, None, source, source_lines)
            mod.top_level[name] = info.id
        return name

    def _register_exported_object(self, mod: _ModuleInfo, obj: Node, source: bytes, source_lines: list[str]) -> None:
        """`module.exports = { a, b: fn, c: function () {}, d() {} }`: each entry is an export of the module."""
        for member in obj.children:
            if member.type == "method_definition":
                name = _member_name(member.child_by_field_name("name"), source)
                if name and member.child_by_field_name("body") is not None:
                    info = self._register_function(mod, member, member, name, None, source, source_lines)
                    mod.top_level[name] = info.id
            elif member.type == "pair":
                key = _member_name(member.child_by_field_name("key"), source)
                value = member.child_by_field_name("value")
                if not key or value is None:
                    continue
                if value.type == "identifier":
                    mod.export_aliases[key] = _text(value, source)
                elif value.type in _FUNCTION_NODE_TYPES:
                    info = self._register_function(mod, value, member, key, None, source, source_lines)
                    mod.top_level[key] = info.id
            # shorthand `a` needs nothing: a top-level `a` is already importable by that name

    def _register_import(self, mod: _ModuleInfo, stmt: Node, source: bytes) -> None:
        specifier = _string_value(stmt.child_by_field_name("source"), source)
        for clause in stmt.children:
            if clause.type == "import_require_clause":  # import x = require("./x")
                ident = next((c for c in clause.children if c.type == "identifier"), None)
                spec = _string_value(clause.child_by_field_name("source"), source)
                if ident is not None:
                    mod.imports[_text(ident, source)] = _Import(spec, None)
            elif clause.type == "import_clause":
                for part in clause.children:
                    if part.type == "identifier":  # import def from
                        mod.imports[_text(part, source)] = _Import(specifier, "default")
                    elif part.type == "namespace_import":  # import * as ns from
                        ident = next((c for c in part.children if c.type == "identifier"), None)
                        if ident is not None:
                            mod.imports[_text(ident, source)] = _Import(specifier, None)
                    elif part.type == "named_imports":  # import { a, b as c } from
                        for item in part.children:
                            if item.type != "import_specifier":
                                continue
                            name = _text(item.child_by_field_name("name"), source)
                            alias = _text(item.child_by_field_name("alias"), source)
                            mod.imports[alias or name] = _Import(specifier, name)

    def _register_export(self, mod: _ModuleInfo, stmt: Node, decl: Node | None, source: bytes) -> None:
        """Records what this module exports beyond declarations it makes itself: `export { }`, re-exports, `export default`."""
        src = stmt.child_by_field_name("source")
        specifier = _string_value(src, source)
        clause = next((c for c in stmt.children if c.type == "export_clause"), None)
        if clause is not None:
            for item in clause.children:
                if item.type != "export_specifier":
                    continue
                name = _text(item.child_by_field_name("name"), source)
                alias = _text(item.child_by_field_name("alias"), source) or name
                if src is not None:
                    mod.reexports[alias] = (specifier, name)  # export { name as alias } from "..."
                else:
                    mod.export_aliases[alias] = name  # export { name as alias }
        elif src is not None and not any(c.type == "namespace_export" for c in stmt.children):
            mod.star_reexports.append(specifier)  # export * from "..."  (`export * as ns` is not followed)

        if decl is not None and any(c.type == "default" for c in stmt.children):
            if decl.type == "identifier":
                mod.default_export = _text(decl, source)
            elif decl.type in _FUNCTION_NODE_TYPES or decl.type in _CLASS_NODE_TYPES:
                mod.default_export = _text(decl.child_by_field_name("name"), source) or self._default_name(mod)

    @staticmethod
    def _default_name(mod: _ModuleInfo) -> str:
        """`export default` with no name of its own: `Button.default` for Button.tsx, so two files' defaults differ."""
        return f"{posixpath.basename(mod.module)}.default"

    def _register_declaration(
        self, mod: _ModuleInfo, decl: Node, stmt: Node, source: bytes, source_lines: list[str]
    ) -> None:
        """decl is the declaration proper; stmt is the whole statement (an `export` wrapper included), shown as code."""
        if decl.type in _FUNCTION_NODE_TYPES:
            name = _text(decl.child_by_field_name("name"), source) or self._default_name(mod)
            info = self._register_function(mod, decl, stmt, name, None, source, source_lines)
            mod.top_level[name] = info.id
        elif decl.type in _CLASS_NODE_TYPES:
            name = _text(decl.child_by_field_name("name"), source) or self._default_name(mod)
            self._register_members(mod, name, decl.child_by_field_name("body"), source, source_lines)
        elif decl.type in _VARIABLE_STATEMENT_TYPES:
            declarators = [c for c in decl.children if c.type == "variable_declarator"]
            for declarator in declarators:
                name_node = declarator.child_by_field_name("name")
                value = declarator.child_by_field_name("value")
                if name_node is None or value is None:
                    continue
                if self._register_require(mod, name_node, value, source):
                    continue
                if name_node.type != "identifier":
                    continue
                name = _text(name_node, source)
                span = stmt if len(declarators) == 1 else declarator
                if value.type in _FUNCTION_NODE_TYPES:
                    info = self._register_function(mod, value, span, name, None, source, source_lines)
                    mod.top_level[name] = info.id
                elif value.type in _CLASS_NODE_TYPES:
                    self._register_members(mod, name, value.child_by_field_name("body"), source, source_lines)
                elif value.type == "object":
                    self._register_members(mod, name, value, source, source_lines)

    def _register_members(
        self, mod: _ModuleInfo, owner: str, container: Node | None, source: bytes, source_lines: list[str]
    ) -> None:
        """Registers a class body's or object literal's function members as `owner.member`."""
        members = mod.classes.setdefault(owner, {})
        if container is None:
            return
        for member in container.children:
            fn_node = member
            if member.type == "method_definition":
                name = _member_name(member.child_by_field_name("name"), source)
                if name is None or member.child_by_field_name("body") is None:
                    continue
                accessor = next((c.type for c in member.children if c.type in ("get", "set")), None)
                if accessor:
                    name = f"{accessor} {name}"  # a getter and setter can share a name, so keep them apart
            elif member.type in ("public_field_definition", "pair"):
                key = member.child_by_field_name("name" if member.type == "public_field_definition" else "key")
                name = _member_name(key, source)
                value = member.child_by_field_name("value")
                if name is None or value is None or value.type not in _FUNCTION_NODE_TYPES:
                    continue
                fn_node = value
            else:
                continue
            info = self._register_function(mod, fn_node, member, name, owner, source, source_lines)
            members[name] = info.id

    def _register_function(
        self,
        mod: _ModuleInfo,
        fn_node: Node,
        span_node: Node,
        name: str,
        class_name: str | None,
        source: bytes,
        source_lines: list[str],
    ) -> FunctionInfo:
        """fn_node holds the params and body; span_node is the whole statement shown as the card's code."""
        qualname = f"{class_name}.{name}" if class_name else name
        func_id = f"{mod.rel_path}::{qualname}"

        start_line, docstring = _leading_decorators_and_jsdoc(span_node, source)
        end_line = span_node.end_point[0] + 1
        raw = source_lines[start_line - 1 : end_line]
        indent = len(raw[0]) - len(raw[0].lstrip()) if raw else 0
        code_lines = []
        for i, line in enumerate(raw):
            text = line[indent:] if line[:indent].strip() == "" else line
            code_lines.append({"lineno": start_line + i, "text": text, "calls": []})

        info = FunctionInfo(
            id=func_id,
            name=name,
            qualname=qualname,
            file=mod.rel_path,
            module=mod.module,
            class_name=class_name,
            lineno=start_line,
            end_lineno=end_line,
            params=_extract_params(fn_node, source),
            returns=_annotation_text(fn_node.child_by_field_name("return_type"), source),
            docstring=docstring,
            is_async=_is_async(fn_node),
            code_lines=code_lines,
        )
        self.functions[func_id] = info
        self.file_functions[mod.rel_path].append(func_id)
        info._node = fn_node  # type: ignore[attr-defined]
        return info

    # -- pass 2: resolve calls --

    def _resolve_calls(self) -> None:
        for info in self.functions.values():
            mod = self.modules[info.file]
            node = info._node  # type: ignore[attr-defined]
            body = node.child_by_field_name("body")

            resolver = self._make_resolver(mod, info.class_name, _collect_local_types(node, self.sources_bytes[mod.rel_path]))
            collector = _CallCollector(resolver)
            if body is not None:
                collector.visit(body)

            line_index = {cl["lineno"]: cl for cl in info.code_lines}
            seen: set[str] = set()
            for lineno, target in collector.found:
                if lineno in line_index and target not in line_index[lineno]["calls"]:
                    line_index[lineno]["calls"].append(target)
                if target not in seen:
                    seen.add(target)
                    info.calls.append(target)
            del info._node  # type: ignore[attr-defined]

    # -- module and export resolution --

    def _resolve_specifier(self, mod: _ModuleInfo, specifier: str) -> _ModuleInfo | None:
        """The loaded module a specifier names, or None (a package, or a file that wasn't picked)."""
        key = (posixpath.dirname(mod.rel_path) if specifier.startswith(".") else "", specifier)
        if key not in self._specifier_cache:
            found = self._resolve_relative(mod, specifier) if specifier.startswith(".") else self._resolve_bare(specifier)
            self._specifier_cache[key] = found
        return self._specifier_cache[key]

    def _resolve_relative(self, mod: _ModuleInfo, specifier: str) -> _ModuleInfo | None:
        base = posixpath.normpath(posixpath.join(posixpath.dirname(mod.rel_path), specifier))
        if base == ".." or base.startswith("../"):
            return None  # climbs out of the picked folder
        stem, ext = posixpath.splitext(base)
        candidates = [base]
        candidates += [stem + e for e in _TS_SOURCE_FOR_JS_SPECIFIER.get(ext, ())]
        candidates += [base + e for e in _RESOLVE_EXTENSIONS]
        candidates += [posixpath.join(base, "index" + e) for e in _RESOLVE_EXTENSIONS]
        return next((self.modules[c] for c in candidates if c in self.modules), None)

    def _resolve_bare(self, specifier: str) -> _ModuleInfo | None:
        """Aliases (`@/lib/x`) and root-relative paths (`src/lib/x`): no config is read, so a
        specifier resolves only when exactly one loaded file's path ends with it."""
        path = specifier
        for prefix in ("@/", "~/", "#/"):
            if path.startswith(prefix):
                path = path[len(prefix) :]
        stem, ext = posixpath.splitext(path)
        if ext in _RESOLVE_EXTENSIONS:
            path = stem
        if not path:
            return None
        matches = []
        for candidate in self.modules.values():
            names = {candidate.module}
            if posixpath.basename(candidate.module) == "index":
                names.add(posixpath.dirname(candidate.module))
            if any(n == path or n.endswith("/" + path) for n in names):
                matches.append(candidate)
        return matches[0] if len(matches) == 1 else None

    def _lookup_export(self, mod: _ModuleInfo, name: str, seen: set[str] | None = None) -> tuple[_ModuleInfo, str] | None:
        """(defining module, local name) of what `mod` exports as `name`, following re-exports.

        The local name is a key of that module's `top_level` (a function) and/or `classes` (a class or object).
        """
        seen = set() if seen is None else seen
        if mod.rel_path in seen:
            return None  # circular re-exports
        seen.add(mod.rel_path)

        if name == "default":
            if mod.default_export is not None:
                return mod, mod.default_export
        else:
            local = mod.export_aliases.get(name, name)
            if local in mod.top_level or local in mod.classes:
                return mod, local
        if name in mod.reexports:
            specifier, original = mod.reexports[name]
            target = self._resolve_specifier(mod, specifier)
            found = self._lookup_export(target, original, seen) if target else None
            if found:
                return found
        if name != "default":  # `export *` never re-exports a default
            for specifier in mod.star_reexports:
                target = self._resolve_specifier(mod, specifier)
                found = self._lookup_export(target, name, seen) if target else None
                if found:
                    return found
        return None

    def _imported_symbol(self, mod: _ModuleInfo, name: str) -> tuple[_ModuleInfo, str] | None:
        """Where the import bound to `name` is defined. A whole-module import (`import * as`, or a
        CommonJS `require`) stands for the module's default export, i.e. `module.exports = thing`."""
        imp = mod.imports.get(name)
        if imp is None:
            return None
        target = self._resolve_specifier(mod, imp.specifier)
        return self._lookup_export(target, imp.original or "default") if target else None

    def _method_of(self, mod: _ModuleInfo, class_name: str, method: str) -> str | None:
        """A method of the class (or object literal) `class_name` names in `mod`, declared there or imported."""
        if class_name in mod.classes:
            return mod.classes[class_name].get(method)
        symbol = self._imported_symbol(mod, class_name)
        return symbol[0].classes.get(symbol[1], {}).get(method) if symbol else None

    def _make_resolver(self, mod: _ModuleInfo, class_name: str | None, local_types: dict[str, str]):
        source = self.sources_bytes[mod.rel_path]

        def resolve(func_node: Node, is_new: bool = False) -> str | None:
            # foo(...) / new Foo(...)
            if func_node.type == "identifier":
                name = _text(func_node, source)
                if is_new:
                    return self._method_of(mod, name, "constructor")
                if name in mod.top_level:
                    return mod.top_level[name]
                symbol = self._imported_symbol(mod, name)
                return symbol[0].top_level.get(symbol[1]) if symbol else None

            # this.m(...) / ns.f(...) / Class.staticM(...) / variable.m(...) / new ns.Foo(...)
            # - one object, one member: no deeper chains (`this.a.b()`), like the other plugins.
            if func_node.type == "member_expression":
                obj = func_node.child_by_field_name("object")
                member = _member_name(func_node.child_by_field_name("property"), source)
                if obj is None or member is None:
                    return None
                if obj.type == "this":
                    return self._method_of(mod, class_name, member) if class_name and not is_new else None
                if obj.type != "identifier":
                    return None
                name = _text(obj, source)

                imp = mod.imports.get(name)
                if imp is not None and imp.original is None:  # import * as ns
                    target = self._resolve_specifier(mod, imp.specifier)
                    found = self._lookup_export(target, member) if target else None
                    if found:
                        if is_new:
                            return found[0].classes.get(found[1], {}).get("constructor")
                        return found[0].top_level.get(found[1])
                    # else `module.exports = class/object`: its members are reached through the default export below
                if is_new:
                    return None

                if name in local_types:  # a variable whose class is known
                    return self._method_of(mod, local_types[name], member)
                return self._method_of(mod, name, member)  # Class.staticMethod() / object.method()
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
            {
                "path": rel_path,
                "functions": ids,
                "error": self.file_errors.get(rel_path),
                "language": _language_id(rel_path),
            }
            for rel_path, ids in sorted(self.file_functions.items())
        ]
        functions = {}
        for fid, info in self.functions.items():
            entry = info.to_dict()
            entry["language"] = _language_id(info.file)
            functions[fid] = entry
        return {"files": files, "functions": functions, "edges": edges}


class TypeScriptLanguage(LanguagePlugin):
    id = "typescript"  # fallback only: each file and function carries its own `language`
    label = "TypeScript/JavaScript"
    extensions = _TYPESCRIPT_EXTENSIONS | _JAVASCRIPT_EXTENSIONS

    def analyze(self, sources: dict[str, str]) -> dict:
        readable = {path: content for path, content in sources.items() if not _is_skipped_file(path)}
        return _TypeScriptAnalyzer(readable).analyze()
