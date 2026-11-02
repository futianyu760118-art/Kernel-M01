import { test } from 'node:test';
import assert from 'node:assert/strict';
import { M01Kernel } from '../src/kernel.js';

test('恒剑光电首个管理员朱剑军可以重复初始化为管理员兼总经理', () => {
  const kernel = new M01Kernel({ allowImplicitOwner: false });

  const first = kernel.ensurePrimaryOwner({
    tenant_id: 'TENANT_A',
    organization_name: '恒剑光电',
    person_id: 'P_OWNER',
    display_name: '朱剑军'
  });

  assert.equal(first.ok, true);
  assert.equal(first.changed, true);
  assert.equal(first.person.profile.display_name, '朱剑军');
  assert.equal(first.person.profile.organization_name, '恒剑光电');
  assert.deepEqual(first.person.profile.role_names, ['管理员', '总经理']);
  assert.equal(first.person.position_id, 'POS_OWNER');
  assert.equal(kernel.authorize({ tenant_id: 'TENANT_A', principal_id: 'P_OWNER', resource: 'data', action: 'export' }).decision, 'REQUIRE_APPROVAL');

  const second = kernel.ensurePrimaryOwner({
    tenant_id: 'TENANT_A',
    organization_name: '恒剑光电',
    person_id: 'P_OWNER',
    display_name: '朱剑军'
  });

  assert.equal(second.ok, true);
  assert.equal(second.changed, false);
  assert.equal(second.idempotent, true);
});

test('恢复旧快照时会把管理员岗位升级为管理员兼总经理权限', () => {
  const legacy = new M01Kernel();
  const state = legacy.exportState();
  state.positions = state.positions.map(([id, position]) => id === 'POS_OWNER'
    ? [id, { ...position, name: 'Owner', capabilities: [{ resource: 'report', action: 'read' }] }]
    : [id, position]);
  state.persons = [['P_OWNER', { person_id: 'P_OWNER', tenant_id: 'TENANT_A', status: 'active', position_id: 'POS_OWNER', seq: 0, authz_version: 1 }]];

  const restored = new M01Kernel({ allowImplicitOwner: false });
  restored.restoreState(state);
  const result = restored.ensurePrimaryOwner({
    tenant_id: 'TENANT_A',
    organization_name: '恒剑光电',
    person_id: 'P_OWNER',
    display_name: '朱剑军'
  });

  assert.equal(result.ok, true);
  assert.equal(restored.positions.get('POS_OWNER').name, '管理员兼总经理');
  assert.ok(restored.positions.get('POS_OWNER').capabilities.some((item) => item.resource === 'task' && item.action === 'execute'));
  assert.equal(restored.authorize({ tenant_id: 'TENANT_A', principal_id: 'P_OWNER', resource: 'task', action: 'execute' }).decision, 'ALLOW');
});
