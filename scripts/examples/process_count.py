import json
import subprocess
import sys


query = sys.argv[1] if len(sys.argv) > 1 else "python"

try:
    output = subprocess.check_output(["ps", "-axo", "pid=,comm="], text=True)
    matches = []
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split(None, 1)
        if len(parts) != 2:
            continue
        pid, command = parts
        if query.lower() in command.lower():
            matches.append({"pid": pid, "command": command})
except (OSError, subprocess.SubprocessError):
    matches = []

print(json.dumps({"process": {"query": query, "count": len(matches), "matches": matches[:10]}}))
