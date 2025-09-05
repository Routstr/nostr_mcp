#!/usr/bin/env python3
import asyncio
import json
from nostr_mcp import convert_pubkey_format

async def test_batch_convert_pubkeys():
    """Test batch conversion of multiple pubkeys"""
    
    # Test data with mixed npub and hex formats
    test_pubkeys = [
    '3a06add309fd8419ea4d4e475e9c0dff5909c635d9769bf0728232f3a0683a84',
    '50d94fc2d8580c682b071a542f8b1e31a200b0508bab95a33bef0855df281d63',
    '8bf629b3d519a0f8a8390137a445c0eb2f5f2b4a8ed71151de898051e8006f13',
    'e47d738ee8d9525a34aff86caea5c7bd57ea593a71d9b4754211650347ab1078',
    '40b9c85fffeafc1cadf8c30a4e5c88660ff6e4971a0dc723d5ab674b5e61b451',
    '82341f882b6eabcd2ba7f1ef90aad961cf074af15b9ef44a09f9d2a8fbfbe6a2',
    'c2622c916d9b90e10a81b2ba67b19bdfc5d6be26c25756d1f990d3785ce1361b',
    '0461fcbecc4c3374439932d6b8f11269ccdb7cc973ad7a50ae362db135a474dd',
    '088436cd039ff89074468fd327facf62784eeb37490e0a118ab9f14c9d2646cc',
    'b8f4c2e90f0dd667121533d7b8dafd77384b0b5051f8272e5493c58f7f93e14b'
    ]
    
    print("Testing batch pubkey conversion")
    print(f"Converting {len(test_pubkeys)} pubkeys")
    print("-" * 60)
    
    try:
        result_json = await convert_pubkey_format(pubkeys=test_pubkeys)
        
        # Parse the JSON response
        result = json.loads(result_json)
        
        if result['success']:
            print("✅ Batch conversion completed")
            print(f"Total processed: {result['total_processed']}")
            print(f"Successful conversions: {result['successful_conversions']}")
            print(f"Failed conversions: {result['failed_conversions']}")
            print()
            
            # Display individual results
            print("📊 Individual Results:")
            print("-" * 40)
            
            for i, conversion_result in enumerate(result['results'], 1):
                print(f"\nResult {i}:")
                print(f"  Input: {conversion_result['input']}")
                
                if conversion_result['success']:
                    print(f"  ✅ Success")
                    print(f"  Input format: {conversion_result['input_format']}")
                    print(f"  NPub: {conversion_result['npub']}")
                    print(f"  Hex: {conversion_result['hex']}")
                else:
                    print(f"  ❌ Failed: {conversion_result['error']}")
            
            # Show summary by format
            npub_conversions = [r for r in result['results'] if r['success'] and r.get('input_format') == 'npub']
            hex_conversions = [r for r in result['results'] if r['success'] and r.get('input_format') == 'hex']
            
            print(f"\n📈 Summary:")
            print(f"  NPub → Hex conversions: {len(npub_conversions)}")
            print(f"  Hex → NPub conversions: {len(hex_conversions)}")
            print(f"  Total errors: {result['failed_conversions']}")
                
        else:
            print(f"❌ Batch conversion failed: {result['error']}")
            
    except json.JSONDecodeError as e:
        print(f"❌ JSON Parse Error: {str(e)}")
    except Exception as e:
        print(f"❌ Exception: {str(e)}")

if __name__ == "__main__":
    asyncio.run(test_batch_convert_pubkeys())