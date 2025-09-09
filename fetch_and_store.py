#!/usr/bin/env python3
import asyncio
import json
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

from pynostr.key import PublicKey

# Import orchestration helpers from nostr_mcp
from nostr_mcp import fetch_nostr_events, get_events_for_summary


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


async def collect_all_data_for_npub(npub_or_hex: str, since: Optional[int] = None) -> Dict[str, Any]:
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

    # Build tasks to fetch summaries for each followee
    summary_tasks = []
    for f in following:
        summary_tasks.append(get_events_for_summary(pubkey=f["hex"], since=since, relays=outbox_relays or None))

    summaries_results: List[Dict[str, Any]] = []
    if summary_tasks:
        summaries_results = await asyncio.gather(*summary_tasks, return_exceptions=True)

    # Helper to format a summary result to the required schema
    def _format_summary_result(followee_hex: str, res: Any) -> Dict[str, Any]:
        npub_value = _pubkey_to_npub(followee_hex) if followee_hex else ""
        if isinstance(res, Exception):
            return {
                "npub": npub_value,
                "name": "",
                "profile_pic": "",
                "events": []
            }

        events_list = []
        try:
            for ev in (res.get("events") or []):
                events_list.append({
                    "event_id": ev.get("event_id", ""),
                    "event_content": ev.get("event_content", ""),
                    "context_content": ev.get("context_content", ""),
                    "timestamp": ev.get("timestamp", 0),
                    "events_in_thread": ev.get("events_in_thread", [])
                })
        except Exception:
            events_list = []

        return {
            "npub": npub_value,
            "name": res.get("name", "") if isinstance(res, dict) else "",
            "profile_pic": res.get("profile_pic", "") if isinstance(res, dict) else "",
            "events": events_list
        }

    # Normalize summaries into mapping by hex pubkey with required schema
    summaries_by_hex: Dict[str, Any] = {}
    for idx, res in enumerate(summaries_results):
        followee_hex = following[idx]["hex"] if idx < len(following) else ""
        summaries_by_hex[followee_hex] = _format_summary_result(followee_hex, res)

    return {
        "input": {
            "npub": npub,
            "hex": hex_pubkey,
            "since": since
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
    args = parser.parse_args(argv)

    since_ts = int(time.time()) - args.since_hours * 3600
    result = await collect_all_data_for_npub(args.npub_or_hex, since=since_ts)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main(sys.argv[1:])))


