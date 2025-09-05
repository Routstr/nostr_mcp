#!/usr/bin/env python3
import json
import asyncio
from typing import Optional, List, Dict, Any
import logging

logger = logging.getLogger("nostr-mcp")

def parse_e_tags(event_tags: List[List[str]]) -> Dict[str, List[str]]:
    """Parse e tags from an event to extract root and reply references.
    
    Args:
        event_tags: List of tag arrays from a nostr event
        
    Returns:
        Dict with 'root' and 'reply' lists containing event IDs
    """
    e_tags = {'root': [], 'reply': []}
    
    for tag in event_tags:
        if len(tag) >= 2 and tag[0] == 'e':
            event_id = tag[1]
            # Check if there's a marker (root/reply) in the tag
            if len(tag) >= 4:
                marker = tag[3]
                if marker == 'root':
                    e_tags['root'].append(event_id)
                elif marker == 'reply':
                    e_tags['reply'].append(event_id)
            else:
                # If no marker, treat as general reference
                e_tags['reply'].append(event_id)
    
    return e_tags

async def fetch_event_context(
    event_id: str,
    fetch_events_func,
    relays: Optional[List[str]] = None,
    max_depth: int = 50
) -> Dict[str, Any]:
    """Fetch the full context of a Nostr event by following the reply chain to the root.
    
    Args:
        event_id: The event ID to analyze
        fetch_events_func: Function to fetch events (should be fetch_nostr_events)
        relays: Optional list of relays to use
        max_depth: Maximum depth to prevent infinite loops
        
    Returns:
        Dict containing the event context, thread events, and summary
    """
    try:
        # Fetch the initial event
        result = await fetch_events_func(ids=[event_id], limit=1, relays=relays)
        parsed_result = json.loads(result)
        
        if not parsed_result['success'] or not parsed_result['events']:
            return {
                'success': False,
                'error': f'Event {event_id} not found',
                'nevent': event_id,
                'context': '',
                'no_of_events': 0,
                'events': []
            }
        
        initial_event = parsed_result['events'][0]
        thread_events = [initial_event]
        current_event = initial_event
        depth = 0
        
        # Follow the reply chain backwards to the root
        while depth < max_depth:
            e_tags = parse_e_tags(current_event['tags'])
            
            # If we have a reply tag, follow it
            if e_tags['reply']:
                # Get the most recent reply tag (last one)
                reply_id = e_tags['reply'][-1]
                
                # Fetch the replied-to event
                reply_result = await fetch_events_func(ids=[reply_id], limit=1, relays=relays)
                reply_parsed = json.loads(reply_result)
                
                if reply_parsed['success'] and reply_parsed['events']:
                    reply_event = reply_parsed['events'][0]
                    thread_events.append(reply_event)
                    current_event = reply_event
                    depth += 1
                    
                    # Check if this event only has root tags (we've reached the conversation start)
                    reply_e_tags = parse_e_tags(reply_event['tags'])
                    if not reply_e_tags['reply'] and reply_e_tags['root']:
                        # This event only replies to root, fetch the root event
                        root_id = reply_e_tags['root'][0]
                        root_result = await fetch_events_func(ids=[root_id], limit=1, relays=relays)
                        root_parsed = json.loads(root_result)
                        
                        if root_parsed['success'] and root_parsed['events']:
                            root_event = root_parsed['events'][0]
                            thread_events.append(root_event)
                        break
                    elif not reply_e_tags['reply'] and not reply_e_tags['root']:
                        # This is the root event itself
                        break
                else:
                    # Can't fetch the reply, stop here
                    break
            
            elif e_tags['root']:
                # Only root tag, fetch the root event
                root_id = e_tags['root'][0]
                root_result = await fetch_events_func(ids=[root_id], limit=1, relays=relays)
                root_parsed = json.loads(root_result)
                
                if root_parsed['success'] and root_parsed['events']:
                    root_event = root_parsed['events'][0]
                    thread_events.append(root_event)
                break
            else:
                # No e tags, this is likely the root event itself
                break
        
        # Sort events by timestamp (oldest first for chronological order)
        thread_events.sort(key=lambda e: e['created_at'])
        
        # Create formatted event list with indices
        formatted_events = []
        for i, event in enumerate(thread_events):
            formatted_events.append({
                'nevent': event['id'],
                'event_index': i + 1,
                'event_content': event['content'],
                'timestamp': event['created_at'],
                'pubkey': event['pubkey'],
                'kind': event['kind']
            })
        
        # Generate context summary
        context_parts = []
        if len(thread_events) == 1:
            context_parts.append("This is a standalone event with no conversation context.")
        else:
            context_parts.append(f"This is part of a conversation thread with {len(thread_events)} events.")
            
            # Find the original event (root)
            root_event = thread_events[0]
            context_parts.append(f"The conversation started with: \"{root_event['content'][:100]}...\"")
            
            if len(thread_events) > 2:
                context_parts.append(f"The thread contains {len(thread_events) - 1} replies.")
        
        context = " ".join(context_parts)
        
        return {
            'success': True,
            'nevent': event_id,
            'context': context,
            'no_of_events': len(thread_events),
            'events': formatted_events,
            'thread_events': thread_events  # Raw events for further processing
        }
        
    except Exception as e:
        logger.error(f"Error fetching event context: {str(e)}")
        return {
            'success': False,
            'error': str(e),
            'nevent': event_id,
            'context': '',
            'no_of_events': 0,
            'events': []
        }

def summarize_thread_context(context_result: Dict[str, Any]) -> str:
    """Generate a human-readable summary of the thread context.
    
    Args:
        context_result: Result from fetch_event_context
        
    Returns:
        Formatted summary string
    """
    if not context_result['success']:
        return f"Failed to fetch context: {context_result.get('error', 'Unknown error')}"
    
    events = context_result['events']
    if not events:
        return "No events found in context."
    
    summary_lines = [
        f"Event Context Analysis for {context_result['nevent']}",
        "=" * 50,
        f"Thread contains {context_result['no_of_events']} event(s)",
        "",
        context_result['context'],
        "",
        "Event Details:",
        "-" * 20
    ]
    
    for event in events:
        timestamp_str = f"({event['timestamp']})"
        content_preview = event['event_content'][:100] + "..." if len(event['event_content']) > 100 else event['event_content']
        summary_lines.append(f"{event['event_index']}. {timestamp_str} {content_preview}")
    
    return "\n".join(summary_lines)


import os
from glob import glob

def files_exist_for_timestamp(npub: str, since: int, curr_timestamp: int) -> bool:
    """
    Check if files already exist for the given npub, since, and curr_timestamp.
    
    Args:
        npub: The npub identifier (main_npub)
        since: The since timestamp
        curr_timestamp: The current timestamp
        
    Returns:
        True if files exist, False otherwise
    """
    base_dir = os.path.join(os.path.dirname(__file__), "mcp_responses")
    pattern = os.path.join(
        base_dir, f"*_{since}_{curr_timestamp}_{npub}.json"
    )
    files = glob(pattern)
    return len(files) > 0

def load_formatted_npub_output(npub: str, since: int, curr_timestamp: int) -> Dict[str, Any]:
   """
   Reads all matching files from mcp_responses and converts them into the required formatted output schema.
   Skips corrupted files safely.
   """
   base_dir = os.path.join(os.path.dirname(__file__), "mcp_responses")
   pattern = os.path.join(
       base_dir, f"*_{since}_{curr_timestamp}_{npub}.json"
   )
   files = glob(pattern)

   output = []
   for file in files:
       try:
           with open(file, "r") as f:
               data = json.load(f)
           npub_summary = {
               "npub": data.get("npub", ""),
               "name": data.get("name", ""),
               "profile_pic": data.get("profile_pic", ""),
               "events": []
           }
           for ev in data.get("events", []):
               npub_summary["events"].append({
                   "event_id": ev.get("event_id", ""),
                   "event_content": ev.get("event_content", ""),
                   "context_content": ev.get("context_content", ""),
                   "context_summary": ev.get("context_summary", ""),
                   "timestamp": ev.get("timestamp", 0),
                   "relevancy_score": ev.get("relevancy_score", 0),
                   "events_in_thread": ev.get("events_in_thread", []),
               })
           output.append(npub_summary)
       except Exception as e:
           logger.warning(f"Skipping corrupted file {file}: {e}")
           continue

   return {"output": output}

def get_param_file_path(npub: str, since: int, curr_timestamp: int) -> str:
    """
    Get the path for the parameter file that tracks running commands.
    
    Args:
        npub: The npub identifier
        since: The since timestamp
        curr_timestamp: The current timestamp
        
    Returns:
        Path to the parameter file
    """
    base_dir = os.path.join(os.path.dirname(__file__), "running_commands")
    os.makedirs(base_dir, exist_ok=True)
    return os.path.join(base_dir, f"{npub}_{since}_{curr_timestamp}.json")

def is_command_running(npub: str, since: int, curr_timestamp: int) -> bool:
    """
    Check if a command is currently running for the given parameters.
    
    Args:
        npub: The npub identifier
        since: The since timestamp
        curr_timestamp: The current timestamp
        
    Returns:
        True if command is running (parameter file exists), False otherwise
    """
    param_file = get_param_file_path(npub, since, curr_timestamp)
    return os.path.exists(param_file)

def mark_command_running(npub: str, since: int, curr_timestamp: int) -> None:
    """
    Mark a command as running by creating a parameter file.
    
    Args:
        npub: The npub identifier
        since: The since timestamp
        curr_timestamp: The current timestamp
    """
    param_file = get_param_file_path(npub, since, curr_timestamp)
    params = {
        "npub": npub,
        "since": since,
        "curr_timestamp": curr_timestamp,
        "started_at": json.dumps({"timestamp": curr_timestamp})
    }
    
    with open(param_file, "w") as f:
        json.dump(params, f, indent=2)

def mark_command_completed(npub: str, since: int, curr_timestamp: int) -> None:
    """
    Mark a command as completed by removing the parameter file.
    
    Args:
        npub: The npub identifier
        since: The since timestamp
        curr_timestamp: The current timestamp
    """
    param_file = get_param_file_path(npub, since, curr_timestamp)
    if os.path.exists(param_file):
        os.remove(param_file)