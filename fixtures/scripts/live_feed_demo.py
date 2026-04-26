#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
import sys


STATE_PATH = Path(__file__).with_name(".live_feed_demo_state.json")


def read_count() -> int:
    if not STATE_PATH.exists():
        return 0
    try:
        return int(json.loads(STATE_PATH.read_text()).get("count", 0))
    except (OSError, ValueError, json.JSONDecodeError, TypeError):
        return 0


def write_count(count: int) -> None:
    STATE_PATH.write_text(json.dumps({"count": count}), encoding="utf-8")


def main() -> None:
    if "--reset" in sys.argv:
        write_count(0)
        print(json.dumps({"status": "reset", "items": []}))
        return

    count = read_count() + 1
    write_count(count)

    items = [
        {
            "guid": f"demo-{index:03d}",
            "title": f"Demo item {index}",
            "link": f"https://example.com/demo/{index}",
        }
        for index in range(1, count + 1)
    ]

    print(
        json.dumps(
            {
                "source": "openpulse-live-feed-demo",
                "price": {"value": 100 + count, "currency": "INR"},
                "status": "ok",
                "items": items,
            }
        )
    )


if __name__ == "__main__":
    main()
