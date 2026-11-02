import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createApp } from '../src/server.js';
import { M01Kernel } from '../src/kernel.js';

async function startServer() {
  const kernel = new M01Kernel();
  kernel.applyM07Event({ event_id: 'QUICK_PERSON_1', tenant_id: 'TENANT_A', type: 'person.hired', person_id: 'P_QUICK_1', position_id: 'POS_STAFF', seq: 1, profile: { display_name: '测试人员' } });
  const server = createApp(kernel, { adminToken: 'quick-admin', demoOneClickLogin: true });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  return { server, kernel, base: `http://127.0.0.1:${server.address().port}` };
}

async function post(base, path, body, cookie) {
  const headers = { 'Content-Type': 'application/json' };
  if (cookie) headers.Cookie = cookie;
  const res = await fetch(base + path, { method: 'POST', headers, body: JSON.stringify(body || {}) });
  return { status: res.status, data: await res.json(), cookie: res.headers.get('set-cookie')?.split(';')[0] || null };
}

test('一键登录建立页面会话，不要求用户填写内部服务密码', async (t) => {
  const { server, base } = await startServer();
  t.after(() => server.close());

  const login = await post(base, '/auth/demo/login');
  assert.equal(login.status, 200);
  assert.equal(login.data.logged_in, true);
  assert.match(login.cookie || '', /^m01_demo_session=/);

  const status = await fetch(base + '/kernel/v1/status', { headers: { Cookie: login.cookie } });
  assert.equal(status.status, 200);
});

test('一键办理普通调岗，离职仍然必须审批', async (t) => {
  const { server, kernel, base } = await startServer();
  t.after(() => server.close());

  const login = await post(base, '/auth/demo/login');
  const register = await post(base, '/kernel/v1/master/register', { tenant_id: 'TENANT_A', owner_person_id: 'P_OWNER' }, login.cookie);
  assert.equal(register.status, 200);

  const transfer = await post(base, '/kernel/v1/quick/person-action', { tenant_id: 'TENANT_A', person_id: 'P_QUICK_1', action: 'transfer_manager' }, login.cookie);
  assert.equal(transfer.status, 200);
  assert.equal(transfer.data.decision, 'EXECUTED');
  assert.equal(kernel.persons.get('P_QUICK_1').position_id, 'POS_MANAGER');

  const offboard = await post(base, '/kernel/v1/quick/person-action', { tenant_id: 'TENANT_A', person_id: 'P_QUICK_1', action: 'offboard' }, login.cookie);
  assert.equal(offboard.status, 200);
  assert.equal(offboard.data.decision, 'REQUIRE_APPROVAL');
  assert.equal(kernel.persons.get('P_QUICK_1').status, 'active');
});

test('页面提供一键登录和一键办理入口', () => {
  const html = readFileSync(new URL('../public/index.html', import.meta.url), 'utf8');
  assert.match(html, /一键登录/);
  assert.match(html, /一键办理/);
  assert.match(html, /quickLogin/);
  assert.match(html, /quickHandle/);
});
