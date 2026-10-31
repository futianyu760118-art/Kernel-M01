"""AI assistant audit logging.

The audit table is intentionally append-only and read-only from the UI.  It
captures enough prompt/response context for model governance without giving one
ordinary user access to another user's conversation history.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import sessionmaker

from models import AssistantAuditLog, AssistantSession, db


MAX_MESSAGE_CHARS = 6000
MAX_RESPONSE_CHARS = 12000
_AUDIT_LINK_SESSION_META_KEY = 'auditLinkSessionId'
logger = logging.getLogger('hengpintong.assistant.audit')


def _clip_text(value: Any, limit: int) -> str:
    text = str(value or '')
    return text if len(text) <= limit else text[:limit] + '...'


def sanitize_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    sanitized: List[Dict[str, Any]] = []
    for item in messages or []:
        if not isinstance(item, dict):
            continue
        sanitized.append({
            'role': item.get('role'),
            'content': _clip_text(item.get('content'), MAX_MESSAGE_CHARS),
        })
    return sanitized


def total_prompt_chars(messages: List[Dict[str, Any]]) -> int:
    return sum(len(str(item.get('content') or '')) for item in messages or [] if isinstance(item, dict))


def _new_audit_session():
    return sessionmaker(bind=db.engine, expire_on_commit=False)()


def _persist_audit_payload(target_session, payload: dict[str, Any], *, session_id: Optional[str]) -> None:
    log = AssistantAuditLog(
        id=str(uuid.uuid4()),
        user_id=payload.get('user_id'),
        session_id=session_id,
        request_id=payload.get('request_id'),
        provider=payload.get('provider'),
        model=payload.get('model'),
        model_profile=payload.get('model_profile'),
        use_case=payload.get('use_case'),
        resource_domain=payload.get('resource_domain'),
        ai_tier=payload.get('ai_tier'),
        tenant_ai_entitlement_id=payload.get('tenant_ai_entitlement_id'),
        prompt_messages=payload.get('prompt_messages') or [],
        response_content=payload.get('response_content') or '',
        response_meta=payload.get('response_meta') or {},
        status=payload.get('status') or 'success',
        error_message=payload.get('error_message'),
        latency_ms=payload.get('latency_ms'),
        prompt_chars=payload.get('prompt_chars') or 0,
        response_chars=payload.get('response_chars') or 0,
    )
    log.tenant_id = payload.get('tenant_id')
    target_session.add(log)
    target_session.flush()
    if payload.get('resource_domain') and payload.get('record_usage', True):
        from services.ai_resource_governance import record_ai_usage

        record_ai_usage(
            tenant_id=payload.get('tenant_id'),
            user_id=payload.get('user_id'),
            resource_domain=payload.get('resource_domain'),
            use_case=payload.get('use_case'),
            tier=payload.get('ai_tier'),
            provider=payload.get('provider'),
            model=payload.get('model'),
            status=payload.get('status') or 'success',
            units=1,
            request_id=payload.get('request_id'),
            assistant_audit_log_id=log.id,
            prompt_chars=log.prompt_chars,
            response_chars=log.response_chars,
            error_code=(payload.get('response_meta') or {}).get('errorCode'),
            meta=payload.get('usage_meta'),
            session=target_session,
            commit=False,
        )


def link_audits_to_session(
    *,
    tenant_id: Optional[str],
    request_id: Optional[str],
    user_id: str,
    session_id: Optional[str],
) -> int:
    if not tenant_id or not request_id or not user_id or not session_id:
        return 0
    audit_session = None
    try:
        audit_session = _new_audit_session()
        committed_session = (
            audit_session.query(AssistantSession)
            .filter(
                AssistantSession.id == session_id,
                AssistantSession.user_id == user_id,
                AssistantSession.tenant_id == tenant_id,
            )
            .one_or_none()
        )
        if committed_session is None:
            return 0
        candidates = (
            audit_session.query(AssistantAuditLog)
            .filter(
                AssistantAuditLog.tenant_id == tenant_id,
                AssistantAuditLog.request_id == request_id,
                AssistantAuditLog.user_id == user_id,
                AssistantAuditLog.session_id.is_(None),
            )
            .all()
        )
        linked = 0
        for row in candidates:
            meta = dict(row.response_meta or {})
            if meta.get(_AUDIT_LINK_SESSION_META_KEY) != session_id:
                continue
            meta.pop(_AUDIT_LINK_SESSION_META_KEY, None)
            row.response_meta = meta
            row.session_id = session_id
            linked += 1
        audit_session.commit()
        return linked
    except Exception:
        if audit_session is not None:
            audit_session.rollback()
        logger.exception('assistant_audit_session_link_failed')
        return 0
    finally:
        if audit_session is not None:
            audit_session.close()


def record_llm_call(
    *,
    tenant_id: Optional[str],
    user_id: Optional[str],
    session_id: Optional[str],
    request_id: Optional[str],
    provider: str,
    model: str,
    model_profile: Optional[str],
    use_case: Optional[str],
    messages: List[Dict[str, Any]],
    response_content: Optional[str],
    response_meta: Optional[Dict[str, Any]] = None,
    status: str = 'success',
    error_message: Optional[str] = None,
    latency_ms: Optional[int] = None,
    resource_domain: Optional[str] = None,
    ai_tier: Optional[str] = None,
    tenant_ai_entitlement_id: Optional[str] = None,
    record_usage: bool = True,
    usage_meta: Optional[Dict[str, Any]] = None,
) -> None:
    audit_session = None
    try:
        audit_session = _new_audit_session()
        prompt_messages = sanitize_messages(messages)
        response = _clip_text(response_content, MAX_RESPONSE_CHARS)
        payload = {
            'tenant_id': tenant_id,
            'user_id': user_id,
            'request_id': request_id,
            'provider': provider,
            'model': model,
            'model_profile': model_profile,
            'use_case': use_case,
            'resource_domain': resource_domain,
            'ai_tier': ai_tier,
            'tenant_ai_entitlement_id': tenant_ai_entitlement_id,
            'record_usage': bool(record_usage),
            'usage_meta': dict(usage_meta or {}),
            'prompt_messages': prompt_messages,
            'response_content': response,
            'response_meta': dict(response_meta or {}),
            'status': status,
            'error_message': _clip_text(error_message, 2000) if error_message else None,
            'latency_ms': latency_ms,
            'prompt_chars': total_prompt_chars(prompt_messages),
            'response_chars': len(response),
        }
        persisted_session_id = session_id
        if session_id:
            committed_session = (
                audit_session.query(AssistantSession)
                .filter(
                    AssistantSession.id == session_id,
                    AssistantSession.tenant_id == tenant_id,
                    AssistantSession.user_id == user_id,
                )
                .one_or_none()
            )
            if committed_session is None:
                persisted_session_id = None
                payload['response_meta'][_AUDIT_LINK_SESSION_META_KEY] = session_id
        _persist_audit_payload(audit_session, payload, session_id=persisted_session_id)
        audit_session.commit()
        if payload.get('resource_domain') and payload.get('record_usage', True):
            from services.ai_resource_governance import settle_ai_usage_reservation

            settle_ai_usage_reservation(
                payload.get('tenant_id'),
                payload.get('resource_domain'),
                units=1,
            )
    except Exception:
        if audit_session is not None:
            audit_session.rollback()
        logger.exception('assistant_audit_record_failed', exc_info=False)
    finally:
        if audit_session is not None:
            audit_session.close()
