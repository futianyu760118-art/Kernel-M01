import { test } from 'node:test';
import assert from 'node:assert/strict';
import { personLabel, positionLabel, statusLabel, tenantLabel } from '../src/human-labels.js';

test('人员、岗位、状态和单位统一显示通俗中文', () => {
  assert.equal(personLabel({ person_id: 'P_OWNER', profile: { display_name: '朱剑军' } }), '朱剑军（编号 P_OWNER）');
  assert.equal(personLabel({ person_id: 'P_1001' }), '未填写姓名（编号 P_1001）');
  assert.equal(positionLabel('POS_OWNER'), '管理员兼总经理');
  assert.equal(positionLabel('POS_MANAGER'), '经理');
  assert.equal(statusLabel('active'), '正常');
  assert.equal(statusLabel('offboarded'), '已离职');
  assert.equal(tenantLabel('TENANT_A'), '恒剑光电');
});
