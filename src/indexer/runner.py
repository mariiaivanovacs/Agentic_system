"""
runner.py — CLI entry point for the System Indexer subsystem.

Usage:
    python -m src.indexer.runner --type openapi --source <url-or-file>
    python -m src.indexer.runner --type python  --source <path>
    python -m src.indexer.runner --type db      --source <dsn>  # or reads INDEX_DB_DSN env var
"""

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def main():
    parser = argparse.ArgumentParser(description="EcoLink System Indexer")
    parser.add_argument("--type", required=True, choices=["openapi", "python", "db"],
                        help="Indexer type")
    parser.add_argument("--source", default=None,
                        help="Source URL, file path, or DSN (db falls back to INDEX_DB_DSN env var)")
    args = parser.parse_args()

    source = args.source
    if args.type == "db" and source is None:
        source = os.getenv("INDEX_DB_DSN")
        if not source:
            print("Error: --source or INDEX_DB_DSN env var required for db indexer", file=sys.stderr)
            sys.exit(1)

    if args.type == "openapi":
        from src.indexer.openapi_indexer import OpenAPIIndexer
        indexer = OpenAPIIndexer(source=source)
    elif args.type == "python":
        from src.indexer.python_indexer import PythonIndexer
        indexer = PythonIndexer(source=source)
    else:
        from src.indexer.db_indexer import DBIndexer
        indexer = DBIndexer(source=source)

    print(f"Discovering from {args.type} source: {source}")
    system = indexer.discover()
    print(f"Found: {len(system.connectors)} connector(s), "
          f"{len(system.skills)} skill(s), "
          f"{len(system.flows)} flow(s)")

    from src.indexer.graph_writer import GraphWriter
    writer = GraphWriter()
    run_id = writer.write(system)
    print(f"Written to Neo4j — IndexRun ID: {run_id}")


if __name__ == "__main__":
    main()
