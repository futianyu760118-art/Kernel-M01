import test from 'node:test';
import assert from 'node:assert/strict';
import { M01Kernel } from '../src/kernel.js';

test('办理离职后人员档案中的原系统状态也显示为已停用', () => {
  const kernel = new M01Kernel();
  kernel.applyM07Event({
    event_id: 'PROFILE-HIRE-1',
    tenant_id: 'TENANT_A',
    type: 'person.hired',
    person_id: 'P_PROFILE_1',
    position_id: 'POS_STAFF',
    profile: { source_status: 'active', display_name: '测试人员' },
    seq: 1
  });
  kernel.applyM07Event({
    event_id: 'PROFILE-OFFBOARD-1',
    tenant_id: 'TENANT_A',
    type: 'person.offboarded',
    person_id: 'P_PROFILE_1',
    seq: 2
  });

  assert.equal(kernel.persons.get('P_PROFILE_1').status, 'offboarded');
  assert.equal(kernel.persons.get('P_PROFILE_1').profile.source_status, 'inactive');
});
