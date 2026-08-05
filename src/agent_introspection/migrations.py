"""Numbered, transactional, fail-closed SQLite migrations."""

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
        identity_kind TEXT NOT NULL CHECK (identity_kind IN ('git', 'non_git')),
        canonical_path TEXT NOT NULL,
        git_common_dir TEXT,
        created_at TEXT NOT NULL,
        UNIQUE (identity_kind, canonical_path)
    ) STRICT
    """,
    """
    CREATE TABLE project_aliases (
        id TEXT PRIMARY KEY,
        project_identity_id TEXT NOT NULL REFERENCES project_identities(id),
        alias_path TEXT NOT NULL UNIQUE,
        reason TEXT NOT NULL,
        approved_at TEXT NOT NULL
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
        detector_id TEXT NOT NULL,
        detector_version INTEGER NOT NULL CHECK (detector_version > 0),
        first_seen_ns INTEGER NOT NULL CHECK (first_seen_ns >= 0),
        last_seen_ns INTEGER NOT NULL CHECK (last_seen_ns >= first_seen_ns),
        occurrence_count INTEGER NOT NULL CHECK (occurrence_count > 0),
        canonical_task_count INTEGER NOT NULL CHECK (canonical_task_count >= 0),
        local_day_count INTEGER NOT NULL CHECK (local_day_count > 0),
        entity_version INTEGER NOT NULL CHECK (entity_version > 0),
        updated_at TEXT NOT NULL
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
        kind TEXT NOT NULL CHECK (kind IN ('classification', 'proposal')),
        requested_model TEXT NOT NULL,
        requested_effort TEXT NOT NULL,
        ordered_candidate_ids_json TEXT NOT NULL CHECK (json_valid(ordered_candidate_ids_json)),
        payload_hash TEXT NOT NULL CHECK (length(payload_hash) = 64),
        byte_count INTEGER NOT NULL CHECK (byte_count >= 0),
        reserved_model_budget INTEGER NOT NULL CHECK (reserved_model_budget >= 0),
        status TEXT NOT NULL CHECK (status IN ('exported', 'imported')),
        created_at TEXT NOT NULL,
        imported_at TEXT
    ) STRICT
    """,
    "CREATE INDEX review_sessions_batch_idx ON review_sessions(batch_id, kind, created_at)",
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
            "project_aliases",
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
            "project_aliases",
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
      OR OLD.kind IS NOT NEW.kind
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

MIGRATIONS: Final[tuple[Migration, ...]] = (
    Migration(version=1, name="initial schema", statements=_INITIAL_SCHEMA),
    Migration(
        version=2,
        name="allow zero current-window finding counts",
        statements=(
            """
            CREATE TABLE new_findings (
                id TEXT PRIMARY KEY,
                fingerprint TEXT NOT NULL UNIQUE CHECK (length(fingerprint) = 64),
                category TEXT NOT NULL,
                project_identity_id TEXT REFERENCES project_identities(id),
                trend_state TEXT NOT NULL CHECK (
                    trend_state IN ('isolated', 'emerging', 'actionable', 'dormant')
                ),
                detector_id TEXT NOT NULL,
                detector_version INTEGER NOT NULL CHECK (detector_version > 0),
                first_seen_ns INTEGER NOT NULL CHECK (first_seen_ns >= 0),
                last_seen_ns INTEGER NOT NULL CHECK (last_seen_ns >= first_seen_ns),
                occurrence_count INTEGER NOT NULL CHECK (occurrence_count >= 0),
                canonical_task_count INTEGER NOT NULL CHECK (canonical_task_count >= 0),
                local_day_count INTEGER NOT NULL CHECK (local_day_count >= 0),
                entity_version INTEGER NOT NULL CHECK (entity_version > 0),
                updated_at TEXT NOT NULL
            ) STRICT
            """,
            """
            INSERT INTO new_findings (
                id, fingerprint, category, project_identity_id, trend_state, detector_id,
                detector_version, first_seen_ns, last_seen_ns, occurrence_count,
                canonical_task_count, local_day_count, entity_version, updated_at
            )
            SELECT
                id, fingerprint, category, project_identity_id, trend_state, detector_id,
                detector_version, first_seen_ns, last_seen_ns, occurrence_count,
                canonical_task_count, local_day_count, entity_version, updated_at
            FROM findings
            """,
            "DROP TABLE findings",
            "ALTER TABLE new_findings RENAME TO findings",
            """
            CREATE TRIGGER findings_no_delete
            BEFORE DELETE ON findings BEGIN
                SELECT RAISE(ABORT, 'findings cannot be deleted');
            END
            """,
        ),
        requires_foreign_keys_disabled=True,
    ),
    Migration(
        version=3,
        name="add immutable analysis generations",
        statements=(
            """
            CREATE TABLE analysis_generations (
                id TEXT PRIMARY KEY,
                ordinal INTEGER NOT NULL UNIQUE CHECK (ordinal > 0),
                window_start_ns INTEGER NOT NULL CHECK (window_start_ns >= 0),
                window_end_ns INTEGER NOT NULL CHECK (window_end_ns > window_start_ns),
                source_contract_fingerprint TEXT NOT NULL
                    CHECK (length(source_contract_fingerprint) = 64),
                detector_contract_hash TEXT NOT NULL CHECK (length(detector_contract_hash) = 64),
                normalization_contract_hash TEXT NOT NULL
                    CHECK (length(normalization_contract_hash) = 64),
                semantic_hash TEXT NOT NULL CHECK (length(semantic_hash) = 64),
                created_at TEXT NOT NULL
            ) STRICT
            """,
            """
            CREATE TABLE analysis_generation_event_links (
                generation_id TEXT NOT NULL REFERENCES analysis_generations(id),
                event_id TEXT NOT NULL REFERENCES otlp_outbox(event_id),
                role TEXT NOT NULL CHECK (role IN ('projection', 'activation')),
                PRIMARY KEY (generation_id, event_id, role),
                UNIQUE (event_id)
            ) STRICT, WITHOUT ROWID
            """,
            """
            CREATE TABLE analysis_generation_activations (
                generation_id TEXT NOT NULL REFERENCES analysis_generations(id),
                activation_event_id TEXT NOT NULL REFERENCES otlp_outbox(event_id),
                activated_at TEXT NOT NULL,
                PRIMARY KEY (generation_id),
                UNIQUE (activation_event_id),
                UNIQUE (generation_id, activation_event_id)
            ) STRICT, WITHOUT ROWID
            """,
            """
            CREATE TABLE analysis_generation_current (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                generation_id TEXT NOT NULL,
                activation_event_id TEXT NOT NULL,
                activated_at TEXT NOT NULL,
                FOREIGN KEY (generation_id, activation_event_id)
                    REFERENCES analysis_generation_activations(generation_id, activation_event_id)
            ) STRICT
            """,
            """
            CREATE TRIGGER analysis_generations_no_update
            BEFORE UPDATE ON analysis_generations BEGIN
                SELECT RAISE(ABORT, 'analysis generations are immutable');
            END
            """,
            """
            CREATE TRIGGER analysis_generations_no_delete
            BEFORE DELETE ON analysis_generations BEGIN
                SELECT RAISE(ABORT, 'analysis generations are immutable');
            END
            """,
            """
            CREATE TRIGGER analysis_generation_event_links_no_update
            BEFORE UPDATE ON analysis_generation_event_links BEGIN
                SELECT RAISE(ABORT, 'analysis generation event links are immutable');
            END
            """,
            """
            CREATE TRIGGER analysis_generation_event_links_no_delete
            BEFORE DELETE ON analysis_generation_event_links BEGIN
                SELECT RAISE(ABORT, 'analysis generation event links are immutable');
            END
            """,
            """
            CREATE TRIGGER analysis_generation_activation_link_guard
            BEFORE INSERT ON analysis_generation_event_links
            WHEN NEW.role = 'activation'
             AND EXISTS (
                SELECT 1
                FROM analysis_generation_event_links link
                JOIN otlp_outbox event ON event.event_id = link.event_id
                WHERE link.generation_id = NEW.generation_id
                  AND link.role = 'projection'
                  AND event.status != 'delivered'
             )
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'analysis generation projections must be delivered before activation'
                );
            END
            """,
            """
            CREATE TRIGGER analysis_generation_activations_no_update
            BEFORE UPDATE ON analysis_generation_activations BEGIN
                SELECT RAISE(ABORT, 'analysis generation activations are immutable');
            END
            """,
            """
            CREATE TRIGGER analysis_generation_activations_no_delete
            BEFORE DELETE ON analysis_generation_activations BEGIN
                SELECT RAISE(ABORT, 'analysis generation activations are immutable');
            END
            """,
            """
            CREATE TRIGGER analysis_generation_activation_evidence_guard
            BEFORE INSERT ON analysis_generation_activations
            WHEN NOT EXISTS (
                SELECT 1
                FROM analysis_generation_event_links link
                JOIN otlp_outbox event ON event.event_id = link.event_id
                WHERE link.generation_id = NEW.generation_id
                  AND link.event_id = NEW.activation_event_id
                  AND link.role = 'activation'
                  AND event.status = 'delivered'
            )
             OR EXISTS (
                SELECT 1
                FROM analysis_generation_event_links link
                JOIN otlp_outbox event ON event.event_id = link.event_id
                WHERE link.generation_id = NEW.generation_id
                  AND link.role = 'projection'
                  AND event.status != 'delivered'
             )
            BEGIN
                SELECT RAISE(ABORT, 'analysis generation activation requires delivered evidence');
            END
            """,
            """
            CREATE TRIGGER analysis_generation_current_insert_guard
            BEFORE INSERT ON analysis_generation_current
            WHEN NOT EXISTS (
                SELECT 1
                FROM analysis_generation_activations activation
                JOIN otlp_outbox event ON event.event_id = activation.activation_event_id
                WHERE activation.generation_id = NEW.generation_id
                  AND activation.activation_event_id = NEW.activation_event_id
                  AND event.status = 'delivered'
            )
             OR EXISTS (
                SELECT 1
                FROM analysis_generation_event_links link
                JOIN otlp_outbox event ON event.event_id = link.event_id
                WHERE link.generation_id = NEW.generation_id
                  AND link.role = 'projection'
                  AND event.status != 'delivered'
             )
            BEGIN
                SELECT RAISE(ABORT, 'current analysis generation requires delivered activation');
            END
            """,
            """
            CREATE TRIGGER analysis_generation_current_update_guard
            BEFORE UPDATE ON analysis_generation_current
            WHEN NOT EXISTS (
                SELECT 1
                FROM analysis_generation_activations activation
                JOIN otlp_outbox event ON event.event_id = activation.activation_event_id
                WHERE activation.generation_id = NEW.generation_id
                  AND activation.activation_event_id = NEW.activation_event_id
                  AND event.status = 'delivered'
            )
             OR EXISTS (
                SELECT 1
                FROM analysis_generation_event_links link
                JOIN otlp_outbox event ON event.event_id = link.event_id
                WHERE link.generation_id = NEW.generation_id
                  AND link.role = 'projection'
                  AND event.status != 'delivered'
             )
            BEGIN
                SELECT RAISE(ABORT, 'current analysis generation requires delivered activation');
            END
            """,
            """
            CREATE TRIGGER analysis_generation_current_no_delete
            BEFORE DELETE ON analysis_generation_current BEGIN
                SELECT RAISE(ABORT, 'current analysis generation cannot be deleted');
            END
            """,
        ),
    ),
    Migration(
        version=4,
        name="add immutable review lifecycle telemetry",
        statements=(
            """
            CREATE TABLE new_review_sessions (
                id TEXT PRIMARY KEY,
                batch_id TEXT NOT NULL,
                nonce TEXT NOT NULL UNIQUE,
                schema_version INTEGER NOT NULL CHECK (schema_version > 0),
                purpose TEXT NOT NULL CHECK (
                    purpose IN ('capability_probe', 'classification', 'proposal')
                ),
                requested_model TEXT NOT NULL,
                requested_effort TEXT NOT NULL,
                ordered_candidate_ids_json TEXT NOT NULL
                    CHECK (json_valid(ordered_candidate_ids_json)),
                payload_hash TEXT NOT NULL CHECK (length(payload_hash) = 64),
                byte_count INTEGER NOT NULL CHECK (byte_count >= 0),
                reserved_model_budget INTEGER NOT NULL CHECK (reserved_model_budget >= 0),
                status TEXT NOT NULL CHECK (status IN ('exported', 'imported')),
                entity_version INTEGER NOT NULL CHECK (entity_version > 0),
                created_at TEXT NOT NULL,
                imported_at TEXT
            ) STRICT
            """,
            """
            INSERT INTO new_review_sessions (
                id, batch_id, nonce, schema_version, purpose, requested_model, requested_effort,
                ordered_candidate_ids_json, payload_hash, byte_count, reserved_model_budget, status,
                entity_version, created_at, imported_at
            )
            SELECT
                id, batch_id, nonce, schema_version, kind, requested_model, requested_effort,
                ordered_candidate_ids_json, payload_hash, byte_count, reserved_model_budget, status,
                CASE status WHEN 'imported' THEN 2 ELSE 1 END, created_at, imported_at
            FROM review_sessions
            """,
            "DROP TABLE review_sessions",
            "ALTER TABLE new_review_sessions RENAME TO review_sessions",
            """
            CREATE INDEX review_sessions_batch_idx
            ON review_sessions(batch_id, purpose, created_at)
            """,
            """
            ALTER TABLE model_runs
            ADD COLUMN total_tokens INTEGER CHECK (total_tokens IS NULL OR total_tokens >= 0)
            """,
            """
            ALTER TABLE model_runs
            ADD COLUMN token_availability TEXT NOT NULL DEFAULT 'unavailable' CHECK (
                token_availability IN ('complete', 'partial', 'unavailable')
            )
            """,
            """
            CREATE TABLE review_session_events (
                id TEXT PRIMARY KEY,
                review_session_id TEXT NOT NULL REFERENCES review_sessions(id),
                entity_version INTEGER NOT NULL CHECK (entity_version > 0),
                status TEXT NOT NULL CHECK (status IN ('exported', 'imported')),
                review_run_id TEXT REFERENCES model_runs(id),
                created_at TEXT NOT NULL,
                UNIQUE (review_session_id, entity_version),
                CHECK (
                    (status = 'exported' AND review_run_id IS NULL)
                    OR (status = 'imported' AND review_run_id IS NOT NULL)
                )
            ) STRICT
            """,
            """
            CREATE TABLE review_activity_snapshots (
                id TEXT PRIMARY KEY,
                entity_version INTEGER NOT NULL UNIQUE CHECK (entity_version > 0),
                trigger_kind TEXT NOT NULL CHECK (trigger_kind IN ('review_session', 'scan_run')),
                trigger_id TEXT NOT NULL,
                trigger_version INTEGER NOT NULL CHECK (trigger_version > 0),
                classification_session_count INTEGER NOT NULL
                    CHECK (classification_session_count >= 0),
                proposal_session_count INTEGER NOT NULL CHECK (proposal_session_count >= 0),
                classification_result_count INTEGER NOT NULL
                    CHECK (classification_result_count >= 0),
                proposal_result_count INTEGER NOT NULL CHECK (proposal_result_count >= 0),
                created_at TEXT NOT NULL,
                UNIQUE (trigger_kind, trigger_id, trigger_version)
            ) STRICT
            """,
            """
            CREATE TRIGGER review_sessions_no_delete
            BEFORE DELETE ON review_sessions BEGIN
                SELECT RAISE(ABORT, 'review_sessions cannot be deleted');
            END
            """,
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
            CREATE TRIGGER review_session_events_no_update
            BEFORE UPDATE ON review_session_events BEGIN
                SELECT RAISE(ABORT, 'review session events are immutable');
            END
            """,
            """
            CREATE TRIGGER review_session_events_no_delete
            BEFORE DELETE ON review_session_events BEGIN
                SELECT RAISE(ABORT, 'review session events are immutable');
            END
            """,
            """
            CREATE TRIGGER review_activity_snapshots_no_update
            BEFORE UPDATE ON review_activity_snapshots BEGIN
                SELECT RAISE(ABORT, 'review activity snapshots are immutable');
            END
            """,
            """
            CREATE TRIGGER review_activity_snapshots_no_delete
            BEFORE DELETE ON review_activity_snapshots BEGIN
                SELECT RAISE(ABORT, 'review activity snapshots are immutable');
            END
            """,
        ),
        requires_foreign_keys_disabled=True,
    ),
    Migration(
        version=5,
        name="remove review activity telemetry",
        statements=(
            "DROP TABLE review_activity_snapshots",
            "DROP TABLE review_session_events",
        ),
    ),
    Migration(
        version=6,
        name="purge retired review telemetry",
        statements=(
            "DROP TRIGGER otlp_outbox_no_delete",
            """
            DELETE FROM otlp_outbox
            WHERE json_extract(payload_json, '$."event.name"') IN (
                'introspection.review.activity_snapshot',
                'introspection.review.session_changed'
            )
            """,
            """
            CREATE TRIGGER otlp_outbox_no_delete
            BEFORE DELETE ON otlp_outbox BEGIN
                SELECT RAISE(ABORT, 'otlp_outbox cannot be deleted');
            END
            """,
        ),
    ),
    Migration(
        version=7,
        name="add source-backed project attribution evidence",
        statements=(
            """
            CREATE TABLE thread_project_evidence (
                id TEXT PRIMARY KEY CHECK (length(id) = 64),
                thread_id TEXT NOT NULL,
                source_trace_id TEXT NOT NULL,
                source_timestamp_ns INTEGER NOT NULL CHECK (source_timestamp_ns >= 0),
                source_contract_fingerprint TEXT NOT NULL
                    CHECK (length(source_contract_fingerprint) = 64),
                attribution_contract_version INTEGER NOT NULL CHECK (
                    attribution_contract_version > 0
                ),
                project_identity_id TEXT NOT NULL REFERENCES project_identities(id),
                created_at TEXT NOT NULL,
                UNIQUE(
                    thread_id, source_trace_id, source_timestamp_ns,
                    source_contract_fingerprint, attribution_contract_version,
                    project_identity_id
                )
            ) STRICT
            """,
            """
            CREATE INDEX thread_project_evidence_window_idx
            ON thread_project_evidence(thread_id, source_timestamp_ns, project_identity_id)
            """,
            """
            CREATE TRIGGER thread_project_evidence_no_update
            BEFORE UPDATE ON thread_project_evidence BEGIN
                SELECT RAISE(ABORT, 'thread project evidence is immutable');
            END
            """,
            """
            CREATE TRIGGER thread_project_evidence_no_delete
            BEFORE DELETE ON thread_project_evidence BEGIN
                SELECT RAISE(ABORT, 'thread project evidence is immutable');
            END
            """,
            "ALTER TABLE analysis_generations ADD COLUMN fact_set_id TEXT",
            """
            CREATE TABLE attribution_reanalysis_fact_sets (
                id TEXT PRIMARY KEY,
                window_start_ns INTEGER NOT NULL CHECK (window_start_ns >= 0),
                window_end_ns INTEGER NOT NULL CHECK (window_end_ns > window_start_ns),
                source_contract_fingerprint TEXT NOT NULL
                    CHECK (length(source_contract_fingerprint) = 64),
                semantic_hash TEXT NOT NULL CHECK (length(semantic_hash) = 64),
                created_at TEXT NOT NULL
            ) STRICT
            """,
            """
            CREATE TABLE attribution_reanalysis_facts (
                id TEXT PRIMARY KEY CHECK (length(id) = 64),
                fact_set_id TEXT NOT NULL REFERENCES attribution_reanalysis_fact_sets(id),
                fact_kind TEXT NOT NULL CHECK (
                    fact_kind IN ('observation', 'evidence', 'membership', 'finding', 'trend')
                ),
                payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
                created_at TEXT NOT NULL,
                UNIQUE (fact_set_id, fact_kind, id)
            ) STRICT
            """,
            """
            CREATE INDEX attribution_reanalysis_facts_kind_idx
            ON attribution_reanalysis_facts(fact_set_id, fact_kind)
            """,
            """
            CREATE TRIGGER attribution_reanalysis_fact_sets_no_update
            BEFORE UPDATE ON attribution_reanalysis_fact_sets BEGIN
                SELECT RAISE(ABORT, 'attribution reanalysis fact sets are immutable');
            END
            """,
            """
            CREATE TRIGGER attribution_reanalysis_fact_sets_no_delete
            BEFORE DELETE ON attribution_reanalysis_fact_sets BEGIN
                SELECT RAISE(ABORT, 'attribution reanalysis fact sets are immutable');
            END
            """,
            """
            CREATE TRIGGER attribution_reanalysis_facts_no_update
            BEFORE UPDATE ON attribution_reanalysis_facts BEGIN
                SELECT RAISE(ABORT, 'attribution reanalysis facts are immutable');
            END
            """,
            """
            CREATE TRIGGER attribution_reanalysis_facts_no_delete
            BEFORE DELETE ON attribution_reanalysis_facts BEGIN
                SELECT RAISE(ABORT, 'attribution reanalysis facts are immutable');
            END
            """,
        ),
    ),
    Migration(
        version=8,
        name="add source project metadata",
        statements=(
            "ALTER TABLE project_identities ADD COLUMN canonical_name TEXT",
            "DROP TRIGGER thread_project_evidence_no_update",
            "DROP TRIGGER thread_project_evidence_no_delete",
            "DROP TABLE thread_project_evidence",
            "DROP TABLE project_aliases",
        ),
    ),
    Migration(
        version=9,
        name="add immutable session context ledger",
        statements=(
            """
            CREATE TABLE session_context_events (
                event_id TEXT PRIMARY KEY CHECK (length(event_id) = 64),
                producer TEXT NOT NULL CHECK (producer IN ('claude-code', 'codex-app-server')),
                session_id TEXT NOT NULL,
                event_type TEXT NOT NULL CHECK (event_type IN ('session_start', 'session_end')),
                occurred_at TEXT NOT NULL,
                project_id TEXT NOT NULL CHECK (length(project_id) = 64),
                project_name TEXT NOT NULL,
                project_root TEXT NOT NULL,
                project_kind TEXT NOT NULL CHECK (project_kind IN ('git', 'non_git'))
            ) STRICT
            """,
            """
            CREATE TABLE session_context_intervals (
                event_id TEXT PRIMARY KEY REFERENCES session_context_events(event_id),
                producer TEXT NOT NULL CHECK (producer IN ('claude-code', 'codex-app-server')),
                session_id TEXT NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                end_event_id TEXT UNIQUE REFERENCES session_context_events(event_id),
                project_id TEXT NOT NULL CHECK (length(project_id) = 64),
                project_name TEXT NOT NULL,
                project_root TEXT NOT NULL,
                project_kind TEXT NOT NULL CHECK (project_kind IN ('git', 'non_git')),
                CHECK (ended_at IS NULL OR ended_at >= started_at)
            ) STRICT
            """,
            """
            CREATE UNIQUE INDEX session_context_open_interval_idx
            ON session_context_intervals(producer, session_id) WHERE ended_at IS NULL
            """,
            """
            CREATE INDEX session_context_correlation_idx
            ON session_context_intervals(session_id, started_at, ended_at)
            """,
            """
            CREATE TRIGGER session_context_events_no_update
            BEFORE UPDATE ON session_context_events BEGIN
                SELECT RAISE(ABORT, 'session context events are immutable');
            END
            """,
            """
            CREATE TRIGGER session_context_events_no_delete
            BEFORE DELETE ON session_context_events BEGIN
                SELECT RAISE(ABORT, 'session context events are immutable');
            END
            """,
            """
            CREATE TRIGGER session_context_intervals_guard_update
            BEFORE UPDATE ON session_context_intervals
            WHEN OLD.event_id IS NOT NEW.event_id
              OR OLD.producer IS NOT NEW.producer
              OR OLD.session_id IS NOT NEW.session_id
              OR OLD.started_at IS NOT NEW.started_at
              OR OLD.project_id IS NOT NEW.project_id
              OR OLD.project_name IS NOT NEW.project_name
              OR OLD.project_root IS NOT NEW.project_root
              OR OLD.project_kind IS NOT NEW.project_kind
              OR OLD.ended_at IS NOT NULL
              OR NEW.ended_at IS NULL
              OR NEW.end_event_id IS NULL
            BEGIN
                SELECT RAISE(ABORT, 'session context interval history is immutable');
            END
            """,
            """
            CREATE TRIGGER session_context_intervals_no_delete
            BEFORE DELETE ON session_context_intervals BEGIN
                SELECT RAISE(ABORT, 'session context intervals are immutable');
            END
            """,
        ),
    ),
    Migration(
        version=10,
        name="expand session context producers",
        requires_foreign_keys_disabled=True,
        statements=(
            "DROP TRIGGER session_context_events_no_update",
            "DROP TRIGGER session_context_events_no_delete",
            "DROP TRIGGER session_context_intervals_guard_update",
            "DROP TRIGGER session_context_intervals_no_delete",
            "DROP INDEX session_context_open_interval_idx",
            "DROP INDEX session_context_correlation_idx",
            "ALTER TABLE session_context_intervals RENAME TO session_context_intervals_old",
            "ALTER TABLE session_context_events RENAME TO session_context_events_old",
            """
            CREATE TABLE session_context_events (
                event_id TEXT PRIMARY KEY CHECK (length(event_id) = 64),
                producer TEXT NOT NULL CHECK (
                    producer IN ('claude-code', 'codex-cli', 'codex-app-server', 'omp')
                ),
                session_id TEXT NOT NULL,
                event_type TEXT NOT NULL CHECK (event_type IN ('session_start', 'session_end')),
                occurred_at TEXT NOT NULL,
                project_id TEXT NOT NULL CHECK (length(project_id) = 64),
                project_name TEXT NOT NULL,
                project_root TEXT NOT NULL,
                project_kind TEXT NOT NULL CHECK (project_kind IN ('git', 'non_git'))
            ) STRICT
            """,
            """
            CREATE TABLE session_context_intervals (
                event_id TEXT PRIMARY KEY REFERENCES session_context_events(event_id),
                producer TEXT NOT NULL CHECK (
                    producer IN ('claude-code', 'codex-cli', 'codex-app-server', 'omp')
                ),
                session_id TEXT NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                end_event_id TEXT UNIQUE REFERENCES session_context_events(event_id),
                project_id TEXT NOT NULL CHECK (length(project_id) = 64),
                project_name TEXT NOT NULL,
                project_root TEXT NOT NULL,
                project_kind TEXT NOT NULL CHECK (project_kind IN ('git', 'non_git')),
                CHECK (ended_at IS NULL OR ended_at >= started_at)
            ) STRICT
            """,
            "INSERT INTO session_context_events SELECT * FROM session_context_events_old",
            "INSERT INTO session_context_intervals SELECT * FROM session_context_intervals_old",
            "DROP TABLE session_context_intervals_old",
            "DROP TABLE session_context_events_old",
            """
            CREATE UNIQUE INDEX session_context_open_interval_idx
            ON session_context_intervals(producer, session_id) WHERE ended_at IS NULL
            """,
            """
            CREATE INDEX session_context_correlation_idx
            ON session_context_intervals(session_id, started_at, ended_at)
            """,
            """
            CREATE TRIGGER session_context_events_no_update
            BEFORE UPDATE ON session_context_events BEGIN
                SELECT RAISE(ABORT, 'session context events are immutable');
            END
            """,
            """
            CREATE TRIGGER session_context_events_no_delete
            BEFORE DELETE ON session_context_events BEGIN
                SELECT RAISE(ABORT, 'session context events are immutable');
            END
            """,
            """
            CREATE TRIGGER session_context_intervals_guard_update
            BEFORE UPDATE ON session_context_intervals
            WHEN OLD.event_id IS NOT NEW.event_id
              OR OLD.producer IS NOT NEW.producer
              OR OLD.session_id IS NOT NEW.session_id
              OR OLD.started_at IS NOT NEW.started_at
              OR OLD.project_id IS NOT NEW.project_id
              OR OLD.project_name IS NOT NEW.project_name
              OR OLD.project_root IS NOT NEW.project_root
              OR OLD.project_kind IS NOT NEW.project_kind
              OR OLD.ended_at IS NOT NULL
              OR NEW.ended_at IS NULL
              OR NEW.end_event_id IS NULL
            BEGIN
                SELECT RAISE(ABORT, 'session context interval history is immutable');
            END
            """,
            """
            CREATE TRIGGER session_context_intervals_no_delete
            BEFORE DELETE ON session_context_intervals BEGIN
                SELECT RAISE(ABORT, 'session context interval history is immutable');
            END
            """,
        ),
    ),
    Migration(
        version=11,
        name="add immutable project evidence intervals",
        statements=(
            """
            CREATE TABLE project_evidence_intervals (
                evidence_id TEXT PRIMARY KEY CHECK (length(evidence_id) = 64),
                producer TEXT NOT NULL CHECK (
                    producer IN ('codex-cli', 'codex-app-server')
                ),
                conversation_id TEXT NOT NULL,
                started_at_ns INTEGER NOT NULL CHECK (started_at_ns >= 0),
                ended_at_ns INTEGER NOT NULL CHECK (ended_at_ns >= started_at_ns),
                first_log_id TEXT NOT NULL,
                last_log_id TEXT NOT NULL,
                anchor_count INTEGER NOT NULL CHECK (anchor_count > 0),
                project_id TEXT NOT NULL REFERENCES project_identities(id),
                project_name TEXT NOT NULL,
                project_root TEXT NOT NULL,
                project_kind TEXT NOT NULL CHECK (project_kind = 'git'),
                attribution_method TEXT NOT NULL CHECK (
                    attribution_method = 'git_validated_tool_workspace_interval'
                ),
                created_at TEXT NOT NULL,
                UNIQUE (
                    producer, conversation_id, started_at_ns, ended_at_ns,
                    project_id, first_log_id, last_log_id
                )
            ) STRICT
            """,
            """
            CREATE INDEX project_evidence_intervals_correlation_idx
            ON project_evidence_intervals(producer, conversation_id, started_at_ns, ended_at_ns)
            """,
            """
            CREATE TRIGGER project_evidence_intervals_no_update
            BEFORE UPDATE ON project_evidence_intervals BEGIN
                SELECT RAISE(ABORT, 'project evidence intervals are immutable');
            END
            """,
            """
            CREATE TRIGGER project_evidence_intervals_no_delete
            BEFORE DELETE ON project_evidence_intervals BEGIN
                SELECT RAISE(ABORT, 'project evidence intervals are immutable');
            END
            """,
        ),
    ),
    Migration(
        version=12,
        name="add workspace changed session context event",
        requires_foreign_keys_disabled=True,
        statements=(
            "DROP TRIGGER session_context_events_no_update",
            "DROP TRIGGER session_context_events_no_delete",
            "DROP TRIGGER session_context_intervals_guard_update",
            "DROP TRIGGER session_context_intervals_no_delete",
            "DROP INDEX session_context_open_interval_idx",
            "DROP INDEX session_context_correlation_idx",
            "ALTER TABLE session_context_intervals RENAME TO session_context_intervals_old",
            "ALTER TABLE session_context_events RENAME TO session_context_events_old",
            """
            CREATE TABLE session_context_events (
                event_id TEXT PRIMARY KEY CHECK (length(event_id) = 64),
                producer TEXT NOT NULL CHECK (
                    producer IN ('claude-code', 'codex-cli', 'codex-app-server', 'omp')
                ),
                session_id TEXT NOT NULL,
                event_type TEXT NOT NULL CHECK (
                    event_type IN ('session_start', 'workspace_changed', 'session_end')
                ),
                occurred_at TEXT NOT NULL,
                project_id TEXT NOT NULL CHECK (length(project_id) = 64),
                project_name TEXT NOT NULL,
                project_root TEXT NOT NULL,
                project_kind TEXT NOT NULL CHECK (project_kind IN ('git', 'non_git'))
            ) STRICT
            """,
            """
            CREATE TABLE session_context_intervals (
                event_id TEXT PRIMARY KEY REFERENCES session_context_events(event_id),
                producer TEXT NOT NULL CHECK (
                    producer IN ('claude-code', 'codex-cli', 'codex-app-server', 'omp')
                ),
                session_id TEXT NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                end_event_id TEXT UNIQUE REFERENCES session_context_events(event_id),
                project_id TEXT NOT NULL CHECK (length(project_id) = 64),
                project_name TEXT NOT NULL,
                project_root TEXT NOT NULL,
                project_kind TEXT NOT NULL CHECK (project_kind IN ('git', 'non_git')),
                CHECK (ended_at IS NULL OR ended_at >= started_at)
            ) STRICT
            """,
            "INSERT INTO session_context_events SELECT * FROM session_context_events_old",
            "INSERT INTO session_context_intervals SELECT * FROM session_context_intervals_old",
            "DROP TABLE session_context_intervals_old",
            "DROP TABLE session_context_events_old",
            """
            CREATE UNIQUE INDEX session_context_open_interval_idx
            ON session_context_intervals(producer, session_id) WHERE ended_at IS NULL
            """,
            """
            CREATE INDEX session_context_correlation_idx
            ON session_context_intervals(session_id, started_at, ended_at)
            """,
            """
            CREATE TRIGGER session_context_events_no_update
            BEFORE UPDATE ON session_context_events BEGIN
                SELECT RAISE(ABORT, 'session context events are immutable');
            END
            """,
            """
            CREATE TRIGGER session_context_events_no_delete
            BEFORE DELETE ON session_context_events BEGIN
                SELECT RAISE(ABORT, 'session context events are immutable');
            END
            """,
            """
            CREATE TRIGGER session_context_intervals_guard_update
            BEFORE UPDATE ON session_context_intervals
            WHEN OLD.event_id IS NOT NEW.event_id
              OR OLD.producer IS NOT NEW.producer
              OR OLD.session_id IS NOT NEW.session_id
              OR OLD.started_at IS NOT NEW.started_at
              OR OLD.project_id IS NOT NEW.project_id
              OR OLD.project_name IS NOT NEW.project_name
              OR OLD.project_root IS NOT NEW.project_root
              OR OLD.project_kind IS NOT NEW.project_kind
              OR OLD.ended_at IS NOT NULL
              OR NEW.ended_at IS NULL
              OR NEW.end_event_id IS NULL
            BEGIN
                SELECT RAISE(ABORT, 'session context interval history is immutable');
            END
            """,
            """
            CREATE TRIGGER session_context_intervals_no_delete
            BEFORE DELETE ON session_context_intervals BEGIN
                SELECT RAISE(ABORT, 'session context intervals are immutable');
            END
            """,
        ),
    ),
    Migration(
        version=13,
        name="add canonical activity ingestion ledger",
        statements=(
            """
            CREATE TABLE canonical_activities (
                id TEXT PRIMARY KEY,
                producer TEXT NOT NULL CHECK (
                    producer IN ('claude-code', 'codex-cli', 'codex-app-server', 'omp')
                ),
                producer_surface TEXT NOT NULL CHECK (length(producer_surface) > 0),
                correlation_id TEXT NOT NULL CHECK (length(correlation_id) > 0),
                source_started_at_ns INTEGER NOT NULL CHECK (source_started_at_ns >= 0),
                source_ended_at_ns INTEGER NOT NULL CHECK (
                    source_ended_at_ns >= source_started_at_ns
                ),
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
                UNIQUE (
                    detector_id, detector_version, normalization_version, source_membership_hash
                )
            ) STRICT
            """,
            """
            CREATE INDEX canonical_activities_source_membership_idx
            ON canonical_activities(source_membership_hash, source_started_at_ns)
            """,
            """
            CREATE INDEX canonical_activities_correlation_window_idx
            ON canonical_activities(producer, correlation_id, source_started_at_ns)
            """,
            """
            CREATE TABLE canonical_activity_versions (
                activity_id TEXT NOT NULL REFERENCES canonical_activities(id),
                version INTEGER NOT NULL CHECK (version > 0),
                attribution_state TEXT NOT NULL CHECK (
                    attribution_state IN ('resolved', 'unresolved')
                ),
                project_identity_id TEXT REFERENCES project_identities(id),
                attribution_method TEXT NOT NULL CHECK (length(attribution_method) > 0),
                attribution_evidence_id TEXT,
                reason_code TEXT CHECK (
                    reason_code IN (
                        'missing_correlation_id', 'conflicting_correlation_id', 'missing_workspace',
                        'invalid_workspace', 'non_git_workspace', 'git_resolution_failed',
                        'invalid_timestamp', 'invalid_transition', 'duplicate_conflict',
                        'out_of_order_event'
                    )
                ),
                created_at TEXT NOT NULL,
                PRIMARY KEY (activity_id, version),
                CHECK (
                    (attribution_state = 'resolved'
                        AND project_identity_id IS NOT NULL
                        AND reason_code IS NULL)
                    OR
                    (attribution_state = 'unresolved'
                        AND project_identity_id IS NULL)
                )
            ) STRICT, WITHOUT ROWID
            """,
            """
            CREATE UNIQUE INDEX canonical_activity_versions_attribution_tuple_idx
            ON canonical_activity_versions(
                activity_id,
                attribution_state,
                ifnull(project_identity_id, ''),
                attribution_method,
                ifnull(attribution_evidence_id, ''),
                ifnull(reason_code, '')
            )
            """,
            """
            CREATE INDEX canonical_activity_versions_latest_idx
            ON canonical_activity_versions(activity_id, version DESC)
            """,
            """
            CREATE TRIGGER canonical_activities_no_update
            BEFORE UPDATE ON canonical_activities BEGIN
                SELECT RAISE(ABORT, 'canonical activities are immutable');
            END
            """,
            """
            CREATE TRIGGER canonical_activities_no_delete
            BEFORE DELETE ON canonical_activities BEGIN
                SELECT RAISE(ABORT, 'canonical activities cannot be deleted');
            END
            """,
            """
            CREATE TRIGGER canonical_activity_versions_monotonic
            BEFORE INSERT ON canonical_activity_versions
            WHEN NEW.version != COALESCE(
                (SELECT MAX(version) + 1 FROM canonical_activity_versions
                 WHERE activity_id = NEW.activity_id),
                1
            )
            BEGIN
                SELECT RAISE(ABORT, 'canonical activity versions must be monotonic');
            END
            """,
            """
            CREATE TRIGGER canonical_activity_versions_no_update
            BEFORE UPDATE ON canonical_activity_versions BEGIN
                SELECT RAISE(ABORT, 'canonical activity versions are immutable');
            END
            """,
            """
            CREATE TRIGGER canonical_activity_versions_no_delete
            BEFORE DELETE ON canonical_activity_versions BEGIN
                SELECT RAISE(ABORT, 'canonical activity versions cannot be deleted');
            END
            """,
            """
            CREATE TABLE canonical_recomputation_schedule (
                activity_id TEXT NOT NULL,
                activity_version INTEGER NOT NULL,
                aggregate_kind TEXT NOT NULL CHECK (aggregate_kind IN ('findings', 'trends')),
                scheduled_at TEXT NOT NULL,
                completed_at TEXT,
                PRIMARY KEY (activity_id, activity_version, aggregate_kind),
                FOREIGN KEY (activity_id, activity_version)
                    REFERENCES canonical_activity_versions(activity_id, version),
                CHECK (completed_at IS NULL OR completed_at >= scheduled_at)
            ) STRICT, WITHOUT ROWID
            """,
            """
            CREATE INDEX canonical_recomputation_pending_idx
            ON canonical_recomputation_schedule(aggregate_kind, scheduled_at)
            WHERE completed_at IS NULL
            """,
            """
            CREATE TABLE canonical_activity_outbox_evidence (
                activity_id TEXT NOT NULL,
                activity_version INTEGER NOT NULL,
                payload_schema_version INTEGER NOT NULL CHECK (payload_schema_version > 0),
                event_name TEXT NOT NULL CHECK (length(event_name) > 0),
                event_id TEXT NOT NULL UNIQUE REFERENCES otlp_outbox(event_id)
                    CHECK (length(event_id) = 64),
                created_at TEXT NOT NULL,
                PRIMARY KEY (
                    activity_id, activity_version, payload_schema_version, event_name
                ),
                FOREIGN KEY (activity_id, activity_version)
                    REFERENCES canonical_activity_versions(activity_id, version)
            ) STRICT, WITHOUT ROWID
            """,
            """
            CREATE TRIGGER canonical_activity_outbox_evidence_no_update
            BEFORE UPDATE ON canonical_activity_outbox_evidence BEGIN
                SELECT RAISE(ABORT, 'canonical activity outbox evidence is immutable');
            END
            """,
            """
            CREATE TRIGGER canonical_activity_outbox_evidence_no_delete
            BEFORE DELETE ON canonical_activity_outbox_evidence BEGIN
                SELECT RAISE(ABORT, 'canonical activity outbox evidence cannot be deleted');
            END
            """,
            """
            CREATE TABLE canonical_rejections (
                id TEXT PRIMARY KEY,
                producer TEXT NOT NULL CHECK (
                    producer IN ('claude-code', 'codex-cli', 'codex-app-server', 'omp')
                ),
                producer_surface TEXT NOT NULL CHECK (length(producer_surface) > 0),
                correlation_id TEXT CHECK (
                    correlation_id IS NULL OR length(correlation_id) > 0
                ),
                lifecycle_event TEXT NOT NULL CHECK (
                    lifecycle_event IN ('session_start', 'workspace_changed', 'session_end')
                ),
                occurred_at TEXT NOT NULL,
                reason_code TEXT NOT NULL CHECK (
                    reason_code IN (
                        'missing_correlation_id', 'conflicting_correlation_id', 'missing_workspace',
                        'invalid_workspace', 'non_git_workspace', 'git_resolution_failed',
                        'invalid_timestamp', 'invalid_transition', 'duplicate_conflict',
                        'out_of_order_event'
                    )
                ),
                source_adapter TEXT NOT NULL CHECK (length(source_adapter) > 0),
                created_at TEXT NOT NULL,
                UNIQUE (
                    producer, producer_surface, correlation_id, lifecycle_event,
                    occurred_at, reason_code, source_adapter
                )
            ) STRICT
            """,
            """
            CREATE TRIGGER canonical_rejections_no_update
            BEFORE UPDATE ON canonical_rejections BEGIN
                SELECT RAISE(ABORT, 'canonical rejections are immutable');
            END
            """,
            """
            CREATE TRIGGER canonical_rejections_no_delete
            BEFORE DELETE ON canonical_rejections BEGIN
                SELECT RAISE(ABORT, 'canonical rejections cannot be deleted');
            END
            """,
        ),
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
