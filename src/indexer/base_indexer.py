"""
base_indexer.py — abstract base class for all system indexers.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class ConnectorSpec:
    id: str
    name: str
    type: str
    description: str
    version: str = "1.0"
    status: str = "active"
    error_rate: float = 0.0


@dataclass
class SkillSpec:
    id: str
    name: str
    description: str
    language: str = "python"
    performance_score: float = 5.0
    avg_execution_ms: float = 100.0


@dataclass
class FlowSpec:
    id: str
    name: str
    description: str
    status: str = "active"
    avg_outcome_score: float = 0.0


@dataclass
class IndexedSystem:
    connectors: list[ConnectorSpec] = field(default_factory=list)
    skills: list[SkillSpec] = field(default_factory=list)
    flows: list[FlowSpec] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


class BaseIndexer(ABC):
    def __init__(self, source: str):
        self.source = source

    @abstractmethod
    def discover(self) -> IndexedSystem:
        """Read the source and return discovered connectors, skills, and flows."""
