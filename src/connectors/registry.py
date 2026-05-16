"""Connector registry matching Graph B connector IDs."""

from __future__ import annotations

from src.connectors.base import BaseConnector
from src.connectors.csv_connector import CSVConnector
from src.connectors.sql_connector import SQLConnector


CONNECTOR_REGISTRY: dict[str, type[BaseConnector]] = {
    CSVConnector.connector_id: CSVConnector,
    SQLConnector.connector_id: SQLConnector,
}


def get_connector(connector_id: str) -> BaseConnector:
    try:
        connector_cls = CONNECTOR_REGISTRY[connector_id]
    except KeyError as exc:
        raise KeyError(f"Unknown connector ID: {connector_id}") from exc
    return connector_cls()
