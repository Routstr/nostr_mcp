#!/usr/bin/env python3
import asyncio
import json
import time
from collections import Counter
from typing import List

from pynostr.key import PublicKey

from nostr_mcp import fetch_nostr_events, get_events_for_summary_multi, fetch_outbox_relays, convert_pubkey_format
from utils import _extract_outbox_relays_from_kind10002
from constants import blacklisted_relays


async def test_fetch_kind3_follows_and_multi():
    """Fetch kind 3 (contacts) for a seed npub, extract follows, and query multi-author events."""
    # seed_npub = "npub1dergggklka99wwrs92yz8wdjs952h2ux2ha2ed598ngwu9w7a6fsh9xzpc"
    seed_npub = "npub1ftt05tgku25m2akgvw6v7aqy5ux5mseqcrzy05g26ml43xf74nyqsredsh"
    since_timestamp = int(time.time()) - (24 * 60 * 60)

    outbox_relays = await fetch_outbox_relays(seed_npub)

    contacts_json = await fetch_nostr_events(
        pubkey=seed_npub,
        kinds=[3],  # Contacts list
        limit=1,
        relays=outbox_relays
    )

    contacts = json.loads(contacts_json)
    if not contacts.get("success") or not contacts.get("events"):
        print("No kind 3 contacts found or fetch failed:", contacts.get("error"))
        assert contacts.get("success") is True or contacts.get("events") == []
        return

    # Extract follows from 'p' tags (hex pubkeys)
    follows_set = set()
    for tag in contacts["events"][0].get("tags", []):
        if isinstance(tag, list) and len(tag) >= 2 and tag[0] == "p":
            follows_set.add(tag[1])

    follows: List[str] = list(follows_set)

    if not follows:
        print("No follows to query; exiting test early.")
        assert True
        return
    
    print(f"follows: {len(follows)}")
    print(f"outbox_relays: {outbox_relays}")
    follows_outbox_relays = await fetch_nostr_events(
        authors=follows,
        kinds=[10002],  # Contacts list
        relays=outbox_relays,
        limit=len(follows)*3
    )

    follows_outbox_relays = json.loads(follows_outbox_relays)
    if not follows_outbox_relays.get("success") or not follows_outbox_relays.get("events"):
        print("No kind 10002 outbox relays found or fetch failed:", follows_outbox_relays.get("error"))
        assert follows_outbox_relays.get("success") is True or follows_outbox_relays.get("events") == []
        return
    # print(f"follows_outbox_relays: {follows_outbox_relays}")
    unique_events = filter_events_keep_latest_by_pubkey(follows_outbox_relays['events'])
    # Greedy multi-cover to pick the smallest relay set so each pubkey has 2 relays
    def _canon(url):
        if not isinstance(url, str):
            return url
        u = url.strip().lower()
        while u.endswith('/') or u.endswith(','):
            u = u[:-1]
        return u

    # Build pubkey -> available relays (canonicalized)
    pubkey_to_relays = {}
    for event in unique_events:
        pk = event.get('pubkey')
        if not pk:
            continue
        relays = _extract_outbox_relays_from_kind10002(event)
        canon_relays = set(_canon(r) for r in relays if r not in blacklisted_relays)
        if canon_relays:
            pubkey_to_relays[pk] = canon_relays

    # Initialize required slots per pubkey (need up to 2, but cap by availability)
    required_slots = {pk: min(2, len(rs)) for pk, rs in pubkey_to_relays.items()}

    selected_relays = set()
    # Precompute relay -> pubkeys mapping for speed (using canonical relays)
    relay_to_pubkeys = {}
    for pk, rs in pubkey_to_relays.items():
        for r in rs:
            relay_to_pubkeys.setdefault(r, set()).add(pk)

    # Greedy selection: pick relay covering most remaining slots
    def remaining_slots_total():
        return sum(required_slots.values())

    while remaining_slots_total() > 0:
        best_relay = None
        best_gain = 0
        for relay, pks in relay_to_pubkeys.items():
            if relay in selected_relays:
                continue
            gain = sum(1 for pk in pks if required_slots.get(pk, 0) > 0)
            if gain > best_gain:
                best_gain = gain
                best_relay = relay
        if not best_relay or best_gain == 0:
            # No further progress possible
            break
        selected_relays.add(best_relay)
        for pk in relay_to_pubkeys.get(best_relay, ()): 
            if required_slots.get(pk, 0) > 0:
                required_slots[pk] -= 1

    # Build per-pubkey assignment (pick up to 2 from selected_relays intersect available)
    per_pubkey_assignment = {}
    for pk, rs in pubkey_to_relays.items():
        chosen = [r for r in selected_relays if r in rs]
        per_pubkey_assignment[pk] = chosen[: min(2, len(rs))]

    print("minimal relay set covering 2 per pubkey:")
    print(f"selected_relays_count: {len(selected_relays)}")
    print(f"selected_relays: {sorted(selected_relays)}")
    # Optionally, show a few assignments
    # for pk, chosen in per_pubkey_assignment.items():
        # print(f"pubkey {pk[:12]}... -> {chosen}")
        
    multi_result = await get_events_for_summary_multi(
        authors=follows,
        since=since_timestamp,
        relays=selected_relays
    )

    assert isinstance(multi_result, dict)
    assert multi_result.get("success") is True
    assert "output" in multi_result
    assert isinstance(multi_result["output"], list)
    # We expect at least 1 layer back, even if some authors fail
    assert len(multi_result["output"]) >= 1

    # Basic shape checks for a layer
    sample = multi_result["output"][0]
    assert "events" in sample
    assert "since_timestamp" in sample
    assert "author_input" in sample

    print("Total layers:", multi_result.get("total_layers"))
    print("Failed authors:", multi_result.get("failed_authors", []))
    print("Total events across layers:", multi_result.get("total_events"))
    # print("Events across layers:", multi_result.get("output"))

def filter_events_keep_latest_by_pubkey(events):
    latest_by_pubkey = {}
    for event in events:
        pubkey = event.get('pubkey')
        if not pubkey:
            continue
        existing = latest_by_pubkey.get(pubkey)
        if existing is None or event.get('created_at', 0) > existing.get('created_at', 0):
            latest_by_pubkey[pubkey] = event
    return list(latest_by_pubkey.values())

if __name__ == "__main__":
    asyncio.run(test_fetch_kind3_follows_and_multi())


