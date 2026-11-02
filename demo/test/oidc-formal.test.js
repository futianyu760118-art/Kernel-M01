import { test } from 'node:test';
import assert from 'node:assert/strict';
import { generateKeyPairSync, createSign } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { createApp } from '../src/server.js';
import { M01Kernel } from '../src/kernel.js';

const ISSUER = 'https://issuer.formal.example/realms/aeos';
const AUDIENCE = 'aeos-m01';
const JWKS_URI = `${ISSUER}/protocol/openid-connect/certs`;
const { privateKey, publicKey } = generateKeyPairSync('rsa', { modulusLength: 2048 });
const jwk = { ...publicKey.export({ format: 'jwk' }), kid: 'formal-key-1', alg: 'RS256', use: 'sig' };
const encode = (value) => Buffer.from(JSON.stringify(value)).toString('base64url');

function token(overrides = {}) {
  const header = { alg: 'RS256', typ: 'JWT', kid: 'formal-key-1' };
  const payload = {
    iss: ISSUER,
    aud: AUDIENCE,
    sub: 'keycloak-sub-001',
    tenant_id: 'TENANT_A',
    session_id: 'session-formal-1',
    authz_version: 1,
    amr: ['pwd', 'otp'],
    scope: ['m01:policy:read'],
    exp: Math.floor(Date.now() / 1000) + 300,
    ...overrides
  };
  const input = `${encode(header)}.${encode(payload)}`;
  const signer = createSign('RSA-SHA256');
  signer.update(input);
  signer.end();
  return `${input}.${signer.sign(privateKey).toString('base64url')}`;
}

const oidc = {
  issuer: ISSUER,
  audience: AUDIENCE,
  jwksUri: JWKS_URI,
  authorizationEndpoint: `${ISSUER}/protocol/openid-connect/auth`,
  tokenEndpoint: `${ISSUER}/protocol/openid-connect/token`,
  clientId: 'aeos-m01-console',
  redirectUri: 'http://127.0.0.1:3101/',
  scopes: ['openid', 'profile', 'email', 'm01:policy:read'],
  fetchImpl: async () => ({ ok: true, async json() { return { keys: [jwk] }; } })
};

async function startOidcServer(kernel = new M01Kernel()) {
  const server = createApp(kernel, { mode: 'oidc', oidc });
  await server.ready;
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  return { server, base: `http://127.0.0.1:${server.address().port}` };
}

test('formal OIDC exposes only public browser configuration and no admin secret', async (t) => {
  const { server, base } = await startOidcServer();
  t.after(() => server.close());

  const response = await fetch(base + '/auth/oidc/config');
  assert.equal(response.status, 200);
  const config = await response.json();
  assert.deepEqual(config, {
    mode: 'oidc',
    issuer: ISSUER,
    authorization_endpoint: oidc.authorizationEndpoint,
    token_endpoint: oidc.tokenEndpoint,
    client_id: oidc.clientId,
    redirect_uri: oidc.redirectUri,
    scope: oidc.scopes.join(' '),
    pkce: 'S256'
  });
  assert.equal('client_secret' in config, false);
  assert.equal('admin_token' in config, false);
});

test('formal OIDC resolves signed sub through M01 external identity mapping', async (t) => {
  const kernel = new M01Kernel({ allowImplicitOwner: false });
  kernel.applyM07Event({ event_id: 'FORMAL_PERSON_1', tenant_id: 'TENANT_A', type: 'person.hired', person_id: 'P_MAPPED', position_id: 'POS_STAFF', seq: 1 });
  kernel.mapExternalIdentity({ issuer: ISSUER, subject: 'keycloak-sub-001', principal_id: 'P_MAPPED', tenant_id: 'TENANT_A' });
  const { server, base } = await startOidcServer(kernel);
  t.after(() => server.close());

  const response = await fetch(base + '/kernel/v1/authorize', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token()}` },
    body: JSON.stringify({ tenant_id: 'TENANT_A', principal_id: 'P_NOT_TRUSTED', resource: 'task', action: 'execute' })
  });
  assert.equal(response.status, 200);
  const decision = await response.json();
  assert.equal(decision.principal_id, 'P_MAPPED');
  assert.equal(decision.decision, 'ALLOW');
});

test('formal console uses Authorization Code + PKCE and never stores a demo bearer token', async () => {
  const html = await readFile(new URL('../public/index.html', import.meta.url), 'utf8');
  assert.match(html, /code_challenge_method=S256|crypto\.subtle\.digest/);
  assert.match(html, /sessionStorage/);
  assert.doesNotMatch(html, /value="local-demo-admin"/);
  assert.doesNotMatch(html, /localStorage\.setItem\(['"]m01-token/);
});
