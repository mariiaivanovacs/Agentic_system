from __future__ import annotations

import py_compile
import csv
import sqlite3
import tempfile
from pathlib import Path

from src.config.schema_validator import SchemaValidator
from src.connectors.base import ConnectorInput
from src.connectors.registry import get_connector
from src.indexer.codebase_analyzer import CodebaseAnalyzer, discover_source_files, stable_project_id


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_safe_discovery_ignores_dependencies_and_secrets() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        _write(root / "app.py", "def public() -> str:\n    return 'ok'\n")
        _write(root / "node_modules" / "lib.js", "function ignored() {}\n")
        _write(root / ".env", "SECRET=value\n")

        files = [p.relative_to(root).as_posix() for p in discover_source_files(root)]

        assert "app.py" in files
        assert "node_modules/lib.js" not in files
        assert ".env" not in files


def test_analyzer_extracts_project_scoped_code_primitives() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        _write(
            root / "server.py",
            """
class CampaignModel:
    pass

def create_campaign(name: str) -> str:
    return name
""",
        )
        _write(
            root / "routes.ts",
            """
import Stripe from "stripe";
localStorage.setItem("campaign", "1");
router.post("/campaigns", async () => {});
const listCampaigns = () => [];
""",
        )

        system = CodebaseAnalyzer(str(root), project_name="Tmp App").discover()
        labels = {node.label for node in system.code_nodes}
        project_id = stable_project_id(root)

        assert {"Project", "Repository", "File", "Function", "Route", "Integration", "DataStore", "DatabaseModel", "BusinessFlow", "FlowStep"}.issubset(labels)
        assert all(node.project_id == project_id for node in system.code_nodes)
        assert all(node.scan_id for node in system.code_nodes)
        assert any(rel.rel_type == "PROJECT_HAS_REPOSITORY" for rel in system.code_relationships)
        assert any(rel.rel_type == "FILE_DEFINES_ROUTE" for rel in system.code_relationships)
        assert any(rel.rel_type == "FILE_USES_DATASTORE" for rel in system.code_relationships)
        assert any(rel.rel_type == "HAS_BUSINESS_FLOW" for rel in system.code_relationships)
        assert any(rel.rel_type == "HAS_STEP" for rel in system.code_relationships)
        assert any(rel.rel_type == "USES_PRIMITIVE" for rel in system.code_relationships)
        flow_steps = [node for node in system.code_nodes if node.label == "FlowStep"]
        assert flow_steps
        assert all("order" in node.properties for node in flow_steps)
        assert all("evidence" in node.properties for node in flow_steps)
        assert system.skills


def test_analyzer_ids_are_stable_for_repeated_scans() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        _write(root / "app.py", "def score_match(x: int) -> int:\n    return x\n")

        first = CodebaseAnalyzer(str(root)).discover()
        second = CodebaseAnalyzer(str(root)).discover()

        assert sorted(node.id for node in first.code_nodes) == sorted(node.id for node in second.code_nodes)
        assert first.metadata["scan_id"] == second.metadata["scan_id"]


def test_schema_knows_software_graph_labels_and_relationships() -> None:
    validator = SchemaValidator()
    for label in ["Project", "Repository", "File", "Function", "Route", "Integration", "DataStore", "Risk", "BusinessFlow", "FlowStep"]:
        validator.validate_node(
            label,
            {
                "id": "id",
                "name": "name",
                "project_id": "project",
                "scan_id": "scan",
                "source_path": "path",
                "confidence": 1.0,
            },
        )
    validator.validate_edge("PROJECT_HAS_REPOSITORY", "Project", "Repository")
    validator.validate_edge("REPOSITORY_HAS_FILE", "Repository", "File")
    validator.validate_edge("FILE_DEFINES_FUNCTION", "File", "Function")
    validator.validate_edge("FILE_USES_DATASTORE", "File", "DataStore")
    validator.validate_edge("HAS_BUSINESS_FLOW", "Project", "BusinessFlow")
    validator.validate_edge("HAS_STEP", "BusinessFlow", "FlowStep")
    validator.validate_edge("ROUTE_CALLS_FUNCTION", "Route", "Function")
    validator.validate_edge("FUNCTION_READS_DATASTORE", "Function", "DataStore")


def test_connector_units_are_read_only_and_registered() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        csv_path = root / "sample.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["id", "name"])
            writer.writeheader()
            writer.writerow({"id": "1", "name": "A"})

        csv_output = get_connector("CSV_Connector").inspect(
            ConnectorInput(source=str(csv_path), limit=5)
        )
        assert csv_output.status == "success"
        assert csv_output.rows == [{"id": 1, "name": "A"}]

        db_path = root / "sample.db"
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO users (name) VALUES ('Ada')")
        conn.commit()
        conn.close()

        sql_output = get_connector("SQL_Connector").inspect(
            ConnectorInput(
                source=f"sqlite:///{db_path}",
                query="SELECT id, name FROM users",
                limit=5,
            )
        )
        assert sql_output.status == "success"
        assert sql_output.data_schema
        assert sql_output.rows == [{"id": 1, "name": "Ada"}]

        try:
            get_connector("SQL_Connector").inspect(
                ConnectorInput(source=f"sqlite:///{db_path}", query="DELETE FROM users", limit=5)
            )
        except ValueError as exc:
            assert "SELECT" in str(exc)
        else:
            raise AssertionError("SQL connector allowed a write query")


def test_key_files_compile() -> None:
    for file_name in [
        "src/indexer/codebase_analyzer.py",
        "src/indexer/graph_writer.py",
        "src/indexer/project_store.py",
        "src/connectors/base.py",
        "src/connectors/csv_connector.py",
        "src/connectors/sql_connector.py",
        "streamlit_app.py",
    ]:
        py_compile.compile(file_name, doraise=True)


if __name__ == "__main__":
    tests = [
        test_safe_discovery_ignores_dependencies_and_secrets,
        test_analyzer_extracts_project_scoped_code_primitives,
        test_analyzer_ids_are_stable_for_repeated_scans,
        test_schema_knows_software_graph_labels_and_relationships,
        test_connector_units_are_read_only_and_registered,
        test_key_files_compile,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print("All codebase analyzer tests passed.")
