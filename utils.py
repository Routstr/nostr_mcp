#!/usr/bin/env python3
import json
import asyncio
from typing import Optional, List, Dict, Any, Callable, Awaitable
import logging
import aiohttp
from constants import blacklisted_relays
logger = logging.getLogger("nostr-mcp")

# Import pynostr for pubkey conversion
try:
    from pynostr.key import PublicKey
    PYNOSTR_AVAILABLE = True
except ImportError:
    PYNOSTR_AVAILABLE = False
    logger.warning("pynostr not available, npub/hex conversion will be disabled")

def parse_e_tags(event_tags: List[List[str]]) -> Dict[str, List[str]]:
    """Parse 'e' and 'q' tags from an event.

    - 'e' tags: extract root/reply references and their relays when present
    - 'q' tags: extract quoted event references and their relays
    
    Args:
        event_tags: List of tag arrays from a nostr event
        
    Returns:
        Dict with:
          - 'root': list of event IDs marked as root
          - 'reply': list of event IDs marked as reply or unmarked 'e' references
          - 'quote': list of quoted event IDs from 'q' tags
          - 'root_relays' | 'reply_relays' | 'quote_relays': lists of relay URLs
    """
    e_tags: Dict[str, List[str]] = {'root': [], 'reply': []}

    for tag in event_tags:
        if not isinstance(tag, list) or len(tag) < 2:
            continue

        kind = tag[0]

        if kind == 'e':
            event_id = tag[1]
            # Check if there's a marker (root/reply) in the tag
            if len(tag) >= 4:
                marker = tag[3]
                relay_url = tag[2]
                if marker == 'root':
                    e_tags['root'].append(event_id)
                    if relay_url:
                        e_tags.setdefault('root_relays', []).append(relay_url)
                elif marker == 'reply':
                    e_tags['reply'].append(event_id)
                    if relay_url:
                        e_tags.setdefault('reply_relays', []).append(relay_url)
                else:
                    # Unknown marker; treat as general reference
                    e_tags['reply'].append(event_id)
            else:
                # If no marker, treat as general reference
                e_tags['reply'].append(event_id)

        elif kind == 'q':
            # NIP-XXX: quote reference tag. Format typically: ['q', event_id, relay?, ...]
            quote_event_id = tag[1]
            e_tags.setdefault('quote', []).append(quote_event_id)
            if len(tag) >= 3:
                relay_url = tag[2]
                if relay_url:
                    e_tags.setdefault('quote_relays', []).append(relay_url)

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

async def compute_selected_relays_for_follows(
    follows: List[str],
    *,
    relays: Optional[List[str]] = None,
    fetch_events_func: Optional[Callable[..., Awaitable[str]]] = None,
) -> List[str]:
    """Given a list of followed hex pubkeys, compute a minimal relay set.

    Strategy:
    - Fetch each follow's kind 10002 (outbox relays) events (latest per pubkey)
    - Build mapping pubkey -> available relays (canonicalized, blocklist applied)
    - Greedy set cover to select the smallest set so each pubkey is covered by up to 2 relays

    Args:
        follows: List of hex pubkeys to cover
        relays: Optional relay list used for fetching kind 10002 events
        fetch_events_func: Optional async function compatible with fetch_nostr_events

    Returns:
        List of selected relay URLs (canonicalized, lowercase, without trailing slashes/commas)
    """
    try:
        if not follows:
            return []

        # Lazy import to avoid circular dependencies if a fetch function is not provided
        if fetch_events_func is None:
            try:
                from nostr_mcp import fetch_nostr_events as _default_fetch
                fetch_events_func = _default_fetch  # type: ignore
            except Exception as e:
                logger.warning(f"fetch_nostr_events unavailable: {e}")
                return []

        # Fetch kind 10002 events for follows
        raw = await fetch_events_func(
            authors=follows,
            kinds=[10002],
            relays=relays,
            limit=max(1, len(follows) * 3),
        )
        parsed = json.loads(raw)
        if not isinstance(parsed, dict) or not parsed.get("success") or not parsed.get("events"):
            return []

        events = parsed.get("events") or []

        # Keep only the latest kind 10002 per pubkey
        latest_by_pubkey: Dict[str, Dict[str, Any]] = {}
        for ev in events:
            pk = ev.get("pubkey")
            if not isinstance(pk, str) or not pk:
                continue
            existing = latest_by_pubkey.get(pk)
            if existing is None or int(ev.get("created_at", 0) or 0) > int(existing.get("created_at", 0) or 0):
                latest_by_pubkey[pk] = ev

        def _canon(url: Any) -> Any:
            if not isinstance(url, str):
                return url
            u = url.strip().lower()
            while u.endswith("/") or u.endswith(","):
                u = u[:-1]
            return u

        # pubkey -> set of available relays
        pubkey_to_relays: Dict[str, List[str]] = {}
        for pk, ev in latest_by_pubkey.items():
            try:
                relay_list = _extract_outbox_relays_from_kind10002(ev)
                canon = []
                for r in relay_list:
                    cr = _canon(r)
                    if isinstance(cr, str) and cr and cr not in blacklisted_relays:
                        canon.append(cr)
                # Deduplicate while preserving order
                seen: set[str] = set()
                canon_unique = [r for r in canon if not (r in seen or seen.add(r))]
                if canon_unique:
                    pubkey_to_relays[pk] = canon_unique
            except Exception:
                continue

        if not pubkey_to_relays:
            return []

        # Each pubkey needs up to 2 distinct relays, capped by availability
        required_slots: Dict[str, int] = {pk: min(2, len(rs)) for pk, rs in pubkey_to_relays.items()}

        selected_relays: set[str] = set()
        # Build reverse index: relay -> set(pubkeys)
        relay_to_pubkeys: Dict[str, set[str]] = {}
        for pk, rs in pubkey_to_relays.items():
            for r in rs:
                relay_to_pubkeys.setdefault(r, set()).add(pk)

        def remaining_slots_total() -> int:
            return int(sum(required_slots.values()))

        # Greedy selection: pick relay covering most remaining slots
        while remaining_slots_total() > 0:
            best_relay: Optional[str] = None
            best_gain = 0
            for relay, pks in relay_to_pubkeys.items():
                if relay in selected_relays:
                    continue
                gain = 0
                for pk in pks:
                    if required_slots.get(pk, 0) > 0:
                        gain += 1
                if gain > best_gain:
                    best_gain = gain
                    best_relay = relay
            if not best_relay or best_gain == 0:
                break
            selected_relays.add(best_relay)
            for pk in relay_to_pubkeys.get(best_relay, set()):
                if required_slots.get(pk, 0) > 0:
                    required_slots[pk] -= 1

        return sorted(selected_relays)
    except Exception as e:
        logger.warning(f"compute_selected_relays_for_follows failed: {e}")
        return []

async def fetch_event_context(
    event_id: str,
    fetch_events_func,
    relays: Optional[List[str]] = None,
    max_depth: int = 50,
    initial_event: Optional[Dict[str, Any]] = None,
    root_event: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Fetch the full context of a Nostr event by following the reply chain to the root.
    
    Args:
        event_id: The event ID to analyze
        fetch_events_func: Function to fetch events (should be fetch_nostr_events)
        relays: Optional list of relays to use
        max_depth: Maximum depth to prevent infinite loops
        initial_event: If provided, use this event as the starting point instead of refetching
        
    Returns:
        Dict containing the event context, thread events, and summary
    """
    try:
        # Fetch or use the initial event
        if initial_event and isinstance(initial_event, dict):
            current_event = initial_event
        else:
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
            current_event = parsed_result['events'][0]
        
        thread_events = [current_event]
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
                        # This event only replies to root, use provided root_event if available, else fetch
                        root_id = reply_e_tags['root'][0]
                        if root_event and isinstance(root_event, dict) and root_event.get('id') == root_id:
                            thread_events.append(root_event)
                        else:
                            root_result = await fetch_events_func(ids=[root_id], limit=1, relays=relays)
                            root_parsed = json.loads(root_result)
                            if root_parsed['success'] and root_parsed['events']:
                                fetched_root = root_parsed['events'][0]
                                thread_events.append(fetched_root)
                        break
                    elif not reply_e_tags['reply'] and not reply_e_tags['root']:
                        # This is the root event itself
                        break
                else:
                    # Can't fetch the reply, stop here
                    break
            
            elif e_tags['root']:
                # Only root tag, append provided root_event if available, otherwise fetch it
                root_id = e_tags['root'][0]
                if root_event and isinstance(root_event, dict) and root_event.get('id') == root_id:
                    thread_events.append(root_event)
                else:
                    root_result = await fetch_events_func(ids=[root_id], limit=1, relays=relays)
                    root_parsed = json.loads(root_result)
                    if root_parsed['success'] and root_parsed['events']:
                        fetched_root = root_parsed['events'][0]
                        thread_events.append(fetched_root)
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


async def process_events_for_npub(
    *,
    authors: List[str],
    since: int,
    used_relays: Optional[List[str]],
    events_by_pubkey: Dict[str, List[Dict[str, Any]]],
    fetch_events_func,
) -> List[Dict[str, Any]]:
    """Build per-author layers with formatted events and thread context.

    Mirrors the per-author logic used by get_events_for_summary_multi.

    Args:
        authors: Input authors in npub or hex format
        since: Unix timestamp used for response metadata
        used_relays: Relays to use for context fetching
        events_by_pubkey: Mapping of hex pubkey -> list of kind 1 events
        fetch_events_func: Callable compatible with fetch_nostr_events for context fetching

    Returns:
        List of layer dicts for each input author
    """
    # Normalize inputs
    normalized_inputs: List[Dict[str, Any]] = []
    for author in authors:
        entry: Dict[str, Any] = {'author_input': author}
        try:
            if isinstance(author, str) and author.startswith('npub'):
                if not PYNOSTR_AVAILABLE:
                    raise ValueError('pynostr not available for npub conversion')
                pk = PublicKey.from_npub(author)
                entry['pubkey'] = pk.hex()
            else:
                entry['pubkey'] = author
        except Exception as e:
            entry['error'] = f'Invalid author pubkey: {str(e)}'
        normalized_inputs.append(entry)

    layers: List[Dict[str, Any]] = []

    # Prefetch root events across all authors to avoid sequential fetches
    prefetched_roots: Dict[str, Dict[str, Any]] = {}
    try:
        root_ids_set: set[str] = set()
        root_relays_set: set[str] = set()
        for author_events in (events_by_pubkey or {}).values():
            for ev in author_events or []:
                try:
                    e_tags_all = parse_e_tags(ev.get('tags', []))
                    if e_tags_all.get('root'):
                        root_ids_set.add(e_tags_all['root'][0])
                        for rr in (e_tags_all.get('root_relays') or []):
                            try:
                                if rr:
                                    root_relays_set.add(rr)
                            except Exception:
                                continue
                except Exception:
                    continue
        if root_ids_set:
            try:
                # Use a union list of root relays if present; otherwise fall back to used_relays
                effective_relays = list(root_relays_set) if root_relays_set else used_relays
                raw = await fetch_events_func(ids=list(root_ids_set), relays=effective_relays, limit=None)
                parsed = json.loads(raw)
                if parsed.get('success') and isinstance(parsed.get('events'), list):
                    for rev in parsed.get('events') or []:
                        rid = rev.get('id')
                        if rid:
                            prefetched_roots[rid] = rev
            except Exception:
                # Best-effort prefetch; proceed without if it fails
                pass
    except Exception:
        pass

    # Build per-author formatted events
    for ni in normalized_inputs:
        original = ni.get('author_input')
        hex_author = ni.get('pubkey')
        prior_err = ni.get('error')

        if prior_err or not hex_author:
            layers.append({
                'success': False,
                'author_input': original,
                'since_timestamp': since,
                'error': prior_err or 'Invalid author pubkey',
                'events': [],
                'total_events': 0
            })
            continue

        author_events = events_by_pubkey.get(hex_author, [])

        formatted_events: List[Dict[str, Any]] = []
        processed_threads = set()

        for event in author_events:
            try:
                event_id = event['id']
                e_tags = parse_e_tags(event['tags'])

                if e_tags['root'] or e_tags['reply']:
                    thread_root = e_tags['root'][0] if e_tags['root'] else None
                    if thread_root and thread_root in processed_threads:
                        continue

                    context_result = await fetch_event_context(
                        event_id=event_id,
                        fetch_events_func=fetch_events_func,
                        relays=used_relays,
                        initial_event=event,
                        root_event=prefetched_roots.get(thread_root) if thread_root else None
                    )

                    if context_result.get('success') and context_result.get('thread_events'):
                        if thread_root:
                            processed_threads.add(thread_root)

                        thread_events = context_result['thread_events']
                        root_event = thread_events[0] if thread_events else None

                        original_event = None
                        for te in thread_events:
                            if te['id'] == event_id:
                                original_event = te
                                break

                        context_parts: List[str] = []
                        if root_event:
                            context_parts.append(f"Root event that started the thread: {root_event['content']}")

                        reply_events = [te for te in thread_events if te['id'] != event_id and (not root_event or te['id'] != root_event['id'])]
                        if reply_events:
                            context_parts.append("Reply events:")
                            for reply in reply_events:
                                context_parts.append(reply['content'])

                        if original_event:
                            context_parts.append(f"Original Event: {original_event['content']}")

                        context_content = "\n".join(context_parts)

                        formatted_event = {
                            'event_id': event_id,
                            'event_content': event['content'],
                            'timestamp': event['created_at'],
                            'context_content': context_content,
                            'events_in_thread': [te for te in thread_events],
                            'pubkey': event['pubkey'],
                            'kind': event['kind']
                        }
                        formatted_events.append(formatted_event)
                else:
                    formatted_event = {
                        'event_id': event_id,
                        'event_content': event['content'],
                        'timestamp': event['created_at'],
                        'context_content': "Standalone event (not part of a thread)",
                        'events_in_thread': [event],
                        'pubkey': event['pubkey'],
                        'kind': event['kind']
                    }
                    formatted_events.append(formatted_event)
            except Exception:
                # Skip problematic events for robustness
                continue

        layers.append({
            'success': True,
            'author_input': original,
            'pubkey': hex_author,
            'since_timestamp': since,
            'name': '',
            'profile_pic': '',
            'total_events': len(formatted_events),
            'events': formatted_events
        })

    return layers

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
           e.reason_for_score  AS reason_for_score,
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
           "reason_for_score": row["reason_for_score"] or "",
           "events_in_thread": related_ids,
       })

   result = get_api_base_url_and_model(npub, since, curr_timestamp, db_path)
   if result is not None:
       api_base_url, api_model = result
   else:
       api_base_url, api_model = None, None
   # Preserve deterministic order by npub key
   output = [grouped[k] for k in sorted(grouped.keys())]
   return {"output": output, "api_base_url": api_base_url, "api_model": api_model}

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

def get_api_base_url_and_model(
    npub: str, since: int, curr_timestamp: int, db_path: Optional[str] = None
) -> Optional[Dict[str, Optional[str]]]:
    """
    Return the most recent API configuration (api_base_url, api_model) for
    (npub, since, till=curr_timestamp), or None if no job exists.

    Args:
        npub: npub identifier
        since: window start
        curr_timestamp: window end used as till
        db_path: optional custom SQLite DB path

    Returns:
        Dict with keys 'api_base_url' and 'api_model', or None if missing.
    """
    try:
        conn = _get_db_connection(db_path)
        cur = conn.execute(
            """
            SELECT j.api_base_url, j.api_model
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
            return None
        return {
            "api_base_url": str(row[0]) if row[0] is not None else None,
            "api_model": str(row[1]) if row[1] is not None else None,
        }
    except Exception:
        return None

def mark_command_running(
    npub: str, since: int, curr_timestamp: int, db_path: Optional[str] = None, api_base_url: Optional[str] = None, api_model: Optional[str] = None
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
            conn, npub=npub, since=since, till=curr_timestamp, status="running", api_base_url=api_base_url, api_model=api_model
        )
        sqlite_store.update_job_status(
            conn, job_id=job_id, status="running", started_at=curr_timestamp, api_base_url=api_base_url, api_model=api_model
        )
        return

    job_id = int(row[0])
    status = str(row[1])
    if status == "queued":
        sqlite_store.update_job_status(
            conn, job_id=job_id, status="running", started_at=curr_timestamp, api_base_url=api_base_url, api_model=api_model
        )
    # If already running, nothing to do

def mark_command_completed(
    npub: str, since: int, curr_timestamp: int, db_path: Optional[str] = None, api_base_url: Optional[str] = None, api_model: Optional[str] = None
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
            conn, job_id=int(r[0]), status="success", finished_at=curr_timestamp, api_base_url=api_base_url, api_model=api_model
        )


def mark_command_failed(
    npub: str,
    since: int,
    curr_timestamp: int,
    error_message: str,
    db_path: Optional[str] = None,
    api_base_url: Optional[str] = None,
    api_model: Optional[str] = None,
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
            api_base_url=api_base_url,
            api_model=api_model,
        )

