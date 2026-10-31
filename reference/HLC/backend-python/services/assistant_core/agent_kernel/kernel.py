"""Run loop for the HRM Agent Kernel."""

from __future__ import annotations

import logging
import time
from typing import Optional

from ..schemas import AssistantContext, ExecutionResult, RouteDecision
from .services import AgentKernelServices, candidate_ids
from .state import AgentKernelRunState

logger = logging.getLogger('hengpintong.assistant.agent_kernel')


class AgentKernel:
    """LLM-first / Skill-first run loop for ordinary HRM assistant traffic."""

    def __init__(self, services: AgentKernelServices, *, top_k: int = 16, min_confidence: float = 0.45) -> None:
        self.services = services
        self.top_k = top_k
        self.min_confidence = min_confidence

    def run(self, message: str, ctx: AssistantContext) -> Optional[ExecutionResult]:
        text = (message or '').strip()
        if not text:
            return None

        state = AgentKernelRunState(message=text, ctx=ctx)
        if self._confirmed_action_node(state):
            return self._observability_node(state)
        if self._understanding_node(state):
            return self._observability_node(state)
        if self._retrieval_node(state):
            return self._observability_node(state)
        if self._skill_provider_node(state):
            return self._observability_node(state)
        if self._planning_node(state):
            return self._observability_node(state)
        if self._slot_reference_node(state):
            return self._observability_node(state)
        if self._risk_node(state):
            return self._observability_node(state)
        if self._decision_node(state):
            return self._observability_node(state)
        if self._skill_playbook_node(state):
            return self._observability_node(state)
        if self._permission_node(state):
            return self._observability_node(state)
        if self._execution_node(state):
            return self._observability_node(state)
        if self._answer_synthesis_node(state):
            return self._observability_node(state)
        self._finalize_node(state)
        return self._observability_node(state)

    def _confirmed_action_node(self, state: AgentKernelRunState) -> bool:
        started = time.perf_counter()
        action_verb, _action_id = self.services.action_message_parts(state.message)
        state.action_verb = action_verb
        if not action_verb:
            state.record_node('confirmed_action', 'skipped', started_at=started)
            return False

        decision = RouteDecision(
            task_type='action',
            route_track='CONFIRMED_ACTION',
            intent='execute_pending_action',
            confidence=1.0,
            risk_level='high',
            reason='agent_kernel_chat_confirmation',
        )
        result = self.services.execute_pending_action(state.message, state.ctx, decision)
        trace = self.services.build_base_trace(
            state.message,
            self.services.normalize_query(state.message),
            [],
            state.ctx,
            state.started_at,
        )
        trace.update({
            'selected_skill': 'execute_pending_action',
            'confidence': 1.0,
            'route_reason': 'chat_confirmation',
            'risk_result': 'confirmed_action',
            'confirmation_policy': 'execute_or_cancel_pending_action',
            'final_executor': 'agent_kernel.action_runtime.execute_pending_action',
            'output_verification_result': 'ok' if not result.error_code else result.error_code,
        })
        state.result = result
        state.trace = trace
        state.record_node('confirmed_action', 'completed', started_at=started, action_verb=action_verb)
        return True

    def _understanding_node(self, state: AgentKernelRunState) -> bool:
        started = time.perf_counter()
        state.understanding = self.services.understand_query(state.message, state.ctx)
        state.normalized_query = str(getattr(state.understanding, 'normalized_query', '') or self.services.normalize_query(state.message))
        state.record_node(
            'understanding',
            'completed',
            started_at=started,
            normalized_query=state.normalized_query,
            source=getattr(state.understanding, 'source', None),
            llm_status=(getattr(state.understanding, 'diagnostics', {}) or {}).get('status'),
            llm_attempted=(getattr(state.understanding, 'diagnostics', {}) or {}).get('attempted'),
            llm_accepted=(getattr(state.understanding, 'diagnostics', {}) or {}).get('accepted'),
        )
        return False

    def _retrieval_node(self, state: AgentKernelRunState) -> bool:
        started = time.perf_counter()
        state.candidates = self.services.retrieve_skills(
            state.message,
            normalized_query=state.normalized_query,
            understanding=state.understanding,
            top_k=self.top_k,
        )
        state.record_node(
            'skill_retrieval',
            'completed' if state.candidates else 'no_candidates',
            started_at=started,
            candidate_count=len(state.candidates),
            top_skill_ids=candidate_ids(state.candidates)[:8],
        )
        if state.candidates:
            return False

        trace = self.services.build_base_trace(
            state.message,
            state.normalized_query,
            [],
            state.ctx,
            state.started_at,
            understanding=state.understanding,
        )
        trace['fallback_reason'] = 'no_skill_recalled'
        result = self.services.skill_first_clarification_result(
            state.message,
            state.ctx,
            '我还没有找到足够匹配的 HRM 技能。请换一种说法，或补充你要查询的对象、模块和时间范围。',
            trace,
            kind='no_skill_recalled',
            attach_trace=False,
        )
        state.trace = trace
        state.result = result
        return True

    def _skill_provider_node(self, state: AgentKernelRunState) -> bool:
        started = time.perf_counter()
        state.skill_provider_records = self.services.provide_skill_records(
            state.candidates,
            limit=self.top_k,
        )
        state.record_node(
            'skill_provider',
            'completed' if state.skill_provider_records else 'no_provider_records',
            started_at=started,
            record_count=len(state.skill_provider_records),
            exposed_skill_ids=[
                str(getattr(record, 'skill_id', '') or '')
                for record in state.skill_provider_records[:8]
                if getattr(record, 'skill_id', '')
            ],
            schema_mode='planner',
        )
        if state.skill_provider_records:
            return False

        trace = self.services.build_base_trace(
            state.message,
            state.normalized_query,
            state.candidates,
            state.ctx,
            state.started_at,
            understanding=state.understanding,
        )
        trace['fallback_reason'] = 'skill_provider_no_records'
        trace['skill_provider'] = {
            'schema_mode': 'planner',
            'record_count': 0,
            'candidate_records': [],
            'selected': None,
        }
        result = self.services.skill_first_clarification_result(
            state.message,
            state.ctx,
            '召回到了候选 Skill，但能力提供层没有生成可执行合同。已记录路由轨迹，请稍后重试。',
            trace,
            kind='skill_provider_no_records',
            attach_trace=False,
        )
        state.trace = trace
        state.result = result
        return True

    def _planning_node(self, state: AgentKernelRunState) -> bool:
        started = time.perf_counter()
        state.plan = self.services.plan_skill(
            state.message,
            state.ctx,
            state.candidates,
            understanding=state.understanding,
        )
        selected = str(getattr(state.plan, 'selected_skill', '') or '')
        state.card = self.services.get_card(selected)
        state.trace = self.services.trace_from_plan(
            state.message,
            state.ctx,
            state.candidates,
            state.plan,
            state.card,
            state.started_at,
            understanding=state.understanding,
        )
        self._sync_provider_trace(state)
        state.record_node(
            'planning',
            'completed' if state.card else 'no_selected_skill',
            started_at=started,
            selected_skill=selected or None,
            confidence=getattr(state.plan, 'confidence', None),
            planner_source=getattr(state.plan, 'planner_source', None),
            llm_status=(getattr(state.plan, 'diagnostics', {}) or {}).get('status'),
            llm_attempted=(getattr(state.plan, 'diagnostics', {}) or {}).get('attempted'),
            llm_accepted=(getattr(state.plan, 'diagnostics', {}) or {}).get('accepted'),
            deterministic_fallback_used=(getattr(state.plan, 'diagnostics', {}) or {}).get('deterministic_fallback_used'),
        )
        if state.card:
            return False

        write_reason = self.services.unsupported_write_intent(state.message, state.card)
        if write_reason:
            if getattr(state.plan, 'slot_values', None) is None:
                state.plan.slot_values = {}
            state.plan.slot_values['_write_guard'] = write_reason
            guard_card = self.services.get_card('system.unsupported_write_intent') or self.services.get_card('system.workflow_help')
            state.plan.selected_skill = getattr(guard_card, 'id', None) or 'system.unsupported_write_intent'
            state.plan.confidence = max(float(getattr(state.plan, 'confidence', 0.0) or 0.0), 0.72)
            state.plan.fallback_reason = 'unsupported_write_intent'
            state.plan.route_reason = (
                f'{getattr(state.plan, "route_reason", "")}; write_guard_selected_unsupported_write_boundary'
                if getattr(state.plan, 'route_reason', '')
                else 'write_guard_selected_unsupported_write_boundary'
            )
            result = self.services.write_guard_result(state.message, state.ctx, state.plan, guard_card)
            state.trace.update({
                'selected_skill': getattr(guard_card, 'id', None) or 'system.unsupported_write_intent',
                'confidence': round(float(getattr(state.plan, 'confidence', 0.0) or 0.0), 4),
                'route_reason': getattr(state.plan, 'route_reason', ''),
                'risk_result': 'blocked_write_intent',
                'fallback_reason': 'unsupported_write_intent',
                'confirmation_policy': getattr(guard_card, 'confirmation_policy', None),
                'final_executor': 'guardrails.detect_write_intent',
                'output_verification_result': 'ok',
            })
            self._sync_provider_trace(state)
            state.result = result
            state.record_node('planning', 'blocked_write_intent', started_at=started, reason=write_reason)
            return True

        state.trace['fallback_reason'] = getattr(state.plan, 'fallback_reason', None) or 'no_selected_skill'
        self._sync_provider_trace(state)
        state.result = self.services.skill_first_clarification_result(
            state.message,
            state.ctx,
            '我还没能把这句话稳定映射到已注册 Skill。请补充你要做的 HRM 模块、对象或时间范围。',
            state.trace,
            kind='no_selected_skill',
            attach_trace=False,
        )
        return True

    def _slot_reference_node(self, state: AgentKernelRunState) -> bool:
        started = time.perf_counter()
        state.slot_reference = self.services.resolve_slots_and_references(
            state.message,
            state.ctx,
            state.card,
            state.plan,
            state.understanding,
        )
        if state.slot_reference:
            normalized_slots, normalizer_trace = self.services.normalize_slot_values(
                message=state.message,
                card=state.card,
                slot_values=dict(state.slot_reference.slot_values or {}),
            )
            state.slot_reference.slot_values = dict(normalized_slots or {})
            if normalizer_trace:
                existing_refiner = dict(state.slot_reference.refiner or {})
                existing_normalizer = existing_refiner.get('normalizer') if isinstance(existing_refiner.get('normalizer'), dict) else {}
                if existing_normalizer:
                    normalizer_trace = {
                        **dict(normalizer_trace or {}),
                        'applied': bool(existing_normalizer.get('applied') or normalizer_trace.get('applied')),
                        'changes': [
                            *(existing_normalizer.get('changes') or []),
                            *(normalizer_trace.get('changes') or []),
                        ],
                        'sources': [
                            *(existing_normalizer.get('sources') or [existing_normalizer.get('source')] if existing_normalizer.get('source') else []),
                            normalizer_trace.get('source'),
                        ],
                    }
                state.slot_reference.refiner = {
                    **existing_refiner,
                    'normalizer': normalizer_trace,
                }
                source = str(state.slot_reference.resolution_source or '')
                if normalizer_trace.get('applied') and 'normalizer' not in source:
                    state.slot_reference.resolution_source = f'{source}+normalizer' if source else 'normalizer'
            state.plan.slot_values = dict(state.slot_reference.slot_values or {})
            state.plan.missing_slots = list(state.slot_reference.missing_slots or [])
            payload = state.slot_reference.to_dict()
            state.trace['slot_fill_result'] = payload
            state.trace['slot_reference_result'] = payload
            refined = self.services.refine_skill_after_reference(
                state.message,
                state.ctx,
                state.card,
                state.plan,
                state.slot_reference,
            )
            if refined:
                state.plan, state.card = refined
                state.trace['selected_skill'] = getattr(state.plan, 'selected_skill', None)
                state.trace['confidence'] = round(float(getattr(state.plan, 'confidence', 0.0) or 0.0), 4)
                state.trace['route_reason'] = getattr(state.plan, 'route_reason', '')
                state.trace['planner_source'] = getattr(state.plan, 'planner_source', '')
                state.trace['fallback_reason'] = getattr(state.plan, 'fallback_reason', None)
                state.trace['skill_refinement_result'] = {
                    'source': 'slot_reference',
                    'selected_skill': getattr(state.plan, 'selected_skill', None),
                    'route_reason': getattr(state.plan, 'route_reason', ''),
                }
                self._sync_provider_trace(state)
            state.record_node(
                'slot_reference',
                'completed',
                started_at=started,
                slot_keys=list(payload.get('slot_values') or {}),
                missing_slots=payload.get('missing_slots') or [],
                reference_detected=payload.get('reference_detected'),
                workset_reference_detected=payload.get('workset_reference_detected'),
                resolution_source=payload.get('resolution_source'),
                refined_skill=getattr(state.plan, 'selected_skill', None) if refined else None,
            )
        else:
            state.record_node('slot_reference', 'skipped', started_at=started)
        return False

    def _risk_node(self, state: AgentKernelRunState) -> bool:
        started = time.perf_counter()
        explicit_write_block = self.services.unsupported_write_intent(state.message, state.card)
        slot_values = getattr(state.plan, 'slot_values', {}) or {}
        explicit_write_block = explicit_write_block or slot_values.get('_write_guard')
        if explicit_write_block:
            slot_values['_write_guard'] = explicit_write_block
            state.plan.slot_values = slot_values
            guard_card = self.services.get_card('system.unsupported_write_intent') or state.card
            if guard_card:
                state.card = guard_card
                state.plan.selected_skill = getattr(guard_card, 'id', None)
                state.plan.confidence = max(float(getattr(state.plan, 'confidence', 0.0) or 0.0), 0.72)
                state.plan.fallback_reason = 'unsupported_write_intent'
                state.plan.route_reason = (
                    f'{getattr(state.plan, "route_reason", "")}; write_guard_selected_unsupported_write_boundary'
                    if getattr(state.plan, 'route_reason', '')
                    else 'write_guard_selected_unsupported_write_boundary'
                )
            result = self.services.write_guard_result(state.message, state.ctx, state.plan, state.card)
            state.trace.update({
                'selected_skill': getattr(state.plan, 'selected_skill', None),
                'confidence': round(float(getattr(state.plan, 'confidence', 0.0) or 0.0), 4),
                'route_reason': getattr(state.plan, 'route_reason', ''),
                'risk_result': 'blocked_write_intent',
                'fallback_reason': 'unsupported_write_intent',
                'confirmation_policy': getattr(state.card, 'confirmation_policy', None),
                'final_executor': 'guardrails.detect_write_intent',
                'output_verification_result': 'ok',
            })
            self._sync_provider_trace(state)
            state.result = result
            state.record_node('risk_gate', 'blocked_write_intent', started_at=started, reason=explicit_write_block)
            return True

        missing_slots = list(getattr(state.plan, 'missing_slots', []) or [])
        if getattr(state.card, 'side_effect', False) and missing_slots:
            if getattr(state.card, 'id', None) == 'interview.schedule_confirm_create':
                slot_values = getattr(state.plan, 'slot_values', {}) or {}
                latest = slot_values.get('latest_workset_or_item') if isinstance(slot_values.get('latest_workset_or_item'), dict) else {}
                if (
                    'target_candidate' in missing_slots
                    and latest.get('kind') == 'item'
                    and latest.get('entity_type') in {'candidate', 'resume'}
                    and latest.get('entity_id')
                ):
                    state.plan.missing_slots = [slot for slot in missing_slots if slot != 'target_candidate']
                    missing_slots = list(state.plan.missing_slots or [])
            if not missing_slots:
                state.record_node('risk_gate', 'allowed', started_at=started, reason='grounded_required_slots')
                return False
            state.trace.update({
                'risk_result': {
                    'risk_level': getattr(state.card, 'risk_level', None),
                    'side_effect': True,
                    'missing_required_slots': missing_slots,
                },
                'fallback_reason': getattr(state.plan, 'fallback_reason', None) or 'missing_required_side_effect_slots',
                'output_verification_result': 'ok',
            })
            question = (
                '这个操作需要先定位明确对象。请补充：'
                + '、'.join(missing_slots)
                + '。'
            )
            state.result = self.services.skill_first_clarification_result(
                state.message,
                state.ctx,
                question,
                state.trace,
                kind='missing_required_side_effect_slots',
                attach_trace=False,
            )
            state.record_node('risk_gate', 'clarification', started_at=started, missing_slots=missing_slots)
            return True

        if getattr(state.plan, 'fallback_reason', None) == 'write_intent_without_side_effect_skill':
            result = self.services.write_guard_result(state.message, state.ctx, state.plan, state.card)
            state.trace.update({
                'risk_result': 'blocked_write_intent',
                'fallback_reason': state.plan.fallback_reason,
                'final_executor': 'guardrails.detect_write_intent',
                'output_verification_result': 'ok',
            })
            state.result = result
            state.record_node('risk_gate', 'blocked_write_intent', started_at=started, reason=state.plan.fallback_reason)
            return True

        state.record_node('risk_gate', 'allowed', started_at=started)
        return False

    def _decision_node(self, state: AgentKernelRunState) -> bool:
        started = time.perf_counter()
        confidence = float(getattr(state.plan, 'confidence', 0.0) or 0.0)
        if confidence < self.min_confidence:
            state.trace['fallback_reason'] = getattr(state.plan, 'fallback_reason', None) or 'low_confidence'
            state.result = self.services.skill_first_clarification_result(
                state.message,
                state.ctx,
                '我有点不确定你要用哪个 HRM 技能。请补充对象、模块或时间范围，我会继续按新 Skill-first 链路处理。',
                state.trace,
                kind='low_confidence',
                attach_trace=False,
            )
            state.record_node('decision', 'clarification', started_at=started, confidence=confidence)
            return True

        decision = self.services.build_route_decision(state.card, state.plan)
        decision = self.services.normalize_tool_plan_inputs(state.message, decision)
        self.services.apply_skill_specific_input_guards(state.card, decision, state.message)
        state.decision = decision
        state.trace['route_decision'] = decision.to_dict()
        self._sync_provider_trace(state)
        state.record_node('decision', 'completed', started_at=started, confidence=confidence)
        return False

    def _skill_playbook_node(self, state: AgentKernelRunState) -> bool:
        started = time.perf_counter()
        if state.decision is None or state.card is None:
            state.record_node('skill_playbook', 'skipped', started_at=started)
            return False
        provider = state.trace.get('skill_provider') if isinstance(state.trace.get('skill_provider'), dict) else {}
        selected_provider = provider.get('selected') if isinstance(provider.get('selected'), dict) else {}
        state.playbook = self.services.build_skill_playbook(
            state.message,
            state.ctx,
            state.card,
            state.decision,
            selected_provider,
        )
        playbook_payload = (
            state.playbook.to_dict()
            if hasattr(state.playbook, 'to_dict')
            else dict(state.playbook or {})
        )
        state.trace['skill_playbook'] = playbook_payload
        diagnostics = playbook_payload.get('diagnostics') if isinstance(playbook_payload.get('diagnostics'), dict) else {}
        state.record_node(
            'skill_playbook',
            'completed',
            started_at=started,
            mode=playbook_payload.get('mode'),
            executor_tool=playbook_payload.get('executor_tool'),
            evidence_step_count=len(playbook_payload.get('evidence_plan') or []),
            source=diagnostics.get('source'),
            llm_status=diagnostics.get('llm_status'),
        )
        return False

    def _permission_node(self, state: AgentKernelRunState) -> bool:
        started = time.perf_counter()
        state.permission_result = self.services.permission_result(state.card, state.ctx)
        state.trace['permission_result'] = state.permission_result
        if state.permission_result == 'allowed':
            state.record_node('permission_gate', 'allowed', started_at=started)
            return False

        result = self.services.permission_denied_result(
            state.message,
            state.ctx,
            state.decision,
            state.permission_result,
        )
        state.trace.update({
            'final_executor': 'guardrails.check_skill_permission',
            'output_verification_result': 'ok',
            'fallback_reason': 'permission_denied',
        })
        state.result = result
        state.record_node('permission_gate', 'denied', started_at=started, permission_result=state.permission_result)
        return True

    def _execution_node(self, state: AgentKernelRunState) -> bool:
        started = time.perf_counter()
        try:
            state.result = self.services.execute_skill(
                state.message,
                state.ctx,
                state.card,
                state.decision,
                state.trace,
            )
            outputs = getattr(state.result, '_kernel_tool_outputs', None)
            state.tool_outputs = list(outputs) if isinstance(outputs, list) else []
            state.record_node(
                'execution',
                'completed',
                started_at=started,
                final_executor=state.trace.get('final_executor'),
                tool_output_count=len(state.tool_outputs),
            )
            return False
        except Exception:
            logger.exception('agent_kernel_execution_failed')
            state.trace['fallback_reason'] = 'skill_first_execution_exception'
            state.result = self.services.skill_first_clarification_result(
                state.message,
                state.ctx,
                '新 Agent Kernel 执行链路遇到异常，已记录路由轨迹和失败样本。请稍后重试或补充更明确的查询条件。',
                state.trace,
                kind='execution_exception',
                error_code='skill_first_execution_exception',
                attach_trace=False,
            )
            state.record_node('execution', 'exception', started_at=started)
            return True

    def _answer_synthesis_node(self, state: AgentKernelRunState) -> bool:
        started = time.perf_counter()
        if state.result is None or state.decision is None:
            state.record_node('answer_synthesis', 'skipped', started_at=started)
            return False
        synthesis_trace = self.services.synthesize_answer(
            state.message,
            state.ctx,
            state.card,
            state.result,
            state.tool_outputs,
            state.decision,
            state.trace,
            state.playbook,
        )
        if isinstance(synthesis_trace, dict):
            state.trace['answer_synthesis'] = synthesis_trace
        else:
            synthesis_trace = {'source': 'deterministic', 'applied': False}
            state.trace['answer_synthesis'] = synthesis_trace
        state.record_node(
            'answer_synthesis',
            'completed',
            started_at=started,
            source=synthesis_trace.get('source'),
            applied=synthesis_trace.get('applied'),
            playbook_used=synthesis_trace.get('playbook_used'),
        )
        return False

    def _finalize_node(self, state: AgentKernelRunState) -> None:
        started = time.perf_counter()
        result = state.result
        if result is None:
            return
        state.trace['latency_ms'] = round((time.perf_counter() - state.started_at) * 1000, 1)
        state.trace['output_verification_result'] = self.services.verify_output(result, state.card)
        state.record_node(
            'output_verification',
            'ok' if state.trace['output_verification_result'] == 'ok' else 'failed',
            started_at=started,
            output_verification_result=state.trace['output_verification_result'],
        )
        if state.trace['output_verification_result'] != 'ok':
            result.fallback_used = True
            result.error_code = result.error_code or 'skill_first_output_verification_failed'

    def _observability_node(self, state: AgentKernelRunState) -> Optional[ExecutionResult]:
        started = time.perf_counter()
        target = state.result
        if target is None:
            state.record_node('observability', 'skipped', started_at=started)
            return None
        self._sync_provider_trace(state)
        state.trace['latency_ms'] = round((time.perf_counter() - state.started_at) * 1000, 1)
        state.record_node(
            'observability',
            'completed',
            started_at=started,
            fallback_reason=state.trace.get('fallback_reason'),
            final_executor=state.trace.get('final_executor'),
        )
        self._attach_kernel_trace(state, result=target, action_verb=state.action_verb)
        return target

    def _attach_kernel_trace(
        self,
        state: AgentKernelRunState,
        *,
        result: Optional[ExecutionResult] = None,
        action_verb: Optional[str] = None,
    ) -> None:
        target = result or state.result
        if target is None:
            return
        state.trace['kernel'] = {
            'name': 'hrm_agent_kernel',
            'version': '2026-06-04',
            'top_k': self.top_k,
            'nodes': state.node_trace(),
        }
        state.trace['kernel_nodes'] = state.node_trace()
        state.trace['learning_events'] = self.services.learning_events(
            state.message,
            state.ctx,
            state.trace,
            result=target,
            action_verb=action_verb or state.action_verb,
        )
        self.services.attach_trace(target, state.trace)

    def _sync_provider_trace(self, state: AgentKernelRunState) -> None:
        if not isinstance(state.trace, dict):
            return
        selected_skill = str(
            getattr(state.plan, 'selected_skill', None)
            or state.trace.get('selected_skill')
            or ''
        )
        selected_record = None
        for record in state.skill_provider_records or []:
            if str(getattr(record, 'skill_id', '') or '') == selected_skill:
                selected_record = record
                break
        if selected_record is None and state.card is not None:
            try:
                selected_record = self.services.provide_skill_record_for_card(state.card)
            except Exception:
                logger.exception('agent_kernel_provider_record_for_card_failed')
                selected_record = None

        provider_trace = {
            'schema_mode': 'planner',
            'record_count': len(state.skill_provider_records or []),
            'candidate_records': [
                self._provider_record_summary(record)
                for record in (state.skill_provider_records or [])[: self.top_k]
            ],
            'selected': self._provider_selected_payload(selected_record),
        }
        state.trace['skill_provider'] = provider_trace

    @staticmethod
    def _provider_record_summary(record: object) -> dict:
        model_schema = (
            record.to_model_schema()
            if hasattr(record, 'to_model_schema')
            else dict(getattr(record, 'model_schema', {}) or {})
        )
        executor_metadata = dict(getattr(record, 'executor_metadata', {}) or {})
        recall_evidence = dict(getattr(record, 'recall_evidence', {}) or {})
        tool_schema = model_schema.get('tool_schema') if isinstance(model_schema.get('tool_schema'), dict) else {}
        return {
            'rank': model_schema.get('rank') or recall_evidence.get('rank'),
            'skill_id': getattr(record, 'skill_id', None) or model_schema.get('id'),
            'executor_tool': executor_metadata.get('executor_tool') or model_schema.get('executor_tool'),
            'purpose': executor_metadata.get('purpose'),
            'risk_level': executor_metadata.get('risk_level') or model_schema.get('risk_level'),
            'side_effect': executor_metadata.get('side_effect'),
            'confirmation_policy': executor_metadata.get('confirmation_policy') or model_schema.get('confirmation_policy'),
            'tool_schema_fields': list(tool_schema.keys()),
            'recall_score': recall_evidence.get('score') if recall_evidence else model_schema.get('recall_score'),
            'score_gap_to_next': model_schema.get('score_gap_to_next'),
            'anti_score': recall_evidence.get('anti_score') if recall_evidence else model_schema.get('anti_score'),
            'match_reason': recall_evidence.get('match_reason') if recall_evidence else model_schema.get('match_reason'),
        }

    @staticmethod
    def _provider_selected_payload(record: object | None) -> Optional[dict]:
        if record is None:
            return None
        model_schema = (
            record.to_model_schema()
            if hasattr(record, 'to_model_schema')
            else dict(getattr(record, 'model_schema', {}) or {})
        )
        return {
            'skill_id': getattr(record, 'skill_id', None) or model_schema.get('id'),
            'model_schema': model_schema,
            'executor_metadata': dict(getattr(record, 'executor_metadata', {}) or {}),
            'recall_evidence': dict(getattr(record, 'recall_evidence', {}) or {}),
        }
