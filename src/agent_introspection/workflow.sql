-- Local workflow store: findings, intervention proposals, and the model review
-- sessions that draft them. History is immutable; state changes are guarded.

CREATE TABLE IF NOT EXISTS findings (
    id TEXT PRIMARY KEY,
    fingerprint TEXT NOT NULL UNIQUE CHECK (length(fingerprint) = 64),
    category TEXT NOT NULL,
    project_identity_id TEXT,
    trend_state TEXT NOT NULL CHECK (
        trend_state IN ('isolated', 'emerging', 'actionable', 'dormant')
    ),
    detector_id TEXT NOT NULL CHECK (length(detector_id) > 0),
    detector_version INTEGER NOT NULL CHECK (detector_version > 0),
    first_seen_ns INTEGER NOT NULL CHECK (first_seen_ns >= 0),
    last_seen_ns INTEGER NOT NULL CHECK (last_seen_ns >= first_seen_ns),
    occurrence_count INTEGER NOT NULL CHECK (occurrence_count >= 0),
    canonical_task_count INTEGER NOT NULL CHECK (canonical_task_count >= 0),
    local_day_count INTEGER NOT NULL CHECK (local_day_count >= 0),
    entity_version INTEGER NOT NULL CHECK (entity_version > 0),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    replaced_by_finding_id TEXT REFERENCES findings(id),
    updated_at TEXT NOT NULL,
    CHECK (
        (is_active = 1 AND replaced_by_finding_id IS NULL)
        OR (is_active = 0 AND replaced_by_finding_id IS NOT NULL)
    )
) STRICT;

CREATE TRIGGER IF NOT EXISTS findings_no_delete
BEFORE DELETE ON findings BEGIN
    SELECT RAISE(ABORT, 'findings cannot be deleted');
END;

CREATE TABLE IF NOT EXISTS proposals (
    id TEXT PRIMARY KEY,
    finding_id TEXT NOT NULL REFERENCES findings(id),
    state TEXT NOT NULL CHECK (
        state IN (
            'pending', 'approved', 'rejected', 'applying', 'applied',
            'implementation_failed'
        )
    ),
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    entity_version INTEGER NOT NULL CHECK (entity_version > 0)
) STRICT;

CREATE TRIGGER IF NOT EXISTS proposals_no_delete
BEFORE DELETE ON proposals BEGIN
    SELECT RAISE(ABORT, 'proposals cannot be deleted');
END;

CREATE TRIGGER IF NOT EXISTS proposals_guard_update
BEFORE UPDATE ON proposals
WHEN OLD.id IS NOT NEW.id
  OR OLD.finding_id IS NOT NEW.finding_id
  OR OLD.payload_json IS NOT NEW.payload_json
  OR OLD.created_at IS NOT NEW.created_at
  OR NEW.entity_version != OLD.entity_version + 1
  OR NEW.updated_at <= OLD.updated_at
BEGIN
    SELECT RAISE(ABORT, 'proposal identity and content are immutable');
END;

CREATE TABLE IF NOT EXISTS proposal_events (
    id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL REFERENCES proposals(id),
    sequence INTEGER NOT NULL CHECK (sequence > 0),
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
    created_at TEXT NOT NULL,
    UNIQUE (proposal_id, sequence)
) STRICT;

CREATE TRIGGER IF NOT EXISTS proposal_events_no_update
BEFORE UPDATE ON proposal_events BEGIN
    SELECT RAISE(ABORT, 'proposal_events are immutable');
END;

CREATE TRIGGER IF NOT EXISTS proposal_events_no_delete
BEFORE DELETE ON proposal_events BEGIN
    SELECT RAISE(ABORT, 'proposal_events are immutable');
END;

CREATE TABLE IF NOT EXISTS review_sessions (
    id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    nonce TEXT NOT NULL UNIQUE,
    schema_version INTEGER NOT NULL CHECK (schema_version > 0),
    purpose TEXT NOT NULL CHECK (purpose = 'proposal'),
    requested_model TEXT NOT NULL,
    requested_effort TEXT NOT NULL,
    ordered_candidate_ids_json TEXT NOT NULL CHECK (json_valid(ordered_candidate_ids_json)),
    payload_hash TEXT NOT NULL CHECK (length(payload_hash) = 64),
    byte_count INTEGER NOT NULL CHECK (byte_count >= 0),
    reserved_model_budget INTEGER NOT NULL CHECK (reserved_model_budget >= 0),
    status TEXT NOT NULL CHECK (status IN ('exported', 'imported')),
    entity_version INTEGER NOT NULL CHECK (entity_version > 0),
    created_at TEXT NOT NULL,
    imported_at TEXT
) STRICT;

CREATE INDEX IF NOT EXISTS review_sessions_batch_idx
ON review_sessions(batch_id, purpose, created_at);

CREATE TRIGGER IF NOT EXISTS review_sessions_no_delete
BEFORE DELETE ON review_sessions BEGIN
    SELECT RAISE(ABORT, 'review_sessions cannot be deleted');
END;

CREATE TRIGGER IF NOT EXISTS review_sessions_guard_update
BEFORE UPDATE ON review_sessions
WHEN OLD.id IS NOT NEW.id
  OR OLD.batch_id IS NOT NEW.batch_id
  OR OLD.nonce IS NOT NEW.nonce
  OR OLD.schema_version IS NOT NEW.schema_version
  OR OLD.purpose IS NOT NEW.purpose
  OR OLD.requested_model IS NOT NEW.requested_model
  OR OLD.requested_effort IS NOT NEW.requested_effort
  OR OLD.ordered_candidate_ids_json IS NOT NEW.ordered_candidate_ids_json
  OR OLD.payload_hash IS NOT NEW.payload_hash
  OR OLD.byte_count IS NOT NEW.byte_count
  OR OLD.reserved_model_budget IS NOT NEW.reserved_model_budget
  OR OLD.created_at IS NOT NEW.created_at
  OR OLD.status != 'exported'
  OR NEW.status != 'imported'
  OR OLD.imported_at IS NOT NULL
  OR NEW.imported_at IS NULL
  OR NEW.entity_version != OLD.entity_version + 1
BEGIN
    SELECT RAISE(ABORT, 'review session history is immutable');
END;

CREATE TABLE IF NOT EXISTS model_runs (
    id TEXT PRIMARY KEY,
    review_session_id TEXT NOT NULL REFERENCES review_sessions(id),
    model TEXT NOT NULL,
    effort TEXT NOT NULL,
    trace_id TEXT,
    input_tokens INTEGER CHECK (input_tokens IS NULL OR input_tokens >= 0),
    output_tokens INTEGER CHECK (output_tokens IS NULL OR output_tokens >= 0),
    reasoning_tokens INTEGER CHECK (reasoning_tokens IS NULL OR reasoning_tokens >= 0),
    total_tokens INTEGER CHECK (total_tokens IS NULL OR total_tokens >= 0),
    token_availability TEXT NOT NULL DEFAULT 'unavailable' CHECK (
        token_availability IN ('complete', 'partial', 'unavailable')
    ),
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
) STRICT;

CREATE TRIGGER IF NOT EXISTS model_runs_no_update
BEFORE UPDATE ON model_runs BEGIN
    SELECT RAISE(ABORT, 'model_runs are immutable');
END;

CREATE TRIGGER IF NOT EXISTS model_runs_no_delete
BEFORE DELETE ON model_runs BEGIN
    SELECT RAISE(ABORT, 'model_runs are immutable');
END;

CREATE TABLE IF NOT EXISTS model_budget_ledger (
    id TEXT PRIMARY KEY,
    review_session_id TEXT NOT NULL REFERENCES review_sessions(id),
    entry_type TEXT NOT NULL,
    amount INTEGER NOT NULL,
    created_at TEXT NOT NULL
) STRICT;

CREATE TRIGGER IF NOT EXISTS model_budget_ledger_no_update
BEFORE UPDATE ON model_budget_ledger BEGIN
    SELECT RAISE(ABORT, 'model_budget_ledger are immutable');
END;

CREATE TRIGGER IF NOT EXISTS model_budget_ledger_no_delete
BEFORE DELETE ON model_budget_ledger BEGIN
    SELECT RAISE(ABORT, 'model_budget_ledger are immutable');
END;

CREATE TABLE IF NOT EXISTS proposal_drafts (
    id TEXT PRIMARY KEY,
    review_session_id TEXT NOT NULL REFERENCES review_sessions(id),
    candidate_id TEXT NOT NULL,
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
    created_at TEXT NOT NULL,
    UNIQUE (review_session_id, candidate_id)
) STRICT;

CREATE TRIGGER IF NOT EXISTS proposal_drafts_no_update
BEFORE UPDATE ON proposal_drafts BEGIN
    SELECT RAISE(ABORT, 'proposal_drafts are immutable');
END;

CREATE TRIGGER IF NOT EXISTS proposal_drafts_no_delete
BEFORE DELETE ON proposal_drafts BEGIN
    SELECT RAISE(ABORT, 'proposal_drafts are immutable');
END;
