import { randomUUID } from 'node:crypto';

export const HIGH_RISK_ACTIONS = new Set(['data.export', 'payment.execute', 'device.control']);

export const MASTER_AGENT_ID = 'A_MASTER_KERNEL';
export const MASTER_COMMANDS = new Set([
  'person.hire',
  'person.transfer',
  'person.offboard',
  'agent.register',
  'agent.revoke',
  'principal.revoke',
  'capabilities.query',
  'authorize.check',
  'system.status',
  'audit.tail'
]);
export const HIGH_RISK_COMMANDS = new Set(['person.offboard', 'agent.revoke', 'principal.revoke']);

const M07_EVENT_TYPES = new Set(['person.hired', 'person.transferred', 'person.offboarded']);
const M13_EVENT_TYPES = new Set(['agent.registered', 'agent.revoked']);

export class M01Kernel {
  constructor() {
    this.positions = new Map();
    this.persons = new Map();
    this.agents = new Map();
    this.events = new Map();
    this.revocations = new Map();
    this.auditLog = [];
    this.master = null;
    this._seedPositions();
  }

  _seedPositions() {
    this.positions.set('POS_STAFF', {
      position_id: 'POS_STAFF',
      tenant_id: 'TENANT_A',
      name: 'Staff',
      capabilities: [
        { resource: 'task', action: 'execute' },
        { resource: 'report', action: 'read' }
      ]
    });
    this.positions.set('POS_MANAGER', {
      position_id: 'POS_MANAGER',
      tenant_id: 'TENANT_A',
      name: 'Manager',
      capabilities: [
        { resource: 'task', action: 'execute' },
        { resource: 'report', action: 'read' },
        { resource: 'report', action: 'approve' },
        { resource: 'data', action: 'export' }
      ]
    });
    this.positions.set('POS_OWNER', {
      position_id: 'POS_OWNER',
      tenant_id: 'TENANT_A',
      name: 'Owner',
      capabilities: [{ resource: 'report', action: 'read' }]
    });
  }

  registerMaster({ tenant_id, owner_person_id }) {
    if (!tenant_id || !owner_person_id) {
      return { ok: false, error: 'required: tenant_id, owner_person_id', status: 400 };
    }
    if (!this.positions.has('POS_OWNER')) {
      return { ok: false, error: 'POS_OWNER not seeded', status: 500 };
    }
    let owner = this.persons.get(owner_person_id);
    if (!owner) {
      owner = { person_id: owner_person_id, tenant_id, status: 'active', position_id: 'POS_OWNER', seq: 0 };
      this.persons.set(owner_person_id, owner);
    }
    if (owner.tenant_id !== tenant_id) {
      return { ok: false, error: 'owner tenant mismatch', status: 403 };
    }
    if (owner.status !== 'active') {
      return { ok: false, error: 'owner person is not active', status: 403 };
    }
    this.master = {
      agent_id: MASTER_AGENT_ID,
      tenant_id,
      owner_person_id,
      scopes: [...MASTER_COMMANDS],
      status: 'active',
      registered_at: new Date().toISOString()
    };
    this.agents.set(MASTER_AGENT_ID, this.master);
    this._audit({ kind: 'master.registered', tenant_id, master_agent_id: MASTER_AGENT_ID, owner_person_id, scopes: this.master.scopes });
    return { ok: true, master: { agent_id: MASTER_AGENT_ID, tenant_id, owner_person_id, status: 'active', commands: this.master.scopes } };
  }

  dispatchCommand({ tenant_id, command, params }) {
    const trace_id = randomUUID();
    const finish = (result, reason, output) => {
      this._audit({ kind: 'master.command', trace_id, tenant_id, command, params: params || {}, decision: result, reason });
      return { trace_id, command, decision: result, reason, output: output || null };
    };
    if (!command || !MASTER_COMMANDS.has(command)) {
      return { ok: false, status: 400, error: `unknown command, allowed: ${[...MASTER_COMMANDS].join(', ')}` };
    }
    const m = this.master;
    if (!m) {
      return finish('DENY', 'master agent not registered');
    }
    if (m.status !== 'active' || this.agents.get(MASTER_AGENT_ID)?.status !== 'active') {
      return finish('DENY', 'master agent is not active');
    }
    if (m.tenant_id !== tenant_id) {
      return finish('DENY', 'tenant boundary violation');
    }
    const owner = this.persons.get(m.owner_person_id);
    if (!owner || owner.status !== 'active') {
      return finish('DENY', 'owner (负责人) is not active');
    }
    if (HIGH_RISK_COMMANDS.has(command) && !params?.approval_ticket) {
      return finish('REQUIRE_APPROVAL', 'high-risk command needs maker-checker approval ticket; master agent cannot bypass (fail closed)');
    }
    if (params?.approval_ticket) {
      return finish('DENY', 'approval workflow not implemented in DEMO (501); no valid ticket can exist');
    }
    const p = params || {};
    const evt = () => 'EVT_MASTER_' + randomUUID();
    switch (command) {
      case 'person.hire':
        return this._masterWrap(trace_id, command, tenant_id, this.applyM07Event({ event_id: evt(), tenant_id, type: 'person.hired', person_id: p.person_id, position_id: p.position_id, seq: Date.now() }));
      case 'person.transfer':
        return this._masterWrap(trace_id, command, tenant_id, this.applyM07Event({ event_id: evt(), tenant_id, type: 'person.transferred', person_id: p.person_id, position_id: p.position_id, seq: Date.now() }));
      case 'person.offboard':
        return this._masterWrap(trace_id, command, tenant_id, this.applyM07Event({ event_id: evt(), tenant_id, type: 'person.offboarded', person_id: p.person_id, seq: Date.now() }));
      case 'agent.register':
        return this._masterWrap(trace_id, command, tenant_id, this.applyM13Event({ event_id: evt(), tenant_id, type: 'agent.registered', agent_id: p.agent_id, owner_person_id: p.owner_person_id || m.owner_person_id, parent_agent_id: p.parent_agent_id || null, scopes: p.scopes || [] }));
      case 'agent.revoke':
        return this._masterWrap(trace_id, command, tenant_id, this.applyM13Event({ event_id: evt(), tenant_id, type: 'agent.revoked', agent_id: p.agent_id }));
      case 'principal.revoke': {
        const r = this.revoke(p.principal_id, `master command by ${m.owner_person_id}`);
        return this._masterWrap(trace_id, command, tenant_id, r.ok === false ? r : { ok: true, revoked: true, ...r });
      }
      case 'capabilities.query':
        return this._masterWrap(trace_id, command, tenant_id, { ok: true, result: this.capabilities(p.principal_id, tenant_id) });
      case 'authorize.check': {
        const d = this.authorize({ tenant_id, principal_id: p.principal_id, resource: p.resource, action: p.action });
        return this._masterWrap(trace_id, command, tenant_id, d.ok === false ? d : { ok: true, result: d });
      }
      case 'system.status':
        return this._masterWrap(trace_id, command, tenant_id, { ok: true, result: { persons: this.persons.size, agents: this.agents.size, events: this.events.size, revocations: this.revocations.size, audit_entries: this.auditLog.length } });
      case 'audit.tail': {
        const limit = Math.min(Number(p.limit || 20), 100);
        return this._masterWrap(trace_id, command, tenant_id, { ok: true, result: this.auditLog.slice(-limit) });
      }
      default:
        return finish('DENY', 'unreachable command branch');
    }
  }

  _qa(options) {
    const uniq = [];
    for (const o of options) {
      if (o && o.label && o.prompt && !uniq.some((u) => u.label === o.label)) uniq.push({ label: o.label.slice(0, 80), prompt: String(o.prompt).slice(0, 500) });
    }
    const list = uniq.slice(0, 3);
    if (list.length && !list.some((o) => o.primary)) list[0].primary = true;
    if (list.filter((o) => o.primary).length > 1) list.forEach((o, i) => { o.primary = i === 0; });
    return list;
  }

  _extract(text) {
    const up = String(text).toUpperCase();
    const person = up.match(/\bP_[A-Z0-9_]+\b/)?.[0] || null;
    const agent = (up.match(/\bA_[A-Z0-9_]+\b/)?.[0] === MASTER_AGENT_ID) ? null : up.match(/\bA_[A-Z0-9_]+\b/)?.[0] || null;
    let position = up.match(/\bPOS_[A-Z0-9_]+\b/)?.[0] || null;
    if (!position) {
      if (/经理|主管|MANAGER/.test(up)) position = 'POS_MANAGER';
      else if (/员工|职员|STAFF|普通/.test(up)) position = 'POS_STAFF';
    }
    const scope = up.match(/\b([A-Z]+):([A-Z]+)\b/)?.[0] || null;
    return { person, agent, position, scope };
  }

  say({ tenant_id, text }) {
    const raw = String(text || '').trim();
    const low = raw.toLowerCase();
    const trace_id = randomUUID();
    const ex = this._extract(raw);
    const reply = (content, quick_actions, extra = {}) => {
      const out = { trace_id, role: 'assistant', content, ...(quick_actions && quick_actions.length ? { quick_actions: this._qa(quick_actions) } : {}), ...extra };
      this._audit({ kind: 'master.say', trace_id, tenant_id, text: raw.slice(0, 200), reply: content.slice(0, 200) });
      return out;
    };
    const needRegister = () => reply('总控 Agent 还没注册，无法执行指令。要先注册吗（绑定负责人 P_OWNER）？', [
      { label: '注册总控', prompt: '注册总控', primary: true }
    ]);
    const run = (command, params, okPhrase) => {
      if (!this.master) return needRegister();
      const d = this.dispatchCommand({ tenant_id, command, params });
      if (d.ok === false) return reply(`✗ 指令格式有问题：${d.error}`, []);
      if (d.decision === 'EXECUTED') return reply(`✓ ${okPhrase || '已完成'}：${command}${d.trace_id ? `（trace ${d.trace_id.slice(0, 8)}）` : ''}`, d.output != null ? undefined : [], { decision: d.decision, command, trace_id: d.trace_id, output: d.output });
      if (d.decision === 'REQUIRE_APPROVAL') return reply(`△ 这个操作我不能直接执行：${d.reason}。你可以先查看当前状态，或者保持等待审批票据。`, [
        { label: '查看系统状态', prompt: '看看系统状态', primary: true },
        { label: '查他的能力', prompt: `查一下 ${params.person_id || params.principal_id || params.agent_id || 'P_1001'} 的能力` }
      ], { decision: d.decision, command, trace_id: d.trace_id });
      return reply(`✗ 无法执行：${d.reason}`, [{ label: '查看系统状态', prompt: '看看系统状态', primary: true }], { decision: d.decision, command, trace_id: d.trace_id });
    };

    if (/^注册总控|注册.{0,4}总控/.test(raw)) {
      const r = this.registerMaster({ tenant_id, owner_person_id: ex.person || 'P_OWNER' });
      if (r.ok === false) return reply(`注册失败：${r.error}`, []);
      return reply(`✓ 总控 Agent 已注册并在线，负责人 ${r.master.owner_person_id}。用大白话告诉我要做什么就行。`, [
        { label: '来个人入职', prompt: `让 ${ex.person || 'P_1001'} 入职，岗位 POS_STAFF`, primary: true },
        { label: '看看状态', prompt: '看看系统状态' },
        { label: '查能力', prompt: `查一下 ${ex.person || 'P_1001'} 的能力` }
      ]);
    }
    if (/^(你好|hi|hello|在吗|帮帮忙|帮助|help|能做什么|你会什么|你能干什么)/.test(low)) {
      return reply('你好，负责人。我是总控 Agent，替你统管这个系统：人员入转调离、子 Agent 注册撤销、能力查询、判权、撤权和状态审计都能办。直接说事就行。', [
        { label: '让 P_1001 入职', prompt: '让 P_1001 入职，岗位 POS_STAFF', primary: true },
        { label: '系统什么状态', prompt: '看看系统状态' },
        { label: 'P_1001 有什么权限', prompt: '查一下 P_1001 的能力' }
      ]);
    }
    if (/取消|算了|不用了/.test(raw)) return reply('好的，本次操作已取消。');

    if (/离职|辞退|开除/.test(raw)) {
      const p = ex.person;
      if (!p) return reply('要给谁办离职？', [
        { label: 'P_1001', prompt: '让 P_1001 离职', primary: true },
        { label: '算了', prompt: '取消' }
      ]);
      if (/确认/.test(raw)) return run('person.offboard', { person_id: p }, `已按流程尝试为 ${p} 办离职（高风险，等待审批）`);
      return reply(`${p} 的离职属于高风险操作（会撤销其本人和名下全部 Agent 的权限）。确认要继续吗？继续也会因无审批票据被安全拦截。`, [
        { label: '确认执行', prompt: `确认让 ${p} 离职`, primary: true },
        { label: '先看他的权限', prompt: `查一下 ${p} 的能力` },
        { label: '取消', prompt: '取消' }
      ]);
    }
    if (/撤销|撤权|停用|禁用|封禁/.test(raw)) {
      if (ex.agent) {
        if (/确认/.test(raw)) return run('agent.revoke', { agent_id: ex.agent }, `已按流程尝试撤销 ${ex.agent}`);
        return reply(`撤销 Agent ${ex.agent} 是高风险操作，会级联停用其子 Agent。确认继续？`, [
          { label: '确认执行', prompt: `确认撤销 Agent ${ex.agent}`, primary: true },
          { label: '取消', prompt: '取消' }
        ]);
      }
      const p = ex.person;
      if (!p) return reply('要撤销谁的身份/Agent？说人员 ID（P_ 开头）或 Agent ID（A_ 开头）。', [
        { label: '撤 P_1001', prompt: '撤销 P_1001', primary: true },
        { label: '撤主 Agent', prompt: '撤销 Agent A_MAIN_1001' },
        { label: '取消', prompt: '取消' }
      ]);
      if (/确认/.test(raw)) return run('principal.revoke', { principal_id: p }, `已按流程尝试撤权 ${p}`);
      return reply(`撤销 ${p} 的全部权限是高风险操作。确认继续？`, [
        { label: '确认执行', prompt: `确认撤销 ${p}`, primary: true },
        { label: '先看权限', prompt: `查一下 ${p} 的能力` },
        { label: '取消', prompt: '取消' }
      ]);
    }
    if (/入职|新员工|招聘|招了|聘用|录用/.test(raw)) {
      const p = ex.person || 'P_1001';
      const pos = ex.position;
      if (!pos) return reply(`好的，让 ${p} 入职。安排到哪个岗位？`, [
        { label: '普通员工', prompt: `让 ${p} 入职，岗位 POS_STAFF`, primary: true },
        { label: '经理', prompt: `让 ${p} 入职，岗位 POS_MANAGER` },
        { label: '算了', prompt: '取消' }
      ]);
      return run('person.hire', { person_id: p, position_id: pos }, `${p} 已入职（${pos}）`);
    }
    if (/调岗|转岗|调到|换成|晋升/.test(raw)) {
      const p = ex.person || 'P_1001';
      const pos = ex.position;
      if (!pos) return reply(`${p} 调到哪个岗位？`, [
        { label: '经理', prompt: `把 ${p} 调到经理岗`, primary: true },
        { label: '普通员工', prompt: `把 ${p} 调到员工岗` },
        { label: '取消', prompt: '取消' }
      ]);
      return run('person.transfer', { person_id: p, position_id: pos }, `${p} 已调岗（${pos}）`);
    }
    if (/(注册|新建|创建|添加).{0,8}(agent|智能体|助手|机器人)/.test(low) || /子\s*agent/.test(low)) {
      const a = ex.agent || 'A_CHILD_1001';
      const owner = ex.person || 'P_1001';
      const sc = ex.scope;
      if (!sc) return reply(`好，给 ${owner} 注册子 Agent ${a}。授什么权限范围（scope）？`, [
        { label: 'task:execute', prompt: `注册子 Agent ${a}，归属 ${owner}，scope task:execute`, primary: true },
        { label: 'report:read', prompt: `注册子 Agent ${a}，归属 ${owner}，scope report:read` },
        { label: '取消', prompt: '取消' }
      ]);
      return run('agent.register', { agent_id: a, owner_person_id: owner, scopes: [sc] }, `子 Agent ${a} 已注册`);
    }
    if (/能力|权限|能干什么|能做什么|有什么权限/.test(raw)) {
      const p = ex.person || ex.agent || 'P_1001';
      return run('capabilities.query', { principal_id: p }, `已查到 ${p} 的能力清单`);
    }
    if (/判权|能不能|允不允许|允许吗|可以吗/.test(raw)) {
      const p = ex.person || ex.agent || 'P_1001';
      const sc = ex.scope;
      if (!sc) return reply(`要判断 ${p} 对哪个动作的权限？格式 资源:动作。`, [
        { label: 'task:execute', prompt: `判断 ${p} 能不能 task:execute`, primary: true },
        { label: 'data:export', prompt: `判断 ${p} 能不能 data:export` },
        { label: '取消', prompt: '取消' }
      ]);
      const [resource, action] = sc.split(':');
      return run('authorize.check', { principal_id: p, resource: resource.toLowerCase(), action: action.toLowerCase() }, `判权完成（${sc}）`);
    }
    if (/状态|概况|怎么样|多少人|多少个|运行/.test(raw)) return run('system.status', {}, '系统状态如下');
    if (/审计|日志|记录|操作记录/.test(raw)) return run('audit.tail', { limit: 10 }, '最近审计记录如下');

    return reply('这个我还不会办。我目前能做：人员入转调离、子 Agent 注册撤销、能力查询、判权、撤权、状态与审计。想办哪件？', [
      { label: '来个人入职', prompt: '让 P_1001 入职，岗位 POS_STAFF', primary: true },
      { label: '看看系统状态', prompt: '看看系统状态' },
      { label: '查权限', prompt: '查一下 P_1001 的能力' }
    ]);
  }

  _masterWrap(trace_id, command, tenant_id, r) {
    const decision = r.ok === false ? 'DENY' : 'EXECUTED';
    this._audit({ kind: 'master.command', trace_id, tenant_id, command, decision, detail: r.error || 'ok' });
    return { trace_id, command, decision, reason: r.error || null, output: r.ok === false ? null : (r.result ?? r.person ?? r.agent ?? r.master ?? r.revoked ?? r) };
  }

  _audit(entry) {
    const record = {
      trace_id: entry.trace_id || randomUUID(),
      at: new Date().toISOString(),
      ...entry
    };
    this.auditLog.push(record);
    return record.trace_id;
  }

  applyM07Event(event) {
    const event_id = event.event_id;
    if (!event_id || !event.tenant_id || !event.type || !event.person_id) {
      return { ok: false, error: 'missing required fields: event_id, tenant_id, type, person_id', status: 400 };
    }
    if (!M07_EVENT_TYPES.has(event.type)) {
      return { ok: false, error: `unknown m07 event type: ${event.type}`, status: 400 };
    }
    if (this.events.has(event_id)) {
      this._audit({ kind: 'm07.event.duplicate', event_id, result: 'ignored' });
      return { ok: true, idempotent: true, event_id, applied: false };
    }
    this.events.set(event_id, { source: 'm07', type: event.type, at: new Date().toISOString() });

    let person = this.persons.get(event.person_id);
    if (!person) {
      person = { person_id: event.person_id, tenant_id: event.tenant_id, status: 'pending', position_id: null, seq: 0 };
      this.persons.set(event.person_id, person);
    }
    if (event.tenant_id !== person.tenant_id) {
      return { ok: false, error: 'tenant mismatch for existing person', status: 403 };
    }
    const hasSeq = event.seq !== undefined && event.seq !== null;
    const seq = Number(event.seq ?? 0);
    if (hasSeq && seq <= person.seq) {
      this._audit({ kind: 'm07.event.stale', event_id, person_id: event.person_id, seq, current_seq: person.seq, result: 'ignored' });
      return { ok: true, idempotent: false, stale: true, applied: false };
    }

    if (event.type === 'person.hired') {
      if (!event.position_id || !this.positions.has(event.position_id)) {
        return { ok: false, error: 'unknown position_id', status: 400 };
      }
      person.status = 'active';
      person.position_id = event.position_id;
      person.seq = seq || person.seq + 1;
      this.revocations.delete(event.person_id);
      this._audit({ kind: 'm07.person.hired', event_id, person_id: person.person_id, position_id: event.position_id });
      return { ok: true, idempotent: false, applied: true, person: this._personView(person) };
    }

    if (event.type === 'person.transferred') {
      if (!event.position_id || !this.positions.has(event.position_id)) {
        return { ok: false, error: 'unknown position_id', status: 400 };
      }
      person.position_id = event.position_id;
      person.seq = seq || person.seq + 1;
      this._audit({ kind: 'm07.person.transferred', event_id, person_id: person.person_id, position_id: event.position_id });
      return { ok: true, idempotent: false, applied: true, person: this._personView(person) };
    }

    if (event.type === 'person.offboarded') {
      person.status = 'offboarded';
      person.position_id = null;
      person.seq = seq || person.seq + 1;
      this.revocations.set(event.person_id, { reason: event.reason || 'offboarded', at: new Date().toISOString() });
      for (const agent of this.agents.values()) {
        if (agent.owner_person_id === event.person_id && agent.status === 'active') {
          agent.status = 'revoked';
          this._audit({ kind: 'm13.agent.revoked', agent_id: agent.agent_id, reason: `owner offboarded: ${event.person_id}` });
        }
      }
      this._audit({ kind: 'm07.person.offboarded', event_id, person_id: event.person_id, agents_revoked: true });
      return { ok: true, idempotent: false, applied: true, person: this._personView(person) };
    }
  }

  applyM13Event(event) {
    const event_id = event.event_id;
    if (!event_id || !event.tenant_id || !event.type || !event.agent_id || !event.owner_person_id) {
      return { ok: false, error: 'missing required fields: event_id, tenant_id, type, agent_id, owner_person_id', status: 400 };
    }
    if (!M13_EVENT_TYPES.has(event.type)) {
      return { ok: false, error: `unknown m13 event type: ${event.type}`, status: 400 };
    }
    if (this.events.has(event_id)) {
      this._audit({ kind: 'm13.event.duplicate', event_id, result: 'ignored' });
      return { ok: true, idempotent: true, event_id, applied: false };
    }
    this.events.set(event_id, { source: 'm13', type: event.type, at: new Date().toISOString() });

    if (event.type === 'agent.registered') {
      const owner = this.persons.get(event.owner_person_id);
      if (!owner || owner.status !== 'active' || owner.tenant_id !== event.tenant_id) {
        return { ok: false, error: 'owner person must be active in same tenant', status: 403 };
      }
      let scopes = Array.isArray(event.scopes) ? event.scopes : [];
      let clamped = [];
      if (event.parent_agent_id) {
        const parent = this.agents.get(event.parent_agent_id);
        if (!parent || parent.status !== 'active' || parent.tenant_id !== event.tenant_id) {
          return { ok: false, error: 'parent agent must be active in same tenant', status: 403 };
        }
        const allowed = new Set(parent.scopes);
        const requested = scopes;
        scopes = requested.filter((s) => allowed.has(s));
        clamped = requested.filter((s) => !allowed.has(s));
      }
      this.agents.set(event.agent_id, {
        agent_id: event.agent_id,
        tenant_id: event.tenant_id,
        owner_person_id: event.owner_person_id,
        parent_agent_id: event.parent_agent_id || null,
        scopes,
        status: 'active'
      });
      this._audit({ kind: 'm13.agent.registered', event_id, agent_id: event.agent_id, owner_person_id: event.owner_person_id, parent_agent_id: event.parent_agent_id || null, scopes, clamped_scopes: clamped });
      return { ok: true, idempotent: false, applied: true, agent: this._agentView(this.agents.get(event.agent_id)), clamped_scopes: clamped };
    }

    if (event.type === 'agent.revoked') {
      const agent = this.agents.get(event.agent_id);
      if (!agent) {
        return { ok: false, error: 'unknown agent_id', status: 404 };
      }
      agent.status = 'revoked';
      for (const child of this.agents.values()) {
        if (child.parent_agent_id === event.agent_id && child.status === 'active') {
          child.status = 'revoked';
        }
      }
      this._audit({ kind: 'm13.agent.revoked', event_id, agent_id: event.agent_id, cascaded: true });
      return { ok: true, idempotent: false, applied: true, agent: this._agentView(agent) };
    }
  }

  authorize(input) {
    const { tenant_id, principal_id, resource, action } = input;
    const trace_id = randomUUID();
    const decision = (result, reason, extra = {}) => {
      this._audit({ kind: 'kernel.authorize', trace_id, tenant_id, principal_id, resource, action, decision: result, reason, ...extra });
      return { trace_id, tenant_id, principal_id, resource, action, decision: result, reason };
    };
    if (!tenant_id || !principal_id || !resource || !action) {
      return { ok: false, status: 400, error: 'required: tenant_id, principal_id, resource, action' };
    }
    if (this.revocations.has(principal_id)) {
      return decision('DENY', `principal revoked: ${this.revocations.get(principal_id).reason}`);
    }

    const person = this.persons.get(principal_id);
    if (person) {
      if (person.status !== 'active') {
        return decision('DENY', `person status is ${person.status}`);
      }
      if (person.tenant_id !== tenant_id) {
        return decision('DENY', 'tenant boundary violation');
      }
      const position = this.positions.get(person.position_id);
      if (!position || position.tenant_id !== tenant_id) {
        return decision('DENY', 'no active position binding');
      }
      const cap = position.capabilities.find((c) => c.resource === resource && c.action === action);
      if (!cap) {
        return decision('DENY', 'capability not granted by position policy');
      }
      if (HIGH_RISK_ACTIONS.has(`${resource}.${action}`)) {
        return decision('REQUIRE_APPROVAL', 'high risk action needs approval ticket before execution');
      }
      return decision('ALLOW', `position ${position.position_id} grants ${resource}:${action}`);
    }

    const agent = this.agents.get(principal_id);
    if (agent) {
      if (agent.status !== 'active') {
        return decision('DENY', `agent status is ${agent.status}`);
      }
      if (agent.tenant_id !== tenant_id) {
        return decision('DENY', 'tenant boundary violation');
      }
      const owner = this.persons.get(agent.owner_person_id);
      if (!owner || owner.status !== 'active') {
        return decision('DENY', 'agent owner is not active');
      }
      const scope = `${resource}:${action}`;
      if (!agent.scopes.includes(scope)) {
        return decision('DENY', `scope ${scope} not granted to agent (no implicit inheritance)`);
      }
      if (HIGH_RISK_ACTIONS.has(`${resource}.${action}`)) {
        return decision('REQUIRE_APPROVAL', 'high risk action needs approval ticket before execution');
      }
      return decision('ALLOW', `agent scope ${scope}`);
    }

    return decision('DENY', 'unknown principal');
  }

  capabilities(principal_id, tenant_id) {
    const trace_id = randomUUID();
    const result = { trace_id, principal_id, tenant_id: tenant_id || null, capabilities: [], status: 'unknown' };
    const person = this.persons.get(principal_id);
    if (person) {
      result.status = person.status;
      result.type = 'person';
      if (person.status === 'active' && (!tenant_id || person.tenant_id === tenant_id)) {
        const position = this.positions.get(person.position_id);
        if (position) {
          result.tenant_id = person.tenant_id;
          result.position_id = position.position_id;
          result.capabilities = position.capabilities.map((c) => ({
            ...c,
            require_approval: HIGH_RISK_ACTIONS.has(`${c.resource}.${c.action}`)
          }));
        }
      }
      this._audit({ kind: 'kernel.capabilities', trace_id, principal_id, count: result.capabilities.length });
      return result;
    }
    const agent = this.agents.get(principal_id);
    if (agent) {
      result.status = agent.status;
      result.type = 'agent';
      if (agent.status === 'active' && (!tenant_id || agent.tenant_id === tenant_id)) {
        result.tenant_id = agent.tenant_id;
        result.capabilities = agent.scopes
          .map((s) => {
            const [resource, action] = s.split(':');
            return { resource, action, require_approval: HIGH_RISK_ACTIONS.has(`${resource}.${action}`) };
          })
          .filter((c) => c.resource && c.action);
      }
      this._audit({ kind: 'kernel.capabilities', trace_id, principal_id, count: result.capabilities.length });
      return result;
    }
    this._audit({ kind: 'kernel.capabilities', trace_id, principal_id, count: 0, note: 'unknown principal' });
    return result;
  }

  revoke(principal_id, reason) {
    const trace_id = randomUUID();
    const person = this.persons.get(principal_id);
    if (person) {
      person.status = 'revoked';
      person.position_id = null;
      this.revocations.set(principal_id, { reason: reason || 'manual revocation', at: new Date().toISOString() });
      let agentsRevoked = 0;
      for (const agent of this.agents.values()) {
        if (agent.owner_person_id === principal_id && agent.status === 'active') {
          agent.status = 'revoked';
          agentsRevoked += 1;
        }
      }
      this._audit({ kind: 'kernel.revocation', trace_id, principal_id, reason, agents_revoked: agentsRevoked });
      return { trace_id, principal_id, revoked: true, agents_revoked: agentsRevoked };
    }
    const agent = this.agents.get(principal_id);
    if (agent) {
      agent.status = 'revoked';
      this.revocations.set(principal_id, { reason: reason || 'manual revocation', at: new Date().toISOString() });
      this._audit({ kind: 'kernel.revocation', trace_id, principal_id, reason, agent: true });
      return { trace_id, principal_id, revoked: true, agents_revoked: 0 };
    }
    return { ok: false, status: 404, error: 'unknown principal_id' };
  }

  status() {
    return {
      master: this.master ? { agent_id: this.master.agent_id, tenant_id: this.master.tenant_id, owner_person_id: this.master.owner_person_id, status: this.agents.get(this.master.agent_id)?.status || this.master.status, commands: this.master.scopes } : null,
      persons: [...this.persons.values()].map((p) => this._personView(p)),
      agents: [...this.agents.values()].map((a) => this._agentView(a)),
      events_received: this.events.size,
      revocations: [...this.revocations.entries()].map(([id, r]) => ({ principal_id: id, ...r })),
      audit_entries: this.auditLog.length
    };
  }

  _personView(p) {
    return { person_id: p.person_id, tenant_id: p.tenant_id, status: p.status, position_id: p.position_id, seq: p.seq };
  }

  _agentView(a) {
    return { agent_id: a.agent_id, tenant_id: a.tenant_id, owner_person_id: a.owner_person_id, parent_agent_id: a.parent_agent_id, scopes: a.scopes, status: a.status };
  }
}
