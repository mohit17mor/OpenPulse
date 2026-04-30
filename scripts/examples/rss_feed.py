import json
import sys
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path


def read_source(source):
    if source.startswith(("http://", "https://")):
        with urllib.request.urlopen(source, timeout=10) as response:
            return response.read()
    return Path(source).expanduser().read_bytes()


def child_text(element, names):
    for name in names:
        found = element.find(name)
        if found is not None and found.text:
            return found.text.strip()
    for child in list(element):
        local_name = child.tag.split("}")[-1]
        if local_name in names and child.text:
            return child.text.strip()
    return ""


def child_attr(element, local_name, attr):
    for child in list(element):
        if child.tag.split("}")[-1] == local_name and child.get(attr):
            return child.get(attr)
    return ""


source = sys.argv[1] if len(sys.argv) > 1 else "scripts/data/sample_feed.xml"
root = ET.fromstring(read_source(source))
items = []

rss_items = root.findall("./channel/item")
if rss_items:
    for item in rss_items:
        title = child_text(item, ["title"])
        link = child_text(item, ["link"])
        guid = child_text(item, ["guid"]) or link or title
        items.append({"id": guid, "title": title, "link": link})
else:
    for entry in root.findall("{http://www.w3.org/2005/Atom}entry") or root.findall("entry"):
        title = child_text(entry, ["title"])
        link = child_attr(entry, "link", "href")
        item_id = child_text(entry, ["id"]) or link or title
        items.append({"id": item_id, "title": title, "link": link})

print(json.dumps({"items": items}))
