"""权限守卫与字段脱敏。

策略原则：
- 默认拒绝写入/审批/删除/发送，AI 助手当前只读 + 草稿 + 报表。
- 角色矩阵：admin/hr_admin > recruiter > manager > finance > executive > employee > viewer
- 敏感字段（薪资/身份证/手机号/邮箱/附件）按角色脱敏。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .constants import (
    ACTION_APPROVE, ACTION_DELETE, ACTION_EXECUTE_CODE, ACTION_GENERATE_DRAFT,
    ACTION_READ_HR, ACTION_READ_PUBLIC, ACTION_READ_SENSITIVE,
    ACTION_SEND_MESSAGE, ACTION_WRITE_BUSINESS,
    ACTIONS_FORBIDDEN, ACTIONS_REQUIRE_CONFIRMATION,
    ALLOWED_ACTIONS_AUTO,
    PAYROLL_AUTHORIZED_ROLES, PRIVILEGED_ROLES,
    ROLE_ADMIN, ROLE_EMPLOYEE, ROLE_EXECUTIVE, ROLE_FINANCE,
    ROLE_HR_ADMIN, ROLE_MANAGER, ROLE_RECRUITER, ROLE_VIEWER,
    RISK_HIGH, RISK_LOW, RISK_MEDIUM,
)


# ------------------------------------------------------------------
# 角色 → 允许的动作
# ------------------------------------------------------------------
ROLE_ACTION_MATRIX: Dict[str, set] = {
    ROLE_ADMIN: {
        ACTION_READ_PUBLIC, ACTION_READ_HR, ACTION_READ_SENSITIVE, ACTION_GENERATE_DRAFT,
        ACTION_WRITE_BUSINESS, ACTION_APPROVE, ACTION_SEND_MESSAGE,  # 仍需 confirm
    },
    ROLE_HR_ADMIN: {
        ACTION_READ_PUBLIC, ACTION_READ_HR, ACTION_READ_SENSITIVE, ACTION_GENERATE_DRAFT,
        ACTION_WRITE_BUSINESS, ACTION_APPROVE, ACTION_SEND_MESSAGE,
    },
    ROLE_RECRUITER: {
        ACTION_READ_PUBLIC, ACTION_READ_HR, ACTION_GENERATE_DRAFT, ACTION_WRITE_BUSINESS,
    },
    ROLE_MANAGER: {
        ACTION_READ_PUBLIC, ACTION_READ_HR, ACTION_GENERATE_DRAFT, ACTION_WRITE_BUSINESS,
    },
    ROLE_FINANCE: {
        ACTION_READ_PUBLIC, ACTION_READ_HR, ACTION_READ_SENSITIVE, ACTION_GENERATE_DRAFT,
    },
    ROLE_EXECUTIVE: {
        ACTION_READ_PUBLIC, ACTION_READ_HR, ACTION_READ_SENSITIVE, ACTION_GENERATE_DRAFT,
        ACTION_WRITE_BUSINESS,
    },
    ROLE_EMPLOYEE: {
        ACTION_READ_PUBLIC, ACTION_GENERATE_DRAFT,
    },
    ROLE_VIEWER: {
        ACTION_READ_PUBLIC,
    },
}


def role_can(role: str, action: str) -> bool:
    return action in ROLE_ACTION_MATRIX.get(role, {ACTION_READ_PUBLIC})


def role_in(role: str, allowed: List[str]) -> bool:
    if not allowed:
        return True
    return role in allowed


# ------------------------------------------------------------------
# 任务级权限检查
# ------------------------------------------------------------------
def check_task_permission(role: str, task) -> Optional[str]:
    """检查角色是否允许执行该 task。返回拒绝原因，None 表示通过。"""
    if task.allowed_roles and role not in task.allowed_roles:
        return f'当前角色（{role}）不允许执行此任务'
    return None


# ------------------------------------------------------------------
# Skill 级权限
# ------------------------------------------------------------------
def check_skill_permission(role: str, skill, ctx=None) -> Optional[str]:
    if skill.allowed_roles and role not in skill.allowed_roles:
        return f'当前角色不允许使用 {skill.id}'
    if not role_can(role, skill.action_type):
        if _can_use_employee_self_service_read(role, skill, ctx):
            return None
        return f'权限不足：{skill.action_type}'
    return None


EMPLOYEE_SELF_SERVICE_READ_HR_SKILLS = {
    'employee_search',
    'attendance_analysis',
    'performance_analysis',
    'training_analysis',
    'approval_search',
}


def _can_use_employee_self_service_read(role: str, skill, ctx=None) -> bool:
    """Allow employee-scoped READ_HR tools only when the caller has a self scope.

    The skill allow-list is intentionally narrow. Broad evidence/search tools
    remain blocked because they may traverse other employees, recruitment or
    payroll data unless they implement their own strict self filter.
    """
    if role != ROLE_EMPLOYEE:
        return False
    if getattr(skill, 'action_type', None) != ACTION_READ_HR:
        return False
    if getattr(skill, 'id', None) not in EMPLOYEE_SELF_SERVICE_READ_HR_SKILLS:
        return False
    if ctx is None:
        return False
    return bool(getattr(ctx, 'user_employee_id', None))


# ------------------------------------------------------------------
# 动作拦截
# ------------------------------------------------------------------
def is_forbidden(action: str) -> bool:
    return action in ACTIONS_FORBIDDEN


def needs_confirmation(action: str) -> bool:
    return action in ACTIONS_REQUIRE_CONFIRMATION


# ------------------------------------------------------------------
# 字段脱敏
# ------------------------------------------------------------------
def mask_phone(phone: Optional[str]) -> Optional[str]:
    if not phone:
        return phone
    s = re.sub(r'\D', '', str(phone))
    if len(s) >= 11:
        return s[:3] + '****' + s[-4:]
    if len(s) >= 7:
        return s[:3] + '****' + s[-2:]
    return '***'


def mask_email(email: Optional[str]) -> Optional[str]:
    if not email or '@' not in email:
        return email
    name, domain = email.split('@', 1)
    if len(name) <= 1:
        return f'*@{domain}'
    return f'{name[0]}***@{domain}'


def mask_id_card(idn: Optional[str]) -> Optional[str]:
    if not idn:
        return idn
    s = str(idn)
    if len(s) >= 8:
        return s[:4] + '*' * (len(s) - 8) + s[-4:]
    return '*' * len(s)


def mask_money(amount) -> str:
    """对未授权角色，金额一律不返回明细。"""
    return '***'


def can_view_field(role: str, field: str) -> bool:
    """字段级权限：是否可看明文。"""
    if role in PRIVILEGED_ROLES:
        return True
    sensitive_money = {'salary', 'payroll', 'bonus', 'base_salary', 'gross_salary', 'net_salary'}
    if field in sensitive_money and role not in PAYROLL_AUTHORIZED_ROLES:
        return False
    sensitive_personal = {'id_card', 'id_no', 'bank', 'bank_account', 'address', 'personal_address'}
    if field in sensitive_personal and role != ROLE_FINANCE:
        # 财务也不一定要看，简化为只 admin/hr_admin
        return role in PRIVILEGED_ROLES
    # phone/email 一般员工看自己的可，他人脱敏
    return True


def desensitize_employee(emp_dict: Dict[str, Any], role: str,
                         self_employee_id: Optional[str] = None) -> Dict[str, Any]:
    """对员工字段脱敏。"""
    if not emp_dict:
        return emp_dict
    is_self = self_employee_id and emp_dict.get('id') == self_employee_id
    out = dict(emp_dict)
    if role in PRIVILEGED_ROLES or is_self:
        return out
    # 普通角色 → 脱敏
    if 'phone' in out:
        out['phone'] = mask_phone(out.get('phone'))
    if 'email' in out:
        out['email'] = mask_email(out.get('email'))
    for k in ('idNo', 'id_no', 'idCard', 'address', 'emergencyContact'):
        if k in out:
            out[k] = '***'
    return out


def desensitize_resume(resume_dict: Dict[str, Any], role: str) -> Dict[str, Any]:
    if not resume_dict:
        return resume_dict
    out = dict(resume_dict)
    if role in PRIVILEGED_ROLES or role == ROLE_RECRUITER:
        return out
    if 'phone' in out:
        out['phone'] = mask_phone(out.get('phone'))
    if 'email' in out:
        out['email'] = mask_email(out.get('email'))
    if 'fileUrl' in out:
        out['fileUrl'] = ''  # 隐藏附件直链
    return out


def desensitize_payroll(record: Dict[str, Any], role: str) -> Optional[Dict[str, Any]]:
    """薪酬记录：未授权角色直接拒绝（返回 None）。"""
    if role not in PAYROLL_AUTHORIZED_ROLES:
        return None
    return record


# ------------------------------------------------------------------
# 生成输出安全扫描
# ------------------------------------------------------------------
SENSITIVE_OUTPUT_PATTERNS = {
    'phone': re.compile(r'(?<!\d)1[3-9]\d{9}(?!\d)'),
    'email': re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}'),
    'id_card': re.compile(r'(?<!\d)\d{17}[\dXx](?!\d)'),
    'salary_label': re.compile(r'(薪酬|薪资|工资|月薪|年薪|应发|实发|奖金|个税)'),
    'address_label': re.compile(r'(住址|地址|家庭住址|现居住地)'),
}


def scan_sensitive_output(text: str, role: str) -> List[str]:
    """Return sensitive flags detected in generated text."""
    flags: List[str] = []
    if not text:
        return flags
    for name, pattern in SENSITIVE_OUTPUT_PATTERNS.items():
        if pattern.search(text):
            flags.append(name)
    if role not in PAYROLL_AUTHORIZED_ROLES and 'salary_label' in flags:
        flags.append('unauthorized_payroll_generation')
    return flags


def redact_generation_output(text: str, role: str) -> str:
    """Redact sensitive values before returning generation output."""
    if not text:
        return ''
    out = SENSITIVE_OUTPUT_PATTERNS['phone'].sub(lambda m: mask_phone(m.group(0)) or '***', text)
    out = SENSITIVE_OUTPUT_PATTERNS['email'].sub(lambda m: mask_email(m.group(0)) or '***', out)
    out = SENSITIVE_OUTPUT_PATTERNS['id_card'].sub(lambda m: mask_id_card(m.group(0)) or '***', out)
    if role not in PAYROLL_AUTHORIZED_ROLES:
        # Remove concrete money-like amounts in salary contexts while preserving prose.
        out = re.sub(
            r'((?:薪酬|薪资|工资|月薪|年薪|应发|实发|奖金|个税)[^，。\n:：]{0,12})'
            r'[:：]?[ \t]*[¥￥]?\d+(?:,\d{3})*(?:\.\d+)?[ \t]*(?:元|人民币|k|K|万)?',
            r'\1：***',
            out,
        )
        out = re.sub(r'[¥￥]\s?\d+(?:,\d{3})*(?:\.\d+)?', '***', out)
    return out


def is_sensitive_generation(message: str) -> bool:
    """Whether a generation request is likely to involve sensitive HR data."""
    if not message:
        return False
    return bool(re.search(r'(薪酬|薪资|工资|身份证|手机号|电话|邮箱|住址|地址)', message))


def require_confirmation_for_action(action_type: str, risk_level: str) -> bool:
    """Side-effect and high-risk actions require explicit confirmation."""
    if action_type in ACTIONS_FORBIDDEN:
        return True
    if action_type in ACTIONS_REQUIRE_CONFIRMATION:
        return True
    return risk_level == RISK_HIGH and action_type not in ALLOWED_ACTIONS_AUTO


# ------------------------------------------------------------------
# 数据访问范围
# ------------------------------------------------------------------
def employee_scope_filter(role: str, ctx) -> Dict[str, Any]:
    """返回查询员工时应应用的额外过滤条件。
    例如 manager → 限本部门，employee → 限自己。
    """
    out: Dict[str, Any] = {}
    if role == ROLE_EMPLOYEE and ctx.user_employee_id:
        out['_self_only'] = ctx.user_employee_id
    elif role == ROLE_MANAGER and ctx.user_department_id:
        out['_department_id'] = ctx.user_department_id
    return out


# ------------------------------------------------------------------
# 写操作拦截：用户问题里出现写动词时立即拒绝/标记 confirmation
# ------------------------------------------------------------------
WRITE_VERBS_DELETE = ['删除', '删掉', '删了', '移除', '清除', 'delete', 'remove']
WRITE_VERBS_APPROVE = ['批准', '通过', '同意', '驳回', '拒绝审批', 'approve', 'reject']
WRITE_VERBS_SEND = ['发送', '群发', '通知所有', '发邮件', 'send', '推送']
WRITE_VERBS_CREATE = [
    '创建一个', '新建一个', '新增一个', '新增', '添加', '录入', '帮我建', '帮我创建',
    '帮我新增', '帮我录入', '创建岗位', '新建岗位', '创建员工', '新建员工',
    '创建候选人', '新建候选人', '创建面试', '创建offer', '提交审批',
]
WRITE_VERBS_UPDATE = [
    '修改', '更新', '编辑', '变更', '改成', '改为', '改到', '改一下',
    '调整状态', '调整为', '重新安排', '直接更新',
]
WRITE_VERBS_SQL = ['执行sql', '执行 sql', '运行sql', '直接sql', '原始sql', 'execute sql', 'run sql']
READ_ONLY_STATUS_PATTERNS = [
    r'未通过.{0,8}(人|人员|员工|候选人|名单|记录|有哪些|有多少|多少)',
    r'(人|人员|员工|候选人|名单|记录).{0,8}未通过',
    r'(已通过|通过初筛|未通过).{0,8}(简历|候选人|转正|试用期|名单|记录|有哪些)',
    r'(有哪些|有多少|多少|几位|几份|列出|查看|查询|搜索|统计|简历|候选人|名单|记录).{0,16}(已通过|通过初筛|未通过)',
    r'通过率',
    r'被驳回的申请',
    r'(驳回|通过).{0,4}(率|统计|记录|列表|申请|名单)',
]
SQL_PATTERNS = [
    'select * from', 'insert into', 'update set', 'delete from', 'drop table', 'truncate ',
    'union select', '--', ';--', '/*', '*/', "' or '1", 'xp_cmdshell',
]


def _is_readonly_create_lookup(text: str) -> bool:
    """Return True for "newly added records" questions, not create commands."""
    value = text or ''
    if not re.search(r'(新增|新增了|新来|新收|新投递|新入职)', value):
        return False
    if re.search(
        r'(新增|添加|录入|创建|新建)\s*'
        r'((?:一个|一条|一名|一位|一份)|(?:[一二两三四五六七八九十\d]+\s*(?:个|条|名|位|份))|[：:])',
        value,
    ):
        return False
    if re.search(r'^(帮我|请|麻烦|给|为|替我).{0,12}(新增|添加|录入|创建|新建)', value):
        return False
    return bool(
        re.search(r'(哪些|名单|列表|有多少|多少|几个|几条|几位|记录|查询|查看|看下|看一下|统计|汇总|情况)', value)
        or re.search(r'(本月|本周|最近|近\s*\d+\s*天|上月|上周|今天|昨天|今年|过去).{0,12}(新增|新增了|新来|新收|新投递)', value)
    )


def _is_readonly_lifecycle_lookup(text: str) -> bool:
    value = text or ''
    if not re.search(r'(候选人|简历|简历录入|录入|投递).{0,32}(入职|员工|培训|薪酬|薪资|工资|发薪|合同|离职)', value, re.I):
        return False
    return bool(re.search(r'(关联|关联到|关联了|哪些模块|哪个模块|链路|全链路|流程|流转|现在有哪些数据|有哪些数据|查询|查看|看下|看一下)', value, re.I))


def detect_write_intent(text: str) -> Optional[str]:
    """识别用户问题中的高风险写意图。返回拒绝原因，None 表示安全。"""
    if not text:
        return None
    if _is_readonly_create_lookup(text) or _is_readonly_lifecycle_lookup(text):
        return None
    if (
        re.search(
            r'(怎么|如何|怎样|为什么|说明|流程|教程|解释|操作步骤|怎么做|怎么用).{0,16}'
            r'(新增|添加|录入|创建|新建|删除|修改|编辑|更新|变更|安排|提交|确认|审批|通过|驳回|申请|操作|办理|处理|补卡|上传|导入)',
            text,
        )
        or re.search(
            r'(新增|添加|录入|创建|新建|删除|修改|编辑|更新|变更|安排|提交|确认|审批|通过|驳回|申请|补卡|上传|导入).{0,16}'
            r'(怎么|如何|怎样|为什么|说明|流程|教程|解释|操作步骤|办理|处理)',
            text,
        )
    ):
        return None
    if (
        re.search(r'(通知提醒|系统设置).{0,20}(待办|待跟进|详情|排序|过滤|看板|数据|记录|有哪些|查看|打开)', text)
        and not re.search(r'(删除|移除|清除|修改|更新|改成|改为|调整状态|直接更新)', text)
    ):
        return None
    low = text.lower()
    readonly_status = any(re.search(pattern, text) for pattern in READ_ONLY_STATUS_PATTERNS)

    # SQL 注入或显式 SQL 请求 —— 优先级最高
    for kw in WRITE_VERBS_SQL:
        if kw in low:
            return '出于安全策略，AI 助手不会执行任何原始 SQL，请使用业务页面或筛选条件查询。'
    for kw in SQL_PATTERNS:
        if kw in low:
            return '检测到疑似 SQL 注入或原始 SQL 请求，已拒绝执行。'

    for kw in WRITE_VERBS_DELETE:
        if kw in text or kw in low:
            return '出于数据安全，AI 助手不会自动删除任何记录。如需删除请到对应业务页面操作。'

    for kw in WRITE_VERBS_APPROVE:
        if not readonly_status and (kw in text or kw in low):
            return '审批操作必须由审批人在审批中心确认，AI 助手不自动执行。'

    for kw in WRITE_VERBS_SEND:
        if kw in text or kw in low:
            return 'AI 助手不会自动发送邮件或消息。可以为您生成草稿，由您手动确认后发送。'

    for kw in WRITE_VERBS_UPDATE:
        if kw in text or kw in low:
            return '出于数据安全，AI 助手不会自动修改业务记录。如需修改请到对应业务页面操作。'

    if re.search(
        r'(上传|导入|批量上传|批量导入).{0,12}'
        r'(简历|附件|员工|候选人|考勤|绩效|工资|薪资|Offer|offer|岗位|职位|数据|记录)',
        text,
    ) or re.search(r'(简历|附件|员工|候选人|考勤|绩效|工资|薪资|数据|记录).{0,8}(上传|导入)', text):
        return '创建业务对象或导入数据需要在对应模块操作，AI 助手不自动上传或导入。'

    if re.search(r'(新增|添加|录入|创建|新建).{0,12}(员工|候选人|简历|岗位|职位|Offer|offer|工资条|薪资|绩效|考勤记录|审批|申请)', text):
        return '创建业务对象（面试/Offer/审批等）需要在对应模块操作，AI 助手不自动创建。'

    for kw in WRITE_VERBS_CREATE:
        if kw in text:
            return '创建业务对象（面试/Offer/审批等）需要在对应模块操作，AI 助手不自动创建。'

    return None
