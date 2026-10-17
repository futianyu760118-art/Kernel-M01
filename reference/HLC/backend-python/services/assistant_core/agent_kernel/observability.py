"""Trace construction and failure learning sinks for the HRM Agent Kernel."""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from ..guardrails import detect_write_intent
from ..schemas import AssistantContext, ExecutionResult
from ..skill_first.failure_samples import append_failure_sample, should_capture_failure
from ..skill_first.observability import append_route_trace, ensure_trace_fields


def base_trace(
    user_text: str,
    normalized_query: str,
    candidates: List[Any],
    ctx: AssistantContext,
    start: float,
    understanding: Optional[Any] = None,
) -> Dict[str, Any]:
    return ensure_trace_fields({
        'request_id': ctx.request_id,
        'user_text': user_text,
        'normalized_query': normalized_query,
        'understanding_result': understanding.to_dict() if understanding else None,
        'understanding_diagnostics': (
            getattr(understanding, 'diagnostics', None) if understanding else None
        ),
        'skill_retrieval_trace': {
            'sources': ['llm_skill_hints', 'semantic_user_text', 'semantic_normalized_query', 'understanding_hint_overlap']
            if understanding else ['semantic_user_text'],
        },
        'top_k_skills': [item.skill_id for item in candidates],
        'candidate_scores': [item.to_dict() for item in candidates],
        'matched_examples': {
            item.skill_id: item.matched_examples
            for item in candidates
            if item.matched_examples
        },
        'latency_ms': round((time.perf_counter() - start) * 1000, 1),
        'permission_result': None,
        'risk_result': 'pending',
        'confirmation_policy': None,
        'output_verification_result': None,
    })


def trace_from_plan(
    user_text: str,
    ctx: AssistantContext,
    candidates: List[Any],
    plan: Any,
    card: Optional[Any],
    start: float,
    understanding: Optional[Any] = None,
) -> Dict[str, Any]:
    trace = base_trace(user_text, plan.normalized_query, candidates, ctx, start, understanding=understanding)
    trace.update({
        'selected_skill': plan.selected_skill,
        'rejected_skills': plan.rejected_skills,
        'slot_fill_result': {
            'slot_values': plan.slot_values,
            'missing_slots': plan.missing_slots,
        },
        'confidence': round(plan.confidence, 4),
        'route_reason': plan.route_reason,
        'fallback_reason': plan.fallback_reason,
        'planner_diagnostics': dict(getattr(plan, 'diagnostics', {}) or {}),
        'risk_result': {
            'risk_level': card.risk_level if card else None,
            'side_effect': card.side_effect if card else None,
            'write_guard': detect_write_intent(user_text),
        },
        'confirmation_policy': card.confirmation_policy if card else None,
        'planner_source': plan.planner_source,
    })
    return trace


def attach_trace(
    result: ExecutionResult,
    trace: Dict[str, Any],
    *,
    first_learning_event_kind: Any = None,
) -> None:
    trace = ensure_trace_fields(trace)
    copilot = dict(result.copilot or {})
    copilot['routePath'] = 'skill_first_runtime'
    copilot['routeTrace'] = trace
    result.copilot = copilot
    append_route_trace(trace)
    if should_capture_failure(trace):
        learning_kind = first_learning_event_kind(trace) if callable(first_learning_event_kind) else None
        append_failure_sample(str(trace.get('fallback_reason') or learning_kind or 'route_observation'), trace)


def record_trace_and_failure(trace: Dict[str, Any], kind: str) -> None:
    trace = ensure_trace_fields(trace)
    append_route_trace(trace)
    append_failure_sample(kind, trace)


__all__ = [
    'attach_trace',
    'base_trace',
    'record_trace_and_failure',
    'trace_from_plan',
]
