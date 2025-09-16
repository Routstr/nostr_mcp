#!/bin/bash
# This script navigates to the project directory and executes the goose run command
# with the specified recipe and parameters for summarizing a feed.
#
# Usage:
#   ./run_goose.sh <npub> <since> <curr_timestamp>

set -e

# Load project environment variables if present
if [ -f "/Users/r/projects/routstr_main/nostr_mcp/.env" ]; then
  set -a
  . "/Users/r/projects/routstr_main/nostr_mcp/.env"
  set +a
fi

cd /Users/r/projects/routstr_main/nostr_mcp

if [ "$#" -ne 5 ]; then
  echo "Usage: $0 <npub> <since> <curr_timestamp> <instruction> <base_dir>"
  exit 1
fi

NPUB=$1
SINCE=$2
CURR_TIMESTAMP=$3
INSTRUCTION=$4
BASE_DIR=$5

goose run \
  --recipe db_powered_summarize.yaml \
  --params "npub=$NPUB" \
  --params "since=$SINCE" \
  --params "till=$CURR_TIMESTAMP" \
  --params "base_dir=$BASE_DIR" \
  --params "instruction=$INSTRUCTION" \
  --name "${NPUB}_${SINCE}_${CURR_TIMESTAMP}"