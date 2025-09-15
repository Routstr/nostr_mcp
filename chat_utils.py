#!/usr/bin/env python3
import asyncio
import os
from typing import Any, Dict, List, Optional
import json

import aiohttp

import sqlite_store
from utils import get_api_base_url_and_model


def build_messages(instruction: str, user_input: str, include_summary: bool) -> List[Dict[str, str]]:
    if include_summary:
        system_prompt = (
            "This is the content of a Nostr event or its thread context you're supposed to score:" + user_input + "\n"
            "Return a strict JSON object with keys: \n"
            "- context_summary: short summary of the context content/thread YOU MUST MAKE SURE YOU SUMMARIZE THE CONTENT/THREAD (<= 280 chars).\n"
            "- relevancy_score: score of 0-100 indicating relevance to the instruction:" + instruction + ". 100 if the content is exactly what the instruction is asking for, 0 if the content is not relevant to the instruction. if the content is relevant but not exactly what the instruction is asking for, give a score between 0 and 100 based on how relevant it is to the instruction. \n"
            "- reason_for_score: reason for the score given based on the instruction \n"
            "No additional text."
        )
    else:
        system_prompt = (
            "This is the content of a single Nostr event you're supposed to score (no thread context):" + user_input + "\n"
            "Return a strict JSON object with two keys: relevancy_score and reason_for_score \n"
            "- relevancy_score: score of 0-100 indicating relevance to the instruction: " + instruction + ". 100 if the content is exactly what the instruction is asking for, 0 if the content is not relevant to the instruction. if the content is relevant but not exactly what the instruction is asking for, give a score between 0 and 100 based on how relevant it is to the instruction. \n"
            "- reason_for_score: reason for the score given based on the instruction \n"
            "No additional text."
        )
    return [
        {"role": "user", "content": system_prompt}
    ]


def build_response_format(include_summary: bool) -> Dict[str, Any]:
    if include_summary:
        return {
            "type": "json_schema",
            "json_schema": {
                "name": "summary_relevancy_score_and_reason",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "context_summary": {
                            "type": "string",
                            "description": "Short summary (<= 280 chars) of content/thread",
                        },
                        "relevancy_score": {
                            "type": "number",
                            "description": "Score 0-100 for relevance to the instruction",
                        },
                        "reason_for_score": {
                            "type": "string",
                            "description": "Reason for the score given based on the instruction",
                        },
                    },
                    "required": ["context_summary", "relevancy_score", "reason_for_score"],
                    "additionalProperties": False,
                },
            },
        }
    else:
        return {
            "type": "json_schema",
            "json_schema": {
                "name": "relevancy_score_and_reason",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "relevancy_score": {
                            "type": "number",
                            "description": "Score 0-100 for relevance to the instruction",
                        },
                        "reason_for_score": {
                            "type": "string",
                            "description": "Reason for the score given based on the instruction",
                        },
                    },
                    "required": ["relevancy_score", "reason_for_score"],
                    "additionalProperties": False,
                },
            },
        }


async def post_chat_completion(
    session: aiohttp.ClientSession,
    api_base_url: str,
    api_key_value: Optional[str],
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    if not api_base_url:
        raise ValueError("OpenAI-compatible API base URL is not set.")
    url = api_base_url.rstrip("/") + "/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        **({"Authorization": f"Bearer {api_key_value}"} if api_key_value else {}),
    }
    try:
        async with session.post(url, json=payload, headers=headers) as resp:
            body = await resp.text()
            if not body.strip():
                raise ValueError("Empty response from API")
            if resp.status >= 400:
                raise ValueError(f"HTTP {resp.status} error from API: {resp.reason}. Body: {body[:500]}")
            try:
                return json.loads(body)
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON response from API: {e}. Response body: {body[:500]}")
    except aiohttp.ClientError as e:
        raise ValueError(f"Network error connecting to API: {e}")


async def chat_completion_formatting(
    session: aiohttp.ClientSession,
    api_base_url: str,
    api_key_value: Optional[str],
    model: str,
    instruction: str,
    user_input: str,
    include_summary: bool,
    temperature: float = 0.2,
    messages: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    used_messages = messages or build_messages(instruction, user_input, include_summary)
    payload: Dict[str, Any] = {
        "model": model,
        "messages": used_messages,
        "temperature": temperature,
        "response_format": build_response_format(include_summary),
    }
    return await post_chat_completion(session, api_base_url, api_key_value, payload)


def chat_completions(
    *,
    event_content: str,
    context_content: Optional[str],
    instruction: str,
    npub: Optional[str] = None,
    since: Optional[int] = None,
    curr_timestamp: Optional[int] = None,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Synchronous wrapper to score relevance and optionally summarize context.

    Inputs:
      - event_content: Content of the primary event
      - context_content: Thread/context text if available (enables summary)
      - instruction: Scoring instruction
      - npub/since/curr_timestamp: Optional; used to fetch API config
      - db_path: Optional goose.db path for config lookup

    Returns: { 'reason_for_score', 'relevancy_score', 'context_summary' }
    """
    include_summary: bool = bool(context_content and str(context_content).strip())
    user_input: str = str(context_content or "") if include_summary else str(event_content or "")

    # Determine API base URL and model from recent job if available
    api_base_url: Optional[str] = None
    api_model: Optional[str] = None
    if npub is not None and since is not None and curr_timestamp is not None:
        try:
            cfg = get_api_base_url_and_model(npub, int(since), int(curr_timestamp), db_path)
            if isinstance(cfg, dict):
                api_base_url = cfg.get("api_base_url")
                api_model = cfg.get("api_model")
        except Exception:
            pass

    # Fallback defaults if missing
    if not api_base_url:
        api_base_url = "https://api.routstr.com"
    if not api_model:
        api_model = "google/gemma-3-27b-it"

    # Per user preference, API keys are stored in a separate DB file (keys.db)
    api_key_value: Optional[str] = None
    try:
        base_dir_val = os.path.dirname(__file__)
        keys_db_path = os.path.join(base_dir_val, "keys.db")
        if os.path.exists(keys_db_path):
            kconn = sqlite_store.get_connection(keys_db_path)
            try:
                rec = sqlite_store.fetch_api_key(kconn, api_id="main")
                if rec and isinstance(rec, dict):
                    api_key_value = rec.get("api_key")
            finally:
                try:
                    kconn.close()
                except Exception:
                    pass
    except Exception:
        api_key_value = None

    async def _run() -> Dict[str, Any]:
        connector = aiohttp.TCPConnector(limit=10)
        async with aiohttp.ClientSession(connector=connector) as session:
            resp = await chat_completion_formatting(
                session=session,
                api_base_url=api_base_url or "",
                api_key_value=api_key_value,
                model=str(api_model or ""),
                instruction=str(instruction or ""),
                user_input=user_input,
                include_summary=include_summary,
                temperature=0.2,
                messages=None,
            )
            content_text = (
                ((resp.get("choices") or [{}])[0].get("message") or {}).get("content")
                if isinstance(resp, dict) else None
            )
            if not content_text:
                raise ValueError("Empty response from LLM")
            parsed = json.loads(content_text)
            context_summary_val = str(parsed.get("context_summary", "") or "").strip()
            relevancy_score_val = parsed.get("relevancy_score", None)
            reason_for_score_val = str(parsed.get("reason_for_score", "") or "").strip()
            try:
                relevancy_score_num = float(relevancy_score_val) if relevancy_score_val is not None else None
            except Exception:
                relevancy_score_num = None
            return {
                "reason_for_score": reason_for_score_val,
                "relevancy_score": relevancy_score_num,
                "context_summary": context_summary_val,
            }

    return asyncio.run(_run())


async def async_chat_completions(
    *,
    event_content: str,
    context_content: Optional[str],
    instruction: str,
    npub: Optional[str] = None,
    since: Optional[int] = None,
    curr_timestamp: Optional[int] = None,
    db_path: Optional[str] = None,
    include_summary: bool = False,
) -> Dict[str, Any]:
    """Async version of chat_completions to avoid nested asyncio.run() calls.

    Inputs:
      - event_content: Content of the primary event
      - context_content: Thread/context text if available (enables summary)
      - instruction: Scoring instruction
      - npub/since/curr_timestamp: Optional; used to fetch API config
      - db_path: Optional goose.db path for config lookup

    Returns: { 'reason_for_score', 'relevancy_score', 'context_summary' }
    """
    user_input: str = str(context_content or "") if include_summary else str(event_content or "")

    # Determine API base URL and model from recent job if available
    api_base_url: Optional[str] = None
    api_model: Optional[str] = None
    if npub is not None and since is not None and curr_timestamp is not None:
        try:
            cfg = get_api_base_url_and_model(npub, int(since), int(curr_timestamp), db_path)
            if isinstance(cfg, dict):
                api_base_url = cfg.get("api_base_url")
                api_model = cfg.get("api_model")
        except Exception:
            pass

    # Fallback defaults if missing
    if not api_base_url:
        api_base_url = "https://api.routstr.com"
    if not api_model:
        api_model = "google/gemma-3-27b-it"

    # Per user preference, API keys are stored in a separate DB file (keys.db)
    api_key_value: Optional[str] = None
    try:
        base_dir_val = os.path.dirname(__file__)
        keys_db_path = os.path.join(base_dir_val, "keys.db")
        if os.path.exists(keys_db_path):
            kconn = sqlite_store.get_connection(keys_db_path)
            try:
                rec = sqlite_store.fetch_api_key(kconn, api_id="main")
                if rec and isinstance(rec, dict):
                    api_key_value = rec.get("api_key")
            finally:
                try:
                    kconn.close()
                except Exception:
                    pass
    except Exception:
        api_key_value = None

    connector = aiohttp.TCPConnector(limit=10)
    async with aiohttp.ClientSession(connector=connector) as session:
        resp = await chat_completion_formatting(
            session=session,
            api_base_url=api_base_url or "",
            api_key_value=api_key_value,
            model=str(api_model or ""),
            instruction=str(instruction or ""),
            user_input=user_input,
            include_summary=include_summary,
            temperature=0.2,
            messages=None,
        )
        content_text = (
            ((resp.get("choices") or [{}])[0].get("message") or {}).get("content")
            if isinstance(resp, dict) else None
        )
        if not content_text:
            raise ValueError("Empty response from LLM")
        parsed = json.loads(content_text)
        context_summary_val = str(parsed.get("context_summary", "") or "").strip()
        relevancy_score_val = parsed.get("relevancy_score", None)
        reason_for_score_val = str(parsed.get("reason_for_score", "") or "").strip()
        try:
            relevancy_score_num = float(relevancy_score_val) if relevancy_score_val is not None else None
        except Exception:
            relevancy_score_num = None
        return {
            "reason_for_score": reason_for_score_val,
            "relevancy_score": relevancy_score_num,
            "context_summary": context_summary_val,
        }

