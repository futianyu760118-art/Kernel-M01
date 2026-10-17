import { mkdir, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const BASE_URL = (process.env.HRM_BASE_URL || 'http://localhost:18280').replace(/\/+$/, '');
const FORBIDDEN_LOCAL_BASE = ['localhost:', '18080'].join('');
if (BASE_URL.includes(FORBIDDEN_LOCAL_BASE)) {
  throw new Error('Refusing to verify against 18080 from HRM-saas');
}
const EMAIL = process.env.HRM_TEST_EMAIL || 'admin@hengpintong.demo';
const PASSWORD = process.env.HRM_TEST_PASSWORD || 'Demo@123456';
const ARTIFACT_DIR = process.env.HRM_BROWSER_ARTIFACT_DIR || path.resolve('artifacts', 'browser-verification');
const PLAYWRIGHT_MODULE_DIR = process.env.NODE_REPL_NODE_MODULE_DIRS
  || 'C:\\Users\\Administrator\\.cache\\codex-runtimes\\codex-primary-runtime\\dependencies\\node\\node_modules';
const PLAYWRIGHT_PNPM_MODULE_DIR = process.env.PLAYWRIGHT_PNPM_MODULE_DIR
  || path.join(PLAYWRIGHT_MODULE_DIR, '.pnpm', 'playwright@1.60.0', 'node_modules');
const EDGE_EXECUTABLE = process.env.HRM_BROWSER_EXECUTABLE
  || 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const CASE_TIMEOUT_MS = Number(process.env.HRM_ASSISTANT_APPROVAL_CASE_TIMEOUT_MS || 180000);

const stamp = new Date().toISOString().replace(/[-:TZ.]/g, '').slice(0, 14);
const runId = process.env.HRM_ASSISTANT_APPROVAL_RUN_ID || `assistant-approval-decision-${stamp}`;
const shortRun = runId.replace(/[^a-z0-9]/gi, '').slice(-12);
const employeeName = process.env.HRM_ASSISTANT_APPROVAL_EMPLOYEE_NAME || `AI审批E2E员工${shortRun}`;
const decisionAction = (process.env.HRM_ASSISTANT_APPROVAL_ACTION || 'reject').toLowerCase();
const decisionLabel = decisionAction === 'approve' ? '通过' : '驳回';
const decisionStatus = decisionAction === 'approve' ? 'approved' : 'rejected';

const fail = (message, details) => {
  if (details) console.error(JSON.stringify(details, null, 2));
  throw new Error(message);
};

const assertIsolatedTarget = () => {
  if (/:(8080|8443)(?:\/|$)/.test(BASE_URL)) {
    fail('Refusing to run approval side-effect E2E against old production ports', { baseUrl: BASE_URL });
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

const asJson = async (response, label = response.url) => {
  const text = await response.text();
  try {
    return JSON.parse(text);
  } catch {
    fail(`${label} did not return JSON`, text.slice(0, 1000));
  }
};

const requestJson = async (method, pathName, payload, token, requestId) => {
  const response = await fetch(`${BASE_URL}${pathName}`, {
    method,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(requestId ? { 'X-Request-ID': requestId } : {}),
    },
    body: method === 'GET' ? undefined : JSON.stringify(payload || {}),
  });
  if (!response.ok) {
    const text = await response.text();
    fail(`${method} ${pathName} failed with HTTP ${response.status}`, text.slice(0, 1200));
  }
  return asJson(response, pathName);
};

const login = async () => {
  const payload = await requestJson('POST', '/api/auth/login', { email: EMAIL, password: PASSWORD }, null, `${runId}-login`);
  if (!payload.token || !payload.user) fail('Login succeeded but token/user is missing', payload);
  return payload;
};

const getJson = (pathName, token, requestId) => requestJson('GET', pathName, null, token, requestId);
const postJson = (pathName, payload, token, requestId) => requestJson('POST', pathName, payload, token, requestId);

const createAssistantSession = async (token) => {
  const payload = await postJson('/api/hrm/assistant/sessions/new', {}, token, `${runId}-new-session`);
  if (!payload.id) fail('Assistant new session did not return an id', payload);
  return payload;
};

const listResource = async (token, resource, params = {}) => {
  const query = Object.entries(params)
    .filter(([, value]) => value !== undefined && value !== null && value !== '')
    .map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`)
    .join('&');
  return toArray(await getJson(`/api/hrm/${resource}${query ? `?${query}` : ''}`, token, `${runId}-${resource}-${Date.now()}`));
};

const createResource = (token, resource, payload) => postJson(`/api/hrm/${resource}`, payload, token, `${runId}-create-${resource}-${Date.now()}`);

const byNewIds = (beforeRows, afterRows) => {
  const beforeIds = new Set(beforeRows.map((row) => row.id).filter(Boolean));
  return afterRows.filter((row) => row.id && !beforeIds.has(row.id));
};

const findApprovalById = async (token, approvalId) => {
  const pending = await listResource(token, 'approvals/pending');
  const foundPending = pending.find((row) => row.id === approvalId);
  if (foundPending) return foundPending;
  const records = await listResource(token, 'approvals/records');
  const relatedRecord = records.find((row) => row.approvalId === approvalId);
  if (!relatedRecord) return null;
  return {
    id: approvalId,
    status: relatedRecord.action,
    businessType: relatedRecord.businessType,
    businessId: relatedRecord.businessId,
    title: relatedRecord.approvalTitle,
  };
};

const seedApprovalCase = async (token, user) => {
  const dept = await createResource(token, 'departments', {
    name: `AI审批E2E部${shortRun}`,
    code: `APR${shortRun}`.slice(0, 50),
    status: 'active',
    description: 'AI 助手审批确认 E2E 临时部门',
  });
  const employee = await createResource(token, 'employees', {
    employeeNo: `EAPR${shortRun}`.slice(0, 50),
    name: employeeName,
    email: `${shortRun.toLowerCase()}@approval-e2e.local`,
    status: 'active',
    departmentId: dept.id,
    jobTitle: '审批确认测试岗',
    hireDate: '2026-01-08',
    remark: 'AI 助手审批确认 E2E 临时员工',
  });
  const requestRow = await createResource(token, 'employee-requests', {
    employeeId: employee.id,
    requestType: 'leave',
    title: `AI审批E2E请假申请${shortRun}`,
    startTime: '2026-06-15T09:00:00',
    endTime: '2026-06-15T18:00:00',
    durationHours: 8,
    reason: 'AI 助手审批确认 E2E 临时申请',
    status: 'pending',
  });
  const pendingApprovals = await listResource(token, 'approvals/pending');
  const approval = pendingApprovals.find((row) => row.businessId === requestRow.id && row.businessType === 'employee_request');
  if (!approval) fail('Seeded employee request did not create a pending approval', { requestRow, pendingApprovals: pendingApprovals.slice(0, 8) });
  const privilegedRoles = new Set(['admin', 'hr_admin', 'manager', 'executive']);
  if (approval.approverId && approval.approverId !== user.id && !privilegedRoles.has(user.role)) {
    fail('Seeded approval is not assigned to current non-privileged test user', { approval, userId: user.id, role: user.role });
  }
  return { dept, employee, requestRow, approval };
};

const fetchAuditRow = async (token, requestId) => {
  const encoded = encodeURIComponent(requestId);
  for (let attempt = 1; attempt <= 16; attempt += 1) {
    const rows = await getJson(
      `/api/hrm/assistant/audit-logs?keyword=${encoded}&limit=20`,
      token,
      `${runId}-audit-${sanitize(requestId)}-${attempt}`,
    );
    const row = rows.find((item) => item.requestId === requestId && item.useCase === 'assistant_stream_answer');
    if (row) return row;
    await sleep(900);
  }
  fail('No assistant_stream_answer audit row found for UI request', { requestId });
};

const isDeterministicReceiptAudit = (row) => (
  String(row?.model || '') === 'deterministic-confirmed-action-receipt'
  || row?.responseMeta?.deterministicReceipt === true
);

const verifyAuditRow = (row, label, options = {}) => {
  const meta = row.responseMeta || {};
  if (row.provider !== 'siliconflow') fail(`${label} audit provider is not siliconflow`, row);
  const deterministicReceipt = isDeterministicReceiptAudit(row);
  if (options.requireRealModel && deterministicReceipt) {
    fail(`${label} audit used deterministic receipt but a real model call is required`, row);
  }
  if (row.status !== 'success') fail(`${label} audit status is not success`, row);
  if (deterministicReceipt) {
    if (meta.skippedProviderCall !== true) fail(`${label} deterministic receipt audit did not mark skippedProviderCall`, row);
  } else if (!row.model || !String(row.model).includes('DeepSeek')) {
    fail(`${label} audit model is neither DeepSeek nor deterministic confirmed-action receipt`, row);
  }
  if (meta.stream !== true) fail(`${label} audit responseMeta.stream is not true`, row);
  if (!(Number(meta.chunkCount) > 0)) fail(`${label} audit has no positive chunkCount`, row);
  return deterministicReceipt ? 'deterministic_receipt' : 'real_model';
};

const auditSummary = (row) => ({
  id: row.id,
  provider: row.provider,
  model: row.model,
  status: row.status,
  useCase: row.useCase,
  errorMessage: row.errorMessage,
  chunkCount: row.responseMeta?.chunkCount,
  deterministicReceipt: row.responseMeta?.deterministicReceipt === true,
  skippedProviderCall: row.responseMeta?.skippedProviderCall === true,
  responseMeta: row.responseMeta || {},
});

const queryCount = async (locator) => locator.count().catch(() => 0);

const panelSnapshot = async (panel) => {
  const text = await panel.innerText({ timeout: 5000 }).catch(() => '');
  return {
    text: compact(text),
    tableCount: await queryCount(panel.locator('table')),
    confirmationButtonCount: await queryCount(panel.locator('button:has-text("确认执行")')),
    sourceButtonCount: await queryCount(panel.locator('button:has-text("证据"), button:has-text("来源"), button:has-text("审批")')),
    loadingCount: await queryCount(panel.locator('.fa-circle-notch.fa-spin, .fa-spinner.fa-spin')),
  };
};

const waitForDraftUi = async (panel, approvalId) => {
  const deadline = Date.now() + CASE_TIMEOUT_MS;
  let last = null;
  while (Date.now() < deadline) {
    last = await panelSnapshot(panel);
    const ready = last.text.includes(`审批${decisionLabel}`)
      && last.text.includes(approvalId)
      && last.text.includes('确认执行')
      && last.text.includes('待确认审批明细')
      && last.tableCount >= 1
      && last.confirmationButtonCount >= 1
      && last.loadingCount === 0;
    if (ready) return last;
    await sleep(1000);
  }
  fail('Assistant did not render the approval decision confirmation draft UI', {
    approvalId,
    snapshot: last,
    textSample: last?.text?.slice(0, 1400),
  });
};

const waitForAssistantIdle = async (panel, label) => {
  const deadline = Date.now() + CASE_TIMEOUT_MS;
  let last = null;
  while (Date.now() < deadline) {
    last = await panelSnapshot(panel);
    if (last.loadingCount === 0) return last;
    await sleep(1000);
  }
  fail(`${label} assistant UI stayed loading`, {
    snapshot: last,
    textSample: last?.text?.slice(0, 1400),
  });
};

const parseAssistantRequest = (request) => {
  try {
    return JSON.parse(request.postData() || '{}');
  } catch {
    return {};
  }
};

const waitForAssistantRequest = (page, predicate, label) => page.waitForRequest((request) => {
  if (request.method() !== 'POST') return false;
  if (!request.url().includes('/api/hrm/assistant/query')) return false;
  return predicate(parseAssistantRequest(request));
}, { timeout: 20000 }).catch((error) => {
  fail(`Timed out waiting for ${label} assistant request`, error?.message || String(error));
});

const requestIdFrom = (request, label) => {
  const requestId = request.headers()['x-request-id'];
  if (!requestId) fail(`${label} request did not include X-Request-ID`, { url: request.url() });
  return requestId;
};

const waitForApprovalWrite = async (token, seed, before) => {
  const deadline = Date.now() + CASE_TIMEOUT_MS;
  let latest = {};
  while (Date.now() < deadline) {
    const approval = await findApprovalById(token, seed.approval.id);
    const requestRows = await listResource(token, 'employee-requests', { q: seed.requestRow.title });
    const records = await listResource(token, 'approvals/records');
    const logs = await listResource(token, 'operation-logs');
    const requestRow = requestRows.find((row) => row.id === seed.requestRow.id);
    const createdRecords = byNewIds(before.approvalRecords, records).filter((row) => (
      row.approvalId === seed.approval.id
      && row.action === decisionStatus
      && String(row.comment || '').includes('AI助手E2E')
    ));
    const createdLogs = byNewIds(before.operationLogs, logs).filter((row) => (
      row.module === 'approval'
      && row.action === decisionAction
      && row.targetId === seed.approval.id
      && row.detail?.source === 'assistant_confirmed_action'
    ));
    latest = { approval, requestRow, records, logs, createdRecords, createdLogs };
    if (
      approval?.status === decisionStatus
      && requestRow?.status === decisionStatus
      && createdRecords.length >= 1
      && createdLogs.length >= 1
    ) {
      return latest;
    }
    await sleep(1000);
  }
  fail('Confirmed approval action did not update approval, business request, approval record, or operation log', latest);
};

const main = async () => {
  assertIsolatedTarget();
  await mkdir(ARTIFACT_DIR, { recursive: true });
  const reportPath = path.join(ARTIFACT_DIR, `${runId}.json`);
  const { chromium } = await importPlaywright();
  const auth = await login();
  const assistantSession = await createAssistantSession(auth.token);
  const seed = await seedApprovalCase(auth.token, auth.user);
  const question = process.env.HRM_ASSISTANT_APPROVAL_QUESTION
    || `${decisionLabel}审批ID：${seed.approval.id}，意见：AI助手E2E${decisionLabel}验证`;

  const before = {
    approvalRecords: await listResource(auth.token, 'approvals/records'),
    operationLogs: await listResource(auth.token, 'operation-logs'),
  };

  const browser = await chromium.launch({
    headless: true,
    executablePath: EDGE_EXECUTABLE,
    args: ['--no-sandbox', '--disable-dev-shm-usage'],
  });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  await context.addInitScript(({ token, user, sessionId }) => {
    window.localStorage.setItem('token', token);
    window.localStorage.setItem('user', JSON.stringify(user));
    for (const store of [window.localStorage, window.sessionStorage]) {
      for (const key of Object.keys(store)) {
        if (key.startsWith('ai_assistant_')) store.removeItem(key);
      }
    }
    window.localStorage.setItem(`ai_assistant_session:user:${user.id}`, sessionId);
    window.sessionStorage.setItem('ai_assistant_open', '0');
  }, { token: auth.token, user: auth.user, sessionId: assistantSession.id });

  const page = await context.newPage();
  const consoleErrors = [];
  const pageErrors = [];
  page.on('console', (message) => {
    if (message.type() === 'error' && !/favicon\.ico|ResizeObserver loop|net::ERR_ABORTED/i.test(message.text())) {
      consoleErrors.push(message.text());
    }
  });
  page.on('pageerror', (error) => pageErrors.push(error?.stack || error?.message || String(error)));

  let result;
  try {
    await page.goto(`${BASE_URL}/approval-center/pending`, { waitUntil: 'domcontentloaded', timeout: 30000 });
    await page.waitForSelector('main.hpt-main', { timeout: 30000 });
    await page.waitForLoadState('networkidle', { timeout: 12000 }).catch(() => null);

    await page.locator('.hpt-ai-assistant-toggle').click({ timeout: 10000 });
    const panel = page.locator('.hpt-ai-assistant-panel');
    await panel.waitFor({ state: 'visible', timeout: 10000 });
    const textarea = panel.locator('textarea').first();
    await textarea.fill(question, { timeout: 10000 });

    const prepareRequestPromise = waitForAssistantRequest(
      page,
      (payload) => payload.stream === true && payload.question === question,
      'approval decision draft',
    );
    await textarea.press('Enter', { timeout: 10000 });
    const prepareRequest = await prepareRequestPromise;
    const prepareRequestId = requestIdFrom(prepareRequest, 'approval decision draft');

    const draftUi = await waitForDraftUi(panel, seed.approval.id);
    const draftApproval = await findApprovalById(auth.token, seed.approval.id);
    const draftRequests = await listResource(auth.token, 'employee-requests', { q: seed.requestRow.title });
    const draftRequest = draftRequests.find((row) => row.id === seed.requestRow.id);
    if (draftApproval.status !== 'pending' || draftRequest?.status !== 'pending') {
      fail('Approval draft unexpectedly changed approval or business request before user confirmation', {
        prepareRequestId,
        draftApproval,
        draftRequest,
      });
    }
    const draftScreenshotPath = path.join(ARTIFACT_DIR, `${runId}-draft.png`);
    await page.screenshot({ path: draftScreenshotPath, fullPage: true }).catch(() => null);

    const confirmButtons = panel.locator('button:has-text("确认执行")');
    if (await confirmButtons.count() < 1) fail('No visible approval confirmation button found before executing action');
    const confirmRequestPromise = waitForAssistantRequest(
      page,
      (payload) => payload.stream === true && String(payload.question || '').startsWith('确认执行动作：act_'),
      'confirmed approval decision',
    );
    await confirmButtons.first().click({ timeout: 10000 });
    const confirmRequest = await confirmRequestPromise;
    const confirmPayload = parseAssistantRequest(confirmRequest);
    const confirmQuestion = String(confirmPayload.question || '');
    const confirmRequestId = requestIdFrom(confirmRequest, 'confirmed approval decision');
    const actionId = confirmQuestion.match(/确认执行动作[：:]([A-Za-z0-9_-]+)/)?.[1];
    if (!actionId) fail('Confirmed approval action payload did not include an action id', { confirmQuestion });

    const writeResult = await waitForApprovalWrite(auth.token, seed, before);
    const confirmUi = await waitForAssistantIdle(panel, 'confirmed approval decision');
    const confirmScreenshotPath = path.join(ARTIFACT_DIR, `${runId}-confirmed.png`);
    await page.screenshot({ path: confirmScreenshotPath, fullPage: true }).catch(() => null);

    const duplicateBefore = {
      approvalRecords: writeResult.records,
      operationLogs: writeResult.logs,
    };
    const duplicateRequestPromise = waitForAssistantRequest(
      page,
      (payload) => payload.stream === true && payload.question === confirmQuestion,
      'duplicate confirmed approval decision',
    );
    await panel.locator('button:has-text("确认执行")').first().click({ timeout: 10000 });
    const duplicateRequest = await duplicateRequestPromise;
    const duplicateRequestId = requestIdFrom(duplicateRequest, 'duplicate confirmed approval decision');
    const duplicateUi = await waitForAssistantIdle(panel, 'duplicate confirmed approval decision');
    const duplicateAfter = {
      approvalRecords: await listResource(auth.token, 'approvals/records'),
      operationLogs: await listResource(auth.token, 'operation-logs'),
    };
    const duplicateRecords = byNewIds(duplicateBefore.approvalRecords, duplicateAfter.approvalRecords)
      .filter((row) => row.approvalId === seed.approval.id);
    const duplicateLogs = byNewIds(duplicateBefore.operationLogs, duplicateAfter.operationLogs)
      .filter((row) => row.module === 'approval' && row.targetId === seed.approval.id);
    if (duplicateRecords.length || duplicateLogs.length) {
      fail('Duplicate approval confirmation created additional approval records or operation logs', {
        duplicateRequestId,
        duplicateRecords,
        duplicateLogs,
      });
    }
    const duplicateScreenshotPath = path.join(ARTIFACT_DIR, `${runId}-duplicate-confirm.png`);
    await page.screenshot({ path: duplicateScreenshotPath, fullPage: true }).catch(() => null);

    if (consoleErrors.length || pageErrors.length) {
      fail('Assistant approval decision UI emitted browser errors', { consoleErrors, pageErrors });
    }

    const prepareAudit = await fetchAuditRow(auth.token, prepareRequestId);
    const confirmAudit = await fetchAuditRow(auth.token, confirmRequestId);
    const duplicateAudit = await fetchAuditRow(auth.token, duplicateRequestId);
    verifyAuditRow(prepareAudit, 'approval draft', { requireRealModel: true });
    verifyAuditRow(confirmAudit, 'confirmed approval action');
    verifyAuditRow(duplicateAudit, 'duplicate approval confirmation');

    result = {
      ok: true,
      runId,
      baseUrl: BASE_URL,
      protectedPorts: ['8080', '8443'],
      isolatedDatabase: 'hengpintong_next',
      question,
      decisionAction,
      decisionStatus,
      actionId,
      seeded: {
        employeeId: seed.employee.id,
        employeeName: seed.employee.name,
        requestId: seed.requestRow.id,
        approvalId: seed.approval.id,
      },
      requestIds: {
        draft: prepareRequestId,
        confirmed: confirmRequestId,
        duplicateConfirmation: duplicateRequestId,
      },
      businessWrites: {
        approvalStatus: writeResult.approval.status,
        employeeRequestStatus: writeResult.requestRow?.status,
        approvalRecords: writeResult.createdRecords.map((row) => ({
          id: row.id,
          approvalId: row.approvalId,
          action: row.action,
          comment: row.comment,
        })),
        operationLogs: writeResult.createdLogs.map((row) => ({
          id: row.id,
          module: row.module,
          action: row.action,
          targetId: row.targetId,
          source: row.detail?.source,
        })),
        duplicateApprovalRecords: duplicateRecords.length,
        duplicateOperationLogs: duplicateLogs.length,
      },
      uiSignals: {
        draft: {
          tableCount: draftUi.tableCount,
          confirmationButtonCount: draftUi.confirmationButtonCount,
          sourceButtonCount: draftUi.sourceButtonCount,
        },
        confirmed: {
          tableCount: confirmUi.tableCount,
          confirmationButtonCount: confirmUi.confirmationButtonCount,
          sourceButtonCount: confirmUi.sourceButtonCount,
        },
        duplicate: {
          textMatchedExpiredAction: duplicateUi.text.includes('没有找到可执行') || duplicateUi.text.includes('已完成或已过期'),
          tableCount: duplicateUi.tableCount,
        },
      },
      screenshots: {
        draft: draftScreenshotPath,
        confirmed: confirmScreenshotPath,
        duplicateConfirmation: duplicateScreenshotPath,
      },
      audits: {
        draft: auditSummary(prepareAudit),
        confirmed: auditSummary(confirmAudit),
        duplicateConfirmation: auditSummary(duplicateAudit),
      },
      aiModelSettingsLookup: `${BASE_URL}/ai-model-settings?keyword=${encodeURIComponent(confirmRequestId)}`,
    };
  } finally {
    await page.close().catch(() => null);
    await browser.close().catch(() => null);
  }

  await writeFile(reportPath, `${JSON.stringify({ ...result, reportPath, checkedAt: new Date().toISOString() }, null, 2)}\n`, 'utf8');
  console.log(JSON.stringify({
    ok: true,
    runId,
    reportPath,
    requestIds: result.requestIds,
    seeded: result.seeded,
    businessWrites: result.businessWrites,
    aiModelSettingsKeyword: result.requestIds.confirmed,
    screenshots: result.screenshots,
  }, null, 2));
};

main().catch((error) => {
  console.error(error?.stack || error?.message || error);
  process.exit(1);
});
