import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createApp } from '../src/server.js';
import { M01Kernel } from '../src/kernel.js';

const ADMIN_TOKEN = 'test-admin-token';
const M07_SECRET = 'test-m07-secret';
const M13_SECRET = 'test-m13-secret';

async function startServer() {
  const kernel = new M01Kernel();
  const server = createApp(kernel, { adminToken: ADMIN_TOKEN, m07Secret: M07_SECRET, m13Secret: M13_SECRET });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  return { server, kernel, base: `http://127.0.0.1:${server.address().port}` };
}

async function postJson(base, path, body) {
  const res = await fetch(base + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${ADMIN_TOKEN}` },
    body: JSON.stringify(body)
  });
  return { status: res.status, data: await res.json() };
}

test('master agent: register by owner then execute hire/query/authorize commands', async (t) => {
  const { server, base } = await startServer();
  t.after(() => server.close());

  const denied = await postJson(base, '/kernel/v1/master/command', { tenant_id: 'TENANT_A', command: 'system.status', params: {} });
  assert.equal(denied.status, 200);
  assert.equal(denied.data.decision, 'DENY');
  assert.match(denied.data.reason, /not registered/);

  const reg = await postJson(base, '/kernel/v1/master/register', { tenant_id: 'TENANT_A', owner_person_id: 'P_OWNER' });
  assert.equal(reg.status, 200);
  assert.equal(reg.data.master.agent_id, 'A_MASTER_KERNEL');
  assert.ok(reg.data.master.commands.includes('person.hire'));

  const hire = await postJson(base, '/kernel/v1/master/command', { tenant_id: 'TENANT_A', command: 'person.hire', params: { person_id: 'P_5001', position_id: 'POS_STAFF' } });
  assert.equal(hire.data.decision, 'EXECUTED');
  assert.equal(hire.data.output.status, 'active');
  assert.equal(hire.data.output.position_id, 'POS_STAFF');

  const caps = await postJson(base, '/kernel/v1/master/command', { tenant_id: 'TENANT_A', command: 'capabilities.query', params: { principal_id: 'P_5001' } });
  assert.equal(caps.data.decision, 'EXECUTED');
  assert.equal(caps.data.output.status, 'active');
  assert.ok(caps.data.output.capabilities.some((c) => c.resource === 'task' && c.action === 'execute'));

  const check = await postJson(base, '/kernel/v1/master/command', { tenant_id: 'TENANT_A', command: 'authorize.check', params: { principal_id: 'P_5001', resource: 'task', action: 'execute' } });
  assert.equal(check.data.decision, 'EXECUTED');
  assert.equal(check.data.output.decision, 'ALLOW');

  const status = await (await fetch(base + '/kernel/v1/status', { headers: { Authorization: `Bearer ${ADMIN_TOKEN}` } })).json();
  assert.equal(status.master.agent_id, 'A_MASTER_KERNEL');
  assert.equal(status.master.owner_person_id, 'P_OWNER');
  assert.equal(status.master.status, 'active');

  const unknown = await postJson(base, '/kernel/v1/master/command', { tenant_id: 'TENANT_A', command: 'deploy.to.prod', params: {} });
  assert.equal(unknown.status, 400);
});

test('master agent: high-risk commands fail closed without approval ticket', async (t) => {
  const { server, kernel, base } = await startServer();
  t.after(() => server.close());

  await postJson(base, '/kernel/v1/master/register', { tenant_id: 'TENANT_A', owner_person_id: 'P_OWNER' });
  await postJson(base, '/kernel/v1/master/command', { tenant_id: 'TENANT_A', command: 'person.hire', params: { person_id: 'P_5002', position_id: 'POS_STAFF' } });

  const offboard = await postJson(base, '/kernel/v1/master/command', { tenant_id: 'TENANT_A', command: 'person.offboard', params: { person_id: 'P_5002' } });
  assert.equal(offboard.data.decision, 'REQUIRE_APPROVAL');
  assert.match(offboard.data.reason, /approval/);
  assert.equal(kernel.persons.get('P_5002').status, 'active');

  const withFakeTicket = await postJson(base, '/kernel/v1/master/command', { tenant_id: 'TENANT_A', command: 'principal.revoke', params: { principal_id: 'P_5002', approval_ticket: 'FAKE-1' } });
  assert.equal(withFakeTicket.data.decision, 'DENY');
  assert.match(withFakeTicket.data.reason, /not implemented/);
  assert.equal(kernel.persons.get('P_5002').status, 'active');

  const crossTenant = await postJson(base, '/kernel/v1/master/command', { tenant_id: 'TENANT_B', command: 'system.status', params: {} });
  assert.equal(crossTenant.data.decision, 'DENY');
  assert.match(crossTenant.data.reason, /tenant/);
});

test('master agent: revoking the master agent stops all command execution; re-register restores', async (t) => {
  const { server, base } = await startServer();
  t.after(() => server.close());

  await postJson(base, '/kernel/v1/master/register', { tenant_id: 'TENANT_A', owner_person_id: 'P_OWNER' });
  const revoke = await postJson(base, '/kernel/v1/revocations', { principal_id: 'A_MASTER_KERNEL', reason: 'owner suspended master' });
  assert.equal(revoke.status, 200);

  const cmd = await postJson(base, '/kernel/v1/master/command', { tenant_id: 'TENANT_A', command: 'system.status', params: {} });
  assert.equal(cmd.data.decision, 'DENY');
  assert.match(cmd.data.reason, /not active/);

  const reg = await postJson(base, '/kernel/v1/master/register', { tenant_id: 'TENANT_A', owner_person_id: 'P_OWNER' });
  assert.equal(reg.status, 200);
  const cmd2 = await postJson(base, '/kernel/v1/master/command', { tenant_id: 'TENANT_A', command: 'system.status', params: {} });
  assert.equal(cmd2.data.decision, 'EXECUTED');
  assert.ok(cmd2.data.output.persons >= 1);
});
