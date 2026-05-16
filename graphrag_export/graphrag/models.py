"""
Shared data contracts for the Stream 2 GraphRAG pipeline.

These are pure data classes with no external dependencies — safe to import anywhere.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RetrievedContext:
    """All graph context needed by the prompt engine and generator."""
    success_patterns: list[dict]   # high-score Company→Mentor matches
    failure_patterns: list[dict]   # low-score Company→Mentor matches
    available_skills: list[dict]   # Skill nodes from Graph B
    infra_status: dict = field(default_factory=dict)  # Server loads from Graph B


@dataclass
class FlowProposal:
    """Output of the GraphRAG generator — a validated flow + its provenance."""
    flow_yaml: str
    reasoning_trace: str
    skills_used: list[str]
    attempts: int = 1
