"""Authoritative runtime decisions for tenant platform entitlements."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from models import Tenant, TenantSubscription


INACTIVE_TENANT_STATUSES = frozenset({'inactive', 'suspended', 'expired'})
INACTIVE_SUBSCRIPTION_STATUSES = frozenset({'suspended', 'cancelled', 'expired'})


@dataclass(frozen=True)
class TenantAccessDecision:
    allowed: bool
    code: str
    message: str
    source: str
    effective_expires_at: datetime | None


def latest_tenant_subscription(tenant_id: str) -> TenantSubscription | None:
    """Return the newest subscription with a stable tie-break for equal timestamps."""
    return (
        TenantSubscription.query
        .filter_by(tenant_id=tenant_id)
        .order_by(TenantSubscription.created_at.desc(), TenantSubscription.id.desc())
        .first()
    )


def _utc_naive(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def effective_tenant_access(
    tenant: Tenant,
    *,
    now: datetime | None = None,
) -> TenantAccessDecision:
    """Resolve persisted tenant and latest-subscription state into one access policy."""
    checked_at = _utc_naive(now) if now is not None else datetime.utcnow()
    subscription = latest_tenant_subscription(tenant.id)
    tenant_expires_at = _utc_naive(tenant.expires_at)
    subscription_expires_at = _utc_naive(subscription.ends_at) if subscription else None
    expiries = [value for value in (tenant_expires_at, subscription_expires_at) if value]
    effective_expiry = min(expiries) if expiries else None

    tenant_status = (tenant.status or '').strip().lower()
    if tenant_status in INACTIVE_TENANT_STATUSES:
        return TenantAccessDecision(
            False,
            'TENANT_INACTIVE',
            '企业服务已停用或到期，请联系平台管理员',
            'tenant_status',
            effective_expiry,
        )

    if tenant_expires_at is not None and tenant_expires_at <= checked_at:
        return TenantAccessDecision(
            False,
            'TENANT_SUBSCRIPTION_EXPIRED',
            '企业订阅已到期，请联系平台管理员续费',
            'effective_expiry',
            effective_expiry,
        )

    subscription_status = (subscription.status or '').strip().lower() if subscription else ''
    if subscription_status in INACTIVE_SUBSCRIPTION_STATUSES:
        return TenantAccessDecision(
            False,
            'TENANT_SUBSCRIPTION_INACTIVE',
            '企业订阅当前不可用，请联系平台管理员',
            'subscription_status',
            effective_expiry,
        )

    if subscription_expires_at is not None and subscription_expires_at <= checked_at:
        return TenantAccessDecision(
            False,
            'TENANT_SUBSCRIPTION_EXPIRED',
            '企业订阅已到期，请联系平台管理员续费',
            'effective_expiry',
            effective_expiry,
        )

    return TenantAccessDecision(
        True,
        'TENANT_ACTIVE',
        '企业服务可用',
        'effective_state',
        effective_expiry,
    )
