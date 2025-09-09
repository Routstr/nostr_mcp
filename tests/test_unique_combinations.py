import os
import json
from glob import glob
from typing import List, Dict, Any

def get_all_unique_combinations() -> List[Dict[str, Any]]:
    """
    Get all unique combinations of npub, since, and curr_timestamp from existing files.
    
    Returns:
        List of dictionaries containing unique combinations with keys:
        - npub: The npub identifier
        - since: The since timestamp
        - curr_timestamp: The current timestamp
        - filename: The original filename
    """
    base_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "mcp_responses")
    
    # Return empty list if directory doesn't exist
    if not os.path.exists(base_dir):
        return []
    
    pattern = os.path.join(base_dir, "*.json")
    files = glob(pattern)
    
    combinations = []
    seen_combinations = set()
    
    for file_path in files:
        filename = os.path.basename(file_path)
        
        # Expected pattern: *_{since}_{curr_timestamp}_{npub}.json
        # We need to parse this carefully since npub can contain underscores
        if filename.endswith('.json'):
            # Remove .json extension
            name_without_ext = filename[:-5]
            
            # Split by underscore and try to identify the pattern
            parts = name_without_ext.split('_')
            
            if len(parts) >= 4:
                # The last part should be npub
                npub = parts[-1]
                
                # The second to last should be curr_timestamp
                try:
                    curr_timestamp = int(parts[-2])
                except ValueError:
                    continue
                
                # The third to last should be since
                try:
                    since = int(parts[-3])
                except ValueError:
                    continue
                
                # Create a unique key for this combination
                combination_key = (npub, since, curr_timestamp)
                
                if combination_key not in seen_combinations:
                    seen_combinations.add(combination_key)
                    combinations.append({
                        'npub': npub,
                        'since': since,
                        'curr_timestamp': curr_timestamp,
                        'filename': filename
                    })
    
    # Sort by timestamp for consistent ordering
    combinations.sort(key=lambda x: (x['since'], x['curr_timestamp'], x['npub']))
    
    return combinations

def test_output_all_unique_combinations():
    """Test function that outputs all unique combinations found in mcp_responses."""
    combinations = get_all_unique_combinations()
    
    print(f"\nFound {len(combinations)} unique combinations:")
    print("=" * 80)
    
    for i, combo in enumerate(combinations, 1):
        print(f"{i:3d}. npub: {combo['npub']}")
        print(f"     since: {combo['since']}")
        print(f"     curr_timestamp: {combo['curr_timestamp']}")
        print(f"     filename: {combo['filename']}")
        print("-" * 40)
    
    # Also return for assertion testing
    return combinations

if __name__ == "__main__":
    # Run the test function when script is executed directly
    test_output_all_unique_combinations()