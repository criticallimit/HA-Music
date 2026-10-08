"""ICY-only live radio metadata for 1LIVE, WDR 2 and SWR3."""
from urllib.request import Request, urlopen
import re
import threading
import time

NAMES = {"1live": "1LIVE", "wdr2": "WDR 2", "swr3": "SWR3"}
ICY_STREAMS = {
    "1live": "https://wdr-1live-live.icecastssl.wdr.de/wdr/1live/live/mp3/128/stream.mp3",
    "wdr2": "https://wdr-wdr2-rheinruhr.icecastssl.wdr.de/wdr/wdr2/rheinruhr/mp3/128/stream.mp3",
    "swr3": "https://liveradio.swr.de/sw282p3/swr3/play.mp3",
}
LOCK = threading.Lock()
ICY_LATEST = {}
ICY_WORKERS = {}
ICY_LAST_REQUEST = {}
ICY_IDLE_LIMIT = 45
ICY_MAX_AGE = 30

def parse_icy_title(value):
    value = (value or "").strip()
    if not value:
        return {"title": None, "artist": None, "show": None, "kind": "unavailable"}
    if " - " in value:
        artist, title = value.split(" - ", 1)
        if artist.strip() and title.strip():
            return {"title": title.strip(), "artist": artist.strip(), "show": None, "kind": "song"}
    return {"title": None, "artist": None, "show": value, "kind": "show"}

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


def _icy_worker(station):
    retry = 2
    while True:
        with LOCK:
            if time.monotonic() - ICY_LAST_REQUEST.get(station, 0) > ICY_IDLE_LIMIT:
                return
        try:
            for value in _icy_blocks(station):
                with LOCK:
                    idle = time.monotonic() - ICY_LAST_REQUEST.get(station, 0) > ICY_IDLE_LIMIT
                    if value:
                        ICY_LATEST[station] = (time.monotonic(), value)
                if idle:
                    return
                retry = 2
        except (OSError, ValueError, EOFError):
            pass
        time.sleep(retry)
        retry = min(retry * 2, 30)

def ensure_icy_worker(station):
    if station not in ICY_STREAMS:
        return
    with LOCK:
        ICY_LAST_REQUEST[station] = time.monotonic()
        worker = ICY_WORKERS.get(station)
        if worker and worker.is_alive():
            return
        worker = threading.Thread(target=_icy_worker, args=(station,), daemon=True, name="icy-" + station)
        ICY_WORKERS[station] = worker
        worker.start()

def now_playing(station):
    result = {"station": NAMES.get(station, ""), "title": None, "artist": None,
              "show": None, "kind": "unavailable", "cover": None,
              "status": "unavailable", "source": None}
    if station not in ICY_STREAMS:
        return result
    ensure_icy_worker(station)
    with LOCK:
        latest = ICY_LATEST.get(station)
    if latest and time.monotonic() - latest[0] <= ICY_MAX_AGE:
        parsed = parse_icy_title(latest[1])
        if parsed["kind"] != "unavailable":
            result.update(parsed)
            result.update(status="available", source="icy")
    return result
