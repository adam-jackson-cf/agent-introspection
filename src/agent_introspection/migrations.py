"""Numbered, transactional, fail-closed SQLite migrations."""
# ruff: noqa: E501

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final


class MigrationError(RuntimeError):
    """Raised when migration history or execution is unsafe."""


@dataclass(frozen=True, slots=True)
class Migration:
    """A numbered collection of statements applied in one transaction."""

    version: int
    name: str
    statements: tuple[str, ...]
    requires_foreign_keys_disabled: bool = False

    @property
    def checksum(self) -> str:
        payload = "\n-- statement --\n".join(self.statements).encode()
        return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True, slots=True)
class AppliedMigration:
    """Evidence for a migration applied by this invocation."""

    version: int
    name: str
    checksum: str
    backup_path: Path


_INITIAL_SCHEMA: Final[tuple[str, ...]] = (
    """
    CREATE TABLE migrations (
        version INTEGER PRIMARY KEY CHECK (version > 0),
        name TEXT NOT NULL,
        checksum TEXT NOT NULL CHECK (length(checksum) = 64),
        applied_at TEXT NOT NULL
    ) STRICT
    """,
    """
    CREATE TABLE scan_runs (
        id TEXT PRIMARY KEY,
        status TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed', 'no_data')),
        started_at TEXT NOT NULL,
        completed_at TEXT,
        source_start_ns INTEGER,
        source_end_ns INTEGER,
        rows_processed INTEGER NOT NULL DEFAULT 0 CHECK (rows_processed >= 0),
        error_code TEXT,
        details_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(details_json))
    ) STRICT
    """,
    """
    CREATE TABLE source_schema_snapshots (
        id TEXT PRIMARY KEY,
        source TEXT NOT NULL,
        fingerprint TEXT NOT NULL CHECK (length(fingerprint) = 64),
        schema_json TEXT NOT NULL CHECK (json_valid(schema_json)),
        captured_at TEXT NOT NULL,
        approved_at TEXT,
        approved_by TEXT,
        UNIQUE (source, fingerprint)
    ) STRICT
    """,
    """
    CREATE TABLE source_watermarks (
        source TEXT PRIMARY KEY,
        timestamp_ns INTEGER NOT NULL CHECK (timestamp_ns >= 0),
        row_id TEXT NOT NULL,
        updated_at TEXT NOT NULL
    ) STRICT
    """,
    """
    CREATE TABLE project_identities (
        id TEXT PRIMARY KEY,
        identity_kind TEXT NOT NULL CHECK (identity_kind = 'git'),
        canonical_path TEXT NOT NULL,
        canonical_name TEXT,
        git_common_dir TEXT,
        created_at TEXT NOT NULL,
        UNIQUE (identity_kind, canonical_path)
    ) STRICT
    """,
    """
    CREATE TABLE observations (
        id TEXT PRIMARY KEY,
        scan_run_id TEXT NOT NULL REFERENCES scan_runs(id),
        detector_id TEXT NOT NULL,
        detector_version INTEGER NOT NULL CHECK (detector_version > 0),
        category TEXT NOT NULL,
        project_identity_id TEXT REFERENCES project_identities(id),
        task_identity TEXT,
        turn_identity TEXT,
        occurred_at_ns INTEGER NOT NULL CHECK (occurred_at_ns >= 0),
        fingerprint TEXT NOT NULL CHECK (length(fingerprint) = 64),
        operation_kind TEXT NOT NULL,
        target_kind TEXT NOT NULL,
        normalized_target TEXT NOT NULL,
        normalized_failure_class TEXT NOT NULL,
        normalization_version INTEGER NOT NULL CHECK (normalization_version > 0),
        membership_explanation TEXT NOT NULL,
        attributes_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(attributes_json)),
        created_at TEXT NOT NULL
    ) STRICT
    """,
    "CREATE INDEX observations_fingerprint_idx ON observations(fingerprint, occurred_at_ns)",
    "CREATE INDEX observations_task_idx ON observations(task_identity, occurred_at_ns)",
    """
    CREATE TABLE evidence (
        id TEXT PRIMARY KEY,
        observation_id TEXT NOT NULL REFERENCES observations(id),
        evidence_kind TEXT NOT NULL,
        source_reference TEXT NOT NULL,
        redacted_content TEXT,
        content_hash TEXT NOT NULL CHECK (length(content_hash) = 64),
        correlation_status TEXT NOT NULL CHECK (
            correlation_status IN ('correlated', 'pending', 'quarantined')
        ),
        created_at TEXT NOT NULL
    ) STRICT
    """,
    """
    CREATE TABLE findings (
        id TEXT PRIMARY KEY,
        fingerprint TEXT NOT NULL UNIQUE CHECK (length(fingerprint) = 64),
        category TEXT NOT NULL,
        project_identity_id TEXT REFERENCES project_identities(id),
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
    ) STRICT
    """,
    """
    CREATE TABLE finding_membership (
        finding_id TEXT NOT NULL REFERENCES findings(id),
        observation_id TEXT NOT NULL REFERENCES observations(id),
        rationale TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (finding_id, observation_id)
    ) STRICT, WITHOUT ROWID
    """,
    """
    CREATE TABLE trend_evaluations (
        id TEXT PRIMARY KEY,
        finding_id TEXT NOT NULL REFERENCES findings(id),
        trend_state TEXT NOT NULL CHECK (
            trend_state IN ('isolated', 'emerging', 'actionable', 'dormant')
        ),
        window_start TEXT NOT NULL,
        window_end TEXT NOT NULL,
        occurrence_count INTEGER NOT NULL CHECK (occurrence_count >= 0),
        canonical_task_count INTEGER NOT NULL CHECK (canonical_task_count >= 0),
        local_day_count INTEGER NOT NULL CHECK (local_day_count >= 0),
        rationale TEXT NOT NULL,
        created_at TEXT NOT NULL
    ) STRICT
    """,
    """
    CREATE TABLE review_sessions (
        id TEXT PRIMARY KEY,
        batch_id TEXT NOT NULL,
        nonce TEXT NOT NULL UNIQUE,
        schema_version INTEGER NOT NULL CHECK (schema_version > 0),
        purpose TEXT NOT NULL CHECK (purpose IN ('capability_probe', 'classification', 'proposal')),
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
    ) STRICT
    """,
    "CREATE INDEX review_sessions_batch_idx ON review_sessions(batch_id, purpose, created_at)",
    """
    CREATE TABLE model_runs (
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
    ) STRICT
    """,
    """
    CREATE TABLE model_budget_ledger (
        id TEXT PRIMARY KEY,
        review_session_id TEXT NOT NULL REFERENCES review_sessions(id),
        entry_type TEXT NOT NULL,
        amount INTEGER NOT NULL,
        created_at TEXT NOT NULL
    ) STRICT
    """,
    """
    CREATE TABLE model_capability_proofs (
        id TEXT PRIMARY KEY,
        model TEXT NOT NULL,
        effort TEXT NOT NULL,
        thread_id TEXT NOT NULL,
        trace_id TEXT NOT NULL,
        schema_version INTEGER NOT NULL CHECK (schema_version > 0),
        total_tokens INTEGER NOT NULL CHECK (total_tokens > 0),
        tool_version TEXT NOT NULL,
        schema_fingerprint TEXT NOT NULL CHECK (length(schema_fingerprint) = 64),
        proven_at TEXT NOT NULL,
        expires_at TEXT NOT NULL
    ) STRICT
    """,
    """
    CREATE INDEX model_capability_proofs_lookup_idx
    ON model_capability_proofs(
        tool_version, schema_fingerprint, expires_at, model, effort
    )
    """,
    """
    CREATE TABLE semantic_classifications (
        id TEXT PRIMARY KEY,
        review_session_id TEXT NOT NULL REFERENCES review_sessions(id),
        candidate_id TEXT NOT NULL,
        payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
        created_at TEXT NOT NULL,
        UNIQUE (review_session_id, candidate_id)
    ) STRICT
    """,
    """
    CREATE TABLE proposal_drafts (
        id TEXT PRIMARY KEY,
        review_session_id TEXT NOT NULL REFERENCES review_sessions(id),
        candidate_id TEXT NOT NULL,
        payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
        created_at TEXT NOT NULL,
        UNIQUE (review_session_id, candidate_id)
    ) STRICT
    """,
    """
    CREATE TABLE proposals (
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
    ) STRICT
    """,
    """
    CREATE TABLE proposal_events (
        id TEXT PRIMARY KEY,
        proposal_id TEXT NOT NULL REFERENCES proposals(id),
        sequence INTEGER NOT NULL CHECK (sequence > 0),
        event_type TEXT NOT NULL,
        payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
        created_at TEXT NOT NULL,
        UNIQUE (proposal_id, sequence)
    ) STRICT
    """,
    """
    CREATE TABLE otlp_outbox (
        event_id TEXT PRIMARY KEY,
        payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
        status TEXT NOT NULL CHECK (status IN ('pending', 'delivered')),
        attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
        next_attempt_at TEXT NOT NULL,
        created_at TEXT NOT NULL,
        delivered_at TEXT
    ) STRICT
    """,
    """
    CREATE TABLE scheduler_leases (
        name TEXT PRIMARY KEY,
        owner_pid INTEGER NOT NULL CHECK (owner_pid > 0),
        heartbeat_at TEXT NOT NULL,
        expires_at TEXT NOT NULL
    ) STRICT
    """,
    """
    CREATE TRIGGER source_schema_snapshots_no_delete
    BEFORE DELETE ON source_schema_snapshots BEGIN
        SELECT RAISE(ABORT, 'source_schema_snapshots are immutable');
    END
    """,
    """
    CREATE TRIGGER source_schema_snapshots_guard_update
    BEFORE UPDATE ON source_schema_snapshots
    WHEN OLD.id IS NOT NEW.id
      OR OLD.source IS NOT NEW.source
      OR OLD.fingerprint IS NOT NEW.fingerprint
      OR OLD.schema_json IS NOT NEW.schema_json
      OR OLD.captured_at IS NOT NEW.captured_at
      OR OLD.approved_at IS NOT NULL
      OR OLD.approved_by IS NOT NULL
      OR NEW.approved_at IS NULL
      OR NEW.approved_by IS NULL
    BEGIN
        SELECT RAISE(ABORT, 'source schema content and approval history are immutable');
    END
    """,
    *tuple(
        f"""
        CREATE TRIGGER {table}_no_update
        BEFORE UPDATE ON {table} BEGIN
            SELECT RAISE(ABORT, '{table} are immutable');
        END
        """
        for table in (
            "migrations",
            "observations",
            "evidence",
            "finding_membership",
            "trend_evaluations",
            "model_runs",
            "model_budget_ledger",
            "model_capability_proofs",
            "semantic_classifications",
            "proposal_drafts",
            "proposal_events",
        )
    ),
    *tuple(
        f"""
        CREATE TRIGGER {table}_no_delete
        BEFORE DELETE ON {table} BEGIN
            SELECT RAISE(ABORT, '{table} are immutable');
        END
        """
        for table in (
            "migrations",
            "observations",
            "evidence",
            "finding_membership",
            "trend_evaluations",
            "model_runs",
            "model_budget_ledger",
            "model_capability_proofs",
            "semantic_classifications",
            "proposal_drafts",
            "proposal_events",
        )
    ),
    *tuple(
        f"""
        CREATE TRIGGER {table}_no_delete
        BEFORE DELETE ON {table} BEGIN
            SELECT RAISE(ABORT, '{table} cannot be deleted');
        END
        """
        for table in (
            "scan_runs",
            "project_identities",
            "findings",
            "proposals",
            "review_sessions",
            "otlp_outbox",
        )
    ),
    """
    CREATE TRIGGER review_sessions_guard_update
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
    END
    """,
    """
    CREATE TRIGGER proposals_guard_update
    BEFORE UPDATE ON proposals
    WHEN OLD.id IS NOT NEW.id
      OR OLD.finding_id IS NOT NEW.finding_id
      OR OLD.payload_json IS NOT NEW.payload_json
      OR OLD.created_at IS NOT NEW.created_at
      OR NEW.entity_version != OLD.entity_version + 1
      OR NEW.updated_at <= OLD.updated_at
    BEGIN
        SELECT RAISE(ABORT, 'proposal identity and content are immutable');
    END
    """,
    """
    CREATE TRIGGER otlp_outbox_guard_update
    BEFORE UPDATE ON otlp_outbox
    WHEN OLD.event_id IS NOT NEW.event_id
      OR OLD.payload_json IS NOT NEW.payload_json
      OR OLD.created_at IS NOT NEW.created_at
    BEGIN
        SELECT RAISE(ABORT, 'OTLP event identity and payload are immutable');
    END
    """,
    """
    CREATE TRIGGER otlp_outbox_guard_insert
    BEFORE INSERT ON otlp_outbox
    WHEN EXISTS (
        SELECT 1 FROM otlp_outbox
        WHERE event_id = NEW.event_id AND payload_json IS NOT NEW.payload_json
    )
    BEGIN
        SELECT RAISE(ABORT, 'OTLP event ID conflicts with an immutable payload');
    END
    """,
)

_CANONICAL_SCHEMA: Final[tuple[str, ...]] = (
    *_INITIAL_SCHEMA,
    """
    CREATE TABLE session_context_events (
        event_id TEXT PRIMARY KEY CHECK (length(event_id) = 64),
        producer TEXT NOT NULL CHECK (producer IN ('claude-code', 'codex-cli', 'codex-app-server', 'omp')),
        session_id TEXT NOT NULL,
        event_type TEXT NOT NULL CHECK (event_type IN ('session_start', 'workspace_changed', 'session_end')),
        occurred_at TEXT NOT NULL,
        project_id TEXT NOT NULL CHECK (length(project_id) = 64),
        project_name TEXT NOT NULL,
        project_root TEXT NOT NULL,
        project_kind TEXT NOT NULL CHECK (project_kind = 'git')
    ) STRICT
    """,
    """
    CREATE TABLE session_context_intervals (
        event_id TEXT PRIMARY KEY REFERENCES session_context_events(event_id),
        producer TEXT NOT NULL CHECK (producer IN ('claude-code', 'codex-cli', 'codex-app-server', 'omp')),
        session_id TEXT NOT NULL,
        started_at TEXT NOT NULL,
        ended_at TEXT,
        end_event_id TEXT UNIQUE REFERENCES session_context_events(event_id),
        project_id TEXT NOT NULL CHECK (length(project_id) = 64),
        project_name TEXT NOT NULL,
        project_root TEXT NOT NULL,
        project_kind TEXT NOT NULL CHECK (project_kind = 'git'),
        CHECK (ended_at IS NULL OR ended_at >= started_at)
    ) STRICT
    """,
    "CREATE UNIQUE INDEX session_context_open_interval_idx ON session_context_intervals(producer, session_id) WHERE ended_at IS NULL",
    "CREATE INDEX session_context_correlation_idx ON session_context_intervals(session_id, started_at, ended_at)",
    """
    CREATE TABLE canonical_activities (
        id TEXT PRIMARY KEY,
        producer TEXT NOT NULL CHECK (producer IN ('claude-code', 'codex-cli', 'codex-app-server', 'omp')),
        producer_surface TEXT NOT NULL CHECK (length(producer_surface) > 0),
        correlation_id TEXT NOT NULL CHECK (length(correlation_id) > 0),
        source_started_at_ns INTEGER NOT NULL CHECK (source_started_at_ns >= 0),
        source_ended_at_ns INTEGER NOT NULL CHECK (source_ended_at_ns >= source_started_at_ns),
        detector_id TEXT NOT NULL CHECK (length(detector_id) > 0),
        detector_version INTEGER NOT NULL CHECK (detector_version > 0),
        normalization_version INTEGER NOT NULL CHECK (normalization_version > 0),
        source_membership_hash TEXT NOT NULL CHECK (length(source_membership_hash) = 64),
        source_membership_json TEXT NOT NULL CHECK (json_valid(source_membership_json)),
        operation_kind TEXT NOT NULL CHECK (length(operation_kind) > 0),
        target_kind TEXT NOT NULL CHECK (length(target_kind) > 0),
        normalized_target TEXT NOT NULL,
        normalized_failure_class TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE (detector_id, detector_version, normalization_version, source_membership_hash)
    ) STRICT
    """,
    "CREATE INDEX canonical_activities_source_membership_idx ON canonical_activities(source_membership_hash, source_started_at_ns)",
    "CREATE INDEX canonical_activities_correlation_window_idx ON canonical_activities(producer, correlation_id, source_started_at_ns)",
    """
    CREATE TABLE canonical_activity_versions (
        activity_id TEXT NOT NULL REFERENCES canonical_activities(id),
        version INTEGER NOT NULL CHECK (version > 0),
        attribution_state TEXT NOT NULL CHECK (attribution_state IN ('resolved', 'unresolved')),
        project_identity_id TEXT REFERENCES project_identities(id),
        attribution_method TEXT NOT NULL CHECK (length(attribution_method) > 0),
        attribution_evidence_id TEXT,
        reason_code TEXT,
        created_at TEXT NOT NULL,
        PRIMARY KEY (activity_id, version),
        CHECK ((attribution_state = 'resolved' AND project_identity_id IS NOT NULL AND reason_code IS NULL) OR (attribution_state = 'unresolved' AND project_identity_id IS NULL))
    ) STRICT, WITHOUT ROWID
    """,
    "CREATE INDEX canonical_activity_versions_latest_idx ON canonical_activity_versions(activity_id, version DESC)",
    """
    CREATE INDEX canonical_activity_versions_attribution_tuple_idx
    ON canonical_activity_versions(
        attribution_state, project_identity_id, attribution_method, attribution_evidence_id
    )
    """,
    """
    CREATE TABLE canonical_recomputation_schedule (
        activity_id TEXT NOT NULL,
        activity_version INTEGER NOT NULL,
        aggregate_kind TEXT NOT NULL CHECK (aggregate_kind IN ('findings', 'trends')),
        scheduled_at TEXT NOT NULL,
        completed_at TEXT,
        PRIMARY KEY (activity_id, activity_version, aggregate_kind),
        FOREIGN KEY (activity_id, activity_version) REFERENCES canonical_activity_versions(activity_id, version),
        CHECK (completed_at IS NULL OR completed_at >= scheduled_at)
    ) STRICT, WITHOUT ROWID
    """,
    "CREATE INDEX canonical_recomputation_pending_idx ON canonical_recomputation_schedule(aggregate_kind, scheduled_at) WHERE completed_at IS NULL",
    """
    CREATE TABLE canonical_activity_outbox_evidence (
        activity_id TEXT NOT NULL,
        activity_version INTEGER NOT NULL,
        payload_schema_version INTEGER NOT NULL CHECK (payload_schema_version > 0),
        event_name TEXT NOT NULL CHECK (length(event_name) > 0),
        event_id TEXT NOT NULL UNIQUE REFERENCES otlp_outbox(event_id) CHECK (length(event_id) = 64),
        created_at TEXT NOT NULL,
        PRIMARY KEY (activity_id, activity_version, payload_schema_version, event_name),
        FOREIGN KEY (activity_id, activity_version) REFERENCES canonical_activity_versions(activity_id, version)
    ) STRICT, WITHOUT ROWID
    """,
    """
    CREATE TABLE canonical_rejections (
        id TEXT PRIMARY KEY,
        producer TEXT NOT NULL CHECK (producer IN ('claude-code', 'codex-cli', 'codex-app-server', 'omp')),
        producer_surface TEXT NOT NULL CHECK (length(producer_surface) > 0),
        correlation_id TEXT,
        lifecycle_event TEXT NOT NULL CHECK (lifecycle_event IN ('session_start', 'workspace_changed', 'session_end')),
        occurred_at TEXT NOT NULL,
        reason_code TEXT NOT NULL,
        source_adapter TEXT NOT NULL CHECK (length(source_adapter) > 0),
        created_at TEXT NOT NULL,
        UNIQUE (producer, producer_surface, correlation_id, lifecycle_event, occurred_at, reason_code, source_adapter)
    ) STRICT
    """,
    """
    CREATE TABLE canonical_finding_membership (
        finding_id TEXT NOT NULL REFERENCES findings(id),
        activity_id TEXT NOT NULL REFERENCES canonical_activities(id),
        rationale TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (finding_id, activity_id)
    ) STRICT, WITHOUT ROWID
    """,
    "CREATE INDEX canonical_finding_membership_activity_idx ON canonical_finding_membership(activity_id, finding_id)",
    *tuple(
        f"CREATE TRIGGER {table}_no_update BEFORE UPDATE ON {table} BEGIN SELECT RAISE(ABORT, '{table} are immutable'); END"
        for table in (
            "session_context_events",
            "canonical_activities",
            "canonical_activity_versions",
            "canonical_activity_outbox_evidence",
            "canonical_rejections",
            "canonical_finding_membership",
        )
    ),
    *tuple(
        f"CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table} BEGIN SELECT RAISE(ABORT, '{table} cannot be deleted'); END"
        for table in (
            "session_context_events",
            "canonical_activities",
            "canonical_activity_versions",
            "canonical_activity_outbox_evidence",
            "canonical_rejections",
            "canonical_finding_membership",
        )
    ),
    """
    CREATE TRIGGER session_context_intervals_guard_update
    BEFORE UPDATE ON session_context_intervals
    WHEN OLD.event_id IS NOT NEW.event_id OR OLD.producer IS NOT NEW.producer
      OR OLD.session_id IS NOT NEW.session_id OR OLD.started_at IS NOT NEW.started_at
      OR OLD.project_id IS NOT NEW.project_id OR OLD.project_name IS NOT NEW.project_name
      OR OLD.project_root IS NOT NEW.project_root OR OLD.project_kind IS NOT NEW.project_kind
      OR OLD.ended_at IS NOT NULL OR NEW.ended_at IS NULL OR NEW.end_event_id IS NULL
    BEGIN SELECT RAISE(ABORT, 'session context interval history is immutable'); END
    """,
    "CREATE TRIGGER session_context_intervals_no_delete BEFORE DELETE ON session_context_intervals BEGIN SELECT RAISE(ABORT, 'session context intervals cannot be deleted'); END",
    "CREATE TRIGGER canonical_activity_versions_monotonic BEFORE INSERT ON canonical_activity_versions WHEN NEW.version != COALESCE((SELECT MAX(version) + 1 FROM canonical_activity_versions WHERE activity_id = NEW.activity_id), 1) BEGIN SELECT RAISE(ABORT, 'canonical activity versions must be monotonic'); END",
)

_TEMPORAL_AND_REJECTION_SCHEMA: Final[tuple[str, ...]] = (
    """
    CREATE TABLE session_context_replay_state (
        producer TEXT NOT NULL CHECK (producer IN ('claude-code', 'codex-cli', 'codex-app-server', 'omp')),
        session_id TEXT NOT NULL,
        latest_occurred_at TEXT NOT NULL,
        PRIMARY KEY (producer, session_id)
    ) STRICT, WITHOUT ROWID
    """,
    "CREATE UNIQUE INDEX canonical_activity_versions_attribution_unique_idx ON canonical_activity_versions(activity_id, attribution_state, COALESCE(project_identity_id, ''), attribution_method, COALESCE(attribution_evidence_id, ''), COALESCE(reason_code, ''))",
    "DROP TRIGGER session_context_intervals_guard_update",
    "DROP TRIGGER session_context_intervals_no_delete",
    """
    CREATE TABLE session_context_replay_mutations (
        producer TEXT NOT NULL,
        session_id TEXT NOT NULL,
        PRIMARY KEY (producer, session_id)
    ) STRICT, WITHOUT ROWID
    """,
    "CREATE TRIGGER session_context_intervals_guard_update BEFORE UPDATE ON session_context_intervals WHEN NOT EXISTS (SELECT 1 FROM session_context_replay_mutations WHERE producer = OLD.producer AND session_id = OLD.session_id) BEGIN SELECT RAISE(ABORT, 'session context interval history is immutable'); END",
    "CREATE TRIGGER session_context_intervals_no_delete BEFORE DELETE ON session_context_intervals WHEN NOT EXISTS (SELECT 1 FROM session_context_replay_mutations WHERE producer = OLD.producer AND session_id = OLD.session_id) BEGIN SELECT RAISE(ABORT, 'session context intervals cannot be deleted'); END",
    "DROP TRIGGER canonical_rejections_no_update",
    "DROP TRIGGER canonical_rejections_no_delete",
    "ALTER TABLE canonical_rejections RENAME TO canonical_rejections_legacy",
    """
    CREATE TABLE canonical_rejections (
        id TEXT PRIMARY KEY,
        producer TEXT NOT NULL CHECK (producer IN ('claude-code', 'codex-cli', 'codex-app-server', 'omp')),
        producer_surface TEXT NOT NULL CHECK (length(producer_surface) > 0),
        correlation_id TEXT,
        lifecycle_event TEXT NOT NULL CHECK (lifecycle_event IN ('session_start', 'workspace_changed', 'session_end', 'source_activity')),
        occurred_at TEXT NOT NULL,
        reason_code TEXT NOT NULL,
        source_adapter TEXT NOT NULL CHECK (length(source_adapter) > 0),
        source_provenance TEXT CHECK (source_provenance IS NULL OR json_valid(source_provenance)),
        created_at TEXT NOT NULL
    ) STRICT
    """,
    """
    INSERT INTO canonical_rejections(
        id, producer, producer_surface, correlation_id, lifecycle_event, occurred_at,
        reason_code, source_adapter, source_provenance, created_at
    )
    SELECT id, producer, producer_surface, correlation_id, lifecycle_event, occurred_at,
           reason_code, source_adapter, NULL, created_at
    FROM canonical_rejections_legacy
    """,
    "DROP TABLE canonical_rejections_legacy",
    "CREATE UNIQUE INDEX canonical_rejections_identity_idx ON canonical_rejections(producer, producer_surface, COALESCE(correlation_id, ''), lifecycle_event, occurred_at, reason_code, source_adapter, COALESCE(source_provenance, ''))",
    "CREATE TRIGGER canonical_rejections_no_update BEFORE UPDATE ON canonical_rejections BEGIN SELECT RAISE(ABORT, 'canonical_rejections are immutable'); END",
    "CREATE TRIGGER canonical_rejections_no_delete BEFORE DELETE ON canonical_rejections BEGIN SELECT RAISE(ABORT, 'canonical_rejections cannot be deleted'); END",
)

_REJECTION_REASON_CHECK_SCHEMA: Final[tuple[str, ...]] = (
    "DROP TRIGGER canonical_rejections_no_update",
    "DROP TRIGGER canonical_rejections_no_delete",
    "ALTER TABLE canonical_rejections RENAME TO canonical_rejections_legacy",
    """
    CREATE TABLE canonical_rejections (
        id TEXT PRIMARY KEY, producer TEXT NOT NULL CHECK (producer IN ('claude-code', 'codex-cli', 'codex-app-server', 'omp')),
        producer_surface TEXT NOT NULL CHECK (length(producer_surface) > 0), correlation_id TEXT,
        lifecycle_event TEXT NOT NULL CHECK (lifecycle_event IN ('session_start', 'workspace_changed', 'session_end', 'source_activity')),
        occurred_at TEXT NOT NULL,
        reason_code TEXT NOT NULL CHECK (reason_code IN ('missing_correlation_id', 'conflicting_correlation_id', 'missing_workspace', 'invalid_workspace', 'non_git_workspace', 'git_resolution_failed', 'invalid_timestamp', 'invalid_transition', 'duplicate_conflict', 'out_of_order_event')),
        source_adapter TEXT NOT NULL CHECK (length(source_adapter) > 0),
        source_provenance TEXT CHECK (source_provenance IS NULL OR json_valid(source_provenance)), created_at TEXT NOT NULL
    ) STRICT
    """,
    "INSERT INTO canonical_rejections SELECT * FROM canonical_rejections_legacy WHERE reason_code IN ('missing_correlation_id', 'conflicting_correlation_id', 'missing_workspace', 'invalid_workspace', 'non_git_workspace', 'git_resolution_failed', 'invalid_timestamp', 'invalid_transition', 'duplicate_conflict', 'out_of_order_event')",
    "DROP TABLE canonical_rejections_legacy",
    "CREATE UNIQUE INDEX canonical_rejections_identity_idx ON canonical_rejections(producer, producer_surface, COALESCE(correlation_id, ''), lifecycle_event, occurred_at, reason_code, source_adapter, COALESCE(source_provenance, ''))",
    "CREATE TRIGGER canonical_rejections_no_update BEFORE UPDATE ON canonical_rejections BEGIN SELECT RAISE(ABORT, 'canonical_rejections are immutable'); END",
    "CREATE TRIGGER canonical_rejections_no_delete BEFORE DELETE ON canonical_rejections BEGIN SELECT RAISE(ABORT, 'canonical_rejections cannot be deleted'); END",
)

_LEGACY_ATTRIBUTION_FACT_SCHEMA: Final[tuple[str, ...]] = (
    """
    CREATE TABLE legacy_attribution_fact_sets (
        id TEXT PRIMARY KEY,
        start_at TEXT NOT NULL,
        end_at TEXT NOT NULL,
        approved_by TEXT NOT NULL CHECK (length(approved_by) > 0),
        denominator INTEGER NOT NULL CHECK (denominator >= 0),
        accepted INTEGER NOT NULL CHECK (accepted >= 0),
        rejected INTEGER NOT NULL CHECK (rejected >= 0),
        unresolved INTEGER NOT NULL CHECK (unresolved >= 0),
        source_ids_json TEXT NOT NULL CHECK (json_valid(source_ids_json)),
        created_at TEXT NOT NULL,
        CHECK (denominator = accepted + rejected + unresolved)
    ) STRICT
    """,
    "CREATE TRIGGER legacy_attribution_fact_sets_no_update BEFORE UPDATE ON legacy_attribution_fact_sets BEGIN SELECT RAISE(ABORT, 'legacy attribution fact sets are immutable'); END",
    "CREATE TRIGGER legacy_attribution_fact_sets_no_delete BEFORE DELETE ON legacy_attribution_fact_sets BEGIN SELECT RAISE(ABORT, 'legacy attribution fact sets cannot be deleted'); END",
)

_LEGACY_ATTRIBUTION_DELIVERY_LEDGER_SCHEMA: Final[tuple[str, ...]] = (
    """
    CREATE TABLE legacy_attribution_delivery_attempts (
        id INTEGER PRIMARY KEY,
        fact_set_id TEXT NOT NULL REFERENCES legacy_attribution_fact_sets(id),
        attempted_at TEXT NOT NULL,
        intended_event_ids_json TEXT NOT NULL CHECK (json_valid(intended_event_ids_json)),
        intended_event_count INTEGER NOT NULL CHECK (intended_event_count >= 0),
        intended_event_hash TEXT NOT NULL CHECK (length(intended_event_hash) = 64),
        local_delivery_result_json TEXT NOT NULL CHECK (json_valid(local_delivery_result_json)),
        remote_event_ids_json TEXT NOT NULL CHECK (json_valid(remote_event_ids_json)),
        remote_event_count INTEGER NOT NULL CHECK (remote_event_count >= 0),
        remote_event_hash TEXT NOT NULL CHECK (length(remote_event_hash) = 64),
        failure_reason TEXT CHECK (failure_reason IN ('local_delivery_incomplete', 'remote_event_id_mismatch')),
        verified_at TEXT,
        CHECK ((failure_reason IS NULL) = (verified_at IS NOT NULL))
    ) STRICT
    """,
    "CREATE INDEX legacy_attribution_delivery_attempts_fact_set_idx ON legacy_attribution_delivery_attempts(fact_set_id, id)",
    "CREATE TRIGGER legacy_attribution_delivery_attempts_no_update BEFORE UPDATE ON legacy_attribution_delivery_attempts BEGIN SELECT RAISE(ABORT, 'legacy attribution delivery attempts are immutable'); END",
    "CREATE TRIGGER legacy_attribution_delivery_attempts_no_delete BEFORE DELETE ON legacy_attribution_delivery_attempts BEGIN SELECT RAISE(ABORT, 'legacy attribution delivery attempts cannot be deleted'); END",
)

_CODEX_SESSION_CONTEXT_SCHEMA: Final[tuple[str, ...]] = (
    "DROP TRIGGER session_context_events_no_update",
    "DROP TRIGGER session_context_events_no_delete",
    "DROP TRIGGER session_context_intervals_guard_update",
    "DROP TRIGGER session_context_intervals_no_delete",
    "ALTER TABLE session_context_intervals RENAME TO session_context_intervals_legacy",
    "ALTER TABLE session_context_events RENAME TO session_context_events_legacy",
    """
    CREATE TABLE session_context_events (
        event_id TEXT PRIMARY KEY CHECK (length(event_id) = 64),
        producer TEXT NOT NULL CHECK (producer IN ('claude-code', 'codex-cli', 'codex-app-server', 'omp')),
        session_id TEXT NOT NULL,
        event_type TEXT NOT NULL CHECK (
            event_type IN ('session_start', 'workspace_changed', 'session_end', 'session_context')
            AND (event_type != 'session_context' OR producer = 'codex-cli')
        ),
        occurred_at TEXT NOT NULL,
        project_id TEXT NOT NULL CHECK (length(project_id) = 64),
        project_name TEXT NOT NULL,
        project_root TEXT NOT NULL,
        project_kind TEXT NOT NULL CHECK (project_kind = 'git')
    ) STRICT
    """,
    """
    INSERT INTO session_context_events
    SELECT * FROM session_context_events_legacy
    """,
    """
    CREATE TABLE session_context_intervals (
        event_id TEXT PRIMARY KEY REFERENCES session_context_events(event_id),
        producer TEXT NOT NULL CHECK (producer IN ('claude-code', 'codex-cli', 'codex-app-server', 'omp')),
        session_id TEXT NOT NULL,
        started_at TEXT NOT NULL,
        ended_at TEXT,
        end_event_id TEXT UNIQUE REFERENCES session_context_events(event_id),
        project_id TEXT NOT NULL CHECK (length(project_id) = 64),
        project_name TEXT NOT NULL,
        project_root TEXT NOT NULL,
        project_kind TEXT NOT NULL CHECK (project_kind = 'git'),
        CHECK (ended_at IS NULL OR ended_at >= started_at)
    ) STRICT
    """,
    """
    INSERT INTO session_context_intervals
    SELECT * FROM session_context_intervals_legacy
    """,
    "DROP TABLE session_context_intervals_legacy",
    "DROP TABLE session_context_events_legacy",
    "CREATE UNIQUE INDEX session_context_open_interval_idx ON session_context_intervals(producer, session_id) WHERE ended_at IS NULL",
    "CREATE INDEX session_context_correlation_idx ON session_context_intervals(session_id, started_at, ended_at)",
    "CREATE TRIGGER session_context_events_no_update BEFORE UPDATE ON session_context_events BEGIN SELECT RAISE(ABORT, 'session_context_events are immutable'); END",
    "CREATE TRIGGER session_context_events_no_delete BEFORE DELETE ON session_context_events BEGIN SELECT RAISE(ABORT, 'session_context_events cannot be deleted'); END",
    "CREATE TRIGGER session_context_intervals_guard_update BEFORE UPDATE ON session_context_intervals WHEN NOT EXISTS (SELECT 1 FROM session_context_replay_mutations WHERE producer = OLD.producer AND session_id = OLD.session_id) BEGIN SELECT RAISE(ABORT, 'session context interval history is immutable'); END",
    "CREATE TRIGGER session_context_intervals_no_delete BEFORE DELETE ON session_context_intervals WHEN NOT EXISTS (SELECT 1 FROM session_context_replay_mutations WHERE producer = OLD.producer AND session_id = OLD.session_id) BEGIN SELECT RAISE(ABORT, 'session context intervals cannot be deleted'); END",
)


_SESSION_CONTEXT_REJECTION_SCHEMA: Final[tuple[str, ...]] = (
    "DROP TRIGGER canonical_rejections_no_update",
    "DROP TRIGGER canonical_rejections_no_delete",
    "ALTER TABLE canonical_rejections RENAME TO canonical_rejections_legacy",
    """
    CREATE TABLE canonical_rejections (
        id TEXT PRIMARY KEY, producer TEXT NOT NULL CHECK (producer IN ('claude-code', 'codex-cli', 'codex-app-server', 'omp')),
        producer_surface TEXT NOT NULL CHECK (length(producer_surface) > 0), correlation_id TEXT,
        lifecycle_event TEXT NOT NULL CHECK (lifecycle_event IN ('session_start', 'workspace_changed', 'session_end', 'session_context', 'source_activity')),
        occurred_at TEXT NOT NULL,
        reason_code TEXT NOT NULL CHECK (reason_code IN ('missing_correlation_id', 'conflicting_correlation_id', 'missing_workspace', 'invalid_workspace', 'non_git_workspace', 'git_resolution_failed', 'invalid_timestamp', 'invalid_transition', 'duplicate_conflict', 'out_of_order_event')),
        source_adapter TEXT NOT NULL CHECK (length(source_adapter) > 0),
        source_provenance TEXT CHECK (source_provenance IS NULL OR json_valid(source_provenance)), created_at TEXT NOT NULL
    ) STRICT
    """,
    "INSERT INTO canonical_rejections SELECT * FROM canonical_rejections_legacy",
    "DROP TABLE canonical_rejections_legacy",
    "CREATE UNIQUE INDEX canonical_rejections_identity_idx ON canonical_rejections(producer, producer_surface, COALESCE(correlation_id, ''), lifecycle_event, occurred_at, reason_code, source_adapter, COALESCE(source_provenance, ''))",
    "CREATE TRIGGER canonical_rejections_no_update BEFORE UPDATE ON canonical_rejections BEGIN SELECT RAISE(ABORT, 'canonical_rejections are immutable'); END",
    "CREATE TRIGGER canonical_rejections_no_delete BEFORE DELETE ON canonical_rejections BEGIN SELECT RAISE(ABORT, 'canonical_rejections cannot be deleted'); END",
)


_RAW_SOURCE_SESSION_SCHEMA: Final[tuple[str, ...]] = (
    """
    CREATE TABLE source_session_records (
        scan_run_id TEXT NOT NULL REFERENCES scan_runs(id),
        source_kind TEXT NOT NULL CHECK (source_kind IN ('log', 'trace')),
        service_name TEXT NOT NULL CHECK (length(service_name) > 0),
        source_id TEXT NOT NULL CHECK (length(source_id) > 0),
        source_timestamp TEXT NOT NULL,
        session_ids_json TEXT NOT NULL CHECK (json_valid(session_ids_json)),
        thread_ids_json TEXT NOT NULL CHECK (json_valid(thread_ids_json)),
        legacy_thread_ids_json TEXT NOT NULL CHECK (json_valid(legacy_thread_ids_json)),
        gen_ai_conversation_ids_json TEXT NOT NULL CHECK (json_valid(gen_ai_conversation_ids_json)),
        terminal_outcome TEXT NOT NULL CHECK (
            terminal_outcome IN ('attributed', 'expected_rejection', 'failed', 'blocked')
        ),
        terminal_reason TEXT NOT NULL CHECK (length(terminal_reason) > 0),
        projection_event_id TEXT NOT NULL CHECK (length(projection_event_id) = 64),
        created_at TEXT NOT NULL,
        PRIMARY KEY (scan_run_id, source_kind, service_name, source_id)
    ) STRICT, WITHOUT ROWID
    """,
    "CREATE INDEX source_session_records_outcome_idx ON source_session_records(scan_run_id, terminal_outcome)",
    "CREATE TRIGGER source_session_records_no_update BEFORE UPDATE ON source_session_records BEGIN SELECT RAISE(ABORT, 'source session records are immutable'); END",
    "CREATE TRIGGER source_session_records_no_delete BEFORE DELETE ON source_session_records BEGIN SELECT RAISE(ABORT, 'source session records cannot be deleted'); END",
)


_RAW_SOURCE_SESSION_CONTEXT_SCHEMA: Final[tuple[str, ...]] = (
    "DROP TRIGGER source_session_records_no_update",
    "DROP TRIGGER source_session_records_no_delete",
    "ALTER TABLE source_session_records RENAME TO source_session_records_legacy",
    """
    CREATE TABLE source_session_records (
        scan_run_id TEXT NOT NULL REFERENCES scan_runs(id),
        source_kind TEXT NOT NULL CHECK (source_kind IN ('log', 'trace')),
        service_name TEXT NOT NULL CHECK (length(service_name) > 0),
        source_id TEXT NOT NULL CHECK (length(source_id) > 0),
        source_timestamp TEXT NOT NULL,
        session_ids_json TEXT NOT NULL CHECK (json_valid(session_ids_json)),
        thread_ids_json TEXT NOT NULL CHECK (json_valid(thread_ids_json)),
        legacy_thread_ids_json TEXT NOT NULL CHECK (json_valid(legacy_thread_ids_json)),
        gen_ai_conversation_ids_json TEXT NOT NULL CHECK (json_valid(gen_ai_conversation_ids_json)),
        terminal_outcome TEXT NOT NULL CHECK (
            terminal_outcome IN ('attributed', 'expected_rejection', 'failed', 'blocked')
        ),
        terminal_reason TEXT NOT NULL CHECK (length(terminal_reason) > 0),
        context_evidence_id TEXT,
        project_id TEXT,
        project_name TEXT,
        project_root TEXT,
        project_kind TEXT CHECK (project_kind IS NULL OR project_kind = 'git'),
        projection_event_id TEXT NOT NULL CHECK (length(projection_event_id) = 64),
        created_at TEXT NOT NULL,
        CHECK (
            (project_id IS NULL AND project_name IS NULL AND project_root IS NULL AND project_kind IS NULL)
            OR (length(project_id) = 64 AND length(project_name) > 0
                AND length(project_root) > 0 AND project_kind = 'git')
        ),
        CHECK (
            (terminal_outcome = 'attributed' AND context_evidence_id IS NOT NULL AND project_id IS NOT NULL)
            OR (terminal_outcome != 'attributed' AND project_id IS NULL)
        ),
        PRIMARY KEY (scan_run_id, source_kind, service_name, source_id)
    ) STRICT, WITHOUT ROWID
    """,
    """
    INSERT INTO source_session_records (
        scan_run_id, source_kind, service_name, source_id, source_timestamp,
        session_ids_json, thread_ids_json, legacy_thread_ids_json,
        gen_ai_conversation_ids_json, terminal_outcome, terminal_reason,
        projection_event_id, created_at
    ) SELECT
        scan_run_id, source_kind, service_name, source_id, source_timestamp,
        session_ids_json, thread_ids_json, legacy_thread_ids_json,
        gen_ai_conversation_ids_json, terminal_outcome, terminal_reason,
        projection_event_id, created_at
    FROM source_session_records_legacy
    """,
    "DROP TABLE source_session_records_legacy",
    "CREATE INDEX source_session_records_outcome_idx ON source_session_records(scan_run_id, terminal_outcome)",
    "CREATE TRIGGER source_session_records_no_update BEFORE UPDATE ON source_session_records BEGIN SELECT RAISE(ABORT, 'source session records are immutable'); END",
    "CREATE TRIGGER source_session_records_no_delete BEFORE DELETE ON source_session_records BEGIN SELECT RAISE(ABORT, 'source session records cannot be deleted'); END",
)


_RAW_SOURCE_SESSION_CURRENT_PROJECTION_SCHEMA: Final[tuple[str, ...]] = (
    """
    CREATE TABLE source_session_current (
        source_kind TEXT NOT NULL CHECK (source_kind IN ('log', 'trace')),
        service_name TEXT NOT NULL CHECK (length(service_name) > 0),
        source_id TEXT NOT NULL CHECK (length(source_id) > 0),
        version INTEGER NOT NULL CHECK (version > 0),
        terminal_outcome TEXT NOT NULL CHECK (
            terminal_outcome IN ('attributed', 'expected_rejection', 'failed', 'blocked')
        ),
        terminal_reason TEXT NOT NULL CHECK (length(terminal_reason) > 0),
        context_evidence_id TEXT,
        project_id TEXT,
        project_name TEXT,
        project_root TEXT,
        project_kind TEXT CHECK (project_kind IS NULL OR project_kind = 'git'),
        projection_event_id TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (source_kind, service_name, source_id),
        CHECK (
            (project_id IS NULL AND project_name IS NULL AND project_root IS NULL AND project_kind IS NULL)
            OR (length(project_id) = 64 AND length(project_name) > 0
                AND length(project_root) > 0 AND project_kind = 'git')
        ),
        CHECK (
            (terminal_outcome = 'attributed' AND context_evidence_id IS NOT NULL AND project_id IS NOT NULL)
            OR (terminal_outcome != 'attributed' AND project_id IS NULL)
        )
    ) STRICT, WITHOUT ROWID
    """,
    """
    CREATE TABLE source_session_current_versions (
        source_kind TEXT NOT NULL,
        service_name TEXT NOT NULL,
        source_id TEXT NOT NULL,
        version INTEGER NOT NULL CHECK (version > 0),
        scan_run_id TEXT NOT NULL REFERENCES scan_runs(id),
        terminal_outcome TEXT NOT NULL,
        terminal_reason TEXT NOT NULL,
        context_evidence_id TEXT,
        project_id TEXT,
        project_name TEXT,
        project_root TEXT,
        project_kind TEXT,
        projection_event_id TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (source_kind, service_name, source_id, version)
    ) STRICT, WITHOUT ROWID
    """,
    "CREATE INDEX source_session_current_versions_scan_idx ON source_session_current_versions(scan_run_id)",
    "CREATE TRIGGER source_session_current_versions_no_update BEFORE UPDATE ON source_session_current_versions BEGIN SELECT RAISE(ABORT, 'source session current versions are immutable'); END",
    "CREATE TRIGGER source_session_current_versions_no_delete BEFORE DELETE ON source_session_current_versions BEGIN SELECT RAISE(ABORT, 'source session current versions cannot be deleted'); END",
)

_SESSION_CONTEXT_SUPERSESSION_SCHEMA: Final[tuple[str, ...]] = (
    """
    CREATE TABLE session_context_event_supersessions (
        original_event_id TEXT PRIMARY KEY REFERENCES session_context_events(event_id),
        replacement_event_id TEXT NOT NULL UNIQUE REFERENCES session_context_events(event_id),
        created_at TEXT NOT NULL,
        CHECK (original_event_id != replacement_event_id)
    ) STRICT
    """,
    "CREATE INDEX session_context_supersessions_replacement_idx ON session_context_event_supersessions(replacement_event_id)",
    "CREATE TRIGGER session_context_event_supersessions_no_update BEFORE UPDATE ON session_context_event_supersessions BEGIN SELECT RAISE(ABORT, 'session context supersessions are immutable'); END",
    "CREATE TRIGGER session_context_event_supersessions_no_delete BEFORE DELETE ON session_context_event_supersessions BEGIN SELECT RAISE(ABORT, 'session context supersessions cannot be deleted'); END",
)

_RAW_SOURCE_SESSION_RECONCILIATION_SCHEMA: Final[tuple[str, ...]] = (
    """
    CREATE TABLE source_session_reconciliation_pending (
        producer TEXT NOT NULL CHECK (
            producer IN ('claude-code', 'codex-cli', 'codex-app-server', 'omp')
        ),
        session_id TEXT NOT NULL CHECK (length(session_id) > 0),
        context_event_id TEXT NOT NULL
            REFERENCES session_context_events(event_id),
        created_at TEXT NOT NULL,
        completed_at TEXT,
        PRIMARY KEY (producer, session_id)
    ) STRICT, WITHOUT ROWID
    """,
    "CREATE INDEX source_session_reconciliation_pending_open_idx "
    "ON source_session_reconciliation_pending(producer, session_id) "
    "WHERE completed_at IS NULL",
)

_RAW_SOURCE_SESSION_CURRENT_PAYLOAD_SCHEMA: Final[tuple[str, ...]] = (
    "ALTER TABLE source_session_current ADD COLUMN source_timestamp TEXT",
    "ALTER TABLE source_session_current ADD COLUMN session_ids_json TEXT",
    "ALTER TABLE source_session_current ADD COLUMN thread_ids_json TEXT",
    "ALTER TABLE source_session_current ADD COLUMN legacy_thread_ids_json TEXT",
    "ALTER TABLE source_session_current ADD COLUMN gen_ai_conversation_ids_json TEXT",
    """
    UPDATE source_session_current AS current
    SET (
        source_timestamp, session_ids_json, thread_ids_json,
        legacy_thread_ids_json, gen_ai_conversation_ids_json
    ) = (
        SELECT records.source_timestamp, records.session_ids_json, records.thread_ids_json,
               records.legacy_thread_ids_json, records.gen_ai_conversation_ids_json
        FROM source_session_records AS records
        WHERE records.source_kind = current.source_kind
          AND records.service_name = current.service_name
          AND records.source_id = current.source_id
        ORDER BY records.created_at DESC, records.scan_run_id DESC
        LIMIT 1
    )
    WHERE EXISTS (
        SELECT 1
        FROM source_session_records AS records
        WHERE records.source_kind = current.source_kind
          AND records.service_name = current.service_name
          AND records.source_id = current.source_id
    )
    """,
)


_RAW_SOURCE_WINDOW_CLAIM_SCHEMA: Final[tuple[str, ...]] = (
    """
    CREATE TABLE raw_source_window_claims (
        source TEXT NOT NULL,
        start_ns INTEGER NOT NULL CHECK (start_ns >= 0),
        end_ns INTEGER NOT NULL CHECK (end_ns > start_ns),
        claimed_at TEXT NOT NULL,
        PRIMARY KEY (source, start_ns, end_ns)
    ) STRICT, WITHOUT ROWID
    """,
    """
    CREATE TABLE raw_source_window_anchors (
        source TEXT PRIMARY KEY,
        start_ns INTEGER NOT NULL CHECK (start_ns >= 0),
        logs_earliest_ns INTEGER NOT NULL CHECK (logs_earliest_ns >= 0),
        traces_earliest_ns INTEGER NOT NULL CHECK (traces_earliest_ns >= 0),
        approved_at TEXT NOT NULL,
        CHECK (start_ns = MAX(logs_earliest_ns, traces_earliest_ns))
    ) STRICT, WITHOUT ROWID
    """,
    """
    CREATE TABLE raw_source_window_completions (
        source TEXT NOT NULL,
        start_ns INTEGER NOT NULL,
        end_ns INTEGER NOT NULL,
        completed_at TEXT NOT NULL,
        PRIMARY KEY (source, start_ns, end_ns),
        FOREIGN KEY (source, start_ns, end_ns)
            REFERENCES raw_source_window_claims(source, start_ns, end_ns)
    ) STRICT, WITHOUT ROWID
    """,
    "CREATE TRIGGER raw_source_window_anchors_no_update BEFORE UPDATE ON raw_source_window_anchors BEGIN SELECT RAISE(ABORT, 'raw source window anchors are immutable'); END",
    "CREATE TRIGGER raw_source_window_anchors_no_delete BEFORE DELETE ON raw_source_window_anchors BEGIN SELECT RAISE(ABORT, 'raw source window anchors cannot be deleted'); END",
    "CREATE TRIGGER raw_source_window_claims_no_update BEFORE UPDATE ON raw_source_window_claims BEGIN SELECT RAISE(ABORT, 'raw source window claims are immutable'); END",
    "CREATE TRIGGER raw_source_window_claims_no_delete BEFORE DELETE ON raw_source_window_claims BEGIN SELECT RAISE(ABORT, 'raw source window claims cannot be deleted'); END",
    "CREATE TRIGGER raw_source_window_completions_no_update BEFORE UPDATE ON raw_source_window_completions BEGIN SELECT RAISE(ABORT, 'raw source window completions are immutable'); END",
    "CREATE TRIGGER raw_source_window_completions_no_delete BEFORE DELETE ON raw_source_window_completions BEGIN SELECT RAISE(ABORT, 'raw source window completions cannot be deleted'); END",
)


_RAW_SOURCE_SESSION_NATIVE_KEY_SCHEMA: Final[tuple[str, ...]] = (
    """
    ALTER TABLE source_session_current
    ADD COLUMN native_producer TEXT CHECK (
        native_producer IS NULL
        OR native_producer IN ('claude-code', 'codex-cli', 'codex-app-server', 'omp')
    )
    """,
    """
    ALTER TABLE source_session_current
    ADD COLUMN native_session_id TEXT CHECK (
        native_session_id IS NULL OR length(native_session_id) > 0
    )
    """,
    """
    UPDATE source_session_current
    SET
        native_producer = CASE
            WHEN service_name = 'claude-code'
                 AND json_array_length(session_ids_json) = 1
                THEN 'claude-code'
            WHEN service_name IN ('codex-cli', 'codex_exec', 'codex_cli_rs')
                 AND json_array_length(thread_ids_json) = 1
                 AND (
                     json_array_length(legacy_thread_ids_json) = 0
                     OR json_extract(thread_ids_json, '$[0]')
                        = json_extract(legacy_thread_ids_json, '$[0]')
                 )
                THEN 'codex-cli'
            WHEN service_name IN ('codex-cli', 'codex_exec', 'codex_cli_rs')
                 AND json_array_length(thread_ids_json) = 0
                 AND json_array_length(legacy_thread_ids_json) = 1
                THEN 'codex-cli'
            WHEN service_name = 'codex-app-server'
                 AND json_array_length(thread_ids_json) = 1
                 AND (
                     json_array_length(legacy_thread_ids_json) = 0
                     OR json_extract(thread_ids_json, '$[0]')
                        = json_extract(legacy_thread_ids_json, '$[0]')
                 )
                THEN 'codex-app-server'
            WHEN service_name = 'codex-app-server'
                 AND json_array_length(thread_ids_json) = 0
                 AND json_array_length(legacy_thread_ids_json) = 1
                THEN 'codex-app-server'
            WHEN source_kind = 'trace'
                 AND service_name IN ('omp', 'oh-my-pi')
                 AND json_array_length(gen_ai_conversation_ids_json) = 1
                THEN 'omp'
        END,
        native_session_id = CASE
            WHEN service_name = 'claude-code'
                 AND json_array_length(session_ids_json) = 1
                THEN json_extract(session_ids_json, '$[0]')
            WHEN service_name IN ('codex-cli', 'codex_exec', 'codex_cli_rs', 'codex-app-server')
                 AND json_array_length(thread_ids_json) = 1
                 AND (
                     json_array_length(legacy_thread_ids_json) = 0
                     OR json_extract(thread_ids_json, '$[0]')
                        = json_extract(legacy_thread_ids_json, '$[0]')
                 )
                THEN json_extract(thread_ids_json, '$[0]')
            WHEN service_name IN ('codex-cli', 'codex_exec', 'codex_cli_rs', 'codex-app-server')
                 AND json_array_length(thread_ids_json) = 0
                 AND json_array_length(legacy_thread_ids_json) = 1
                THEN json_extract(legacy_thread_ids_json, '$[0]')
            WHEN source_kind = 'trace'
                 AND service_name IN ('omp', 'oh-my-pi')
                 AND json_array_length(gen_ai_conversation_ids_json) = 1
                THEN json_extract(gen_ai_conversation_ids_json, '$[0]')
        END
    """,
    """
    CREATE INDEX source_session_current_native_session_idx
    ON source_session_current(native_producer, native_session_id)
    WHERE native_producer IS NOT NULL AND native_session_id IS NOT NULL
    """,
)


MIGRATIONS: Final[tuple[Migration, ...]] = (
    Migration(version=1, name="canonical schema", statements=_CANONICAL_SCHEMA),
    Migration(
        version=2,
        name="temporal replay and canonical rejection identity",
        statements=_TEMPORAL_AND_REJECTION_SCHEMA,
    ),
    Migration(
        version=3,
        name="legacy attribution fact ledger",
        statements=_LEGACY_ATTRIBUTION_FACT_SCHEMA,
    ),
    Migration(
        version=4,
        name="canonical rejection reason vocabulary",
        statements=_REJECTION_REASON_CHECK_SCHEMA,
    ),
    Migration(
        version=5,
        name="legacy attribution delivery attempt ledger",
        statements=_LEGACY_ATTRIBUTION_DELIVERY_LEDGER_SCHEMA,
    ),
    Migration(
        version=6,
        name="codex CLI non-temporal session context",
        statements=_CODEX_SESSION_CONTEXT_SCHEMA,
        requires_foreign_keys_disabled=True,
    ),
    Migration(
        version=7,
        name="session context rejection vocabulary",
        statements=_SESSION_CONTEXT_REJECTION_SCHEMA,
    ),
    Migration(
        version=8,
        name="raw source session conservation",
        statements=_RAW_SOURCE_SESSION_SCHEMA,
    ),
    Migration(
        version=9,
        name="raw source session context evidence",
        statements=_RAW_SOURCE_SESSION_CONTEXT_SCHEMA,
    ),
    Migration(
        version=10,
        name="versioned raw source session current projection",
        statements=_RAW_SOURCE_SESSION_CURRENT_PROJECTION_SCHEMA,
    ),
    Migration(
        version=11,
        name="durable raw source session reconciliation",
        statements=_RAW_SOURCE_SESSION_RECONCILIATION_SCHEMA,
    ),
    Migration(
        version=12,
        name="durable current raw source payload",
        statements=_RAW_SOURCE_SESSION_CURRENT_PAYLOAD_SCHEMA,
    ),
    Migration(
        version=13,
        name="durable immutable raw source windows",
        statements=_RAW_SOURCE_WINDOW_CLAIM_SCHEMA,
    ),
    Migration(
        version=14,
        name="immutable session context supersession",
        statements=_SESSION_CONTEXT_SUPERSESSION_SCHEMA,
    ),
    Migration(
        version=15,
        name="indexed native raw source sessions",
        statements=_RAW_SOURCE_SESSION_NATIVE_KEY_SCHEMA,
    ),
)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _verify_backup(path: Path) -> None:
    try:
        connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        try:
            result = connection.execute("PRAGMA integrity_check").fetchall()
            foreign_key_violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise MigrationError(f"migration backup cannot be opened: {path}") from exc
    if result != [("ok",)]:
        raise MigrationError(f"migration backup failed integrity_check: {result!r}")
    if foreign_key_violations:
        raise MigrationError(f"migration backup violates foreign keys: {foreign_key_violations!r}")


def _backup_before_migration(
    connection: sqlite3.Connection, database_path: Path, version: int
) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = database_path.with_name(
        f"{database_path.name}.migration-v{version:04d}-{stamp}.bak"
    )
    destination = sqlite3.connect(backup_path)
    try:
        connection.backup(destination)
    except sqlite3.Error as exc:
        raise MigrationError(f"backup before migration {version} failed") from exc
    finally:
        destination.close()
    _verify_backup(backup_path)
    return backup_path


def _applied_history(connection: sqlite3.Connection) -> dict[int, tuple[str, str]]:
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'migrations'"
    ).fetchone()
    if exists is None:
        return {}
    try:
        rows = connection.execute(
            "SELECT version, name, checksum FROM migrations ORDER BY version"
        ).fetchall()
    except sqlite3.Error as exc:
        raise MigrationError("migration history is unreadable") from exc
    return {int(row[0]): (str(row[1]), str(row[2])) for row in rows}


def apply_migrations(
    connection: sqlite3.Connection,
    database_path: Path,
    migrations: tuple[Migration, ...] = MIGRATIONS,
) -> tuple[AppliedMigration, ...]:
    """Validate migration history and apply every pending migration safely."""

    if connection.in_transaction:
        raise MigrationError("migrations require a connection with no active transaction")
    versions = [migration.version for migration in migrations]
    if versions != list(range(1, len(migrations) + 1)):
        raise MigrationError("migration versions must be contiguous and begin at one")

    history = _applied_history(connection)
    known = {migration.version: migration for migration in migrations}
    unknown_versions = sorted(set(history) - set(known))
    if unknown_versions:
        raise MigrationError(f"database contains unknown migrations: {unknown_versions}")
    for version, (name, checksum) in history.items():
        migration = known[version]
        if name != migration.name or checksum != migration.checksum:
            raise MigrationError(f"migration {version} does not match canonical history")
    if sorted(history) != list(range(1, max(history, default=0) + 1)):
        raise MigrationError("migration history must be a contiguous applied prefix")
    user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    expected_user_version = max(history, default=0)
    if user_version != expected_user_version:
        raise MigrationError(
            f"database user_version {user_version} does not match migration history "
            f"{expected_user_version}"
        )

    applied: list[AppliedMigration] = []
    for migration in migrations:
        if migration.version in history:
            continue
        backup_path = _backup_before_migration(connection, database_path, migration.version)
        foreign_keys_were_enabled = int(connection.execute("PRAGMA foreign_keys").fetchone()[0])
        if migration.requires_foreign_keys_disabled:
            if foreign_keys_were_enabled != 1:
                raise MigrationError(
                    f"migration {migration.version} requires foreign-key enforcement before rebuild"
                )
            connection.execute("PRAGMA foreign_keys = OFF")
            if int(connection.execute("PRAGMA foreign_keys").fetchone()[0]) != 0:
                raise MigrationError(
                    f"migration {migration.version} could not disable foreign-key enforcement"
                )
        try:
            connection.execute("BEGIN IMMEDIATE")
            for statement in migration.statements:
                connection.execute(statement)
            connection.execute(
                "INSERT INTO migrations(version, name, checksum, applied_at) VALUES (?, ?, ?, ?)",
                (migration.version, migration.name, migration.checksum, _utc_now()),
            )
            connection.execute(f"PRAGMA user_version = {migration.version}")
            foreign_key_violations = connection.execute("PRAGMA foreign_key_check").fetchall()
            if foreign_key_violations:
                raise MigrationError(
                    f"migration {migration.version} violates foreign keys: "
                    f"{foreign_key_violations!r}"
                )
            quick_check = connection.execute("PRAGMA quick_check").fetchall()
            if quick_check != [("ok",)]:
                raise MigrationError(
                    f"migration {migration.version} failed quick_check: {quick_check!r}"
                )
            connection.commit()
        except BaseException as exc:
            if connection.in_transaction:
                connection.rollback()
            if isinstance(exc, MigrationError):
                raise
            if isinstance(exc, sqlite3.Error):
                raise MigrationError(f"migration {migration.version} failed") from exc
            raise
        finally:
            if migration.requires_foreign_keys_disabled:
                connection.execute("PRAGMA foreign_keys = ON")
        if migration.requires_foreign_keys_disabled:
            if int(connection.execute("PRAGMA foreign_keys").fetchone()[0]) != 1:
                raise MigrationError(
                    f"migration {migration.version} could not restore foreign-key enforcement"
                )
            foreign_key_violations = connection.execute("PRAGMA foreign_key_check").fetchall()
            if foreign_key_violations:
                raise MigrationError(
                    f"migration {migration.version} violates foreign keys after rebuild: "
                    f"{foreign_key_violations!r}"
                )
            quick_check = connection.execute("PRAGMA quick_check").fetchall()
            if quick_check != [("ok",)]:
                raise MigrationError(
                    f"migration {migration.version} failed quick_check after rebuild: "
                    f"{quick_check!r}"
                )
        applied.append(
            AppliedMigration(
                version=migration.version,
                name=migration.name,
                checksum=migration.checksum,
                backup_path=backup_path,
            )
        )
    return tuple(applied)
