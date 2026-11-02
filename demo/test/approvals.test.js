import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createApp } from '../src/server.js';
import { M01Kernel } from '../src/kernel.js';

const ADMIN_TOKEN = 'approval-test-admin';

async function startServer() {
  const server = createApp(new M01Kernel(), { mode: 'demo', adminToken: ADMIN_TOKEN });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  return { server, base: `http://127.0.0.1:${server.address().port}` };
}

async function request(base, path, body, method = 'POST') {
  const response = await fetch(base + path, {
    method,
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${ADMIN_TOKEN}` },
    body: method === 'GET' ? undefined : JSON.stringify(body)
  });
  return { status: response.status, data: await response.json() };
}

test('approval ticket binds action/object/parameters and is consumed once', async (t) => {
  const { server, base } = await startServer();
  t.after(() => server.close());

  await request(base, '/kernel/v1/master/register', { tenant_id: 'TENANT_A', owner_person_id: 'P_OWNER' });
  const hire = await request(base, '/kernel/v1/master/command', {
    tenant_id: 'TENANT_A',
    command: 'person.hire',
    params: { person_id: 'P_5002', position_id: 'POS_STAFF' }
  });
  assert.equal(hire.data.decision, 'EXECUTED');

  const requestApproval = await request(base, '/kernel/v1/approvals', {
    tenant_id: 'TENANT_A',
    requester_id: 'P_REQUESTER',
    resource: 'master',
    action: 'principal.revoke',
    object_id: 'P_5002',
    parameters: { principal_id: 'P_5002' }
  });
  assert.equal(requestApproval.status, 201);
  assert.equal(requestApproval.data.approval.status, 'PENDING');

  const approvalId = requestApproval.data.approval.approval_id;
  const decided = await request(base, `/kernel/v1/approvals/${approvalId}/decision`, {
    tenant_id: 'TENANT_A',
    approver_id: 'P_APPROVER',
    decision: 'APPROVE',
    comment: '已核对撤权对象'
  });
  assert.equal(decided.status, 200);
  assert.equal(decided.data.approval.status, 'APPROVED');

  const executed = await request(base, '/kernel/v1/master/command', {
    tenant_id: 'TENANT_A',
    command: 'principal.revoke',
    params: { principal_id: 'P_5002', approval_ticket: approvalId }
  });
  assert.equal(executed.status, 200);
  assert.equal(executed.data.decision, 'EXECUTED');

  const replay = await request(base, '/kernel/v1/master/command', {
    tenant_id: 'TENANT_A',
    command: 'principal.revoke',
    params: { principal_id: 'P_5002', approval_ticket: approvalId }
  });
  assert.equal(replay.data.decision, 'DENY');
  assert.match(replay.data.reason, /consumed|used/i);
});

test('approval rejects self-approval and mismatched execution binding', async (t) => {
  const { server, base } = await startServer();
  t.after(() => server.close());

  await request(base, '/kernel/v1/master/register', { tenant_id: 'TENANT_A', owner_person_id: 'P_OWNER' });

  const created = await request(base, '/kernel/v1/approvals', {
    tenant_id: 'TENANT_A',
    requester_id: 'P_SAME',
    resource: 'master',
    action: 'principal.revoke',
    object_id: 'P_5003',
    parameters: { principal_id: 'P_5003' }
  });
  const approvalId = created.data.approval.approval_id;
  const selfDecision = await request(base, `/kernel/v1/approvals/${approvalId}/decision`, {
    tenant_id: 'TENANT_A', approver_id: 'P_SAME', decision: 'APPROVE'
  });
  assert.equal(selfDecision.status, 403);

  const createdAgain = await request(base, '/kernel/v1/approvals', {
    tenant_id: 'TENANT_A',
    requester_id: 'P_REQUESTER',
    resource: 'master',
    action: 'principal.revoke',
    object_id: 'P_5003',
    parameters: { principal_id: 'P_5003' }
  });
  const approvalId2 = createdAgain.data.approval.approval_id;
  await request(base, `/kernel/v1/approvals/${approvalId2}/decision`, {
    tenant_id: 'TENANT_A', approver_id: 'P_APPROVER', decision: 'APPROVE'
  });
  const mismatched = await request(base, '/kernel/v1/master/command', {
    tenant_id: 'TENANT_A',
    command: 'principal.revoke',
    params: { principal_id: 'P_OTHER', approval_ticket: approvalId2 }
  });
  assert.equal(mismatched.data.decision, 'DENY');
  assert.match(mismatched.data.reason, /binding|object|parameter/i);
});
