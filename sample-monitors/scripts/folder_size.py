import json
import os
import sys
from pathlib import Path


root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").expanduser()
total_bytes = 0
file_count = 0
skipped = 0

for dirpath, dirnames, filenames in os.walk(root, onerror=lambda _error: None):
    dirnames[:] = [name for name in dirnames if name not in {".git", ".venv", "__pycache__"}]
    for filename in filenames:
        path = Path(dirpath) / filename
        try:
            total_bytes += path.stat().st_size
            file_count += 1
        except OSError:
            skipped += 1

print(
    json.dumps(
        {
            "folder": {
                "path": str(root),
                "sizeMb": round(total_bytes / (1024 ** 2), 2),
                "fileCount": file_count,
                "skipped": skipped,
            }
        }
    )
)
