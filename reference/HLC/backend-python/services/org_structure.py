"""组织架构服务：部门树、部门负责人解析、汇报关系与下属链。

本模块只负责组织结构的读取与校验，不提交事务（调用方负责 commit）。
所有查询都按 tenant_id 过滤，跨租户一律视为不存在。
"""

from __future__ import annotations

from sqlalchemy import func, or_

from models import Department, Employee, db

# 视为"非在职"的 HRM 员工状态；用于组织展示与人数统计。
INACTIVE_EMPLOYEE_STATUSES = ('inactive', 'resigned', 'terminated', 'departed')
# 视为"停用"的部门状态。
INACTIVE_DEPARTMENT_STATUSES = ('inactive', 'archived', 'disabled')


class OrgStructureError(Exception):
    """组织架构校验失败。"""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _active_employee_filter():
    return or_(
        Employee.status.is_(None),
        ~Employee.status.in_(INACTIVE_EMPLOYEE_STATUSES),
    )


def department_descendant_ids(tenant_id, department_id, *, include_inactive: bool = False) -> set:
    """返回该部门及其所有下级部门的 id 集合（含防环）。"""
    if not department_id:
        return set()

    query = Department.query
    if tenant_id:
        query = query.filter(Department.tenant_id == tenant_id)
    if not include_inactive:
        query = query.filter(
            or_(
                Department.status.is_(None),
                ~Department.status.in_(INACTIVE_DEPARTMENT_STATUSES),
            )
        )
    departments = query.all()

    children: dict = {}
    known = set()
    for department in departments:
        known.add(department.id)
        children.setdefault(department.parent_id, []).append(department.id)

    if department_id not in known:
        return {department_id}

    result = set()
    stack = [department_id]
    while stack:
        current = stack.pop()
        if current in result:
            continue
        result.add(current)
        stack.extend(children.get(current, []))
    return result


def build_department_tree(tenant_id, *, include_inactive: bool = False) -> list:
    """构建嵌套部门树，含负责人与在职人数。"""
    query = Department.query
    if tenant_id:
        query = query.filter(Department.tenant_id == tenant_id)
    if not include_inactive:
        query = query.filter(
            or_(
                Department.status.is_(None),
                ~Department.status.in_(INACTIVE_DEPARTMENT_STATUSES),
            )
        )
    departments = query.order_by(Department.sort_order.asc(), Department.name.asc()).all()

    counts = _employee_counts_by_department(tenant_id)

    nodes: dict = {}
    order: list = []
    for department in departments:
        manager = _employee_in_tenant(tenant_id, department.manager_id)
        nodes[department.id] = {
            'id': department.id,
            'name': department.name,
            'shortName': department.short_name,
            'code': department.code,
            'parentId': department.parent_id,
            'managerId': department.manager_id,
            'managerName': manager.name if manager else None,
            'status': department.status or 'active',
            'employeeCount': counts.get(department.id, 0),
            'totalEmployeeCount': counts.get(department.id, 0),
            'children': [],
        }
        order.append(department)

    roots: list = []
    for department in order:
        node = nodes[department.id]
        parent = nodes.get(department.parent_id) if department.parent_id else None
        if parent is not None:
            parent['children'].append(node)
        else:
            roots.append(node)

    # 汇总含下级的总人数（分部/上级部门展示用）
    for node in roots:
        _aggregate_employee_count(node)
    return roots


def _aggregate_employee_count(node: dict) -> int:
    total = node.get('employeeCount', 0)
    for child in node['children']:
        total += _aggregate_employee_count(child)
    node['totalEmployeeCount'] = total
    return total


def build_org_chart(tenant_id, *, include_inactive: bool = False) -> dict:
    """组织架构图数据：部门 + 在职员工 + 汇报线。"""
    departments = Department.query
    employees = Employee.query.filter(_active_employee_filter())
    if tenant_id:
        departments = departments.filter(Department.tenant_id == tenant_id)
        employees = employees.filter(Employee.tenant_id == tenant_id)
    if not include_inactive:
        departments = departments.filter(
            or_(
                Department.status.is_(None),
                ~Department.status.in_(INACTIVE_DEPARTMENT_STATUSES),
            )
        )

    department_rows = departments.all()
    employee_rows = employees.all()

    department_payload = [
        {
            'id': department.id,
            'name': department.name,
            'parentId': department.parent_id,
            'managerId': department.manager_id,
            'status': department.status or 'active',
        }
        for department in department_rows
    ]

    employee_ids = {employee.id for employee in employee_rows}
    employee_payload = []
    reporting_lines = []
    for employee in employee_rows:
        employee_payload.append({
            'id': employee.id,
            'employeeNo': employee.employee_no,
            'name': employee.name,
            'departmentId': employee.department_id,
            'positionId': employee.position_id,
            'jobTitle': employee.job_title,
            'managerId': employee.manager_id,
            'status': employee.status or 'active',
        })
        # 仅保留本租户内的有效汇报线，避免展示悬空上级
        if employee.manager_id and employee.manager_id in employee_ids:
            reporting_lines.append({
                'employeeId': employee.id,
                'managerId': employee.manager_id,
            })

    return {
        'departments': department_payload,
        'employees': employee_payload,
        'reportingLines': reporting_lines,
    }


def resolve_department_manager(tenant_id, department_id):
    """解析部门负责人；无负责人或人员不存在时返回 None（不报错）。"""
    department = Department.query.filter_by(id=department_id, tenant_id=tenant_id).first()
    if department is None or not department.manager_id:
        return None
    return _employee_in_tenant(tenant_id, department.manager_id)


def managed_department_ids_for_user(tenant_id, employee_id) -> set:
    """某负责人管理的部门集合（含所有下级）。

    同时考虑两种来源：
    1. 部门负责人字段（Department.manager_id）
    2. 员工直属上级（Employee.manager_id）—— 兼容 OA 同步的上级关系
    """
    if not employee_id:
        return set()

    owned = Department.query.filter_by(tenant_id=tenant_id, manager_id=employee_id).all()
    result = set()
    for department in owned:
        result |= department_descendant_ids(tenant_id, department.id)

    direct = (
        db.session.query(Employee.department_id)
        .filter(
            Employee.tenant_id == tenant_id,
            Employee.manager_id == employee_id,
            Employee.department_id.isnot(None),
        )
        .distinct()
    )
    for (department_id,) in direct:
        result |= department_descendant_ids(tenant_id, department_id)

    return result


def employee_subordinate_ids(tenant_id, employee_id) -> set:
    """递归下属员工 id 集合（含防环）。"""
    if not employee_id:
        return set()

    rows = (
        db.session.query(Employee.id, Employee.manager_id)
        .filter(Employee.tenant_id == tenant_id)
        .all()
    )
    children: dict = {}
    for child_id, manager_id in rows:
        children.setdefault(manager_id, []).append(child_id)

    result = set()
    stack = list(children.get(employee_id, []))
    while stack:
        current = stack.pop()
        if current in result:
            continue
        result.add(current)
        stack.extend(children.get(current, []))
    return result


def validate_and_set_manager(employee, manager_id):
    """校验并设置员工直属上级；返回上级 Employee 或 None。

    校验：不能自引用、上级必须同租户、不得成环。
    """
    tenant_id = employee.tenant_id
    cleaned = (manager_id or '').strip() if isinstance(manager_id, str) else manager_id
    if not cleaned:
        employee.manager_id = None
        return None

    if cleaned == employee.id:
        raise OrgStructureError('不能将自己设为直属上级')

    manager = _employee_in_tenant(tenant_id, cleaned)
    if manager is None:
        raise OrgStructureError('直属上级不存在或不属于当前企业')

    seen = {employee.id}
    current = manager
    while current is not None:
        if current.id in seen:
            raise OrgStructureError('汇报关系存在环，无法设置')
        seen.add(current.id)
        current = _employee_in_tenant(tenant_id, current.manager_id) if current.manager_id else None

    employee.manager_id = manager.id
    return manager


def set_department_manager(department, manager_id):
    """校验并设置部门负责人；返回负责人 Employee 或 None。"""
    tenant_id = department.tenant_id
    cleaned = (manager_id or '').strip() if isinstance(manager_id, str) else manager_id
    if not cleaned:
        department.manager_id = None
        return None

    manager = _employee_in_tenant(tenant_id, cleaned)
    if manager is None:
        raise OrgStructureError('负责人不存在或不属于当前企业')

    department.manager_id = manager.id
    return manager


def _employee_in_tenant(tenant_id, employee_id):
    if not employee_id:
        return None
    query = Employee.query.filter(Employee.id == employee_id)
    if tenant_id:
        query = query.filter(Employee.tenant_id == tenant_id)
    return query.first()


def _employee_counts_by_department(tenant_id) -> dict:
    query = db.session.query(Employee.department_id, func.count(Employee.id)).filter(
        _active_employee_filter(),
        Employee.department_id.isnot(None),
    )
    if tenant_id:
        query = query.filter(Employee.tenant_id == tenant_id)
    return {department_id: count for department_id, count in query.group_by(Employee.department_id).all()}
