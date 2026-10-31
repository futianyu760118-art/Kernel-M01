"""State objects used by the HRM Agent Kernel run loop."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..schemas import AssistantContext, ExecutionResult, RouteDecision


@dataclass
class KernelNodeEvent:
    """A traceable lifecycle event for one Agent Kernel node."""

    node: str
    status: str
    detail: Dict[str, Any] = field(default_factory=dict)
    latency_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            'node': self.node,
            'status': self.status,
            'latency_ms': round(self.latency_ms, 1),
        }
        if self.detail:
            payload['detail'] = dict(self.detail)
        return payload


@dataclass
class SlotReferenceResolution:
    """Observable slot/entity/reference result for one planned Skill."""

    slot_values: Dict[str, Any] = field(default_factory=dict)
    missing_slots: List[str] = field(default_factory=list)
    required_slots: List[str] = field(default_factory=list)
    optional_slots: List[str] = field(default_factory=list)
    reference_detected: bool = False
    workset_reference_detected: bool = False
    side_effect: bool = False
    planner_source: str = ''
    resolution_source: str = 'planner_guarded_slots'
    refiner: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'slot_values': dict(self.slot_values),
            'missing_slots': list(self.missing_slots),
            'required_slots': list(self.required_slots),
            'optional_slots': list(self.optional_slots),
            'reference_detected': self.reference_detected,
            'workset_reference_detected': self.workset_reference_detected,
            'side_effect': self.side_effect,
            'planner_source': self.planner_source,
            'resolution_source': self.resolution_source,
            'refiner': dict(self.refiner),
        }


@dataclass
class AgentKernelRunState:
    """Mutable state carried through one assistant request."""

    message: str
    ctx: AssistantContext
    started_at: float = field(default_factory=time.perf_counter)
    normalized_query: str = ''
    action_verb: Optional[str] = None
    understanding: Any = None
    candidates: List[Any] = field(default_factory=list)
    skill_provider_records: List[Any] = field(default_factory=list)
    plan: Any = None
    card: Any = None
    slot_reference: Optional[SlotReferenceResolution] = None
    decision: Optional[RouteDecision] = None
    playbook: Any = None
    permission_result: str = 'not_checked'
    trace: Dict[str, Any] = field(default_factory=dict)
    tool_outputs: List[Dict[str, Any]] = field(default_factory=list)
    result: Optional[ExecutionResult] = None
    node_events: List[KernelNodeEvent] = field(default_factory=list)

    def record_node(self, node: str, status: str, *, started_at: float, **detail: Any) -> None:
        clean_detail = {key: value for key, value in detail.items() if value is not None}
        self.node_events.append(
            KernelNodeEvent(
                node=node,
                status=status,
                detail=clean_detail,
                latency_ms=(time.perf_counter() - started_at) * 1000,
            )
        )

    def node_trace(self) -> List[Dict[str, Any]]:
        return [event.to_dict() for event in self.node_events]
