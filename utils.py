#!/usr/bin/env python3
import json
import asyncio
from typing import Optional, List, Dict, Any
import logging

logger = logging.getLogger("nostr-mcp")

# Import pynostr for pubkey conversion
try:
    from pynostr.key import PublicKey
    PYNOSTR_AVAILABLE = True
except ImportError:
    PYNOSTR_AVAILABLE = False
    logger.warning("pynostr not available, npub/hex conversion will be disabled")

def parse_e_tags(event_tags: List[List[str]]) -> Dict[str, List[str]]:
    """Parse e tags from an event to extract root and reply references.
    
    Args:
        event_tags: List of tag arrays from a nostr event
        
    Returns:
        Dict with 'root' and 'reply' lists containing event IDs
    """
    e_tags = {'root': [], 'reply': []}
    
    for tag in event_tags:
        if len(tag) >= 2 and tag[0] == 'e':
            event_id = tag[1]
            # Check if there's a marker (root/reply) in the tag
            if len(tag) >= 4:
                marker = tag[3]
                if marker == 'root':
                    e_tags['root'].append(event_id)
                elif marker == 'reply':
                    e_tags['reply'].append(event_id)
            else:
                # If no marker, treat as general reference
                e_tags['reply'].append(event_id)
    
    return e_tags


def _extract_outbox_relays_from_kind10002(event: Dict[str, Any], include_onion: bool = False) -> List[str]:
    """Parse a kind 10002 event to extract outbox (write) relays.

    NIP-65 specifies 'r' tags optionally annotated with 'read'/'write'. If no
    mode is given, we include the relay as a general relay.
    """
    relays: List[str] = []
    for tag in event.get("tags", []):
        if not isinstance(tag, list) or not tag:
            continue
        if tag[0] != "r":
            continue
        # tag format: ['r', 'wss://relay', 'read'|'write'|...]
        url = tag[1] if len(tag) > 1 else None
        mode = tag[2] if len(tag) > 2 else None
        if not url:
            continue
        # Filter out .onion relays unless explicitly included
        if not include_onion:
            try:
                if ".onion" in str(url).lower():
                    continue
            except Exception:
                pass
        if mode is None or str(mode).lower() == "write":
            relays.append(url)
    # Deduplicate while preserving order
    seen: set[str] = set()
    ordered_unique: List[str] = []
    for r in relays:
        if r not in seen:
            seen.add(r)
            ordered_unique.append(r)
    return ordered_unique

async def fetch_event_context(
    event_id: str,
    fetch_events_func,
    relays: Optional[List[str]] = None,
    max_depth: int = 50
) -> Dict[str, Any]:
    """Fetch the full context of a Nostr event by following the reply chain to the root.
    
    Args:
        event_id: The event ID to analyze
        fetch_events_func: Function to fetch events (should be fetch_nostr_events)
        relays: Optional list of relays to use
        max_depth: Maximum depth to prevent infinite loops
        
    Returns:
        Dict containing the event context, thread events, and summary
    """
    try:
        # Fetch the initial event
        result = await fetch_events_func(ids=[event_id], limit=1, relays=relays)
        parsed_result = json.loads(result)
        
        if not parsed_result['success'] or not parsed_result['events']:
            return {
                'success': False,
                'error': f'Event {event_id} not found',
                'nevent': event_id,
                'context': '',
                'no_of_events': 0,
                'events': []
            }
        
        initial_event = parsed_result['events'][0]
        thread_events = [initial_event]
        current_event = initial_event
        depth = 0
        
        # Follow the reply chain backwards to the root
        while depth < max_depth:
            e_tags = parse_e_tags(current_event['tags'])
            
            # If we have a reply tag, follow it
            if e_tags['reply']:
                # Get the most recent reply tag (last one)
                reply_id = e_tags['reply'][-1]
                
                # Fetch the replied-to event
                reply_result = await fetch_events_func(ids=[reply_id], limit=1, relays=relays)
                reply_parsed = json.loads(reply_result)
                
                if reply_parsed['success'] and reply_parsed['events']:
                    reply_event = reply_parsed['events'][0]
                    thread_events.append(reply_event)
                    current_event = reply_event
                    depth += 1
                    
                    # Check if this event only has root tags (we've reached the conversation start)
                    reply_e_tags = parse_e_tags(reply_event['tags'])
                    if not reply_e_tags['reply'] and reply_e_tags['root']:
                        # This event only replies to root, fetch the root event
                        root_id = reply_e_tags['root'][0]
                        root_result = await fetch_events_func(ids=[root_id], limit=1, relays=relays)
                        root_parsed = json.loads(root_result)
                        
                        if root_parsed['success'] and root_parsed['events']:
                            root_event = root_parsed['events'][0]
                            thread_events.append(root_event)
                        break
                    elif not reply_e_tags['reply'] and not reply_e_tags['root']:
                        # This is the root event itself
                        break
                else:
                    # Can't fetch the reply, stop here
                    break
            
            elif e_tags['root']:
                # Only root tag, fetch the root event
                root_id = e_tags['root'][0]
                root_result = await fetch_events_func(ids=[root_id], limit=1, relays=relays)
                root_parsed = json.loads(root_result)
                
                if root_parsed['success'] and root_parsed['events']:
                    root_event = root_parsed['events'][0]
                    thread_events.append(root_event)
                break
            else:
                # No e tags, this is likely the root event itself
                break
        
        # Sort events by timestamp (oldest first for chronological order)
        thread_events.sort(key=lambda e: e['created_at'])
        
        # Create formatted event list with indices
        formatted_events = []
        for i, event in enumerate(thread_events):
            formatted_events.append({
                'nevent': event['id'],
                'event_index': i + 1,
                'event_content': event['content'],
                'timestamp': event['created_at'],
                'pubkey': event['pubkey'],
                'kind': event['kind']
            })
        
        # Generate context summary
        context_parts = []
        if len(thread_events) == 1:
            context_parts.append("This is a standalone event with no conversation context.")
        else:
            context_parts.append(f"This is part of a conversation thread with {len(thread_events)} events.")
            
            # Find the original event (root)
            root_event = thread_events[0]
            context_parts.append(f"The conversation started with: \"{root_event['content'][:100]}...\"")
            
            if len(thread_events) > 2:
                context_parts.append(f"The thread contains {len(thread_events) - 1} replies.")
        
        context = " ".join(context_parts)
        
        return {
            'success': True,
            'nevent': event_id,
            'context': context,
            'no_of_events': len(thread_events),
            'events': formatted_events,
            'thread_events': thread_events  # Raw events for further processing
        }
        
    except Exception as e:
        logger.error(f"Error fetching event context: {str(e)}")
        return {
            'success': False,
            'error': str(e),
            'nevent': event_id,
            'context': '',
            'no_of_events': 0,
            'events': []
        }

def summarize_thread_context(context_result: Dict[str, Any]) -> str:
    """Generate a human-readable summary of the thread context.
    
    Args:
        context_result: Result from fetch_event_context
        
    Returns:
        Formatted summary string
    """
    if not context_result['success']:
        return f"Failed to fetch context: {context_result.get('error', 'Unknown error')}"
    
    events = context_result['events']
    if not events:
        return "No events found in context."
    
    summary_lines = [
        f"Event Context Analysis for {context_result['nevent']}",
        "=" * 50,
        f"Thread contains {context_result['no_of_events']} event(s)",
        "",
        context_result['context'],
        "",
        "Event Details:",
        "-" * 20
    ]
    
    for event in events:
        timestamp_str = f"({event['timestamp']})"
        content_preview = event['event_content'][:100] + "..." if len(event['event_content']) > 100 else event['event_content']
        summary_lines.append(f"{event['event_index']}. {timestamp_str} {content_preview}")
    
    return "\n".join(summary_lines)


import os
from glob import glob
import sqlite_store

def _compute_task_id_candidates(npub: str, since: int, till: int) -> List[str]:
    candidates: List[str] = []
    if PYNOSTR_AVAILABLE and npub.startswith('npub'):
        try:
            pk = PublicKey.from_npub(npub)
            hex_pubkey = pk.hex()
            candidates.append(f"{hex_pubkey}_{since}_{till}")
        except Exception as e:
            logger.warning(f"Failed to convert npub to hex for task_id: {e}")
    candidates.append(f"{npub}_{since}_{till}")
    # Deduplicate preserving order
    seen = set()
    unique = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            unique.append(c)
    return unique


def files_exist_for_timestamp(
    npub: str, since: int, curr_timestamp: int, db_path: Optional[str] = None
) -> bool:
    """
    Check if any events exist in the DB for (npub, since, till=curr_timestamp).
    Uses task_id candidates based on npub and its hex.
    """
    try:
        conn = _get_db_connection(db_path)
        task_ids = _compute_task_id_candidates(npub, since, curr_timestamp)
        qmarks = ",".join(["?"] * len(task_ids))
        cur = conn.execute(
            f"SELECT 1 FROM events WHERE task_id IN ({qmarks}) LIMIT 1",
            task_ids,
        )
        return cur.fetchone() is not None
    except Exception as e:
        logger.warning(f"DB check for existing events failed: {e}")
        return False

def load_formatted_npub_output(
    npub: str, since: int, curr_timestamp: int, db_path: Optional[str] = None
) -> Dict[str, Any]:
   """
   Load formatted output from the database for all events with matching task_id
   (npub or its hex) for the (since, till=curr_timestamp) window.
   """
   conn = _get_db_connection(db_path)
   task_ids = _compute_task_id_candidates(npub, since, curr_timestamp)
   qmarks = ",".join(["?"] * len(task_ids))

   cur = conn.execute(
       f"""
       SELECT
           e.id                AS event_row_id,
           e.event_id          AS event_id,
           e.event_content     AS event_content,
           e.context_content   AS context_content,
           e.context_summary   AS context_summary,
           e.timestamp         AS timestamp,
           e.relevancy_score   AS relevancy_score,
           n.npub              AS npub,
           n.name              AS name,
           n.profile_pic       AS profile_pic
       FROM events e
       JOIN npubs n ON n.id = e.npub_id
       WHERE e.task_id IN ({qmarks})
       ORDER BY n.npub, e.timestamp ASC
       """,
       task_ids,
   )

   rows = cur.fetchall() or []
   grouped: Dict[str, Dict[str, Any]] = {}

   for row in rows:
       npub_key = row["npub"] or ""
       bucket = grouped.get(npub_key)
       if bucket is None:
           bucket = {
               "npub": npub_key,
               "name": row["name"] or "",
               "profile_pic": row["profile_pic"] or "",
               "events": [],
           }
           grouped[npub_key] = bucket

       # Fetch related event external ids for thread links
       rel_cur = conn.execute(
           """
           SELECT e2.event_id
           FROM event_thread_links l
           JOIN events e2 ON e2.id = l.related_event_id
           WHERE l.event_id = ?
           """,
           (row["event_row_id"],),
       )
       related_ids = [r[0] for r in (rel_cur.fetchall() or [])]

       bucket["events"].append({
           "event_id": row["event_id"] or "",
           "event_content": row["event_content"] or "",
           "context_content": row["context_content"] or "",
           "context_summary": row["context_summary"] or "",
           "timestamp": int(row["timestamp"]) if row["timestamp"] is not None else 0,
           "relevancy_score": float(row["relevancy_score"]) if row["relevancy_score"] is not None else 0.0,
           "events_in_thread": related_ids,
       })

   # Preserve deterministic order by npub key
   output = [grouped[k] for k in sorted(grouped.keys())]
   return {"output": output}

def get_param_file_path(npub: str, since: int, curr_timestamp: int) -> str:
    """
    Get the path for the parameter file that tracks running commands.
    
    Args:
        npub: The npub identifier
        since: The since timestamp
        curr_timestamp: The current timestamp
        
    Returns:
        Path to the parameter file
    """
    base_dir = os.path.join(os.path.dirname(__file__), "running_commands")
    os.makedirs(base_dir, exist_ok=True)
    return os.path.join(base_dir, f"{npub}_{since}_{curr_timestamp}.json")

def _get_db_connection(db_path: Optional[str] = None):
    return sqlite_store.get_connection(db_path or 'goose.db')

def get_job_status(
    npub: str, since: int, curr_timestamp: int, db_path: Optional[str] = None
) -> Optional[str]:
    """
    Return the most recent job status for (npub, since, till=curr_timestamp),
    or None if no job exists. Optionally specify DB path.
    """
    try:
        conn = _get_db_connection(db_path)
        cur = conn.execute(
            """
            SELECT j.status
            FROM jobs j
            JOIN npubs n ON n.id = j.npub_id
            WHERE n.npub = ? AND j.since = ? AND j.till = ?
            ORDER BY j.id DESC
            LIMIT 1
            """,
            (npub, since, curr_timestamp),
        )
        row = cur.fetchone()
        return str(row[0]) if row is not None else None
    except Exception:
        return None

def mark_command_running(
    npub: str, since: int, curr_timestamp: int, db_path: Optional[str] = None
) -> None:
    """
    Mark a job as running in the database for (npub, since, till=curr_timestamp).
    Creates a job if missing or updates queued -> running.
    Optionally specify a custom SQLite database path.
    """
    conn = _get_db_connection(db_path)
    # Try to find an existing job row for this key
    cur = conn.execute(
        """
        SELECT j.id, j.status
        FROM jobs j
        JOIN npubs n ON n.id = j.npub_id
        WHERE n.npub = ? AND j.since = ? AND j.till = ?
        ORDER BY j.id DESC
        LIMIT 1
        """,
        (npub, since, curr_timestamp),
    )
    row = cur.fetchone()

    if row is None:
        # No existing job; enqueue directly as running and set started_at
        job_id = sqlite_store.enqueue_job(
            conn, npub=npub, since=since, till=curr_timestamp, status="running"
        )
        sqlite_store.update_job_status(
            conn, job_id=job_id, status="running", started_at=curr_timestamp
        )
        return

    job_id = int(row[0])
    status = str(row[1])
    if status == "queued":
        sqlite_store.update_job_status(
            conn, job_id=job_id, status="running", started_at=curr_timestamp
        )
    # If already running, nothing to do

def mark_command_completed(
    npub: str, since: int, curr_timestamp: int, db_path: Optional[str] = None
) -> None:
    """
    Mark any queued/running job for (npub, since, till=curr_timestamp) as success.
    Optionally specify a custom SQLite database path.
    """
    conn = _get_db_connection(db_path)
    cur = conn.execute(
        """
        SELECT j.id
        FROM jobs j
        JOIN npubs n ON n.id = j.npub_id
        WHERE n.npub = ? AND j.since = ? AND j.till = ?
          AND j.status IN ('queued','running')
        """,
        (npub, since, curr_timestamp),
    )
    rows = cur.fetchall() or []
    for r in rows:
        sqlite_store.update_job_status(
            conn, job_id=int(r[0]), status="success", finished_at=curr_timestamp
        )


def mark_command_failed(
    npub: str,
    since: int,
    curr_timestamp: int,
    error_message: str,
    db_path: Optional[str] = None,
) -> None:
    """
    Mark any queued/running job for (npub, since, till=curr_timestamp) as failed.
    Records the error message and finished_at timestamp.
    Optionally specify a custom SQLite database path.
    """
    conn = _get_db_connection(db_path)
    cur = conn.execute(
        """
        SELECT j.id
        FROM jobs j
        JOIN npubs n ON n.id = j.npub_id
        WHERE n.npub = ? AND j.since = ? AND j.till = ?
          AND j.status IN ('queued','running')
        """,
        (npub, since, curr_timestamp),
    )
    rows = cur.fetchall() or []
    for r in rows:
        sqlite_store.update_job_status(
            conn,
            job_id=int(r[0]),
            status="failed",
            error=error_message[:1000],
            finished_at=curr_timestamp,
        )