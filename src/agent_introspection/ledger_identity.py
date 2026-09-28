"""Connected local ledger identity for backups and canonical OTLP authority."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

DATABASE_IDENTITY_ATTRIBUTE = "introspection.database.identity"


def database_identity(connection: sqlite3.Connection, *, database_path: Path | None = None) -> str:
    """Return a stable, non-public identity for the main on-disk database."""
    row = connection.execute("SELECT file FROM pragma_database_list WHERE name = 'main'").fetchone()
    if row is None or not row[0]:
        raise RuntimeError("backup observation requires an on-disk main database")
    source_path = Path(str(row[0])).resolve(strict=False)
    if (
        database_path is not None
        and database_path.expanduser().resolve(strict=False) != source_path
    ):
        raise ValueError("backup observation connection does not match database path")
    return hashlib.sha256(str(source_path).encode("utf-8")).hexdigest()
