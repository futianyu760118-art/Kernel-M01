"""组织架构接口：部门树、组织架构图、汇报关系与部门负责人维护。"""

from __future__ import annotations

from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required

from models import Department, Employee, db
from services.authz import ORG_READ_ROLES, ORG_WRITE_ROLES, require_roles
from services.org_structure import (
    OrgStructureError,
    build_department_tree,
    build_org_chart,
    set_department_manager,
    validate_and_set_manager,
)
from services.tenancy import current_tenant_id

org_bp = Blueprint('org', __name__)


def _wants_inactive() -> bool:
    value = (request.args.get('includeInactive') or '').strip().lower()
    return value in ('1', 'true', 'yes')


@org_bp.route('/tree', methods=['GET'])
@jwt_required()
def org_tree():
    rbac_err = require_roles(ORG_READ_ROLES, '无权限查看组织架构')
    if rbac_err:
        return rbac_err
    tree = build_department_tree(current_tenant_id(), include_inactive=_wants_inactive())
    return jsonify({'tree': tree})


@org_bp.route('/chart', methods=['GET'])
@jwt_required()
def org_chart():
    rbac_err = require_roles(ORG_READ_ROLES, '无权限查看组织架构')
    if rbac_err:
        return rbac_err
    payload = build_org_chart(current_tenant_id(), include_inactive=_wants_inactive())
    return jsonify(payload)


@org_bp.route('/employees/<employee_id>/manager', methods=['PUT'])
@jwt_required()
def set_employee_manager(employee_id):
    rbac_err = require_roles(ORG_WRITE_ROLES, '无权限维护汇报关系')
    if rbac_err:
        return rbac_err

    tenant_id = current_tenant_id()
    employee = Employee.query.filter_by(id=employee_id, tenant_id=tenant_id).first()
    if employee is None:
        return jsonify({'error': '员工不存在'}), 404

    data = request.get_json(silent=True) or {}
    try:
        manager = validate_and_set_manager(employee, data.get('managerId'))
    except OrgStructureError as exc:
        db.session.rollback()
        return jsonify({'error': exc.message}), exc.status_code

    db.session.commit()
    return jsonify({
        'employee': employee.to_dict(),
        'manager': manager.to_dict() if manager else None,
    })


@org_bp.route('/departments/<department_id>/manager', methods=['PUT'])
@jwt_required()
def set_department_manager_route(department_id):
    rbac_err = require_roles(ORG_WRITE_ROLES, '无权限维护部门负责人')
    if rbac_err:
        return rbac_err

    tenant_id = current_tenant_id()
    department = Department.query.filter_by(id=department_id, tenant_id=tenant_id).first()
    if department is None:
        return jsonify({'error': '部门不存在'}), 404

    data = request.get_json(silent=True) or {}
    try:
        manager = set_department_manager(department, data.get('managerId'))
    except OrgStructureError as exc:
        db.session.rollback()
        return jsonify({'error': exc.message}), exc.status_code

    db.session.commit()
    return jsonify({
        'department': department.to_dict(),
        'manager': manager.to_dict() if manager else None,
    })
