"""Ingress app and restricted Home Assistant Alexa control API."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit, urlencode
from urllib.request import Request, urlopen
from http.client import HTTPException
import json
import base64
import csv
import io
import plistlib
from xml.parsers.expat import ExpatError
import hashlib
import hmac
from copy import deepcopy
import os
import re
import threading
import time
import unicodedata
from datetime import datetime
from metadata_feed import MONITOR
from metadata import close_stream

WEB = (Path(__file__).parent / "web").resolve()
PORT = int(os.environ.get("PORT", "8099"))
OPTIONS = Path(os.environ.get("OPTIONS_FILE", "/data/options.json"))
HA_API = os.environ.get("HA_API", "http://supervisor/core/api").rstrip("/")
SUPERVISOR_API = os.environ.get("SUPERVISOR_API", "http://supervisor").rstrip("/")
TOKEN = os.environ.get("SUPERVISOR_TOKEN", "") or os.environ.get("HASSIO_TOKEN", "")
ENTITY_RE = re.compile(r"^media_player\.[a-z0-9_]+$")
DEFAULT_PLAYBACK_TARGET = "media_player.wohnzimmer"
DEFAULT_PLAYBACK_GROUP = "Wohnung"

VOLUME_FILE = Path(os.environ.get("VOLUME_FILE", "/data/volumes.json"))
STATION_FILE = Path(os.environ.get("STATION_FILE", "/data/last_station.json"))
SPEAKER_FILE = Path(os.environ.get("SPEAKER_FILE", "/data/speaker_levels.json"))
VIEW_FILE = Path(os.environ.get("VIEW_FILE", "/data/selected_view.json"))
SESSION_FILE = Path(os.environ.get("SESSION_FILE", "/data/session.json"))
SOURCE_FILE = Path(os.environ.get("SOURCE_FILE", "/data/last_source.json"))
LIBRARY_FILE = Path(os.environ.get("LIBRARY_FILE", "/data/apple_music_library.json"))
LIBRARY_LOCK = threading.RLock()
ARTWORK_FILE = Path(os.environ.get("ARTWORK_FILE", "/data/album_artwork.json"))
ARTWORK_DIR = Path(os.environ.get("ARTWORK_DIR", "/data/album_covers"))
ARTWORK_LOCK = threading.Lock()
ARTWORK_LAST_REQUEST = 0.0
CARD_INSTALLED_FILE = Path("/data/dashboard_card_installed")
STARTED_AT = None
RESTORE_GENERATION = 0
RADIO_MONITOR_STOP = threading.Event()
STANDBY = threading.Event()
STANDBY.set()  # Bootstrap recovery has separate, read-only admission.
READY = False
PREPARING = False
ROOM_TARGETS = {}
ROOM_REMEMBERED = {}
STARTUP_ERROR = None
STATE_LOCK = threading.RLock()
COMMAND_LOCK = threading.RLock()
POWER_LOCK = threading.Lock()
CANCEL = threading.Event()
NETWORK_ERRORS = (RuntimeError, OSError, ValueError, HTTPException)
STANDBY_UNTIL = 0.0
LAST_POWER = "off"
ACTIVE_APPLE = None
APPLE_VERIFICATION_STARTED = None
RECOVERING = False
RECOVERED_SESSION = False
SOURCE_UNCONFIRMED = False
SOURCE_RESTORE_ERROR = None
RECOVERY_MESSAGE = None
VOLUME_CONFIRMATION = {}
SINGLE_TRACK = None
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
MEDIA_FEATURE_PREVIOUS = 16
MEDIA_FEATURE_NEXT = 32
MEDIA_FEATURE_SHUFFLE = 32768
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
    # Standard routing is built in; retain explicit nonstandard installations.
    for key, default in (("apple_music_target", DEFAULT_PLAYBACK_TARGET),
                         ("apple_music_group", DEFAULT_PLAYBACK_GROUP)):
        if merged.get(key) == default:
            merged.pop(key)
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
        migrate_library(current)
        merged = merge_discovered_devices(current, found)
        merged.pop("apple_music_favorites", None)
        if merged != current:
            # Read again immediately before the write; preserve intervening edits.
            latest = request("/addons/self/info").get("options")
            if not isinstance(latest, dict):
                raise ValueError("Supervisor-Gerätekonfiguration fehlt")
            migrate_library(latest)
            merged = merge_discovered_devices(latest, found)
            merged.pop("apple_music_favorites", None)
            check_active()
            if merged != latest:
                request("/addons/self/options", {"options": merged})
        check_active()
        with CONFIG_LOCK:
            SUPERVISOR_OPTIONS = deepcopy(merged)
        if migrate_only:
            print("[HA Music] Configuration migration completed", flush=True)
        print(f"[HA Music] Device configuration: {len(merged.get('devices', []))} entries", flush=True)
def normalize_playlist_tracks(tracks):
    if not isinstance(tracks, list) or len(tracks) > 1000:
        raise ValueError("Bitte höchstens 1000 Titel pro Playlist importieren")
    result = []
    for number, track in enumerate(tracks, 1):
        if not isinstance(track, dict) or not all(isinstance(track.get(key), str) and 0 < len(track[key].strip()) <= 200
                and not any(ord(c) < 32 for c in track[key]) for key in ("name", "artist")):
            raise ValueError(f"Titel {number}: Titel und Interpret müssen 1 bis 200 Zeichen enthalten")
        name, artist = track["name"].strip(), track["artist"].strip()
        extra = {}
        for key in ("album", "album_artist"):
            value = track.get(key, "")
            if not isinstance(value, str) or len(value) > 200 or any(ord(c) < 32 for c in value):
                raise ValueError(f"Titel {number}: Ungültige Albumangabe")
            if value.strip():
                extra[key] = value.strip()
        if "cover" in track:
            if not isinstance(track["cover"], str) or not re.fullmatch(r"[a-f0-9]{64}", track["cover"]):
                raise ValueError(f"Titel {number}: Ungültige Coverzuordnung")
            extra["cover"] = track["cover"]
        if "local_covers" in track:
            if type(track["local_covers"]) is not bool:
                raise ValueError(f"Titel {number}: Ungültige Coverquelle")
            extra["local_covers"] = track["local_covers"]
        if track.get("duration") is not None:
            duration = track["duration"]
            if type(duration) not in (int, float) or not 0 < duration <= 86400:
                raise ValueError(f"Titel {number}: Ungültige Titellänge")
            extra["duration"] = float(duration)
        identity_fields = [number, name, artist] + ([extra] if extra else [])
        identity = int(hashlib.sha256(json.dumps(identity_fields, sort_keys=True).encode()).hexdigest()[:12], 16) + 1
        result.append({"id":identity, "number":number, "name":name, "artist":artist, **extra})
    return result


def import_playlist(body):
    """Parse an individual Apple Music export locally; never contact Apple or HA."""
    content = body.get("content")
    if not isinstance(content, str) or len(content.encode("utf-8")) > 1048576:
        raise ValueError("Bitte eine Playlistdatei bis 1 MB importieren")
    content = content.lstrip("\ufeff")
    if not content.strip():
        raise ValueError("Die Playlistdatei ist leer")
    if content.lstrip().startswith("<"):
        # plistlib permits Apple's standard DOCTYPE but no entity definitions.
        if "<!ENTITY" in content.upper():
            raise ValueError("Ungültiger XML-Export")
        try:
            content = re.sub(r'(<\?xml[^>]*encoding=[\"\'])[^\"\']+', r'\1UTF-8', content, count=1)
            data = plistlib.loads(content.encode("utf-8"))
        except (ValueError, TypeError, plistlib.InvalidFileException, ExpatError):
            # Expat/InvalidFile errors must not echo imported contents.
            raise ValueError("Bitte einen gültigen Apple-Music-Playlist-XML-Export verwenden") from None
        if not isinstance(data, dict) or not isinstance(data.get("Tracks"), dict) or not isinstance(data.get("Playlists"), list) or len(data["Playlists"]) != 1:
            raise ValueError("Bitte eine einzelne Playlist exportieren, nicht die gesamte Mediathek")
        playlist = data["Playlists"][0]
        entries = playlist.get("Playlist Items", []) if isinstance(playlist, dict) else None
        if not isinstance(entries, list) or len(entries) > 1000:
            raise ValueError("Bitte höchstens 1000 Titel pro Playlist importieren")
        tracks = []
        for entry in entries:
            track = data["Tracks"].get(str(entry.get("Track ID"))) if isinstance(entry, dict) else None
            if not isinstance(track, dict):
                raise ValueError("Der XML-Export enthält eine unvollständige Titelliste")
            tracks.append({"name":track.get("Name"), "artist":track.get("Artist"),
                           "album":track.get("Album", ""), "album_artist":track.get("Album Artist", ""),
                           **({"duration":track["Total Time"]/1000} if type(track.get("Total Time")) is int and track["Total Time"] > 0 else {})})
    else:
        try:
            sample = content[:8192]
            delimiter = "\t" if "\t" in sample.splitlines()[0] else csv.Sniffer().sniff(sample, delimiters=",;").delimiter
            rows = csv.reader(io.StringIO(content), delimiter=delimiter)
            headers = [x.strip().casefold() for x in next(rows)]
            title = next(i for i, x in enumerate(headers) if x in ("name", "titel", "title", "track name"))
            artist = next(i for i, x in enumerate(headers) if x in ("artist", "interpret", "künstler", "artist name"))
            album = next((i for i,x in enumerate(headers) if x in ("album", "album name", "albumtitel")), None)
            album_artist = next((i for i,x in enumerate(headers) if x in ("album artist", "albuminterpret", "album artist name")), None)
            tracks = []
            for row in rows:
                if not row or not any(x.strip() for x in row):
                    continue
                if max(title, artist) >= len(row):
                    raise ValueError("Unvollständige Titelzeile im Export")
                tracks.append({"name":row[title], "artist":row[artist],
                               **({"album":row[album]} if album is not None and album < len(row) else {}),
                               **({"album_artist":row[album_artist]} if album_artist is not None and album_artist < len(row) else {})})
                if len(tracks) > 1000:
                    raise ValueError("Bitte höchstens 1000 Titel pro Playlist importieren")
        except (csv.Error, StopIteration):
            raise ValueError("Text-/CSV-Export benötigt Spalten für Titel (Name) und Interpret (Artist)") from None
    return {"tracks":normalize_playlist_tracks(tracks)}


def normalize_library(entries, *, legacy=False):
    if not isinstance(entries, list) or (not legacy and len(entries) > 50):
        raise ValueError("Bitte höchstens 50 Playlists und Alben eintragen")
    result = []
    for entry in entries:
        if not isinstance(entry, dict):
            if legacy:
                continue
            raise ValueError("Ungültiger Eintrag")
        name, kind = entry.get("name"), entry.get("kind")
        search = entry.get("search") or name
        valid = kind in ("Playlist", "Album") and all(
            isinstance(value, str) and value.strip() and len(value) <= 200
            and not any(ord(c) < 32 for c in value) for value in (name, search))
        if not valid:
            if legacy:
                continue
            raise ValueError("Name und Alexa-Name müssen 1 bis 200 Zeichen enthalten; Art: Playlist oder Album")
        item = {"name": name.strip(), "kind": kind, "search": search.strip()}
        if "command" in entry:
            command = entry["command"]
            if not isinstance(command, str) or not command.strip() or len(command) > 500 or any(ord(c) < 32 for c in command):
                raise ValueError("Alexa-Text muss 1 bis 500 Zeichen ohne Zeilenumbrüche enthalten")
            item["command"] = command
        if "album_id" in entry:
            if kind != "Album" or type(entry["album_id"]) is not int or not 0 < entry["album_id"] < 10**16:
                raise ValueError("Ungültige Albumzuordnung")
            item["album_id"] = entry["album_id"]
        if "tracks" in entry:
            if kind != "Playlist":
                raise ValueError("Importierte Titel sind nur für Playlists vorgesehen")
            item["tracks"] = normalize_playlist_tracks(entry["tracks"])
        existing = next((saved for saved in result if all(saved[key] == item[key] for key in ("name", "kind", "search"))), None)
        if existing is not None:
            if existing.get("command") != item.get("command"):
                raise ValueError("Doppelte Einträge mit verschiedenen Alexa-Texten: unterschiedliche Anzeigenamen verwenden")
            if existing.get("album_id") != item.get("album_id"):
                raise ValueError("Doppelter Albumeintrag: für verschiedene Versionen unterschiedliche Namen verwenden")
            if existing.get("tracks") != item.get("tracks"):
                raise ValueError("Doppelte Playlist mit unterschiedlicher Titelliste")
        else:
            result.append(item)
    return result


def library_snapshot(config=None):
    """Local data only, including in standby; a corrupt file is never overwritten."""
    with LIBRARY_LOCK:
        try:
            data = json.loads(LIBRARY_FILE.read_text(encoding="utf-8"))
        except FileNotFoundError:
            config = options() if config is None else config
            data = normalize_library(config.get("apple_music_favorites", []), legacy=True)
        else:
            # Existing migrated libraries may contain more than the editor limit.
            if not isinstance(data, list):
                raise ValueError("Gespeicherte Musikbibliothek ist beschädigt")
            normalized = normalize_library(data, legacy=True)
            if len(normalized) != len(data):
                raise ValueError("Gespeicherte Musikbibliothek ist beschädigt")
            data = normalized
        revision = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
        return {"items": deepcopy(data), "revision": revision}


def migrate_library(config):
    with LIBRARY_LOCK:
        if LIBRARY_FILE.exists():
            library_snapshot(config)  # Keep legacy options intact if local storage is damaged.
            return
        if not LIBRARY_FILE.exists() and "apple_music_favorites" in config:
            data = library_snapshot(config)["items"]
            with LOCK:
                write_durable_json(LIBRARY_FILE, data)


def save_library(body):
    entries = normalize_library(body.get("items"))
    with LIBRARY_LOCK:
        current = library_snapshot()
        if body.get("revision") != current["revision"]:
            raise ValueError("Die Liste wurde inzwischen geändert. Bitte Fenster schließen und erneut öffnen.")
        with LOCK:
            write_durable_json(LIBRARY_FILE, entries)
        return library_snapshot()


def library_item_id(item):
    identity = {key:item[key] for key in ("name", "kind", "search")}
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]


def reorder_library(body):
    """Reorder existing favorites only, retaining all metadata and other kinds."""
    kind, order = body.get("kind"), body.get("order")
    if kind not in ("Playlist", "Album") or not isinstance(order, list) or any(not isinstance(value, str) for value in order):
        raise ValueError("Ungültige Sortierung")
    with LIBRARY_LOCK:
        current = library_snapshot()
        if body.get("revision") != current["revision"]:
            raise ValueError("Die Liste wurde inzwischen geändert. Bitte erneut sortieren.")
        favorites = {library_item_id(item):item for item in current["items"] if item["kind"] == kind}
        if len(order) != len(favorites) or len(set(order)) != len(order) or set(order) != set(favorites):
            raise ValueError("Die Sortierung muss alle vorhandenen Einträge genau einmal enthalten")
        ordered = iter(favorites[value] for value in order)
        entries = [next(ordered) if item["kind"] == kind else item for item in current["items"]]
        with LOCK:
            write_durable_json(LIBRARY_FILE, entries)
        return library_snapshot()


def sync_playlist(body):
    """Update playlist tracks; optionally add a playlist, never issue commands."""
    name = body.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > 200:
        raise ValueError("Ungültiger Playlistname")
    tracks = normalize_playlist_tracks(body.get("tracks"))
    for identity in {track["cover"] for track in tracks if track.get("cover")}:
        if identity:
            try:
                local_playlist_image(identity)  # Require uploaded bytes before committing references.
            except (OSError, ValueError):
                raise ValueError("Playlistcover bitte zuerst übertragen") from None
    if "create" in body and type(body["create"]) is not bool:
        raise ValueError("Ungültige Auswahl für neue Playlists")
    with LIBRARY_LOCK:
        items = library_snapshot()["items"]
        matches = [item for item in items if item["kind"] == "Playlist" and item["name"] == name]
        created = False
        if not matches and body.get("create") is True:
            if len(items) >= 50:
                raise ValueError("Bitte höchstens 50 Playlists und Alben eintragen")
            new_item = normalize_library([{"name": name, "search": name, "kind": "Playlist",
                                           "command": "spiel playlist " + name, "tracks": tracks}])[0]
            items.append(new_item)
            matches = [new_item]
            created = True
        if len(matches) != 1:
            raise ValueError("Playlist muss in HA Music mit eindeutigem Namen angelegt sein")
        changed = created or matches[0].get("tracks") != tracks
        if changed:
            matches[0]["tracks"] = tracks
            with LOCK:
                write_durable_json(LIBRARY_FILE, items)
        return {"ok": True, "changed": changed, "created": created, "tracks": len(tracks)}


def artwork_url(value, host):
    if not isinstance(value, str) or len(value) > 2048:
        return ""
    try:
        parsed = urlsplit(value)
        return value if (parsed.scheme == "https" and not parsed.username and not parsed.password
                         and parsed.port in (None, 443) and parsed.hostname
                         and (parsed.hostname == host or parsed.hostname.endswith("." + host))) else ""
    except ValueError:
        return ""


def album_candidates(data):
    """Only real album records and Apple-hosted artwork; never consume playback metadata."""
    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list):
        raise ValueError("Ungültige Antwort der Albumsuche")
    albums = {}
    for result in results[:25]:
        if not isinstance(result, dict) or result.get("collectionType") != "Album":
            continue
        identity, title, artist = result.get("collectionId"), result.get("collectionName"), result.get("artistName")
        if type(identity) is not int or not 0 < identity < 10**16 or not all(isinstance(v, str) and 0 < len(v) <= 300 for v in (title, artist)):
            continue
        image = artwork_url(result.get("artworkUrl100"), "mzstatic.com")
        store = artwork_url(result.get("collectionViewUrl"), "music.apple.com") or artwork_url(result.get("collectionViewUrl"), "itunes.apple.com")
        if image and store:
            albums[identity] = {"album_id":identity, "name":title, "artist":artist,
                                "image":re.sub(r"/100x100bb\.(jpg|png)$", r"/600x600bb.\1", image),
                                "fallback_image":image, "store_url":store}
    return list(albums.values())


def artwork_cache():
    try:
        value = json.loads(ARTWORK_FILE.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def artwork_search_index():
    try:
        value = json.loads((ARTWORK_DIR / "search-index.json").read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def remember_artwork_search(key, identity):
    """Called under ARTWORK_LOCK; successful mappings outlive the query cache."""
    index = artwork_search_index()
    if index.get(key) == identity:
        return
    index[key] = identity
    with LOCK:
        ARTWORK_DIR.mkdir(parents=True, exist_ok=True)
        write_durable_json(ARTWORK_DIR / "search-index.json", index)


def image_mime(data):
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    raise ValueError("Coverantwort ist kein unterstütztes Bild")


def local_playlist_image(identity):
    if not isinstance(identity, str) or not re.fullmatch(r"[a-f0-9]{64}", identity):
        raise ValueError("Ungültige Coverzuordnung")
    data = (ARTWORK_DIR / "playlists" / f"{identity}.image").read_bytes()
    if not 0 < len(data) <= 128 * 1024 or hashlib.sha256(data).hexdigest() != identity:
        raise ValueError("Ungültiges Playlistcover")
    return data, image_mime(data)


def sync_playlist_artwork(body):
    """Authenticated local upload; content addressed, deduplicated, no network or HA."""
    identity, encoded = body.get("cover"), body.get("data")
    if not isinstance(identity, str) or not re.fullmatch(r"[a-f0-9]{64}", identity) or not isinstance(encoded, str) or len(encoded) > 175000:
        raise ValueError("Ungültiges Playlistcover")
    try:
        data = base64.b64decode(encoded, validate=True)
    except ValueError:
        raise ValueError("Ungültiges Playlistcover") from None
    if not 0 < len(data) <= 128 * 1024 or hashlib.sha256(data).hexdigest() != identity:
        raise ValueError("Ungültiges Playlistcover")
    image_mime(data)
    with ARTWORK_LOCK:
        folder = ARTWORK_DIR / "playlists"
        folder.mkdir(parents=True, exist_ok=True)
        destination = folder / f"{identity}.image"
        changed = not destination.exists() or destination.read_bytes() != data
        if changed:
            temporary = destination.with_suffix(".tmp")
            try:
                with temporary.open("wb") as stream:
                    stream.write(data); stream.flush(); os.fsync(stream.fileno())
                temporary.replace(destination)
            finally:
                temporary.unlink(missing_ok=True)
    return {"ok":True, "changed":changed, "tracks":0}


def local_album_image(identity):
    if type(identity) is not int or not 0 < identity < 10**16:
        raise ValueError("Ungültige Albumzuordnung")
    data = (ARTWORK_DIR / f"{identity}.image").read_bytes()
    if not 0 < len(data) <= 3 * 1024 * 1024:
        raise ValueError("Ungültige Covergröße")
    return data, image_mime(data)


def stored_album(identity):
    if type(identity) is not int or not 0 < identity < 10**16:
        return None
    try:
        album = json.loads((ARTWORK_DIR / f"{identity}.json").read_text(encoding="utf-8"))
        if not isinstance(album, dict) or album.get("album_id") != identity:
            return None
        image = ARTWORK_DIR / f"{identity}.image"
        if not 0 < image.stat().st_size <= 3 * 1024 * 1024:
            return None
        with image.open("rb") as stream:
            image_mime(stream.read(12))
        return {**album, "image":f"api/album-art/{identity}", "fallback_image":""}
    except (OSError, ValueError):
        return None


def store_album_image(album):
    """Called under ARTWORK_LOCK. Persist confirmed pictures, not just remote URLs."""
    identity = album["album_id"]
    existing = stored_album(identity)
    if existing:
        return existing
    if STANDBY.is_set() or not READY or PREPARING:
        raise RuntimeError("Coverdownload erst verfügbar, wenn HA Music bereit ist")
    generation = RESTORE_GENERATION
    last_error = None
    for value in dict.fromkeys((album.get("image"), album.get("fallback_image"))):
        url = artwork_url(value, "mzstatic.com")
        if not url:
            continue
        try:
            with urlopen(Request(url, headers={"Accept":"image/jpeg,image/png,image/webp"}), timeout=8) as response:
                with RESPONSE_LOCK:
                    check_generation(generation)
                    ACTIVE_RESPONSES.add(response)
                try:
                    data = response.read(3 * 1024 * 1024 + 1)
                finally:
                    with RESPONSE_LOCK:
                        ACTIVE_RESPONSES.discard(response)
            check_generation(generation)
            if len(data) > 3 * 1024 * 1024:
                raise ValueError("Coverbild ist zu groß")
            image_mime(data)
            with LOCK:
                ARTWORK_DIR.mkdir(parents=True, exist_ok=True)
                destination = ARTWORK_DIR / f"{identity}.image"
                temporary = destination.with_suffix(".tmp")
                try:
                    with temporary.open("wb") as stream:
                        stream.write(data); stream.flush(); os.fsync(stream.fileno())
                    check_generation(generation)
                    temporary.replace(destination)
                    write_durable_json(ARTWORK_DIR / f"{identity}.json", album)
                finally:
                    temporary.unlink(missing_ok=True)
            return stored_album(identity)
        except StartupCancelled:
            raise
        except NETWORK_ERRORS as exc:
            last_error = exc
    raise RuntimeError("Cover konnte nicht lokal gespeichert werden: " + str(last_error or "Bildadresse fehlt"))


def album_cover_search(body, *, max_search_length=200):
    """Independent catalog lookup; no Alexa/HA call, never leave network standby."""
    global ARTWORK_LAST_REQUEST
    search, identity = body.get("search"), body.get("album_id")
    if not isinstance(search, str) or not search.strip() or len(search) > max_search_length or any(ord(c) < 32 for c in search):
        raise ValueError("Bitte Albumtitel und möglichst den Interpreten eingeben")
    if identity is not None and (type(identity) is not int or not 0 < identity < 10**16):
        raise ValueError("Ungültige Albumzuordnung")
    key = "id:" + str(identity) if identity else "search:" + search.strip().casefold()
    with ARTWORK_LOCK:
        cache = artwork_cache()
        entry = cache.get(key, {})
        saved = stored_album(identity) if identity else stored_album(artwork_search_index().get(key))
        if saved:
            albums = [saved]
        elif isinstance(entry, dict) and type(entry.get("at")) in (int, float) and time.time() - entry["at"] < 86400 and isinstance(entry.get("items"), list):
            albums = entry["items"]
        else:
            if STANDBY.is_set() or not READY or PREPARING:
                raise RuntimeError("Albumsuche erst verfügbar, wenn HA Music bereit ist")
            if time.monotonic() - ARTWORK_LAST_REQUEST < 4:
                raise RuntimeError("Albumsuche kurz ausgelastet. Bitte erneut versuchen.")
            ARTWORK_LAST_REQUEST = time.monotonic()
            generation = RESTORE_GENERATION
            params = {"country":"DE", "entity":"album", "limit":25}
            params["id" if identity else "term"] = identity or search.strip()
            endpoint = "lookup" if identity else "search"
            request = Request("https://itunes.apple.com/" + endpoint + "?" + urlencode(params), headers={"Accept":"application/json"})
            with urlopen(request, timeout=8) as response:
                with RESPONSE_LOCK:
                    check_generation(generation)
                    ACTIVE_RESPONSES.add(response)
                try:
                    raw = response.read(262145)
                finally:
                    with RESPONSE_LOCK:
                        ACTIVE_RESPONSES.discard(response)
            check_generation(generation)
            if len(raw) > 262144:
                raise ValueError("Antwort der Albumsuche ist zu groß")
            albums = album_candidates(json.loads(raw))
            if identity:
                albums = [album for album in albums if album["album_id"] == identity]
            cache[key] = {"at":time.time(), "items":albums}
            for album in albums:
                cache["id:" + str(album["album_id"])] = {"at":time.time(), "items":[album]}
            cache = dict(sorted(cache.items(), key=lambda pair:pair[1].get("at", 0) if isinstance(pair[1], dict) else 0)[-100:])
            with LOCK:
                write_durable_json(ARTWORK_FILE, cache)
        def normalized(value):
            return " ".join(re.findall(r"\w+", unicodedata.normalize("NFKC", value).casefold()))
        term = normalized(search)
        matches = [album for album in albums if identity == album.get("album_id") or term in (
            normalized(album.get("name", "")), normalized(album.get("artist", "") + " " + album.get("name", "")),
            normalized(album.get("name", "") + " " + album.get("artist", "")))]
        selected = matches[0] if len(matches) == 1 else None
        if selected and not saved:
            selected = store_album_image(selected)
            albums = [selected]
        if selected:
            remember_artwork_search(key, selected["album_id"])
        return {"items":albums, "selected":selected}


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
        saved = {entity: float(value) for entity, value in obj.items()
                 if isinstance(entity, str) and ENTITY_RE.fullmatch(entity) and type(value) in (int, float)
                 and 0 <= value <= 1} if isinstance(obj, dict) else {}
    except (OSError, ValueError):
        saved = {}
    return {**saved, **ROOM_REMEMBERED}
def write_durable_json(path, data):
    """Call under LOCK: flush the file, replace atomically, flush the directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    if hasattr(os, "O_DIRECTORY"):
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


def save_remembered(entity, level):
    with LOCK:
        obj = remembered()
        obj[entity] = level
        write_durable_json(VOLUME_FILE, obj)
        ROOM_REMEMBERED[entity] = level
def speaker_levels():
    try:
        data = json.loads(SPEAKER_FILE.read_text())
        levels = {key: float(value) for key, value in data.items()
                if isinstance(key, str) and ENTITY_RE.fullmatch(key)
                and type(value) in (int, float) and 0 <= value <= 1}
        if data.get("_version") != 2:
            # Older room commands saved 1.0 as an active marker, not 100%.
            levels = {key:(levels.get("media_player.wohnung", 0.3) if key != "media_player.wohnung" and value == 1 else value)
                      for key,value in levels.items()}
        return levels
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


def save_speaker_levels(levels):
    with LOCK:
        state = speaker_levels()
        state.update(levels)
        write_durable_json(SPEAKER_FILE, {"_version":2, **state})


def displayed_speaker_levels():
    with STATE_LOCK:
        saved = speaker_levels()
        master = saved.get("media_player.wohnung", 0.3)
        displayed = {**dict.fromkeys(enabled_device_ids(), master), **saved}
        if master == 0:
            displayed.update(dict.fromkeys(enabled_device_ids(), 0.0))
        return {**displayed, **ROOM_TARGETS}


class StartupCancelled(RuntimeError):
    pass


def check_generation(generation):
    if generation != RESTORE_GENERATION or LAST_POWER != "on" or STANDBY.is_set():
        raise StartupCancelled("Start abgebrochen")


def startup_request(generation, path, payload=None):
    # Serialize commands; recheck after acquiring the lock and after slow I/O.
    with COMMAND_LOCK:
        check_generation(generation)
        if path in ("/services/media_player/volume_set", "/services/media_player/play_media",
                    "/services/media_player/media_play", "/services/media_player/media_pause",
                    "/services/media_player/media_previous_track", "/services/media_player/media_next_track",
                    "/services/media_player/shuffle_set", "/services/homeassistant/update_entity"):
            targets = payload.get("entity_id", [])
            targets = [targets] if isinstance(targets, str) else targets
            selected = enabled_device_ids()
            if any(entity not in selected for entity in targets):
                raise ValueError("Gerät ist in der Add-on-Konfiguration deaktiviert")
        result = ha_request(path, payload)
        check_generation(generation)
        return result


def reapply_startup_volumes_after_playback(generation, expected):
    """An optimistic HA volume value cannot complete normal startup restore.

    Reapply the same positive target once after a room reports playing for
    three observations, two seconds apart. Never start/resume media here.
    """
    expected = dict(expected)
    pending = {entity for entity, level in expected.items() if level > 0}
    stable, applied = {}, {}
    for _ in range(15):
        if not pending:
            break
        if not wait_for_start(generation, 2):
            raise StartupCancelled("Startlautstärke abgebrochen")
        try:
            states = state_snapshot(fresh=True)
            check_generation(generation)
        except StartupCancelled:
            raise
        except NETWORK_ERRORS as exc:
            stable.clear()
            print(f"[HA Music] Startup playback wait failed: {exc}", flush=True)
            continue
        for entity in sorted(pending):
            with COMMAND_LOCK:
                with STATE_LOCK:
                    check_generation(generation)
                    if entity not in enabled_device_ids() or ROOM_TARGETS.get(entity) != expected[entity]:
                        pending.discard(entity)
                        expected.pop(entity, None)
                        continue  # A later manual room/master command wins.
                    playing = states.get(entity, {}).get("state") == "playing"
                    stable[entity] = stable.get(entity, 0) + 1 if playing else 0
                if stable[entity] < 3:
                    continue
                try:
                    startup_request(generation, "/services/media_player/volume_set", {
                        "entity_id":entity, "volume_level":expected[entity]})
                except StartupCancelled:
                    raise
                except NETWORK_ERRORS as exc:
                    print(f"[HA Music] Deferred startup volume failed for {entity}: {exc}", flush=True)
                else:
                    applied[entity] = expected[entity]
                # One delayed application per room, then the bounded verifier.
                pending.discard(entity)
    if applied:
        print("[HA Music] Saved room levels reapplied after playback: " + json.dumps(applied, sort_keys=True), flush=True)
    for entity in sorted(pending):
        print(f"[HA Music] WARNING: {entity} playback did not settle; deferred startup volume skipped", flush=True)
        expected.pop(entity, None)  # Never retry positive volumes in idle/paused rooms.
    return expected


def verify_restored_volumes(generation, expected, *, after_playback=False):
    expected = dict(expected)
    if after_playback:
        try:
            expected = reapply_startup_volumes_after_playback(generation, expected)
        except StartupCancelled:
            return
    if not expected:
        return
    for attempt in range(3):
        if not wait_for_start(generation, 2):
            return
        try:
            states = state_snapshot(fresh=True)
            check_generation(generation)
        except StartupCancelled:
            return
        except NETWORK_ERRORS as exc:
            print(f"[HA Music] Volume verification retry: {exc}", flush=True)
            continue
        for entity, level in list(expected.items()):
            entry = states.get(entity, {})
            observed = (entry.get("attributes") or {}).get("volume_level")
            with COMMAND_LOCK:
                with STATE_LOCK:
                    if generation != RESTORE_GENERATION or LAST_POWER != "on" or STANDBY.is_set():
                        return
                    # A later manual change always wins over startup verification.
                    if entity not in enabled_device_ids() or ROOM_TARGETS.get(entity) != level:
                        expected.pop(entity)
                        VOLUME_CONFIRMATION.pop(entity, None)
                        continue
                    if after_playback and level > 0 and entry.get("state") != "playing":
                        expected.pop(entity)
                        VOLUME_CONFIRMATION.pop(entity, None)
                        continue  # A later pause/unavailable state must not trigger retries.
                    confirmed = entry.get("state") not in ("unknown", "unavailable") and type(observed) in (float, int) and abs(observed - level) <= 0.005
                    if confirmed:
                        expected.pop(entity)
                        VOLUME_CONFIRMATION.pop(entity, None)
                        continue
                    VOLUME_CONFIRMATION[entity] = {"expected":level, "observed":observed}
                if attempt < 2:
                    try:
                        startup_request(generation, "/services/media_player/volume_set", {"entity_id":entity, "volume_level":level})
                    except StartupCancelled:
                        return
                    except NETWORK_ERRORS as exc:
                        print(f"[HA Music] Volume restore retry failed for {entity}: {exc}", flush=True)
        if not expected:
            return
    for entity in expected:
        print(f"[HA Music] WARNING: {entity} has not confirmed its restored volume", flush=True)


def restore_speakers(generation):
    levels = speaker_levels()
    master = levels.get("media_player.wohnung")
    available = enabled_device_ids()
    sent, failed = {}, []
    # Wohnung is a virtual master. A real Alexa group-volume command can
    # finish after room commands and overwrite their independent levels.
    for entity in sorted(e for e in levels if e != "media_player.wohnung"):
        check_generation(generation)
        if entity not in available:
            continue
        # A muted virtual master must remain silent across power cycles.
        level = 0.0 if master == 0 else levels[entity]
        try:
            startup_request(generation, "/services/media_player/volume_set",
                            {"entity_id": entity, "volume_level": level})
            sent[entity] = level
            with STATE_LOCK:
                check_generation(generation)
                ROOM_TARGETS[entity] = level
                VOLUME_CONFIRMATION[entity] = {"expected":level, "observed":None}
        except StartupCancelled:
            raise
        except NETWORK_ERRORS:
            failed.append(entity)
    if sent:
        threading.Thread(target=verify_restored_volumes,
                         args=(generation, sent), kwargs={"after_playback":True}, daemon=True).start()
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


def prepare_speaker_levels(generation):
    saved = speaker_levels()
    permitted = enabled_device_ids()
    if "media_player.wohnung" not in saved:
        saved["media_player.wohnung"] = remembered().get("media_player.wohnung", 0.3)
    for entity in permitted:
        if entity not in saved:
            saved[entity] = saved["media_player.wohnung"]
    with STATE_LOCK:
        check_generation(generation)
        save_speaker_levels(saved)
    # Capture settings without queuing temporary 1% commands. Alexa may
    # acknowledge them before execution and apply them after the restore.
    # Only pre-mute rooms whose saved intent is silent, before media starts.
    muted = {}
    for entity in sorted(permitted - {"media_player.wohnung"}):
        if saved[entity] != 0 and saved["media_player.wohnung"] != 0:
            continue
        try:
            startup_request(generation, "/services/media_player/volume_set",
                            {"entity_id": entity, "volume_level": 0.0})
            muted[entity] = 0.0
        except StartupCancelled:
            raise
        except NETWORK_ERRORS as exc:
            print(f"[HA Music] Startup mute failed for {entity}: {exc}", flush=True)
    targets = {entity: 0.0 if saved["media_player.wohnung"] == 0 else saved[entity]
               for entity in permitted if entity != "media_player.wohnung"}
    print(f"[HA Music] Startup room levels prepared: {json.dumps(targets, sort_keys=True)}; master: {saved['media_player.wohnung']}; pre-muted: {json.dumps(muted, sort_keys=True)}", flush=True)
    return saved


def radio_start_sequence(generation):
    global READY, PREPARING, STARTUP_ERROR, STARTED_AT
    global SOURCE_RESTORE_ERROR
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
        print("[HA Music] Waiting 45 seconds before Alexa reload", flush=True)
        if not wait_for_start(generation, 45):
            return
        selected = enabled_device_ids()
        source = last_selected_source()
        station = source["id"] if source and source["kind"] == "radio" else ""
        if source and source["kind"] == "apple":
            preferred = apple_music_selection()["target"]
        else:
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
        print("[HA Music] Waiting 20 seconds after Alexa reload", flush=True)
        if not wait_for_start(generation, 20):
            return
        refresh_entities = sorted(enabled_device_ids())
        if refresh_entities:
            try:
                startup_request(generation, "/services/homeassistant/update_entity", {"entity_id": refresh_entities})
            except StartupCancelled:
                raise
            except NETWORK_ERRORS as exc:
                print(f"[HA Music] Device refresh failed; continuing startup: {exc}", flush=True)
        print("[HA Music] Waiting 5 seconds after device refresh", flush=True)
        if not wait_for_start(generation, 5):
            return
        source = last_selected_source()
        station = source["id"] if source and source["kind"] == "radio" else ""
        try:
            prepare_speaker_levels(generation)
            with STATE_LOCK:
                check_generation(generation)
                if source:
                    save_selected_view("apple" if source["kind"] == "apple" else "radio")
                READY = True
                STARTED_AT = None
                # Radiotext belongs to the displayed preset, independently of
                # whether Alexa accepts its later volume/playback commands.
                MONITOR.select(station)
            print("[HA Music] Interface released after startup preparation", flush=True)
            try:
                startup_request(generation, "/services/input_boolean/turn_on", {"entity_id": RADIO_READY})
            except StartupCancelled:
                raise
            except NETWORK_ERRORS as exc:
                print(f"[HA Music] Ready helper update failed: {exc}", flush=True)
            if source:
                try:
                    if source["kind"] == "apple":
                        play_apple_music(source["id"], generation, startup=True)
                    elif station:
                        play_station(station, generation)
                except StartupCancelled:
                    raise
                except NETWORK_ERRORS as exc:
                    with STATE_LOCK:
                        check_generation(generation)
                        SOURCE_RESTORE_ERROR = "Gespeicherte Wiedergabe konnte nicht wiederhergestellt werden: " + str(exc)
                    print(f"[HA Music] Saved source start failed: {exc}", flush=True)
            elif SOURCE_FILE.exists():
                with STATE_LOCK:
                    check_generation(generation)
                    SOURCE_RESTORE_ERROR = "Gespeicherte Wiedergabequelle ist nicht verfügbar. Bitte Sender oder Apple-Music-Favorit auswählen."
        finally:
            # Restore individual levels even if the media request failed.
            check_generation(generation)
            restore_speakers(generation)
        try:
            synchronize_device_configuration(generation)
        except StartupCancelled:
            raise
        except NETWORK_ERRORS as exc:
            print(f"[HA Music] Device synchronization failed: {exc}", flush=True)
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


def session_intent():
    """Missing/invalid pre-upgrade state is unknown, never assumed active."""
    try:
        data = json.loads(SESSION_FILE.read_text())
        if isinstance(data, dict) and data.get("version") == 1:
            value = data.get("intent")
            return value if value in ("on", "off") else None
    except (OSError, ValueError):
        pass
    return None


def save_session_intent(on):
    with LOCK:
        write_durable_json(SESSION_FILE, {"version": 1, "intent": "on" if on else "off"})


def start_session_recovery():
    """Bootstrap only. A known local off does not admit even a HA state read.

    On the first upgrade there is no intent file: inspect HA's stored states,
    without contacting/reloading Alexa or invoking any HA service.
    """
    global RECOVERING, RECOVERY_MESSAGE
    with POWER_LOCK, STATE_LOCK:
        if session_intent() == "off" or RECOVERING or not STANDBY.is_set():
            return
        RECOVERING = True
        RECOVERY_MESSAGE = "Bestehenden Einschalt- und Wiedergabestatus prüfen …"
        generation, cancellation = RESTORE_GENERATION, CANCEL
        threading.Thread(target=recover_session, args=(generation, cancellation), daemon=True).start()


def recover_session(generation, cancellation):
    """Confirm playback or already-ready devices; never run cold startup."""
    global RECOVERING, RECOVERY_MESSAGE, RECOVERED_SESSION, LAST_POWER, READY
    global STATE_CACHE
    global SOURCE_UNCONFIRMED
    previous = set()
    previous_ready = set()
    try:
        for attempt in range(12):
            with STATE_LOCK:
                if generation != RESTORE_GENERATION or cancellation.is_set() or not RECOVERING:
                    return
            try:
                raw = ha_request("/states", recovery_read=True)
                if not isinstance(raw, list):
                    raise ValueError("Ungültige Home-Assistant-Zustände")
                states = {s["entity_id"]: s for s in raw
                          if isinstance(s, dict) and isinstance(s.get("entity_id"), str)}
                switch = states.get(RADIO_SWITCH, {}).get("state")
                active = {entity for entity in enabled_device_ids()
                          if states.get(entity, {}).get("state") in ("playing", "paused")}
                already_ready = {entity for entity in enabled_device_ids()
                                 if states.get(entity, {}).get("state") in ("on", "idle", "playing", "paused")}
                if states.get(RADIO_READY, {}).get("state") != "on":
                    already_ready = set()
                with STATE_LOCK:
                    if generation != RESTORE_GENERATION or cancellation.is_set() or not RECOVERING:
                        return
                    if switch == "off":
                        save_session_intent(False)
                        RECOVERY_MESSAGE = None
                        return
                    if switch == "on" and (active & previous or already_ready & previous_ready):
                        save_session_intent(True)
                        # No transition_power(), ready-helper write, metadata
                        # selection, volume restore or playback command here.
                        LAST_POWER = "on"
                        READY = True
                        RECOVERED_SESSION = True
                        SOURCE_UNCONFIRMED = True
                        RECOVERY_MESSAGE = None
                        MONITOR.resume()  # No station selected; opens no streams.
                        STANDBY.clear()
                        # Do not acquire CACHE_LOCK under STATE_LOCK: requests
                        # already use the opposite order for standby admission.
                        break
                previous = active if switch == "on" else set()
                previous_ready = already_ready if switch == "on" else set()
            except NETWORK_ERRORS as exc:
                previous = set()
                previous_ready = set()
                print(f"[HA Music] Read-only session recovery retry: {exc}", flush=True)
            if attempt < 11 and cancellation.wait(5):
                return
        else:
            with STATE_LOCK:
                if generation == RESTORE_GENERATION:
                    RECOVERY_MESSAGE = "Wiedergabe nicht sicher erkannt; keine Gerätebefehle ausgeführt"
            return
        with CACHE_LOCK:
            # The normal monitor will obtain fresh states after this snapshot.
            STATE_CACHE = (0.0, None)
        print("[HA Music] Existing powered session reattached without device commands", flush=True)
    finally:
        with STATE_LOCK:
            if generation == RESTORE_GENERATION:
                RECOVERING = False


def transition_power(on):
    global RESTORE_GENERATION, STARTED_AT, LAST_POWER, STANDBY_UNTIL, READY, PREPARING, STARTUP_ERROR, CANCEL
    global ACTIVE_APPLE
    global RECOVERING, RECOVERED_SESSION, RECOVERY_MESSAGE, SOURCE_UNCONFIRMED
    global SOURCE_RESTORE_ERROR
    with STATE_LOCK:
        if not on and SOURCE_UNCONFIRMED and not SOURCE_FILE.exists():
            try:
                # A legacy radio preset is not evidence for a reattached source.
                save_selected_source("unknown", "")
            except OSError as exc:
                print(f"[HA Music] Unknown source could not be saved: {exc}", flush=True)
        try:
            save_session_intent(on)
        except OSError as exc:
            if on:
                raise  # Failed persistence must not open standby admission.
            # Disk failure must never prevent an explicit/observed power-off.
            print(f"[HA Music] Off intent could not be saved: {exc}", flush=True)
        CANCEL.set()
        CANCEL = threading.Event()
        RESTORE_GENERATION += 1
        generation = RESTORE_GENERATION
        LAST_POWER = "on" if on else "off"
        READY = False
        PREPARING = on
        STARTUP_ERROR = None
        ACTIVE_APPLE = None
        cancel_single_track()
        RECOVERING = False
        RECOVERED_SESSION = False
        source = last_selected_source()
        SOURCE_UNCONFIRMED = on and (bool(source and source["kind"] == "apple") or (source is None and SOURCE_FILE.exists()))
        SOURCE_RESTORE_ERROR = None
        RECOVERY_MESSAGE = None
        ROOM_TARGETS.clear()
        ROOM_REMEMBERED.clear()
        VOLUME_CONFIRMATION.clear()
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
                    observed_generation, seen_on = generation, RECOVERED_SESSION
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

def ha_request(path, payload=None, *, supervisor=False, startup_configuration=False, recovery_read=False):
    global STATE_CACHE, INVENTORY_CACHE
    configuration_access = startup_configuration and supervisor and path in ("/addons/self/info", "/addons/self/options")
    if startup_configuration and not configuration_access:
        raise ValueError("Initialisierung erlaubt nur die eigenen Supervisor-Optionen")
    recovery_access = recovery_read and RECOVERING and path == "/states" and payload is None and not supervisor
    if recovery_read and not recovery_access:
        raise ValueError("Wiederanbindung erlaubt nur das Lesen der HA-Zustände")
    if STANDBY.is_set() and not (configuration_access or recovery_access):
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
            if STANDBY.is_set() and not (configuration_access or (recovery_access and RECOVERING)):
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
    "1live": {"name": "1LIVE", "target": DEFAULT_PLAYBACK_TARGET, "media_content_type": "custom", "media_content_id": f"spiele eins live aus der ARD Audiothek auf {DEFAULT_PLAYBACK_GROUP}"},
    "wdr2": {"name": "WDR 2", "target": DEFAULT_PLAYBACK_TARGET, "media_content_type": "custom", "media_content_id": f"spiele wdr zwei aus der ARD Audiothek auf {DEFAULT_PLAYBACK_GROUP}"},
    "swr3": {"name": "SWR3", "target": DEFAULT_PLAYBACK_TARGET, "media_content_type": "custom", "media_content_id": f"spiele swr3 aus der ard audiothek auf {DEFAULT_PLAYBACK_GROUP}"},
    "sommerhits": {"name": "Sommerhits", "target": DEFAULT_PLAYBACK_TARGET, "media_content_type": "custom", "media_content_id": f"spiele amazon Sommerhits auf {DEFAULT_PLAYBACK_GROUP}"},
    "charts": {"name": "Charts", "target": DEFAULT_PLAYBACK_TARGET, "media_content_type": "AMAZON_MUSIC", "media_content_id": f"spiele  die charts auf {DEFAULT_PLAYBACK_GROUP}"},
    "80s": {"name": "80er", "target": DEFAULT_PLAYBACK_TARGET, "media_content_type": "AMAZON_MUSIC", "media_content_id": f"spiele best of achtziger auf {DEFAULT_PLAYBACK_GROUP}"},
    "90s": {"name": "90er", "target": DEFAULT_PLAYBACK_TARGET, "media_content_type": "AMAZON_MUSIC", "media_content_id": f"spiele hits der neunziger auf {DEFAULT_PLAYBACK_GROUP}"},
}

def last_selected_station():
    try:
        station = json.loads(STATION_FILE.read_text()).get("station", "")
        return station if isinstance(station, str) and station in DIRECT_STATIONS else ""
    except (OSError, ValueError, AttributeError):
        return ""


def apple_music_selection():
    """Local configured favorites; never accepts arbitrary browser commands."""
    config = options()
    target = config.get("apple_music_target", DEFAULT_PLAYBACK_TARGET)
    group = config.get("apple_music_group", DEFAULT_PLAYBACK_GROUP)
    target = target if isinstance(target, str) and ENTITY_RE.fullmatch(target) else ""
    group = group.strip() if isinstance(group, str) else ""
    if len(group) > 100 or any(ord(c) < 32 for c in group):
        group = ""
        target = ""
    items = []
    snapshot = library_snapshot(config)
    for favorite in snapshot["items"]:
        item = dict(favorite)
        item["id"] = library_item_id(item)
        if item["kind"] == "Album":
            saved = stored_album(item.get("album_id") or artwork_search_index().get("search:" + (item.get("search") or item["name"]).strip().casefold()))
            if saved:
                item["artwork"] = saved
        items.append(item)
    with STATE_LOCK:
        active = deepcopy(ACTIVE_APPLE)
        available = LAST_POWER == "on" and READY and not PREPARING and not STANDBY.is_set()
    return {"items": items, "revision":snapshot["revision"], "available": available and target in enabled_device_ids(),
            "target": target, "group": group, "active": active}


def play_on_target(generation, target, media_type, content):
    if target not in enabled_device_ids():
        raise ValueError("Alexa-Abspielgerät ist deaktiviert")
    if media_type in ("custom", "APPLE_MUSIC"):
        inventory = integration_inventory()
        sources = [domain for domain in ("alexa_media", "alexa_devices") if target in inventory.get(domain, [])]
        if len(sources) != 1:
            raise ValueError("Alexa-Integration des Abspielgeräts nicht eindeutig erkannt")
        if media_type == "custom" and sources[0] == "alexa_devices":
            template = "{{ {'domain': config_entry_attr(config_entry_id('" + target + "'), 'domain'), 'device_id': device_id('" + target + "')} | to_json }}"
            raw = startup_request(generation, "/template", {"template":template})
            if not isinstance(raw, str):
                raise ValueError("Ungültige Antwort zur Alexa-Devices-Zuordnung")
            mapping = json.loads(raw)
            if not isinstance(mapping, dict) or mapping.get("domain") != "alexa_devices" or not isinstance(mapping.get("device_id"), str) or not re.fullmatch(r"[0-9a-f]{32}", mapping["device_id"]):
                raise ValueError("Alexa-Devices-Zuordnung des Abspielgeräts fehlt oder hat sich geändert")
            if target not in enabled_device_ids():
                raise ValueError("Alexa-Abspielgerät wurde deaktiviert")
            return startup_request(generation, "/services/alexa_devices/send_text_command", {
                "device_id":mapping["device_id"], "text_command":content})
    return startup_request(generation, "/services/media_player/play_media", {
        "entity_id":target, "media":{"media_content_type":media_type, "media_content_id":content, "metadata":{}}})


def album_tracks(favorite_id):
    """Catalog titles only; album identity comes from a saved favorite."""
    favorite = next((x for x in apple_music_selection()["items"] if x["id"] == favorite_id and x["kind"] == "Album"), None)
    if favorite is None:
        raise ValueError("Album nicht gefunden")
    if STANDBY.is_set() or not READY or PREPARING:
        raise ValueError("HA Music ist ausgeschaltet")
    album_id = favorite.get("album_id")
    if not album_id:
        match = album_cover_search({"search": favorite.get("search") or favorite["name"]}).get("selected")
        album_id = match.get("album_id") if match else None
        if not album_id:
            raise ValueError("Album nicht eindeutig erkannt. Bitte unter Alben verwalten eine Albumzuordnung auswählen")
    generation = RESTORE_GENERATION
    url = "https://itunes.apple.com/lookup?" + urlencode({"id": album_id, "entity": "song", "country": "DE", "limit": 200})
    request = Request(url, headers={"Accept": "application/json"})
    with urlopen(request, timeout=8) as response:
        with RESPONSE_LOCK:
            check_generation(generation)
            ACTIVE_RESPONSES.add(response)
        try:
            raw = response.read(262145)
        finally:
            with RESPONSE_LOCK:
                ACTIVE_RESPONSES.discard(response)
    check_generation(generation)
    if len(raw) > 262144:
        raise ValueError("Album-Titelliste zu groß")
    records = json.loads(raw).get("results", [])
    if not isinstance(records, list):
        raise ValueError("Ungültige Antwort von Apple")
    album = next((x for x in records if isinstance(x, dict) and x.get("wrapperType") == "collection" and x.get("collectionId") == album_id), None)
    if album is None:
        raise ValueError("Album im Apple-Katalog nicht gefunden")
    album_name = album.get("collectionName")
    if not isinstance(album_name, str) or not 0 < len(album_name.strip()) <= 200 or any(ord(c) < 32 for c in album_name):
        album_name = ""
    tracks = []
    for item in records:
        if not isinstance(item, dict) or item.get("wrapperType") != "track" or item.get("collectionId") != album_id:
            continue
        title, artist = item.get("trackName"), item.get("artistName")
        if not all(isinstance(v, str) and 0 < len(v.strip()) <= 200 and not any(ord(c) < 32 for c in v) for v in (title, artist)):
            continue
        tracks.append({"id": item.get("trackId"), "name": title, "artist": artist,
                       **({"duration":item["trackTimeMillis"]/1000} if type(item.get("trackTimeMillis")) is int and 0 < item["trackTimeMillis"] <= 86400000 else {}),
                       **({"album":album_name.strip()} if album_name else {}),
                       "number": item.get("trackNumber") if type(item.get("trackNumber")) is int else len(tracks) + 1})
    tracks.sort(key=lambda item: item["number"])
    saved = stored_album(album_id)
    image = saved["image"] if saved else artwork_url(album.get("artworkUrl100"), "mzstatic.com")
    return {"album": favorite["name"], "artist": album.get("artistName", ""),
            "image": image, "tracks": tracks[:200]}


def playlist_tracks(favorite_id):
    favorite = next((x for x in apple_music_selection()["items"] if x["id"] == favorite_id and x["kind"] == "Playlist"), None)
    if favorite is None:
        raise ValueError("Playlist nicht gefunden")
    if STANDBY.is_set() or not READY or PREPARING:
        raise ValueError("HA Music ist ausgeschaltet")
    tracks = favorite.get("tracks")
    if tracks is None:
        raise ValueError("Bitte unter Playlists verwalten eine Titelliste importieren")
    index = artwork_search_index()
    checked_images, checked_albums = {}, {}
    rendered = []
    for track in tracks:
        if track.get("cover"):
            try:
                if track["cover"] not in checked_images:
                    local_playlist_image(track["cover"])
                    checked_images[track["cover"]] = True
                rendered.append({**track, "image":f"api/playlist-art/{track['cover']}"})
                continue
            except (OSError, ValueError):
                pass
        key = playlist_catalog_key(track)
        if key not in checked_albums:
            checked_albums[key] = stored_album(index.get(key))
        cover = checked_albums[key]
        rendered.append({**track, **({"image":cover["image"]} if cover and cover.get("image") else {})})
    return {"album":favorite["name"], "artist":"Importierte Playlist", "tracks":rendered}


def playlist_album_search(track):
    return (track.get("album", "") + " " + (track.get("album_artist") or track["artist"])).strip()


def playlist_catalog_key(track):
    return ("search:" + playlist_album_search(track).casefold() if track.get("album") else
            "song:" + json.dumps([track["name"].casefold(), track["artist"].casefold()], ensure_ascii=False))


def playlist_song_cover(track):
    """Fallback for old exports without album metadata; exact title and artist only."""
    global ARTWORK_LAST_REQUEST
    key = playlist_catalog_key(track)
    with ARTWORK_LOCK:
        saved = stored_album(artwork_search_index().get(key))
        if saved:
            return saved
        cache = artwork_cache()
        entry = cache.get(key, {})
        if isinstance(entry, dict) and type(entry.get("at")) in (int, float) and time.time() - entry["at"] < 86400:
            albums = entry.get("items", [])
        else:
            if STANDBY.is_set() or not READY or PREPARING:
                raise RuntimeError("Coversuche erst verfügbar, wenn HA Music bereit ist")
            if time.monotonic() - ARTWORK_LAST_REQUEST < 4:
                raise RuntimeError("Coversuche kurz ausgelastet. Bitte erneut versuchen.")
            ARTWORK_LAST_REQUEST = time.monotonic()
            generation = RESTORE_GENERATION
            params = {"country":"DE", "entity":"song", "limit":25, "term":track["name"] + " " + track["artist"]}
            with urlopen(Request("https://itunes.apple.com/search?" + urlencode(params), headers={"Accept":"application/json"}), timeout=8) as response:
                with RESPONSE_LOCK:
                    check_generation(generation)
                    ACTIVE_RESPONSES.add(response)
                try:
                    raw = response.read(262145)
                finally:
                    with RESPONSE_LOCK:
                        ACTIVE_RESPONSES.discard(response)
            check_generation(generation)
            if len(raw) > 262144:
                raise ValueError("Antwort der Coversuche ist zu groß")
            data = json.loads(raw)
            results = data.get("results") if isinstance(data, dict) else None
            if not isinstance(results, list):
                raise ValueError("Ungültige Antwort der Coversuche")
            def normalized(value):
                return " ".join(re.findall(r"\w+", unicodedata.normalize("NFKC", value).casefold())) if isinstance(value, str) else ""
            songs = [{**song,"collectionType":"Album"} for song in results[:25] if isinstance(song, dict)
                     and song.get("kind") == "song" and normalized(song.get("trackName")) == normalized(track["name"])
                     and normalized(song.get("artistName")) == normalized(track["artist"])]
            albums = album_candidates({"results":songs})
            cache[key] = {"at":time.time(),"items":albums}
            cache = dict(sorted(cache.items(), key=lambda pair:pair[1].get("at",0) if isinstance(pair[1],dict) else 0)[-100:])
            with LOCK:
                write_durable_json(ARTWORK_FILE,cache)
        if len(albums) != 1:
            return None
        cover = store_album_image(albums[0])
        remember_artwork_search(key, cover["album_id"])
        return cover


def matching_playlist_album(albums, track):
    def normalized(value):
        return " ".join(re.findall(r"\w+", unicodedata.normalize("NFKC", value).casefold()))
    if not track.get("album") or not isinstance(albums, list):
        return None
    matches = [album for album in albums if isinstance(album, dict) and
               isinstance(album.get("name"), str) and isinstance(album.get("artist"), str) and
               normalized(album["name"]) == normalized(track["album"]) and
               normalized(album["artist"]) == normalized(track.get("album_artist") or track["artist"])]
    return matches[0] if len(matches) == 1 else None


def playlist_track_cover(favorite_id, track_id):
    if type(track_id) is not int:
        raise ValueError("Ungültige Titel-ID")
    track = next((item for item in playlist_tracks(favorite_id)["tracks"] if item["id"] == track_id), None)
    if track is None:
        raise ValueError("Titel gehört nicht zur gespeicherten Playlist")
    if track.get("image"):
        return {"image":track["image"]}
    if not track.get("album"):
        cover = playlist_song_cover(track)
        return {"image":cover["image"] if cover else ""}
    result = album_cover_search({"search":playlist_album_search(track)}, max_search_length=401)
    cover = matching_playlist_album(result.get("items"), track)
    if cover:
        with ARTWORK_LOCK:
            cover = store_album_image(cover)
            remember_artwork_search(playlist_catalog_key(track), cover["album_id"])
    return {"image":cover["image"] if cover else ""}


def cancel_single_track():
    global SINGLE_TRACK
    with STATE_LOCK:
        if SINGLE_TRACK:
            SINGLE_TRACK["cancel"].set()
        SINGLE_TRACK = None


def single_track_step(session, states, now):
    def normalized(value):
        return re.sub(r"[^\w]+", "", unicodedata.normalize("NFKC", value).casefold()) if isinstance(value,str) else ""
    candidates = (session["stop_target"],) if session.get("confirmed") else ("media_player.wohnung", session["target"])
    entries = [(entity, states.get(entity, {})) for entity in dict.fromkeys(candidates)
               if entity in enabled_device_ids()]
    matches = [(entity,state) for entity,state in entries if state.get("state") in ("playing","paused")
               and normalized((state.get("attributes") or {}).get("media_title")) == normalized(session["track"]["name"])
               and (not (state.get("attributes") or {}).get("media_artist") or
                    normalized((state.get("attributes") or {}).get("media_artist")) == normalized(session["track"]["artist"]))]
    if not matches:
        if session.get("confirmed") and any(state.get("state") in ("idle", "off") or
                state.get("state") == "playing" and (state.get("attributes") or {}).get("media_title") for _,state in entries):
            return True  # The selected song ended; never let an automatic follow-up continue.
        if session.get("confirmed"):
            return now-session.get("last_seen",now) >= 60
        return not session.get("confirmed") and now-session["started"] >= 60
    entity, state = matches[0]
    attrs = state.get("attributes") or {}
    session["confirmed"] = True
    session["last_seen"] = now
    session["stop_target"] = entity
    features = attrs.get("supported_features", 0)
    session["native_pause"] = type(features) is int and bool(features & 1)
    duration = attrs.get("media_duration")
    if type(duration) not in (int,float) or not 0 < duration <= 86400:
        duration = session["track"].get("duration")
    if type(duration) not in (int,float) or not 0 < duration <= 86400:
        return False  # Watch title changes when Alexa supplies no duration.
    position = attrs.get("media_position")
    position = float(position) if type(position) in (int,float) and 0 <= position <= duration else None
    stamp = attrs.get("media_position_updated_at")
    changed = session.get("position") != position or session.get("stamp") != stamp
    if state["state"] == "paused":
        if not session.get("paused"):
            session["progress"] = (position if changed and position is not None else
                                   duration - max(0, session["deadline"]-now) if session.get("deadline") is not None else
                                   position or session.get("progress",0))
        session.update(paused=True, deadline=None, position=position, stamp=stamp)
        return False
    resumed = session.pop("paused",False)
    if session.get("deadline") is None or changed:
        progress = position if position is not None and (changed or not resumed) else session.get("progress",0)
        if isinstance(stamp,str) and not resumed:
            try:
                updated = datetime.fromisoformat(stamp.replace("Z","+00:00"))
                if updated.tzinfo is not None:
                    progress += max(0,time.time()-updated.timestamp())
            except ValueError:
                pass
        deadline = now+max(0,duration-progress)
        if session.get("deadline") is not None and session.get("position") == position:
            deadline = min(deadline, session["deadline"])
        session["deadline"] = deadline
        session["position"], session["stamp"] = position,stamp
        session["progress"] = progress
    return now >= session["deadline"]


def single_track_worker(session):
    global SINGLE_TRACK, SOURCE_RESTORE_ERROR
    failures = 0
    while not session["cancel"].is_set():
        with STATE_LOCK:
            if SINGLE_TRACK is not session or session["generation"] != RESTORE_GENERATION or STANDBY.is_set() or not READY:
                return
        try:
            should_pause = single_track_step(session,state_snapshot(),time.monotonic())
            if should_pause:
                with COMMAND_LOCK:
                    with STATE_LOCK:
                        if SINGLE_TRACK is not session or session["cancel"].is_set():
                            return
                        check_generation(session["generation"])
                    if session.get("native_pause"):
                        startup_request(session["generation"], "/services/media_player/media_pause", {"entity_id":session["stop_target"]})
                    else:
                        play_on_target(session["generation"],session["target"],"custom","pause")
                    with STATE_LOCK:
                        if SINGLE_TRACK is session:
                            SINGLE_TRACK = None
                            if not session.get("confirmed"):
                                SOURCE_RESTORE_ERROR = "Einzeltitel nicht bestätigt; Wiedergabe vorsorglich pausiert."
                    return
            failures = 0
        except StartupCancelled:
            return
        except NETWORK_ERRORS as exc:
            failures += 1
            print(f"[HA Music] Single-track end check failed: {exc}",flush=True)
            if failures >= 3:
                with STATE_LOCK:
                    if SINGLE_TRACK is session:
                        SOURCE_RESTORE_ERROR = "Einzeltitel-Ende konnte nicht überwacht oder pausiert werden."
                        SINGLE_TRACK = None
                return
        remaining = max(0.05,session.get("deadline",time.monotonic()+1)-time.monotonic()) if session.get("deadline") is not None else 1
        session["cancel"].wait(min(1,remaining))


def start_single_track(track, target, generation):
    global SINGLE_TRACK
    with STATE_LOCK:
        cancel_single_track()
        SINGLE_TRACK = {"track":dict(track),"target":target,"generation":generation,"started":time.monotonic(),"cancel":threading.Event()}
        session = SINGLE_TRACK
    threading.Thread(target=single_track_worker,args=(session,),daemon=True).start()


def play_apple_album_track(favorite_id, track_id, generation, *, playlist=False):
    global ACTIVE_APPLE, APPLE_VERIFICATION_STARTED, SOURCE_UNCONFIRMED, SOURCE_RESTORE_ERROR
    if type(track_id) is not int or not 0 < track_id < 10**16:
        raise ValueError("Ungültige Titel-ID")
    selection = apple_music_selection()
    if not selection["available"]:
        raise ValueError("Apple-Music-Steuergerät nicht bereit")
    data = playlist_tracks(favorite_id) if playlist else album_tracks(favorite_id)
    track = next((x for x in data["tracks"] if x["id"] == track_id), None)
    if track is None:
        raise ValueError("Titel gehört nicht zur gespeicherten Playlist" if playlist else "Titel gehört nicht zum gespeicherten Album")
    check_generation(generation)
    command = "spiel " + track["name"] + " von " + track["artist"]
    if track.get("album"):
        command += " aus dem Album " + track["album"]
    print("[HA Music] Single track request: " + json.dumps({"title":track["name"],
          "artist":track["artist"], "target":selection["target"], "media_type":"custom",
          "command":command}, ensure_ascii=False), flush=True)
    result = play_on_target(generation, selection["target"], "custom", command)
    favorite = next(item for item in selection["items"] if item["id"] == favorite_id)
    with STATE_LOCK:
        check_generation(generation)
        save_selected_source("unknown", "")  # A single track must not restore its entire playlist on restart.
        save_selected_view("apple")
        ACTIVE_APPLE = {**favorite, "target": selection["target"], "kind": "Track", "name": track["name"]}
        APPLE_VERIFICATION_STARTED = None
        SOURCE_UNCONFIRMED = False
        SOURCE_RESTORE_ERROR = None
        MONITOR.select("")
        start_single_track(track,selection["target"],generation)
    return result


def play_apple_music(favorite_id, generation, *, startup=False):
    global ACTIVE_APPLE, SOURCE_UNCONFIRMED, SOURCE_RESTORE_ERROR, APPLE_VERIFICATION_STARTED
    selection = apple_music_selection()
    favorite = next((item for item in selection["items"] if item["id"] == favorite_id), None)
    if favorite is None:
        raise ValueError("Unbekannter Apple-Music-Favorit")
    check_generation(generation)
    startup_allowed = startup and READY and PREPARING and selection["target"] in enabled_device_ids()
    if not selection["available"] and not startup_allowed:
        raise ValueError("Apple-Music-Steuergerät ist nicht freigegeben oder HA Music ist noch nicht bereit")
    media_type = "APPLE_MUSIC"
    phrase = favorite.get("command", ("spiel playlist " if favorite["kind"] == "Playlist" else "spiel album ") + favorite["name"])
    print("[HA Music] Apple playback request: " + json.dumps({"name":favorite["name"],
          "playback_name":favorite["name"], "kind":favorite["kind"], "target":selection["target"],
          "group":"",
          "media_type":media_type, "command":phrase}, ensure_ascii=False), flush=True)
    result = play_on_target(generation, selection["target"], media_type, phrase)
    with STATE_LOCK:
        check_generation(generation)
        cancel_single_track()
        save_selected_source("apple", favorite_id)
        save_selected_view("apple")
        ACTIVE_APPLE = {**favorite, "target": selection["target"]}
        APPLE_VERIFICATION_STARTED = time.monotonic()
        SOURCE_UNCONFIRMED = False
        SOURCE_RESTORE_ERROR = None
        MONITOR.select("")  # Apple playback no longer needs a radio metadata stream.
    return result


def last_selected_source():
    try:
        data = json.loads(SOURCE_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        station = last_selected_station()
        return {"kind":"radio", "id":station} if station else None
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("version") != 1:
        return None
    kind, identity = data.get("kind"), data.get("id")
    if kind == "radio" and isinstance(identity, str) and identity in DIRECT_STATIONS:
        return {"kind":kind, "id":identity}
    if kind == "apple" and isinstance(identity, str) and re.fullmatch(r"[0-9a-f]{24}", identity):
        return {"kind":kind, "id":identity}
    return None


def save_selected_source(kind, identity):
    with LOCK:
        write_durable_json(SOURCE_FILE, {"version":1, "kind":kind, "id":identity})


def save_selected_station(station):
    if station not in DIRECT_STATIONS:
        return
    with LOCK:
        write_durable_json(SOURCE_FILE, {"version":1, "kind":"radio", "id":station})
        write_durable_json(STATION_FILE, {"station":station})


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
        write_durable_json(VIEW_FILE, {"view":view})


def startup_remaining():
    if STARTED_AT is None:
        return None
    return max(0, 70 - int(time.monotonic() - STARTED_AT))


def state_snapshot(*, fresh=False):
    global STATE_CACHE
    with CACHE_LOCK:
        if STANDBY.is_set():
            raise RuntimeError("HA Music standby: outbound network disabled")
        at, cached = STATE_CACHE
        if not fresh and cached is not None and time.monotonic() - at < 2:
            return cached
        states = ha_request("/states")
        if not isinstance(states, list):
            raise ValueError("Ungültige Home-Assistant-Zustände")
        result = {s["entity_id"]:s for s in states if isinstance(s,dict) and isinstance(s.get("entity_id"),str)}
        STATE_CACHE = (time.monotonic(), result)
        return result

def radio_state():
    with STATE_LOCK:
        return _radio_state()


def _radio_state():
    """Read one consistent local lifecycle snapshot; no unused HA request."""
    if STANDBY.is_set():
        return {"power":"off", "ready":"off", "standby":True, "last_station":last_selected_station(),
                "recovering":RECOVERING, "recovery_message":RECOVERY_MESSAGE,
                "selected_view":selected_view(), "startup_remaining":None, "apple_music":apple_music_selection(),
                "dashboard_card_installed":dashboard_card_installed(),
                "show_dashboard_setup":options().get("show_dashboard_setup", False) is True,
                "stations":[{"id":key,"name":item["name"],"available":False} for key,item in DIRECT_STATIONS.items()]}
    selected = enabled_device_ids()
    return {"power":LAST_POWER,
            "ready":"on" if READY else "off",
            "preparing":PREPARING,
            "startup_error":STARTUP_ERROR,
            "last_station":"" if SOURCE_UNCONFIRMED else last_selected_station(),
            "recovering":False, "recovered_session":SOURCE_UNCONFIRMED,
            "selected_view":selected_view(),
            "apple_music":apple_music_selection(),
            "startup_remaining":startup_remaining(),
            "dashboard_card_installed":dashboard_card_installed(),
            "show_dashboard_setup":options().get("show_dashboard_setup", False) is True,
            "stations":[{"id":key, "name":item["name"],
                         "available":item["target"] in selected}
                        for key,item in DIRECT_STATIONS.items()]}


def group_transport_state(states):
    group = states.get("media_player.wohnung", {}) if "media_player.wohnung" in enabled_device_ids() else {}
    state = group.get("state", "unavailable")
    features = (group.get("attributes") or {}).get("supported_features", 0)
    features = features if type(features) is int else 0
    return {"state": state,
            "can_play": state == "paused" and bool(features & MEDIA_FEATURE_PLAY),
            "can_pause": state == "playing" and bool(features & MEDIA_FEATURE_PAUSE)}


def track_transport_state(states):
    """Choose the live group/controller, never an unrelated playing room."""
    result = {"entity_id":None, "can_previous":False, "can_next":False,
              "can_shuffle":False, "shuffle":None}
    with STATE_LOCK:
        if not ACTIVE_APPLE and not SOURCE_UNCONFIRMED and last_selected_station() in ("wdr2", "1live", "swr3"):
            return result  # Live radio has no track queue.
        target = ACTIVE_APPLE["target"] if ACTIVE_APPLE else apple_music_selection()["target"] if SOURCE_UNCONFIRMED else "media_player.wohnzimmer"
    selected = enabled_device_ids()
    for entity in dict.fromkeys(("media_player.wohnung", target)):
        if entity not in selected:
            continue
        entry = states.get(entity, {})
        attrs = entry.get("attributes") or {}
        features = attrs.get("supported_features", 0)
        features = features if type(features) is int else 0
        if entry.get("state") not in ("playing", "paused") or not features & (
            MEDIA_FEATURE_PREVIOUS | MEDIA_FEATURE_NEXT | MEDIA_FEATURE_SHUFFLE
        ) or attrs.get("media_content_type") in ("channel", "radio", "url"):
            continue
        shuffle = attrs.get("shuffle")
        return {"entity_id":entity, "can_previous":bool(features & MEDIA_FEATURE_PREVIOUS),
                "can_next":bool(features & MEDIA_FEATURE_NEXT),
                "can_shuffle":bool(features & MEDIA_FEATURE_SHUFFLE) and type(shuffle) is bool,
                "shuffle":shuffle if type(shuffle) is bool else None}
    return result


def verify_apple_album(favorite, metadata, elapsed):
    """Conservative post-playback validation; never issues another Alexa command."""
    if not favorite or favorite.get("kind") != "Album":
        return None
    if elapsed is None:
        return {"status": "unverifiable", "reason": "Keine neue Albumprüfung aktiv"}
    if elapsed < 20:
        return {"status": "pending", "reason": "Warte auf Alexa-Metadaten"}
    if not metadata or metadata.get("state") not in ("playing", "paused"):
        return {"status": "unverifiable", "reason": "Keine aktive Wiedergabe bestätigt"}
    actual = metadata.get("album")
    if not isinstance(actual, str) or not actual.strip():
        return {"status": "unverifiable", "reason": "Alexa liefert keinen Albumnamen"}
    expected = favorite.get("name", "")
    def normalize(value):
        return re.sub(r"[^\w]+", "", unicodedata.normalize("NFKC", value).casefold())
    if normalize(actual) != normalize(expected):
        return {"status": "mismatch", "reason": "Alexa meldet anderes Album: " + actual[:160]}
    # Album title match alone cannot establish the exact recording or edition.
    return {"status": "confirmed", "reason": "Alexa meldet das erwartete Album: " + actual[:160]}


def playback_status():
    states = state_snapshot(fresh=True)
    with STATE_LOCK:
        target = ACTIVE_APPLE["target"] if ACTIVE_APPLE else apple_music_selection()["target"] if SOURCE_UNCONFIRMED else "media_player.wohnzimmer"
        apple = ACTIVE_APPLE is not None
        verification_favorite = deepcopy(ACTIVE_APPLE)
        verification_elapsed = time.monotonic() - APPLE_VERIFICATION_STARTED if APPLE_VERIFICATION_STARTED is not None else None
        unconfirmed_source = SOURCE_UNCONFIRMED
        source_restore_error = SOURCE_RESTORE_ERROR
        for entity, confirmation in list(VOLUME_CONFIRMATION.items()):
            entry = states.get(entity, {})
            observed_volume = (entry.get("attributes") or {}).get("volume_level")
            if entry.get("state") not in ("unknown", "unavailable") and type(observed_volume) in (int, float) and abs(observed_volume - confirmation["expected"]) <= 0.005:
                VOLUME_CONFIRMATION.pop(entity, None)
        volume_confirmation = deepcopy(VOLUME_CONFIRMATION)
    candidates = dict.fromkeys((target, "media_player.wohnung", *enabled_device_ids()))
    selected = enabled_device_ids()
    observed = []
    for entity in candidates:
        if entity not in selected:
            continue
        state = states.get(entity)
        if not state:
            continue
        attrs = state.get("attributes") or {}
        observed.append({"entity_id": entity, "state": state.get("state","unknown"),
                         "title": attrs.get("media_title"),
                         "artist": attrs.get("media_artist"),
                         "album": attrs.get("media_album_name"),
                         "image": attrs.get("media_image_url") or attrs.get("entity_picture"),
                         "content_type": attrs.get("media_content_type")})
    track_transport = track_transport_state(states)
    priority = dict.fromkeys((track_transport["entity_id"], "media_player.wohnung", target))
    queue_players = [p for entity in priority for p in observed if p["entity_id"] == entity]
    if unconfirmed_source:
        queue_players += [p for p in observed if p["entity_id"] not in priority]
    # Idle devices may carry another session's title/artwork in normal starts
    # as well as reattached sessions. Never fill a live player's missing fields
    # from one of those stale entries.
    details = next((p for p in queue_players if p["state"] in ("playing", "paused")), None)
    active = details if details and details["state"] == "playing" else None
    if apple:
        # Read one complete snapshot from the player used by Vor/Zurück.
        # Never fill missing fields from the controller or another room.
        source = track_transport["entity_id"] or target
        details = next((p for p in observed if p["entity_id"] == source and p["state"] in ("playing", "paused")), None)
        active = details if details and details["state"] == "playing" else None
    _, station, payload = MONITOR.snapshot()
    return {"playing": active is not None, "players": observed,
            "radio_metadata": {"station": station, "metadata": payload},
            "transport": group_transport_state(states),
            "track_transport": track_transport,
            "volume_confirmation":volume_confirmation,
            "source_restore_error":source_restore_error,
            "apple_verification": verify_apple_album(verification_favorite, details, verification_elapsed) if apple else None,
            "details": details}


def play_station(key, generation):
    global ACTIVE_APPLE, SOURCE_UNCONFIRMED, SOURCE_RESTORE_ERROR
    if not isinstance(key, str) or key not in DIRECT_STATIONS:
        raise ValueError("Unbekannter Sender")
    preset = DIRECT_STATIONS[key]
    target = preset["target"]
    if target not in enabled_device_ids():
        raise ValueError(f"Alexa-Senderziel {target} ist in der Add-on-Konfiguration deaktiviert")
    result = play_on_target(generation, target, preset["media_content_type"], preset["media_content_id"])
    with STATE_LOCK:
        check_generation(generation)
        cancel_single_track()
        save_selected_station(key)
        save_selected_view("radio")
        ACTIVE_APPLE = None
        SOURCE_UNCONFIRMED = False
        SOURCE_RESTORE_ERROR = None
        MONITOR.select(key)
    return result


def power_command(on):
    if not isinstance(on, bool):
        raise ValueError("Ungültiger Schaltzustand")
    with POWER_LOCK:
        if on:
            if RECOVERING:
                raise ValueError("Bestehender Einschaltzustand wird noch geprüft; keine Startsequenz ausgeführt")
            if LAST_POWER == "on" and not STANDBY.is_set():
                return
            with STATE_LOCK:
                generation = transition_power(True)
                STANDBY.clear()
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
                # An explicit off also cancels a pending read-only reattachment.
                transition_power(False)
                enter_standby(RESTORE_GENERATION)
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
        if action == "apple_music":
            return play_apple_music(body.get("favorite"), generation)
        if action == "apple_album_track":
            return play_apple_album_track(body.get("favorite"), body.get("track_id"), generation)
        if action == "apple_playlist_track":
            return play_apple_album_track(body.get("favorite"), body.get("track_id"), generation, playlist=True)
        return perform_control(action, body, generation)


def master_room_levels(room_players):
    """Use the same active/muted intent for master commands and UI previews."""
    stored = speaker_levels()
    if stored.get("media_player.wohnung") == 0:
        saved = stored  # Preserve which rooms were active before master mute.
    else:
        saved = {**stored, **ROOM_TARGETS}
    intent = {p["entity_id"]: saved.get(p["entity_id"], stored.get("media_player.wohnung",0.3)) for p in room_players}
    return {entity: float(value) for entity, value in intent.items()
            if type(value) in (int, float) and 0 <= value <= 1}


def perform_control(action, body, generation):
    if action == "track_transport":
        command = body.get("command")
        if command not in ("previous", "next", "shuffle"):
            raise ValueError("Ungültiger Titelbefehl")
        transport = track_transport_state(state_snapshot(fresh=True))
        entity = body.get("entity_id")
        if not entity or entity != transport["entity_id"] or entity not in allowed_entities():
            raise ValueError("Wiedergabeziel hat sich geändert oder ist nicht freigegeben")
        if not transport["can_" + command]:
            raise ValueError("Titelfunktion wird für die aktuelle Wiedergabe nicht unterstützt")
        payload = {"entity_id":entity}
        if command == "shuffle":
            if type(body.get("shuffle")) is not bool:
                raise ValueError("Ungültiger Shuffle-Zustand")
            payload["shuffle"] = body["shuffle"]
        service = {"previous":"media_previous_track", "next":"media_next_track", "shuffle":"shuffle_set"}[command]
        result = startup_request(generation, "/services/media_player/" + service, payload)
        if command in ("previous", "next"):
            cancel_single_track()
        if command in ("previous", "next"):
            try:
                startup_request(generation, "/services/homeassistant/update_entity", {"entity_id":entity})
            except StartupCancelled:
                raise
            except NETWORK_ERRORS as exc:
                # The track command was accepted: do not report it as failed
                # or repeat it just because a metadata refresh failed.
                print(f"[HA Music] Track metadata refresh failed for {entity}: {exc}", flush=True)
        return result
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
        if player is None:
            raise ValueError("Raumgerät ist nicht freigegeben oder nicht verfügbar")
        level = displayed_speaker_levels().get(entity)
        known_level = type(level) in (int, float) and 0 <= level <= 1
        if on and known_level and level > 0:
            return {"ok": True, "volume": level}
        restore = remembered().get(entity)
        if type(restore) not in (int, float) or not 0 < restore <= 1:
            restore = speaker_levels().get("media_player.wohnung", 0.3)
        if type(restore) not in (int, float) or not 0 < restore <= 1:
            restore = 0.3
        perform_control("volume", {"entity_id": entity, "volume": restore if on else 0}, generation)
        if not on and known_level and level > 0:
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
        if entity == "media_player.wohnung":
            # Independent virtual master: apply its absolute percentage to unmuted rooms.
            room_players = classified["players"]
            # Use saved intent even when Alexa reports a shared group volume.
            room_intent = master_room_levels(room_players)
            active = [p for p in room_players
                      if room_intent.get(p["entity_id"], 0) > 0]
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
                                     **(changed if level > 0 else room_intent)})
                for changed_entity in changed:
                    VOLUME_CONFIRMATION.pop(changed_entity, None)
                    restore = float(level) if level > 0 else room_intent[changed_entity]
                    if restore > 0:
                        save_remembered(changed_entity, restore)
                if level > 0:
                    save_remembered(entity, level)
            if failed:
                print("[HA Music] Master partial failure: " + "; ".join(failed), flush=True)
                raise RuntimeError("Master: " + "; ".join(failed))
            return {"ok": True, "updated": len(changed)}
        previous = displayed_speaker_levels().get(entity)
        result = startup_request(generation, "/services/media_player/volume_set", {"entity_id": entity, "volume_level": level})
        with STATE_LOCK:
            check_generation(generation)
            save_speaker_levels({entity: float(level)})
            ROOM_TARGETS[entity] = float(level)
            VOLUME_CONFIRMATION.pop(entity, None)
        print(f"[HA Music] Saved speaker {entity}: {round(level * 100)}%", flush=True)
        if level > 0:
            save_remembered(entity, level)
        elif type(previous) in (int, float) and previous > 0:
            save_remembered(entity, previous)
        return result
    raise ValueError("Unbekannte Aktion")

class Handler(BaseHTTPRequestHandler):
    def read_request_body(self, max_size=2048):
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
                if len(body) + size > max_size:
                    raise ValueError("Invalid request length")
                part = read(size)
                if len(part) != size or read(2) != b"\r\n":
                    raise ValueError("Truncated chunked request")
                body.extend(part)
                framing_size += 2
        if len(lengths) != 1 or not re.fullmatch(r"[0-9]{1,10}", lengths[0].strip()):
            raise ValueError("Invalid request length")
        size = int(lengths[0])
        if not 0 < size <= max_size:
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
        if "/api/playlist-art/" in path:
            try:
                content, mime = local_playlist_image(name)
            except (OSError, ValueError):
                return self.reply(404, {"error":"Cover nicht gefunden"})
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "private, max-age=31536000, immutable")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(content)
            return
        if "/api/album-art/" in path:
            if not re.fullmatch(r"[1-9][0-9]{0,15}", name):
                return self.reply(404, {"error":"Cover nicht gefunden"})
            try:
                content, mime = local_album_image(int(name))
            except (OSError, ValueError):
                return self.reply(404, {"error":"Cover nicht gefunden"})
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "private, max-age=31536000, immutable")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(content)
            return
        if name == "status" and "/api/" in path:
            return self.reply(200, {"radio": "direct_presets",
                                    "apple_music": "alexa_favorites", "backend": "connected" if TOKEN else "unavailable"})
        if name in ("album-tracks", "playlist-tracks", "playlist-cover", "playlist-import") and "/api/" in path:
            return self.reply(405, {"error":"POST required"})
        if name == "apple-library" and "/api/" in path:
            try:
                return self.reply(200, library_snapshot())
            except NETWORK_ERRORS as exc:
                return self.reply(503, {"error": str(exc)})
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
                    "master_room_levels": master_room_levels(classified["players"]),
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
        if self.path in ("/api/playlist-sync", "/api/playlist-artwork-sync"):
            # The optional host port exposes only this capability. Never accept
            # a Supervisor/HA token or trust forwarded ingress headers here.
            token = options().get("playlist_sync_token", "")
            authorization = self.headers.get_all("Authorization", [])
            if (not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", token)
                    or len(authorization) != 1
                    or not hmac.compare_digest(authorization[0].encode(), ("Bearer " + token).encode())
                    or self.headers.get("Origin") or self.headers.get("Sec-Fetch-Site") == "cross-site"):
                return self.reply(403, {"error": "Playlist sync authorization required"})
            if self.headers.get_content_type() != "application/json":
                return self.reply(415, {"error": "JSON required"})
            try:
                artwork = self.path == "/api/playlist-artwork-sync"
                body = json.loads(self.read_request_body(192 * 1024 if artwork else 2097152))
                if not isinstance(body, dict):
                    raise ValueError("Invalid body")
                return self.reply(200, sync_playlist_artwork(body) if artwork else sync_playlist(body))
            except (ValueError, TypeError) as exc:
                return self.reply(400, {"error": str(exc)})
            except OSError:
                return self.reply(502, {"error": "Playlist konnte nicht gespeichert werden"})
        if not self.ingress_allowed():
            return self.reply(403, {"error": "Ingress only"})
        path = unquote(urlsplit(self.path).path)
        action = path.rsplit("/", 1)[-1]
        if "/api/" not in path or action not in ("volume", "room_audio", "group_transport", "track_transport", "radio_direct", "apple_music", "radio_power", "selected_view", "dashboard_card_installed", "apple-library", "apple-library-order", "album-covers", "album-tracks", "apple_album_track", "playlist-tracks", "playlist-cover", "playlist-import", "apple_playlist_track"):
            return self.reply(404, {"error": "Not found"})
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            return self.reply(403, {"error": "Cross-site request rejected"})
        if self.headers.get_content_type() != "application/json":
            return self.reply(415, {"error": "JSON required"})
        try:
            body = json.loads(self.read_request_body(2097152) if action in ("apple-library", "playlist-import", "apple-library-order") else self.read_request_body())
            if not isinstance(body, dict):
                raise ValueError("Invalid body")
            if action == "album-tracks":
                return self.reply(200, album_tracks(body.get("favorite")))
            if action == "playlist-tracks":
                return self.reply(200, playlist_tracks(body.get("favorite")))
            if action == "playlist-cover":
                return self.reply(200, playlist_track_cover(body.get("favorite"), body.get("track_id")))
            if action == "playlist-import":
                return self.reply(200, import_playlist(body))
            if action == "album-covers":
                return self.reply(200, album_cover_search(body))
            if action == "apple-library":
                saved = save_library(body)
                return self.reply(200, {**saved, "selection": apple_music_selection()})
            if action == "apple-library-order":
                saved = reorder_library(body)
                return self.reply(200, {**saved, "selection": apple_music_selection()})
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
        migrate_library(options())
    except NETWORK_ERRORS as exc:
        print(f"[HA Music] Music library migration pending: {exc}", flush=True)
    try:
        synchronize_device_configuration(None, migrate_only=True, bootstrap=True)
    except NETWORK_ERRORS as exc:
        print(f"[HA Music] Device status migration pending: {exc}", flush=True)
    threading.Thread(target=radio_switch_monitor, daemon=True).start()
    start_session_recovery()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
