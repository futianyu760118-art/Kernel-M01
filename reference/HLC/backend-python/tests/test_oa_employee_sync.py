from __future__ import annotations

from datetime import date, datetime

import pytest
import bcrypt
from flask import Flask
from flask_jwt_extended import JWTManager, create_access_token

from app import _register_request_lifecycle, _register_tenant_context
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
from services.oa_employee_sync import (
    OA_TENANT_SLUG,
    OaDepartmentRow,
    OaEmployeeRow,
    OaEmployeeTypeRow,
    OaReadOnlyClient,
    OaSubCompanyRow,
    OaSyncDisabled,
    preview_oa_employee_sync,
    sync_oa_employees,
)
from services.tenancy import register_tenant_orm_filters, tenant_claims_for
from routes import hrm_bp, portal_bp
from models import User


@pytest.fixture()
def sync_app():
    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    db.init_app(app)
    with app.app_context():
        db.create_all()
        target = Tenant(id='tenant-00001', slug=OA_TENANT_SLUG, name='00001', status='active')
        other = Tenant(id='tenant-other', slug='HPT-00000002', name='Other', status='active')
        db.session.add_all([target, other])
        db.session.add_all([
            Department(id='dept-old', tenant_id=target.id, code='OLD', name='旧部门'),
            Employee(
                id='employee-existing', tenant_id=target.id, employee_no='HJ-0001',
                name='张三', status='active', department_id='dept-old',
            ),
            Employee(
                id='employee-extra', tenant_id=target.id, employee_no='HJ-9999',
                name='OA外人员', status='active',
            ),
            Employee(
                id='employee-other', tenant_id=other.id, employee_no='B001',
                name='其他租户员工', status='active',
            ),
        ])
        db.session.commit()
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def route_app():
    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        SECRET_KEY='test-secret',
        JWT_SECRET_KEY='test-secret',
        SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    db.init_app(app)
    register_tenant_orm_filters()
    JWTManager(app)
    app.register_blueprint(hrm_bp, url_prefix='/api/hrm')
    app.register_blueprint(portal_bp, url_prefix='/api/portal')
    _register_request_lifecycle(app)
    _register_tenant_context(app)
    with app.app_context():
        db.create_all()
        tenant = Tenant(id='tenant-00001', slug=OA_TENANT_SLUG, name='00001', status='active')
        other = Tenant(id='tenant-other', slug='HPT-00000002', name='Other', status='active')
        admin = User(
            id='admin-00001', tenant_id=tenant.id, email='admin@example.com',
            password_hash='unused', name='Admin', role='admin', status='active',
        )
        other_admin = User(
            id='admin-other', tenant_id=other.id, email='other@example.com',
            password_hash='unused', name='Other', role='admin', status='active',
        )
        employee = Employee(
            id='employee-existing', tenant_id=tenant.id, employee_no='OA001',
            oa_person_id='101', oa_login_id='zhangsan', source_system='oa_ecology',
            name='张三', status='active', address='本地地址', remark='本地备注',
        )
        account = EmployeePortalAccount(
            id='portal-existing', tenant_id=tenant.id, employee_id=employee.id,
            password_hash=bcrypt.hashpw(b'ChangedPass123!', bcrypt.gensalt()).decode('utf-8'),
            must_change_password=False, status='active',
        )
        db.session.add_all([tenant, other, admin, other_admin, employee, account])
        db.session.commit()
        tokens = {
            'target': create_access_token(
                identity=admin.id,
                additional_claims={'role': 'admin', 'account_type': 'hrm', **tenant_claims_for(tenant)},
            ),
            'other': create_access_token(
                identity=other_admin.id,
                additional_claims={'role': 'admin', 'account_type': 'hrm', **tenant_claims_for(other)},
            ),
        }
    yield app, tokens
    with app.app_context():
        db.session.remove()
        db.drop_all()


def _rows():
    return [
        OaEmployeeRow(
            person_id=101,
            name='张三',
            mobile='13800000001',
            login_id='zhangsan',
            sex='0',
            email='',
            department_id=9,
            department_name='人事行政部',
            department_parent_id=0,
            department_code='HR',
            workcode='OA001',
            manager_id=None,
            status=1,
            jobtitle_id=65,
            jobtitle_name='人事行政主管',
            erp_user_id=501,
            start_date=date(2023, 1, 2),
            modified_at=datetime(2026, 8, 12, 9, 0, 0),
        ),
        OaEmployeeRow(
            person_id=102,
            name='李四',
            mobile='13800000002',
            login_id='lisi',
            sex='1',
            email='lisi@example.com',
            department_id=9,
            department_name='人事行政部',
            department_parent_id=0,
            department_code='HR',
            workcode='',
            manager_id=101,
            status=3,
            jobtitle_id=43,
            jobtitle_name='专员',
            erp_user_id=None,
            start_date=date(2025, 2, 3),
            modified_at=datetime(2026, 8, 12, 9, 1, 0),
        ),
    ]


def test_sync_uses_oa_as_exact_current_roster_and_preserves_other_tenants(sync_app):
    with sync_app.app_context():
        result = sync_oa_employees(rows=_rows(), trigger='test')

        current = Employee.query.filter_by(tenant_id='tenant-00001').filter(
            Employee.status.in_(('active', 'probation'))
        ).order_by(Employee.name).all()
        assert [employee.name for employee in current] == ['张三', '李四']
        assert result.source_count == 2
        assert result.created_count == 1
        assert result.updated_count == 1
        assert result.archived_count == 1

        zhang = Employee.query.filter_by(tenant_id='tenant-00001', oa_person_id='101').one()
        assert zhang.employee_no == 'HJ-0001'
        assert zhang.oa_workcode == 'OA001'
        assert zhang.phone == '13800000001'
        assert zhang.gender == '男'
        assert zhang.department.name == '人事行政部'
        assert zhang.position.title == '人事行政主管'
        assert zhang.hire_date == date(2023, 1, 2)
        assert zhang.erp_user_id == '501'

        li = Employee.query.filter_by(tenant_id='tenant-00001', oa_person_id='102').one()
        assert li.employee_no is None
        assert li.oa_login_id == 'lisi'
        assert li.oa_workcode is None
        assert li.gender == '女'
        assert li.status == 'probation'
        assert li.manager_id == zhang.id

        archived = db.session.get(Employee, 'employee-extra')
        assert archived.status == 'inactive'
        assert db.session.get(Employee, 'employee-other').status == 'active'


def test_sync_clears_legacy_oa_workcode_from_employee_number(sync_app):
    with sync_app.app_context():
        employee = db.session.get(Employee, 'employee-existing')
        employee.employee_no = 'OA001'
        employee.source_system = 'oa_ecology'
        db.session.commit()

        sync_oa_employees(rows=_rows(), trigger='test')

        employee = db.session.get(Employee, 'employee-existing')
        assert employee.employee_no is None
        assert employee.oa_workcode == 'OA001'


def test_sync_uses_confirmed_attendance_badge_as_employee_number(sync_app):
    with sync_app.app_context():
        db.session.add(AttendanceExternalPerson(
            id='attendance-person-101',
            tenant_id='tenant-00001',
            external_user_id='101',
            badge_number='17',
            name='张三',
            employee_id='employee-existing',
            suggested_employee_id='employee-existing',
            mapping_status='confirmed',
        ))
        db.session.commit()

        sync_oa_employees(rows=_rows(), trigger='test')

        employee = db.session.get(Employee, 'employee-existing')
        assert employee.employee_no == '17'
        assert employee.oa_workcode == 'OA001'


def test_sync_reuses_stable_oa_person_id_after_name_changes(sync_app):
    with sync_app.app_context():
        sync_oa_employees(rows=_rows(), trigger='test')
        changed = _rows()
        changed[0] = OaEmployeeRow(**{**changed[0].__dict__, 'name': '张三新名'})
        result = sync_oa_employees(rows=changed, trigger='test')

        assert result.created_count == 0
        assert Employee.query.filter_by(tenant_id='tenant-00001', oa_person_id='101').one().name == '张三新名'
        assert Employee.query.filter_by(tenant_id='tenant-00001').count() == 3


def test_sync_preserves_employee_portal_and_hrm_local_state(sync_app):
    with sync_app.app_context():
        employee = db.session.get(Employee, 'employee-existing')
        employee.address = '本地地址'
        employee.emergency_contact = '家属 13800009999'
        employee.remark = '本地人事备注'
        account = EmployeePortalAccount(
            id='portal-existing',
            tenant_id='tenant-00001',
            employee_id=employee.id,
            password_hash=bcrypt.hashpw(b'ChangedPass123!', bcrypt.gensalt()).decode('utf-8'),
            must_change_password=False,
            status='active',
        )
        db.session.add(account)
        db.session.commit()
        original_hash = account.password_hash

        sync_oa_employees(rows=_rows(), trigger='test')

        employee = db.session.get(Employee, 'employee-existing')
        account = db.session.get(EmployeePortalAccount, 'portal-existing')
        assert employee.address == '本地地址'
        assert employee.emergency_contact == '家属 13800009999'
        assert employee.remark == '本地人事备注'
        assert account.password_hash == original_hash
        assert account.must_change_password is False
        assert account.status == 'active'
        assert account.to_dict()['loginIdentifier'] == 'HJ-0001'


def test_archived_employee_portal_account_reactivates_without_password_reset(sync_app):
    with sync_app.app_context():
        employee = db.session.get(Employee, 'employee-existing')
        account = EmployeePortalAccount(
            id='portal-existing',
            tenant_id='tenant-00001',
            employee_id=employee.id,
            password_hash=bcrypt.hashpw(b'ChangedPass123!', bcrypt.gensalt()).decode('utf-8'),
            must_change_password=False,
            status='active',
        )
        db.session.add(account)
        db.session.commit()
        original_hash = account.password_hash

        sync_oa_employees(rows=[_rows()[1]], trigger='test')
        assert db.session.get(EmployeePortalAccount, 'portal-existing').status == 'inactive'

        sync_oa_employees(rows=_rows(), trigger='test')
        account = db.session.get(EmployeePortalAccount, 'portal-existing')
        assert account.status == 'active'
        assert account.password_hash == original_hash
        assert account.must_change_password is False


def test_sync_refuses_non_00001_tenant(sync_app):
    with sync_app.app_context():
        with pytest.raises(OaSyncDisabled):
            sync_oa_employees(rows=_rows(), tenant_slug='HPT-00000002', trigger='test')


def test_preview_reports_diff_without_changing_hrm(sync_app):
    with sync_app.app_context():
        preview = preview_oa_employee_sync(rows=_rows())
        assert preview.source_count == 2
        assert preview.create_count == 1
        assert preview.update_count == 1
        assert preview.archive_count == 1
        assert preview.create_names == ('李四',)
        assert preview.archive_names == ('OA外人员',)
        assert Employee.query.filter_by(tenant_id='tenant-00001').count() == 2
        assert Employee.query.filter_by(tenant_id='tenant-00001', oa_person_id='101').count() == 0
        assert OaEmployeeSyncRun.query.count() == 0


def test_failed_sync_rolls_back_hrm_changes_and_records_failure(sync_app):
    rows = _rows()
    rows.append(OaEmployeeRow(**{**rows[1].__dict__, 'person_id': 103, 'name': '李四'}))
    with sync_app.app_context():
        with pytest.raises(ValueError, match='duplicate OA employee name'):
            sync_oa_employees(rows=rows, trigger='test')
        assert Employee.query.filter_by(tenant_id='tenant-00001', oa_person_id='101').count() == 0
        run = OaEmployeeSyncRun.query.order_by(OaEmployeeSyncRun.started_at.desc()).first()
        assert run.status == 'failed'


def test_external_client_only_executes_fixed_select_and_rolls_back(monkeypatch):
    events = []

    class Cursor:
        def execute(self, sql):
            events.append(('execute', sql))

        def fetchall(self):
            return []

    class Connection:
        def cursor(self, as_dict=False):
            assert as_dict is True
            return Cursor()

        def rollback(self):
            events.append(('rollback', None))

        def close(self):
            events.append(('close', None))

    connect_kwargs = {}

    def fake_connect(**kwargs):
        connect_kwargs.update(kwargs)
        return Connection()

    monkeypatch.setattr('services.oa_employee_sync.pymssql.connect', fake_connect)
    client = OaReadOnlyClient(host='oa.example', port=2807, user='reader', password='secret')
    assert client.fetch_employees() == []
    assert connect_kwargs['tds_version'] == '7.0'
    statements = [sql for action, sql in events if action == 'execute']
    assert statements
    assert all(sql.lstrip().upper().startswith(('SET TRANSACTION', 'SELECT')) for sql in statements)
    assert ('rollback', None) in events
    assert ('close', None) in events


def test_sync_excludes_canceled_departments(sync_app):
    with sync_app.app_context():
        rows = _rows()
        rows[0] = OaEmployeeRow(**{**rows[0].__dict__, 'department_canceled': '1'})
        rows[1] = OaEmployeeRow(**{**rows[1].__dict__, 'department_canceled': '1'})

        sync_oa_employees(rows=rows, trigger='test')

        # 封存部门不落库（与参考实现一致）
        assert Department.query.filter_by(tenant_id='tenant-00001', code='HR').first() is None


def test_sync_nests_departments_under_subcompany(sync_app):
    with sync_app.app_context():
        subcompanies = [
            OaSubCompanyRow(subcompany_id=2, name='恒剑光电', parent_id=None, code='', canceled=''),
        ]
        rows = _rows()
        rows[0] = OaEmployeeRow(**{**rows[0].__dict__, 'subcompany_id': 2})
        rows[1] = OaEmployeeRow(**{**rows[1].__dict__, 'subcompany_id': 2})

        sync_oa_employees(rows=rows, subcompanies=subcompanies, trigger='test')

        subcompany = Department.query.filter_by(tenant_id='tenant-00001', code='OA-SUB-2').one()
        assert subcompany.name == '恒剑光电'
        assert subcompany.parent_id is None
        # 顶级部门（supdepid=0）挂到分部下面，形成一级/二级层级
        department = Department.query.filter_by(tenant_id='tenant-00001', code='HR').one()
        assert department.parent_id == subcompany.id


def test_sync_excludes_canceled_subcompany(sync_app):
    with sync_app.app_context():
        subcompanies = [
            OaSubCompanyRow(subcompany_id=1, name='Default', parent_id=None, code='', canceled='1'),
        ]
        sync_oa_employees(rows=_rows(), subcompanies=subcompanies, trigger='test')

        assert Department.query.filter_by(tenant_id='tenant-00001', code='OA-SUB-1').first() is None


def test_sync_creates_intermediate_departments_without_employees(sync_app):
    """没有直属员工的中间层（如供应链中心）也要建出来，子部门才挂得对。"""
    with sync_app.app_context():
        subcompanies = [
            OaSubCompanyRow(subcompany_id=2, name='恒剑光电', parent_id=None, code='', canceled=''),
        ]
        departments = [
            OaDepartmentRow(department_id=7, name='供应链中心', parent_id=None, subcompany_id=2, code='', canceled=''),
            OaDepartmentRow(department_id=38, name='采购', parent_id=7, subcompany_id=2, code='', canceled=''),
        ]
        rows = _rows()
        rows[0] = OaEmployeeRow(**{
            **rows[0].__dict__, 'department_id': 38, 'department_name': '采购',
            'department_parent_id': 7, 'subcompany_id': 2,
        })
        rows[1] = OaEmployeeRow(**{
            **rows[1].__dict__, 'department_id': 38, 'department_name': '采购',
            'department_parent_id': 7, 'subcompany_id': 2,
        })

        sync_oa_employees(
            rows=rows, departments=departments, subcompanies=subcompanies, trigger='test'
        )

        subcompany = Department.query.filter_by(tenant_id='tenant-00001', code='OA-SUB-2').one()
        supply = Department.query.filter_by(tenant_id='tenant-00001', code='OA-7').one()
        purchase = Department.query.filter_by(tenant_id='tenant-00001', code='OA-38').one()
        assert supply.name == '供应链中心'
        assert supply.parent_id == subcompany.id
        assert purchase.parent_id == supply.id


def test_sync_stores_department_short_name(sync_app):
    with sync_app.app_context():
        departments = [
            OaDepartmentRow(
                department_id=9, name='组装一线', parent_id=None, subcompany_id=None,
                code='', canceled='', short_name='组装',
            ),
        ]
        rows = _rows()
        rows[0] = OaEmployeeRow(**{**rows[0].__dict__, 'department_id': 9, 'department_name': '组装一线'})
        rows[1] = OaEmployeeRow(**{**rows[1].__dict__, 'department_id': 9, 'department_name': '组装一线'})

        sync_oa_employees(rows=rows, departments=departments, trigger='test')

        department = Department.query.filter_by(tenant_id='tenant-00001', code='OA-9').one()
        assert department.name == '组装一线'
        assert department.short_name == '组装'


def test_sync_applies_employee_type_from_treatment_list(sync_app):
    with sync_app.app_context():
        types = [
            OaEmployeeTypeRow(work_number='4', oa_person_id=101, employee_type='车间'),
        ]
        sync_oa_employees(
            rows=_rows(), work_numbers={101: '4', 102: '5'}, employee_types=types, trigger='test'
        )

        assert db.session.get(Employee, 'employee-existing').employee_type == '车间'
        li = Employee.query.filter_by(tenant_id='tenant-00001', oa_person_id='102').one()
        assert li.employee_type is None


def test_sync_employee_type_falls_back_to_oa_person_id(sync_app):
    with sync_app.app_context():
        types = [
            OaEmployeeTypeRow(work_number='', oa_person_id=101, employee_type='后勤'),
        ]
        sync_oa_employees(rows=_rows(), employee_types=types, trigger='test')

        assert db.session.get(Employee, 'employee-existing').employee_type == '后勤'


def test_fetch_employee_types_keeps_only_valid_records(monkeypatch):
    rows = [
        {'work_number': '4', 'employee_type': 0, 'oa_person_id': 113, 'record_status': '有效', 'record_id': 1},
        {'work_number': '4', 'employee_type': 1, 'oa_person_id': 113, 'record_status': '无效', 'record_id': 2},
        {'work_number': '124', 'employee_type': 1, 'oa_person_id': 29, 'record_status': '有效', 'record_id': 3},
        {'work_number': '004', 'employee_type': 0, 'oa_person_id': 9, 'record_status': '有效', 'record_id': 4},
    ]
    client = _fake_client(monkeypatch, rows)
    result = client.fetch_employee_types()
    assert {(row.work_number, row.employee_type) for row in result} == {
        ('4', '后勤'),
        ('124', '车间'),
    }


def test_sync_applies_department_manager_from_matrix(sync_app):
    with sync_app.app_context():
        sync_oa_employees(rows=_rows(), department_managers={9: 101}, trigger='test')

        department = Department.query.filter_by(tenant_id='tenant-00001', code='HR').one()
        manager = Employee.query.filter_by(tenant_id='tenant-00001', oa_person_id='101').one()
        assert department.manager_id == manager.id


def test_sync_skips_unmatched_department_manager(sync_app):
    with sync_app.app_context():
        # 主管不在 OA 名单中：跳过且不报错
        sync_oa_employees(rows=_rows(), department_managers={9: 9999}, trigger='test')
        department = Department.query.filter_by(tenant_id='tenant-00001', code='HR').one()
        assert department.manager_id is None


def test_sync_applies_oa_work_number_from_custom_field(sync_app):
    with sync_app.app_context():
        sync_oa_employees(rows=_rows(), work_numbers={101: '108', 102: '109'}, trigger='test')

        zhang = Employee.query.filter_by(tenant_id='tenant-00001', oa_person_id='101').one()
        li = Employee.query.filter_by(tenant_id='tenant-00001', oa_person_id='102').one()
        assert zhang.employee_no == '108'
        assert li.employee_no == '109'


def test_confirmed_attendance_badge_wins_over_oa_work_number(sync_app):
    with sync_app.app_context():
        db.session.add(AttendanceExternalPerson(
            id='attendance-person-101',
            tenant_id='tenant-00001',
            external_user_id='101',
            badge_number='17',
            name='张三',
            employee_id='employee-existing',
            suggested_employee_id='employee-existing',
            mapping_status='confirmed',
        ))
        db.session.commit()

        sync_oa_employees(rows=_rows(), work_numbers={101: '108'}, trigger='test')

        employee = db.session.get(Employee, 'employee-existing')
        assert employee.employee_no == '17'


def test_employee_select_excludes_resigned_status():
    compact = OaReadOnlyClient.EMPLOYEE_SELECT.replace(' ', '')
    assert 'IN(0,1,3)' in compact
    assert 'department_canceled' in compact


def _fake_client(monkeypatch, rows):
    class Cursor:
        def execute(self, sql):
            pass

        def fetchall(self):
            return rows

    class Connection:
        def cursor(self, as_dict=False):
            return Cursor()

        def rollback(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr('services.oa_employee_sync.pymssql.connect', lambda **kwargs: Connection())
    return OaReadOnlyClient(host='oa.example', port=2807, user='reader', password='secret')


def test_fetch_department_managers_keeps_smallest_dataorder(monkeypatch):
    rows = [
        {'department_id': 58, 'manager_id': 11, 'dataorder': 5},
        {'department_id': 58, 'manager_id': 99, 'dataorder': 32},
        {'department_id': 9, 'manager_id': 55, 'dataorder': 24},
        {'department_id': None, 'manager_id': 1, 'dataorder': 30},
    ]
    client = _fake_client(monkeypatch, rows)
    assert client.fetch_department_managers() == {58: 11, 9: 55}


def test_fetch_work_numbers_parses_custom_field(monkeypatch):
    rows = [
        {'oa_person_id': 9, 'work_number': '108'},
        {'oa_person_id': 10, 'work_number': None},
        {'oa_person_id': 11, 'work_number': ' 044 '},
    ]
    client = _fake_client(monkeypatch, rows)
    assert client.fetch_work_numbers() == {9: '108', 11: '044'}


def test_fetch_subcompanies_parses_rows(monkeypatch):
    rows = [
        {'subcompany_id': 2, 'subcompanyname': '恒剑光电', 'supsubcomid': 0, 'subcompanycode': '', 'canceled': None},
        {'subcompany_id': 3, 'subcompanyname': '恒有信息', 'supsubcomid': 0, 'subcompanycode': 'HY', 'canceled': '1'},
    ]
    client = _fake_client(monkeypatch, rows)
    result = client.fetch_subcompanies()
    assert [(row.subcompany_id, row.name, row.parent_id, row.code, row.canceled) for row in result] == [
        (2, '恒剑光电', None, '', ''),
        (3, '恒有信息', None, 'HY', '1'),
    ]


def test_oa_managed_api_fields_are_read_only_but_local_fields_remain_editable(route_app):
    app, tokens = route_app
    client = app.test_client()
    headers = {'Authorization': f"Bearer {tokens['target']}"}

    response = client.put(
        '/api/hrm/employees/employee-existing',
        json={'name': 'HRM手改姓名'},
        headers=headers,
    )
    assert response.status_code == 409
    assert response.get_json()['code'] == 'OA_MANAGED_FIELDS_READ_ONLY'

    response = client.put(
        '/api/hrm/employees/employee-existing',
        json={'address': '更新后的本地地址', 'remark': '更新后的本地备注'},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.get_json()['address'] == '更新后的本地地址'
    with app.app_context():
        account = db.session.get(EmployeePortalAccount, 'portal-existing')
        assert account.must_change_password is False


def test_portal_login_accepts_oa_login_id(route_app):
    app, _tokens = route_app
    response = app.test_client().post('/api/portal/auth/login', json={
        'tenantSlug': OA_TENANT_SLUG,
        'employeeNo': 'zhangsan',
        'password': 'ChangedPass123!',
    })
    assert response.status_code == 200
    user = response.get_json()['user']
    assert user['employeeId'] == 'employee-existing'
    assert user['loginIdentifier'] == 'OA001'


def test_sync_status_is_hidden_from_other_tenants(route_app):
    app, tokens = route_app
    response = app.test_client().get(
        '/api/hrm/oa-employee-sync/status',
        headers={'Authorization': f"Bearer {tokens['other']}"},
    )
    assert response.status_code == 404


def test_00001_employee_list_hides_archived_by_default(route_app):
    app, tokens = route_app
    with app.app_context():
        db.session.add(Employee(
            id='employee-archived', tenant_id='tenant-00001', employee_no='OLD001',
            name='历史员工', status='inactive',
        ))
        db.session.commit()
    client = app.test_client()
    headers = {'Authorization': f"Bearer {tokens['target']}"}

    response = client.get('/api/hrm/employees', headers=headers)
    assert response.status_code == 200
    assert [item['name'] for item in response.get_json()] == ['张三']

    response = client.get('/api/hrm/employees?status=inactive', headers=headers)
    assert response.status_code == 200
    assert [item['name'] for item in response.get_json()] == ['历史员工']
