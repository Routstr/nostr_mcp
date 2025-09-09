#!/usr/bin/env python3
import asyncio
import json
from nostr_mcp import get_events_for_summary
import time

async def test_npub_events():
    """Test fetching events for the specified npub with kind 1 only"""
    npub = "npub1dergggklka99wwrs92yz8wdjs952h2ux2ha2ed598ngwu9w7a6fsh9xzpc"
    
    # Get events from the last 7 days
    since_timestamp = int(time.time()) - (2 * 24 * 60 * 60)
    
    print(f"Fetching events for {npub} since {since_timestamp}")
    print("This should only return kind 1 events (text notes)")
    print("-" * 60)
    
    try:
        result = await get_events_for_summary(
            pubkey=npub,
            since=since_timestamp
        )
        
        if result['success']:
            print(f"✅ Successfully fetched {result['total_events']} events")
            print(f"Using relays: {result['relays_used']}")
            print()
            
            # Verify all events are kind 1
            kind_1_count = 0
            other_kinds = []
            
            for event in result['events']:
                if event['kind'] == 1:
                    kind_1_count += 1
                else:
                    other_kinds.append(event['kind'])
            
            print(f"Kind 1 events: {kind_1_count}")
            if other_kinds:
                print(f"⚠️  Other kinds found: {set(other_kinds)}")
            else:
                print("✅ All events are kind 1 (text notes)")
            
            print("\nSample events:")
            print("\n:", result['events'])
            for i, event in enumerate(result['events'][:3]):  # Show first 3 events
                print(f"\nEvent {i+1}:")
                print(f"  ID: {event['event_id']}")
                print(f"  Kind: {event['kind']}")
                print(f"  Content: {event['event_content'][:100]}...")
                print(f"  Thread size: {len(event.get('events_in_thread', [])) + 1}")
        else:
            print(f"❌ Error: {result['error']}")
            
    except Exception as e:
        print(f"❌ Exception: {str(e)}")

if __name__ == "__main__":
    asyncio.run(test_npub_events())