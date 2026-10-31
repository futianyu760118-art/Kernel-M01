"""Role based access helpers for API routes."""

from __future__ import annotations

from typing import Iterable, Optional, Set

from flask import jsonify
from flask_jwt_extended import get_jwt_identity

from models import User


ALL_ROLES: Set[str] = {
    'admin', 'hr_admin', 'recruiter', 'manager',
    'employee', 'finance', 'executive', 'viewer',
}
PRIVILEGED_ROLES: Set[str] = {'admin', 'hr_admin'}
RECRUITMENT_ROLES: Set[str] = {'admin', 'hr_admin', 'recruiter', 'manager', 'executive'}
INTERVIEW_ROLES: Set[str] = {'admin', 'hr_admin', 'recruiter', 'manager', 'executive'}
ORG_READ_ROLES: Set[str] = {'admin', 'hr_admin', 'manager', 'executive'}
ORG_WRITE_ROLES: Set[str] = {'admin', 'hr_admin'}
APPROVAL_ROLES: Set[str] = {'admin', 'hr_admin', 'manager', 'executive'}
TRAINING_READ_ROLES: Set[str] = {'admin', 'hr_admin', 'manager', 'executive'}
TRAINING_WRITE_ROLES: Set[str] = {'admin', 'hr_admin', 'manager'}
PAYROLL_READ_ROLES: Set[str] = {'admin', 'hr_admin', 'finance', 'executive'}
PAYROLL_WRITE_ROLES: Set[str] = {'admin', 'hr_admin', 'finance'}
SYSTEM_ROLES: Set[str] = {'admin', 'hr_admin'}


def current_user() -> Optional[User]:
    user_id = get_jwt_identity()
    user = User.query.get(user_id) if user_id else None
    if user and getattr(user, 'status', None) not in (None, 'active'):
        return None
    return user


def current_role() -> Optional[str]:
    user = current_user()
    return user.role if user else None


def deny(message: str = '无权限执行此操作', status: int = 403):
    return jsonify({'error': message}), status


def allowed(roles: Iterable[str]) -> bool:
    role = current_role()
    return bool(role and role in set(roles))


def require_roles(roles: Iterable[str], message: str = '无权限执行此操作'):
    if not allowed(roles):
        return deny(message)
    return None


RESOURCE_ACCESS = {
    # Recruitment
    'candidates': {'read': RECRUITMENT_ROLES, 'write': RECRUITMENT_ROLES},
    'offers': {'read': RECRUITMENT_ROLES, 'write': RECRUITMENT_ROLES},

    # Organization and employee lifecycle
    'departments': {'read': ORG_READ_ROLES, 'write': ORG_WRITE_ROLES},
    'positions': {'read': ORG_READ_ROLES, 'write': ORG_WRITE_ROLES},
    'employees': {'read': ORG_READ_ROLES, 'write': ORG_WRITE_ROLES},
    'onboarding-tasks': {'read': ORG_READ_ROLES, 'write': ORG_WRITE_ROLES},
    'probations': {'read': ORG_READ_ROLES, 'write': ORG_WRITE_ROLES},
    'employee-changes': {'read': ORG_READ_ROLES, 'write': ORG_WRITE_ROLES},
    'resignations': {'read': ORG_READ_ROLES, 'write': ORG_WRITE_ROLES},
    'contracts': {'read': ORG_READ_ROLES, 'write': ORG_WRITE_ROLES},
    'attendance-records': {'read': ORG_READ_ROLES | PAYROLL_READ_ROLES, 'write': ORG_WRITE_ROLES},
    'employee-requests': {'read': APPROVAL_ROLES, 'write': APPROVAL_ROLES},

    # Performance and compensation
    'performance-cycles': {'read': ORG_READ_ROLES, 'write': ORG_WRITE_ROLES},
    'performance-templates': {'read': ORG_READ_ROLES, 'write': ORG_WRITE_ROLES},
    'performance-reviews': {'read': ORG_READ_ROLES, 'write': ORG_WRITE_ROLES},
    'salary-profiles': {'read': PAYROLL_READ_ROLES, 'write': PAYROLL_WRITE_ROLES},
    'payroll-records': {'read': PAYROLL_READ_ROLES, 'write': PAYROLL_WRITE_ROLES},

    # Training/exam administration
    'training-courses': {'read': TRAINING_READ_ROLES, 'write': TRAINING_WRITE_ROLES},
    'training-plans': {'read': TRAINING_READ_ROLES, 'write': TRAINING_WRITE_ROLES},
    'training-records': {'read': TRAINING_READ_ROLES, 'write': TRAINING_WRITE_ROLES},
    'course-categories': {'read': TRAINING_READ_ROLES, 'write': TRAINING_WRITE_ROLES},
    'courses': {'read': TRAINING_READ_ROLES, 'write': TRAINING_WRITE_ROLES},
    'course-chapters': {'read': TRAINING_READ_ROLES, 'write': TRAINING_WRITE_ROLES},
    'course-hours': {'read': TRAINING_READ_ROLES, 'write': TRAINING_WRITE_ROLES},
    'course-attachments': {'read': TRAINING_READ_ROLES, 'write': TRAINING_WRITE_ROLES},
    'course-assignments': {'read': TRAINING_READ_ROLES, 'write': TRAINING_WRITE_ROLES},
    'course-progress': {'read': TRAINING_READ_ROLES, 'write': TRAINING_WRITE_ROLES},
    'hour-progress': {'read': TRAINING_READ_ROLES, 'write': TRAINING_WRITE_ROLES},
    'exam-questions': {'read': TRAINING_READ_ROLES, 'write': TRAINING_WRITE_ROLES},
    'exam-papers': {'read': TRAINING_READ_ROLES, 'write': TRAINING_WRITE_ROLES},
    'exam-tasks': {'read': TRAINING_READ_ROLES, 'write': TRAINING_WRITE_ROLES},
    'exam-assignments': {'read': TRAINING_READ_ROLES, 'write': TRAINING_WRITE_ROLES},
    'exam-attempts': {'read': TRAINING_READ_ROLES, 'write': TRAINING_WRITE_ROLES},
    'learning-paths': {'read': TRAINING_READ_ROLES, 'write': TRAINING_WRITE_ROLES},
    'learning-path-assignments': {'read': TRAINING_READ_ROLES, 'write': TRAINING_WRITE_ROLES},
    'competency-models': {'read': TRAINING_READ_ROLES, 'write': TRAINING_WRITE_ROLES},
    'competency-assessments': {'read': TRAINING_READ_ROLES, 'write': TRAINING_WRITE_ROLES},
    'pk-seasons': {'read': TRAINING_READ_ROLES, 'write': TRAINING_WRITE_ROLES},
    'rewards': {'read': TRAINING_READ_ROLES, 'write': TRAINING_WRITE_ROLES},
    'exchange-records': {'read': TRAINING_READ_ROLES, 'write': TRAINING_WRITE_ROLES},

    # System
    'roles': {'read': SYSTEM_ROLES, 'write': SYSTEM_ROLES},
    'users': {'read': SYSTEM_ROLES, 'write': SYSTEM_ROLES},
    'operation-logs': {'read': SYSTEM_ROLES, 'write': SYSTEM_ROLES},
    'notifications': {'read': ALL_ROLES, 'write': SYSTEM_ROLES},
    'feedbacks': {'read': ALL_ROLES, 'write': ALL_ROLES},
}


def roles_for_resource(resource: str, write: bool = False) -> Set[str]:
    rule = RESOURCE_ACCESS.get(resource)
    if not rule:
        return PRIVILEGED_ROLES if write else ORG_READ_ROLES
    return set(rule['write' if write else 'read'])


def require_resource_access(resource: str, write: bool = False):
    roles = roles_for_resource(resource, write=write)
    action = '执行此操作' if write else '查看此数据'
    return require_roles(roles, f'无权限{action}')
