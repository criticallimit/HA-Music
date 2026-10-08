"""Optional WDR current-song lookup. Never return stale or invented titles."""
from html.parser import HTMLParser
from urllib.request import Request, urlopen
from urllib.parse import quote
import json
import re
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

URLS = {
    "wdr2": "https://www1.wdr.de/radio/player/streams/wdr2/index.html",
    "wdr4": "https://www1.wdr.de/radio/wdr4/musik/playlist/",
    "1live": "https://www1.wdr.de/radio/1live/on-air/playlist/einslive-playlist-100.html",
    "swr3": "https://www.swr3.de/playlisten/",
}
NAMES = {"wdr2":"WDR 2","wdr4":"WDR 4","1live":"1LIVE","swr3":"SWR3","80s80s":"80s80s","ndr2":"NDR 2","radiobob":"Radio BOB!"}
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

def parse_recent_playlist(station, html, now=None):
    """Only consider a track current when its published start time is very recent."""
    parser = Text()
    parser.feed(html)
    plain = re.sub(r"\s+", " ", " ".join(parser.parts))
    now = now or datetime.now(ZoneInfo("Europe/Berlin"))
    if station == "1live":
        # Playlist table: "08.10.2026, 17.09 Uhr <title> <artist>" lacks
        # unambiguous title/artist boundaries after HTML has been flattened.
        # Prefer the explicit "... mit ..." item in the station's player.
        player_section = plain.split("Stream im 1LIVE-Player hören", 1)[-1]
        player_section = player_section.split("Ausführliche Playlist", 1)[0]
        match = re.search(r"(\d{1,2})[.:](\d{2})\s+(.{2,75}?)\s+mit\s+(.{2,100}?)(?=\s+\d{1,2}[.:]\d{2}\s|$)", player_section, re.I)
        if match:
            hour, minute = int(match[1]), int(match[2])
            song_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if timedelta(0) <= now - song_time <= timedelta(minutes=5):
                return {"title":match[4].strip(), "artist":match[3].strip(), "show":None, "kind":"song"}
    elif station == "swr3":
        # SWR3 exposes track fields separately ("Titel" and "Interpret").
        match = re.search(r"(\d{2})[.](\d{2})[.](\d{4})\s+(\d{1,2}):(\d{2})\s+.*?\bTitel\s+(.{2,100}?)\s+Interpret\s+(.{2,85}?)(?=\s+(?:Credits|Komponiert von:|Früher|Später)|$)", plain, re.I)
        if match:
            day, month, year, hour, minute = map(int, match.group(1,2,3,4,5))
            try:
                song_time = now.replace(year=year, month=month, day=day, hour=hour, minute=minute, second=0, microsecond=0)
                if timedelta(0) <= now - song_time <= timedelta(minutes=5):
                    return {"title":match[6].strip(), "artist":match[7].strip(), "show":None, "kind":"song"}
            except ValueError:
                pass
    return {"title":None, "artist":None, "show":None, "kind":"unavailable"}

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
        parsed = parse_wdr_live(raw) if station == "wdr2" else parse_recent_playlist(station, raw)
        result.update(parsed)
        if parsed["kind"] != "unavailable":
            result["status"] = "available"
    except Exception:
        pass
    with LOCK:
        CACHE[station] = (time.monotonic(), dict(result))
    return result
