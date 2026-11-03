CREATE TABLE IF NOT EXISTS m01_schema_migrations (
  version INTEGER PRIMARY KEY,
  applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS m01_state_snapshots (
  snapshot_key TEXT PRIMARY KEY,
  revision BIGINT NOT NULL,
  state JSONB NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS m01_audit_events (
  id BIGSERIAL PRIMARY KEY,
  snapshot_key TEXT NOT NULL,
  trace_id TEXT NOT NULL,
  event JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (snapshot_key, trace_id)
);

CREATE INDEX IF NOT EXISTS m01_audit_events_snapshot_created_idx
  ON m01_audit_events (snapshot_key, created_at DESC, id DESC);
