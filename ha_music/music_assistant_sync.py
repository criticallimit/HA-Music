"""Read-only Music Assistant library import for Apple Music Alexa presets.

No Music Assistant playback, Apple credentials, or media stream is required.
Imported items are stored separately from manually maintained Alexa commands.
"""
import json
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


def load_imported(path):
    """Preserve last successful import; malformed storage must not be destroyed."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    if not isinstance(data, list):
        raise ValueError("Music-Assistant-Importdatei ist beschädigt")
    for item in data:
        if not isinstance(item, dict) or item.get("kind") not in ("Playlist", "Album") or not isinstance(item.get("name"), str):
            raise ValueError("Music-Assistant-Importdatei ist beschädigt")
    return data


def _apple_item(item):
    if not isinstance(item, dict):
        return False
    if item.get("provider") == "apple_music":
        return True
    mappings = item.get("provider_mappings") or []
    return isinstance(mappings, list) and any(
        isinstance(mapping, dict) and (
            mapping.get("provider_domain") == "apple_music"
            or mapping.get("provider_instance", "").split("--", 1)[0] == "apple_music"
        )
        for mapping in mappings
    )


def _request(url, token, command, offset):
    body = json.dumps({
        "message_id": "ha-music-import",
        "command": command,
        "args": {"limit": 100, "offset": offset, "provider": "apple_music"},
    }).encode("utf-8")
    req = Request(url, data=body, headers={
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }, method="POST")
    with urlopen(req, timeout=12) as response:
        if response.status != 200:
            raise RuntimeError("Music Assistant HTTP " + str(response.status))
        # Limit server response size even if peer is not trusted.
        raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError("Music Assistant Antwort zu groß")
    data = json.loads(raw)
    if not isinstance(data, dict) or data.get("error"):
        raise ValueError("Music Assistant API Fehler: " + str(data.get("error") if isinstance(data, dict) else "ungültig"))
    result = data.get("result")
    if not isinstance(result, list):
        raise ValueError("Music Assistant lieferte keine gültige Medienliste")
    return result


def sync_music_assistant(base_url, token):
    """Return validated imported playlists/albums; callers atomically persist on success."""
    if not isinstance(base_url, str) or not isinstance(token, str) or not token.strip():
        raise ValueError("Music Assistant URL und Token fehlen")
    parsed = urlsplit(base_url.strip())
    if (parsed.scheme not in ("http", "https") or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in ("", "/") or len(base_url) > 512):
        raise ValueError("Music Assistant URL muss http(s)://host:8095 sein")
    url = base_url.strip().rstrip("/") + "/api"
    items = []
    seen = set()
    for kind, command in (("Playlist", "music/playlists/library_items"),
                          ("Album", "music/albums/library_items")):
        for offset in range(0, 1000, 100):
            batch = _request(url, token, command, offset)
            for row in batch:
                if not _apple_item(row):
                    continue
                name = row.get("name")
                if (not isinstance(name, str) or not name.strip() or len(name.strip()) > 200
                        or any(ord(c) < 32 for c in name)):
                    continue
                name = name.strip()
                key = (kind, name.casefold())
                if key in seen:
                    continue
                seen.add(key)
                items.append({"kind": kind, "name": name, "search": name})
            if len(batch) < 100:
                break
        else:
            raise ValueError("Music Assistant liefert mehr als 1000 Einträge pro Kategorie")
    return items
