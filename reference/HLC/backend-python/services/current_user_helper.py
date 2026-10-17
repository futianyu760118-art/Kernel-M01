"""当前员工解析工具。

供个人学习门户（portal 蓝图）使用：从 JWT 解析当前登录账号绑定的员工档案。

设计：
- 员工门户 JWT 使用 employee_portal_accounts 作为身份主体，并携带 employee_id。
- 旧版 HRM 用户 JWT 如绑定 employee_id，仍保留兼容解析，便于测试和渐进迁移。
- 参照 services/assistant_core/memory.py:128-136 的解析模式。
"""

from __future__ import annotations

from typing import Optional, Tuple

from flask import jsonify
from flask_jwt_extended import get_jwt, get_jwt_identity

from models import db, Employee, EmployeePortalAccount, User


def current_employee() -> Optional[Employee]:
    """返回当前 JWT 对应的 Employee，未绑定/未登录返回 None。"""
    identity = get_jwt_identity()
    if not identity:
        return None
    claims = get_jwt() or {}
    if claims.get('account_type') == 'employee_portal':
        account = db.session.get(EmployeePortalAccount, identity)
        if not account:
            return None
        employee_id = claims.get('employee_id') or account.employee_id
        return db.session.get(Employee, employee_id)

    user = db.session.get(User, identity)
    if not user or not user.employee_id:
        return None
    return db.session.get(Employee, user.employee_id)


def require_employee() -> Tuple[Optional[Employee], Optional[tuple]]:
    """门户端点鉴权助手。

    用法::

        emp, err = require_employee()
        if err:
            return jsonify(err[0]), err[1]
        # emp 一定非 None

    返回二元组 (employee, error)：
    - 鉴权通过： (Employee, None)
    - 鉴权失败： (None, (dict_body, status_code))
    """
    emp = current_employee()
    if emp is None:
        return None, (
            {'error': '当前账号未绑定员工档案，无法使用员工门户'},
            403,
        )
    return emp, None
