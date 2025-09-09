#!/usr/bin/env python3
import asyncio
import json
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

from pynostr.key import PublicKey

# Optional storage
try:
    from sqlite_store import (
        get_connection,
        initialize_database,
        store_collected_data,
        upsert_event,
        fetch_api_key,
    )
except Exception:
    get_connection = None  # type: ignore
    initialize_database = None  # type: ignore
    store_collected_data = None  # type: ignore
    upsert_event = None  # type: ignore
    fetch_api_key = None  # type: ignore

# Import orchestration helpers from nostr_mcp
from nostr_mcp import fetch_nostr_events, get_events_for_summary_multi


def _to_hex_pubkey(npub_or_hex: str) -> Tuple[str, str]:
    """Return (hex_pubkey, npub) for a provided npub or hex string."""
    if npub_or_hex.startswith("npub"):
        pk = PublicKey.from_npub(npub_or_hex)
        hex_pubkey = pk.hex()
        npub = npub_or_hex
    else:
        # Assume hex
        if len(npub_or_hex) != 64:
            raise ValueError("Hex pubkey must be 64 characters long")
        pk = PublicKey(bytes.fromhex(npub_or_hex))
        hex_pubkey = npub_or_hex
        try:
            npub = pk.npub
        except Exception:
            npub = f"hex:{hex_pubkey}"
    return hex_pubkey, npub


def _pubkey_to_npub(hex_pubkey: str) -> str:
    try:
        return PublicKey(bytes.fromhex(hex_pubkey)).npub
    except Exception:
        return f"hex:{hex_pubkey}"


def _extract_outbox_relays_from_kind10002(event: Dict[str, Any]) -> List[str]:
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
        if mode is None or mode.lower() == "write":
            relays.append(url)
    # Deduplicate while preserving order
    seen = set()
    ordered_unique: List[str] = []
    for r in relays:
        if r not in seen:
            seen.add(r)
            ordered_unique.append(r)
    return ordered_unique


def _extract_following_from_kind3(event: Dict[str, Any]) -> List[Dict[str, str]]:
    """Parse a kind 3 (contacts) event to extract followed pubkeys.

    Each 'p' tag is a followed pubkey. Optionally includes a relay hint.
    Returns list of { 'hex': str, 'npub': str, 'relay_hint': Optional[str] }.
    """
    follows: List[Dict[str, str]] = []
    seen_hex: set[str] = set()
    for tag in event.get("tags", []):
        if not isinstance(tag, list) or not tag:
            continue
        if tag[0] != "p":
            continue
        hex_pubkey = tag[1] if len(tag) > 1 else None
        relay_hint = tag[2] if len(tag) > 2 else None
        if not hex_pubkey or len(hex_pubkey) != 64:
            continue
        if hex_pubkey in seen_hex:
            continue
        seen_hex.add(hex_pubkey)
        follows.append({
            "hex": hex_pubkey,
            "npub": _pubkey_to_npub(hex_pubkey),
            "relay_hint": relay_hint or ""
        })
    return follows


async def collect_all_data_for_npub(
    npub_or_hex: str,
    since: Optional[int] = None,
    till: Optional[int] = None
) -> Dict[str, Any]:
    """Collect outbox relays (kind 10002), following list (kind 3), and
    summaries for each followee via get_events_for_summary.

    Args:
        npub_or_hex: Nostr public key in npub or hex format.
        since: Unix timestamp; if not provided, defaults to 24 hours ago.

    Returns:
        A dict with combined data suitable for JSON serialization.
    """
    # Compute default since (24h)
    if since is None:
        since = int(time.time()) - 24 * 3600

    hex_pubkey, npub = _to_hex_pubkey(npub_or_hex)

    # Fetch kind 10002 (outbox relays) and kind 3 (following) concurrently
    kind10002_task = fetch_nostr_events(pubkey=hex_pubkey, kinds=[10002], limit=1)
    kind3_task = fetch_nostr_events(pubkey=hex_pubkey, kinds=[3], limit=1)
    kind10002_raw, kind3_raw = await asyncio.gather(kind10002_task, kind3_task)

    # Parse results
    outbox_relays: List[str] = []
    following: List[Dict[str, str]] = []

    try:
        k10002_parsed = json.loads(kind10002_raw)
        if k10002_parsed.get("success") and k10002_parsed.get("events"):
            latest = k10002_parsed["events"][0]
            outbox_relays = _extract_outbox_relays_from_kind10002(latest)
    except Exception:
        pass

    try:
        k3_parsed = json.loads(kind3_raw)
        if k3_parsed.get("success") and k3_parsed.get("events"):
            latest = k3_parsed["events"][0]
            following = _extract_following_from_kind3(latest)
    except Exception:
        pass

    # Use multi-author fetch to retrieve events in a single call
    summaries_by_hex: Dict[str, Any] = {}
    authors: List[str] = [f.get("hex", "") for f in following if f.get("hex")]

    # Use only outbox relays
    combined_relays: List[str] = list(dict.fromkeys([r for r in outbox_relays if r]))

    multi_result: Dict[str, Any] = {"success": True, "output": []}
    if authors:
        try:
            multi_result = await get_events_for_summary_multi(
                authors=authors,
                since=since,
                relays=combined_relays if combined_relays else None,
            )
        except Exception as e:
            multi_result = {"success": False, "error": str(e), "output": []}

    layers: List[Dict[str, Any]] = multi_result.get("output", []) if isinstance(multi_result, dict) else []

    # Build mapping from hex pubkey to formatted summary structure
    for layer in layers:
        if not isinstance(layer, dict) or not layer.get("success"):
            continue
        hex_author = layer.get("pubkey", "")
        if not hex_author:
            continue
        events_formatted: List[Dict[str, Any]] = []
        for ev in layer.get("events", []) or []:
            events_formatted.append({
                "event_id": ev.get("id", ""),
                "event_content": ev.get("content", ""),
                "context_content": "Standalone event (not part of a thread)",
                "timestamp": ev.get("created_at", 0),
                "events_in_thread": [ev.get("id")] if ev.get("id") else [],
            })
        summaries_by_hex[hex_author] = {
            "npub": _pubkey_to_npub(hex_author),
            "name": layer.get("name", ""),
            "profile_pic": layer.get("profile_pic", ""),
            "events": events_formatted,
        }

    return {
        "input": {
            "npub": npub,
            "hex": hex_pubkey,
            "since": since,
            "till": till
        },
        "outbox_relays": outbox_relays,
        "following_count": len(following),
        "following": following,
        "summaries_by_hex": summaries_by_hex
    }


async def _main(argv: List[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Collect Nostr data (relays, following, and summaries)")
    parser.add_argument("npub_or_hex", help="npub or hex public key")
    parser.add_argument("--since-hours", type=int, default=24, help="How many hours back to fetch (default: 24)")
    parser.add_argument("--db", type=str, default=None, help="Optional path to sqlite database to persist results")
    args = parser.parse_args(argv)

    start_time = time.time()
    since_ts = int(time.time()) - args.since_hours * 3600
    till_ts = int(time.time())
    result = await collect_all_data_for_npub(args.npub_or_hex, since=since_ts, till=till_ts)
    print(json.dumps(result, indent=2))
    elapsed = time.time() - start_time
    print(f"[timing] total_elapsed_seconds={elapsed:.2f}", file=sys.stderr)

    if args.db:
        if get_connection is None or initialize_database is None or store_collected_data is None:
            print("Warning: sqlite_store not available; skipping DB write", file=sys.stderr)
            return 0
        try:
            conn = get_connection(args.db)
            initialize_database(conn)
            store_collected_data(conn, collected=result)
            print(f"Stored collected data into DB: {args.db}")
        except Exception as e:
            print(f"Warning: failed to store collected data: {e}", file=sys.stderr)
    return 0

def summarize_and_add_relevancy_score(
    instruction: str,
    npub: str,
    since: Optional[int] = None,
    till: Optional[int] = None,
    max_concurrency: int = 20,
    base_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Summarize events and add relevancy scores in goose.db for a given npub.

    - Fetches events from the local SQLite DB (goose.db)
    - Uses context_content when the event is part of a thread (has related events)
    - Calls an OpenAI-compatible API to generate {context_summary, relevancy_score}
    - Stores results back into the DB (events.context_summary, events.relevancy_score)

    Args:
        instruction: Instruction passed to the LLM (e.g., what to score for)
        npub: The npub whose events to process
        since: Optional unix timestamp lower bound
        till: Optional unix timestamp upper bound

    Returns:
        Summary dict with counts and per-event statuses.
    """
    import os
    import sqlite3
    import urllib.request
    import urllib.error

    if get_connection is None:
        raise RuntimeError("sqlite storage is unavailable in this runtime")

    # Resolve DB paths
    base_dir_val = base_dir or os.path.dirname(__file__)
    db_path = os.path.join(base_dir_val, "goose.db")

    # Per user preference, API keys are in a separate DB (keys.db). [[memory:8544858]]
    keys_db_path = os.path.join(base_dir_val, "keys.db")

    # Debug: function start and DB paths
    print(
        f"[summarize_and_add_relevancy_score] start npub={npub} since={since} till={till}",
        flush=True,
    )
    print(
        f"[summarize_and_add_relevancy_score] db_path={db_path} keys_db_path={keys_db_path}",
        flush=True,
    )

    # Load API key if available; URL left blank intentionally for user to fill later
    api_key_value: Optional[str] = None
    if fetch_api_key is not None and os.path.exists(keys_db_path):
        try:
            kconn = get_connection(keys_db_path)
            try:
                rec = fetch_api_key(kconn, api_id="main")
                if rec and isinstance(rec, dict):
                    api_key_value = rec.get("api_key")
            finally:
                kconn.close()
        except Exception:
            api_key_value = None

    api_base_url = "https://ai.redsh1ft.com"  # Intentionally left blank; user will set this
    api_model = "google/gemma-3-27b-it"

    def _chat_completion(payload: Dict[str, Any]) -> Dict[str, Any]:
        if not api_base_url:
            raise ValueError("OpenAI-compatible API base URL is not set.")
        url = api_base_url.rstrip("/") + "/v1/chat/completions"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                **({"Authorization": f"Bearer {api_key_value}"} if api_key_value else {}),
            },
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body)

    def _build_messages(user_input: str, include_summary: bool) -> List[Dict[str, str]]:
        if include_summary:
            system_prompt = (
                f"{instruction}\n\n"
                "You will receive content from a Nostr event or its thread context.\n"
                "Return a strict JSON object with keys: \n"
                "- context_summary: short summary of the content/thread (<= 280 chars).\n"
                "- relevancy_score: score of 0-100 indicating relevance to the instruction.\n"
                "No additional text."
            )
        else:
            system_prompt = (
                f"{instruction}\n\n"
                "You will receive content from a single Nostr event (no thread context).\n"
                "Return a strict JSON object with the single key: \n"
                "- relevancy_score: score of 0-100 indicating relevance to the instruction.\n"
                "No additional text."
            )
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_input},
        ]

    # Connect to goose.db
    connection = get_connection(db_path)
    try:
        cur = connection.cursor()

        # Derive task_id like sqlite_store.store_collected_data: hex_or_npub_since_till
        # If since/till not supplied, try to infer from the npubs table for this npub
        since_val = since
        till_val = till

        if since_val is None or till_val is None:
            raise ValueError("since/till not provided and could not be inferred from DB for the npub")

        # Prefer hex pubkey as base, fallback to npub string
        try:
            hex_pubkey, _ = _to_hex_pubkey(npub)
            base_pub = hex_pubkey or npub
        except Exception:
            base_pub = npub

        task_id = f"{base_pub}_{since_val}_{till_val}"
        print(f"[summarize_and_add_relevancy_score] task_id={task_id}", flush=True)

        # Fetch candidate events for this task
        cur.execute(
            """
            SELECT id, npub_id, event_id, event_content, context_content, timestamp, relevancy_score, context_summary
            FROM events
            WHERE task_id = ?
            ORDER BY timestamp ASC
            """,
            (task_id,),
        )

        events: List[sqlite3.Row] = cur.fetchall() or []
        print(f"[summarize_and_add_relevancy_score] fetched {len(events)} events from DB", flush=True)
        processed = 0
        updated = 0
        skipped = 0
        details: List[Dict[str, Any]] = []

        # Build jobs on main thread (no DB objects in workers)
        jobs: List[Dict[str, Any]] = []
        for ev in events:
            processed += 1
            event_row_id = int(ev[0])
            npub_id = int(ev[1])
            event_id = str(ev[2])
            event_content = ev[3] or ""
            context_content = ev[4] or ""

            # Determine thread size
            cur.execute("SELECT COUNT(*) FROM event_thread_links WHERE event_id = ?", (event_row_id,))
            thread_related = int(cur.fetchone()[0])
            thread_size = 1 + thread_related

            # Choose input source per requirement
            user_input = context_content if thread_size > 1 and context_content else event_content
            print(
                f"[summarize_and_add_relevancy_score] ({processed}) event_id={event_id} thread_size={thread_size} "
                f"input_source={'context' if (thread_size > 1 and context_content) else 'event'} "
                f"input_len={len(user_input)}",
                flush=True,
            )

            if not user_input:
                skipped += 1
                details.append({
                    "event_id": event_id,
                    "status": "skipped",
                    "reason": "no content to summarize",
                })
                print(f"[summarize_and_add_relevancy_score] ({processed}) skipped event_id={event_id}: empty input", flush=True)
                continue

            include_summary = thread_size > 1
            jobs.append({
                "event_row_id": event_row_id,
                "npub_id": npub_id,
                "event_id": event_id,
                "user_input": user_input,
                "include_summary": include_summary,
                "thread_size": thread_size,
            })

        if not jobs:
            return {
                "success": True,
                "npub": npub,
                "processed": processed,
                "updated": updated,
                "skipped": skipped,
                "details": details,
            }

        from concurrent.futures import ThreadPoolExecutor, as_completed

        def _build_response_format(include_summary: bool) -> Dict[str, Any]:
            if include_summary:
                return {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "summary_and_relevancy",
                        "strict": True,
                        "schema": {
                            "type": "object",
                            "properties": {
                                "context_summary": {
                                    "type": "string",
                                    "description": "Short summary (<= 280 chars) of content/thread",
                                },
                                "relevancy_score": {
                                    "type": "number",
                                    "description": "Score 0-100 for relevance to the instruction",
                                },
                            },
                            "required": ["context_summary", "relevancy_score"],
                            "additionalProperties": False,
                        },
                    },
                }
            else:
                return {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "relevancy_only",
                        "strict": True,
                        "schema": {
                            "type": "object",
                            "properties": {
                                "relevancy_score": {
                                    "type": "number",
                                    "description": "Score 0-100 for relevance to the instruction",
                                }
                            },
                            "required": ["relevancy_score"],
                            "additionalProperties": False,
                        },
                    },
                }

        def _worker(job: Dict[str, Any]) -> Dict[str, Any]:
            user_input = job["user_input"]
            include_summary = job["include_summary"]
            messages = _build_messages(user_input, include_summary=include_summary)
            payload = {
                "model": api_model,
                "messages": messages,
                "temperature": 0.2,
                "response_format": _build_response_format(include_summary),
            }
            print(
                f"[summarize_and_add_relevancy_score] calling LLM model={api_model} temp=0.2 format={'summary+score' if include_summary else 'score-only'} input_preview={user_input[:120].replace('\n',' ')}...",
                flush=True,
            )

            attempts = 3
            last_error: Optional[Exception] = None
            for attempt in range(1, attempts + 1):
                try:
                    resp = _chat_completion(payload)
                    content_text = (
                        ((resp.get("choices") or [{}])[0].get("message") or {}).get("content")
                        if isinstance(resp, dict) else None
                    )
                    if not content_text:
                        raise ValueError("Empty response from LLM")

                    result_obj = json.loads(content_text)
                    context_summary_val = str(result_obj.get("context_summary", "")).strip()
                    relevancy_score_val = result_obj.get("relevancy_score", None)
                    try:
                        relevancy_score_num = float(relevancy_score_val) if relevancy_score_val is not None else None
                    except Exception:
                        relevancy_score_num = None

                    return {
                        "ok": True,
                        "event_row_id": job["event_row_id"],
                        "npub_id": job["npub_id"],
                        "event_id": job["event_id"],
                        "thread_size": job["thread_size"],
                        "context_summary": context_summary_val,
                        "relevancy_score": relevancy_score_num,
                    }
                except Exception as e:
                    last_error = e
                    if attempt < attempts:
                        try:
                            time.sleep(1)
                        except Exception:
                            pass
                    else:
                        return {
                            "ok": False,
                            "event_row_id": job["event_row_id"],
                            "npub_id": job["npub_id"],
                            "event_id": job["event_id"],
                            "thread_size": job["thread_size"],
                            "error": str(last_error),
                            "attempts": attempts,
                        }

        # Cap concurrency to positive integer
        try:
            max_workers = int(max_concurrency)
        except Exception:
            max_workers = 10
        if max_workers <= 0:
            max_workers = 1

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_job = {executor.submit(_worker, job): job for job in jobs}
            for future in as_completed(future_to_job):
                job = future_to_job[future]
                event_id = job["event_id"]
                try:
                    result = future.result()
                except Exception as e:
                    skipped += 1
                    details.append({
                        "event_id": event_id,
                        "status": "error",
                        "error": str(e),
                    })
                    print(f"[summarize_and_add_relevancy_score] future failed for event_id={event_id}: {e}", flush=True)
                    continue

                if result.get("ok"):
                    context_summary_val = str(result.get("context_summary") or "").strip()
                    relevancy_score_num = result.get("relevancy_score")
                    event_row_id = int(result["event_row_id"])  # for fallback update
                    npub_id_val = int(result["npub_id"])  # for upsert_event path
                    if upsert_event is None:
                        update_fields = []
                        update_params: List[Any] = []
                        if context_summary_val:
                            update_fields.append("context_summary = ?")
                            update_params.append(context_summary_val)
                        if relevancy_score_num is not None:
                            update_fields.append("relevancy_score = ?")
                            update_params.append(relevancy_score_num)
                        if update_fields:
                            update_sql = ", ".join(update_fields)
                            cur.execute(f"UPDATE events SET {update_sql} WHERE id = ?", update_params + [event_row_id])
                            connection.commit()
                            updated += 1
                    else:
                        upsert_event(
                            connection,
                            npub_id=npub_id_val,
                            event_id=event_id,
                            context_summary=context_summary_val or None,
                            relevancy_score=relevancy_score_num,
                        )
                        updated += 1

                    details.append({
                        "event_id": event_id,
                        "status": "updated",
                        "thread_size": int(result.get("thread_size") or 1),
                    })
                else:
                    skipped += 1
                    details.append({
                        "event_id": event_id,
                        "status": "error",
                        "error": str(result.get("error")),
                        "attempts": int(result.get("attempts") or 0),
                    })
                    print(
                        f"[summarize_and_add_relevancy_score] error event_id={event_id}: {result.get('error')}",
                        flush=True,
                    )

        return {
            "success": True,
            "npub": npub,
            "processed": processed,
            "updated": updated,
            "skipped": skipped,
            "details": details,
        }
    finally:
        try:
            connection.close()
        except Exception:
            pass

if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main(sys.argv[1:])))

