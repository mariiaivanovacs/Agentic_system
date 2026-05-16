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
from collections import defaultdict
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

ACTION_VERBS = {
    "add",
    "approve",
    "connect",
    "create",
    "delete",
    "donate",
    "get",
    "list",
    "login",
    "register",
    "submit",
    "update",
}
WRITE_ACTION_VERBS = {"add", "approve", "connect", "create", "delete", "donate", "register", "submit", "update"}
FLOW_STOPWORDS = {
    "api",
    "app",
    "button",
    "component",
    "components",
    "controller",
    "controllers",
    "form",
    "handler",
    "handlers",
    "hook",
    "hooks",
    "index",
    "lib",
    "page",
    "pages",
    "route",
    "routes",
    "screen",
    "service",
    "services",
    "src",
    "ui",
    "utils",
    "view",
    "views",
}


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
    "BusinessFlow": (
        "A business-logic capability inferred from entrypoints, functions, services, and storage usage.",
        "A user-facing thing the app can do, shown as an ordered chain of software parts.",
    ),
    "FlowStep": (
        "An ordered step inside a business flow, linked to the primitive that provides evidence for the step.",
        "One step in the app function, such as UI, route, function, storage, integration, or review.",
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


def _tokens(text: str) -> list[str]:
    de_camel = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    raw = re.sub(r"[^A-Za-z0-9]+", " ", de_camel).lower().split()
    return [token for token in raw if token and token not in FLOW_STOPWORDS]


def _flow_signature(text: str) -> tuple[str, str, str] | None:
    tokens = _tokens(text)
    for index, token in enumerate(tokens):
        if token not in ACTION_VERBS:
            continue
        nouns = [
            item for item in tokens[index + 1 :]
            if item not in ACTION_VERBS and item not in FLOW_STOPWORDS
        ][:3]
        if not nouns:
            nouns = ["item"]
        display = " ".join([token, *nouns]).title()
        return _slug(display), display, token
    return None


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

        self._build_business_flows(ctx, nodes, relationships)

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

    def _build_business_flows(
        self,
        ctx: _AnalysisContext,
        nodes: dict[str, CodeNodeSpec],
        relationships: list[CodeRelationshipSpec],
    ) -> None:
        by_source: dict[str, list[CodeNodeSpec]] = defaultdict(list)
        for node in nodes.values():
            by_source[node.source_path].append(node)

        candidate_sources: dict[str, set[str]] = defaultdict(set)
        flow_names: dict[str, str] = {}
        flow_verbs: dict[str, str] = {}
        candidate_labels = {"File", "Route", "Function", "Service", "Workflow"}
        for node in nodes.values():
            if node.label not in candidate_labels:
                continue
            signature = _flow_signature(f"{node.name} {node.source_path}")
            if not signature:
                continue
            key, display_name, verb = signature
            candidate_sources[key].add(node.source_path)
            flow_names[key] = display_name
            flow_verbs[key] = verb

        for flow_key in sorted(candidate_sources):
            sources = sorted(candidate_sources[flow_key])
            primitives = self._collect_flow_primitives(by_source, sources)
            if not primitives:
                continue

            flow_name = flow_names[flow_key]
            verb = flow_verbs.get(flow_key, "")
            entrypoint = self._entrypoint_for_flow(primitives)
            flow_node = ctx.node(
                "BusinessFlow",
                flow_key,
                flow_name,
                sources[0],
                confidence=self._flow_confidence(primitives),
                source_paths=sources,
                entrypoint=entrypoint,
                flow_type="business_logic",
                action_verb=verb,
                evidence_summary=self._flow_evidence_summary(primitives),
            )
            nodes[flow_node.id] = flow_node
            relationships.append(CodeRelationshipSpec("HAS_BUSINESS_FLOW", ctx.project_id, flow_node.id))

            ordered = self._ordered_flow_primitives(primitives)
            for order, primitive in enumerate(ordered, start=1):
                step_type = self._step_type(primitive)
                step_name = f"{order:02d} {step_type}: {primitive.name}"
                step_node = ctx.node(
                    "FlowStep",
                    f"{flow_key}:{order}:{primitive.id}",
                    step_name,
                    primitive.source_path,
                    confidence=min(float(flow_node.confidence), float(primitive.confidence)),
                    order=order,
                    step_type=step_type,
                    primitive_id=primitive.id,
                    primitive_label=primitive.label,
                    evidence=primitive.source_path,
                    evidence_summary=f"{step_type} evidence from {primitive.source_path}",
                )
                nodes[step_node.id] = step_node
                relationships.append(CodeRelationshipSpec("HAS_STEP", flow_node.id, step_node.id, {"order": order}))
                relationships.append(CodeRelationshipSpec("USES_PRIMITIVE", step_node.id, primitive.id))

            self._add_inferred_call_edges(relationships, ordered, verb)

    @staticmethod
    def _collect_flow_primitives(
        by_source: dict[str, list[CodeNodeSpec]],
        sources: list[str],
    ) -> list[CodeNodeSpec]:
        labels = {
            "File",
            "Route",
            "Function",
            "Service",
            "DatabaseModel",
            "DatabaseTable",
            "DataStore",
            "Integration",
            "Risk",
        }
        collected: dict[str, CodeNodeSpec] = {}
        for source in sources:
            for node in by_source.get(source, []):
                if node.label in labels:
                    collected[node.id] = node
        return list(collected.values())

    @staticmethod
    def _entrypoint_for_flow(primitives: list[CodeNodeSpec]) -> str:
        for label in ("Route", "Function", "Service", "File"):
            for primitive in primitives:
                if primitive.label == label:
                    return str(primitive.properties.get("route_path") or primitive.name)
        return primitives[0].name if primitives else ""

    @staticmethod
    def _flow_confidence(primitives: list[CodeNodeSpec]) -> float:
        labels = {primitive.label for primitive in primitives}
        confidence = 0.58
        if "Route" in labels:
            confidence += 0.14
        if "Function" in labels:
            confidence += 0.12
        if "DataStore" in labels or "Integration" in labels:
            confidence += 0.08
        if "File" in labels:
            confidence += 0.04
        return min(confidence, 0.92)

    @staticmethod
    def _flow_evidence_summary(primitives: list[CodeNodeSpec]) -> str:
        counts: dict[str, int] = defaultdict(int)
        for primitive in primitives:
            counts[primitive.label] += 1
        return ", ".join(f"{label}: {counts[label]}" for label in sorted(counts))

    @staticmethod
    def _step_type(node: CodeNodeSpec) -> str:
        if node.label == "File":
            ext = str(node.properties.get("extension", ""))
            if ext in {".jsx", ".tsx"} or any(part in node.source_path.lower() for part in ("component", "page", "view")):
                return "UI/File"
            return "File"
        return {
            "Route": "API Route",
            "Function": "Function",
            "Service": "Service",
            "DatabaseModel": "Data Model",
            "DatabaseTable": "Data Table",
            "DataStore": "Storage",
            "Integration": "Integration",
            "Risk": "Review",
        }.get(node.label, node.label)

    @classmethod
    def _ordered_flow_primitives(cls, primitives: list[CodeNodeSpec]) -> list[CodeNodeSpec]:
        rank = {
            "File": 10,
            "Route": 20,
            "Function": 30,
            "Service": 40,
            "DatabaseModel": 50,
            "DatabaseTable": 55,
            "DataStore": 60,
            "Integration": 70,
            "Risk": 80,
        }
        return sorted(
            primitives,
            key=lambda node: (
                rank.get(node.label, 90),
                int(node.properties.get("line") or 0),
                node.source_path,
                node.name,
            ),
        )

    @staticmethod
    def _add_inferred_call_edges(
        relationships: list[CodeRelationshipSpec],
        ordered: list[CodeNodeSpec],
        verb: str,
    ) -> None:
        routes = [node for node in ordered if node.label == "Route"]
        functions = [node for node in ordered if node.label == "Function"]
        stores = [node for node in ordered if node.label == "DataStore"]

        for route in routes:
            for function in functions:
                relationships.append(
                    CodeRelationshipSpec(
                        "ROUTE_CALLS_FUNCTION",
                        route.id,
                        function.id,
                        {"inferred": True},
                    )
                )

        for left, right in zip(functions, functions[1:]):
            relationships.append(
                CodeRelationshipSpec(
                    "FUNCTION_CALLS_FUNCTION",
                    left.id,
                    right.id,
                    {"inferred": True},
                )
            )

        rel_type = "FUNCTION_WRITES_DATASTORE" if verb in WRITE_ACTION_VERBS else "FUNCTION_READS_DATASTORE"
        for function in functions:
            for store in stores:
                relationships.append(
                    CodeRelationshipSpec(
                        rel_type,
                        function.id,
                        store.id,
                        {"inferred": True},
                    )
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
