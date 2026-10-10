"""tree-sitter helpers shared by the C and C++ language plugins.

C and C++ are separate plugins (separate Languages, badges and highlighting),
but they read the same declarator syntax, the same comment conventions and
the same `#include` directives. Everything here works on a plain tree-sitter
tree and knows nothing about either plugin's resolution rules, so the C++
plugin can reuse it unchanged.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Iterator

from tree_sitter import Node

# Nodes a top-level walk looks through to reach more top-level items: every
# preprocessor conditional branch is read (nothing is preprocessed), and
# `extern "C" { ... }` is just a wrapper around declarations. A plugin that
# also reads namespaces extends this set.
TRANSPARENT_TYPES = frozenset(
    {
        "preproc_if",
        "preproc_ifdef",
        "preproc_else",
        "preproc_elif",
        "preproc_elifdef",
        "linkage_specification",
        "declaration_list",
    }
)

# A declarator that names the thing declared (as opposed to wrapping another declarator).
_NAME_TYPES = frozenset({"identifier", "field_identifier", "qualified_identifier", "destructor_name", "operator_name"})
_WRAPPER_TYPES = frozenset(
    {
        "pointer_declarator",
        "reference_declarator",
        "array_declarator",
        "function_declarator",
        "parenthesized_declarator",
        "attributed_declarator",
        "init_declarator",
    }
)


def node_text(node: Node | None, source: bytes) -> str:
    if node is None:
        return ""
    return source[node.start_byte : node.end_byte].decode("utf-8", "replace")


def prepare_source(source: str) -> str:
    """Drops a leading byte-order mark, which would otherwise be parsed as part of the first token, and
    ends the file with a newline, without which a last-line `#include` is reported as a parse error.
    """
    source = source[1:] if source.startswith("\ufeff") else source
    return source if source.endswith("\n") else source + "\n"


def split_lines(source: str) -> list[str]:
    """Lines indexed the way tree-sitter rows are: split on "\\n" only.

    `str.splitlines()` would also split on form feeds (common in GNU C
    sources) and shift every later row off by one.
    """
    return [line[:-1] if line.endswith("\r") else line for line in source.split("\n")]


def walk_top_level(node: Node, transparent: frozenset[str] = TRANSPARENT_TYPES) -> Iterator[Node]:
    """Every top-level item under `node`, looking through preprocessor branches and linkage blocks."""
    for child in node.children:
        if child.type in transparent:
            yield from walk_top_level(child, transparent)
        else:
            yield child


# -- declarators --


def declarator_name_node(declarator: Node | None) -> Node | None:
    """The identifier a declarator declares: `*dup(...)` -> `dup`, `(*cb)(int)` -> `cb`, `a[10]` -> `a`."""
    while declarator is not None:
        if declarator.type in _NAME_TYPES:
            return declarator
        if declarator.type not in _WRAPPER_TYPES:
            return None
        inner = declarator.child_by_field_name("declarator")
        if inner is None:
            # attributed_declarator and parenthesized_declarator carry the inner one as an unnamed child
            inner = next((c for c in declarator.named_children if c.type in _NAME_TYPES or c.type in _WRAPPER_TYPES), None)
        declarator = inner
    return None


def declarator_name(declarator: Node | None, source: bytes) -> str | None:
    return node_text(declarator_name_node(declarator), source) or None


def function_declarator_of(declarator: Node | None) -> Node | None:
    """The `function_declarator` in a definition's declarator chain (`*dup(...)` wraps one in a pointer)."""
    while declarator is not None:
        if declarator.type == "function_declarator":
            return declarator
        if declarator.type not in _WRAPPER_TYPES:
            return None
        declarator = declarator.child_by_field_name("declarator")
    return None


def declares_function(declarator: Node | None) -> bool:
    """Whether a declarator declares a function (`int foo(int)`) rather than a variable that holds one
    (`int (*foo)(int)`): the function declarator sits directly on the name, not on a parenthesized pointer.
    """
    fn_declarator = function_declarator_of(declarator)
    if fn_declarator is None:
        return False
    inner = fn_declarator.child_by_field_name("declarator")
    return inner is not None and inner.type in _NAME_TYPES


def pointer_depth(declarator: Node | None) -> int:
    """How many `*` wrap the function declarator in a definition's declarator (`char *f()` -> 1)."""
    depth = 0
    while declarator is not None and declarator.type != "function_declarator":
        if declarator.type == "pointer_declarator":
            depth += 1
        elif declarator.type not in _WRAPPER_TYPES:
            break
        declarator = declarator.child_by_field_name("declarator")
    return depth


# -- signatures --


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def extract_params(fn_declarator: Node | None, source: bytes) -> list[dict]:
    """A function declarator's parameters as {"name", "annotation", "default"}, with the C type as the annotation.

    `(void)` is no parameters; `...` is a parameter named `...`; an old-style `f(a, b)` name list is kept as names.
    """
    params_node = fn_declarator.child_by_field_name("parameters") if fn_declarator is not None else None
    params: list[dict] = []
    if params_node is None:
        return params
    for child in params_node.named_children:
        if child.type == "parameter_declaration":
            name_node = declarator_name_node(child.child_by_field_name("declarator"))
            if name_node is None:
                text = _collapse(node_text(child, source))
                if text == "void" and len(params_node.named_children) == 1:
                    continue
                params.append({"name": text, "annotation": None, "default": None})
                continue
            # the type is everything in the parameter except the name itself
            start = name_node.start_byte - child.start_byte
            end = name_node.end_byte - child.start_byte
            raw = source[child.start_byte : child.end_byte]
            annotation = _collapse((raw[:start] + raw[end:]).decode("utf-8", "replace"))
            params.append({"name": node_text(name_node, source), "annotation": annotation or None, "default": None})
        elif child.type == "variadic_parameter":
            params.append({"name": "...", "annotation": None, "default": None})
        elif child.type == "identifier":
            params.append({"name": node_text(child, source), "annotation": None, "default": None})
    return params


def param_names(fn_declarator: Node | None, source: bytes) -> set[str]:
    """The names a function's parameters bind (for telling an indirect call from a direct one)."""
    return {p["name"] for p in extract_params(fn_declarator, source) if p["name"] != "..."}


def return_type(fn_node: Node, source: bytes) -> str | None:
    """A definition's return type as written: qualifiers, the type, then one `*` per pointer level."""
    type_node = fn_node.child_by_field_name("type")
    if type_node is None:
        return None
    qualifiers = [node_text(c, source) for c in fn_node.children if c.type == "type_qualifier"]
    text = _collapse(" ".join([*qualifiers, node_text(type_node, source)]))
    stars = pointer_depth(fn_node.child_by_field_name("declarator"))
    return f"{text} {'*' * stars}" if stars else text


def is_static(fn_node: Node, source: bytes) -> bool:
    return any(c.type == "storage_class_specifier" and node_text(c, source) == "static" for c in fn_node.children)


def local_names(body: Node, source: bytes) -> set[str]:
    """Every variable a function body declares, in any block: `int n`, `void (*cb)(int) = f`, `for (int i...)`.

    A prototype declared inside the body (`int helper(int);`) names a function, not a variable, so it is left out.
    """
    names: set[str] = set()
    stack = [body]
    while stack:
        node = stack.pop()
        if node.type == "declaration":
            for declarator in node.children_by_field_name("declarator"):
                inner = declarator.child_by_field_name("declarator") if declarator.type == "init_declarator" else declarator
                if inner is None or declares_function(inner):
                    continue
                name = declarator_name(inner, source)
                if name:
                    names.add(name)
        stack.extend(node.children)
    return names


# -- comments --


def _strip_line_comment(text: str) -> str:
    text = text.strip()
    for marker in ("///", "//!", "//"):
        if text.startswith(marker):
            return text[len(marker) :].strip()
    return text


def _strip_block_comment(text: str) -> str:
    text = text.strip()
    if text.startswith("/*"):
        text = text[2:]
    if text.endswith("*/"):
        text = text[:-2]
    if text[:1] in ("*", "!"):  # the second marker character of /** and /*!
        text = text[1:]
    lines = []
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("*"):  # the decoration column of a block comment
            line = line[1:]
        lines.append(line.strip())
    return "\n".join(lines).strip()


def strip_comment_markers(text: str) -> str:
    """A comment's text with `//`, `///`, `/*`, `/**`, `*/` and a block's leading `*` column removed."""
    return _strip_block_comment(text) if text.lstrip().startswith("/*") else _strip_line_comment(text)


def comment_block_above(node: Node, source: bytes, source_lines: list[str]) -> str | None:
    """The contiguous block of own-line comments directly above `node`, markers stripped, or None.

    "Directly above" means no blank line, preprocessor line or code between the comment and the node; "own-line"
    means nothing else shares the comment's first line, so `int x; // note` never documents what follows it.
    """
    comments: list[Node] = []
    expected_row = node.start_point[0] - 1
    sibling = node.prev_sibling
    while sibling is not None and sibling.type == "comment":
        row = sibling.start_point[0]
        own_line = source_lines[row][: sibling.start_point[1]].strip() == ""
        if sibling.end_point[0] != expected_row or not own_line:
            break
        comments.append(sibling)
        expected_row = row - 1
        sibling = sibling.prev_sibling
    if not comments:
        return None
    comments.reverse()
    text = "\n".join(strip_comment_markers(node_text(c, source)) for c in comments).strip()
    return text or None


# -- #include --


def quoted_includes(root: Node, source: bytes) -> list[str]:
    """The paths of every `#include "..."` in a file, in order; `<system.h>` and macro includes are skipped."""
    paths: list[str] = []
    for node in walk_top_level(root):
        if node.type != "preproc_include":
            continue
        path = node.child_by_field_name("path")
        if path is not None and path.type == "string_literal":
            paths.append(node_text(path, source).strip('"').replace("\\", "/"))
    return paths


def resolve_include(includer: str, include: str, loaded: set[str], by_suffix: dict[str, list[str]]) -> str | None:
    """Maps a quoted include to a loaded file, or None.

    The path is tried against the including file's directory first, as a compiler does, then against the
    picked root; failing that, a path suffix that exactly one loaded file ends with stands in for the `-I`
    directories (which are not read).
    """
    if include.startswith("/"):
        return None
    relative = posixpath.normpath(posixpath.join(posixpath.dirname(includer), include))
    if relative in loaded:
        return relative
    normalized = posixpath.normpath(include)
    if normalized in loaded:
        return normalized
    matches = by_suffix.get(normalized, [])
    return matches[0] if len(matches) == 1 else None


def suffix_index(paths: set[str]) -> dict[str, list[str]]:
    """Every trailing path of every file ("a/b/c.h" -> "c.h", "b/c.h", "a/b/c.h") -> the files that end that way."""
    index: dict[str, list[str]] = {}
    for path in paths:
        parts = path.split("/")
        for i in range(len(parts)):
            index.setdefault("/".join(parts[i:]), []).append(path)
    return index


def reached_files(start: str, direct_includes: dict[str, list[str]]) -> list[str]:
    """Every file reachable from `start` through quoted includes, transitively, nearest first.

    `start` itself is left out, even when an include cycle leads back to it.
    """
    seen = {start}
    order: list[str] = []
    queue = [start]
    while queue:
        current = queue.pop(0)
        for included in direct_includes.get(current, []):
            if included not in seen:
                seen.add(included)
                order.append(included)
                queue.append(included)
    return order


# -- code body --


def make_code_lines(source_lines: list[str], start_line: int, end_line: int) -> list[dict]:
    """A function's source lines (1-based, inclusive) as card lines, de-indented by the first line's indent."""
    raw = source_lines[start_line - 1 : end_line]
    indent = len(raw[0]) - len(raw[0].lstrip()) if raw else 0
    code_lines = []
    for i, line in enumerate(raw):
        text = line[indent:] if line[:indent].strip() == "" else line
        code_lines.append({"lineno": start_line + i, "text": text, "calls": []})
    return code_lines
