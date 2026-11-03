import { randomUUID } from 'node:crypto';
import { enqueuePersonLifecycleEvent, updatePersonLifecycleOutbox } from './person-lifecycle-outbox.js';

const EVENT_TYPES = {
  offboard: { event_type: 'person.offboarded', status: 'inactive', employment_action: 'offboard' },
  reinstate: { event_type: 'person.reinstated', status: 'active', employment_action: 'rehire' },
  rehire: { event_type: 'person.reinstated', status: 'active', employment_action: 'rehire' }
};

function requireText(value, field) {
  if (typeof value !== 'string' || !value.trim()) throw new Error(`${field} is required`);
  return value.trim();
}

function iso(value, field) {
  const parsed = new Date(value || Date.now());
  if (Number.isNaN(parsed.getTime())) throw new Error(`${field} must be a valid date`);
  return parsed.toISOString();
}

function lifecycleConfig(action) {
  const config = EVENT_TYPES[String(action || '').toLowerCase()];
  if (!config) throw new Error('action must be offboard or reinstate');
  return config;
}

export function createPersonLifecycleEvent({
  action,
  tenantId,
  person,
  revision,
  effectiveAt,
  correlationId,
  actor,
  traceId = correlationId || randomUUID()
}) {
  const config = lifecycleConfig(action);
  const tenant_id = requireText(tenantId, 'tenantId');
  const person_id = requireText(person?.person_id, 'person.person_id');
  const emp_code = requireText(person?.emp_code, 'person.emp_code');
  const numericRevision = Number(revision);
  if (!Number.isInteger(numericRevision) || numericRevision < 1) throw new Error('revision must be a positive integer');
  const event_type = config.event_type;
  return {
    event_id: `EVT_PERSON_${randomUUID()}`,
    event_type,
    event_version: 1,
    tenant_id,
    source_system: 'M01_KERNEL',
    object_type: 'Person',
    object_id: person_id,
    revision: numericRevision,
    occurred_at: new Date().toISOString(),
    effective_at: iso(effectiveAt, 'effectiveAt'),
    correlation_id: requireText(correlationId || traceId, 'correlationId'),
    trace_id: requireText(traceId, 'traceId'),
    idempotency_key: `${tenant_id}:M01_KERNEL:Person:${person_id}:${numericRevision}:${event_type}`,
    person: { person_id, emp_code },
    status: config.status,
    employment_action: config.employment_action,
    occurred_by: requireText(actor, 'actor')
  };
}

function currentRevision(kernel, personId) {
  const person = kernel.persons.get(personId);
  const outboxMax = (kernel.personLifecycleOutbox || [])
    .filter((entry) => entry.object_id === personId)
    .reduce((max, entry) => Math.max(max, Number(entry.revision) || 0), 0);
  return Math.max(Number(person?.lifecycle_revision) || 0, outboxMax);
}

function applyReinstatement(kernel, event, person) {
  if (kernel.events.has(event.event_id)) return { ok: true, idempotent: true, applied: false, state: 'active', event_id: event.event_id };
  kernel.events.set(event.event_id, { source: 'm01', type: event.event_type, at: new Date().toISOString() });
  person.status = 'active';
  person.position_id = null;
  person.profile = { ...(person.profile || {}), source_status: 'active' };
  person.seq = Math.max(Number(person.seq) || 0, event.revision);
  person.lifecycle_revision = event.revision;
  kernel.revocations.delete(event.person.person_id);
  kernel._bumpAuthorizationVersion(event.person.person_id, 'person.reinstated', event.trace_id);
  kernel._audit({ kind: 'm01.person.reinstated', event_id: event.event_id, person_id: event.person.person_id, pending_confirmation: true });
  return { ok: true, idempotent: false, applied: true, state: 'active', event_id: event.event_id, pending_confirmation: true };
}

export function applyPersonLifecycleEvent(kernel, event) {
  if (!event || !event.event_id || !event.tenant_id || !event.person || !event.object_id) {
    return { ok: false, status: 'failed', error: 'invalid person lifecycle event' };
  }
  const person = kernel.persons.get(event.object_id);
  if (!person) return { ok: false, status: 'failed', error: 'person not found' };
  if (person.tenant_id !== event.tenant_id) return { ok: false, status: 'failed', error: 'tenant mismatch' };
  const existing = (kernel.personLifecycleOutbox || []).find((item) => item.event_id === event.event_id);
  const revision = Number(event.revision);
  const current = currentRevision(kernel, event.object_id);
  if (revision < current || (revision === current && !existing)) {
    return { ok: true, status: 'stale', state: 'stale', event_id: event.event_id, applied: false };
  }
  const effectiveAt = new Date(event.effective_at).getTime();
  if (Number.isNaN(effectiveAt)) return { ok: false, status: 'failed', error: 'invalid effective_at' };
  if (effectiveAt > Date.now()) {
    enqueuePersonLifecycleEvent(kernel, event, 'scheduled');
    return { ok: true, status: 'scheduled', state: 'scheduled', event_id: event.event_id, applied: false };
  }
  if (existing?.delivery_status === 'applied' || kernel.events.has(event.event_id)) {
    return { ok: true, status: 'synced', state: person.status, event_id: event.event_id, applied: false, idempotent: true };
  }
  let applied;
  if (event.event_type === 'person.offboarded') {
    applied = kernel.applyM07Event({
      event_id: event.event_id,
      tenant_id: event.tenant_id,
      type: 'person.offboarded',
      person_id: event.object_id,
      // M07 的 seq 是人员资料变更序号，不能因为生命周期 revision 从 1 开始就被已有入职事件挡住。
      seq: Math.max(revision, (Number(person.seq) || 0) + 1),
      reason: 'M01 person lifecycle event'
    });
    if (applied.ok === false) return { ok: false, status: 'failed', error: applied.error };
    person.lifecycle_revision = revision;
    applied = { ...applied, state: 'offboarded' };
  } else if (event.event_type === 'person.reinstated') {
    applied = applyReinstatement(kernel, event, person);
  } else {
    return { ok: false, status: 'failed', error: `unsupported event_type: ${event.event_type}` };
  }
  enqueuePersonLifecycleEvent(kernel, event, 'pending');
  updatePersonLifecycleOutbox(kernel, event.event_id, { delivery_status: 'applied', applied_at: new Date().toISOString() });
  return { ok: true, status: applied.pending_confirmation ? 'pending_confirmation' : 'synced', ...applied };
}

export function applyPersonLifecycleTransition(kernel, personId, action, effectiveAt, actor, correlationId = randomUUID()) {
  const person = kernel.persons.get(personId);
  if (!person) return { ok: false, status: 'failed', error: 'person not found' };
  const normalizedAction = String(action || '').toLowerCase();
  if (normalizedAction === 'offboard' && person.status === 'offboarded') {
    return { ok: true, state: 'offboarded', idempotent: true, revision: currentRevision(kernel, personId) };
  }
  if ((normalizedAction === 'reinstate' || normalizedAction === 'rehire') && person.status === 'active') {
    return { ok: true, state: 'active', idempotent: true, revision: currentRevision(kernel, personId) };
  }
  const revision = currentRevision(kernel, personId) + 1;
  const event = createPersonLifecycleEvent({
    action: normalizedAction,
    tenantId: person.tenant_id,
    person: { person_id: person.person_id, emp_code: person.profile?.employee_code || person.person_id },
    revision,
    effectiveAt,
    correlationId,
    actor
  });
  const result = applyPersonLifecycleEvent(kernel, event);
  if (result.ok === false) return result;
  return { ...result, state: result.state || (result.status === 'pending_confirmation' ? 'active' : result.status), revision, event, eventId: event.event_id };
}
