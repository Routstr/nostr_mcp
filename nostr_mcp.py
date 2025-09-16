#!/usr/bin/env python3
import os
import asyncio
import json
import time
import uuid
from typing import Optional, List, Dict, Any, cast
import logging
from datetime import datetime

from mcp.server.fastmcp import FastMCP
from pynostr.event import Event, EventKind
from pynostr.relay_manager import RelayManager
from pynostr.filters import FiltersList, Filters
from pynostr.key import PrivateKey, PublicKey
from dotenv import load_dotenv
from utils import fetch_event_context, summarize_thread_context, parse_e_tags, _extract_outbox_relays_from_kind10002, process_events_for_npub
from constants import blacklisted_relays

# Load environment variables
load_dotenv()

# Initialize Nostr configuration
MAIN_RELAYS = os.environ.get("MAIN_RELAYS", "wss://relay.damus.io,wss://nos.lol").split(",")
BACKUP_RELAYS = os.environ.get("BACKUP_RELAYS", "wss://multiplexer.huszonegy.world").split(",")
NOSTR_BOT_NSEC = os.environ.get("NOSTR_BOT_NSEC")
DEFAULT_TIMEOUT = int(os.environ.get("NOSTR_TIMEOUT", "10"))

# Set up logging: write to logs/YYYY-MM-DD.log and console
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOGS_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOGS_DIR, exist_ok=True)

today_str = datetime.now().strftime("%Y-%m-%d")
log_file_path = os.path.join(LOGS_DIR, f"{today_str}.log")

logger = logging.getLogger("nostr-mcp")
logger.setLevel(logging.INFO)
logger.propagate = False

if not logger.handlers:
    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.FileHandler(log_file_path)
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

# Create the FastMCP instance
mcp = FastMCP("nostr-mcp")

@mcp.tool()

async def fetch_nostr_events(
    pubkey: Optional[str] = None,
    kinds: Optional[List[int]] = None,
    since: Optional[int] = None,
    until: Optional[int] = None,
    limit: Optional[int] = 20,
    search: Optional[str] = None,
    tags: Optional[Dict[str, List[str]]] = None,
    authors: Optional[List[str]] = None,
    ids: Optional[List[str]] = None,
    relays: Optional[List[str]] = None
) -> str:
    """Fetch Nostr events from relays with comprehensive filtering options."""
    try:
        # Validate and set defaults
        if limit and limit > 100:
            limit = 100
        if limit and limit < 1:
            limit = 1
            
        used_timeout = DEFAULT_TIMEOUT
        # Filter out unwanted relays
        all_relays = relays or (MAIN_RELAYS + BACKUP_RELAYS)
        used_relays = [relay for relay in all_relays if relay not in blacklisted_relays]
        
        # Convert pubkey formats if needed
        processed_authors = []
        if pubkey:
            try:
                if pubkey.startswith('npub'):
                    pk = PublicKey.from_npub(pubkey)
                    processed_authors.append(pk.hex())
                else:
                    processed_authors.append(pubkey)
            except Exception as e:
                return json.dumps({"error": f"Invalid pubkey format: {str(e)}"})
        
        if authors:
            for author in authors:
                try:
                    if author.startswith('npub'):
                        pk = PublicKey.from_npub(author)
                        processed_authors.append(pk.hex())
                    else:
                        processed_authors.append(author)
                except Exception as e:
                    return json.dumps({"error": f"Invalid author pubkey format: {str(e)}"})

        def _fetch_events_sync():
            try:
                # Create relay manager
                relay_manager = RelayManager(timeout=used_timeout)
                for relay_url in used_relays:
                    relay_manager.add_relay(relay_url.strip())
                
                # Build filters
                filter_kwargs = {}
                
                if processed_authors:
                    filter_kwargs['authors'] = processed_authors
                if kinds:
                    filter_kwargs['kinds'] = kinds
                if since:
                    filter_kwargs['since'] = since
                if until:
                    filter_kwargs['until'] = until
                if limit:
                    filter_kwargs['limit'] = limit
                if ids:
                    filter_kwargs['ids'] = ids
                if search:
                    filter_kwargs['search'] = search
                
                # Handle tag filters
                if tags:
                    for tag_name, tag_values in tags.items():
                        if tag_name == 'p':
                            filter_kwargs['pubkey_refs'] = tag_values
                        elif tag_name == 'e':
                            filter_kwargs['event_refs'] = tag_values
                        else:
                            # For other tags, we'll need to filter manually after fetching
                            pass
                
                filters = FiltersList([Filters(**filter_kwargs)])
                
                subscription_id = uuid.uuid1().hex
                relay_manager.add_subscription_on_all_relays(subscription_id, filters)
                relay_manager.run_sync()
                
                # Collect events
                events = []
                notices = []
                
                # Check for notices
                while relay_manager.message_pool.has_notices():
                    notice_msg = relay_manager.message_pool.get_notice()
                    notices.append(notice_msg.content)
                
                # Collect events
                while relay_manager.message_pool.has_events():
                    event_msg = relay_manager.message_pool.get_event()
                    event = event_msg.event
                    
                    # Additional filtering for search and custom tags
                    if search and search.lower() not in event.content.lower():
                        continue
                    
                    # Filter by custom tags if specified
                    if tags:
                        tag_match = True
                        for tag_name, tag_values in tags.items():
                            if tag_name not in ['p', 'e']:  # These are handled by pynostr
                                event_has_tag = False
                                for event_tag in event.tags:
                                    if len(event_tag) >= 2 and event_tag[0] == tag_name:
                                        if event_tag[1] in tag_values:
                                            event_has_tag = True
                                            break
                                if not event_has_tag:
                                    tag_match = False
                                    break
                        if not tag_match:
                            continue
                    
                    events.append({
                        'id': event.id,
                        'pubkey': event.pubkey,
                        'created_at': event.created_at,
                        'kind': event.kind,
                        'content': event.content,
                        'tags': event.tags,
                        'sig': event.sig
                    })
                
                relay_manager.close_all_relay_connections()
                
                # Sort events by creation time (newest first)
                events.sort(key=lambda e: e['created_at'], reverse=True)
                
                return {
                    'success': True,
                    'events': events,
                    'count': len(events),
                    'notices': notices,
                    'relays_used': used_relays,
                    'filters_applied': filter_kwargs
                }
                
            except Exception as e:
                return {
                    'success': False,
                    'error': str(e),
                    'events': [],
                    'count': 0
                }
        
        # Run the synchronous function in a thread
        result = await asyncio.to_thread(_fetch_events_sync)
        return json.dumps(result, indent=2)
        
    except Exception as e:
        return json.dumps({
            'success': False,
            'error': f"Unexpected error: {str(e)}",
            'events': [],
            'count': 0
        })

@mcp.tool()
async def publish_nostr_event(
    content: str,
    kind: Optional[int] = 1,
    tags: Optional[List[List[str]]] = None,
    relays: Optional[List[str]] = None
) -> str:
    """Publish a Nostr event to relays."""
    try:
        if not NOSTR_BOT_NSEC:
            return json.dumps({
                'success': False,
                'error': 'NOSTR_BOT_NSEC environment variable not set'
            })
        
        used_timeout = DEFAULT_TIMEOUT
        used_relays = relays or (MAIN_RELAYS + BACKUP_RELAYS)
        used_tags = tags or []
        
        def _publish_sync():
            try:
                # Parse private key
                try:
                    private_key = PrivateKey.from_nsec(cast(str, NOSTR_BOT_NSEC))
                except ValueError as e:
                    return {
                        'success': False,
                        'error': f'Invalid NOSTR_BOT_NSEC format: {str(e)}'
                    }
                
                # Create relay manager
                relay_manager = RelayManager(timeout=used_timeout)
                for relay_url in used_relays:
                    relay_manager.add_relay(relay_url.strip())
                
                # Create event
                event = Event(
                    content=content,
                    tags=used_tags,
                    kind=kind
                )
                event.sign(private_key.hex())
                
                # Publish event
                relay_manager.publish_event(event)
                relay_manager.run_sync()
                time.sleep(3)  # Allow time for publishing
                
                # Check for OK notices
                ok_notices = []
                error_notices = []
                
                while relay_manager.message_pool.has_ok_notices():
                    ok_msg = relay_manager.message_pool.get_ok_notice()
                    if "True" in str(ok_msg):
                        ok_notices.append(str(ok_msg))
                    else:
                        error_notices.append(str(ok_msg))
                
                relay_manager.close_all_relay_connections()
                
                return {
                    'success': True,
                    'event_id': event.id,
                    'event': {
                        'id': event.id,
                        'pubkey': event.pubkey,
                        'created_at': event.created_at,
                        'kind': event.kind,
                        'content': event.content,
                        'tags': event.tags,
                        'sig': event.sig
                    },
                    'relays_used': used_relays,
                    'ok_notices': ok_notices,
                    'error_notices': error_notices
                }
                
            except Exception as e:
                return {
                    'success': False,
                    'error': str(e)
                }
        
        # Run the synchronous function in a thread
        result = await asyncio.to_thread(_publish_sync)
        return json.dumps(result, indent=2)
        
    except Exception as e:
        return json.dumps({
            'success': False,
            'error': f"Unexpected error: {str(e)}"
        })

@mcp.tool()
async def get_nostr_profiles(pubkeys: List[str], relays: Optional[List[str]] = None) -> str:
    """Fetch Nostr user profile metadata for multiple pubkeys.

    Args:
        pubkeys: List of public keys in npub or hex format
        relays: Optional list of relay URLs to query

    Returns:
        JSON string with a list under 'profiles', preserving input order.
    """
    try:
        # Normalize inputs to hex and retain mapping and per-input errors
        inputs: List[Dict[str, Any]] = []
        processed_authors: List[str] = []

        for original in pubkeys:
            entry: Dict[str, Any] = {'input': original}
            try:
                if isinstance(original, str) and original.startswith('npub'):
                    pk = PublicKey.from_npub(original)
                    hex_pk = pk.hex()
                    entry['pubkey'] = hex_pk
                else:
                    # Basic validation for hex length; detailed errors will surface later if invalid
                    hex_pk = original
                    entry['pubkey'] = hex_pk
                processed_authors.append(hex_pk)
            except Exception as e:
                entry['error'] = f'Invalid pubkey format: {str(e)}'
            inputs.append(entry)

        # If none are valid, short-circuit
        if not processed_authors:
            return json.dumps({
                'success': True,
                'count': 0,
                'profiles': [
                    {
                        'success': False,
                        'input': it.get('input'),
                        'error': it.get('error', 'Invalid pubkey')
                    } for it in inputs
                ]
            }, indent=2)

        # Fetch kind 0 metadata for all authors at once
        raw = await fetch_nostr_events(
            authors=processed_authors,
            kinds=[0],
            relays=relays
        )

        parsed = json.loads(raw)
        if not parsed.get('success'):
            return json.dumps({
                'success': False,
                'error': parsed.get('error', 'Failed to fetch metadata'),
                'profiles': []
            }, indent=2)

        events = parsed.get('events', []) or []

        # Take the newest metadata per pubkey (events are already sorted newest-first)
        latest_by_pubkey: Dict[str, Dict[str, Any]] = {}
        for ev in events:
            pk_hex = ev.get('pubkey')
            if not pk_hex:
                continue
            if pk_hex not in latest_by_pubkey:
                latest_by_pubkey[pk_hex] = ev

        outputs: List[Dict[str, Any]] = []
        for it in inputs:
            original = it.get('input')
            hex_pk = it.get('pubkey')
            prior_err = it.get('error')

            if prior_err or not hex_pk:
                outputs.append({
                    'success': False,
                    'input': original,
                    'error': prior_err or 'Invalid pubkey'
                })
                continue

            ev = latest_by_pubkey.get(hex_pk)
            if not ev:
                # Provide npub conversion if possible even when not found
                try:
                    npub_format = PublicKey(bytes.fromhex(hex_pk)).npub
                except Exception:
                    npub_format = f'hex:{hex_pk}'
                outputs.append({
                    'success': False,
                    'input': original,
                    'pubkey': hex_pk,
                    'npub': npub_format,
                    'error': 'No profile found for this pubkey'
                })
                continue

            # Parse profile content
            content = ev.get('content', '') or ''
            try:
                profile_data = json.loads(content)
            except json.JSONDecodeError:
                profile_data = {'raw_content': content}

            # Convert back to npub for convenience
            try:
                npub_format = PublicKey(bytes.fromhex(hex_pk)).npub
            except Exception:
                npub_format = f'hex:{hex_pk}'

            outputs.append({
                'success': True,
                'input': original,
                'pubkey': hex_pk,
                'npub': npub_format,
                'profile': profile_data,
                'last_updated': ev.get('created_at'),
                'event_id': ev.get('id')
            })

        return json.dumps({
            'success': True,
            'count': len(outputs),
            'profiles': outputs,
            'relays_used': parsed.get('relays_used')
        }, indent=2)

    except Exception as e:
        return json.dumps({
            'success': False,
            'error': f"Unexpected error: {str(e)}",
            'profiles': []
        })

@mcp.tool()
async def search_nostr_content(
    query: str,
    kinds: Optional[List[int]] = None,
    since: Optional[int] = None,
    until: Optional[int] = None,
    limit: Optional[int] = 20,
    relays: Optional[List[str]] = None
) -> str:
    """Search for Nostr events containing specific text."""
    try:
        search_kinds = kinds or [1]  # Default to text notes
        
        result = await fetch_nostr_events(
            kinds=search_kinds,
            since=since,
            until=until,
            limit=limit,
            search=query,
            relays=relays
        )
        
        return result
        
    except Exception as e:
        return json.dumps({
            'success': False,
            'error': f"Search error: {str(e)}",
            'events': [],
            'count': 0
        })

@mcp.tool()
async def fetch_outbox_relays(pubkey: str) -> List[str]:
    """Return outbox (write) relays from user's kind 10002 event.

    Args:
        pubkey: npub or hex public key

    Returns:
        List of relay URLs (strings). Empty list if none or on error.
    """
    try:
        hex_pubkey = pubkey
        if pubkey.startswith('npub'):
            try:
                pk = PublicKey.from_npub(pubkey)
                hex_pubkey = pk.hex()
            except Exception:
                return []

        raw = await fetch_nostr_events(pubkey=hex_pubkey, kinds=[10002], limit=1)
        parsed = json.loads(raw)
        if not parsed.get('success') or not parsed.get('events'):
            return []
        latest = parsed['events'][0]
        relays = _extract_outbox_relays_from_kind10002(latest)
        return relays
    except Exception:
        return []

@mcp.tool()
async def convert_pubkey_format(pubkeys: List[str]) -> str:
    """Convert between npub and hex pubkey formats for multiple pubkeys in batch.
    
    Args:
        pubkeys: List of public keys in either npub or hex format
        
    Returns:
        JSON with conversion results for all pubkeys
    """
    try:
        results = []
        
        for pubkey in pubkeys:
            try:
                if pubkey.startswith('npub'):
                    # Convert npub to hex
                    try:
                        pk = PublicKey.from_npub(pubkey)
                        hex_format = pk.hex()
                        results.append({
                            'success': True,
                            'input': pubkey,
                            'input_format': 'npub',
                            'npub': pubkey,
                            'hex': hex_format
                        })
                    except Exception as e:
                        results.append({
                            'success': False,
                            'error': f'Invalid npub format: {str(e)}',
                            'input': pubkey
                        })
                else:
                    # Assume hex format, convert to npub
                    try:
                        # Validate hex format
                        if len(pubkey) != 64:
                            raise ValueError("Hex pubkey must be 64 characters long")
                        
                        # Try to create PublicKey from hex
                        pk = PublicKey(bytes.fromhex(pubkey))
                        try:
                            npub_format = pk.npub
                        except Exception:
                            npub_format = f"hex:{pubkey}"
                        results.append({
                            'success': True,
                            'input': pubkey,
                            'input_format': 'hex',
                            'npub': npub_format,
                            'hex': pubkey
                        })
                    except Exception as e:
                        results.append({
                            'success': False,
                            'error': f'Invalid hex format: {str(e)}',
                            'input': pubkey
                        })
            except Exception as e:
                results.append({
                    'success': False,
                    'error': f'Unexpected error: {str(e)}',
                    'input': pubkey
                })
        
        # Calculate summary statistics
        successful_conversions = len([r for r in results if r['success']])
        failed_conversions = len([r for r in results if not r['success']])
        
        return json.dumps({
            'success': True,
            'total_processed': len(pubkeys),
            'successful_conversions': successful_conversions,
            'failed_conversions': failed_conversions,
            'results': results
        }, indent=2)
        
    except Exception as e:
        return json.dumps({
            'success': False,
            'error': f'Unexpected error processing batch: {str(e)}',
            'total_processed': 0,
            'successful_conversions': 0,
            'failed_conversions': 0,
            'results': []
        })

@mcp.tool()
async def fetch_event_thread_context(event_id: str, relays: Optional[List[str]] = None) -> str:
    """Fetch the full conversation context for a Nostr event by following reply chains to the root.
    
    Args:
        event_id: The event ID to analyze for context
        relays: Optional list of relays to use for fetching events
        
    Returns:
        JSON string containing thread context, events, and summary
    """
    try:
        # Use the fetch_event_context function from utils
        result = await fetch_event_context(
            event_id=event_id,
            fetch_events_func=fetch_nostr_events,
            relays=relays
        )
        
        # Add formatted summary for easy reading
        if result['success']:
            result['formatted_summary'] = summarize_thread_context(result)
        
        return json.dumps(result, indent=2)
        
    except Exception as e:
        return json.dumps({
            'success': False,
            'error': f"Unexpected error: {str(e)}",
            'nevent': event_id,
            'context': '',
            'no_of_events': 0,
            'events': []
        })

@mcp.tool()
async def get_events_for_summary(
    pubkey: str,
    since: int,
    relays: Optional[List[str]] = None
) -> Dict[str, Any]:
    """Fetch events for a user since a timestamp and include full thread context for replies.
    
    Args:
        pubkey: npub or hex public key
        since: Unix timestamp to fetch events from
        relays: Optional list of relays (if not provided, will fetch from user's kind 10002)
        
    Returns:
        Dict containing formatted events with thread context
    """
    try:
        # Convert pubkey to hex if needed
        hex_pubkey = pubkey
        if pubkey.startswith('npub'):
            pk = PublicKey.from_npub(pubkey)
            hex_pubkey = pk.hex()
        
        # Use provided relays or fallback to default relays
        used_relays = relays or (MAIN_RELAYS + BACKUP_RELAYS)
        
        # Prepare profile fields; fill after fetching kind 1 if any
        name = ""
        profile_pic = ""
        
        # Fetch only kind 1 events (text notes) from this user since the timestamp
        events_result = await fetch_nostr_events(
            pubkey=hex_pubkey,
            kinds=[1],
            since=since,
        )
        events_parsed = json.loads(events_result)
        
        if not events_parsed['success']:
            return {
                'success': False,
                'error': events_parsed.get('error', 'Failed to fetch events'),
                'events': []
            }
        
        formatted_events = []
        processed_threads = set()  # Track which threads we've already processed
        
        for event in events_parsed['events']:
            event_id = event['id']
            
            # Check if this event has e tags (is part of a thread)
            e_tags = parse_e_tags(event['tags'])
            
            if e_tags['root'] or e_tags['reply']:
                # This event is part of a thread, fetch full context
                # Skip if we've already processed this thread
                thread_root = e_tags['root'][0] if e_tags['root'] else None
                if thread_root and thread_root in processed_threads:
                    continue
                
                context_result = await fetch_event_context(
                    event_id=event_id,
                    fetch_events_func=fetch_nostr_events,
                    relays=used_relays,
                    initial_event=event
                )
                
                if context_result['success'] and context_result['thread_events']:
                    # Mark this thread as processed
                    if thread_root:
                        processed_threads.add(thread_root)
                    
                    # Build the context content string
                    thread_events = context_result['thread_events']
                    
                    # Find the root event (first in chronological order)
                    root_event = thread_events[0] if thread_events else None
                    
                    # Find the original event in the thread
                    original_event = None
                    for te in thread_events:
                        if te['id'] == event_id:
                            original_event = te
                            break
                    
                    # Build context content
                    context_parts = []
                    
                    # Add root event
                    if root_event:
                        context_parts.append(f"Root event that started the thread: {root_event['content']}")
                    
                    # Add reply events (excluding root and original)
                    reply_events = [te for te in thread_events if te['id'] != event_id and (not root_event or te['id'] != root_event['id'])]
                    if reply_events:
                        context_parts.append("Reply events:")
                        for reply in reply_events:
                            context_parts.append(reply['content'])
                    
                    # Add original event
                    if original_event:
                        context_parts.append(f"Original Event: {original_event['content']}")
                    
                    context_content = "\n".join(context_parts)
                    
                    # Format the event with context
                    formatted_event = {
                        'event_id': event_id,
                        'event_content': event['content'],
                        'timestamp': event['created_at'],
                        'context_content': context_content,
                        'events_in_thread': [te['id'] for te in thread_events],
                        'pubkey': event['pubkey'],
                        'kind': event['kind']
                    }
                    formatted_events.append(formatted_event)
            else:
                # Standalone event without thread context
                formatted_event = {
                    'event_id': event_id,
                    'event_content': event['content'],
                    'timestamp': event['created_at'],
                    'context_content': "Standalone event (not part of a thread)",
                    'events_in_thread': [event_id],
                    'pubkey': event['pubkey'],
                    'kind': event['kind']
                }
                formatted_events.append(formatted_event)
        
        # After fetching kind 1 events, fetch metadata only if there are events
        if events_parsed.get('events'):
            try:
                metadata_raw = await fetch_nostr_events(
                    pubkey=hex_pubkey,
                    kinds=[0],
                    limit=1,
                    relays=used_relays
                )
                metadata_parsed = json.loads(metadata_raw)
                if metadata_parsed.get('success') and metadata_parsed.get('events'):
                    latest_md = metadata_parsed['events'][0]
                    try:
                        md_content = json.loads(latest_md.get('content', '') or '{}')
                        name = md_content.get('display_name') or md_content.get('name') or ""
                        profile_pic = md_content.get('picture') or md_content.get('image') or ""
                    except Exception:
                        pass
            except Exception:
                pass

        return {
            'success': True,
            'pubkey': hex_pubkey,
            'since_timestamp': since,
            'relays_used': used_relays,
            'total_events': len(formatted_events),
            'events': formatted_events,
            'name': name,
            'profile_pic': profile_pic
        }
        
    except Exception as e:
        logger.error(f"Error in get_events_for_summary: {str(e)}")
        return {
            'success': False,
            'error': str(e),
            'events': []
        }

@mcp.tool()
async def get_events_for_summary_multi(
    authors: List[str],
    since: int,
    relays: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Fetch events for multiple authors since a timestamp with thread context.
    
    Returns a layers JSON where each layer corresponds to one author and
    contains their events in the same formatted shape as `get_events_for_summary`.
    """
    try:
        used_relays = relays or (MAIN_RELAYS + BACKUP_RELAYS)

        # Single fetch across all authors using the authors filter
        raw = await fetch_nostr_events(
            authors=authors,
            kinds=[1],
            since=since,
            relays=used_relays
        )
        parsed = json.loads(raw)

        if not parsed.get('success'):
            return {
                'success': False,
                'error': parsed.get('error', 'Failed to fetch events'),
                'output': []
            }

        events = parsed.get('events', []) or []

        # Group events by author (hex pubkey)
        events_by_pubkey: Dict[str, List[Dict[str, Any]]] = {}
        for ev in events:
            pk = ev.get('pubkey')
            if not pk:
                continue
            events_by_pubkey.setdefault(pk, []).append(ev)

        # Build layers using shared utility
        layers: List[Dict[str, Any]] = await process_events_for_npub(
            authors=authors,
            since=since,
            used_relays=used_relays,
            events_by_pubkey=events_by_pubkey,
            fetch_events_func=fetch_nostr_events,
        )

        layers = [ layer for layer in layers if layer.get('success') and layer.get('total_events', 0) > 0]
        authors = [layer.get('pubkey') for layer in layers]
        # Batch-fetch metadata (kind 0) for authors that have events
        latest_md_by_pubkey: Dict[str, Dict[str, Any]] = {}
        if authors:
            try:
                md_raw = await fetch_nostr_events(
                    authors=authors,
                    kinds=[0],
                    relays=used_relays
                )
                md_parsed = json.loads(md_raw)
                if md_parsed.get('success'):
                    md_events = md_parsed.get('events', []) or []
                    for ev in md_events:
                        pk_hex = ev.get('pubkey')
                        if not pk_hex:
                            continue
                        # Take the newest only; events are sorted newest-first
                        if pk_hex not in latest_md_by_pubkey:
                            latest_md_by_pubkey[pk_hex] = ev
            except Exception:
                pass

        # Fill metadata into layers
        for layer in layers:
            if not layer.get('success'):
                continue
            pk_hex = layer.get('pubkey')
            ev = latest_md_by_pubkey.get(pk_hex)
            if not ev:
                continue
            try:
                md_content = json.loads(ev.get('content', '') or '{}')
                layer['name'] = md_content.get('display_name') or md_content.get('name') or ''
                layer['profile_pic'] = md_content.get('picture') or md_content.get('image') or ''
            except Exception:
                # Keep defaults on parse error
                pass

        total_events = sum(layer.get('total_events', 0) for layer in layers if layer.get('success'))
        failed_authors = [layer.get('author_input') for layer in layers if not layer.get('success')]

        return {
            'success': True,
            'since_timestamp': since,
            'relays_used': used_relays,
            'total_layers': len(layers),
            'total_events': total_events,
            'output': layers,
            'failed_authors': failed_authors
        }
    except Exception as e:
        logger.error(f"Error in get_events_for_summary_multi: {str(e)}")
        return {
            'success': False,
            'error': str(e),
            'output': []
        }

@mcp.tool()
async def fetch_and_store(
    npub_or_hex: str,
    since: Optional[int] = None,
    till: Optional[int] = None,
    base_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Collect outbox relays, following, and summaries, and optionally store in SQLite.

    - Collects via `fetch_and_store.collect_all_data_for_npub`.
    - If `base_dir` is provided, writes to `<base_dir>/goose.db` using `sqlite_store`.
    """
    try:
        logger.info(
            f"fetch_and_store start: npub_or_hex={npub_or_hex}, since={since}, till={till}, base_dir={(base_dir or '')}"
        )
        # Import at runtime to avoid circular import during module load
        from fetch_and_store import collect_all_data_for_npub as _collect

        collected = await _collect(npub_or_hex, since=since, till=till)

        # Log concise metrics (no large payloads)
        try:
            seed_info_safe = collected.get('input', {}) if isinstance(collected, dict) else {}
            since_ts_safe = seed_info_safe.get('since')
            till_ts_safe = seed_info_safe.get('till')
            relays_list_safe = collected.get('outbox_relays', []) if isinstance(collected, dict) else []
            follows_count_safe = int(collected.get('following_count', 0)) if isinstance(collected, dict) else 0
            follows_list_safe = collected.get('following', []) if isinstance(collected, dict) else []
            summaries_safe = collected.get('summaries_by_hex', {}) if isinstance(collected, dict) else {}
            authors_with_summaries = len(summaries_safe) if isinstance(summaries_safe, dict) else 0
            total_events_safe = 0
            if isinstance(summaries_safe, dict):
                for v in summaries_safe.values():
                    try:
                        total_events_safe += len((v or {}).get('events', []) or [])
                    except Exception:
                        pass
            logger.info(
                "fetch_and_store metrics: since=%s, till=%s, relays=%d, follows=%d (list_len=%d), authors_with_summaries=%d, total_events=%d",
                since_ts_safe,
                till_ts_safe,
                len(relays_list_safe),
                follows_count_safe,
                len(follows_list_safe) if isinstance(follows_list_safe, list) else 0,
                authors_with_summaries,
                total_events_safe,
            )
        except Exception as _log_err:
            logger.warning(f"fetch_and_store metrics logging failed: {str(_log_err)}")

        db_write = False
        db_error: Optional[str] = None

        db_path: Optional[str] = None
        if base_dir:
            try:
                import os as _os
                db_path = _os.path.join(base_dir, "goose.db")
            except Exception as e:
                db_error = f"failed to resolve db_path from base_dir: {str(e)}"
                db_path = None

        print(f"collected: {collected}")
        if db_path:
            try:
                from sqlite_store import (
                    get_connection,
                    initialize_database,
                    store_collected_data,
                )
            except Exception as e:
                db_error = f"sqlite_store not available: {str(e)}"
            else:
                try:
                    conn = get_connection(db_path)
                    try:
                        initialize_database(conn)
                        store_collected_data(conn, collected=collected)
                        db_write = True
                    finally:
                        try:
                            conn.close()
                        except Exception:
                            pass
                except Exception as e:
                    db_error = str(e)

        # DB write summary (no large payloads)
        logger.info(
            "fetch_and_store DB: path=%s, wrote=%s, had_error=%s",
            db_path,
            str(db_write),
            str(bool(db_error)),
        )

        # Build compact summary similar to get_events_for_summary_multi shape
        seed_info = collected.get('input', {}) if isinstance(collected, dict) else {}
        since_ts = seed_info.get('since')
        summaries = collected.get('summaries_by_hex', {}) if isinstance(collected, dict) else {}

        layers: List[Dict[str, Any]] = []
        total_events = 0
        for hex_author, data in summaries.items():
            events_list = (data or {}).get('events', []) or []
            layer = {
                'success': True,
                'author_input': (data or {}).get('npub') or hex_author,
                'pubkey': hex_author,
                'since_timestamp': since_ts,
                'name': (data or {}).get('name', ''),
                'profile_pic': (data or {}).get('profile_pic', ''),
                'total_events': len(events_list),
                'events': events_list,
            }
            total_events += len(events_list)
            layers.append(layer)

        summary_response: Dict[str, Any] = {
            'success': True,
            'since_timestamp': since_ts,
            'total_events': total_events,
            'db_path': db_path,
            'db_write': db_write,
        }
        if db_error:
            summary_response['db_error'] = db_error
        return summary_response
    except Exception as e:
        logger.error(f"Error in fetch_and_store tool: {str(e)}")
        return {
            'success': False,
            'error': str(e)
        }

@mcp.tool()
async def summarize_and_add_relevancy_score(
    instruction: str,
    npub: str,
    since: Optional[int] = None,
    till: Optional[int] = None,
    max_concurrency: int = 20,
    base_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Summarize events and add relevancy scores for a user's events in goose.db.

    Runs inline without threading to avoid MCP transport issues.
    """
    try:
        # Start log with concise parameters (avoid large payloads)
        logger.info(
            "summarize_and_add_relevancy_score start: npub=%s, since=%s, till=%s, max_concurrency=%s, base_dir=%s, instruction_len=%d",
            npub,
            str(since),
            str(till),
            str(max_concurrency),
            (base_dir or ""),
            len(instruction or ""),
        )
        # Import the function directly and run it in the current context
        # This avoids any threading issues with the MCP transport layer
        from fetch_and_store import summarize_and_add_relevancy_score as _summarize
        
        # Run the async function directly
        result = await _summarize(
            instruction=instruction,
            npub=npub,
            since=since,
            till=till,
            max_concurrency=max_concurrency,
            base_dir=base_dir,
            logger=logger,
        )
        # Log concise metrics from result
        try:
            success_safe = bool(result.get('success')) if isinstance(result, dict) else False
            processed_safe = int(result.get('processed', 0)) if isinstance(result, dict) else 0
            updated_safe = int(result.get('updated', 0)) if isinstance(result, dict) else 0
            skipped_safe = int(result.get('skipped', 0)) if isinstance(result, dict) else 0
            details_len_safe = len(result.get('details', []) or []) if isinstance(result, dict) else 0
            logger.info(
                "summarize_and_add_relevancy_score metrics: success=%s, processed=%d, updated=%d, skipped=%d, details_len=%d",
                str(success_safe),
                processed_safe,
                updated_safe,
                skipped_safe,
                details_len_safe,
            )
        except Exception as _log_err:
            logger.warning(f"summarize_and_add_relevancy_score metrics logging failed: {str(_log_err)}")

        return result
    except Exception as e:
        logger.error(f"Error in summarize_and_add_relevancy_score tool: {str(e)}")
        return {
            'success': False,
            'error': str(e)
        }

if __name__ == "__main__":
    mcp.run()
