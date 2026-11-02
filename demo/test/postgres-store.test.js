import { test } from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { PostgresStore } from '../src/postgres-store.js';

const databaseUrl = process.env.M01_TEST_DATABASE_URL || 'postgres://m01_test:m01_test@127.0.0.1:55432/m01_test';

function state() {
  return {
    version: 1,
    positions: [['POS_STAFF', { position_id: 'POS_STAFF' }]],
    persons: [['P_OWNER', { person_id: 'P_OWNER', status: 'active' }]],
    agents: [['A_MASTER_KERNEL', { agent_id: 'A_MASTER_KERNEL', status: 'active' }]],
    events: [['EVT_1', { source: 'm07' }]],
    revocations: [],
    approvals: [],
    auditLog: [{ trace_id: 'TRACE_1', kind: 'master.registered' }],
    master: { agent_id: 'A_MASTER_KERNEL', tenant_id: 'TENANT_A', owner_person_id: 'P_OWNER', status: 'active' }
  };
}

test('postgres store migrates, saves and restores a kernel snapshot with audit entries', async (t) => {
  const store = new PostgresStore({ connectionString: databaseUrl, snapshotKey: `test-${randomUUID()}`, required: true });
  t.after(() => store.close());
  await store.initialize();

  const saved = await store.saveState(state(), [{ trace_id: 'TRACE_2', kind: 'master.command' }], 0);
  assert.equal(saved.revision, 1);
  const loaded = await store.loadState();
  assert.equal(loaded.revision, 1);
  assert.equal(loaded.state.master.owner_person_id, 'P_OWNER');
  assert.equal(loaded.state.events[0][0], 'EVT_1');
  assert.equal(loaded.auditEntries.at(-1).trace_id, 'TRACE_2');
});

test('postgres store rejects stale revisions without overwriting newer state', async (t) => {
  const store = new PostgresStore({ connectionString: databaseUrl, snapshotKey: `test-${randomUUID()}`, required: true });
  t.after(() => store.close());
  await store.initialize();
  await store.saveState(state(), [], 0);
  await assert.rejects(() => store.saveState({ ...state(), master: { ...state().master, owner_person_id: 'P_OTHER' } }, [], 0), /revision conflict/i);
  const loaded = await store.loadState();
  assert.equal(loaded.state.master.owner_person_id, 'P_OWNER');
});

test('required postgres store fails closed when the database is unavailable', async () => {
  const store = new PostgresStore({ connectionString: 'postgres://m01_test:m01_test@127.0.0.1:59999/m01_test', required: true, connectionTimeoutMillis: 250 });
  await assert.rejects(() => store.initialize(), /connect|refused|timeout/i);
  await store.close();
});
