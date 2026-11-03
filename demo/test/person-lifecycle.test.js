import test from 'node:test';
import assert from 'node:assert/strict';

import { M01Kernel } from '../src/kernel.js';
import {
  applyPersonLifecycleEvent,
  applyPersonLifecycleTransition,
  createPersonLifecycleEvent
} from '../src/person-lifecycle-event.js';
import { buildPersonLifecycleRequest } from '../src/m01-ebms-sync.js';

function makeKernel() {
  const kernel = new M01Kernel();
  kernel.applyM07Event({
    event_id: 'HIRE-PERSON-1',
    tenant_id: 'TENANT_A',
    type: 'person.hired',
    person_id: 'P_PERSON_1',
    position_id: 'POS_STAFF',
    seq: 1,
    profile: { employee_code: '19', display_name: '测试人员', source_status: 'active' }
  });
  return kernel;
}

test('人员事件能区分离职和复职并固定事件字段', () => {
  const offboard = createPersonLifecycleEvent({
    action: 'offboard',
    tenantId: 'TENANT_A',
    person: { person_id: 'P_PERSON_1', emp_code: '19' },
    revision: 2,
    effectiveAt: '2026-11-03T00:00:00.000Z',
    correlationId: 'CASE-1',
    actor: 'P_OWNER'
  });
  const reinstate = createPersonLifecycleEvent({
    action: 'reinstate',
    tenantId: 'TENANT_A',
    person: { person_id: 'P_PERSON_1', emp_code: '19' },
    revision: 3,
    effectiveAt: '2026-11-04T00:00:00.000Z',
    correlationId: 'CASE-2',
    actor: 'P_OWNER'
  });

  assert.equal(offboard.event_type, 'person.offboarded');
  assert.equal(offboard.status, 'inactive');
  assert.equal(reinstate.event_type, 'person.reinstated');
  assert.equal(reinstate.status, 'active');
  assert.equal(offboard.object_type, 'Person');
  assert.match(offboard.idempotency_key, /TENANT_A.*P_PERSON_1.*2/);
});

test('M01 生成统一事件上报请求并包含 key_id', () => {
  const request = buildPersonLifecycleRequest({
    url: 'http://edp.test',
    keyId: 'm01-dev',
    secret: 'sync-secret',
    event: { event_id: 'EVT-1', event_type: 'person.offboarded' },
    timestamp: 1730545200000
  });

  assert.equal(request.url, 'http://edp.test/api/v1/person-lifecycle/events');
  assert.equal(request.headers['X-AEOS-Key-Id'], 'm01-dev');
  assert.match(request.headers['X-AEOS-Signature'], /^sha256=[a-f0-9]{64}$/);
});

test('离职变更与事件 Outbox 一起保存，并撤销人员和智能体', () => {
  const kernel = makeKernel();
  kernel.agents.set('AGENT-1', { agent_id: 'AGENT-1', tenant_id: 'TENANT_A', owner_person_id: 'P_PERSON_1', status: 'active', scopes: ['read'] });

  const result = applyPersonLifecycleTransition(kernel, 'P_PERSON_1', 'offboard', new Date().toISOString(), 'P_OWNER');

  assert.equal(result.state, 'offboarded');
  assert.equal(kernel.persons.get('P_PERSON_1').status, 'offboarded');
  assert.equal(kernel.agents.get('AGENT-1').status, 'revoked');
  assert.equal(kernel.personLifecycleOutbox.length, 1);
  assert.equal(kernel.personLifecycleOutbox[0].event_type, 'person.offboarded');
  assert.equal(kernel.exportState().personLifecycleOutbox.length, 1);
});

test('复职的未来生效时间不会提前恢复登录', () => {
  const kernel = makeKernel();
  applyPersonLifecycleTransition(kernel, 'P_PERSON_1', 'offboard', new Date().toISOString(), 'P_OWNER');
  const event = createPersonLifecycleEvent({
    action: 'reinstate',
    tenantId: 'TENANT_A',
    person: { person_id: 'P_PERSON_1', emp_code: '19' },
    revision: 2,
    effectiveAt: new Date(Date.now() + 60_000).toISOString(),
    correlationId: 'CASE-REINSTATE',
    actor: 'P_OWNER'
  });

  const result = applyPersonLifecycleEvent(kernel, event);

  assert.equal(result.state, 'scheduled');
  assert.equal(kernel.persons.get('P_PERSON_1').status, 'offboarded');
});

test('旧版本和跨租户人员事件不能改变状态', () => {
  const kernel = makeKernel();
  applyPersonLifecycleTransition(kernel, 'P_PERSON_1', 'offboard', new Date().toISOString(), 'P_OWNER');
  const stale = createPersonLifecycleEvent({
    action: 'reinstate',
    tenantId: 'TENANT_A',
    person: { person_id: 'P_PERSON_1', emp_code: '19' },
    revision: 1,
    effectiveAt: new Date().toISOString(),
    correlationId: 'CASE-STALE',
    actor: 'P_OWNER'
  });
  const crossTenant = { ...stale, event_id: 'CROSS-TENANT', tenant_id: 'TENANT_B', idempotency_key: 'TENANT_B:M01_KERNEL:Person:P_PERSON_1:1:person.reinstated' };

  assert.equal(applyPersonLifecycleEvent(kernel, stale).state, 'stale');
  assert.equal(applyPersonLifecycleEvent(kernel, crossTenant).status, 'failed');
  assert.equal(kernel.persons.get('P_PERSON_1').status, 'offboarded');
});
