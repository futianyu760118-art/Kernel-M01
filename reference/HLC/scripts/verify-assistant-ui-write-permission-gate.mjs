import { mkdir, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const BASE_URL = (process.env.HRM_BASE_URL || 'http://localhost:18280').replace(/\/+$/, '');
const FORBIDDEN_LOCAL_BASE = ['localhost:', '18080'].join('');
if (BASE_URL.includes(FORBIDDEN_LOCAL_BASE)) {
  throw new Error('Refusing to verify against 18080 from HRM-saas');
}
const PASSWORD = process.env.HRM_TEST_PASSWORD || 'Demo@123456';
const ARTIFACT_DIR = process.env.HRM_BROWSER_ARTIFACT_DIR || path.resolve('artifacts', 'browser-verification');
const PLAYWRIGHT_MODULE_DIR = process.env.NODE_REPL_NODE_MODULE_DIRS
  || 'C:\\Users\\Administrator\\.cache\\codex-runtimes\\codex-primary-runtime\\dependencies\\node\\node_modules';
const PLAYWRIGHT_PNPM_MODULE_DIR = process.env.PLAYWRIGHT_PNPM_MODULE_DIR
  || path.join(PLAYWRIGHT_MODULE_DIR, '.pnpm', 'playwright@1.60.0', 'node_modules');
const EDGE_EXECUTABLE = process.env.HRM_BROWSER_EXECUTABLE
  || 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const CASE_TIMEOUT_MS = Number(process.env.HRM_ASSISTANT_UI_PERMISSION_GATE_TIMEOUT_MS || 90000);
const LOGIN_SPACING_MS = Number(process.env.HRM_ASSISTANT_UI_PERMISSION_GATE_LOGIN_SPACING_MS || 900);

const stamp = new Date().toISOString().replace(/[-:TZ.]/g, '').slice(0, 14);
const runId = process.env.HRM_ASSISTANT_UI_PERMISSION_GATE_RUN_ID || `assistant-ui-write-permission-${stamp}`;

const ACCOUNTS = {
  admin: process.env.HRM_ADMIN_TEST_EMAIL || process.env.HRM_TEST_EMAIL || 'admin@hengpintong.demo',
  employee: process.env.HRM_EMPLOYEE_TEST_EMAIL || 'employee@hengpintong.demo',
  finance: process.env.HRM_FINANCE_TEST_EMAIL || 'finance@hengpintong.demo',
  manager: process.env.HRM_MANAGER_TEST_EMAIL || 'rd.manager@hengpintong.demo',
};

const CASES = [
  {
    key: 'attendanceReminderEmployeeDenied',
    role: 'employee',
    path: '/attendance-stats',
    question: '帮我给本月迟到最多的5个人生成提醒',
    intent: 'attendance.late_top_notify',
    expectedMessagePart: '只有管理员或 HR 管理员',
  },
  {
    key: 'salaryAdjustmentFinanceDenied',
    role: 'finance',
    path: '/salary-profiles',
    question: '给张三调薪 1800 元，先分析影响并准备确认',
    intent: 'employee.salary_adjustment.impact',
    expectedMessagePart: '只有管理员、HR 管理员或管理角色可以发起调薪影响分析',
  },
  {
    key: 'approvalDecisionManagerOwnershipDenied',
    role: 'manager',
    path: '/approval-center/pending',
    question: '请通过审批 demo-approval-offer-zhaoliu，意见：同意',
    intent: 'approval.decision',
    expectedMessagePart: '这条审批不属于当前用户',
  },
  {
    key: 'employeeChangeEmployeeDenied',
    role: 'employee',
    path: '/hrm/employee-changes',
    question: '如果把张三调到产品部，会影响哪些岗位、薪资、通知和审批？',
    intent: 'employee.change.impact',
    expectedMessagePart: '只有管理员、HR 管理员或管理角色可以发起员工异动影响分析',
  },
];

const fail = (message, details) => {
  if (details) console.error(JSON.stringify(details, null, 2));
  throw new Error(message);
};

const assertIsolatedTarget = () => {
  const url = new URL(BASE_URL);
  if (url.port === '8080' || url.port === '8443') {
    fail('Refusing to run assistant UI write-permission gate against old production ports', { baseUrl: BASE_URL });
  }
};

const importPlaywright = async () => {
  try {
    return await import('playwright');
  } catch {
    const packageJson = path.join(PLAYWRIGHT_PNPM_MODULE_DIR, 'playwright', 'package.json');
    const runtimeRequire = createRequire(pathToFileURL(packageJson).href);
    return runtimeRequire('playwright');
  }
};

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const compact = (value) => String(value || '').replace(/\s+/g, ' ').trim();
const sanitize = (value) => String(value).replace(/[^a-z0-9_-]+/gi, '-').replace(/^-|-$/g, '').slice(0, 80);
const toArray = (payload) => Array.isArray(payload) ? payload : Array.isArray(payload?.items) ? payload.items : [];

const requestJson = async (method, pathName, payload, token, requestId, options = {}) => {
  const response = await fetch(`${BASE_URL}${pathName}`, {
    method,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(requestId ? { 'X-Request-ID': requestId } : {}),
    },
    body: method === 'GET' ? undefined : JSON.stringify(payload || {}),
  });
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
  return toArray(await requestJson(
    'GET',
    `/api/hrm/${resource}${query ? `?${query}` : ''}`,
    null,
    token,
    `${runId}-${resource}-${Date.now()}`,
  ));
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
  notifications: await countRows(token, 'notifications'),
  zhangsanEmployeeChanges: await countRows(token, 'employee-changes', { employeeId: 'demo-emp-rd-zhangsan' }),
  overtimeApproval: await findApproval(token, 'demo-approval-overtime-lisi'),
  offerApproval: await findApproval(token, 'demo-approval-offer-zhaoliu'),
});

const assertSnapshotUnchanged = (before, after, label) => {
  const fields = ['employeeChanges', 'approvalRecords', 'operationLogs', 'notifications', 'zhangsanEmployeeChanges'];
  for (const field of fields) {
    if (before[field] !== after[field]) fail(`${label} business snapshot field changed: ${field}`, { before, after });
  }
  for (const field of ['overtimeApproval', 'offerApproval']) {
    if ((before[field]?.status || null) !== (after[field]?.status || null)) {
      fail(`${label} approval status changed: ${field}`, { before: before[field], after: after[field] });
    }
  }
};

const responseActions = (payload) => [
  ...(Array.isArray(payload?.actions) ? payload.actions : []),
  ...(Array.isArray(payload?.copilot?.actions) ? payload.copilot.actions : []),
];

const assertForbiddenPayload = (payload, testCase) => {
  const meta = payload?.meta || {};
  const decision = meta.routeDecision || {};
  const copilot = payload?.copilot || {};
  const actions = responseActions(payload);
  if (meta.taskType !== 'forbidden') fail('Final payload meta taskType is not forbidden', { case: testCase.key, meta });
  if (copilot.taskType !== 'forbidden') fail('Final payload copilot taskType is not forbidden', { case: testCase.key, copilot });
  if (meta.requiresConfirmation !== false) fail('Final payload meta requiresConfirmation is not false', { case: testCase.key, meta });
  if (copilot.requiresConfirmation !== false) fail('Final payload copilot requiresConfirmation is not false', { case: testCase.key, copilot });
  if (actions.length !== 0) fail('Forbidden UI payload exposed executable actions', { case: testCase.key, actions });
  if (decision.intent !== testCase.intent) fail('Route intent mismatch', { case: testCase.key, expected: testCase.intent, decision });
  if (!Array.isArray(decision.safetyFlags) || !decision.safetyFlags.includes('permission_denied')) {
    fail('Route decision did not expose permission_denied safety flag', { case: testCase.key, decision });
  }
  if (testCase.expectedMessagePart && !compact(payload.summary).includes(testCase.expectedMessagePart)) {
    fail('Forbidden payload summary did not include expected permission message', {
      case: testCase.key,
      expected: testCase.expectedMessagePart,
      summary: payload.summary,
    });
  }
  const responseGeneration = copilot.responseGeneration || {};
  if (responseGeneration.model !== 'deterministic-permission-denied-receipt') {
    fail('Forbidden payload did not expose deterministic permission-denied receipt model', { case: testCase.key, responseGeneration });
  }
  if (responseGeneration.skippedProviderCall !== true || responseGeneration.deterministicReceipt !== true) {
    fail('Forbidden payload did not mark deterministic skipped provider receipt', { case: testCase.key, responseGeneration });
  }
};

const fetchAuditRow = async (adminToken, requestId) => {
  const encoded = encodeURIComponent(requestId);
  for (let attempt = 1; attempt <= 16; attempt += 1) {
    const rows = await requestJson(
      'GET',
      `/api/hrm/assistant/audit-logs?keyword=${encoded}&limit=20`,
      null,
      adminToken,
      `${runId}-audit-${sanitize(requestId)}-${attempt}`,
    );
    const row = rows.find((item) => item.requestId === requestId && item.useCase === 'assistant_stream_answer');
    if (row) return row;
    await sleep(900);
  }
  fail('No assistant_stream_answer audit row found for UI permission request', { requestId });
};

const assertPermissionDeniedAudit = (row, testCase) => {
  const meta = row.responseMeta || {};
  if (row.provider !== 'siliconflow') fail('Permission-denied audit provider is not siliconflow', { case: testCase.key, row });
  if (row.model !== 'deterministic-permission-denied-receipt') {
    fail('Permission-denied audit model is not deterministic permission receipt', { case: testCase.key, row });
  }
  if (row.status !== 'success') fail('Permission-denied audit status is not success', { case: testCase.key, row });
  if (meta.stream !== true) fail('Permission-denied audit responseMeta.stream is not true', { case: testCase.key, row });
  if (!(Number(meta.chunkCount) > 0)) fail('Permission-denied audit has no positive chunkCount', { case: testCase.key, row });
  if (meta.deterministicReceipt !== true || meta.skippedProviderCall !== true) {
    fail('Permission-denied audit did not mark deterministic skipped provider receipt', { case: testCase.key, row });
  }
  if (meta.reason !== 'permission_denied_receipt') {
    fail('Permission-denied audit reason mismatch', { case: testCase.key, row });
  }
};

const parseAssistantRequest = (request) => {
  try {
    return JSON.parse(request.postData() || '{}');
  } catch {
    return {};
  }
};

const requestIdFrom = (request, label) => {
  const requestId = request.headers()['x-request-id'];
  if (!requestId) fail(`${label} request did not include X-Request-ID`, { url: request.url() });
  return requestId;
};

const parseSseFinalPayload = (text) => {
  let finalPayload = null;
  for (const rawEvent of String(text || '').split(/\n\n+/)) {
    const lines = rawEvent.split('\n');
    let event = 'message';
    const dataLines = [];
    for (const line of lines) {
      if (line.startsWith('event:')) event = line.slice(6).trim();
      if (line.startsWith('data:')) dataLines.push(line.slice(5).trim());
    }
    if (!dataLines.length) continue;
    let payload = null;
    try {
      payload = JSON.parse(dataLines.join('\n'));
    } catch {
      continue;
    }
    if (event === 'final' || payload?.summary || payload?.answer) {
      finalPayload = payload;
    }
  }
  if (!finalPayload) fail('Assistant SSE response did not include a final payload', text.slice(0, 1200));
  return finalPayload;
};

const queryCount = async (locator) => locator.count().catch(() => 0);

const panelSnapshot = async (panel) => {
  const text = await panel.innerText({ timeout: 5000 }).catch(() => '');
  return {
    text: compact(text),
    confirmationButtonCount: await queryCount(panel.locator('button:has-text("确认执行")')),
    loadingCount: await queryCount(panel.locator('.fa-circle-notch.fa-spin, .fa-spinner.fa-spin')),
  };
};

const waitForForbiddenUi = async (panel, testCase) => {
  const deadline = Date.now() + CASE_TIMEOUT_MS;
  let last = null;
  while (Date.now() < deadline) {
    last = await panelSnapshot(panel);
    if (
      last.text.includes(testCase.expectedMessagePart)
      && last.confirmationButtonCount === 0
      && last.loadingCount === 0
    ) {
      return last;
    }
    await sleep(1000);
  }
  fail('Assistant UI did not render the expected forbidden response', {
    case: testCase.key,
    expectedMessagePart: testCase.expectedMessagePart,
    snapshot: last,
    textSample: last?.text?.slice(0, 1200),
  });
};

const runCase = async ({ browser, authByRole, adminToken, testCase }) => {
  const auth = authByRole[testCase.role];
  const before = await captureSnapshot(adminToken);
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  await context.addInitScript(({ token, user }) => {
    window.localStorage.setItem('token', token);
    window.localStorage.setItem('user', JSON.stringify(user));
    for (const store of [window.localStorage, window.sessionStorage]) {
      for (const key of Object.keys(store)) {
        if (key.startsWith('ai_assistant_')) store.removeItem(key);
      }
    }
    window.sessionStorage.setItem('ai_assistant_open', '0');
  }, { token: auth.token, user: auth.user });

  const page = await context.newPage();
  const consoleErrors = [];
  const pageErrors = [];
  page.on('console', (message) => {
    if (message.type() === 'error' && !/favicon\.ico|ResizeObserver loop|net::ERR_ABORTED/i.test(message.text())) {
      consoleErrors.push(message.text());
    }
  });
  page.on('pageerror', (error) => pageErrors.push(error?.stack || error?.message || String(error)));

  try {
    await page.goto(`${BASE_URL}${testCase.path}`, { waitUntil: 'domcontentloaded', timeout: 30000 });
    await page.waitForSelector('main.hpt-main', { timeout: 30000 });
    await page.waitForLoadState('networkidle', { timeout: 12000 }).catch(() => null);

    await page.locator('.hpt-ai-assistant-toggle').click({ timeout: 10000 });
    const panel = page.locator('.hpt-ai-assistant-panel');
    await panel.waitFor({ state: 'visible', timeout: 10000 });
    const textarea = panel.locator('textarea').first();
    await textarea.fill(testCase.question, { timeout: 10000 });

    const requestPromise = page.waitForRequest((request) => {
      if (request.method() !== 'POST') return false;
      if (!request.url().includes('/api/hrm/assistant/query')) return false;
      const payload = parseAssistantRequest(request);
      return payload.stream === true && payload.question === testCase.question;
    }, { timeout: 15000 });

    await textarea.press('Enter', { timeout: 10000 });
    const assistantRequest = await requestPromise;
    const requestId = requestIdFrom(assistantRequest, testCase.key);
    const ui = await waitForForbiddenUi(panel, testCase);
    const assistantResponse = await assistantRequest.response();
    if (!assistantResponse) fail('Assistant request did not expose a response', { case: testCase.key, requestId });
    const responseText = await assistantResponse.text();
    const finalPayload = parseSseFinalPayload(responseText);
    assertForbiddenPayload(finalPayload, testCase);

    if (consoleErrors.length || pageErrors.length) {
      fail('Assistant UI permission gate emitted browser errors', {
        case: testCase.key,
        requestId,
        consoleErrors,
        pageErrors,
      });
    }

    const after = await captureSnapshot(adminToken);
    assertSnapshotUnchanged(before, after, testCase.key);

    const audit = await fetchAuditRow(adminToken, requestId);
    assertPermissionDeniedAudit(audit, testCase);

    const screenshotPath = path.join(ARTIFACT_DIR, `${runId}-${sanitize(testCase.key)}.png`);
    await page.screenshot({ path: screenshotPath, fullPage: true }).catch(() => null);

    return {
      ok: true,
      key: testCase.key,
      role: testCase.role,
      path: testCase.path,
      question: testCase.question,
      requestId,
      intent: finalPayload.meta?.routeDecision?.intent,
      taskType: finalPayload.meta?.taskType,
      requiresConfirmation: finalPayload.meta?.requiresConfirmation,
      actionCount: responseActions(finalPayload).length,
      uiSignals: {
        textMatchedPermissionMessage: ui.text.includes(testCase.expectedMessagePart),
        confirmationButtonCount: ui.confirmationButtonCount,
      },
      snapshot: {
        employeeChanges: after.employeeChanges,
        approvalRecords: after.approvalRecords,
        operationLogs: after.operationLogs,
        notifications: after.notifications,
        zhangsanEmployeeChanges: after.zhangsanEmployeeChanges,
        overtimeApprovalStatus: after.overtimeApproval?.status || null,
        offerApprovalStatus: after.offerApproval?.status || null,
      },
      audit: {
        id: audit.id,
        provider: audit.provider,
        model: audit.model,
        status: audit.status,
        useCase: audit.useCase,
        chunkCount: audit.responseMeta?.chunkCount,
        deterministicReceipt: audit.responseMeta?.deterministicReceipt === true,
        skippedProviderCall: audit.responseMeta?.skippedProviderCall === true,
        reason: audit.responseMeta?.reason,
      },
      screenshotPath,
    };
  } finally {
    await page.close().catch(() => null);
    await context.close().catch(() => null);
  }
};

const main = async () => {
  assertIsolatedTarget();
  await mkdir(ARTIFACT_DIR, { recursive: true });
  const reportPath = path.join(ARTIFACT_DIR, `${runId}.json`);

  const adminAuth = await login('admin');
  const roles = [...new Set(CASES.map((testCase) => testCase.role))];
  const authByRole = { admin: adminAuth };
  for (const role of roles) {
    await sleep(LOGIN_SPACING_MS);
    authByRole[role] = await login(role);
  }

  const { chromium } = await importPlaywright();
  const browser = await chromium.launch({
    headless: true,
    executablePath: EDGE_EXECUTABLE,
    args: ['--no-sandbox', '--disable-dev-shm-usage'],
  });

  const results = [];
  const failures = [];
  try {
    for (const testCase of CASES) {
      try {
        const result = await runCase({
          browser,
          authByRole,
          adminToken: adminAuth.token,
          testCase,
        });
        results.push(result);
        console.log(`PASS ${testCase.key} ${testCase.role} ${result.requestId}`);
      } catch (error) {
        const failure = {
          ok: false,
          key: testCase.key,
          role: testCase.role,
          path: testCase.path,
          question: testCase.question,
          error: error?.stack || error?.message || String(error),
        };
        failures.push(failure);
        results.push(failure);
        console.log(`FAIL ${testCase.key} ${testCase.role}`);
      }
    }
  } finally {
    await browser.close().catch(() => null);
  }

  const report = {
    ok: failures.length === 0,
    runId,
    baseUrl: BASE_URL,
    protectedPorts: ['8080', '8443'],
    isolatedDatabase: 'hengpintong_next',
    checkedAt: new Date().toISOString(),
    realBrowser: true,
    modelPolicy: {
      permissionDeniedShortCircuitsBeforeProvider: true,
      deterministicReceiptModel: 'deterministic-permission-denied-receipt',
      skippedProviderCallRequired: true,
      note: 'This gate verifies permission short-circuit behavior. It is not evidence of real model generation quality.',
    },
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
    deterministicPermissionReceipts: results.filter((item) => item.audit?.skippedProviderCall).length,
    failures,
  }, null, 2));
  if (!report.ok) process.exit(1);
};

main().catch((error) => {
  console.error(error?.stack || error?.message || error);
  process.exit(1);
});
