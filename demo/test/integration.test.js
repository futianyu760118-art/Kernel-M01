import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHmac } from 'node:crypto';
import { createApp } from '../src/server.js';
import { M01Kernel } from '../src/kernel.js';

const ADMIN_TOKEN = 'test-admin-token';
const M07_SECRET = 'test-m07-secret';
const M13_SECRET = 'test-m13-secret';

async function startServer() {
  const kernel = new M01Kernel();
  const server = createApp(kernel, { adminToken: ADMIN_TOKEN, m07Secret: M07_SECRET, m13Secret: M13_SECRET });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  return { server, kernel, base };
}

const sign = (secret, body) => createHmac('sha256', secret).update(body).digest('hex');

async function postJson(base, path, body, headers = {}) {
  const res = await fetch(base + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...headers },
    body: JSON.stringify(body)
  });
  return { status: res.status, data: await res.json() };
}

const adminHeaders = { Authorization: `Bearer ${ADMIN_TOKEN}` };

test('M07 hire -> POMS capabilities -> M13 agent -> offboard revokes person and agents', async (t) => {
  const { server, base } = await startServer();
  t.after(() => server.close());

  const health = await (await fetch(base + '/health')).json();
  assert.equal(health.status, 'ok');

  const hireBody = JSON.stringify({ event_id: 'EVT-001', tenant_id: 'TENANT_A', type: 'person.hired', person_id: 'P_1001', position_id: 'POS_STAFF', seq: 1 });
  const hire = await postJson(base, '/integrations/m07', JSON.parse(hireBody), { 'X-M01-Signature': `sha256=${sign(M07_SECRET, hireBody)}` });
  assert.equal(hire.status, 200);
  assert.equal(hire.data.applied, true);
  assert.equal(hire.data.person.status, 'active');

  const caps = await postJson(base, '/kernel/v1/capabilities', { principal_id: 'P_1001', tenant_id: 'TENANT_A' }, adminHeaders);
  assert.equal(caps.status, 200);
  const capKeys = caps.data.capabilities.map((c) => `${c.resource}:${c.action}`).sort();
  assert.deepEqual(capKeys, ['report:read', 'task:execute']);
  const highRisk = caps.data.capabilities.find((c) => c.resource === 'data' && c.action === 'export');
  assert.equal(highRisk, undefined);

  const agentBody = JSON.stringify({ event_id: 'EVT-A-001', tenant_id: 'TENANT_A', type: 'agent.registered', agent_id: 'A_MAIN_1001', owner_person_id: 'P_1001', parent_agent_id: null, scopes: ['task:execute'] });
  const agent = await postJson(base, '/integrations/m13', JSON.parse(agentBody), { 'X-M01-Signature': `sha256=${sign(M13_SECRET, agentBody)}` });
  assert.equal(agent.status, 200);
  assert.deepEqual(agent.data.agent.scopes, ['task:execute']);

  const agentAllow = await postJson(base, '/kernel/v1/authorize', { tenant_id: 'TENANT_A', principal_id: 'A_MAIN_1001', resource: 'task', action: 'execute' }, adminHeaders);
  assert.equal(agentAllow.data.decision, 'ALLOW');

  const highRiskDecision = await postJson(base, '/kernel/v1/authorize', { tenant_id: 'TENANT_A', principal_id: 'P_1001', resource: 'device', action: 'control' }, adminHeaders);
  assert.equal(highRiskDecision.data.decision, 'DENY');

  const offboardBody = JSON.stringify({ event_id: 'EVT-002', tenant_id: 'TENANT_A', type: 'person.offboarded', person_id: 'P_1001', seq: 2 });
  const offboard = await postJson(base, '/integrations/m07', JSON.parse(offboardBody), { 'X-M01-Signature': `sha256=${sign(M07_SECRET, offboardBody)}` });
  assert.equal(offboard.status, 200);
  assert.equal(offboard.data.person.status, 'offboarded');

  const personDenied = await postJson(base, '/kernel/v1/authorize', { tenant_id: 'TENANT_A', principal_id: 'P_1001', resource: 'task', action: 'execute' }, adminHeaders);
  assert.equal(personDenied.data.decision, 'DENY');

  const agentDenied = await postJson(base, '/kernel/v1/authorize', { tenant_id: 'TENANT_A', principal_id: 'A_MAIN_1001', resource: 'task', action: 'execute' }, adminHeaders);
  assert.equal(agentDenied.data.decision, 'DENY');
  assert.match(agentDenied.data.reason, /revoked/);

  const capsAfter = await postJson(base, '/kernel/v1/capabilities', { principal_id: 'P_1001', tenant_id: 'TENANT_A' }, adminHeaders);
  assert.equal(capsAfter.data.status, 'offboarded');
  assert.equal(capsAfter.data.capabilities.length, 0);
});

test('M07 duplicate event_id is idempotent and does not re-apply', async (t) => {
  const { server, kernel, base } = await startServer();
  t.after(() => server.close());

  const event = { event_id: 'EVT-DUP-1', tenant_id: 'TENANT_A', type: 'person.hired', person_id: 'P_2001', position_id: 'POS_STAFF', seq: 1 };
  const raw = JSON.stringify(event);
  const headers = { 'X-M01-Signature': `sha256=${sign(M07_SECRET, raw)}` };

  const first = await postJson(base, '/integrations/m07', event, headers);
  assert.equal(first.status, 200);
  assert.equal(first.data.idempotent, false);

  const second = await postJson(base, '/integrations/m07', event, headers);
  assert.equal(second.status, 200);
  assert.equal(second.data.idempotent, true);
  assert.equal(second.data.applied, false);

  const stale = await postJson(base, '/integrations/m07', { event_id: 'EVT-STALE-1', tenant_id: 'TENANT_A', type: 'person.transferred', person_id: 'P_2001', position_id: 'POS_MANAGER', seq: 0 }, { 'X-M01-Signature': `sha256=${sign(M07_SECRET, JSON.stringify({ event_id: 'EVT-STALE-1', tenant_id: 'TENANT_A', type: 'person.transferred', person_id: 'P_2001', position_id: 'POS_MANAGER', seq: 0 }))}` });
  assert.equal(stale.data.stale, true);
  assert.equal(stale.data.applied, false);
  assert.equal(kernel.persons.get('P_2001').position_id, 'POS_STAFF');

  const eventsBefore = kernel.events.size;
  const third = await postJson(base, '/integrations/m07', event, headers);
  assert.equal(third.data.idempotent, true);
  assert.equal(kernel.events.size, eventsBefore);
});

test('HMAC tampering on M07/M13 webhooks is rejected with 401', async (t) => {
  const { server, base } = await startServer();
  t.after(() => server.close());

  const event = { event_id: 'EVT-BAD-1', tenant_id: 'TENANT_A', type: 'person.hired', person_id: 'P_3001', position_id: 'POS_STAFF', seq: 1 };

  const tampered = await postJson(base, '/integrations/m07', event, { 'X-M01-Signature': `sha256=${'0'.repeat(64)}` });
  assert.equal(tampered.status, 401);

  const missing = await postJson(base, '/integrations/m07', event, {});
  assert.equal(missing.status, 401);

  const wrongSecret = sign('attacker-secret', JSON.stringify(event));
  const forged = await postJson(base, '/integrations/m07', event, { 'X-M01-Signature': `sha256=${wrongSecret}` });
  assert.equal(forged.status, 401);

  const m13Event = { event_id: 'EVT-BAD-M13', tenant_id: 'TENANT_A', type: 'agent.registered', agent_id: 'A_BAD', owner_person_id: 'P_3001', scopes: ['task:execute'] };
  const m13Tampered = await postJson(base, '/integrations/m13', m13Event, { 'X-M01-Signature': `sha256=${sign(M07_SECRET, JSON.stringify(m13Event))}` });
  assert.equal(m13Tampered.status, 401);

  const noToken = await postJson(base, '/kernel/v1/authorize', { tenant_id: 'TENANT_A', principal_id: 'P_3001', resource: 'task', action: 'execute' }, {});
  assert.equal(noToken.status, 401);

  const status = await (await fetch(base + '/kernel/v1/status')).status;
  assert.equal(status, 401);
});
