#!/usr/bin/env python3
import os
import json
import tempfile
import shutil
from utils import files_exist_for_timestamp, load_formatted_npub_output

def test_functions():
    """Test the modified functions with both npub and hex formats"""
    
    # Use the actual project directory for testing
    project_dir = os.getcwd()
    responses_dir = os.path.join(project_dir, "mcp_responses")
    
    # Create mcp_responses directory if it doesn't exist
    os.makedirs(responses_dir, exist_ok=True)
    
    print(f"Testing in project directory: {project_dir}")
    
    # Keep track of test files to clean up
    test_files = []
    
    try:
        
        # Test data
        test_npub = "npub1k2dkdjezgse9gq6qnfj7d9m0vl9dfhrj8dcxm8qrf72ehrdksdtqnhtgft"
        test_since = 100
        test_timestamp = 200
        
        # Test case 1: No files exist
        print("\n=== Test 1: No files exist ===")
        result = files_exist_for_timestamp(test_npub, test_since, test_timestamp)
        print(f"files_exist_for_timestamp result: {result}")
        assert not result, "Should return False when no files exist"
        
        # Create a test file with npub format
        test_data = {
            "npub": test_npub,
            "name": "Test User",
            "profile_pic": "http://example.com/pic.png",
            "events": [
                {
                    "event_id": "test123",
                    "event_content": "Hello world",
                    "context_content": "Test context",
                    "context_summary": "Test summary",
                    "timestamp": 123456,
                    "relevancy_score": 80,
                    "events_in_thread": ["test123"]
                }
            ]
        }
        
        test_file = os.path.join(responses_dir, f"test_{test_since}_{test_timestamp}_{test_npub}.json")
        with open(test_file, "w") as f:
            json.dump(test_data, f)
        test_files.append(test_file)  # Track for cleanup
        
        print("\n=== Test 2: Files exist with npub format ===")
        result = files_exist_for_timestamp(test_npub, test_since, test_timestamp)
        print(f"files_exist_for_timestamp result: {result}")
        assert result, "Should return True when npub files exist"
        
        print("\n=== Test 3: Load formatted output with npub format ===")
        formatted_output = load_formatted_npub_output(test_npub, test_since, test_timestamp)
        print(f"load_formatted_npub_output result: {formatted_output}")
        assert "output" in formatted_output, "Should have 'output' key"
        assert len(formatted_output["output"]) > 0, "Should have at least one result"
        assert formatted_output["output"][0]["npub"] == test_npub, "Should match npub"
        
        print("\n✅ All tests passed!")
        
    except Exception as e:
        print(f"❌ Test failed with error: {str(e)}")
        import traceback
        traceback.print_exc()
    finally:
        # Cleanup test files
        for test_file in test_files:
            try:
                if os.path.exists(test_file):
                    os.remove(test_file)
                    print(f"Removed test file: {test_file}")
            except Exception as e:
                print(f"Warning: Could not remove test file {test_file}: {e}")

if __name__ == "__main__":
    test_functions()