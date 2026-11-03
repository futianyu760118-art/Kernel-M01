import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHmac } from 'node:crypto';
import { createApp } from '../src/server.js';
import { M01Kernel } from '../src/kernel.js';

const ADMIN_TOKEN = 'change-request-admin';
const M07_SECRET = 'change-request-m07-secret';

async function startServer() {
  const kernel = new M01Kernel();
  kernel.applyM07Event({ event_id: 'PCR_OWNER_HIRE', tenant_id: 'TENANT_A', type: 'person.hired', person_id: 'P_OWNER', position_id: 'POS_OWNER', seq: 1 });
  kernel.applyM07Event({ event_id: 'PCR_REQUESTER_HIRE', tenant_id: 'TENANT_A', type: 'person.hired', person_id: 'P_REQUESTER', position_id: 'POS_STAFF', seq: 1 });
  kernel.applyM07Event({ event_id: 'PCR_TARGET_HIRE', tenant_id: 'TENANT_A', type: 'person.hired', person_id: 'P_TARGET', position_id: 'POS_MANAGER', seq: 1, profile: { display_name: '测试人员' } });
  const server = createApp(kernel, { adminToken: ADMIN_TOKEN, m07Secret: M07_SECRET });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  return { server, kernel, base: `http://127.0.0.1:${server.address().port}` };
}

async function post(base, path, body, headers = {}) {
  const raw = JSON.stringify(body);
  const response = await fetch(base + path, { method: 'POST', headers: { 'Content-Type': 'application/json', ...headers }, body: raw });
  return { status: response.status, data: await response.json() };
}

test('M07提交人员变动到M01，M01审批后才办理并保留岗位资料', async (t) => {
  const { server, kernel, base } = await startServer();
  t.after(() => server.close());
  const request = {
    type: 'person.change.request', event_id: 'PCR_M07_1', tenant_id: 'TENANT_A', requester_id: 'P_REQUESTER',
    person_id: 'P_TARGET', action: 'offboard', department_id: 'D_SALES', position_id: 'POS_MANAGER',
    job_title: '销售经理', effective_at: new Date().toISOString(), reason: '办理离职', idempotency_key: 'TENANT_A:P_TARGET:offboard:1'
  };
  const raw = JSON.stringify(request);
  const submitted = await post(base, '/integrations/m07', request, { 'X-M01-Signature': `sha256=${createHmac('sha256', M07_SECRET).update(raw).digest('hex')}` });
  assert.equal(submitted.status, 200);
  assert.equal(submitted.data.request.status, 'PENDING');
  assert.equal(kernel.persons.get('P_TARGET').status, 'active');

  const list = await fetch(base + '/kernel/v1/person-change-requests?tenant_id=TENANT_A', { headers: { Authorization: `Bearer ${ADMIN_TOKEN}` } });
  assert.equal(list.status, 200);
  const listData = await list.json();
  assert.equal(listData.requests[0].job_title, '销售经理');

  const decided = await post(base, `/kernel/v1/person-change-requests/${submitted.data.request.request_id}/decision`, {
    tenant_id: 'TENANT_A', approver_id: 'P_OWNER', decision: 'APPROVE', comment: '同意办理'
  }, { Authorization: `Bearer ${ADMIN_TOKEN}` });
  assert.equal(decided.status, 200);
  assert.equal(decided.data.request.status, 'COMPLETED');
  assert.equal(kernel.persons.get('P_TARGET').status, 'offboarded');
  assert.equal(kernel.persons.get('P_TARGET').profile.job_title, '销售经理');
});

test('M01可直接发起同一人员变动流程，重复幂等且不能自己审批', async (t) => {
  const { server, base } = await startServer();
  t.after(() => server.close());
  const headers = { Authorization: `Bearer ${ADMIN_TOKEN}` };
  const body = { tenant_id: 'TENANT_A', requester_id: 'P_REQUESTER', person_id: 'P_TARGET', action: 'reinstate', department_id: 'D_RD', position_id: 'POS_STAFF', job_title: '研发工程师', effective_at: new Date().toISOString(), reason: '重新入职', idempotency_key: 'TENANT_A:P_TARGET:reinstate:2' };
  const created = await post(base, '/kernel/v1/person-change-requests', body, headers);
  assert.equal(created.status, 201);
  const duplicate = await post(base, '/kernel/v1/person-change-requests', body, headers);
  assert.equal(duplicate.status, 201);
  assert.equal(duplicate.data.idempotent, true);
  const selfApprove = await post(base, `/kernel/v1/person-change-requests/${created.data.request.request_id}/decision`, { tenant_id: 'TENANT_A', approver_id: 'P_REQUESTER', decision: 'APPROVE' }, headers);
  assert.equal(selfApprove.status, 403);
});

test('人员变动申请拒绝未知提交人和未知审批人', () => {
  const kernel = new M01Kernel();
  kernel.applyM07Event({ event_id: 'PCR_OWNER_HIRE_2', tenant_id: 'TENANT_A', type: 'person.hired', person_id: 'P_OWNER', position_id: 'POS_OWNER', seq: 1 });
  kernel.applyM07Event({ event_id: 'PCR_TARGET_HIRE_2', tenant_id: 'TENANT_A', type: 'person.hired', person_id: 'P_TARGET', position_id: 'POS_STAFF', seq: 1 });
  const unknownRequester = kernel.submitPersonChangeRequest({
    tenant_id: 'TENANT_A', requester_id: 'P_UNKNOWN', person_id: 'P_TARGET', action: 'offboard',
    department_id: 'D_RD', position_id: 'POS_STAFF', job_title: '普通员工', effective_at: new Date().toISOString(),
    reason: '测试', idempotency_key: 'unknown-requester'
  });
  assert.equal(unknownRequester.status, 403);

  kernel.applyM07Event({ event_id: 'PCR_REQUESTER_HIRE_2', tenant_id: 'TENANT_A', type: 'person.hired', person_id: 'P_REQUESTER', position_id: 'POS_STAFF', seq: 1 });
  const created = kernel.submitPersonChangeRequest({
    tenant_id: 'TENANT_A', requester_id: 'P_REQUESTER', person_id: 'P_TARGET', action: 'offboard',
    department_id: 'D_RD', position_id: 'POS_STAFF', job_title: '普通员工', effective_at: new Date().toISOString(),
    reason: '测试', idempotency_key: 'unknown-approver'
  });
  const unknownApprover = kernel.decidePersonChangeRequest({
    tenant_id: 'TENANT_A', request_id: created.request.request_id, approver_id: 'P_UNKNOWN', decision: 'APPROVE'
  });
  assert.equal(unknownApprover.status, 403);
});

