import http from 'node:http';
import { createHmac, timingSafeEqual } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { extname, join, normalize, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { existsSync, readFileSync } from 'node:fs';
import { M01Kernel } from './kernel.js';

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
  const adminToken = config.adminToken || process.env.M01_ADMIN_TOKEN || 'local-demo-admin';
  const m07Secret = config.m07Secret || process.env.M07_WEBHOOK_SECRET || 'local-demo-m07-secret';
  const m13Secret = config.m13Secret || process.env.M13_WEBHOOK_SECRET || 'local-demo-m13-secret';

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

  const send = (res, status, payload) => {
    const body = JSON.stringify(payload, null, 2);
    res.writeHead(status, {
      'Content-Type': 'application/json; charset=utf-8',
      'Content-Length': Buffer.byteLength(body),
      'X-M01-Environment': 'DEMO ONLY'
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
      'X-M01-Environment': 'DEMO ONLY'
    });
    res.end(data);
  };

  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://localhost');
    const pathname = url.pathname;

    try {
      if (req.method === 'GET' && pathname === '/health') {
        send(res, 200, { status: 'ok', service: 'aeos-m01-kernel', environment: 'DEMO ONLY' });
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
        const secret = pathname === '/integrations/m07' ? m07Secret : pathname === '/integrations/m13' ? m13Secret : null;
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
        const result = pathname === '/integrations/m07' ? kernel.applyM07Event(parsed.data) : kernel.applyM13Event(parsed.data);
        send(res, result.ok === false ? result.status || 400 : 200, result.ok === false ? { error: result.error } : result);
        return;
      }

      if (pathname.startsWith('/kernel/')) {
        if (pathname === '/kernel/v1/approvals' && req.method === 'POST') {
          send(res, 501, { error: 'approval workflow not implemented in DEMO; high-risk actions stay REQUIRE_APPROVAL / fail closed' });
          return;
        }
        if (!checkAdmin(req)) {
          send(res, 401, { error: 'missing or invalid service token' });
          return;
        }
        if (req.method === 'GET' && pathname === '/kernel/v1/status') {
          send(res, 200, { ...kernel.status(), environment: 'DEMO ONLY' });
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
          const result = kernel.revoke(body.principal_id, body.reason);
          if (result.ok === false) {
            send(res, result.status || 404, { error: result.error });
            return;
          }
          send(res, 200, result);
          return;
        }
        if (pathname === '/kernel/v1/master/register') {
          const result = kernel.registerMaster({ tenant_id: body.tenant_id, owner_person_id: body.owner_person_id });
          if (result.ok === false) {
            send(res, result.status || 400, { error: result.error });
            return;
          }
          send(res, 200, result);
          return;
        }
        if (pathname === '/kernel/v1/master/command') {
          const result = kernel.dispatchCommand({ tenant_id: body.tenant_id, command: body.command, params: body.params });
          if (result.ok === false) {
            send(res, result.status || 400, { error: result.error });
            return;
          }
          send(res, 200, result);
          return;
        }
        if (pathname === '/kernel/v1/master/say') {
          if (!body.text) {
            send(res, 400, { error: 'required: text' });
            return;
          }
          send(res, 200, kernel.say({ tenant_id: body.tenant_id, text: body.text }));
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
  return server;
}

export function start() {
  loadEnvFile(join(ROOT, '.env'));
  const port = Number(process.env.PORT || 3101);
  const host = process.env.HOST || '0.0.0.0';
  const server = createApp();
  server.listen(port, host, () => {
    console.log(`[M01 DEMO] listening on http://${host}:${port} (DEMO ONLY, in-memory, do not expose)`);
  });
  return server;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  start();
}
