"""Read-only CSV connector."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.connectors.base import BaseConnector, ConnectorInput, ConnectorOutput


class CSVConnector(BaseConnector):
    connector_id = "CSV_Connector"
    name = "CSV Connector"
    description = "Read-only connector for inspecting CSV files inside the sandbox."

    def inspect(self, connector_input: ConnectorInput) -> ConnectorOutput:
        path = Path(connector_input.source).expanduser().resolve()
        frame = pd.read_csv(path, nrows=connector_input.limit)
        schema = [
            {"name": column, "type": str(dtype)}
            for column, dtype in frame.dtypes.items()
        ]
        return ConnectorOutput(
            connector_id=self.connector_id,
            status="success",
            rows=frame.to_dict(orient="records"),
            data_schema=schema,
            metadata={
                "source": str(path),
                "row_preview_count": len(frame),
                "side_effects": "none",
            },
        )
