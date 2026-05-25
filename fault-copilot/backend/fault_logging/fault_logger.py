"""Auto-log fault queries and resolutions into SQLite."""

from __future__ import annotations

import csv
import json
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator, Optional

_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from config import LOG_DB_PATH  # noqa: E402

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS fault_resolutions (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp               TEXT    NOT NULL,
    fault_input             TEXT    NOT NULL,
    manual_ids              TEXT    NOT NULL,
    operator_level          TEXT    NOT NULL,
    steps_generated         INTEGER NOT NULL,
    confidence              TEXT    NOT NULL,
    manual_references       TEXT    NOT NULL,
    resolution_accepted     INTEGER,
    time_to_resolve_seconds INTEGER,
    plant_id                TEXT,
    notes                   TEXT
)
"""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


@contextmanager
def _db() -> Generator[sqlite3.Connection, None, None]:
    """Open a connection, commit on success, rollback on error, always close."""
    LOG_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(LOG_DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    for field in ("manual_ids", "manual_references"):
        raw = d.get(field)
        if isinstance(raw, str):
            try:
                d[field] = json.loads(raw)
            except json.JSONDecodeError:
                pass
    return d


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def init_db() -> None:
    """Create the fault_resolutions table if it does not already exist."""
    with _db() as conn:
        conn.execute(_CREATE_TABLE_SQL)


def log_fault_start(
    fault_input: str,
    manual_ids: list[str],
    operator_level: str,
    confidence: str,
    steps_generated: int,
    manual_references: list[str],
    plant_id: Optional[str] = None,
) -> int:
    """Insert a new fault record and return its auto-assigned id."""
    ts = datetime.now(timezone.utc).isoformat()
    with _db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO fault_resolutions
                (timestamp, fault_input, manual_ids, operator_level,
                 steps_generated, confidence, manual_references, plant_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ts,
                fault_input,
                json.dumps(manual_ids),
                operator_level,
                steps_generated,
                confidence,
                json.dumps(manual_references),
                plant_id,
            ),
        )
        return cursor.lastrowid


def log_resolution_complete(
    fault_id: int,
    accepted: bool,
    time_seconds: int,
    notes: Optional[str] = None,
) -> None:
    """Update a fault record with the operator's outcome."""
    with _db() as conn:
        conn.execute(
            """
            UPDATE fault_resolutions
            SET resolution_accepted     = ?,
                time_to_resolve_seconds = ?,
                notes                   = ?
            WHERE id = ?
            """,
            (1 if accepted else 0, time_seconds, notes, fault_id),
        )


def get_recent_faults(
    limit: int = 20,
    plant_id: Optional[str] = None,
) -> list[dict]:
    """Return the most recent fault records, newest first."""
    with _db() as conn:
        if plant_id is not None:
            cursor = conn.execute(
                """
                SELECT * FROM fault_resolutions
                WHERE plant_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (plant_id, limit),
            )
        else:
            cursor = conn.execute(
                """
                SELECT * FROM fault_resolutions
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (limit,),
            )
        rows = cursor.fetchall()
    return [_row_to_dict(row) for row in rows]


def get_fault_frequency() -> list[dict]:
    """Return each unique fault grouped by count, most frequent first."""
    with _db() as conn:
        cursor = conn.execute(
            """
            SELECT
                fault_input,
                COUNT(*)                         AS count,
                MAX(timestamp)                   AS last_seen,
                AVG(time_to_resolve_seconds)     AS avg_resolve_seconds,
                SUM(CASE WHEN resolution_accepted = 1 THEN 1 ELSE 0 END) AS times_accepted
            FROM fault_resolutions
            GROUP BY fault_input
            ORDER BY count DESC
            """
        )
        rows = cursor.fetchall()
    return [dict(row) for row in rows]


def export_to_csv(
    output_path: str,
    plant_id: Optional[str] = None,
) -> int:
    """Write all fault records to a CSV file. Returns number of rows written."""
    with _db() as conn:
        if plant_id is not None:
            cursor = conn.execute(
                """
                SELECT * FROM fault_resolutions
                WHERE plant_id = ?
                ORDER BY timestamp
                """,
                (plant_id,),
            )
        else:
            cursor = conn.execute(
                "SELECT * FROM fault_resolutions ORDER BY timestamp"
            )
        columns = [desc[0] for desc in cursor.description]
        rows = cursor.fetchall()

    if not rows:
        return 0

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(columns)
        for row in rows:
            writer.writerow(list(row))

    return len(rows)


# ---------------------------------------------------------------------------
# Auto-initialize on import
# ---------------------------------------------------------------------------

try:
    init_db()
except Exception as _exc:  # noqa: BLE001
    print(
        f"[error] fault_logger: failed to initialise DB at {LOG_DB_PATH}: {_exc}",
        file=sys.stderr,
    )
