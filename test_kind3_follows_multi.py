#!/usr/bin/env python3
import asyncio
import json
import time
from typing import List

from nostr_mcp import fetch_nostr_events, get_events_for_summary_multi, fetch_outbox_relays


async def test_fetch_kind3_follows_and_multi():
    """Fetch kind 3 (contacts) for a seed npub, extract follows, and query multi-author events."""
    seed_npub = "npub1dergggklka99wwrs92yz8wdjs952h2ux2ha2ed598ngwu9w7a6fsh9xzpc"
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


    multi_result = await get_events_for_summary_multi(
        authors=follows,
        since=since_timestamp,
        relays=outbox_relays
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


if __name__ == "__main__":
    asyncio.run(test_fetch_kind3_follows_and_multi())


