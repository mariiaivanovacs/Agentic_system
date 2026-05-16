from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RetrievedContext:
    """Live graph context used by Planner, Generator, and Critic."""

    goal: str
    industry: str
    industry_stats: list[dict[str, Any]] = field(default_factory=list)
    failure_patterns: list[dict[str, Any]] = field(default_factory=list)
    success_patterns: list[dict[str, Any]] = field(default_factory=list)
    active_flows: list[dict[str, Any]] = field(default_factory=list)
    available_skills: list[dict[str, Any]] = field(default_factory=list)
    available_connectors: list[dict[str, Any]] = field(default_factory=list)
    infra_status: dict[str, Any] = field(default_factory=dict)
    learning_events: list[dict[str, Any]] = field(default_factory=list)
    website_entities: list[dict[str, Any]] = field(default_factory=list)
    software_nodes: list[dict[str, Any]] = field(default_factory=list)
    baseline_score: float = 5.0


@dataclass
class FlowProposal:
    flow_yaml: str
    reasoning_trace: str
    skills_used: list[str]
    attempts: int = 1
