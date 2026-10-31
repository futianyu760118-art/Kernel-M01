"""RouteDecision helpers for the hybrid assistant router.

This module keeps the new three-track contracts near intent_router while the
actual dataclasses live in schemas.py for broad reuse and compatibility.
"""

from __future__ import annotations

from ..schemas import (
    GenerationPlan,
    RouteDecision,
    ToolCallPlan,
    route_decision_to_router_result,
    router_result_to_route_decision,
)

__all__ = [
    'GenerationPlan',
    'RouteDecision',
    'ToolCallPlan',
    'route_decision_to_router_result',
    'router_result_to_route_decision',
]
