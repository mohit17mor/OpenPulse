import json
import shutil
import sys
from pathlib import Path


def gb(value):
    return round(value / (1024 ** 3), 2)


path = Path(sys.argv[1] if len(sys.argv) > 1 else "/").expanduser()
usage = shutil.disk_usage(path)
used = usage.total - usage.free
used_percent = (used / usage.total * 100) if usage.total else 0

print(
    json.dumps(
        {
            "disk": {
                "path": str(path),
                "totalGb": gb(usage.total),
                "usedGb": gb(used),
                "freeGb": gb(usage.free),
                "usedPercent": round(used_percent, 2),
            }
        }
    )
)
