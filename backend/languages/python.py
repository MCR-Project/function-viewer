"""Python language plugin: AST-based analysis of Python source trees.

Walks each file, extracts every top-level function and class method, and
resolves which loaded functions each code line calls. Only calls to
functions defined within the loaded files are reported; stdlib and
third-party calls are ignored.

A picked folder isn't always itself the import root: a monorepo commonly
has a nested "real" root (e.g. backend/, with services/, config.py etc.
directly inside it, everything importing absolute-style as if backend/ is
on sys.path). _infer_roots() detects that per directory, the same idea as
the Rust plugin's crate-root detection, just inferred from imports instead
of a fixed directory-name convention.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

from .base import FunctionInfo, LanguagePlugin


@dataclass
class ModuleInfo:
    rel_path: str  # forward slashes
    module: str  # dotted module name relative to root
    # local name -> function id, for top-level functions of this module
    top_level: dict[str, str] = field(default_factory=dict)
    # class name -> {method name -> function id}
    classes: dict[str, dict[str, str]] = field(default_factory=dict)
    # import alias -> dotted module name (import x, import x.y as z)
    module_aliases: dict[str, str] = field(default_factory=dict)
    # local name -> (module, original name) for `from mod import name [as alias]`
    from_imports: dict[str, tuple[str, str]] = field(default_factory=dict)


def _unparse(node: ast.AST | None) -> str | None:
    if node is None:
        return None
    try:
        return ast.unparse(node)
    except Exception:
        return None


def _docstring_line_range(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[int, int] | None:
    """(start, end) source lines of the function's docstring statement, if it has one."""
    if not node.body:
        return None
    first = node.body[0]
    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
        return first.lineno, first.end_lineno or first.lineno
    return None


def _extract_params(args: ast.arguments) -> list[dict]:
    params: list[dict] = []

    def add(arg: ast.arg, default: ast.expr | None, prefix: str = "") -> None:
        params.append(
            {
                "name": prefix + arg.arg,
                "annotation": _unparse(arg.annotation),
                "default": _unparse(default),
            }
        )

    positional = args.posonlyargs + args.args
    defaults: list[ast.expr | None] = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
    for arg, default in zip(positional, defaults):
        add(arg, default)
    if args.vararg:
        add(args.vararg, None, "*")
    for arg, kw_default in zip(args.kwonlyargs, args.kw_defaults):
        add(arg, kw_default)
    if args.kwarg:
        add(args.kwarg, None, "**")
    return params


def _module_name(rel_path: str) -> str:
    parts = rel_path[: -len(".py")].split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) if parts else ""


def _infer_roots(sources: dict[str, str]) -> dict[str, str]:
    """rel_path -> the ancestor directory ("" for the picked root itself)
    that file's absolute imports are actually relative to.

    For each directory, pools the first segment of every absolute import
    (`import x...`, `from x... import y`, level 0) used by files in it, then
    walks from that directory outward looking for the closest ancestor that
    directly contains a sibling of that name - e.g. `services.supabase_client`
    only resolves once the search reaches the directory that actually has a
    `services/` child. Directories with no first-party-looking imports default
    to "" (the picked root), which is the previous, unchanged behavior.
    """
    children_of: dict[str, set[str]] = {}
    all_names: set[str] = set()
    for rel_path in sources:
        parts = rel_path.split("/")
        for i in range(len(parts)):
            parent = "/".join(parts[:i])
            name = parts[i][: -len(".py")] if i == len(parts) - 1 and parts[i].endswith(".py") else parts[i]
            children_of.setdefault(parent, set()).add(name)
            all_names.add(name)

    segs_by_dir: dict[str, set[str]] = {}
    for rel_path, source in sources.items():
        directory = rel_path.rsplit("/", 1)[0] if "/" in rel_path else ""
        try:
            tree = ast.parse(source)
        except (SyntaxError, ValueError):
            continue
        segs = segs_by_dir.setdefault(directory, set())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    segs.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                segs.add(node.module.split(".")[0])

    root_for_dir: dict[str, str] = {}
    for directory, segs in segs_by_dir.items():
        relevant = segs & all_names
        parts = directory.split("/") if directory else []
        chosen = ""
        for i in range(len(parts), -1, -1):
            candidate = "/".join(parts[:i])
            if relevant and relevant.issubset(children_of.get(candidate, set())):
                chosen = candidate
                break
        root_for_dir[directory] = chosen

    return {
        rel_path: root_for_dir.get(rel_path.rsplit("/", 1)[0] if "/" in rel_path else "", "") for rel_path in sources
    }


class _ImportVisitor(ast.NodeVisitor):
    """Collects import aliases at any level of the module."""

    def __init__(self, mod: ModuleInfo):
        self.mod = mod

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            local = alias.asname or alias.name.split(".")[0]
            # `import a.b` binds `a`; `import a.b as c` binds c -> a.b
            target = alias.name if alias.asname else alias.name.split(".")[0]
            self.mod.module_aliases[local] = target

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level > 0:
            # relative import: `from . import x` / `from ..pkg import y`
            package_parts = self.mod.module.split(".") if self.mod.module else []
            if self.mod.rel_path.endswith("__init__.py"):
                package_parts.append("")  # packages resolve one level higher than modules
            base = package_parts[: len(package_parts) - node.level] if node.level <= len(package_parts) else []
            module = ".".join(base + node.module.split(".")) if node.module else ".".join(base)
        else:
            module = node.module or ""
        for alias in node.names:
            local = alias.asname or alias.name
            self.mod.from_imports[local] = (module, alias.name)


class _CallCollector(ast.NodeVisitor):
    """Collects (lineno, callee-id) pairs inside one function body."""

    def __init__(self, resolver, class_name: str | None):
        self.resolver = resolver
        self.class_name = class_name
        self.found: list[tuple[int, str]] = []

    # Do not descend into nested function/class definitions: their calls
    # belong to them, not to the enclosing function's flow.
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        pass

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        pass

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        pass

    def visit_Call(self, node: ast.Call) -> None:
        target = self.resolver(node.func, self.class_name)
        if target:
            self.found.append((node.lineno, target))
        self.generic_visit(node)


class _Analyzer:
    def __init__(self, sources: dict[str, str]):
        """sources: rel_path (forward slashes) -> file content, already read."""
        self.sources = sources
        self.modules: dict[str, ModuleInfo] = {}  # dotted module name -> info
        self.functions: dict[str, FunctionInfo] = {}
        self.file_errors: dict[str, str] = {}  # rel_path -> error message
        self.file_functions: dict[str, list[str]] = {}  # rel_path -> [function ids]

    def analyze(self) -> dict:
        self._parse_files()
        self._resolve_calls()
        return self._to_response()

    # -- pass 1: parse everything, register functions and imports --

    def _parse_files(self) -> None:
        roots = _infer_roots(self.sources)
        for rel_path, source in sorted(self.sources.items()):
            self.file_functions.setdefault(rel_path, [])
            try:
                tree = ast.parse(source)
            except (SyntaxError, ValueError) as exc:
                self.file_errors[rel_path] = f"{type(exc).__name__}: {exc}"
                continue

            root = roots[rel_path]
            within_root = rel_path[len(root) + 1 :] if root else rel_path
            mod = ModuleInfo(rel_path=rel_path, module=_module_name(within_root))
            self.modules[mod.module] = mod
            _ImportVisitor(mod).visit(tree)
            source_lines = source.splitlines()

            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    info = self._register_function(mod, node, source_lines, class_name=None)
                    mod.top_level[node.name] = info.id
                elif isinstance(node, ast.ClassDef):
                    methods: dict[str, str] = {}
                    for child in node.body:
                        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            info = self._register_function(mod, child, source_lines, class_name=node.name)
                            methods[child.name] = info.id
                    mod.classes[node.name] = methods

    def _register_function(
        self,
        mod: ModuleInfo,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        source_lines: list[str],
        class_name: str | None,
    ) -> FunctionInfo:
        qualname = f"{class_name}.{node.name}" if class_name else node.name
        func_id = f"{mod.rel_path}::{qualname}"
        start = node.lineno
        end = node.end_lineno or node.lineno
        if node.decorator_list:
            start = min(d.lineno for d in node.decorator_list)
        raw = source_lines[start - 1 : end]
        indent = len(raw[0]) - len(raw[0].lstrip()) if raw else 0
        doc_range = _docstring_line_range(node)
        code_lines = []
        for i, line in enumerate(raw):
            lineno = start + i
            if doc_range and doc_range[0] <= lineno <= doc_range[1]:
                continue
            text = line[indent:] if line[:indent].strip() == "" else line
            code_lines.append({"lineno": lineno, "text": text, "calls": []})

        info = FunctionInfo(
            id=func_id,
            name=node.name,
            qualname=qualname,
            file=mod.rel_path,
            module=mod.module,
            class_name=class_name,
            lineno=node.lineno,
            end_lineno=end,
            params=_extract_params(node.args),
            returns=_unparse(node.returns),
            docstring=ast.get_docstring(node),
            is_async=isinstance(node, ast.AsyncFunctionDef),
            code_lines=code_lines,
        )
        self.functions[func_id] = info
        self.file_functions[mod.rel_path].append(func_id)
        # stash the AST node for pass 2
        info._node = node  # type: ignore[attr-defined]
        return info

    # -- pass 2: resolve calls --

    def _resolve_calls(self) -> None:
        for info in self.functions.values():
            mod = self.modules[info.module]
            resolver = self._make_resolver(mod)
            collector = _CallCollector(resolver, info.class_name)
            node = info._node  # type: ignore[attr-defined]
            for child in node.body:
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

    def _resolve_class(self, name: str, mod: ModuleInfo) -> tuple[ModuleInfo, str] | None:
        """If `name` refers to a class visible in `mod` (defined locally or imported), return (owning module, class name)."""
        if name in mod.classes:
            return mod, name
        if name in mod.from_imports:
            src_module, original = mod.from_imports[name]
            target_mod = self.modules.get(src_module)
            if target_mod and original in target_mod.classes:
                return target_mod, original
        return None

    def _make_resolver(self, mod: ModuleInfo):
        def resolve(func: ast.expr, class_name: str | None) -> str | None:
            # foo(...)
            if isinstance(func, ast.Name):
                name = func.id
                if name in mod.top_level:
                    return mod.top_level[name]
                # calling a class constructor -> its __init__
                ref = self._resolve_class(name, mod)
                if ref:
                    return ref[0].classes[ref[1]].get("__init__")
                if name in mod.from_imports:
                    src_module, original = mod.from_imports[name]
                    target_mod = self.modules.get(src_module)
                    if target_mod and original in target_mod.top_level:
                        return target_mod.top_level[original]
                return None

            if isinstance(func, ast.Attribute):
                # ClassName(...).method() - chained straight off a constructor call, e.g.
                # Analyzer(sources).analyze(): the outer call's receiver is itself a Call.
                if isinstance(func.value, ast.Call) and isinstance(func.value.func, ast.Name):
                    ref = self._resolve_class(func.value.func.id, mod)
                    if ref:
                        return ref[0].classes[ref[1]].get(func.attr)
                    return None

                # something.attr(...)
                if isinstance(func.value, ast.Name):
                    base, attr = func.value.id, func.attr
                    # self.method()
                    if base == "self" and class_name and class_name in mod.classes:
                        return mod.classes[class_name].get(attr)
                    # mod_alias.func()
                    target_module = None
                    if base in mod.module_aliases:
                        target_module = mod.module_aliases[base]
                    elif base in mod.from_imports:
                        # `from pkg import mod` -> base refers to a module
                        src_module, original = mod.from_imports[base]
                        dotted = f"{src_module}.{original}" if src_module else original
                        if dotted in self.modules:
                            target_module = dotted
                    if target_module and target_module in self.modules:
                        target_mod = self.modules[target_module]
                        if attr in target_mod.top_level:
                            return target_mod.top_level[attr]
                        if attr in target_mod.classes and "__init__" in target_mod.classes[attr]:
                            return target_mod.classes[attr]["__init__"]
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
            {
                "path": rel_path,
                "functions": ids,
                "error": self.file_errors.get(rel_path),
            }
            for rel_path, ids in sorted(self.file_functions.items())
        ]
        return {
            "files": files,
            "functions": {fid: info.to_dict() for fid, info in self.functions.items()},
            "edges": edges,
        }


class PythonLanguage(LanguagePlugin):
    id = "python"
    label = "Python"
    extensions = frozenset({".py"})

    def analyze(self, sources: dict[str, str]) -> dict:
        return _Analyzer(sources).analyze()
