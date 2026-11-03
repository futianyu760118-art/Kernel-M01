const baseUrl = String(process.env.M01_SMOKE_BASE_URL || 'http://127.0.0.1:3101').replace(/\/$/, '');
const token = String(process.env.M01_SMOKE_TOKEN || '');
const expectedPersons = Number(process.env.M01_SMOKE_EXPECT_PERSONS || 0);

async function request(path, options = {}) {
  const response = await fetch(`${baseUrl}${path}`, options);
  const text = await response.text();
  let data = {};
  try { data = JSON.parse(text); } catch { /* health endpoints should be JSON */ }
  if (!response.ok) throw new Error(`${path} returned HTTP ${response.status}: ${data.error || text.slice(0, 200)}`);
  return data;
}

const health = await request('/health');
const ready = await request('/readyz');
if (ready.status !== 'ready') throw new Error(`M01 is not ready: ${ready.status}`);
console.log(`[smoke] health=${health.status} mode=${health.mode} persistence=${ready.persistence} revision=${ready.revision}`);

if (token) {
  const status = await request('/kernel/v1/status', { headers: { Authorization: `Bearer ${token}` } });
  if (expectedPersons > 0 && status.persons.length < expectedPersons) {
    throw new Error(`expected at least ${expectedPersons} persons, got ${status.persons.length}`);
  }
  console.log(`[smoke] persons=${status.persons.length} agents=${status.agents.length} master=${status.master?.status || 'unregistered'} audit=${status.audit_entries}`);
}
