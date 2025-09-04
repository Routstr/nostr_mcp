#!/usr/bin/env python3
import os
import asyncio
import json
import time
import uuid
from typing import Optional, List, Dict, Any
import logging

from mcp.server.fastmcp import FastMCP
from pynostr.event import Event, EventKind
from pynostr.relay_manager import RelayManager
from pynostr.filters import FiltersList, Filters
from pynostr.key import PrivateKey, PublicKey
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Initialize Nostr configuration
MAIN_RELAYS = os.environ.get("MAIN_RELAYS", "wss://relay.damus.io,wss://nos.lol").split(",")
BACKUP_RELAYS = os.environ.get("BACKUP_RELAYS", "wss://multiplexer.huszonegy.world").split(",")
NOSTR_BOT_NSEC = os.environ.get("NOSTR_BOT_NSEC")
DEFAULT_TIMEOUT = int(os.environ.get("NOSTR_TIMEOUT", "10"))

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("nostr-mcp")

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
        used_relays = relays or (MAIN_RELAYS + BACKUP_RELAYS)
        
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
                    private_key = PrivateKey.from_nsec(NOSTR_BOT_NSEC)
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
async def get_nostr_profile(pubkey: str, relays: Optional[List[str]] = None) -> str:
    """Fetch a Nostr user's profile metadata."""
    try:
        # Convert pubkey format if needed
        try:
            if pubkey.startswith('npub'):
                pk = PublicKey.from_npub(pubkey)
                hex_pubkey = pk.hex()
            else:
                hex_pubkey = pubkey
        except Exception as e:
            return json.dumps({"error": f"Invalid pubkey format: {str(e)}"})
        
        # Fetch kind 0 (metadata) events for this pubkey
        result = await fetch_nostr_events(
            pubkey=hex_pubkey,
            kinds=[0],  # Metadata events
            limit=1,
            relays=relays
        )
        
        parsed_result = json.loads(result)
        
        if not parsed_result['success'] or not parsed_result['events']:
            return json.dumps({
                'success': False,
                'error': 'No profile found for this pubkey',
                'pubkey': pubkey
            })
        
        # Parse the metadata
        metadata_event = parsed_result['events'][0]
        try:
            profile_data = json.loads(metadata_event['content'])
        except json.JSONDecodeError:
            profile_data = {'raw_content': metadata_event['content']}
        
        return json.dumps({
            'success': True,
            'pubkey': hex_pubkey,
            'npub': PublicKey(bytes.fromhex(hex_pubkey)).npub(),
            'profile': profile_data,
            'last_updated': metadata_event['created_at'],
            'event_id': metadata_event['id']
        }, indent=2)
        
    except Exception as e:
        return json.dumps({
            'success': False,
            'error': f"Unexpected error: {str(e)}"
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
async def convert_pubkey_format(pubkey: str) -> str:
    """Convert between npub and hex pubkey formats.
    
    Args:
        pubkey: Public key in either npub or hex format
        
    Returns:
        JSON with both formats or error message
    """
    try:
        if pubkey.startswith('npub'):
            # Convert npub to hex
            try:
                pk = PublicKey.from_npub(pubkey)
                hex_format = pk.hex()
                return json.dumps({
                    'success': True,
                    'input': pubkey,
                    'input_format': 'npub',
                    'npub': pubkey,
                    'hex': hex_format
                }, indent=2)
            except Exception as e:
                return json.dumps({
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
                npub_format = pk.npub()
                return json.dumps({
                    'success': True,
                    'input': pubkey,
                    'input_format': 'hex',
                    'npub': npub_format,
                    'hex': pubkey
                }, indent=2)
            except Exception as e:
                return json.dumps({
                    'success': False,
                    'error': f'Invalid hex format: {str(e)}',
                    'input': pubkey
                })
    except Exception as e:
        return json.dumps({
            'success': False,
            'error': f'Unexpected error: {str(e)}',
            'input': pubkey
        })

if __name__ == "__main__":
    mcp.run()
