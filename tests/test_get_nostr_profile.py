#!/usr/bin/env python3
import asyncio
import json
from nostr_mcp import get_nostr_profile

async def test_get_nostr_profile():
    """Test fetching profile metadata for the specified npub using get_nostr_profile"""
    npub = "npub1sg6plzptd64u62a878hep2kev88swjh3tw00gjsfl8f237lmu63q0uf63m"
    
    print(f"Fetching profile for {npub}")
    print("Using get_nostr_profile function")
    print("-" * 60)
    
    try:
        result_json = await get_nostr_profile(pubkey=npub)
        
        # Parse the JSON response
        result = json.loads(result_json)
        
        if result['success']:
            print("✅ Successfully fetched profile")
            print(f"Pubkey (hex): {result['pubkey']}")
            print(f"Pubkey (npub): {result['npub']}")
            print(f"Last updated: {result['last_updated']}")
            print(f"Event ID: {result['event_id']}")
            print()
            
            # Display profile information
            profile = result['profile']
            print("📄 Profile Information:")
            print("-" * 40)
            
            # Common profile fields
            profile_fields = [
                ('name', 'Name'),
                ('display_name', 'Display Name'),
                ('about', 'About'),
                ('picture', 'Picture URL'),
                ('banner', 'Banner URL'),
                ('website', 'Website'),
                ('nip05', 'NIP-05 Identifier'),
                ('lud16', 'Lightning Address (LUD-16)'),
                ('lud06', 'Lightning Address (LUD-06)')
            ]
            
            for field_key, field_label in profile_fields:
                if field_key in profile:
                    value = profile[field_key]
                    if field_key == 'about' and len(value) > 200:
                        # Truncate long about sections
                        print(f"{field_label}: {value[:200]}...")
                    else:
                        print(f"{field_label}: {value}")
            
            # Show any additional fields not in the common list
            additional_fields = {k: v for k, v in profile.items() 
                               if k not in [field[0] for field in profile_fields]}
            
            if additional_fields:
                print("\n🔧 Additional Profile Fields:")
                print("-" * 40)
                for key, value in additional_fields.items():
                    if isinstance(value, str) and len(value) > 100:
                        print(f"{key}: {value[:100]}...")
                    else:
                        print(f"{key}: {value}")
            
            # Show raw profile data if it's available
            if 'raw_content' in profile:
                print(f"\n⚠️  Raw content (failed to parse as JSON): {profile['raw_content']}")
                
        else:
            print(f"❌ Error: {result['error']}")
            
    except json.JSONDecodeError as e:
        print(f"❌ JSON Parse Error: {str(e)}")
    except Exception as e:
        print(f"❌ Exception: {str(e)}")

if __name__ == "__main__":
    asyncio.run(test_get_nostr_profile())