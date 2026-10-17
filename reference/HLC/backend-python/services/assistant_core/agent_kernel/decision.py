"""RouteDecision and ToolCallPlan assembly for the HRM Agent Kernel."""

from __future__ import annotations

import re
from typing import Any, Dict, List

from .. import skill_registry
from ..schemas import RouteDecision, ToolCallPlan
from ..skill_first.cards import AgentSkillCard
from .planner_slots import PLANNER_FORBIDDEN_SLOT_KEYS


def build_route_decision(card: AgentSkillCard, plan: Any) -> RouteDecision:
    tool = str((card.executor or {}).get('tool') or '')
    purpose = 'side_effect' if card.side_effect else str((card.executor or {}).get('purpose') or 'query')
    intent = str((card.executor or {}).get('intent') or card.id)
    params = dict(getattr(plan, 'slot_values', {}) or {})
    for key in list(params.keys()):
        if key.startswith('_'):
            params.pop(key, None)
    target_candidate = params.get('target_candidate')
    params.pop('target_candidate', None)
    params.pop('latest_workset_or_item', None)
    params = filter_tool_params(tool, params)
    normalized_query = str(getattr(plan, 'normalized_query', '') or '')
    if card.id == 'smart.data_query':
        params.setdefault('question', normalized_query)
    if card.id == 'workset.analysis':
        params = {'question': normalized_query, 'limit': params.get('limit', 20)}
    if card.id == 'interview.schedule_confirm_create':
        params = {}
    if card.id == 'attendance.reminder_confirm':
        params.update({
            key: value
            for key, value in (getattr(plan, 'slot_values', {}) or {}).items()
            if key in {'month', 'limit', 'rank_status'}
        })
    if card.id == 'employee.change_submit_confirm':
        params = {
            key: value
            for key, value in params.items()
            if key in {
                'employee_id', 'keyword', 'target_department_id', 'target_department',
                'target_position_id', 'target_position', 'effective_date', 'reason',
            }
        }
    if card.id == 'employee.salary_adjustment_submit_confirm':
        params = {
            key: value
            for key, value in params.items()
            if key in {
                'employee_id', 'keyword', 'target_department_id', 'target_department',
                'salary_adjustment', 'effective_date', 'reason',
            }
        }

    route_track = 'A_DATA_ACTION'
    task_type = 'action' if card.side_effect else 'data_query'
    required_context: List[str] = []
    requires_confirmation = card.side_effect
    if card.id == 'workset.analysis':
        required_context = ['latest_workset']
    elif card.id == 'interview.schedule_confirm_create':
        latest_reference = (
            plan.slot_values.get('latest_workset_or_item')
            if isinstance(getattr(plan, 'slot_values', None), dict)
            and isinstance(plan.slot_values.get('latest_workset_or_item'), dict)
            else {}
        )
        if latest_reference.get('kind') == 'item' and latest_reference.get('entity_type') in {'candidate', 'resume'}:
            required_context = ['latest_workset']
        elif target_candidate and not looks_like_workset_reference(str(target_candidate)):
            required_context = ['named_subject_evidence']
        else:
            required_context = ['latest_workset']
    elif card.id == 'task.followup_create_confirm' and looks_like_workset_reference(normalized_query):
        required_context = ['latest_workset']

    return RouteDecision(
        task_type=task_type,
        route_track=route_track,
        intent=intent,
        confidence=getattr(plan, 'confidence', 0.0),
        risk_level=card.risk_level,
        required_context=required_context,
        tool_plan=[ToolCallPlan(
            step_id=card.id.replace('.', '_'),
            tool=tool,
            purpose=purpose,
            input=params,
            required=True,
            risk_level=card.risk_level,
            cache_policy='session',
        )],
        requires_confirmation=requires_confirmation,
        fallback_policy=card.fallback_policy,
        reason=getattr(plan, 'route_reason', ''),
    )


def filter_tool_params(tool: str, params: Dict[str, Any]) -> Dict[str, Any]:
    spec = skill_registry.get(tool)
    schema = getattr(spec, 'input_schema', None) if spec else None
    if not isinstance(schema, dict) or not schema:
        return {
            str(key): value
            for key, value in dict(params).items()
            if str(key) not in PLANNER_FORBIDDEN_SLOT_KEYS
        }
    allowed = set(schema.keys())
    return {
        key: value
        for key, value in params.items()
        if key in allowed and str(key) not in PLANNER_FORBIDDEN_SLOT_KEYS
    }


def looks_like_workset_reference(value: str) -> bool:
    return bool(re.search(r'(这个|这位|这些|这几|上面|刚才|第二|第一|第三|前\s*[一二两三四五六七八九十\d]+|ws_|workset)', value or '', re.I))


__all__ = [
    'build_route_decision',
    'filter_tool_params',
    'looks_like_workset_reference',
]
