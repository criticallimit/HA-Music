"""Optional WDR current-song lookup. Never return stale or invented titles."""
from html.parser import HTMLParser
from urllib.request import Request, urlopen
from urllib.parse import quote
import json
import re
import threading
import time

URLS = {
    "wdr2": "https://www1.wdr.de/radio/wdr2/musik/playlist/titelsuche-playlist-wdrzwei-100.html",
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

def now_playing(station):
    result = {"station": NAMES.get(station, ""), "title": None, "artist": None,
              "cover": None, "status": "unavailable"}
    if station not in URLS:
        return result
    with LOCK:
        cached = CACHE.get(station)
        if cached and time.monotonic() - cached[0] < 60:
            return dict(cached[1])
    try:
        parser = Text(); parser.feed(fetch(URLS[station]))
        text = " ".join(parser.parts)
        text = re.sub(r"\s+", " ", text)
        match = re.search(r"Jetzt läuft:\s*(.{2,130}?)\s+von\s+(.{2,100}?)(?=\s+(?:Playlist|Bildquelle|Jetzt|Die |[0-9]{2}[:.][0-9]{2})|$)", text, re.I)
        if match:
            result["title"], result["artist"] = [x.strip() for x in match.groups()]
            result["status"] = "available"
            query = quote(result["title"] + " " + result["artist"])
            try:
                data = json.loads(fetch("https://itunes.apple.com/search?entity=song&limit=3&term=" + query))
                for song in data.get("results", []):
                    if song.get("trackName", "").casefold() == result["title"].casefold() and song.get("artistName", "").casefold() == result["artist"].casefold():
                        result["cover"] = song.get("artworkUrl100"); break
            except Exception:
                pass
    except Exception:
        pass
    with LOCK: CACHE[station] = (time.monotonic(), dict(result))
    return result
