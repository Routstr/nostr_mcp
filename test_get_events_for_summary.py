#!/usr/bin/env python3
"""Test script for the get_events_for_summary function."""

import asyncio
import json
import time
from datetime import datetime, timedelta
from nostr_mcp import get_events_for_summary, fetch_and_store

async def test_get_events_for_summary():
    """Test the get_events_for_summary function."""
    
    # Example npub (you can replace with a real one for testing)
    test_npub = "npub1ftt05tgku25m2akgvw6v7aqy5ux5mseqcrzy05g26ml43xf74nyqsredsh"  # Example npub
    
    # Get events from the last 24 hours
    since_timestamp = int(time.time()) - (24 * 60 * 60)  # 24 hours ago
    
    print(f"Testing get_events_for_summary")
    print(f"Fetching events for: {test_npub}")
    print(f"Since timestamp: {since_timestamp} ({datetime.fromtimestamp(since_timestamp).isoformat()})")
    print("-" * 50)
    
    try:
        # Call the function
        result = await fetch_and_store(
            npub_or_hex=test_npub,
            since=since_timestamp,
            till=int(time.time()),
            base_dir="/Users/r/projects/routstr_main/nostr_mcp"
        )
        print(result)
        
        if result['success']:
            print(f"✅ Successfully fetched events")
            print(f"Total events found: {result['total_events']}")
            print(f"Relays used: {result.get('relays_used', [])}")
            print("\n" + "=" * 50)
            
            # Display each event with its context
            for i, event in enumerate(result['events'], 1):
                print(f"\nEvent {i}/{result['total_events']}")
                print("-" * 30)
                print(f"Event ID: {event['event_id']}")
                print(f"Timestamp: {datetime.fromtimestamp(event['timestamp']).isoformat()}")
                print(f"Thread size: {len(event.get('events_in_thread', [])) + 1} events")
                print(f"\nEvent Content:")
                print(event['event_content'][:200] + "..." if len(event['event_content']) > 200 else event['event_content'])
                print(f"\nContext Content:")
                print(event['context_content'][:500] + "..." if len(event['context_content']) > 500 else event['context_content'])
                print("=" * 50)
                
                # Limit output for readability
                if i >= 5:
                    remaining = result['total_events'] - i
                    if remaining > 0:
                        print(f"\n... and {remaining} more events")
                    break
        else:
            print(f"❌ Failed to fetch events")
            print(f"Error: {result.get('error', 'Unknown error')}")
            
    except Exception as e:
        print(f"❌ Exception occurred: {str(e)}")
        import traceback
        traceback.print_exc()

async def test_with_hex_pubkey():
    """Test with a hex public key instead of npub."""
    
    # Example hex pubkey (32 bytes / 64 hex chars)
    test_hex = "32e1827635450ebb3c5a7d12c1f8e7b2b514439ac10a67eef3d9fd9c5c68e245"
    
    # Get events from the last 7 days
    since_timestamp = int(time.time()) - (7 * 24 * 60 * 60)
    
    print(f"\nTesting with hex pubkey")
    print(f"Fetching events for: {test_hex}")
    print(f"Since timestamp: {since_timestamp} ({datetime.fromtimestamp(since_timestamp).isoformat()})")
    print("-" * 50)
    
    try:
        result = await get_events_for_summary(
            pubkey=test_hex,
            since=since_timestamp
        )
        
        if result['success']:
            print(f"✅ Successfully fetched {result['total_events']} events")
        else:
            print(f"❌ Failed: {result.get('error', 'Unknown error')}")
            
    except Exception as e:
        print(f"❌ Exception: {str(e)}")

async def test_with_custom_relays():
    """Test with custom relay list."""
    
    test_npub = "npub1xtscya34g58tk0z605fvr788k263gsu6cy9x0mhnm87echrgufzsevkk5s"
    since_timestamp = int(time.time()) - (3 * 60 * 60)  # Last 3 hours
    custom_relays = ["wss://relay.damus.io", "wss://nos.lol", "wss://relay.nostr.band"]
    
    print(f"\nTesting with custom relays")
    print(f"Custom relays: {custom_relays}")
    print("-" * 50)
    
    try:
        result = await get_events_for_summary(
            pubkey=test_npub,
            since=since_timestamp,
            relays=custom_relays
        )
        
        if result['success']:
            print(f"✅ Successfully fetched {result['total_events']} events")
            print(f"Actually used relays: {result.get('relays_used', [])}")
        else:
            print(f"❌ Failed: {result.get('error', 'Unknown error')}")
            
    except Exception as e:
        print(f"❌ Exception: {str(e)}")

async def main():
    """Run all tests."""
    print("=" * 60)
    print("Testing get_events_for_summary function")
    print("=" * 60)
    
    # Test 1: Basic test with npub
    await test_get_events_for_summary()
    
    # # Test 2: Test with hex pubkey
    # await test_with_hex_pubkey()
    
    # # Test 3: Test with custom relays
    # await test_with_custom_relays()
    
    print("\n" + "=" * 60)
    print("All tests completed!")
    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(main())