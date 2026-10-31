from __future__ import annotations

import uuid

import bcrypt
import pytest
from flask import Flask
from flask_jwt_extended import JWTManager, create_access_token

from app import _register_tenant_context
from models import Department, Employee, Plan, Tenant, TenantModule, User, db
from routes import org_bp
from services.org_structure import (
    OrgStructureError,
    build_department_tree,
    build_org_chart,
    department_descendant_ids,
    employee_subordinate_ids,
    managed_department_ids_for_user,
    resolve_department_manager,
    set_department_manager,
    validate_and_set_manager,
)
from services.tenancy import SAAS_MODULE_KEYS, register_tenant_orm_filters, tenant_claims_for


def _hash(password: str) -> str:
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')


def _tenant(slug: str, name: str) -> Tenant:
    return Tenant(
        id=str(uuid.uuid4()),
        slug=slug,
        name=name,
        status='active',
        plan_code='enterprise',
        max_users=20,
        max_employees=200,
        max_storage_mb=1024,
        ai_monthly_quota=100,
    )


def _seed_modules(tenant_id: str) -> None:
    for module_key in SAAS_MODULE_KEYS:
        db.session.add(TenantModule(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            module_key=module_key,
            enabled=True,
            limits_json={},
        ))


@pytest.fixture()
def org_app(tmp_path):
    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        SECRET_KEY='test-secret',
        JWT_SECRET_KEY='test-secret',
        SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        UPLOAD_FOLDER=str(tmp_path / 'uploads'),
    )
    db.init_app(app)
    register_tenant_orm_filters()
    JWTManager(app)
    app.register_blueprint(org_bp, url_prefix='/api/org')
    _register_tenant_context(app)

    with app.app_context():
        db.create_all()
        db.session.add(Plan(
            code='enterprise',
            name='Enterprise',
            enabled_modules_json=list(SAAS_MODULE_KEYS),
            limits_json={},
            is_active=True,
        ))
        alpha = _tenant('alpha', 'Alpha')
        beta = _tenant('beta', 'Beta')
        db.session.add_all([alpha, beta])
        db.session.flush()
        _seed_modules(alpha.id)
        _seed_modules(beta.id)

        departments = [
            Department(id='alpha-root', tenant_id=alpha.id, name='总部', code='A-ROOT', status='active'),
            Department(id='alpha-child', tenant_id=alpha.id, name='研发部', code='A-RD', parent_id='alpha-root', status='active'),
            Department(id='alpha-grand', tenant_id=alpha.id, name='前端组', code='A-FE', parent_id='alpha-child', status='active'),
            Department(id='alpha-frozen', tenant_id=alpha.id, name='封存部门', code='A-OLD', parent_id='alpha-root', status='inactive'),
            Department(id='beta-root', tenant_id=beta.id, name='Beta总部', code='B-ROOT', status='active'),
        ]
        employees = [
            Employee(id='alpha-mgr', tenant_id=alpha.id, employee_no='A001', name='Alpha经理', status='active', department_id='alpha-child'),
            Employee(id='alpha-emp', tenant_id=alpha.id, employee_no='A002', name='Alpha员工', status='probation', department_id='alpha-child', manager_id='alpha-mgr'),
            Employee(id='alpha-emp2', tenant_id=alpha.id, employee_no='A003', name='Alpha下属', status='active', department_id='alpha-grand', manager_id='alpha-emp'),
            Employee(id='alpha-inactive', tenant_id=alpha.id, employee_no='A004', name='Alpha离职', status='inactive', department_id='alpha-child'),
            Employee(id='beta-emp', tenant_id=beta.id, employee_no='B001', name='Beta员工', status='active', department_id='beta-root'),
        ]
        db.session.add_all(departments + employees)
        db.session.flush()

        users = [
            User(id='alpha-admin', tenant_id=alpha.id, email='admin@alpha.test', password_hash=_hash('Passw0rd!'), name='Alpha Admin', role='admin', status='active'),
            User(id='alpha-manager', tenant_id=alpha.id, email='mgr@alpha.test', password_hash=_hash('Passw0rd!'), name='Alpha Manager', role='manager', status='active', employee_id='alpha-mgr'),
            User(id='alpha-viewer', tenant_id=alpha.id, email='viewer@alpha.test', password_hash=_hash('Passw0rd!'), name='Alpha Viewer', role='viewer', status='active'),
            User(id='beta-admin', tenant_id=beta.id, email='admin@beta.test', password_hash=_hash('Passw0rd!'), name='Beta Admin', role='admin', status='active'),
        ]
        db.session.add_all(users)
        db.session.commit()

        def token_for(user_id: str, tenant: Tenant) -> str:
            user = db.session.get(User, user_id)
            return create_access_token(
                identity=user_id,
                additional_claims={
                    'role': user.role,
                    'account_type': 'hrm',
                    **tenant_claims_for(tenant),
                },
            )

        tokens = {
            'alpha_admin': token_for('alpha-admin', alpha),
            'alpha_manager': token_for('alpha-manager', alpha),
            'alpha_viewer': token_for('alpha-viewer', alpha),
            'beta_admin': token_for('beta-admin', beta),
            'alpha_id': alpha.id,
            'beta_id': beta.id,
        }

    yield app, tokens

    with app.app_context():
        db.session.remove()
        db.drop_all()


def _client(app: Flask, token: str):
    client = app.test_client()
    client.environ_base['HTTP_AUTHORIZATION'] = f'Bearer {token}'
    return client


# ---------------------------------------------------------------------------
# 服务层
# ---------------------------------------------------------------------------

def test_department_descendant_ids_includes_self_and_descendants(org_app):
    app, tokens = org_app
    with app.app_context():
        ids = department_descendant_ids(tokens['alpha_id'], 'alpha-root')
        assert ids == {'alpha-root', 'alpha-child', 'alpha-grand'}

        child_ids = department_descendant_ids(tokens['alpha_id'], 'alpha-child')
        assert child_ids == {'alpha-child', 'alpha-grand'}


def test_department_descendant_ids_excludes_inactive_by_default(org_app):
    app, tokens = org_app
    with app.app_context():
        active_ids = department_descendant_ids(tokens['alpha_id'], 'alpha-root')
        assert 'alpha-frozen' not in active_ids

        all_ids = department_descendant_ids(tokens['alpha_id'], 'alpha-root', include_inactive=True)
        assert 'alpha-frozen' in all_ids


def test_build_department_tree_nests_and_counts_active_employees(org_app):
    app, tokens = org_app
    with app.app_context():
        tree = build_department_tree(tokens['alpha_id'])
        assert len(tree) == 1
        root = tree[0]
        assert root['id'] == 'alpha-root'
        names = {child['id'] for child in root['children']}
        assert names == {'alpha-child'}  # 封存部门默认隐藏

        child = root['children'][0]
        grand = child['children'][0]
        assert grand['id'] == 'alpha-grand'
        # alpha-child 下在职 2 人（离职不计），alpha-grand 下 1 人
        assert child['employeeCount'] == 2
        assert grand['employeeCount'] == 1


def test_build_department_tree_aggregates_total_employee_count(org_app):
    app, tokens = org_app
    with app.app_context():
        tree = build_department_tree(tokens['alpha_id'])
        root = tree[0]
        # 直接人数为 0（总部无直属员工），含下级应为 3（alpha-child 2 + alpha-grand 1）
        assert root['employeeCount'] == 0
        assert root['totalEmployeeCount'] == 3
        child = root['children'][0]
        assert child['totalEmployeeCount'] == 3


def test_build_department_tree_exposes_short_name(org_app):
    app, tokens = org_app
    with app.app_context():
        department = db.session.get(Department, 'alpha-child')
        department.short_name = '研发'
        db.session.commit()

        tree = build_department_tree(tokens['alpha_id'])
        child = tree[0]['children'][0]
        assert child['shortName'] == '研发'
        assert child['name'] == '研发部'


def test_build_department_tree_includes_manager_name(org_app):
    app, tokens = org_app
    with app.app_context():
        department = db.session.get(Department, 'alpha-child')
        department.manager_id = 'alpha-mgr'
        db.session.commit()

        tree = build_department_tree(tokens['alpha_id'])
        child = tree[0]['children'][0]
        assert child['managerId'] == 'alpha-mgr'
        assert child['managerName'] == 'Alpha经理'


def test_build_org_chart_excludes_inactive_employees_and_cross_tenant_lines(org_app):
    app, tokens = org_app
    with app.app_context():
        chart = build_org_chart(tokens['alpha_id'])
        employee_ids = {row['id'] for row in chart['employees']}
        assert 'alpha-inactive' not in employee_ids
        assert 'beta-emp' not in employee_ids

        lines = {(line['employeeId'], line['managerId']) for line in chart['reportingLines']}
        assert ('alpha-emp', 'alpha-mgr') in lines
        assert ('alpha-emp2', 'alpha-emp') in lines


def test_resolve_department_manager_returns_none_when_missing(org_app):
    app, tokens = org_app
    with app.app_context():
        assert resolve_department_manager(tokens['alpha_id'], 'alpha-child') is None

        department = db.session.get(Department, 'alpha-child')
        department.manager_id = 'alpha-mgr'
        db.session.commit()

        manager = resolve_department_manager(tokens['alpha_id'], 'alpha-child')
        assert manager is not None and manager.id == 'alpha-mgr'
        # 跨租户查询应返回 None
        assert resolve_department_manager(tokens['beta_id'], 'alpha-child') is None


def test_managed_department_ids_uses_department_manager(org_app):
    app, tokens = org_app
    with app.app_context():
        department = db.session.get(Department, 'alpha-child')
        department.manager_id = 'alpha-mgr'
        db.session.commit()

        managed = managed_department_ids_for_user(tokens['alpha_id'], 'alpha-mgr')
        assert managed == {'alpha-child', 'alpha-grand'}


def test_managed_department_ids_derives_from_employee_manager(org_app):
    """兼容 OA 同步的上级关系（部门负责人字段为空）。"""
    app, tokens = org_app
    with app.app_context():
        managed = managed_department_ids_for_user(tokens['alpha_id'], 'alpha-mgr')
        # alpha-emp 的直属上级是 alpha-mgr，alpha-emp 在 alpha-child
        assert {'alpha-child', 'alpha-grand'} <= managed


def test_employee_subordinate_ids_is_recursive(org_app):
    app, tokens = org_app
    with app.app_context():
        assert employee_subordinate_ids(tokens['alpha_id'], 'alpha-mgr') == {'alpha-emp', 'alpha-emp2'}
        assert employee_subordinate_ids(tokens['alpha_id'], 'alpha-emp') == {'alpha-emp2'}


def test_validate_and_set_manager_rejects_self_and_cycles(org_app):
    app, tokens = org_app
    with app.app_context():
        employee = db.session.get(Employee, 'alpha-mgr')

        with pytest.raises(OrgStructureError):
            validate_and_set_manager(employee, 'alpha-mgr')

        # alpha-mgr -> alpha-emp -> alpha-emp2，再设 alpha-emp2 为 alpha-mgr 的上级会成环
        with pytest.raises(OrgStructureError):
            validate_and_set_manager(employee, 'alpha-emp2')


def test_validate_and_set_manager_rejects_cross_tenant(org_app):
    app, tokens = org_app
    with app.app_context():
        employee = db.session.get(Employee, 'alpha-mgr')
        with pytest.raises(OrgStructureError):
            validate_and_set_manager(employee, 'beta-emp')


def test_validate_and_set_manager_sets_and_clears(org_app):
    app, tokens = org_app
    with app.app_context():
        employee = db.session.get(Employee, 'alpha-emp2')
        manager = validate_and_set_manager(employee, 'alpha-mgr')
        assert manager.id == 'alpha-mgr'
        assert employee.manager_id == 'alpha-mgr'

        assert validate_and_set_manager(employee, '') is None
        assert employee.manager_id is None


def test_set_department_manager_rejects_cross_tenant(org_app):
    app, tokens = org_app
    with app.app_context():
        department = db.session.get(Department, 'alpha-child')
        with pytest.raises(OrgStructureError):
            set_department_manager(department, 'beta-emp')

        manager = set_department_manager(department, 'alpha-mgr')
        assert manager.id == 'alpha-mgr'
        assert department.manager_id == 'alpha-mgr'


# ---------------------------------------------------------------------------
# 接口层
# ---------------------------------------------------------------------------

def test_org_tree_requires_permission(org_app):
    app, tokens = org_app
    viewer = _client(app, tokens['alpha_viewer'])
    assert viewer.get('/api/org/tree').status_code == 403


def test_org_tree_is_tenant_scoped(org_app):
    app, tokens = org_app
    alpha = _client(app, tokens['alpha_admin'])
    beta = _client(app, tokens['beta_admin'])

    alpha_tree = alpha.get('/api/org/tree').get_json()['tree']
    beta_tree = beta.get('/api/org/tree').get_json()['tree']

    assert {node['id'] for node in alpha_tree} == {'alpha-root'}
    assert {node['id'] for node in beta_tree} == {'beta-root'}


def test_org_tree_include_inactive_flag(org_app):
    app, tokens = org_app
    client = _client(app, tokens['alpha_admin'])

    default_children = {child['id'] for child in client.get('/api/org/tree').get_json()['tree'][0]['children']}
    assert 'alpha-frozen' not in default_children

    all_children = {
        child['id']
        for child in client.get('/api/org/tree?includeInactive=1').get_json()['tree'][0]['children']
    }
    assert 'alpha-frozen' in all_children


def test_org_chart_endpoint_returns_departments_employees_and_lines(org_app):
    app, tokens = org_app
    client = _client(app, tokens['alpha_manager'])
    response = client.get('/api/org/chart')
    assert response.status_code == 200
    payload = response.get_json()
    assert {'departments', 'employees', 'reportingLines'} <= set(payload.keys())
    assert any(line['employeeId'] == 'alpha-emp' for line in payload['reportingLines'])


def test_set_employee_manager_endpoint(org_app):
    app, tokens = org_app
    admin = _client(app, tokens['alpha_admin'])

    response = admin.put('/api/org/employees/alpha-emp2/manager', json={'managerId': 'alpha-mgr'})
    assert response.status_code == 200
    assert response.get_json()['employee']['managerId'] == 'alpha-mgr'

    denied = _client(app, tokens['alpha_manager']).put(
        '/api/org/employees/alpha-emp2/manager', json={'managerId': 'alpha-mgr'}
    )
    assert denied.status_code == 403


def test_set_employee_manager_rejects_cycle_and_unknown(org_app):
    app, tokens = org_app
    admin = _client(app, tokens['alpha_admin'])

    cycle = admin.put('/api/org/employees/alpha-mgr/manager', json={'managerId': 'alpha-emp2'})
    assert cycle.status_code == 400
    assert '环' in cycle.get_json()['error']

    missing = admin.put('/api/org/employees/alpha-mgr/manager', json={'managerId': 'does-not-exist'})
    assert missing.status_code == 400

    not_found = admin.put('/api/org/employees/nope/manager', json={'managerId': 'alpha-mgr'})
    assert not_found.status_code == 404


def test_set_employee_manager_is_tenant_scoped(org_app):
    app, tokens = org_app
    beta = _client(app, tokens['beta_admin'])

    response = beta.put('/api/org/employees/alpha-emp2/manager', json={'managerId': 'beta-emp'})
    assert response.status_code == 404


def test_set_department_manager_endpoint(org_app):
    app, tokens = org_app
    admin = _client(app, tokens['alpha_admin'])

    response = admin.put('/api/org/departments/alpha-child/manager', json={'managerId': 'alpha-mgr'})
    assert response.status_code == 200
    assert response.get_json()['department']['managerId'] == 'alpha-mgr'

    missing = admin.put('/api/org/departments/alpha-child/manager', json={'managerId': 'beta-emp'})
    assert missing.status_code == 400

    not_found = admin.put('/api/org/departments/nope/manager', json={'managerId': 'alpha-mgr'})
    assert not_found.status_code == 404
