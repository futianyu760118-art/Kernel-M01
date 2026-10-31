"""Task 9.1 —— PermissionGate 单元测试。

校验目标（Validates: Requirements 5.1, 5.2, 5.3, 5.4）：

* **5.1 角色过滤**：``candidate_skills`` 中 ``ctx.role`` 不在
  ``allowed_roles`` 的 skill 必须被剔除；过滤后空时用
  ``task_template.candidate_skills`` 兜底（同样过滤）。
* **5.2 HighRiskDomain 改写**：``payroll_month_summary`` + ``role='employee'``
  （不在 ``TaskTemplate.allowed_roles``）→ 改写为
  ``permission_denied_response``、``requires_confirmation=False``、
  ``risk_level='high'``、``clarification_options=[]``。
* **5.3 payroll_* 风险锁定**：``payroll_*`` 任务始终保持
  ``risk_level='high'``，无论入参声明的是什么；同时强制
  ``requires_confirmation=True``。
* **5.4 side_effect 强制确认**：任一保留 skill ``side_effect=True`` 或
  ``risk_level='high'`` 时，``requires_confirmation`` 被强制设为 ``True``。
* 不变更：``route_path`` 保留入参值；不修改入参（dataclasses.replace 语义）。
"""

from __future__ import annotations

import dataclasses
from typing import List, Optional

import pytest

from services.assistant_core import skill_registry, task_registry
from services.assistant_core.constants import (
    ACTION_GENERATE_DRAFT, ACTION_READ_HR, ACTION_READ_PUBLIC,
    ACTION_READ_SENSITIVE, RISK_HIGH, RISK_LOW,
)
from services.assistant_core.intent_router import permission_gate as pg
from services.assistant_core.intent_router.permission_gate import (
    PermissionGate, is_high_risk_domain,
)
from services.assistant_core.schemas import (
    AssistantContext, RouterResult, SkillSpec, TaskTemplate,
)


# ---------------------------------------------------------------------------
# 共享 fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_module_singleton():
    """避免模块级 ``_default_gate`` 在测试间共享。"""
    pg.reload()
    yield
    pg.reload()


@pytest.fixture
def gate() -> PermissionGate:
    return PermissionGate()


def _ctx(role: str, **kw) -> AssistantContext:
    return AssistantContext(
        user_id='u-1', role=role, session_id='s-1', **kw,
    )


def _result(
    *,
    task_template_id: Optional[str],
    intent: str = 'task_intent',
    confidence: float = 0.9,
    candidate_skills: Optional[List[str]] = None,
    requires_confirmation: bool = False,
    risk_level: str = RISK_LOW,
    route_path: str = 'retrieval',
    clarification_options: Optional[List[dict]] = None,
) -> RouterResult:
    return RouterResult(
        intent=intent,
        task_template_id=task_template_id,
        confidence=confidence,
        candidate_skills=list(candidate_skills or []),
        requires_confirmation=requires_confirmation,
        risk_level=risk_level,
        route_path=route_path,
        clarification_options=list(clarification_options or []),
    )


# ---------------------------------------------------------------------------
# 5.1 角色过滤
# ---------------------------------------------------------------------------


class TestRoleFiltering:
    def test_drops_skill_when_role_not_in_allowed(self, gate):
        """payroll_analysis.allowed_roles 不含 employee → 必须被过滤掉。"""
        # employee_lookup 的 candidate_skills 中加上 payroll_analysis 模拟越权。
        result = _result(
            task_template_id='employee_lookup',
            candidate_skills=['employee_search', 'payroll_analysis'],
        )
        out = gate.apply(result, _ctx('employee'))
        assert 'payroll_analysis' not in out.candidate_skills
        assert 'employee_search' in out.candidate_skills

    def test_keeps_skill_when_role_in_allowed(self, gate):
        """所有 candidate_skills 对当前角色都开放 → 全部保留。"""
        result = _result(
            task_template_id='employee_lookup',
            candidate_skills=['employee_search', 'organization_search'],
        )
        out = gate.apply(result, _ctx('hr_admin'))
        assert out.candidate_skills == ['employee_search', 'organization_search']

    def test_drops_unknown_skill_id(self, gate):
        """未在 skill_registry 注册的 skill_id 直接剔除，不抛错。"""
        result = _result(
            task_template_id='employee_lookup',
            candidate_skills=['employee_search', 'no_such_skill'],
        )
        out = gate.apply(result, _ctx('hr_admin'))
        assert out.candidate_skills == ['employee_search']

    def test_falls_back_to_task_template_candidate_skills(self, gate):
        """全部 candidate_skills 被过滤掉时，用 TaskTemplate 自带的兜底。

        构造方式：employee_lookup 对 employee 角色开放，TaskTemplate 自带
        ``[employee_search, organization_search]`` 都允许 employee；这里把
        入参 candidate_skills 全部填成对 employee 不允许的 skill，看是否能
        兜底回到 TaskTemplate 上。
        """
        # 找一个 employee 不在 allowed_roles 的 skill：interview_planning。
        spec = skill_registry.get('interview_planning')
        assert spec is not None
        assert 'employee' not in spec.allowed_roles

        result = _result(
            task_template_id='employee_lookup',
            candidate_skills=['interview_planning'],
        )
        out = gate.apply(result, _ctx('employee'))
        # 兜底后必须从 employee_lookup.candidate_skills 选 employee 可访问的。
        assert out.candidate_skills, (
            'expected fallback to task_template.candidate_skills'
        )
        for sid in out.candidate_skills:
            spec = skill_registry.get(sid)
            assert spec is not None and 'employee' in spec.allowed_roles


# ---------------------------------------------------------------------------
# 5.2 HighRiskDomain 改写：payroll + employee
# ---------------------------------------------------------------------------


class TestPayrollDeniedForEmployee:
    def test_payroll_month_summary_with_employee_rewrites_to_denied(self, gate):
        result = _result(
            task_template_id='payroll_month_summary',
            candidate_skills=['payroll_analysis'],
            requires_confirmation=False,
            risk_level=RISK_HIGH,
            route_path='fast_path',
            clarification_options=[
                {'label': 'opt1', 'payload': 'x'},
            ],
        )
        out = gate.apply(result, _ctx('employee'))

        assert out.task_template_id == 'permission_denied_response'
        assert out.intent == 'permission_denied'
        assert out.requires_confirmation is False
        assert out.risk_level == RISK_HIGH
        assert out.candidate_skills == []
        assert out.clarification_options == []
        assert out.clarification_question is None
        assert 'permission_gate:role_not_allowed:employee:payroll_month_summary' in out.reason
        # confidence 保留入参（截断到 3 位小数）。
        assert out.confidence == pytest.approx(0.9)
        # route_path 入参 'fast_path' 非空非 unknown → 保留。
        assert out.route_path == 'fast_path'

    def test_denied_route_path_falls_back_when_unknown(self, gate):
        """``route_path`` 入参为 ``'unknown'`` → 改写时降级为 ``'fallback'``。"""
        result = _result(
            task_template_id='payroll_month_summary',
            candidate_skills=['payroll_analysis'],
            route_path='unknown',
        )
        out = gate.apply(result, _ctx('employee'))
        assert out.task_template_id == 'permission_denied_response'
        assert out.route_path == 'fallback'

    def test_denied_route_path_falls_back_when_empty(self, gate):
        """``route_path`` 入参为空字符串 → 改写时降级为 ``'fallback'``。"""
        result = _result(
            task_template_id='payroll_month_summary',
            candidate_skills=['payroll_analysis'],
            route_path='',
        )
        out = gate.apply(result, _ctx('employee'))
        assert out.task_template_id == 'permission_denied_response'
        assert out.route_path == 'fallback'

    def test_denied_for_recruiter_role(self, gate):
        """``recruiter`` 也不在 payroll_month_summary.allowed_roles，应被拒。"""
        result = _result(
            task_template_id='payroll_month_summary',
            candidate_skills=['payroll_analysis'],
        )
        out = gate.apply(result, _ctx('recruiter'))
        assert out.task_template_id == 'permission_denied_response'

    def test_post_filter_empty_for_high_risk_role_disallowed(self, gate):
        """二次过滤后仍空且命中 HighRiskDomain → permission_denied_response。

        构造：payroll_month_summary，TaskTemplate 自带的
        ``candidate_skills=[payroll_analysis]`` 对 employee 不开放，二次过滤
        后空；且 task_id 命中 ``payroll_*`` 前缀，HighRiskDomain 成立。
        """
        result = _result(
            task_template_id='payroll_month_summary',
            # 入参 candidate_skills 全部对 employee 关闭。
            candidate_skills=['interview_planning'],
        )
        out = gate.apply(result, _ctx('employee'))
        assert out.task_template_id == 'permission_denied_response'
        assert out.intent == 'permission_denied'


# ---------------------------------------------------------------------------
# 5.2 / 5.3 高权限角色保留 + 强制确认 + 风险锁定
# ---------------------------------------------------------------------------


class TestPayrollKeptForHrAdmin:
    def test_hr_admin_keeps_payroll_and_forces_confirmation(self, gate):
        """hr_admin 在 payroll_month_summary.allowed_roles 中：

        - 候选保留 ``payroll_analysis``。
        - ``risk_level`` 始终 ``high``（即使入参为 low 也会被锁回 high）。
        - ``payroll_analysis.risk_level='high'`` → 强制
          ``requires_confirmation=True``。
        """
        result = _result(
            task_template_id='payroll_month_summary',
            candidate_skills=['payroll_analysis'],
            requires_confirmation=False,
            risk_level=RISK_LOW,  # 故意写错，看会不会被锁回 high
            route_path='fast_path',
        )
        out = gate.apply(result, _ctx('hr_admin'))

        assert out.task_template_id == 'payroll_month_summary'
        assert out.candidate_skills == ['payroll_analysis']
        assert out.risk_level == RISK_HIGH
        assert out.requires_confirmation is True
        assert out.route_path == 'fast_path'  # route_path 不变

    @pytest.mark.parametrize('role', ['admin', 'hr_admin', 'finance', 'executive'])
    def test_payroll_kept_for_authorized_roles(self, gate, role):
        result = _result(
            task_template_id='payroll_month_summary',
            candidate_skills=['payroll_analysis'],
        )
        out = gate.apply(result, _ctx(role))
        assert out.task_template_id == 'payroll_month_summary'
        assert 'payroll_analysis' in out.candidate_skills
        assert out.risk_level == RISK_HIGH
        assert out.requires_confirmation is True


# ---------------------------------------------------------------------------
# 5.4 side_effect 强制确认
# ---------------------------------------------------------------------------


class TestSideEffectForceConfirmation:
    def test_side_effect_skill_forces_confirmation(self, gate, monkeypatch):
        """注入一个 side_effect=True 的 skill，验证强制确认生效。"""
        # 临时注入一个 side_effect=True、低风险、HR 可访问的 skill。
        injected_id = '__test_side_effect_skill__'
        injected = SkillSpec(
            id=injected_id,
            name='测试副作用 skill',
            domain='test',
            description='unit-test only',
            allowed_roles=['hr_admin', 'employee'],
            risk_level=RISK_LOW,
            side_effect=True,
            action_type=ACTION_READ_HR,
        )
        # 直接塞进注册表内部 dict（测试隔离，autouse fixture 会重置 gate）。
        from services.assistant_core import skill_registry as sr_module
        sr_module._SKILL_REGISTRY[injected_id] = injected
        try:
            result = _result(
                task_template_id='employee_lookup',
                candidate_skills=['employee_search', injected_id],
                requires_confirmation=False,
                risk_level=RISK_LOW,
            )
            out = gate.apply(result, _ctx('hr_admin'))
            assert injected_id in out.candidate_skills
            assert out.requires_confirmation is True
        finally:
            sr_module._SKILL_REGISTRY.pop(injected_id, None)

    def test_high_risk_skill_forces_confirmation_outside_payroll(
        self, gate, monkeypatch,
    ):
        """RISK_HIGH 的 skill 即使任务不是 payroll_* 也要强制确认。"""
        injected_id = '__test_high_risk_skill__'
        injected = SkillSpec(
            id=injected_id,
            name='测试高风险 skill',
            domain='test',
            description='unit-test only',
            allowed_roles=['hr_admin'],
            risk_level=RISK_HIGH,
            side_effect=False,
            action_type=ACTION_READ_SENSITIVE,
        )
        from services.assistant_core import skill_registry as sr_module
        sr_module._SKILL_REGISTRY[injected_id] = injected
        try:
            result = _result(
                task_template_id='employee_lookup',
                candidate_skills=[injected_id],
                requires_confirmation=False,
                risk_level=RISK_LOW,
            )
            out = gate.apply(result, _ctx('hr_admin'))
            assert out.requires_confirmation is True
        finally:
            sr_module._SKILL_REGISTRY.pop(injected_id, None)

    def test_generate_draft_only_skill_does_not_force_confirmation(self, gate):
        """``ACTION_GENERATE_DRAFT`` + ``side_effect=False`` + ``risk_level=low``
        不计入写动作；不应强制 confirm。``draft_generator`` 满足这一组合。"""
        spec = skill_registry.get('draft_generator')
        assert spec is not None
        assert spec.side_effect is False
        assert spec.risk_level == RISK_LOW
        assert spec.action_type == ACTION_GENERATE_DRAFT

        result = _result(
            task_template_id='hr_draft_generation',
            candidate_skills=['draft_generator'],
            requires_confirmation=False,
            risk_level=RISK_LOW,
        )
        out = gate.apply(result, _ctx('hr_admin'))
        assert out.requires_confirmation is False
        assert 'draft_generator' in out.candidate_skills


# ---------------------------------------------------------------------------
# 低风险任务保留候选
# ---------------------------------------------------------------------------


class TestLowRiskKeptForEmployee:
    def test_global_search_answer_with_employee_keeps_candidates(self, gate):
        """``global_search_answer`` 对 employee 开放且不命中 HighRiskDomain。

        预期：``candidate_skills`` 保留、``risk_level='low'``、
        ``requires_confirmation`` 与入参一致（不被强制设 True）。
        """
        result = _result(
            task_template_id='global_search_answer',
            candidate_skills=['global_search'],
            requires_confirmation=False,
            risk_level=RISK_LOW,
            route_path='retrieval',
        )
        out = gate.apply(result, _ctx('employee'))
        assert out.task_template_id == 'global_search_answer'
        assert out.candidate_skills == ['global_search']
        assert out.risk_level == RISK_LOW
        assert out.requires_confirmation is False
        assert out.route_path == 'retrieval'

    def test_employee_lookup_with_employee_keeps_candidates(self, gate):
        result = _result(
            task_template_id='employee_lookup',
            candidate_skills=['employee_search', 'organization_search'],
        )
        out = gate.apply(result, _ctx('employee'))
        assert out.candidate_skills == ['employee_search', 'organization_search']
        assert out.risk_level == RISK_LOW
        assert out.requires_confirmation is False


# ---------------------------------------------------------------------------
# 入参不可变 / route_path 保留
# ---------------------------------------------------------------------------


class TestImmutability:
    def test_input_router_result_not_mutated(self, gate):
        """``apply`` 必须返回新对象，且不修改入参的可变字段。"""
        original_skills = ['payroll_analysis']
        original_options = [{'label': 'a', 'payload': 'b'}]
        original = _result(
            task_template_id='payroll_month_summary',
            candidate_skills=original_skills,
            risk_level=RISK_HIGH,
            clarification_options=original_options,
        )
        # 留一份深快照
        snapshot = dataclasses.replace(
            original,
            candidate_skills=list(original.candidate_skills),
            clarification_options=[dict(o) for o in original.clarification_options],
        )

        out = gate.apply(original, _ctx('employee'))
        assert out is not original
        # 入参未变
        assert original.task_template_id == snapshot.task_template_id
        assert original.candidate_skills == snapshot.candidate_skills
        assert original.requires_confirmation == snapshot.requires_confirmation
        assert original.clarification_options == snapshot.clarification_options
        assert original.risk_level == snapshot.risk_level

    def test_route_path_preserved_when_not_denied(self, gate):
        for path in ('fast_path', 'retrieval', 'clarification', 'fallback'):
            result = _result(
                task_template_id='employee_lookup',
                candidate_skills=['employee_search'],
                route_path=path,
            )
            out = gate.apply(result, _ctx('hr_admin'))
            assert out.route_path == path

    def test_pass_through_for_forbidden_action_response(self, gate):
        """FastPath 已经设的 ``forbidden_action`` 不被降级。"""
        result = RouterResult(
            intent='forbidden_action',
            task_template_id='forbidden_action_response',
            confidence=0.99,
            risk_level=RISK_HIGH,
            requires_confirmation=True,
            forbidden_reason='delete_intent',
            route_path='fast_path',
        )
        out = gate.apply(result, _ctx('employee'))
        assert out is not result
        assert out.task_template_id == 'forbidden_action_response'
        assert out.requires_confirmation is True
        assert out.risk_level == RISK_HIGH
        assert out.forbidden_reason == 'delete_intent'
        assert out.route_path == 'fast_path'

    def test_pass_through_for_permission_denied_response(self, gate):
        """``permission_denied_response`` 入参已被上游设为拒绝，不重写。"""
        result = RouterResult(
            intent='permission_denied',
            task_template_id='permission_denied_response',
            confidence=0.5,
            risk_level=RISK_HIGH,
            requires_confirmation=False,
            route_path='fallback',
            reason='upstream_set',
        )
        out = gate.apply(result, _ctx('employee'))
        assert out is not result
        assert out.task_template_id == 'permission_denied_response'
        # 不重写 reason / requires_confirmation
        assert out.reason == 'upstream_set'
        assert out.requires_confirmation is False


# ---------------------------------------------------------------------------
# 缺失 task_template 保守降级
# ---------------------------------------------------------------------------


class TestMissingTaskTemplate:
    def test_unknown_task_id_skips_high_risk_rewrite(self, gate):
        """``task_template`` 不存在时跳过 HighRiskDomain 改写，但仍做角色过滤
        与 requires_confirmation 强制。"""
        result = _result(
            task_template_id='__no_such_task__',
            candidate_skills=['payroll_analysis'],
        )
        # employee 不在 payroll_analysis.allowed_roles → 角色过滤后空。
        out = gate.apply(result, _ctx('employee'))
        # 没有 task_template → 不改写为 permission_denied_response。
        assert out.task_template_id == '__no_such_task__'
        assert out.candidate_skills == []

    def test_unknown_task_id_with_payroll_prefix_still_locks_risk(self, gate):
        """task_id 以 ``payroll_`` 开头但 task_template 不存在 → 不能改写为
        denied（保守降级），但 risk_level 仍锁定为 high。"""
        result = _result(
            task_template_id='payroll_unknown_task',
            candidate_skills=['payroll_analysis'],
            risk_level=RISK_LOW,
        )
        out = gate.apply(result, _ctx('hr_admin'))
        assert out.task_template_id == 'payroll_unknown_task'
        assert out.risk_level == RISK_HIGH
        assert out.requires_confirmation is True


# ---------------------------------------------------------------------------
# is_high_risk_domain helper 单测
# ---------------------------------------------------------------------------


class TestIsHighRiskDomainHelper:
    def test_payroll_prefix_is_high_risk(self):
        assert is_high_risk_domain('payroll_month_summary', []) is True
        assert is_high_risk_domain('payroll_anything', []) is True

    def test_high_risk_skill_makes_domain_high_risk(self):
        spec = skill_registry.get('payroll_analysis')
        assert spec is not None and spec.risk_level == RISK_HIGH
        assert is_high_risk_domain('some_task', [spec]) is True

    def test_side_effect_skill_makes_domain_high_risk(self):
        spec = SkillSpec(
            id='x', name='x', domain='x', description='',
            allowed_roles=['hr_admin'], risk_level=RISK_LOW, side_effect=True,
            action_type=ACTION_READ_HR,
        )
        assert is_high_risk_domain('any_task', [spec]) is True

    def test_low_risk_read_only_is_not_high_risk(self):
        spec = skill_registry.get('global_search')
        assert spec is not None
        assert spec.risk_level == RISK_LOW
        assert spec.side_effect is False
        assert is_high_risk_domain('global_search_answer', [spec]) is False

    def test_generate_draft_alone_is_not_high_risk(self):
        """``ACTION_GENERATE_DRAFT`` 不计入写动作。"""
        spec = skill_registry.get('draft_generator')
        assert spec is not None
        assert spec.action_type == ACTION_GENERATE_DRAFT
        assert is_high_risk_domain('hr_draft_generation', [spec]) is False

    def test_empty_inputs_are_not_high_risk(self):
        assert is_high_risk_domain('', []) is False
        assert is_high_risk_domain(None, []) is False


# ---------------------------------------------------------------------------
# 模块级 API
# ---------------------------------------------------------------------------


class TestModuleLevelApi:
    def test_module_apply_uses_singleton(self):
        result = _result(
            task_template_id='payroll_month_summary',
            candidate_skills=['payroll_analysis'],
        )
        out = pg.apply(result, _ctx('employee'))
        assert out.task_template_id == 'permission_denied_response'

    def test_module_reload_is_safe_to_call(self):
        # 调用前后行为一致（reload 是 no-op）。
        result = _result(
            task_template_id='employee_lookup',
            candidate_skills=['employee_search'],
        )
        out1 = pg.apply(result, _ctx('hr_admin'))
        pg.reload()
        out2 = pg.apply(result, _ctx('hr_admin'))
        assert out1.task_template_id == out2.task_template_id
        assert out1.candidate_skills == out2.candidate_skills
        assert out1.requires_confirmation == out2.requires_confirmation


# ---------------------------------------------------------------------------
# 输入校验
# ---------------------------------------------------------------------------


class TestInputValidation:
    def test_none_router_result_raises(self, gate):
        with pytest.raises(ValueError):
            gate.apply(None, _ctx('hr_admin'))

    def test_missing_role_raises(self, gate):
        result = _result(
            task_template_id='employee_lookup',
            candidate_skills=['employee_search'],
        )
        # role 设为空字符串
        bad_ctx = AssistantContext(user_id='u', role='', session_id='s')
        with pytest.raises(ValueError):
            gate.apply(result, bad_ctx)
