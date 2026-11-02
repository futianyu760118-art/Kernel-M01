import http from 'node:http';
import { createHmac, randomUUID, timingSafeEqual } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { extname, join, normalize, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { existsSync, readFileSync } from 'node:fs';
import { M01Kernel } from './kernel.js';
import { createOidcVerifier } from './oidc.js';
import { PostgresStore } from './postgres-store.js';
import { buildLegacyPersonnelCandidates, filterLegacyPersonnel, importLegacyPersonnel, loadEbmsPersonnelSourceSync } from './legacy-import.js';

const ROOT = fileURLToPath(new URL('..', import.meta.url));

export function loadEnvFile(path) {
  if (!existsSync(path)) return;
  const lines = readFileLines(path);
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const eq = trimmed.indexOf('=');
    if (eq <= 0) continue;
    const key = trimmed.slice(0, eq).trim();
    const value = trimmed.slice(eq + 1).trim();
    if (!(key in process.env)) process.env[key] = value;
  }
}

function readFileLines(path) {
  return readFileSync(path, 'utf8').split(/\r?\n/);
}

export function createApp(kernel = new M01Kernel(), config = {}) {
  const configuredMode = Object.prototype.hasOwnProperty.call(config, 'mode') ? config.mode : (process.env.M01_MODE ?? 'demo');
  const mode = String(configuredMode || '').trim().toLowerCase();
  if (mode !== 'demo' && mode !== 'oidc') throw new Error('M01_MODE must be explicitly set to demo or oidc');
  const oidcConfig = {
    ...(config.oidc || {}),
    issuer: config.oidc?.issuer || config.oidcIssuer || process.env.M01_OIDC_ISSUER,
    audience: config.oidc?.audience || config.oidcAudience || process.env.M01_OIDC_AUDIENCE,
    jwksUri: config.oidc?.jwksUri || config.oidcJwksUri || process.env.M01_OIDC_JWKS_URI,
    requiredScope: config.oidc?.requiredScope || config.requiredScope || process.env.M01_OIDC_REQUIRED_SCOPE,
    authorizationEndpoint: config.oidc?.authorizationEndpoint || process.env.M01_OIDC_AUTHORIZATION_ENDPOINT,
    tokenEndpoint: config.oidc?.tokenEndpoint || process.env.M01_OIDC_TOKEN_ENDPOINT,
    clientId: config.oidc?.clientId || process.env.M01_OIDC_CLIENT_ID,
    redirectUri: config.oidc?.redirectUri || process.env.M01_OIDC_REDIRECT_URI,
    scopes: config.oidc?.scopes || process.env.M01_OIDC_SCOPES || 'openid profile email'
  };
  const oidcVerifier = mode === 'oidc' ? createOidcVerifier(oidcConfig) : null;
  const oidcIssuerBase = oidcConfig.issuer ? String(oidcConfig.issuer).replace(/\/$/, '') : null;
  const oidcPublicConfig = mode === 'oidc' ? {
    mode,
    issuer: oidcConfig.issuer,
    authorization_endpoint: oidcConfig.authorizationEndpoint || (oidcIssuerBase ? `${oidcIssuerBase}/protocol/openid-connect/auth` : null),
    token_endpoint: oidcConfig.tokenEndpoint || (oidcIssuerBase ? `${oidcIssuerBase}/protocol/openid-connect/token` : null),
    client_id: oidcConfig.clientId || null,
    redirect_uri: oidcConfig.redirectUri || null,
    scope: Array.isArray(oidcConfig.scopes) ? oidcConfig.scopes.join(' ') : String(oidcConfig.scopes || 'openid profile email'),
    pkce: 'S256'
  } : null;
  const requireExternalIdentityMapping = config.requireExternalIdentityMapping === true || process.env.M01_REQUIRE_EXTERNAL_IDENTITY_MAPPING === 'true';
  const requiredScope = oidcConfig.requiredScope || 'm01:policy:read';
  const adminScope = config.adminScope || oidcConfig.adminScope || process.env.M01_OIDC_ADMIN_SCOPE || 'm01:admin';
  const delegateScope = config.delegateScope || oidcConfig.delegateScope || 'm01:policy:delegate';
  const organization = {
    tenant_id: config.primaryTenantId || process.env.M01_PRIMARY_TENANT_ID || 'TENANT_A',
    name: config.primaryOrganizationName || process.env.M01_PRIMARY_TENANT_NAME || '恒剑光电',
    owner_person_id: config.primaryOwnerPersonId || process.env.M01_PRIMARY_OWNER_PERSON_ID || 'P_OWNER',
    owner_name: config.primaryOwnerName || process.env.M01_PRIMARY_OWNER_NAME || '朱剑军'
  };
  const adminToken = config.adminToken || process.env.M01_ADMIN_TOKEN || 'local-demo-admin';
  const demoOneClickLogin = config.demoOneClickLogin ?? String(process.env.M01_DEMO_ONE_CLICK_LOGIN || 'true').toLowerCase() === 'true';
  const demoRequesterPersonId = config.demoRequesterPersonId || process.env.M01_DEMO_REQUESTER_PERSON_ID || 'P_DEMO_OPERATOR';
  const demoSessions = new Map();
  const m07Secret = config.m07Secret || process.env.M07_WEBHOOK_SECRET || 'local-demo-m07-secret';
  const m13Secret = config.m13Secret || process.env.M13_WEBHOOK_SECRET || 'local-demo-m13-secret';
  const persistence = config.persistence || null;
  let persistenceRevision = 0;
  let persistenceStatus = persistence ? 'initializing' : 'memory';
  let persistenceError = null;

  const persistenceReady = persistence
    ? Promise.resolve()
      .then(async () => {
        await persistence.initialize();
        const loaded = await persistence.loadState();
        persistenceRevision = loaded.revision || 0;
        if (loaded.state) kernel.restoreState(loaded.state);
        persistenceStatus = 'ready';
        return { revision: persistenceRevision };
      })
      .catch((error) => {
        persistenceStatus = 'error';
        persistenceError = error;
        throw error;
      })
    : Promise.resolve({ revision: 0 });

  const persistMutation = async (beforeState) => {
    if (!persistence) return;
    try {
      const saved = await persistence.saveState(kernel.exportState(), kernel.auditLog, persistenceRevision);
      persistenceRevision = saved.revision;
    } catch (error) {
      if (beforeState) kernel.restoreState(beforeState);
      throw error;
    }
  };

  const snapshotBeforeMutation = () => (persistence ? kernel.exportState() : null);

  const persistOr503 = async (res, beforeState) => {
    try {
      await persistMutation(beforeState);
      return true;
    } catch {
      send(res, 503, { error: 'persistence unavailable; mutation rolled back' });
      return false;
    }
  };

  const safeEqual = (a, b) => {
    const ba = Buffer.from(String(a));
    const bb = Buffer.from(String(b));
    if (ba.length !== bb.length) return false;
    return timingSafeEqual(ba, bb);
  };

  const sign = (secret, body) => createHmac('sha256', secret).update(body).digest('hex');

  const verifyHmac = (secret, rawBody, headers) => {
    const header = headers['x-m01-signature'] || headers['x-signature'];
    if (!header) return false;
    const provided = String(header).startsWith('sha256=') ? String(header).slice(7) : String(header);
    return safeEqual(provided, sign(secret, rawBody));
  };

  const checkAdmin = (req) => {
    const header = req.headers['authorization'] || '';
    if (!header.startsWith('Bearer ')) return false;
    return safeEqual(header.slice(7), adminToken);
  };

  const cookieValue = (req, name) => {
    const cookies = String(req.headers.cookie || '').split(';');
    const item = cookies.find((part) => part.trim().startsWith(`${name}=`));
    return item ? decodeURIComponent(item.trim().slice(name.length + 1)) : '';
  };

  const checkDemoSession = (req) => {
    const sessionId = cookieValue(req, 'm01_demo_session');
    const expiresAt = sessionId ? demoSessions.get(sessionId) : 0;
    if (!expiresAt) return false;
    if (expiresAt <= Date.now()) {
      demoSessions.delete(sessionId);
      return false;
    }
    return true;
  };

  const hasScope = (principal, scope) => principal.scope.includes(scope);
  const authenticateKernel = async (req) => {
    const header = String(req.headers.authorization || '');
    if (mode === 'demo') {
      return (checkAdmin(req) || checkDemoSession(req))
        ? { ok: true, principal: null }
        : { ok: false, status: 401, error: '请先点击一键登录' };
    }
    if (!header.startsWith('Bearer ')) return { ok: false, status: 401, error: 'missing or invalid bearer token' };
    try {
      const verified = await oidcVerifier.verify(header.slice(7));
      const mapping = kernel.resolveExternalIdentity({ issuer: oidcConfig.issuer, subject: verified.principal_id });
      if (mapping && mapping.tenant_id !== verified.tenant_id) return { ok: false, status: 403, error: 'external identity tenant mismatch' };
      if (requireExternalIdentityMapping && !mapping) return { ok: false, status: 403, error: 'external identity is not mapped' };
      const principal = mapping ? { ...verified, principal_id: mapping.principal_id, tenant_id: mapping.tenant_id } : verified;
      const current = kernel.authorizationStatus(principal.principal_id);
      if (current.ok !== false && current.revoked) return { ok: false, status: 401, error: 'principal revoked' };
      if (current.ok !== false && principal.authz_version < current.authz_version) return { ok: false, status: 401, error: 'stale authorization version' };
      if (!hasScope(principal, requiredScope)) return { ok: false, status: 403, error: 'missing required scope' };
      return { ok: true, principal };
    } catch {
      return { ok: false, status: 401, error: 'invalid or unverifiable bearer token' };
    }
  };

  const send = (res, status, payload, extraHeaders = {}) => {
    const body = JSON.stringify(payload, null, 2);
    res.writeHead(status, {
      'Content-Type': 'application/json; charset=utf-8',
      'Content-Length': Buffer.byteLength(body),
      'X-M01-Environment': mode === 'oidc' ? 'OIDC' : 'DEMO ONLY',
      ...extraHeaders
    });
    res.end(body);
  };

  const readBody = (req, limit = 1024 * 1024) =>
    new Promise((resolveBody, reject) => {
      const chunks = [];
      let size = 0;
      req.on('data', (chunk) => {
        size += chunk.length;
        if (size > limit) {
          reject(new Error('payload too large'));
          req.destroy();
          return;
        }
        chunks.push(chunk);
      });
      req.on('end', () => resolveBody(Buffer.concat(chunks)));
      req.on('error', reject);
    });

  const parseJson = (raw) => {
    try {
      return { ok: true, data: JSON.parse(raw.toString('utf8') || '{}') };
    } catch {
      return { ok: false };
    }
  };

  const serveStatic = async (pathname, res) => {
    const publicDir = resolve(ROOT, 'public');
    let target = normalize(join(publicDir, pathname === '/' ? 'index.html' : pathname));
    if (!target.startsWith(publicDir + sep) && target !== publicDir) {
      send(res, 403, { error: 'forbidden' });
      return;
    }
    if (!existsSync(target)) {
      send(res, 404, { error: 'not found' });
      return;
    }
    const types = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8' };
    const data = await readFile(target);
    res.writeHead(200, {
      'Content-Type': types[extname(target)] || 'application/octet-stream',
      'Content-Length': data.length,
      'X-M01-Environment': mode === 'oidc' ? 'OIDC' : 'DEMO ONLY'
    });
    res.end(data);
  };

  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://localhost');
    const pathname = url.pathname;

    try {
      if (req.method === 'GET' && pathname === '/health') {
        send(res, 200, { status: 'ok', service: 'aeos-m01-kernel', mode, environment: mode === 'oidc' ? 'OIDC' : 'DEMO ONLY' });
        return;
      }

      if (req.method === 'POST' && pathname === '/auth/demo/login') {
        if (mode !== 'demo' || !demoOneClickLogin) {
          send(res, 404, { error: '演示环境一键登录未启用' });
          return;
        }
        const sessionId = randomUUID();
        demoSessions.set(sessionId, Date.now() + 8 * 60 * 60 * 1000);
        send(res, 200, { logged_in: true, mode: 'demo', organization }, {
          'Set-Cookie': `m01_demo_session=${encodeURIComponent(sessionId)}; HttpOnly; Path=/; SameSite=Lax; Max-Age=28800`,
          'Cache-Control': 'no-store'
        });
        return;
      }

      if (req.method === 'POST' && pathname === '/auth/demo/logout') {
        const sessionId = cookieValue(req, 'm01_demo_session');
        if (sessionId) demoSessions.delete(sessionId);
        send(res, 200, { logged_in: false }, { 'Set-Cookie': 'm01_demo_session=; HttpOnly; Path=/; SameSite=Lax; Max-Age=0' });
        return;
      }

      if (req.method === 'GET' && pathname === '/auth/oidc/config') {
        if (mode !== 'oidc') {
          send(res, 404, { error: 'OIDC is disabled; set M01_MODE=oidc for formal browser login' });
          return;
        }
        send(res, 200, oidcPublicConfig);
        return;
      }

      if (req.method === 'GET' && pathname === '/readyz') {
        if (persistenceStatus === 'error') {
          send(res, 503, { status: 'not_ready', persistence: 'postgres', error: 'persistence initialization failed' });
          return;
        }
        if (persistenceStatus === 'initializing') {
          send(res, 503, { status: 'not_ready', persistence: 'postgres' });
          return;
        }
        send(res, 200, { status: 'ready', persistence: persistence ? 'postgres' : 'memory', revision: persistenceRevision });
        return;
      }

      if (persistenceStatus !== 'ready' && persistenceStatus !== 'memory') {
        send(res, 503, { error: 'service not ready' });
        return;
      }

      if (req.method === 'GET' && !pathname.startsWith('/kernel/') && !pathname.startsWith('/integrations/')) {
        await serveStatic(pathname, res);
        return;
      }

      if (pathname.startsWith('/integrations/')) {
        if (req.method !== 'POST') {
          send(res, 405, { error: 'method not allowed' });
          return;
        }
        const raw = await readBody(req);
        const isM13Status = pathname === '/integrations/m13/status';
        const secret = pathname === '/integrations/m07' ? m07Secret : (pathname === '/integrations/m13' || isM13Status) ? m13Secret : null;
        if (!secret) {
          send(res, 404, { error: 'unknown integration' });
          return;
        }
        if (!verifyHmac(secret, raw, req.headers)) {
          send(res, 401, { error: 'invalid or missing HMAC signature' });
          return;
        }
        const parsed = parseJson(raw);
        if (!parsed.ok) {
          send(res, 400, { error: 'invalid JSON body' });
          return;
        }
        if (isM13Status) {
          const tenantId = parsed.data.tenant_id;
          const agentId = parsed.data.agent_id;
          if (!tenantId || !agentId) {
            send(res, 400, { error: 'required: tenant_id, agent_id' });
            return;
          }
          const agent = kernel.status().agents.find((item) => item.agent_id === agentId && item.tenant_id === tenantId);
          if (!agent) {
            send(res, 404, { error: 'unknown agent_id' });
            return;
          }
          send(res, 200, { ok: true, agent });
          return;
        }
        const beforeState = snapshotBeforeMutation();
        const result = pathname === '/integrations/m07' ? kernel.applyM07Event(parsed.data) : kernel.applyM13Event(parsed.data);
        if (result.ok !== false && !(await persistOr503(res, beforeState))) return;
        send(res, result.ok === false ? result.status || 400 : 200, result.ok === false ? { error: result.error } : result);
        return;
      }

      if (pathname.startsWith('/kernel/')) {
        const auth = await authenticateKernel(req);
        if (!auth.ok) {
          send(res, auth.status || 401, { error: auth.error });
          return;
        }
        const principal = auth.principal;
        if (pathname === '/kernel/v1/approvals' && req.method === 'POST') {
          const parsed = parseJson(await readBody(req));
          if (!parsed.ok) {
            send(res, 400, { error: 'invalid JSON body' });
            return;
          }
          const body = parsed.data;
          const beforeState = snapshotBeforeMutation();
          const result = kernel.createApproval({
            tenant_id: mode === 'oidc' ? principal.tenant_id : body.tenant_id,
            requester_id: mode === 'oidc' ? principal.principal_id : body.requester_id,
            resource: body.resource,
            action: body.action,
            object_id: body.object_id,
            parameters: body.parameters || {}
          });
          if (result.ok && !(await persistOr503(res, beforeState))) return;
          send(res, result.ok ? 201 : result.status || 400, result.ok ? result : { error: result.error });
          return;
        }
        const approvalDecisionPath = pathname.match(/^\/kernel\/v1\/approvals\/([^/]+)\/decision$/);
        if (approvalDecisionPath && req.method === 'POST') {
          if (mode === 'oidc' && !hasScope(principal, adminScope)) {
            send(res, 403, { error: 'admin scope required to decide approval' });
            return;
          }
          const parsed = parseJson(await readBody(req));
          if (!parsed.ok) {
            send(res, 400, { error: 'invalid JSON body' });
            return;
          }
          const body = parsed.data;
          const beforeState = snapshotBeforeMutation();
          const result = kernel.decideApproval({
            tenant_id: mode === 'oidc' ? principal.tenant_id : body.tenant_id,
            approval_id: decodeURIComponent(approvalDecisionPath[1]),
            approver_id: mode === 'oidc' ? principal.principal_id : body.approver_id,
            decision: body.decision,
            comment: body.comment
          });
          if (result.ok && !(await persistOr503(res, beforeState))) return;
          send(res, result.ok ? 200 : result.status || 400, result.ok ? result : { error: result.error });
          return;
        }
        if (pathname === '/kernel/v1/approvals' && req.method === 'GET') {
          if (mode === 'oidc' && !hasScope(principal, adminScope)) {
            send(res, 403, { error: 'admin scope required to list approvals' });
            return;
          }
          const tenant_id = mode === 'oidc' ? principal.tenant_id : url.searchParams.get('tenant_id') || 'TENANT_A';
          send(res, 200, { approvals: kernel.listApprovals(tenant_id) });
          return;
        }
        const adminRoute = pathname === '/kernel/v1/status' || pathname === '/kernel/v1/audit' || pathname === '/kernel/v1/revocations';
        const masterRoute = pathname.startsWith('/kernel/v1/master/');
        const quickRoute = pathname === '/kernel/v1/quick/person-action';
        if (mode === 'oidc' && (adminRoute || masterRoute || quickRoute) && !hasScope(principal, adminScope)) {
          send(res, 403, { error: 'missing admin scope' });
          return;
        }
        const authorizationPath = pathname.match(/^\/kernel\/v1\/principals\/([^/]+)\/authorization$/);
        if (req.method === 'GET' && authorizationPath) {
          const target = decodeURIComponent(authorizationPath[1]);
          if (mode === 'oidc' && target !== principal.principal_id && !hasScope(principal, adminScope)) {
            send(res, 403, { error: 'admin scope required to inspect another principal' });
            return;
          }
          const result = kernel.authorizationStatus(target);
          send(res, result.ok === false ? result.status || 404 : 200, result.ok === false ? { error: result.error } : result);
          return;
        }
        if (req.method === 'GET' && pathname === '/kernel/v1/status') {
          send(res, 200, { ...kernel.status(), organization, environment: mode === 'oidc' ? 'OIDC' : 'DEMO ONLY', mode });
          return;
        }
        if (req.method === 'GET' && pathname === '/kernel/v1/audit') {
          send(res, 200, { entries: kernel.auditLog.slice(-100), total: kernel.auditLog.length });
          return;
        }
        if (req.method !== 'POST') {
          send(res, 405, { error: 'method not allowed' });
          return;
        }
        const parsed = parseJson(await readBody(req));
        if (!parsed.ok) {
          send(res, 400, { error: 'invalid JSON body' });
          return;
        }
        const body = parsed.data;
        if (mode === 'oidc') {
          if (body.tenant_id && body.tenant_id !== principal.tenant_id) {
            send(res, 403, { error: 'tenant boundary violation' });
            return;
          }
          body.tenant_id = principal.tenant_id;
          if (pathname === '/kernel/v1/capabilities' || pathname === '/kernel/v1/authorize') {
            const requested = body.principal_id || principal.principal_id;
            if (requested !== principal.principal_id && !hasScope(principal, delegateScope)) {
              body.principal_id = principal.principal_id;
            } else if (requested !== principal.principal_id) {
              const target = kernel.authorizationStatus(requested);
              if (target.ok === false || target.tenant_id !== principal.tenant_id) {
                send(res, 403, { error: 'delegation tenant boundary violation' });
                return;
              }
              body.principal_id = requested;
            } else {
              body.principal_id = principal.principal_id;
            }
          }
        }
        if (pathname === '/kernel/v1/quick/person-action') {
          const actionMap = {
            hire: { command: 'person.hire', position_id: body.position_id || 'POS_STAFF' },
            transfer_manager: { command: 'person.transfer', position_id: 'POS_MANAGER' },
            offboard: { command: 'person.offboard' }
          };
          const selected = actionMap[body.action];
          if (!selected || !body.person_id) {
            send(res, 400, { error: '请选择人员和办理事项' });
            return;
          }
          const beforeState = snapshotBeforeMutation();
          const tenantId = mode === 'oidc' ? principal.tenant_id : body.tenant_id;
          const result = kernel.dispatchCommand({
            tenant_id: tenantId,
            command: selected.command,
            params: {
              person_id: body.person_id,
              ...(selected.position_id ? { position_id: selected.position_id } : {}),
              ...(body.approval_ticket ? { approval_ticket: body.approval_ticket } : {})
            }
          });
          let approval = null;
          if (result.decision === 'REQUIRE_APPROVAL') {
            approval = kernel.listApprovals(tenantId).find((item) => item.status === 'PENDING'
              && item.resource === 'master' && item.action === 'person.offboard' && item.object_id === body.person_id);
            if (!approval) {
              const created = kernel.createApproval({
                tenant_id: tenantId,
                requester_id: mode === 'oidc' ? principal.principal_id : demoRequesterPersonId,
                resource: 'master',
                action: 'person.offboard',
                object_id: body.person_id,
                parameters: { person_id: body.person_id }
              });
              if (!created.ok) {
                send(res, created.status || 400, { error: created.error });
                return;
              }
              approval = created.approval;
            }
          }
          if (result.trace_id && !(await persistOr503(res, beforeState))) return;
          const message = result.decision === 'EXECUTED' ? '办理完成' : result.decision === 'REQUIRE_APPROVAL' ? '需要审批，尚未办理' : '暂时不能办理';
          send(res, 200, { ...result, message, ...(approval ? { approval } : {}) });
          return;
        }
        if (pathname === '/kernel/v1/capabilities') {
          if (!body.principal_id) {
            send(res, 400, { error: 'required: principal_id' });
            return;
          }
          send(res, 200, kernel.capabilities(body.principal_id, body.tenant_id));
          return;
        }
        if (pathname === '/kernel/v1/authorize') {
          const decision = kernel.authorize(body);
          if (decision.ok === false) {
            send(res, decision.status || 400, { error: decision.error });
            return;
          }
          send(res, 200, decision);
          return;
        }
        if (pathname === '/kernel/v1/revocations') {
          if (!body.principal_id) {
            send(res, 400, { error: 'required: principal_id' });
            return;
          }
          const beforeState = snapshotBeforeMutation();
          const result = kernel.revoke(body.principal_id, body.reason);
          if (result.ok === false) {
            send(res, result.status || 404, { error: result.error });
            return;
          }
          if (!(await persistOr503(res, beforeState))) return;
          send(res, 200, result);
          return;
        }
        if (pathname === '/kernel/v1/master/register') {
          const beforeState = snapshotBeforeMutation();
          const result = kernel.registerMaster({
            tenant_id: body.tenant_id,
            owner_person_id: body.owner_person_id,
            master_agent_id: body.master_agent_id,
            idempotency_key: body.idempotency_key
          });
          if (result.ok === false) {
            send(res, result.status || 400, { error: result.error });
            return;
          }
          if (!(await persistOr503(res, beforeState))) return;
          send(res, 200, result);
          return;
        }
        if (pathname === '/kernel/v1/master/command') {
          const beforeState = snapshotBeforeMutation();
          const result = kernel.dispatchCommand({ tenant_id: body.tenant_id, command: body.command, params: body.params });
          if (result.ok === false) {
            send(res, result.status || 400, { error: result.error });
            return;
          }
          if (result.trace_id && !(await persistOr503(res, beforeState))) return;
          send(res, 200, result);
          return;
        }
        if (pathname === '/kernel/v1/master/say') {
          if (!body.text) {
            send(res, 400, { error: 'required: text' });
            return;
          }
          const beforeState = snapshotBeforeMutation();
          const result = kernel.say({ tenant_id: body.tenant_id, text: body.text });
          if ((result.decision === 'EXECUTED' || result.registration) && !(await persistOr503(res, beforeState))) return;
          send(res, 200, result);
          return;
        }
        send(res, 404, { error: 'unknown kernel endpoint' });
        return;
      }

      send(res, 404, { error: 'not found' });
    } catch (err) {
      send(res, 500, { error: 'internal error', detail: String(err.message || err) });
    }
  });

  server.m01Kernel = kernel;
  server.ready = persistenceReady;
  server.persistence = persistence;
  server.persistState = async (beforeState = null) => {
    await persistenceReady;
    await persistMutation(beforeState);
    return persistenceRevision;
  };
  return server;
}

export function start() {
  loadEnvFile(join(ROOT, '.env'));
  const configuredMode = String(process.env.M01_MODE || '').trim().toLowerCase();
  if (configuredMode !== 'demo' && configuredMode !== 'oidc') {
    throw new Error('M01_MODE must be explicitly set to demo or oidc before starting M01');
  }
  const port = Number(process.env.PORT || 3101);
  const host = process.env.HOST || '0.0.0.0';
  const databaseUrl = String(process.env.M01_DATABASE_URL || process.env.DATABASE_URL || '').trim();
  if (configuredMode === 'oidc' && !databaseUrl) {
    throw new Error('M01_DATABASE_URL must be configured in oidc formal mode');
  }
  if (configuredMode === 'oidc') {
    for (const name of ['M01_OIDC_ISSUER', 'M01_OIDC_AUDIENCE', 'M01_OIDC_JWKS_URI', 'M01_OIDC_CLIENT_ID', 'M01_OIDC_REDIRECT_URI']) {
      if (!String(process.env[name] || '').trim()) throw new Error(`${name} must be configured in oidc formal mode`);
    }
  }
  const kernel = new M01Kernel({ allowImplicitOwner: configuredMode !== 'oidc' });
  const persistence = databaseUrl ? new PostgresStore({ connectionString: databaseUrl }) : null;
  const legacySourceDir = String(process.env.M01_EBMS_SOURCE_DIR || '').trim();
  const primaryOwnerEnabled = String(process.env.M01_PRIMARY_OWNER_ENABLED || (configuredMode === 'demo' ? 'true' : 'false')).toLowerCase() === 'true';
  const primaryOrganizationName = String(process.env.M01_PRIMARY_TENANT_NAME || '恒剑光电').trim();
  const primaryTenantId = String(process.env.M01_PRIMARY_TENANT_ID || 'TENANT_A').trim();
  const primaryOwnerPersonId = String(process.env.M01_PRIMARY_OWNER_PERSON_ID || 'P_OWNER').trim();
  const primaryOwnerName = String(process.env.M01_PRIMARY_OWNER_NAME || '朱剑军').trim();
  const server = createApp(kernel, {
    mode: configuredMode,
    persistence,
    requireExternalIdentityMapping: configuredMode === 'oidc',
    primaryOrganizationName,
    primaryTenantId,
    primaryOwnerPersonId,
    primaryOwnerName
  });
  server.startup = (async () => {
    await server.ready;
    if (primaryOwnerEnabled) {
      const beforeState = persistence ? kernel.exportState() : null;
      const bootstrapped = kernel.ensurePrimaryOwner({
        tenant_id: primaryTenantId,
        organization_name: primaryOrganizationName,
        person_id: primaryOwnerPersonId,
        display_name: primaryOwnerName
      });
      if (bootstrapped.ok === false) throw new Error(`primary owner bootstrap failed: ${bootstrapped.error}`);
      if (bootstrapped.changed && persistence) await server.persistState(beforeState);
      console.log(`[M01] 首个管理员已配置：${primaryOrganizationName} / ${primaryOwnerName}（${primaryOwnerPersonId}）`);
    }
    const identityBootstrap = String(process.env.M01_EXTERNAL_IDENTITIES_JSON || '').trim();
    if (identityBootstrap) {
      let mappings;
      try { mappings = JSON.parse(identityBootstrap); } catch { throw new Error('M01_EXTERNAL_IDENTITIES_JSON must be valid JSON'); }
      if (!Array.isArray(mappings)) throw new Error('M01_EXTERNAL_IDENTITIES_JSON must be an array');
      const beforeState = persistence ? kernel.exportState() : null;
      let changed = false;
      for (const mapping of mappings) {
        const result = kernel.mapExternalIdentity(mapping);
        if (result.ok === false) throw new Error(`external identity bootstrap failed: ${result.error}`);
        changed ||= !result.idempotent;
      }
      if (changed && persistence) await server.persistState(beforeState);
    }
    if (legacySourceDir) {
      const beforeState = persistence ? kernel.exportState() : null;
      const source = loadEbmsPersonnelSourceSync(resolve(legacySourceDir));
      const candidates = buildLegacyPersonnelCandidates(source);
      const filtered = filterLegacyPersonnel(candidates, {
        status: process.env.M01_EBMS_IMPORT_STATUS || 'all',
        account: process.env.M01_EBMS_IMPORT_ACCOUNT || 'all',
        department: process.env.M01_EBMS_IMPORT_DEPARTMENT || undefined
      });
      const imported = importLegacyPersonnel(kernel, filtered.selected, { tenant_id: process.env.M01_EBMS_TENANT_ID || 'TENANT_A' });
      if (imported.rejected > 0) throw new Error(`EBMS personnel import rejected ${imported.rejected} record(s)`);
      if (persistence && imported.applied > 0) await server.persistState(beforeState);
      console.log(`[M01] EBMS personnel imported: source=${source.personnel.length}, selected=${filtered.selected.length}, applied=${imported.applied}, idempotent=${imported.idempotent}`);
    }
    await new Promise((resolveListen) => server.listen(port, host, resolveListen));
    console.log(`[M01] listening on http://${host}:${port} (mode=${configuredMode}, persistence=${persistence ? 'postgres' : 'memory'})`);
  })();
  const shutdown = async () => {
    try { await persistence?.close(); } finally {
      if (server.listening) server.close(() => process.exit(0));
      else process.exit(0);
    }
  };
  process.once('SIGTERM', shutdown);
  process.once('SIGINT', shutdown);
  return server;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  start().startup.catch((error) => {
    console.error(`[M01] startup failed: ${error.message || error}`);
    process.exitCode = 1;
  });
}
