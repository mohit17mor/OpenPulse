import json
import os
import subprocess
import time
from pathlib import Path


def read_proc_stat():
    stat_path = Path("/proc/stat")
    if not stat_path.exists():
        return None
    parts = stat_path.read_text().splitlines()[0].split()[1:]
    values = [int(part) for part in parts]
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    total = sum(values)
    return total, idle


def proc_stat_percent():
    first = read_proc_stat()
    if first is None:
        return None
    time.sleep(0.2)
    second = read_proc_stat()
    if second is None:
        return None
    total_delta = second[0] - first[0]
    idle_delta = second[1] - first[1]
    if total_delta <= 0:
        return 0.0
    return round((1 - idle_delta / total_delta) * 100, 2)


def ps_percent():
    result = subprocess.run(
        ["ps", "-A", "-o", "%cpu="],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    values = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            values.append(float(line))
        except ValueError:
            pass
    total = sum(values)
    cpu_count = os.cpu_count() or 1
    return round(min(total / cpu_count, 100.0), 2)


method = "proc_stat"
percent = proc_stat_percent()
if percent is None:
    method = "ps"
    percent = ps_percent()

print(json.dumps({"cpu": {"percent": percent, "method": method}}))
