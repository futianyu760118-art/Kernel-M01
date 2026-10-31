import { createPublicKey, createVerify } from 'node:crypto';

const MIN_CACHE_TTL_MS = 1000;
const MAX_CACHE_TTL_MS = 60 * 60 * 1000;

export class OidcVerificationError extends Error {
  constructor(message) {
    super(message);
    this.name = 'OidcVerificationError';
  }
}

const fail = (message) => { throw new OidcVerificationError(message); };

function decodeJson(segment, label) {
  try {
    return JSON.parse(Buffer.from(segment, 'base64url').toString('utf8'));
  } catch {
    fail(`malformed ${label}`);
  }
}

function stringClaim(claims, name) {
  if (typeof claims[name] !== 'string' || !claims[name].trim()) fail(`invalid ${name}`);
  return claims[name];
}

function stringArrayClaim(claims, name) {
  const value = claims[name];
  if (!Array.isArray(value) || value.length === 0 || value.some((item) => typeof item !== 'string' || !item.trim())) {
    fail(`invalid ${name}`);
  }
  return [...value];
}

function scopeClaim(claims) {
  if (Array.isArray(claims.scope)) return stringArrayClaim(claims, 'scope');
  if (typeof claims.scope === 'string' && claims.scope.trim()) return claims.scope.trim().split(/\s+/);
  fail('invalid scope');
}

function validAudience(value, expected) {
  return Array.isArray(value) ? value.includes(expected) : value === expected;
}

export function createOidcVerifier(config = {}) {
  const issuer = config.issuer;
  const audience = config.audience;
  const jwksUri = config.jwksUri;
  const fetchImpl = config.fetchImpl || globalThis.fetch;
  if (!issuer || !audience || !jwksUri || typeof fetchImpl !== 'function') {
    throw new TypeError('OIDC verifier requires issuer, audience, jwksUri, and fetchImpl');
  }
  const ttlMs = Math.min(MAX_CACHE_TTL_MS, Math.max(MIN_CACHE_TTL_MS, Number(config.cacheTtlMs || 60 * 1000)));
  const keys = new Map();

  async function getKey(kid) {
    const cached = keys.get(kid);
    if (cached && cached.expiresAt > Date.now()) return cached.key;
    const response = await fetchImpl(jwksUri);
    if (!response?.ok) fail('JWKS fetch failed');
    let document;
    try { document = await response.json(); } catch { fail('malformed JWKS response'); }
    if (!Array.isArray(document?.keys)) fail('malformed JWKS response');
    const jwk = document.keys.find((candidate) => candidate?.kid === kid && candidate.alg === 'RS256' && candidate.kty === 'RSA');
    if (!jwk) fail('unknown kid');
    let key;
    try { key = createPublicKey({ key: jwk, format: 'jwk' }); } catch { fail('invalid JWKS key'); }
    keys.set(kid, { key, expiresAt: Date.now() + ttlMs });
    return key;
  }

  async function verify(compact) {
    if (typeof compact !== 'string' || compact.length > 16384) fail('malformed JWT');
    const parts = compact.split('.');
    if (parts.length !== 3 || parts.some((part) => !part)) fail('malformed JWT');
    const [encodedHeader, encodedPayload, encodedSignature] = parts;
    const header = decodeJson(encodedHeader, 'JWT header');
    const claims = decodeJson(encodedPayload, 'JWT claims');
    if (header.alg !== 'RS256') fail('unsupported algorithm');
    if (typeof header.kid !== 'string' || !header.kid) fail('missing kid');
    if (claims.iss !== issuer) fail('issuer mismatch');
    if (!validAudience(claims.aud, audience)) fail('audience mismatch');
    const now = Math.floor(Date.now() / 1000);
    if (typeof claims.exp !== 'number' || !Number.isFinite(claims.exp) || claims.exp <= now) fail('token expired');
    if (claims.nbf !== undefined && (typeof claims.nbf !== 'number' || !Number.isFinite(claims.nbf) || claims.nbf > now)) fail('token not yet valid');
    const principal_id = stringClaim(claims, 'sub');
    const tenant_id = stringClaim(claims, 'tenant_id');
    const session_id = stringClaim(claims, 'session_id');
    if (!Number.isInteger(claims.authz_version) || claims.authz_version < 1) fail('invalid authz_version');
    const amr = stringArrayClaim(claims, 'amr');
    const scope = scopeClaim(claims);
    const key = await getKey(header.kid);
    const verifier = createVerify('RSA-SHA256');
    verifier.update(`${encodedHeader}.${encodedPayload}`);
    verifier.end();
    let valid = false;
    try { valid = verifier.verify(key, Buffer.from(encodedSignature, 'base64url')); } catch { valid = false; }
    if (!valid) fail('invalid signature');
    return { principal_id, tenant_id, session_id, authz_version: claims.authz_version, amr, scope };
  }

  return { verify };
}
