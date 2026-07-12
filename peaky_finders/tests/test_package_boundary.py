"""Package boundary: core must not import serve."""

from __future__ import annotations

import ast
from pathlib import Path


def _collect_imports(module_path: Path) -> set[str]:
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


def test_core_does_not_import_serve() -> None:
    pkg_root = Path(__file__).resolve().parents[1] / "src" / "peaky_finders" / "core"
    offenders: list[str] = []
    for path in pkg_root.rglob("*.py"):
        for mod in _collect_imports(path):
            if mod == "peaky_finders.serve" or mod.startswith("peaky_finders.serve."):
                offenders.append(f"{path.relative_to(pkg_root.parent)} imports {mod}")
    assert not offenders, "core → serve imports:\n" + "\n".join(offenders)
