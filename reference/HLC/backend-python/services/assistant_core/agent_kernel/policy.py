"""Policy helpers owned by the HRM Agent Kernel.

The Skill-first runtime still wires services together for compatibility, but
permission, write-boundary, clarification and output-verification policy belong
to the Kernel. These helpers deliberately keep business execution outside the
model layer: they only build deterministic policy results or classify whether a
planned Skill is allowed to continue.
"""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING, Any, Callable, Optional

from .. import skill_registry, widgets as W
from ..guardrails import check_skill_permission, detect_write_intent
from ..schemas import AssistantContext, ExecutionResult, RouteDecision, route_decision_to_router_result
from ..skill_first.cards import AgentSkillCard, get_card
from . import decision as kernel_decision

if TYPE_CHECKING:
    from .planning import SkillPlan


ALLOWED_SIDE_EFFECT_SKILLS = {
    'interview.schedule_confirm_create',
    'attendance.reminder_confirm',
    'approval.confirm_action',
    'notification.create_confirm',
    'task.followup_create_confirm',
    'employee.change_submit_confirm',
    'employee.salary_adjustment_submit_confirm',
}


def unsupported_write_intent(text: str, card: Optional[AgentSkillCard]) -> Optional[str]:
    """Return a write-guard reason unless the selected Skill supports it."""

    reason = detect_write_intent(text)
    if not reason:
        return None
    if card and card.id in ALLOWED_SIDE_EFFECT_SKILLS:
        if re.search(r'(删除|删掉|删了|移除|清除|改成|改为|改到|修改|编辑|变更|重新安排|调整状态|直接更新)', text or ''):
            return reason
        if card.id == 'notification.create_confirm' and re.search(r'(发送|群发|通知所有|发邮件|send|推送|发一下|推一下)', text or ''):
            return None
        if card.id == 'attendance.reminder_confirm' and re.search(r'(迟到|考勤异常|打卡异常|缺卡|早退).{0,24}(提醒|通知|生成|发送|推送|发一下)', text or ''):
            return None
        if card.id == 'task.followup_create_confirm' and (
            re.search(r'(创建|新建|新增|添加|安排|设个|加个|提醒我|帮我建|帮我创建).{0,12}(跟进任务|跟进提醒|个人待办|待办|任务|提醒)', text or '')
            or re.search(r'(跟进任务|跟进提醒|个人待办|待办|任务|提醒)\s*[:：].{2,}', text or '')
        ):
            return None
        if card.id == 'approval.confirm_action' and re.search(r'(同意|通过|批准|驳回|拒绝|拒了)', text or ''):
            return None
        if card.id == 'employee.change_submit_confirm' and re.search(r'(调岗|转岗|调到|转到|调入|转入|异动|部门调整|调整到)', text or ''):
            return None
        if card.id == 'employee.salary_adjustment_submit_confirm' and re.search(r'(调薪|涨薪|加薪|降薪|减薪|薪资调整|薪酬调整|工资调整|调整工资|上调|下调)', text or ''):
            return None
        if card.id == 'interview.schedule_confirm_create' and re.search(r'(安排|创建|新建|预约|约|约一轮|约面|排).{0,12}(面试|复试|一面|二面|三面|终面)', text or ''):
            return None
    return reason


def permission_result(card: AgentSkillCard, ctx: AssistantContext) -> str:
    tool = str((card.executor or {}).get('tool') or '')
    spec = skill_registry.get(tool)
    if not spec:
        return 'tool_not_registered'
    deny = check_skill_permission(ctx.role, spec, ctx)
    return deny or 'allowed'


def verify_output(result: ExecutionResult, card: AgentSkillCard) -> str:
    if result is None:
        return 'missing_result'
    if result.error_code in {'permission_denied', 'write_intent_blocked'}:
        return 'ok'
    if result.route_decision and result.route_decision.task_type in {'clarification', 'forbidden'}:
        return 'ok'
    if card.side_effect and not result.requires_confirmation:
        return 'side_effect_without_confirmation'
    if card.side_effect and not getattr(result, '_pending_action', None):
        return 'side_effect_without_pending_action'
    return 'ok'


def write_guard_result(message: str, ctx: AssistantContext, plan: 'SkillPlan', card: Optional[AgentSkillCard]) -> ExecutionResult:
    boundary_card = get_card('system.unsupported_write_intent')
    if boundary_card:
        card = boundary_card
        plan.selected_skill = boundary_card.id
    elif card is None:
        card = get_card('system.workflow_help') or get_card('system.page_capability')
    decision = kernel_decision.build_route_decision(card, plan)
    decision.task_type = 'forbidden'
    decision.intent = 'system.unsupported_write_intent'
    decision.safety_flags.append(plan.slot_values.get('_write_guard') or 'write_intent_blocked')
    summary = plan.slot_values.get('_write_guard') or '检测到写入/审批/发送等高风险意图，请使用业务页面或明确进入待确认动作。'
    return ExecutionResult(
        summary=summary,
        widgets=[W.warning(summary, level='danger')],
        session_id=ctx.session_id,
        route_decision=decision,
        router=route_decision_to_router_result(decision),
        error_code='write_intent_blocked',
        answer={'content': summary, 'format': 'text'},
    )


def skill_first_clarification_result(
    message: str,
    ctx: AssistantContext,
    question: str,
    trace: dict[str, Any],
    *,
    kind: str,
    error_code: Optional[str] = None,
    attach_trace: bool = True,
    attach_trace_fn: Optional[Callable[[ExecutionResult, dict[str, Any]], None]] = None,
) -> ExecutionResult:
    decision = RouteDecision(
        task_type='clarification',
        route_track='A_DATA_ACTION',
        intent='skill_first_clarification',
        confidence=float(trace.get('confidence') or 0.0),
        risk_level='low',
        clarification_question=question,
        clarification_options=[
            {'label': '补充对象', 'payload': '我补充具体对象'},
            {'label': '补充范围', 'payload': '我补充时间范围或模块'},
        ],
        requires_confirmation=False,
        fallback_policy='skill_first_clarify',
        reason=str(trace.get('fallback_reason') or kind),
    )
    result = ExecutionResult(
        summary=question,
        widgets=[W.clarification(question, decision.clarification_options, '')],
        session_id=ctx.session_id,
        router=route_decision_to_router_result(decision),
        route_decision=decision,
        requires_confirmation=False,
        fallback_used=True,
        error_code=error_code,
        answer={'content': question, 'format': 'markdown'},
    )
    trace.update({
        'selected_skill': trace.get('selected_skill') or None,
        'risk_result': trace.get('risk_result') or 'clarification',
        'final_executor': 'agent_kernel.policy.clarification',
        'output_verification_result': 'ok',
        'latency_ms': trace.get('latency_ms') or 0,
    })
    if attach_trace and attach_trace_fn:
        attach_trace_fn(result, trace)
    return result


def unhandled_result(
    message: str,
    ctx: AssistantContext,
    *,
    reason: str = 'skill_first_unhandled',
    error_code: Optional[str] = None,
    normalize_query: Callable[[str], str],
    build_base_trace: Callable[..., dict[str, Any]],
    clarification_result_fn: Callable[..., ExecutionResult],
) -> ExecutionResult:
    text = (message or '').strip()
    start = time.perf_counter()
    trace = build_base_trace(text, normalize_query(text), [], ctx, start)
    trace.update({
        'fallback_reason': reason,
        'risk_result': 'unhandled',
        'final_executor': 'agent_kernel.policy.unhandled',
        'output_verification_result': 'ok',
    })
    question = (
        '我还没有足够信息处理这个 HRM 请求。请补充你要查询的模块、对象或时间范围。'
        if text else
        '请告诉我你想查询或处理的 HRM 事项。'
    )
    return clarification_result_fn(
        text,
        ctx,
        question,
        trace,
        kind=reason,
        error_code=error_code,
    )


def permission_denied_result(
    message: str,
    ctx: AssistantContext,
    decision: RouteDecision,
    reason: str,
) -> ExecutionResult:
    decision.task_type = 'forbidden'
    decision.safety_flags.append('permission_denied')
    summary = reason if '权限' in reason else f'权限不足：{reason}'
    return ExecutionResult(
        summary=summary,
        widgets=[W.warning(summary, level='danger')],
        session_id=ctx.session_id,
        route_decision=decision,
        router=route_decision_to_router_result(decision),
        error_code='permission_denied',
        answer={'content': summary, 'format': 'text'},
    )


__all__ = [
    'ALLOWED_SIDE_EFFECT_SKILLS',
    'permission_denied_result',
    'permission_result',
    'skill_first_clarification_result',
    'unhandled_result',
    'unsupported_write_intent',
    'verify_output',
    'write_guard_result',
]
