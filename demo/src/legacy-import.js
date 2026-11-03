import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';

const ACTIVE_STATUSES = new Set(['active', 'enabled', '在职', '正常', '启用']);
const INACTIVE_STATUSES = new Set(['inactive', 'disabled', 'offboarded', '离职', '停用', '禁用']);

function recordsOf(value) {
  if (Array.isArray(value)) return value;
  if (Array.isArray(value?.records)) return value.records;
  if (Array.isArray(value?.data?.records)) return value.data.records;
  return [];
}

function text(value) {
  return value === undefined || value === null ? '' : String(value).trim();
}

function normalized(value) {
  return text(value).toLocaleLowerCase();
}

function sourceStatus(value) {
  const raw = normalized(value);
  if (ACTIVE_STATUSES.has(raw)) return 'active';
  if (INACTIVE_STATUSES.has(raw)) return 'inactive';
  return raw || 'unknown';
}

function stableSourceId(row, index) {
  const value = text(row?.id) || text(row?.emp_code) || `row-${index + 1}`;
  return value;
}

function eventRevision(row) {
  const parsed = Date.parse(text(row?.updated_at) || text(row?.created_at));
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 1;
}

function targetPositionId(positionName) {
  const value = text(positionName);
  if (/(经理|主管|主任|总监|部长|厂长|负责人)/.test(value)) return 'POS_MANAGER';
  return 'POS_STAFF';
}

async function readRecords(sourceDir, name) {
  try {
    const parsed = JSON.parse(await readFile(join(sourceDir, `${name}.json`), 'utf8'));
    return recordsOf(parsed);
  } catch (error) {
    if (error?.code === 'ENOENT') return [];
    throw new Error(`无法读取 EBMS 表 ${name}: ${error.message}`);
  }
}

function readRecordsSync(sourceDir, name) {
  try {
    return recordsOf(JSON.parse(readFileSync(join(sourceDir, `${name}.json`), 'utf8')));
  } catch (error) {
    if (error?.code === 'ENOENT') return [];
    throw new Error(`无法读取 EBMS 表 ${name}: ${error.message}`);
  }
}

function loadSourceFromReaders(sourceDir, reader) {
  const users = reader(sourceDir, 'users');
  const orgPersonnel = reader(sourceDir, 'org_personnel');
  const personnel = reader(sourceDir, 'personnel');
  const departments = reader(sourceDir, 'org_departments');
  const positions = reader(sourceDir, 'org_positions');
  const people = orgPersonnel.length ? orgPersonnel : personnel;
  if (!people.length) throw new Error(`EBMS 人员表为空：${sourceDir}`);
  return { users, personnel: people, departments, positions, source_dir: sourceDir };
}

export async function loadEbmsPersonnelSource(sourceDir) {
  const [users, orgPersonnel, personnel, departments, positions] = await Promise.all([
    readRecords(sourceDir, 'users'),
    readRecords(sourceDir, 'org_personnel'),
    readRecords(sourceDir, 'personnel'),
    readRecords(sourceDir, 'org_departments'),
    readRecords(sourceDir, 'org_positions')
  ]);
  return loadSourceFromReaders(sourceDir, (directory, table) => {
    if (table === 'users') return users;
    if (table === 'org_personnel') return orgPersonnel;
    if (table === 'personnel') return personnel;
    if (table === 'org_departments') return departments;
    return positions;
  });
}

export function loadEbmsPersonnelSourceSync(sourceDir) {
  return loadSourceFromReaders(sourceDir, readRecordsSync);
}

function userMatchesFor(row, users) {
  const linkedId = text(row?.linked_user_id);
  const exact = linkedId
    ? users.filter((user) => text(user.id) === linkedId)
    : users.filter((user) => normalized(user.name) && normalized(user.name) === normalized(row.name));
  const matchType = linkedId ? 'linked_user_id' : 'name';
  if (exact.length === 1) {
    const user = exact[0];
    return {
      match_type: linkedId ? 'direct' : 'unique_name',
      user_id: text(user.id),
      username: text(user.username),
      role: text(user.role)
    };
  }
  if (exact.length > 1) return { match_type: 'ambiguous', candidate_count: exact.length };
  return { match_type: 'none' };
}

export function buildLegacyPersonnelCandidates(source) {
  const users = Array.isArray(source?.users) ? source.users : [];
  const byEmployeeCode = new Map();
  for (const row of source.personnel || []) {
    const code = text(row.emp_code);
    if (code) byEmployeeCode.set(code, (byEmployeeCode.get(code) || 0) + 1);
  }

  return (source.personnel || []).map((row, index) => {
    const sourceId = stableSourceId(row, index);
    const status = sourceStatus(row.status);
    const user = userMatchesFor(row, users);
    const employeeCode = text(row.emp_code);
    const positionName = text(row.position_name || row.position);
    const flags = [];
    if (!text(row.name)) flags.push('MISSING_NAME');
    if (user.match_type === 'ambiguous') flags.push('AMBIGUOUS_LOGIN_ACCOUNT');
    if (user.match_type === 'none') flags.push('NO_LOGIN_ACCOUNT');
    if (!positionName) flags.push('POSITION_NOT_MAPPED');
    if (employeeCode && byEmployeeCode.get(employeeCode) > 1) flags.push('DUPLICATE_EMPLOYEE_CODE');
    if (status !== 'active') flags.push('NOT_ACTIVE');

    return {
      person_id: `P_EBMS_${sourceId}`,
      source_system: 'EBMS',
      source_table: 'org_personnel',
      source_id: sourceId,
      source_revision: eventRevision(row),
      source_status: status,
      display_name: text(row.name),
      employee_code: employeeCode,
      department_id: text(row.department_id),
      department_name: text(row.department_name || row.org_name),
      source_position_name: positionName,
      target_position_id: targetPositionId(positionName),
      account: user,
      flags,
      identity_review_required: user.match_type === 'ambiguous',
      profile: {
        source_system: 'EBMS',
        source_table: 'org_personnel',
        source_id: sourceId,
        employee_code: employeeCode || null,
        display_name: text(row.name),
        department_id: text(row.department_id) || null,
        department_name: text(row.department_name || row.org_name) || null,
        source_position_name: positionName || null,
        source_status: status,
        account: user
      }
    };
  });
}

export function filterLegacyPersonnel(candidates, options = {}) {
  const status = options.status || 'active';
  const department = normalized(options.department);
  const keyword = normalized(options.keyword);
  const account = options.account || 'all';
  const limit = Number.isInteger(options.limit) && options.limit > 0 ? options.limit : null;
  const selected = (candidates || []).filter((candidate) => {
    if (status !== 'all' && candidate.source_status !== status) return false;
    if (department && normalized(candidate.department_name) !== department) return false;
    if (keyword && ![candidate.display_name, candidate.employee_code, candidate.department_name].some((value) => normalized(value).includes(keyword))) return false;
    if (account === 'linked' && !['direct', 'unique_name'].includes(candidate.account.match_type)) return false;
    if (account === 'ambiguous' && candidate.account.match_type !== 'ambiguous') return false;
    if (account === 'unlinked' && candidate.account.match_type !== 'none') return false;
    if (options.excludeAmbiguous && candidate.identity_review_required) return false;
    return true;
  });
  return { selected: limit ? selected.slice(0, limit) : selected, matched: selected.length };
}

export function summarizeLegacyPersonnel(candidates) {
  const summary = {
    total: candidates.length,
    by_status: {},
    by_account_match: {},
    identity_review: 0,
    no_login_account: 0,
    position_not_mapped: 0,
    duplicate_employee_code: 0
  };
  for (const candidate of candidates) {
    summary.by_status[candidate.source_status] = (summary.by_status[candidate.source_status] || 0) + 1;
    summary.by_account_match[candidate.account.match_type] = (summary.by_account_match[candidate.account.match_type] || 0) + 1;
    if (candidate.identity_review_required) summary.identity_review += 1;
    if (candidate.flags.includes('NO_LOGIN_ACCOUNT')) summary.no_login_account += 1;
    if (candidate.flags.includes('POSITION_NOT_MAPPED')) summary.position_not_mapped += 1;
    if (candidate.flags.includes('DUPLICATE_EMPLOYEE_CODE')) summary.duplicate_employee_code += 1;
  }
  return summary;
}

export function importLegacyPersonnel(kernel, candidates, options = {}) {
  const tenantId = options.tenant_id || 'TENANT_A';
  const results = [];
  for (const candidate of candidates || []) {
    if (!candidate.person_id || !candidate.source_id) {
      results.push({ person_id: candidate.person_id || null, status: 'REJECTED', error: 'missing stable source identity' });
      continue;
    }
    if (!['active', 'inactive'].includes(candidate.source_status)) {
      results.push({ person_id: candidate.person_id, status: 'REJECTED', error: `unsupported source status: ${candidate.source_status}` });
      continue;
    }
    const fingerprint = createHash('sha256')
      .update(JSON.stringify({ person_id: candidate.person_id, source_revision: candidate.source_revision, source_status: candidate.source_status, target_position_id: candidate.target_position_id }))
      .digest('hex')
      .slice(0, 16);
    const event = {
      event_id: `EBMS_PERSON_IMPORT_V1_${candidate.source_id}_${fingerprint}`,
      tenant_id: tenantId,
      type: candidate.source_status === 'active' ? 'person.hired' : 'person.offboarded',
      person_id: candidate.person_id,
      seq: candidate.source_revision,
      profile: candidate.profile
    };
    if (event.type === 'person.hired') event.position_id = candidate.target_position_id;
    const result = kernel.applyM07Event(event);
    results.push({ person_id: candidate.person_id, source_id: candidate.source_id, status: result.ok ? (result.idempotent ? 'IDEMPOTENT' : result.stale ? 'STALE' : 'APPLIED') : 'REJECTED', error: result.error || null });
  }
  return {
    tenant_id: tenantId,
    selected: (candidates || []).length,
    applied: results.filter((result) => result.status === 'APPLIED').length,
    idempotent: results.filter((result) => result.status === 'IDEMPOTENT').length,
    stale: results.filter((result) => result.status === 'STALE').length,
    rejected: results.filter((result) => result.status === 'REJECTED').length,
    results
  };
}
