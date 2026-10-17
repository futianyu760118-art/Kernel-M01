"""Context and learning helpers for the HRM Agent Kernel.

This module is the canonical place where route history, pending actions,
WorkSet metadata and last focus are interpreted for Kernel observability and
failure learning. It keeps the runtime from owning cross-turn memory semantics.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .. import memory
from ..schemas import AssistantContext, ExecutionResult


def learning_events(
    message: str,
    ctx: AssistantContext,
    trace: Dict[str, Any],
    *,
    result: Optional[ExecutionResult] = None,
    action_verb: Optional[str] = None,
) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    previous = previous_assistant_route(ctx)
    previous_trace = previous.get('route_trace') if previous else None
    previous_skill = previous_trace.get('selected_skill') if isinstance(previous_trace, dict) else None
    current_skill = trace.get('selected_skill')

    if action_verb == '取消动作':
        pending_action = getattr(result, '_pending_action', None)
        events.append({
            'kind': 'confirmation_cancelled',
            'action_verb': action_verb,
            'resolved_action_id': getattr(result, '_resolved_action_id', None),
            'summary': getattr(result, 'summary', None),
            'previous_selected_skill': previous_skill,
            'current_selected_skill': current_skill,
            'previous_request_id': previous_trace.get('request_id') if isinstance(previous_trace, dict) else None,
            'pending_action_label': pending_action.get('label') if isinstance(pending_action, dict) else None,
            'pending_action_tools': [
                tool.get('tool')
                for tool in (pending_action.get('tools') if isinstance(pending_action, dict) else []) or []
                if isinstance(tool, dict) and tool.get('tool')
            ],
        })

    if previous_skill and looks_like_user_correction(message):
        events.append({
            'kind': 'user_correction',
            'previous_selected_skill': previous_skill,
            'current_selected_skill': current_skill,
            'previous_request_id': previous_trace.get('request_id') if isinstance(previous_trace, dict) else None,
        })

    if previous and previous.get('was_clarification') and current_skill and current_skill != previous_skill:
        events.append({
            'kind': 'clarification_changed_selection',
            'previous_selected_skill': previous_skill,
            'current_selected_skill': current_skill,
            'previous_request_id': previous_trace.get('request_id') if isinstance(previous_trace, dict) else None,
        })
    return events


def looks_like_user_correction(message: str) -> bool:
    text = message or ''
    return bool(
        ('不是' in text or '不对' in text or '错了' in text or '搞错' in text)
        or any(marker in text for marker in ('我说的是', '应该是', '改成', '换成', '更正一下'))
    )


def previous_assistant_route(ctx: AssistantContext) -> Optional[Dict[str, Any]]:
    for entry in reversed(ctx.history or []):
        if not isinstance(entry, dict) or entry.get('role') != 'assistant':
            continue
        trace = route_trace_from_history_entry(entry)
        return {
            'route_trace': trace or {},
            'was_clarification': history_entry_was_clarification(entry, trace),
        }
    return None


def route_trace_from_history_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    copilot = entry.get('copilot') if isinstance(entry.get('copilot'), dict) else {}
    trace = copilot.get('routeTrace') if isinstance(copilot.get('routeTrace'), dict) else None
    if trace:
        return trace
    meta = entry.get('meta') if isinstance(entry.get('meta'), dict) else {}
    trace = meta.get('routeTrace') if isinstance(meta.get('routeTrace'), dict) else None
    return trace or {}


def history_entry_was_clarification(entry: Dict[str, Any], trace: Dict[str, Any]) -> bool:
    meta = entry.get('meta') if isinstance(entry.get('meta'), dict) else {}
    if meta.get('taskType') == 'clarification' or meta.get('routePath') == 'clarification':
        return True
    route_decision = meta.get('routeDecision') if isinstance(meta.get('routeDecision'), dict) else {}
    if route_decision.get('taskType') == 'clarification':
        return True
    trace_decision = trace.get('route_decision') if isinstance(trace.get('route_decision'), dict) else {}
    if trace_decision.get('taskType') == 'clarification' or trace.get('route_path') == 'clarification':
        return True
    return any(
        isinstance(widget, dict) and widget.get('type') == 'clarification'
        for widget in entry.get('widgets') or []
    )


def first_learning_event_kind(trace: Dict[str, Any]) -> Optional[str]:
    events = trace.get('learning_events')
    if not isinstance(events, list):
        return None
    for event in events:
        if isinstance(event, dict) and event.get('kind'):
            return str(event.get('kind'))
    return None


def last_focus_from_result(result: ExecutionResult) -> Optional[Dict[str, Any]]:
    for entity in result.entities or []:
        if not isinstance(entity, dict):
            continue
        entity_type = entity.get('type')
        if entity_type not in {'candidate', 'resume', 'employee'}:
            continue
        return {
            'entity_type': entity_type,
            'entity_id': entity.get('id'),
            'name': entity.get('title') or entity.get('name'),
        }
    for workset in result.worksets or []:
        if not isinstance(workset, dict):
            continue
        for item in workset.get('items') or []:
            if not isinstance(item, dict):
                continue
            entity_type = item.get('entity_type')
            if entity_type in {'candidate', 'resume', 'employee'}:
                return {
                    'entity_type': entity_type,
                    'entity_id': item.get('entity_id'),
                    'name': item.get('title'),
                }
    return None


def persist_turn(
    message: str,
    ctx: AssistantContext,
    result: ExecutionResult,
    *,
    route_path: str = 'skill_first_runtime',
) -> None:
    router_obj = result.router
    decision_obj = result.route_decision
    route_trace = (result.copilot or {}).get('routeTrace') if isinstance(result.copilot, dict) else None
    metadata: Dict[str, Any] = {
        'router': router_obj.to_dict() if router_obj else None,
        'routeDecision': decision_obj.to_dict() if decision_obj else None,
        'routeTrack': decision_obj.route_track if decision_obj else None,
        'taskType': decision_obj.task_type if decision_obj else None,
        'errorCode': result.error_code,
        'fallbackUsed': result.fallback_used,
        'usedSkills': list(dict.fromkeys([s.skill for s in result.trace if s.skill])),
        'riskLevel': router_obj.risk_level if router_obj else 'low',
        'requiresConfirmation': result.requires_confirmation,
        'routePath': route_path,
        'routeTrace': route_trace,
        'worksets': list(result.worksets or []),
        'pendingAction': getattr(result, '_pending_action', None),
        'resolvedActionId': getattr(result, '_resolved_action_id', None),
    }
    last_focus = last_focus_from_result(result)
    if last_focus:
        metadata['lastFocus'] = last_focus
    saved = memory.save_turn(
        user_id=ctx.user_id,
        session_id=ctx.session_id,
        question=message,
        summary=result.summary or '',
        entity_count=len(result.entities or []),
        metadata=metadata,
        request_id=ctx.request_id,
        entities=result.entities or [],
        widgets=result.widgets or [],
        actions=result.actions or [],
        worksets=result.worksets or [],
        answer=result.answer,
        schema_version=result.schema_version,
        render_blocks=result.render_blocks or [],
        copilot=result.copilot or {},
        client_context=result.client_context or {},
    )
    result.session_id = saved or ctx.session_id


__all__ = [
    'first_learning_event_kind',
    'history_entry_was_clarification',
    'last_focus_from_result',
    'learning_events',
    'looks_like_user_correction',
    'persist_turn',
    'previous_assistant_route',
    'route_trace_from_history_entry',
]
