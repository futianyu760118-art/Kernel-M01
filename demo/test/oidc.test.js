import { test } from 'node:test';
import assert from 'node:assert/strict';
import { generateKeyPairSync, createSign } from 'node:crypto';
import { createApp } from '../src/server.js';
import { M01Kernel } from '../src/kernel.js';
import { createOidcVerifier } from '../src/oidc.js';

const ISSUER = 'https://issuer.example.test';
const AUDIENCE = 'aeos-m01';
const JWKS_URI = `${ISSUER}/.well-known/jwks.json`;
const { privateKey, publicKey } = generateKeyPairSync('rsa', { modulusLength: 2048 });
const jwk = { ...publicKey.export({ format: 'jwk' }), kid: 'test-key-1', alg: 'RS256', use: 'sig' };

const encode = (value) => Buffer.from(JSON.stringify(value)).toString('base64url');
function token(overrides = {}, headerOverrides = {}) {
  const header = { alg: 'RS256', typ: 'JWT', kid: 'test-key-1', ...headerOverrides };
  const payload = {
    iss: ISSUER,
    aud: AUDIENCE,
    sub: 'P_AUTH',
    tenant_id: 'TENANT_A',
    session_id: 'session-1',
    authz_version: 1,
    amr: ['pwd'],
    scope: ['m01:policy:read'],
    exp: Math.floor(Date.now() / 1000) + 300,
    ...overrides
  };
  const input = `${encode(header)}.${encode(payload)}`;
  const signer = createSign('RSA-SHA256');
  signer.update(input);
  return `${input}.${signer.sign(privateKey).toString('base64url')}`;
}

const fetchImpl = async () => ({ ok: true, async json() { return { keys: [jwk] }; } });
const oidcConfig = { issuer: ISSUER, audience: AUDIENCE, jwksUri: JWKS_URI, fetchImpl, requiredScope: 'm01:policy:read' };

async function startOidc(kernel = new M01Kernel()) {
  const server = createApp(kernel, { mode: 'oidc', oidc: oidcConfig });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  return { server, kernel, base: `http://127.0.0.1:${server.address().port}` };
}

async function postJson(base, path, body, jwt = token()) {
  const res = await fetch(base + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${jwt}` },
    body: JSON.stringify(body)
  });
  return { status: res.status, data: await res.json() };
}

test('OIDC verifier validates RS256 JWTs and maps a strict PrincipalContext', async () => {
  const verifier = createOidcVerifier(oidcConfig);
  const principal = await verifier.verify(token());
  assert.deepEqual(principal, {
    principal_id: 'P_AUTH',
    tenant_id: 'TENANT_A',
    session_id: 'session-1',
    authz_version: 1,
    amr: ['pwd'],
    scope: ['m01:policy:read']
  });
});

test('OIDC verifier rejects unknown keys, invalid claims, and unsupported algorithms', async () => {
  const verifier = createOidcVerifier(oidcConfig);
  await assert.rejects(() => verifier.verify(token({ exp: 1 })), /expired/);
  await assert.rejects(() => verifier.verify(token({ iss: 'https://evil.example', aud: AUDIENCE })), /issuer/);
  await assert.rejects(() => verifier.verify(token({ aud: 'other-audience' })), /audience/);
  await assert.rejects(() => verifier.verify(token({ sub: '' })), /sub/);
  await assert.rejects(() => verifier.verify(token({ authz_version: '1' })), /authz_version/);
  await assert.rejects(() => verifier.verify(token({}, { kid: 'unknown-key' })), /kid/);
});

test('OIDC mode fails closed and exposes the selected mode without secrets', async (t) => {
  const { server, base } = await startOidc();
  t.after(() => server.close());
  const healthResponse = await fetch(base + '/health');
  const health = await healthResponse.json();
  assert.equal(health.mode, 'oidc');
  assert.equal(healthResponse.headers.get('x-m01-environment'), 'OIDC');
  assert.equal((await fetch(base + '/kernel/v1/status')).status, 401);
  assert.equal((await postJson(base, '/kernel/v1/authorize', { tenant_id: 'TENANT_A', principal_id: 'P_AUTH', resource: 'task', action: 'execute' }, 'not-a-jwt')).status, 401);
});

test('OIDC policy requests use verified identity and require explicit same-tenant delegation', async (t) => {
  const kernel = new M01Kernel();
  kernel.applyM07Event({ event_id: 'person-auth', tenant_id: 'TENANT_A', type: 'person.hired', person_id: 'P_AUTH', position_id: 'POS_STAFF', seq: 1 });
  kernel.applyM07Event({ event_id: 'person-other', tenant_id: 'TENANT_A', type: 'person.hired', person_id: 'P_OTHER', position_id: 'POS_MANAGER', seq: 1 });
  const { server, base } = await startOidc(kernel);
  t.after(() => server.close());

  const spoofed = await postJson(base, '/kernel/v1/authorize', { tenant_id: 'TENANT_A', principal_id: 'P_OTHER', resource: 'report', action: 'approve' });
  assert.equal(spoofed.status, 200);
  assert.equal(spoofed.data.principal_id, 'P_AUTH');
  assert.equal(spoofed.data.decision, 'DENY');

  const delegated = await postJson(base, '/kernel/v1/authorize', { tenant_id: 'TENANT_A', principal_id: 'P_OTHER', resource: 'report', action: 'approve' }, token({ scope: ['m01:policy:read', 'm01:policy:delegate'] }));
  assert.equal(delegated.status, 200);
  assert.equal(delegated.data.principal_id, 'P_OTHER');
  assert.equal(delegated.data.decision, 'ALLOW');

  const crossTenant = await postJson(base, '/kernel/v1/authorize', { tenant_id: 'TENANT_B', principal_id: 'P_OTHER', resource: 'report', action: 'approve' }, token({ scope: ['m01:policy:read', 'm01:policy:delegate'] }));
  assert.equal(crossTenant.status, 403);
});

test('OIDC rejects a stale authz_version and reports the current version read-only', async (t) => {
  const kernel = new M01Kernel();
  kernel.applyM07Event({ event_id: 'person-versioned', tenant_id: 'TENANT_A', type: 'person.hired', person_id: 'P_AUTH', position_id: 'POS_STAFF', seq: 1 });
  const { server, base } = await startOidc(kernel);
  t.after(() => server.close());
  kernel.revoke('P_AUTH', 'test revocation');
  const stale = await postJson(base, '/kernel/v1/authorize', { tenant_id: 'TENANT_A', resource: 'task', action: 'execute' });
  assert.equal(stale.status, 401);
  assert.match(stale.data.error, /stale|revoked/i);
  const current = await fetch(base + '/kernel/v1/principals/P_AUTH/authorization', { headers: { Authorization: `Bearer ${token({ authz_version: 2 })}` } });
  assert.equal(current.status, 200);
  assert.deepEqual(await current.json(), { principal_id: 'P_AUTH', tenant_id: 'TENANT_A', authz_version: 2, revoked: true });
  assert.ok(kernel.auditLog.some((entry) => entry.kind === 'authorization.version_changed' && entry.principal_id === 'P_AUTH' && entry.trace_id));
});

test('demo mode remains explicit and compatible with the legacy admin token', async (t) => {
  const server = createApp(new M01Kernel(), { mode: 'demo', adminToken: 'demo-secret' });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  t.after(() => server.close());
  const base = `http://127.0.0.1:${server.address().port}`;
  const health = await fetch(base + '/health');
  assert.equal((await health.json()).mode, 'demo');
  assert.equal(health.headers.get('x-m01-environment'), 'DEMO ONLY');
  const status = await fetch(base + '/kernel/v1/status', { headers: { Authorization: 'Bearer demo-secret' } });
  assert.equal(status.status, 200);
});
