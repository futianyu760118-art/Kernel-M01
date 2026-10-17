"""User ↔ Employee 绑定逻辑（脚本与管理端点共用）。

匹配规则（幂等）：User.email == Employee.email（忽略大小写、去空白）。
"""

from __future__ import annotations

from typing import Dict

from models import db, Employee, User


def bind_users_by_email(dry_run: bool = False, commit: bool = True) -> Dict[str, int]:
    """按 email 绑定 User.employee_id，返回统计。

    Args:
        dry_run: True 只统计不写库。
        commit: True 时绑定后提交事务（dry_run=True 时忽略）。

    Returns:
        {'matched', 'already_bound', 'no_employee_match', 'users_total'}
    """
    stats = {'matched': 0, 'already_bound': 0, 'no_employee_match': 0, 'users_total': 0}

    # 预建 email→employee 索引（小写）
    employees = Employee.query.all()
    emp_by_email: dict[str, Employee] = {}
    for emp in employees:
        if emp.email:
            emp_by_email.setdefault(emp.email.strip().lower(), emp)

    users = User.query.all()
    stats['users_total'] = len(users)
    seen_employee_ids: set[str] = set()

    for user in users:
        if user.employee_id:
            stats['already_bound'] += 1
            seen_employee_ids.add(user.employee_id)
            continue
        if not user.email:
            stats['no_employee_match'] += 1
            continue
        emp = emp_by_email.get(user.email.strip().lower())
        if not emp or emp.id in seen_employee_ids:
            stats['no_employee_match'] += 1
            continue
        if not dry_run:
            user.employee_id = emp.id
        seen_employee_ids.add(emp.id)
        stats['matched'] += 1

    if not dry_run and commit and stats['matched']:
        db.session.commit()
    return stats
