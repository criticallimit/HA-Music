"""Ingress app and restricted Home Assistant Alexa control API."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import json
import os
import re
import threading
from metadata import now_playing

WEB = (Path(__file__).parent / "web").resolve()
PORT = int(os.environ.get("PORT", "8099"))
OPTIONS = Path(os.environ.get("OPTIONS_FILE", "/data/options.json"))
HA_API = os.environ.get("HA_API", "http://supervisor/core/api").rstrip("/")
TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
STATIONS = {"wdr2": "WDR 2", "1live": "1LIVE", "wdr4": "WDR 4",
            "80s80s": "80s80s", "ndr2": "NDR 2", "radiobob": "Radio BOB!"}
ENTITY_RE = re.compile(r"^media_player\.[a-z0-9_]+$")
DEVICE_RE = re.compile(r"^[a-f0-9]{32}$")

VOLUME_FILE = Path(os.environ.get("VOLUME_FILE", "/config/volumes.json"))
LOCK = threading.Lock()
def allowed_entities():
    raw = options().get("echo_entities", "")
    if not isinstance(raw, str):
        return set()
    return {x.strip() for x in raw.split(",") if ENTITY_RE.fullmatch(x.strip())}
def remembered():
    try:
        obj = json.loads(VOLUME_FILE.read_text())
        return obj if isinstance(obj, dict) else {}
    except (OSError, ValueError): return {}
def save_remembered(entity, level):
    with LOCK:
        obj = remembered()
        obj[entity] = level
        VOLUME_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary = VOLUME_FILE.with_suffix(".tmp")
        temporary.write_text(json.dumps(obj))
        temporary.replace(VOLUME_FILE)
def options():
    try:
        data = json.loads(OPTIONS.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}

def ha_request(path, payload=None):
    if not TOKEN:
        raise RuntimeError("Home Assistant API token unavailable")
    data = json.dumps(payload).encode() if payload is not None else None
    req = Request(HA_API + path, data=data, headers={
        "Authorization": "Bearer " + TOKEN,
        "Content-Type": "application/json",
    }, method="POST" if payload is not None else "GET")
    with urlopen(req, timeout=12) as response:
        raw = response.read()
        # Some Home Assistant service responses are empty on success.
        return json.loads(raw) if raw.strip() else {}

def players():
    states = ha_request("/states")
    return [{"entity_id": state["entity_id"],
             "name": state.get("attributes", {}).get("friendly_name", state["entity_id"]),
             "state": state.get("state", "unknown"),
             "volume": state.get("attributes", {}).get("volume_level"),
             "features": state.get("attributes", {}).get("supported_features", 0)}
            for state in states if state.get("entity_id") in allowed_entities()]

def perform(action, body):
    if action == "radio":
        station = STATIONS.get(body.get("station"))
        config = options()
        device = config.get("command_device_id", "")
        group = config.get("alexa_group_name", "")
        if isinstance(group, str): group = group.strip()
        if not station or not isinstance(device, str) or not DEVICE_RE.fullmatch(device) or not isinstance(group, str) or not group:
            raise ValueError("Sender oder Alexa-Gruppe/Geräte-ID nicht konfiguriert")
        if len(group) > 80 or re.search(r"[\r\n]", group):
            raise ValueError("Ungültiger Gruppenname")
        command = f"Spiele {station} auf {group}"
        return ha_request("/services/alexa_devices/send_text_command",
                          {"device_id": device, "text_command": command})
    if action == "volume":
        entity = body.get("entity_id", "")
        level = body.get("volume")
        if not isinstance(entity, str) or not ENTITY_RE.fullmatch(entity) or type(level) not in (int, float) or not 0 <= level <= 1:
            raise ValueError("Ungültige Lautstärke oder Entity")
        if entity not in allowed_entities():
            raise ValueError("Media Player nicht freigegeben")
        found = next((x for x in players() if x["entity_id"] == entity), None)
        if not found:
            raise ValueError("Media Player nicht gefunden")
        result = ha_request("/services/media_player/volume_set", {"entity_id": entity, "volume_level": level})
        if level > 0: save_remembered(entity, level)
        return result
    raise ValueError("Unbekannte Aktion")

class Handler(BaseHTTPRequestHandler):
    def reply(self, status, payload):
        encoded = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self):
        path = unquote(urlsplit(self.path).path)
        name = path.rsplit("/", 1)[-1]
        if name == "status" and "/api/" in path:
            config = options()
            return self.reply(200, {"radio": "configured" if config.get("alexa_group_name") and config.get("command_device_id") else "setup_required",
                                    "apple_music": "planned", "backend": "connected" if TOKEN else "unavailable"})
        if name == "now-playing" and "/api/" in path:
            from urllib.parse import parse_qs
            station = parse_qs(urlsplit(self.path).query).get("station", [""])[0]
            return self.reply(200, now_playing(station))
        if name == "players" and "/api/" in path:
            try:
                return self.reply(200, {"players": players(), "remembered": remembered(), "configured": sorted(allowed_entities())})
            except (RuntimeError, HTTPError, URLError, ValueError) as exc:
                return self.reply(503, {"error": str(exc)})
        name = name or "index.html"
        if name not in ("index.html", "style.css", "app.js"):
            self.send_error(404)
            return
        content = (WEB / name).read_bytes()
        mime = {"index.html": "text/html", "style.css": "text/css", "app.js": "application/javascript"}[name]
        self.send_response(200)
        self.send_header("Content-Type", mime + "; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_POST(self):
        path = unquote(urlsplit(self.path).path)
        action = path.rsplit("/", 1)[-1]
        if "/api/" not in path or action not in ("radio", "volume"):
            return self.reply(404, {"error": "Not found"})
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 2048:
                return self.reply(400, {"error": "Invalid request length"})
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict):
                raise ValueError("Invalid body")
            perform(action, body)
            return self.reply(200, {"ok": True})
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            return self.reply(400, {"error": str(exc)})
        except (RuntimeError, HTTPError, URLError) as exc:
            return self.reply(502, {"error": str(exc)})

if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
