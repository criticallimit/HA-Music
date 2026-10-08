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
import time
from metadata import now_playing
from metadata_feed import MONITOR

WEB = (Path(__file__).parent / "web").resolve()
PORT = int(os.environ.get("PORT", "8099"))
OPTIONS = Path(os.environ.get("OPTIONS_FILE", "/data/options.json"))
HA_API = os.environ.get("HA_API", "http://supervisor/core/api").rstrip("/")
TOKEN = os.environ.get("SUPERVISOR_TOKEN", "") or os.environ.get("HASSIO_TOKEN", "")
STATIONS = {"wdr2": "WDR 2", "1live": "1LIVE", "wdr4": "WDR 4",
            "80s80s": "80s80s", "ndr2": "NDR 2", "radiobob": "Radio BOB!"}
ENTITY_RE = re.compile(r"^media_player\.[a-z0-9_]+$")

VOLUME_FILE = Path(os.environ.get("VOLUME_FILE", "/data/volumes.json"))
STATION_FILE = Path(os.environ.get("STATION_FILE", "/data/last_station.json"))
SPEAKER_FILE = Path(os.environ.get("SPEAKER_FILE", "/data/speaker_levels.json"))
RESTORE_GENERATION = 0
RADIO_MONITOR_STOP = threading.Event()
RADIO_PLAYERS = ("media_player.wohnung", "media_player.wohnzimmer", "media_player.kueche", "media_player.bad")
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
    classified = classify_devices()
    return {p["entity_id"] for p in classified["players"] + classified["groups"]}

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
def speaker_levels():
    try:
        data = json.loads(SPEAKER_FILE.read_text())
        return {key: float(value) for key, value in data.items()
                if isinstance(key, str) and ENTITY_RE.fullmatch(key)
                and type(value) in (int, float) and 0 <= value <= 1}
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


def save_speaker_levels(levels):
    with LOCK:
        state = speaker_levels()
        state.update(levels)
        SPEAKER_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary = SPEAKER_FILE.with_suffix(".tmp")
        temporary.write_text(json.dumps(state))
        temporary.replace(SPEAKER_FILE)


def capture_speaker_levels(states):
    # Capture before the radio switch powers the devices down.
    permitted = allowed_entities()
    saved = {}
    for entity in permitted:
        entry = states.get(entity, {})
        if entry.get("state") in ("unavailable", "unknown"):
            continue
        value = (entry.get("attributes") or {}).get("volume_level")
        if type(value) in (float, int) and 0 <= value <= 1:
            saved[entity] = float(value)
    if saved:
        save_speaker_levels(saved)


def restore_speakers(generation):
    """Restore only individual room volumes; leave the virtual master alone."""
    levels = speaker_levels()
    print(f"[HA Music] Restoring {len(levels)} stored speaker levels", flush=True)
    available = allowed_entities()
    for entity in sorted(e for e in levels if e != "media_player.wohnung"):
        if generation != RESTORE_GENERATION:
            return
        if entity not in available:
            continue
        try:
            ha_request("/services/media_player/volume_set", {
                "entity_id": entity, "volume_level": levels[entity]})
            print(f"[HA Music] Restored {entity}: {round(levels[entity]*100)}%", flush=True)
        except (RuntimeError, HTTPError, URLError, ValueError) as exc:
            print(f"[HA Music] Restore failed for {entity}: {exc}", flush=True)


def set_radio_ready(enabled):
    ha_request("/services/input_boolean/" + ("turn_on" if enabled else "turn_off"),
               {"entity_id": RADIO_READY})


def set_probe_volume(generation):
    """Temporary 2% volume for unmuted rooms; never write it to saved settings."""
    saved = speaker_levels()
    states = state_snapshot()
    for entity in RADIO_PLAYERS:
        if generation != RESTORE_GENERATION:
            return
        if entity == "media_player.wohnung":
            continue  # Never change the Alexa group master.
        if states.get(entity, {}).get("state") in (None, "unknown", "unavailable"):
            continue
        # Preserve explicitly muted devices. Unconfigured devices are kept quiet.
        if saved.get(entity) == 0:
            continue
        try:
            ha_request("/services/media_player/volume_set",
                       {"entity_id": entity, "volume_level": 0.02})
        except (RuntimeError, HTTPError, URLError, ValueError) as exc:
            print(f"[HA Music] Probe volume failed for {entity}: {exc}", flush=True)


def radio_start_sequence(generation):
    """Wait for Alexa boot, refresh entities, restore levels, and confirm playback."""
    try:
        # Probe the Echo states immediately; playback confirmation is the readiness test.
        print("[HA Music] Checking Echo responsiveness", flush=True)
        for attempt in range(90):
            if generation != RESTORE_GENERATION:
                return
            states = state_snapshot()
            if states.get(RADIO_SWITCH, {}).get("state") != "on":
                return
            try:
                ha_request("/services/homeassistant/update_entity",
                           {"entity_id": list(RADIO_PLAYERS)})
            except (RuntimeError, HTTPError, URLError, ValueError) as exc:
                print(f"[HA Music] Entity refresh retry: {exc}", flush=True)
            time.sleep(5)
            if generation != RESTORE_GENERATION:
                return
            states = state_snapshot()
            if states.get(RADIO_SWITCH, {}).get("state") != "on":
                return
            if not all(states.get(e, {}).get("state") not in
                       (None, "unknown", "unavailable") for e in RADIO_PLAYERS):
                time.sleep(2)
                continue

            # A successful play_media service call does not prove the Echo plays.
            station = last_selected_station()
            if station:
                confirmed = False
                for play_attempt in range(5):
                    if generation != RESTORE_GENERATION:
                        return
                    states = state_snapshot()
                    if states.get(RADIO_SWITCH, {}).get("state") != "on":
                        return
                    if any(states.get(e, {}).get("state") == "playing" for e in RADIO_PLAYERS):
                        confirmed = True
                        break
                    # Keep the playback probe quiet, including after Alexa reconnects.
                    set_probe_volume(generation)
                    if generation != RESTORE_GENERATION:
                        return
                    try:
                        perform("radio_direct", {"station": station})
                        print(f"[HA Music] Starting {station}, attempt {play_attempt+1}", flush=True)
                    except (RuntimeError, HTTPError, URLError, ValueError) as exc:
                        print(f"[HA Music] Station start retry: {exc}", flush=True)
                    time.sleep(8)
                if not confirmed:
                    states = state_snapshot()
                    confirmed = any(states.get(e, {}).get("state") == "playing"
                                    for e in RADIO_PLAYERS)
                if not confirmed:
                    print("[HA Music] Playback unconfirmed; retrying Alexa readiness", flush=True)
                    time.sleep(4)
                    continue
                MONITOR.select(station)

            restore_speakers(generation)
            if generation != RESTORE_GENERATION:
                return
            set_radio_ready(True)
            print("[HA Music] Alexa initialization completed", flush=True)
            return
        print("[HA Music] Alexa startup timed out; readiness stays off", flush=True)
    except (RuntimeError, HTTPError, URLError, ValueError) as exc:
        print(f"[HA Music] Initialization failed: {exc}", flush=True)


def radio_switch_monitor():
    """Track all changes to the HA switch, including external automations."""
    global RESTORE_GENERATION
    last_power = None
    while not RADIO_MONITOR_STOP.is_set():
        try:
            state = state_snapshot().get(RADIO_SWITCH, {}).get("state")
            if state in ("on", "off") and state != last_power:
                last_power = state
                with LOCK:
                    RESTORE_GENERATION += 1
                    generation = RESTORE_GENERATION
                if state == "on":
                    # Clear stale readiness from a previous add-on process.
                    set_radio_ready(False)
                    threading.Thread(target=radio_start_sequence,
                                     args=(generation,), daemon=True).start()
                else:
                    MONITOR.select("")
                    # Mirror old off-delay, but cancel a pending start immediately.
                    time.sleep(10)
                    if generation == RESTORE_GENERATION:
                        set_radio_ready(False)
                print(f"[HA Music] Radio switch detected: {state}", flush=True)
        except (RuntimeError, HTTPError, URLError, ValueError) as exc:
            print(f"[HA Music] Radio monitor retry: {exc}", flush=True)
        RADIO_MONITOR_STOP.wait(3)


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

RADIO_SWITCH = "switch.alexa_alle"
RADIO_READY = "input_boolean.alexa_hochgefahren"
# Initial provider phrases copied exactly from the user's existing radio scripts.
DIRECT_STATIONS = {
    "1live": {"name": "1LIVE", "target": "media_player.wohnzimmer", "media_content_type": "custom", "media_content_id": "spiele eins live aus der ARD Audiothek auf Wohnung"},
    "wdr2": {"name": "WDR 2", "target": "media_player.wohnzimmer", "media_content_type": "custom", "media_content_id": "spiele wdr zwei aus der ARD Audiothek auf Wohnung"},
    "swr3": {"name": "SWR3", "target": "media_player.wohnzimmer", "media_content_type": "custom", "media_content_id": "spiele swr3 aus der ard audiothek auf Wohnung"},
    "sommerhits": {"name": "Sommerhits", "target": "media_player.wohnzimmer", "media_content_type": "custom", "media_content_id": "spiele amazon Sommerhits auf Wohnung"},
    "charts": {"name": "Charts", "target": "media_player.wohnzimmer", "media_content_type": "AMAZON_MUSIC", "media_content_id": "spiele  die charts auf Wohnung"},
    "80s": {"name": "80er", "target": "media_player.wohnzimmer", "media_content_type": "AMAZON_MUSIC", "media_content_id": "spiele best of achtziger auf Wohnung"},
    "90s": {"name": "90er", "target": "media_player.wohnzimmer", "media_content_type": "AMAZON_MUSIC", "media_content_id": "spiele hits der neunziger auf Wohnung"},
}

def last_selected_station():
    try:
        station = json.loads(STATION_FILE.read_text()).get("station", "")
        return station if station in DIRECT_STATIONS else ""
    except (OSError, ValueError, AttributeError):
        return ""


def save_selected_station(station):
    if station not in DIRECT_STATIONS:
        return
    with LOCK:
        STATION_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary = STATION_FILE.with_suffix(".tmp")
        temporary.write_text(json.dumps({"station": station}))
        temporary.replace(STATION_FILE)


def state_snapshot():
    states = ha_request("/states")
    return {s["entity_id"]:s for s in states if isinstance(s,dict) and isinstance(s.get("entity_id"),str)}

def radio_state():
    states = state_snapshot()
    return {"power":states.get(RADIO_SWITCH,{}).get("state","unavailable"),
            "ready":states.get(RADIO_READY,{}).get("state","unavailable"),
            "last_station":last_selected_station(),
            "stations":[{"id":key, "name":item["name"],
                         "available":item["target"] in states and
                            states[item["target"]].get("state") not in ("unknown","unavailable")}
                        for key,item in DIRECT_STATIONS.items()]}


def playback_status():
    states = state_snapshot()
    candidates = ("media_player.wohnzimmer", "media_player.wohnung",
                  "media_player.kueche", "media_player.bad")
    observed = []
    for entity in candidates:
        state = states.get(entity)
        if not state:
            continue
        attrs = state.get("attributes") or {}
        observed.append({"entity_id": entity, "state": state.get("state","unknown"),
                         "title": attrs.get("media_title"),
                         "artist": attrs.get("media_artist"),
                         "album": attrs.get("media_album_name"),
                         "image": attrs.get("entity_picture") or attrs.get("media_image_url"),
                         "content_type": attrs.get("media_content_type")})
    active = next((p for p in observed if p["state"] == "playing"), None)
    details = next((p for p in observed if p["title"] or p["artist"]), None)
    return {"playing": active is not None, "players": observed,
            "details": active if active and (active["title"] or active["artist"]) else details}


def perform(action, body):
    if action == "radio_direct":
        key = body.get("station")
        if not isinstance(key,str) or key not in DIRECT_STATIONS:
            raise ValueError("Sender noch nicht für die direkte Wiedergabe bestätigt")
        preset = DIRECT_STATIONS[key]
        states = state_snapshot()
        target = preset["target"]
        if target not in states or states[target].get("state") in ("unknown","unavailable"):
            raise ValueError("Alexa-Zielgerät nicht verfügbar")
        return ha_request("/services/media_player/play_media", {
            "entity_id":target, "media": {
                "media_content_id":preset["media_content_id"],
                "media_content_type":preset["media_content_type"], "metadata":{}}})
    if action == "radio_power":
        turn_on = body.get("on")
        if not isinstance(turn_on, bool):
            raise ValueError("Ungültiger Schaltzustand")
        states = state_snapshot()
        if states.get(RADIO_SWITCH, {}).get("state") in (None, "unavailable", "unknown"):
            raise ValueError("Radioschalter nicht verfügbar")
        # The background monitor handles every switch transition exactly once.
        return ha_request("/services/switch/" + ("turn_on" if turn_on else "turn_off"),
                          {"entity_id": RADIO_SWITCH})
    if action == "volume":
        entity = body.get("entity_id", "")
        level = body.get("volume")
        if not isinstance(entity, str) or not ENTITY_RE.fullmatch(entity) or type(level) not in (int, float) or not 0 <= level <= 1:
            raise ValueError("Ungültige Lautstärke oder Entity")
        if entity not in allowed_entities():
            raise ValueError("Media Player nicht freigegeben")
        found = next((x for x in players() if x["entity_id"] == entity), None)
        if not found and not any(x["entity_id"] == entity for x in classify_devices()["groups"]):
            raise ValueError("Media Player nicht gefunden")
        if entity == "media_player.wohnung":
            # Independent virtual master: apply its absolute percentage to unmuted rooms.
            room_players = players()
            saved = speaker_levels()
            active = [p for p in room_players
                      if p["state"] not in ("unknown", "unavailable")
                      and saved.get(p["entity_id"], p.get("volume") or 0) > 0]
            changed = {}
            failed = []
            for p in active:
                try:
                    ha_request("/services/media_player/volume_set", {
                        "entity_id": p["entity_id"], "volume_level": level})
                    changed[p["entity_id"]] = float(level)
                except (RuntimeError, HTTPError, URLError, ValueError) as exc:
                    failed.append(f"{p['entity_id']}: {exc}")
            # At 0%, preserve each room's active/muted intent so raising master
            # can restore active rooms without reactivating explicitly muted ones.
            save_speaker_levels({"media_player.wohnung": float(level),
                                 **(changed if level > 0 else {})})
            if failed:
                print("[HA Music] Master partial failure: " + "; ".join(failed), flush=True)
                raise RuntimeError("Master: " + "; ".join(failed))
            return {"ok": True, "updated": len(changed)}
        result = ha_request("/services/media_player/volume_set", {"entity_id": entity, "volume_level": level})
        save_speaker_levels({entity: float(level)})
        print(f"[HA Music] Saved speaker {entity}: {round(level * 100)}%", flush=True)
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
        if name == "events" and "/api/" in path:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-transform")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            seq = -1
            try:
                while True:
                    next_seq, station, payload = MONITOR.await_change(seq, timeout=15)
                    if next_seq == seq:
                        self.wfile.write(b": keepalive\n\n")
                    else:
                        message = json.dumps({"station": station, "metadata": payload}, ensure_ascii=False)
                        self.wfile.write(("data: " + message + "\n\n").encode("utf-8"))
                        seq = next_seq
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            return
        if name == "radio-state" and "/api/" in path:
            try:
                return self.reply(200, radio_state())
            except (RuntimeError, HTTPError, URLError, ValueError) as exc:
                return self.reply(503, {"error": str(exc)})
        if name == "playback-status" and "/api/" in path:
            try:
                return self.reply(200, playback_status())
            except (RuntimeError, HTTPError, URLError, ValueError) as exc:
                return self.reply(503, {"error": str(exc)})
        if name == "players" and "/api/" in path:
            try:
                inventory = integration_inventory()
                classified = classify_devices()
                return self.reply(200, {"players": classified["players"], "groups": classified["groups"], "excluded": classified["excluded"],
                    "remembered": remembered(), "saved_levels": speaker_levels(), "discovery": "integration_registry",
                    "diagnostics": {domain: {"entities": len(values),
                        "media_players": sum(v.startswith("media_player.") for v in values)}
                        for domain, values in inventory.items()}})
            except (RuntimeError, HTTPError, URLError, ValueError) as exc:
                return self.reply(503, {"error": str(exc)})
        name = name or "index.html"
        if name not in ("index.html", "style.css", "app.js", "1live.svg", "wdr2.svg", "swr3.svg"):
            self.send_error(404)
            return
        content = (WEB / name).read_bytes()
        mime = {"index.html": "text/html", "style.css": "text/css", "app.js": "application/javascript", "1live.svg": "image/svg+xml", "wdr2.svg": "image/svg+xml", "swr3.svg": "image/svg+xml"}[name]
        self.send_response(200)
        self.send_header("Content-Type", mime + "; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def do_POST(self):
        path = unquote(urlsplit(self.path).path)
        action = path.rsplit("/", 1)[-1]
        if "/api/" not in path or action not in ("volume", "radio_direct", "radio_power"):
            return self.reply(404, {"error": "Not found"})
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 2048:
                return self.reply(400, {"error": "Invalid request length"})
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict):
                raise ValueError("Invalid body")
            perform(action, body)
            if action == "radio_direct":
                MONITOR.select(body["station"])
                save_selected_station(body["station"])
            elif action == "radio_power" and not body["on"]:
                MONITOR.select("")
            return self.reply(200, {"ok": True})
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            return self.reply(400, {"error": str(exc)})
        except (RuntimeError, HTTPError, URLError) as exc:
            return self.reply(502, {"error": str(exc)})

if __name__ == "__main__":
    threading.Thread(target=radio_switch_monitor, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
