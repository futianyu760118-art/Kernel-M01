"""Side-effect confirmation preparation owned by the HRM Agent Kernel."""

from __future__ import annotations

import re
import uuid
from typing import Any, Dict, List

from services.business_time import business_now

from .. import widgets as W
from ..executors import ToolExecutor
from ..schemas import AssistantContext, ExecutionResult, ExecutionStep, RouteDecision, route_decision_to_router_result
from .action_runtime import clarification_result
from .policy import permission_denied_result


def prepare_approval_confirmation(message: str, ctx: AssistantContext, decision: RouteDecision) -> ExecutionResult:
    from models import ApprovalInstance

    params = dict((decision.tool_plan[0].input if decision.tool_plan else {}) or {})
    approval_id = str(params.get('approval_id') or '').strip()
    action = str(params.get('action') or '').strip().lower()
    if action in {'approved', 'pass', 'agree', '同意', '通过', '批准'}:
        action = 'approve'
    elif action in {'rejected', 'deny', '驳回', '拒绝', '不同意'}:
        action = 'reject'
    if not approval_id:
        return clarification_result('请告诉我要处理哪条审批，例如“同意第一条请假申请”或“通过审批 ap-1”。', ctx, decision)
    if action not in {'approve', 'reject'}:
        return clarification_result('请明确要通过还是驳回这条审批。', ctx, decision)

    approval = ApprovalInstance.query.get(approval_id)
    if not approval:
        return clarification_result(f'没有找到审批记录：{approval_id}。', ctx, decision)
    if approval.status != 'pending':
        return clarification_result(f'审批“{approval.title or approval.id}”当前状态为 {approval.status or "未知"}，不能直接处理。', ctx, decision)
    if approval.approver_id and approval.approver_id != ctx.user_id and ctx.role not in {'admin', 'hr_admin', 'executive'}:
        return permission_denied_result(message, ctx, decision, '权限不足：该审批不属于你处理。')

    action_label = '通过' if action == 'approve' else '驳回'
    comment = str(params.get('comment') or '').strip() or (
        'AI 助手确认后审批通过' if action == 'approve' else 'AI 助手确认后审批驳回'
    )
    action_id = 'act_' + uuid.uuid4().hex[:12]
    summary = f'{action_label}审批：{approval.title or approval.id}。确认后将按审批工作流更新关联业务单据。'
    pending_action = {
        'id': action_id,
        'label': f'{action_label}审批',
        'tools': [{
            'tool': 'approval_decision',
            'input': {'approval_id': approval.id, 'action': action, 'comment': comment},
        }],
        'summary': summary,
    }
    widgets = [
        W.status_badges([
            {'label': '审批标题', 'value': approval.title or approval.id, 'status': 'neutral', 'icon': 'fa-check-double'},
            {'label': '业务类型', 'value': approval.business_type or '-', 'status': 'neutral', 'icon': 'fa-briefcase'},
            {'label': '处理动作', 'value': action_label, 'status': 'success' if action == 'approve' else 'danger', 'icon': 'fa-check' if action == 'approve' else 'fa-ban'},
        ], title='审批确认'),
        W.confirmation_card(f'{action_label}审批', summary, action_id),
    ]
    result = ExecutionResult(
        summary=summary,
        entities=[{
            'type': 'approval',
            'typeLabel': '审批',
            'icon': 'fa-check-double',
            'id': approval.id,
            'title': approval.title or approval.id,
            'subtitle': approval.business_type or '',
            'meta': f'状态：{approval.status or "—"}',
            'url': '/approval-center',
        }],
        widgets=widgets,
        session_id=ctx.session_id,
        router=route_decision_to_router_result(decision),
        route_decision=decision,
        requires_confirmation=True,
        actions=widgets[-1].get('actions') or [],
        answer={'content': summary, 'format': 'text'},
    )
    result._pending_action = pending_action
    return result


def prepare_notification_confirmation(message: str, ctx: AssistantContext, decision: RouteDecision) -> ExecutionResult:
    params = dict((decision.tool_plan[0].input if decision.tool_plan else {}) or {})
    title = str(params.get('title') or '').strip()[:200]
    content = str(params.get('content') or '').strip()
    notice_type = str(params.get('notice_type') or params.get('noticeType') or 'hr').strip()[:60] or 'hr'
    link_url = str(params.get('link_url') or params.get('linkUrl') or '/notifications').strip()[:500] or '/notifications'
    user_id = str(params.get('user_id') or params.get('userId') or '').strip()
    if not title:
        return clarification_result('请补充通知标题或内容，例如“给全员推一条 HR 通知：明天下午三点全员培训”。', ctx, decision)
    if not content:
        content = title

    action_id = 'act_' + uuid.uuid4().hex[:12]
    audience = '指定用户' if user_id else '全员/通用通知'
    summary = f'创建系统通知：{title}。确认后将在通知中心写入一条 {notice_type} 通知，范围：{audience}。'
    pending_input = {
        'title': title,
        'content': content[:1000],
        'notice_type': notice_type,
        'link_url': link_url,
    }
    if user_id:
        pending_input['user_id'] = user_id
    pending_action = {
        'id': action_id,
        'label': '创建系统通知',
        'tools': [{'tool': 'notification_create', 'input': pending_input}],
        'summary': summary,
    }
    widgets = [
        W.status_badges([
            {'label': '通知标题', 'value': title, 'status': 'neutral', 'icon': 'fa-bell'},
            {'label': '通知类型', 'value': notice_type, 'status': 'info', 'icon': 'fa-tag'},
            {'label': '发送范围', 'value': audience, 'status': 'warning', 'icon': 'fa-users'},
        ], title='通知创建确认'),
        W.data_table([
            {'key': 'field', 'label': '字段'},
            {'key': 'value', 'label': '内容'},
        ], [
            {'field': '标题', 'value': title},
            {'field': '正文', 'value': content[:200]},
            {'field': '链接', 'value': link_url},
        ], title='待创建通知预览', exportable=False),
        {
            'type': 'source_citations',
            'title': '通知创建确认依据',
            'sources': [{
                'module': 'notifications',
                'recordType': 'notification_draft',
                'recordId': action_id,
                'title': '待确认通知草稿',
                'url': '/notifications',
                'fields': [
                    {'field': 'title', 'label': '通知标题', 'value': title},
                    {'field': 'content', 'label': '通知正文', 'value': content[:200]},
                    {'field': 'notice_type', 'label': '通知类型', 'value': notice_type},
                    {'field': 'audience', 'label': '发送范围', 'value': audience},
                ],
            }],
        },
        W.confirmation_card('创建系统通知', summary, action_id),
    ]
    result = ExecutionResult(
        summary=summary,
        entities=[],
        widgets=widgets,
        session_id=ctx.session_id,
        router=route_decision_to_router_result(decision),
        route_decision=decision,
        requires_confirmation=True,
        actions=widgets[-1].get('actions') or [],
        answer={'content': summary, 'format': 'text'},
    )
    result._pending_action = pending_action
    return result


def prepare_attendance_reminder_confirmation(message: str, ctx: AssistantContext, decision: RouteDecision) -> ExecutionResult:
    params = dict((decision.tool_plan[0].input if decision.tool_plan else {}) or {})
    limit = clamp_int(params.get('limit'), default=5, minimum=1, maximum=20)
    rank_status = str(params.get('rank_status') or params.get('rankStatus') or 'late').strip() or 'late'
    month = normalize_skill_month(params.get('month')) or business_now().strftime('%Y-%m')
    analysis_params = {
        'month': month,
        'anomaly_only': True,
        'rank_status': rank_status,
        'limit': limit,
    }
    output = ToolExecutor().execute('attendance_analysis', analysis_params, ctx)
    data = output.get('data') or {}
    if not output.get('ok'):
        return clarification_result(f'考勤排名查询失败：{output.get("error") or "未知错误"}。', ctx, decision)
    metrics = data.get('metrics') if isinstance(data.get('metrics'), dict) else {}
    ranked = metrics.get('topRankedEmployees') if isinstance(metrics.get('topRankedEmployees'), list) else []
    if not ranked:
        return clarification_result(f'{month} 没有查到需要提醒的考勤对象。', ctx, decision)

    action_id = 'act_' + uuid.uuid4().hex[:12]
    table_rows: List[Dict[str, Any]] = []
    tools: List[Dict[str, Any]] = []
    label = '迟到' if rank_status == 'late' else '考勤异常'
    for item in ranked[:limit]:
        if not isinstance(item, dict):
            continue
        employee_name = str(item.get('employeeName') or '员工').strip() or '员工'
        employee_id = str(item.get('employeeId') or item.get('id') or '').strip()
        count = int(item.get('count') or 0)
        department = item.get('department') or '-'
        job_title = item.get('jobTitle') or '-'
        latest_date = item.get('latestDate') or ''
        title = f'{month} 考勤{label}提醒 - {employee_name}'
        content = (
            f'{employee_name}，系统统计你在 {month} 有 {count} 次{label}记录。'
            '请及时核对考勤记录，如存在请假、外勤、补卡等合理原因，请在员工申请中补充说明；'
            '如无特殊原因，请注意后续出勤时间。'
        )
        table_rows.append({
            'employeeName': employee_name,
            'department': department,
            'jobTitle': job_title,
            'count': count,
            'latestDate': latest_date,
            'draftTitle': title,
        })
        pending_input = {
            'title': title,
            'content': content,
            'notice_type': 'attendance',
            'link_url': '/hrm/attendance-records',
        }
        if employee_id:
            pending_input['target_employee_id'] = employee_id
        tools.append({'tool': 'notification_create', 'input': pending_input})

    if not tools:
        return clarification_result(f'{month} 没有可生成提醒的员工记录。', ctx, decision)

    summary = (
        f'已根据 {month} 考勤记录找出{label}最多的 {len(table_rows)} 人，并生成站内通知草稿。'
        f'确认后将写入通知中心 {len(tools)} 条考勤提醒；未确认前不会创建通知。'
    )
    widgets = list(data.get('widgets') or [])
    widgets.append(W.data_table([
        {'key': 'employeeName', 'label': '员工'},
        {'key': 'department', 'label': '部门'},
        {'key': 'jobTitle', 'label': '岗位'},
        {'key': 'count', 'label': f'{label}次数', 'align': 'right'},
        {'key': 'latestDate', 'label': f'最近{label}'},
        {'key': 'draftTitle', 'label': '通知标题'},
    ], table_rows, title='待发送提醒草稿'))
    confirmation = W.confirmation_card('发送考勤迟到提醒' if rank_status == 'late' else '发送考勤异常提醒', summary, action_id)
    widgets.append(confirmation)
    if not any(isinstance(widget, dict) and widget.get('type') == 'source_citations' for widget in widgets):
        widgets.append({
            'type': 'source_citations',
            'title': '证据来源',
            'sources': [
                {
                    'module': 'attendance_records',
                    'recordType': 'attendance_records',
                    'title': f'{month} 考勤记录',
                    'url': '/hrm/attendance-records',
                },
                {
                    'module': 'notifications',
                    'recordType': 'notifications',
                    'title': '确认后写入通知中心',
                    'url': '/notifications',
                },
            ],
        })
    pending_action = {
        'id': action_id,
        'label': '发送考勤迟到提醒' if rank_status == 'late' else '发送考勤异常提醒',
        'tools': tools,
        'summary': summary,
    }
    result = ExecutionResult(
        summary=summary,
        entities=data.get('entities') or [],
        widgets=widgets,
        session_id=ctx.session_id,
        trace=[execution_trace('attendance_analysis', output)],
        router=route_decision_to_router_result(decision),
        route_decision=decision,
        requires_confirmation=True,
        actions=confirmation.get('actions') or [],
        answer={'content': summary, 'format': 'markdown'},
    )
    result._pending_action = pending_action
    return result


def prepare_employee_change_confirmation(message: str, ctx: AssistantContext, decision: RouteDecision) -> ExecutionResult:
    params = dict((decision.tool_plan[0].input if decision.tool_plan else {}) or {})
    keyword = str(params.get('keyword') or '').strip()
    employee_id = str(params.get('employee_id') or '').strip()
    target_department = str(params.get('target_department') or '').strip()
    if not employee_id and not keyword:
        return clarification_result('请告诉我要给哪位员工发起异动，例如“把张三调到研发部”。', ctx, decision)
    if not target_department and not params.get('target_department_id'):
        return clarification_result('请补充目标部门，例如“把张三调到研发部”。', ctx, decision)

    action_id = 'act_' + uuid.uuid4().hex[:12]
    impact_params = dict(params)
    impact_params['action_id'] = action_id
    output = ToolExecutor().execute('employee_change_impact', impact_params, ctx)
    data = output.get('data') or {}
    if not output.get('ok'):
        return clarification_result(f'员工异动影响分析失败：{output.get("error") or "未知错误"}。', ctx, decision)
    facts = data.get('facts') if isinstance(data.get('facts'), dict) else {}
    if not facts.get('employee_id'):
        return clarification_result(str(data.get('summary_hint') or '未找到需要异动的员工。'), ctx, decision)

    target_department_name = facts.get('target_department_name') or target_department
    summary = str(data.get('summary_hint') or '').strip() or (
        f'已分析 {facts.get("employee_name") or keyword or employee_id} 调岗到 {target_department_name or "目标部门"} 的影响。'
    )
    pending_input = {
        'employee_id': facts.get('employee_id') or employee_id,
        'keyword': facts.get('employee_name') or keyword,
        'target_department_id': facts.get('target_department_id') or params.get('target_department_id'),
        'target_department': target_department_name,
        'target_position_id': facts.get('target_position_id') or params.get('target_position_id'),
        'target_position': (
            facts.get('target_position_title')
            or facts.get('target_position')
            or params.get('target_position')
        ),
        'effective_date': params.get('effective_date') or business_now().date().isoformat(),
        'reason': params.get('reason') or (
            f'AI 助手影响分析确认后提交：{facts.get("employee_name") or keyword or employee_id} '
            f'调岗到 {target_department_name or "目标部门"}。'
        ),
        'action_id': action_id,
    }
    pending_input = {key: value for key, value in pending_input.items() if value not in (None, '', [])}
    pending_action = {
        'id': action_id,
        'label': '提交员工异动审批',
        'tools': [{'tool': 'employee_change_submit', 'input': pending_input}],
        'summary': (
            f'{facts.get("employee_name") or keyword or employee_id} 调岗影响已分析，'
            '确认后将提交待审批员工异动申请；审批通过前不会修改员工档案。'
        ),
    }
    result = ExecutionResult(
        summary=summary,
        entities=data.get('entities') or [],
        widgets=data.get('widgets') or [],
        session_id=ctx.session_id,
        trace=[execution_trace('employee_change_impact', output)],
        router=route_decision_to_router_result(decision),
        route_decision=decision,
        requires_confirmation=True,
        actions=confirmation_actions(data.get('widgets') or []),
        answer={'content': summary, 'format': 'markdown'},
    )
    result._pending_action = pending_action
    return result


def prepare_salary_adjustment_confirmation(message: str, ctx: AssistantContext, decision: RouteDecision) -> ExecutionResult:
    params = dict((decision.tool_plan[0].input if decision.tool_plan else {}) or {})
    keyword = str(params.get('keyword') or '').strip()
    employee_id = str(params.get('employee_id') or '').strip()
    salary_adjustment = str(params.get('salary_adjustment') or '').strip()
    if not employee_id and not keyword:
        return clarification_result('请告诉我要给哪位员工发起调薪，例如“给张三调薪 1800 元”。', ctx, decision)
    if not salary_adjustment:
        return clarification_result('请补充调薪金额，例如“给张三调薪 1800 元”。', ctx, decision)

    action_id = 'act_' + uuid.uuid4().hex[:12]
    impact_params = dict(params)
    impact_params['action_id'] = action_id
    output = ToolExecutor().execute('employee_salary_adjustment_impact', impact_params, ctx)
    data = output.get('data') or {}
    if not output.get('ok'):
        return clarification_result(f'员工调薪影响分析失败：{output.get("error") or "未知错误"}。', ctx, decision)
    summary = str(data.get('summary_hint') or '').strip()
    if '权限不足' in summary:
        return permission_denied_result(message, ctx, decision, summary)
    facts = data.get('facts') if isinstance(data.get('facts'), dict) else {}
    if not facts.get('employee_id'):
        return clarification_result(summary or '未找到需要调薪的员工。', ctx, decision)

    pending_input = {
        'employee_id': facts.get('employee_id') or employee_id,
        'keyword': facts.get('employee_name') or keyword,
        'target_department_id': facts.get('department_id') or params.get('target_department_id'),
        'target_department': facts.get('department_name') or params.get('target_department'),
        'salary_adjustment': facts.get('salary_adjustment') or salary_adjustment,
        'effective_date': params.get('effective_date') or business_now().date().isoformat(),
        'reason': params.get('reason') or (
            f'AI 助手调薪影响分析确认后提交：{facts.get("employee_name") or keyword or employee_id} '
            f'调薪 {facts.get("salary_adjustment") or salary_adjustment} 元。'
        ),
        'action_id': action_id,
    }
    pending_input = {key: value for key, value in pending_input.items() if value not in (None, '', [])}
    pending_action = {
        'id': action_id,
        'label': '提交调薪审批',
        'tools': [{'tool': 'employee_change_submit', 'input': pending_input}],
        'summary': (
            f'{facts.get("employee_name") or keyword or employee_id} 调薪影响已分析，'
            '确认后将提交待审批调薪异动；审批通过前不会修改薪资档案。'
        ),
    }
    result = ExecutionResult(
        summary=summary or pending_action['summary'],
        entities=data.get('entities') or [],
        widgets=data.get('widgets') or [],
        session_id=ctx.session_id,
        trace=[execution_trace('employee_salary_adjustment_impact', output)],
        router=route_decision_to_router_result(decision),
        route_decision=decision,
        requires_confirmation=True,
        actions=confirmation_actions(data.get('widgets') or []),
        answer={'content': summary or pending_action['summary'], 'format': 'markdown'},
    )
    result._pending_action = pending_action
    return result


def normalize_skill_month(value: Any) -> str:
    text = str(value or '').strip()
    if not text:
        return ''
    if re.fullmatch(r'20\d{2}-\d{1,2}', text):
        year, month = text.split('-', 1)
        return f'{year}-{int(month):02d}'
    if text == 'current_month':
        return business_now().strftime('%Y-%m')
    if text == 'previous_month':
        now = business_now()
        year = now.year
        month = now.month - 1
        if month <= 0:
            year -= 1
            month = 12
        return f'{year}-{month:02d}'
    return text


def clamp_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(number, maximum))


def execution_trace(skill: str, output: Dict[str, Any]) -> ExecutionStep:
    return ExecutionStep(
        step_id=skill,
        type='skill',
        skill=skill,
        status='ok' if output.get('ok') else 'error',
        rows_count=int(output.get('rows_count') or 0),
        error=output.get('error') or None,
        risk_level='high' if skill in {'employee_salary_adjustment_impact'} else 'medium',
    )


def confirmation_actions(widgets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    for widget in widgets or []:
        if isinstance(widget, dict) and widget.get('type') == 'confirmation_card':
            actions = widget.get('actions')
            if isinstance(actions, list):
                return actions
    return []


__all__ = [
    'clamp_int',
    'confirmation_actions',
    'execution_trace',
    'normalize_skill_month',
    'prepare_approval_confirmation',
    'prepare_attendance_reminder_confirmation',
    'prepare_employee_change_confirmation',
    'prepare_notification_confirmation',
    'prepare_salary_adjustment_confirmation',
]
