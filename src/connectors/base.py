"""Base classes for side-effect-free connector units."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ConnectorInput(BaseModel):
    """Immutable connector execution input."""

    model_config = ConfigDict(frozen=True)

    source: str
    query: str | None = None
    limit: int = Field(default=20, ge=1, le=500)
    options: dict[str, Any] = Field(default_factory=dict)


class ConnectorOutput(BaseModel):
    """Immutable connector execution output."""

    model_config = ConfigDict(frozen=True)

    connector_id: str
    status: str
    rows: list[dict[str, Any]] = Field(default_factory=list)
    data_schema: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class BaseConnector(ABC):
    """Read-only connector interface used by sandbox analysis."""

    connector_id: str
    name: str
    description: str

    @abstractmethod
    def inspect(self, connector_input: ConnectorInput) -> ConnectorOutput:
        """Return schema/sample data without mutating the source."""
