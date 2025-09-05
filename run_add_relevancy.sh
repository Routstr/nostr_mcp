#!/bin/bash
# This script navigates to the project directory and executes the goose run command
# with the specified recipe and parameters.

set -e

cd /Users/r/projects/routstr_main/nostr_mcp

goose run \
  --recipe add_relevancy_score.yaml \
  --params npub=npub1sg6plzptd64u62a878hep2kev88swjh3tw00gjsfl8f237lmu63q0uf63m \
  --params since=1756825792 \
  --params instruction="Posts that contain useful information that educate me in someway or the other. Shitposting should be avoided. Low effort notes should be avoided."