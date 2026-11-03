import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createApp } from '../src/server.js';
import { M01Kernel } from '../src/kernel.js';

const ADMIN_TOKEN = 'persistence-server-token';

function fakePersistence({ state = null, failSave = false } = {}) {
  const calls = { initialize: 0, save: 0, close: 0 };
  let revision = state ? 1 : 0;
  return {
    calls,
    async initialize() { calls.initialize += 1; },
    async loadState() { return { state, revision, auditEntries: [] }; },
    async saveState(nextState, auditEntries, expectedRevision) {
      calls.save += 1;
      if (failSave) throw new Error('database write unavailable');
      assert.equal(expectedRevision, revision);
      revision += 1;
      state = nextState;
      return { revision };
    },
    async close() { calls.close += 1; }
  };
}

async function startServer(persistence) {
  const kernel = new M01Kernel();
  const server = createApp(kernel, { adminToken: ADMIN_TOKEN, m07Secret: 's1', m13Secret: 's2', persistence });
  await server.ready;
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  return { server, kernel, base: `http://127.0.0.1:${server.address().port}` };
}

async function postJson(base, path, body) {
  const res = await fetch(base + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${ADMIN_TOKEN}` },
    body: JSON.stringify(body)
  });
  return { status: res.status, data: await res.json() };
}

test('server initializes persistence, exposes readiness and persists master registration', async (t) => {
  const persistence = fakePersistence();
  const { server, base } = await startServer(persistence);
  t.after(async () => { await persistence.close(); server.close(); });

  assert.equal(persistence.calls.initialize, 1);
  const ready = await fetch(base + '/readyz');
  assert.equal(ready.status, 200);

  const registration = await postJson(base, '/kernel/v1/master/register', { tenant_id: 'TENANT_A', owner_person_id: 'P_OWNER' });
  assert.equal(registration.status, 200);
  assert.equal(persistence.calls.save, 1);
});

test('server rolls back an in-memory mutation when persistence fails', async (t) => {
  const persistence = fakePersistence({ failSave: true });
  const { server, kernel, base } = await startServer(persistence);
  t.after(async () => { await persistence.close(); server.close(); });

  const registration = await postJson(base, '/kernel/v1/master/register', { tenant_id: 'TENANT_A', owner_person_id: 'P_OWNER' });
  assert.equal(registration.status, 503);
  assert.equal(kernel.master, null);
});
