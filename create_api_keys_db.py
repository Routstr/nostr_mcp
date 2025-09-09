#!/usr/bin/env python3

import argparse
import sqlite3
from getpass import getpass

from sqlite_store import initialize_api_keys_database


def upsert_api_key(connection: sqlite3.Connection, api_id: str, api_key: str) -> None:
    """Insert the API key for the given id, or update it if it already exists."""
    cursor = connection.cursor()
    try:
        cursor.execute(
            "INSERT INTO api_keys (api_id, api_key) VALUES (?, ?)",
            (api_id, api_key),
        )
    except sqlite3.IntegrityError:
        cursor.execute(
            "UPDATE api_keys SET api_key = ? WHERE api_id = ?",
            (api_key, api_id),
        )
    connection.commit()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create an API keys SQLite DB (api_keys table only) and store a key."
    )
    parser.add_argument(
        "--db",
        "--db-path",
        dest="db_path",
        required=True,
        help="Path to the SQLite DB file, e.g. ./api_keys.db",
    )
    parser.add_argument(
        "--id",
        "--api-id",
        dest="api_id",
        default="main",
        help='API id to use (default: "main")',
    )

    args = parser.parse_args()

    connection = sqlite3.connect(args.db_path)
    try:
        # Create only the api_keys table if it doesn't exist
        initialize_api_keys_database(connection)
        # Prompt for the API key securely (not shown on screen)
        prompt_label = f"Enter API key for id '{args.api_id}': "
        api_key = getpass(prompt_label)
        if not api_key:
            raise SystemExit("API key cannot be empty.")
        # Insert or update the provided key
        upsert_api_key(connection, args.api_id, api_key)
    finally:
        connection.close()


if __name__ == "__main__":
    main()


