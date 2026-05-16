"""
openapi_indexer.py — reads an OpenAPI 3.x spec and produces ConnectorSpec/SkillSpec nodes.

Each path+method  → one ConnectorSpec
Each operationId  → one SkillSpec

Usage:
    OpenAPIIndexer(source="https://api.example.com/openapi.json").discover()
    OpenAPIIndexer(source="./openapi.yaml").discover()
"""

import re
import json
from pathlib import Path

import httpx
import yaml

from src.indexer.base_indexer import BaseIndexer, ConnectorSpec, IndexedSystem, SkillSpec


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9_]", "_", text.lower()).strip("_")


class OpenAPIIndexer(BaseIndexer):
    def discover(self) -> IndexedSystem:
        spec = self._load_spec()
        info = spec.get("info", {})
        base_name = info.get("title", self.source)
        version = info.get("version", "1.0")
        server_url = (spec.get("servers") or [{}])[0].get("url", self.source)

        connectors: list[ConnectorSpec] = []
        skills: list[SkillSpec] = []

        for path, path_item in spec.get("paths", {}).items():
            for method, operation in path_item.items():
                if method.startswith("x-") or not isinstance(operation, dict):
                    continue

                op_id = operation.get("operationId") or f"{method}_{_slug(path)}"
                summary = operation.get("summary") or operation.get("description") or op_id

                connector_id = f"openapi_{_slug(base_name)}_{_slug(method)}_{_slug(path)}"
                connectors.append(ConnectorSpec(
                    id=connector_id,
                    name=f"{method.upper()} {path}",
                    type="http",
                    description=summary,
                    version=version,
                ))

                skill_id = f"skill_{_slug(op_id)}"
                skills.append(SkillSpec(
                    id=skill_id,
                    name=op_id,
                    description=summary,
                    language="openapi",
                ))

        return IndexedSystem(
            connectors=connectors,
            skills=skills,
            metadata={"source": self.source, "source_type": "openapi", "api_title": base_name},
        )

    def _load_spec(self) -> dict:
        src = self.source
        if src.startswith("http://") or src.startswith("https://"):
            response = httpx.get(src, timeout=15, follow_redirects=True)
            response.raise_for_status()
            text = response.text
        else:
            text = Path(src).read_text()

        if src.endswith(".json") or (src.startswith("http") and "json" in src):
            return json.loads(text)
        return yaml.safe_load(text)
