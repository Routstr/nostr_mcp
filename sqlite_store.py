from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple


JOB_STATUSES: Tuple[str, ...] = (
    "queued",
    "running",
    "success",
    "failed",
    "cancelled",
)


def get_connection(database_path: str) -> sqlite3.Connection:
    """Return a SQLite connection with sane defaults.

    - Enables foreign key enforcement
    - Sets row factory to sqlite3.Row for dict-like access
    """
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON;")
    return connection


def initialize_database(connection: sqlite3.Connection) -> None:
    """Create tables and indexes if they do not exist.

    Schema:
      - npubs: one row per npub profile and (optionally) last known window
      - events: posts for an npub
      - event_thread_links: many-to-many self relation among events
      - jobs: async job tracking with (npub, since, till, status)
    """
    cursor = connection.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS npubs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            npub            TEXT NOT NULL UNIQUE,
            name            TEXT,
            profile_pic     TEXT,
            since           INTEGER,  -- unix timestamp; optional last stored window start
            till            INTEGER,  -- unix timestamp; optional last stored window end
            task_id         TEXT, -- batch identifier: pubkey_since_till
            created_at      INTEGER DEFAULT (strftime('%s','now')),
            updated_at      INTEGER DEFAULT (strftime('%s','now'))
        );
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            npub_id             INTEGER NOT NULL,
            event_id            TEXT NOT NULL,
            event_content       TEXT,
            context_content     TEXT,
            context_summary     TEXT,
            timestamp           INTEGER, -- unix timestamp of the event
            relevancy_score     REAL,
            reason_for_score    TEXT,
            task_id             TEXT, -- batch identifier: pubkey_since_till
            created_at          INTEGER DEFAULT (strftime('%s','now')),
            updated_at          INTEGER DEFAULT (strftime('%s','now')),
            FOREIGN KEY (npub_id) REFERENCES npubs(id) ON DELETE CASCADE
        );
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS event_thread_links (
            event_id            INTEGER NOT NULL,
            related_event_id    INTEGER NOT NULL,
            PRIMARY KEY (event_id, related_event_id),
            FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE CASCADE,
            FOREIGN KEY (related_event_id) REFERENCES events(id) ON DELETE CASCADE
        );
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS jobs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            npub_id         INTEGER NOT NULL,
            since           INTEGER NOT NULL,
            till            INTEGER NOT NULL,
            status          TEXT NOT NULL,
            last_error      TEXT,
            created_at      INTEGER DEFAULT (strftime('%s','now')),
            updated_at      INTEGER DEFAULT (strftime('%s','now')),
            started_at      INTEGER,
            finished_at     INTEGER,
            FOREIGN KEY (npub_id) REFERENCES npubs(id) ON DELETE CASCADE,
            CHECK (status IN ('queued','running','success','failed','cancelled'))
        );
        """
    )

    # API keys table
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS api_keys (
            api_id          TEXT PRIMARY KEY,
            api_key         TEXT NOT NULL,
            balance         REAL NOT NULL DEFAULT 0
        );
        """
    )

    # Helpful indexes
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_events_npub_timestamp
        ON events(npub_id, timestamp DESC);
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_jobs_status
        ON jobs(status);
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_jobs_npub
        ON jobs(npub_id);
        """
    )

    connection.commit()


def initialize_api_keys_database(connection: sqlite3.Connection) -> None:
    """Create only the api_keys table if it does not exist.

    Use this when maintaining API keys in a separate database file.
    """
    cursor = connection.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS api_keys (
            api_id          TEXT PRIMARY KEY,
            api_key         TEXT NOT NULL,
            balance         REAL NOT NULL DEFAULT 0
        );
        """
    )
    connection.commit()


def _touch_updated_at(connection: sqlite3.Connection, table: str, row_id: int) -> None:
    connection.execute(
        f"UPDATE {table} SET updated_at = strftime('%s','now') WHERE id = ?",
        (row_id,),
    )


def upsert_npub(
    connection: sqlite3.Connection,
    *,
    npub: str,
    name: Optional[str] = None,
    profile_pic: Optional[str] = None,
    since: Optional[int] = None,
    till: Optional[int] = None,
    task_id: Optional[str] = None,
) -> int:
    """Insert or update a npub row and return its id.

    If the npub exists, updates the provided non-null fields.
    """
    cursor = connection.cursor()
    cursor.execute("SELECT id, name, profile_pic, since, till, task_id FROM npubs WHERE npub = ?", (npub,))
    row = cursor.fetchone()

    if row is None:
        cursor.execute(
            """
            INSERT INTO npubs (npub, name, profile_pic, since, till, task_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (npub, name, profile_pic, since, till, task_id),
        )
        npub_id = cursor.lastrowid
        connection.commit()
        return npub_id

    npub_id = int(row["id"])  # update path

    # Build dynamic update only for provided fields
    updates: List[str] = []
    params: List[Any] = []
    if name is not None:
        updates.append("name = ?")
        params.append(name)
    if profile_pic is not None:
        updates.append("profile_pic = ?")
        params.append(profile_pic)
    if since is not None:
        updates.append("since = ?")
        params.append(since)
    if till is not None:
        updates.append("till = ?")
        params.append(till)
    if task_id is not None:
        updates.append("task_id = ?")
        params.append(task_id)

    if updates:
        set_clause = ", ".join(updates)
        params.extend([npub_id])
        cursor.execute(f"UPDATE npubs SET {set_clause} WHERE id = ?", params)
        _touch_updated_at(connection, "npubs", npub_id)
        connection.commit()

    return npub_id


def _get_event_row_id_by_event_id(connection: sqlite3.Connection, event_id: str) -> Optional[int]:
    cur = connection.execute("SELECT id FROM events WHERE event_id = ?", (event_id,))
    row = cur.fetchone()
    return int(row["id"]) if row else None


def upsert_event(
    connection: sqlite3.Connection,
    *,
    npub_id: int,
    event_id: str,
    event_content: Optional[str] = None,
    context_content: Optional[str] = None,
    context_summary: Optional[str] = None,
    timestamp: Optional[int] = None,
    relevancy_score: Optional[float] = None,
    reason_for_score: Optional[str] = None,
    task_id: Optional[str] = None,
) -> int:
    """Insert or update an event row by its external event_id, return row id.

    On update, only non-null fields will be updated.
    """
    cursor = connection.cursor()
    cursor.execute("SELECT id FROM events WHERE event_id = ?", (event_id,))
    row = cursor.fetchone()

    if row is None:
        cursor.execute(
            """
            INSERT INTO events (
                npub_id, event_id, event_content, context_content, context_summary,
                timestamp, relevancy_score, reason_for_score, task_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                npub_id,
                event_id,
                event_content,
                context_content,
                context_summary,
                timestamp,
                relevancy_score,
                reason_for_score,
                task_id,
            ),
        )
        event_row_id = cursor.lastrowid
        connection.commit()
        return event_row_id

    event_row_id = int(row["id"])  # update path

    updates: List[str] = []
    params: List[Any] = []
    if event_content is not None:
        updates.append("event_content = ?")
        params.append(event_content)
    if context_content is not None:
        updates.append("context_content = ?")
        params.append(context_content)
    if context_summary is not None:
        updates.append("context_summary = ?")
        params.append(context_summary)
    if timestamp is not None:
        updates.append("timestamp = ?")
        params.append(timestamp)
    if relevancy_score is not None:
        updates.append("relevancy_score = ?")
        params.append(relevancy_score)
    if reason_for_score is not None:
        updates.append("reason_for_score = ?")
        params.append(reason_for_score)
    if updates:
        set_clause = ", ".join(updates)
        params.append(event_row_id)
        cursor.execute(f"UPDATE events SET {set_clause} WHERE id = ?", params)
        _touch_updated_at(connection, "events", event_row_id)
        connection.commit()

    return event_row_id


def set_event_thread_links(
    connection: sqlite3.Connection,
    *,
    parent_event_row_id: int,
    related_event_row_ids: Iterable[int],
) -> None:
    """Replace thread links for a given event.

    Links are stored as (event_id -> related_event_id). This function clears
    existing links for the event and inserts the provided set.
    """
    cursor = connection.cursor()
    cursor.execute("DELETE FROM event_thread_links WHERE event_id = ?", (parent_event_row_id,))
    cursor.executemany(
        "INSERT OR IGNORE INTO event_thread_links (event_id, related_event_id) VALUES (?, ?)",
        [(parent_event_row_id, rid) for rid in related_event_row_ids if rid != parent_event_row_id],
    )
    connection.commit()


def store_npub_record_with_events(
    connection: sqlite3.Connection,
    *,
    record: Dict[str, Any],
    task_id: Optional[str] = None,
) -> int:
    """Store a single npub record (and its events) shaped like the YAML schema.

    Expected keys in record:
      - npub: str
      - name: Optional[str]
      - profile_pic: Optional[str]
      - since: Optional[int]
      - till: Optional[int]
      - events: Optional[List[Dict[str, Any]]]
    """
    npub_value = str(record.get("npub"))
    npub_id = upsert_npub(
        connection,
        npub=npub_value,
        name=record.get("name"),
        profile_pic=record.get("profile_pic"),
        since=record.get("since"),
        till=record.get("till"),
        task_id=task_id,
    )

    events = record.get("events") or []
    event_id_to_row_id: Dict[str, int] = {}

    for event in events:
        row_id = upsert_event(
            connection,
            npub_id=npub_id,
            event_id=str(event.get("event_id")),
            event_content=event.get("event_content"),
            context_content=event.get("context_content"),
            context_summary=event.get("context_summary"),
            timestamp=event.get("timestamp"),
            relevancy_score=event.get("relevancy_score"),
            reason_for_score=event.get("reason_for_score"),
            task_id=task_id,
        )
        event_id_to_row_id[str(event.get("event_id"))] = row_id

    # Second pass for thread links so we resolve row ids
    for event in events:
        parent_row_id = event_id_to_row_id.get(str(event.get("event_id")))
        if parent_row_id is None:
            continue
        related_external_ids: List[str] = list(event.get("events_in_thread") or [])
        related_row_ids: List[int] = []
        for external_id in related_external_ids:
            row_id = event_id_to_row_id.get(str(external_id))
            if row_id is None:
                found = _get_event_row_id_by_event_id(connection, str(external_id))
                if found is not None:
                    row_id = found
            if row_id is not None:
                related_row_ids.append(row_id)

        if related_row_ids:
            set_event_thread_links(
                connection,
                parent_event_row_id=parent_row_id,
                related_event_row_ids=related_row_ids,
            )

    return npub_id


def enqueue_job(
    connection: sqlite3.Connection,
    *,
    npub: str,
    since: int,
    till: int,
    status: str = "queued",
) -> int:
    """Create a job row for an npub and return job id."""
    if status not in JOB_STATUSES:
        raise ValueError(f"Invalid status '{status}'. Must be one of: {JOB_STATUSES}")

    npub_id = upsert_npub(connection, npub=npub)
    cursor = connection.cursor()
    cursor.execute(
        """
        INSERT INTO jobs (npub_id, since, till, status)
        VALUES (?, ?, ?, ?)
        """,
        (npub_id, since, till, status),
    )
    job_id = cursor.lastrowid
    connection.commit()
    return int(job_id)


def update_job_status(
    connection: sqlite3.Connection,
    *,
    job_id: int,
    status: str,
    error: Optional[str] = None,
    started_at: Optional[int] = None,
    finished_at: Optional[int] = None,
) -> None:
    """Update status and timestamps for a job."""
    if status not in JOB_STATUSES:
        raise ValueError(f"Invalid status '{status}'. Must be one of: {JOB_STATUSES}")

    updates: List[str] = ["status = ?", "updated_at = strftime('%s','now')"]
    params: List[Any] = [status]

    if error is not None:
        updates.append("last_error = ?")
        params.append(error)

    if started_at is not None:
        updates.append("started_at = ?")
        params.append(started_at)

    if finished_at is not None:
        updates.append("finished_at = ?")
        params.append(finished_at)

    params.append(job_id)
    set_clause = ", ".join(updates)
    connection.execute(f"UPDATE jobs SET {set_clause} WHERE id = ?", params)
    connection.commit()


def get_or_create_npub_id(connection: sqlite3.Connection, npub: str) -> int:
    """Utility to fetch an existing npub id or create a new one quickly."""
    return upsert_npub(connection, npub=npub)


def bulk_store_records(
    connection: sqlite3.Connection,
    records: Iterable[Dict[str, Any]],
) -> List[int]:
    """Store multiple npub records with events. Returns npub ids."""
    ids: List[int] = []
    for record in records:
        ids.append(store_npub_record_with_events(connection, record=record))
    return ids


def store_collected_data(
    connection: sqlite3.Connection,
    *,
    collected: Dict[str, Any],
) -> None:
    """Persist the output of fetch_and_store.collect_all_data_for_npub.

    Schema of 'collected':
      {
        'input': { 'npub': str, 'hex': str, 'since': int },
        'outbox_relays': List[str],
        'following_count': int,
        'following': List[{ 'hex': str, 'npub': str, 'relay_hint': str }],
        'summaries_by_hex': { hex: { 'npub': str, 'name': str, 'profile_pic': str, 'events': [...] } }
      }
    """
    input_block = collected.get("input") or {}
    root_npub: str = str(input_block.get("npub") or "")
    root_hex: str = str(input_block.get("hex") or "")
    since_val = input_block.get("since")
    till_val = input_block.get("till")

    # Build task_id: hex_since_till (falls back to npub if hex missing)
    base_pub = root_hex or root_npub
    task_id = f"{base_pub}_{since_val}_{till_val}"

    # Ensure root npub exists and set last window
    if root_npub:
        upsert_npub(connection, npub=root_npub, since=since_val, till=till_val, task_id=task_id)

    # Store summarized events per followee
    summaries_by_hex = collected.get("summaries_by_hex") or {}
    if isinstance(summaries_by_hex, dict):
        for _, followee_record in summaries_by_hex.items():
            if not isinstance(followee_record, dict):
                continue
            # Attach since window for informational purposes
            followee_payload = dict(followee_record)
            followee_payload["since"] = since_val
            followee_payload["till"] = till_val
            store_npub_record_with_events(connection, record=followee_payload, task_id=task_id)

    connection.commit()


def add_api_key(
    connection: sqlite3.Connection,
    *,
    api_id: str,
    api_key: str,
    balance: float = 0.0,
) -> None:
    """Insert or update an API key row.

    If the api_id already exists, updates api_key and balance.
    """
    connection.execute(
        """
        INSERT INTO api_keys (api_id, api_key, balance)
        VALUES (?, ?, ?)
        ON CONFLICT(api_id) DO UPDATE SET
            api_key = excluded.api_key,
            balance = excluded.balance
        """,
        (api_id, api_key, balance),
    )
    connection.commit()


def delete_api_key(
    connection: sqlite3.Connection,
    *,
    api_id: Optional[str] = None,
    api_key: Optional[str] = None,
) -> int:
    """Delete API key rows by api_id and/or api_key.

    Returns the number of deleted rows.
    """
    if api_id is None and api_key is None:
        raise ValueError("Provide api_id or api_key to delete")

    clauses: List[str] = []
    params: List[Any] = []
    if api_id is not None:
        clauses.append("api_id = ?")
        params.append(api_id)
    if api_key is not None:
        clauses.append("api_key = ?")
        params.append(api_key)

    where_clause = " AND ".join(clauses)
    cur = connection.execute(f"DELETE FROM api_keys WHERE {where_clause}", params)
    connection.commit()
    return int(cur.rowcount if cur.rowcount is not None else 0)


def fetch_api_key(
    connection: sqlite3.Connection,
    *,
    api_id: Optional[str] = None,
    api_key: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Fetch a single API key record by api_id or api_key.

    Returns a dict like { 'api_id': str, 'api_key': str, 'balance': float }
    or None if not found.
    """
    if api_id is None and api_key is None:
        raise ValueError("Provide api_id or api_key to fetch")

    clauses: List[str] = []
    params: List[Any] = []
    if api_id is not None:
        clauses.append("api_id = ?")
        params.append(api_id)
    if api_key is not None:
        clauses.append("api_key = ?")
        params.append(api_key)

    where_clause = " AND ".join(clauses)
    cur = connection.execute(
        f"SELECT api_id, api_key, balance FROM api_keys WHERE {where_clause} LIMIT 1",
        params,
    )
    row = cur.fetchone()
    if not row:
        return None
    return {
        "api_id": row["api_id"],
        "api_key": row["api_key"],
        "balance": float(row["balance"]),
    }


if __name__ == "__main__":
    # Minimal CLI: initialize a database file
    import argparse

    parser = argparse.ArgumentParser(description="Initialize and manage Nostr SQLite storage.")
    parser.add_argument("db", help="goose.db")
    args = parser.parse_args()

    conn = get_connection(args.db)
    initialize_database(conn)
    print(f"Initialized database at: {args.db}")

