"""
python_indexer.py — reads a Python package directory and produces SkillSpec nodes.

Each public function with type hints → one SkillSpec.

Usage:
    PythonIndexer(source="./my_package").discover()
"""

import ast
import re
from pathlib import Path

from src.indexer.base_indexer import BaseIndexer, IndexedSystem, SkillSpec


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9_]", "_", text.lower()).strip("_")


class PythonIndexer(BaseIndexer):
    def discover(self) -> IndexedSystem:
        root = Path(self.source).resolve()
        skills: list[SkillSpec] = []

        for py_file in sorted(root.rglob("*.py")):
            if py_file.name.startswith("_"):
                continue
            try:
                tree = ast.parse(py_file.read_text(encoding="utf-8"))
            except SyntaxError:
                continue

            module_rel = py_file.relative_to(root).with_suffix("")
            module_name = ".".join(module_rel.parts)

            for node in ast.walk(tree):
                if not isinstance(node, ast.FunctionDef):
                    continue
                if node.name.startswith("_"):
                    continue
                if not self._has_type_hints(node):
                    continue

                docstring = ast.get_docstring(node) or ""
                skill_id = f"skill_{_slug(module_name)}_{_slug(node.name)}"
                skills.append(SkillSpec(
                    id=skill_id,
                    name=f"{module_name}.{node.name}",
                    description=docstring[:200] if docstring else node.name,
                    language="python",
                ))

        return IndexedSystem(
            skills=skills,
            metadata={"source": self.source, "source_type": "python", "file_count": len(list(root.rglob("*.py")))},
        )

    @staticmethod
    def _has_type_hints(func: ast.FunctionDef) -> bool:
        has_return = func.returns is not None
        has_arg_hints = any(a.annotation is not None for a in func.args.args)
        return has_return or has_arg_hints
