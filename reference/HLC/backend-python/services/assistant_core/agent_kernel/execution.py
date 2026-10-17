"""Execution support helpers for the HRM Agent Kernel."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from .. import widgets as W, workset_store
from ..executors import ToolExecutor
from ..schemas import AssistantContext, ExecutionResult, RouteDecision, route_decision_to_router_result
from . import confirmations
from . import result_augmentation
from .tool_workbench import ToolWorkbench


def attach_kernel_tool_outputs(result: ExecutionResult, outputs: List[Dict[str, Any]]) -> None:
    result._kernel_tool_outputs = list(outputs or [])


def record_result_workset_if_needed(
    message: str,
    ctx: AssistantContext,
    result: ExecutionResult,
    outputs: List[Dict[str, Any]],
) -> None:
    if result.worksets:
        return
    workset_entities: List[Dict[str, Any]] = []
    for output in outputs or []:
        data = output.get('data') if isinstance(output, dict) else {}
        raw = data.get('workset_entities') if isinstance(data, dict) else None
        if isinstance(raw, list):
            workset_entities.extend(item for item in raw if isinstance(item, dict))
    entities = workset_entities or [item for item in (result.entities or []) if isinstance(item, dict)]
    if not entities:
        return
    ws = workset_store.create_workset_from_entities(ctx.session_id, ctx.user_id, message[:80], entities)
    if ws:
        result.worksets.append(ws)


def record_tool_workbench_trace(trace: Dict[str, Any], output: Dict[str, Any]) -> None:
    if not isinstance(output, dict):
        return
    workbench = output.get('workbench')
    if isinstance(workbench, dict):
        events = trace.get('tool_workbench')
        if not isinstance(events, list):
            events = []
            trace['tool_workbench'] = events
        events.append({
            'tool': output.get('tool'),
            'schema_checked': bool(workbench.get('schema_checked')),
            'visible_params': list(workbench.get('visible_params') or []),
            'hidden_params': list(workbench.get('hidden_params') or []),
            'provider_contract_checked': bool(workbench.get('provider_contract_checked')),
            'provider_skill': workbench.get('provider_skill'),
            'provider_tool': workbench.get('provider_tool'),
        })
    if output.get('ok') is False and output.get('error') in {
        'tool_not_found',
        'invalid_tool_arguments',
        'permission_denied',
        'provider_tool_mismatch',
    }:
        errors = trace.get('tool_workbench_errors')
        if not isinstance(errors, list):
            errors = []
            trace['tool_workbench_errors'] = errors
        errors.append({
            'tool': output.get('tool'),
            'error': output.get('error'),
            'message': output.get('error_message') or output.get('error'),
            'details': output.get('error_details') or {},
        })


def selected_provider_executor_metadata(trace: Dict[str, Any]) -> Dict[str, Any]:
    provider = trace.get('skill_provider') if isinstance(trace.get('skill_provider'), dict) else {}
    selected = provider.get('selected') if isinstance(provider.get('selected'), dict) else {}
    metadata = selected.get('executor_metadata') if isinstance(selected.get('executor_metadata'), dict) else {}
    return dict(metadata or {})


def execute_workset_analysis(
    message: str,
    ctx: AssistantContext,
    decision: RouteDecision,
    *,
    compose_tool_outputs: Callable[[List[Dict[str, Any]], str, AssistantContext, RouteDecision], ExecutionResult],
) -> ExecutionResult:
    resolved = workset_store.resolve_mention(message, ctx.session_id)
    if not resolved:
        question = '我还不知道“上面那批/第二个/这个人”指哪些结果。请先查询一组候选人、员工或岗位，或直接告诉我要分析谁。'
        return ExecutionResult(
            summary=question,
            widgets=[W.clarification(question, [], '')],
            session_id=ctx.session_id,
            router=route_decision_to_router_result(decision),
            route_decision=decision,
            requires_confirmation=True,
            answer={'content': question, 'format': 'markdown'},
        )
    params = {'question': message, 'limit': 20}
    ws = resolved.get('workset') or {}
    if ws.get('entity_type'):
        params['entity_type'] = ws.get('entity_type')
    output = ToolExecutor().execute('workset_analyze', params, ctx)
    result = compose_tool_outputs([output], message, ctx, decision)
    record_result_workset_if_needed(message, ctx, result, [output])
    result._kernel_tool_outputs = [output]
    return result


def execute_skill_node(
    message: str,
    ctx: AssistantContext,
    card: Any,
    decision: RouteDecision,
    trace: Dict[str, Any],
    *,
    prepare_action_confirmation: Callable[[str, AssistantContext, RouteDecision], ExecutionResult],
    compose_tool_outputs: Callable[[List[Dict[str, Any]], str, AssistantContext, RouteDecision], ExecutionResult],
    execute_workset_analysis_fn: Optional[Callable[[str, AssistantContext, RouteDecision], ExecutionResult]] = None,
    tool_workbench_factory: Callable[[], Any] = ToolWorkbench,
) -> ExecutionResult:
    card_id = str(getattr(card, 'id', '') or '')
    if getattr(card, 'side_effect', False):
        if card_id == 'approval.confirm_action':
            result = confirmations.prepare_approval_confirmation(message, ctx, decision)
            trace['final_executor'] = 'agent_kernel.confirmations.prepare_approval_confirmation'
        elif card_id == 'attendance.reminder_confirm':
            result = confirmations.prepare_attendance_reminder_confirmation(message, ctx, decision)
            trace['final_executor'] = 'agent_kernel.confirmations.prepare_attendance_reminder_confirmation'
        elif card_id == 'notification.create_confirm':
            result = confirmations.prepare_notification_confirmation(message, ctx, decision)
            trace['final_executor'] = 'agent_kernel.confirmations.prepare_notification_confirmation'
        elif card_id == 'employee.change_submit_confirm':
            result = confirmations.prepare_employee_change_confirmation(message, ctx, decision)
            trace['final_executor'] = 'agent_kernel.confirmations.prepare_employee_change_confirmation'
        elif card_id == 'employee.salary_adjustment_submit_confirm':
            result = confirmations.prepare_salary_adjustment_confirmation(message, ctx, decision)
            trace['final_executor'] = 'agent_kernel.confirmations.prepare_salary_adjustment_confirmation'
        else:
            result = prepare_action_confirmation(message, ctx, decision)
            trace['final_executor'] = 'agent_kernel.action_runtime.prepare_action_confirmation'
        return result

    if card_id == 'workset.analysis':
        trace['final_executor'] = 'ToolExecutor(workset_analyze)'
        if execute_workset_analysis_fn is not None:
            result = execute_workset_analysis_fn(message, ctx, decision)
        else:
            result = execute_workset_analysis(
                message,
                ctx,
                decision,
                compose_tool_outputs=compose_tool_outputs,
            )
        attach_kernel_tool_outputs(result, getattr(result, '_kernel_tool_outputs', []) or [])
        return result

    outputs: List[Dict[str, Any]] = []
    tool_workbench = tool_workbench_factory()
    for plan_step in decision.tool_plan[:4]:
        if plan_step.purpose == 'side_effect':
            trace['final_executor'] = 'agent_kernel.action_runtime.prepare_action_confirmation'
            return prepare_action_confirmation(message, ctx, decision)
        provider_metadata = selected_provider_executor_metadata(trace)
        output = tool_workbench.call(
            plan_step.tool,
            plan_step.input or {},
            ctx,
            provider_metadata=provider_metadata,
        )
        outputs.append(output)
        record_tool_workbench_trace(trace, output)
        if plan_step.required and not outputs[-1].get('ok'):
            result = compose_tool_outputs(outputs, message, ctx, decision)
            result_augmentation.augment_skill_specific_result(card, result, outputs, message, decision)
            attach_kernel_tool_outputs(result, outputs)
            trace['final_executor'] = 'ToolExecutor+' + ','.join([out.get('tool') or '' for out in outputs])
            return result

    result_augmentation.augment_candidate_evidence_widgets(outputs)
    result = compose_tool_outputs(outputs, message, ctx, decision)
    result_augmentation.augment_skill_specific_result(card, result, outputs, message, decision)
    record_result_workset_if_needed(message, ctx, result, outputs)
    attach_kernel_tool_outputs(result, outputs)
    trace['final_executor'] = 'ToolExecutor+' + ','.join([out.get('tool') or '' for out in outputs])
    return result


__all__ = [
    'attach_kernel_tool_outputs',
    'execute_skill_node',
    'execute_workset_analysis',
    'record_result_workset_if_needed',
    'record_tool_workbench_trace',
    'selected_provider_executor_metadata',
]
