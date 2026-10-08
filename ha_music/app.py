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
TOKEN = os.environ.get("SUPERVISOR_TOKEN", "") or os.environ.get("HASSIO_TOKEN", "")
STATIONS = {"wdr2": "WDR 2", "1live": "1LIVE", "wdr4": "WDR 4",
            "80s80s": "80s80s", "ndr2": "NDR 2", "radiobob": "Radio BOB!"}
ENTITY_RE = re.compile(r"^media_player\.[a-z0-9_]+$")

VOLUME_FILE = Path(os.environ.get("VOLUME_FILE", "/config/volumes.json"))
LOCK = threading.Lock()
def integration_inventory():
    """Discover registered Alexa entities from both supported integration domains."""
    response = ha_request("/template", {
        "template": "{{ dict(alexa_devices=integration_entities('alexa_devices'), alexa_media=integration_entities('alexa_media')) | to_json }}"
    })
    if not isinstance(response, str):
        raise ValueError("Unerwartete Antwort der Home-Assistant-Template-API")
    data = json.loads(response)
    if not isinstance(data, dict):
        raise ValueError("Ungültiges Alexa-Inventar")
    return {domain: [eid for eid in data.get(domain, []) if isinstance(eid, str)]
            for domain in ("alexa_devices", "alexa_media")}

def integration_player_ids():
    inventory = integration_inventory()
    return {entity for values in inventory.values() for entity in values
            if ENTITY_RE.fullmatch(entity)}

def detected_devices():
    ids = integration_player_ids()
    states = ha_request("/states")
    found = []
    for state in states:
        entity = state.get("entity_id")
        if entity not in ids:
            continue
        attributes = state.get("attributes") or {}
        name = str(attributes.get("friendly_name") or entity)
        found.append({"entity_id": entity, "name": name,
                      "state": state.get("state", "unknown"),
                      "volume": attributes.get("volume_level"),
                      "features": attributes.get("supported_features", 0),
                      "possible_group": False})
    return sorted(found, key=lambda item: item["name"].casefold())

def classify_devices():
    """Separate user-confirmed Wohnung group and non-room endpoint types."""
    group, rooms, excluded = [], [], []
    for player in detected_devices():
        name = player["name"].strip().casefold()
        entity = player["entity_id"].casefold()
        if name == "wohnung" or entity == "media_player.wohnung":
            group.append(player)
        elif "fire tv" in name or name == "this device" or "fire_tv" in entity:
            excluded.append(player)
        else:
            rooms.append(player)
    return {"groups": group, "players": rooms, "excluded": excluded}

def allowed_entities():
    return {p["entity_id"] for p in classify_devices()["players"]}

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
        if not raw.strip():
            return {}
        if path == "/template":
            return raw.decode("utf-8")
        return json.loads(raw)

def players():
    return classify_devices()["players"]

WDR2_STREAM = "https://wdr-wdr2-rheinruhr.icecastssl.wdr.de/wdr/wdr2/rheinruhr/mp3/128/stream.mp3"
PLAY_MEDIA_FEATURE = 512

def wohnung_group():
    return next((group for group in classify_devices()["groups"]
                 if group["name"].strip().casefold() == "wohnung"), None)

def playback_capability():
    group = wohnung_group()
    if not group:
        return {"available": False, "reason": "Gruppe Wohnung nicht gefunden"}
    supported = bool((group.get("features") or 0) & PLAY_MEDIA_FEATURE)
    return {"available": supported, "entity_id": group["entity_id"],
            "state": group["state"], "features": group["features"],
            "reason": "play_media verfügbar; Streamunterstützung nicht bestätigt" if supported
                      else "Gruppe unterstützt play_media nicht"}

def perform(action, body):
    if action in ("test_wdr2", "test_tunein_wdr2"):
        capability = playback_capability()
        if not capability["available"]:
            raise ValueError(capability["reason"])
        return ha_request("/services/media_player/play_media", {
            "entity_id": capability["entity_id"],
            "media_content_type": "TUNEIN" if action == "test_tunein_wdr2" else "music",
            "media_content_id": "WDR 2" if action == "test_tunein_wdr2" else WDR2_STREAM,
        })
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
            return self.reply(200, {"radio": "direct_playback_pending",
                                    "apple_music": "planned", "backend": "connected" if TOKEN else "unavailable"})
        if name == "now-playing" and "/api/" in path:
            from urllib.parse import parse_qs
            station = parse_qs(urlsplit(self.path).query).get("station", [""])[0]
            return self.reply(200, now_playing(station))
        if name == "playback-check" and "/api/" in path:
            try:
                return self.reply(200, playback_capability())
            except (RuntimeError, HTTPError, URLError, ValueError) as exc:
                return self.reply(503, {"error": str(exc)})
        if name == "players" and "/api/" in path:
            try:
                inventory = integration_inventory()
                classified = classify_devices()
                return self.reply(200, {"players": classified["players"], "groups": classified["groups"], "excluded": classified["excluded"],
                    "remembered": remembered(), "discovery": "integration_registry",
                    "diagnostics": {domain: {"entities": len(values),
                        "media_players": sum(v.startswith("media_player.") for v in values)}
                        for domain, values in inventory.items()}})
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
        if "/api/" not in path or action not in ("volume", "test_wdr2", "test_tunein_wdr2"):
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
