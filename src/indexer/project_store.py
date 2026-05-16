"""Project metadata helpers for permission-first codebase analysis."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from neo4j import GraphDatabase, Query

from src.indexer.codebase_analyzer import stable_project_id


def _driver():
    return GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"]),
    )


def _db() -> str:
    return os.environ.get("NEO4J_DATABASE", "neo4j")


def _timeout() -> float:
    return float(os.environ.get("NEO4J_QUERY_TIMEOUT_SECONDS", "10"))


def approve_project(repo_path: str, name: str | None = None) -> dict[str, Any]:
    root = Path(repo_path).expanduser().resolve()
    project_id = stable_project_id(root)
    now = datetime.now(timezone.utc).isoformat()
    project_name = name or root.name
    props = {
        "id": project_id,
        "project_id": project_id,
        "name": project_name,
        "repo_path": str(root),
        "source_path": str(root),
        "permission_status": "approved",
        "analysis_status": "approved",
        "scan_id": "permission_pending",
        "confidence": 1.0,
        "updated_at": now,
    }
    driver = _driver()
    try:
        with driver.session(database=_db()) as session:
            row = session.run(
                Query(
                    """
                    MERGE (p:Project {id: $id})
                    ON CREATE SET p.created_at = $now
                    SET p += $props
                    RETURN p.id AS project_id,
                           p.name AS name,
                           p.repo_path AS repo_path,
                           p.permission_status AS permission_status,
                           p.analysis_status AS analysis_status,
                           p.last_scan_id AS last_scan_id
                    """,
                    timeout=_timeout(),
                ),
                {"id": project_id, "now": now, "props": props},
            ).single()
            return dict(row) if row else props
    finally:
        driver.close()


def mark_project_status(project_id: str, analysis_status: str, last_scan_id: str | None = None) -> None:
    updates = {"analysis_status": analysis_status, "updated_at": datetime.now(timezone.utc).isoformat()}
    if last_scan_id:
        updates["last_scan_id"] = last_scan_id
    driver = _driver()
    try:
        with driver.session(database=_db()) as session:
            session.run(
                Query("MATCH (p:Project {id: $id}) SET p += $updates", timeout=_timeout()),
                {"id": project_id, "updates": updates},
            )
    finally:
        driver.close()
