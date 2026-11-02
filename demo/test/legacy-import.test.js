import { test } from 'node:test';
import assert from 'node:assert/strict';
import { M01Kernel } from '../src/kernel.js';
import {
  buildLegacyPersonnelCandidates,
  filterLegacyPersonnel,
  importLegacyPersonnel,
  summarizeLegacyPersonnel
} from '../src/legacy-import.js';

test('legacy personnel candidates classify account conflicts without importing passwords', () => {
  const candidates = buildLegacyPersonnelCandidates({
    users: [
      { id: 1, username: 'zhang', name: '张三', role: 'sales', password: 'secret' },
      { id: 2, username: 'zhang2', name: '张三', role: 'viewer', password: 'secret2' }
    ],
    personnel: [
      { id: 10, emp_code: 'E10', name: '张三', status: 'active', department_name: '销售部', position_name: '销售' },
      { id: 11, emp_code: 'E11', name: '李四', status: '离职', department_name: '研发中心', position_name: '' }
    ]
  });

  assert.equal(candidates[0].account.match_type, 'ambiguous');
  assert.ok(candidates[0].flags.includes('AMBIGUOUS_LOGIN_ACCOUNT'));
  assert.equal(candidates[1].source_status, 'inactive');
  assert.ok(candidates[1].flags.includes('NOT_ACTIVE'));
  assert.equal(candidates[1].profile.account.match_type, 'none');
  assert.equal(Object.hasOwn(candidates[0].profile, 'password'), false);
  assert.deepEqual(summarizeLegacyPersonnel(candidates).by_status, { active: 1, inactive: 1 });
});

test('legacy personnel import is idempotent and supports post-import filtering', () => {
  const candidates = buildLegacyPersonnelCandidates({
    users: [{ id: 1, username: 'li', name: '李四', role: 'sales' }],
    personnel: [
      { id: 10, emp_code: 'E10', name: '李四', status: 'active', department_name: '销售部', position_name: '销售', updated_at: '2026-07-01 10:00:00' },
      { id: 11, emp_code: 'E11', name: '王五', status: 'active', department_name: '研发中心', position_name: '经理', updated_at: '2026-07-01 10:00:00' }
    ]
  });
  const kernel = new M01Kernel();
  const first = importLegacyPersonnel(kernel, candidates);
  assert.equal(first.applied, 2);
  assert.equal(kernel.persons.get('P_EBMS_10').profile.display_name, '李四');
  assert.equal(kernel.persons.get('P_EBMS_11').position_id, 'POS_MANAGER');

  const linked = filterLegacyPersonnel(candidates, { account: 'linked' });
  assert.equal(linked.matched, 1);
  assert.equal(linked.selected[0].person_id, 'P_EBMS_10');
  const department = filterLegacyPersonnel(candidates, { department: '研发中心' });
  assert.equal(department.selected[0].person_id, 'P_EBMS_11');

  const second = importLegacyPersonnel(kernel, candidates);
  assert.equal(second.idempotent, 2);
  assert.equal(second.applied, 0);
  assert.equal(kernel.authorize({ tenant_id: 'TENANT_A', principal_id: 'P_EBMS_10', resource: 'task', action: 'execute' }).decision, 'ALLOW');
});
