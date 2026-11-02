export const KernelStateVersion = 1;

const MAP_FIELDS = ['positions', 'persons', 'agents', 'events', 'revocations', 'approvals'];

function clone(value) {
  return value == null ? value : JSON.parse(JSON.stringify(value));
}

export function parseKernelState(input) {
  if (!input || typeof input !== 'object') throw new Error('kernel state must be an object');
  if (input.version !== KernelStateVersion) throw new Error(`unsupported kernel state version: ${input.version}`);
  for (const field of MAP_FIELDS) {
    if (!Array.isArray(input[field])) throw new Error(`kernel state ${field} must be an array`);
    for (const entry of input[field]) {
      if (!Array.isArray(entry) || entry.length !== 2 || typeof entry[0] !== 'string') {
        throw new Error(`kernel state ${field} contains an invalid map entry`);
      }
    }
  }
  if (!Array.isArray(input.auditLog)) throw new Error('kernel state auditLog must be an array');
  if (input.master !== null && (typeof input.master !== 'object' || Array.isArray(input.master))) {
    throw new Error('kernel state master must be an object or null');
  }
  return {
    version: KernelStateVersion,
    positions: clone(input.positions),
    persons: clone(input.persons),
    agents: clone(input.agents),
    events: clone(input.events),
    revocations: clone(input.revocations),
    approvals: clone(input.approvals),
    auditLog: clone(input.auditLog),
    master: clone(input.master)
  };
}
