"""Read-only SQL connector."""

from __future__ import annotations

from sqlalchemy import create_engine, inspect, text

from src.connectors.base import BaseConnector, ConnectorInput, ConnectorOutput


class SQLConnector(BaseConnector):
    connector_id = "SQL_Connector"
    name = "SQL Connector"
    description = "Read-only connector for inspecting SQL schemas and SELECT queries inside the sandbox."

    def inspect(self, connector_input: ConnectorInput) -> ConnectorOutput:
        engine = create_engine(connector_input.source)
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
                if not query.lower().startswith("select"):
                    raise ValueError("SQLConnector only permits SELECT statements.")
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
