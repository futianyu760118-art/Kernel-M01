"""Schema-grounded input guards for Agent Kernel tool plans."""

from __future__ import annotations

import re
from typing import Any

from ..schemas import RouteDecision


def apply_skill_specific_input_guards(card: Any, decision: RouteDecision, message: str = '') -> None:
    for plan_step in decision.tool_plan or []:
        params = dict(plan_step.input or {})
        if card.id in {'system.page_capability', 'system.workflow_help'} and plan_step.tool == 'system_search':
            params['module'] = 'module_catalog'
            params['view'] = params.get('view') or 'overview'
            params['limit'] = params.get('limit') or 12
            params.pop('keyword', None)
            params.pop('query', None)
            plan_step.input = params
            continue
        if card.id == 'candidate.recent_resumes' and plan_step.tool == 'resume_analysis':
            if looks_like_generic_recent_resume_question(message):
                params.pop('keyword', None)
            params['recent_days'] = params.get('recent_days') or 30
            params['top_n'] = params.get('top_n') or 10
            params['question'] = message
            plan_step.input = params
            continue
        if card.id == 'candidate.job_fit' and plan_step.tool == 'candidate_evidence_bundle':
            plan_step.input = params
            continue
        if card.id != 'recruitment.open_jobs':
            continue
        if plan_step.tool != 'recruitment_search':
            continue
        params['module'] = 'jobs'
        params['status'] = 'open'
        params['limit'] = params.get('limit') or 20
        params.pop('keyword', None)
        params.pop('query', None)
        params.pop('date_range', None)
        plan_step.input = params


def looks_like_generic_recent_resume_question(text: Any) -> bool:
    value = str(text or '')
    return bool(
        re.search(r'(简历|投递)', value)
        and re.search(r'(最近|近\s*\d+\s*天|本周|本月|收到|收到了|新来|新来的|哪些|有哪些|质量)', value)
        and not re.search(r'[\u4e00-\u9fa5A-Za-z0-9·]{2,18}(?:工程师|专员|经理|总监|主管|岗位|职位)', value)
    )


__all__ = [
    'apply_skill_specific_input_guards',
    'looks_like_generic_recent_resume_question',
]
