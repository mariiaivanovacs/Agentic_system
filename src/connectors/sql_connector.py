"""Read-only SQL connector."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, event, inspect, text

from src.connectors.base import BaseConnector, ConnectorInput, ConnectorOutput


class SQLConnector(BaseConnector):
    connector_id = "SQL_Connector"
    name = "SQL Connector"
    description = "Read-only connector for inspecting SQL schemas and SELECT queries inside the sandbox."

    def inspect(self, connector_input: ConnectorInput) -> ConnectorOutput:
        sqlite_path = self._sqlite_database_path(connector_input.source)
        if sqlite_path and not sqlite_path.exists():
            raise FileNotFoundError(f"SQLite database does not exist: {sqlite_path}")

        engine = create_engine(connector_input.source)
        if connector_input.source.lower().startswith("sqlite"):
            @event.listens_for(engine, "connect")
            def _set_sqlite_readonly(dbapi_connection, _connection_record):
                dbapi_connection.execute("PRAGMA query_only = ON")

        try:
            inspector = inspect(engine)
            schema: list[dict] = []
            for table_name in inspector.get_table_names():
                columns = inspector.get_columns(table_name)
                schema.append(
                    {
                        "table": table_name,
                        "columns": [
                            {"name": column["name"], "type": str(column["type"])}
                            for column in columns
                        ],
                    }
                )

            rows: list[dict] = []
            if connector_input.query:
                query = connector_input.query.strip()
                query_start = query.lower().lstrip()
                if not query_start.startswith(("select", "with")):
                    raise ValueError("SQLConnector only permits read-only SELECT statements.")
                with engine.connect() as conn:
                    result = conn.execute(text(query))
                    rows = [dict(row._mapping) for row in result.fetchmany(connector_input.limit)]

            return ConnectorOutput(
                connector_id=self.connector_id,
                status="success",
                rows=rows,
                data_schema=schema,
                metadata={
                    "table_count": len(schema),
                    "row_preview_count": len(rows),
                    "side_effects": "none",
                },
            )
        finally:
            engine.dispose()

    @staticmethod
    def _sqlite_database_path(source: str) -> Path | None:
        lowered = source.lower()
        if not lowered.startswith(("sqlite:///", "sqlite+aiosqlite:///")):
            return None
        raw_path = source.split(":///", 1)[1]
        if raw_path in (":memory:", ""):
            return None
        return Path(raw_path).expanduser()
