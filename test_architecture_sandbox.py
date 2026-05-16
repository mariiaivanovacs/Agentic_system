from __future__ import annotations

from pathlib import Path
import sqlite3

from src.agents.architecture_sandbox import build_architecture_proposal
from src.agents.architecture_sandbox import build_database_only_architecture_proposal
from src.agents.architecture_sandbox import discover_database_sources
from src.agents.architecture_sandbox import probe_database_source
from src.agents.architecture_sandbox import resolve_project_source_path


def test_architecture_sandbox_copies_analyzes_and_tests(tmp_path):
    project = tmp_path / "sample_app"
    project.mkdir()
    (project / ".env").write_text("API_TOKEN=should-not-copy\n", encoding="utf-8")
    (project / "app.py").write_text(
        """
from fastapi import FastAPI

app = FastAPI()

@app.get("/health")
def health():
    return {"ok": True}
""".strip(),
        encoding="utf-8",
    )

    payload = build_architecture_proposal(
        source_path=str(project),
        project_id="project_sample",
        project_name="Sample App",
        sandbox_home=str(tmp_path / "sandboxes"),
        validation_command=None,
    )

    sandbox_copy = Path(payload["sandbox"]["project_copy"])
    assert payload["validation"]["status"] == "success"
    assert sandbox_copy.exists()
    assert not (sandbox_copy / ".env").exists()
    assert payload["summary"]["code_nodes"] > 0
    assert payload["communication_rules"]


def test_database_detection_prefers_env_and_masks_secret(tmp_path, monkeypatch):
    project = tmp_path / "sample_app"
    project.mkdir()
    (project / "local.sqlite").write_text("", encoding="utf-8")
    monkeypatch.setenv("INDEX_DB_DSN", "postgresql://user:super-secret@example.test/app")

    detected = discover_database_sources(str(project))

    assert detected["selected_source"] == "ENV:INDEX_DB_DSN"
    assert detected["detected_sources"][0]["credential_ref"] == "INDEX_DB_DSN"
    assert "super-secret" not in detected["detected_sources"][0]["display_value"]
    assert any(item["kind"] == "local_file" for item in detected["detected_sources"])


def test_project_path_resolver_accepts_existing_folder(tmp_path):
    project = tmp_path / "sample_app"
    project.mkdir()

    resolved = resolve_project_source_path(str(project))

    assert resolved["exists"] is True
    assert Path(resolved["resolved_path"]) == project.resolve()


def test_database_only_architecture_proposal_reads_schema(tmp_path):
    db_path = tmp_path / "external.sqlite"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT)")
    conn.execute("INSERT INTO users (username) VALUES (?)", ("alice",))
    conn.commit()
    conn.close()

    payload = build_database_only_architecture_proposal(
        project_id="project_sample",
        project_name="Sample App",
        sandbox_home=str(tmp_path / "sandboxes"),
        database_source=f"sqlite:///{db_path.as_posix()}",
        credential_refs=["external_db_sample"],
    )

    assert payload["validation"]["status"] == "success"
    assert payload["database_connectors"]
    assert "external_db_sample" in payload["credential_refs"]
    assert payload["sandbox"]["project_copy"] is None
    db_copy = payload["sandbox"]["database_copy"]
    assert db_copy["copied"] is True
    assert Path(db_copy["copied_to"]).exists()
    assert db_copy["rows_copied"] == 1
    assert payload["limitations"]


def test_database_detection_reads_dotnet_mysql_appsettings(tmp_path):
    project = tmp_path / "dotnet_app"
    project.mkdir()
    (project / "Program.cs").write_text("options.UseMySql(connectionString);", encoding="utf-8")
    (project / "appsettings.json").write_text(
        '{"ConnectionStrings":{"DefaultConnection":"server=localhost;port=3306;database=ilow_learning_system;user=root;password=;"}}',
        encoding="utf-8",
    )

    detected = discover_database_sources(str(project))

    assert detected["selected_source"].startswith("mysql+pymysql://root:")
    assert "ilow_learning_system" in detected["selected_source"]
    assert detected["detected_sources"][0]["kind"] == "project_config"
    assert detected["detected_sources"][0]["credential_ref"] == "appsettings.json:ConnectionStrings:DefaultConnection"


def test_database_probe_reports_closed_mysql_cleanly():
    result = probe_database_source("mysql+pymysql://root:@127.0.0.1:1/missing")

    assert result["ok"] is False
    assert "refused" in result.get("hint", "").lower() or "database server" in result.get("hint", "").lower()

