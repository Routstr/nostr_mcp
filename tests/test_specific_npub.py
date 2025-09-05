import os
import json
import pytest
from utils import load_formatted_npub_output

def test_load_formatted_npub_output(tmp_path):
    # Prepare a valid file
    valid_data = {
        "npub": "npub_test",
        "name": "Tester",
        "profile_pic": "http://example.com/pic.png",
        "events": [
            {
                "event_id": "abcd",
                "event_content": "hello world",
                "context_content": "root event",
                "context_summary": "summary",
                "timestamp": 123456,
                "relevancy_score": 80,
                "events_in_thread": ["abcd"]
            }
        ]
    }
    responses_dir = tmp_path / "mcp_responses"
    responses_dir.mkdir()
    json_file = responses_dir / "npub_test_100_200.json"
    with open(json_file, "w") as f:
        json.dump(valid_data, f)

    # Change working dir so utils finds it
    os.chdir(tmp_path)
    result = load_formatted_npub_output("npub_test", 100, 200)
    assert "output" in result
    assert result["output"][0]["npub"] == "npub_test"
    assert result["output"][0]["events"][0]["event_id"] == "abcd"

def test_corrupted_file_skipped(tmp_path):
    responses_dir = tmp_path / "mcp_responses"
    responses_dir.mkdir()
    json_file = responses_dir / "npub_bad_100_200.json"
    with open(json_file, "w") as f:
        f.write("{not valid json}")

    os.chdir(tmp_path)
    result = load_formatted_npub_output("npub_bad", 100, 200)
    assert result == {"output": []}