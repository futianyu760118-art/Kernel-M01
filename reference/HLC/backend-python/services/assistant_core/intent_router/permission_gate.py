"""PermissionGate：角色过滤、HighRiskDomain 改写与 side_effect 强制确认。

定位
----
位于 ``ConfidenceGate`` 之后、Executor 调用之前的最后一道安全闸门。无论
``RouterResult`` 来自 FastPath / Retriever / Reranker 哪条路径，本模块都
要负责：

1. **角色过滤 candidate_skills**：用 ``skill_registry`` 查询每个 candidate
   skill 的 ``allowed_roles``，剔除 ``ctx.role`` 未授权的 skill。过滤后为空
   时，用 ``TaskTemplate.candidate_skills`` 兜底（再次过滤）。
2. **HighRiskDomain 改写**：当 ``task_template_id`` 命中 HighRiskDomain
   （``payroll_*`` 前缀、任一保留 skill ``risk_level='high'`` 或
   ``side_effect=True``）但 ``ctx.role`` 不在 ``TaskTemplate.allowed_roles``
   时，整条路由被改写为 ``permission_denied_response``。
3. **side_effect 强制确认**：保留下来的任一 skill ``side_effect=True`` 或
   ``risk_level='high'``、或 ``task_template_id`` 以 ``payroll_`` 开头时，
   把 ``requires_confirmation`` 强制设为 ``True``。
4. **payroll_* 风险锁定**：``payroll_*`` 系列任务始终保持
   ``risk_level='high'``，无论上游声明的是什么。

不变量与边界
------------

- ``RouterResult`` 是值对象：所有改写都通过 ``dataclasses.replace`` 产生新
  对象，不修改入参。
- ``task_template_id ∈ {'forbidden_action_response',
  'permission_denied_response'}`` 时直接 ``replace`` 同值返回，避免把
  FastPath 已经设好的 ``forbidden_action`` 状态降级。
- ``task_registry.get(task_id)`` 缺失时跳过 HighRiskDomain 改写检查（保守
  降级到不改写，但仍做角色过滤与 requires_confirmation 强制）。
- ``ACTION_GENERATE_DRAFT`` 只读，不计入写动作；写判定仅看
  ``side_effect=True`` 或 ``risk_level='high'``。

API
---

- :class:`PermissionGate`：无构造参数。
- :meth:`PermissionGate.apply` ``(router_result, ctx) -> RouterResult``：
  返回新对象。
- 模块级 :func:`apply` / :func:`reload` 函数包装；``reload`` 当前是 no-op
  （PermissionGate 没有 env / 文件状态），保留接口形状以与同包其它模块
  对齐。

Validates: Requirements 5.1, 5.2, 5.3, 5.4
"""

from __future__ import annotations

import dataclasses
import logging
import threading
from typing import Iterable, List, Optional, Sequence

from .. import skill_registry, task_registry
from ..constants import RISK_HIGH
from ..schemas import AssistantContext, RouterResult, SkillSpec, TaskTemplate

logger = logging.getLogger('hengpintong.assistant.intent_router.permission_gate')


# ---------------------------------------------------------------------------
# 常量：FastPath 已经处理的特殊任务，不再二次改写
# ---------------------------------------------------------------------------

#: FastPath / 上游已经决断的特殊任务 id，PermissionGate 不再改写。
_PASS_THROUGH_TASK_IDS = frozenset({
    'forbidden_action_response',
    'permission_denied_response',
})

#: HighRiskDomain 判定的 task_id 前缀（高敏感薪酬域）。
_HIGH_RISK_TASK_ID_PREFIXES = ('payroll_',)


# ---------------------------------------------------------------------------
# 帮助函数
# ---------------------------------------------------------------------------


def is_high_risk_domain(
    task_id: Optional[str],
    kept_skill_specs: Sequence[SkillSpec],
) -> bool:
    """判定 ``task_id`` + 保留下来的 skill 集是否构成 HighRiskDomain。

    判定规则（任一成立即为 True）：

    - ``task_id`` 以 ``payroll_`` 开头；
    - 任一 ``kept_skill_specs`` 元素 ``risk_level == 'high'``；
    - 任一 ``kept_skill_specs`` 元素 ``side_effect == True``。

    注：``ACTION_GENERATE_DRAFT`` 只读，不计入写动作；写判定仅看
    ``side_effect=True`` 或 ``risk_level='high'``。
    """
    if task_id and any(task_id.startswith(p) for p in _HIGH_RISK_TASK_ID_PREFIXES):
        return True
    for spec in kept_skill_specs:
        if spec is None:
            continue
        if spec.risk_level == RISK_HIGH:
            return True
        if spec.side_effect:
            return True
    return False


def _filter_skills_by_role(
    skill_ids: Iterable[str], role: str,
) -> List[str]:
    """按 ``role`` 过滤 ``skill_ids``，保序、去重。

    skill 不在 ``skill_registry`` 中时直接剔除（避免下游再 ``KeyError``）。
    """
    seen: set = set()
    kept: List[str] = []
    for sid in skill_ids:
        if not isinstance(sid, str) or sid in seen:
            continue
        seen.add(sid)
        spec = skill_registry.get(sid)
        if spec is None:
            continue
        if role in spec.allowed_roles:
            kept.append(sid)
    return kept


def _resolve_kept_specs(skill_ids: Iterable[str]) -> List[SkillSpec]:
    """把 skill_id 列表转换为 ``SkillSpec`` 列表，丢弃未注册的。"""
    out: List[SkillSpec] = []
    for sid in skill_ids:
        spec = skill_registry.get(sid)
        if spec is not None:
            out.append(spec)
    return out


def _build_permission_denied(
    router_result: RouterResult, ctx: AssistantContext,
) -> RouterResult:
    """把 ``router_result`` 改写为 ``permission_denied_response``。

    - ``intent='permission_denied'``
    - ``task_template_id='permission_denied_response'``
    - ``confidence`` 保留入参
    - ``risk_level='high'``
    - ``candidate_skills=[]``
    - ``requires_confirmation=False``
    - ``clarification_options=[]``
    - ``reason='permission_gate:role_not_allowed:{role}:{original_task_id}'``
    - ``route_path``：入参为空或 ``'unknown'`` → ``'fallback'``，否则保留
    """
    original_task_id = router_result.task_template_id or ''
    incoming_route = router_result.route_path or ''
    new_route_path = (
        'fallback'
        if incoming_route in ('', 'unknown')
        else incoming_route
    )
    return dataclasses.replace(
        router_result,
        intent='permission_denied',
        task_template_id='permission_denied_response',
        # confidence 保留入参（dataclasses.replace 默认行为）
        risk_level=RISK_HIGH,
        candidate_skills=[],
        requires_confirmation=False,
        clarification_question=None,
        clarification_options=[],
        reason=f'permission_gate:role_not_allowed:{ctx.role}:{original_task_id}',
        route_path=new_route_path,
    )


# ---------------------------------------------------------------------------
# PermissionGate 主体
# ---------------------------------------------------------------------------


class PermissionGate:
    """无状态的权限闸门。

    设计上不持有任何 env / 文件状态：只读 ``skill_registry`` 与
    ``task_registry`` 的当前快照。``reload()`` 因此是 no-op，保留接口
    与同包其它模块对齐。
    """

    def __init__(self) -> None:
        # 没有需要初始化的状态；保留构造器以便后续扩展依赖注入。
        pass

    # -- 主入口 -----------------------------------------------------------

    def apply(
        self, router_result: RouterResult, ctx: AssistantContext,
    ) -> RouterResult:
        """对 ``router_result`` 施加角色与风险闸门，返回新对象。"""
        if router_result is None:
            raise ValueError('router_result must not be None')
        if ctx is None or not getattr(ctx, 'role', None):
            raise ValueError('AssistantContext.role must be provided')

        task_id = router_result.task_template_id or ''

        # 1) 已经被 FastPath / 上游断定的特殊任务不再二次改写。
        if task_id in _PASS_THROUGH_TASK_IDS:
            return dataclasses.replace(router_result)

        # 2) 角色过滤 candidate_skills（保序去重）。
        kept_skills = _filter_skills_by_role(
            router_result.candidate_skills or [], ctx.role,
        )

        # 3) 过滤后空 → 用 task_template 自带 candidate_skills 兜底（再次过滤）。
        task_template: Optional[TaskTemplate] = (
            task_registry.get(task_id) if task_id else None
        )
        if not kept_skills and task_template is not None:
            kept_skills = _filter_skills_by_role(
                task_template.candidate_skills or [], ctx.role,
            )

        # 4) 解析 SkillSpec，便于 HighRiskDomain 判定。
        kept_specs = _resolve_kept_specs(kept_skills)
        is_hrd = is_high_risk_domain(task_id, kept_specs)

        # 5) HighRiskDomain 改写：任务存在且角色不在 allowed_roles → 拒绝。
        #    task_template 缺失时保守降级（不改写），避免误伤未登记的任务。
        if (
            task_template is not None
            and is_hrd
            and ctx.role not in (task_template.allowed_roles or [])
        ):
            return _build_permission_denied(router_result, ctx)

        # 6) payroll_* 始终保持 risk_level='high'。
        is_payroll = task_id.startswith('payroll_') if task_id else False
        new_risk_level = (
            RISK_HIGH if is_payroll else router_result.risk_level
        )

        # 7) 强制 requires_confirmation：任一保留 skill side_effect/high，
        #    或任务为 payroll_*。
        has_side_effect = any(s.side_effect for s in kept_specs)
        has_high_risk_skill = any(
            s.risk_level == RISK_HIGH for s in kept_specs
        )
        force_confirm = has_side_effect or has_high_risk_skill or is_payroll
        new_requires_confirmation = (
            True if force_confirm else router_result.requires_confirmation
        )

        return dataclasses.replace(
            router_result,
            candidate_skills=kept_skills,
            risk_level=new_risk_level,
            requires_confirmation=new_requires_confirmation,
        )


# ---------------------------------------------------------------------------
# 模块级单例 + 函数式包装
# ---------------------------------------------------------------------------


_default_gate: Optional[PermissionGate] = None
_default_lock = threading.Lock()


def _get_default() -> PermissionGate:
    """惰性构造模块级单例。"""
    global _default_gate
    if _default_gate is None:
        with _default_lock:
            if _default_gate is None:
                _default_gate = PermissionGate()
    return _default_gate


def apply(router_result: RouterResult, ctx: AssistantContext) -> RouterResult:
    """模块级 ``apply`` 包装（使用默认单例）。"""
    return _get_default().apply(router_result, ctx)


def reload() -> None:
    """重置默认单例。

    PermissionGate 没有 env / 文件状态，因此 ``reload()`` 实际是 no-op；
    保留接口形状以便测试隔离与未来扩展（依赖注入、可调阈值等）。
    """
    global _default_gate
    with _default_lock:
        _default_gate = None


__all__ = [
    'PermissionGate',
    'apply',
    'is_high_risk_domain',
    'reload',
]
