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
  assert.equal(offboard.data.message, '需要审批，尚未办理');
  assert.equal(offboard.data.approval.status, 'PENDING');
  assert.equal(kernel.persons.get('P_QUICK_1').status, 'active');

  const approvals = await fetch(`${base}/kernel/v1/approvals?tenant_id=TENANT_A`, { headers: { Cookie: login.cookie } });
  assert.equal(approvals.status, 200);
  const approvalList = (await approvals.json()).approvals;
  assert.equal(approvalList.length, 1);

  const decided = await post(base, `/kernel/v1/approvals/${approvalList[0].approval_id}/decision`, {
    tenant_id: 'TENANT_A', approver_id: 'P_OWNER', decision: 'APPROVE', comment: '同意办理'
  }, login.cookie);
  assert.equal(decided.status, 200);
  assert.equal(decided.data.approval.status, 'APPROVED');

  const executed = await post(base, '/kernel/v1/quick/person-action', {
    tenant_id: 'TENANT_A', person_id: 'P_QUICK_1', action: 'offboard', approval_ticket: approvalList[0].approval_id
  }, login.cookie);
  assert.equal(executed.status, 200);
  assert.equal(executed.data.decision, 'EXECUTED');
  assert.equal(kernel.persons.get('P_QUICK_1').status, 'offboarded');
});

test('页面提供一键登录和一键办理入口', () => {
  const html = readFileSync(new URL('../public/index.html', import.meta.url), 'utf8');
  assert.match(html, /一键登录/);
  assert.match(html, /一键办理/);
  assert.match(html, /quickLogin/);
  assert.match(html, /quickHandle/);
});

test('快速办理支持输入查找人员并显示选中人员状态', () => {
  const html = readFileSync(new URL('../public/index.html', import.meta.url), 'utf8');
  assert.match(html, /id="quickPersonSearch"/);
  assert.match(html, /输入姓名、编号或手机号后四位/);
  assert.match(html, /id="quickPersonStatus"/);
  assert.match(html, /statusLabel\(person\.status\)/);
});

test('对话区显示通俗的审批状态和办理结果', async (t) => {
  const { server, kernel, base } = await startServer();
  t.after(() => server.close());

  const login = await post(base, '/auth/demo/login');
  await post(base, '/kernel/v1/master/register', { tenant_id: 'TENANT_A', owner_person_id: 'P_OWNER' }, login.cookie);
  const reply = kernel.say({ tenant_id: 'TENANT_A', text: '确认让 P_QUICK_1 离职' });
  assert.equal(reply.decision, 'REQUIRE_APPROVAL');
  assert.match(reply.content, /等待审批/);
  assert.match(reply.content, /还没有办理/);
  assert.doesNotMatch(reply.content, /high-risk|approval ticket|REQUIRE_APPROVAL/);

  const html = readFileSync(new URL('../public/index.html', import.meta.url), 'utf8');
  assert.match(html, /id="dialogApprovalStatus"/);
  assert.match(html, /id="dialogActionResult"/);
  assert.match(html, /审批状态/);
  assert.match(html, /办理结果/);
});

test('选择人员后显示办理过程和进度，并支持推进到完成', () => {
  const html = readFileSync(new URL('../public/index.html', import.meta.url), 'utf8');
  assert.match(html, /id="quickProcess"/);
  assert.match(html, /id="quickProcessSteps"/);
  assert.match(html, /id="quickProcessProgress"/);
  assert.match(html, /确认人员/);
  assert.match(html, /检查当前状态/);
  assert.match(html, /办理完成/);
  assert.match(html, /renderQuickProcess/);
  assert.match(html, /personLabel\(person\)/);
});

test('页面提供审批中心和可用的页面导航', () => {
  const html = readFileSync(new URL('../public/index.html', import.meta.url), 'utf8');
  assert.match(html, /审批中心/);
  assert.match(html, /id="approvalRows"/);
  assert.match(html, /data-nav-target="approvalCard"/);
  assert.match(html, /data-action="refreshApprovals"/);
  assert.match(html, /data-action="approveApproval"/);
  assert.match(html, /data-action="rejectApproval"/);
});

test('人员主名单隐藏离职人员，并提供单独名单和配备清单', () => {
  const html = readFileSync(new URL('../public/index.html', import.meta.url), 'utf8');
  assert.match(html, /id="offboardedPersonRows"/);
  assert.match(html, /已离职人员/);
  assert.match(html, /id="quickInventoryRows"/);
  assert.match(html, /人员配备清单/);
  assert.match(html, /renderPersonInventory/);
  assert.match(html, /activePeople/);
});
