"""HRM 管理后台鉴权路由。

加固点：
- 邮箱格式与密码强度校验
- 支持姓名/邮箱作为管理后台登录账号，首次登录可强制修改初始密码
- 普通员工不登录管理后台，员工门户使用独立账号体系
- 登录失败计数（内存级，配合 Nginx 限速使用）
- 不在错误信息里区分"用户不存在"与"密码错误"，避免账号枚举
- 记录登录事件日志（含 request_id）
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from datetime import datetime
from threading import Lock

import bcrypt
from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import create_access_token, get_jwt_identity, jwt_required
from sqlalchemy import func

from models import Tenant, User, db
from services.platform_entitlements import effective_tenant_access
from services.tenancy import (
    DEFAULT_TENANT_SLUG,
    PLATFORM_TENANT_SLUG,
    find_tenant_by_slug,
    tenant_claims_for,
)

auth_bp = Blueprint('auth', __name__)
logger = logging.getLogger('hengpintong.auth')

EMAIL_RE = re.compile(r'^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$')
DEFAULT_INITIAL_PASSWORD = 'hj@123'

# 简单的内存级失败计数（多实例部署应迁移到 Redis）
_failed_attempts: dict[str, list[float]] = {}
_failed_lock = Lock()
FAILED_WINDOW_SEC = 600   # 10 分钟窗口
FAILED_THRESHOLD = 5      # 超过则进入慢响应模式
FAILED_DELAY_SEC = 2.0


def _record_failure(key: str) -> None:
    now = time.time()
    with _failed_lock:
        attempts = [t for t in _failed_attempts.get(key, []) if now - t < FAILED_WINDOW_SEC]
        attempts.append(now)
        _failed_attempts[key] = attempts


def _is_throttled(key: str) -> bool:
    now = time.time()
    with _failed_lock:
        attempts = [t for t in _failed_attempts.get(key, []) if now - t < FAILED_WINDOW_SEC]
        _failed_attempts[key] = attempts
        return len(attempts) >= FAILED_THRESHOLD


def _clear_failures(key: str) -> None:
    with _failed_lock:
        _failed_attempts.pop(key, None)


def _validate_register_payload(data: dict) -> str | None:
    """返回错误信息或 None。"""
    email = (data.get('email') or '').strip()
    password = data.get('password') or ''
    name = (data.get('name') or '').strip()

    if not email or not password or not name:
        return '邮箱、密码、姓名均为必填'
    if not EMAIL_RE.match(email):
        return '邮箱格式不正确'
    if len(password) < 8:
        return '密码长度至少 8 位'
    if len(password) > 128:
        return '密码过长'
    # 密码复杂度：至少包含 3 种字符类型（大写/小写/数字/特殊符号）
    import string
    char_classes = sum([
        any(c in string.ascii_lowercase for c in password),
        any(c in string.ascii_uppercase for c in password),
        any(c in string.digits for c in password),
        any(c in '!@#$%^&*()_+-=[]{}|;:,.<>?/' for c in password),
    ])
    if char_classes < 3:
        return '密码需包含大写字母、小写字母、数字、特殊符号中的至少 3 种'
    if len(name) > 64:
        return '姓名过长'
    return None


def _password_policy_error(password: str, *, current_password: str | None = None) -> str | None:
    if not password:
        return '新密码不能为空'
    if len(password) < 8:
        return '新密码长度至少 8 位'
    if len(password) > 128:
        return '新密码过长'
    if current_password and password == current_password:
        return '新密码不能与当前密码相同'
    if password == DEFAULT_INITIAL_PASSWORD:
        return '新密码不能继续使用初始密码'

    import string
    char_classes = sum([
        any(c in string.ascii_lowercase for c in password),
        any(c in string.ascii_uppercase for c in password),
        any(c in string.digits for c in password),
        any(c in '!@#$%^&*()_+-=[]{}|;:,.<>?/' for c in password),
    ])
    if char_classes < 3:
        return '新密码需包含大写字母、小写字母、数字、特殊符号中的至少 3 种'
    return None


def _find_login_user(identifier: str, tenant_id: str | None, *, platform_login: bool = False) -> tuple[User | None, bool]:
    """Return (user, duplicated_name). Identifier can be email or exact name."""
    normalized = identifier.strip()
    if not normalized:
        return None, False

    query = User.query
    if platform_login:
        query = query.filter(User.role == 'platform_admin')
    elif tenant_id and hasattr(User, 'tenant_id'):
        query = query.filter(User.tenant_id == tenant_id)

    user = query.filter(func.lower(User.email) == normalized.lower()).first()
    if user:
        return user, False

    matches = query.filter(User.name == normalized).all()
    if len(matches) == 1:
        return matches[0], False
    return None, len(matches) > 1


def _token_for_user(user: User, tenant=None, *, is_platform_admin: bool = False) -> str:
    if tenant is None and not is_platform_admin and getattr(user, 'tenant_id', None):
        tenant = db.session.get(Tenant, user.tenant_id)
    return create_access_token(
        identity=user.id,
        additional_claims={
            'role': user.role,
            'name': user.name,
            'employee_id': user.employee_id,
            'account_type': 'hrm',
            'must_change_password': bool(user.must_change_password),
            **tenant_claims_for(tenant, is_platform_admin=is_platform_admin),
        },
    )


@auth_bp.route('/register', methods=['POST'])
def register():
    import os as _os
    allow_public_registration = (
        _os.getenv('ALLOW_PUBLIC_REGISTRATION', '0') == '1'
        or _os.getenv('ALLOW_REGISTRATION', '0') == '1'
        or
        current_app.config.get('ALLOW_PUBLIC_REGISTRATION') is True
        or current_app.config.get('ALLOW_PUBLIC_REGISTRATION') == '1'
    )
    if not allow_public_registration:
        return jsonify({'error': '公开注册已关闭，请联系管理员创建账号'}), 403

    data = request.get_json(silent=True) or {}
    error = _validate_register_payload(data)
    if error:
        return jsonify({'error': error}), 400

    email = data['email'].strip().lower()
    password = data['password']
    name = data['name'].strip()
    tenant = find_tenant_by_slug(data.get('tenantSlug') or DEFAULT_TENANT_SLUG)
    if not tenant:
        return jsonify({'error': '企业编码不存在'}), 404

    if User.query.filter_by(email=email, tenant_id=tenant.id).first():
        return jsonify({'error': '该邮箱已注册'}), 409

    # 注册频率限制（同 IP 10分钟内最多 5 次）—— 复用 _is_throttled/_record_failure 机制
    register_ip = request.headers.get('X-Forwarded-For', request.remote_addr or '')
    register_fail_key = f'register:{register_ip}'
    if _is_throttled(register_fail_key):
        return jsonify({'error': '注册过于频繁，请稍后再试'}), 429

    password_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    is_first_user = User.query.count() == 0
    # 首用户 admin 必须显式允许；生产环境默认关闭，避免公开注册抢占管理员。
    allow_first_admin = (
        _os.getenv('ALLOW_FIRST_ADMIN', '0') == '1'
        or bool(current_app.config.get('ALLOW_FIRST_ADMIN'))
    )
    user = User(
        id=str(uuid.uuid4()),
        email=email,
        password_hash=password_hash,
        name=name,
        role=('admin' if is_first_user and allow_first_admin else 'employee'),
        status='active',
        tenant_id=tenant.id,
    )

    db.session.add(user)
    db.session.commit()
    # 记录注册频率（复用限流计数）
    _record_failure(register_fail_key)
    logger.info('user_registered', extra={'user_id': user.id, 'role': user.role})
    return jsonify(user.to_dict()), 201


@auth_bp.route('/login', methods=['POST'])
def login():
    data = request.get_json(silent=True) or {}
    identifier = (data.get('identifier') or data.get('email') or '').strip()
    password = data.get('password') or ''
    tenant_slug = (data.get('tenantSlug') or DEFAULT_TENANT_SLUG).strip() or DEFAULT_TENANT_SLUG
    platform_login = tenant_slug.lower() == PLATFORM_TENANT_SLUG
    tenant = None if platform_login else find_tenant_by_slug(tenant_slug)

    if not identifier or not password:
        return jsonify({'error': '账号或密码不能为空'}), 400
    if platform_login:
        return jsonify({
            'error': '平台管理员请使用平台登录入口',
            'code': 'PLATFORM_LOGIN_REQUIRED',
        }), 403
    if not platform_login and not tenant:
        return jsonify({'error': '企业编码不存在'}), 404

    # 失败计数 key（按 IP+账号组合，比单独 IP 更精准）
    remote = request.headers.get('X-Forwarded-For', request.remote_addr) or 'unknown'
    fail_key = f'{remote}:{tenant_slug.lower()}:{identifier.lower()}'

    # 多次失败后强制延迟（轻量级反暴力破解）
    if _is_throttled(fail_key):
        time.sleep(FAILED_DELAY_SEC)

    user, duplicated_name = _find_login_user(identifier, tenant.id if tenant else None, platform_login=platform_login)
    if duplicated_name:
        return jsonify({'error': '姓名账号重复，请使用邮箱登录或联系管理员处理重名账号'}), 409

    # 即使用户不存在也走 bcrypt 比对一次假哈希，避免基于响应时间枚举账号
    dummy_hash = b'$2b$12$00000000000000000000000000000000000000000000000000000'
    candidate_hash = (user.password_hash.encode('utf-8') if user else dummy_hash)
    is_valid = False
    try:
        is_valid = bcrypt.checkpw(password.encode('utf-8'), candidate_hash)
    except (ValueError, TypeError):
        is_valid = False

    if not user or not is_valid:
        _record_failure(fail_key)
        logger.warning(
            'login_failed',
            extra={'identifier': identifier, 'remote': remote, 'user_exists': bool(user)},
        )
        return jsonify({'error': '账号或密码错误'}), 401

    if getattr(user, 'status', 'active') == 'inactive':
        return jsonify({'error': '账号已停用，请联系管理员'}), 403
    if tenant:
        decision = effective_tenant_access(tenant)
        if not decision.allowed:
            return jsonify({'error': decision.message, 'code': decision.code}), 403
    if platform_login and user.role != 'platform_admin':
        return jsonify({'error': '平台入口仅允许平台管理员登录'}), 403
    if getattr(user, 'role', '') == 'employee':
        return jsonify({'error': '普通员工请切换到员工门户，用员工编号登录'}), 403

    user.last_login_at = datetime.utcnow()
    db.session.commit()
    _clear_failures(fail_key)

    logger.info('login_success', extra={'user_id': user.id, 'role': user.role, 'tenant_id': tenant.id if tenant else None})
    user_payload = user.to_dict()
    user_payload.update({
        'tenantId': tenant.id if tenant else None,
        'tenantSlug': tenant.slug if tenant else PLATFORM_TENANT_SLUG,
        'tenantName': tenant.name if tenant else '平台管理',
        'isPlatformAdmin': bool(platform_login),
    })
    return jsonify({'token': _token_for_user(user, tenant, is_platform_admin=platform_login), 'user': user_payload})


@auth_bp.route('/change-password', methods=['POST'])
@jwt_required()
def change_password():
    data = request.get_json(silent=True) or {}
    current_password = data.get('currentPassword') or data.get('oldPassword') or ''
    new_password = data.get('newPassword') or ''

    if not current_password or not new_password:
        return jsonify({'error': '当前密码和新密码均为必填'}), 400

    user = db.session.get(User, get_jwt_identity())
    if not user:
        return jsonify({'error': '账号不存在'}), 404
    if getattr(user, 'status', 'active') == 'inactive':
        return jsonify({'error': '账号已停用，请联系管理员'}), 403

    try:
        current_ok = bcrypt.checkpw(current_password.encode('utf-8'), user.password_hash.encode('utf-8'))
    except (ValueError, TypeError):
        current_ok = False
    if not current_ok:
        return jsonify({'error': '当前密码不正确'}), 401

    policy_error = _password_policy_error(new_password, current_password=current_password)
    if policy_error:
        return jsonify({'error': policy_error}), 400

    user.password_hash = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    user.must_change_password = False
    user.updated_at = datetime.utcnow()
    db.session.commit()

    is_platform_admin = user.role == 'platform_admin' and not getattr(user, 'tenant_id', None)
    tenant = None if is_platform_admin else (db.session.get(Tenant, user.tenant_id) if getattr(user, 'tenant_id', None) else None)
    user_payload = user.to_dict()
    if is_platform_admin:
        user_payload.update({
            'tenantId': None,
            'tenantSlug': PLATFORM_TENANT_SLUG,
            'tenantName': '平台管理',
            'isPlatformAdmin': True,
        })
    elif tenant:
        user_payload.update({
            'tenantId': tenant.id,
            'tenantSlug': tenant.slug,
            'tenantName': tenant.name,
            'isPlatformAdmin': False,
        })

    logger.info('password_changed', extra={'user_id': user.id})
    return jsonify({'token': _token_for_user(user, tenant, is_platform_admin=is_platform_admin), 'user': user_payload})
