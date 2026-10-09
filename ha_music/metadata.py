"""ICY-only live radio metadata for 1LIVE, WDR 2 and SWR3."""
from urllib.request import Request, urlopen
from http.client import HTTPException
import re
import socket
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
ICY_STOP = threading.Event()
ICY_CONNECTIONS = {}
ICY_CANCEL = {}

def parse_icy_title(value):
    value = (value or "").strip()
    if not value:
        return {"title": None, "artist": None, "show": None, "kind": "unavailable"}
    if " - " in value:
        artist, title = value.split(" - ", 1)
        if artist.strip() and title.strip():
            return {"title": title.strip(), "artist": artist.strip(), "show": None, "kind": "song"}
    return {"title": None, "artist": None, "show": value, "kind": "show"}

def _read_exact(stream, amount, cancel=None):
    chunks = []
    while amount:
        if cancel is not None and cancel.is_set():
            raise EOFError("ICY cancelled")
        block = stream.read(min(amount, 8192))
        if not block:
            raise EOFError("ICY-Verbindung beendet")
        chunks.append(block)
        amount -= len(block)
    return b"".join(chunks)


def _icy_blocks(station, cancel):
    """Yield StreamTitle from one open connection; audio is discarded."""
    req = Request(ICY_STREAMS[station], headers={
        "User-Agent": "HA-Music/0.0.6", "Icy-MetaData": "1"})
    if cancel.is_set():
        return
    with urlopen(req, timeout=8) as stream:
        with LOCK:
            if cancel.is_set():
                return
            ICY_CONNECTIONS[station] = stream
        try:
            yield from _icy_stream_blocks(stream, cancel)
        finally:
            with LOCK:
                if ICY_CONNECTIONS.get(station) is stream:
                    ICY_CONNECTIONS.pop(station, None)


def _icy_stream_blocks(stream, cancel=None):
        value = stream.headers.get("icy-metaint", "")
        if not value.isdigit() or not 0 < int(value) <= 131072:
            raise ValueError("ICY-Metadatenintervall nicht verfügbar")
        interval = int(value)
        while True:
            _read_exact(stream, interval, cancel)
            length = _read_exact(stream, 1, cancel)[0] * 16
            if length:
                raw = _read_exact(stream, length, cancel).decode("utf-8", "replace")
                match = re.search(r"StreamTitle='([^']*)'", raw)
                if match and match.group(1).strip():
                    yield match.group(1).strip()
                else:
                    yield None
            else:
                yield None


def _icy_worker(station, cancel):
    retry = 2
    while not cancel.is_set():
        with LOCK:
            if time.monotonic() - ICY_LAST_REQUEST.get(station, 0) > ICY_IDLE_LIMIT:
                return
        try:
            for value in _icy_blocks(station, cancel):
                with LOCK:
                    idle = time.monotonic() - ICY_LAST_REQUEST.get(station, 0) > ICY_IDLE_LIMIT
                    if cancel.is_set():
                        return
                    if value:
                        ICY_LATEST[station] = (time.monotonic(), value)
                if idle:
                    return
                retry = 2
        except (OSError, ValueError, EOFError, HTTPException):
            pass
        if cancel.wait(retry):
            return
        retry = min(retry * 2, 30)

def close_stream(stream, *, reader_owned=False):
    """Shutdown now; never wait on another thread's BufferedReader lock."""
    sock = None
    try:
        sock = getattr(getattr(getattr(stream, "fp", None), "raw", None), "_sock", None)
        if sock is not None:
            sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    if reader_owned and sock is not None:
        # HTTPResponse.read() may itself close fp on EOF. A concurrent close()
        # races its _close_conn(). ICY's context manager owns final cleanup.
        return
    def finish():
        try:
            stream.close()
        except (OSError, ValueError):
            pass
    # Windows may keep a timed recv blocked despite shutdown. Cleanup must not
    # delay the standby deadline; the socket timeout bounds that reader instead.
    closer = threading.Thread(target=finish, daemon=True, name="http-close")
    closer.start()
    closer.join(timeout=0.05)


def stop_icy_workers():
    with LOCK:
        ICY_STOP.set()
        for cancel in ICY_CANCEL.values():
            cancel.set()  # Never clear a previous worker's cancellation token.
        ICY_LAST_REQUEST.clear()
        ICY_LATEST.clear()
        streams = list(ICY_CONNECTIONS.values())
        ICY_CONNECTIONS.clear()
    for stream in streams:
        close_stream(stream, reader_owned=True)


def resume_icy_workers():
    with LOCK:
        ICY_STOP.clear()


def ensure_icy_worker(station):
    if ICY_STOP.is_set():
        return
    if station not in ICY_STREAMS:
        return
    with LOCK:
        if ICY_STOP.is_set():
            return
        ICY_LAST_REQUEST[station] = time.monotonic()
        worker = ICY_WORKERS.get(station)
        if worker and worker.is_alive():
            # A cancelled predecessor exits on shutdown/timeout. The monitor
            # retries later; do not accumulate workers during rapid switching.
            return
        cancel = threading.Event()
        ICY_CANCEL[station] = cancel
        worker = threading.Thread(target=_icy_worker, args=(station, cancel), daemon=True, name="icy-" + station)
        ICY_WORKERS[station] = worker
        worker.start()

def now_playing(station):
    result = {"station": NAMES.get(station, ""), "title": None, "artist": None,
              "show": None, "kind": "unavailable", "cover": None,
              "status": "unavailable", "source": None}
    if station not in ICY_STREAMS or ICY_STOP.is_set():
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
