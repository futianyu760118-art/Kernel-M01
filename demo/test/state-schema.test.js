import { test } from 'node:test';
import assert from 'node:assert/strict';
import { M01Kernel } from '../src/kernel.js';
import { KernelStateVersion, parseKernelState } from '../src/state-schema.js';

test('kernel state export and restore preserve control-plane state', () => {
  const source = new M01Kernel();
  source.registerMaster({ tenant_id: 'TENANT_A', owner_person_id: 'P_OWNER' });
  source.applyM07Event({ event_id: 'STATE_PERSON_1', tenant_id: 'TENANT_A', type: 'person.hired', person_id: 'P_STATE_1', position_id: 'POS_STAFF', seq: 1 });
  source.applyM13Event({ event_id: 'STATE_AGENT_1', tenant_id: 'TENANT_A', type: 'agent.registered', agent_id: 'A_STATE_1', owner_person_id: 'P_STATE_1', scopes: ['task:execute'] });
  source.revoke('P_STATE_1', 'state round trip');

  const state = parseKernelState(source.exportState());
  assert.equal(state.version, KernelStateVersion);
  const restored = new M01Kernel();
  restored.restoreState(state);

  assert.equal(restored.persons.get('P_STATE_1').status, 'revoked');
  assert.equal(restored.agents.get('A_STATE_1').status, 'revoked');
  assert.equal(restored.master.owner_person_id, 'P_OWNER');
  assert.equal(restored.events.has('STATE_PERSON_1'), true);
  assert.ok(restored.auditLog.length >= source.auditLog.length);
});

test('kernel state parser rejects unsupported versions and malformed collections', () => {
  assert.throws(() => parseKernelState({ version: 99 }), /unsupported kernel state version/i);
  assert.throws(() => parseKernelState({ version: KernelStateVersion, positions: 'not-an-array' }), /positions/i);
});
