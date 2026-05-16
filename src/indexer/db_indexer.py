"""
db_indexer.py — reads a database via SQLAlchemy and produces ConnectorSpec nodes.

Each table/view → one ConnectorSpec with column schema in description.

Usage:
    DBIndexer(source="postgresql://user:pass@host/db").discover()
    DBIndexer(source=os.getenv("INDEX_DB_DSN")).discover()
"""

import re
from src.indexer.base_indexer import BaseIndexer, ConnectorSpec, IndexedSystem


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9_]", "_", text.lower()).strip("_")


class DBIndexer(BaseIndexer):
    def discover(self) -> IndexedSystem:
        from sqlalchemy import create_engine, inspect

        engine = create_engine(self.source)
        inspector = inspect(engine)
        connectors: list[ConnectorSpec] = []

        schema_name = inspector.default_schema_name or "public"

        for table_name in inspector.get_table_names(schema=schema_name):
            columns = inspector.get_columns(table_name, schema=schema_name)
            col_summary = ", ".join(
                f"{c['name']}:{c['type']}" for c in columns[:10]
            )
            connector_id = f"db_{_slug(self.source.split('@')[-1])}_{_slug(table_name)}"
            connectors.append(ConnectorSpec(
                id=connector_id,
                name=table_name,
                type="database_table",
                description=f"Columns: {col_summary}",
                version="1.0",
            ))

        for view_name in inspector.get_view_names(schema=schema_name):
            connector_id = f"db_{_slug(self.source.split('@')[-1])}_view_{_slug(view_name)}"
            connectors.append(ConnectorSpec(
                id=connector_id,
                name=view_name,
                type="database_view",
                description=f"View: {view_name}",
                version="1.0",
            ))

        engine.dispose()

        return IndexedSystem(
            connectors=connectors,
            metadata={"source": self.source, "source_type": "db", "table_count": len(connectors)},
        )
