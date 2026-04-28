import json
import os


try:
    load1, load5, load15 = os.getloadavg()
    available = True
except (AttributeError, OSError):
    load1, load5, load15 = 0.0, 0.0, 0.0
    available = False

print(
    json.dumps(
        {
            "system": {
                "loadAvailable": available,
                "load1": round(load1, 2),
                "load5": round(load5, 2),
                "load15": round(load15, 2),
                "cpuCount": os.cpu_count() or 0,
            }
        }
    )
)
