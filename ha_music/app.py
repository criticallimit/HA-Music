"""Ingress app and restricted Home Assistant Alexa control API."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit
from urllib.request import Request, urlopen
from http.client import HTTPException
import json
from copy import deepcopy
import os
import re
import threading
import time
from metadata_feed import MONITOR
from metadata import close_stream

WEB = (Path(__file__).parent / "web").resolve()
PORT = int(os.environ.get("PORT", "8099"))
OPTIONS = Path(os.environ.get("OPTIONS_FILE", "/data/options.json"))
HA_API = os.environ.get("HA_API", "http://supervisor/core/api").rstrip("/")
SUPERVISOR_API = os.environ.get("SUPERVISOR_API", "http://supervisor").rstrip("/")
TOKEN = os.environ.get("SUPERVISOR_TOKEN", "") or os.environ.get("HASSIO_TOKEN", "")
STATIONS = {"wdr2": "WDR 2", "1live": "1LIVE", "wdr4": "WDR 4",
            "80s80s": "80s80s", "ndr2": "NDR 2", "radiobob": "Radio BOB!"}
ENTITY_RE = re.compile(r"^media_player\.[a-z0-9_]+$")

VOLUME_FILE = Path(os.environ.get("VOLUME_FILE", "/data/volumes.json"))
STATION_FILE = Path(os.environ.get("STATION_FILE", "/data/last_station.json"))
SPEAKER_FILE = Path(os.environ.get("SPEAKER_FILE", "/data/speaker_levels.json"))
VIEW_FILE = Path(os.environ.get("VIEW_FILE", "/data/selected_view.json"))
CARD_INSTALLED_FILE = Path("/data/dashboard_card_installed")
STARTED_AT = None
RESTORE_GENERATION = 0
RADIO_MONITOR_STOP = threading.Event()
STANDBY = threading.Event()
STANDBY.set()  # Only an explicit local power-on permits network/startup.
READY = False
PREPARING = False
ROOM_TARGETS = {}
STARTUP_ERROR = None
STATE_LOCK = threading.RLock()
COMMAND_LOCK = threading.RLock()
POWER_LOCK = threading.Lock()
CANCEL = threading.Event()
NETWORK_ERRORS = (RuntimeError, OSError, ValueError, HTTPException)
STANDBY_UNTIL = 0.0
LAST_POWER = "off"
RADIO_PLAYERS = ("media_player.wohnung", "media_player.wohnzimmer", "media_player.kueche", "media_player.bad")
LOCK = threading.Lock()
CACHE_LOCK = threading.RLock()
STATE_CACHE = (0.0, None)
SSE_SLOTS = threading.BoundedSemaphore(16)
RESPONSE_LOCK = threading.Lock()
ACTIVE_RESPONSES = set()
CONFIG_LOCK = threading.RLock()
INVENTORY_LOCK = threading.Lock()
INVENTORY_CACHE = (0.0, None)
DEVICE_SYNC_LOCK = threading.Lock()
SUPERVISOR_OPTIONS = None
REGISTERED_DEVICE_NAMES = {}
MEDIA_FEATURE_PAUSE = 1
MEDIA_FEATURE_PLAY = 16384
INVENTORY_TEMPLATE = """
{% set result = namespace(data={}, names={}) %}
{% for domain in ['alexa_devices', 'alexa_media'] %}
  {% set catalog = namespace(entities=integration_entities(domain) | list, entries=[]) %}
  {% for entity in catalog.entities %}
    {% set entry = config_entry_id(entity) %}
    {% if entry and entry not in catalog.entries and config_entry_attr(entry, 'domain') == domain %}
      {% set catalog.entries = catalog.entries + [entry] %}
      {% set title = config_entry_attr(entry, 'title') %}
      {% if title %}
        {% for registered in integration_entities(title) %}
          {% set registered_entry = config_entry_id(registered) %}
          {% if registered_entry and config_entry_attr(registered_entry, 'domain') == domain %}
            {% set catalog.entities = catalog.entities + [registered] %}
          {% endif %}
        {% endfor %}
      {% endif %}
    {% endif %}
  {% endfor %}
  {% set verified = namespace(entities=[]) %}
  {% for entity in catalog.entities | unique %}
    {% set entry = config_entry_id(entity) %}
    {% if not entry or config_entry_attr(entry, 'domain') == domain %}
      {% set verified.entities = verified.entities + [entity] %}
    {% endif %}
  {% endfor %}
  {% set catalog.entities = verified.entities %}
  {% for entity in catalog.entities %}
    {% if entity.startswith('media_player.') %}
      {% set name = device_attr(entity, 'name_by_user') or device_attr(entity, 'name') %}
      {% if name %}
        {% set result.names = dict(result.names, **{entity: name}) %}
      {% endif %}
    {% endif %}
  {% endfor %}
  {% set result.data = dict(result.data, **{domain: catalog.entities}) %}
{% endfor %}
{{ dict(result.data, device_names=result.names) | to_json }}
"""


def configured_devices(config=None):
    config = options() if config is None else config
    result = {}
    entries = config.get("devices", [])
    if not isinstance(entries, list):
        raise ValueError("Ungültige Geräteliste in der Add-on-Konfiguration")
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Ungültiger Geräteeintrag")
        entity = entry.get("entity_id")
        if not isinstance(entity, str) or not ENTITY_RE.fullmatch(entity) or entity in result:
            raise ValueError("Ungültige oder doppelte Geräte-Entity")
        if "status" in entry:
            if entry["status"] not in ("Aktiv", "Inaktiv"):
                raise ValueError("Ungültiger Gerätestatus: Aktiv oder Inaktiv auswählen")
            enabled = entry["status"] == "Aktiv"
        elif type(entry.get("enabled")) is bool:
            enabled = entry["enabled"]
        else:
            raise ValueError("Gerätestatus fehlt")
        result[entity] = {**entry, "enabled": enabled}
    return result


def enabled_device_ids():
    return {entity for entity, entry in configured_devices().items() if entry["enabled"]}


def merge_discovered_devices(config, found):
    """Append discoveries; keep user labels, toggles and unavailable entries."""
    merged = deepcopy(config)
    known = configured_devices(config)
    entries = merged.setdefault("devices", [])
    for entry in entries:
        entry["status"] = "Aktiv" if known[entry["entity_id"]]["enabled"] else "Inaktiv"
        entry.pop("enabled", None)
    for device in sorted(found, key=lambda item: item["entity_id"]):
        entity = device["entity_id"]
        if entity not in known:
            entries.append({"entity_id": entity, "name": device["name"], "status": "Inaktiv"})
            known[entity] = entries[-1]
    return merged


def supervisor_request(path, payload=None, *, bootstrap=False):
    if path not in ("/addons/self/info", "/addons/self/options"):
        raise ValueError("Supervisor-Endpunkt nicht freigegeben")
    kwargs = {"startup_configuration": True} if bootstrap else {}
    response = ha_request(path, payload, supervisor=True, **kwargs)
    if not isinstance(response, dict) or response.get("result") != "ok":
        raise RuntimeError("Supervisor-Konfiguration konnte nicht gelesen/gespeichert werden")
    data = response.get("data") or {}
    if not isinstance(data, dict):
        raise ValueError("Ungültige Supervisor-Konfigurationsantwort")
    return data


def synchronize_device_configuration(generation, *, migrate_only=False, bootstrap=False):
    global SUPERVISOR_OPTIONS
    if bootstrap and (generation is not None or not migrate_only):
        raise ValueError("Initialisierung darf nur vorhandene Geräteoptionen übernehmen")
    def check_active():
        if not bootstrap:
            check_generation(generation)
    def request(path, payload=None):
        kwargs = {"bootstrap": True} if bootstrap else {}
        return supervisor_request(path, payload, **kwargs)
    with DEVICE_SYNC_LOCK:
        check_active()
        current = request("/addons/self/info").get("options")
        if not isinstance(current, dict):
            raise ValueError("Supervisor-Gerätekonfiguration fehlt")
        found = [] if migrate_only else detected_devices()
        if not migrate_only:
            print("[HA Music] Alexa discovery: " + str(len(found)) + " media players: " +
                  ", ".join(device["entity_id"] for device in found), flush=True)
        check_active()
        merged = merge_discovered_devices(current, found)
        if merged != current:
            # Read again immediately before the write; preserve intervening edits.
            latest = request("/addons/self/info").get("options")
            if not isinstance(latest, dict):
                raise ValueError("Supervisor-Gerätekonfiguration fehlt")
            merged = merge_discovered_devices(latest, found)
            check_active()
            if merged != latest:
                request("/addons/self/options", {"options": merged})
        check_active()
        with CONFIG_LOCK:
            SUPERVISOR_OPTIONS = deepcopy(merged)
        if migrate_only:
            print("[HA Music] Device status migration completed: Aktiv/Inaktiv", flush=True)
        print(f"[HA Music] Device configuration: {len(merged.get('devices', []))} entries", flush=True)
def integration_inventory():
    """Coalesce registry reads; never serve the cache as a standby wake-up."""
    global INVENTORY_CACHE
    with INVENTORY_LOCK:
        if STANDBY.is_set():
            raise RuntimeError("HA Music standby: outbound network disabled")
        at, cached = INVENTORY_CACHE
        if cached is not None and time.monotonic() - at < 30:
            return deepcopy(cached)
        inventory = read_integration_inventory()
        if STANDBY.is_set():
            raise RuntimeError("HA Music standby: inventory cancelled")
        INVENTORY_CACHE = (time.monotonic(), deepcopy(inventory))
        return inventory


def read_integration_inventory():
    """Expand loaded Alexa sources to each account's complete entity registry."""
    response = ha_request("/template", {"template": INVENTORY_TEMPLATE})
    if not isinstance(response, str):
        raise ValueError("Unerwartete Antwort der Home-Assistant-Template-API")
    data = json.loads(response)
    if not isinstance(data, dict):
        raise ValueError("Ungültiges Alexa-Inventar")
    if any(not isinstance(data.get(domain, []), list) for domain in ("alexa_devices", "alexa_media")):
        raise ValueError("Ungültige Alexa-Entity-Liste")
    names = data.get("device_names", {})
    if not isinstance(names, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in names.items()):
        raise ValueError("Ungültige Alexa-Gerätenamen")
    with CONFIG_LOCK:
        REGISTERED_DEVICE_NAMES.clear()
        REGISTERED_DEVICE_NAMES.update({k: v for k, v in names.items() if ENTITY_RE.fullmatch(k)})
    return {domain: [eid for eid in data.get(domain, []) if isinstance(eid, str)]
            for domain in ("alexa_devices", "alexa_media")}

def integration_player_ids():
    inventory = integration_inventory()
    return {entity for values in inventory.values() for entity in values
            if ENTITY_RE.fullmatch(entity)}

def detected_devices(inventory=None):
    inventory = inventory if inventory is not None else integration_inventory()
    ids = {eid for values in inventory.values() for eid in values if ENTITY_RE.fullmatch(eid)}
    states = state_snapshot()
    with CONFIG_LOCK:
        names = dict(REGISTERED_DEVICE_NAMES)
    found = []
    for entity in ids:
        state = states.get(entity, {})
        attributes = state.get("attributes") or {}
        name = str(attributes.get("friendly_name") or names.get(entity) or entity)
        found.append({"entity_id": entity, "name": name,
                      "state": state.get("state", "unavailable"),
                      "volume": attributes.get("volume_level"),
                      "features": attributes.get("supported_features", 0),
                      "possible_group": False})
    return sorted(found, key=lambda item: item["name"].casefold())

def classify_devices(inventory=None):
    """Show only enabled registered devices; Wohnung is the virtual master."""
    group, rooms, excluded = [], [], []
    selected = configured_devices()
    for player in detected_devices(inventory):
        entry = selected.get(player["entity_id"])
        if not entry or not entry["enabled"]:
            continue
        player["name"] = str(entry.get("name") or player["name"])
        entity = player["entity_id"].casefold()
        if entity == "media_player.wohnung":
            group.append(player)
        else:
            rooms.append(player)
    return {"groups": group, "players": rooms, "excluded": excluded}

def allowed_entities():
    classified = classify_devices()
    return {p["entity_id"] for p in classified["players"] + classified["groups"]}

def remembered():
    try:
        obj = json.loads(VOLUME_FILE.read_text())
        return {entity: float(value) for entity, value in obj.items()
                if isinstance(entity, str) and ENTITY_RE.fullmatch(entity)
                and type(value) in (int, float) and 0 <= value <= 1} if isinstance(obj, dict) else {}
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


def displayed_speaker_levels():
    with STATE_LOCK:
        return {**speaker_levels(), **ROOM_TARGETS}


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


class StartupCancelled(RuntimeError):
    pass


def check_generation(generation):
    if generation != RESTORE_GENERATION or LAST_POWER != "on" or STANDBY.is_set():
        raise StartupCancelled("Start abgebrochen")


def startup_request(generation, path, payload=None):
    # Serialize commands; recheck after acquiring the lock and after slow I/O.
    with COMMAND_LOCK:
        check_generation(generation)
        if path in ("/services/media_player/volume_set", "/services/media_player/play_media", "/services/media_player/media_play", "/services/media_player/media_pause", "/services/homeassistant/update_entity"):
            targets = payload.get("entity_id", [])
            targets = [targets] if isinstance(targets, str) else targets
            selected = enabled_device_ids()
            if any(entity not in selected for entity in targets):
                raise ValueError("Gerät ist in der Add-on-Konfiguration deaktiviert")
        result = ha_request(path, payload)
        check_generation(generation)
        return result


def verify_restored_volumes(generation, expected):
    expected = dict(expected)
    for _ in range(3):
        if not wait_for_start(generation, 2):
            return
        try:
            states = state_snapshot()
            check_generation(generation)
        except StartupCancelled:
            return
        except NETWORK_ERRORS as exc:
            print(f"[HA Music] Volume verification retry: {exc}", flush=True)
            continue
        for entity, level in list(expected.items()):
            entry = states.get(entity, {})
            observed = (entry.get("attributes") or {}).get("volume_level")
            if entry.get("state") not in ("unknown", "unavailable") and type(observed) in (float, int) and abs(observed - level) <= 0.011:
                expected.pop(entity)
        if not expected:
            return
    for entity in expected:
        print(f"[HA Music] WARNING: {entity} has not confirmed its restored volume", flush=True)


def restore_speakers(generation, use_master=False):
    levels = speaker_levels()
    master = levels.get("media_player.wohnung")
    if use_master and master is None:
        raise RuntimeError("Master-Lautstärke nicht verfügbar")
    available = allowed_entities()
    states = state_snapshot()
    sent, failed = {}, []
    for entity in sorted(e for e in levels if e != "media_player.wohnung"):
        check_generation(generation)
        if entity not in available or states.get(entity, {}).get("state") in (None, "unknown", "unavailable"):
            continue
        # A muted virtual master must remain silent across power cycles.
        level = 0.0 if levels[entity] == 0 or master == 0 else master if use_master else levels[entity]
        try:
            startup_request(generation, "/services/media_player/volume_set",
                            {"entity_id": entity, "volume_level": level})
            sent[entity] = level
            with STATE_LOCK:
                check_generation(generation)
                ROOM_TARGETS[entity] = level
                if use_master and level > 0:
                    save_speaker_levels({entity: level})
                    save_remembered(entity, level)
        except StartupCancelled:
            raise
        except NETWORK_ERRORS:
            failed.append(entity)
    if sent:
        threading.Thread(target=verify_restored_volumes,
                         args=(generation, sent), daemon=True).start()
    if failed:
        raise RuntimeError("Lautstärke nicht wiederhergestellt: " + ", ".join(failed))


def set_radio_ready(enabled):
    ha_request("/services/input_boolean/" + ("turn_on" if enabled else "turn_off"),
               {"entity_id": RADIO_READY})


def wait_for_start(generation, seconds):
    with STATE_LOCK:
        if generation != RESTORE_GENERATION or LAST_POWER != "on" or STANDBY.is_set():
            return False
        cancellation = CANCEL
    if cancellation.wait(seconds):
        return False
    try:
        check_generation(generation)
        return True
    except StartupCancelled:
        return False


def set_probe_volume(generation):
    saved = speaker_levels()
    states = state_snapshot()
    permitted = allowed_entities()
    if "media_player.wohnung" not in saved:
        entry = states.get("media_player.wohnung", {})
        master = (entry.get("attributes") or {}).get("volume_level")
        if entry.get("state") in (None, "unknown", "unavailable") or type(master) not in (int, float) or not 0 <= master <= 1:
            raise RuntimeError("Master-Lautstärke nicht verfügbar")
        check_generation(generation)
        save_speaker_levels({"media_player.wohnung": float(master)})
        saved["media_player.wohnung"] = float(master)
    # Preserve known pre-probe levels on first use; never guess a restore value.
    missing = {}
    for entity in sorted(permitted):
        entry = states.get(entity, {})
        value = (entry.get("attributes") or {}).get("volume_level")
        if entity != "media_player.wohnung" and entity in permitted and entity not in saved and entry.get("state") not in (None, "unknown", "unavailable") and type(value) in (int, float) and 0 <= value <= 1:
            missing[entity] = value
    check_generation(generation)
    if missing:
        save_speaker_levels(missing)
        saved.update(missing)
    probed = {}
    for entity in sorted(permitted):
        check_generation(generation)
        if entity == "media_player.wohnung" or entity not in permitted or entity not in saved:
            continue
        if states.get(entity, {}).get("state") in (None, "unknown", "unavailable"):
            continue
        level = 0.0 if saved[entity] == 0 or saved.get("media_player.wohnung") == 0 else 0.01
        startup_request(generation, "/services/media_player/volume_set",
                        {"entity_id": entity, "volume_level": level})
        probed[entity] = level
    print(f"[HA Music] Startup probe applied: {json.dumps(probed, sort_keys=True)}", flush=True)
    return probed


def radio_start_sequence(generation):
    global READY, PREPARING, STARTUP_ERROR, STARTED_AT
    try:
        with STATE_LOCK:
            check_generation(generation)
            PREPARING = True
        try:
            startup_request(generation, "/services/input_boolean/turn_off", {"entity_id": RADIO_READY})
        except StartupCancelled:
            raise
        except NETWORK_ERRORS as exc:
            print(f"[HA Music] Ready helper reset failed: {exc}", flush=True)
        print("[HA Music] Waiting 40 seconds before Alexa reload", flush=True)
        if not wait_for_start(generation, 40):
            return
        release_deadline = time.monotonic() + 10
        selected = enabled_device_ids()
        station = last_selected_station()
        preferred = DIRECT_STATIONS[station]["target"] if station else "media_player.wohnung"
        reload_target = preferred if preferred in selected else next(iter(sorted(selected)), None)
        if reload_target:
            print(f"[HA Music] Reloading Alexa integration via {reload_target}", flush=True)
            try:
                startup_request(generation, "/services/homeassistant/reload_config_entry",
                                {"entity_id": reload_target})
            except StartupCancelled:
                raise
            except NETWORK_ERRORS as exc:
                print(f"[HA Music] Alexa integration reload failed: {exc}", flush=True)
            else:
                print("[HA Music] Alexa integration reload completed", flush=True)
        remaining = max(0.0, release_deadline - time.monotonic())
        if not wait_for_start(generation, remaining):
            return
        with STATE_LOCK:
            check_generation(generation)
            READY = True
            STARTED_AT = None
        print("[HA Music] Interface released after 50-second wait", flush=True)
        try:
            startup_request(generation, "/services/input_boolean/turn_on", {"entity_id": RADIO_READY})
        except StartupCancelled:
            raise
        except NETWORK_ERRORS as exc:
            print(f"[HA Music] Ready helper update failed: {exc}", flush=True)
        try:
            synchronize_device_configuration(generation)
        except StartupCancelled:
            raise
        except NETWORK_ERRORS as exc:
            print(f"[HA Music] Device synchronization failed: {exc}", flush=True)
        refresh_entities = sorted(allowed_entities())
        if refresh_entities:
            startup_request(generation, "/services/homeassistant/update_entity",
                            {"entity_id": refresh_entities})
        station = last_selected_station()
        startup_completed = False
        try:
            probed = set_probe_volume(generation)
            if not wait_for_start(generation, 2):
                return
            if station and DIRECT_STATIONS[station]["target"] in enabled_device_ids():
                target = DIRECT_STATIONS[station]["target"]
                if target not in probed:
                    raise RuntimeError(f"Senderstart nicht gesendet: keine sichere Startlautstärke für {target}")
                play_station(station, generation, check_availability=False)
            startup_completed = True
        finally:
            # Even a failed station/probe command must not leave rooms at 1%.
            check_generation(generation)
            restore_speakers(generation, use_master=startup_completed)
        print("[HA Music] Startup commands completed; audible playback is not guaranteed", flush=True)
    except StartupCancelled:
        return
    except NETWORK_ERRORS as exc:
        with STATE_LOCK:
            if generation == RESTORE_GENERATION:
                STARTED_AT = None
                if not READY:
                    STARTUP_ERROR = str(exc)
        print(f"[HA Music] Startup failed: {exc}", flush=True)
    finally:
        with STATE_LOCK:
            if generation == RESTORE_GENERATION:
                PREPARING = False


def enter_standby(generation):
    with STATE_LOCK:
        if generation != RESTORE_GENERATION or LAST_POWER != "off":
            return
        STANDBY.set()  # Close admission before stopping streams.
        with RESPONSE_LOCK:
            responses = list(ACTIVE_RESPONSES)
        for response in responses:
            close_stream(response)
        MONITOR.stop()
    print("[HA Music] Standby active: outgoing requests disabled", flush=True)


def transition_power(on):
    global RESTORE_GENERATION, STARTED_AT, LAST_POWER, STANDBY_UNTIL, READY, PREPARING, STARTUP_ERROR, CANCEL
    with STATE_LOCK:
        CANCEL.set()
        CANCEL = threading.Event()
        RESTORE_GENERATION += 1
        generation = RESTORE_GENERATION
        LAST_POWER = "on" if on else "off"
        READY = False
        PREPARING = on
        STARTUP_ERROR = None
        ROOM_TARGETS.clear()
        STARTED_AT = time.monotonic() if on else None
        STANDBY_UNTIL = 0 if on else time.monotonic() + 10
        if on:
            MONITOR.resume()
        else:
            MONITOR.stop()
            timer = threading.Timer(10, enter_standby, args=(generation,))
            timer.daemon = True
            timer.start()
        return generation


def radio_switch_monitor():
    # External off cancels startup; external on never initiates playback/wake.
    observed_generation, seen_on = None, False
    while not RADIO_MONITOR_STOP.wait(3):
        if STANDBY.is_set() or LAST_POWER != "on":
            continue
        try:
            with POWER_LOCK:
                generation = RESTORE_GENERATION
                if generation != observed_generation:
                    observed_generation, seen_on = generation, False
                state = state_snapshot().get(RADIO_SWITCH, {}).get("state")
                if state == "on":
                    seen_on = True
                switched_off = False
                with STATE_LOCK:
                    if generation == RESTORE_GENERATION and seen_on and state == "off":
                        transition_power(False)
                        switched_off = True
                if switched_off:
                    set_radio_ready(False)
        except NETWORK_ERRORS as exc:
            print(f"[HA Music] Radio monitor retry: {exc}", flush=True)


def options():
    with CONFIG_LOCK:
        if SUPERVISOR_OPTIONS is not None:
            return deepcopy(SUPERVISOR_OPTIONS)
    try:
        data = json.loads(OPTIONS.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}

def ha_request(path, payload=None, *, supervisor=False, startup_configuration=False):
    global STATE_CACHE, INVENTORY_CACHE
    configuration_access = startup_configuration and supervisor and path in ("/addons/self/info", "/addons/self/options")
    if startup_configuration and not configuration_access:
        raise ValueError("Initialisierung erlaubt nur die eigenen Supervisor-Optionen")
    if STANDBY.is_set() and not configuration_access:
        raise RuntimeError("HA Music standby: outbound network disabled")
    if not TOKEN:
        raise RuntimeError("Home Assistant API token unavailable")
    data = json.dumps(payload).encode() if payload is not None else None
    req = Request((SUPERVISOR_API if supervisor else HA_API) + path, data=data, headers={
        "Authorization": "Bearer " + TOKEN,
        "Content-Type": "application/json",
    }, method="POST" if payload is not None else "GET")
    timeout = 30 if path == "/services/homeassistant/reload_config_entry" else 8
    with urlopen(req, timeout=timeout) as response:
        with RESPONSE_LOCK:
            if STANDBY.is_set() and not configuration_access:
                raise RuntimeError("HA Music standby: response cancelled")
            ACTIVE_RESPONSES.add(response)
        try:
            raw = response.read()
        finally:
            with RESPONSE_LOCK:
                ACTIVE_RESPONSES.discard(response)
        if payload is not None and not supervisor:
            with CACHE_LOCK:
                STATE_CACHE = (0.0, None)
            if path == "/services/homeassistant/reload_config_entry":
                with INVENTORY_LOCK:
                    INVENTORY_CACHE = (0.0, None)
        # Some Home Assistant service responses are empty on success.
        if not raw.strip():
            return {}
        if path == "/template" and not supervisor:
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
        return station if isinstance(station, str) and station in DIRECT_STATIONS else ""
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


def dashboard_card_installed():
    return CARD_INSTALLED_FILE.is_file()


def mark_dashboard_card_installed():
    CARD_INSTALLED_FILE.parent.mkdir(parents=True, exist_ok=True)
    CARD_INSTALLED_FILE.touch(exist_ok=True)


def selected_view():
    try:
        value = json.loads(VIEW_FILE.read_text()).get("view")
        return value if value in ("radio", "apple") else "radio"
    except (OSError, ValueError, AttributeError):
        return "radio"


def save_selected_view(view):
    if view not in ("radio", "apple"):
        raise ValueError("Ungültige Ansicht")
    with LOCK:
        VIEW_FILE.parent.mkdir(parents=True, exist_ok=True)
        temp = VIEW_FILE.with_suffix(".tmp")
        temp.write_text(json.dumps({"view": view}))
        temp.replace(VIEW_FILE)


def startup_remaining():
    if STARTED_AT is None:
        return None
    return max(0, 50 - int(time.monotonic() - STARTED_AT))


def state_snapshot():
    global STATE_CACHE
    with CACHE_LOCK:
        if STANDBY.is_set():
            raise RuntimeError("HA Music standby: outbound network disabled")
        at, cached = STATE_CACHE
        if cached is not None and time.monotonic() - at < 2:
            return cached
        states = ha_request("/states")
        if not isinstance(states, list):
            raise ValueError("Ungültige Home-Assistant-Zustände")
        result = {s["entity_id"]:s for s in states if isinstance(s,dict) and isinstance(s.get("entity_id"),str)}
        STATE_CACHE = (time.monotonic(), result)
        return result

def radio_state():
    if STANDBY.is_set():
        return {"power":"off", "ready":"off", "standby":True, "last_station":last_selected_station(),
                "selected_view":selected_view(), "startup_remaining":None,
                "dashboard_card_installed":dashboard_card_installed(),
                "show_dashboard_setup":options().get("show_dashboard_setup", False) is True,
                "stations":[{"id":key,"name":item["name"],"available":False} for key,item in DIRECT_STATIONS.items()]}
    states = state_snapshot()
    selected = enabled_device_ids()
    return {"power":LAST_POWER,
            "ready":"on" if READY else "off",
            "preparing":PREPARING,
            "startup_error":STARTUP_ERROR,
            "last_station":last_selected_station(),
            "selected_view":selected_view(),
            "startup_remaining":startup_remaining(),
            "dashboard_card_installed":dashboard_card_installed(),
            "show_dashboard_setup":options().get("show_dashboard_setup", False) is True,
            "stations":[{"id":key, "name":item["name"],
                         "available":item["target"] in selected and item["target"] in states and
                            states[item["target"]].get("state") not in ("unknown","unavailable")}
                        for key,item in DIRECT_STATIONS.items()]}


def group_transport_state(states):
    group = states.get("media_player.wohnung", {}) if "media_player.wohnung" in enabled_device_ids() else {}
    state = group.get("state", "unavailable")
    features = (group.get("attributes") or {}).get("supported_features", 0)
    features = features if type(features) is int else 0
    return {"state": state,
            "can_play": state == "paused" and bool(features & MEDIA_FEATURE_PLAY),
            "can_pause": state == "playing" and bool(features & MEDIA_FEATURE_PAUSE)}


def playback_status():
    states = state_snapshot()
    candidates = sorted(enabled_device_ids(), key=lambda entity: (entity != "media_player.wohnzimmer", entity))
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
            "transport": group_transport_state(states),
            "details": active if active and (active["title"] or active["artist"]) else details}


def play_station(key, generation, *, check_availability=True):
    if not isinstance(key, str) or key not in DIRECT_STATIONS:
        raise ValueError("Unbekannter Sender")
    preset = DIRECT_STATIONS[key]
    states = state_snapshot()
    target = preset["target"]
    if target not in enabled_device_ids():
        raise ValueError(f"Alexa-Senderziel {target} ist in der Add-on-Konfiguration deaktiviert")
    state = states.get(target, {}).get("state")
    if check_availability and state in (None, "unknown", "unavailable"):
        raise ValueError(f"Alexa-Senderziel {target}: {state or 'Entity fehlt'} – nicht verfügbar")
    result = startup_request(generation, "/services/media_player/play_media", {
        "entity_id": target, "media": {
            "media_content_id": preset["media_content_id"],
            "media_content_type": preset["media_content_type"], "metadata": {}}})
    with STATE_LOCK:
        check_generation(generation)
        MONITOR.select(key)
        save_selected_station(key)
    return result


def power_command(on):
    if not isinstance(on, bool):
        raise ValueError("Ungültiger Schaltzustand")
    with POWER_LOCK:
        if on:
            if LAST_POWER == "on" and not STANDBY.is_set():
                return
            with STATE_LOCK:
                STANDBY.clear()
                generation = transition_power(True)
            try:
                with COMMAND_LOCK:
                    ha_request("/services/switch/turn_on", {"entity_id": RADIO_SWITCH})
                threading.Thread(target=radio_start_sequence, args=(generation,), daemon=True).start()
            except NETWORK_ERRORS:
                generation = transition_power(False)
                enter_standby(generation)
                raise
        else:
            if STANDBY.is_set():
                return
            if LAST_POWER != "off":
                transition_power(False)
            # Power-off must not queue behind slow discovery/media commands.
            try:
                ha_request("/services/switch/turn_off", {"entity_id": RADIO_SWITCH})
            finally:
                if not STANDBY.is_set():
                    set_radio_ready(False)


def perform(action, body):
    if action == "radio_power":
        return power_command(body.get("on"))
    generation = RESTORE_GENERATION
    with COMMAND_LOCK:
        check_generation(generation)
        if not READY or PREPARING:
            raise ValueError("Radio ist noch nicht bereit")
        if action == "radio_direct":
            return play_station(body.get("station"), generation)
        return perform_control(action, body, generation)


def perform_control(action, body, generation):
    if action == "group_transport":
        command = body.get("command")
        if command not in ("play", "pause"):
            raise ValueError("Ungültiger Gruppenbefehl")
        if "media_player.wohnung" not in allowed_entities():
            raise ValueError("Gruppe Wohnung ist nicht freigegeben")
        transport = group_transport_state(state_snapshot())
        if not transport["can_" + command]:
            raise ValueError("Gruppenwiedergabe kann derzeit nicht " + ("fortgesetzt" if command == "play" else "pausiert") + " werden")
        return startup_request(generation, "/services/media_player/media_" + command,
                               {"entity_id": "media_player.wohnung"})
    if action == "room_audio":
        entity, on = body.get("entity_id"), body.get("on")
        if type(on) is not bool:
            raise ValueError("Ungültiger Raum-Schaltzustand")
        player = next((p for p in classify_devices()["players"] if p["entity_id"] == entity), None)
        if player is None or player["state"] in ("unknown", "unavailable"):
            raise ValueError("Raumgerät ist nicht freigegeben oder nicht verfügbar")
        level = displayed_speaker_levels().get(entity, player.get("volume"))
        if type(level) not in (int, float) or not 0 <= level <= 1:
            raise ValueError("Raumlautstärke ist nicht verfügbar")
        if on == (level > 0):
            return {"ok": True, "volume": level}
        restore = remembered().get(entity)
        if type(restore) not in (int, float) or not 0 < restore <= 1:
            restore = speaker_levels().get("media_player.wohnung", 0.3)
        if type(restore) not in (int, float) or not 0 < restore <= 1:
            restore = 0.3
        perform_control("volume", {"entity_id": entity, "volume": restore if on else 0}, generation)
        if not on:
            save_remembered(entity, level)
        return {"ok": True, "volume": restore if on else 0}
    if action == "volume":
        entity = body.get("entity_id", "")
        level = body.get("volume")
        if not isinstance(entity, str) or not ENTITY_RE.fullmatch(entity) or type(level) not in (int, float) or not 0 <= level <= 1:
            raise ValueError("Ungültige Lautstärke oder Entity")
        classified = classify_devices()
        permitted = classified["players"] + classified["groups"]
        if entity not in {p["entity_id"] for p in permitted}:
            raise ValueError("Media Player nicht freigegeben")
        found = next(x for x in permitted if x["entity_id"] == entity)
        if found["state"] in ("unknown", "unavailable"):
            raise ValueError("Media Player nicht verfügbar")
        if entity == "media_player.wohnung":
            # Independent virtual master: apply its absolute percentage to unmuted rooms.
            room_players = classified["players"]
            saved = speaker_levels()
            active = [p for p in room_players
                      if p["state"] not in ("unknown", "unavailable")
                      and saved.get(p["entity_id"], p.get("volume") or 0) > 0]
            changed = {}
            failed = []
            for p in active:
                try:
                    startup_request(generation, "/services/media_player/volume_set", {
                        "entity_id": p["entity_id"], "volume_level": level})
                    changed[p["entity_id"]] = float(level)
                    with STATE_LOCK:
                        check_generation(generation)
                        ROOM_TARGETS[p["entity_id"]] = float(level)
                except StartupCancelled:
                    raise
                except NETWORK_ERRORS as exc:
                    failed.append(f"{p['entity_id']}: {exc}")
            # At 0%, preserve each room's active/muted intent so raising master
            # can restore active rooms without reactivating explicitly muted ones.
            with STATE_LOCK:
                check_generation(generation)
                save_speaker_levels({"media_player.wohnung": float(level),
                                     **(changed if level > 0 else {})})
                if level > 0:
                    save_remembered(entity, level)
            if failed:
                print("[HA Music] Master partial failure: " + "; ".join(failed), flush=True)
                raise RuntimeError("Master: " + "; ".join(failed))
            return {"ok": True, "updated": len(changed)}
        result = startup_request(generation, "/services/media_player/volume_set", {"entity_id": entity, "volume_level": level})
        with STATE_LOCK:
            check_generation(generation)
            save_speaker_levels({entity: float(level)})
            ROOM_TARGETS[entity] = float(level)
        print(f"[HA Music] Saved speaker {entity}: {round(level * 100)}%", flush=True)
        if level > 0: save_remembered(entity, level)
        return result
    raise ValueError("Unbekannte Aktion")

class Handler(BaseHTTPRequestHandler):
    def read_request_body(self):
        """Accept bounded JSON bodies, including Supervisor's streamed POSTs."""
        deadline = time.monotonic() + 20

        def read(size, *, line=False):
            result = bytearray()
            while len(result) < size:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Request body timeout")
                connection = getattr(self, "connection", None)
                if connection is not None:
                    connection.settimeout(remaining)
                # read1 performs at most one socket read, so trickling data
                # cannot reset the deadline inside a buffered read(size).
                part = self.rfile.read1(1 if line else size - len(result))
                if not part:
                    break
                result.extend(part)
                if line and part == b"\n":
                    break
            return bytes(result)

        lengths = self.headers.get_all("Content-Length", [])
        encodings = self.headers.get_all("Transfer-Encoding", [])
        if encodings:
            if lengths or len(encodings) != 1 or encodings[0].strip().lower() != "chunked":
                raise ValueError("Invalid request framing")
            body = bytearray()
            framing_size = 0

            def read_line():
                nonlocal framing_size
                line = read(257, line=True)
                framing_size += len(line)
                if len(line) > 256 or not line.endswith(b"\r\n") or framing_size > 16384:
                    raise ValueError("Invalid chunk framing")
                return line

            while True:
                line = read_line()
                if not re.fullmatch(rb"[0-9a-fA-F]{1,8}(?:;[^\r\n]*)?\r\n", line):
                    raise ValueError("Invalid chunk size")
                size = int(line.split(b";", 1)[0].strip(), 16)
                if size == 0:
                    # Consume trailers; they never replace the checked headers.
                    while (trailer := read_line()) != b"\r\n":
                        if not re.fullmatch(rb"[!#$%&'*+.^_`|~0-9a-zA-Z-]+:[^\r\n]*\r\n", trailer):
                            raise ValueError("Invalid chunk trailer")
                    if not body:
                        raise ValueError("Invalid request length")
                    return bytes(body)
                if len(body) + size > 2048:
                    raise ValueError("Invalid request length")
                part = read(size)
                if len(part) != size or read(2) != b"\r\n":
                    raise ValueError("Truncated chunked request")
                body.extend(part)
                framing_size += 2
        if len(lengths) != 1 or not re.fullmatch(r"[0-9]{1,10}", lengths[0].strip()):
            raise ValueError("Invalid request length")
        size = int(lengths[0])
        if not 0 < size <= 2048:
            raise ValueError("Invalid request length")
        body = read(size)
        if len(body) != size:
            raise ValueError("Truncated request body")
        return body

    def ingress_allowed(self):
        # Trust the TCP peer, never a spoofable X-Forwarded-For header.
        return self.client_address[0] == "172.30.32.2"

    def setup(self):
        super().setup()
        self.connection.settimeout(20)

    def reply(self, status, payload):
        encoded = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self):
        if not self.ingress_allowed():
            return self.reply(403, {"error": "Ingress only"})
        path = unquote(urlsplit(self.path).path)
        name = path.rsplit("/", 1)[-1]
        if name == "status" and "/api/" in path:
            return self.reply(200, {"radio": "direct_playback_pending",
                                    "apple_music": "planned", "backend": "connected" if TOKEN else "unavailable"})
        if name == "events" and "/api/" in path:
            if STANDBY.is_set():
                return self.reply(503, {"error":"Standby"})
            if not SSE_SLOTS.acquire(blocking=False):
                return self.reply(503, {"error":"Too many event clients"})
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache, no-transform")
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()
                seq = -1
                while not STANDBY.is_set():
                    next_seq, station, payload = MONITOR.await_change(seq, timeout=15)
                    if STANDBY.is_set():
                        break
                    if next_seq == seq:
                        self.wfile.write(b": keepalive\n\n")
                    else:
                        message = json.dumps({"station": station, "metadata": payload}, ensure_ascii=False)
                        self.wfile.write(("data: " + message + "\n\n").encode("utf-8"))
                        seq = next_seq
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                SSE_SLOTS.release()
            return
        if name == "radio-state" and "/api/" in path:
            try:
                return self.reply(200, radio_state())
            except NETWORK_ERRORS as exc:
                return self.reply(503, {"error": str(exc)})
        if name == "playback-status" and "/api/" in path:
            if STANDBY.is_set():
                return self.reply(503, {"error":"Standby"})
            try:
                return self.reply(200, playback_status())
            except NETWORK_ERRORS as exc:
                return self.reply(503, {"error": str(exc)})
        if name == "players" and "/api/" in path:
            if STANDBY.is_set():
                return self.reply(503, {"error":"Standby"})
            try:
                inventory = integration_inventory()
                classified = classify_devices(inventory)
                return self.reply(200, {"players": classified["players"], "groups": classified["groups"], "excluded": classified["excluded"],
                    "remembered": remembered(), "saved_levels": displayed_speaker_levels(), "discovery": "integration_registry",
                    "diagnostics": {domain: {"entities": len(values),
                        "media_players": sum(v.startswith("media_player.") for v in values)}
                        for domain, values in inventory.items()}})
            except NETWORK_ERRORS as exc:
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
        if not self.ingress_allowed():
            return self.reply(403, {"error": "Ingress only"})
        path = unquote(urlsplit(self.path).path)
        action = path.rsplit("/", 1)[-1]
        if "/api/" not in path or action not in ("volume", "room_audio", "group_transport", "radio_direct", "radio_power", "selected_view", "dashboard_card_installed"):
            return self.reply(404, {"error": "Not found"})
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            return self.reply(403, {"error": "Cross-site request rejected"})
        if self.headers.get_content_type() != "application/json":
            return self.reply(415, {"error": "JSON required"})
        try:
            body = json.loads(self.read_request_body())
            if not isinstance(body, dict):
                raise ValueError("Invalid body")
            if action == "dashboard_card_installed":
                mark_dashboard_card_installed()
            elif action == "selected_view":
                save_selected_view(body.get("view"))
            else:
                result = perform(action, body)
                if action == "room_audio":
                    return self.reply(200, {"ok": True, "volume": result["volume"]})
            return self.reply(200, {"ok": True})
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            print(f"[HA Music] Request {action} rejected: {exc}", flush=True)
            return self.reply(400, {"error": str(exc)})
        except (RuntimeError, OSError, HTTPException) as exc:
            print(f"[HA Music] Request {action} failed: {exc}", flush=True)
            return self.reply(502, {"error": str(exc)})

if __name__ == "__main__":
    try:
        synchronize_device_configuration(None, migrate_only=True, bootstrap=True)
    except NETWORK_ERRORS as exc:
        print(f"[HA Music] Device status migration pending: {exc}", flush=True)
    threading.Thread(target=radio_switch_monitor, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
