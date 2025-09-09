#!/usr/bin/env python3
import json
import asyncio

from nostr_mcp import summarize_and_add_relevancy_score


def main() -> None:
    # Edit these inputs as needed
    instruction = "Posts that contain useful information that educate me in someway or the other. Shitposting should be avoided. Low effort notes should be avoided. "
    npub = "4ad6fa2d16e2a9b576c863b4cf7404a70d4dc320c0c447d10ad6ff58993eacc8"

    # Specify a time window; set to None to disable filtering
    since = 1757340914
    till = 1757427314

    base_dir = "/Users/r/projects/routstr_main/nostr_mcp"

    result = asyncio.run(
        summarize_and_add_relevancy_score(
            instruction=instruction,
            npub=npub,
            since=since,
            till=till,
            max_concurrency=20,
            base_dir=base_dir,
        )
    )

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()


