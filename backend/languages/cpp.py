"""C++ language plugin: tree-sitter-based analysis of C++ source trees.

Owns `.cc`, `.cpp`, `.cxx`, `.hpp`, `.hh` and `.hxx`, and claims `.h` for a
load that contains any of those (in a C-only load a header stays C). C and
C++ are separate plugins, so no Call edge joins a C file to a C++ one. As in
the C plugin, nothing is preprocessed or compiled: the plugin reads each file
as written and resolves calls by name, following C++'s own scoping, and it
never picks one candidate out of several.

Functions are definitions only, a function with a body: free functions,
methods defined in their class or out of it (`void Catalog::add(...) {}`),
constructors, destructors, operators and conversion operators. A declaration
alone (an in-class or header prototype, `= default`, `= delete`, a pure
virtual) is not a Function. `qualname` is the scoped name, `ns::Class::method`
(`Outer::Inner::method` for a nested class), `className` is the qualified
class a method belongs to, and `module` is the file path without its
extension. Template arguments are dropped, so a template is one Function. A
friend function defined in its class belongs to the enclosing namespace.
Lambdas are not Functions: a call inside one belongs to the Function the
lambda is written in. The id is `<file>::<qualname>`. Overloads defined in one
file get their parameter types as a suffix in `name`, `qualname` and the id
(`add(int)`, `add(const std::string &)`, `at(int) const`); a name defined once
in its file keeps its plain name, and identical definitions in one file (the
branches of an `#ifdef`) get `#2`, `#3` appended to the id.

The contiguous own-line comment block directly above a definition (above its
`template <...>` line, if any) is its docstring, as in C. A definition with
no such comment takes the one above a declaration of the same qualified name
(the in-class prototype, a header prototype) in its own file or a file it
reaches through quoted includes. Default arguments written only on such a
declaration still count when a call is matched by its number of arguments.

A call resolves at the first step that finds any candidate. When that step
finds several (overloads, or definitions in several files), the call links
only if the number of arguments, counting default arguments and `...`,
leaves exactly one of them.
- An unqualified `f()` is looked up in the enclosing class and its loaded
  base classes (so `m()` in a method finds the method wherever it is
  defined), then in each enclosing namespace from the innermost out, then in
  what the file's `using namespace ns;` and `using ns::f;` bring in, then at
  global scope. Outside a class the C plugin's visibility applies: the same
  file first, then files reached through quoted `#include "..."` and the
  `.cpp`/`.cc`/`.cxx` files implementing those headers (same file stem, the
  header's own directory first), then the one definition visible anywhere. A
  `static` free function, or one in an anonymous namespace, is visible only
  in its own file and in files that include it.
- `Class::m()` and `ns::f()` find their qualifier from the enclosing scopes
  outwards (`::f()` only at global scope), then look the name up in that
  class and its bases, or in that namespace.
- `this->m()`, `obj.m()` and `obj->m()` look the method up in the receiver's
  class, then its bases, the first class of the chain that defines it
  winning: a call through a `Base &` links to `Base::run` when it has a body,
  never to the overrides. The receiver's class is the enclosing class for
  `this`, the declared type of a parameter, a local variable or a data member
  of a loaded class (`items_`, `this->items_`, `order.item`), or the target of
  a `static_cast<Foo *>(...)`-style cast; `const Foo &`, `Foo *`,
  `std::unique_ptr<Foo>`, `std::shared_ptr<Foo>` and a `using` or `typedef`
  alias of one all mean `Foo`. When the type is not visible (`auto`, a
  template parameter, a returned value, a member that was not loaded), or the
  class chain has no definition of the method (a pure virtual), the call
  links to the one loaded method of that name, if there is exactly one. A
  visible type that is not a loaded class (`std::string`, `int`) links
  nothing.
- A constructor is called by `new T(...)`, `T(...)`, `T{...}`, `T var(...)`,
  `T var{...}` and a base initializer `: Base(...)`; `T var(x);`, which C++'s
  grammar also reads as a function declaration, is taken as a variable unless
  `x` names a class. A plain `T var;`, a copy and a destruction are not
  linked.
- A call whose name is a parameter or a local variable (`cb(x)`, a lambda
  held in a variable) is indirect and never linked.

Real limits: nothing is preprocessed, so a function defined by a macro is not
a Function, a call hidden in a macro is invisible, and a class body holding
an unknown macro (`Q_OBJECT`) can lose methods to tree-sitter's error
recovery (such a file is flagged with an error but keeps whatever functions
were recovered from it); every `#ifdef` branch is parsed; templates are not
instantiated, so a call on a value of a template parameter's type only gets
the unique-name fallback; there is no argument-dependent lookup and no
overload resolution by argument types; operator expressions (`a + b`) and
implicit conversions are not linked; calls through function pointers,
member pointers and `std::function` are not linked; and virtual dispatch to
overrides is not followed.
"""

from __future__ import annotations

import math
import os
import posixpath
import re
import tree_sitter_cpp
from collections.abc import Collection
from dataclasses import dataclass, field, replace
from tree_sitter import Language, Node, Parser

from .base import FunctionInfo, LanguagePlugin, build_edges, dedupe_calls
from .cfamily import (
    PARAMETER_TYPES,
    TRANSPARENT_TYPES,
    comment_block_above,
    declarator_name_node,
    extract_params,
    function_declarator_of,
    is_static,
    make_code_lines,
    node_text,
    prepare_source,
    quoted_includes,
    reached_files,
    resolve_include,
    split_lines,
    suffix_index,
)

_CPP_LANGUAGE = Language(tree_sitter_cpp.language())

# A header is implemented by a source file of the same stem.
_HEADER_EXTENSIONS = frozenset({".h", ".hh", ".hpp", ".hxx"})
_SOURCE_EXTENSIONS = frozenset({".cc", ".cpp", ".cxx"})

_CLASS_TYPES = frozenset({"class_specifier", "struct_specifier", "union_specifier"})
# Nodes whose children are items of the same scope: a namespace or class body, an `#ifdef` branch.
_CONTAINER_TYPES = TRANSPARENT_TYPES | {"field_declaration_list"}
_TYPE_PARAMETER_TYPES = frozenset(
    {
        "type_parameter_declaration",
        "optional_type_parameter_declaration",
        "variadic_type_parameter_declaration",
        "template_template_parameter_declaration",
    }
)
# Declarators a definition's function declarator can sit in: `Foo &Foo::get()`, `char *name()`.
_DEFINITION_WRAPPERS = frozenset(
    {"pointer_declarator", "reference_declarator", "attributed_declarator", "parenthesized_declarator"}
)
_DECLARATOR_TYPES = _DEFINITION_WRAPPERS | {"function_declarator", "operator_cast", "qualified_identifier"}
# Types whose meaning is only known once the program is compiled: a variable of one has no visible class.
_OPAQUE_TYPES = frozenset({"placeholder_type_specifier", "decltype", "dependent_type"})
_SMART_POINTERS = frozenset({"unique_ptr", "shared_ptr"})
_CASTS = frozenset({"static_cast", "dynamic_cast", "reinterpret_cast", "const_cast"})
# Nodes pass 2 inspects for calls; everything else is only walked through.
_CALL_SITES = frozenset({"call_expression", "new_expression", "compound_literal_expression", "declaration", "field_initializer"})

# The class of a variable whose type is not visible (`auto`, a template parameter): calls on it fall
# back to the one loaded method of the name. Never a valid class name.
_OPAQUE = "?"


@dataclass(frozen=True)
class _Scope:
    """Where a walk is: the enclosing namespaces and class, as written."""

    namespace: tuple[str, ...] = ()
    cls: str | None = None  # the qualified class whose body this is
    local: bool = False  # inside an anonymous namespace
    template_params: frozenset[str] = frozenset()


@dataclass
class _CppFile:
    rel_path: str  # forward slashes
    module: str  # the path without its extension
    source: bytes
    lines: list[str]
    include_paths: list[str] = field(default_factory=list)  # the quoted paths as written
    # each `using` as (the namespace it sits in, the path written, whether it is a `using namespace`)
    using: list[tuple[tuple[str, ...], tuple[str, ...], bool]] = field(default_factory=list)
    using_namespaces: list[str] = field(default_factory=list)  # qualified, from `using namespace ns;`
    using_names: dict[str, list[str]] = field(default_factory=dict)  # name -> qualified names, from `using ns::f;`


@dataclass
class _Class:
    # each base as (its name as written, the scopes it is looked up from, the file)
    bases: list[tuple[tuple[str, ...], tuple[str, ...], str]] = field(default_factory=list)
    # data member -> (its type's class name as parts, None for a type naming no class, or _OPAQUE; lookup scopes; file)
    fields: dict[str, tuple[tuple[str, ...] | str | None, tuple[str, ...], str]] = field(default_factory=dict)
    chain: list[str] | None = None  # the class, then its loaded bases depth first; filled on first use


@dataclass
class _Decl:
    """A function definition or declaration, as read in pass 1."""

    file: _CppFile
    scope: _Scope
    written: tuple[str, ...]  # the qualifier before the name: ("Catalog",) for `Catalog::add`
    name: str  # `add`, `Catalog`, `~Catalog`, `operator+=`, `operator bool`
    friend: bool
    params: list[dict]
    signature: str  # `(const Item &, int) const`: the parameter types and trailing qualifiers, normalised
    min_args: int
    max_args: float
    # set once every file is read
    owner: str = ""  # the qualified class or namespace it belongs to; "" is the global namespace
    is_method: bool = False
    lookup: tuple[str, ...] = ()  # the scopes an unqualified name is looked up in, innermost first, ending with ""
    # definitions only
    node: Node | None = None  # the function_definition
    span: Node | None = None  # the outermost node the definition is written as (`template <...>`, `friend`)
    local: bool = False  # visible only to its own file and its includers
    # declarations only
    doc: str | None = None

    @property
    def qualname(self) -> str:
        return _join(self.owner, self.name)


def _join(*parts: str) -> str:
    return "::".join(part for part in parts if part)


def _scopes_of(qualified: str) -> tuple[str, ...]:
    """A scope and every scope enclosing it, innermost first: `a::B` -> ("a::B", "a", "")."""
    parts = qualified.split("::") if qualified else []
    return tuple("::".join(parts[:i]) for i in range(len(parts), -1, -1))


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _operator_name(text: str) -> str:
    """`operator +=` -> `operator+=`, `operator  new [ ]` -> `operator new[]`."""
    rest = _collapse(text[len("operator") :])
    if rest[:1].isalpha() or rest[:1] == "_":
        return "operator " + re.sub(r"\s+(?=[\[\]])", "", rest)
    return "operator" + rest.replace(" ", "")


def _normalise_type(text: str) -> str:
    """One spelling per type: `const Foo&` and `const Foo &` -> `const Foo &`, `std::map< int,Foo* >` ->
    `std::map<int, Foo *>`."""
    text = re.sub(r"\s*([*&]+)\s*", r" \1 ", text)
    text = re.sub(r"\s*([<>()\[\]])\s*", r"\1", text)
    text = re.sub(r"\s*,\s*", ", ", text)
    return _collapse(text)


def _name_parts(node: Node | None, source: bytes) -> tuple[tuple[str, ...], str] | None:
    """A declarator's or a call's name as (qualifier as written, name), template arguments dropped:
    `Box<T>::get` -> (("Box",), "get"), `twice<int>` -> ((), "twice"). A leading `::` is an empty first part.
    """
    if node is None:
        return None
    kind = node.type
    if kind in ("identifier", "field_identifier", "type_identifier", "namespace_identifier"):
        return (), node_text(node, source)
    if kind == "destructor_name":
        inner = node.named_children[0] if node.named_children else None
        return ((), "~" + node_text(inner, source)) if inner is not None else None
    if kind == "operator_name":
        return (), _operator_name(node_text(node, source))
    if kind == "operator_cast":
        return (), "operator " + _collapse(node_text(node.child_by_field_name("type"), source))
    if kind in ("template_function", "template_method", "template_type"):
        return _name_parts(node.child_by_field_name("name"), source)
    if kind in ("dependent_name", "parenthesized_declarator"):  # `obj.template m<T>()`, `int (f)(int)`
        return _name_parts(node.named_children[0] if node.named_children else None, source)
    if kind == "qualified_identifier":  # `a::b::c` nests to the right: (a, (b, c))
        written: list[str] = []
        while node is not None and node.type == "qualified_identifier":
            scope = node.child_by_field_name("scope")
            outer = _name_parts(scope, source) if scope is not None else ((), "")
            if outer is None:
                return None
            written.extend((*outer[0], outer[1]))
            node = node.child_by_field_name("name")
        rest = _name_parts(node, source)
        return ((*written, *rest[0]), rest[1]) if rest is not None else None
    return None


def _function_parts(declarator: Node | None) -> tuple[Node, Node | None] | None:
    """A function declarator's name node and the node holding its parameters, or None for anything else
    (a variable, a function pointer). Conversion operators (`operator bool() const`) have their own shape.
    """
    while declarator is not None:
        kind = declarator.type
        if kind == "function_declarator":
            name = declarator.child_by_field_name("declarator")
            return (name, declarator) if name is not None else None
        if kind == "operator_cast":
            return declarator, declarator.child_by_field_name("declarator")
        if kind == "qualified_identifier":  # `Foo::operator bool() const`
            last = declarator
            while last is not None and last.type == "qualified_identifier":
                last = last.child_by_field_name("name")
            if last is not None and last.type == "operator_cast":
                return declarator, last.child_by_field_name("declarator")
            return None
        if kind not in _DEFINITION_WRAPPERS:
            return None
        declarator = declarator.child_by_field_name("declarator") or next(
            (c for c in declarator.named_children if c.type in _DECLARATOR_TYPES), None
        )
    return None


def _signature(params: list[dict], holder: Node | None, source: bytes) -> str:
    types = [_normalise_type(p["annotation"] or p["name"]) for p in params]
    trailing = holder.children if holder is not None else []
    qualifiers = [node_text(c, source) for c in trailing if c.type in ("type_qualifier", "ref_qualifier")]
    suffix = " " + " ".join(qualifiers) if qualifiers else ""
    return f"({', '.join(types)}){suffix}"


def _arity(params: list[dict]) -> tuple[int, float]:
    """How many arguments a call can pass: (required, most), `...` and parameter packs taking any number."""
    required = total = 0
    variadic = False
    for p in params:
        if p["name"] == "..." or (p["annotation"] or p["name"]).endswith("..."):
            variadic = True
            continue
        total += 1
        if p["default"] is None:
            required += 1
    return required, math.inf if variadic else total


def _arg_count(args: Node | None) -> int:
    return sum(1 for c in args.named_children if c.type != "comment") if args is not None else 0


def _return_type(fn_node: Node, holder: Node | None, source: bytes) -> str | None:
    """A definition's return type as written: `const`, the type, then its `*` and `&`; a trailing `-> T` wins."""
    trailing = next((c for c in holder.children if c.type == "trailing_return_type"), None) if holder else None
    if trailing is not None:
        return _collapse(node_text(trailing, source).removeprefix("->")) or None
    type_node = fn_node.child_by_field_name("type")
    if type_node is None:
        return None  # constructors, destructors, conversion operators
    qualifiers = [node_text(c, source) for c in fn_node.children if c.type == "type_qualifier"]
    text = _collapse(" ".join([*(q for q in qualifiers if q in ("const", "volatile")), node_text(type_node, source)]))
    marks = ""
    declarator = fn_node.child_by_field_name("declarator")
    while declarator is not None and declarator.type in ("pointer_declarator", "reference_declarator"):
        marks += "*" if declarator.type == "pointer_declarator" else node_text(declarator.children[0], source)
        declarator = declarator.child_by_field_name("declarator") or next(
            (c for c in declarator.named_children if c.type in _DECLARATOR_TYPES), None
        )
    return f"{text} {marks}" if marks else text


def _type_name(node: Node | None, source: bytes) -> tuple[tuple[str, ...], bool] | None:
    """The class name a type is written with, as parts, and whether a smart pointer was looked through:
    `const ns::Foo` -> (("ns", "Foo"), False), `std::unique_ptr<Foo>` -> (("Foo",), True). None for a type
    that cannot name a class (`int`, a function type).
    """
    wrapped = False
    while node is not None:
        kind = node.type
        if kind in _CLASS_TYPES:  # `struct Foo *p`
            node = node.child_by_field_name("name")
            continue
        template = node
        if kind == "qualified_identifier":  # `std::unique_ptr<Foo>`
            last = node.child_by_field_name("name")
            template = last if last is not None and last.type == "template_type" else node
        if template.type == "template_type" and node_text(template.child_by_field_name("name"), source) in _SMART_POINTERS:
            args = template.child_by_field_name("arguments")
            first = args.named_children[0] if args is not None and args.named_children else None
            node = first.child_by_field_name("type") if first is not None else None
            wrapped = True
            continue
        if kind in ("type_identifier", "template_type", "qualified_identifier"):
            parts = _name_parts(node, source)
            return (tuple(p for p in (*parts[0], parts[1]) if p), wrapped) if parts is not None else None
        return None
    return None


def _template_params(node: Node, source: bytes) -> frozenset[str]:
    """The type parameters a `template <...>` introduces."""
    params = node.child_by_field_name("parameters")
    names = set()
    for param in params.named_children if params is not None else []:
        if param.type not in _TYPE_PARAMETER_TYPES:
            continue
        name = param.child_by_field_name("name") or next((c for c in param.named_children if c.type == "type_identifier"), None)
        if name is not None:
            names.add(node_text(name, source))
    return frozenset(names)


def _typedef_name(declarator: Node | None, source: bytes) -> str | None:
    """The name a `typedef` introduces, through `*` and `&`: `typedef Foo *FooRef;` -> `FooRef`."""
    while declarator is not None and declarator.type in ("pointer_declarator", "reference_declarator"):
        declarator = declarator.child_by_field_name("declarator") or next(iter(declarator.named_children), None)
    return node_text(declarator, source) if declarator is not None and declarator.type == "type_identifier" else None


def _bare_name(param: Node, source: bytes) -> tuple[str, ...] | None:
    """`x` in `Foo var(x);`, which tree-sitter reads as a parameter of type `x` (the vexing parse)."""
    if param.type != "parameter_declaration" or param.child_by_field_name("declarator") is not None:
        return None
    type_node = param.child_by_field_name("type")
    if type_node is None or type_node.type not in ("type_identifier", "qualified_identifier") or len(param.named_children) != 1:
        return None
    parts = _name_parts(type_node, source)
    return tuple(p for p in (*parts[0], parts[1]) if p) if parts is not None else None


class _CppAnalyzer:
    def __init__(self, sources: dict[str, str]):
        self.sources = sources
        self.files: dict[str, _CppFile] = {}
        self.functions: dict[str, FunctionInfo] = {}
        self.file_errors: dict[str, str] = {}
        self.file_functions: dict[str, list[str]] = {}
        self._definitions: list[_Decl] = []  # in file order
        self._declarations: list[_Decl] = []
        self._classes: dict[str, _Class] = {}  # qualified name -> its bases and data members
        self._namespaces: set[str] = set()  # qualified names
        self._raw_aliases: list[tuple[str, tuple[tuple[str, ...], bool] | None, tuple[str, ...], _CppFile]] = []
        self._aliases: dict[str, tuple[str | None, bool]] = {}  # alias -> (the class it names, through a smart pointer?)
        self._by_qualname: dict[str, list[str]] = {}  # qualname (no overload suffix) -> function ids, every file
        self._methods_by_name: dict[str, list[str]] = {}  # bare method name -> function ids
        self._defs: dict[str, _Decl] = {}  # function id -> its definition
        self._declared: dict[str, list[_Decl]] = {}  # qualname -> its declarations
        self._reached: dict[str, list[str]] = {}
        self._reached_sets: dict[str, set[str]] = {}
        self._implementations: dict[str, set[str]] = {}  # file -> the source files implementing the headers it reaches
        self._resolved: dict[tuple, str | None] = {}

    def analyze(self) -> dict:
        self._parse_files()
        self._place_declarations()
        self._link_includes()
        self._register_functions()
        self._fill_missing_docstrings()
        self._resolve_calls()
        return self._to_response()

    # -- pass 1: parse everything, collect definitions, declarations, classes and namespaces --

    def _parse_files(self) -> None:
        parser = Parser(_CPP_LANGUAGE)
        for rel_path, raw in sorted(self.sources.items()):
            self.file_functions.setdefault(rel_path, [])
            source = prepare_source(raw)
            source_bytes = source.encode("utf-8")
            tree = parser.parse(source_bytes)
            if tree.root_node.has_error:
                self.file_errors[rel_path] = "SyntaxError: C++ source has one or more parse errors"

            module = posixpath.splitext(rel_path)[0]
            cfile = _CppFile(rel_path, module, source_bytes, split_lines(source))
            cfile.include_paths = quoted_includes(tree.root_node, source_bytes)
            self.files[rel_path] = cfile
            self._walk(cfile, tree.root_node)

    def _walk(self, cfile: _CppFile, root: Node) -> None:
        """Visits every item of a file, namespace and class bodies included, in source order. A stack stands in
        for recursion, so no depth of nesting can exhaust Python's."""
        # the root's children, whatever the root is: a badly broken file's root is an ERROR node
        pending: list[tuple[Node, _Scope, Node | None]] = [(child, _Scope(), None) for child in reversed(root.children)]
        while pending:
            node, scope, span = pending.pop()
            pending.extend(reversed(self._visit(cfile, node, scope, span or node)))

    def _visit(self, cfile: _CppFile, node: Node, scope: _Scope, span: Node) -> list[tuple[Node, _Scope, Node | None]]:
        """Registers what `node` declares, and returns the nodes inside it still to visit, in source order, each
        with its scope and, when it is wrapped (`template <...>`), the node its lines and comment start at."""
        kind = node.type
        source = cfile.source
        if kind in _CONTAINER_TYPES:
            return [(child, scope, None) for child in node.children]
        if kind == "function_definition":
            self._add_definition(cfile, node, span, scope, friend=False)
        elif kind == "template_declaration":
            inner = replace(scope, template_params=scope.template_params | _template_params(node, source))
            skipped = ("template_parameter_list", "requires_clause", "comment")
            return [(c, inner, span) for c in node.named_children if c.type not in skipped]
        elif kind == "namespace_definition":
            return self._add_namespace(cfile, node, scope)
        elif kind in _CLASS_TYPES:
            return self._add_class(cfile, node, scope, None)
        elif kind in ("declaration", "field_declaration"):
            type_node = node.child_by_field_name("type")
            self._add_declarations(cfile, node, span, scope, friend=False)
            if scope.cls is not None:
                self._add_fields(cfile, node, scope)
            if type_node is not None and type_node.type in _CLASS_TYPES:
                return self._add_class(cfile, type_node, scope, None)
        elif kind == "friend_declaration":
            for child in node.named_children:
                if child.type == "function_definition":
                    self._add_definition(cfile, child, span, scope, friend=True)
                elif child.type == "declaration":
                    self._add_declarations(cfile, child, span, scope, friend=True)
        elif kind == "type_definition":
            type_node = node.child_by_field_name("type")
            names = [_typedef_name(d, source) for d in node.children_by_field_name("declarator")]
            for name in names:
                if name:
                    self._add_alias(cfile, scope, name, type_node)
            if type_node is not None and type_node.type in _CLASS_TYPES:
                return self._add_class(cfile, type_node, scope, next((n for n in names if n), None))
        elif kind == "alias_declaration":
            name = node_text(node.child_by_field_name("name"), source)
            descriptor = node.child_by_field_name("type")
            if name and descriptor is not None:
                self._add_alias(cfile, scope, name, descriptor.child_by_field_name("type"))
        elif kind == "using_declaration" and scope.cls is None:  # in a class, `using Base::f;` adds nothing the bases don't
            target = next((c for c in node.named_children if c.type in ("identifier", "qualified_identifier")), None)
            parts = _name_parts(target, source)
            if parts is not None:
                path = tuple(p for p in (*parts[0], parts[1]) if p)
                is_namespace = any(c.type == "namespace" for c in node.children)
                cfile.using.append((scope.namespace, path, is_namespace))
        return []

    def _add_namespace(self, cfile: _CppFile, node: Node, scope: _Scope) -> list[tuple[Node, _Scope, Node | None]]:
        body = node.child_by_field_name("body")
        if body is None:
            return []
        name_node = node.child_by_field_name("name")
        if name_node is None:
            inner = replace(scope, local=True)  # an anonymous namespace: its names stay in this file
        else:
            if name_node.type == "nested_namespace_specifier":  # `namespace a::b {`
                parts = tuple(node_text(c, cfile.source) for c in name_node.named_children if c.type == "namespace_identifier")
            else:
                parts = (node_text(name_node, cfile.source),)
            namespace = scope.namespace + parts
            for i in range(1, len(namespace) + 1):
                self._namespaces.add("::".join(namespace[:i]))
            inner = replace(scope, namespace=namespace)
        return [(body, inner, None)]

    def _add_class(
        self, cfile: _CppFile, node: Node, scope: _Scope, fallback_name: str | None
    ) -> list[tuple[Node, _Scope, Node | None]]:
        body = node.child_by_field_name("body")
        if body is None:
            return []  # a forward declaration, or an elaborated type (`struct Foo *p`)
        name_node = node.child_by_field_name("name")
        parts = _name_parts(name_node, cfile.source) if name_node is not None else ((), fallback_name or "")
        if parts is None or not parts[1]:
            return []  # an anonymous class: its methods have no name to be called by
        enclosing = scope.cls or _join(*scope.namespace)
        qualname = _join(enclosing, *parts[0], parts[1])
        cls = self._classes.setdefault(qualname, _Class())
        lookup = _scopes_of(enclosing)
        for clause in node.children:
            if clause.type != "base_class_clause":
                continue
            for base in clause.named_children:
                base_parts = _name_parts(base, cfile.source) if base.type != "access_specifier" else None
                if base_parts is not None:
                    cls.bases.append((tuple(p for p in (*base_parts[0], base_parts[1]) if p), lookup, cfile.rel_path))
        return [(body, replace(scope, cls=qualname), None)]

    def _add_fields(self, cfile: _CppFile, node: Node, scope: _Scope) -> None:
        """Remembers the declared type of each data member, so `member_.m()` is typed like a local."""
        type_node = node.child_by_field_name("type")
        named = _type_name(type_node, cfile.source)
        if type_node is None or type_node.type in _OPAQUE_TYPES:
            parts: tuple[str, ...] | str | None = _OPAQUE
        elif named is None:
            parts = None
        else:
            parts = _OPAQUE if len(named[0]) == 1 and named[0][0] in scope.template_params else named[0]
        fields = self._classes[scope.cls].fields
        for declarator in node.children_by_field_name("declarator"):
            if _function_parts(declarator) is not None:
                continue
            name_node = declarator_name_node(declarator)
            if name_node is not None and name_node.type == "field_identifier":
                fields[node_text(name_node, cfile.source)] = (parts, _scopes_of(scope.cls), cfile.rel_path)

    def _add_alias(self, cfile: _CppFile, scope: _Scope, name: str, type_node: Node | None) -> None:
        enclosing = scope.cls or _join(*scope.namespace)
        self._raw_aliases.append((_join(enclosing, name), _type_name(type_node, cfile.source), _scopes_of(enclosing), cfile))

    def _read_declaration(self, cfile: _CppFile, declarator: Node | None, scope: _Scope, friend: bool) -> _Decl | None:
        parts = _function_parts(declarator)
        if parts is None:
            return None
        name_node, holder = parts
        named = _name_parts(name_node, cfile.source)
        if named is None or not named[1]:
            return None
        params = extract_params(holder, cfile.source)
        min_args, max_args = _arity(params)
        written = tuple(p for p in named[0] if p)
        signature = _signature(params, holder, cfile.source)
        return _Decl(cfile, scope, written, named[1], friend, params, signature, min_args, max_args)

    def _add_definition(self, cfile: _CppFile, fn_node: Node, span: Node, scope: _Scope, friend: bool) -> None:
        if fn_node.child_by_field_name("body") is None:
            return  # `= default`, `= delete`
        decl = self._read_declaration(cfile, fn_node.child_by_field_name("declarator"), scope, friend)
        if decl is None:
            return  # tree-sitter's recovery of something that is not a plain definition
        decl.node = fn_node
        decl.span = span
        decl.local = scope.local or (scope.cls is None and not decl.written and is_static(fn_node, cfile.source))
        self._definitions.append(decl)

    def _add_declarations(self, cfile: _CppFile, node: Node, span: Node, scope: _Scope, friend: bool) -> None:
        """Remembers each function a declaration declares: its comment documents a definition elsewhere, and its
        default arguments count when calls to that definition are matched by argument count."""
        doc = None
        for declarator in node.children_by_field_name("declarator"):
            decl = self._read_declaration(cfile, declarator, scope, friend)
            if decl is None:
                continue
            doc = doc or comment_block_above(span, cfile.source, cfile.lines)
            decl.doc = doc
            self._declarations.append(decl)

    # -- between the passes: owners, names and what each file can see --

    def _resolve_written(self, written: tuple[str, ...], namespace: tuple[str, ...]) -> str:
        """The class or namespace a qualifier written in `namespace` names: the innermost known match, else the
        qualifier taken as relative to `namespace`."""
        for i in range(len(namespace), -1, -1):
            candidate = "::".join(namespace[:i] + written)
            if candidate in self._classes or candidate in self._namespaces:
                return candidate
        return "::".join(namespace + written)

    def _place_declarations(self) -> None:
        for decl in [*self._definitions, *self._declarations]:
            scope = decl.scope
            if scope.cls is not None and not decl.friend:
                decl.owner, decl.is_method = scope.cls, True
            elif decl.written:  # `void Catalog::add(...)`, `void ns::helper()`
                decl.owner = self._resolve_written(decl.written, scope.namespace)
                decl.is_method = decl.owner not in self._namespaces
                if decl.is_method:
                    self._classes.setdefault(decl.owner, _Class())  # a class whose body was not loaded
            else:
                decl.owner, decl.is_method = _join(*scope.namespace), False
            # a friend defined in its class is a namespace function, but its body still sees the class
            decl.lookup = _scopes_of(scope.cls if decl.friend and scope.cls and not decl.written else decl.owner)

        for cfile in self.files.values():
            for namespace, path, is_namespace in cfile.using:
                if is_namespace:
                    target = self._resolve_written(path, namespace)
                    if target not in cfile.using_namespaces:
                        cfile.using_namespaces.append(target)
                elif len(path) > 1:
                    qualified = _join(self._resolve_written(path[:-1], namespace), path[-1])
                    cfile.using_names.setdefault(path[-1], []).append(qualified)

        for qualname, named, lookup, cfile in self._raw_aliases:  # in file order, so an alias can name an earlier one
            if named is None:
                self._aliases[qualname] = (None, False)
                continue
            parts, wrapped = named
            target, through_pointer = self._class_entry(parts, lookup, cfile) or (None, False)
            self._aliases[qualname] = (target, wrapped or through_pointer)

    def _link_includes(self) -> None:
        loaded = set(self.files)
        by_suffix = suffix_index(loaded)
        direct: dict[str, list[str]] = {}
        for rel_path, cfile in self.files.items():
            resolved = (resolve_include(rel_path, path, loaded, by_suffix) for path in cfile.include_paths)
            direct[rel_path] = [target for target in resolved if target is not None and target != rel_path]

        stems = {rel_path: posixpath.splitext(posixpath.basename(rel_path)) for rel_path in self.files}
        by_stem: dict[str, list[str]] = {}
        for rel_path, (stem, ext) in stems.items():
            if ext in _SOURCE_EXTENSIONS:
                by_stem.setdefault(stem, []).append(rel_path)
        implementing: dict[str, list[str]] = {}
        for rel_path, (stem, ext) in stems.items():
            if ext not in _HEADER_EXTENSIONS:
                continue
            candidates = by_stem.get(stem, [])
            beside = [c for c in candidates if posixpath.dirname(c) == posixpath.dirname(rel_path)]
            implementing[rel_path] = beside or candidates

        for rel_path in self.files:
            reached = reached_files(rel_path, direct)
            self._reached[rel_path] = reached
            self._reached_sets[rel_path] = set(reached)
            self._implementations[rel_path] = {
                impl for header in reached for impl in implementing.get(header, []) if impl != rel_path
            }

    def _register_functions(self) -> None:
        for decl in self._declarations:
            self._declared.setdefault(decl.qualname, []).append(decl)

        by_file: dict[str, list[_Decl]] = {}
        for decl in self._definitions:
            by_file.setdefault(decl.file.rel_path, []).append(decl)
        for rel_path, defs in by_file.items():
            signatures: dict[str, set[str]] = {}
            for decl in defs:
                signatures.setdefault(decl.qualname, set()).add(decl.signature)
            taken: dict[str, int] = {}
            for decl in defs:
                suffix = decl.signature if len(signatures[decl.qualname]) > 1 else ""
                func_id = f"{rel_path}::{decl.qualname}{suffix}"
                taken[func_id] = taken.get(func_id, 0) + 1
                if taken[func_id] > 1:
                    func_id += f"#{taken[func_id]}"
                for proto in self._declared.get(decl.qualname, []):
                    if proto.signature == decl.signature:
                        decl.min_args = min(decl.min_args, proto.min_args)
                self._add_function(func_id, decl, suffix)

    def _add_function(self, func_id: str, decl: _Decl, suffix: str) -> None:
        cfile = decl.file
        start_line = decl.span.start_point[0] + 1
        end_line = decl.node.end_point[0] + 1
        holder = _function_parts(decl.node.child_by_field_name("declarator"))[1]
        self.functions[func_id] = FunctionInfo(
            id=func_id,
            name=decl.name + suffix,
            qualname=decl.qualname + suffix,
            file=cfile.rel_path,
            module=cfile.module,
            class_name=decl.owner if decl.is_method else None,
            lineno=start_line,
            end_lineno=end_line,
            params=decl.params,
            returns=_return_type(decl.node, holder, cfile.source),
            docstring=comment_block_above(decl.span, cfile.source, cfile.lines),
            is_async=False,
            code_lines=make_code_lines(cfile.lines, start_line, end_line),
        )
        self.file_functions[cfile.rel_path].append(func_id)
        self._defs[func_id] = decl
        self._by_qualname.setdefault(decl.qualname, []).append(func_id)
        if decl.is_method:
            self._methods_by_name.setdefault(decl.name, []).append(func_id)

    def _fill_missing_docstrings(self) -> None:
        for func_id, info in self.functions.items():
            if info.docstring is not None:
                continue
            decl = self._defs[func_id]
            declared = self._declared.get(decl.qualname)
            if not declared:
                continue
            order = {rel_path: i for i, rel_path in enumerate([info.file, *self._reached[info.file]])}
            protos = [p for p in declared if p.file.rel_path in order]
            if not protos:
                continue
            protos.sort(key=lambda p: order[p.file.rel_path])
            exact = [p for p in protos if p.signature == decl.signature and p.doc]
            if exact:
                info.docstring = exact[0].doc
            elif len({p.signature for p in protos}) == 1:  # not overloaded: the declaration is this definition's
                info.docstring = next((p.doc for p in protos if p.doc), None)

    # -- pass 2: resolve calls --

    def _resolve_calls(self) -> None:
        for func_id, info in self.functions.items():
            decl = self._defs[func_id]
            fn_node = decl.node
            roots = [c for c in fn_node.children if c.type == "field_initializer_list"]
            body = fn_node.child_by_field_name("body")
            if body is not None:
                roots.append(body)
            variables, constructed = self._variables(decl, roots)
            found: list[tuple[int, str]] = []
            stack = list(reversed(roots))
            while stack:
                node = stack.pop()
                if node.type in _CALL_SITES:
                    found.extend(self._call_targets(decl, variables, constructed, node))
                stack.extend(reversed(node.children))  # so calls come out in source order
            dedupe_calls(info, found)
            decl.node = decl.span = None  # the trees are no longer needed

    def _variables(self, decl: _Decl, roots: list[Node]) -> tuple[dict[str, str | None], set[tuple[int, int]]]:
        """Every parameter and local variable of a function, lambdas included, with the class its type names
        (`_OPAQUE` when the type is not visible, None when it is not a loaded class), plus the `Foo var(x)`
        declarations that construct a variable rather than declare a function."""
        source = decl.file.source
        variables: dict[str, str | None] = {}

        def declare(name: str, value: str | None) -> None:
            variables[name] = _OPAQUE if name in variables and variables[name] != value else value

        def declare_param(param: Node) -> None:
            name_node = declarator_name_node(param.child_by_field_name("declarator"))
            if name_node is not None and name_node.type == "identifier":
                declare(node_text(name_node, source), self._type_of(decl, param.child_by_field_name("type")))

        holder = _function_parts(decl.node.child_by_field_name("declarator"))[1]
        params = holder.child_by_field_name("parameters") if holder is not None else None
        for param in params.named_children if params is not None else []:
            if param.type in PARAMETER_TYPES:
                declare_param(param)

        maybe_constructed: list[tuple[Node, Node | None]] = []
        stack = list(roots)
        while stack:
            node = stack.pop()
            kind = node.type
            if kind == "declaration":
                type_node = node.child_by_field_name("type")
                for declarator in node.children_by_field_name("declarator"):
                    if declarator.type == "function_declarator":
                        maybe_constructed.append((declarator, type_node))
                        continue
                    target = declarator
                    if declarator.type == "init_declarator":
                        target = declarator.child_by_field_name("declarator")
                    if target is not None and target.type == "structured_binding_declarator":  # `auto [a, b] = ...`
                        for name in target.named_children:
                            declare(node_text(name, source), _OPAQUE)
                        continue
                    if function_declarator_of(target) is not None:
                        continue  # a prototype inside the body names a function, not a variable
                    name_node = declarator_name_node(target)
                    if name_node is not None and name_node.type == "identifier":
                        declare(node_text(name_node, source), self._type_of(decl, type_node))
            elif kind in PARAMETER_TYPES:  # a lambda's or a catch clause's
                declare_param(node)
            elif kind == "for_range_loop":
                name_node = declarator_name_node(node.child_by_field_name("declarator"))
                if name_node is not None and name_node.type == "identifier":
                    declare(node_text(name_node, source), self._type_of(decl, node.child_by_field_name("type")))
            stack.extend(node.named_children)

        # `Foo var(x);` reads as a prototype of a function `var` taking an `x`. Inside a body it is far more
        # likely a variable constructed from `x`, and is taken as one unless some "parameter" names a class.
        constructed: set[tuple[int, int]] = set()
        for declarator, type_node in maybe_constructed:
            name_node = declarator.child_by_field_name("declarator")
            args = declarator.child_by_field_name("parameters")
            names = [_bare_name(c, source) for c in args.named_children if c.type != "comment"] if args is not None else []
            if name_node is None or name_node.type != "identifier" or not names:
                continue
            if not all(self._is_value(decl, variables, n) for n in names):
                continue
            declare(node_text(name_node, source), self._type_of(decl, type_node))
            constructed.add((declarator.start_byte, declarator.end_byte))
        return variables, constructed

    def _is_value(self, decl: _Decl, variables: dict[str, str | None], name: tuple[str, ...] | None) -> bool:
        """Whether a name in `Foo var(x)` can be a value: a variable, or any name that is not a type."""
        if name is None:
            return False
        if len(name) == 1 and name[0] in variables:
            return True
        if len(name) == 1 and name[0] in decl.scope.template_params:
            return False
        return self._find_class(name, decl, constructing=False) is None

    def _call_targets(
        self, decl: _Decl, variables: dict[str, str | None], constructed: set[tuple[int, int]], node: Node
    ) -> list[tuple[int, str]]:
        """The (line, callee id) of each call `node` makes, for the node kinds in `_CALL_SITES`."""
        kind = node.type
        line = node.start_point[0] + 1
        type_node = node.child_by_field_name("type")
        target = None
        if kind == "call_expression":
            argc = _arg_count(node.child_by_field_name("arguments"))
            target = self._resolve_call(decl, variables, node.child_by_field_name("function"), argc)
        elif kind == "new_expression":
            if node.child_by_field_name("declarator") is None:  # not `new T[n]`
                target = self._construct(decl, type_node, _arg_count(node.child_by_field_name("arguments")))
        elif kind == "compound_literal_expression":  # `T{...}`
            target = self._construct(decl, type_node, _arg_count(node.child_by_field_name("value")))
        elif kind == "field_initializer":  # `: Base(...)` in a constructor
            named = [c for c in node.named_children if c.type != "comment"]
            parts = _name_parts(named[0], decl.file.source) if len(named) == 2 else None
            if parts is not None:
                cls = self._find_class(tuple(p for p in (*parts[0], parts[1]) if p), decl, constructing=True)
                if cls is not None:
                    target = self._constructor(decl.file.rel_path, cls, _arg_count(named[1]))
        elif kind == "declaration":  # `T var(...)`, `T var{...}`
            found = []
            for declarator in node.children_by_field_name("declarator"):
                argc = None
                if declarator.type == "init_declarator":
                    inner = declarator.child_by_field_name("declarator")
                    value = declarator.child_by_field_name("value")
                    initialized = value is not None and value.type in ("argument_list", "initializer_list")
                    if inner is not None and inner.type == "identifier" and initialized:
                        argc = _arg_count(value)
                elif (declarator.start_byte, declarator.end_byte) in constructed:
                    argc = _arg_count(declarator.child_by_field_name("parameters"))
                if argc is not None:
                    found_target = self._construct(decl, type_node, argc)
                    if found_target:
                        found.append((declarator.start_point[0] + 1, found_target))
            return found
        return [(line, target)] if target else []

    def _resolve_call(self, decl: _Decl, variables: dict[str, str | None], func: Node | None, argc: int) -> str | None:
        if func is None:
            return None
        source = decl.file.source
        kind = func.type
        if kind in ("identifier", "template_function"):  # `f(x)`, `f<int>(x)`
            parts = _name_parts(func, source)
            if parts is None or parts[0] or parts[1] in variables:
                return None  # a call through a parameter or a local (a function pointer, a lambda) is not linked
            return self._resolve_unqualified(decl, parts[1], argc)
        if kind == "qualified_identifier":  # `Catalog::make()`, `ns::f()`, `::f()`
            parts = _name_parts(func, source)
            if parts is None:
                return None
            written, name = parts
            return self._resolve_qualified(decl, tuple(p for p in written if p), name, argc, written[:1] == ("",))
        if kind == "field_expression":  # `obj.m()`, `obj->m()`, `this->m()`
            parts = _name_parts(func.child_by_field_name("field"), source)
            if parts is None:
                return None
            written, name = parts
            written = tuple(p for p in written if p)
            if written:  # `obj.Base::m()`
                target = self._find_scope(written, decl)
                found = self._members(target, name) if target in self._classes else []
                return self._pick_member(decl.file.rel_path, found, argc)
            cls = self._receiver_class(decl, variables, func.child_by_field_name("argument"))
            return self._resolve_method(decl.file.rel_path, cls, name, argc)
        return None  # `(*fp)(x)`, `tbl[i](x)`, `make()(x)`: calls through an expression are not linked

    def _receiver_class(self, decl: _Decl, variables: dict[str, str | None], expr: Node | None) -> str | None:
        """The class of the object a method is called on: `this`, a typed variable, a typed data member reached
        through either (`this->items_`, `order.item`), or a cast's target type."""
        source = decl.file.source
        members: list[str] = []  # `a.b.c` -> ["c", "b"], read back from `a` outwards
        while expr is not None:
            if expr.type in ("parenthesized_expression", "pointer_expression"):  # `(*this)`, `*it`
                expr = expr.child_by_field_name("argument") or next(iter(expr.named_children), None)
            elif expr.type == "field_expression":
                member = expr.child_by_field_name("field")
                if member is None or member.type != "field_identifier":
                    return _OPAQUE
                members.append(node_text(member, source))
                expr = expr.child_by_field_name("argument")
            else:
                break
        cls = _OPAQUE
        if expr is None:
            return _OPAQUE
        if expr.type == "this":
            cls = decl.owner if decl.is_method else _OPAQUE
        elif expr.type == "identifier":
            name = node_text(expr, source)
            if name in variables:
                cls = variables[name]
            elif decl.is_method:
                cls = self._field_class(decl.owner, name)  # a data member, or a global (`_OPAQUE`)
        elif expr.type == "call_expression":  # `static_cast<Foo *>(p)->m()`
            func = expr.child_by_field_name("function")
            is_template = func is not None and func.type == "template_function"
            if is_template and node_text(func.child_by_field_name("name"), source) in _CASTS:
                args = func.child_by_field_name("arguments")
                first = args.named_children[0] if args is not None and args.named_children else None
                if first is not None and first.type == "type_descriptor":
                    cls = self._type_of(decl, first.child_by_field_name("type"))
        for name in reversed(members):
            if cls is None or cls == _OPAQUE:
                return _OPAQUE  # a member of something untyped, or of a type that was not loaded
            cls = self._field_class(cls, name)
        return cls

    # -- lookups, each cached --

    def _cached(self, key: tuple, compute) -> str | None:
        if key not in self._resolved:
            self._resolved[key] = compute()
        return self._resolved[key]

    def _resolve_unqualified(self, decl: _Decl, name: str, argc: int) -> str | None:
        if name in decl.scope.template_params:
            return None  # `T(x)`: a conversion to a template parameter's type
        key = ("unqualified", decl.file.rel_path, decl.lookup, name, argc)
        return self._cached(key, lambda: self._lookup_unqualified(decl, name, argc))

    def _lookup_unqualified(self, decl: _Decl, name: str, argc: int) -> str | None:
        cfile = decl.file
        rel_path = cfile.rel_path
        # (1) the enclosing classes and their bases, then the enclosing namespaces, innermost first
        for scope in decl.lookup[:-1]:
            if scope in self._classes:
                found = self._members(scope, name)
                if found:
                    return self._pick_member(rel_path, found, argc)
                if any(_join(owner, name) in self._declared for owner in self._chain(scope)):
                    return self._lookup_method(rel_path, _OPAQUE, name, argc)  # a pure virtual: as for `this->m()`
            else:
                found = self._visible(rel_path, self._by_qualname.get(f"{scope}::{name}", []))
                if found:
                    return self._pick(found, argc)
        # (2) what `using namespace ns;` and `using ns::f;` bring in
        candidates = [fid for ns in cfile.using_namespaces for fid in self._by_qualname.get(_join(ns, name), [])]
        candidates += [fid for qualified in cfile.using_names.get(name, []) for fid in self._by_qualname.get(qualified, [])]
        found = self._visible(rel_path, list(dict.fromkeys(candidates)))
        if found:
            return self._pick(found, argc)
        # (3) the global namespace, the way C sees it
        found = self._visible(rel_path, self._by_qualname.get(name, []))
        if found:
            return self._pick(found, argc)
        # (4) `Foo(x)`: a class's constructor
        cls = self._find_class((name,), decl, constructing=True)
        return self._constructor(rel_path, cls, argc) if cls is not None else None

    def _resolve_qualified(self, decl: _Decl, written: tuple[str, ...], name: str, argc: int, is_global: bool) -> str | None:
        rel_path = decl.file.rel_path
        key = ("qualified", rel_path, decl.lookup, written, name, argc, is_global)
        return self._cached(key, lambda: self._lookup_qualified(decl, written, name, argc, is_global))

    def _lookup_qualified(self, decl: _Decl, written: tuple[str, ...], name: str, argc: int, is_global: bool) -> str | None:
        rel_path = decl.file.rel_path
        if not written:  # `::f()`
            found = self._visible(rel_path, self._by_qualname.get(name, []))
            return self._pick(found, argc) if found else None
        target = self._find_scope(written, decl, ("",) if is_global else None)
        if target is None:
            return None  # `std::sort()`: a scope that was not loaded
        if target in self._classes:
            found = self._members(target, name)
            if found:
                return self._pick_member(rel_path, found, argc)
        else:
            found = self._visible(rel_path, self._by_qualname.get(f"{target}::{name}", []))
            if found:
                return self._pick(found, argc)
        nested = _join(target, name)  # `ns::Foo(x)`: a constructor
        return self._constructor(rel_path, nested, argc) if nested in self._classes else None

    def _resolve_method(self, rel_path: str, cls: str | None, name: str, argc: int) -> str | None:
        """A call on a receiver of class `cls` (`_OPAQUE` when its type is not visible)."""
        if cls is None:
            return None  # a visible type that is not a loaded class: `std::string`, `int`
        return self._cached(("method", rel_path, cls, name, argc), lambda: self._lookup_method(rel_path, cls, name, argc))

    def _lookup_method(self, rel_path: str, cls: str, name: str, argc: int) -> str | None:
        found = self._members(cls, name) if cls != _OPAQUE else []
        if found:
            return self._pick_member(rel_path, found, argc)
        # the type is not visible, or its classes define no such method: the one loaded method of that name
        methods = self._methods_by_name.get(name, [])
        return methods[0] if len(methods) == 1 else None

    def _construct(self, decl: _Decl, type_node: Node | None, argc: int) -> str | None:
        cls = self._type_of(decl, type_node, constructing=True)
        if cls is None or cls == _OPAQUE:
            return None
        return self._constructor(decl.file.rel_path, cls, argc)

    def _constructor(self, rel_path: str, cls: str, argc: int) -> str | None:
        found = self._by_qualname.get(_join(cls, cls.rsplit("::", 1)[-1]), [])
        return self._pick_member(rel_path, found, argc)

    def _type_of(self, decl: _Decl, type_node: Node | None, constructing: bool = False) -> str | None:
        """The loaded class a declared type names, `_OPAQUE` when the type is not visible, None otherwise.

        A smart pointer (`std::unique_ptr<Foo>`) names its pointee, except when the question is which
        constructor a declaration calls (`constructing`).
        """
        if type_node is None or type_node.type in _OPAQUE_TYPES:
            return _OPAQUE
        named = _type_name(type_node, decl.file.source)
        if named is None:
            return None
        parts, wrapped = named
        if wrapped and constructing:
            return None
        if len(parts) == 1 and parts[0] in decl.scope.template_params:
            return _OPAQUE
        return self._find_class(parts, decl, constructing)

    def _find_class(self, parts: tuple[str, ...], decl: _Decl, constructing: bool) -> str | None:
        key = ("class", decl.file.rel_path, decl.lookup, parts, constructing)
        return self._cached(key, lambda: self._lookup_class(parts, decl.lookup, decl.file, constructing))

    def _lookup_class(self, parts: tuple[str, ...], lookup: tuple[str, ...], cfile: _CppFile, constructing: bool) -> str | None:
        target, wrapped = self._class_entry(parts, lookup, cfile) or (None, False)
        return None if wrapped and constructing else target

    def _class_entry(self, parts: tuple[str, ...], lookup: tuple[str, ...], cfile: _CppFile) -> tuple[str | None, bool] | None:
        """The class a type name finds from `lookup` outwards, and whether it went through a smart-pointer alias;
        None when the name finds nothing."""
        for scope in (*lookup, *cfile.using_namespaces):
            candidate = _join(scope, *parts)
            if candidate in self._classes:
                return candidate, False
            if candidate in self._aliases:
                return self._aliases[candidate]
        return None

    def _field_class(self, cls: str, name: str) -> str | None:
        """What a data member's declared type names, like `_type_of`; `_OPAQUE` for a member that was not loaded."""
        return self._cached(("field", cls, name), lambda: self._lookup_field(cls, name))

    def _lookup_field(self, cls: str, name: str) -> str | None:
        for owner in self._chain(cls):
            info = self._classes.get(owner)
            if info is not None and name in info.fields:
                parts, lookup, rel_path = info.fields[name]
                if parts is None or parts == _OPAQUE:
                    return parts
                return self._lookup_class(parts, lookup, self.files[rel_path], constructing=False)
        return _OPAQUE

    def _find_scope(self, written: tuple[str, ...], decl: _Decl, lookup: tuple[str, ...] | None = None) -> str | None:
        """The class or namespace a qualifier names, looked up from the enclosing scopes outwards."""
        for scope in (*(decl.lookup if lookup is None else lookup), *decl.file.using_namespaces):
            candidate = _join(scope, *written)
            if candidate in self._classes or candidate in self._namespaces:
                return candidate
            target, wrapped = self._aliases.get(candidate, (None, True))
            if target is not None and not wrapped:
                return target
        return None

    def _chain(self, cls: str) -> list[str]:
        """A class and its loaded base classes, depth first, in declaration order."""
        info = self._classes.get(cls)
        if info is None:
            return [cls]
        if info.chain is None:
            chain: list[str] = []
            seen: set[str] = set()
            pending = [cls]
            while pending:
                current = pending.pop()
                if current in seen:
                    continue  # a diamond, or an inheritance cycle in broken code
                seen.add(current)
                chain.append(current)
                current_info = self._classes.get(current)
                bases = [
                    self._lookup_class(parts, lookup, self.files[rel_path], constructing=False)
                    for parts, lookup, rel_path in (current_info.bases if current_info else [])
                ]
                pending.extend(reversed([base for base in bases if base is not None]))
            info.chain = chain
        return info.chain

    def _members(self, cls: str, name: str) -> list[str]:
        for owner in self._chain(cls):
            found = self._by_qualname.get(f"{owner}::{name}")
            if found:
                return found
        return []

    # -- choosing among candidates --

    def _visible(self, rel_path: str, candidates: list[str]) -> list[str]:
        """The candidates a file sees first, the way C resolves a name: the same file, then the files it reaches
        through includes and the files implementing those, then every definition visible from anywhere."""
        if not candidates:
            return []
        same = [fid for fid in candidates if self._defs[fid].file.rel_path == rel_path]
        if same:
            return same
        reached = self._reached_sets[rel_path]
        implementations = self._implementations[rel_path]
        near = []
        for fid in candidates:
            decl = self._defs[fid]
            if decl.file.rel_path in reached or (decl.file.rel_path in implementations and not decl.local):
                near.append(fid)
        if near:
            return near
        return [fid for fid in candidates if not self._defs[fid].local]

    def _pick_member(self, rel_path: str, candidates: list[str], argc: int) -> str | None:
        """One of a class's definitions of a name, as `_pick` chooses, among those in this file, the files it
        reaches and the files implementing those when there are any (so two unrelated classes of one name, each
        in its own anonymous namespace, stay apart), else among all those visible from anywhere."""
        reached = self._reached_sets[rel_path]
        implementations = self._implementations[rel_path]
        near = []
        for fid in candidates:
            path = self._defs[fid].file.rel_path
            if path == rel_path or path in reached or path in implementations:
                near.append(fid)
        return self._pick(near or [fid for fid in candidates if not self._defs[fid].local], argc)

    def _pick(self, candidates: list[str], argc: int) -> str | None:
        """The one candidate, or the one the argument count leaves; None when that is not exactly one."""
        if len(candidates) == 1:
            return candidates[0]
        fitting = [fid for fid in candidates if self._defs[fid].min_args <= argc <= self._defs[fid].max_args]
        return fitting[0] if len(fitting) == 1 else None

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


class CppLanguage(LanguagePlugin):
    id = "cpp"
    label = "C++"
    extensions = frozenset({".cc", ".cpp", ".cxx", ".hpp", ".hh", ".hxx"})
    conditional_extensions = frozenset({".h"})

    def claims(self, ext: str, paths: Collection[str]) -> bool:
        """`.h` is C++ in a load holding any C++-only file, and stays C in a C-only one."""
        return ext in self.conditional_extensions and any(os.path.splitext(p)[1] in self.extensions for p in paths)

    def analyze(self, sources: dict[str, str]) -> dict:
        return _CppAnalyzer(sources).analyze()
