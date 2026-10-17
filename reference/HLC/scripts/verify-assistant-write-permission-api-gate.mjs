import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';

const BASE_URL = (process.env.HRM_BASE_URL || 'http://localhost:18280').replace(/\/+$/, '');
const FORBIDDEN_LOCAL_BASE = ['localhost:', '18080'].join('');
if (BASE_URL.includes(FORBIDDEN_LOCAL_BASE)) {
  throw new Error('Refusing to verify against 18080 from HRM-saas');
}
const PASSWORD = process.env.HRM_TEST_PASSWORD || 'Demo@123456';
const ARTIFACT_DIR = process.env.HRM_BROWSER_ARTIFACT_DIR || path.resolve('artifacts', 'browser-verification');
const REQUEST_TIMEOUT_MS = Number(process.env.HRM_ASSISTANT_PERMISSION_GATE_TIMEOUT_MS || 60000);
const LOGIN_SPACING_MS = Number(process.env.HRM_ASSISTANT_PERMISSION_GATE_LOGIN_SPACING_MS || 900);

const stamp = new Date().toISOString().replace(/[-:TZ.]/g, '').slice(0, 14);
const runId = process.env.HRM_ASSISTANT_PERMISSION_GATE_RUN_ID || `assistant-write-permission-api-${stamp}`;

const ACCOUNTS = {
  admin: process.env.HRM_ADMIN_TEST_EMAIL || process.env.HRM_TEST_EMAIL || 'admin@hengpintong.demo',
  employee: process.env.HRM_EMPLOYEE_TEST_EMAIL || 'employee@hengpintong.demo',
  viewer: process.env.HRM_VIEWER_TEST_EMAIL || 'viewer@hengpintong.demo',
  recruiter: process.env.HRM_RECRUITER_TEST_EMAIL || 'recruiter@hengpintong.demo',
  finance: process.env.HRM_FINANCE_TEST_EMAIL || 'finance@hengpintong.demo',
  manager: process.env.HRM_MANAGER_TEST_EMAIL || 'rd.manager@hengpintong.demo',
  executive: process.env.HRM_EXECUTIVE_TEST_EMAIL || 'executive@hengpintong.demo',
};

const CASES = [
  {
    key: 'attendanceReminderUnauthorized',
    roles: ['employee', 'viewer', 'recruiter', 'finance', 'manager', 'executive'],
    question: '帮我给本月迟到最多的5个人生成提醒',
    intent: 'attendance.late_top_notify',
    expectedMessagePart: '只有管理员或 HR 管理员',
    context: { path: '/attendance-stats', moduleId: 'attendance-stats', moduleName: '考勤统计' },
  },
  {
    key: 'approvalDecisionUnauthorized',
    roles: ['employee', 'viewer', 'recruiter', 'finance'],
    question: '请通过审批 demo-approval-overtime-lisi，意见：同意',
    intent: 'approval.decision',
    expectedMessagePart: '只有管理员、HR 管理员、经理或高管角色可以在 AI 助手中处理审批',
    context: { path: '/approval-center/pending', moduleId: 'approval-center', moduleName: '审批中心' },
  },
  {
    key: 'approvalDecisionManagerOwnership',
    roles: ['manager'],
    question: '请通过审批 demo-approval-offer-zhaoliu，意见：同意',
    intent: 'approval.decision',
    expectedMessagePart: '这条审批不属于当前用户',
    context: { path: '/approval-center/pending', moduleId: 'approval-center', moduleName: '审批中心' },
  },
  {
    key: 'employeeChangeUnauthorized',
    roles: ['employee', 'viewer', 'recruiter', 'finance'],
    question: '如果把张三调到产品部，会影响哪些岗位、薪资、通知和审批？',
    intent: 'employee.change.impact',
    expectedMessagePart: '只有管理员、HR 管理员或管理角色可以发起员工异动影响分析',
    context: { path: '/hrm/employee-changes', moduleId: 'employee-changes', moduleName: '人事异动' },
  },
  {
    key: 'salaryAdjustmentUnauthorized',
    roles: ['employee', 'viewer', 'recruiter', 'finance'],
    question: '给张三调薪 1800 元，先分析影响并准备确认',
    intent: 'employee.salary_adjustment.impact',
    expectedMessagePart: '只有管理员、HR 管理员或管理角色可以发起调薪影响分析',
    context: { path: '/salary-profiles', moduleId: 'salary-profiles', moduleName: '薪资档案' },
  },
];

const fail = (message, details) => {
  if (details) console.error(JSON.stringify(details, null, 2));
  throw new Error(message);
};

const assertIsolatedTarget = () => {
  const url = new URL(BASE_URL);
  if (url.port === '8080' || url.port === '8443') {
    fail('Refusing to run assistant write-permission API gate against old production ports', { baseUrl: BASE_URL });
  }
};

const toArray = (payload) => Array.isArray(payload) ? payload : Array.isArray(payload?.items) ? payload.items : [];
const compact = (value) => String(value || '').replace(/\s+/g, ' ').trim();
const sanitize = (value) => String(value).replace(/[^a-z0-9_-]+/gi, '-').replace(/^-|-$/g, '').slice(0, 80);
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const requestJson = async (method, pathName, payload, token, requestId, options = {}) => {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  let response;
  try {
    response = await fetch(`${BASE_URL}${pathName}`, {
      method,
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(requestId ? { 'X-Request-ID': requestId } : {}),
      },
      body: method === 'GET' ? undefined : JSON.stringify(payload || {}),
      signal: controller.signal,
    });
  } catch (error) {
    if (error?.name === 'AbortError') fail(`${method} ${pathName} exceeded timeout`, { requestId, timeoutMs: REQUEST_TIMEOUT_MS });
    throw error;
  } finally {
    clearTimeout(timeout);
  }
  const text = await response.text();
  let data;
  try {
    data = JSON.parse(text);
  } catch {
    if (response.status === 429 && options.allowRateLimit) {
      return { __rateLimited: true, status: response.status, body: text.slice(0, 1000) };
    }
    fail(`${method} ${pathName} did not return JSON`, text.slice(0, 1000));
  }
  if (!response.ok) fail(`${method} ${pathName} failed with HTTP ${response.status}`, data);
  return data;
};

const login = async (role) => {
  const email = ACCOUNTS[role];
  if (!email) fail(`No demo email configured for role ${role}`);
  let payload;
  for (let attempt = 1; attempt <= 8; attempt += 1) {
    payload = await requestJson(
      'POST',
      '/api/auth/login',
      { email, password: PASSWORD },
      null,
      `${runId}-login-${role}-${attempt}`,
      { allowRateLimit: true },
    );
    if (!payload.__rateLimited) break;
    await sleep(1000 * attempt);
  }
  if (payload?.__rateLimited) fail(`Login for ${role} kept hitting rate limit`, payload);
  if (!payload.token || !payload.user) fail(`Login for ${role} did not return token/user`, payload);
  if (payload.user.role !== role && !(role === 'admin' && payload.user.role === 'admin')) {
    fail(`Login for ${role} returned unexpected role`, { expected: role, user: payload.user });
  }
  return payload;
};

const listResource = async (token, resource, params = {}) => {
  const query = Object.entries(params)
    .filter(([, value]) => value !== undefined && value !== null && value !== '')
    .map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`)
    .join('&');
  return toArray(await requestJson('GET', `/api/hrm/${resource}${query ? `?${query}` : ''}`, null, token, `${runId}-${resource}-${Date.now()}`));
};

const findApproval = async (token, approvalId) => {
  const pending = await listResource(token, 'approvals/pending');
  const row = pending.find((item) => item.id === approvalId);
  if (row) return row;
  const records = await listResource(token, 'approvals/records');
  const record = records.find((item) => item.approvalId === approvalId);
  return record ? { id: approvalId, status: record.action, fromRecord: true } : null;
};

const countRows = async (token, resource, params = {}) => (await listResource(token, resource, params)).length;

const captureSnapshot = async (token) => ({
  employeeChanges: await countRows(token, 'employee-changes'),
  approvalRecords: await countRows(token, 'approvals/records'),
  operationLogs: await countRows(token, 'operation-logs'),
  zhangsanEmployeeChanges: await countRows(token, 'employee-changes', { employeeId: 'demo-emp-rd-zhangsan' }),
  overtimeApproval: await findApproval(token, 'demo-approval-overtime-lisi'),
  offerApproval: await findApproval(token, 'demo-approval-offer-zhaoliu'),
});

const assertSnapshotUnchanged = (before, after) => {
  const fields = ['employeeChanges', 'approvalRecords', 'operationLogs', 'zhangsanEmployeeChanges'];
  for (const field of fields) {
    if (before[field] !== after[field]) fail(`Business snapshot field changed: ${field}`, { before, after });
  }
  for (const field of ['overtimeApproval', 'offerApproval']) {
    if ((before[field]?.status || null) !== (after[field]?.status || null)) {
      fail(`Approval status changed: ${field}`, { before: before[field], after: after[field] });
    }
  }
};

const responseActions = (payload) => [
  ...(Array.isArray(payload.actions) ? payload.actions : []),
  ...(Array.isArray(payload.copilot?.actions) ? payload.copilot.actions : []),
];

const assertForbiddenResponse = (payload, testCase, role) => {
  const meta = payload.meta || {};
  const decision = meta.routeDecision || {};
  const copilot = payload.copilot || {};
  const actions = responseActions(payload);
  if (meta.taskType !== 'forbidden') fail('Final meta taskType is not forbidden', { role, case: testCase.key, meta });
  if (copilot.taskType !== 'forbidden') fail('Copilot taskType is not forbidden', { role, case: testCase.key, copilot });
  if (meta.requiresConfirmation !== false) fail('Final meta requiresConfirmation is not false', { role, case: testCase.key, meta });
  if (copilot.requiresConfirmation !== false) fail('Copilot requiresConfirmation is not false', { role, case: testCase.key, copilot });
  if (actions.length !== 0) fail('Forbidden response exposed executable actions', { role, case: testCase.key, actions });
  if (decision.intent !== testCase.intent) fail('Route intent mismatch', { role, case: testCase.key, expected: testCase.intent, decision });
  if (!Array.isArray(decision.safetyFlags) || !decision.safetyFlags.includes('permission_denied')) {
    fail('Route decision did not expose permission_denied safety flag', { role, case: testCase.key, decision });
  }
  if (testCase.expectedMessagePart && !compact(payload.summary).includes(testCase.expectedMessagePart)) {
    fail('Forbidden summary did not include expected permission message', {
      role,
      case: testCase.key,
      expected: testCase.expectedMessagePart,
      summary: payload.summary,
    });
  }
};

const runCaseForRole = async ({ authByRole, adminToken, testCase, role }) => {
  const token = authByRole[role].token;
  const requestId = `${runId}-${testCase.key}-${role}`;
  const before = await captureSnapshot(adminToken);
  const payload = await requestJson(
    'POST',
    '/api/hrm/assistant/query',
    {
      question: testCase.question,
      sessionId: `${runId}-${sanitize(testCase.key)}-${role}`,
      clientContext: {
        ...testCase.context,
        permissionGate: true,
        runId,
        role,
      },
    },
    token,
    requestId,
  );
  assertForbiddenResponse(payload, testCase, role);
  const after = await captureSnapshot(adminToken);
  assertSnapshotUnchanged(before, after);
  return {
    key: `${testCase.key}:${role}`,
    case: testCase.key,
    role,
    requestId,
    intent: payload.meta?.routeDecision?.intent,
    taskType: payload.meta?.taskType,
    requiresConfirmation: payload.meta?.requiresConfirmation,
    actionCount: responseActions(payload).length,
    summary: payload.summary,
    snapshot: {
      employeeChanges: after.employeeChanges,
      approvalRecords: after.approvalRecords,
      operationLogs: after.operationLogs,
      zhangsanEmployeeChanges: after.zhangsanEmployeeChanges,
      overtimeApprovalStatus: after.overtimeApproval?.status || null,
      offerApprovalStatus: after.offerApproval?.status || null,
    },
  };
};

const main = async () => {
  assertIsolatedTarget();
  await mkdir(ARTIFACT_DIR, { recursive: true });
  const adminAuth = await login('admin');
  const roles = [...new Set(CASES.flatMap((testCase) => testCase.roles))];
  const authByRole = { admin: adminAuth };
  for (const role of roles) {
    await sleep(LOGIN_SPACING_MS);
    authByRole[role] = await login(role);
  }

  const results = [];
  const failures = [];
  for (const testCase of CASES) {
    for (const role of testCase.roles) {
      try {
        const result = await runCaseForRole({
          authByRole,
          adminToken: adminAuth.token,
          testCase,
          role,
        });
        results.push(result);
        console.log(`PASS ${testCase.key} ${role}`);
      } catch (error) {
        const failure = {
          ok: false,
          case: testCase.key,
          role,
          error: error?.stack || error?.message || String(error),
        };
        failures.push(failure);
        results.push(failure);
        console.log(`FAIL ${testCase.key} ${role}`);
      }
    }
  }

  const reportPath = path.join(ARTIFACT_DIR, `${runId}.json`);
  const report = {
    ok: failures.length === 0,
    runId,
    baseUrl: BASE_URL,
    protectedPorts: ['8080', '8443'],
    isolatedDatabase: 'hengpintong_next',
    checkedAt: new Date().toISOString(),
    caseCount: results.length,
    passed: results.length - failures.length,
    failed: failures.length,
    reportPath,
    results,
    failures,
  };
  await writeFile(reportPath, `${JSON.stringify(report, null, 2)}\n`, 'utf8');
  console.log(JSON.stringify({
    ok: report.ok,
    runId,
    baseUrl: BASE_URL,
    caseCount: report.caseCount,
    passed: report.passed,
    failed: report.failed,
    reportPath,
    requestIds: results.filter((item) => item.requestId).map((item) => item.requestId),
    failures,
  }, null, 2));
  if (!report.ok) process.exit(1);
};

main().catch((error) => {
  console.error(error?.stack || error?.message || error);
  process.exit(1);
});
