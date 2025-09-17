#!/usr/bin/env python3
import json
import asyncio
import os

import sqlite_store
from nostr_mcp import fetch_and_store


def main() -> None:
    # Edit these inputs as needed
    npub_or_hex = "4ad6fa2d16e2a9b576c863b4cf7404a70d4dc320c0c447d10ad6ff58993eacc8"
    # Specify a time window; set to None to disable filtering
    since = 1758018240
    till = 1758104729

    # Where to write goose.db if enabled
    base_dir = "/Users/r/projects/routstr_main/nostr_mcp"

    # Ensure base_dir exists and initialize database before running
    os.makedirs(base_dir, exist_ok=True)
    db_path = os.path.join(base_dir, "goose.db")
    try:
        _conn = sqlite_store.get_connection(db_path)
        sqlite_store.initialize_database(_conn)
        _conn.close()
    except Exception as e:
        print(f"Failed to initialize database at {db_path}: {e}")

    result = asyncio.run(
        fetch_and_store(
            npub_or_hex=npub_or_hex,
            since=since,
            till=till,
            base_dir=base_dir,
        )
    )



if __name__ == "__main__":
    main()


