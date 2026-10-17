from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, Optional

from models import (
    ApprovalInstance, ApprovalRecord, Employee, EmployeeChange, EmployeeRequest,
    Offer, OperationLog, Position, Probation, Resignation, SalaryProfile, db,
)

# 导入通知服务
try:
    from services.notification_service import notify_approval_pending, notify_approval_result
    NOTIFICATION_AVAILABLE = True
except ImportError:
    NOTIFICATION_AVAILABLE = False


SALARY_CHANGE_TYPES = {
    'salary_adjustment',
    'performance_salary_adjustment',
    'salary_change',
    'compensation_adjustment',
    'raise',
    'promotion',
    '调薪',
    '薪资调整',
    '薪酬调整',
    '绩效调薪',
    '晋升',
}

EMPLOYEE_REQUEST_APPROVAL_TYPES = {
    'employee_request',
    'leave',
    'overtime',
    'business_trip',
    'travel',
    'makeup',
    'makeup_card',
    '补卡',
    '请假',
    '加班',
    '出差',
}


def new_id() -> str:
    return str(uuid.uuid4())


def payroll_money(value: Any):
    return (value or Decimal('0')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def salary_profile_for_employee(employee_id: str) -> SalaryProfile:
    profile = SalaryProfile.query.filter_by(employee_id=employee_id).first()
    if profile:
        return profile
    profile = SalaryProfile(
        id=new_id(),
        employee_id=employee_id,
        status='active',
        remark='Created automatically for compensation linkage',
    )
    db.session.add(profile)
    return profile


def normalize_employee_change_type(change_type: Optional[str]) -> str:
    return (change_type or '').strip()


def is_salary_related_change(change: EmployeeChange) -> bool:
    change_type = normalize_employee_change_type(change.change_type)
    if change_type in SALARY_CHANGE_TYPES:
        return True
    return bool(change.salary_adjustment and change.salary_adjustment != Decimal('0'))


def apply_salary_adjustment_to_profile(change: EmployeeChange) -> Optional[SalaryProfile]:
    if not change.salary_adjustment:
        return None
    profile = salary_profile_for_employee(change.employee_id)
    applied_marker = f'[employee_change:{change.id}:applied]'
    if applied_marker in (profile.remark or ''):
        return profile
    before = profile.performance_salary or Decimal('0')
    profile.performance_salary = payroll_money(before + change.salary_adjustment)
    profile.status = profile.status or 'active'
    note = f'{applied_marker} Applied salary adjustment {change.salary_adjustment} from {change.change_type}'
    if change.reason:
        note = f'{note}: {change.reason}'
    profile.remark = f'{profile.remark}\n{note}' if profile.remark else note
    profile.updated_at = datetime.utcnow()
    return profile


def apply_employee_change(change: EmployeeChange) -> None:
    employee = Employee.query.get(change.employee_id)
    if not employee or change.status not in ('effective', 'approved'):
        return
    if change.after_department_id:
        employee.department_id = change.after_department_id
    if change.after_position_id:
        employee.position_id = change.after_position_id
        position = Position.query.get(change.after_position_id)
        if position:
            employee.job_title = position.title
    if change.after_level:
        employee.remark = (employee.remark or '') + f'\n职级调整为：{change.after_level}'
    if is_salary_related_change(change):
        apply_salary_adjustment_to_profile(change)


def update_business_by_approval(approval: ApprovalInstance, approved: bool, comment: str = '') -> None:
    status = 'approved' if approved else 'rejected'
    if approval.business_type == 'offer':
        obj = Offer.query.get(approval.business_id)
        if obj:
            obj.approval_status = status
            obj.status = 'sent' if approved else 'rejected'
    elif approval.business_type in EMPLOYEE_REQUEST_APPROVAL_TYPES:
        obj = EmployeeRequest.query.get(approval.business_id)
        if obj:
            obj.status = status
    elif approval.business_type == 'probation':
        obj = Probation.query.get(approval.business_id)
        if obj:
            obj.status = status
            if approved:
                obj.result = obj.result or '通过'
                obj.regular_date = obj.regular_date or date.today()
                employee = Employee.query.get(obj.employee_id)
                if employee:
                    employee.status = 'active'
    elif approval.business_type == 'resignation':
        obj = Resignation.query.get(approval.business_id)
        if obj:
            obj.status = status
            if approved:
                obj.actual_leave_date = obj.actual_leave_date or obj.expected_leave_date or date.today()
                employee = Employee.query.get(obj.employee_id)
                if employee:
                    employee.status = 'resigned'
    elif approval.business_type == 'employee_change':
        obj = EmployeeChange.query.get(approval.business_id)
        if obj:
            obj.status = status
            if approved:
                apply_employee_change(obj)
    elif approval.business_type == 'onboarding':
        obj = Employee.query.get(approval.business_id)
        if obj and approved:
            obj.status = 'probation'
    approval.remark = comment or approval.remark


def complete_approval(
    approval_id: str,
    action: str,
    *,
    approver_id: Optional[str],
    comment: Optional[str] = None,
    operation_source: str = 'approval_center',
    ip: Optional[str] = None,
) -> Dict[str, Any]:
    if action not in ('approve', 'reject'):
        raise ValueError('Unsupported action')
    approval = ApprovalInstance.query.get(approval_id)
    if not approval:
        raise LookupError('Approval not found')
    if approval.status != 'pending':
        raise RuntimeError('Approval already completed')

    approved = action == 'approve'
    final_status = 'approved' if approved else 'rejected'
    final_comment = comment or ('审批通过' if approved else '审批驳回')
    approval.status = final_status
    approval.completed_at = datetime.utcnow()
    approval.updated_at = datetime.utcnow()
    update_business_by_approval(approval, approved, final_comment)
    record = ApprovalRecord(
        id=new_id(),
        approval_id=approval.id,
        approver_id=approver_id,
        action=approval.status,
        comment=final_comment,
    )
    db.session.add(record)
    db.session.add(OperationLog(
        id=new_id(),
        user_id=approver_id,
        module='approval',
        action=action,
        target_id=approval_id,
        detail={'comment': final_comment, 'source': operation_source},
        ip=ip,
    ))
    return {'approval': approval, 'record': record}
