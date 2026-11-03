const TENANT_NAMES = new Map([
  ['TENANT_A', '恒剑光电']
]);

const POSITION_NAMES = new Map([
  ['POS_STAFF', '普通员工'],
  ['POS_MANAGER', '经理'],
  ['POS_OWNER', '管理员兼总经理']
]);

const STATUS_NAMES = new Map([
  ['active', '正常'],
  ['pending', '待确认'],
  ['offboarded', '已离职'],
  ['revoked', '已撤销'],
  ['inactive', '未启用'],
  ['enabled', '正常']
]);

export function tenantLabel(tenantId) {
  return TENANT_NAMES.get(tenantId) || tenantId || '未填写单位';
}

export function positionLabel(positionId) {
  return POSITION_NAMES.get(positionId) || positionId || '未分配岗位';
}

export function statusLabel(status) {
  return STATUS_NAMES.get(status) || status || '未知';
}

export function personLabel(person) {
  const name = person?.profile?.display_name || person?.display_name || '未填写姓名';
  const id = person?.person_id || '无编号';
  return `${name}（编号 ${id}）`;
}
