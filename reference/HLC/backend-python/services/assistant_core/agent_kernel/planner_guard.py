"""Schema and safety guards for Agent Kernel planner decisions."""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .. import skill_registry
from ..guardrails import check_skill_permission, detect_write_intent
from ..schemas import AssistantContext
from ..skill_first.cards import AgentSkillCard, get_card


def guard_plan(
    message: str,
    ctx: AssistantContext,
    candidates: Sequence[Any],
    plan: Any,
    *,
    high_confidence: float,
    fill_slots: Callable[[str, AssistantContext, Optional[AgentSkillCard]], Dict[str, Any]],
    guard_slot_values: Callable[[str, AssistantContext, AgentSkillCard, Dict[str, Any], str], Dict[str, Any]],
    slot_guard_changes: Callable[[Dict[str, Any], Dict[str, Any]], List[Dict[str, Any]]],
) -> Any:
    candidate_ids = {str(getattr(item, 'skill_id', '') or '') for item in candidates}
    if getattr(plan, 'selected_skill', None) and plan.selected_skill not in candidate_ids:
        plan.selected_skill = None
        plan.confidence = 0.0
        plan.fallback_reason = 'planner_selected_skill_outside_top_k'
        plan.diagnostics = {
            **dict(getattr(plan, 'diagnostics', {}) or {}),
            'status': 'rejected',
            'accepted': False,
            'reject_reason': 'planner_selected_skill_outside_top_k',
        }
    card = get_card(getattr(plan, 'selected_skill', None) or '')
    if not card:
        if not getattr(plan, 'fallback_reason', None):
            plan.fallback_reason = 'selected_skill_not_found'
        return plan

    write_reason = detect_write_intent(message)
    unsupported_boundary_card = unsupported_write_boundary_card(message, candidates, card, write_reason)
    if unsupported_boundary_card:
        previous_skill = plan.selected_skill
        plan.selected_skill = unsupported_boundary_card.id
        plan.confidence = max(float(getattr(plan, 'confidence', 0.0) or 0.0), high_confidence)
        plan.fallback_reason = 'unsupported_write_intent'
        plan.route_reason = (
            f'{plan.route_reason}; skill_card_contract_corrected_to_unsupported_write_boundary'
            if getattr(plan, 'route_reason', '')
            else 'skill_card_contract_corrected_to_unsupported_write_boundary'
        )
        plan.slot_values = {
            **dict(getattr(plan, 'slot_values', {}) or {}),
            '_write_guard': write_reason,
        }
        plan.diagnostics = {
            **dict(getattr(plan, 'diagnostics', {}) or {}),
            'skill_card_contract_correction': {
                'from': previous_skill,
                'to': unsupported_boundary_card.id,
                'reason': 'unsupported_write_intent_boundary',
            },
            'guardrail_adjustment': 'unsupported_write_intent',
        }
        card = unsupported_boundary_card

    plan, card = apply_skill_card_contract_corrections(
        message,
        candidates,
        plan,
        card,
        high_confidence=high_confidence,
    )
    tool = str((card.executor or {}).get('tool') or '')
    spec = skill_registry.get(tool)
    if not spec:
        plan.fallback_reason = 'executor_tool_not_registered'
        plan.confidence = min(getattr(plan, 'confidence', 0.0) or 0.0, 0.3)
        return plan

    original_slot_values = dict(getattr(plan, 'slot_values', {}) or {})
    plan.slot_values = guard_slot_values(
        message,
        ctx,
        card,
        dict(getattr(plan, 'slot_values', {}) or {}),
        getattr(plan, 'planner_source', ''),
    )
    changes = slot_guard_changes(original_slot_values, plan.slot_values)
    if changes:
        plan.diagnostics = {
            **dict(getattr(plan, 'diagnostics', {}) or {}),
            'slot_guard_changes': changes,
        }
        plan.slot_values['_slot_guard_changes'] = changes

    plan.missing_slots = missing_required_slots(card, plan.slot_values, ctx)
    if plan.missing_slots and card.side_effect:
        plan.fallback_reason = getattr(plan, 'fallback_reason', None) or 'missing_required_side_effect_slots'
        plan.confidence = min(getattr(plan, 'confidence', 0.0) or 0.0, 0.62)

    deny = check_skill_permission(ctx.role, spec, ctx)
    if deny:
        plan.slot_values['_permission_denied'] = deny
        plan.confidence = max(getattr(plan, 'confidence', 0.0) or 0.0, high_confidence)

    unsupported_reason = unsupported_bulk_or_unsafe_side_effect_intent(message, card)
    if unsupported_reason:
        plan.fallback_reason = 'unsupported_write_intent'
        plan.slot_values['_write_guard'] = unsupported_reason
        plan.confidence = max(getattr(plan, 'confidence', 0.0) or 0.0, high_confidence)
        plan.diagnostics = {
            **dict(getattr(plan, 'diagnostics', {}) or {}),
            'guardrail_adjustment': 'unsupported_write_intent',
        }
        return plan

    if write_reason and not card.side_effect:
        plan.fallback_reason = 'write_intent_without_side_effect_skill'
        plan.slot_values['_write_guard'] = write_reason
        plan.confidence = max(getattr(plan, 'confidence', 0.0) or 0.0, high_confidence)
        plan.diagnostics = {
            **dict(getattr(plan, 'diagnostics', {}) or {}),
            'guardrail_adjustment': 'write_intent_without_side_effect_skill',
        }
    if card.side_effect:
        plan.slot_values['_side_effect_confirmation_required'] = True
    return plan


def unsupported_write_boundary_card(
    message: str,
    candidates: Sequence[Any],
    selected_card: AgentSkillCard,
    write_reason: Optional[str],
) -> Optional[AgentSkillCard]:
    if not write_reason:
        return None
    if selected_card.id == 'system.unsupported_write_intent':
        return selected_card
    if selected_card.side_effect:
        return None
    boundary_candidate = next(
        (item for item in candidates if getattr(item, 'skill_id', None) == 'system.unsupported_write_intent'),
        None,
    )
    if not boundary_candidate:
        return None
    if looks_like_workflow_help_request(message):
        return None
    return get_card('system.unsupported_write_intent')


def looks_like_workflow_help_request(message: str) -> bool:
    text = message or ''
    return bool(
        re.search(r'(怎么|如何|怎样|为什么|说明|流程|教程|解释|操作步骤|怎么做|怎么用)', text)
        and re.search(r'(新增|添加|录入|创建|新建|删除|修改|编辑|更新|变更|安排|提交|确认|审批|通过|驳回|申请|操作|办理|处理|补卡|上传|导入)', text)
    )


def apply_skill_card_contract_corrections(
    message: str,
    candidates: Sequence[Any],
    plan: Any,
    selected_card: AgentSkillCard,
    *,
    high_confidence: float,
) -> Tuple[Any, AgentSkillCard]:
    """Keep LLM decisions aligned with the most specific recalled Skill Card."""

    if not getattr(plan, 'selected_skill', None) or not candidates:
        return plan, selected_card
    if plan.selected_skill == 'candidate.resume_analysis':
        return plan, selected_card
    if (
        plan.selected_skill in {'performance.anomaly', 'employee.profile', 'employee.org_health'}
        and looks_like_performance_employee_profile_link_request(message)
    ):
        corrected = correct_to_more_specific_candidate(
            candidates,
            plan,
            target_skill='performance.employee_link',
            reason='performance_employee_profile_link_request',
            min_target_score=8.0,
            min_gap=2.0,
            high_confidence=high_confidence,
        )
        if corrected:
            return corrected
    if plan.selected_skill not in {'candidate.search', 'candidate.recent_resumes'}:
        return plan, selected_card
    if not looks_like_single_resume_evidence_request(message):
        return plan, selected_card
    resume_candidate = next(
        (item for item in candidates if getattr(item, 'skill_id', None) == 'candidate.resume_analysis'),
        None,
    )
    selected_candidate = next(
        (item for item in candidates if getattr(item, 'skill_id', None) == plan.selected_skill),
        None,
    )
    if not resume_candidate:
        return plan, selected_card
    selected_score = float(getattr(selected_candidate, 'score', 0.0) or 0.0) if selected_candidate else 0.0
    resume_score = float(getattr(resume_candidate, 'score', 0.0) or 0.0)
    if resume_score < 8.0 or (selected_score and (resume_score - selected_score) < 2.5):
        return plan, selected_card
    corrected_card = get_card('candidate.resume_analysis')
    if not corrected_card:
        return plan, selected_card
    previous_skill = plan.selected_skill
    plan.selected_skill = corrected_card.id
    plan.confidence = max(float(getattr(plan, 'confidence', 0.0) or 0.0), high_confidence)
    plan.route_reason = (
        f'{plan.route_reason}; skill_card_contract_corrected_generic_candidate_lookup_to_resume_analysis'
        if getattr(plan, 'route_reason', '')
        else 'skill_card_contract_corrected_generic_candidate_lookup_to_resume_analysis'
    )
    plan.diagnostics = {
        **dict(getattr(plan, 'diagnostics', {}) or {}),
        'skill_card_contract_correction': {
            'from': previous_skill,
            'to': corrected_card.id,
            'reason': 'single_candidate_resume_evidence_request',
            'resume_score': round(resume_score, 4),
            'selected_score': round(selected_score, 4),
            'score_gap': round(resume_score - selected_score, 4),
        },
    }
    return plan, corrected_card


def correct_to_more_specific_candidate(
    candidates: Sequence[Any],
    plan: Any,
    *,
    target_skill: str,
    reason: str,
    min_target_score: float,
    min_gap: float,
    high_confidence: float,
) -> Optional[Tuple[Any, AgentSkillCard]]:
    target_candidate = next((item for item in candidates if getattr(item, 'skill_id', None) == target_skill), None)
    selected_candidate = next((item for item in candidates if getattr(item, 'skill_id', None) == plan.selected_skill), None)
    if not target_candidate:
        return None
    selected_score = float(getattr(selected_candidate, 'score', 0.0) or 0.0) if selected_candidate else 0.0
    target_score = float(getattr(target_candidate, 'score', 0.0) or 0.0)
    if target_score < min_target_score or (selected_score and (target_score - selected_score) < min_gap):
        return None
    corrected_card = get_card(target_skill)
    if not corrected_card:
        return None
    previous_skill = plan.selected_skill
    plan.selected_skill = corrected_card.id
    plan.confidence = max(float(getattr(plan, 'confidence', 0.0) or 0.0), high_confidence)
    plan.route_reason = (
        f'{plan.route_reason}; skill_card_contract_corrected_to_more_specific_skill:{reason}'
        if getattr(plan, 'route_reason', '')
        else f'skill_card_contract_corrected_to_more_specific_skill:{reason}'
    )
    plan.diagnostics = {
        **dict(getattr(plan, 'diagnostics', {}) or {}),
        'skill_card_contract_correction': {
            'from': previous_skill,
            'to': corrected_card.id,
            'reason': reason,
            'target_score': round(target_score, 4),
            'selected_score': round(selected_score, 4),
            'score_gap': round(target_score - selected_score, 4),
        },
    }
    return plan, corrected_card


def looks_like_performance_employee_profile_link_request(message: str) -> bool:
    text = message or ''
    if not re.search(r'(绩效|低绩效|评分|表现)', text):
        return False
    profile_or_reason = re.search(r'(画像|原因|为什么|怎么回事|关联|联动|状态|证据|影响)', text)
    employee_scope = re.search(r'(员工|人员|人群|这个人|这位|他|她|[\u4e00-\u9fa5·]{2,8})', text)
    return bool(profile_or_reason and employee_scope)


def looks_like_single_resume_evidence_request(message: str) -> bool:
    text = message or ''
    if not re.search(r'(简历|履历|投递材料|个人材料|资料)', text):
        return False
    if re.search(r'(最近|近\d+\s*天|本周|本月|这个月|最新|top|TOP|前\d+|哪些|列表|多少|质量分布|收到)', text):
        return False
    return bool(
        re.search(r'[\u4e00-\u9fa5·]{2,8}(?:的)?(?:简历|履历|投递材料|个人材料|资料)', text)
        or re.search(r'(这份|这个人|这位|他|她)(?:的)?(?:简历|履历|投递材料|个人材料|资料)', text)
    )


def missing_required_slots(
    card: Optional[AgentSkillCard],
    slot_values: Dict[str, Any],
    ctx: AssistantContext,
) -> List[str]:
    if not card:
        return []
    missing = [slot for slot in card.required_slots if not slot_values.get(slot)]
    if card.id == 'workset.analysis' and not slot_values.get('latest_workset_or_item'):
        missing = list(dict.fromkeys([*missing, 'latest_workset_or_item']))
    if card.id == 'payroll.employee_self_service' and not getattr(ctx, 'user_employee_id', None):
        missing = list(dict.fromkeys([*missing, 'employee_scope']))
    return missing


def unsupported_bulk_or_unsafe_side_effect_intent(message: str, card: AgentSkillCard) -> Optional[str]:
    text = message or ''
    if card.id == 'approval.confirm_action' and re.search(
        r'(所有|全部|全都|批量|每一条|每个|都).{0,8}(审批|申请)?.{0,8}(同意|通过|批准|驳回|拒绝|拒了)',
        text,
    ):
        return '审批操作必须指定单条审批并进入确认流程，AI 助手不会批量通过或驳回审批。'
    return None


__all__ = [
    'apply_skill_card_contract_corrections',
    'correct_to_more_specific_candidate',
    'guard_plan',
    'looks_like_performance_employee_profile_link_request',
    'looks_like_single_resume_evidence_request',
    'looks_like_workflow_help_request',
    'missing_required_slots',
    'unsupported_bulk_or_unsafe_side_effect_intent',
    'unsupported_write_boundary_card',
]
