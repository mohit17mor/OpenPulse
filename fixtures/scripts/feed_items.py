#!/usr/bin/env python3
import json

print(
    json.dumps(
        {
            "items": [
                {
                    "guid": "post-001",
                    "title": "First OpenPulse item",
                    "link": "https://example.com/posts/001",
                },
                {
                    "guid": "post-002",
                    "title": "Second OpenPulse item",
                    "link": "https://example.com/posts/002",
                },
            ]
        }
    )
)

