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
ICY_LATEST = {}
ICY_WORKERS = {}
ICY_STOP = {}
ICY_LAST_REQUEST = {}
ICY_IDLE_LIMIT = 45
ICY_MAX_AGE = 30


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

def parse_icy_title(value):
    """Return a current song only when the stream explicitly identifies one."""
    value = (value or "").strip()
    if not value:
        return {"title": None, "artist": None, "show": None, "kind": "unavailable"}
    # Broadcasters often send programme text rather than a track during speech.
    if " - " in value:
        artist, title = value.split(" - ", 1)
        if artist.strip() and title.strip():
            return {"title": title.strip(), "artist": artist.strip(),
                    "show": None, "kind": "song"}
    return {"title": None, "artist": None, "show": value, "kind": "show"}


def now_playing(station):
    result = {"station": NAMES.get(station, ""), "title": None, "artist": None,
              "show": None, "kind": "unavailable", "cover": None,
              "status": "unavailable", "source": None}
    if station not in URLS and station not in ICY_STREAMS:
        return result
    if station in ICY_STREAMS:
        ensure_icy_worker(station)
        with LOCK:
            latest = ICY_LATEST.get(station)
        if latest and time.monotonic() - latest[0] <= ICY_MAX_AGE:
            parsed = parse_icy_title(latest[1])
            if parsed["kind"] != "unavailable":
                result.update(parsed)
                result.update(status="available", source="icy")
                return result
    with LOCK:
        cached = CACHE.get(station)
        if cached and time.monotonic() - cached[0] < 20:
            return dict(cached[1])
    if station in URLS:
        try:
            raw = fetch(URLS[station])
            parsed = parse_wdr_live(raw) if station == "wdr2" else parse_recent_playlist(station, raw)
            result.update(parsed)
            if parsed["kind"] != "unavailable":
                result.update(status="available", source="web")
        except Exception:
            pass
    with LOCK:
        CACHE[station] = (time.monotonic(), dict(result))
    return result

# Probe official WDR MP3 streams for optional ICY metadata.
# Never download more than one metadata interval or start background streams.
ICY_STREAMS = {
    "1live": "https://wdr-1live-live.icecastssl.wdr.de/wdr/1live/live/mp3/128/stream.mp3",
    "wdr2": "https://wdr-wdr2-rheinruhr.icecastssl.wdr.de/wdr/wdr2/rheinruhr/mp3/128/stream.mp3",
    "swr3": "https://liveradio.swr.de/sw282p3/swr3/play.mp3",
}

def _read_exact(stream, amount):
    chunks = []
    while amount:
        block = stream.read(min(amount, 8192))
        if not block:
            raise EOFError("ICY-Verbindung beendet")
        chunks.append(block)
        amount -= len(block)
    return b"".join(chunks)


def _icy_blocks(station):
    """Yield StreamTitle from one open connection; audio is discarded."""
    req = Request(ICY_STREAMS[station], headers={
        "User-Agent": "HA-Music/0.0.3", "Icy-MetaData": "1"})
    with urlopen(req, timeout=8) as stream:
        value = stream.headers.get("icy-metaint", "")
        if not value.isdigit() or not 0 < int(value) <= 131072:
            raise ValueError("ICY-Metadatenintervall nicht verfügbar")
        interval = int(value)
        while True:
            _read_exact(stream, interval)
            length = _read_exact(stream, 1)[0] * 16
            if length:
                raw = _read_exact(stream, length).decode("utf-8", "replace")
                match = re.search(r"StreamTitle='([^']*)'", raw)
                if match and match.group(1).strip():
                    yield match.group(1).strip()
                else:
                    yield None
            else:
                yield None


def _icy_worker(station, stop):
    retry = 2
    while not stop.is_set():
        try:
            for value in _icy_blocks(station):
                with LOCK:
                    idle = time.monotonic() - ICY_LAST_REQUEST.get(station, 0) > ICY_IDLE_LIMIT
                    if value:
                        ICY_LATEST[station] = (time.monotonic(), value)
                if stop.is_set() or idle:
                    return
                retry = 2
        except (OSError, ValueError, EOFError):
            pass
        if stop.wait(retry):
            break
        retry = min(retry * 2, 60)


def ensure_icy_worker(station):
    if station not in ICY_STREAMS:
        return
    with LOCK:
        ICY_LAST_REQUEST[station] = time.monotonic()
        current = ICY_WORKERS.get(station)
        if current and current.is_alive():
            return
        stop = threading.Event()
        worker = threading.Thread(target=_icy_worker, args=(station, stop),
                                  daemon=True, name="icy-" + station)
        ICY_STOP[station] = stop
        ICY_WORKERS[station] = worker
        worker.start()


def probe_icy(station):
    if station not in ICY_STREAMS:
        return {"station": station, "supported": False, "reason": "Kein bestätigter offizieller MP3-Stream konfiguriert"}
    try:
        req = Request(ICY_STREAMS[station], headers={"User-Agent": "HA-Music/0.0.3", "Icy-MetaData": "1"})
        with urlopen(req, timeout=6) as stream:
            raw_interval = stream.headers.get("icy-metaint")
            if not raw_interval or not raw_interval.isdigit():
                return {"station": station, "supported": False, "reason": "Stream liefert kein icy-metaint"}
            interval = int(raw_interval)
            if not 0 < interval <= 131072:
                return {"station": station, "supported": False, "reason": "Ungültiges oder zu großes Metadatenintervall"}
            remaining = interval
            while remaining:
                chunk = stream.read(min(8192, remaining))
                if not chunk:
                    return {"station": station, "supported": False, "reason": "Stream vor Metadatenblock beendet"}
                remaining -= len(chunk)
            length_byte = stream.read(1)
            if not length_byte:
                return {"station": station, "supported": False, "reason": "Kein ICY-Metadatenblock"}
            block = stream.read(length_byte[0] * 16).decode("utf-8", "replace")
            match = re.search(r"StreamTitle='([^']*)'", block)
            value = match.group(1).strip() if match else ""
            return {"station": station, "supported": bool(value),
                    "reason": "ICY-StreamTitle empfangen" if value else "ICY vorhanden, kein Titel im ersten Block",
                    "sample": value if value else None}
    except Exception as exc:
        return {"station": station, "supported": False, "reason": type(exc).__name__ + ": " + str(exc)[:120]}
