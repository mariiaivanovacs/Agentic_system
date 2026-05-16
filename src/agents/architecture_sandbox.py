from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote_plus

import pandas as pd
from src.indexer.codebase_analyzer import CodebaseAnalyzer
from src.indexer.db_indexer import DBIndexer


IGNORED_COPY_PATTERNS = (
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
    ".agent_architecture_sandbox",
    "*.pyc",
)

SECRET_FILE_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    ".npmrc",
    ".pypirc",
    "id_rsa",
    "id_dsa",
}

SECRET_KEY_RE = re.compile(
    r"(password|passwd|secret|token|api[_-]?key|credential|private[_-]?key|access[_-]?key)",
    re.IGNORECASE,
)

DATABASE_ENV_CANDIDATES = (
    "INDEX_DB_DSN",
    "DATABASE_URL",
    "DB_URL",
    "SQLALCHEMY_DATABASE_URI",
    "POSTGRES_URL",
    "POSTGRES_DSN",
    "MYSQL_URL",
    "MYSQL_DSN",
    "SQLITE_URL",
    "SQLITE_PATH",
)


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9_]+", "_", text.lower()).strip("_")
    return slug or "project"


def resolve_project_source_path(repo_path: str) -> dict[str, Any]:
    """Resolve a saved repository path to a folder on the current machine.

    Project scans can be stored with paths from another OS or user account, for
    example `/Users/name/Desktop/app`. The sandbox must copy a real local
    folder, so this helper tries the saved path first, then conservative local
    equivalents without doing an expensive full-disk search.
    """
    raw = str(repo_path or "").strip()
    candidates: list[Path] = []
    notes: list[str] = []

    def add(path: Path, note: str) -> None:
        if not path:
            return
        resolved_text = str(path)
        if resolved_text not in {str(existing) for existing in candidates}:
            candidates.append(path)
            notes.append(note)

    if raw:
        add(Path(raw).expanduser(), "saved Project Review path")

    if raw.startswith("/Users/") or raw.startswith("/home/"):
        parts = PurePosixPath(raw).parts
        if len(parts) >= 4:
            tail = parts[3:]
            add(Path.home().joinpath(*tail), "current user's matching path")
            add(Path("C:/Users").joinpath(parts[2], *tail), "same username on Windows")

    project_folder_name = Path(PurePosixPath(raw).name).name if raw else ""
    if project_folder_name:
        for root in (
            Path.home() / "Desktop",
            Path.home() / "Documents",
            Path.home() / "Downloads",
            Path.cwd().parent,
            Path.cwd(),
        ):
            add(root / project_folder_name, f"{root} / {project_folder_name}")
        for root in (Path.home() / "Desktop", Path.home() / "Documents"):
            if root.exists():
                for direct_child in root.iterdir():
                    if direct_child.is_dir():
                        add(direct_child / project_folder_name, f"{direct_child} / {project_folder_name}")

    checked: list[dict[str, str]] = []
    for index, candidate in enumerate(candidates):
        try:
            expanded = candidate.expanduser()
            checked.append({"path": str(expanded), "source": notes[index]})
            if expanded.is_dir():
                return {
                    "input_path": raw,
                    "resolved_path": str(expanded.resolve()),
                    "exists": True,
                    "source": notes[index],
                    "checked": checked,
                }
        except OSError:
            continue

    return {
        "input_path": raw,
        "resolved_path": "",
        "exists": False,
        "source": "",
        "checked": checked[:12],
    }


def _safe_copy_ignore(_: str, names: list[str]) -> set[str]:
    ignored = set()
    for name in names:
        lower = name.lower()
        if lower in SECRET_FILE_NAMES:
            ignored.add(name)
            continue
        if lower.endswith((".pyc", ".pyo")):
            ignored.add(name)
    ignored.update(shutil.ignore_patterns(*IGNORED_COPY_PATTERNS)(_, names))
    return ignored


def _sanitize_dsn(value: str) -> str:
    return re.sub(r"//([^:/@]+):([^@]+)@", r"//\1:***@", value)


def _friendly_database_error(source: str, error: Exception | str) -> str:
    message = str(error)
    lowered = message.lower()
    if "winerror 10061" in lowered or "connection refused" in lowered or "can't connect to mysql server" in lowered:
        return (
            "The database server refused the connection. For MySQL, start MySQL/XAMPP and make sure "
            "it is listening on localhost:3306, then try again."
        )
    if "access denied" in lowered:
        return "The database server answered, but the username or password was rejected."
    if "unknown database" in lowered or "database does not exist" in lowered:
        return "The database server answered, but this database name does not exist yet."
    if "no such file" in lowered or "unable to open database file" in lowered:
        return "The database file could not be opened from this machine."
    if not source.strip():
        return "No database source was provided."
    return message


def _credential_refs() -> list[str]:
    return sorted(
        key for key in os.environ
        if SECRET_KEY_RE.search(key) or key in {"NEO4J_URI", "NEO4J_USERNAME", "INDEX_DB_DSN"}
    )


def _safe_table_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", name).strip("_") or "table"


def _sanitize_tabular_data(frame: pd.DataFrame) -> pd.DataFrame:
    secret_columns = [col for col in frame.columns if SECRET_KEY_RE.search(str(col))]
    if secret_columns:
        frame = frame.drop(columns=secret_columns)
    return frame


def _copy_sqlalchemy_database_to_sqlite(
    source: str,
    sandbox_root: Path,
    max_rows_per_table: int = 1000,
) -> tuple[str | None, dict[str, Any]]:
    """Copy table/view samples from a SQLAlchemy source into sandbox SQLite."""
    from sqlalchemy import create_engine, inspect

    db_dir = sandbox_root / "database"
    db_dir.mkdir(parents=True, exist_ok=True)
    target = db_dir / "sandbox_database_snapshot.sqlite"
    if target.exists():
        target.unlink()

    copied_tables: list[dict[str, Any]] = []
    sqlite_conn = sqlite3.connect(target)
    try:
        source_engine = create_engine(source)
        try:
            inspector = inspect(source_engine)
            schema = inspector.default_schema_name or None
            preparer = source_engine.dialect.identifier_preparer

            table_names = [
                ("table", name) for name in inspector.get_table_names(schema=schema)
            ]
            table_names.extend(
                ("view", name) for name in inspector.get_view_names(schema=schema)
            )

            for kind, table_name in table_names:
                qualified_name = preparer.quote(table_name)
                if schema and source_engine.dialect.name not in {"sqlite", "mysql"}:
                    qualified_name = f"{preparer.quote_schema(schema)}.{qualified_name}"
                query = f"SELECT * FROM {qualified_name} LIMIT {int(max_rows_per_table)}"
                frame = pd.read_sql_query(query, source_engine)
                frame = _sanitize_tabular_data(frame)
                target_table = _safe_table_name(table_name)
                frame.to_sql(target_table, sqlite_conn, if_exists="replace", index=False)
                copied_tables.append(
                    {
                        "name": table_name,
                        "snapshot_table": target_table,
                        "kind": kind,
                        "rows_copied": int(len(frame)),
                        "columns": list(frame.columns),
                    }
                )
        finally:
            source_engine.dispose()
    except Exception:
        sqlite_conn.close()
        target.unlink(missing_ok=True)
        raise
    finally:
        try:
            sqlite_conn.close()
        except Exception:
            pass

    return f"sqlite:///{target.as_posix()}", {
        "source": _sanitize_dsn(source),
        "copied": True,
        "copied_to": str(target),
        "snapshot_url": f"sqlite:///{target.as_posix()}",
        "tables": copied_tables,
        "table_count": len(copied_tables),
        "rows_copied": sum(item["rows_copied"] for item in copied_tables),
        "max_rows_per_table": max_rows_per_table,
    }



def _parse_semicolon_connection_string(value: str) -> dict[str, str]:
    parts: dict[str, str] = {}
    for chunk in value.split(";"):
        if "=" not in chunk:
            continue
        key, raw_value = chunk.split("=", 1)
        key = key.strip().lower().replace(" ", "")
        parts[key] = raw_value.strip()
    return parts


def _project_prefers_mysql(root: Path) -> bool:
    for pattern in ("*.csproj", "Program.cs", "appsettings*.json"):
        for file_path in root.rglob(pattern):
            if any(part in {"bin", "obj", ".git"} for part in file_path.parts):
                continue
            try:
                text = file_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if "UseMySql" in text or "Pomelo.EntityFrameworkCore.MySql" in text or "MySqlConnector" in text:
                return True
    return False


def _sqlalchemy_url_from_project_config(value: str, provider_hint: str) -> str | None:
    parts = _parse_semicolon_connection_string(value)
    if not parts:
        return None

    host = parts.get("server") or parts.get("host") or parts.get("datasource") or parts.get("data source")
    database = parts.get("database") or parts.get("initialcatalog") or parts.get("initial catalog")
    username = parts.get("user") or parts.get("userid") or parts.get("user id") or parts.get("uid")
    password = parts.get("password") or parts.get("pwd") or ""
    port = parts.get("port") or ("3306" if provider_hint == "mysql" else "")

    if provider_hint == "mysql" and host and database and username:
        return (
            f"mysql+pymysql://{quote_plus(username)}:{quote_plus(password)}"
            f"@{host}:{port or '3306'}/{quote_plus(database)}"
        )
    return None


def _discover_project_config_database_sources(root: Path) -> list[dict[str, Any]]:
    detected: list[dict[str, Any]] = []
    provider_hint = "mysql" if _project_prefers_mysql(root) else ""
    for config_path in sorted(root.rglob("appsettings*.json")):
        if any(part in {"bin", "obj", ".git"} for part in config_path.parts):
            continue
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        connection_strings = config.get("ConnectionStrings")
        if not isinstance(connection_strings, dict):
            continue
        for name, raw_value in connection_strings.items():
            if not isinstance(raw_value, str) or not raw_value.strip():
                continue
            source_url = _sqlalchemy_url_from_project_config(raw_value, provider_hint)
            if not source_url:
                continue
            rel_path = config_path.relative_to(root)
            detected.append(
                {
                    "source": source_url,
                    "kind": "project_config",
                    "credential_ref": f"{rel_path}:ConnectionStrings:{name}",
                    "display_value": _sanitize_dsn(source_url),
                    "evidence_file": str(rel_path),
                    "usable": True,
                }
            )
    return detected


def probe_database_source(database_source: str) -> dict[str, Any]:
    """Check whether a database source can be opened before saving a proposal."""
    source = str(database_source or "").strip()
    if not source:
        return {"ok": False, "error": "No database source was provided.", "hint": "Enter a database source first."}

    if source.upper().startswith("ENV:"):
        env_name = source.split(":", 1)[1].strip()
        env_value = os.environ.get(env_name, "")
        if not env_value:
            return {
                "ok": False,
                "source": source,
                "credential_ref": env_name,
                "error": f"Environment variable {env_name} is not set.",
                "hint": f"Set {env_name}, then try again.",
            }
        source = env_value

    maybe_path = Path(source).expanduser()
    if maybe_path.exists() and maybe_path.is_file():
        try:
            conn = sqlite3.connect(f"file:{maybe_path.resolve().as_posix()}?mode=ro", uri=True)
            try:
                rows = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table', 'view') ORDER BY name"
                ).fetchall()
            finally:
                conn.close()
            return {
                "ok": True,
                "source": str(maybe_path.resolve()),
                "kind": "sqlite_file",
                "table_count": len(rows),
                "tables": [row[0] for row in rows[:20]],
            }
        except Exception as exc:
            return {
                "ok": False,
                "source": str(maybe_path),
                "error": str(exc),
                "hint": _friendly_database_error(source, exc),
            }

    try:
        from sqlalchemy import create_engine, inspect

        connect_args: dict[str, Any] = {}
        if source.startswith("mysql"):
            connect_args["connect_timeout"] = 5
        engine = create_engine(source, pool_pre_ping=True, connect_args=connect_args)
        try:
            with engine.connect() as conn:
                inspector = inspect(conn)
                tables = inspector.get_table_names()
                views = inspector.get_view_names()
        finally:
            engine.dispose()
        return {
            "ok": True,
            "source": _sanitize_dsn(source),
            "kind": "sqlalchemy",
            "table_count": len(tables),
            "view_count": len(views),
            "tables": tables[:20],
            "views": views[:20],
        }
    except Exception as exc:
        return {
            "ok": False,
            "source": _sanitize_dsn(source),
            "error": str(exc),
            "hint": _friendly_database_error(source, exc),
        }

def discover_database_sources(project_path: str) -> dict[str, Any]:
    """Detect database credentials and local database files without exposing secrets."""
    detected: list[dict[str, Any]] = []
    for name in DATABASE_ENV_CANDIDATES:
        value = os.environ.get(name)
        if not value:
            continue
        detected.append(
            {
                "source": f"ENV:{name}",
                "kind": "environment",
                "credential_ref": name,
                "display_value": _sanitize_dsn(value),
                "usable": True,
            }
        )

    root = Path(project_path).expanduser()
    if root.exists():
        detected.extend(_discover_project_config_database_sources(root))
        for db_file in sorted(root.rglob("*")):
            if db_file.is_file() and db_file.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
                if any(part in IGNORED_COPY_PATTERNS for part in db_file.parts):
                    continue
                detected.append(
                    {
                        "source": str(db_file.resolve()),
                        "kind": "local_file",
                        "credential_ref": None,
                        "display_value": db_file.name,
                        "usable": True,
                    }
                )

    graph_refs = [
        {
            "source": f"ENV:{name}",
            "kind": "graph_credential",
            "credential_ref": name,
            "display_value": _sanitize_dsn(os.environ.get(name, "")),
            "usable": False,
        }
        for name in ("NEO4J_URI", "NEO4J_USERNAME", "NEO4J_DATABASE")
        if os.environ.get(name)
    ]

    selected = next((item["source"] for item in detected if item.get("usable")), "")
    return {
        "selected_source": selected,
        "detected_sources": detected,
        "graph_credentials": graph_refs,
    }


def _copy_database_source(database_source: str, sandbox_root: Path) -> tuple[str | None, dict[str, Any] | None]:
    if not database_source.strip():
        return None, None

    source = database_source.strip()
    db_dir = sandbox_root / "database"
    db_dir.mkdir(parents=True, exist_ok=True)

    if source.upper().startswith("ENV:"):
        env_name = source.split(":", 1)[1].strip()
        value = os.environ.get(env_name, "")
        if not value:
            return None, {"source": source, "error": f"Environment variable {env_name} is not set."}
        try:
            snapshot_url, copy_info = _copy_sqlalchemy_database_to_sqlite(value, sandbox_root)
            copy_info["source"] = f"ENV:{env_name}"
            copy_info["credential_ref"] = env_name
            return snapshot_url, copy_info
        except Exception as exc:
            return None, {
                "source": f"ENV:{env_name}",
                "credential_ref": env_name,
                "copied": False,
                "error": _friendly_database_error(value, exc),
                "raw_error": str(exc),
            }

    maybe_path = Path(source).expanduser()
    if maybe_path.exists() and maybe_path.is_file():
        target = db_dir / maybe_path.name
        shutil.copy2(maybe_path, target)
        return f"sqlite:///{target.as_posix()}", {
            "source": str(maybe_path.resolve()),
            "copied_to": str(target),
            "copied": True,
            "credential_ref": None,
        }

    try:
        return _copy_sqlalchemy_database_to_sqlite(source, sandbox_root)
    except Exception as exc:
        return None, {
            "source": _sanitize_dsn(source),
            "copied": False,
            "credential_ref": "connection string supplied at runtime",
            "error": _friendly_database_error(source, exc),
            "raw_error": str(exc),
        }


def _detect_validation_command(sandbox_source: Path) -> str | None:
    if (sandbox_source / "package.json").exists():
        try:
            pkg = json.loads((sandbox_source / "package.json").read_text(encoding="utf-8"))
        except Exception:
            pkg = {}
        if pkg.get("scripts", {}).get("test"):
            return "npm test"
    if (sandbox_source / "Cargo.toml").exists():
        return "cargo test"

    py_files = [
        path for path in sandbox_source.rglob("*.py")
        if ".venv" not in path.parts and "__pycache__" not in path.parts
    ]
    test_files = [path for path in py_files if path.name.startswith("test_") or "tests" in path.parts]
    if test_files:
        return f'"{sys.executable}" -m pytest --tb=short -q'
    if py_files:
        return f'"{sys.executable}" -m py_compile ' + " ".join(f'"{path}"' for path in py_files[:40])
    return None


def _run_validation(sandbox_source: Path, command: str | None = None) -> dict[str, Any]:
    cmd = command or _detect_validation_command(sandbox_source)
    if not cmd:
        return {
            "status": "skipped",
            "command": None,
            "exit_code": None,
            "duration_ms": 0,
            "stdout": "",
            "stderr": "No validation command was detected.",
        }

    start = time.time()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(sandbox_source),
            shell=True,
            capture_output=True,
            text=True,
            timeout=180,
            env={**os.environ, "PYTHONPATH": str(sandbox_source)},
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "status": "fail",
            "command": cmd,
            "exit_code": None,
            "duration_ms": 180000,
            "stdout": (exc.stdout or "")[:4000],
            "stderr": "Validation command timed out after 180 seconds.",
        }

    return {
        "status": "success" if proc.returncode == 0 else "fail",
        "command": cmd,
        "exit_code": proc.returncode,
        "duration_ms": round((time.time() - start) * 1000),
        "stdout": proc.stdout[-4000:],
        "stderr": proc.stderr[-4000:],
    }


def _summarize_code_architecture(system: Any) -> dict[str, Any]:
    label_counts = Counter(node.label for node in system.code_nodes)
    rel_counts = Counter(rel.rel_type for rel in system.code_relationships)

    def nodes(label: str, limit: int = 8) -> list[dict[str, Any]]:
        return [
            {
                "id": node.id,
                "name": node.name,
                "source_path": node.source_path,
                **{
                    key: value for key, value in node.properties.items()
                    if key in {"route_path", "method", "storage_type", "integration_type", "flow_type"}
                },
            }
            for node in system.code_nodes
            if node.label == label
        ][:limit]

    return {
        "counts": dict(sorted(label_counts.items())),
        "relationships": dict(sorted(rel_counts.items())),
        "routes": nodes("Route"),
        "business_flows": nodes("BusinessFlow"),
        "datastores": nodes("DataStore"),
        "integrations": nodes("Integration"),
        "risks": nodes("Risk"),
    }


def _communication_rules(code_summary: dict[str, Any], db_connectors: list[dict[str, Any]]) -> list[dict[str, str]]:
    rules = [
        {
            "name": "Sandbox isolation",
            "rule": "The agent copies source and local database files into an isolated sandbox before testing changes.",
        },
        {
            "name": "Credential reference only",
            "rule": "Secrets are not copied into sandbox payloads; approved architecture keeps environment-variable references.",
        },
        {
            "name": "Connector boundary",
            "rule": "Project code talks to data through explicit connector units with immutable input/output contracts.",
        },
        {
            "name": "Admin approval gate",
            "rule": "A tested architecture proposal cannot replace active architecture records until an admin approves it.",
        },
    ]
    if code_summary.get("routes"):
        rules.append({
            "name": "Route entrypoints",
            "rule": "Routes are treated as external entrypoints and may only call service/function primitives, not raw storage directly.",
        })
    if code_summary.get("datastores") or db_connectors:
        rules.append({
            "name": "Data access",
            "rule": "Functions and services read/write database tables through named SQL/CSV connector records with schema metadata.",
        })
    if code_summary.get("risks"):
        rules.append({
            "name": "Risk escalation",
            "rule": "Risk primitives require review or validation before the agent can propose executable changes touching them.",
        })
    return rules


def build_architecture_proposal(
    source_path: str,
    project_id: str,
    project_name: str,
    sandbox_home: str,
    database_source: str = "",
    validation_command: str | None = None,
    replacement_mode: str = "merge",
    credential_refs: list[str] | None = None,
) -> dict[str, Any]:
    """Copy project/database into a sandbox, analyze it, run validation, and return a proposal payload."""
    source_root = Path(source_path).expanduser().resolve()
    if not source_root.exists() or not source_root.is_dir():
        raise ValueError(f"Project path does not exist or is not a folder: {source_path}")

    proposal_id = f"arch_{_slug(project_name)}_{uuid.uuid4().hex[:8]}"
    sandbox_root = Path(sandbox_home).expanduser().resolve() / proposal_id
    sandbox_source = sandbox_root / "project"
    sandbox_root.mkdir(parents=True, exist_ok=True)

    shutil.copytree(source_root, sandbox_source, ignore=_safe_copy_ignore)
    db_analysis_source, db_copy = _copy_database_source(database_source, sandbox_root)

    sandbox_project_id = f"{project_id}_sandbox_{proposal_id[-8:]}"
    code_system = CodebaseAnalyzer(
        str(sandbox_source),
        project_name=f"{project_name} sandbox",
        project_id=sandbox_project_id,
    ).discover()
    code_summary = _summarize_code_architecture(code_system)

    db_connectors: list[dict[str, Any]] = []
    db_error = db_copy.get("error") if isinstance(db_copy, dict) else None
    if db_analysis_source:
        try:
            db_system = DBIndexer(db_analysis_source).discover()
            db_connectors = [
                {
                    "id": connector.id,
                    "name": connector.name,
                    "type": connector.type,
                    "description": connector.description,
                    "version": connector.version,
                }
                for connector in db_system.connectors
            ]
        except Exception as exc:
            db_error = str(exc)

    validation = _run_validation(sandbox_source, validation_command)
    rules = _communication_rules(code_summary, db_connectors)
    test_passed = validation["status"] == "success" and not db_error

    return {
        "proposal_id": proposal_id,
        "project_id": project_id,
        "project_name": project_name,
        "status": "tested" if test_passed else "needs_fix",
        "replacement_mode": replacement_mode if replacement_mode in {"merge", "replace"} else "merge",
        "sandbox": {
            "root": str(sandbox_root),
            "project_copy": str(sandbox_source),
            "database_copy": db_copy,
            "excluded": sorted(SECRET_FILE_NAMES | {".git", ".venv", "node_modules", "dist", "build"}),
        },
        "credential_refs": sorted(set(_credential_refs() + (credential_refs or []))),
        "code_architecture": code_summary,
        "database_connectors": db_connectors,
        "database_error": db_error,
        "communication_rules": rules,
        "validation": validation,
        "summary": {
            "title": f"{project_name} tested architecture proposal",
            "code_nodes": sum(code_summary["counts"].values()),
            "relationship_types": len(code_summary["relationships"]),
            "connectors": len(db_connectors),
            "rules": len(rules),
            "tested": test_passed,
        },
    }


def build_database_only_architecture_proposal(
    project_id: str,
    project_name: str,
    sandbox_home: str,
    database_source: str,
    replacement_mode: str = "merge",
    credential_refs: list[str] | None = None,
) -> dict[str, Any]:
    """Analyze an externally reachable database when project source is unavailable.

    This does not claim to copy or test project code. It validates the database
    connection/schema, proposes connector rules, and records that full code
    sandboxing remains blocked until source is connected.
    """
    proposal_id = f"dbarch_{_slug(project_name)}_{uuid.uuid4().hex[:8]}"
    sandbox_root = Path(sandbox_home).expanduser().resolve() / proposal_id
    sandbox_root.mkdir(parents=True, exist_ok=True)

    db_analysis_source, db_copy = _copy_database_source(database_source, sandbox_root)
    db_connectors: list[dict[str, Any]] = []
    db_error = db_copy.get("error") if isinstance(db_copy, dict) else None
    if not db_analysis_source:
        db_error = db_error or "No database source was provided."
    else:
        try:
            db_system = DBIndexer(db_analysis_source).discover()
            db_connectors = [
                {
                    "id": connector.id,
                    "name": connector.name,
                    "type": connector.type,
                    "description": connector.description,
                    "version": connector.version,
                }
                for connector in db_system.connectors
            ]
        except Exception as exc:
            db_error = str(exc)

    code_summary = {
        "counts": {},
        "relationships": {},
        "routes": [],
        "business_flows": [],
        "datastores": [],
        "integrations": [],
        "risks": [],
    }
    rules = _communication_rules(code_summary, db_connectors)
    rules.append(
        {
            "name": "Project source required for code tests",
            "rule": "Database-only proposals validate schema/connectors; project code changes cannot be tested until source is connected.",
        }
    )

    test_passed = db_error is None
    validation = {
        "status": "success" if test_passed else "fail",
        "command": "database schema inspection",
        "exit_code": 0 if test_passed else 1,
        "duration_ms": 0,
        "stdout": f"Discovered {len(db_connectors)} database connector(s)." if test_passed else "",
        "stderr": db_error or "",
    }

    return {
        "proposal_id": proposal_id,
        "project_id": project_id,
        "project_name": project_name,
        "status": "tested" if test_passed else "needs_fix",
        "replacement_mode": replacement_mode if replacement_mode in {"merge", "replace"} else "merge",
        "sandbox": {
            "root": str(sandbox_root),
            "project_copy": None,
            "database_copy": db_copy,
            "excluded": sorted(SECRET_FILE_NAMES | {".git", ".venv", "node_modules", "dist", "build"}),
        },
        "credential_refs": sorted(set(_credential_refs() + (credential_refs or []))),
        "code_architecture": code_summary,
        "database_connectors": db_connectors,
        "database_error": db_error,
        "communication_rules": rules,
        "validation": validation,
        "limitations": [
            "Project source folder was not available, so code was not copied or executed.",
            "This proposal validates database schema/connectors only.",
        ],
        "summary": {
            "title": f"{project_name} database connector proposal",
            "code_nodes": 0,
            "relationship_types": 0,
            "connectors": len(db_connectors),
            "rules": len(rules),
            "tested": test_passed,
        },
    }
