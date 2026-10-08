"""Optional WDR current-song lookup. Never return stale or invented titles."""
from html.parser import HTMLParser
from urllib.request import Request, urlopen
from urllib.parse import quote
import json
import re
import threading
import time

URLS = {
    "wdr2": "https://www1.wdr.de/radio/player/streams/wdr2/index.html",
    "wdr4": "https://www1.wdr.de/radio/wdr4/musik/playlist/",
    "1live": "https://www1.wdr.de/radio/1live/musik/playlist/",
}
NAMES = {"wdr2":"WDR 2","wdr4":"WDR 4","1live":"1LIVE","80s80s":"80s80s","ndr2":"NDR 2","radiobob":"Radio BOB!"}
CACHE = {}
LOCK = threading.Lock()

class Text(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts = []
    def handle_data(self, data):
        self.parts.append(data)

def fetch(url):
    with urlopen(Request(url, headers={"User-Agent":"HA-Music/0.0.1"}), timeout=7) as response:
        return response.read(300000).decode("utf-8", "replace")

def parse_wdr_live(text):
    """Extract only the short live player header, not playlist search results."""
    parser = Text()
    parser.feed(text)
    plain = re.sub(r"\s+", " ", " ".join(parser.parts)).strip()
    result = {"title": None, "artist": None, "show": None, "kind": "unavailable"}
    # Restrict the song to the heading preceding the programme's time slot.
    segment = plain.split("Jetzt läuft:", 1)
    if len(segment) == 2:
        header = re.split(r"\b\d{1,2}[.:]\d{2}\s*[-–]\s*\d{1,2}[.:]\d{2}\s*Uhr\b", segment[1], maxsplit=1)[0]
        matched = re.search(r"^\s*(.{2,100}?)\s+von\s+(.{2,75}?)\s*$", header, re.I)
        if matched:
            result["title"], result["artist"] = [s.strip() for s in matched.groups()]
            result["kind"] = "song"
    programme = re.search(r"\b\d{1,2}[.:]\d{2}\s*[-–]\s*\d{1,2}[.:]\d{2}\s*Uhr\s+(.{3,100}?)(?=\s+(?:mit\s|Mail ins Studio|Playlist|Bildquelle|Image\b|#)|$)", plain, re.I)
    if programme:
        result["show"] = programme.group(1).strip()
        if result["kind"] != "song":
            result["kind"] = "show"
    return result

def now_playing(station):
    result = {"station": NAMES.get(station, ""), "title": None, "artist": None,
              "show": None, "kind": "unavailable", "cover": None, "status": "unavailable"}
    if station not in URLS:
        return result
    with LOCK:
        cached = CACHE.get(station)
        if cached and time.monotonic() - cached[0] < 30:
            return dict(cached[1])
    try:
        raw = fetch(URLS[station])
        parsed = parse_wdr_live(raw)
        result.update(parsed)
        if parsed["kind"] != "unavailable":
            result["status"] = "available"
    except Exception:
        pass
    with LOCK:
        CACHE[station] = (time.monotonic(), dict(result))
    return result
