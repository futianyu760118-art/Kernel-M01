import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { M01Kernel } from '../src/kernel.js';
import {
  buildLegacyPersonnelCandidates,
  filterLegacyPersonnel,
  importLegacyPersonnel,
  loadEbmsPersonnelSource,
  summarizeLegacyPersonnel
} from '../src/legacy-import.js';

const scriptDir = dirname(fileURLToPath(import.meta.url));
const defaultSource = resolve(scriptDir, '../../../EBMS-aeos-phase1/database/database');

function parseArgs(argv) {
  const result = { source: defaultSource, status: 'active', account: 'all', json: false };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === '--json') {
      result.json = true;
      continue;
    }
    if (!arg.startsWith('--')) continue;
    const equal = arg.indexOf('=');
    const key = equal >= 0 ? arg.slice(2, equal) : arg.slice(2);
    const value = equal >= 0 ? arg.slice(equal + 1) : argv[i + 1];
    if (equal < 0) i += 1;
    if (key === 'source') result.source = resolve(value);
    if (key === 'status') result.status = value;
    if (key === 'account') result.account = value;
    if (key === 'department') result.department = value;
    if (key === 'keyword') result.keyword = value;
    if (key === 'limit') result.limit = Number(value);
  }
  return result;
}

function check(tests, name, actual, expected, details = {}) {
  const pass = actual === expected;
  tests.push({ name, pass, actual, expected, ...details });
  return pass;
}

function compactImportReport(report) {
  return {
    tenant_id: report.tenant_id,
    selected: report.selected,
    applied: report.applied,
    idempotent: report.idempotent,
    stale: report.stale,
    rejected: report.rejected,
    errors: report.results.filter((result) => result.status === 'REJECTED').slice(0, 20)
  };
}

function runActualDataTests(kernel, candidates, selected, importReport) {
  const tests = [];
  check(tests, '所有可识别的 EBMS 人员成功导入 M01', importReport.applied, candidates.length);
  check(tests, 'M01 人员数与导入记录数一致', kernel.persons.size, candidates.length);
  const sample = selected.find((candidate) => ['direct', 'unique_name'].includes(candidate.account.match_type)) || selected[0];
  if (!sample) {
    tests.push({ name: '存在可用于授权验证的筛选人员', pass: false, error: '筛选结果为空' });
    return tests;
  }
  const allow = kernel.authorize({ tenant_id: 'TENANT_A', principal_id: sample.person_id, resource: 'task', action: 'execute' });
  const exportDecision = kernel.authorize({ tenant_id: 'TENANT_A', principal_id: sample.person_id, resource: 'data', action: 'export' });
  const crossTenant = kernel.authorize({ tenant_id: 'TENANT_B', principal_id: sample.person_id, resource: 'task', action: 'execute' });
  check(tests, '实际导入人员普通任务授权', allow.decision, 'ALLOW', { person_id: sample.person_id });
  check(tests, '实际导入人员高风险导出不越权', exportDecision.decision, 'DENY', { person_id: sample.person_id });
  check(tests, '实际导入人员跨租户访问拒绝', crossTenant.decision, 'DENY', { person_id: sample.person_id });

  const revoked = kernel.revoke(sample.person_id, 'EBMS migration authorization test');
  const afterRevoke = kernel.authorize({ tenant_id: 'TENANT_A', principal_id: sample.person_id, resource: 'task', action: 'execute' });
  check(tests, '实际导入人员撤权后拒绝', afterRevoke.decision, 'DENY', { person_id: sample.person_id, revoke_ok: revoked.ok });

  const repeat = importLegacyPersonnel(kernel, candidates);
  check(tests, '重复导入保持幂等', repeat.idempotent, candidates.length, { applied_on_repeat: repeat.applied });
  check(tests, '重复导入不恢复已撤销人员', kernel.persons.get(sample.person_id)?.status, 'revoked');
  return tests;
}

function humanReport(report) {
  console.log(`EBMS 人员导入验证：${report.source_dir}`);
  console.log(`源数据：users=${report.source_counts.users}，personnel=${report.source_counts.personnel}`);
  console.log(`导入：选取=${report.import.selected}，成功=${report.import.applied}，拒绝=${report.import.rejected}，重复幂等=${report.repeat_import.idempotent}`);
  console.log(`筛选：status=${report.filter.status}，account=${report.filter.account}，结果=${report.filter.selected} / 匹配=${report.filter.matched}`);
  console.log(`账号关联：唯一/直连=${report.identity_summary.linked}，歧义=${report.identity_summary.ambiguous}，未关联=${report.identity_summary.unlinked}`);
  for (const test of report.tests) console.log(`${test.pass ? 'PASS' : 'FAIL'}  ${test.name}${test.person_id ? ` (${test.person_id})` : ''}`);
  if (report.samples.length) console.log('筛选样本：', report.samples);
  if (report.tests.some((test) => !test.pass)) process.exitCode = 1;
}

const options = parseArgs(process.argv.slice(2));
try {
  const source = await loadEbmsPersonnelSource(options.source);
  const candidates = buildLegacyPersonnelCandidates(source);
  const importSelection = filterLegacyPersonnel(candidates, { status: 'all' }).selected;
  const kernel = new M01Kernel();
  const importReport = importLegacyPersonnel(kernel, importSelection);
  const filtered = filterLegacyPersonnel(candidates, options);
  const repeatImport = importLegacyPersonnel(kernel, importSelection);
  const identitySummary = {
    linked: candidates.filter((candidate) => ['direct', 'unique_name'].includes(candidate.account.match_type)).length,
    ambiguous: candidates.filter((candidate) => candidate.account.match_type === 'ambiguous').length,
    unlinked: candidates.filter((candidate) => candidate.account.match_type === 'none').length
  };
  const report = {
    source_dir: source.source_dir,
    source_counts: { users: source.users.length, personnel: source.personnel.length, departments: source.departments.length, positions: source.positions.length },
    candidate_summary: summarizeLegacyPersonnel(candidates),
    import: compactImportReport(importReport),
    repeat_import: compactImportReport(repeatImport),
    filter: { status: options.status, account: options.account, department: options.department || null, keyword: options.keyword || null, selected: filtered.selected.length, matched: filtered.matched },
    identity_summary: identitySummary,
    samples: filtered.selected.slice(0, 5).map((candidate) => ({ person_id: candidate.person_id, display_name: candidate.display_name, department_name: candidate.department_name, account_match: candidate.account.match_type, flags: candidate.flags })),
    tests: runActualDataTests(kernel, candidates, filtered.selected, importReport)
  };
  if (options.json) console.log(JSON.stringify(report, null, 2));
  else humanReport(report);
} catch (error) {
  console.error(`[EBMS import] ${error.message}`);
  process.exitCode = 1;
}
