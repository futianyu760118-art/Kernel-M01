import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { Pool } from 'pg';
import { parseKernelState } from './state-schema.js';

const MIGRATION_VERSION = 1;
const MIGRATION_FILE = fileURLToPath(new URL('../migrations/001_m01_control_plane.sql', import.meta.url));

export class PostgresStore {
  constructor({ connectionString, snapshotKey = 'default', required = true, connectionTimeoutMillis = 2_000 } = {}) {
    if (!connectionString) throw new Error('M01_DATABASE_URL is required for PostgreSQL persistence');
    if (!snapshotKey || snapshotKey.length > 160) throw new Error('snapshotKey is invalid');
    this.snapshotKey = snapshotKey;
    this.required = required;
    this.pool = new Pool({ connectionString, connectionTimeoutMillis, max: 5 });
    this.initialized = false;
  }

  async initialize() {
    const migration = await readFile(MIGRATION_FILE, 'utf8');
    const client = await this.pool.connect();
    try {
      await client.query('BEGIN');
      await client.query(migration);
      await client.query(
        'INSERT INTO m01_schema_migrations (version) VALUES ($1) ON CONFLICT (version) DO NOTHING',
        [MIGRATION_VERSION]
      );
      await client.query('COMMIT');
      this.initialized = true;
    } catch (error) {
      await client.query('ROLLBACK').catch(() => {});
      throw new Error(`M01 PostgreSQL initialization failed: ${error.message}`);
    } finally {
      client.release();
    }
  }

  async loadState() {
    this.assertInitialized();
    const stateResult = await this.pool.query(
      'SELECT revision, state FROM m01_state_snapshots WHERE snapshot_key = $1',
      [this.snapshotKey]
    );
    const auditResult = await this.pool.query(
      'SELECT event FROM m01_audit_events WHERE snapshot_key = $1 ORDER BY id ASC',
      [this.snapshotKey]
    );
    if (!stateResult.rows[0]) return { state: null, revision: 0, auditEntries: auditResult.rows.map((row) => row.event) };
    return {
      state: parseKernelState(stateResult.rows[0].state),
      revision: Number(stateResult.rows[0].revision),
      auditEntries: auditResult.rows.map((row) => row.event)
    };
  }

  async saveState(state, auditEntries = [], expectedRevision = 0) {
    this.assertInitialized();
    const validated = parseKernelState(state);
    if (!Number.isInteger(expectedRevision) || expectedRevision < 0) throw new Error('expectedRevision must be a non-negative integer');
    const client = await this.pool.connect();
    try {
      await client.query('BEGIN');
      const currentResult = await client.query(
        'SELECT revision FROM m01_state_snapshots WHERE snapshot_key = $1 FOR UPDATE',
        [this.snapshotKey]
      );
      const currentRevision = currentResult.rows[0] ? Number(currentResult.rows[0].revision) : 0;
      if (currentRevision !== expectedRevision) throw new Error(`revision conflict: expected ${expectedRevision}, current ${currentRevision}`);
      const revision = currentRevision + 1;
      if (currentResult.rows[0]) {
        await client.query(
          'UPDATE m01_state_snapshots SET revision = $2, state = $3::jsonb, updated_at = NOW() WHERE snapshot_key = $1',
          [this.snapshotKey, revision, JSON.stringify(validated)]
        );
      } else {
        await client.query(
          'INSERT INTO m01_state_snapshots (snapshot_key, revision, state) VALUES ($1, $2, $3::jsonb)',
          [this.snapshotKey, revision, JSON.stringify(validated)]
        );
      }
      for (const [index, event] of auditEntries.entries()) {
        const traceId = typeof event?.trace_id === 'string' && event.trace_id ? event.trace_id : `${this.snapshotKey}:${revision}:${index}`;
        await client.query(
          'INSERT INTO m01_audit_events (snapshot_key, trace_id, event) VALUES ($1, $2, $3::jsonb) ON CONFLICT (snapshot_key, trace_id) DO NOTHING',
          [this.snapshotKey, traceId, JSON.stringify(event)]
        );
      }
      await client.query('COMMIT');
      return { revision };
    } catch (error) {
      await client.query('ROLLBACK').catch(() => {});
      throw error;
    } finally {
      client.release();
    }
  }

  async close() {
    await this.pool.end();
  }

  assertInitialized() {
    if (!this.initialized) throw new Error('M01 PostgreSQL store is not initialized');
  }
}
