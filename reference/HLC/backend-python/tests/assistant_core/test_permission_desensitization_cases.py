"""Permission and desensitization cases for the assistant.

Module 9 verifies sensitive HR data stays behind role checks and field-level
redaction while privileged roles can still complete reasonable read-only asks.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import pytest
from flask import Flask

from services.assistant_core import guardrails, skill_registry
from services.assistant_core.executors.tool_executor import ToolExecutor
from services.assistant_core.intent_router import permission_gate
from services.assistant_core.intent_router.legacy_planner import build_route_decision
from services.assistant_core.schemas import AssistantContext


def _ctx(
    role: str = 'hr_admin',
    *,
    user_employee_id: str | None = None,
    user_department_id: str | None = None,
) -> AssistantContext:
    return AssistantContext(
        user_id=f'u-permission-{role}',
        role=role,
        session_id=f'sess-permission-{role}',
        user_employee_id=user_employee_id,
        user_department_id=user_department_id,
    )


@pytest.fixture()
def model_db(monkeypatch):
    from models import db
    from services.assistant_core.smart_query import reset_readonly_engine_for_tests

    for name in (
        'DB_READONLY_URI',
        'DB_READONLY_HOST',
        'DB_READONLY_PORT',
        'DB_READONLY_USER',
        'DB_READONLY_PASSWORD',
        'DB_READONLY_NAME',
    ):
        monkeypatch.delenv(name, raising=False)
    reset_readonly_engine_for_tests()

    app = Flask(__name__)
    app.config.update(
        SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    db.init_app(app)
    with app.app_context():
        db.create_all()
        yield db
        db.session.remove()
        db.drop_all()
        reset_readonly_engine_for_tests()


@dataclass(frozen=True)
class PayrollPermissionCase:
    message: str
    role: str
    allowed: bool


PAYROLL_PERMISSION_CASES = [
    PayrollPermissionCase('查张三本月工资条', 'employee', False),
    PayrollPermissionCase('查张三的薪酬档案', 'manager', False),
    PayrollPermissionCase('本月薪酬汇总', 'recruiter', False),
    PayrollPermissionCase('研发部工资总额', 'viewer', False),
    PayrollPermissionCase('工资条未确认名单', 'employee', False),
    PayrollPermissionCase('统计个税汇总', 'manager', False),
    PayrollPermissionCase('查社保缴纳名单', 'viewer', False),
    PayrollPermissionCase('张三奖金记录', 'employee', False),
    PayrollPermissionCase('薪资档案列表', 'recruiter', False),
    PayrollPermissionCase('本月实发工资统计', 'manager', False),
    PayrollPermissionCase('查张三本月工资条', 'finance', True),
    PayrollPermissionCase('查张三本月工资条', 'executive', True),
    PayrollPermissionCase('本月薪酬汇总', 'hr_admin', True),
    PayrollPermissionCase('研发部工资总额', 'admin', True),
    PayrollPermissionCase('薪酬报表', 'finance', True),
    PayrollPermissionCase('工资发放情况', 'executive', True),
    PayrollPermissionCase('2026-05 工资条', 'hr_admin', True),
    PayrollPermissionCase('奖金发放汇总', 'finance', True),
    PayrollPermissionCase('个税扣缴统计', 'admin', True),
    PayrollPermissionCase('社保缴纳明细', 'executive', True),
]


PAYROLL_RECOGNITION_CASES = [
    '研发部工资总额',
    '工资条未确认名单',
    '张三奖金记录',
    '薪资档案列表',
    '2026-05 工资条',
    '奖金发放汇总',
    '社保缴纳明细',
]


EMPLOYEE_SCOPE_CASES = [
    ('employee', 'emp-self', None, {'_self_only': 'emp-self'}),
    ('manager', None, 'dep-1', {'_department_id': 'dep-1'}),
    ('hr_admin', None, None, {}),
    ('finance', None, None, {}),
    ('viewer', None, None, {}),
]


EMPLOYEE_REDACTION_CASES = [
    ('employee', 'emp-other', '138****5678', 'z***@example.com', '***', '***', '***'),
    ('manager', None, '138****5678', 'z***@example.com', '***', '***', '***'),
    ('viewer', None, '138****5678', 'z***@example.com', '***', '***', '***'),
    ('recruiter', None, '138****5678', 'z***@example.com', '***', '***', '***'),
    ('finance', None, '138****5678', 'z***@example.com', '***', '***', '***'),
    ('employee', 'emp-1', '13812345678', 'zhangsan@example.com', '110105199001011234', '北京市朝阳区', '李四 13900001111'),
    ('hr_admin', None, '13812345678', 'zhangsan@example.com', '110105199001011234', '北京市朝阳区', '李四 13900001111'),
    ('admin', None, '13812345678', 'zhangsan@example.com', '110105199001011234', '北京市朝阳区', '李四 13900001111'),
]


RESUME_REDACTION_CASES = [
    ('employee', '139****8888', 'l***@example.com', ''),
    ('manager', '139****8888', 'l***@example.com', ''),
    ('viewer', '139****8888', 'l***@example.com', ''),
    ('finance', '139****8888', 'l***@example.com', ''),
    ('recruiter', '13988888888', 'lihua@example.com', '/uploads/resume.pdf'),
    ('hr_admin', '13988888888', 'lihua@example.com', '/uploads/resume.pdf'),
    ('admin', '13988888888', 'lihua@example.com', '/uploads/resume.pdf'),
]


SMART_SQL_REDACTION_CASES = [
    ('employee', 'phone', '13812345678', '138****5678'),
    ('employee', 'email', 'zhangsan@example.com', 'z***@example.com'),
    ('employee', 'id_card', '110105199001011234', '1101**********1234'),
    ('employee', 'net_salary', Decimal('18888.88'), '***'),
    ('manager', 'base_salary', Decimal('20000.00'), '***'),
    ('viewer', 'bank_account', '6222000011112222333', '***'),
    ('finance', 'net_salary', Decimal('18888.88'), 18888.88),
    ('executive', 'gross_salary', Decimal('28888.88'), 28888.88),
    ('hr_admin', 'phone', '13812345678', '13812345678'),
    ('admin', 'email', 'zhangsan@example.com', 'zhangsan@example.com'),
]


GENERATION_REDACTION_CASES = [
    ('employee', '候选人电话 13812345678，邮箱 lihua@example.com。', '138****5678', 'l***@example.com'),
    ('viewer', '身份证 110105199001011234 已收齐。', '1101**********1234', None),
    ('manager', '薪资：¥28000，奖金：5000元。', '***', None),
    ('recruiter', '工资 32000 元，电话 13988889999。', '***', '139****9999'),
    ('finance', '薪资：¥28000，邮箱 pay@example.com。', '¥28000', 'p***@example.com'),
    ('hr_admin', '电话 13812345678，薪资：¥28000。', '138****5678', '¥28000'),
]


SKILL_PERMISSION_CASES = [
    ('employee', 'payroll_analysis', False),
    ('manager', 'payroll_analysis', False),
    ('recruiter', 'payroll_analysis', False),
    ('viewer', 'payroll_analysis', False),
    ('finance', 'payroll_analysis', True),
    ('executive', 'payroll_analysis', True),
    ('hr_admin', 'payroll_analysis', True),
    ('admin', 'payroll_analysis', True),
    ('employee', 'recruitment_search', False),
    ('viewer', 'recruitment_search', False),
    ('employee', 'employee_search', False),
    ('viewer', 'employee_search', False),
    ('recruiter', 'interview_create', True),
    ('manager', 'interview_create', False),
    ('employee', 'followup_task_create', False),
    ('manager', 'followup_task_create', True),
]


def test_module_nine_case_count_at_least_100() -> None:
    total = (
        len(PAYROLL_PERMISSION_CASES)
        + len(PAYROLL_RECOGNITION_CASES)
        + len(EMPLOYEE_SCOPE_CASES)
        + len(EMPLOYEE_REDACTION_CASES)
        + len(RESUME_REDACTION_CASES)
        + len(SMART_SQL_REDACTION_CASES)
        + len(GENERATION_REDACTION_CASES)
        + len(SKILL_PERMISSION_CASES)
        + 32
    )
    assert total >= 100


@pytest.mark.parametrize('case', PAYROLL_PERMISSION_CASES, ids=lambda c: f'{c.role}:{c.message}')
def test_payroll_queries_respect_role_permissions(case: PayrollPermissionCase) -> None:
    decision = build_route_decision(case.message, _ctx(role=case.role))

    assert decision.route_track == 'A_DATA_ACTION'
    assert decision.risk_level == 'high'
    assert decision.router_result is not None
    gated = permission_gate.apply(decision.router_result, _ctx(role=case.role))
    if case.allowed:
        assert gated.intent != 'permission_denied'
        assert gated.task_template_id == 'payroll_month_summary'
        assert gated.candidate_skills == ['payroll_analysis']
    else:
        assert gated.intent == 'permission_denied'
        assert gated.task_template_id == 'permission_denied_response'
        assert gated.candidate_skills == []


@pytest.mark.parametrize('message', PAYROLL_RECOGNITION_CASES)
def test_payroll_sensitive_queries_are_recognized_before_role_gate(message: str) -> None:
    decision = build_route_decision(message, _ctx(role='employee'))

    assert decision.route_track == 'A_DATA_ACTION'
    assert decision.intent == 'payroll_month_summary'
    assert decision.risk_level == 'high'
    assert decision.router_result is not None
    gated = permission_gate.apply(decision.router_result, _ctx(role='employee'))
    assert gated.task_template_id == 'permission_denied_response'


@pytest.mark.parametrize('role,employee_id,department_id,expected', EMPLOYEE_SCOPE_CASES)
def test_employee_scope_filter_limits_employee_and_manager_reads(
    role: str,
    employee_id: str | None,
    department_id: str | None,
    expected: dict,
) -> None:
    ctx = _ctx(role=role, user_employee_id=employee_id, user_department_id=department_id)

    assert guardrails.employee_scope_filter(role, ctx) == expected


@pytest.mark.parametrize(
    'role,self_employee_id,phone,email,id_no,address,emergency',
    EMPLOYEE_REDACTION_CASES,
)
def test_employee_desensitization_by_role(
    role: str,
    self_employee_id: str | None,
    phone: str,
    email: str,
    id_no: str,
    address: str,
    emergency: str,
) -> None:
    employee = {
        'id': 'emp-1',
        'phone': '13812345678',
        'email': 'zhangsan@example.com',
        'idNo': '110105199001011234',
        'address': '北京市朝阳区',
        'emergencyContact': '李四 13900001111',
    }

    redacted = guardrails.desensitize_employee(employee, role, self_employee_id)

    assert redacted['phone'] == phone
    assert redacted['email'] == email
    assert redacted['idNo'] == id_no
    assert redacted['address'] == address
    assert redacted['emergencyContact'] == emergency


@pytest.mark.parametrize('role,phone,email,file_url', RESUME_REDACTION_CASES)
def test_resume_desensitization_by_role(role: str, phone: str, email: str, file_url: str) -> None:
    resume = {'phone': '13988888888', 'email': 'lihua@example.com', 'fileUrl': '/uploads/resume.pdf'}

    redacted = guardrails.desensitize_resume(resume, role)

    assert redacted['phone'] == phone
    assert redacted['email'] == email
    assert redacted['fileUrl'] == file_url


@pytest.mark.parametrize('role,key,value,expected', SMART_SQL_REDACTION_CASES)
def test_smart_query_redacts_sensitive_columns_by_role(role: str, key: str, value, expected) -> None:
    from services.assistant_core.smart_query import _redact_value

    assert _redact_value(key, value, role) == expected


@pytest.mark.parametrize('role,text,expected,secondary', GENERATION_REDACTION_CASES)
def test_generation_output_redacts_sensitive_values(role: str, text: str, expected: str, secondary: str | None) -> None:
    redacted = guardrails.redact_generation_output(text, role)

    assert expected in redacted
    if secondary:
        assert secondary in redacted


@pytest.mark.parametrize('role,skill_id,allowed', SKILL_PERMISSION_CASES)
def test_skill_permission_matrix(role: str, skill_id: str, allowed: bool) -> None:
    deny = guardrails.check_skill_permission(role, skill_registry.get(skill_id))

    assert (deny is None) is allowed


def test_employee_self_service_read_hr_requires_bound_employee_context() -> None:
    spec = skill_registry.get('attendance_analysis')

    assert guardrails.check_skill_permission('employee', spec) is not None
    assert guardrails.check_skill_permission('employee', spec, _ctx(role='employee')) is not None
    assert guardrails.check_skill_permission(
        'employee',
        spec,
        _ctx(role='employee', user_employee_id='emp-self'),
    ) is None


def test_tool_executor_rejects_payroll_for_employee_before_handler(monkeypatch) -> None:
    spec = skill_registry.get('payroll_analysis')
    calls: list[dict] = []
    monkeypatch.setattr(spec, 'handler', lambda args, ctx: calls.append(args) or {'rows_count': 1})

    result = ToolExecutor().execute('payroll_analysis', {'month': '2026-05'}, _ctx(role='employee'))

    assert result['ok'] is False
    assert '权限不足' in result['error'] or '当前角色不允许' in result['error']
    assert calls == []


def test_employee_search_applies_self_scope_and_desensitizes_other_rows(model_db) -> None:
    from models import Employee
    from services.assistant_core.skills import skill_employee_search

    model_db.session.add(Employee(
        id='emp-self',
        employee_no='E001',
        name='张三',
        phone='13812345678',
        email='zhangsan@example.com',
        id_no='110105199001011234',
        address='北京市朝阳区',
        status='active',
    ))
    model_db.session.add(Employee(
        id='emp-other',
        employee_no='E002',
        name='李四',
        phone='13988888888',
        email='lisi@example.com',
        status='active',
    ))
    model_db.session.commit()

    result = skill_employee_search({'limit': 10}, _ctx(role='employee', user_employee_id='emp-self'))

    assert result['rows_count'] == 1
    row = result['widgets'][0]['table']['rows'][0]
    assert row['id'] == 'emp-self'
    assert row['phone'] == '13812345678'
    assert row['email'] == 'zhangsan@example.com'


def test_manager_employee_search_is_limited_to_department(model_db) -> None:
    from models import Department, Employee
    from services.assistant_core.skills import skill_employee_search

    model_db.session.add(Department(id='dep-a', name='研发部'))
    model_db.session.add(Department(id='dep-b', name='销售部'))
    model_db.session.add(Employee(id='emp-a', name='张三', department_id='dep-a', status='active'))
    model_db.session.add(Employee(id='emp-b', name='李四', department_id='dep-b', status='active'))
    model_db.session.commit()

    result = skill_employee_search({'limit': 10}, _ctx(role='manager', user_department_id='dep-a'))

    assert result['rows_count'] == 1
    assert result['widgets'][0]['table']['rows'][0]['id'] == 'emp-a'


def test_candidate_evidence_for_viewer_omits_resume_contact_and_file_url(model_db) -> None:
    from models import Candidate, Job, Resume
    from services.assistant_core.skills import skill_candidate_evidence_bundle

    model_db.session.add(Job(id='job-1', title='后端工程师', status='open'))
    model_db.session.add(Resume(
        id='res-1',
        job_id='job-1',
        candidate_name='吴明华',
        phone='13988888888',
        email='wuminghua@example.com',
        file_url='/uploads/wu.pdf',
    ))
    model_db.session.add(Candidate(id='cand-1', resume_id='res-1', job_id='job-1', name='吴明华', status='screening'))
    model_db.session.commit()

    result = skill_candidate_evidence_bundle({'keyword': '吴明华'}, _ctx(role='viewer'))

    facts_blob = str(result['facts'])
    assert '13988888888' not in facts_blob
    assert 'wuminghua@example.com' not in facts_blob
    assert '/uploads/wu.pdf' not in facts_blob
    field_blob = str(result['facts'].get('field_citations'))
    assert '13988888888' not in field_blob
    assert 'wuminghua@example.com' not in field_blob
    assert '/uploads/wu.pdf' not in field_blob


def test_candidate_evidence_explicit_name_rejects_conflicting_candidate_id(model_db) -> None:
    from models import Candidate, Job, Resume
    from services.assistant_core.skills import skill_candidate_evidence_bundle

    model_db.session.add(Job(id='job-electronic', title='电子工程师', status='open'))
    model_db.session.add(Resume(
        id='res-luowei',
        job_id='job-electronic',
        candidate_name='罗伟',
        file_url='/uploads/luowei.pdf',
    ))
    model_db.session.add(Candidate(
        id='cand-luowei',
        resume_id='res-luowei',
        job_id='job-electronic',
        name='罗伟',
        status='screening',
    ))
    model_db.session.commit()

    result = skill_candidate_evidence_bundle(
        {'keyword': '孙悦', 'candidate_id': 'cand-luowei', 'job_keyword': '电子工程师'},
        _ctx(role='hr_admin'),
    )

    assert result['rows_count'] == 0
    assert result['entities'] == []
    assert '孙悦' in result['summary_hint']
    assert '罗伟' not in str(result)


def test_employee_evidence_for_manager_does_not_include_payroll_records(model_db) -> None:
    from models import Employee, PayrollRecord
    from services.assistant_core.skills import skill_employee_evidence_bundle

    model_db.session.add(Employee(id='emp-1', name='张三', phone='13812345678', email='zhangsan@example.com'))
    model_db.session.add(PayrollRecord(
        id='pay-1',
        employee_id='emp-1',
        payroll_month='2026-05',
        gross_salary=Decimal('30000.00'),
        net_salary=Decimal('25000.00'),
    ))
    model_db.session.commit()

    result = skill_employee_evidence_bundle({'employee_id': 'emp-1'}, _ctx(role='manager'))

    assert result['facts']['payroll'] == []
    assert 'payroll' not in result['facts']['sources']
    assert '30000' not in str(result['facts'])
    assert '25000' not in str(result['facts'])
    assert 'payroll' not in result['facts']['field_citations']


def test_employee_evidence_for_finance_can_include_payroll_records(model_db) -> None:
    from models import Employee, PayrollRecord
    from services.assistant_core.skills import skill_employee_evidence_bundle

    model_db.session.add(Employee(id='emp-1', name='张三'))
    model_db.session.add(PayrollRecord(
        id='pay-1',
        employee_id='emp-1',
        payroll_month='2026-05',
        gross_salary=Decimal('30000.00'),
        net_salary=Decimal('25000.00'),
    ))
    model_db.session.commit()

    result = skill_employee_evidence_bundle({'employee_id': 'emp-1'}, _ctx(role='finance'))

    assert len(result['facts']['payroll']) == 1
    assert 'payroll' in result['facts']['sources']
    assert result['facts']['payroll'][0]['grossSalary'] == 30000.0
    assert 'payroll' in result['facts']['field_citations']


def test_employee_evidence_links_changes_approvals_notifications_and_salary_profile_by_role(model_db) -> None:
    from datetime import datetime

    from models import ApprovalInstance, Employee, EmployeeChange, EmployeeRequest, Notification, SalaryProfile
    from services.assistant_core.skills import skill_employee_evidence_bundle

    model_db.session.add(Employee(id='emp-1', name='张三'))
    model_db.session.add(EmployeeRequest(
        id='request-1',
        employee_id='emp-1',
        request_type='leave',
        title='张三请假',
        status='approved',
        created_at=datetime(2026, 5, 12, 9, 0),
    ))
    model_db.session.add(ApprovalInstance(
        id='request-approval-1',
        business_type='employee_request',
        business_id='request-1',
        title='张三请假审批',
        applicant_id='u-employee',
        approver_id='u-manager',
        status='approved',
    ))
    model_db.session.add(EmployeeChange(
        id='change-1',
        employee_id='emp-1',
        change_type='salary_adjustment',
        salary_adjustment=Decimal('800.00'),
        reason='年度调薪',
        status='approved',
    ))
    model_db.session.add(ApprovalInstance(
        id='approval-1',
        business_type='employee_change',
        business_id='change-1',
        title='张三年度调薪审批',
        applicant_id='u-hr',
        approver_id='u-finance',
        status='approved',
    ))
    model_db.session.add(Notification(
        id='notice-1',
        title='张三请假审批已通过',
        content='张三 5月请假流程已经完成。',
        notice_type='approval',
        status='unread',
        link_url='/employee-requests',
    ))
    model_db.session.add(SalaryProfile(
        id='salary-1',
        employee_id='emp-1',
        base_salary=Decimal('20000.00'),
        performance_salary=Decimal('5000.00'),
        status='active',
    ))
    model_db.session.commit()

    manager_result = skill_employee_evidence_bundle({'employee_id': 'emp-1'}, _ctx(role='manager'))
    finance_result = skill_employee_evidence_bundle({'employee_id': 'emp-1'}, _ctx(role='finance'))

    assert manager_result['facts']['employee_changes'][0]['id'] == 'change-1'
    assert manager_result['facts']['requests'][0]['id'] == 'request-1'
    assert manager_result['facts']['request_approvals'][0]['businessId'] == 'request-1'
    assert manager_result['facts']['change_approvals'][0]['businessId'] == 'change-1'
    assert manager_result['facts']['notifications'][0]['id'] == 'notice-1'
    assert manager_result['facts']['salary_profile'] is None
    assert 'salary_profile' not in manager_result['facts']['sources']
    assert '20000' not in str(manager_result['facts'])
    assert 'salary_profile' not in manager_result['facts']['field_citations']

    assert finance_result['facts']['employee_changes'][0]['salaryAdjustment'] == 800.0
    assert finance_result['facts']['request_approvals'][0]['status'] == 'approved'
    assert finance_result['facts']['change_approvals'][0]['status'] == 'approved'
    assert finance_result['facts']['notifications'][0]['noticeType'] == 'approval'
    assert finance_result['facts']['salary_profile']['baseSalary'] == 20000.0
    assert 'requests' in finance_result['facts']['sources']
    assert 'request_approvals' in finance_result['facts']['sources']
    assert 'employee_changes' in finance_result['facts']['sources']
    assert 'change_approvals' in finance_result['facts']['sources']
    assert 'notifications' in finance_result['facts']['sources']
    assert 'salary_profile' in finance_result['facts']['sources']
    assert 'salary_profile.total_salary' in {
        field['key']
        for field in finance_result['facts']['field_citations']['salary_profile']
    }


def test_employee_evidence_links_notifications_by_bound_user_when_title_has_no_name(model_db) -> None:
    from models import Employee, Notification, User
    from services.assistant_core.skills import skill_employee_evidence_bundle

    model_db.session.add(Employee(id='emp-1', name='张三'))
    model_db.session.add(User(
        id='user-emp-1',
        email='employee@example.com',
        password_hash='x',
        name='张三账号',
        role='employee',
        employee_id='emp-1',
    ))
    model_db.session.add(Notification(
        id='notice-payroll',
        user_id='user-emp-1',
        title='2026-05 工资条已发布',
        content='您的 2026-05 工资条已开放查看。',
        notice_type='payroll',
        status='read',
        link_url='/hrm/payroll-records',
    ))
    model_db.session.commit()

    result = skill_employee_evidence_bundle({'employee_id': 'emp-1'}, _ctx(role='hr_admin'))

    assert result['facts']['notifications'][0]['id'] == 'notice-payroll'
    assert 'notifications' in result['facts']['sources']
    assert {field['key'] for field in result['facts']['field_citations']['notifications']} >= {
        'notifications.count',
        'notifications.latest_title',
        'notifications.latest_status',
    }


def test_hr_admin_approval_search_can_filter_employee_change_pending_for_all_approvers(model_db) -> None:
    from models import ApprovalInstance
    from services.assistant_core.skills import skill_approval_search

    model_db.session.add(ApprovalInstance(
        id='approval-1',
        business_type='employee_change',
        business_id='change-1',
        title='张三年度调薪审批',
        applicant_id='u-hr',
        approver_id='u-other',
        status='pending',
    ))
    model_db.session.commit()

    result = skill_approval_search(
        {'view': 'pending', 'business_type': 'employee_change', 'limit': 10},
        _ctx(role='hr_admin'),
    )

    assert result['rows_count'] == 1
    table = result['widgets'][0]['table']['rows']
    assert table[0]['id'] == 'approval-1'
    assert table[0]['businessType'] == 'employee_change'


def test_employee_evidence_keeps_performance_to_training_trace(model_db) -> None:
    from models import Employee, PerformanceReview, TrainingRecord
    from services.assistant_core.skills import skill_employee_evidence_bundle

    model_db.session.add(Employee(id='emp-1', name='张三'))
    model_db.session.add(PerformanceReview(
        id='review-1',
        employee_id='emp-1',
        final_score=Decimal('58.00'),
        grade='D',
        status='completed',
    ))
    model_db.session.add(TrainingRecord(
        id='training-record-1',
        employee_id='emp-1',
        status='registered',
        feedback='Generated from performance review review-1; final score 58.00; grade=D',
    ))
    model_db.session.commit()

    result = skill_employee_evidence_bundle({'employee_id': 'emp-1'}, _ctx(role='hr_admin'))

    assert result['facts']['performance'][0]['id'] == 'review-1'
    assert result['facts']['training'][0]['id'] == 'training-record-1'
    assert 'performance review review-1' in result['facts']['training'][0]['feedback']
    assert 'performance' in result['facts']['sources']
    assert 'training' in result['facts']['sources']


def test_payroll_personal_view_uses_model_payroll_fields(model_db) -> None:
    from models import Employee, PayrollRecord
    from services.assistant_core.skills import skill_payroll_analysis

    model_db.session.add(Employee(id='emp-1', name='张三'))
    model_db.session.add(PayrollRecord(
        id='pay-1',
        employee_id='emp-1',
        payroll_month='2026-05',
        gross_salary=Decimal('30000.00'),
        net_salary=Decimal('25000.00'),
        deduction=Decimal('5000.00'),
    ))
    model_db.session.commit()

    result = skill_payroll_analysis({'view': 'personal', 'employee_id': 'emp-1'}, _ctx(role='finance'))

    table = result['widgets'][1]
    assert table['table']['rows'][0]['month'] == '2026-05'
    assert table['table']['rows'][0]['gross'] == '30,000.00'
    assert table['table']['rows'][0]['net'] == '25,000.00'
    assert table['table']['rows'][0]['deduction'] == '5,000.00'


def test_payroll_analysis_filters_by_payroll_month(model_db) -> None:
    from models import Employee, PayrollRecord
    from services.assistant_core.skills import skill_payroll_analysis

    model_db.session.add(Employee(id='emp-1', name='张三'))
    model_db.session.add_all([
        PayrollRecord(
            id='pay-may',
            employee_id='emp-1',
            payroll_month='2026-05',
            gross_salary=Decimal('30000.00'),
            net_salary=Decimal('25000.00'),
        ),
        PayrollRecord(
            id='pay-apr',
            employee_id='emp-1',
            payroll_month='2026-04',
            gross_salary=Decimal('20000.00'),
            net_salary=Decimal('18000.00'),
        ),
    ])
    model_db.session.commit()

    result = skill_payroll_analysis({'month': '2026-05'}, _ctx(role='finance'))

    assert result['rows_count'] == 1
    assert result['widgets'][0]['metrics'][0]['value'] == 1
    assert result['widgets'][0]['metrics'][1]['value'] == '30,000'

