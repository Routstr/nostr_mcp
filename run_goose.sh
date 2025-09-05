#!/bin/bash
# This script navigates to the project directory and executes the goose run command
# with the specified recipe and parameters for summarizing a feed.
#
# Usage:
#   ./run_goose.sh <npub> <since> <curr_timestamp>

set -e

cd /Users/r/projects/routstr_main/nostr_mcp

if [ "$#" -ne 3 ]; then
  echo "Usage: $0 <npub> <since> <curr_timestamp>"
  exit 1
fi

NPUB=$1
SINCE=$2
CURR_TIMESTAMP=$3

goose run \
  --recipe summarize_feed.yaml \
  --params npub=$NPUB \
  --params since=$SINCE \
  --params curr_timestamp=$CURR_TIMESTAMP