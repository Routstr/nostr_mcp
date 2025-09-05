#!/usr/bin/env python3
import asyncio
import json
from nostr_mcp import fetch_nostr_events
import time

async def test_fetch_nostr_events():
    """Test fetching kind 1 events for the specified npub using fetch_nostr_events"""
    npub = "npub1dergggklka99wwrs92yz8wdjs952h2ux2ha2ed598ngwu9w7a6fsh9xzpc"
    
    # Get events from the last 2 days
    since_timestamp = int(time.time()) - (2 * 24 * 60 * 60)
    
    print(f"Fetching kind 1 events for {npub} since {since_timestamp}")
    print("Using fetch_nostr_events function")
    print("-" * 60)
    
    try:
        result_json = await fetch_nostr_events(
            pubkey=npub,
            kinds=[1],  # Only fetch kind 1 (text notes)
            since=since_timestamp,
            limit=50
        )
        
        # Parse the JSON response
        result = json.loads(result_json)
        
        if result['success']:
            print(f"✅ Successfully fetched {result['count']} events")
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
            
            print(f"Filters applied: {result.get('filters_applied', {})}")
            
            # Show notices if any
            if result.get('notices'):
                print(f"Relay notices: {result['notices']}")
            
            print("\nSample events:")
            for i, event in enumerate(result['events'][:3]):  # Show first 3 events
                print(f"\nEvent {i+1}:")
                print(f"  ID: {event['id']}")
                print(f"  Kind: {event['kind']}")
                print(f"  Pubkey: {event['pubkey']}")
                print(f"  Created: {event['created_at']}")
                print(f"  Content: {event['content'][:100]}...")
                if event.get('tags'):
                    print(f"  Tags: {len(event['tags'])} tags")
        else:
            print(f"❌ Error: {result['error']}")
            
    except json.JSONDecodeError as e:
        print(f"❌ JSON Parse Error: {str(e)}")
    except Exception as e:
        print(f"❌ Exception: {str(e)}")

if __name__ == "__main__":
    asyncio.run(test_fetch_nostr_events())