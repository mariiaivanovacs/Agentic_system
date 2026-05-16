"""
codebase_analyzer.py — local repository analyzer for the agentic layer.

The analyzer is intentionally conservative: it reads source files, applies
simple deterministic heuristics, and emits project-scoped graph primitives. It
does not execute project code.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from src.indexer.base_indexer import (
    BaseIndexer,
    CodeNodeSpec,
    CodeRelationshipSpec,
    IndexedSystem,
    SkillSpec,
)


IGNORED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    "dist",
    "build",
    ".next",
    ".nuxt",
    "coverage",
    ".turbo",
}

SECRET_FILE_PATTERNS = {
    ".env",
    ".env.local",
    ".env.production",
    ".npmrc",
    ".pypirc",
    "id_rsa",
    "id_dsa",
}

SOURCE_EXTENSIONS = {
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".sol",
    ".json",
    ".yaml",
    ".yml",
}

ROUTE_RE = re.compile(
    r"""(?:
        (?:app|router|server)\.(?:get|post|put|patch|delete|route)\(\s*["']([^"']+)["'] |
        path\s*:\s*["']([^"']+)["'] |
        path\s*=\s*["']([^"']+)["'] |
        route\s*[:=]\s*["']([^"']+)["']
    )""",
    re.IGNORECASE | re.VERBOSE,
)
JS_FUNCTION_RE = re.compile(
    r"""(?:function\s+([A-Za-z_$][\w$]*)\s*\(|const\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\(|export\s+function\s+([A-Za-z_$][\w$]*)\s*\()"""
)
CLASS_RE = re.compile(r"\bclass\s+([A-Za-z_$][\w$]*)")
MODEL_HINT_RE = re.compile(r"(model|schema|table|entity)", re.IGNORECASE)
INTEGRATION_HINTS = {
    "stripe": "Stripe",
    "sendgrid": "SendGrid",
    "twilio": "Twilio",
    "firebase": "Firebase",
    "supabase": "Supabase",
    "openai": "OpenAI",
    "google": "Google",
    "aws": "AWS",
    "s3": "AWS S3",
    "neo4j": "Neo4j",
    "postgres": "Postgres",
    "mongodb": "MongoDB",
    "web3": "Web3",
    "ethers": "Ethers",
}
STORAGE_HINTS = {
    "localstorage": ("Browser localStorage", "browser_key_value"),
    "sessionstorage": ("Browser sessionStorage", "browser_key_value"),
    "indexeddb": ("IndexedDB", "browser_database"),
    "sqlite": ("SQLite", "sql_database"),
    "postgres": ("Postgres", "sql_database"),
    "postgresql": ("Postgres", "sql_database"),
    "mysql": ("MySQL", "sql_database"),
    "mongodb": ("MongoDB", "document_database"),
    "mongoose": ("MongoDB", "document_database"),
    "redis": ("Redis", "cache"),
    "prisma": ("Prisma ORM", "orm"),
    "typeorm": ("TypeORM", "orm"),
    "sequelize": ("Sequelize ORM", "orm"),
    "sqlalchemy": ("SQLAlchemy", "orm"),
    "clarinet": ("Stacks/Clarinet local chain", "blockchain_state"),
    "contract": ("Smart contract state", "blockchain_state"),
}
RISK_HINT_RE = re.compile(r"\b(eval|exec|private[_-]?key|secret|password|token|dangerouslySetInnerHTML)\b", re.IGNORECASE)


PRIMITIVE_DESCRIPTIONS = {
    "Project": (
        "The approved software system being analyzed.",
        "The product or application connected to the agentic layer.",
    ),
    "Repository": (
        "The local source-code root scanned by the analyzer.",
        "The code folder that contains the application.",
    ),
    "File": (
        "A source or manifest file that may define routes, functions, storage access, or risks.",
        "A code file that contributes behavior to the application.",
    ),
    "Route": (
        "An entrypoint path detected from router/API declarations.",
        "A screen or API path a user or system can enter.",
    ),
    "Service": (
        "A class or service-like unit that groups behavior.",
        "A business capability or backend component.",
    ),
    "Function": (
        "A callable unit discovered from Python/JavaScript/TypeScript/Solidity source.",
        "An action the system can perform and potentially turn into an agent skill.",
    ),
    "DatabaseModel": (
        "A model/schema/table-like code definition.",
        "A representation of business data in the code.",
    ),
    "DataStore": (
        "A detected persistence mechanism such as SQL, browser storage, files, cache, or blockchain state.",
        "Where the application appears to keep or retrieve data.",
    ),
    "Workflow": (
        "A file-level flow inferred from route/controller/workflow naming.",
        "A user or business process made from smaller software pieces.",
    ),
    "Integration": (
        "An external library/platform detected from code references.",
        "An outside service the product depends on.",
    ),
    "Artifact": (
        "A manifest/config/build file that describes the project runtime.",
        "A supporting file that tells us how the app is built or run.",
    ),
    "Risk": (
        "A static code hint that may require sandbox review before changes are proposed.",
        "A potential safety, security, or data concern.",
    ),
}


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9_]+", "_", text.lower()).strip("_")
    return slug or "root"


def stable_project_id(path: str | Path) -> str:
    root = Path(path).expanduser().resolve()
    digest = hashlib.sha1(str(root).encode("utf-8")).hexdigest()[:10]
    return f"project_{_slug(root.name)}_{digest}"


def stable_scan_id(project_id: str, root: Path) -> str:
    parts = [project_id, str(root)]
    for file_path in discover_source_files(root):
        try:
            stat = file_path.stat()
        except OSError:
            continue
        parts.append(f"{file_path.relative_to(root)}:{stat.st_mtime_ns}:{stat.st_size}")
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:12]
    return f"scan_{digest}"


def should_ignore_path(path: Path) -> bool:
    parts = set(path.parts)
    if parts.intersection(IGNORED_DIRS):
        return True
    name = path.name
    if name in SECRET_FILE_PATTERNS:
        return True
    if name.startswith(".env."):
        return True
    return False


def discover_source_files(root: str | Path) -> list[Path]:
    base = Path(root).expanduser().resolve()
    files: list[Path] = []
    if not base.exists() or not base.is_dir():
        return files
    for path in sorted(base.rglob("*")):
        if should_ignore_path(path):
            continue
        if path.is_file() and path.suffix.lower() in SOURCE_EXTENSIONS:
            files.append(path)
    return files


@dataclass
class _AnalysisContext:
    root: Path
    project_id: str
    scan_id: str
    created_at: str

    def node(
        self,
        label: str,
        local_id: str,
        name: str,
        source_path: str,
        confidence: float = 1.0,
        **properties: object,
    ) -> CodeNodeSpec:
        node_id = f"{self.project_id}:{label.lower()}:{_slug(local_id)}"
        technical, stakeholder = PRIMITIVE_DESCRIPTIONS.get(
            label,
            ("Software primitive discovered by static analysis.", "A detected part of the application."),
        )
        return CodeNodeSpec(
            id=node_id,
            label=label,
            name=name,
            project_id=self.project_id,
            scan_id=self.scan_id,
            source_path=source_path,
            confidence=confidence,
            properties={
                "created_at": self.created_at,
                "display_name": f"{label}: {name}",
                "technical_description": technical,
                "stakeholder_description": stakeholder,
                **{k: v for k, v in properties.items() if v is not None},
            },
        )


class CodebaseAnalyzer(BaseIndexer):
    def __init__(self, source: str, project_name: str | None = None, project_id: str | None = None):
        super().__init__(source)
        self.root = Path(source).expanduser().resolve()
        self.project_name = project_name or self.root.name
        self.project_id = project_id or stable_project_id(self.root)

    def discover(self) -> IndexedSystem:
        if not self.root.exists() or not self.root.is_dir():
            raise ValueError(f"Codebase path does not exist or is not a directory: {self.root}")

        scan_id = stable_scan_id(self.project_id, self.root)
        created_at = datetime.now(timezone.utc).isoformat()
        ctx = _AnalysisContext(self.root, self.project_id, scan_id, created_at)

        nodes: dict[str, CodeNodeSpec] = {}
        relationships: list[CodeRelationshipSpec] = []
        skills: list[SkillSpec] = []

        project = CodeNodeSpec(
            id=self.project_id,
            label="Project",
            name=self.project_name,
            project_id=self.project_id,
            scan_id=scan_id,
            source_path=str(self.root),
            confidence=1.0,
            properties={
                "repo_path": str(self.root),
                "permission_status": "approved",
                "analysis_status": "analysis_complete",
                "last_scan_id": scan_id,
                "created_at": created_at,
                "updated_at": created_at,
            },
        )
        repository = ctx.node(
            "Repository",
            self.root.name,
            self.root.name,
            str(self.root),
            repo_path=str(self.root),
        )
        nodes[project.id] = project
        nodes[repository.id] = repository
        relationships.append(CodeRelationshipSpec("PROJECT_HAS_REPOSITORY", project.id, repository.id))

        source_files = discover_source_files(self.root)
        for file_path in source_files:
            rel_path = str(file_path.relative_to(self.root))
            file_node = ctx.node(
                "File",
                rel_path,
                rel_path,
                rel_path,
                extension=file_path.suffix.lower(),
            )
            nodes[file_node.id] = file_node
            relationships.append(CodeRelationshipSpec("REPOSITORY_HAS_FILE", repository.id, file_node.id))

            try:
                text = file_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue

            for node in self._extract_python(ctx, file_path, text):
                nodes[node.id] = node
                rel_type = {
                    "Function": "FILE_DEFINES_FUNCTION",
                    "Service": "FILE_DEFINES_SERVICE",
                    "DatabaseModel": "FILE_DEFINES_MODEL",
                    "Entity": "FILE_DEFINES_ENTITY",
                }.get(node.label, "FILE_DEFINES_FUNCTION")
                relationships.append(CodeRelationshipSpec(rel_type, file_node.id, node.id))
                if node.label == "Function":
                    skills.append(self._skill_from_function(node))

            for node in self._extract_textual(ctx, file_path, text):
                nodes[node.id] = node
                rel_type = {
                    "Route": "FILE_DEFINES_ROUTE",
                    "Function": "FILE_DEFINES_FUNCTION",
                    "Service": "FILE_DEFINES_SERVICE",
                    "DatabaseModel": "FILE_DEFINES_MODEL",
                    "Integration": "FILE_USES_INTEGRATION",
                    "DataStore": "FILE_USES_DATASTORE",
                    "Risk": "RISK_FOUND_IN",
                    "Workflow": "FILE_DEFINES_WORKFLOW",
                    "Artifact": "REPOSITORY_HAS_ARTIFACT",
                }.get(node.label, "REPOSITORY_HAS_ARTIFACT")
                from_id = repository.id if node.label == "Artifact" else file_node.id
                relationships.append(CodeRelationshipSpec(rel_type, from_id, node.id))
                if node.label == "Function":
                    skills.append(self._skill_from_function(node))

        return IndexedSystem(
            skills=self._dedupe_skills(skills),
            code_nodes=list(nodes.values()),
            code_relationships=self._dedupe_relationships(relationships),
            metadata={
                "source": str(self.root),
                "source_type": "codebase",
                "project_id": self.project_id,
                "project_name": self.project_name,
                "scan_id": scan_id,
                "file_count": len(source_files),
            },
        )

    def _extract_python(self, ctx: _AnalysisContext, file_path: Path, text: str) -> Iterable[CodeNodeSpec]:
        if file_path.suffix.lower() != ".py":
            return []
        rel_path = str(file_path.relative_to(ctx.root))
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return []

        nodes: list[CodeNodeSpec] = []
        for item in ast.walk(tree):
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and not item.name.startswith("_"):
                nodes.append(
                    ctx.node(
                        "Function",
                        f"{rel_path}:{item.name}",
                        item.name,
                        rel_path,
                        line=item.lineno,
                        async_function=isinstance(item, ast.AsyncFunctionDef),
                        docstring=(ast.get_docstring(item) or "")[:240],
                    )
                )
            elif isinstance(item, ast.ClassDef) and not item.name.startswith("_"):
                label = "DatabaseModel" if MODEL_HINT_RE.search(item.name) else "Service"
                nodes.append(ctx.node(label, f"{rel_path}:{item.name}", item.name, rel_path, line=item.lineno))
        return nodes

    def _extract_textual(self, ctx: _AnalysisContext, file_path: Path, text: str) -> Iterable[CodeNodeSpec]:
        rel_path = str(file_path.relative_to(ctx.root))
        nodes: list[CodeNodeSpec] = []

        for match in ROUTE_RE.finditer(text):
            route = next(value for value in match.groups() if value)
            nodes.append(
                ctx.node(
                    "Route",
                    f"{rel_path}:{route}",
                    route,
                    rel_path,
                    confidence=0.86,
                    route_path=route,
                )
            )

        if file_path.suffix.lower() in {".js", ".jsx", ".ts", ".tsx"}:
            for match in JS_FUNCTION_RE.finditer(text):
                name = next(value for value in match.groups() if value)
                nodes.append(ctx.node("Function", f"{rel_path}:{name}", name, rel_path, confidence=0.78))
            for match in CLASS_RE.finditer(text):
                name = match.group(1)
                label = "DatabaseModel" if MODEL_HINT_RE.search(name) else "Service"
                nodes.append(ctx.node(label, f"{rel_path}:{name}", name, rel_path, confidence=0.76))

        if file_path.suffix.lower() == ".sol":
            for match in re.finditer(r"\bcontract\s+([A-Za-z_]\w*)", text):
                nodes.append(ctx.node("Service", f"{rel_path}:{match.group(1)}", match.group(1), rel_path, confidence=0.82, service_type="smart_contract"))
            for match in re.finditer(r"\bfunction\s+([A-Za-z_]\w*)\s*\(", text):
                nodes.append(ctx.node("Function", f"{rel_path}:{match.group(1)}", match.group(1), rel_path, confidence=0.8, language="solidity"))

        for key, display in INTEGRATION_HINTS.items():
            if re.search(rf"\b{re.escape(key)}\b", text, re.IGNORECASE):
                nodes.append(ctx.node("Integration", f"{rel_path}:{display}", display, rel_path, confidence=0.7, integration_type=display))

        lowered = text.lower()
        for key, (display, storage_type) in STORAGE_HINTS.items():
            if key in lowered:
                nodes.append(
                    ctx.node(
                        "DataStore",
                        f"{rel_path}:{display}",
                        display,
                        rel_path,
                        confidence=0.72,
                        storage_type=storage_type,
                    )
                )

        if file_path.suffix.lower() == ".csv":
            nodes.append(
                ctx.node(
                    "DataStore",
                    f"{rel_path}:CSV files",
                    "CSV files",
                    rel_path,
                    confidence=0.9,
                    storage_type="file_storage",
                )
            )

        for match in RISK_HINT_RE.finditer(text):
            nodes.append(
                ctx.node(
                    "Risk",
                    f"{rel_path}:{match.group(0)}",
                    f"Risk hint: {match.group(0)}",
                    rel_path,
                    confidence=0.66,
                    risk_type=match.group(0).lower(),
                )
            )

        if file_path.name in {"package.json", "pyproject.toml", "requirements.txt", "Dockerfile"}:
            nodes.append(ctx.node("Artifact", rel_path, file_path.name, rel_path, confidence=0.9, artifact_type="project_manifest"))

        if any(token in rel_path.lower() for token in ("workflow", "pipeline", "route", "controller")):
            nodes.append(ctx.node("Workflow", rel_path, Path(rel_path).stem, rel_path, confidence=0.62))

        return nodes

    @staticmethod
    def _skill_from_function(node: CodeNodeSpec) -> SkillSpec:
        language = node.properties.get("language") or Path(node.source_path).suffix.lstrip(".") or "code"
        return SkillSpec(
            id=f"skill_{_slug(node.id)}",
            name=node.name,
            description=f"Derived from {node.source_path}",
            language=str(language),
            performance_score=5.0,
            avg_execution_ms=100.0,
        )

    @staticmethod
    def _dedupe_skills(skills: list[SkillSpec]) -> list[SkillSpec]:
        unique: dict[str, SkillSpec] = {}
        for skill in skills:
            unique[skill.id] = skill
        return list(unique.values())

    @staticmethod
    def _dedupe_relationships(relationships: list[CodeRelationshipSpec]) -> list[CodeRelationshipSpec]:
        unique: dict[tuple[str, str, str], CodeRelationshipSpec] = {}
        for rel in relationships:
            unique[(rel.rel_type, rel.from_id, rel.to_id)] = rel
        return list(unique.values())


def analyze_codebase(source: str, project_name: str | None = None, project_id: str | None = None) -> IndexedSystem:
    return CodebaseAnalyzer(source=source, project_name=project_name, project_id=project_id).discover()
