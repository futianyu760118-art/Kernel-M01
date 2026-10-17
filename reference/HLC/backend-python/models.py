from flask_sqlalchemy import SQLAlchemy
from flask_sqlalchemy.query import Query
from datetime import datetime, date, timezone
from decimal import Decimal
from functools import lru_cache


class SessionGetQuery(Query):
    """Compatibility query that routes ``Model.query.get`` to SQLAlchemy 2.x."""

    def get(self, ident):
        mapper = self._only_full_mapper_zero('get')
        return self.session.get(mapper.class_, ident)


db = SQLAlchemy(query_class=SessionGetQuery)

# 请求级名称缓存（避免 N+1）。
# 注意：SQLAlchemy 的 session identity map 已经缓存了同一 session 内的 get()，
# 但跨 to_dict() 调用时仍可能重复查询。这里用简单字典做二级缓存。
_name_cache: dict = {}


def _reset_name_cache():
    """在每个请求开始时清空缓存（由 app.before_request 调用）。"""
    _name_cache.clear()


def iso(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return None


def utc_iso(value):
    """Serialize database-naive UTC datetimes with an explicit ``Z``."""
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.isoformat().replace('+00:00', 'Z')


def money(value):
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return value


class Tenant(db.Model):
    """SaaS customer tenant."""
    __tablename__ = 'tenants'

    id = db.Column(db.String(36), primary_key=True)
    name = db.Column(db.String(160), nullable=False)
    slug = db.Column(db.String(80), unique=True, nullable=False, index=True)
    status = db.Column(db.String(30), default='active', nullable=False, index=True)
    plan_code = db.Column(db.String(60), db.ForeignKey('plans.code', ondelete='SET NULL'), nullable=True, index=True)
    max_users = db.Column(db.Integer, default=50)
    max_employees = db.Column(db.Integer, default=500)
    max_storage_mb = db.Column(db.Integer, default=10240)
    ai_monthly_quota = db.Column(db.Integer, default=1000)
    expires_at = db.Column(db.DateTime)
    settings_json = db.Column(db.JSON, default=dict)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'slug': self.slug,
            'status': self.status,
            'planCode': self.plan_code,
            'maxUsers': self.max_users,
            'maxEmployees': self.max_employees,
            'maxStorageMb': self.max_storage_mb,
            'aiMonthlyQuota': self.ai_monthly_quota,
            'expiresAt': iso(self.expires_at),
            'settings': self.settings_json or {},
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
        }


class Plan(db.Model):
    """Commercial package definition."""
    __tablename__ = 'plans'

    code = db.Column(db.String(60), primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    enabled_modules_json = db.Column(db.JSON, default=list)
    limits_json = db.Column(db.JSON, default=dict)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'code': self.code,
            'name': self.name,
            'enabledModules': self.enabled_modules_json or [],
            'limits': self.limits_json or {},
            'isActive': bool(self.is_active),
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
        }


class TenantSubscription(db.Model):
    __tablename__ = 'tenant_subscriptions'

    id = db.Column(db.String(36), primary_key=True)
    tenant_id = db.Column(db.String(36), db.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False, index=True)
    plan_code = db.Column(db.String(60), db.ForeignKey('plans.code', ondelete='SET NULL'), nullable=True, index=True)
    status = db.Column(db.String(30), default='active', nullable=False, index=True)
    starts_at = db.Column(db.DateTime, default=datetime.utcnow)
    ends_at = db.Column(db.DateTime)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    tenant = db.relationship('Tenant', foreign_keys=[tenant_id])

    def to_dict(self):
        return {
            'id': self.id,
            'tenantId': self.tenant_id,
            'planCode': self.plan_code,
            'status': self.status,
            'startsAt': iso(self.starts_at),
            'endsAt': iso(self.ends_at),
            'notes': self.notes,
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
        }


class TenantOrder(db.Model):
    __tablename__ = 'tenant_orders'

    id = db.Column(db.String(36), primary_key=True)
    tenant_id = db.Column(db.String(36), db.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False, index=True)
    subscription_id = db.Column(db.String(36), db.ForeignKey('tenant_subscriptions.id', ondelete='SET NULL'), nullable=True, index=True)
    order_no = db.Column(db.String(80), unique=True, nullable=False, index=True)
    amount_cents = db.Column(db.Integer, default=0, nullable=False)
    currency = db.Column(db.String(12), default='CNY', nullable=False)
    status = db.Column(db.String(30), default='pending', nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    paid_at = db.Column(db.DateTime)

    tenant = db.relationship('Tenant', foreign_keys=[tenant_id])
    subscription = db.relationship('TenantSubscription', foreign_keys=[subscription_id])

    def to_dict(self):
        return {
            'id': self.id,
            'tenantId': self.tenant_id,
            'subscriptionId': self.subscription_id,
            'orderNo': self.order_no,
            'amountCents': self.amount_cents,
            'currency': self.currency,
            'status': self.status,
            'createdAt': iso(self.created_at),
            'paidAt': iso(self.paid_at),
        }


class TenantInvoice(db.Model):
    __tablename__ = 'tenant_invoices'

    id = db.Column(db.String(36), primary_key=True)
    tenant_id = db.Column(db.String(36), db.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False, index=True)
    order_id = db.Column(db.String(36), db.ForeignKey('tenant_orders.id', ondelete='SET NULL'), nullable=True, index=True)
    title = db.Column(db.String(160), nullable=False)
    tax_no = db.Column(db.String(80))
    amount_cents = db.Column(db.Integer, default=0, nullable=False)
    status = db.Column(db.String(30), default='requested', nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    issued_at = db.Column(db.DateTime)

    tenant = db.relationship('Tenant', foreign_keys=[tenant_id])
    order = db.relationship('TenantOrder', foreign_keys=[order_id])

    def to_dict(self):
        return {
            'id': self.id,
            'tenantId': self.tenant_id,
            'orderId': self.order_id,
            'title': self.title,
            'taxNo': self.tax_no,
            'amountCents': self.amount_cents,
            'status': self.status,
            'createdAt': iso(self.created_at),
            'issuedAt': iso(self.issued_at),
        }


class TenantBillingEvent(db.Model):
    __tablename__ = 'tenant_billing_events'

    id = db.Column(db.String(36), primary_key=True)
    tenant_id = db.Column(db.String(36), db.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False, index=True)
    event_type = db.Column(db.String(80), nullable=False, index=True)
    note = db.Column(db.Text)
    operator_id = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    tenant = db.relationship('Tenant', foreign_keys=[tenant_id])
    operator = db.relationship('User', foreign_keys=[operator_id])

    def to_dict(self):
        data = {
            'id': self.id,
            'tenantId': self.tenant_id,
            'eventType': self.event_type,
            'note': self.note or '',
            'operatorId': self.operator_id,
            'createdAt': iso(self.created_at),
        }
        if self.operator:
            data['operatorName'] = self.operator.name
        return data


class TenantModule(db.Model):
    __tablename__ = 'tenant_modules'

    id = db.Column(db.String(36), primary_key=True)
    tenant_id = db.Column(db.String(36), db.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False, index=True)
    module_key = db.Column(db.String(80), nullable=False, index=True)
    enabled = db.Column(db.Boolean, default=True, nullable=False)
    limits_json = db.Column(db.JSON, default=dict)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    tenant = db.relationship('Tenant', foreign_keys=[tenant_id])

    __table_args__ = (db.UniqueConstraint('tenant_id', 'module_key', name='uq_tenant_module_key'),)

    def to_dict(self):
        return {
            'id': self.id,
            'tenantId': self.tenant_id,
            'moduleKey': self.module_key,
            'enabled': bool(self.enabled),
            'limits': self.limits_json or {},
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
        }


class TenantOnboardingItem(db.Model):
    __tablename__ = 'tenant_onboarding_items'

    id = db.Column(db.String(36), primary_key=True)
    tenant_id = db.Column(db.String(36), db.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False, index=True)
    item_key = db.Column(db.String(80), nullable=False)
    label = db.Column(db.String(160))
    status = db.Column(db.String(30), default='pending', nullable=False, index=True)
    note = db.Column(db.Text)
    completed_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    tenant = db.relationship('Tenant', foreign_keys=[tenant_id])

    __table_args__ = (
        db.UniqueConstraint('tenant_id', 'item_key', name='uq_tenant_onboarding_item'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'tenantId': self.tenant_id,
            'key': self.item_key,
            'label': self.label,
            'status': self.status,
            'note': self.note,
            'completedAt': iso(self.completed_at),
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
        }


class HeadcountPlan(db.Model):
    __tablename__ = 'headcount_plans'
    __table_args__ = (
        db.UniqueConstraint('tenant_id', 'department_id', name='uq_headcount_plan_department'),
    )

    id = db.Column(db.String(36), primary_key=True)
    tenant_id = db.Column(db.String(36), db.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False, index=True)
    department_id = db.Column(db.String(36), db.ForeignKey('departments.id', ondelete='CASCADE'), nullable=False, index=True)
    plan_count = db.Column(db.Integer, default=0, nullable=False)
    note = db.Column(db.Text)
    created_by = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='SET NULL'))
    updated_by = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='SET NULL'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    department = db.relationship('Department', foreign_keys=[department_id])

    def to_dict(self):
        return {
            'id': self.id,
            'tenantId': self.tenant_id,
            'departmentId': self.department_id,
            'departmentName': self.department.name if self.department else None,
            'plan': self.plan_count,
            'note': self.note or '',
            'createdBy': self.created_by,
            'updatedBy': self.updated_by,
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
        }


class HeadcountChange(db.Model):
    __tablename__ = 'headcount_changes'

    id = db.Column(db.String(36), primary_key=True)
    tenant_id = db.Column(db.String(36), db.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False, index=True)
    department_id = db.Column(db.String(36), db.ForeignKey('departments.id', ondelete='CASCADE'), nullable=False, index=True)
    old_plan = db.Column(db.Integer, default=0, nullable=False)
    new_plan = db.Column(db.Integer, default=0, nullable=False)
    actual_count = db.Column(db.Integer, default=0, nullable=False)
    note = db.Column(db.Text)
    operator_id = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='SET NULL'), index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    department = db.relationship('Department', foreign_keys=[department_id])
    operator = db.relationship('User', foreign_keys=[operator_id])

    def to_dict(self):
        return {
            'id': self.id,
            'tenantId': self.tenant_id,
            'departmentId': self.department_id,
            'departmentName': self.department.name if self.department else None,
            'oldPlan': self.old_plan,
            'newPlan': self.new_plan,
            'actual': self.actual_count,
            'note': self.note or '',
            'operatorId': self.operator_id,
            'operatorName': self.operator.name if self.operator else None,
            'createdAt': iso(self.created_at),
        }


class PeopleAnalyticsSnapshot(db.Model):
    __tablename__ = 'people_analytics_snapshots'

    id = db.Column(db.String(36), primary_key=True)
    tenant_id = db.Column(db.String(36), db.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False, index=True)
    metric_key = db.Column(db.String(80), nullable=False, index=True)
    payload_json = db.Column(db.JSON, default=dict)
    generated_by = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='SET NULL'), index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    generator = db.relationship('User', foreign_keys=[generated_by])

    def to_dict(self):
        return {
            'id': self.id,
            'tenantId': self.tenant_id,
            'metricKey': self.metric_key,
            'payload': self.payload_json or {},
            'generatedBy': self.generated_by,
            'generatorName': self.generator.name if self.generator else None,
            'createdAt': iso(self.created_at),
        }


class TalentStrategySnapshot(db.Model):
    __tablename__ = 'talent_strategy_snapshots'

    id = db.Column(db.String(36), primary_key=True)
    tenant_id = db.Column(db.String(36), db.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False, index=True)
    feature_key = db.Column(db.String(100), nullable=False, index=True)
    payload_json = db.Column(db.JSON)
    generated_by = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    generator = db.relationship('User', foreign_keys=[generated_by])

    def to_dict(self):
        return {
            'id': self.id,
            'tenantId': self.tenant_id,
            'featureKey': self.feature_key,
            'payload': self.payload_json or {},
            'generatedBy': self.generated_by,
            'generatorName': self.generator.name if self.generator else None,
            'createdAt': iso(self.created_at),
        }


class AnalyticsInsightSnapshot(db.Model):
    __tablename__ = 'analytics_insight_snapshots'

    id = db.Column(db.String(36), primary_key=True)
    tenant_id = db.Column(db.String(36), db.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False, index=True)
    feature_key = db.Column(db.String(100), nullable=False, index=True)
    payload_json = db.Column(db.JSON)
    generated_by = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    generator = db.relationship('User', foreign_keys=[generated_by])

    def to_dict(self):
        return {
            'id': self.id,
            'tenantId': self.tenant_id,
            'featureKey': self.feature_key,
            'payload': self.payload_json or {},
            'generatedBy': self.generated_by,
            'generatorName': self.generator.name if self.generator else None,
            'createdAt': iso(self.created_at),
        }


class IntelligenceInsightSnapshot(db.Model):
    __tablename__ = 'intelligence_insight_snapshots'

    id = db.Column(db.String(36), primary_key=True)
    tenant_id = db.Column(db.String(36), db.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False, index=True)
    feature_key = db.Column(db.String(100), nullable=False, index=True)
    payload_json = db.Column(db.JSON)
    generated_by = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    generator = db.relationship('User', foreign_keys=[generated_by])

    def to_dict(self):
        return {
            'id': self.id,
            'tenantId': self.tenant_id,
            'featureKey': self.feature_key,
            'payload': self.payload_json or {},
            'generatedBy': self.generated_by,
            'generatorName': self.generator.name if self.generator else None,
            'createdAt': iso(self.created_at),
        }


class CollaborationInsightSnapshot(db.Model):
    __tablename__ = 'collaboration_insight_snapshots'

    id = db.Column(db.String(36), primary_key=True)
    tenant_id = db.Column(db.String(36), db.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False, index=True)
    feature_key = db.Column(db.String(100), nullable=False, index=True)
    payload_json = db.Column(db.JSON)
    generated_by = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    generator = db.relationship('User', foreign_keys=[generated_by])

    def to_dict(self):
        return {
            'id': self.id,
            'tenantId': self.tenant_id,
            'featureKey': self.feature_key,
            'payload': self.payload_json or {},
            'generatedBy': self.generated_by,
            'generatorName': self.generator.name if self.generator else None,
            'createdAt': iso(self.created_at),
        }


class WorkforceInsightSnapshot(db.Model):
    __tablename__ = 'workforce_insight_snapshots'

    id = db.Column(db.String(36), primary_key=True)
    tenant_id = db.Column(db.String(36), db.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False, index=True)
    feature_key = db.Column(db.String(100), nullable=False, index=True)
    payload_json = db.Column(db.JSON)
    generated_by = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    generator = db.relationship('User', foreign_keys=[generated_by])

    def to_dict(self):
        return {
            'id': self.id,
            'tenantId': self.tenant_id,
            'featureKey': self.feature_key,
            'payload': self.payload_json or {},
            'generatedBy': self.generated_by,
            'generatorName': self.generator.name if self.generator else None,
            'createdAt': iso(self.created_at),
        }


class IntegrationInsightSnapshot(db.Model):
    __tablename__ = 'integration_insight_snapshots'

    id = db.Column(db.String(36), primary_key=True)
    tenant_id = db.Column(db.String(36), db.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False, index=True)
    feature_key = db.Column(db.String(100), nullable=False, index=True)
    payload_json = db.Column(db.JSON)
    generated_by = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    generator = db.relationship('User', foreign_keys=[generated_by])

    def to_dict(self):
        return {
            'id': self.id,
            'tenantId': self.tenant_id,
            'featureKey': self.feature_key,
            'payload': self.payload_json or {},
            'generatedBy': self.generated_by,
            'generatorName': self.generator.name if self.generator else None,
            'createdAt': iso(self.created_at),
        }


class MobileInsightSnapshot(db.Model):
    __tablename__ = 'mobile_insight_snapshots'

    id = db.Column(db.String(36), primary_key=True)
    tenant_id = db.Column(db.String(36), db.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False, index=True)
    feature_key = db.Column(db.String(100), nullable=False, index=True)
    payload_json = db.Column(db.JSON)
    generated_by = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    generator = db.relationship('User', foreign_keys=[generated_by])

    def to_dict(self):
        return {
            'id': self.id,
            'tenantId': self.tenant_id,
            'featureKey': self.feature_key,
            'payload': self.payload_json or {},
            'generatedBy': self.generated_by,
            'generatorName': self.generator.name if self.generator else None,
            'createdAt': iso(self.created_at),
        }


class SecurityInsightSnapshot(db.Model):
    __tablename__ = 'security_insight_snapshots'

    id = db.Column(db.String(36), primary_key=True)
    tenant_id = db.Column(db.String(36), db.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False, index=True)
    feature_key = db.Column(db.String(100), nullable=False, index=True)
    payload_json = db.Column(db.JSON)
    generated_by = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    generator = db.relationship('User', foreign_keys=[generated_by])

    def to_dict(self):
        return {
            'id': self.id,
            'tenantId': self.tenant_id,
            'featureKey': self.feature_key,
            'payload': self.payload_json or {},
            'generatedBy': self.generated_by,
            'generatorName': self.generator.name if self.generator else None,
            'createdAt': iso(self.created_at),
        }


class ContinuousFeedbackEntry(db.Model):
    __tablename__ = 'continuous_feedback_entries'

    id = db.Column(db.String(36), primary_key=True)
    tenant_id = db.Column(db.String(36), db.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False, index=True)
    employee_id = db.Column(db.String(36), db.ForeignKey('employees.id', ondelete='CASCADE'), nullable=False, index=True)
    giver_id = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True)
    feedback_type = db.Column(db.String(40), default='coaching', nullable=False, index=True)
    category = db.Column(db.String(80), default='')
    content = db.Column(db.Text, nullable=False)
    visibility = db.Column(db.String(40), default='manager', nullable=False, index=True)
    sentiment = db.Column(db.String(30), default='neutral', nullable=False, index=True)
    status = db.Column(db.String(40), default='open', nullable=False, index=True)
    handler_id = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True)
    handler_reply = db.Column(db.Text)
    handled_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    employee = db.relationship('Employee', foreign_keys=[employee_id])
    giver = db.relationship('User', foreign_keys=[giver_id])
    handler = db.relationship('User', foreign_keys=[handler_id])

    def to_dict(self):
        return {
            'id': self.id,
            'tenantId': self.tenant_id,
            'employeeId': self.employee_id,
            'employeeName': self.employee.name if self.employee else None,
            'giverId': self.giver_id,
            'giverName': self.giver.name if self.giver else None,
            'feedbackType': self.feedback_type,
            'category': self.category or '',
            'content': self.content,
            'visibility': self.visibility,
            'sentiment': self.sentiment,
            'status': self.status,
            'handlerId': self.handler_id,
            'handlerName': self.handler.name if self.handler else None,
            'handlerReply': self.handler_reply,
            'handledAt': iso(self.handled_at),
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
        }


class TenantDataRequest(db.Model):
    __tablename__ = 'tenant_data_requests'

    id = db.Column(db.String(36), primary_key=True)
    tenant_id = db.Column(db.String(36), db.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False, index=True)
    request_type = db.Column(db.String(30), nullable=False, index=True)
    requester = db.Column(db.String(160), nullable=False)
    scope = db.Column(db.String(240), nullable=False)
    status = db.Column(db.String(40), default='requested', nullable=False, index=True)
    evidence_ref = db.Column(db.String(240))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    tenant = db.relationship('Tenant', foreign_keys=[tenant_id])

    def to_dict(self):
        return {
            'id': self.id,
            'tenantId': self.tenant_id,
            'requestType': self.request_type,
            'requester': self.requester,
            'scope': self.scope,
            'status': self.status,
            'evidenceRef': self.evidence_ref,
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
        }


class AIProviderConfig(db.Model):
    __tablename__ = 'ai_provider_configs'

    id = db.Column(db.String(36), primary_key=True)
    provider_code = db.Column(db.String(60), unique=True, nullable=False, index=True)
    display_name = db.Column(db.String(120), nullable=False)
    base_url = db.Column(db.String(240), nullable=False)
    api_key_encrypted = db.Column(db.Text)
    api_key_mask = db.Column(db.String(80))
    enabled = db.Column(db.Boolean, default=True, nullable=False)
    settings_json = db.Column(db.JSON, default=dict)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self, include_secret=False):
        public_settings = {
            key: value
            for key, value in (self.settings_json or {}).items()
            if not str(key).startswith('_')
        }
        data = {
            'id': self.id,
            'providerCode': self.provider_code,
            'displayName': self.display_name,
            'baseUrl': self.base_url,
            'hasApiKey': bool(self.api_key_encrypted),
            'maskedApiKey': self.api_key_mask,
            'enabled': bool(self.enabled),
            'settings': public_settings,
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
        }
        if include_secret:
            data['apiKeyEncrypted'] = self.api_key_encrypted
        return data


class ExtensionProviderConfig(db.Model):
    __tablename__ = 'extension_provider_configs'

    id = db.Column(db.String(36), primary_key=True)
    provider_type = db.Column(db.String(80), nullable=False, index=True)
    provider_code = db.Column(db.String(80), unique=True, nullable=False, index=True)
    status = db.Column(db.String(30), default='unconfigured', nullable=False, index=True)
    config_json = db.Column(db.JSON, default=dict)
    secret_ref = db.Column(db.String(160))
    last_tested_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'providerType': self.provider_type,
            'providerCode': self.provider_code,
            'status': self.status,
            'configJson': self.config_json or {},
            'secretRef': self.secret_ref or '',
            'lastTestedAt': iso(self.last_tested_at),
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
        }


class AIModelPolicy(db.Model):
    __tablename__ = 'ai_model_policies'
    __table_args__ = (
        db.UniqueConstraint('resource_domain', 'use_case', 'tier', name='uq_ai_model_policy_scope'),
    )

    id = db.Column(db.String(36), primary_key=True)
    resource_domain = db.Column(db.String(80), nullable=False, index=True)
    use_case = db.Column(db.String(120), default='*', nullable=False, index=True)
    tier = db.Column(db.String(40), default='standard', nullable=False, index=True)
    provider_code = db.Column(db.String(60), nullable=False, index=True)
    model_profile = db.Column(db.String(40), default='standard')
    model = db.Column(db.String(160), nullable=False)
    max_tokens = db.Column(db.Integer, default=1200)
    enabled = db.Column(db.Boolean, default=True, nullable=False)
    settings_json = db.Column(db.JSON, default=dict)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self, tenant_safe=False):
        data = {
            'id': self.id,
            'resourceDomain': self.resource_domain,
            'useCase': self.use_case,
            'tier': self.tier,
            'modelProfile': self.model_profile,
            'maxTokens': self.max_tokens,
            'enabled': bool(self.enabled),
            'settings': self.settings_json or {},
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
        }
        if not tenant_safe:
            data.update({
                'providerCode': self.provider_code,
                'model': self.model,
            })
        return data


class AIAddonPackage(db.Model):
    __tablename__ = 'ai_addon_packages'

    code = db.Column(db.String(80), primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    resource_domains = db.Column(db.JSON, default=list)
    allowed_tiers = db.Column(db.JSON, default=list)
    default_monthly_quota = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    settings_json = db.Column(db.JSON, default=dict)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'code': self.code,
            'name': self.name,
            'resourceDomains': self.resource_domains or [],
            'allowedTiers': self.allowed_tiers or [],
            'defaultMonthlyQuota': self.default_monthly_quota,
            'isActive': bool(self.is_active),
            'settings': self.settings_json or {},
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
        }


class TenantAIEntitlement(db.Model):
    __tablename__ = 'tenant_ai_entitlements'
    __table_args__ = (
        db.UniqueConstraint('tenant_id', 'resource_domain', name='uq_tenant_ai_entitlement_domain'),
    )

    id = db.Column(db.String(36), primary_key=True)
    tenant_id = db.Column(db.String(36), db.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False, index=True)
    resource_domain = db.Column(db.String(80), nullable=False, index=True)
    package_code = db.Column(db.String(80), db.ForeignKey('ai_addon_packages.code', ondelete='SET NULL'), nullable=True, index=True)
    enabled = db.Column(db.Boolean, default=False, nullable=False)
    selected_tier = db.Column(db.String(40), default='standard')
    allowed_tiers = db.Column(db.JSON, default=list)
    monthly_quota = db.Column(db.Integer, default=0)
    period_key = db.Column(db.String(20))
    settings_json = db.Column(db.JSON, default=dict)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    tenant = db.relationship('Tenant', foreign_keys=[tenant_id])
    package = db.relationship('AIAddonPackage', foreign_keys=[package_code])

    def used_this_month(self) -> int:
        month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return AIUsageLedger.query.filter(
            AIUsageLedger.tenant_id == self.tenant_id,
            AIUsageLedger.resource_domain == self.resource_domain,
            AIUsageLedger.created_at >= month_start,
        ).count()

    def to_dict(self, tenant_safe=False):
        used = self.used_this_month()
        quota = self.monthly_quota
        remaining = None if quota is None or quota < 0 else max(quota - used, 0)
        data = {
            'id': self.id,
            'tenantId': self.tenant_id,
            'resourceDomain': self.resource_domain,
            'packageCode': self.package_code,
            'enabled': bool(self.enabled),
            'selectedTier': self.selected_tier,
            'allowedTiers': self.allowed_tiers or [],
            'monthlyQuota': quota,
            'usedThisMonth': used,
            'remainingQuota': remaining,
            'settings': self.settings_json or {},
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
        }
        if self.package:
            data['packageName'] = self.package.name
        if tenant_safe:
            data.pop('settings', None)
        return data


class AIUsageLedger(db.Model):
    __tablename__ = 'ai_usage_ledger'

    id = db.Column(db.String(36), primary_key=True)
    tenant_id = db.Column(db.String(36), db.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=True, index=True)
    user_id = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True)
    resource_domain = db.Column(db.String(80), nullable=False, index=True)
    use_case = db.Column(db.String(120), index=True)
    tier = db.Column(db.String(40), index=True)
    provider = db.Column(db.String(60), index=True)
    model = db.Column(db.String(160), index=True)
    status = db.Column(db.String(30), default='success', nullable=False, index=True)
    units = db.Column(db.Integer, default=1, nullable=False)
    request_id = db.Column(db.String(64), index=True)
    assistant_audit_log_id = db.Column(db.String(36), db.ForeignKey('assistant_audit_logs.id', ondelete='SET NULL'), nullable=True, index=True)
    prompt_chars = db.Column(db.Integer, default=0)
    response_chars = db.Column(db.Integer, default=0)
    error_code = db.Column(db.String(80))
    meta_json = db.Column(db.JSON, default=dict)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    tenant = db.relationship('Tenant', foreign_keys=[tenant_id])
    user = db.relationship('User', foreign_keys=[user_id])

    def to_dict(self, tenant_safe=False):
        data = {
            'id': self.id,
            'tenantId': self.tenant_id,
            'userId': self.user_id,
            'userName': self.user.name if self.user else None,
            'resourceDomain': self.resource_domain,
            'useCase': self.use_case,
            'tier': self.tier,
            'status': self.status,
            'units': self.units,
            'requestId': self.request_id,
            'promptChars': self.prompt_chars,
            'responseChars': self.response_chars,
            'errorCode': self.error_code,
            'createdAt': iso(self.created_at),
        }
        if not tenant_safe:
            data.update({
                'provider': self.provider,
                'model': self.model,
                'assistantAuditLogId': self.assistant_audit_log_id,
                'meta': self.meta_json or {},
            })
        return data


class EnterpriseServiceApplication(db.Model):
    """Public enterprise-service lead managed only by platform operators."""

    __tablename__ = 'enterprise_service_applications'
    __table_args__ = (
        db.Index('idx_enterprise_application_status_created', 'status', 'created_at'),
        db.Index('idx_enterprise_application_mobile_created', 'mobile', 'created_at'),
        db.Index('idx_enterprise_application_owner_status', 'owner_user_id', 'status'),
    )

    id = db.Column(db.String(36), primary_key=True)
    application_no = db.Column(db.String(32), nullable=False, unique=True, index=True)
    company_name = db.Column(db.String(120), nullable=False)
    contact_name = db.Column(db.String(60), nullable=False)
    mobile = db.Column(db.String(30), nullable=False)
    email = db.Column(db.String(255))
    region = db.Column(db.String(80))
    employee_scale = db.Column(db.String(30))
    interested_modules = db.Column(db.JSON, default=list)
    preferred_contact_time = db.Column(db.String(40))
    notes = db.Column(db.Text)
    status = db.Column(db.String(20), nullable=False, default='new', index=True)
    owner_user_id = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True)
    follow_up_at = db.Column(db.DateTime)
    internal_note = db.Column(db.Text)
    converted_tenant_id = db.Column(db.String(36), db.ForeignKey('tenants.id', ondelete='SET NULL'), nullable=True, index=True)
    source = db.Column(db.String(40), nullable=False, default='enterprise_application')
    request_id = db.Column(db.String(64), index=True)
    idempotency_key = db.Column(db.String(128), nullable=False, unique=True, index=True)
    fingerprint_hash = db.Column(db.String(64), nullable=False, index=True)
    consent_at = db.Column(db.DateTime, nullable=False)
    version = db.Column(db.Integer, nullable=False, default=1)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __mapper_args__ = {'version_id_col': version}

    owner = db.relationship('User', foreign_keys=[owner_user_id])
    converted_tenant = db.relationship('Tenant', foreign_keys=[converted_tenant_id])

    def to_public_dict(self):
        return {
            'applicationNo': self.application_no,
            # The public receipt is immutable even after internal qualification,
            # closure, or tenant linkage.
            'status': 'new',
        }

    def to_platform_list_dict(self):
        """PII-minimized row used by search and pagination responses."""
        return {
            'id': self.id,
            'applicationNo': self.application_no,
            'companyName': self.company_name,
            'region': self.region,
            'employeeScale': self.employee_scale,
            'status': self.status,
            'ownerUserId': self.owner_user_id,
            'ownerName': self.owner.name if self.owner else None,
            'convertedTenantId': self.converted_tenant_id,
            'convertedTenantName': self.converted_tenant.name if self.converted_tenant else None,
            'source': self.source,
            'version': self.version,
            'createdAt': utc_iso(self.created_at),
            'updatedAt': utc_iso(self.updated_at),
        }

    def to_platform_detail_dict(self, *, timeline=None):
        data = self.to_platform_list_dict()
        data.update({
            'contactName': self.contact_name,
            'mobile': self.mobile,
            'email': self.email,
            'interestedModules': self.interested_modules or [],
            'preferredContactTime': self.preferred_contact_time,
            'notes': self.notes,
            'followUpAt': utc_iso(self.follow_up_at),
            'internalNote': self.internal_note,
            'consentAt': utc_iso(self.consent_at),
            'timeline': timeline or [],
        })
        return data

    def to_platform_dict(self, *, include_notes=False):
        return self.to_platform_detail_dict() if include_notes else self.to_platform_list_dict()


class EnterpriseApplicationSubmissionGuard(db.Model):
    """One transactional cooldown claim per pseudonymous public source."""

    __tablename__ = 'enterprise_application_submission_guards'

    fingerprint_hash = db.Column(db.String(64), primary_key=True)
    active_idempotency_hash = db.Column(db.String(128), nullable=False)
    application_id = db.Column(
        db.String(36),
        db.ForeignKey('enterprise_service_applications.id', ondelete='SET NULL'),
        nullable=True,
        index=True,
    )
    claim_token = db.Column(db.String(36), nullable=False)
    cooldown_until = db.Column(db.DateTime, nullable=False, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class PlatformOperationLog(db.Model):
    __tablename__ = 'platform_operation_logs'

    id = db.Column(db.String(36), primary_key=True)
    user_id = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True)
    tenant_id = db.Column(db.String(36), db.ForeignKey('tenants.id', ondelete='SET NULL'), nullable=True, index=True)
    action = db.Column(db.String(80), nullable=False, index=True)
    target_type = db.Column(db.String(80))
    target_id = db.Column(db.String(80))
    # ``result``/``request_id``/``error_code`` make high-risk platform
    # operations auditable without putting secrets or free-form exception text
    # into the detail JSON.  Defaults keep legacy callers and rows compatible.
    result = db.Column(db.String(20), nullable=False, default='success', index=True)
    request_id = db.Column(db.String(64), index=True)
    error_code = db.Column(db.String(80), index=True)
    detail = db.Column(db.JSON)
    ip_address = db.Column(db.String(64))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    user = db.relationship('User', foreign_keys=[user_id])
    tenant = db.relationship('Tenant', foreign_keys=[tenant_id])

    def to_dict(self):
        return {
            'id': self.id,
            'userId': self.user_id,
            'userName': self.user.name if self.user else None,
            'tenantId': self.tenant_id,
            'tenantName': self.tenant.name if self.tenant else None,
            'action': self.action,
            'targetType': self.target_type,
            'targetId': self.target_id,
            'result': self.result,
            'requestId': self.request_id,
            'errorCode': self.error_code,
            'detail': self.detail or {},
            'ipAddress': self.ip_address,
            'createdAt': iso(self.created_at),
        }


def user_name(user_id):
    if not user_id:
        return None
    cache_key = f'u:{user_id}'
    if cache_key in _name_cache:
        return _name_cache[cache_key]
    user = User.query.get(user_id)
    name = user.name if user else None
    _name_cache[cache_key] = name
    return name


def employee_name(employee_id):
    if not employee_id:
        return None
    cache_key = f'e:{employee_id}'
    if cache_key in _name_cache:
        return _name_cache[cache_key]
    employee = Employee.query.get(employee_id)
    name = employee.name if employee else None
    _name_cache[cache_key] = name
    return name


class User(db.Model):
    __tablename__ = 'users'
    __table_args__ = (
        db.UniqueConstraint('tenant_id', 'email', name='uq_users_tenant_email'),
    )

    id = db.Column(db.String(36), primary_key=True)
    tenant_id = db.Column(db.String(36), db.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=True, index=True)
    email = db.Column(db.String(255), nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    role = db.Column(db.String(40), default='recruiter', index=True)
    phone = db.Column(db.String(30))
    status = db.Column(db.String(20), default='active', index=True)
    department_id = db.Column(db.String(36), nullable=True)
    employee_id = db.Column(db.String(36), nullable=True)
    must_change_password = db.Column(db.Boolean, default=False, nullable=False)
    last_login_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'accountType': 'hrm',
            'tenantId': getattr(self, 'tenant_id', None),
            'email': self.email,
            'name': self.name,
            'role': self.role,
            'phone': self.phone,
            'status': self.status,
            'departmentId': self.department_id,
            'employeeId': self.employee_id,
            'mustChangePassword': bool(self.must_change_password),
            'lastLoginAt': iso(self.last_login_at),
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
        }


class Job(db.Model):
    __tablename__ = 'jobs'

    id = db.Column(db.String(36), primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    status = db.Column(db.String(20), default='open', index=True)
    scoring_criteria = db.Column(db.JSON)
    category = db.Column(db.String(100))
    department = db.Column(db.String(100))
    department_id = db.Column(db.String(36), db.ForeignKey('departments.id', ondelete='SET NULL'), nullable=True, index=True)
    level = db.Column(db.String(50))
    responsibilities = db.Column(db.Text)
    requirements = db.Column(db.Text)
    headcount = db.Column(db.Integer, default=1)
    location = db.Column(db.String(120))
    employment_type = db.Column(db.String(50), default='全职')
    salary_range = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    created_by = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='SET NULL'))
    updated_by = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='SET NULL'))

    creator = db.relationship('User', foreign_keys=[created_by])
    updater = db.relationship('User', foreign_keys=[updated_by])
    department_ref = db.relationship('Department', foreign_keys=[department_id])

    resumes = db.relationship('Resume', backref='job', lazy='dynamic')
    interviews = db.relationship('Interview', backref='job', lazy='dynamic')

    def to_dict(self):
        return {
            'id': self.id,
            'title': self.title,
            'description': self.description,
            'status': self.status,
            'scoringCriteria': self.scoring_criteria,
            'category': self.category,
            'department': self.department or (self.department_ref.name if self.department_ref else None),
            'departmentId': self.department_id,
            'level': self.level,
            'responsibilities': self.responsibilities,
            'requirements': self.requirements,
            'headcount': self.headcount,
            'location': self.location,
            'employmentType': self.employment_type,
            'salaryRange': self.salary_range,
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
            'createdBy': self.created_by,
            'createdByName': self.creator.name if self.creator else None,
            'updatedBy': self.updated_by,
            'updatedByName': self.updater.name if self.updater else None,
        }


class Resume(db.Model):
    __tablename__ = 'resumes'

    id = db.Column(db.String(36), primary_key=True)
    job_id = db.Column(db.String(36), db.ForeignKey('jobs.id', ondelete='SET NULL'), nullable=True, index=True)
    candidate_name = db.Column(db.String(100), nullable=False, index=True)
    email = db.Column(db.String(255))
    phone = db.Column(db.String(20))
    file_url = db.Column(db.String(500), nullable=False)
    ai_analysis = db.Column(db.JSON)
    tags = db.Column(db.JSON)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='SET NULL'))
    updated_by = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='SET NULL'))

    creator = db.relationship('User', foreign_keys=[created_by])
    updater = db.relationship('User', foreign_keys=[updated_by])
    interviews = db.relationship('Interview', backref='resume', lazy='dynamic')

    def to_dict(self):
        return {
            'id': self.id,
            'jobId': self.job_id,
            'candidateName': self.candidate_name,
            'email': self.email,
            'phone': self.phone,
            'fileUrl': self.file_url,
            'aiAnalysis': self.ai_analysis,
            'tags': self.tags or [],
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
            'createdBy': self.created_by,
            'createdByName': self.creator.name if self.creator else None,
            'updatedBy': self.updated_by,
            'updatedByName': self.updater.name if self.updater else None,
        }


class Interview(db.Model):
    __tablename__ = 'interviews'

    id = db.Column(db.String(36), primary_key=True)
    job_id = db.Column(db.String(36), db.ForeignKey('jobs.id', ondelete='SET NULL'), nullable=True, index=True)
    resume_id = db.Column(db.String(36), db.ForeignKey('resumes.id', ondelete='SET NULL'), nullable=True, index=True)
    scheduled_at = db.Column(db.DateTime, nullable=False, index=True)
    status = db.Column(db.String(20), default='scheduled', index=True)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='SET NULL'))
    updated_by = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='SET NULL'))

    creator = db.relationship('User', foreign_keys=[created_by])
    updater = db.relationship('User', foreign_keys=[updated_by])

    feedback_text = db.Column(db.Text)
    feedback_file_url = db.Column(db.String(500))
    feedback_file_type = db.Column(db.String(20))
    ai_analysis = db.Column(db.JSON)
    analyzed_at = db.Column(db.DateTime)

    room_id = db.Column(db.String(64), unique=True)
    invite_token = db.Column(db.String(64), unique=True)
    recording_url = db.Column(db.String(500))
    transcript = db.Column(db.Text)
    meeting_started_at = db.Column(db.DateTime)
    meeting_ended_at = db.Column(db.DateTime)

    def to_dict(self):
        return {
            'id': self.id,
            'jobId': self.job_id,
            'resumeId': self.resume_id,
            'scheduledAt': iso(self.scheduled_at),
            'status': self.status,
            'notes': self.notes,
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
            'createdBy': self.created_by,
            'createdByName': self.creator.name if self.creator else None,
            'updatedBy': self.updated_by,
            'updatedByName': self.updater.name if self.updater else None,
            'feedbackText': self.feedback_text,
            'feedbackFileUrl': self.feedback_file_url,
            'feedbackFileType': self.feedback_file_type,
            'aiAnalysis': self.ai_analysis,
            'analyzedAt': iso(self.analyzed_at),
            'roomId': self.room_id,
            'inviteToken': self.invite_token,
            'recordingUrl': self.recording_url,
            'transcript': self.transcript,
            'meetingStartedAt': iso(self.meeting_started_at),
            'meetingEndedAt': iso(self.meeting_ended_at),
        }


class Setting(db.Model):
    __tablename__ = 'settings'

    key = db.Column(db.String(100), primary_key=True)
    value = db.Column(db.JSON, nullable=False)

    def to_dict(self):
        return {self.key: self.value}


class TenantSetting(db.Model):
    __tablename__ = 'tenant_settings'
    __table_args__ = (
        db.UniqueConstraint('tenant_id', 'key', name='uq_tenant_settings_tenant_key'),
    )

    id = db.Column(db.String(36), primary_key=True)
    tenant_id = db.Column(db.String(36), db.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False, index=True)
    key = db.Column(db.String(100), nullable=False, index=True)
    value = db.Column(db.JSON, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {self.key: self.value}


class AssistantAuditLog(db.Model):
    __tablename__ = 'assistant_audit_logs'

    id = db.Column(db.String(36), primary_key=True)
    user_id = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True)
    session_id = db.Column(db.String(36), db.ForeignKey('assistant_sessions.id', ondelete='SET NULL'), nullable=True, index=True)
    request_id = db.Column(db.String(64), index=True)
    provider = db.Column(db.String(40), default='siliconflow')
    model = db.Column(db.String(120), index=True)
    model_profile = db.Column(db.String(40), index=True)
    use_case = db.Column(db.String(80), index=True)
    resource_domain = db.Column(db.String(80), index=True)
    ai_tier = db.Column(db.String(40), index=True)
    tenant_ai_entitlement_id = db.Column(db.String(36), db.ForeignKey('tenant_ai_entitlements.id', ondelete='SET NULL'), nullable=True, index=True)
    prompt_messages = db.Column(db.JSON)
    response_content = db.Column(db.Text)
    response_meta = db.Column(db.JSON)
    status = db.Column(db.String(30), default='success', index=True)
    error_message = db.Column(db.Text)
    latency_ms = db.Column(db.Integer)
    prompt_chars = db.Column(db.Integer, default=0)
    response_chars = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    user = db.relationship('User', foreign_keys=[user_id])
    session = db.relationship('AssistantSession', foreign_keys=[session_id])

    def to_dict(self, include_payload=False):
        data = {
            'id': self.id,
            'userId': self.user_id,
            'userName': self.user.name if self.user else None,
            'sessionId': self.session_id,
            'sessionTitle': self.session.title if self.session else None,
            'requestId': self.request_id,
            'provider': self.provider,
            'model': self.model,
            'modelProfile': self.model_profile,
            'useCase': self.use_case,
            'resourceDomain': self.resource_domain,
            'aiTier': self.ai_tier,
            'tenantAiEntitlementId': self.tenant_ai_entitlement_id,
            'status': self.status,
            'errorMessage': self.error_message,
            'latencyMs': self.latency_ms,
            'promptChars': self.prompt_chars,
            'responseChars': self.response_chars,
            'createdAt': iso(self.created_at),
        }
        meta = self.response_meta if isinstance(self.response_meta, dict) else {}
        if meta:
            data['responseMeta'] = {
                key: meta.get(key)
                for key in (
                    'stream', 'chunkCount', 'finishReason',
                    'deterministicReceipt', 'skippedProviderCall', 'reason',
                    'runtimeSource',
                )
                if key in meta
            }
        if include_payload:
            data['promptMessages'] = self.prompt_messages or []
            data['responseContent'] = self.response_content or ''
            data['responseMeta'] = self.response_meta or {}
        return data


class AssistantEvaluationRun(db.Model):
    __tablename__ = 'assistant_evaluation_runs'

    id = db.Column(db.String(36), primary_key=True)
    case_id = db.Column(db.String(120), nullable=False, index=True)
    question = db.Column(db.Text, nullable=False)
    scenario = db.Column(db.String(120), index=True)
    user_id = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True)
    session_id = db.Column(db.String(36), db.ForeignKey('assistant_sessions.id', ondelete='SET NULL'), nullable=True, index=True)
    request_id = db.Column(db.String(64), index=True)
    expected_modules = db.Column(db.JSON)
    expected_render_blocks = db.Column(db.JSON)
    expected_behavior = db.Column(db.JSON)
    actual_modules = db.Column(db.JSON)
    actual_render_blocks = db.Column(db.JSON)
    used_tools = db.Column(db.JSON)
    evidence_sources = db.Column(db.JSON)
    entity_summary = db.Column(db.JSON)
    time_range = db.Column(db.JSON)
    response_summary = db.Column(db.Text)
    pass_status = db.Column(db.String(30), default='needs_review', index=True)
    failure_categories = db.Column(db.JSON)
    missing_expectations = db.Column(db.JSON)
    root_cause_notes = db.Column(db.Text)
    fix_plan = db.Column(db.Text)
    retest_of = db.Column(db.String(64), nullable=True, index=True)
    response_payload = db.Column(db.JSON)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    user = db.relationship('User', foreign_keys=[user_id])
    session = db.relationship('AssistantSession', foreign_keys=[session_id])

    def to_dict(self, include_payload=False):
        payload = self.response_payload or {}
        raw_evaluation_audit = payload.get('evaluationAudit') if isinstance(payload, dict) else None
        evaluation_audit = {}
        if isinstance(raw_evaluation_audit, dict):
            evaluation_audit = {
                'requestId': raw_evaluation_audit.get('requestId'),
                'useCase': raw_evaluation_audit.get('useCase'),
                'provider': raw_evaluation_audit.get('provider'),
                'modelProfile': raw_evaluation_audit.get('modelProfile'),
                'model': raw_evaluation_audit.get('model'),
                'status': raw_evaluation_audit.get('status'),
                'ok': raw_evaluation_audit.get('ok'),
                'auditRecorded': raw_evaluation_audit.get('auditRecorded'),
                'auditLogId': raw_evaluation_audit.get('auditLogId'),
                'latencyMs': raw_evaluation_audit.get('latencyMs'),
                'promptChars': raw_evaluation_audit.get('promptChars'),
                'responseChars': raw_evaluation_audit.get('responseChars'),
                'error': raw_evaluation_audit.get('error'),
            }
        data = {
            'id': self.id,
            'caseId': self.case_id,
            'question': self.question,
            'scenario': self.scenario,
            'userId': self.user_id,
            'userName': self.user.name if self.user else None,
            'sessionId': self.session_id,
            'sessionTitle': self.session.title if self.session else None,
            'requestId': self.request_id,
            'expectedModules': self.expected_modules or [],
            'expectedRenderBlocks': self.expected_render_blocks or [],
            'expectedBehavior': self.expected_behavior or [],
            'actualModules': self.actual_modules or [],
            'actualRenderBlocks': self.actual_render_blocks or [],
            'usedTools': self.used_tools or [],
            'evidenceSources': self.evidence_sources or [],
            'entitySummary': self.entity_summary or [],
            'timeRange': self.time_range or {},
            'responseSummary': self.response_summary or '',
            'passStatus': self.pass_status,
            'failureCategories': self.failure_categories or [],
            'missingExpectations': self.missing_expectations or [],
            'rootCauseNotes': self.root_cause_notes or '',
            'fixPlan': self.fix_plan or '',
            'retestOf': self.retest_of,
            'evaluationAudit': evaluation_audit,
            'createdAt': iso(self.created_at),
        }
        if include_payload:
            data['responsePayload'] = payload
        return data


class Department(db.Model):
    __tablename__ = 'departments'
    __table_args__ = (
        db.UniqueConstraint('tenant_id', 'code', name='uq_departments_tenant_code'),
    )

    id = db.Column(db.String(36), primary_key=True)
    tenant_id = db.Column(db.String(36), db.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=True, index=True)
    name = db.Column(db.String(100), nullable=False, index=True)
    short_name = db.Column(db.String(100))
    code = db.Column(db.String(50))
    parent_id = db.Column(db.String(36), db.ForeignKey('departments.id', ondelete='SET NULL'), nullable=True)
    manager_id = db.Column(db.String(36), nullable=True)
    status = db.Column(db.String(20), default='active', index=True)
    sort_order = db.Column(db.Integer, default=0)
    description = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='SET NULL'))
    updated_by = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='SET NULL'))

    parent = db.relationship('Department', remote_side=[id])
    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'shortName': self.short_name,
            'code': self.code,
            'parentId': self.parent_id,
            'parentName': self.parent.name if self.parent else None,
            'managerId': self.manager_id,
            'managerName': employee_name(self.manager_id),
            'status': self.status,
            'sortOrder': self.sort_order,
            'description': self.description,
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
        }


class Position(db.Model):
    __tablename__ = 'positions'

    id = db.Column(db.String(36), primary_key=True)
    title = db.Column(db.String(120), nullable=False, index=True)
    category = db.Column(db.String(100))
    department_id = db.Column(db.String(36), db.ForeignKey('departments.id', ondelete='SET NULL'), nullable=True, index=True)
    level = db.Column(db.String(50))
    responsibilities = db.Column(db.Text)
    requirements = db.Column(db.Text)
    status = db.Column(db.String(20), default='active', index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='SET NULL'))
    updated_by = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='SET NULL'))

    department = db.relationship('Department', foreign_keys=[department_id])

    def to_dict(self):
        return {
            'id': self.id,
            'title': self.title,
            'category': self.category,
            'departmentId': self.department_id,
            'departmentName': self.department.name if self.department else None,
            'level': self.level,
            'responsibilities': self.responsibilities,
            'requirements': self.requirements,
            'status': self.status,
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
        }


class Candidate(db.Model):
    __tablename__ = 'candidates'

    id = db.Column(db.String(36), primary_key=True)
    resume_id = db.Column(db.String(36), db.ForeignKey('resumes.id', ondelete='SET NULL'), nullable=True, index=True)
    job_id = db.Column(db.String(36), db.ForeignKey('jobs.id', ondelete='SET NULL'), nullable=True, index=True)
    name = db.Column(db.String(100), nullable=False, index=True)
    email = db.Column(db.String(255))
    phone = db.Column(db.String(30))
    status = db.Column(db.String(40), default='new', index=True)
    source = db.Column(db.String(80), default='简历上传')
    tags = db.Column(db.JSON)
    match_records = db.Column(db.JSON)
    ai_summary = db.Column(db.Text)
    ai_recommendation = db.Column(db.Text)
    owner_id = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='SET NULL'))
    remark = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    resume = db.relationship('Resume', foreign_keys=[resume_id])
    job = db.relationship('Job', foreign_keys=[job_id])
    owner = db.relationship('User', foreign_keys=[owner_id])

    def to_dict(self):
        return {
            'id': self.id,
            'resumeId': self.resume_id,
            'jobId': self.job_id,
            'jobTitle': self.job.title if self.job else None,
            'name': self.name,
            'email': self.email,
            'phone': self.phone,
            'status': self.status,
            'source': self.source,
            'tags': self.tags or [],
            'matchRecords': self.match_records or [],
            'aiSummary': self.ai_summary,
            'aiRecommendation': self.ai_recommendation,
            'ownerId': self.owner_id,
            'ownerName': self.owner.name if self.owner else None,
            'remark': self.remark,
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
        }


class Offer(db.Model):
    __tablename__ = 'offers'

    id = db.Column(db.String(36), primary_key=True)
    candidate_id = db.Column(db.String(36), db.ForeignKey('candidates.id', ondelete='SET NULL'), nullable=True, index=True)
    job_id = db.Column(db.String(36), db.ForeignKey('jobs.id', ondelete='SET NULL'), nullable=True, index=True)
    employee_id = db.Column(db.String(36), nullable=True)
    title = db.Column(db.String(200), nullable=False)
    salary = db.Column(db.Numeric(12, 2))
    salary_structure = db.Column(db.JSON)
    onboarding_date = db.Column(db.Date)
    work_location = db.Column(db.String(120))
    status = db.Column(db.String(40), default='draft', index=True)
    approval_status = db.Column(db.String(40), default='draft', index=True)
    accepted_at = db.Column(db.DateTime)
    rejected_at = db.Column(db.DateTime)
    remark = db.Column(db.Text)
    created_by = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='SET NULL'))
    updated_by = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='SET NULL'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    candidate = db.relationship('Candidate', foreign_keys=[candidate_id])
    job = db.relationship('Job', foreign_keys=[job_id])
    def to_dict(self):
        return {
            'id': self.id,
            'candidateId': self.candidate_id,
            'candidateName': self.candidate.name if self.candidate else None,
            'jobId': self.job_id,
            'jobTitle': self.job.title if self.job else None,
            'employeeId': self.employee_id,
            'employeeName': employee_name(self.employee_id),
            'title': self.title,
            'salary': money(self.salary),
            'salaryStructure': self.salary_structure or {},
            'onboardingDate': iso(self.onboarding_date),
            'workLocation': self.work_location,
            'status': self.status,
            'approvalStatus': self.approval_status,
            'acceptedAt': iso(self.accepted_at),
            'rejectedAt': iso(self.rejected_at),
            'remark': self.remark,
            'createdBy': self.created_by,
            'createdByName': user_name(self.created_by),
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
        }


class Employee(db.Model):
    __tablename__ = 'employees'
    __table_args__ = (
        db.UniqueConstraint('tenant_id', 'employee_no', name='uq_employees_tenant_employee_no'),
        db.UniqueConstraint('tenant_id', 'oa_person_id', name='uq_employees_tenant_oa_person_id'),
    )

    id = db.Column(db.String(36), primary_key=True)
    tenant_id = db.Column(db.String(36), db.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=True, index=True)
    employee_no = db.Column(db.String(50), index=True)
    name = db.Column(db.String(100), nullable=False, index=True)
    email = db.Column(db.String(255))
    phone = db.Column(db.String(30))
    gender = db.Column(db.String(20))
    status = db.Column(db.String(40), default='pending_onboard', index=True)
    department_id = db.Column(db.String(36), db.ForeignKey('departments.id', ondelete='SET NULL'), nullable=True, index=True)
    position_id = db.Column(db.String(36), db.ForeignKey('positions.id', ondelete='SET NULL'), nullable=True)
    job_title = db.Column(db.String(120))
    manager_id = db.Column(db.String(36), nullable=True)
    employee_type = db.Column(db.String(20))
    performance_participant = db.Column(db.Boolean, default=False)
    hire_date = db.Column(db.Date)
    probation_months = db.Column(db.Integer, default=3)
    probation_end_date = db.Column(db.Date)
    attachments = db.Column(db.JSON)
    source_offer_id = db.Column(db.String(36), nullable=True)
    candidate_id = db.Column(db.String(36), db.ForeignKey('candidates.id', ondelete='SET NULL'), nullable=True)
    id_no = db.Column(db.String(80))
    address = db.Column(db.String(255))
    emergency_contact = db.Column(db.String(255))
    remark = db.Column(db.Text)
    oa_person_id = db.Column(db.String(40), nullable=True, index=True)
    oa_login_id = db.Column(db.String(100), nullable=True, index=True)
    oa_workcode = db.Column(db.String(100), nullable=True)
    oa_status = db.Column(db.String(20), nullable=True)
    oa_department_id = db.Column(db.String(40), nullable=True)
    oa_jobtitle_id = db.Column(db.String(40), nullable=True)
    erp_user_id = db.Column(db.String(40), nullable=True, index=True)
    source_system = db.Column(db.String(40), nullable=True, index=True)
    source_modified_at = db.Column(db.DateTime, nullable=True)
    source_synced_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='SET NULL'))
    updated_by = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='SET NULL'))

    department = db.relationship('Department', foreign_keys=[department_id])
    position = db.relationship('Position', foreign_keys=[position_id])
    candidate = db.relationship('Candidate', foreign_keys=[candidate_id])

    def to_dict(self):
        return {
            'id': self.id,
            'employeeNo': self.employee_no,
            'name': self.name,
            'email': self.email,
            'phone': self.phone,
            'gender': self.gender,
            'status': self.status,
            'departmentId': self.department_id,
            'departmentName': self.department.name if self.department else None,
            'positionId': self.position_id,
            'positionTitle': self.position.title if self.position else None,
            'jobTitle': self.job_title,
            'managerId': self.manager_id,
            'managerName': employee_name(self.manager_id),
            'employeeType': self.employee_type,
            'performanceParticipant': bool(self.performance_participant),
            'hireDate': iso(self.hire_date),
            'probationMonths': self.probation_months,
            'probationEndDate': iso(self.probation_end_date),
            'attachments': self.attachments or [],
            'sourceOfferId': self.source_offer_id,
            'candidateId': self.candidate_id,
            'idNo': self.id_no,
            'address': self.address,
            'emergencyContact': self.emergency_contact,
            'remark': self.remark,
            'oaPersonId': self.oa_person_id,
            'oaLoginId': self.oa_login_id,
            'oaWorkcode': self.oa_workcode,
            'oaStatus': self.oa_status,
            'oaDepartmentId': self.oa_department_id,
            'oaJobtitleId': self.oa_jobtitle_id,
            'erpUserId': self.erp_user_id,
            'sourceSystem': self.source_system,
            'sourceModifiedAt': iso(self.source_modified_at),
            'sourceSyncedAt': iso(self.source_synced_at),
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
        }


class OaEmployeeSyncRun(db.Model):
    __tablename__ = 'oa_employee_sync_runs'

    id = db.Column(db.String(36), primary_key=True)
    tenant_id = db.Column(db.String(36), db.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False, index=True)
    trigger = db.Column(db.String(30), nullable=False, default='scheduled')
    status = db.Column(db.String(20), nullable=False, default='running', index=True)
    source_count = db.Column(db.Integer, nullable=False, default=0)
    created_count = db.Column(db.Integer, nullable=False, default=0)
    updated_count = db.Column(db.Integer, nullable=False, default=0)
    archived_count = db.Column(db.Integer, nullable=False, default=0)
    unchanged_count = db.Column(db.Integer, nullable=False, default=0)
    error_code = db.Column(db.String(80), nullable=True)
    error_message = db.Column(db.String(500), nullable=True)
    started_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    completed_at = db.Column(db.DateTime, nullable=True)

    def to_dict(self):
        return {
            'id': self.id,
            'trigger': self.trigger,
            'status': self.status,
            'sourceCount': self.source_count,
            'createdCount': self.created_count,
            'updatedCount': self.updated_count,
            'archivedCount': self.archived_count,
            'unchangedCount': self.unchanged_count,
            'errorCode': self.error_code,
            'errorMessage': self.error_message,
            'startedAt': iso(self.started_at),
            'completedAt': iso(self.completed_at),
        }


class EmployeePortalAccount(db.Model):
    """Login account for the employee portal.

    HRM management users live in ``users``. Regular employees use this separate
    account table and sign in with their employee number.
    """

    __tablename__ = 'employee_portal_accounts'

    id = db.Column(db.String(36), primary_key=True)
    employee_id = db.Column(db.String(36), db.ForeignKey('employees.id', ondelete='CASCADE'), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    status = db.Column(db.String(20), default='active', index=True)
    must_change_password = db.Column(db.Boolean, default=True, nullable=False)
    last_login_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    employee = db.relationship('Employee', foreign_keys=[employee_id])

    def to_dict(self):
        employee = self.employee
        return {
            'id': self.id,
            'accountType': 'employee_portal',
            'tenantId': getattr(self, 'tenant_id', None),
            'email': employee.email if employee else '',
            'name': employee.name if employee else '',
            'role': 'employee',
            'status': self.status,
            'departmentId': employee.department_id if employee else None,
            'employeeId': self.employee_id,
            'employeeNo': employee.employee_no if employee else None,
            'oaLoginId': employee.oa_login_id if employee else None,
            'loginIdentifier': (
                employee.employee_no or employee.oa_login_id or employee.oa_person_id
                if employee else None
            ),
            'mustChangePassword': bool(self.must_change_password),
            'lastLoginAt': iso(self.last_login_at),
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
        }


class OnboardingTask(db.Model):
    __tablename__ = 'onboarding_tasks'

    id = db.Column(db.String(36), primary_key=True)
    employee_id = db.Column(db.String(36), db.ForeignKey('employees.id', ondelete='CASCADE'), nullable=False, index=True)
    name = db.Column(db.String(200), nullable=False)
    task_type = db.Column(db.String(60), default='入职准备')
    owner_id = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='SET NULL'))
    due_date = db.Column(db.Date)
    status = db.Column(db.String(30), default='pending', index=True)
    materials = db.Column(db.JSON)
    remark = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    employee = db.relationship('Employee', foreign_keys=[employee_id])
    owner = db.relationship('User', foreign_keys=[owner_id])

    def to_dict(self):
        return {
            'id': self.id,
            'employeeId': self.employee_id,
            'employeeName': self.employee.name if self.employee else None,
            'name': self.name,
            'taskType': self.task_type,
            'ownerId': self.owner_id,
            'ownerName': self.owner.name if self.owner else None,
            'dueDate': iso(self.due_date),
            'status': self.status,
            'materials': self.materials or [],
            'remark': self.remark,
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
        }


class Probation(db.Model):
    __tablename__ = 'probations'

    id = db.Column(db.String(36), primary_key=True)
    employee_id = db.Column(db.String(36), db.ForeignKey('employees.id', ondelete='CASCADE'), nullable=False, index=True)
    apply_date = db.Column(db.Date)
    expected_regular_date = db.Column(db.Date)
    regular_date = db.Column(db.Date)
    status = db.Column(db.String(40), default='draft', index=True)
    self_review = db.Column(db.Text)
    manager_review = db.Column(db.Text)
    result = db.Column(db.String(60))
    approval_id = db.Column(db.String(36), db.ForeignKey('approval_instances.id', ondelete='SET NULL'))
    remark = db.Column(db.Text)
    created_by = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='SET NULL'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    employee = db.relationship('Employee', foreign_keys=[employee_id])

    def to_dict(self):
        return {
            'id': self.id,
            'employeeId': self.employee_id,
            'employeeName': self.employee.name if self.employee else None,
            'applyDate': iso(self.apply_date),
            'expectedRegularDate': iso(self.expected_regular_date),
            'regularDate': iso(self.regular_date),
            'status': self.status,
            'selfReview': self.self_review,
            'managerReview': self.manager_review,
            'result': self.result,
            'approvalId': self.approval_id,
            'remark': self.remark,
            'createdBy': self.created_by,
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
        }


class EmployeeChange(db.Model):
    __tablename__ = 'employee_changes'

    id = db.Column(db.String(36), primary_key=True)
    employee_id = db.Column(db.String(36), db.ForeignKey('employees.id', ondelete='CASCADE'), nullable=False, index=True)
    change_type = db.Column(db.String(50), nullable=False, index=True)
    before_department_id = db.Column(db.String(36), db.ForeignKey('departments.id', ondelete='SET NULL'))
    after_department_id = db.Column(db.String(36), db.ForeignKey('departments.id', ondelete='SET NULL'))
    before_position_id = db.Column(db.String(36), db.ForeignKey('positions.id', ondelete='SET NULL'))
    after_position_id = db.Column(db.String(36), db.ForeignKey('positions.id', ondelete='SET NULL'))
    before_level = db.Column(db.String(50))
    after_level = db.Column(db.String(50))
    salary_adjustment = db.Column(db.Numeric(12, 2))
    effective_date = db.Column(db.Date)
    reason = db.Column(db.Text)
    status = db.Column(db.String(40), default='effective', index=True)
    created_by = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='SET NULL'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    employee = db.relationship('Employee', foreign_keys=[employee_id])
    before_department = db.relationship('Department', foreign_keys=[before_department_id])
    after_department = db.relationship('Department', foreign_keys=[after_department_id])
    before_position = db.relationship('Position', foreign_keys=[before_position_id])
    after_position = db.relationship('Position', foreign_keys=[after_position_id])

    def to_dict(self):
        return {
            'id': self.id,
            'employeeId': self.employee_id,
            'employeeName': self.employee.name if self.employee else None,
            'changeType': self.change_type,
            'beforeDepartmentId': self.before_department_id,
            'beforeDepartmentName': self.before_department.name if self.before_department else None,
            'afterDepartmentId': self.after_department_id,
            'afterDepartmentName': self.after_department.name if self.after_department else None,
            'beforePositionId': self.before_position_id,
            'beforePositionTitle': self.before_position.title if self.before_position else None,
            'afterPositionId': self.after_position_id,
            'afterPositionTitle': self.after_position.title if self.after_position else None,
            'beforeLevel': self.before_level,
            'afterLevel': self.after_level,
            'salaryAdjustment': money(self.salary_adjustment),
            'effectiveDate': iso(self.effective_date),
            'reason': self.reason,
            'status': self.status,
            'createdBy': self.created_by,
            'createdAt': iso(self.created_at),
        }


class Resignation(db.Model):
    __tablename__ = 'resignations'

    id = db.Column(db.String(36), primary_key=True)
    employee_id = db.Column(db.String(36), db.ForeignKey('employees.id', ondelete='CASCADE'), nullable=False, index=True)
    apply_date = db.Column(db.Date)
    expected_leave_date = db.Column(db.Date)
    actual_leave_date = db.Column(db.Date)
    reason = db.Column(db.Text)
    handover_items = db.Column(db.JSON)
    status = db.Column(db.String(40), default='draft', index=True)
    approval_id = db.Column(db.String(36), db.ForeignKey('approval_instances.id', ondelete='SET NULL'))
    ai_summary = db.Column(db.Text)
    created_by = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='SET NULL'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    employee = db.relationship('Employee', foreign_keys=[employee_id])

    def to_dict(self):
        return {
            'id': self.id,
            'employeeId': self.employee_id,
            'employeeName': self.employee.name if self.employee else None,
            'applyDate': iso(self.apply_date),
            'expectedLeaveDate': iso(self.expected_leave_date),
            'actualLeaveDate': iso(self.actual_leave_date),
            'reason': self.reason,
            'handoverItems': self.handover_items or [],
            'status': self.status,
            'approvalId': self.approval_id,
            'aiSummary': self.ai_summary,
            'createdBy': self.created_by,
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
        }


class Contract(db.Model):
    __tablename__ = 'contracts'

    id = db.Column(db.String(36), primary_key=True)
    employee_id = db.Column(db.String(36), db.ForeignKey('employees.id', ondelete='CASCADE'), nullable=False, index=True)
    contract_no = db.Column(db.String(80), index=True)
    contract_type = db.Column(db.String(80), default='劳动合同')
    start_date = db.Column(db.Date)
    end_date = db.Column(db.Date)
    status = db.Column(db.String(40), default='active', index=True)
    reminder_date = db.Column(db.Date)
    attachment_url = db.Column(db.String(500))
    remark = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='SET NULL'))

    employee = db.relationship('Employee', foreign_keys=[employee_id])

    def to_dict(self):
        return {
            'id': self.id,
            'employeeId': self.employee_id,
            'employeeName': self.employee.name if self.employee else None,
            'contractNo': self.contract_no,
            'contractType': self.contract_type,
            'startDate': iso(self.start_date),
            'endDate': iso(self.end_date),
            'status': self.status,
            'reminderDate': iso(self.reminder_date),
            'attachmentUrl': self.attachment_url,
            'remark': self.remark,
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
        }


class AttendanceShift(db.Model):
    __tablename__ = 'attendance_shifts'
    __table_args__ = (
        db.UniqueConstraint('tenant_id', 'code', name='uq_attendance_shift_code'),
    )

    id = db.Column(db.String(36), primary_key=True)
    tenant_id = db.Column(db.String(36), db.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False, index=True)
    code = db.Column(db.String(40), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    shift_type = db.Column(db.String(20), nullable=False, default='fixed')
    start_time = db.Column(db.String(5), nullable=True)
    end_time = db.Column(db.String(5), nullable=True)
    cross_day = db.Column(db.Boolean, nullable=False, default=False)
    required_work_hours = db.Column(db.Numeric(6, 2), nullable=True)
    late_grace_minutes = db.Column(db.Integer, nullable=False, default=0)
    early_leave_grace_minutes = db.Column(db.Integer, nullable=False, default=0)
    require_check_in = db.Column(db.Boolean, nullable=False, default=True)
    require_check_out = db.Column(db.Boolean, nullable=False, default=True)
    status = db.Column(db.String(20), nullable=False, default='active', index=True)
    remark = db.Column(db.Text)
    created_by = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='SET NULL'))
    updated_by = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='SET NULL'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'tenantId': self.tenant_id,
            'code': self.code,
            'name': self.name,
            'shiftType': self.shift_type,
            'startTime': self.start_time,
            'endTime': self.end_time,
            'crossDay': bool(self.cross_day),
            'requiredWorkHours': money(self.required_work_hours),
            'lateGraceMinutes': self.late_grace_minutes or 0,
            'earlyLeaveGraceMinutes': self.early_leave_grace_minutes or 0,
            'requireCheckIn': bool(self.require_check_in),
            'requireCheckOut': bool(self.require_check_out),
            'status': self.status,
            'remark': self.remark,
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
        }


class AttendanceRuleSet(db.Model):
    __tablename__ = 'attendance_rule_sets'
    __table_args__ = (
        db.UniqueConstraint('tenant_id', 'code', name='uq_attendance_rule_set_code'),
    )

    id = db.Column(db.String(36), primary_key=True)
    tenant_id = db.Column(db.String(36), db.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False, index=True)
    code = db.Column(db.String(40), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    version = db.Column(db.Integer, nullable=False, default=1)
    effective_from = db.Column(db.Date, nullable=True)
    effective_to = db.Column(db.Date, nullable=True)
    status = db.Column(db.String(20), nullable=False, default='active', index=True)
    absent_if_no_punch = db.Column(db.Boolean, nullable=False, default=True)
    missing_card_if_single_punch = db.Column(db.Boolean, nullable=False, default=True)
    late_grace_minutes = db.Column(db.Integer, nullable=True)
    early_leave_grace_minutes = db.Column(db.Integer, nullable=True)
    remark = db.Column(db.Text)
    created_by = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='SET NULL'))
    updated_by = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='SET NULL'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'tenantId': self.tenant_id,
            'code': self.code,
            'name': self.name,
            'version': self.version or 1,
            'effectiveFrom': iso(self.effective_from),
            'effectiveTo': iso(self.effective_to),
            'status': self.status,
            'absentIfNoPunch': bool(self.absent_if_no_punch),
            'missingCardIfSinglePunch': bool(self.missing_card_if_single_punch),
            'lateGraceMinutes': self.late_grace_minutes,
            'earlyLeaveGraceMinutes': self.early_leave_grace_minutes,
            'remark': self.remark,
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
        }


class AttendanceGroup(db.Model):
    __tablename__ = 'attendance_groups'
    __table_args__ = (
        db.UniqueConstraint('tenant_id', 'code', name='uq_attendance_group_code'),
    )

    id = db.Column(db.String(36), primary_key=True)
    tenant_id = db.Column(db.String(36), db.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False, index=True)
    code = db.Column(db.String(40), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    default_shift_id = db.Column(db.String(36), db.ForeignKey('attendance_shifts.id', ondelete='SET NULL'), nullable=True, index=True)
    default_rule_set_id = db.Column(db.String(36), db.ForeignKey('attendance_rule_sets.id', ondelete='SET NULL'), nullable=True, index=True)
    priority = db.Column(db.Integer, nullable=False, default=0)
    effective_from = db.Column(db.Date, nullable=True)
    effective_to = db.Column(db.Date, nullable=True)
    status = db.Column(db.String(20), nullable=False, default='active', index=True)
    remark = db.Column(db.Text)
    created_by = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='SET NULL'))
    updated_by = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='SET NULL'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    default_shift = db.relationship('AttendanceShift', foreign_keys=[default_shift_id])
    default_rule_set = db.relationship('AttendanceRuleSet', foreign_keys=[default_rule_set_id])

    def to_dict(self):
        return {
            'id': self.id,
            'tenantId': self.tenant_id,
            'code': self.code,
            'name': self.name,
            'defaultShiftId': self.default_shift_id,
            'defaultShiftName': self.default_shift.name if self.default_shift else None,
            'defaultRuleSetId': self.default_rule_set_id,
            'defaultRuleSetName': self.default_rule_set.name if self.default_rule_set else None,
            'priority': self.priority or 0,
            'effectiveFrom': iso(self.effective_from),
            'effectiveTo': iso(self.effective_to),
            'status': self.status,
            'remark': self.remark,
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
        }


class AttendanceGroupMembership(db.Model):
    __tablename__ = 'attendance_group_memberships'

    id = db.Column(db.String(36), primary_key=True)
    tenant_id = db.Column(db.String(36), db.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False, index=True)
    group_id = db.Column(db.String(36), db.ForeignKey('attendance_groups.id', ondelete='CASCADE'), nullable=False, index=True)
    employee_id = db.Column(db.String(36), db.ForeignKey('employees.id', ondelete='CASCADE'), nullable=True, index=True)
    department_id = db.Column(db.String(36), db.ForeignKey('departments.id', ondelete='CASCADE'), nullable=True, index=True)
    position_id = db.Column(db.String(36), db.ForeignKey('positions.id', ondelete='CASCADE'), nullable=True, index=True)
    priority = db.Column(db.Integer, nullable=False, default=0)
    effective_from = db.Column(db.Date, nullable=True)
    effective_to = db.Column(db.Date, nullable=True)
    status = db.Column(db.String(20), nullable=False, default='active', index=True)
    remark = db.Column(db.Text)
    created_by = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='SET NULL'))
    updated_by = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='SET NULL'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    group = db.relationship('AttendanceGroup', foreign_keys=[group_id])
    employee = db.relationship('Employee', foreign_keys=[employee_id])
    department = db.relationship('Department', foreign_keys=[department_id])
    position = db.relationship('Position', foreign_keys=[position_id])

    def to_dict(self):
        scope_type = 'employee' if self.employee_id else 'department' if self.department_id else 'position' if self.position_id else None
        scope_name = None
        if self.employee:
            scope_name = self.employee.name
        elif self.department:
            scope_name = self.department.name
        elif self.position:
            scope_name = self.position.title
        return {
            'id': self.id,
            'tenantId': self.tenant_id,
            'groupId': self.group_id,
            'groupName': self.group.name if self.group else None,
            'employeeId': self.employee_id,
            'employeeName': self.employee.name if self.employee else None,
            'departmentId': self.department_id,
            'departmentName': self.department.name if self.department else None,
            'positionId': self.position_id,
            'positionTitle': self.position.title if self.position else None,
            'scopeType': scope_type,
            'scopeName': scope_name,
            'priority': self.priority or 0,
            'effectiveFrom': iso(self.effective_from),
            'effectiveTo': iso(self.effective_to),
            'status': self.status,
            'remark': self.remark,
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
        }


class AttendanceHoliday(db.Model):
    __tablename__ = 'attendance_holidays'
    __table_args__ = (
        db.UniqueConstraint('tenant_id', 'holiday_date', name='uq_attendance_holiday_date'),
    )

    id = db.Column(db.String(36), primary_key=True)
    tenant_id = db.Column(db.String(36), db.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False, index=True)
    holiday_date = db.Column(db.Date, nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    day_type = db.Column(db.String(20), nullable=False, default='holiday')
    status = db.Column(db.String(20), nullable=False, default='active', index=True)
    remark = db.Column(db.Text)
    created_by = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='SET NULL'))
    updated_by = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='SET NULL'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'tenantId': self.tenant_id,
            'holidayDate': iso(self.holiday_date),
            'name': self.name,
            'dayType': self.day_type,
            'status': self.status,
            'remark': self.remark,
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
        }


class AttendanceSchedule(db.Model):
    __tablename__ = 'attendance_schedules'
    __table_args__ = (
        db.UniqueConstraint('tenant_id', 'employee_id', 'schedule_date', name='uq_attendance_schedule_employee_date'),
    )

    id = db.Column(db.String(36), primary_key=True)
    tenant_id = db.Column(db.String(36), db.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False, index=True)
    employee_id = db.Column(db.String(36), db.ForeignKey('employees.id', ondelete='CASCADE'), nullable=False, index=True)
    schedule_date = db.Column(db.Date, nullable=False, index=True)
    day_type = db.Column(db.String(20), nullable=False, default='workday')
    shift_id = db.Column(db.String(36), db.ForeignKey('attendance_shifts.id', ondelete='SET NULL'), nullable=True, index=True)
    source = db.Column(db.String(30), nullable=False, default='manual')
    remark = db.Column(db.Text)
    created_by = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='SET NULL'))
    updated_by = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='SET NULL'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    employee = db.relationship('Employee', foreign_keys=[employee_id])
    shift = db.relationship('AttendanceShift', foreign_keys=[shift_id])

    def to_dict(self):
        return {
            'id': self.id,
            'tenantId': self.tenant_id,
            'employeeId': self.employee_id,
            'employeeName': self.employee.name if self.employee else None,
            'scheduleDate': iso(self.schedule_date),
            'dayType': self.day_type,
            'shiftId': self.shift_id,
            'shiftName': self.shift.name if self.shift else None,
            'source': self.source,
            'remark': self.remark,
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
        }


class AttendanceDailyResult(db.Model):
    __tablename__ = 'attendance_daily_results'
    __table_args__ = (
        db.UniqueConstraint('tenant_id', 'employee_id', 'attendance_date', name='uq_attendance_daily_employee_date'),
    )

    id = db.Column(db.String(36), primary_key=True)
    tenant_id = db.Column(db.String(36), db.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False, index=True)
    employee_id = db.Column(db.String(36), db.ForeignKey('employees.id', ondelete='CASCADE'), nullable=False, index=True)
    attendance_date = db.Column(db.Date, nullable=False, index=True)
    day_type = db.Column(db.String(20), nullable=False, default='workday')
    schedule_source = db.Column(db.String(30), nullable=True)
    group_id = db.Column(db.String(36), db.ForeignKey('attendance_groups.id', ondelete='SET NULL'), nullable=True, index=True)
    shift_id = db.Column(db.String(36), db.ForeignKey('attendance_shifts.id', ondelete='SET NULL'), nullable=True, index=True)
    rule_set_id = db.Column(db.String(36), db.ForeignKey('attendance_rule_sets.id', ondelete='SET NULL'), nullable=True, index=True)
    scheduled_start = db.Column(db.DateTime, nullable=True)
    scheduled_end = db.Column(db.DateTime, nullable=True)
    check_in_time = db.Column(db.DateTime, nullable=True)
    check_out_time = db.Column(db.DateTime, nullable=True)
    work_hours = db.Column(db.Numeric(6, 2), nullable=True)
    status = db.Column(db.String(30), nullable=False, default='unscheduled', index=True)
    late_minutes = db.Column(db.Integer, nullable=False, default=0)
    early_leave_minutes = db.Column(db.Integer, nullable=False, default=0)
    source_punch_ids = db.Column(db.JSON, default=list)
    request_ids = db.Column(db.JSON, default=list)
    explanation_json = db.Column(db.JSON, default=dict)
    calculation_version = db.Column(db.String(20), nullable=False, default='v1')
    calculated_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    employee = db.relationship('Employee', foreign_keys=[employee_id])
    group = db.relationship('AttendanceGroup', foreign_keys=[group_id])
    shift = db.relationship('AttendanceShift', foreign_keys=[shift_id])
    rule_set = db.relationship('AttendanceRuleSet', foreign_keys=[rule_set_id])

    def to_dict(self):
        return {
            'id': self.id,
            'tenantId': self.tenant_id,
            'employeeId': self.employee_id,
            'employeeName': self.employee.name if self.employee else None,
            'attendanceDate': iso(self.attendance_date),
            'dayType': self.day_type,
            'scheduleSource': self.schedule_source,
            'groupId': self.group_id,
            'groupName': self.group.name if self.group else None,
            'shiftId': self.shift_id,
            'shiftName': self.shift.name if self.shift else None,
            'ruleSetId': self.rule_set_id,
            'ruleSetName': self.rule_set.name if self.rule_set else None,
            'scheduledStart': iso(self.scheduled_start),
            'scheduledEnd': iso(self.scheduled_end),
            'checkInTime': iso(self.check_in_time),
            'checkOutTime': iso(self.check_out_time),
            'workHours': money(self.work_hours),
            'status': self.status,
            'lateMinutes': self.late_minutes or 0,
            'earlyLeaveMinutes': self.early_leave_minutes or 0,
            'sourcePunchIds': self.source_punch_ids or [],
            'requestIds': self.request_ids or [],
            'explanation': self.explanation_json or {},
            'calculationVersion': self.calculation_version,
            'calculatedAt': iso(self.calculated_at),
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
        }


class AttendanceRecord(db.Model):
    __tablename__ = 'attendance_records'

    id = db.Column(db.String(36), primary_key=True)
    employee_id = db.Column(db.String(36), db.ForeignKey('employees.id', ondelete='CASCADE'), nullable=False, index=True)
    attendance_date = db.Column(db.Date, nullable=False, index=True)
    check_in_time = db.Column(db.DateTime)
    check_out_time = db.Column(db.DateTime)
    work_hours = db.Column(db.Numeric(6, 2))
    status = db.Column(db.String(40), default='normal', index=True)
    source = db.Column(db.String(60), default='manual')
    remark = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='SET NULL'))

    employee = db.relationship('Employee', foreign_keys=[employee_id])

    def to_dict(self):
        return {
            'id': self.id,
            'employeeId': self.employee_id,
            'employeeName': self.employee.name if self.employee else None,
            'attendanceDate': iso(self.attendance_date),
            'checkInTime': iso(self.check_in_time),
            'checkOutTime': iso(self.check_out_time),
            'workHours': money(self.work_hours),
            'status': self.status,
            'source': self.source,
            'remark': self.remark,
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
        }


class AttendanceTimesheet(db.Model):
    """基于 OA 排班 + 打卡机打出的日考勤明细（可人工校对）。"""

    __tablename__ = 'attendance_timesheets'
    __table_args__ = (
        db.UniqueConstraint('tenant_id', 'employee_id', 'work_date', name='uq_attendance_timesheet_day'),
    )

    id = db.Column(db.String(36), primary_key=True)
    tenant_id = db.Column(db.String(36), db.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=True, index=True)
    employee_id = db.Column(db.String(36), db.ForeignKey('employees.id', ondelete='CASCADE'), nullable=False, index=True)
    department_id = db.Column(db.String(36), nullable=True, index=True)
    work_date = db.Column(db.Date, nullable=False, index=True)
    employee_type = db.Column(db.String(20))
    worktime_ids = db.Column(db.String(200))
    check_in = db.Column(db.DateTime)
    check_out = db.Column(db.DateTime)
    check_in_fixed = db.Column(db.Boolean, default=False)
    check_out_fixed = db.Column(db.Boolean, default=False)
    punch_times = db.Column(db.JSON)
    work_hours = db.Column(db.Numeric(6, 2), default=0)
    overtime_hours = db.Column(db.Numeric(6, 2), default=0)
    leave_hours = db.Column(db.Numeric(6, 2), default=0)
    event_summary = db.Column(db.String(200))
    expected_punches = db.Column(db.Integer, default=0)
    actual_punches = db.Column(db.Integer, default=0)
    missing_count = db.Column(db.Integer, default=0)
    status = db.Column(db.String(20), default='normal', index=True)
    source = db.Column(db.String(20), default='oa_schedule')
    manual_corrected = db.Column(db.Boolean, default=False)
    corrected_by = db.Column(db.String(36))
    corrected_at = db.Column(db.DateTime)
    original_snapshot = db.Column(db.JSON)
    remark = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'employeeId': self.employee_id,
            'employeeName': employee_name(self.employee_id),
            'departmentId': self.department_id,
            'workDate': iso(self.work_date),
            'employeeType': self.employee_type,
            'worktimeIds': self.worktime_ids,
            'checkIn': iso(self.check_in),
            'checkOut': iso(self.check_out),
            'checkInFixed': bool(self.check_in_fixed),
            'checkOutFixed': bool(self.check_out_fixed),
            'punchTimes': self.punch_times or [],
            'workHours': money(self.work_hours),
            'overtimeHours': money(self.overtime_hours),
            'leaveHours': money(self.leave_hours),
            'eventSummary': self.event_summary,
            'expectedPunches': self.expected_punches,
            'actualPunches': self.actual_punches,
            'missingCount': self.missing_count,
            'status': self.status,
            'source': self.source,
            'manualCorrected': bool(self.manual_corrected),
            'correctedBy': self.corrected_by,
            'correctedAt': iso(self.corrected_at),
            'remark': self.remark,
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
        }


class AttendanceSyncRun(db.Model):
    __tablename__ = 'attendance_sync_runs'

    id = db.Column(db.String(36), primary_key=True)
    tenant_id = db.Column(db.String(36), db.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False, index=True)
    run_trigger = db.Column(db.String(30), nullable=False, default='scheduled')
    status = db.Column(db.String(20), nullable=False, default='running', index=True)
    people_read = db.Column(db.Integer, nullable=False, default=0)
    people_upserted = db.Column(db.Integer, nullable=False, default=0)
    punches_read = db.Column(db.Integer, nullable=False, default=0)
    punches_inserted = db.Column(db.Integer, nullable=False, default=0)
    punches_duplicate = db.Column(db.Integer, nullable=False, default=0)
    unmatched_count = db.Column(db.Integer, nullable=False, default=0)
    suggested_count = db.Column(db.Integer, nullable=False, default=0)
    lookback_days = db.Column(db.Integer, nullable=False, default=90)
    cursor_check_time = db.Column(db.DateTime, nullable=True)
    error_code = db.Column(db.String(80), nullable=True)
    error_message = db.Column(db.String(500), nullable=True)
    started_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    completed_at = db.Column(db.DateTime, nullable=True)

    def to_dict(self):
        return {
            'id': self.id,
            'trigger': self.run_trigger,
            'status': self.status,
            'peopleRead': self.people_read,
            'peopleUpserted': self.people_upserted,
            'punchesRead': self.punches_read,
            'punchesInserted': self.punches_inserted,
            'punchesDuplicate': self.punches_duplicate,
            'unmatchedCount': self.unmatched_count,
            'suggestedCount': self.suggested_count,
            'lookbackDays': self.lookback_days,
            'cursorCheckTime': iso(self.cursor_check_time),
            'errorCode': self.error_code,
            'errorMessage': self.error_message,
            'startedAt': iso(self.started_at),
            'completedAt': iso(self.completed_at),
        }


class AttendanceExternalPerson(db.Model):
    __tablename__ = 'attendance_external_people'
    __table_args__ = (
        db.UniqueConstraint('tenant_id', 'external_user_id', name='uq_attendance_external_user'),
    )

    id = db.Column(db.String(36), primary_key=True)
    tenant_id = db.Column(db.String(36), db.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False, index=True)
    external_user_id = db.Column(db.String(40), nullable=False, index=True)
    badge_number = db.Column(db.String(80), nullable=True)
    name = db.Column(db.String(100), nullable=False, default='')
    department_name = db.Column(db.String(120), nullable=True)
    employee_id = db.Column(db.String(36), db.ForeignKey('employees.id', ondelete='SET NULL'), nullable=True, index=True)
    suggested_employee_id = db.Column(db.String(36), db.ForeignKey('employees.id', ondelete='SET NULL'), nullable=True)
    mapping_status = db.Column(db.String(20), nullable=False, default='unmatched', index=True)
    mapped_at = db.Column(db.DateTime, nullable=True)
    mapped_by = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    source_synced_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    employee = db.relationship('Employee', foreign_keys=[employee_id])
    suggested_employee = db.relationship('Employee', foreign_keys=[suggested_employee_id])

    def to_dict(self):
        return {
            'id': self.id,
            'externalUserId': self.external_user_id,
            'badgeNumber': self.badge_number,
            'name': self.name,
            'departmentName': self.department_name,
            'employeeId': self.employee_id,
            'employeeNo': self.employee.employee_no if self.employee else None,
            'employeeName': self.employee.name if self.employee else None,
            'employeeStatus': self.employee.status if self.employee else None,
            'suggestedEmployeeId': self.suggested_employee_id,
            'suggestedEmployeeNo': self.suggested_employee.employee_no if self.suggested_employee else None,
            'suggestedEmployeeName': self.suggested_employee.name if self.suggested_employee else None,
            'mappingStatus': self.mapping_status,
            'mappedAt': iso(self.mapped_at),
            'sourceSyncedAt': iso(self.source_synced_at),
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
        }


class AttendanceRawPunch(db.Model):
    __tablename__ = 'attendance_raw_punches'
    __table_args__ = (
        db.UniqueConstraint('tenant_id', 'external_user_id', 'check_time', name='uq_attendance_raw_punch'),
    )

    id = db.Column(db.String(36), primary_key=True)
    tenant_id = db.Column(db.String(36), db.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False, index=True)
    external_user_id = db.Column(db.String(40), nullable=False, index=True)
    check_time = db.Column(db.DateTime, nullable=False, index=True)
    check_type = db.Column(db.String(20), nullable=True)
    verify_code = db.Column(db.String(20), nullable=True)
    sensor_id = db.Column(db.String(80), nullable=True)
    serial_number = db.Column(db.String(80), nullable=True)
    fingerprint = db.Column(db.String(64), nullable=False, index=True)
    sync_run_id = db.Column(db.String(36), nullable=True, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def to_dict(self):
        return {
            'id': self.id,
            'externalUserId': self.external_user_id,
            'checkTime': iso(self.check_time),
            'checkType': self.check_type,
            'verifyCode': self.verify_code,
            'sensorId': self.sensor_id,
            'serialNumber': self.serial_number,
            'syncRunId': self.sync_run_id,
            'createdAt': iso(self.created_at),
        }


class AttendanceEvent(db.Model):
    """OA 考勤单据（请假/加班/补卡/调休/外出/出差），只读同步落库。"""

    __tablename__ = 'attendance_events'
    __table_args__ = (
        db.UniqueConstraint('tenant_id', 'source_key', name='uq_attendance_event_source'),
    )

    id = db.Column(db.String(36), primary_key=True)
    tenant_id = db.Column(db.String(36), db.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=True, index=True)
    event_type = db.Column(db.String(20), nullable=False, index=True)
    employee_id = db.Column(db.String(36), db.ForeignKey('employees.id', ondelete='SET NULL'), nullable=True, index=True)
    employee_no = db.Column(db.String(80), nullable=True, index=True)
    employee_name = db.Column(db.String(100), nullable=True)
    oa_person_id = db.Column(db.String(40), nullable=True)
    oa_department_id = db.Column(db.String(40), nullable=True)
    start_at = db.Column(db.DateTime, nullable=True, index=True)
    end_at = db.Column(db.DateTime, nullable=True)
    hours = db.Column(db.Numeric(8, 2), nullable=True)
    subtype = db.Column(db.String(40), nullable=True)
    reason = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(20), nullable=False, default='approved', index=True)
    affects_hours = db.Column(db.Boolean, nullable=False, default=True)
    oa_request_id = db.Column(db.String(40), nullable=True, index=True)
    oa_workflow_id = db.Column(db.String(40), nullable=True)
    oa_form_table = db.Column(db.String(60), nullable=True)
    oa_main_id = db.Column(db.String(40), nullable=True)
    source_key = db.Column(db.String(160), nullable=False, index=True)
    source = db.Column(db.String(20), nullable=False, default='oa_workflow')
    synced_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'eventType': self.event_type,
            'employeeId': self.employee_id,
            'employeeName': self.employee_name or employee_name(self.employee_id),
            'employeeNo': self.employee_no,
            'oaPersonId': self.oa_person_id,
            'oaDepartmentId': self.oa_department_id,
            'startAt': iso(self.start_at),
            'endAt': iso(self.end_at),
            'hours': money(self.hours),
            'subtype': self.subtype,
            'reason': self.reason,
            'status': self.status,
            'affectsHours': bool(self.affects_hours),
            'oaRequestId': self.oa_request_id,
            'oaWorkflowId': self.oa_workflow_id,
            'oaFormTable': self.oa_form_table,
            'oaMainId': self.oa_main_id,
            'source': self.source,
            'syncedAt': iso(self.synced_at),
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
        }


class EmployeeRequest(db.Model):
    __tablename__ = 'employee_requests'

    id = db.Column(db.String(36), primary_key=True)
    employee_id = db.Column(db.String(36), db.ForeignKey('employees.id', ondelete='CASCADE'), nullable=False, index=True)
    request_type = db.Column(db.String(50), nullable=False, index=True)
    title = db.Column(db.String(200))
    start_time = db.Column(db.DateTime)
    end_time = db.Column(db.DateTime)
    duration_hours = db.Column(db.Numeric(8, 2))
    reason = db.Column(db.Text)
    status = db.Column(db.String(40), default='draft', index=True)
    approval_id = db.Column(db.String(36), db.ForeignKey('approval_instances.id', ondelete='SET NULL'))
    created_by = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='SET NULL'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    employee = db.relationship('Employee', foreign_keys=[employee_id])

    def to_dict(self):
        return {
            'id': self.id,
            'employeeId': self.employee_id,
            'employeeName': self.employee.name if self.employee else None,
            'requestType': self.request_type,
            'title': self.title,
            'startTime': iso(self.start_time),
            'endTime': iso(self.end_time),
            'durationHours': money(self.duration_hours),
            'reason': self.reason,
            'status': self.status,
            'approvalId': self.approval_id,
            'createdBy': self.created_by,
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
        }


class ApprovalInstance(db.Model):
    __tablename__ = 'approval_instances'

    id = db.Column(db.String(36), primary_key=True)
    business_type = db.Column(db.String(80), nullable=False, index=True)
    business_id = db.Column(db.String(36), nullable=False, index=True)
    title = db.Column(db.String(200), nullable=False)
    applicant_id = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='SET NULL'))
    approver_id = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='SET NULL'))
    status = db.Column(db.String(40), default='pending', index=True)
    current_step = db.Column(db.Integer, default=1)
    submitted_at = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime)
    remark = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    applicant = db.relationship('User', foreign_keys=[applicant_id])
    approver = db.relationship('User', foreign_keys=[approver_id])

    def to_dict(self):
        return {
            'id': self.id,
            'businessType': self.business_type,
            'businessId': self.business_id,
            'title': self.title,
            'applicantId': self.applicant_id,
            'applicantName': self.applicant.name if self.applicant else None,
            'approverId': self.approver_id,
            'approverName': self.approver.name if self.approver else None,
            'status': self.status,
            'currentStep': self.current_step,
            'submittedAt': iso(self.submitted_at),
            'completedAt': iso(self.completed_at),
            'remark': self.remark,
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
        }


class ApprovalRecord(db.Model):
    __tablename__ = 'approval_records'

    id = db.Column(db.String(36), primary_key=True)
    approval_id = db.Column(db.String(36), db.ForeignKey('approval_instances.id', ondelete='CASCADE'), nullable=False, index=True)
    approver_id = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='SET NULL'))
    action = db.Column(db.String(40), nullable=False)
    comment = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    approval = db.relationship('ApprovalInstance', foreign_keys=[approval_id])
    approver = db.relationship('User', foreign_keys=[approver_id])

    def to_dict(self):
        return {
            'id': self.id,
            'approvalId': self.approval_id,
            'approvalTitle': self.approval.title if self.approval else None,
            'businessType': self.approval.business_type if self.approval else None,
            'businessId': self.approval.business_id if self.approval else None,
            'approverId': self.approver_id,
            'approverName': self.approver.name if self.approver else None,
            'action': self.action,
            'comment': self.comment,
            'createdAt': iso(self.created_at),
        }


class PerformanceCycle(db.Model):
    __tablename__ = 'performance_cycles'

    id = db.Column(db.String(36), primary_key=True)
    tenant_id = db.Column(db.String(36), db.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=True, index=True)
    name = db.Column(db.String(120), nullable=False)
    cycle_type = db.Column(db.String(50), default='月度')
    start_date = db.Column(db.Date)
    end_date = db.Column(db.Date)
    status = db.Column(db.String(40), default='draft', index=True)
    remark = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'tenantId': self.tenant_id,
            'name': self.name,
            'cycleType': self.cycle_type,
            'startDate': iso(self.start_date),
            'endDate': iso(self.end_date),
            'status': self.status,
            'remark': self.remark,
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
        }


class PerformanceTemplate(db.Model):
    __tablename__ = 'performance_templates'

    id = db.Column(db.String(36), primary_key=True)
    tenant_id = db.Column(db.String(36), db.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=True, index=True)
    name = db.Column(db.String(120), nullable=False)
    template_type = db.Column(db.String(60), default='通用')
    indicators = db.Column(db.JSON)
    status = db.Column(db.String(40), default='active', index=True)
    remark = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'tenantId': self.tenant_id,
            'name': self.name,
            'templateType': self.template_type,
            'indicators': self.indicators or [],
            'status': self.status,
            'remark': self.remark,
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
        }


class PerformanceReview(db.Model):
    __tablename__ = 'performance_reviews'

    id = db.Column(db.String(36), primary_key=True)
    tenant_id = db.Column(db.String(36), db.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=True, index=True)
    employee_id = db.Column(db.String(36), db.ForeignKey('employees.id', ondelete='CASCADE'), nullable=False, index=True)
    cycle_id = db.Column(db.String(36), db.ForeignKey('performance_cycles.id', ondelete='SET NULL'))
    template_id = db.Column(db.String(36), db.ForeignKey('performance_templates.id', ondelete='SET NULL'))
    manager_id = db.Column(db.String(36), db.ForeignKey('employees.id', ondelete='SET NULL'), nullable=True, index=True)
    self_review = db.Column(db.Text)
    self_score = db.Column(db.Numeric(6, 2))
    manager_score = db.Column(db.Numeric(6, 2))
    auto_score = db.Column(db.Numeric(6, 2))
    final_score = db.Column(db.Numeric(6, 2))
    grade = db.Column(db.String(20), index=True)
    result = db.Column(db.Text)
    remark = db.Column(db.Text)
    status = db.Column(db.String(40), default='draft', index=True)
    self_submitted_at = db.Column(db.DateTime)
    manager_scored_at = db.Column(db.DateTime)
    confirmed_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    employee = db.relationship('Employee', foreign_keys=[employee_id])
    cycle = db.relationship('PerformanceCycle', foreign_keys=[cycle_id])
    template = db.relationship('PerformanceTemplate', foreign_keys=[template_id])

    def to_dict(self):
        return {
            'id': self.id,
            'tenantId': self.tenant_id,
            'employeeId': self.employee_id,
            'employeeName': self.employee.name if self.employee else None,
            'cycleId': self.cycle_id,
            'cycleName': self.cycle.name if self.cycle else None,
            'templateId': self.template_id,
            'templateName': self.template.name if self.template else None,
            'managerId': self.manager_id,
            'managerName': employee_name(self.manager_id),
            'selfReview': self.self_review,
            'selfScore': money(self.self_score),
            'managerScore': money(self.manager_score),
            'autoScore': money(self.auto_score),
            'finalScore': money(self.final_score),
            'grade': self.grade,
            'result': self.result,
            'remark': self.remark,
            'status': self.status,
            'selfSubmittedAt': iso(self.self_submitted_at),
            'managerScoredAt': iso(self.manager_scored_at),
            'confirmedAt': iso(self.confirmed_at),
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
        }


class PerformanceReviewItem(db.Model):
    """考核维度明细：一个考核表对应多行指标。"""

    __tablename__ = 'performance_review_items'

    id = db.Column(db.String(36), primary_key=True)
    tenant_id = db.Column(db.String(36), db.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=True, index=True)
    review_id = db.Column(db.String(36), db.ForeignKey('performance_reviews.id', ondelete='CASCADE'), nullable=False, index=True)
    indicator_code = db.Column(db.String(80), index=True)
    indicator_name = db.Column(db.String(200))
    weight = db.Column(db.Numeric(6, 2))
    data_source = db.Column(db.String(40))
    metric = db.Column(db.String(120))
    auto_score = db.Column(db.Numeric(6, 2))
    self_score = db.Column(db.Numeric(6, 2))
    manager_score = db.Column(db.Numeric(6, 2))
    final_score = db.Column(db.Numeric(6, 2))
    comment = db.Column(db.Text)
    sort_order = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'reviewId': self.review_id,
            'indicatorCode': self.indicator_code,
            'indicatorName': self.indicator_name,
            'weight': money(self.weight),
            'dataSource': self.data_source,
            'metric': self.metric,
            'autoScore': money(self.auto_score),
            'selfScore': money(self.self_score),
            'managerScore': money(self.manager_score),
            'finalScore': money(self.final_score),
            'comment': self.comment,
            'sortOrder': self.sort_order,
        }


class PerformanceIndicator(db.Model):
    """岗位考核指标（按 部门 + 岗位 维护）。"""

    __tablename__ = 'performance_indicators'

    id = db.Column(db.String(36), primary_key=True)
    tenant_id = db.Column(db.String(36), db.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=True, index=True)
    department_name = db.Column(db.String(100), index=True)
    position_name = db.Column(db.String(120), index=True)
    seq = db.Column(db.Integer, default=0)
    name = db.Column(db.String(200), nullable=False)
    target_value = db.Column(db.String(200))
    definition = db.Column(db.Text)
    scoring_rule = db.Column(db.Text)
    weight = db.Column(db.Numeric(6, 2))
    evaluator_department = db.Column(db.String(100))
    linked_to_salary = db.Column(db.Boolean, default=True)
    status = db.Column(db.String(20), default='active', index=True)
    remark = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'departmentName': self.department_name,
            'positionName': self.position_name,
            'seq': self.seq,
            'name': self.name,
            'targetValue': self.target_value,
            'definition': self.definition,
            'scoringRule': self.scoring_rule,
            'weight': money(self.weight),
            'evaluatorDepartment': self.evaluator_department,
            'linkedToSalary': bool(self.linked_to_salary),
            'status': self.status,
            'remark': self.remark,
        }


class PerformanceScoreRule(db.Model):
    """可配置自动打分规则。"""

    __tablename__ = 'performance_score_rules'

    id = db.Column(db.String(36), primary_key=True)
    tenant_id = db.Column(db.String(36), db.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=True, index=True)
    indicator_code = db.Column(db.String(80), index=True)
    indicator_name = db.Column(db.String(200))
    data_source = db.Column(db.String(40))
    metric = db.Column(db.String(120))
    operator = db.Column(db.String(10), default='>=')
    threshold = db.Column(db.Numeric(10, 2))
    target_source = db.Column(db.String(20), default='fixed')
    target_oa_department_id = db.Column(db.String(40))
    scoring_mode = db.Column(db.String(20), default='threshold')
    score = db.Column(db.Numeric(6, 2))
    weight = db.Column(db.Numeric(6, 2))
    status = db.Column(db.String(20), default='active', index=True)
    remark = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'indicatorCode': self.indicator_code,
            'indicatorName': self.indicator_name,
            'dataSource': self.data_source,
            'metric': self.metric,
            'operator': self.operator,
            'threshold': money(self.threshold),
            'targetSource': self.target_source or 'fixed',
            'targetOaDepartmentId': self.target_oa_department_id,
            'scoringMode': self.scoring_mode or 'threshold',
            'score': money(self.score),
            'weight': money(self.weight),
            'status': self.status,
            'remark': self.remark,
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
        }


class SalaryProfile(db.Model):
    __tablename__ = 'salary_profiles'

    id = db.Column(db.String(36), primary_key=True)
    employee_id = db.Column(db.String(36), db.ForeignKey('employees.id', ondelete='CASCADE'), nullable=False, unique=True, index=True)
    base_salary = db.Column(db.Numeric(12, 2), default=0)
    position_salary = db.Column(db.Numeric(12, 2), default=0)
    performance_salary = db.Column(db.Numeric(12, 2), default=0)
    allowance = db.Column(db.Numeric(12, 2), default=0)
    bonus = db.Column(db.Numeric(12, 2), default=0)
    deduction = db.Column(db.Numeric(12, 2), default=0)
    currency = db.Column(db.String(20), default='CNY')
    status = db.Column(db.String(40), default='active', index=True)
    remark = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    employee = db.relationship('Employee', foreign_keys=[employee_id])

    def to_dict(self):
        total = sum([money(self.base_salary) or 0, money(self.position_salary) or 0,
                     money(self.performance_salary) or 0, money(self.allowance) or 0,
                     money(self.bonus) or 0]) - (money(self.deduction) or 0)
        return {
            'id': self.id,
            'employeeId': self.employee_id,
            'employeeName': self.employee.name if self.employee else None,
            'baseSalary': money(self.base_salary),
            'positionSalary': money(self.position_salary),
            'performanceSalary': money(self.performance_salary),
            'allowance': money(self.allowance),
            'bonus': money(self.bonus),
            'deduction': money(self.deduction),
            'totalSalary': total,
            'currency': self.currency,
            'status': self.status,
            'remark': self.remark,
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
        }


class PayrollRecord(db.Model):
    __tablename__ = 'payroll_records'

    id = db.Column(db.String(36), primary_key=True)
    employee_id = db.Column(db.String(36), db.ForeignKey('employees.id', ondelete='CASCADE'), nullable=False, index=True)
    payroll_month = db.Column(db.String(20), nullable=False, index=True)
    base_salary = db.Column(db.Numeric(12, 2), default=0)
    position_salary = db.Column(db.Numeric(12, 2), default=0)
    performance_salary = db.Column(db.Numeric(12, 2), default=0)
    bonus = db.Column(db.Numeric(12, 2), default=0)
    allowance = db.Column(db.Numeric(12, 2), default=0)
    deduction = db.Column(db.Numeric(12, 2), default=0)
    gross_salary = db.Column(db.Numeric(12, 2), default=0)
    net_salary = db.Column(db.Numeric(12, 2), default=0)
    status = db.Column(db.String(40), default='draft', index=True)
    employee_visible = db.Column(db.Boolean, default=False)
    remark = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    employee = db.relationship('Employee', foreign_keys=[employee_id])

    def to_dict(self):
        return {
            'id': self.id,
            'employeeId': self.employee_id,
            'employeeName': self.employee.name if self.employee else None,
            'payrollMonth': self.payroll_month,
            'baseSalary': money(self.base_salary),
            'positionSalary': money(self.position_salary),
            'performanceSalary': money(self.performance_salary),
            'bonus': money(self.bonus),
            'allowance': money(self.allowance),
            'deduction': money(self.deduction),
            'grossSalary': money(self.gross_salary),
            'netSalary': money(self.net_salary),
            'status': self.status,
            'employeeVisible': self.employee_visible,
            'remark': self.remark,
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
        }


class TrainingCourse(db.Model):
    __tablename__ = 'training_courses'

    id = db.Column(db.String(36), primary_key=True)
    name = db.Column(db.String(160), nullable=False, index=True)
    category = db.Column(db.String(80))
    lecturer = db.Column(db.String(100))
    duration_hours = db.Column(db.Numeric(8, 2))
    description = db.Column(db.Text)
    status = db.Column(db.String(40), default='active', index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'category': self.category,
            'lecturer': self.lecturer,
            'durationHours': money(self.duration_hours),
            'description': self.description,
            'status': self.status,
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
        }


class TrainingPlan(db.Model):
    __tablename__ = 'training_plans'

    id = db.Column(db.String(36), primary_key=True)
    name = db.Column(db.String(160), nullable=False)
    course_id = db.Column(db.String(36), db.ForeignKey('training_courses.id', ondelete='SET NULL'))
    start_time = db.Column(db.DateTime)
    end_time = db.Column(db.DateTime)
    target_departments = db.Column(db.JSON)
    status = db.Column(db.String(40), default='planned', index=True)
    remark = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    course = db.relationship('TrainingCourse', foreign_keys=[course_id])

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'courseId': self.course_id,
            'courseName': self.course.name if self.course else None,
            'startTime': iso(self.start_time),
            'endTime': iso(self.end_time),
            'targetDepartments': self.target_departments or [],
            'status': self.status,
            'remark': self.remark,
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
        }


class TrainingRecord(db.Model):
    __tablename__ = 'training_records'

    id = db.Column(db.String(36), primary_key=True)
    plan_id = db.Column(db.String(36), db.ForeignKey('training_plans.id', ondelete='SET NULL'))
    course_id = db.Column(db.String(36), db.ForeignKey('training_courses.id', ondelete='SET NULL'))
    employee_id = db.Column(db.String(36), db.ForeignKey('employees.id', ondelete='CASCADE'), nullable=False, index=True)
    status = db.Column(db.String(40), default='registered', index=True)
    result = db.Column(db.String(80))
    score = db.Column(db.Numeric(6, 2))
    feedback = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    plan = db.relationship('TrainingPlan', foreign_keys=[plan_id])
    course = db.relationship('TrainingCourse', foreign_keys=[course_id])
    employee = db.relationship('Employee', foreign_keys=[employee_id])

    def to_dict(self):
        return {
            'id': self.id,
            'planId': self.plan_id,
            'planName': self.plan.name if self.plan else None,
            'courseId': self.course_id,
            'courseName': self.course.name if self.course else None,
            'employeeId': self.employee_id,
            'employeeName': self.employee.name if self.employee else None,
            'status': self.status,
            'result': self.result,
            'score': money(self.score),
            'feedback': self.feedback,
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
        }


# ===========================================================================
# 培训体系（参照 PlayEdu 开源版：课程/章节/课时/附件/分类/指派/进度）
# 与上方历史 training_* 三表并存：新门户用本节模型，旧绩效联动不破坏。
# ===========================================================================


class CourseCategory(db.Model):
    """课程分类树（支持多级，parent_chain 缓存祖先 id 逗号串加速判定）。"""
    __tablename__ = 'course_categories'

    id = db.Column(db.String(36), primary_key=True)
    name = db.Column(db.String(120), nullable=False, index=True)
    parent_id = db.Column(db.String(36), db.ForeignKey('course_categories.id', ondelete='SET NULL'), nullable=True)
    parent_chain = db.Column(db.String(500))  # 逗号分隔的祖先 id，根为空串
    sort = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    parent = db.relationship('CourseCategory', remote_side=[id])

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'parentId': self.parent_id,
            'parentName': self.parent.name if self.parent else None,
            'parentChain': self.parent_chain or '',
            'sort': self.sort,
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
        }


class Course(db.Model):
    """培训课程（视频章节制）。镜像 PlayEdu courses 表。"""
    __tablename__ = 'courses'

    id = db.Column(db.String(36), primary_key=True)
    title = db.Column(db.String(200), nullable=False, index=True)
    category_id = db.Column(db.String(36), db.ForeignKey('course_categories.id', ondelete='SET NULL'), nullable=True)
    cover_url = db.Column(db.String(500))
    is_required = db.Column(db.Boolean, default=False)  # True=必修 False=选修
    deadline = db.Column(db.DateTime)  # 必修课完成截止时间（合规用）；选修课一般为空
    short_desc = db.Column(db.String(500))
    content = db.Column(db.Text)  # 课程图文介绍（可选）
    is_show = db.Column(db.Boolean, default=True, index=True)  # 是否上架
    class_hour = db.Column(db.Integer, default=0)  # 冗余课时数（便于列表展示）
    sort = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    category = db.relationship('CourseCategory', foreign_keys=[category_id])

    def to_dict(self):
        return {
            'id': self.id,
            'title': self.title,
            'categoryId': self.category_id,
            'categoryName': self.category.name if self.category else None,
            'coverUrl': self.cover_url,
            'isRequired': bool(self.is_required),
            'deadline': iso(self.deadline),
            'shortDesc': self.short_desc,
            'content': self.content,
            'isShow': bool(self.is_show),
            'classHour': self.class_hour,
            'sort': self.sort,
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
        }


class CourseChapter(db.Model):
    """课程章节（课时的分组 + 章节文档资料）。"""
    __tablename__ = 'course_chapters'

    id = db.Column(db.String(36), primary_key=True)
    course_id = db.Column(db.String(36), db.ForeignKey('courses.id', ondelete='CASCADE'), nullable=False, index=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)  # 章节简介/学习要点
    materials = db.Column(db.JSON)  # [{title, url, ext}] 章节文档资料（PDF/Word/PPT 等）
    sort = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    course = db.relationship('Course', foreign_keys=[course_id])

    def to_dict(self):
        return {
            'id': self.id,
            'courseId': self.course_id,
            'courseTitle': self.course.title if self.course else None,
            'name': self.name,
            'description': self.description,
            'materials': self.materials or [],
            'sort': self.sort,
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
        }


class CourseHour(db.Model):
    """课时（一个视频为一个课时）。镜像 PlayEdu course_hour。"""
    __tablename__ = 'course_hours'

    id = db.Column(db.String(36), primary_key=True)
    course_id = db.Column(db.String(36), db.ForeignKey('courses.id', ondelete='CASCADE'), nullable=False, index=True)
    chapter_id = db.Column(db.String(36), db.ForeignKey('course_chapters.id', ondelete='CASCADE'), nullable=True)
    title = db.Column(db.String(200), nullable=False)
    type = db.Column(db.String(20), default='VIDEO')  # 开源版仅 VIDEO
    sort = db.Column(db.Integer, default=0)
    video_key = db.Column(db.String(500))  # MinIO object key（本地回退时为相对路径）
    duration = db.Column(db.Integer, default=0)  # 视频时长（秒）
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    course = db.relationship('Course', foreign_keys=[course_id])
    chapter = db.relationship('CourseChapter', foreign_keys=[chapter_id])

    def to_dict(self):
        return {
            'id': self.id,
            'courseId': self.course_id,
            'courseTitle': self.course.title if self.course else None,
            'chapterId': self.chapter_id,
            'chapterName': self.chapter.name if self.chapter else None,
            'title': self.title,
            'type': self.type,
            'sort': self.sort,
            'videoKey': self.video_key,
            'duration': self.duration,
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
        }


class CourseAttachment(db.Model):
    """课程附件（PDF/Word 等可下载材料）。"""
    __tablename__ = 'course_attachments'

    id = db.Column(db.String(36), primary_key=True)
    course_id = db.Column(db.String(36), db.ForeignKey('courses.id', ondelete='CASCADE'), nullable=False, index=True)
    title = db.Column(db.String(200), nullable=False)
    file_url = db.Column(db.String(500))
    ext = db.Column(db.String(20))
    sort = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    course = db.relationship('Course', foreign_keys=[course_id])

    def to_dict(self):
        return {
            'id': self.id,
            'courseId': self.course_id,
            'courseTitle': self.course.title if self.course else None,
            'title': self.title,
            'fileUrl': self.file_url,
            'ext': self.ext,
            'sort': self.sort,
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
        }


class CourseAssignment(db.Model):
    """课程指派（多态：按部门或按员工）。

    target_type: 'dept' | 'user'
    target_id:   部门 id 或 员工 id
    镜像 PlayEdu course_department_user(type, range_id)。
    """
    __tablename__ = 'course_assignments'

    id = db.Column(db.String(36), primary_key=True)
    course_id = db.Column(db.String(36), db.ForeignKey('courses.id', ondelete='CASCADE'), nullable=False, index=True)
    target_type = db.Column(db.String(10), nullable=False)  # dept | user
    target_id = db.Column(db.String(36), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    course = db.relationship('Course', foreign_keys=[course_id])

    def to_dict(self, name_map=None):
        """序列化。name_map 可外部预加载传入 {(target_type, target_id): name}，避免 N+1。"""
        name = None
        if name_map:
            name = name_map.get((self.target_type, self.target_id))
        elif self.target_type == 'dept':
            dept = Department.query.get(self.target_id)
            name = dept.name if dept else None
        elif self.target_type == 'user':
            name = employee_name(self.target_id)
        return {
            'id': self.id,
            'courseId': self.course_id,
            'targetType': self.target_type,
            'targetId': self.target_id,
            'targetName': name,
            'createdAt': iso(self.created_at),
        }


class UserCourseProgress(db.Model):
    """员工课程级学习进度。镜像 PlayEdu user_course_records。"""
    __tablename__ = 'user_course_progress'

    id = db.Column(db.String(36), primary_key=True)
    employee_id = db.Column(db.String(36), db.ForeignKey('employees.id', ondelete='CASCADE'), nullable=False, index=True)
    course_id = db.Column(db.String(36), db.ForeignKey('courses.id', ondelete='CASCADE'), nullable=False, index=True)
    hour_count = db.Column(db.Integer, default=0)  # 课程总课时数（冗余）
    finished_count = db.Column(db.Integer, default=0)
    progress = db.Column(db.Integer, default=0)  # 0-100
    is_finished = db.Column(db.Boolean, default=False, index=True)
    finished_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    employee = db.relationship('Employee', foreign_keys=[employee_id])
    course = db.relationship('Course', foreign_keys=[course_id])

    def to_dict(self):
        return {
            'id': self.id,
            'employeeId': self.employee_id,
            'employeeName': self.employee.name if self.employee else None,
            'courseId': self.course_id,
            'courseTitle': self.course.title if self.course else None,
            'hourCount': self.hour_count,
            'finishedCount': self.finished_count,
            'progress': self.progress,
            'isFinished': bool(self.is_finished),
            'finishedAt': iso(self.finished_at),
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
        }


class UserHourProgress(db.Model):
    """员工单课时学习进度。镜像 PlayEdu user_course_hour_records。"""
    __tablename__ = 'user_hour_progress'

    id = db.Column(db.String(36), primary_key=True)
    employee_id = db.Column(db.String(36), db.ForeignKey('employees.id', ondelete='CASCADE'), nullable=False, index=True)
    course_id = db.Column(db.String(36), db.ForeignKey('courses.id', ondelete='CASCADE'), nullable=False, index=True)
    hour_id = db.Column(db.String(36), db.ForeignKey('course_hours.id', ondelete='CASCADE'), nullable=False, index=True)
    total_duration = db.Column(db.Integer, default=0)  # 课时总时长（秒，冗余）
    watched_duration = db.Column(db.Integer, default=0)  # 已观看时长（秒）
    is_finished = db.Column(db.Boolean, default=False, index=True)
    finished_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    employee = db.relationship('Employee', foreign_keys=[employee_id])
    course = db.relationship('Course', foreign_keys=[course_id])
    hour = db.relationship('CourseHour', foreign_keys=[hour_id])

    def to_dict(self):
        return {
            'id': self.id,
            'employeeId': self.employee_id,
            'employeeName': self.employee.name if self.employee else None,
            'courseId': self.course_id,
            'hourId': self.hour_id,
            'hourTitle': self.hour.title if self.hour else None,
            'totalDuration': self.total_duration,
            'watchedDuration': self.watched_duration,
            'isFinished': bool(self.is_finished),
            'finishedAt': iso(self.finished_at),
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
        }


# ===========================================================================
# 考试体系（自研）：题库 / 试卷 / 考试任务 / 授权 / 答题记录
# 题库支持单选 single / 多选 multiple / 判断 judge；判分见 services/exam_grading.py
# ===========================================================================


class ExamQuestion(db.Model):
    """题库题目。

    - type: single | multiple | judge
    - options: JSON 数组，如 ['选项A','选项B','选项C','选项D']
    - answer: JSON 索引数组，如 [0, 2]（正确选项的 0-based 索引）
    - source: 'ai' | 'manual'（AI 生成的默认 pending 审核）
    - review_status: pending | approved | rejected
    """
    __tablename__ = 'exam_questions'

    id = db.Column(db.String(36), primary_key=True)
    course_id = db.Column(db.String(36), db.ForeignKey('courses.id', ondelete='SET NULL'), nullable=True, index=True)
    category = db.Column(db.String(80), index=True)
    type = db.Column(db.String(20), default='single', nullable=False)  # single|multiple|judge
    stem = db.Column(db.Text, nullable=False)
    options = db.Column(db.JSON)
    answer = db.Column(db.JSON)
    score = db.Column(db.Numeric(6, 2), default=Decimal('5'))
    explanation = db.Column(db.Text)
    difficulty = db.Column(db.Integer, default=3)  # 1-5
    source = db.Column(db.String(10), default='manual', index=True)  # ai|manual
    review_status = db.Column(db.String(20), default='approved', index=True)  # pending|approved|rejected
    reviewed_by = db.Column(db.String(36), nullable=True)
    reviewed_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    course = db.relationship('Course', foreign_keys=[course_id])

    def to_dict(self):
        return {
            'id': self.id,
            'courseId': self.course_id,
            'courseTitle': self.course.title if self.course else None,
            'category': self.category,
            'type': self.type,
            'stem': self.stem,
            'options': self.options or [],
            'answer': self.answer or [],
            'score': money(self.score),
            'explanation': self.explanation,
            'difficulty': self.difficulty,
            'source': self.source,
            'reviewStatus': self.review_status,
            'reviewedBy': self.reviewed_by,
            'reviewedAt': iso(self.reviewed_at),
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
        }


class ExamPaper(db.Model):
    """试卷：题目集合 + 判分参数。

    question_mode:
    - fixed: 固定题，question_ids 全部生效
    - random: 题池随机抽题，draw_count 表示每次考试抽取题数
    """
    __tablename__ = 'exam_papers'

    id = db.Column(db.String(36), primary_key=True)
    title = db.Column(db.String(200), nullable=False, index=True)
    description = db.Column(db.Text)
    question_ids = db.Column(db.JSON)  # ["q-uuid", ...]
    question_mode = db.Column(db.String(20), default='fixed', index=True)  # fixed|random
    draw_count = db.Column(db.Integer)  # random 模式下每次抽取题数
    total_score = db.Column(db.Numeric(8, 2), default=Decimal('0'))
    pass_score = db.Column(db.Numeric(8, 2), default=Decimal('60'))
    duration_minutes = db.Column(db.Integer, default=30)
    status = db.Column(db.String(20), default='draft', index=True)  # draft|published|archived
    shuffle = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        question_mode = (self.question_mode or '').strip().lower() or 'fixed'
        if question_mode == 'fixed' and self.draw_count:
          question_mode = 'random'
        return {
            'id': self.id,
            'title': self.title,
            'description': self.description,
            'questionIds': self.question_ids or [],
            'questionMode': question_mode,
            'drawCount': self.draw_count,
            'totalScore': money(self.total_score),
            'passScore': money(self.pass_score),
            'durationMinutes': self.duration_minutes,
            'status': self.status,
            'shuffle': bool(self.shuffle),
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
        }


class ExamTask(db.Model):
    """考试任务：一次考试活动（关联试卷 + 时间窗 + 指派目标）。"""
    __tablename__ = 'exam_tasks'

    id = db.Column(db.String(36), primary_key=True)
    paper_id = db.Column(db.String(36), db.ForeignKey('exam_papers.id', ondelete='SET NULL'), nullable=True)
    title = db.Column(db.String(200), nullable=False, index=True)
    description = db.Column(db.Text)
    start_time = db.Column(db.DateTime)
    end_time = db.Column(db.DateTime)
    max_attempts = db.Column(db.Integer, default=1)
    target_type = db.Column(db.String(10), default='dept')  # dept|user
    target_ids = db.Column(db.JSON)  # [dept_id,...] 或 [employee_id,...]
    status = db.Column(db.String(20), default='draft', index=True)  # draft|published|closed
    created_by = db.Column(db.String(36), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    paper = db.relationship('ExamPaper', foreign_keys=[paper_id])

    def to_dict(self):
        question_ids = list((self.paper.question_ids or []) if self.paper else [])
        question_mode = (self.paper.question_mode or '').strip().lower() if self.paper else 'fixed'
        if not question_mode:
            question_mode = 'fixed'
        if question_mode == 'fixed' and self.paper and self.paper.draw_count:
            question_mode = 'random'
        draw_count = self.paper.draw_count if self.paper else None
        question_count = draw_count if question_mode == 'random' and draw_count else len(question_ids)
        return {
            'id': self.id,
            'paperId': self.paper_id,
            'paperTitle': self.paper.title if self.paper else None,
            'questionMode': question_mode,
            'drawCount': draw_count,
            'poolCount': len(question_ids),
            'questionCount': question_count,
            'title': self.title,
            'description': self.description,
            'startTime': iso(self.start_time),
            'endTime': iso(self.end_time),
            'maxAttempts': self.max_attempts,
            'targetType': self.target_type,
            'targetIds': self.target_ids or [],
            'status': self.status,
            'createdBy': self.created_by,
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
        }


class ExamAssignment(db.Model):
    """考试授权：per-员工（任务发布时展开）。

    UniqueConstraint(task_id, employee_id) 保证一个员工在一项任务里只有一条授权。
    """
    __tablename__ = 'exam_assignments'
    __table_args__ = (
        db.UniqueConstraint('task_id', 'employee_id', name='uq_exam_assignment_task_employee'),
    )

    id = db.Column(db.String(36), primary_key=True)
    task_id = db.Column(db.String(36), db.ForeignKey('exam_tasks.id', ondelete='CASCADE'), nullable=False, index=True)
    employee_id = db.Column(db.String(36), db.ForeignKey('employees.id', ondelete='CASCADE'), nullable=False, index=True)
    attempts_used = db.Column(db.Integer, default=0)
    status = db.Column(db.String(20), default='assigned', index=True)  # assigned|passed|failed
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    task = db.relationship('ExamTask', foreign_keys=[task_id])
    employee = db.relationship('Employee', foreign_keys=[employee_id])

    def to_dict(self):
        return {
            'id': self.id,
            'taskId': self.task_id,
            'taskTitle': self.task.title if self.task else None,
            'employeeId': self.employee_id,
            'employeeName': self.employee.name if self.employee else None,
            'attemptsUsed': self.attempts_used,
            'status': self.status,
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
        }


class ExamAttempt(db.Model):
    """一次答题记录。

    - paper_snapshot: 提交时刻的完整试卷快照（含题干/选项/答案/分值），
      防止题库后续修改影响历史成绩。
    - answers: {questionId: [idx,...]}
    - status: doing | submitted
    """
    __tablename__ = 'exam_attempts'

    id = db.Column(db.String(36), primary_key=True)
    assignment_id = db.Column(db.String(36), db.ForeignKey('exam_assignments.id', ondelete='SET NULL'), nullable=True)
    employee_id = db.Column(db.String(36), db.ForeignKey('employees.id', ondelete='CASCADE'), nullable=False, index=True)
    task_id = db.Column(db.String(36), db.ForeignKey('exam_tasks.id', ondelete='CASCADE'), nullable=True, index=True)
    paper_id = db.Column(db.String(36), db.ForeignKey('exam_papers.id', ondelete='SET NULL'), nullable=True)
    paper_snapshot = db.Column(db.JSON)  # [{id,type,options,answer,score,...}]
    answers = db.Column(db.JSON)
    score = db.Column(db.Numeric(8, 2), default=None)
    total_score = db.Column(db.Numeric(8, 2), default=None)
    pass_score = db.Column(db.Numeric(8, 2), default=None)
    passed = db.Column(db.Boolean, default=None)
    status = db.Column(db.String(20), default='doing', index=True)  # doing|submitted
    started_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    submitted_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    employee = db.relationship('Employee', foreign_keys=[employee_id])
    task = db.relationship('ExamTask', foreign_keys=[task_id])

    def to_dict(self, include_snapshot=False):
        d = {
            'id': self.id,
            'assignmentId': self.assignment_id,
            'employeeId': self.employee_id,
            'employeeName': self.employee.name if self.employee else None,
            'taskId': self.task_id,
            'paperId': self.paper_id,
            'score': money(self.score),
            'totalScore': money(self.total_score),
            'passScore': money(self.pass_score),
            'passed': self.passed,
            'status': self.status,
            'startedAt': iso(self.started_at),
            'submittedAt': iso(self.submitted_at),
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
        }
        if include_snapshot:
            d['paperSnapshot'] = self.paper_snapshot or []
            d['answers'] = self.answers or {}
        return d


# ===========================================================================
# 学习地图 / 学习路径（参照钉钉/酷学院，把课程按岗位串成成长路径）
# 阶段结构用 JSON 存储：[{name, courseIds: [], examTaskId?}]，避免多表 join
# ===========================================================================


class LearningPath(db.Model):
    """学习路径：按岗位/阶段组织的课程序列。

    stages JSON 结构示例:
    [
      {"name": "入职基础", "courseIds": ["c1", "c2"], "examTaskId": null},
      {"name": "岗位技能", "courseIds": ["c3"], "examTaskId": "t1"},
      {"name": "进阶认证", "courseIds": ["c4"], "examTaskId": "t2"}
    ]
    员工需按阶段顺序学习，前序阶段课程全部完成后解锁下一阶段（闯关式）。
    """
    __tablename__ = 'learning_paths'

    id = db.Column(db.String(36), primary_key=True)
    name = db.Column(db.String(200), nullable=False, index=True)
    description = db.Column(db.Text)
    cover_url = db.Column(db.String(500))
    target_position = db.Column(db.String(120), index=True)  # 目标岗位，如"前端工程师"
    target_department_id = db.Column(db.String(36), db.ForeignKey('departments.id', ondelete='SET NULL'), nullable=True)
    stages = db.Column(db.JSON)  # [{name, courseIds: [], examTaskId?}]
    status = db.Column(db.String(20), default='draft', index=True)  # draft|published|archived
    created_by = db.Column(db.String(36), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    department = db.relationship('Department', foreign_keys=[target_department_id])

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'coverUrl': self.cover_url,
            'targetPosition': self.target_position,
            'targetDepartmentId': self.target_department_id,
            'targetDepartmentName': self.department.name if self.department else None,
            'stages': self.stages or [],
            'stageCount': len(self.stages or []),
            'courseCount': sum(len(s.get('courseIds') or []) for s in (self.stages or [])),
            'status': self.status,
            'createdBy': self.created_by,
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
        }


class LearningPathAssignment(db.Model):
    """学习路径指派：路径发布时展开为 per-员工授权。"""
    __tablename__ = 'learning_path_assignments'

    id = db.Column(db.String(36), primary_key=True)
    path_id = db.Column(db.String(36), db.ForeignKey('learning_paths.id', ondelete='CASCADE'), nullable=False, index=True)
    employee_id = db.Column(db.String(36), db.ForeignKey('employees.id', ondelete='CASCADE'), nullable=False, index=True)
    current_stage = db.Column(db.Integer, default=0)  # 当前所在阶段索引（0-based）
    progress = db.Column(db.Integer, default=0)  # 整体进度 0-100
    is_completed = db.Column(db.Boolean, default=False, index=True)
    completed_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    path = db.relationship('LearningPath', foreign_keys=[path_id])
    employee = db.relationship('Employee', foreign_keys=[employee_id])

    def to_dict(self):
        return {
            'id': self.id,
            'pathId': self.path_id,
            'pathName': self.path.name if self.path else None,
            'employeeId': self.employee_id,
            'employeeName': self.employee.name if self.employee else None,
            'currentStage': self.current_stage,
            'progress': self.progress,
            'isCompleted': bool(self.is_completed),
            'completedAt': iso(self.completed_at),
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
        }


class WrongQuestion(db.Model):
    """错题本：员工考试中做错的题目自动采集 + 收藏/重练状态。

    一道题对同一员工只存一条（去重），重复做错时累加 wrong_count。
    mastery: 'wrong'(未掌握) | 'mastered'(已掌握，移出错题本)
    """
    __tablename__ = 'wrong_questions'

    id = db.Column(db.String(36), primary_key=True)
    employee_id = db.Column(db.String(36), db.ForeignKey('employees.id', ondelete='CASCADE'), nullable=False, index=True)
    question_id = db.Column(db.String(36), db.ForeignKey('exam_questions.id', ondelete='CASCADE'), nullable=False, index=True)
    # 冗余快照（题库改了不影响错题本回顾）
    snapshot = db.Column(db.JSON)  # {type, stem, options, answer, explanation, score, difficulty}
    user_answer = db.Column(db.JSON)  # 员工当时答错的答案索引
    course_id = db.Column(db.String(36), index=True)  # 归类用（题目关联的课程）
    category = db.Column(db.String(80), index=True)  # 题目分类
    wrong_count = db.Column(db.Integer, default=1)  # 累计答错次数
    is_favorited = db.Column(db.Boolean, default=False, index=True)  # 收藏（重点题）
    mastery = db.Column(db.String(20), default='wrong', index=True)  # wrong|mastered
    last_wrong_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    employee = db.relationship('Employee', foreign_keys=[employee_id])

    def to_dict(self, include_snapshot=True):
        d = {
            'id': self.id,
            'employeeId': self.employee_id,
            'questionId': self.question_id,
            'userAnswer': self.user_answer or [],
            'courseId': self.course_id,
            'category': self.category,
            'wrongCount': self.wrong_count,
            'isFavorited': bool(self.is_favorited),
            'mastery': self.mastery,
            'lastWrongAt': iso(self.last_wrong_at),
            'createdAt': iso(self.created_at),
        }
        if include_snapshot and self.snapshot:
            # 快照里的 id 是题目 id，不能覆盖错题记录的 id
            snap = {k: v for k, v in self.snapshot.items() if k != 'id'}
            d.update(snap)
        return d


class CourseReview(db.Model):
    """课程评价：总分 + 维度评分 + 文字反馈。

    一人一课一评价（UniqueConstraint），可修改覆盖。
    只有学完课程（is_finished）的员工才能评价。
    维度：content_quality（内容质量）、clarity（讲解清晰度）、practicality（实用性），各 1-5。
    """
    __tablename__ = 'course_reviews'
    __table_args__ = (
        db.UniqueConstraint('course_id', 'employee_id', name='uq_course_review_course_employee'),
    )

    id = db.Column(db.String(36), primary_key=True)
    course_id = db.Column(db.String(36), db.ForeignKey('courses.id', ondelete='CASCADE'), nullable=False, index=True)
    employee_id = db.Column(db.String(36), db.ForeignKey('employees.id', ondelete='CASCADE'), nullable=False, index=True)
    rating = db.Column(db.Integer, nullable=False)  # 总分 1-5
    content_quality = db.Column(db.Integer)  # 内容质量 1-5
    clarity = db.Column(db.Integer)  # 讲解清晰度 1-5
    practicality = db.Column(db.Integer)  # 实用性 1-5
    comment = db.Column(db.Text)  # 文字反馈
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    course = db.relationship('Course', foreign_keys=[course_id])
    employee = db.relationship('Employee', foreign_keys=[employee_id])

    def to_dict(self):
        return {
            'id': self.id,
            'courseId': self.course_id,
            'courseTitle': self.course.title if self.course else None,
            'employeeId': self.employee_id,
            'employeeName': self.employee.name if self.employee else None,
            'rating': self.rating,
            'contentQuality': self.content_quality,
            'clarity': self.clarity,
            'practicality': self.practicality,
            'comment': self.comment,
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
        }


# ===========================================================================
# 积分激励体系（游戏化：完成课程/考试/错题掌握 → 积分 → 排行榜）
# ===========================================================================

# 积分规则常量（集中管理，方便调整）
POINT_RULES = {
    'course_hour_finished': 5,       # 学完一个课时
    'course_completed': 20,          # 完成整门课程
    'exam_passed': 50,               # 考试通过
    'exam_attempted': 10,            # 参加考试（无论是否通过）
    'competency_assessed': 15,       # 完成胜任力测评
    'review_submitted': 5,           # 提交课程评价
}


class PointLog(db.Model):
    """积分日志：记录员工每次积分获取。

    action: POINT_RULES 的 key
    points: 本次获得的积分数（正数）
    ref_id: 关联对象 id（课程/考试/错题 id，用于去重防止重复奖励）
    UniqueConstraint(employee_id, action, ref_id) 防同一行为重复计分。
    """
    __tablename__ = 'point_logs'
    __table_args__ = (
        db.UniqueConstraint('employee_id', 'action', 'ref_id', name='uq_point_log_dedup'),
    )

    id = db.Column(db.String(36), primary_key=True)
    employee_id = db.Column(db.String(36), db.ForeignKey('employees.id', ondelete='CASCADE'), nullable=False, index=True)
    action = db.Column(db.String(40), nullable=False)  # POINT_RULES key
    points = db.Column(db.Integer, default=0)
    ref_id = db.Column(db.String(36), default='', nullable=False)  # 去重用（空串表示无具体对象）
    detail = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    employee = db.relationship('Employee', foreign_keys=[employee_id])

    def to_dict(self):
        return {
            'id': self.id,
            'employeeId': self.employee_id,
            'action': self.action,
            'actionLabel': ACTION_LABELS.get(self.action, self.action),
            'points': self.points,
            'refId': self.ref_id,
            'detail': self.detail,
            'createdAt': iso(self.created_at),
        }


ACTION_LABELS = {
    'course_hour_finished': '完成课时',
    'course_completed': '完成课程',
    'exam_passed': '考试通过',
    'exam_attempted': '参加考试',
    'wrong_question_mastered': '掌握错题',
    'competency_assessed': '能力测评',
    'review_submitted': '课程评价',
    'reward_exchange': '积分兑换',
}


class Certificate(db.Model):
    """学习证书：课程完成或考试通过后自动颁发。

    Pillow 渲染 PNG（模板背景 + 姓名/课程/日期/编号），存到 uploads/certificates/。
    cert_no 全局唯一（CERT-年-流水号），用于验证防伪。
    """
    __tablename__ = 'certificates'

    id = db.Column(db.String(36), primary_key=True)
    employee_id = db.Column(db.String(36), db.ForeignKey('employees.id', ondelete='CASCADE'), nullable=False, index=True)
    course_id = db.Column(db.String(36), db.ForeignKey('courses.id', ondelete='SET NULL'), nullable=True, index=True)
    employee_name = db.Column(db.String(80))
    course_title = db.Column(db.String(200))
    cert_no = db.Column(db.String(40), unique=True, nullable=False, index=True)  # CERT-2026-000001
    image_url = db.Column(db.String(500))  # /uploads/certificates/xxx.png
    issued_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    employee = db.relationship('Employee', foreign_keys=[employee_id])

    def to_dict(self):
        return {
            'id': self.id,
            'employeeId': self.employee_id,
            'employeeName': self.employee_name,
            'courseId': self.course_id,
            'courseTitle': self.course_title,
            'certNo': self.cert_no,
            'imageUrl': self.image_url,
            'issuedAt': iso(self.issued_at),
            'createdAt': iso(self.created_at),
        }


# ===========================================================================
# 课程讨论区/学习社区（发帖 + 回复 + 点赞）
# ===========================================================================


class Discussion(db.Model):
    """课程讨论帖。与课程绑定，员工可见可发帖。"""
    __tablename__ = 'discussions'

    id = db.Column(db.String(36), primary_key=True)
    course_id = db.Column(db.String(36), db.ForeignKey('courses.id', ondelete='CASCADE'), nullable=False, index=True)
    employee_id = db.Column(db.String(36), db.ForeignKey('employees.id', ondelete='CASCADE'), nullable=False, index=True)
    title = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text, nullable=False)
    is_pinned = db.Column(db.Boolean, default=False)  # 置顶（管理员）
    like_count = db.Column(db.Integer, default=0)
    reply_count = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    course = db.relationship('Course', foreign_keys=[course_id])
    employee = db.relationship('Employee', foreign_keys=[employee_id])

    def to_dict(self, include_replies=False, name_map=None):
        """name_map: 可选 {employee_id: name}，避免批量场景下懒加载 employee 的 N+1。"""
        d = {
            'id': self.id,
            'courseId': self.course_id,
            'courseTitle': self.course.title if self.course else None,
            'employeeId': self.employee_id,
            'employeeName': (name_map or {}).get(self.employee_id) if name_map is not None
                            else (self.employee.name if self.employee else None),
            'title': self.title,
            'content': self.content,
            'isPinned': bool(self.is_pinned),
            'likeCount': self.like_count,
            'replyCount': self.reply_count,
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
        }
        if include_replies:
            replies = DiscussionReply.query.filter_by(
                discussion_id=self.id
            ).order_by(DiscussionReply.created_at.asc()).all()
            # 批量取回复人姓名（消除 r.to_dict 内逐条懒加载 employee 的 N+1）
            reply_emp_ids = {r.employee_id for r in replies if r.employee_id}
            reply_name_map = dict(name_map) if name_map else {}
            missing = reply_emp_ids - set(reply_name_map.keys())
            if missing:
                from models import Employee as _Emp
                for eid, nm in _Emp.query.filter(_Emp.id.in_(missing)).with_entities(_Emp.id, _Emp.name).all():
                    reply_name_map[eid] = nm
            d['replies'] = [r.to_dict(name_map=reply_name_map) for r in replies]
        return d


class DiscussionReply(db.Model):
    """讨论帖回复。"""
    __tablename__ = 'discussion_replies'

    id = db.Column(db.String(36), primary_key=True)
    discussion_id = db.Column(db.String(36), db.ForeignKey('discussions.id', ondelete='CASCADE'), nullable=False, index=True)
    employee_id = db.Column(db.String(36), db.ForeignKey('employees.id', ondelete='CASCADE'), nullable=False, index=True)
    content = db.Column(db.Text, nullable=False)
    like_count = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    employee = db.relationship('Employee', foreign_keys=[employee_id])

    def to_dict(self, name_map=None):
        return {
            'id': self.id,
            'discussionId': self.discussion_id,
            'employeeId': self.employee_id,
            'employeeName': (name_map or {}).get(self.employee_id) if name_map is not None
                            else (self.employee.name if self.employee else None),
            'content': self.content,
            'likeCount': self.like_count,
            'createdAt': iso(self.created_at),
        }


class DiscussionLike(db.Model):
    """点赞记录（帖子和回复共用，去重）。"""
    __tablename__ = 'discussion_likes'
    __table_args__ = (
        db.UniqueConstraint('employee_id', 'target_type', 'target_id', name='uq_discussion_like'),
    )

    id = db.Column(db.String(36), primary_key=True)
    employee_id = db.Column(db.String(36), db.ForeignKey('employees.id', ondelete='CASCADE'), nullable=False)
    target_type = db.Column(db.String(10), nullable=False)  # post | reply
    target_id = db.Column(db.String(36), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


# ===========================================================================
# 团队学习 PK 竞赛（部门间学习竞技，按赛季聚合积分排名）
# ===========================================================================


class PKSeason(db.Model):
    """PK 赛季：一段时间内的部门学习竞赛。

    参赛部门在赛季时间窗内的员工积分汇总排名。
    metric: 'total_points'(总积分) | 'avg_points'(人均积分) | 'completion_rate'(完成率)
    """
    __tablename__ = 'pk_seasons'

    id = db.Column(db.String(36), primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    start_date = db.Column(db.DateTime, nullable=False)
    end_date = db.Column(db.DateTime, nullable=False)
    metric = db.Column(db.String(30), default='total_points')  # total_points|avg_points
    participant_dept_ids = db.Column(db.JSON)  # [dept_id,...] 空=全部部门
    status = db.Column(db.String(20), default='active', index=True)  # active|ended
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'startDate': iso(self.start_date),
            'endDate': iso(self.end_date),
            'metric': self.metric,
            'participantDeptIds': self.participant_dept_ids or [],
            'status': self.status,
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
        }


# ===========================================================================
# 积分商城（积分消耗闭环：兑换奖品/虚拟权益）
# ===========================================================================


class Reward(db.Model):
    """积分商城商品。

    reward_type: virtual(虚拟:兑换码/权益) | physical(实物) | badge(徽章)
    stock: -1 表示无限
    """
    __tablename__ = 'rewards'

    id = db.Column(db.String(36), primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text)
    icon = db.Column(db.String(50), default='fa-gift')  # FontAwesome 图标
    reward_type = db.Column(db.String(20), default='virtual')  # virtual|physical|badge
    points_cost = db.Column(db.Integer, nullable=False)  # 兑换所需积分
    stock = db.Column(db.Integer, default=-1)  # -1=无限
    exchanged_count = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'icon': self.icon,
            'rewardType': self.reward_type,
            'pointsCost': self.points_cost,
            'stock': self.stock,
            'exchangedCount': self.exchanged_count,
            'isActive': bool(self.is_active),
            'inStock': self.stock == -1 or self.stock > 0,
            'createdAt': iso(self.created_at),
        }


class ExchangeRecord(db.Model):
    """积分兑换记录。"""
    __tablename__ = 'exchange_records'

    id = db.Column(db.String(36), primary_key=True)
    employee_id = db.Column(db.String(36), db.ForeignKey('employees.id', ondelete='CASCADE'), nullable=False, index=True)
    reward_id = db.Column(db.String(36), db.ForeignKey('rewards.id', ondelete='SET NULL'), nullable=True)
    reward_name = db.Column(db.String(120))
    points_cost = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(20), default='completed')  # completed|pending|shipped
    redeem_code = db.Column(db.String(100))  # 虚拟奖品的兑换码
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    employee = db.relationship('Employee', foreign_keys=[employee_id])

    def to_dict(self):
        return {
            'id': self.id,
            'employeeId': self.employee_id,
            'employeeName': self.employee.name if self.employee else None,
            'rewardId': self.reward_id,
            'rewardName': self.reward_name,
            'pointsCost': self.points_cost,
            'status': self.status,
            'redeemCode': self.redeem_code,
            'createdAt': iso(self.created_at),
        }


# ===========================================================================
# 岗位胜任力测评（测训一体：测评→短板→推送学习→再测评）
# ===========================================================================


class CompetencyModel(db.Model):
    """岗位胜任力模型：定义某岗位所需的能力维度及要求等级。

    dimensions JSON: [{key, name, requiredLevel(1-5), description}]
    如 研发工程师 = [
      {key:'tech', name:'专业技术', requiredLevel:4, description:'编程/架构能力'},
      {key:'comm', name:'沟通协作', requiredLevel:3, description:'团队沟通'},
      {key:'exec', name:'执行力', requiredLevel:4, description:'任务交付'},
    ]
    """
    __tablename__ = 'competency_models'

    id = db.Column(db.String(36), primary_key=True)
    name = db.Column(db.String(120), nullable=False, index=True)  # 岗位名称
    description = db.Column(db.Text)
    dimensions = db.Column(db.JSON)  # [{key,name,requiredLevel,description}]
    status = db.Column(db.String(20), default='active', index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'dimensions': self.dimensions or [],
            'dimensionCount': len(self.dimensions or []),
            'status': self.status,
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
        }


class CompetencyAssessment(db.Model):
    """员工胜任力测评结果：每个能力维度的实际得分。

    scores JSON: {dimensionKey: actualLevel(1-5)}
    gaps JSON: {dimensionKey: gap}（requiredLevel - actualLevel，负数归零）
    """
    __tablename__ = 'competency_assessments'

    id = db.Column(db.String(36), primary_key=True)
    employee_id = db.Column(db.String(36), db.ForeignKey('employees.id', ondelete='CASCADE'), nullable=False, index=True)
    model_id = db.Column(db.String(36), db.ForeignKey('competency_models.id', ondelete='CASCADE'), nullable=False, index=True)
    scores = db.Column(db.JSON)  # {dimKey: level}
    gaps = db.Column(db.JSON)  # {dimKey: gap}
    overall_score = db.Column(db.Integer, default=0)  # 综合达标率 0-100
    weakest_dims = db.Column(db.JSON)  # [dimKey,...] 短板维度（gap 最大的）
    assessor = db.Column(db.String(36), nullable=True)  # 测评人（null=自评）
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    employee = db.relationship('Employee', foreign_keys=[employee_id])
    model = db.relationship('CompetencyModel', foreign_keys=[model_id])

    def to_dict(self, include_model=False):
        d = {
            'id': self.id,
            'employeeId': self.employee_id,
            'employeeName': self.employee.name if self.employee else None,
            'modelId': self.model_id,
            'scores': self.scores or {},
            'gaps': self.gaps or {},
            'overallScore': self.overall_score,
            'weakestDims': self.weakest_dims or [],
            'assessor': self.assessor,
            'createdAt': iso(self.created_at),
        }
        if include_model and self.model:
            d['modelName'] = self.model.name
            d['dimensions'] = self.model.dimensions or []
        return d


class Role(db.Model):
    __tablename__ = 'roles'
    __table_args__ = (
        db.UniqueConstraint('tenant_id', 'code', name='uq_roles_tenant_code'),
    )

    id = db.Column(db.String(36), primary_key=True)
    tenant_id = db.Column(db.String(36), db.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=True, index=True)
    code = db.Column(db.String(60), nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    permissions = db.Column(db.JSON)
    status = db.Column(db.String(40), default='active', index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'code': self.code,
            'name': self.name,
            'description': self.description,
            'permissions': self.permissions or [],
            'status': self.status,
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
        }


class OperationLog(db.Model):
    __tablename__ = 'operation_logs'

    id = db.Column(db.String(36), primary_key=True)
    user_id = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='SET NULL'))
    module = db.Column(db.String(100), index=True)
    action = db.Column(db.String(80), index=True)
    target_id = db.Column(db.String(36))
    detail = db.Column(db.JSON)
    ip = db.Column(db.String(80))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    user = db.relationship('User', foreign_keys=[user_id])

    def to_dict(self):
        return {
            'id': self.id,
            'userId': self.user_id,
            'userName': self.user.name if self.user else None,
            'module': self.module,
            'action': self.action,
            'targetId': self.target_id,
            'detail': self.detail or {},
            'ip': self.ip,
            'createdAt': iso(self.created_at),
        }


class Notification(db.Model):
    __tablename__ = 'notifications'

    id = db.Column(db.String(36), primary_key=True)
    user_id = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='CASCADE'), nullable=True, index=True)
    employee_id = db.Column(db.String(36), db.ForeignKey('employees.id', ondelete='CASCADE'), nullable=True, index=True)
    title = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text)
    notice_type = db.Column(db.String(60), default='system')
    status = db.Column(db.String(40), default='unread', index=True)
    link_url = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    read_at = db.Column(db.DateTime)

    user = db.relationship('User', foreign_keys=[user_id])
    employee = db.relationship('Employee', foreign_keys=[employee_id])

    def to_dict(self):
        return {
            'id': self.id,
            'userId': self.user_id,
            'userName': self.user.name if self.user else None,
            'employeeId': self.employee_id,
            'employeeName': self.employee.name if self.employee else None,
            'title': self.title,
            'content': self.content,
            'noticeType': self.notice_type,
            'status': self.status,
            'linkUrl': self.link_url,
            'createdAt': iso(self.created_at),
            'readAt': iso(self.read_at),
        }


class Feedback(db.Model):
    """用户问题反馈：缺陷、需求、其他类型问题收集。"""
    __tablename__ = 'feedbacks'

    id = db.Column(db.String(36), primary_key=True)
    user_id = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True)
    feedback_type = db.Column(db.String(40), default='bug', index=True)  # bug | feature | other
    severity = db.Column(db.String(20), default='medium')  # low | medium | high | critical
    title = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text, nullable=False)
    page_url = db.Column(db.String(500))
    user_agent = db.Column(db.String(500))
    attachments = db.Column(db.JSON)
    status = db.Column(db.String(40), default='pending', index=True)  # pending | in_progress | resolved | closed
    handler_id = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    handler_reply = db.Column(db.Text)
    handled_at = db.Column(db.DateTime)
    reopen_reason = db.Column(db.Text)
    reopen_current_state = db.Column(db.Text)
    reopen_history = db.Column(db.JSON, default=list)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = db.relationship('User', foreign_keys=[user_id])
    handler = db.relationship('User', foreign_keys=[handler_id])

    def to_dict(self):
        return {
            'id': self.id,
            'userId': self.user_id,
            'userName': self.user.name if self.user else None,
            'userEmail': self.user.email if self.user else None,
            'feedbackType': self.feedback_type,
            'severity': self.severity,
            'title': self.title,
            'content': self.content,
            'pageUrl': self.page_url,
            'userAgent': self.user_agent,
            'attachments': self.attachments or [],
            'status': self.status,
            'handlerId': self.handler_id,
            'handlerName': self.handler.name if self.handler else None,
            'handlerReply': self.handler_reply,
            'handledAt': iso(self.handled_at),
            'reopenReason': self.reopen_reason,
            'reopenCurrentState': self.reopen_current_state,
            'reopenHistory': self.reopen_history or [],
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
        }



class AssistantSession(db.Model):
    """智能助手对话会话。每个用户可以有多个会话，每个会话保留有限轮次的对话上下文。

    - 用户隔离：通过 user_id 严格区分，禁止跨用户读取
    - 自动归档：会话消息超过阈值时前端可主动新建会话
    - messages 是一个列表 [{role, content, tool_calls?, ...}]，按时间顺序追加
    """
    __tablename__ = 'assistant_sessions'

    id = db.Column(db.String(36), primary_key=True)
    user_id = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    title = db.Column(db.String(200))  # 第一条用户消息作为标题
    messages = db.Column(db.JSON, nullable=False, default=list)  # 存储完整对话
    is_active = db.Column(db.Boolean, default=True, index=True)  # 当前活跃会话
    message_count = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = db.relationship('User', foreign_keys=[user_id])

    def to_dict(self, include_messages=False):
        d = {
            'id': self.id,
            'userId': self.user_id,
            'title': self.title,
            'isActive': self.is_active,
            'messageCount': self.message_count,
            'createdAt': iso(self.created_at),
            'updatedAt': iso(self.updated_at),
        }
        if include_messages:
            d['messages'] = self.messages or []
        return d


TENANT_SCOPED_MODELS = (
    User,
    TenantAIEntitlement,
    AIUsageLedger,
    HeadcountPlan,
    HeadcountChange,
    PeopleAnalyticsSnapshot,
    TalentStrategySnapshot,
    AnalyticsInsightSnapshot,
    IntelligenceInsightSnapshot,
    CollaborationInsightSnapshot,
    WorkforceInsightSnapshot,
    IntegrationInsightSnapshot,
    MobileInsightSnapshot,
    SecurityInsightSnapshot,
    ContinuousFeedbackEntry,
    Job,
    Resume,
    Interview,
    Setting,
    TenantSetting,
    AssistantAuditLog,
    AssistantEvaluationRun,
    Department,
    Position,
    Candidate,
    Offer,
    Employee,
    OaEmployeeSyncRun,
    EmployeePortalAccount,
    OnboardingTask,
    Probation,
    EmployeeChange,
    Resignation,
    Contract,
    AttendanceRecord,
    AttendanceShift,
    AttendanceRuleSet,
    AttendanceGroup,
    AttendanceGroupMembership,
    AttendanceHoliday,
    AttendanceSchedule,
    AttendanceDailyResult,
    AttendanceSyncRun,
    AttendanceExternalPerson,
    AttendanceRawPunch,
    AttendanceEvent,
    EmployeeRequest,
    ApprovalInstance,
    ApprovalRecord,
    PerformanceCycle,
    PerformanceTemplate,
    PerformanceReview,
    SalaryProfile,
    PayrollRecord,
    TrainingCourse,
    TrainingPlan,
    TrainingRecord,
    CourseCategory,
    Course,
    CourseChapter,
    CourseHour,
    CourseAttachment,
    CourseAssignment,
    UserCourseProgress,
    UserHourProgress,
    ExamQuestion,
    ExamPaper,
    ExamTask,
    ExamAssignment,
    ExamAttempt,
    LearningPath,
    LearningPathAssignment,
    WrongQuestion,
    CourseReview,
    PointLog,
    Certificate,
    Discussion,
    DiscussionReply,
    DiscussionLike,
    PKSeason,
    Reward,
    ExchangeRecord,
    CompetencyModel,
    CompetencyAssessment,
    Role,
    OperationLog,
    Notification,
    Feedback,
    AssistantSession,
)

for _tenant_model in TENANT_SCOPED_MODELS:
    if not hasattr(_tenant_model, 'tenant_id'):
        _tenant_model.tenant_id = db.Column(
            db.String(36),
            db.ForeignKey('tenants.id', ondelete='CASCADE'),
            nullable=True,
            index=True,
        )
