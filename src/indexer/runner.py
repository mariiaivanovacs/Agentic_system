"""
runner.py — CLI entry point for the System Indexer subsystem.

Usage:
    python -m src.indexer.runner --type openapi --source <url-or-file>
    python -m src.indexer.runner --type python  --source <path>
    python -m src.indexer.runner --type db      --source <dsn>  # or reads INDEX_DB_DSN env var
    python -m src.indexer.runner --type web     --source <url> --source-path <local-app-folder>
    python -m src.indexer.runner --type codebase --source <local-repo-path>
"""

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def main():
    parser = argparse.ArgumentParser(description="EcoLink System Indexer")
    parser.add_argument("--type", required=True, choices=["openapi", "python", "db", "web", "codebase"],
                        help="Indexer type")
    parser.add_argument("--source", default=None,
                        help="Source URL, file path, or DSN (db falls back to INDEX_DB_DSN env var)")
    parser.add_argument("--source-path", default=None,
                        help="Optional local source folder for web indexing")
    parser.add_argument("--depth", type=int, default=1,
                        help="Web crawl depth")
    parser.add_argument("--max-pages", type=int, default=30,
                        help="Web crawl max page count")
    parser.add_argument("--clear", action="store_true",
                        help="Clear existing web graph for this domain first")
    args = parser.parse_args()

    source = args.source
    if args.type == "db" and source is None:
        source = os.getenv("INDEX_DB_DSN")
        if not source:
            print("Error: --source or INDEX_DB_DSN env var required for db indexer", file=sys.stderr)
            sys.exit(1)
    if args.type == "codebase" and source is None:
        print("Error: --source local repository path is required for codebase indexer", file=sys.stderr)
        sys.exit(1)

    if args.type == "web":
        from src.indexer.web_indexer import crawl
        if not source:
            print("Error: --source URL is required for web indexer", file=sys.stderr)
            sys.exit(1)
        result = crawl(
            start_url=source,
            max_depth=args.depth,
            max_pages=args.max_pages,
            clear_existing=args.clear,
            source_path=args.source_path,
        )
        print(f"Written to Neo4j — WebSite domain: {result['domain']}")
        print(f"  WebPage nodes : {result['pages_written']}")
        print(f"  LINKS_TO edges: {result['edges_written']}")
        print(f"  WebEntity nodes: {result['entities_written']}")
        print(f"  Entity edges   : {result['entity_relationships_written']}")
        return

    if args.type == "codebase":
        from src.indexer.codebase_analyzer import CodebaseAnalyzer
        indexer = CodebaseAnalyzer(source=source)
    elif args.type == "openapi":
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
