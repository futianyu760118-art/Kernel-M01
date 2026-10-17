"""Governed security and compliance extension MVP services."""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

from models import (
    AssistantAuditLog,
    Employee,
    ExtensionProviderConfig,
    OperationLog,
    Resume,
    SecurityInsightSnapshot,
    Tenant,
    TenantDataRequest,
    db,
)
from services.extension_registry import extension_feature_maturity, extension_read_marker
from services.tenant_data_lifecycle import tenant_data_inventory, tenant_deletion_plan


SECURITY_FEATURES = {
    'data_masking': 'extension.security.data_masking',
    'compliance_reporting': 'extension.security.compliance_reporting',
    'audit_logs': 'extension.security.audit_logs',
    'gdpr': 'extension.security.gdpr',
    'database_encryption': 'extension.security.database_encryption',
    'point_in_time_recovery': 'extension.security.point_in_time_recovery',
    'globalization_locale': 'extension.security.globalization_locale',
    'globalization_currency': 'extension.security.globalization_currency',
    'globalization_timezone': 'extension.security.globalization_timezone',
    'globalization_policy': 'extension.security.globalization_policy',
    'data_lifecycle': 'extension.security.data_lifecycle',
    'mlps_evidence': 'extension.security.mlps_evidence',
}

EXTERNAL_GATES = (
    'Legal review',
    'ICP filing',
    'MLPS decision',
    'Data export/deletion',
    'Restore drill',
    'Final release gate',
)


def _mask(value: Any) -> str:
    raw = str(value or '').strip()
    if not raw:
        return ''
    digest = hashlib.sha256(raw.encode('utf-8')).hexdigest()[:8]
    return f'masked:{digest}'


def _settings(tenant: Tenant | None) -> dict[str, Any]:
    return tenant.settings_json if tenant and isinstance(tenant.settings_json, dict) else {}


class SecurityService:
    @staticmethod
    def build_feature_summary(tenant_id: str, feature: str, operator_id: str | None = None) -> dict[str, Any]:
        if feature not in SECURITY_FEATURES:
            raise LookupError(feature)

        base = SecurityService._base_context(tenant_id)
        builders = {
            'data_masking': SecurityService._data_masking_payload,
            'compliance_reporting': SecurityService._compliance_reporting_payload,
            'audit_logs': SecurityService._audit_logs_payload,
            'gdpr': SecurityService._gdpr_payload,
            'database_encryption': SecurityService._database_encryption_payload,
            'point_in_time_recovery': SecurityService._point_in_time_recovery_payload,
            'globalization_locale': SecurityService._globalization_locale_payload,
            'globalization_currency': SecurityService._globalization_currency_payload,
            'globalization_timezone': SecurityService._globalization_timezone_payload,
            'globalization_policy': SecurityService._globalization_policy_payload,
            'data_lifecycle': SecurityService._data_lifecycle_payload,
            'mlps_evidence': SecurityService._mlps_evidence_payload,
        }
        payload = builders[feature](tenant_id, base)
        feature_key = SECURITY_FEATURES[feature]
        payload.update({
            'featureKey': feature_key,
            'status': extension_feature_maturity(feature_key),
        })
        payload.setdefault('summary', {})['dataScope'] = 'currentTenant'

        payload['snapshot'] = extension_read_marker(feature_key)
        return payload

    @staticmethod
    def _base_context(tenant_id: str) -> dict[str, Any]:
        tenant = db.session.get(Tenant, tenant_id)
        employees = Employee.query.filter_by(tenant_id=tenant_id).order_by(Employee.created_at.desc()).limit(50).all()
        resumes = Resume.query.filter_by(tenant_id=tenant_id).order_by(Resume.created_at.desc()).limit(50).all()
        data_requests = TenantDataRequest.query.filter_by(tenant_id=tenant_id).order_by(TenantDataRequest.created_at.desc()).all()
        operation_logs = OperationLog.query.filter_by(tenant_id=tenant_id).order_by(OperationLog.created_at.desc()).limit(100).all()
        assistant_audits = AssistantAuditLog.query.filter_by(tenant_id=tenant_id).order_by(AssistantAuditLog.created_at.desc()).limit(100).all()
        providers = ExtensionProviderConfig.query.order_by(ExtensionProviderConfig.provider_code.asc()).all()
        return {
            'tenant': tenant,
            'settings': _settings(tenant),
            'employees': employees,
            'resumes': resumes,
            'dataRequests': data_requests,
            'operationLogs': operation_logs,
            'assistantAudits': assistant_audits,
            'providers': providers,
        }

    @staticmethod
    def _data_masking_payload(_tenant_id: str, base: dict[str, Any]) -> dict[str, Any]:
        masked_rows = []
        for employee in base['employees'][:10]:
            masked_rows.append({
                'recordType': 'employee',
                'recordId': employee.id,
                'name': employee.name,
                'maskedEmail': _mask(employee.email),
                'maskedPhone': _mask(employee.phone),
                'strategy': 'sha256_preview_mask',
            })
        for resume in base['resumes'][:10]:
            masked_rows.append({
                'recordType': 'resume',
                'recordId': resume.id,
                'name': resume.candidate_name,
                'maskedEmail': _mask(resume.email),
                'maskedPhone': _mask(resume.phone),
                'strategy': 'sha256_preview_mask',
            })
        return {
            'summary': {
                'maskedRecordCount': len(masked_rows),
                'maskedFieldCount': sum(
                    1
                    for row in masked_rows
                    for key in ('maskedEmail', 'maskedPhone')
                    if row.get(key)
                ),
                'maskingMode': 'deterministic_preview',
            },
            'maskedSamples': masked_rows,
        }

    @staticmethod
    def _compliance_reporting_payload(_tenant_id: str, base: dict[str, Any]) -> dict[str, Any]:
        requests = base['dataRequests']
        controls = [
            {'key': 'tenant_isolation', 'label': '租户隔离', 'status': 'implemented'},
            {'key': 'module_gating', 'label': '模块开关', 'status': 'implemented'},
            {'key': 'operation_audit', 'label': '操作审计', 'status': 'implemented'},
            {'key': 'external_evidence', 'label': '外部上线证据', 'status': 'pending_owner_evidence'},
        ]
        return {
            'summary': {
                'controlCount': len(controls),
                'implementedControlCount': sum(1 for row in controls if row['status'] == 'implemented'),
                'externalGateCount': len(EXTERNAL_GATES),
                'externalAcceptedGateCount': 0,
                'externalGateAccepted': False,
            },
            'controls': controls,
            'externalGates': [
                {'gate': gate, 'status': 'Pending owner evidence'}
                for gate in EXTERNAL_GATES
            ],
            'dataRequests': [row.to_dict() for row in requests[:20]],
        }

    @staticmethod
    def _audit_logs_payload(_tenant_id: str, base: dict[str, Any]) -> dict[str, Any]:
        operation_rows = [
            {
                'id': row.id,
                'module': row.module,
                'action': row.action,
                'targetId': row.target_id,
                'createdAt': row.created_at.isoformat() if row.created_at else None,
            }
            for row in base['operationLogs'][:20]
        ]
        assistant_rows = [
            {
                'id': row.id,
                'requestId': row.request_id,
                'provider': row.provider,
                'modelProfile': row.model_profile,
                'useCase': row.use_case,
                'status': row.status,
                'createdAt': row.created_at.isoformat() if row.created_at else None,
            }
            for row in base['assistantAudits'][:20]
        ]
        return {
            'summary': {
                'operationLogCount': len(base['operationLogs']),
                'assistantAuditCount': len(base['assistantAudits']),
                'retentionMode': 'review_before_execution',
            },
            'operationLogs': operation_rows,
            'assistantAudits': assistant_rows,
        }

    @staticmethod
    def _gdpr_payload(_tenant_id: str, base: dict[str, Any]) -> dict[str, Any]:
        requests = [row.to_dict() for row in base['dataRequests']]
        return {
            'summary': {
                'dataRequestCount': len(requests),
                'exportRequestCount': sum(1 for row in requests if row['requestType'] == 'export'),
                'deletionRequestCount': sum(1 for row in requests if row['requestType'] == 'deletion'),
                'ownerEvidenceRequired': True,
            },
            'requests': requests[:30],
            'rights': [
                {'key': 'access', 'label': '访问权', 'status': 'workflow_ready'},
                {'key': 'export', 'label': '可携带权', 'status': 'structured_export_ready'},
                {'key': 'erasure', 'label': '删除/匿名化', 'status': 'approval_required'},
            ],
        }

    @staticmethod
    def _database_encryption_payload(_tenant_id: str, base: dict[str, Any]) -> dict[str, Any]:
        provider_rows = [
            {
                'providerCode': row.provider_code,
                'providerType': row.provider_type,
                'status': row.status,
                'hasSecretRef': bool(row.secret_ref),
            }
            for row in base['providers'][:20]
        ]
        return {
            'summary': {
                'secretReferenceControl': 'platform_secret_ref',
                'providerConfigCount': len(provider_rows),
                'tenantPayloadSecretLeakGuard': 'enabled',
                'databaseEncryptionStatus': 'adapter_ready_pending_ops_evidence',
            },
            'controls': [
                {'key': 'secret_ref_only', 'label': '供应商密钥引用化', 'status': 'implemented'},
                {'key': 'safe_export_columns', 'label': '结构化导出敏感列剔除', 'status': 'implemented'},
                {'key': 'database_tde', 'label': '数据库透明加密', 'status': 'pending_ops_evidence'},
            ],
            'providers': provider_rows,
        }

    @staticmethod
    def _point_in_time_recovery_payload(_tenant_id: str, base: dict[str, Any]) -> dict[str, Any]:
        return {
            'summary': {
                'restoreDrillAccepted': False,
                'restoreEvidenceStatus': 'Pending owner evidence',
                'targetProject': 'hengpintong-saas-preview',
                'targetBaseUrl': 'http://localhost:18280',
            },
            'runbookChecks': [
                {'key': 'backup_mysql', 'label': 'MySQL 备份', 'status': 'documented'},
                {'key': 'backup_minio', 'label': 'MinIO 备份', 'status': 'documented'},
                {'key': 'restore_drill', 'label': '真实恢复演练', 'status': 'Pending owner evidence'},
            ],
            'tenantContext': {
                'tenantId': _tenant_id,
                'operationLogCount': len(base['operationLogs']),
            },
        }

    @staticmethod
    def _globalization_locale_payload(_tenant_id: str, base: dict[str, Any]) -> dict[str, Any]:
        settings = base['settings']
        locales = settings.get('locales') if isinstance(settings.get('locales'), list) else ['zh-CN', 'en-US']
        return {
            'summary': {
                'defaultLocale': settings.get('locale') or 'zh-CN',
                'supportedLocaleCount': len(locales),
            },
            'locales': [{'code': code, 'status': 'mvp_ready'} for code in locales],
        }

    @staticmethod
    def _globalization_currency_payload(_tenant_id: str, base: dict[str, Any]) -> dict[str, Any]:
        settings = base['settings']
        currencies = settings.get('currencies') if isinstance(settings.get('currencies'), list) else ['CNY', 'USD']
        return {
            'summary': {
                'defaultCurrency': settings.get('currency') or 'CNY',
                'supportedCurrencyCount': len(currencies),
            },
            'currencies': [{'code': code, 'status': 'mvp_ready'} for code in currencies],
        }

    @staticmethod
    def _globalization_timezone_payload(_tenant_id: str, base: dict[str, Any]) -> dict[str, Any]:
        settings = base['settings']
        timezones = settings.get('timezones') if isinstance(settings.get('timezones'), list) else ['Asia/Shanghai', 'UTC']
        return {
            'summary': {
                'defaultTimezone': settings.get('timezone') or 'Asia/Shanghai',
                'supportedTimezoneCount': len(timezones),
            },
            'timezones': [{'code': code, 'status': 'mvp_ready'} for code in timezones],
        }

    @staticmethod
    def _globalization_policy_payload(_tenant_id: str, base: dict[str, Any]) -> dict[str, Any]:
        policy = base['settings'].get('globalPolicy') if isinstance(base['settings'].get('globalPolicy'), dict) else {}
        regions = policy.get('regions') if isinstance(policy.get('regions'), list) else ['CN']
        policies = [
            {'key': 'dataResidency', 'value': policy.get('dataResidency') or 'cn-primary'},
            {'key': 'crossBorderTransfer', 'value': policy.get('crossBorderTransfer') or 'approval_required'},
            {'key': 'legalHold', 'value': policy.get('legalHold') or 'review_before_execution'},
        ]
        return {
            'summary': {
                'regionCount': len(regions),
                'policyCount': len(policies),
                'crossBorderTransfer': policy.get('crossBorderTransfer') or 'approval_required',
            },
            'regions': [{'code': code, 'status': 'configured'} for code in regions],
            'policies': policies,
        }

    @staticmethod
    def _data_lifecycle_payload(tenant_id: str, _base: dict[str, Any]) -> dict[str, Any]:
        inventory = tenant_data_inventory(tenant_id, include_empty=False)
        plan = tenant_deletion_plan(tenant_id, tenant_slug=None, mode='anonymize', include_empty=False)
        return {
            'summary': {
                'tableCount': inventory['tableCount'],
                'totalRows': inventory['totalRows'],
                'requiresOwnerApproval': plan['requiresOwnerApproval'],
                'requiresFinalBackup': plan['requiresFinalBackup'],
                'destructive': plan['destructive'],
            },
            'inventory': {
                'tableCount': inventory['tableCount'],
                'totalRows': inventory['totalRows'],
                'tables': inventory['tables'][:20],
            },
            'deletionPlan': {
                'mode': plan['mode'],
                'destructive': plan['destructive'],
                'requiresOwnerApproval': plan['requiresOwnerApproval'],
                'requiresFinalBackup': plan['requiresFinalBackup'],
                'requiresDualConfirmation': plan['requiresDualConfirmation'],
                'requiredSteps': plan['requiredSteps'],
                'retentionReview': plan['retentionReview'],
            },
        }

    @staticmethod
    def _mlps_evidence_payload(_tenant_id: str, _base: dict[str, Any]) -> dict[str, Any]:
        evidence_items = [
            {'key': 'applicability', 'label': '等保适用性判断', 'status': 'Pending owner evidence'},
            {'key': 'level', 'label': '等保级别', 'status': 'Pending owner evidence'},
            {'key': 'assessment_plan', 'label': '测评/备案计划', 'status': 'Pending owner evidence'},
        ]
        return {
            'summary': {
                'mlpsAccepted': False,
                'evidenceItemCount': len(evidence_items),
                'pendingEvidenceCount': len(evidence_items),
            },
            'evidenceItems': evidence_items,
            'externalGate': {
                'gate': 'MLPS decision',
                'status': 'Pending owner evidence',
            },
        }
