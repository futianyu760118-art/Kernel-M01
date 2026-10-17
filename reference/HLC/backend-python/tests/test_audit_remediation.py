from __future__ import annotations

import io
import uuid
from datetime import datetime

from flask import g
from flask_jwt_extended import create_access_token
from sqlalchemy.exc import IntegrityError

from models import (
    Department,
    Employee,
    EmployeePortalAccount,
    Feedback,
    Interview,
    Job,
    Notification,
    OperationLog,
    PeopleAnalyticsSnapshot,
    Resume,
    Role,
    SalaryProfile,
    TenantSetting,
    TenantModule,
    User,
    db,
)
from routes.hrm import create_approval
from services.tenancy import tenant_claims_for
from services.training_notify import notify_employee
from tests.test_saas_tenancy import _client, saas_app


def _employee_client(app, tenant_id: str, suffix: str, *, status: str = 'active'):
    user_id = f'audit-employee-{suffix}'
    with app.app_context():
        tenant = db.session.get(__import__('models').Tenant, tenant_id)
        user = User(
            id=user_id,
            tenant_id=tenant_id,
            email=f'{suffix}@audit.example.test',
            password_hash='not-used',
            name=f'Audit {suffix}',
            role='employee',
            status=status,
        )
        db.session.add(user)
        db.session.commit()
        token = create_access_token(
            identity=user_id,
            additional_claims={
                'role': 'employee',
                'account_type': 'hrm',
                **tenant_claims_for(tenant),
            },
        )
    return user_id, _client(app, token)


def _enable_module(app, tenant_id: str, module_key: str):
    with app.app_context():
        row = TenantModule.query.filter_by(tenant_id=tenant_id, module_key=module_key).first()
        if row:
            row.enabled = True
        else:
            db.session.add(TenantModule(
                id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                module_key=module_key,
                enabled=True,
                limits_json={},
            ))
        db.session.commit()


def test_employee_cannot_read_tenant_smart_compensation(saas_app):
    app, tokens = saas_app
    user_id, employee = _employee_client(app, tokens['alpha_id'], 'salary-reader')
    _enable_module(app, tokens['alpha_id'], 'extension.intelligence.smart_compensation')

    with app.app_context():
        worker = Employee(
            id='audit-salary-worker',
            tenant_id=tokens['alpha_id'],
            employee_no='AUD-SAL-1',
            name='Salary Target',
            status='active',
        )
        db.session.add(worker)
        db.session.add(SalaryProfile(
            id='audit-salary-profile',
            tenant_id=tokens['alpha_id'],
            employee_id=worker.id,
            base_salary=20000,
            allowance=3000,
            currency='CNY',
            status='active',
        ))
        db.session.commit()

    response = employee.get('/api/extensions/intelligence/smart_compensation/summary')

    assert response.status_code == 403
    assert 'Salary Target' not in response.get_data(as_text=True)
    assert user_id not in response.get_data(as_text=True)


def test_feedback_owner_boundary_blocks_same_tenant_idor(saas_app):
    app, tokens = saas_app
    owner_id, _owner = _employee_client(app, tokens['alpha_id'], 'feedback-owner')
    _attacker_id, attacker = _employee_client(app, tokens['alpha_id'], 'feedback-attacker')

    with app.app_context():
        db.session.add(Feedback(
            id='audit-private-feedback',
            tenant_id=tokens['alpha_id'],
            user_id=owner_id,
            title='Private feedback',
            content='Owner-only details',
            status='pending',
        ))
        db.session.commit()

    assert attacker.get('/api/hrm/feedbacks/audit-private-feedback').status_code in {403, 404}
    assert attacker.put(
        '/api/hrm/feedbacks/audit-private-feedback',
        json={'title': 'tampered'},
    ).status_code in {403, 404}
    assert attacker.delete('/api/hrm/feedbacks/audit-private-feedback').status_code in {403, 404}


def test_employee_cannot_mutate_headcount_or_manage_continuous_feedback(saas_app):
    app, tokens = saas_app
    _user_id, employee = _employee_client(app, tokens['alpha_id'], 'extension-writer')
    _enable_module(app, tokens['alpha_id'], 'extension.foundation.headcount')
    _enable_module(app, tokens['alpha_id'], 'extension.foundation.continuous_feedback')

    with app.app_context():
        department = Department(
            id='audit-headcount-dept',
            tenant_id=tokens['alpha_id'],
            name='Audit Department',
            code='AUD-HC',
            status='active',
        )
        worker = Employee(
            id='audit-feedback-worker',
            tenant_id=tokens['alpha_id'],
            employee_no='AUD-FB-1',
            name='Feedback Target',
            department_id=department.id,
            status='active',
        )
        db.session.add_all([department, worker])
        db.session.commit()

    headcount = employee.put(
        '/api/extensions/foundation/headcount/departments/audit-headcount-dept',
        json={'plan': 99},
    )
    feedback = employee.post(
        '/api/extensions/foundation/continuous_feedback/entries',
        json={'employeeId': 'audit-feedback-worker', 'content': 'unauthorized'},
    )

    assert headcount.status_code == 403
    assert feedback.status_code == 403


def test_employee_cannot_upload_interview_recording(saas_app):
    app, tokens = saas_app
    _user_id, employee = _employee_client(app, tokens['alpha_id'], 'interview-uploader')

    with app.app_context():
        job = Job(id='audit-interview-job', tenant_id=tokens['alpha_id'], title='Audit Job', status='open')
        resume = Resume(
            id='audit-interview-resume',
            tenant_id=tokens['alpha_id'],
            candidate_name='Candidate',
            file_url='/uploads/audit-resume.pdf',
        )
        db.session.add_all([job, resume])
        db.session.flush()
        db.session.add(Interview(
            id='audit-interview',
            tenant_id=tokens['alpha_id'],
            job_id=job.id,
            resume_id=resume.id,
            scheduled_at=datetime.utcnow(),
            status='scheduled',
        ))
        db.session.commit()

    response = employee.post(
        '/api/interviews/audit-interview/recording',
        data={'file': (io.BytesIO(b'audio'), 'audit.mp3')},
        content_type='multipart/form-data',
    )

    assert response.status_code == 403


def test_disabled_account_old_token_is_rejected(saas_app):
    app, tokens = saas_app
    _user_id, disabled = _employee_client(app, tokens['alpha_id'], 'disabled', status='disabled')

    response = disabled.get('/api/extensions/catalog')

    assert response.status_code in {401, 403}


def test_new_tenant_scoped_rows_receive_request_tenant_before_flush(saas_app):
    app, tokens = saas_app
    _user_id, employee = _employee_client(app, tokens['alpha_id'], 'tenant-assignment')

    with app.test_request_context(
        '/api/audit',
        headers={'Authorization': employee.environ_base['HTTP_AUTHORIZATION']},
    ):
        from app import _register_tenant_context
        from services.tenancy import set_request_tenant_context

        del _register_tenant_context
        set_request_tenant_context()
        assert g.tenant_id == tokens['alpha_id']
        approval = create_approval('audit', 'audit-business', 'Audit approval')
        db.session.flush()
        assert approval.tenant_id == tokens['alpha_id']
        db.session.rollback()


def test_settings_are_isolated_between_tenants(saas_app):
    app, tokens = saas_app
    alpha = _client(app, tokens['alpha'])
    beta = _client(app, tokens['beta'])

    saved = alpha.post('/api/settings', json={'companyName': 'Alpha Private Name'})
    assert saved.status_code == 200

    beta_settings = beta.get('/api/settings')
    assert beta_settings.status_code == 200
    assert beta_settings.get_json().get('companyName') != 'Alpha Private Name'


def test_settings_can_remove_an_unset_value_with_null(saas_app):
    app, tokens = saas_app
    alpha = _client(app, tokens['alpha'])

    saved = alpha.post('/api/settings', json={'companyName': 'Temporary Company'})
    assert saved.status_code == 200
    assert saved.get_json()['companyName'] == 'Temporary Company'

    removed = alpha.post('/api/settings', json={'companyName': None})
    assert removed.status_code == 200
    assert 'companyName' not in removed.get_json()

    with app.app_context():
        assert TenantSetting.query.filter_by(
            tenant_id=tokens['alpha_id'],
            key='companyName',
        ).first() is None


def test_role_codes_can_repeat_in_different_tenants(saas_app):
    app, tokens = saas_app
    with app.app_context():
        db.session.add_all([
            Role(
                id='audit-alpha-role',
                tenant_id=tokens['alpha_id'],
                code='audit_specialist',
                name='Alpha Audit Specialist',
                status='active',
            ),
            Role(
                id='audit-beta-role',
                tenant_id=tokens['beta_id'],
                code='audit_specialist',
                name='Beta Audit Specialist',
                status='active',
            ),
        ])
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            raise AssertionError('role code is still globally unique')


def test_management_notifications_exclude_employee_portal_rows(saas_app):
    app, tokens = saas_app
    alpha = _client(app, tokens['alpha'])

    with app.app_context():
        employee = Employee(
            id='audit-notification-employee',
            tenant_id=tokens['alpha_id'],
            employee_no='AUD-N-1',
            name='Portal Recipient',
            status='active',
        )
        db.session.add(employee)
        db.session.flush()
        db.session.add_all([
            Notification(
                id='audit-portal-notification',
                tenant_id=tokens['alpha_id'],
                employee_id=employee.id,
                user_id=None,
                title='Portal only',
                status='unread',
            ),
            Notification(
                id='audit-broadcast-notification',
                tenant_id=tokens['alpha_id'],
                employee_id=None,
                user_id=None,
                title='True broadcast',
                status='unread',
            ),
        ])
        db.session.commit()

    payload = alpha.get('/api/hrm/notifications').get_json()
    ids = {row['id'] for row in payload}

    assert 'audit-broadcast-notification' in ids
    assert 'audit-portal-notification' not in ids


def test_training_notifications_are_idempotent(saas_app):
    app, tokens = saas_app
    with app.app_context():
        employee = Employee(
            id='audit-training-employee',
            tenant_id=tokens['alpha_id'],
            employee_no='AUD-T-1',
            name='Training Recipient',
            status='active',
        )
        db.session.add(employee)
        db.session.flush()
        db.session.add(EmployeePortalAccount(
            id='audit-training-account',
            tenant_id=tokens['alpha_id'],
            employee_id=employee.id,
            password_hash='not-used',
            status='active',
            must_change_password=False,
        ))
        db.session.commit()

        assert notify_employee(employee.id, 'Same course', 'Same content', 'course', '/portal/my-courses') is True
        assert notify_employee(employee.id, 'Same course', 'Same content', 'course', '/portal/my-courses') is True
        db.session.commit()

        assert Notification.query.filter_by(
            employee_id=employee.id,
            title='Same course',
            notice_type='course',
            link_url='/portal/my-courses',
        ).count() == 1


def test_extension_summary_get_is_read_only_and_reports_honest_maturity(saas_app):
    app, tokens = saas_app
    alpha = _client(app, tokens['alpha'])
    _enable_module(app, tokens['alpha_id'], 'extension.analytics.people')

    first = alpha.get('/api/extensions/analytics/people/summary')
    second = alpha.get('/api/extensions/analytics/people/summary')

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.get_json()['status'] == 'insight'
    assert first.get_json()['snapshot']['persisted'] is False
    assert first.get_json()['snapshot']['id'] is None
    with app.app_context():
        assert PeopleAnalyticsSnapshot.query.filter_by(tenant_id=tokens['alpha_id']).count() == 0
        assert OperationLog.query.filter_by(
            tenant_id=tokens['alpha_id'],
            action='snapshot_generated',
        ).count() == 0


def test_extension_registry_reports_all_69_keys_with_truthful_status(saas_app):
    app, tokens = saas_app
    platform = _client(app, tokens['platform'])
    alpha = _client(app, tokens['alpha'])

    catalog = platform.get('/api/platform/extensions/catalog').get_json()
    assert catalog['featureCount'] == 69
    assert len(catalog['moduleKeys']) == 69
    assert 'extension.foundation.todo_notification' in catalog['moduleKeys']
    assert 'extension.foundation.people_analytics_recovery' in catalog['moduleKeys']

    _enable_module(app, tokens['alpha_id'], 'extension.foundation.headcount')
    _enable_module(app, tokens['alpha_id'], 'extension.foundation.people_analytics_recovery')
    headcount = alpha.get('/api/extensions/foundation/headcount/status').get_json()
    recovery = alpha.get('/api/extensions/foundation/people_analytics_recovery/status').get_json()
    assert headcount['status'] == 'workflow'
    assert headcount['acceptance']['browserVerification'] == 'verified'
    assert recovery['status'] == 'planned'
    assert recovery['acceptance']['ui'] == 'hidden'


def test_tenant_provider_status_redacts_secret_like_config_keys(saas_app):
    app, tokens = saas_app
    alpha = _client(app, tokens['alpha'])
    platform = _client(app, tokens['platform'])
    _enable_module(app, tokens['alpha_id'], 'extension.integration.prebuilt_integrations')

    saved = platform.put('/api/platform/extensions/providers/video_meeting', json={
        'providerType': 'video_meeting',
        'providerCode': 'video_meeting',
        'status': 'mock',
        'configJson': {
            'mode': 'mock',
            'endpoint': 'https://provider.example.test',
            'apiKey': 'must-not-leak',
            'clientSecret': 'must-not-leak-either',
        },
    })
    assert saved.status_code == 200

    payload = alpha.get('/api/extensions/integration/provider-status/video_meeting').get_json()
    serialized = str(payload).lower()
    assert 'must-not-leak' not in serialized
    assert 'apikey' not in serialized
    assert 'clientsecret' not in serialized
    assert payload['configJson']['endpoint'] == 'https://provider.example.test'
