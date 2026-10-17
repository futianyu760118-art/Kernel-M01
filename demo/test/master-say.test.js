import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createApp } from '../src/server.js';
import { M01Kernel } from '../src/kernel.js';

const ADMIN_TOKEN = 'test-admin-token';

async function startServer() {
  const kernel = new M01Kernel();
  const server = createApp(kernel, { adminToken: ADMIN_TOKEN, m07Secret: 's1', m13Secret: 's2' });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  return { server, kernel, base: `http://127.0.0.1:${server.address().port}` };
}

async function say(base, text) {
  const res = await fetch(base + '/kernel/v1/master/say', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${ADMIN_TOKEN}` },
    body: JSON.stringify({ tenant_id: 'TENANT_A', text })
  });
  return { status: res.status, data: await res.json() };
}

const onePrimary = (qa) => qa.filter((a) => a.primary).length === 1;

test('say: colloquial hire asks clarifying options, chip prompt resolves and executes', async (t) => {
  const { server, base } = await startServer();
  t.after(() => server.close());

  await say(base, '注册总控');
  const r1 = await say(base, '让 P_7001 入职');
  assert.equal(r1.status, 200);
  assert.ok(r1.data.content.includes('岗位'));
  assert.ok(r1.data.quick_actions.length >= 2 && r1.data.quick_actions.length <= 3);
  assert.ok(onePrimary(r1.data.quick_actions));
  const chip = r1.data.quick_actions.find((a) => a.primary);
  assert.match(chip.prompt, /P_7001/);
  assert.match(chip.prompt, /POS_STAFF/);

  const r2 = await say(base, chip.prompt);
  assert.equal(r2.data.decision, 'EXECUTED');
  assert.equal(r2.data.command, 'person.hire');
  assert.equal(r2.data.output.status, 'active');
  assert.equal(r2.data.output.position_id, 'POS_STAFF');
});

test('say: destructive offboard asks confirmation, then fail-closed REQUIRE_APPROVAL', async (t) => {
  const { server, kernel, base } = await startServer();
  t.after(() => server.close());

  await say(base, '注册总控');
  await say(base, '让 P_7002 入职，岗位 POS_STAFF');

  const r1 = await say(base, '让 P_7002 离职');
  assert.ok(!r1.data.decision);
  assert.ok(r1.data.content.includes('高风险'));
  assert.ok(r1.data.quick_actions.some((a) => /确认/.test(a.label)));

  const r2 = await say(base, '确认让 P_7002 离职');
  assert.equal(r2.data.decision, 'REQUIRE_APPROVAL');
  assert.equal(r2.data.command, 'person.offboard');
  assert.equal(kernel.persons.get('P_7002').status, 'active');
});

test('say: greetings, status and unknown intents follow quick_actions contract', async (t) => {
  const { server, base } = await startServer();
  t.after(() => server.close());

  const hi = await say(base, '你好');
  assert.ok(hi.data.content.includes('总控'));
  assert.ok(onePrimary(hi.data.quick_actions));

  await say(base, '注册总控');
  const before = await say(base, '让 P_7003 入职，岗位 POS_STAFF');
  assert.equal(before.data.decision, 'EXECUTED');

  const st = await say(base, '现在系统什么状态');
  assert.equal(st.data.decision, 'EXECUTED');
  assert.equal(st.data.command, 'system.status');
  assert.ok(st.data.output.persons >= 2);

  const unknown = await say(base, '帮我订一杯咖啡');
  assert.ok(!unknown.data.decision);
  assert.ok(unknown.data.content.includes('还不会'));
  assert.ok(unknown.data.quick_actions.length <= 3 && onePrimary(unknown.data.quick_actions));
});
