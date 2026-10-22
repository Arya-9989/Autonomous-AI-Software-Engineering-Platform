"""Which HTTP routes a generated FastAPI project actually implements, read statically.

The architecture lists the endpoints the model *designed*; this reads the code to find the ones
it *built*, without importing or running it: ``APIRouter(prefix=...)`` assignments, route
decorators (``@router.get("/{id}")``, ``@app.post(...)``) and ``app.include_router(...,
prefix=...)``. Paths are normalised so ``/books/{book_id}`` and ``/books/{id}`` compare equal.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable, Mapping
from pathlib import PurePosixPath

HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete"})
_PARAM = re.compile(r"\{[^}]*\}")


def normalize_endpoint(method: str, path: str) -> str:
    """``"get", "/books/{book_id}/"`` -> ``"GET /books/{}"``."""
    cleaned = "/" + "/".join(part for part in _PARAM.sub("{}", path).split("/") if part)
    return f"{method.upper()} {cleaned}"


def parse_endpoint(text: str) -> str:
    method, _, path = text.strip().partition(" ")
    return normalize_endpoint(method, path.strip())


def _string(node: ast.expr | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _keyword(call: ast.Call, name: str) -> str | None:
    return next((_string(k.value) for k in call.keywords if k.arg == name), None)


def _module_name(path: str) -> str:
    return ".".join(PurePosixPath(path).with_suffix("").parts)


class _ModuleRoutes:
    def __init__(self, tree: ast.Module) -> None:
        self.prefixes: dict[str, str] = {}  # APIRouter variable -> prefix
        self.apps: set[str] = set()  # FastAPI() variables
        self.routes: list[tuple[str, str, str]] = []  # (router variable, method, path)
        self.includes: list[tuple[str, str]] = []  # (dotted router reference, extra prefix)
        self.imports: dict[str, str] = {}  # local name -> dotted module
        for node in ast.walk(tree):
            self._visit(node)

    def _visit(self, node: ast.AST) -> None:
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                self.imports[alias.asname or alias.name] = f"{node.module}.{alias.name}"
        elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            func = node.value.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            for target in node.targets:
                if isinstance(target, ast.Name) and name == "APIRouter":
                    self.prefixes[target.id] = _keyword(node.value, "prefix") or ""
                elif isinstance(target, ast.Name) and name == "FastAPI":
                    self.apps.add(target.id)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            for decorator in node.decorator_list:
                self._decorator(decorator)
        elif (
            isinstance(node, ast.Call)
            and getattr(node.func, "attr", None) == "include_router"
            and node.args
        ):
            self.includes.append((ast.unparse(node.args[0]), _keyword(node, "prefix") or ""))

    def _decorator(self, decorator: ast.expr) -> None:
        if not (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)):
            return
        method = decorator.func.attr
        owner = decorator.func.value
        if method in HTTP_METHODS and isinstance(owner, ast.Name):
            path = _string(decorator.args[0]) if decorator.args else _keyword(decorator, "path")
            if path is not None:
                self.routes.append((owner.id, method, path))


def implemented_endpoints(files: Mapping[str, str]) -> set[str]:
    """Normalised ``METHOD /path`` strings the project serves (FastAPI, statically)."""
    modules: dict[str, _ModuleRoutes] = {}
    for path, content in files.items():
        if not path.endswith(".py"):
            continue
        try:
            modules[_module_name(path)] = _ModuleRoutes(ast.parse(content))
        except SyntaxError:
            continue

    # A router's routes are served under the prefix of every include_router() that mounts it.
    mounts: dict[tuple[str, str], list[str]] = {}
    for module_name, module in modules.items():
        for reference, extra in module.includes:
            target = _resolve(reference, module, module_name)
            if target is not None:
                mounts.setdefault(target, []).append(extra)

    endpoints: set[str] = set()
    for module_name, module in modules.items():
        for variable, method, path in module.routes:
            if variable in module.apps:
                endpoints.add(normalize_endpoint(method, path))
            elif variable in module.prefixes:  # a router serves nothing until it is mounted
                prefix = module.prefixes[variable]
                for extra in mounts.get((module_name, variable), []):
                    endpoints.add(normalize_endpoint(method, f"{extra}{prefix}{path}"))
    return endpoints


def _resolve(reference: str, module: _ModuleRoutes, module_name: str) -> tuple[str, str] | None:
    """``books.router`` (after ``from app.routers import books``) -> ``("app.routers.books",
    "router")``; a bare ``router`` resolves through ``from x import router``."""
    head, _, attr = reference.rpartition(".")
    if head:
        dotted = module.imports.get(head.split(".")[0])
        if dotted is None:
            return None
        rest = head.split(".")[1:]
        return (".".join([dotted, *rest]), attr)
    dotted = module.imports.get(attr)
    if dotted is None:
        return (module_name, attr)
    owner, _, name = dotted.rpartition(".")
    return (owner, name)


def recall(expected: Iterable[str], actual: set[str]) -> float | None:
    wanted = {parse_endpoint(item) for item in expected}
    if not wanted:
        return None
    return len(wanted & actual) / len(wanted)
