#!/usr/bin/env python3
"""Test get_events_for_summary with a specific npub."""

import asyncio
import json
import time
from datetime import datetime
from nostr_mcp import get_events_for_summary

async def main():
    # The specific npub to test
    npub = "npub1dergggklka99wwrs92yz8wdjs952h2ux2ha2ed598ngwu9w7a6fsh9xzpc"
    
    # Get events from the last 7 days
    since_timestamp = int(time.time()) - (7 * 24 * 60 * 60)
    
    print(f"Fetching events for: {npub}")
    print(f"Since: {datetime.fromtimestamp(since_timestamp).isoformat()}")
    print("=" * 80)
    
    try:
        result = await get_events_for_summary(
            pubkey=npub,
            since=since_timestamp
        )
        
        if result['success']:
            print(f"✅ Successfully fetched events")
            print(f"Total events: {result['total_events']}")
            print(f"Relays used: {json.dumps(result.get('relays_used', []), indent=2)}")
            print("\n" + "=" * 80)
            
            # Display each event with context
            for i, event in enumerate(result['events'], 1):
                print(f"\n📝 Event {i}/{result['total_events']}")
                print("-" * 40)
                print(f"Event ID: {event['event_id'][:16]}...")
                print(f"Timestamp: {datetime.fromtimestamp(event['timestamp']).isoformat()}")
                print(f"Thread size: {event.get('thread_size', 1)} events")
                print(f"Kind: {event['kind']}")
                
                # Show event content (truncated for readability)
                print(f"\n🔹 Event Content:")
                content = event['event_content']
                if len(content) > 300:
                    print(content[:300] + "...")
                else:
                    print(content)
                
                # Show context content
                print(f"\n🔗 Thread Context:")
                context = event['context_content']
                if len(context) > 800:
                    print(context[:800] + "...")
                else:
                    print(context)
                
                print("\n" + "=" * 80)
                
                # Limit to first 10 events for readability
                if i >= 10:
                    remaining = result['total_events'] - i
                    if remaining > 0:
                        print(f"\n... and {remaining} more events")
                    break
            
            # Save full results to file for inspection
            output_file = "npub_events_summary.json"
            with open(output_file, 'w') as f:
                json.dump(result, f, indent=2)
            print(f"\n💾 Full results saved to: {output_file}")
            
        else:
            print(f"❌ Failed to fetch events")
            print(f"Error: {result.get('error', 'Unknown error')}")
            
    except Exception as e:
        print(f"❌ Exception occurred: {str(e)}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())