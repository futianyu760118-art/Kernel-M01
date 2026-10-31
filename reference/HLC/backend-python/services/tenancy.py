"""Tenant helpers for the SaaS edition.

The first SaaS version uses a shared database with tenant_id isolation.  This
module keeps tenant lookup, request scoping, and default tenant behavior in one
place so routes do not each invent their own rules.
"""

from __future__ import annotations

import os
import re
import uuid
from datetime import datetime
from typing import Any

from flask import g, has_request_context, jsonify, request
from flask_jwt_extended import get_jwt, get_jwt_identity, verify_jwt_in_request

from models import Tenant, TenantModule, db
from services.extension_registry import (
    EXTENSION_MODULE_KEY_SET,
    extension_module_for_path,
    is_extension_module,
)
from services.platform_entitlements import effective_tenant_access


TENANT_CODE_PREFIX = os.getenv('TENANT_CODE_PREFIX', 'HPT').strip().upper() or 'HPT'
TENANT_CODE_WIDTH = int(os.getenv('TENANT_CODE_WIDTH', '8') or '8')
DEFAULT_TENANT_SLUG = (
    os.getenv('DEFAULT_TENANT_SLUG', f'{TENANT_CODE_PREFIX}-{1:0{TENANT_CODE_WIDTH}d}').strip()
    or f'{TENANT_CODE_PREFIX}-{1:0{TENANT_CODE_WIDTH}d}'
)
DEFAULT_TENANT_NAME = os.getenv('DEFAULT_TENANT_NAME', '默认企业').strip() or '默认企业'
PLATFORM_TENANT_SLUG = 'platform'
TENANT_CODE_RE = re.compile(rf'^{re.escape(TENANT_CODE_PREFIX)}-(\d{{{TENANT_CODE_WIDTH}}})$', re.IGNORECASE)

SAAS_MODULE_KEYS = (
    'recruitment',
    'organization',
    'attendance',
    'approval',
    'performance',
    'payroll',
    'training_exam',
    'assistant',
    'feedback',
    'system',
)

MANAGED_MODULE_KEYS = SAAS_MODULE_KEYS + tuple(sorted(EXTENSION_MODULE_KEY_SET))

HRM_RESOURCE_MODULES = {
    'jobs': 'recruitment',
    'resumes': 'recruitment',
    'candidates': 'recruitment',
    'interviews': 'recruitment',
    'offers': 'recruitment',
    'departments': 'organization',
    'positions': 'organization',
    'employees': 'organization',
    'onboarding-tasks': 'organization',
    'probations': 'organization',
    'employee-changes': 'organization',
    'resignations': 'organization',
    'contracts': 'organization',
    'attendance-records': 'attendance',
    'attendance': 'attendance',
    'employee-requests': 'approval',
    'approval-instances': 'approval',
    'approval-records': 'approval',
    'performance-cycles': 'performance',
    'performance-templates': 'performance',
    'performance-reviews': 'performance',
    'salary-profiles': 'payroll',
    'payroll-records': 'payroll',
    'training-courses': 'training_exam',
    'training-plans': 'training_exam',
    'training-records': 'training_exam',
    'course-categories': 'training_exam',
    'courses': 'training_exam',
    'course-chapters': 'training_exam',
    'course-hours': 'training_exam',
    'course-attachments': 'training_exam',
    'course-assignments': 'training_exam',
    'course-progress': 'training_exam',
    'hour-progress': 'training_exam',
    'exam-questions': 'training_exam',
    'exam-papers': 'training_exam',
    'exam-tasks': 'training_exam',
    'exam-assignments': 'training_exam',
    'exam-attempts': 'training_exam',
    'learning-paths': 'training_exam',
    'learning-path-assignments': 'training_exam',
    'wrong-questions': 'training_exam',
    'course-reviews': 'training_exam',
    'point-logs': 'training_exam',
    'certificates': 'training_exam',
    'discussions': 'training_exam',
    'discussion-replies': 'training_exam',
    'discussion-likes': 'training_exam',
    'pk-seasons': 'training_exam',
    'rewards': 'training_exam',
    'exchange-records': 'training_exam',
    'competency-models': 'performance',
    'competency-assessments': 'performance',
    'feedbacks': 'feedback',
    'feedback-attachments': 'feedback',
    'users': 'system',
    'roles': 'system',
    'settings': 'system',
    'notifications': 'system',
    'operation-logs': 'system',
}

PATH_MODULE_PREFIXES = (
    ('/api/jobs', 'recruitment'),
    ('/api/resumes', 'recruitment'),
    ('/api/interviews', 'recruitment'),
    ('/api/tasks', 'recruitment'),
    ('/api/portal', 'training_exam'),
    ('/api/hrm/assistant', 'assistant'),
    ('/api/hrm/export/exam-results', 'training_exam'),
    ('/api/hrm/training-', 'training_exam'),
    ('/api/hrm/exam-', 'training_exam'),
    ('/api/hrm/course-', 'training_exam'),
    ('/api/hrm/learning-', 'training_exam'),
    ('/api/hrm/payroll-', 'payroll'),
    ('/api/hrm/salary-', 'payroll'),
    ('/api/hrm/attendance', 'attendance'),
    ('/api/hrm/approval-', 'approval'),
    ('/api/hrm/performance-', 'performance'),
    ('/api/hrm/feedback', 'feedback'),
)


def normalize_slug(value: str | None, *, default: str = DEFAULT_TENANT_SLUG) -> str:
    raw = (value or default or '').strip()
    if not raw:
        return default
    if TENANT_CODE_RE.fullmatch(raw):
        return raw.upper()
    text = raw.lower()
    text = re.sub(r'[^a-z0-9_-]+', '-', text).strip('-')
    return text or default


def next_tenant_slug() -> str:
    """Return the next SaaS customer code, e.g. HPT-00000002."""
    max_number = 0
    for row in db.session.query(Tenant.slug).all():
        match = TENANT_CODE_RE.fullmatch((row[0] or '').strip())
        if match:
            max_number = max(max_number, int(match.group(1)))
    return f'{TENANT_CODE_PREFIX}-{max_number + 1:0{TENANT_CODE_WIDTH}d}'


def default_tenant() -> Tenant:
    tenant = Tenant.query.filter_by(slug=DEFAULT_TENANT_SLUG).first()
    if tenant:
        return tenant
    tenant = Tenant(
        id=str(uuid.uuid4()),
        name=DEFAULT_TENANT_NAME,
        slug=DEFAULT_TENANT_SLUG,
        status='active',
        plan_code='enterprise',
        max_users=200,
        max_employees=2000,
        max_storage_mb=102400,
        ai_monthly_quota=10000,
    )
    db.session.add(tenant)
    db.session.flush()
    seed_tenant_modules(tenant.id)
    return tenant


def find_tenant_by_slug(slug: str | None) -> Tenant | None:
    normalized = normalize_slug(slug)
    if normalized == PLATFORM_TENANT_SLUG:
        return None
    return Tenant.query.filter_by(slug=normalized).first()


def seed_tenant_modules(tenant_id: str, enabled: bool = True) -> None:
    existing = {
        row.module_key
        for row in TenantModule.query.filter_by(tenant_id=tenant_id).all()
    }
    for module_key in SAAS_MODULE_KEYS:
        if module_key in existing:
            continue
        db.session.add(TenantModule(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            module_key=module_key,
            enabled=enabled,
            limits_json={},
        ))


def tenant_claims_for(tenant: Tenant | None, *, is_platform_admin: bool = False) -> dict[str, Any]:
    return {
        'tenant_id': tenant.id if tenant else None,
        'tenant_slug': tenant.slug if tenant else PLATFORM_TENANT_SLUG,
        'tenant_name': tenant.name if tenant else '平台管理',
        'is_platform_admin': bool(is_platform_admin),
    }


def current_tenant_id() -> str | None:
    if not has_request_context():
        return None
    return getattr(g, 'tenant_id', None)


def current_tenant() -> Tenant | None:
    if not has_request_context():
        return None
    return getattr(g, 'tenant', None)


def is_platform_admin() -> bool:
    if not has_request_context():
        return False
    return bool(getattr(g, 'is_platform_admin', False))


_TENANT_ORM_FILTER_REGISTERED = False
TENANT_ORM_FILTER_EXCLUDED_TABLES = {'settings'}


def register_tenant_orm_filters() -> None:
    """Add request-time tenant criteria to most ORM SELECT statements."""
    global _TENANT_ORM_FILTER_REGISTERED
    if _TENANT_ORM_FILTER_REGISTERED:
        return

    from sqlalchemy import event
    from sqlalchemy.orm import Session, with_loader_criteria
    from models import TENANT_SCOPED_MODELS

    scoped_models = tuple(
        model for model in TENANT_SCOPED_MODELS
        if getattr(model, '__tablename__', None) not in TENANT_ORM_FILTER_EXCLUDED_TABLES
    )

    @event.listens_for(Session, 'do_orm_execute')
    def _add_tenant_criteria(execute_state):
        if not execute_state.is_select:
            return
        if execute_state.execution_options.get('skip_tenant_criteria'):
            return
        tenant_id = current_tenant_id()
        if not tenant_id or is_platform_admin():
            return
        statement = execute_state.statement
        for model in scoped_models:
            statement = statement.options(
                with_loader_criteria(
                    model,
                    lambda cls: cls.tenant_id == tenant_id,
                    include_aliases=True,
                )
            )
        execute_state.statement = statement

    @event.listens_for(Session, 'before_flush')
    def _assign_request_tenant(session, _flush_context, _instances):
        tenant_id = current_tenant_id()
        if not tenant_id or is_platform_admin():
            return
        scoped_model_set = set(scoped_models)
        for obj in session.new:
            if type(obj) in scoped_model_set and not getattr(obj, 'tenant_id', None):
                obj.tenant_id = tenant_id

    _TENANT_ORM_FILTER_REGISTERED = True


def set_request_tenant_context() -> None:
    g.tenant_id = None
    g.tenant = None
    g.is_platform_admin = False

    if request.method == 'OPTIONS':
        return

    try:
        verify_jwt_in_request(optional=True)
    except Exception:
        return

    try:
        claims = get_jwt() or {}
    except RuntimeError:
        return
    identity = get_jwt_identity()
    account_type = claims.get('account_type') or 'hrm'
    account = None
    if identity:
        from models import EmployeePortalAccount, User

        if account_type == 'employee_portal':
            account = db.session.get(EmployeePortalAccount, identity)
        else:
            account = db.session.get(User, identity)
        if account is None or getattr(account, 'status', None) not in (None, 'active'):
            return jsonify({
                'error': '账号已停用或不存在，请重新登录',
                'code': 'ACCOUNT_INACTIVE',
            }), 403

    claim_is_platform_admin = (
        bool(claims.get('is_platform_admin'))
        and claims.get('role') == 'platform_admin'
        and claims.get('tenant_id') is None
        and account_type == 'hrm'
    )
    persisted_is_platform_admin = bool(
        account is not None
        and isinstance(account, User)
        and account.status == 'active'
        and account.role == 'platform_admin'
        and account.tenant_id is None
    )
    # A platform request needs both the original platform-only JWT claim and
    # the account's current database authority.  This revokes old tokens after
    # role, tenant, or status changes without letting an ordinary old token
    # gain authority merely because its database row was later promoted.
    g.is_platform_admin = claim_is_platform_admin and persisted_is_platform_admin
    tenant_id = claims.get('tenant_id')
    if tenant_id:
        tenant = db.session.get(Tenant, tenant_id)
        if tenant:
            g.tenant = tenant
            g.tenant_id = tenant.id


def tenant_required_response():
    return jsonify({
        'error': '缺少企业租户上下文，请使用企业编码重新登录',
        'code': 'TENANT_REQUIRED',
    }), 403


def platform_required_response():
    return jsonify({
        'error': '需要平台管理员权限',
        'code': 'PLATFORM_ADMIN_REQUIRED',
    }), 403


def module_disabled_response(module_key: str):
    return jsonify({
        'error': '当前企业未开通该模块，请联系平台管理员',
        'code': 'MODULE_DISABLED',
        'module': module_key,
    }), 403


def tenant_limit_response(code: str, message: str, *, limit: int | None, current: int):
    return jsonify({
        'error': message,
        'code': code,
        'limit': limit,
        'current': current,
    }), 403


def module_for_request_path(path: str) -> str | None:
    extension_module = extension_module_for_path(path)
    if extension_module:
        return extension_module
    for prefix, module_key in PATH_MODULE_PREFIXES:
        if path.startswith(prefix):
            return module_key
    if path.startswith('/api/hrm/'):
        resource = path[len('/api/hrm/'):].strip('/').split('/', 1)[0]
        return HRM_RESOURCE_MODULES.get(resource)
    return None


def module_enabled_for_tenant(tenant_id: str, module_key: str) -> bool:
    if is_extension_module(module_key):
        module = TenantModule.query.filter_by(tenant_id=tenant_id, module_key=module_key).first()
        return bool(module and module.enabled)
    if module_key not in SAAS_MODULE_KEYS:
        return True
    module = TenantModule.query.filter_by(tenant_id=tenant_id, module_key=module_key).first()
    # Existing single-company deployments should keep working until modules are seeded.
    return True if module is None else bool(module.enabled)


def enforce_request_tenant_boundary():
    if request.method == 'OPTIONS':
        return None
    if not request.path.startswith('/api/'):
        return None
    allowed_prefixes = (
        '/api/auth/login',
        '/api/auth/register',
        '/api/platform/auth/login',
        '/api/portal/auth/login',
        '/api/public/enterprise-applications',
        '/api/health',
        '/api/ready',
        '/api/media/',
    )
    if any(request.path.startswith(prefix) for prefix in allowed_prefixes):
        return None
    if request.path.startswith('/api/platform/'):
        if not is_platform_admin():
            return platform_required_response()
        return None
    if current_tenant_id() is None:
        return tenant_required_response()
    tenant = current_tenant()
    decision = effective_tenant_access(tenant)
    if not decision.allowed:
        return jsonify({
            'error': decision.message,
            'code': decision.code,
        }), 403
    module_key = module_for_request_path(request.path)
    if module_key and not module_enabled_for_tenant(current_tenant_id(), module_key):
        return module_disabled_response(module_key)
    return None


def apply_tenant_scope(query, model):
    tenant_id = current_tenant_id()
    if getattr(model, '__tablename__', None) in TENANT_ORM_FILTER_EXCLUDED_TABLES:
        return query
    if tenant_id and hasattr(model, 'tenant_id'):
        return query.filter(getattr(model, 'tenant_id') == tenant_id)
    return query


def assign_tenant(obj, tenant_id: str | None = None):
    if getattr(obj, '__tablename__', None) in TENANT_ORM_FILTER_EXCLUDED_TABLES:
        return obj
    target_tenant_id = tenant_id or current_tenant_id()
    if target_tenant_id and hasattr(obj, 'tenant_id') and not getattr(obj, 'tenant_id', None):
        setattr(obj, 'tenant_id', target_tenant_id)
    return obj


def enforce_create_limit(model):
    tenant = current_tenant()
    tenant_id = current_tenant_id()
    if not tenant or not tenant_id:
        return None

    from models import Employee, User

    if model is User and tenant.max_users:
        current = User.query.filter_by(tenant_id=tenant_id).count()
        if current >= tenant.max_users:
            return tenant_limit_response(
                'TENANT_USER_LIMIT_EXCEEDED',
                '后台用户数已达到当前套餐上限',
                limit=tenant.max_users,
                current=current,
            )
    if model is Employee and tenant.max_employees:
        current = Employee.query.filter_by(tenant_id=tenant_id).count()
        if current >= tenant.max_employees:
            return tenant_limit_response(
                'TENANT_EMPLOYEE_LIMIT_EXCEEDED',
                '员工档案数已达到当前套餐上限',
                limit=tenant.max_employees,
                current=current,
            )
    return None


def object_belongs_to_current_tenant(obj) -> bool:
    tenant_id = current_tenant_id()
    if getattr(obj, '__tablename__', None) in TENANT_ORM_FILTER_EXCLUDED_TABLES:
        return True
    if not tenant_id or not hasattr(obj, 'tenant_id'):
        return True
    return getattr(obj, 'tenant_id', None) == tenant_id


def tenant_usage(tenant_id: str) -> dict[str, Any]:
    from services.tenant_metering import tenant_usage as _tenant_usage
    return _tenant_usage(tenant_id)
