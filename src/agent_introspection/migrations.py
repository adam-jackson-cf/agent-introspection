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
