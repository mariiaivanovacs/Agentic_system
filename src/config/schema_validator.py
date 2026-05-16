"""
schema_validator.py — validates nodes and edges against schema.yaml.
"""

import os
import yaml
from pathlib import Path


class SchemaValidationError(ValueError):
    pass


class SchemaValidator:
    def __init__(self, schema_path: str | None = None):
        if schema_path is None:
            schema_path = Path(__file__).parent / "schema.yaml"
        with open(schema_path) as f:
            self._schema = yaml.safe_load(f)

    def validate_node(self, label: str, props: dict) -> None:
        """Raise SchemaValidationError if required fields are missing."""
        node_def = self._schema["nodes"].get(label)
        if node_def is None:
            raise SchemaValidationError(f"Unknown node label: {label!r}")
        missing = [f for f in node_def.get("required", []) if f not in props]
        if missing:
            raise SchemaValidationError(
                f"{label} node missing required fields: {missing}"
            )

    def validate_edge(self, rel_type: str, from_label: str, to_label: str) -> None:
        """Raise SchemaValidationError if the relationship is not defined."""
        rel_def = self._schema["relationships"].get(rel_type)
        if rel_def is None:
            raise SchemaValidationError(f"Unknown relationship type: {rel_type!r}")
        if rel_def["from"] != from_label or rel_def["to"] != to_label:
            raise SchemaValidationError(
                f"{rel_type} expects ({rel_def['from']})->({rel_def['to']}), "
                f"got ({from_label})->({to_label})"
            )

    def node_labels(self) -> list[str]:
        return list(self._schema["nodes"].keys())

    def rel_types(self) -> list[str]:
        return list(self._schema["relationships"].keys())
