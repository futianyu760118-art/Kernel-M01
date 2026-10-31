"""00001 company OA employee master-data synchronization.

The external SQL Server is a strictly read-only source. This module only runs
fixed SELECT statements against OA and writes the resulting roster to HRM's
own MySQL database.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable

import pymssql
from sqlalchemy import text

from models import (
    AttendanceExternalPerson,
    Department,
    Employee,
    EmployeePortalAccount,
    OaEmployeeSyncRun,
    Position,
    Tenant,
    db,
)
from services.employee_portal_accounts import ensure_employee_portal_account


logger = logging.getLogger('hengpintong.oa_employee_sync')

OA_TENANT_SLUG = 'HPT-00000001'
OA_SOURCE_SYSTEM = 'oa_ecology'
OA_SYNC_LOCK_NAME = 'hengpintong:oa-employee-sync:HPT-00000001'
OA_EXPECTED_MIN_ROSTER = 100
OA_EXPECTED_MAX_ROSTER = 500

_running = False


class OaSyncDisabled(RuntimeError):
    pass


class OaSyncBusy(RuntimeError):
    pass


@dataclass(frozen=True)
class OaEmployeeRow:
    person_id: int
    name: str
    mobile: str
    login_id: str
    sex: str
    email: str
    department_id: int | None
    department_name: str
    department_parent_id: int | None
    department_code: str
    workcode: str
    manager_id: int | None
    status: int | None
    jobtitle_id: int | None
    jobtitle_name: str
    erp_user_id: int | None
    start_date: date | None
    modified_at: datetime | None
    department_canceled: str = ''
    subcompany_id: int | None = None


@dataclass(frozen=True)
class OaSubCompanyRow:
    subcompany_id: int
    name: str
    parent_id: int | None
    code: str
    canceled: str
    sort_order: int = 0


@dataclass(frozen=True)
class OaDepartmentRow:
    department_id: int
    name: str
    parent_id: int | None
    subcompany_id: int | None
    code: str
    canceled: str
    sort_order: int = 0
    short_name: str = ''


@dataclass(frozen=True)
class OaEmployeeTypeRow:
    work_number: str
    oa_person_id: int | None
    employee_type: str


@dataclass(frozen=True)
class OaSyncResult:
    run_id: str
    source_count: int
    created_count: int
    updated_count: int
    archived_count: int
    unchanged_count: int


@dataclass(frozen=True)
class OaSyncPreview:
    source_count: int
    create_count: int
    update_count: int
    archive_count: int
    create_names: tuple[str, ...]
    archive_names: tuple[str, ...]


def _clean(value) -> str:
    return str(value or '').strip()


def _to_int(value) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


OA_CANCELED_FLAG = '1'
OA_VALID_RECORD_FLAG = '有效'
OA_EMPLOYEE_TYPE_OFFICE = '后勤'
OA_EMPLOYEE_TYPE_WORKSHOP = '车间'


def _normalize_work_number(value) -> str:
    """工号归一化：去掉前导零，便于 OA('4') 与档案('004') 匹配。"""
    cleaned = _clean(value)
    return cleaned.lstrip('0') or '0'


def _normalize_name(value) -> str:
    return re.sub(r'\s+', '', _clean(value)).casefold()


def _parse_oa_date(value) -> date | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = _clean(value)
    for pattern in ('%Y-%m-%d', '%Y/%m/%d', '%Y%m%d'):
        try:
            return datetime.strptime(raw[:10], pattern).date()
        except ValueError:
            continue
    return None


class OaReadOnlyClient:
    """Execute one fixed OA roster SELECT in a rollback-only transaction."""

    EMPLOYEE_SELECT = """
        SELECT
            a.id AS oa_person_id,
            a.lastname AS employee_name,
            a.mobile,
            a.loginid,
            a.sex,
            a.email,
            a.departmentid,
            d.departmentname,
            d.supdepid,
            d.departmentcode,
            d.canceled AS department_canceled,
            d.subcompanyid1 AS subcompany_id,
            a.workcode,
            a.managerid,
            a.status,
            b.id AS jobtitle_id,
            b.jobtitlename,
            erp_u.FUserID AS erp_user_id,
            a.startdate,
            a.modified
        FROM FWsv.ecology.dbo.HrmResource a
        LEFT JOIN FWsv.ecology.dbo.HrmDepartment d ON d.id = a.departmentid
        LEFT JOIN FWsv.ecology.dbo.HrmJobTitles b ON b.id = a.jobtitle
        LEFT JOIN k3sv.AIS20161019115614.dbo.t_user erp_u ON erp_u.FName = a.lastname
        WHERE (a.status IN (0,1,3) OR a.status IS NULL)
        ORDER BY a.id
    """

    # 工号：泛微自定义字段表。field0 = 工号，与考勤机 BADGENUMBER 同值。
    WORK_NUMBER_SELECT = """
        SELECT
            id AS oa_person_id,
            field0 AS work_number
        FROM FWsv.ecology.dbo.cus_fielddata
        WHERE scope = 'HrmCustomFieldByInfoType'
        ORDER BY id
    """

    # 部门主管：泛微矩阵表。bm = 部门 id，bmzg = 主管人员 id。
    DEPARTMENT_MANAGER_SELECT = """
        SELECT
            bm AS department_id,
            bmzg AS manager_id,
            dataorder
        FROM FWsv.ecology.dbo.Matrixtable_4
        WHERE bm IS NOT NULL AND bmzg IS NOT NULL
        ORDER BY dataorder
    """

    # 分部（一级组织）：在泛微里位于部门之上。
    SUBCOMPANY_SELECT = """
        SELECT
            id AS subcompany_id,
            subcompanyname,
            supsubcomid,
            subcompanycode,
            canceled,
            showorder AS sort_order
        FROM FWsv.ecology.dbo.HrmSubCompany
        WHERE (canceled IS NULL OR canceled = 0 OR canceled = '0')
          AND id > 0
          AND subcompanyname IS NOT NULL
          AND LTRIM(RTRIM(subcompanyname)) <> ''
          AND LOWER(LTRIM(RTRIM(subcompanyname))) NOT IN ('default', '默认')
        ORDER BY showorder, id
    """

    # 全量部门（含没有直属员工的中间层，如"供应链中心"）；排除已封存。
    DEPARTMENT_SELECT = """
        SELECT
            id AS department_id,
            departmentname,
            supdepid,
            subcompanyid1,
            departmentcode,
            canceled,
            showorder AS sort_order,
            departmentmark AS short_name
        FROM FWsv.ecology.dbo.HrmDepartment
        WHERE (canceled IS NULL OR canceled = 0 OR canceled = '0')
        ORDER BY showorder, id
    """

    # 人员类型（后勤/车间）：OA 待遇清单。gh=工号，rylx: 0=后勤 1=车间。
    # 中文条件在 Python 侧按 read 值过滤，避免查询串编码问题。
    EMPLOYEE_TYPE_SELECT = """
        SELECT
            gh AS work_number,
            rylx AS employee_type,
            xm AS oa_person_id,
            zt AS record_status,
            id AS record_id
        FROM FWsv.ecology.dbo.formtable_main_16
        WHERE rylx IS NOT NULL
        ORDER BY id
    """

    def __init__(self, *, host: str, port: int, user: str, password: str):
        self.host = host
        self.port = int(port)
        self.user = user
        self.password = password

    @classmethod
    def from_environment(cls) -> 'OaReadOnlyClient':
        required = {
            'host': os.getenv('OA_DB_HOST'),
            'user': os.getenv('OA_DB_USER'),
            'password': os.getenv('OA_DB_PASSWORD'),
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise OaSyncDisabled('OA sync credentials are not configured')
        return cls(
            host=required['host'],
            port=int(os.getenv('OA_DB_PORT', '2807')),
            user=required['user'],
            password=required['password'],
        )

    def _fetch_dicts(self, statement: str) -> list[dict]:
        connection = pymssql.connect(
            server=self.host,
            port=self.port,
            user=self.user,
            password=self.password,
            database='master',
            charset='CP936',
            tds_version='7.0',
            login_timeout=8,
            timeout=60,
            autocommit=False,
        )
        try:
            cursor = connection.cursor(as_dict=True)
            cursor.execute('SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED')
            cursor.execute(statement)
            return cursor.fetchall()
        finally:
            connection.rollback()
            connection.close()

    def fetch_employees(self) -> list[OaEmployeeRow]:
        return [self._to_row(row) for row in self._fetch_dicts(self.EMPLOYEE_SELECT)]

    def fetch_work_numbers(self) -> dict[int, str]:
        """OA 自定义字段表里的真实工号：person_id -> 工号。"""
        result: dict[int, str] = {}
        for row in self._fetch_dicts(self.WORK_NUMBER_SELECT):
            person_id = _to_int(row.get('oa_person_id'))
            number = _clean(row.get('work_number'))
            if person_id is None or not number:
                continue
            result[person_id] = number
        return result

    def fetch_department_managers(self) -> dict[int, int]:
        """部门主管：department_id -> manager person_id（同部门取 dataorder 最小）。"""
        result: dict[int, int] = {}
        for row in self._fetch_dicts(self.DEPARTMENT_MANAGER_SELECT):
            department_id = _to_int(row.get('department_id'))
            manager_id = _to_int(row.get('manager_id'))
            if department_id is None or manager_id is None:
                continue
            result.setdefault(department_id, manager_id)
        return result

    def fetch_subcompanies(self) -> list[OaSubCompanyRow]:
        """分部列表（一级组织）。"""
        result = []
        for row in self._fetch_dicts(self.SUBCOMPANY_SELECT):
            subcompany_id = _to_int(row.get('subcompany_id'))
            if subcompany_id is None:
                continue
            result.append(OaSubCompanyRow(
                subcompany_id=subcompany_id,
                name=_clean(row.get('subcompanyname')),
                parent_id=_to_int(row.get('supsubcomid')) or None,
                code=_clean(row.get('subcompanycode')),
                canceled=_clean(row.get('canceled')),
                sort_order=_to_int(row.get('sort_order')) or 0,
            ))
        return result

    def fetch_departments(self) -> list[OaDepartmentRow]:
        """全量部门（含无直属员工的中间层）。"""
        result = []
        for row in self._fetch_dicts(self.DEPARTMENT_SELECT):
            department_id = _to_int(row.get('department_id'))
            if department_id is None:
                continue
            result.append(OaDepartmentRow(
                department_id=department_id,
                name=_clean(row.get('departmentname')),
                parent_id=_to_int(row.get('supdepid')) or None,
                subcompany_id=_to_int(row.get('subcompanyid1')),
                code=_clean(row.get('departmentcode')),
                canceled=_clean(row.get('canceled')),
                sort_order=_to_int(row.get('sort_order')) or 0,
                short_name=_clean(row.get('short_name')),
            ))
        return result

    def fetch_employee_types(self) -> list[OaEmployeeTypeRow]:
        """人员类型（后勤/车间）：仅取 zt='有效' 的记录。"""
        result = []
        for row in self._fetch_dicts(self.EMPLOYEE_TYPE_SELECT):
            if _clean(row.get('record_status')) != OA_VALID_RECORD_FLAG:
                continue
            work_number = _normalize_work_number(row.get('work_number'))
            rylx = _to_int(row.get('employee_type'))
            if rylx is None:
                continue
            result.append(OaEmployeeTypeRow(
                work_number=work_number,
                oa_person_id=_to_int(row.get('oa_person_id')),
                employee_type=(
                    OA_EMPLOYEE_TYPE_OFFICE if rylx == 0 else OA_EMPLOYEE_TYPE_WORKSHOP
                ),
            ))
        return result

    @staticmethod
    def _to_row(row: dict) -> OaEmployeeRow:
        return OaEmployeeRow(
            person_id=int(row['oa_person_id']),
            name=_clean(row['employee_name']),
            mobile=_clean(row['mobile']),
            login_id=_clean(row['loginid']),
            sex=_clean(row['sex']),
            email=_clean(row['email']),
            department_id=row['departmentid'],
            department_name=_clean(row['departmentname']),
            department_parent_id=row['supdepid'],
            department_code=_clean(row['departmentcode']),
            workcode=_clean(row['workcode']),
            manager_id=row['managerid'],
            status=row['status'],
            jobtitle_id=row['jobtitle_id'],
            jobtitle_name=_clean(row['jobtitlename']),
            erp_user_id=row['erp_user_id'],
            start_date=_parse_oa_date(row['startdate']),
            modified_at=row['modified'],
            department_canceled=_clean(row.get('department_canceled')),
            subcompany_id=_to_int(row.get('subcompany_id')),
        )


def _validate_rows(rows: list[OaEmployeeRow], *, enforce_roster_bounds: bool) -> None:
    if enforce_roster_bounds and not OA_EXPECTED_MIN_ROSTER <= len(rows) <= OA_EXPECTED_MAX_ROSTER:
        raise ValueError(f'OA roster size outside safety bounds: {len(rows)}')
    person_ids = [row.person_id for row in rows]
    if len(person_ids) != len(set(person_ids)):
        raise ValueError('duplicate OA person id')
    names = [_normalize_name(row.name) for row in rows]
    if any(not name for name in names):
        raise ValueError('blank OA employee name')
    if len(names) != len(set(names)):
        raise ValueError('duplicate OA employee name')
    workcodes = [_clean(row.workcode).casefold() for row in rows if _clean(row.workcode)]
    if len(workcodes) != len(set(workcodes)):
        raise ValueError('duplicate OA employee workcode')
    login_ids = [_clean(row.login_id).casefold() for row in rows if _clean(row.login_id)]
    if len(login_ids) != len(set(login_ids)):
        raise ValueError('duplicate OA employee login id')


def _stable_id(namespace: str, tenant_id: str, external_id: object) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f'hengpintong:{namespace}:{tenant_id}:{external_id}'))


def _employee_status(oa_status: int | None) -> str:
    return 'probation' if oa_status in (0, 3) else 'active'


def _gender(sex: str) -> str | None:
    return {'0': '男', '1': '女'}.get(_clean(sex))


def _get_sync_tenant(tenant_slug: str) -> Tenant:
    if tenant_slug != OA_TENANT_SLUG:
        raise OaSyncDisabled('OA employee sync is only enabled for HPT-00000001')
    tenant = Tenant.query.filter_by(slug=tenant_slug).first()
    if not tenant:
        raise OaSyncDisabled('OA sync tenant does not exist')
    return tenant


@contextmanager
def _sync_lock():
    if not db.engine.dialect.name.startswith('mysql'):
        yield
        return
    connection = db.engine.connect()
    try:
        acquired = connection.execute(
            text('SELECT GET_LOCK(:name, 0)'), {'name': OA_SYNC_LOCK_NAME}
        ).scalar()
        if acquired != 1:
            raise OaSyncBusy('OA employee sync is already running')
        yield
    finally:
        try:
            connection.execute(
                text('SELECT RELEASE_LOCK(:name)'), {'name': OA_SYNC_LOCK_NAME}
            )
        finally:
            connection.close()


def _create_or_update_subcompanies(
    tenant: Tenant,
    subcompanies: list[OaSubCompanyRow],
    existing_by_code: dict,
    existing_by_name: dict,
) -> dict[int, Department]:
    """把泛微分部建成顶层部门节点，还原「一级组织」；已封存/默认分部直接排除。"""
    result: dict[int, Department] = {}
    for source in subcompanies:
        if source.canceled == OA_CANCELED_FLAG:
            continue
        if _normalize_name(source.name) in ('default', '默认'):
            continue
        subcompany_id = _stable_id('oa-subcompany', tenant.id, source.subcompany_id)
        department = db.session.get(Department, subcompany_id)
        if not department and source.code:
            department = existing_by_code.get(source.code.casefold())
        if not department:
            name_candidates = existing_by_name.get(_normalize_name(source.name), [])
            department = name_candidates[0] if len(name_candidates) == 1 else None
        if not department:
            department = Department(id=subcompany_id, tenant_id=tenant.id)
            db.session.add(department)
        department.name = source.name or f'OA分部{source.subcompany_id}'
        department.code = source.code or f'OA-SUB-{source.subcompany_id}'
        department.status = 'active'
        department.sort_order = source.sort_order
        department.description = f'OA同步分部，外部ID：{source.subcompany_id}'
        result[source.subcompany_id] = department
    db.session.flush()
    for source in subcompanies:
        if source.subcompany_id not in result:
            continue
        department = result[source.subcompany_id]
        parent = result.get(source.parent_id) if source.parent_id else None
        department.parent_id = parent.id if parent and parent is not department else None
    return result


def _create_or_update_departments(
    tenant: Tenant,
    departments: Iterable[OaDepartmentRow],
    subcompanies: list[OaSubCompanyRow] | None = None,
) -> tuple[dict[int, Department], dict[int, Department]]:
    """返回 (部门映射, 分部映射)。已封存部门直接排除，不落库。"""
    result = {}
    existing = Department.query.filter_by(tenant_id=tenant.id).all()
    existing_by_code = {_clean(row.code).casefold(): row for row in existing if _clean(row.code)}
    existing_by_name = {}
    for row in existing:
        existing_by_name.setdefault(_normalize_name(row.name), []).append(row)
    subcompany_nodes = _create_or_update_subcompanies(
        tenant, list(subcompanies or []), existing_by_code, existing_by_name
    )
    by_external_id = {row.department_id: row for row in departments}
    claimed: set = set()
    for external_id, source in by_external_id.items():
        if source.canceled == OA_CANCELED_FLAG:
            continue
        department_id = _stable_id('oa-department', tenant.id, external_id)
        department = db.session.get(Department, department_id)
        if department is None and source.code:
            candidate = existing_by_code.get(source.code.casefold())
            department = candidate if candidate and candidate.id not in claimed else None
        if department is None:
            name_candidates = [
                candidate
                for candidate in existing_by_name.get(_normalize_name(source.name), [])
                if candidate.id not in claimed
            ]
            department = name_candidates[0] if len(name_candidates) == 1 else None
        if department is None:
            department = Department(id=department_id, tenant_id=tenant.id)
            db.session.add(department)
        claimed.add(department.id)
        department.name = source.name or f'OA部门{external_id}'
        department.code = source.code or f'OA-{external_id}'
        department.short_name = source.short_name or None
        department.status = 'active'
        department.sort_order = source.sort_order
        department.description = f'OA同步部门，外部ID：{external_id}'
        result[external_id] = department
    db.session.flush()
    for external_id, department in result.items():
        source = by_external_id[external_id]
        if source.parent_id in result and source.parent_id != external_id:
            parent_id = result[source.parent_id].id
        elif source.subcompany_id in subcompany_nodes:
            # 顶级部门（supdepid=0）挂在分部下面，形成一级/二级/三级层级
            parent_id = subcompany_nodes[source.subcompany_id].id
        else:
            parent_id = None
        department.parent_id = parent_id
    return result, subcompany_nodes


def _cleanup_stale_departments(tenant: Tenant, active_department_ids: set) -> int:
    """保守清理：删除 OA 已不存在、且无员工、无子部门的同步部门（含已封存/默认分部）。"""
    removed = 0
    for department in Department.query.filter_by(tenant_id=tenant.id).all():
        if department.id in active_department_ids:
            continue
        if not _clean(department.description).startswith('OA同步'):
            continue
        if Employee.query.filter_by(tenant_id=tenant.id, department_id=department.id).first():
            continue
        has_child = Department.query.filter(
            Department.tenant_id == tenant.id,
            Department.parent_id == department.id,
            Department.id != department.id,
        ).first()
        if has_child:
            continue
        db.session.delete(department)
        removed += 1
    return removed


def _departments_from_employee_rows(rows: Iterable[OaEmployeeRow]) -> list[OaDepartmentRow]:
    """测试/兼容路径：仅有员工行时，从其部门字段推导部门列表。"""
    seen: dict[int, OaDepartmentRow] = {}
    for row in rows:
        if row.department_id is None or row.department_id in seen:
            continue
        seen[row.department_id] = OaDepartmentRow(
            department_id=row.department_id,
            name=row.department_name,
            parent_id=row.department_parent_id,
            subcompany_id=row.subcompany_id,
            code=row.department_code,
            canceled=row.department_canceled,
        )
    return list(seen.values())


def _create_or_update_positions(
    tenant: Tenant,
    rows: list[OaEmployeeRow],
    departments: dict[int, Department],
) -> dict[tuple[int | None, int], Position]:
    result = {}
    for row in rows:
        if row.jobtitle_id is None:
            continue
        key = (row.department_id, row.jobtitle_id)
        if key in result:
            continue
        position_id = _stable_id('oa-position', tenant.id, f'{row.department_id}:{row.jobtitle_id}')
        position = db.session.get(Position, position_id)
        if not position:
            position = Position(id=position_id, tenant_id=tenant.id)
            db.session.add(position)
        position.title = row.jobtitle_name or f'OA岗位{row.jobtitle_id}'
        position.department_id = departments.get(row.department_id).id if row.department_id in departments else None
        position.status = 'active'
        result[key] = position
    return result


def _match_existing_employees(tenant: Tenant, rows: list[OaEmployeeRow]) -> dict[int, Employee]:
    existing = Employee.query.filter_by(tenant_id=tenant.id).all()
    by_oa_id = {_clean(employee.oa_person_id): employee for employee in existing if employee.oa_person_id}
    by_name: dict[str, list[Employee]] = {}
    for employee in existing:
        by_name.setdefault(_normalize_name(employee.name), []).append(employee)

    matched = {}
    used_employee_ids = set()
    for row in rows:
        employee = by_oa_id.get(str(row.person_id))
        if not employee:
            candidates = [
                candidate for candidate in by_name.get(_normalize_name(row.name), [])
                if candidate.id not in used_employee_ids and not candidate.oa_person_id
            ]
            if len(candidates) > 1:
                raise ValueError(f'ambiguous HRM employee name for OA person id {row.person_id}')
            employee = candidates[0] if candidates else None
        if employee:
            matched[row.person_id] = employee
            used_employee_ids.add(employee.id)
    return matched


def preview_oa_employee_sync(
    *,
    rows: Iterable[OaEmployeeRow] | None = None,
    tenant_slug: str = OA_TENANT_SLUG,
    enforce_roster_bounds: bool | None = None,
) -> OaSyncPreview:
    tenant = _get_sync_tenant(tenant_slug)
    source_rows = list(rows) if rows is not None else OaReadOnlyClient.from_environment().fetch_employees()
    if enforce_roster_bounds is None:
        enforce_roster_bounds = rows is None
    _validate_rows(source_rows, enforce_roster_bounds=enforce_roster_bounds)
    matches = _match_existing_employees(tenant, source_rows)
    matched_ids = {employee.id for employee in matches.values()}
    create_names = tuple(row.name for row in source_rows if row.person_id not in matches)
    archive_names = tuple(
        employee.name
        for employee in Employee.query.filter_by(tenant_id=tenant.id).order_by(Employee.name).all()
        if employee.id not in matched_ids and employee.status != 'inactive'
    )
    return OaSyncPreview(
        source_count=len(source_rows),
        create_count=len(create_names),
        update_count=len(matches),
        archive_count=len(archive_names),
        create_names=create_names,
        archive_names=archive_names,
    )


def sync_oa_employees(
    *,
    rows: Iterable[OaEmployeeRow] | None = None,
    work_numbers: dict[int, str] | None = None,
    department_managers: dict[int, int] | None = None,
    subcompanies: Iterable[OaSubCompanyRow] | None = None,
    departments: Iterable[OaDepartmentRow] | None = None,
    employee_types: Iterable[OaEmployeeTypeRow] | None = None,
    tenant_slug: str = OA_TENANT_SLUG,
    trigger: str = 'scheduled',
    enforce_roster_bounds: bool | None = None,
) -> OaSyncResult:
    tenant = _get_sync_tenant(tenant_slug)
    with _sync_lock():
        run = OaEmployeeSyncRun(
            id=str(uuid.uuid4()), tenant_id=tenant.id, trigger=trigger, status='running'
        )
        db.session.add(run)
        db.session.commit()
        try:
            if rows is not None:
                source_rows = list(rows)
                work_numbers = work_numbers or {}
                department_managers = department_managers or {}
                subcompany_rows = list(subcompanies or [])
                department_rows = (
                    list(departments) if departments is not None
                    else _departments_from_employee_rows(source_rows)
                )
                employee_type_rows = list(employee_types or [])
            else:
                client = OaReadOnlyClient.from_environment()
                source_rows = client.fetch_employees()
                if work_numbers is None:
                    work_numbers = client.fetch_work_numbers()
                if department_managers is None:
                    department_managers = client.fetch_department_managers()
                subcompany_rows = list(subcompanies) if subcompanies is not None else client.fetch_subcompanies()
                department_rows = list(departments) if departments is not None else client.fetch_departments()
                employee_type_rows = (
                    list(employee_types) if employee_types is not None
                    else client.fetch_employee_types()
                )
            if enforce_roster_bounds is None:
                enforce_roster_bounds = rows is None
            _validate_rows(source_rows, enforce_roster_bounds=enforce_roster_bounds)

            departments, subcompany_nodes = _create_or_update_departments(
                tenant, department_rows, subcompany_rows
            )
            positions = _create_or_update_positions(tenant, source_rows, departments)
            matches = _match_existing_employees(tenant, source_rows)
            now = datetime.utcnow()
            created_count = 0
            updated_count = 0
            source_employee_ids = set()
            by_oa_person_id = {}
            confirmed_attendance_people = {
                person.employee_id: person
                for person in AttendanceExternalPerson.query.filter_by(
                    tenant_id=tenant.id,
                    mapping_status='confirmed',
                ).all()
                if person.employee_id and _clean(person.badge_number)
            }
            work_number_types = {
                type_row.work_number: type_row.employee_type
                for type_row in employee_type_rows
                if type_row.work_number
            }
            person_type_map = {
                type_row.oa_person_id: type_row.employee_type
                for type_row in employee_type_rows
                if type_row.oa_person_id is not None
            }

            for row in source_rows:
                employee = matches.get(row.person_id)
                if not employee:
                    employee = Employee(
                        id=_stable_id('oa-employee', tenant.id, row.person_id),
                        tenant_id=tenant.id,
                    )
                    db.session.add(employee)
                    created_count += 1
                else:
                    updated_count += 1

                employee.oa_person_id = str(row.person_id)
                employee.oa_login_id = row.login_id or None
                employee.oa_workcode = row.workcode or None
                employee.oa_status = str(row.status) if row.status is not None else None
                employee.oa_department_id = str(row.department_id) if row.department_id is not None else None
                employee.oa_jobtitle_id = str(row.jobtitle_id) if row.jobtitle_id is not None else None
                employee.erp_user_id = str(row.erp_user_id) if row.erp_user_id is not None else None
                employee.source_system = OA_SOURCE_SYSTEM
                employee.source_modified_at = row.modified_at
                employee.source_synced_at = now
                employee.name = row.name
                employee.phone = row.mobile or None
                employee.gender = _gender(row.sex)
                employee.status = _employee_status(row.status)
                employee.department_id = departments.get(row.department_id).id if row.department_id in departments else None
                employee.position_id = positions.get((row.department_id, row.jobtitle_id)).id if (row.department_id, row.jobtitle_id) in positions else None
                employee.job_title = row.jobtitle_name or None
                employee.hire_date = row.start_date
                attendance_person = confirmed_attendance_people.get(employee.id)
                oa_work_number = _clean(work_numbers.get(row.person_id))
                if attendance_person:
                    employee.employee_no = _clean(attendance_person.badge_number)
                elif oa_work_number:
                    # 真实工号来自 OA 自定义字段表 cus_fielddata.field0
                    employee.employee_no = oa_work_number
                elif employee.source_system == OA_SOURCE_SYSTEM and employee.employee_no == row.workcode:
                    # Older releases stored OA workcode in the HRM employee-number field.
                    employee.employee_no = None
                # 人员类型（后勤/车间）：优先按工号匹配，回退按 OA 人员ID
                matched_type = (
                    work_number_types.get(_normalize_work_number(employee.employee_no))
                    or person_type_map.get(row.person_id)
                )
                if matched_type:
                    employee.employee_type = matched_type
                if row.email:
                    employee.email = row.email
                employee.updated_at = now
                source_employee_ids.add(employee.id)
                by_oa_person_id[row.person_id] = employee

            db.session.flush()
            for row in source_rows:
                employee = by_oa_person_id[row.person_id]
                manager = by_oa_person_id.get(row.manager_id)
                employee.manager_id = manager.id if manager and manager.id != employee.id else None
                ensure_employee_portal_account(employee, reset_password=False)

            # 部门主管（OA 矩阵表 Matrixtable_4）：封存部门不写入，找不到员工则告警跳过。
            for external_department_id, manager_person_id in (department_managers or {}).items():
                department = departments.get(external_department_id)
                if department is None:
                    continue
                if _clean(department.status) == 'inactive':
                    continue
                manager_employee = by_oa_person_id.get(manager_person_id)
                if manager_employee is None:
                    logger.warning(
                        'oa_department_manager_unmatched',
                        extra={'departmentExternalId': external_department_id},
                    )
                    continue
                department.manager_id = manager_employee.id

            # 清理 OA 已不存在的同步部门（封存/删除），仅限无员工、无子部门者
            active_department_ids = {department.id for department in departments.values()}
            active_department_ids.update(node.id for node in subcompany_nodes.values())
            _cleanup_stale_departments(tenant, active_department_ids)

            archived_count = 0
            for employee in Employee.query.filter_by(tenant_id=tenant.id).all():
                if employee.id in source_employee_ids:
                    continue
                if employee.status != 'inactive':
                    archived_count += 1
                employee.status = 'inactive'
                employee.source_synced_at = now
                account = EmployeePortalAccount.query.filter_by(employee_id=employee.id).first()
                if account:
                    account.status = 'inactive'

            run.status = 'success'
            run.source_count = len(source_rows)
            run.created_count = created_count
            run.updated_count = updated_count
            run.archived_count = archived_count
            run.unchanged_count = 0
            run.completed_at = now
            db.session.commit()
            return OaSyncResult(
                run_id=run.id,
                source_count=len(source_rows),
                created_count=created_count,
                updated_count=updated_count,
                archived_count=archived_count,
                unchanged_count=0,
            )
        except Exception as exc:
            db.session.rollback()
            failed_run = db.session.get(OaEmployeeSyncRun, run.id)
            if failed_run:
                failed_run.status = 'failed'
                failed_run.error_code = type(exc).__name__[:80]
                failed_run.error_message = str(exc)[:500]
                failed_run.completed_at = datetime.utcnow()
                db.session.commit()
            raise


def latest_sync_run(tenant_slug: str = OA_TENANT_SLUG) -> OaEmployeeSyncRun | None:
    tenant = _get_sync_tenant(tenant_slug)
    return OaEmployeeSyncRun.query.filter_by(tenant_id=tenant.id).order_by(
        OaEmployeeSyncRun.started_at.desc()
    ).first()


def run_scheduled_sync() -> OaSyncResult | None:
    if os.getenv('OA_EMPLOYEE_SYNC_ENABLED', '0') != '1':
        return None
    latest = latest_sync_run()
    if latest and latest.status == 'success' and latest.completed_at:
        if datetime.utcnow() - latest.completed_at < timedelta(hours=20):
            return None
    try:
        return sync_oa_employees(trigger='scheduled')
    except OaSyncBusy:
        return None
    except Exception:
        logger.exception('oa_employee_scheduled_sync_failed')
        return None


def _daily_loop(app) -> None:
    global _running
    while _running:
        with app.app_context():
            run_scheduled_sync()
        for _ in range(24):
            if not _running:
                break
            threading.Event().wait(3600)


def start_oa_employee_scheduler(app) -> None:
    global _running
    if _running or os.getenv('OA_EMPLOYEE_SYNC_ENABLED', '0') != '1':
        return
    _running = True
    thread = threading.Thread(
        target=_daily_loop,
        args=(app,),
        daemon=True,
        name='oa-employee-sync',
    )
    thread.start()
    logger.info('oa_employee_scheduler_started')


def stop_oa_employee_scheduler() -> None:
    global _running
    _running = False


OA_AUTHORITATIVE_API_FIELDS = frozenset({
    'employeeNo', 'name', 'phone', 'gender', 'status', 'departmentId',
    'positionId', 'jobTitle', 'managerId', 'hireDate', 'email',
})


def oa_managed_field_conflicts(employee: Employee, payload: dict) -> list[str]:
    if employee.source_system != OA_SOURCE_SYSTEM:
        return []
    return sorted(OA_AUTHORITATIVE_API_FIELDS & set(payload))
