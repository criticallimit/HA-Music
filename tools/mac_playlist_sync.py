#!/usr/bin/env python3
"""Local Music.app -> HA Music bridge, using only Python's standard library."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import plistlib
import secrets
import shutil
import subprocess
import sys
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler

HOME = Path.home()
FOLDER = HOME / "Library/Application Support/HA Music Playlist Sync"
CONFIG = FOLDER / "config.json"
LABEL = "local.ha-music.playlist-sync"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward the sync key to another host.


def endpoint(url):
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
        raise ValueError("Adresse muss http(s)://HOST:PORT ohne Pfad sein")
    if parsed.scheme == "http":
        try:
            private = ipaddress.ip_address(parsed.hostname).is_private
        except ValueError:
            private = parsed.hostname.endswith(".local") or parsed.hostname == "localhost"
        if not private:
            raise ValueError("HTTP nur mit lokaler IP oder .local-Adresse verwenden; sonst HTTPS")
    return url.rstrip("/") + "/api/playlist-sync"


def music_tracks(name):
    script = '''
    const music = Application('Music');
    const matches = music.userPlaylists.whose({name: NAME})();
    if (matches.length !== 1) throw new Error('Playlist fehlt oder Name ist mehrfach vorhanden');
    const tracks = matches[0].tracks();
    if (tracks.length > 1000) throw new Error('Mehr als 1000 Titel');
    function optional(read) { try { return read() || ''; } catch (_) { return ''; } }
    JSON.stringify(tracks.map(t => { const duration = optional(() => t.duration()); return {name: t.name(), artist: t.artist(), album: optional(() => t.album()), album_artist: optional(() => t.albumArtist()), duration: Number.isFinite(duration) && duration > 0 && duration <= 86400 ? duration : null}; }));
    '''.replace("NAME", json.dumps(name))
    result = subprocess.run(["/usr/bin/osascript", "-l", "JavaScript", "-"],
                            input=script, text=True, encoding="utf-8", capture_output=True,
                            timeout=120, check=True)
    tracks = json.loads(result.stdout)
    if not isinstance(tracks, list) or len(tracks) > 1000 or any(
            not isinstance(t, dict) or any(not isinstance(t.get(k), str) or not t[k].strip()
            or len(t[k]) > 200 or any(ord(c) < 32 for c in t[k]) for k in ("name", "artist")) for t in tracks):
        raise ValueError("Playlist enthält ungültige Titelinformationen")
    if any(any(not isinstance(t.get(k, ""), str) or len(t.get(k, "")) > 200 or
                   any(ord(c) < 32 for c in t.get(k, "")) for k in ("album", "album_artist")) for t in tracks):
        raise ValueError("Playlist enthält ungültige Albuminformationen")
    if any(t.get("duration") is not None and (type(t["duration"]) not in (int, float) or not 0 < t["duration"] <= 86400) for t in tracks):
        raise ValueError("Playlist enthält ungültige Titellängen")
    return tracks


def sync(config):
    target = endpoint(config["url"])
    # Do not send a private LAN key through system HTTP proxies.
    opener = build_opener(NoRedirect(), ProxyHandler({}))
    failed = False
    for mapping in config["playlists"]:
        try:
            tracks = music_tracks(mapping["music"])
            if not tracks and mapping.get("allow_empty") is not True:
                raise ValueError("Leere Playlist erst nach vollständiger Mediathek-Synchronisierung freigeben")
            payload = json.dumps({"name": mapping["ha_music"], "tracks": tracks}, ensure_ascii=False).encode("utf-8")
            if len(payload) > 2097152:
                raise ValueError("Playlist zu groß")
            request = Request(target, data=payload, headers={"Content-Type": "application/json",
                              "Authorization": "Bearer " + config["token"]}, method="POST")
            with opener.open(request, timeout=30) as response:
                result = json.load(response)
            if result.get("ok") is not True:
                raise ValueError("Keine Bestätigung erhalten")
            print(f'{mapping["ha_music"]}: {len(tracks)} Titel synchronisiert', flush=True)
        except Exception as error:
            # Never log request headers, credentials, or an untrusted error body.
            print(f'{mapping["ha_music"]}: Synchronisierung fehlgeschlagen ({type(error).__name__}); bisherige Liste bleibt erhalten.', file=sys.stderr, flush=True)
            failed = True
    return 1 if failed else 0


def setup():
    if sys.platform != "darwin":
        raise ValueError("Einrichtung nur auf dem Mac möglich")
    FOLDER.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(FOLDER, 0o700)
    existing = json.loads(CONFIG.read_text(encoding="utf-8")) if CONFIG.exists() else {}
    url = input("HA-Music-Adresse (z.B. http://homeassistant.local:8099): ").strip()
    endpoint(url)
    token = existing.get("token") or secrets.token_urlsafe(32)
    playlists = []
    print("Playlistnamen eingeben; eine leere Eingabe beendet die Auswahl.")
    while True:
        name = input("Playlistname in der Mac-Musik-App: ").strip()
        if not name:
            break
        destination = input("Name in HA Music (Enter = gleicher Name): ").strip() or name
        if len(name) > 200 or len(destination) > 200 or any(x["ha_music"] == destination for x in playlists):
            raise ValueError("Namen müssen eindeutig sein und höchstens 200 Zeichen enthalten")
        playlists.append({"music": name, "ha_music": destination})
        if len(playlists) >= 50:
            break
    if not playlists:
        raise ValueError("Mindestens eine Playlist auswählen")
    CONFIG.write_text(json.dumps({"url": url, "token": token, "playlists": playlists}, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(CONFIG, 0o600)
    helper = FOLDER / "mac_playlist_sync.py"
    if Path(__file__).resolve() != helper.resolve():
        shutil.copyfile(__file__, helper)
    print("\nDiesen Schlüssel in den HA-Music-Add-on-Optionen unter playlist_sync_token speichern:")
    print(token)
    print("Netzwerkport 8099 aktivieren und Add-on neu starten. Danach hier fortfahren.")
    input("Enter zum Testen: ")
    if sync(json.loads(CONFIG.read_text(encoding="utf-8"))):
        print("Noch kein Zeitplan eingerichtet. Berechtigungen, Namen, Schlüssel und Port prüfen und --setup erneut ausführen.")
        return 1
    agents = HOME / "Library/LaunchAgents"
    agents.mkdir(parents=True, exist_ok=True)
    plist = agents / (LABEL + ".plist")
    data = {"Label": LABEL, "ProgramArguments": [sys.executable, str(helper)],
            "RunAtLoad": True, "StartInterval": 1800,
            "StandardOutPath": str(FOLDER / "sync.log"), "StandardErrorPath": str(FOLDER / "sync-error.log")}
    plist.write_bytes(plistlib.dumps(data))
    service = f"gui/{os.getuid()}/{LABEL}"
    subprocess.run(["/bin/launchctl", "bootout", service], capture_output=True)
    subprocess.run(["/bin/launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist)], check=True)
    print("Eingerichtet: Synchronisierung bei Anmeldung und alle 30 Minuten. Mac muss eingeschaltet und angemeldet sein.")
    print("Auch den ersten Hintergrundlauf in sync.log/sync-error.log prüfen; macOS kann dafür erneut eine Automationsfreigabe verlangen.")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--setup", action="store_true", help="Auf dem Mac interaktiv einrichten und Zeitplan installieren")
    parser.add_argument("--config", type=Path, default=CONFIG)
    args = parser.parse_args()
    try:
        return setup() if args.setup else sync(json.loads(args.config.read_text(encoding="utf-8")))
    except Exception as error:
        print(f"Einrichtung/Konfiguration fehlgeschlagen ({type(error).__name__}). Siehe Anleitung tools/MAC_PLAYLIST_SYNC.md.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
