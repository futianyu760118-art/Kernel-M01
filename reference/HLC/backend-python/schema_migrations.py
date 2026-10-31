"""Small, dependency-free schema compatibility helpers.

The project historically used db.create_all() and a hand-written init.sql. SQLAlchemy
will create missing tables, but it will not add missing columns to existing tables.
This file keeps old deployments runnable after the HRM upgrade without introducing
Alembic or a heavyweight migration dependency.
"""
import os
from contextlib import contextmanager

import bcrypt
from sqlalchemy import text
from sqlalchemy.exc import OperationalError, ProgrammingError
from models import (
    db,
    Role,
    Setting,
    Tenant,
    Plan,
    User,
    TENANT_SCOPED_MODELS,
)
from services.assistant_core.model_settings import SETTING_KEY as ASSISTANT_MODEL_SETTINGS_KEY
from services.assistant_core.model_settings import defaults as assistant_model_settings_defaults
from services.tenancy import (
    DEFAULT_TENANT_NAME,
    DEFAULT_TENANT_SLUG,
    SAAS_MODULE_KEYS,
    default_tenant,
    seed_tenant_modules,
)
import uuid


MYSQL_COLUMNS = {
    'departments': [
        ('sort_order', 'INT NULL DEFAULT 0'),
        ('short_name', 'VARCHAR(100) NULL'),
    ],
    'users': [
        ('phone', 'VARCHAR(30) NULL'),
        ('status', "VARCHAR(20) NULL DEFAULT 'active'"),
        ('department_id', 'VARCHAR(36) NULL'),
        ('employee_id', 'VARCHAR(36) NULL'),
        ('must_change_password', 'TINYINT(1) NOT NULL DEFAULT 0'),
        ('last_login_at', 'DATETIME NULL'),
        ('updated_at', 'DATETIME NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP'),
    ],
    'jobs': [
        ('category', 'VARCHAR(100) NULL'),
        ('department', 'VARCHAR(100) NULL'),
        ('department_id', 'VARCHAR(36) NULL'),
        ('level', 'VARCHAR(50) NULL'),
        ('responsibilities', 'TEXT NULL'),
        ('requirements', 'TEXT NULL'),
        ('headcount', 'INT NULL DEFAULT 1'),
        ('location', 'VARCHAR(120) NULL'),
        ('employment_type', "VARCHAR(50) NULL DEFAULT '全职'"),
        ('salary_range', 'VARCHAR(100) NULL'),
        ('created_by', 'VARCHAR(36) NULL'),
        ('updated_by', 'VARCHAR(36) NULL'),
    ],
    'resumes': [
        ('updated_at', 'DATETIME NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP'),
        ('created_by', 'VARCHAR(36) NULL'),
        ('updated_by', 'VARCHAR(36) NULL'),
    ],
    'interviews': [
        ('updated_at', 'DATETIME NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP'),
        ('created_by', 'VARCHAR(36) NULL'),
        ('updated_by', 'VARCHAR(36) NULL'),
        ('feedback_text', 'TEXT NULL'),
        ('feedback_file_url', 'VARCHAR(500) NULL'),
        ('feedback_file_type', 'VARCHAR(20) NULL'),
        ('ai_analysis', 'JSON NULL'),
        ('analyzed_at', 'DATETIME NULL'),
        ('room_id', 'VARCHAR(64) NULL'),
        ('invite_token', 'VARCHAR(64) NULL'),
        ('recording_url', 'VARCHAR(500) NULL'),
        ('transcript', 'TEXT NULL'),
        ('meeting_started_at', 'DATETIME NULL'),
        ('meeting_ended_at', 'DATETIME NULL'),
    ],
    'feedbacks': [
        ('reopen_reason', 'TEXT NULL'),
        ('reopen_current_state', 'TEXT NULL'),
        ('reopen_history', 'JSON NULL'),
    ],
    'notifications': [
        ('employee_id', 'VARCHAR(36) NULL'),
    ],
    'employees': [
        ('oa_person_id', 'VARCHAR(40) NULL'),
        ('oa_login_id', 'VARCHAR(100) NULL'),
        ('oa_workcode', 'VARCHAR(100) NULL'),
        ('oa_status', 'VARCHAR(20) NULL'),
        ('oa_department_id', 'VARCHAR(40) NULL'),
        ('oa_jobtitle_id', 'VARCHAR(40) NULL'),
        ('erp_user_id', 'VARCHAR(40) NULL'),
        ('source_system', 'VARCHAR(40) NULL'),
        ('source_modified_at', 'DATETIME NULL'),
        ('source_synced_at', 'DATETIME NULL'),
        ('employee_type', 'VARCHAR(20) NULL'),
        ('performance_participant', 'TINYINT(1) NOT NULL DEFAULT 0'),
    ],
    'attendance_timesheets': [
        ('overtime_hours', 'DECIMAL(6,2) NULL DEFAULT 0'),
        ('leave_hours', 'DECIMAL(6,2) NULL DEFAULT 0'),
        ('event_summary', 'VARCHAR(200) NULL'),
        ('check_in_fixed', 'TINYINT(1) NOT NULL DEFAULT 0'),
        ('check_out_fixed', 'TINYINT(1) NOT NULL DEFAULT 0'),
        ('punch_times', 'JSON NULL'),
    ],
    'performance_cycles': [
        ('tenant_id', 'VARCHAR(36) NULL'),
    ],
    'performance_templates': [
        ('tenant_id', 'VARCHAR(36) NULL'),
    ],
    'performance_reviews': [
        ('tenant_id', 'VARCHAR(36) NULL'),
        ('manager_id', 'VARCHAR(36) NULL'),
        ('auto_score', 'DECIMAL(6,2) NULL'),
        ('self_submitted_at', 'DATETIME NULL'),
        ('manager_scored_at', 'DATETIME NULL'),
        ('confirmed_at', 'DATETIME NULL'),
    ],
    'performance_score_rules': [
        ('target_source', "VARCHAR(20) NULL DEFAULT 'fixed'"),
        ('target_oa_department_id', 'VARCHAR(40) NULL'),
        ('scoring_mode', "VARCHAR(20) NULL DEFAULT 'threshold'"),
    ],
    'exam_papers': [
        ('question_mode', "VARCHAR(20) NULL DEFAULT 'fixed'"),
        ('draw_count', 'INT NULL'),
    ],
    'assistant_audit_logs': [
        ('resource_domain', 'VARCHAR(80) NULL'),
        ('ai_tier', 'VARCHAR(40) NULL'),
        ('tenant_ai_entitlement_id', 'VARCHAR(36) NULL'),
    ],
    'platform_operation_logs': [
        ('result', "VARCHAR(20) NOT NULL DEFAULT 'success'"),
        ('request_id', 'VARCHAR(64) NULL'),
        ('error_code', 'VARCHAR(80) NULL'),
    ],
    'enterprise_service_applications': [
        ('version', 'INT NOT NULL DEFAULT 1'),
    ],
}

SQLITE_COLUMNS = {
    'departments': [
        ('sort_order', 'INTEGER DEFAULT 0'),
        ('short_name', 'TEXT'),
    ],
    'users': [
        ('phone', 'TEXT'),
        ('status', "TEXT DEFAULT 'active'"),
        ('department_id', 'TEXT'),
        ('employee_id', 'TEXT'),
        ('must_change_password', 'BOOLEAN DEFAULT 0'),
        ('last_login_at', 'DATETIME'),
        ('updated_at', 'DATETIME'),
    ],
    'jobs': [
        ('category', 'TEXT'),
        ('department', 'TEXT'),
        ('department_id', 'TEXT'),
        ('level', 'TEXT'),
        ('responsibilities', 'TEXT'),
        ('requirements', 'TEXT'),
        ('headcount', 'INTEGER DEFAULT 1'),
        ('location', 'TEXT'),
        ('employment_type', "TEXT DEFAULT '全职'"),
        ('salary_range', 'TEXT'),
        ('created_by', 'TEXT'),
        ('updated_by', 'TEXT'),
    ],
    'resumes': [
        ('updated_at', 'DATETIME'),
        ('created_by', 'TEXT'),
        ('updated_by', 'TEXT'),
    ],
    'interviews': [
        ('updated_at', 'DATETIME'),
        ('created_by', 'TEXT'),
        ('updated_by', 'TEXT'),
        ('feedback_text', 'TEXT'),
        ('feedback_file_url', 'TEXT'),
        ('feedback_file_type', 'TEXT'),
        ('ai_analysis', 'JSON'),
        ('analyzed_at', 'DATETIME'),
        ('room_id', 'TEXT'),
        ('invite_token', 'TEXT'),
        ('recording_url', 'TEXT'),
        ('transcript', 'TEXT'),
        ('meeting_started_at', 'DATETIME'),
        ('meeting_ended_at', 'DATETIME'),
    ],
    'feedbacks': [
        ('reopen_reason', 'TEXT'),
        ('reopen_current_state', 'TEXT'),
        ('reopen_history', 'JSON'),
    ],
    'notifications': [
        ('employee_id', 'TEXT'),
    ],
    'employees': [
        ('oa_person_id', 'TEXT'),
        ('oa_login_id', 'TEXT'),
        ('oa_workcode', 'TEXT'),
        ('oa_status', 'TEXT'),
        ('oa_department_id', 'TEXT'),
        ('oa_jobtitle_id', 'TEXT'),
        ('erp_user_id', 'TEXT'),
        ('source_system', 'TEXT'),
        ('source_modified_at', 'DATETIME'),
        ('source_synced_at', 'DATETIME'),
        ('employee_type', 'TEXT'),
        ('performance_participant', 'BOOLEAN DEFAULT 0'),
    ],
    'attendance_timesheets': [
        ('overtime_hours', 'NUMERIC DEFAULT 0'),
        ('leave_hours', 'NUMERIC DEFAULT 0'),
        ('event_summary', 'TEXT'),
        ('check_in_fixed', 'BOOLEAN DEFAULT 0'),
        ('check_out_fixed', 'BOOLEAN DEFAULT 0'),
        ('punch_times', 'JSON'),
    ],
    'performance_cycles': [
        ('tenant_id', 'TEXT'),
    ],
    'performance_templates': [
        ('tenant_id', 'TEXT'),
    ],
    'performance_reviews': [
        ('tenant_id', 'TEXT'),
        ('manager_id', 'TEXT'),
        ('auto_score', 'NUMERIC'),
        ('self_submitted_at', 'DATETIME'),
        ('manager_scored_at', 'DATETIME'),
        ('confirmed_at', 'DATETIME'),
    ],
    'performance_score_rules': [
        ('target_source', "TEXT DEFAULT 'fixed'"),
        ('target_oa_department_id', 'TEXT'),
        ('scoring_mode', "TEXT DEFAULT 'threshold'"),
    ],
    'exam_papers': [
        ('question_mode', "TEXT DEFAULT 'fixed'"),
        ('draw_count', 'INTEGER'),
    ],
    'assistant_audit_logs': [
        ('resource_domain', 'TEXT'),
        ('ai_tier', 'TEXT'),
        ('tenant_ai_entitlement_id', 'TEXT'),
    ],
    'platform_operation_logs': [
        ('result', "TEXT NOT NULL DEFAULT 'success'"),
        ('request_id', 'TEXT'),
        ('error_code', 'TEXT'),
    ],
    'enterprise_service_applications': [
        ('version', 'INTEGER NOT NULL DEFAULT 1'),
    ],
}

TENANT_SCOPED_TABLES = sorted({
    model.__tablename__
    for model in TENANT_SCOPED_MODELS
    if getattr(model, '__tablename__', None)
})

for _table_name in TENANT_SCOPED_TABLES:
    MYSQL_COLUMNS.setdefault(_table_name, [])
    SQLITE_COLUMNS.setdefault(_table_name, [])
    if not any(column == 'tenant_id' for column, _sql in MYSQL_COLUMNS[_table_name]):
        MYSQL_COLUMNS[_table_name].insert(0, ('tenant_id', 'VARCHAR(36) NULL'))
    if not any(column == 'tenant_id' for column, _sql in SQLITE_COLUMNS[_table_name]):
        SQLITE_COLUMNS[_table_name].insert(0, ('tenant_id', 'TEXT'))

MYSQL_CREATE_TABLES = [
    """
    CREATE TABLE IF NOT EXISTS tenant_settings (
        id VARCHAR(36) NOT NULL PRIMARY KEY,
        tenant_id VARCHAR(36) NOT NULL,
        `key` VARCHAR(100) NOT NULL,
        value JSON NOT NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uq_tenant_settings_tenant_key (tenant_id, `key`),
        INDEX idx_tenant_settings_tenant (tenant_id),
        INDEX idx_tenant_settings_key (`key`)
    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS assistant_audit_logs (
        id VARCHAR(36) NOT NULL PRIMARY KEY,
        user_id VARCHAR(36) NULL,
        session_id VARCHAR(36) NULL,
        request_id VARCHAR(64) NULL,
        provider VARCHAR(40) NULL DEFAULT 'siliconflow',
        model VARCHAR(120) NULL,
        model_profile VARCHAR(40) NULL,
        use_case VARCHAR(80) NULL,
        prompt_messages JSON NULL,
        response_content TEXT NULL,
        response_meta JSON NULL,
        status VARCHAR(30) NULL DEFAULT 'success',
        error_message TEXT NULL,
        latency_ms INT NULL,
        prompt_chars INT NULL DEFAULT 0,
        response_chars INT NULL DEFAULT 0,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_assistant_audit_user (user_id),
        INDEX idx_assistant_audit_session (session_id),
        INDEX idx_assistant_audit_request (request_id),
        INDEX idx_assistant_audit_model (model),
        INDEX idx_assistant_audit_use_case (use_case),
        INDEX idx_assistant_audit_created (created_at)
    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS assistant_evaluation_runs (
        id VARCHAR(36) NOT NULL PRIMARY KEY,
        case_id VARCHAR(120) NOT NULL,
        question TEXT NOT NULL,
        scenario VARCHAR(120) NULL,
        user_id VARCHAR(36) NULL,
        session_id VARCHAR(36) NULL,
        request_id VARCHAR(64) NULL,
        expected_modules JSON NULL,
        expected_render_blocks JSON NULL,
        expected_behavior JSON NULL,
        actual_modules JSON NULL,
        actual_render_blocks JSON NULL,
        used_tools JSON NULL,
        evidence_sources JSON NULL,
        entity_summary JSON NULL,
        time_range JSON NULL,
        response_summary TEXT NULL,
        pass_status VARCHAR(30) NULL DEFAULT 'needs_review',
        failure_categories JSON NULL,
        missing_expectations JSON NULL,
        root_cause_notes TEXT NULL,
        fix_plan TEXT NULL,
        retest_of VARCHAR(64) NULL,
        response_payload JSON NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_assistant_eval_case (case_id),
        INDEX idx_assistant_eval_user (user_id),
        INDEX idx_assistant_eval_session (session_id),
        INDEX idx_assistant_eval_request (request_id),
        INDEX idx_assistant_eval_status (pass_status),
        INDEX idx_assistant_eval_created (created_at)
    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS tenant_orders (
        id VARCHAR(36) NOT NULL PRIMARY KEY,
        tenant_id VARCHAR(36) NOT NULL,
        subscription_id VARCHAR(36) NULL,
        order_no VARCHAR(80) NOT NULL UNIQUE,
        amount_cents INT NOT NULL DEFAULT 0,
        currency VARCHAR(12) NOT NULL DEFAULT 'CNY',
        status VARCHAR(30) NOT NULL DEFAULT 'pending',
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        paid_at DATETIME NULL,
        INDEX idx_tenant_orders_tenant (tenant_id),
        INDEX idx_tenant_orders_subscription (subscription_id),
        INDEX idx_tenant_orders_order_no (order_no),
        INDEX idx_tenant_orders_status (status)
    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS tenant_invoices (
        id VARCHAR(36) NOT NULL PRIMARY KEY,
        tenant_id VARCHAR(36) NOT NULL,
        order_id VARCHAR(36) NULL,
        title VARCHAR(160) NOT NULL,
        tax_no VARCHAR(80) NULL,
        amount_cents INT NOT NULL DEFAULT 0,
        status VARCHAR(30) NOT NULL DEFAULT 'requested',
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        issued_at DATETIME NULL,
        INDEX idx_tenant_invoices_tenant (tenant_id),
        INDEX idx_tenant_invoices_order (order_id),
        INDEX idx_tenant_invoices_status (status)
    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS tenant_billing_events (
        id VARCHAR(36) NOT NULL PRIMARY KEY,
        tenant_id VARCHAR(36) NOT NULL,
        event_type VARCHAR(80) NOT NULL,
        note TEXT NULL,
        operator_id VARCHAR(36) NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_tenant_billing_events_tenant (tenant_id),
        INDEX idx_tenant_billing_events_type (event_type),
        INDEX idx_tenant_billing_events_operator (operator_id),
        INDEX idx_tenant_billing_events_created (created_at)
    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS tenant_onboarding_items (
        id VARCHAR(36) NOT NULL PRIMARY KEY,
        tenant_id VARCHAR(36) NOT NULL,
        item_key VARCHAR(80) NOT NULL,
        label VARCHAR(160) NULL,
        status VARCHAR(30) NOT NULL DEFAULT 'pending',
        note TEXT NULL,
        completed_at DATETIME NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uq_tenant_onboarding_item (tenant_id, item_key),
        INDEX idx_tenant_onboarding_tenant (tenant_id),
        INDEX idx_tenant_onboarding_status (status)
    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS headcount_plans (
        id VARCHAR(36) NOT NULL PRIMARY KEY,
        tenant_id VARCHAR(36) NOT NULL,
        department_id VARCHAR(36) NOT NULL,
        plan_count INT NOT NULL DEFAULT 0,
        note TEXT NULL,
        created_by VARCHAR(36) NULL,
        updated_by VARCHAR(36) NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uq_headcount_plan_department (tenant_id, department_id),
        INDEX idx_headcount_plans_tenant (tenant_id),
        INDEX idx_headcount_plans_department (department_id)
    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS headcount_changes (
        id VARCHAR(36) NOT NULL PRIMARY KEY,
        tenant_id VARCHAR(36) NOT NULL,
        department_id VARCHAR(36) NOT NULL,
        old_plan INT NOT NULL DEFAULT 0,
        new_plan INT NOT NULL DEFAULT 0,
        actual_count INT NOT NULL DEFAULT 0,
        note TEXT NULL,
        operator_id VARCHAR(36) NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_headcount_changes_tenant (tenant_id),
        INDEX idx_headcount_changes_department (department_id),
        INDEX idx_headcount_changes_operator (operator_id),
        INDEX idx_headcount_changes_created (created_at)
    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS people_analytics_snapshots (
        id VARCHAR(36) NOT NULL PRIMARY KEY,
        tenant_id VARCHAR(36) NOT NULL,
        metric_key VARCHAR(80) NOT NULL,
        payload_json JSON NULL,
        generated_by VARCHAR(36) NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_people_analytics_snapshots_tenant (tenant_id),
        INDEX idx_people_analytics_snapshots_metric (metric_key),
        INDEX idx_people_analytics_snapshots_generated_by (generated_by),
        INDEX idx_people_analytics_snapshots_created (created_at)
    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS talent_strategy_snapshots (
        id VARCHAR(36) NOT NULL PRIMARY KEY,
        tenant_id VARCHAR(36) NOT NULL,
        feature_key VARCHAR(100) NOT NULL,
        payload_json JSON NULL,
        generated_by VARCHAR(36) NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_talent_strategy_snapshots_tenant (tenant_id),
        INDEX idx_talent_strategy_snapshots_feature (feature_key),
        INDEX idx_talent_strategy_snapshots_generated_by (generated_by),
        INDEX idx_talent_strategy_snapshots_created (created_at)
    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS analytics_insight_snapshots (
        id VARCHAR(36) NOT NULL PRIMARY KEY,
        tenant_id VARCHAR(36) NOT NULL,
        feature_key VARCHAR(100) NOT NULL,
        payload_json JSON NULL,
        generated_by VARCHAR(36) NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_analytics_insight_snapshots_tenant (tenant_id),
        INDEX idx_analytics_insight_snapshots_feature (feature_key),
        INDEX idx_analytics_insight_snapshots_generated_by (generated_by),
        INDEX idx_analytics_insight_snapshots_created (created_at)
    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS intelligence_insight_snapshots (
        id VARCHAR(36) NOT NULL PRIMARY KEY,
        tenant_id VARCHAR(36) NOT NULL,
        feature_key VARCHAR(100) NOT NULL,
        payload_json JSON NULL,
        generated_by VARCHAR(36) NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_intelligence_insight_snapshots_tenant (tenant_id),
        INDEX idx_intelligence_insight_snapshots_feature (feature_key),
        INDEX idx_intelligence_insight_snapshots_generated_by (generated_by),
        INDEX idx_intelligence_insight_snapshots_created (created_at)
    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS collaboration_insight_snapshots (
        id VARCHAR(36) NOT NULL PRIMARY KEY,
        tenant_id VARCHAR(36) NOT NULL,
        feature_key VARCHAR(100) NOT NULL,
        payload_json JSON NULL,
        generated_by VARCHAR(36) NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_collaboration_insight_snapshots_tenant (tenant_id),
        INDEX idx_collaboration_insight_snapshots_feature (feature_key),
        INDEX idx_collaboration_insight_snapshots_generated_by (generated_by),
        INDEX idx_collaboration_insight_snapshots_created (created_at)
    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS workforce_insight_snapshots (
        id VARCHAR(36) NOT NULL PRIMARY KEY,
        tenant_id VARCHAR(36) NOT NULL,
        feature_key VARCHAR(100) NOT NULL,
        payload_json JSON NULL,
        generated_by VARCHAR(36) NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_workforce_insight_snapshots_tenant (tenant_id),
        INDEX idx_workforce_insight_snapshots_feature (feature_key),
        INDEX idx_workforce_insight_snapshots_generated_by (generated_by),
        INDEX idx_workforce_insight_snapshots_created (created_at)
    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS integration_insight_snapshots (
        id VARCHAR(36) NOT NULL PRIMARY KEY,
        tenant_id VARCHAR(36) NOT NULL,
        feature_key VARCHAR(100) NOT NULL,
        payload_json JSON NULL,
        generated_by VARCHAR(36) NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_integration_insight_snapshots_tenant (tenant_id),
        INDEX idx_integration_insight_snapshots_feature (feature_key),
        INDEX idx_integration_insight_snapshots_generated_by (generated_by),
        INDEX idx_integration_insight_snapshots_created (created_at)
    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS mobile_insight_snapshots (
        id VARCHAR(36) NOT NULL PRIMARY KEY,
        tenant_id VARCHAR(36) NOT NULL,
        feature_key VARCHAR(100) NOT NULL,
        payload_json JSON NULL,
        generated_by VARCHAR(36) NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_mobile_insight_snapshots_tenant (tenant_id),
        INDEX idx_mobile_insight_snapshots_feature (feature_key),
        INDEX idx_mobile_insight_snapshots_generated_by (generated_by),
        INDEX idx_mobile_insight_snapshots_created (created_at)
    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS security_insight_snapshots (
        id VARCHAR(36) NOT NULL PRIMARY KEY,
        tenant_id VARCHAR(36) NOT NULL,
        feature_key VARCHAR(100) NOT NULL,
        payload_json JSON NULL,
        generated_by VARCHAR(36) NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_security_insight_snapshots_tenant (tenant_id),
        INDEX idx_security_insight_snapshots_feature (feature_key),
        INDEX idx_security_insight_snapshots_generated_by (generated_by),
        INDEX idx_security_insight_snapshots_created (created_at)
    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS continuous_feedback_entries (
        id VARCHAR(36) NOT NULL PRIMARY KEY,
        tenant_id VARCHAR(36) NOT NULL,
        employee_id VARCHAR(36) NOT NULL,
        giver_id VARCHAR(36) NULL,
        feedback_type VARCHAR(40) NOT NULL DEFAULT 'coaching',
        category VARCHAR(80) NULL,
        content TEXT NOT NULL,
        visibility VARCHAR(40) NOT NULL DEFAULT 'manager',
        sentiment VARCHAR(30) NOT NULL DEFAULT 'neutral',
        status VARCHAR(40) NOT NULL DEFAULT 'open',
        handler_id VARCHAR(36) NULL,
        handler_reply TEXT NULL,
        handled_at DATETIME NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        INDEX idx_continuous_feedback_tenant (tenant_id),
        INDEX idx_continuous_feedback_employee (employee_id),
        INDEX idx_continuous_feedback_giver (giver_id),
        INDEX idx_continuous_feedback_type (feedback_type),
        INDEX idx_continuous_feedback_visibility (visibility),
        INDEX idx_continuous_feedback_sentiment (sentiment),
        INDEX idx_continuous_feedback_status (status),
        INDEX idx_continuous_feedback_handler (handler_id),
        INDEX idx_continuous_feedback_created (created_at)
    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS tenant_data_requests (
        id VARCHAR(36) NOT NULL PRIMARY KEY,
        tenant_id VARCHAR(36) NOT NULL,
        request_type VARCHAR(30) NOT NULL,
        requester VARCHAR(160) NOT NULL,
        scope VARCHAR(240) NOT NULL,
        status VARCHAR(40) NOT NULL DEFAULT 'requested',
        evidence_ref VARCHAR(240) NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        INDEX idx_tenant_data_requests_tenant (tenant_id),
        INDEX idx_tenant_data_requests_type (request_type),
        INDEX idx_tenant_data_requests_status (status)
    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS ai_provider_configs (
        id VARCHAR(36) NOT NULL PRIMARY KEY,
        provider_code VARCHAR(60) NOT NULL UNIQUE,
        display_name VARCHAR(120) NOT NULL,
        base_url VARCHAR(240) NOT NULL,
        api_key_encrypted TEXT NULL,
        api_key_mask VARCHAR(80) NULL,
        enabled TINYINT(1) NOT NULL DEFAULT 1,
        settings_json JSON NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        INDEX idx_ai_provider_code (provider_code)
    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS extension_provider_configs (
        id VARCHAR(36) NOT NULL PRIMARY KEY,
        provider_type VARCHAR(80) NOT NULL,
        provider_code VARCHAR(80) NOT NULL UNIQUE,
        status VARCHAR(30) NOT NULL DEFAULT 'unconfigured',
        config_json JSON NULL,
        secret_ref VARCHAR(160) NULL,
        last_tested_at DATETIME NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        INDEX idx_extension_provider_type (provider_type),
        INDEX idx_extension_provider_code (provider_code),
        INDEX idx_extension_provider_status (status)
    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS ai_model_policies (
        id VARCHAR(36) NOT NULL PRIMARY KEY,
        resource_domain VARCHAR(80) NOT NULL,
        use_case VARCHAR(120) NOT NULL DEFAULT '*',
        tier VARCHAR(40) NOT NULL DEFAULT 'standard',
        provider_code VARCHAR(60) NOT NULL,
        model_profile VARCHAR(40) NULL DEFAULT 'standard',
        model VARCHAR(160) NOT NULL,
        max_tokens INT NULL DEFAULT 1200,
        enabled TINYINT(1) NOT NULL DEFAULT 1,
        settings_json JSON NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uq_ai_model_policy_scope (resource_domain, use_case, tier),
        INDEX idx_ai_model_policy_provider (provider_code)
    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS ai_addon_packages (
        code VARCHAR(80) NOT NULL PRIMARY KEY,
        name VARCHAR(120) NOT NULL,
        resource_domains JSON NULL,
        allowed_tiers JSON NULL,
        default_monthly_quota INT NULL DEFAULT 0,
        is_active TINYINT(1) NOT NULL DEFAULT 1,
        settings_json JSON NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS tenant_ai_entitlements (
        id VARCHAR(36) NOT NULL PRIMARY KEY,
        tenant_id VARCHAR(36) NOT NULL,
        resource_domain VARCHAR(80) NOT NULL,
        package_code VARCHAR(80) NULL,
        enabled TINYINT(1) NOT NULL DEFAULT 0,
        selected_tier VARCHAR(40) NULL DEFAULT 'standard',
        allowed_tiers JSON NULL,
        monthly_quota INT NULL DEFAULT 0,
        period_key VARCHAR(20) NULL,
        settings_json JSON NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uq_tenant_ai_entitlement_domain (tenant_id, resource_domain),
        INDEX idx_tenant_ai_entitlements_tenant (tenant_id),
        INDEX idx_tenant_ai_entitlements_domain (resource_domain),
        INDEX idx_tenant_ai_entitlements_package (package_code)
    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS ai_usage_ledger (
        id VARCHAR(36) NOT NULL PRIMARY KEY,
        tenant_id VARCHAR(36) NULL,
        user_id VARCHAR(36) NULL,
        resource_domain VARCHAR(80) NOT NULL,
        use_case VARCHAR(120) NULL,
        tier VARCHAR(40) NULL,
        provider VARCHAR(60) NULL,
        model VARCHAR(160) NULL,
        status VARCHAR(30) NOT NULL DEFAULT 'success',
        units INT NOT NULL DEFAULT 1,
        request_id VARCHAR(64) NULL,
        assistant_audit_log_id VARCHAR(36) NULL,
        prompt_chars INT NULL DEFAULT 0,
        response_chars INT NULL DEFAULT 0,
        error_code VARCHAR(80) NULL,
        meta_json JSON NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_ai_usage_tenant (tenant_id),
        INDEX idx_ai_usage_user (user_id),
        INDEX idx_ai_usage_domain (resource_domain),
        INDEX idx_ai_usage_created (created_at),
        INDEX idx_ai_usage_request (request_id)
    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS enterprise_service_applications (
        id VARCHAR(36) NOT NULL PRIMARY KEY,
        application_no VARCHAR(32) NOT NULL,
        company_name VARCHAR(120) NOT NULL,
        contact_name VARCHAR(60) NOT NULL,
        mobile VARCHAR(30) NOT NULL,
        email VARCHAR(255) NULL,
        region VARCHAR(80) NULL,
        employee_scale VARCHAR(30) NULL,
        interested_modules JSON NULL,
        preferred_contact_time VARCHAR(40) NULL,
        notes TEXT NULL,
        status VARCHAR(20) NOT NULL DEFAULT 'new',
        owner_user_id VARCHAR(36) NULL,
        follow_up_at DATETIME NULL,
        internal_note TEXT NULL,
        converted_tenant_id VARCHAR(36) NULL,
        source VARCHAR(40) NOT NULL DEFAULT 'enterprise_application',
        request_id VARCHAR(64) NULL,
        idempotency_key VARCHAR(128) NOT NULL,
        fingerprint_hash VARCHAR(64) NOT NULL,
        consent_at DATETIME NOT NULL,
        version INT NOT NULL DEFAULT 1,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uq_enterprise_application_no (application_no),
        UNIQUE KEY uq_enterprise_application_idempotency (idempotency_key),
        INDEX idx_enterprise_application_status_created (status, created_at),
        INDEX idx_enterprise_application_mobile_created (mobile, created_at),
        INDEX idx_enterprise_application_owner_status (owner_user_id, status),
        INDEX idx_enterprise_application_converted_tenant (converted_tenant_id),
        INDEX idx_enterprise_application_request (request_id),
        INDEX idx_enterprise_application_fingerprint (fingerprint_hash),
        CONSTRAINT fk_enterprise_application_owner
            FOREIGN KEY (owner_user_id) REFERENCES users(id) ON DELETE SET NULL,
        CONSTRAINT fk_enterprise_application_converted_tenant
            FOREIGN KEY (converted_tenant_id) REFERENCES tenants(id) ON DELETE SET NULL
    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS enterprise_application_submission_guards (
        fingerprint_hash VARCHAR(64) NOT NULL PRIMARY KEY,
        active_idempotency_hash VARCHAR(128) NOT NULL,
        application_id VARCHAR(36) NULL,
        claim_token VARCHAR(36) NOT NULL,
        cooldown_until DATETIME NOT NULL,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        INDEX idx_enterprise_guard_application (application_id),
        INDEX idx_enterprise_guard_cooldown (cooldown_until),
        CONSTRAINT fk_enterprise_guard_application
            FOREIGN KEY (application_id) REFERENCES enterprise_service_applications(id) ON DELETE SET NULL
    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS attendance_sync_runs (
        id VARCHAR(36) NOT NULL PRIMARY KEY,
        tenant_id VARCHAR(36) NOT NULL,
        run_trigger VARCHAR(30) NOT NULL DEFAULT 'scheduled',
        status VARCHAR(20) NOT NULL DEFAULT 'running',
        people_read INT NOT NULL DEFAULT 0,
        people_upserted INT NOT NULL DEFAULT 0,
        punches_read INT NOT NULL DEFAULT 0,
        punches_inserted INT NOT NULL DEFAULT 0,
        punches_duplicate INT NOT NULL DEFAULT 0,
        unmatched_count INT NOT NULL DEFAULT 0,
        suggested_count INT NOT NULL DEFAULT 0,
        lookback_days INT NOT NULL DEFAULT 90,
        cursor_check_time DATETIME NULL,
        error_code VARCHAR(80) NULL,
        error_message VARCHAR(500) NULL,
        started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        completed_at DATETIME NULL,
        INDEX idx_attendance_sync_runs_tenant (tenant_id),
        INDEX idx_attendance_sync_runs_status (status),
        INDEX idx_attendance_sync_runs_started (started_at)
    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS attendance_external_people (
        id VARCHAR(36) NOT NULL PRIMARY KEY,
        tenant_id VARCHAR(36) NOT NULL,
        external_user_id VARCHAR(40) NOT NULL,
        badge_number VARCHAR(80) NULL,
        name VARCHAR(100) NOT NULL DEFAULT '',
        department_name VARCHAR(120) NULL,
        employee_id VARCHAR(36) NULL,
        suggested_employee_id VARCHAR(36) NULL,
        mapping_status VARCHAR(20) NOT NULL DEFAULT 'unmatched',
        mapped_at DATETIME NULL,
        mapped_by VARCHAR(36) NULL,
        source_synced_at DATETIME NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uq_attendance_external_user (tenant_id, external_user_id),
        INDEX idx_attendance_external_people_tenant (tenant_id),
        INDEX idx_attendance_external_people_status (mapping_status),
        INDEX idx_attendance_external_people_employee (employee_id)
    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS attendance_raw_punches (
        id VARCHAR(36) NOT NULL PRIMARY KEY,
        tenant_id VARCHAR(36) NOT NULL,
        external_user_id VARCHAR(40) NOT NULL,
        check_time DATETIME NOT NULL,
        check_type VARCHAR(20) NULL,
        verify_code VARCHAR(20) NULL,
        sensor_id VARCHAR(80) NULL,
        serial_number VARCHAR(80) NULL,
        fingerprint VARCHAR(64) NOT NULL,
        sync_run_id VARCHAR(36) NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE KEY uq_attendance_raw_punch (tenant_id, external_user_id, check_time),
        INDEX idx_attendance_raw_punches_tenant (tenant_id),
        INDEX idx_attendance_raw_punches_user_time (external_user_id, check_time),
        INDEX idx_attendance_raw_punches_fingerprint (fingerprint)
    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS attendance_shifts (
        id VARCHAR(36) NOT NULL PRIMARY KEY,
        tenant_id VARCHAR(36) NOT NULL,
        code VARCHAR(40) NOT NULL,
        name VARCHAR(120) NOT NULL,
        shift_type VARCHAR(20) NOT NULL DEFAULT 'fixed',
        start_time VARCHAR(5) NULL,
        end_time VARCHAR(5) NULL,
        cross_day TINYINT(1) NOT NULL DEFAULT 0,
        required_work_hours DECIMAL(6,2) NULL,
        late_grace_minutes INT NOT NULL DEFAULT 0,
        early_leave_grace_minutes INT NOT NULL DEFAULT 0,
        require_check_in TINYINT(1) NOT NULL DEFAULT 1,
        require_check_out TINYINT(1) NOT NULL DEFAULT 1,
        status VARCHAR(20) NOT NULL DEFAULT 'active',
        remark TEXT NULL,
        created_by VARCHAR(36) NULL,
        updated_by VARCHAR(36) NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uq_attendance_shift_code (tenant_id, code),
        INDEX idx_attendance_shifts_tenant (tenant_id),
        INDEX idx_attendance_shifts_status (status)
    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS attendance_rule_sets (
        id VARCHAR(36) NOT NULL PRIMARY KEY,
        tenant_id VARCHAR(36) NOT NULL,
        code VARCHAR(40) NOT NULL,
        name VARCHAR(120) NOT NULL,
        version INT NOT NULL DEFAULT 1,
        effective_from DATE NULL,
        effective_to DATE NULL,
        status VARCHAR(20) NOT NULL DEFAULT 'active',
        absent_if_no_punch TINYINT(1) NOT NULL DEFAULT 1,
        missing_card_if_single_punch TINYINT(1) NOT NULL DEFAULT 1,
        late_grace_minutes INT NULL,
        early_leave_grace_minutes INT NULL,
        remark TEXT NULL,
        created_by VARCHAR(36) NULL,
        updated_by VARCHAR(36) NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uq_attendance_rule_set_code (tenant_id, code),
        INDEX idx_attendance_rule_sets_tenant (tenant_id),
        INDEX idx_attendance_rule_sets_status (status),
        INDEX idx_attendance_rule_sets_effective (effective_from, effective_to)
    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS attendance_groups (
        id VARCHAR(36) NOT NULL PRIMARY KEY,
        tenant_id VARCHAR(36) NOT NULL,
        code VARCHAR(40) NOT NULL,
        name VARCHAR(120) NOT NULL,
        default_shift_id VARCHAR(36) NULL,
        default_rule_set_id VARCHAR(36) NULL,
        priority INT NOT NULL DEFAULT 0,
        effective_from DATE NULL,
        effective_to DATE NULL,
        status VARCHAR(20) NOT NULL DEFAULT 'active',
        remark TEXT NULL,
        created_by VARCHAR(36) NULL,
        updated_by VARCHAR(36) NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uq_attendance_group_code (tenant_id, code),
        INDEX idx_attendance_groups_tenant (tenant_id),
        INDEX idx_attendance_groups_status (status),
        INDEX idx_attendance_groups_shift (default_shift_id),
        INDEX idx_attendance_groups_rule (default_rule_set_id)
    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS attendance_group_memberships (
        id VARCHAR(36) NOT NULL PRIMARY KEY,
        tenant_id VARCHAR(36) NOT NULL,
        group_id VARCHAR(36) NOT NULL,
        employee_id VARCHAR(36) NULL,
        department_id VARCHAR(36) NULL,
        position_id VARCHAR(36) NULL,
        priority INT NOT NULL DEFAULT 0,
        effective_from DATE NULL,
        effective_to DATE NULL,
        status VARCHAR(20) NOT NULL DEFAULT 'active',
        remark TEXT NULL,
        created_by VARCHAR(36) NULL,
        updated_by VARCHAR(36) NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        INDEX idx_attendance_memberships_tenant (tenant_id),
        INDEX idx_attendance_memberships_group (group_id),
        INDEX idx_attendance_memberships_employee (employee_id),
        INDEX idx_attendance_memberships_department (department_id),
        INDEX idx_attendance_memberships_position (position_id),
        INDEX idx_attendance_memberships_effective (effective_from, effective_to)
    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS attendance_holidays (
        id VARCHAR(36) NOT NULL PRIMARY KEY,
        tenant_id VARCHAR(36) NOT NULL,
        holiday_date DATE NOT NULL,
        name VARCHAR(120) NOT NULL,
        day_type VARCHAR(20) NOT NULL DEFAULT 'holiday',
        status VARCHAR(20) NOT NULL DEFAULT 'active',
        remark TEXT NULL,
        created_by VARCHAR(36) NULL,
        updated_by VARCHAR(36) NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uq_attendance_holiday_date (tenant_id, holiday_date),
        INDEX idx_attendance_holidays_tenant (tenant_id),
        INDEX idx_attendance_holidays_date (holiday_date),
        INDEX idx_attendance_holidays_status (status)
    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS attendance_schedules (
        id VARCHAR(36) NOT NULL PRIMARY KEY,
        tenant_id VARCHAR(36) NOT NULL,
        employee_id VARCHAR(36) NOT NULL,
        schedule_date DATE NOT NULL,
        day_type VARCHAR(20) NOT NULL DEFAULT 'workday',
        shift_id VARCHAR(36) NULL,
        source VARCHAR(30) NOT NULL DEFAULT 'manual',
        remark TEXT NULL,
        created_by VARCHAR(36) NULL,
        updated_by VARCHAR(36) NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uq_attendance_schedule_employee_date (tenant_id, employee_id, schedule_date),
        INDEX idx_attendance_schedules_tenant (tenant_id),
        INDEX idx_attendance_schedules_employee_date (employee_id, schedule_date),
        INDEX idx_attendance_schedules_shift (shift_id)
    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS attendance_daily_results (
        id VARCHAR(36) NOT NULL PRIMARY KEY,
        tenant_id VARCHAR(36) NOT NULL,
        employee_id VARCHAR(36) NOT NULL,
        attendance_date DATE NOT NULL,
        day_type VARCHAR(20) NOT NULL DEFAULT 'workday',
        schedule_source VARCHAR(30) NULL,
        group_id VARCHAR(36) NULL,
        shift_id VARCHAR(36) NULL,
        rule_set_id VARCHAR(36) NULL,
        scheduled_start DATETIME NULL,
        scheduled_end DATETIME NULL,
        check_in_time DATETIME NULL,
        check_out_time DATETIME NULL,
        work_hours DECIMAL(6,2) NULL,
        status VARCHAR(30) NOT NULL DEFAULT 'unscheduled',
        late_minutes INT NOT NULL DEFAULT 0,
        early_leave_minutes INT NOT NULL DEFAULT 0,
        source_punch_ids JSON NULL,
        request_ids JSON NULL,
        explanation_json JSON NULL,
        calculation_version VARCHAR(20) NOT NULL DEFAULT 'v1',
        calculated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uq_attendance_daily_employee_date (tenant_id, employee_id, attendance_date),
        INDEX idx_attendance_daily_tenant (tenant_id),
        INDEX idx_attendance_daily_employee_date (employee_id, attendance_date),
        INDEX idx_attendance_daily_status (status),
        INDEX idx_attendance_daily_group (group_id),
        INDEX idx_attendance_daily_shift (shift_id)
    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """,
]

SQLITE_CREATE_TABLES = [
    """
    CREATE TABLE IF NOT EXISTS tenant_settings (
        id TEXT NOT NULL PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        key TEXT NOT NULL,
        value JSON NOT NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME,
        CONSTRAINT uq_tenant_settings_tenant_key UNIQUE (tenant_id, key)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS assistant_audit_logs (
        id TEXT NOT NULL PRIMARY KEY,
        user_id TEXT,
        session_id TEXT,
        request_id TEXT,
        provider TEXT DEFAULT 'siliconflow',
        model TEXT,
        model_profile TEXT,
        use_case TEXT,
        prompt_messages JSON,
        response_content TEXT,
        response_meta JSON,
        status TEXT DEFAULT 'success',
        error_message TEXT,
        latency_ms INTEGER,
        prompt_chars INTEGER DEFAULT 0,
        response_chars INTEGER DEFAULT 0,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS assistant_evaluation_runs (
        id TEXT NOT NULL PRIMARY KEY,
        case_id TEXT NOT NULL,
        question TEXT NOT NULL,
        scenario TEXT,
        user_id TEXT,
        session_id TEXT,
        request_id TEXT,
        expected_modules JSON,
        expected_render_blocks JSON,
        expected_behavior JSON,
        actual_modules JSON,
        actual_render_blocks JSON,
        used_tools JSON,
        evidence_sources JSON,
        entity_summary JSON,
        time_range JSON,
        response_summary TEXT,
        pass_status TEXT DEFAULT 'needs_review',
        failure_categories JSON,
        missing_expectations JSON,
        root_cause_notes TEXT,
        fix_plan TEXT,
        retest_of TEXT,
        response_payload JSON,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tenant_orders (
        id TEXT NOT NULL PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        subscription_id TEXT,
        order_no TEXT NOT NULL UNIQUE,
        amount_cents INTEGER NOT NULL DEFAULT 0,
        currency TEXT NOT NULL DEFAULT 'CNY',
        status TEXT NOT NULL DEFAULT 'pending',
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        paid_at DATETIME
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tenant_invoices (
        id TEXT NOT NULL PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        order_id TEXT,
        title TEXT NOT NULL,
        tax_no TEXT,
        amount_cents INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'requested',
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        issued_at DATETIME
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tenant_billing_events (
        id TEXT NOT NULL PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        event_type TEXT NOT NULL,
        note TEXT,
        operator_id TEXT,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tenant_onboarding_items (
        id TEXT NOT NULL PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        item_key TEXT NOT NULL,
        label TEXT,
        status TEXT NOT NULL DEFAULT 'pending',
        note TEXT,
        completed_at DATETIME,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME,
        UNIQUE (tenant_id, item_key)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS headcount_plans (
        id TEXT NOT NULL PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        department_id TEXT NOT NULL,
        plan_count INTEGER NOT NULL DEFAULT 0,
        note TEXT,
        created_by TEXT,
        updated_by TEXT,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME,
        UNIQUE (tenant_id, department_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS headcount_changes (
        id TEXT NOT NULL PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        department_id TEXT NOT NULL,
        old_plan INTEGER NOT NULL DEFAULT 0,
        new_plan INTEGER NOT NULL DEFAULT 0,
        actual_count INTEGER NOT NULL DEFAULT 0,
        note TEXT,
        operator_id TEXT,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS people_analytics_snapshots (
        id TEXT NOT NULL PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        metric_key TEXT NOT NULL,
        payload_json JSON,
        generated_by TEXT,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS talent_strategy_snapshots (
        id TEXT NOT NULL PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        feature_key TEXT NOT NULL,
        payload_json JSON,
        generated_by TEXT,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS analytics_insight_snapshots (
        id TEXT NOT NULL PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        feature_key TEXT NOT NULL,
        payload_json JSON,
        generated_by TEXT,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS intelligence_insight_snapshots (
        id TEXT NOT NULL PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        feature_key TEXT NOT NULL,
        payload_json JSON,
        generated_by TEXT,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS collaboration_insight_snapshots (
        id TEXT NOT NULL PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        feature_key TEXT NOT NULL,
        payload_json JSON,
        generated_by TEXT,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS workforce_insight_snapshots (
        id TEXT NOT NULL PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        feature_key TEXT NOT NULL,
        payload_json JSON,
        generated_by TEXT,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS integration_insight_snapshots (
        id TEXT NOT NULL PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        feature_key TEXT NOT NULL,
        payload_json JSON,
        generated_by TEXT,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS mobile_insight_snapshots (
        id TEXT NOT NULL PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        feature_key TEXT NOT NULL,
        payload_json JSON,
        generated_by TEXT,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS security_insight_snapshots (
        id TEXT NOT NULL PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        feature_key TEXT NOT NULL,
        payload_json JSON,
        generated_by TEXT,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS continuous_feedback_entries (
        id TEXT NOT NULL PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        employee_id TEXT NOT NULL,
        giver_id TEXT,
        feedback_type TEXT NOT NULL DEFAULT 'coaching',
        category TEXT,
        content TEXT NOT NULL,
        visibility TEXT NOT NULL DEFAULT 'manager',
        sentiment TEXT NOT NULL DEFAULT 'neutral',
        status TEXT NOT NULL DEFAULT 'open',
        handler_id TEXT,
        handler_reply TEXT,
        handled_at DATETIME,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tenant_data_requests (
        id TEXT NOT NULL PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        request_type TEXT NOT NULL,
        requester TEXT NOT NULL,
        scope TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'requested',
        evidence_ref TEXT,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ai_provider_configs (
        id TEXT NOT NULL PRIMARY KEY,
        provider_code TEXT NOT NULL UNIQUE,
        display_name TEXT NOT NULL,
        base_url TEXT NOT NULL,
        api_key_encrypted TEXT,
        api_key_mask TEXT,
        enabled BOOLEAN NOT NULL DEFAULT 1,
        settings_json JSON,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS extension_provider_configs (
        id TEXT NOT NULL PRIMARY KEY,
        provider_type TEXT NOT NULL,
        provider_code TEXT NOT NULL UNIQUE,
        status TEXT NOT NULL DEFAULT 'unconfigured',
        config_json JSON,
        secret_ref TEXT,
        last_tested_at DATETIME,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ai_model_policies (
        id TEXT NOT NULL PRIMARY KEY,
        resource_domain TEXT NOT NULL,
        use_case TEXT NOT NULL DEFAULT '*',
        tier TEXT NOT NULL DEFAULT 'standard',
        provider_code TEXT NOT NULL,
        model_profile TEXT DEFAULT 'standard',
        model TEXT NOT NULL,
        max_tokens INTEGER DEFAULT 1200,
        enabled BOOLEAN NOT NULL DEFAULT 1,
        settings_json JSON,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME,
        UNIQUE (resource_domain, use_case, tier)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ai_addon_packages (
        code TEXT NOT NULL PRIMARY KEY,
        name TEXT NOT NULL,
        resource_domains JSON,
        allowed_tiers JSON,
        default_monthly_quota INTEGER DEFAULT 0,
        is_active BOOLEAN NOT NULL DEFAULT 1,
        settings_json JSON,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tenant_ai_entitlements (
        id TEXT NOT NULL PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        resource_domain TEXT NOT NULL,
        package_code TEXT,
        enabled BOOLEAN NOT NULL DEFAULT 0,
        selected_tier TEXT DEFAULT 'standard',
        allowed_tiers JSON,
        monthly_quota INTEGER DEFAULT 0,
        period_key TEXT,
        settings_json JSON,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME,
        UNIQUE (tenant_id, resource_domain)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ai_usage_ledger (
        id TEXT NOT NULL PRIMARY KEY,
        tenant_id TEXT,
        user_id TEXT,
        resource_domain TEXT NOT NULL,
        use_case TEXT,
        tier TEXT,
        provider TEXT,
        model TEXT,
        status TEXT NOT NULL DEFAULT 'success',
        units INTEGER NOT NULL DEFAULT 1,
        request_id TEXT,
        assistant_audit_log_id TEXT,
        prompt_chars INTEGER DEFAULT 0,
        response_chars INTEGER DEFAULT 0,
        error_code TEXT,
        meta_json JSON,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS enterprise_service_applications (
        id TEXT NOT NULL PRIMARY KEY,
        application_no TEXT NOT NULL,
        company_name TEXT NOT NULL,
        contact_name TEXT NOT NULL,
        mobile TEXT NOT NULL,
        email TEXT,
        region TEXT,
        employee_scale TEXT,
        interested_modules JSON,
        preferred_contact_time TEXT,
        notes TEXT,
        status TEXT NOT NULL DEFAULT 'new',
        owner_user_id TEXT,
        follow_up_at DATETIME,
        internal_note TEXT,
        converted_tenant_id TEXT,
        source TEXT NOT NULL DEFAULT 'enterprise_application',
        request_id TEXT,
        idempotency_key TEXT NOT NULL,
        fingerprint_hash TEXT NOT NULL,
        consent_at DATETIME NOT NULL,
        version INTEGER NOT NULL DEFAULT 1,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME,
        CONSTRAINT fk_enterprise_application_owner
            FOREIGN KEY (owner_user_id) REFERENCES users(id) ON DELETE SET NULL,
        CONSTRAINT fk_enterprise_application_converted_tenant
            FOREIGN KEY (converted_tenant_id) REFERENCES tenants(id) ON DELETE SET NULL
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_enterprise_application_no
    ON enterprise_service_applications (application_no)
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_enterprise_application_idempotency
    ON enterprise_service_applications (idempotency_key)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_enterprise_application_status_created
    ON enterprise_service_applications (status, created_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_enterprise_application_mobile_created
    ON enterprise_service_applications (mobile, created_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_enterprise_application_owner_status
    ON enterprise_service_applications (owner_user_id, status)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_enterprise_application_converted_tenant
    ON enterprise_service_applications (converted_tenant_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_enterprise_application_request
    ON enterprise_service_applications (request_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_enterprise_application_fingerprint
    ON enterprise_service_applications (fingerprint_hash)
    """,
    """
    CREATE TABLE IF NOT EXISTS enterprise_application_submission_guards (
        fingerprint_hash TEXT NOT NULL PRIMARY KEY,
        active_idempotency_hash TEXT NOT NULL,
        application_id TEXT,
        claim_token TEXT NOT NULL,
        cooldown_until DATETIME NOT NULL,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CONSTRAINT fk_enterprise_guard_application
            FOREIGN KEY (application_id) REFERENCES enterprise_service_applications(id) ON DELETE SET NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_enterprise_guard_application
    ON enterprise_application_submission_guards (application_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_enterprise_guard_cooldown
    ON enterprise_application_submission_guards (cooldown_until)
    """,
    """
    CREATE TABLE IF NOT EXISTS attendance_sync_runs (
        id TEXT NOT NULL PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        run_trigger TEXT NOT NULL DEFAULT 'scheduled',
        status TEXT NOT NULL DEFAULT 'running',
        people_read INTEGER NOT NULL DEFAULT 0,
        people_upserted INTEGER NOT NULL DEFAULT 0,
        punches_read INTEGER NOT NULL DEFAULT 0,
        punches_inserted INTEGER NOT NULL DEFAULT 0,
        punches_duplicate INTEGER NOT NULL DEFAULT 0,
        unmatched_count INTEGER NOT NULL DEFAULT 0,
        suggested_count INTEGER NOT NULL DEFAULT 0,
        lookback_days INTEGER NOT NULL DEFAULT 90,
        cursor_check_time DATETIME,
        error_code TEXT,
        error_message TEXT,
        started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        completed_at DATETIME
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS attendance_external_people (
        id TEXT NOT NULL PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        external_user_id TEXT NOT NULL,
        badge_number TEXT,
        name TEXT NOT NULL DEFAULT '',
        department_name TEXT,
        employee_id TEXT,
        suggested_employee_id TEXT,
        mapping_status TEXT NOT NULL DEFAULT 'unmatched',
        mapped_at DATETIME,
        mapped_by TEXT,
        source_synced_at DATETIME,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_attendance_external_user
    ON attendance_external_people (tenant_id, external_user_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS attendance_raw_punches (
        id TEXT NOT NULL PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        external_user_id TEXT NOT NULL,
        check_time DATETIME NOT NULL,
        check_type TEXT,
        verify_code TEXT,
        sensor_id TEXT,
        serial_number TEXT,
        fingerprint TEXT NOT NULL,
        sync_run_id TEXT,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_attendance_raw_punch
    ON attendance_raw_punches (tenant_id, external_user_id, check_time)
    """,
    """
    CREATE TABLE IF NOT EXISTS attendance_shifts (
        id TEXT NOT NULL PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        code TEXT NOT NULL,
        name TEXT NOT NULL,
        shift_type TEXT NOT NULL DEFAULT 'fixed',
        start_time TEXT,
        end_time TEXT,
        cross_day BOOLEAN NOT NULL DEFAULT 0,
        required_work_hours NUMERIC(6,2),
        late_grace_minutes INTEGER NOT NULL DEFAULT 0,
        early_leave_grace_minutes INTEGER NOT NULL DEFAULT 0,
        require_check_in BOOLEAN NOT NULL DEFAULT 1,
        require_check_out BOOLEAN NOT NULL DEFAULT 1,
        status TEXT NOT NULL DEFAULT 'active',
        remark TEXT,
        created_by TEXT,
        updated_by TEXT,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME,
        UNIQUE (tenant_id, code)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS attendance_rule_sets (
        id TEXT NOT NULL PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        code TEXT NOT NULL,
        name TEXT NOT NULL,
        version INTEGER NOT NULL DEFAULT 1,
        effective_from DATE,
        effective_to DATE,
        status TEXT NOT NULL DEFAULT 'active',
        absent_if_no_punch BOOLEAN NOT NULL DEFAULT 1,
        missing_card_if_single_punch BOOLEAN NOT NULL DEFAULT 1,
        late_grace_minutes INTEGER,
        early_leave_grace_minutes INTEGER,
        remark TEXT,
        created_by TEXT,
        updated_by TEXT,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME,
        UNIQUE (tenant_id, code)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS attendance_groups (
        id TEXT NOT NULL PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        code TEXT NOT NULL,
        name TEXT NOT NULL,
        default_shift_id TEXT,
        default_rule_set_id TEXT,
        priority INTEGER NOT NULL DEFAULT 0,
        effective_from DATE,
        effective_to DATE,
        status TEXT NOT NULL DEFAULT 'active',
        remark TEXT,
        created_by TEXT,
        updated_by TEXT,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME,
        UNIQUE (tenant_id, code)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS attendance_group_memberships (
        id TEXT NOT NULL PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        group_id TEXT NOT NULL,
        employee_id TEXT,
        department_id TEXT,
        position_id TEXT,
        priority INTEGER NOT NULL DEFAULT 0,
        effective_from DATE,
        effective_to DATE,
        status TEXT NOT NULL DEFAULT 'active',
        remark TEXT,
        created_by TEXT,
        updated_by TEXT,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS attendance_holidays (
        id TEXT NOT NULL PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        holiday_date DATE NOT NULL,
        name TEXT NOT NULL,
        day_type TEXT NOT NULL DEFAULT 'holiday',
        status TEXT NOT NULL DEFAULT 'active',
        remark TEXT,
        created_by TEXT,
        updated_by TEXT,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME,
        UNIQUE (tenant_id, holiday_date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS attendance_schedules (
        id TEXT NOT NULL PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        employee_id TEXT NOT NULL,
        schedule_date DATE NOT NULL,
        day_type TEXT NOT NULL DEFAULT 'workday',
        shift_id TEXT,
        source TEXT NOT NULL DEFAULT 'manual',
        remark TEXT,
        created_by TEXT,
        updated_by TEXT,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME,
        UNIQUE (tenant_id, employee_id, schedule_date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS attendance_daily_results (
        id TEXT NOT NULL PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        employee_id TEXT NOT NULL,
        attendance_date DATE NOT NULL,
        day_type TEXT NOT NULL DEFAULT 'workday',
        schedule_source TEXT,
        group_id TEXT,
        shift_id TEXT,
        rule_set_id TEXT,
        scheduled_start DATETIME,
        scheduled_end DATETIME,
        check_in_time DATETIME,
        check_out_time DATETIME,
        work_hours NUMERIC(6,2),
        status TEXT NOT NULL DEFAULT 'unscheduled',
        late_minutes INTEGER NOT NULL DEFAULT 0,
        early_leave_minutes INTEGER NOT NULL DEFAULT 0,
        source_punch_ids JSON,
        request_ids JSON,
        explanation_json JSON,
        calculation_version TEXT NOT NULL DEFAULT 'v1',
        calculated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME,
        UNIQUE (tenant_id, employee_id, attendance_date)
    )
    """,
]

DEFAULT_ROLES = [
    ('platform_admin', '平台管理员', ['platform:*']),
    ('admin', '系统管理员', ['*']),
    ('hr_admin', 'HR管理员', ['hrm:*', 'recruit:*']),
    ('recruiter', '招聘专员', ['recruit:*', 'candidate:*', 'offer:*']),
    ('manager', '部门负责人', ['employee:view', 'approval:*', 'performance:*']),
    ('employee', '普通员工', ['self:*', 'request:create']),
    ('finance', '财务人员', ['salary:*', 'payroll:*']),
    ('executive', '高管/老板', ['dashboard:*', 'report:*']),
]

DEFAULT_PLAN_LIMITS = {
    'maxUsers': 200,
    'maxEmployees': 2000,
    'maxStorageMb': 102400,
    'aiMonthlyQuota': 10000,
}


def _column_exists_mysql(conn, table_name, column_name):
    sql = text(
        "SELECT COUNT(*) FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table AND COLUMN_NAME = :column"
    )
    return conn.execute(sql, {'table': table_name, 'column': column_name}).scalar() > 0


def _column_exists_sqlite(conn, table_name, column_name):
    rows = conn.execute(text(f"PRAGMA table_info({table_name})")).fetchall()
    return any(row[1] == column_name for row in rows)


def _is_duplicate_column_error(exc):
    original = getattr(exc, 'orig', None)
    candidates = [exc, original]
    for candidate in candidates:
        if candidate is None:
            continue
        args = getattr(candidate, 'args', ())
        if args and args[0] == 1060:
            return True
    return 'duplicate column' in str(exc).lower()


def _add_column_mysql(conn, table_name, column_name, column_sql):
    if not _column_exists_mysql(conn, table_name, column_name):
        try:
            conn.execute(text(f"ALTER TABLE `{table_name}` ADD COLUMN `{column_name}` {column_sql}"))
        except (OperationalError, ProgrammingError) as exc:
            if not _is_duplicate_column_error(exc):
                raise


def _add_column_sqlite(conn, table_name, column_name, column_sql):
    if not _column_exists_sqlite(conn, table_name, column_name):
        try:
            conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}"))
        except (OperationalError, ProgrammingError) as exc:
            if 'duplicate column name' not in str(exc).lower():
                raise


def _fix_attendance_sync_runs_table(conn, engine_name):
    if not engine_name.startswith('mysql'):
        return
    if not _table_exists(conn, engine_name, 'attendance_sync_runs'):
        return
    has_run_trigger = _column_exists_mysql(conn, 'attendance_sync_runs', 'run_trigger')
    has_trigger = _column_exists_mysql(conn, 'attendance_sync_runs', 'trigger')
    if has_trigger and not has_run_trigger:
        conn.execute(text(
            "ALTER TABLE `attendance_sync_runs` "
            "CHANGE COLUMN `trigger` `run_trigger` VARCHAR(30) NOT NULL DEFAULT 'scheduled'"
        ))
        return
    if not has_run_trigger:
        conn.execute(text(
            "ALTER TABLE `attendance_sync_runs` "
            "ADD COLUMN `run_trigger` VARCHAR(30) NOT NULL DEFAULT 'scheduled'"
        ))


def _fix_settings_table(conn, engine_name):
    if engine_name.startswith('mysql'):
        has_key = _column_exists_mysql(conn, 'settings', 'key')
        has_setting_key = _column_exists_mysql(conn, 'settings', 'setting_key')
        if has_setting_key and not has_key:
            conn.execute(text("ALTER TABLE `settings` CHANGE COLUMN `setting_key` `key` VARCHAR(100) NOT NULL"))
        has_value = _column_exists_mysql(conn, 'settings', 'value')
        has_setting_value = _column_exists_mysql(conn, 'settings', 'setting_value')
        if has_setting_value and not has_value:
            conn.execute(text("ALTER TABLE `settings` CHANGE COLUMN `setting_value` `value` JSON NOT NULL"))
    elif engine_name == 'sqlite':
        # SQLite 不支持 RENAME COLUMN（老版本），legacy 迁移仅在 MySQL 执行。
        # 明确记录 warning 而非静默跳过，便于开发环境排查。
        import logging
        logging.getLogger('hengpintong.migrations').warning(
            'settings_table_fix_skipped_on_sqlite',
            extra={'reason': 'SQLite 不支持列重命名，如需迁移请在 MySQL 执行'}
        )
        return


def _fix_assistant_evaluation_runs_table(conn, engine_name):
    if engine_name.startswith('mysql') and _column_exists_mysql(conn, 'assistant_evaluation_runs', 'retest_of'):
        conn.execute(text("ALTER TABLE `assistant_evaluation_runs` MODIFY COLUMN `retest_of` VARCHAR(64) NULL"))


def _mysql_index_definitions(conn, table_name):
    rows = conn.execute(text(
        "SELECT INDEX_NAME, COLUMN_NAME, SEQ_IN_INDEX, NON_UNIQUE "
        "FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table "
        "ORDER BY INDEX_NAME, SEQ_IN_INDEX"
    ), {'table': table_name}).fetchall()
    indexes = {}
    for index_name, column_name, sequence, non_unique in rows:
        item = indexes.setdefault(index_name, {'columns': [], 'unique': int(non_unique) == 0})
        item['columns'].append((int(sequence), column_name))
    for item in indexes.values():
        item['columns'] = [column for _sequence, column in sorted(item['columns'])]
    return indexes


def _fix_tenant_unique_indexes(conn, engine_name):
    if not engine_name.startswith('mysql'):
        return
    desired = (
        ('users', 'email', 'uq_users_tenant_email'),
        ('roles', 'code', 'uq_roles_tenant_code'),
    )
    for table_name, legacy_column, desired_name in desired:
        if not _table_exists(conn, engine_name, table_name):
            continue
        indexes = _mysql_index_definitions(conn, table_name)
        desired_exists = any(
            item['unique'] and item['columns'] == ['tenant_id', legacy_column]
            for item in indexes.values()
        )
        for index_name, item in indexes.items():
            if index_name == 'PRIMARY' or index_name == desired_name:
                continue
            if item['unique'] and item['columns'] == [legacy_column]:
                conn.execute(text(f"ALTER TABLE `{table_name}` DROP INDEX `{index_name}`"))
        if not desired_exists:
            conn.execute(text(
                f"ALTER TABLE `{table_name}` ADD CONSTRAINT `{desired_name}` "
                f"UNIQUE (`tenant_id`, `{legacy_column}`)"
            ))


def _ensure_employee_oa_indexes(conn, engine_name):
    if not _table_exists(conn, engine_name, 'employees'):
        return
    desired = (
        ('uq_employees_tenant_oa_person_id', ['tenant_id', 'oa_person_id'], True),
        ('ix_employees_oa_login_id', ['oa_login_id'], False),
        ('ix_employees_source_system', ['source_system'], False),
    )
    indexes = (
        _mysql_index_definitions(conn, 'employees')
        if engine_name.startswith('mysql')
        else _sqlite_index_definitions(conn, 'employees')
        if engine_name == 'sqlite'
        else {}
    )
    for index_name, columns, unique in desired:
        if any(
            item['columns'] == columns and item['unique'] == unique
            for item in indexes.values()
        ):
            continue
        unique_sql = 'UNIQUE ' if unique else ''
        if engine_name.startswith('mysql'):
            quoted_columns = ', '.join(f'`{column}`' for column in columns)
            conn.execute(text(
                f"ALTER TABLE `employees` ADD {unique_sql}INDEX `{index_name}` "
                f"({quoted_columns})"
            ))
        elif engine_name == 'sqlite':
            quoted_columns = ', '.join(f'"{column}"' for column in columns)
            conn.execute(text(
                f'CREATE {unique_sql}INDEX IF NOT EXISTS "{index_name}" '
                f'ON "employees" ({quoted_columns})'
            ))


def _ensure_enterprise_application_indexes(conn, engine_name):
    """Repair indexes when an older deployment already has the new table.

    ``CREATE TABLE IF NOT EXISTS`` only applies indexes on first creation.  A
    partially upgraded database must receive the same uniqueness and query
    indexes without requiring a destructive table rebuild.
    """
    if not _table_exists(conn, engine_name, 'enterprise_service_applications'):
        return
    desired = (
        ('uq_enterprise_application_no', ['application_no'], True),
        ('uq_enterprise_application_idempotency', ['idempotency_key'], True),
        ('idx_enterprise_application_status_created', ['status', 'created_at'], False),
        ('idx_enterprise_application_mobile_created', ['mobile', 'created_at'], False),
        ('idx_enterprise_application_owner_status', ['owner_user_id', 'status'], False),
        ('idx_enterprise_application_converted_tenant', ['converted_tenant_id'], False),
        ('idx_enterprise_application_request', ['request_id'], False),
        ('idx_enterprise_application_fingerprint', ['fingerprint_hash'], False),
    )
    if engine_name.startswith('mysql'):
        indexes = _mysql_index_definitions(conn, 'enterprise_service_applications')
    elif engine_name == 'sqlite':
        indexes = _sqlite_index_definitions(conn, 'enterprise_service_applications')
    else:
        return
    for index_name, columns, unique in desired:
        named = indexes.get(index_name)
        named_is_correct = bool(
            named and named['columns'] == columns and named['unique'] == unique
        )
        if named_is_correct:
            continue
        if named is not None:
            if engine_name.startswith('mysql'):
                conn.execute(text(
                    f"ALTER TABLE `enterprise_service_applications` DROP INDEX `{index_name}`"
                ))
            else:
                conn.execute(text(f'DROP INDEX "{index_name}"'))
        elif any(
            item['columns'] == columns and item['unique'] == unique
            for item in indexes.values()
        ):
            continue
        unique_sql = 'UNIQUE ' if unique else ''
        if engine_name.startswith('mysql'):
            quoted_columns = ', '.join(f'`{column}`' for column in columns)
            conn.execute(text(
                f"ALTER TABLE `enterprise_service_applications` "
                f"ADD {unique_sql}INDEX `{index_name}` ({quoted_columns})"
            ))
        else:
            quoted_columns = ', '.join(f'"{column}"' for column in columns)
            conn.execute(text(
                f'CREATE {unique_sql}INDEX "{index_name}" '
                f'ON "enterprise_service_applications" ({quoted_columns})'
            ))


def _sqlite_index_definitions(conn, table_name):
    indexes = {}
    for row in conn.execute(text(f'PRAGMA index_list("{table_name}")')).fetchall():
        index_name = row[1]
        columns = [
            info[2]
            for info in conn.execute(text(f'PRAGMA index_info("{index_name}")')).fetchall()
        ]
        indexes[index_name] = {'columns': columns, 'unique': bool(row[2])}
    return indexes


def _mysql_foreign_key_definitions(conn, table_name):
    rows = conn.execute(text(
        "SELECT k.CONSTRAINT_NAME, k.COLUMN_NAME, k.REFERENCED_TABLE_NAME, "
        "k.REFERENCED_COLUMN_NAME, r.DELETE_RULE "
        "FROM information_schema.KEY_COLUMN_USAGE k "
        "LEFT JOIN information_schema.REFERENTIAL_CONSTRAINTS r "
        "ON r.CONSTRAINT_SCHEMA = k.CONSTRAINT_SCHEMA "
        "AND r.TABLE_NAME = k.TABLE_NAME "
        "AND r.CONSTRAINT_NAME = k.CONSTRAINT_NAME "
        "WHERE k.TABLE_SCHEMA = DATABASE() AND k.TABLE_NAME = :table "
        "AND k.REFERENCED_TABLE_NAME IS NOT NULL"
    ), {'table': table_name}).fetchall()
    return {
        row[0]: {
            'column': row[1],
            'referred_table': row[2],
            'referred_column': row[3],
            'ondelete': (row[4] or '').upper(),
        }
        for row in rows
    }


def _ensure_enterprise_application_foreign_keys(conn, engine_name):
    if not engine_name.startswith('mysql') or not _table_exists(
        conn, engine_name, 'enterprise_service_applications'
    ):
        return
    desired = (
        ('fk_enterprise_application_owner', 'owner_user_id', 'users'),
        ('fk_enterprise_application_converted_tenant', 'converted_tenant_id', 'tenants'),
    )
    definitions = _mysql_foreign_key_definitions(conn, 'enterprise_service_applications')
    for constraint_name, column_name, referred_table in desired:
        expected = {
            'column': column_name,
            'referred_table': referred_table,
            'referred_column': 'id',
            'ondelete': 'SET NULL',
        }
        matching_constraint = None
        for existing_name, definition in definitions.items():
            relevant = existing_name == constraint_name or definition.get('column') == column_name
            if not relevant:
                continue
            if definition == expected and matching_constraint is None:
                matching_constraint = existing_name
                continue
            conn.execute(text(
                f"ALTER TABLE `enterprise_service_applications` "
                f"DROP FOREIGN KEY `{existing_name}`"
            ))
        if matching_constraint is not None:
            continue
        conn.execute(text(
            f"UPDATE `enterprise_service_applications` a "
            f"LEFT JOIN `{referred_table}` r ON r.`id` = a.`{column_name}` "
            f"SET a.`{column_name}` = NULL "
            f"WHERE a.`{column_name}` IS NOT NULL AND r.`id` IS NULL"
        ))
        conn.execute(text(
            f"ALTER TABLE `enterprise_service_applications` "
            f"ADD CONSTRAINT `{constraint_name}` FOREIGN KEY (`{column_name}`) "
            f"REFERENCES `{referred_table}` (`id`) ON DELETE SET NULL"
        ))


_SQLITE_ENTERPRISE_APPLICATION_TABLE = """
CREATE TABLE enterprise_service_applications (
    id TEXT NOT NULL PRIMARY KEY,
    application_no TEXT NOT NULL,
    company_name TEXT NOT NULL,
    contact_name TEXT NOT NULL,
    mobile TEXT NOT NULL,
    email TEXT,
    region TEXT,
    employee_scale TEXT,
    interested_modules JSON,
    preferred_contact_time TEXT,
    notes TEXT,
    status TEXT NOT NULL DEFAULT 'new',
    owner_user_id TEXT,
    follow_up_at DATETIME,
    internal_note TEXT,
    converted_tenant_id TEXT,
    source TEXT NOT NULL DEFAULT 'enterprise_application',
    request_id TEXT,
    idempotency_key TEXT NOT NULL,
    fingerprint_hash TEXT NOT NULL,
    consent_at DATETIME NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME,
    CONSTRAINT fk_enterprise_application_owner
        FOREIGN KEY (owner_user_id) REFERENCES users(id) ON DELETE SET NULL,
    CONSTRAINT fk_enterprise_application_converted_tenant
        FOREIGN KEY (converted_tenant_id) REFERENCES tenants(id) ON DELETE SET NULL
)
"""

_SQLITE_ENTERPRISE_GUARD_TABLE = """
CREATE TABLE enterprise_application_submission_guards (
    fingerprint_hash TEXT NOT NULL PRIMARY KEY,
    active_idempotency_hash TEXT NOT NULL,
    application_id TEXT,
    claim_token TEXT NOT NULL,
    cooldown_until DATETIME NOT NULL,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_enterprise_guard_application
        FOREIGN KEY (application_id) REFERENCES enterprise_service_applications(id) ON DELETE SET NULL
)
"""


def _sqlite_foreign_key_definitions(conn, table_name):
    return {
        (row[3], row[2], row[4], (row[6] or '').upper())
        for row in conn.execute(text(f'PRAGMA foreign_key_list("{table_name}")')).fetchall()
    }


def _ensure_enterprise_application_sqlite_schema(conn, engine_name):
    if engine_name != 'sqlite' or not _table_exists(
        conn, engine_name, 'enterprise_service_applications'
    ):
        return
    expected_foreign_keys = {
        ('owner_user_id', 'users', 'id', 'SET NULL'),
        ('converted_tenant_id', 'tenants', 'id', 'SET NULL'),
    }
    destination_columns = [
        'id', 'application_no', 'company_name', 'contact_name', 'mobile', 'email',
        'region', 'employee_scale', 'interested_modules', 'preferred_contact_time',
        'notes', 'status', 'owner_user_id', 'follow_up_at', 'internal_note',
        'converted_tenant_id', 'source', 'request_id', 'idempotency_key',
        'fingerprint_hash', 'consent_at', 'version', 'created_at', 'updated_at',
    ]
    existing_columns = {
        row[1]
        for row in conn.execute(text(
            'PRAGMA table_info("enterprise_service_applications")'
        )).fetchall()
    }
    if (
        set(destination_columns) <= existing_columns
        and expected_foreign_keys <= _sqlite_foreign_key_definitions(
            conn, 'enterprise_service_applications'
        )
    ):
        return

    guard_exists = _table_exists(conn, engine_name, 'enterprise_application_submission_guards')
    if guard_exists:
        conn.execute(text(
            'ALTER TABLE enterprise_application_submission_guards '
            'RENAME TO enterprise_application_submission_guards__legacy'
        ))
    conn.execute(text(
        'ALTER TABLE enterprise_service_applications '
        'RENAME TO enterprise_service_applications__legacy'
    ))
    conn.execute(text(_SQLITE_ENTERPRISE_APPLICATION_TABLE))

    legacy_columns = {
        row[1]
        for row in conn.execute(text(
            'PRAGMA table_info("enterprise_service_applications__legacy")'
        )).fetchall()
    }
    required = {
        'id', 'application_no', 'company_name', 'contact_name', 'mobile',
        'idempotency_key', 'fingerprint_hash', 'consent_at', 'created_at',
    }
    missing_required = sorted(required - legacy_columns)
    if missing_required:
        raise RuntimeError(
            'enterprise application migration missing required columns: '
            + ', '.join(missing_required)
        )
    select_expressions = []
    for column_name in destination_columns:
        if column_name == 'version':
            select_expressions.append(
                'COALESCE(version, 1) AS version' if 'version' in legacy_columns else '1 AS version'
            )
        elif column_name == 'status':
            select_expressions.append(
                "COALESCE(status, 'new') AS status"
                if 'status' in legacy_columns else "'new' AS status"
            )
        elif column_name == 'source':
            select_expressions.append(
                "COALESCE(source, 'enterprise_application') AS source"
                if 'source' in legacy_columns else "'enterprise_application' AS source"
            )
        elif column_name == 'owner_user_id' and column_name in legacy_columns:
            select_expressions.append(
                'CASE WHEN owner_user_id IS NULL OR EXISTS '
                '(SELECT 1 FROM users WHERE users.id = owner_user_id) '
                'THEN owner_user_id ELSE NULL END AS owner_user_id'
            )
        elif column_name == 'converted_tenant_id' and column_name in legacy_columns:
            select_expressions.append(
                'CASE WHEN converted_tenant_id IS NULL OR EXISTS '
                '(SELECT 1 FROM tenants WHERE tenants.id = converted_tenant_id) '
                'THEN converted_tenant_id ELSE NULL END AS converted_tenant_id'
            )
        elif column_name in legacy_columns:
            select_expressions.append(f'"{column_name}"')
        else:
            select_expressions.append('NULL')
    quoted_columns = ', '.join(f'"{column}"' for column in destination_columns)
    conn.execute(text(
        f'INSERT INTO enterprise_service_applications ({quoted_columns}) '
        f'SELECT {", ".join(select_expressions)} '
        'FROM enterprise_service_applications__legacy'
    ))

    conn.execute(text(_SQLITE_ENTERPRISE_GUARD_TABLE))
    if guard_exists:
        guard_columns = {
            row[1]
            for row in conn.execute(text(
                'PRAGMA table_info("enterprise_application_submission_guards__legacy")'
            )).fetchall()
        }
        expected_guard_columns = {
            'fingerprint_hash', 'active_idempotency_hash', 'application_id',
            'claim_token', 'cooldown_until', 'updated_at',
        }
        if expected_guard_columns <= guard_columns:
            conn.execute(text('''
                INSERT INTO enterprise_application_submission_guards (
                    fingerprint_hash, active_idempotency_hash, application_id,
                    claim_token, cooldown_until, updated_at
                )
                SELECT fingerprint_hash, active_idempotency_hash,
                    CASE WHEN application_id IS NULL OR EXISTS (
                        SELECT 1 FROM enterprise_service_applications a
                        WHERE a.id = application_id
                    ) THEN application_id ELSE NULL END,
                    claim_token, cooldown_until, updated_at
                FROM enterprise_application_submission_guards__legacy
            '''))
        conn.execute(text('DROP TABLE enterprise_application_submission_guards__legacy'))
    conn.execute(text('DROP TABLE enterprise_service_applications__legacy'))


def _pseudonymize_enterprise_application_metadata(conn, engine_name):
    if not _table_exists(conn, engine_name, 'enterprise_service_applications'):
        return
    from services.request_metadata import enterprise_idempotency_digest, sanitize_request_id

    rows = conn.execute(text(
        'SELECT id, idempotency_key, fingerprint_hash, request_id '
        'FROM enterprise_service_applications'
    )).fetchall()
    for application_id, idempotency_key, fingerprint_hash, request_id in rows:
        safe_idempotency = idempotency_key
        if idempotency_key and not str(idempotency_key).startswith('idem_'):
            safe_idempotency = enterprise_idempotency_digest(
                str(idempotency_key), str(fingerprint_hash or '')
            )
        safe_request_id = sanitize_request_id(request_id)
        if safe_idempotency != idempotency_key or safe_request_id != request_id:
            conn.execute(text(
                'UPDATE enterprise_service_applications '
                'SET idempotency_key = :idempotency_key, request_id = :request_id '
                'WHERE id = :application_id'
            ), {
                'idempotency_key': safe_idempotency,
                'request_id': safe_request_id,
                'application_id': application_id,
            })


@contextmanager
def schema_migration_lock_for_connection(conn, engine_name, timeout_seconds=120):
    if not engine_name.startswith('mysql'):
        yield
        return
    lock_name = 'hengpintong_saas_schema_migration'
    acquired = conn.execute(
        text('SELECT GET_LOCK(:lock_name, :timeout_seconds)'),
        {'lock_name': lock_name, 'timeout_seconds': int(timeout_seconds)},
    ).scalar()
    if acquired != 1:
        raise RuntimeError('无法获取数据库迁移锁')
    try:
        yield
    finally:
        conn.execute(text('SELECT RELEASE_LOCK(:lock_name)'), {'lock_name': lock_name})


@contextmanager
def schema_migration_lock(timeout_seconds=120):
    engine_name = db.engine.name
    if not engine_name.startswith('mysql'):
        yield
        return
    with db.engine.connect() as conn:
        with schema_migration_lock_for_connection(conn, engine_name, timeout_seconds):
            yield


def _table_exists(conn, engine_name, table_name):
    if engine_name.startswith('mysql'):
        sql = text(
            "SELECT COUNT(*) FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table"
        )
        return conn.execute(sql, {'table': table_name}).scalar() > 0
    if engine_name == 'sqlite':
        sql = text("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=:table")
        return conn.execute(sql, {'table': table_name}).scalar() > 0
    return True


def _backfill_default_tenant(conn, engine_name, tenant_id):
    for table_name in TENANT_SCOPED_TABLES:
        if not _table_exists(conn, engine_name, table_name):
            continue
        if engine_name.startswith('mysql'):
            if not _column_exists_mysql(conn, table_name, 'tenant_id'):
                continue
            if table_name == 'users':
                conn.execute(
                    text("UPDATE `users` SET tenant_id = :tenant_id WHERE tenant_id IS NULL AND role <> 'platform_admin'"),
                    {'tenant_id': tenant_id},
                )
            else:
                conn.execute(text(f"UPDATE `{table_name}` SET tenant_id = :tenant_id WHERE tenant_id IS NULL"), {'tenant_id': tenant_id})
        elif engine_name == 'sqlite':
            if not _column_exists_sqlite(conn, table_name, 'tenant_id'):
                continue
            if table_name == 'users':
                conn.execute(
                    text("UPDATE users SET tenant_id = :tenant_id WHERE tenant_id IS NULL AND role <> 'platform_admin'"),
                    {'tenant_id': tenant_id},
                )
            else:
                conn.execute(text(f"UPDATE {table_name} SET tenant_id = :tenant_id WHERE tenant_id IS NULL"), {'tenant_id': tenant_id})


def ensure_schema():
    engine_name = db.engine.name
    with db.engine.begin() as conn:
        if engine_name.startswith('mysql'):
            for sql in MYSQL_CREATE_TABLES:
                conn.execute(text(sql))
            for table, columns in MYSQL_COLUMNS.items():
                for column_name, column_sql in columns:
                    _add_column_mysql(conn, table, column_name, column_sql)
            _fix_settings_table(conn, engine_name)
            _fix_assistant_evaluation_runs_table(conn, engine_name)
            _fix_tenant_unique_indexes(conn, engine_name)
            _ensure_employee_oa_indexes(conn, engine_name)
            _ensure_enterprise_application_foreign_keys(conn, engine_name)
            _ensure_enterprise_application_indexes(conn, engine_name)
            _pseudonymize_enterprise_application_metadata(conn, engine_name)
            _fix_attendance_sync_runs_table(conn, engine_name)
        elif engine_name == 'sqlite':
            deferred_enterprise_indexes = []
            for sql in SQLITE_CREATE_TABLES:
                normalized_sql = ' '.join(sql.lower().split())
                if (
                    'index' in normalized_sql
                    and (
                        'on enterprise_service_applications ' in normalized_sql
                        or 'on enterprise_application_submission_guards ' in normalized_sql
                    )
                ):
                    deferred_enterprise_indexes.append(sql)
                    continue
                conn.execute(text(sql))
            for table, columns in SQLITE_COLUMNS.items():
                for column_name, column_sql in columns:
                    _add_column_sqlite(conn, table, column_name, column_sql)
            _fix_settings_table(conn, engine_name)
            _ensure_employee_oa_indexes(conn, engine_name)
            _ensure_enterprise_application_sqlite_schema(conn, engine_name)
            _ensure_enterprise_application_indexes(conn, engine_name)
            for sql in deferred_enterprise_indexes:
                conn.execute(text(sql))
            _pseudonymize_enterprise_application_metadata(conn, engine_name)


def seed_defaults():
    plan = Plan.query.get('enterprise')
    if not plan:
        db.session.add(Plan(
            code='enterprise',
            name='企业全功能版',
            enabled_modules_json=list(SAAS_MODULE_KEYS),
            limits_json=DEFAULT_PLAN_LIMITS,
            is_active=True,
        ))
    tenant = default_tenant()
    tenant.name = tenant.name or DEFAULT_TENANT_NAME
    tenant.plan_code = tenant.plan_code or 'enterprise'
    seed_tenant_modules(tenant.id)

    for code, name, permissions in DEFAULT_ROLES:
        role = Role.query.filter_by(tenant_id=tenant.id, code=code).first()
        if not role:
            db.session.add(Role(
                id=str(uuid.uuid4()),
                tenant_id=tenant.id,
                code=code,
                name=name,
                description=f'{name}基础角色，权限结构为后续RBAC预留',
                permissions=permissions,
                status='active',
            ))
    if not Setting.query.get('companyName'):
        db.session.add(Setting(key='companyName', tenant_id=tenant.id, value='恒聘通智能HRM'))
    if not Setting.query.get(ASSISTANT_MODEL_SETTINGS_KEY):
        db.session.add(Setting(
            key=ASSISTANT_MODEL_SETTINGS_KEY,
            tenant_id=tenant.id,
            value=assistant_model_settings_defaults(),
        ))
    platform_email = os.getenv('PLATFORM_ADMIN_EMAIL', '').strip().lower()
    platform_password = os.getenv('PLATFORM_ADMIN_PASSWORD', '').strip()
    if platform_email and platform_password and not User.query.filter_by(email=platform_email, tenant_id=None).first():
        db.session.add(User(
            id=str(uuid.uuid4()),
            email=platform_email,
            password_hash=bcrypt.hashpw(platform_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8'),
            name=os.getenv('PLATFORM_ADMIN_NAME', '平台管理员'),
            role='platform_admin',
            status='active',
            tenant_id=None,
        ))
    db.session.commit()
    engine_name = db.engine.name
    with db.engine.begin() as conn:
        _backfill_default_tenant(conn, engine_name, tenant.id)
