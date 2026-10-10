"""Real HTTP boundary plus Mac helper failure and data preservation tests."""
from contextlib import ExitStack
from http.client import HTTPConnection
import importlib.util
import json
import base64
import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ha_music"))
import app

spec = importlib.util.spec_from_file_location("mac_sync", ROOT / "tools/mac_playlist_sync.py")
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


class PlaylistSyncTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        folder = Path(self.stack.enter_context(tempfile.TemporaryDirectory(dir=ROOT)))
        self.stack.enter_context(patch.object(app, "LIBRARY_FILE", folder / "library.json"))
        self.stack.enter_context(patch.object(app, "ARTWORK_DIR", folder / "covers"))
        self.stack.enter_context(patch.object(app, "ARTWORK_FILE", folder / "cover-cache.json"))
        self.key = "s" * 43
        self.stack.enter_context(patch.object(app, "options", return_value={"playlist_sync_token": self.key}))
        self.stack.enter_context(patch.object(app, "ha_request", side_effect=AssertionError("Sync must not contact HA")))
        self.items = [{"name": "Mix", "search": "Alexa Mix", "command": "spiel meine Playlist", "kind": "Playlist",
                       "tracks": [{"name": "Old", "artist": "Artist"}]},
                      {"name": "Album", "search": "Album", "kind": "Album", "album_id": 123},
                      {"name": "Other", "search": "Other", "kind": "Playlist"}]
        app.LIBRARY_FILE.write_text(json.dumps(app.normalize_library(self.items)))
        self.tracks = [{"name": "Grüße 🎵", "artist": "Björk", "album":"Debut", "album_artist":"Björk", "duration": 243.5}, {"name": "Again", "artist": "Band"}, {"name": "Again", "artist": "Band"}]
        class QuietHandler(app.Handler):
            def log_message(self, *args):
                pass
        self.server = app.ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.url = "http://127.0.0.1:" + str(self.server.server_port)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def request(self, path="/api/playlist-sync", body=None, headers=None, method="POST"):
        client = HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        self.addCleanup(client.close)
        default = {"Content-Type": "application/json", "Authorization": "Bearer " + self.key}
        if headers is not None:
            default.update(headers)
        client.request(method, path, json.dumps(body or {"name": "Mix", "tracks": self.tracks}).encode(), default)
        response = client.getresponse()
        return response.status, json.loads(response.read())

    def test_sync_preserves_commands_albums_and_duplicates_during_standby(self):
        standby = threading.Event()
        standby.set()
        before = app.library_snapshot()
        with patch.object(app, "STANDBY", standby):
            status, result = self.request()
        self.assertEqual(status, 200)
        self.assertTrue(result["changed"])
        after = app.library_snapshot()
        self.assertNotEqual(before["revision"], after["revision"])
        self.assertEqual(after["items"][1:], before["items"][1:])
        self.assertEqual(after["items"][0]["command"], before["items"][0]["command"])
        self.assertEqual(after["items"][0]["search"], "Alexa Mix")
        self.assertEqual([t["number"] for t in after["items"][0]["tracks"]], [1, 2, 3])
        self.assertEqual(after["items"][0]["tracks"][0]["name"], "Grüße 🎵")
        self.assertEqual(after["items"][0]["tracks"][0]["album"], "Debut")
        with patch.object(app, "write_durable_json", side_effect=AssertionError("Unchanged sync must not rewrite")):
            self.assertFalse(self.request()[1]["changed"])
        with self.assertRaises(ValueError):
            app.save_library(before)  # Reject editor saves opened before a sync.

    def test_ipad_shortcuts_newline_json_synchronizes_without_mac(self):
        shortcuts_tracks = "\n".join(json.dumps({"name": name, "artist": artist}, ensure_ascii=False)
            for name, artist in [("Hot N' Cold", "Katy Perry"), ('The Downeaster "Alexa"', "Billy Joel")])
        status, result = self.request(body={"name": "Mix", "tracks": shortcuts_tracks})
        self.assertEqual(status, 200)
        self.assertEqual(result["tracks"], 2)
        saved = app.library_snapshot()["items"][0]
        self.assertEqual([item["name"] for item in saved["tracks"]], ["Hot N' Cold", 'The Downeaster "Alexa"'])
        before = app.LIBRARY_FILE.read_bytes()
        for bad in ("not json", '{"name":"Missing artist"}', "{}", shortcuts_tracks + "\nnot-json"):
            status, _ = self.request(body={"name": "Mix", "tracks": bad})
            self.assertEqual(status, 400)
            self.assertEqual(app.LIBRARY_FILE.read_bytes(), before)

    def test_authenticated_cover_upload_deduplicates_and_track_sync_keeps_local_image(self):
        picture = b"\x89PNG\r\n\x1a\nlocal fixture"
        identity = hashlib.sha256(picture).hexdigest()
        payload = {"cover":identity,"data":base64.b64encode(picture).decode()}
        with patch.object(app,"urlopen",side_effect=AssertionError("Uploading never uses the internet")):
            status,result = self.request(path="/api/playlist-artwork-sync",body=payload)
            self.assertEqual(status,200)
            self.assertTrue(result["changed"])
            self.assertFalse(self.request(path="/api/playlist-artwork-sync",body=payload)[1]["changed"])
            self.assertEqual(app.local_playlist_image(identity),(picture,"image/png"))
            tracks = [{**self.tracks[0],"cover":identity,"local_covers":True},self.tracks[1]]
            self.assertEqual(self.request(body={"name":"Mix","tracks":tracks})[0],200)
            with patch.object(app,"READY",True), patch.object(app,"PREPARING",False), patch.object(app,"STANDBY",threading.Event()):
                favorite = app.apple_music_selection()["items"][0]
                rendered = app.playlist_tracks(favorite["id"])["tracks"][0]
                self.assertEqual(rendered["image"],"api/playlist-art/"+identity)
                with patch.object(app,"album_cover_search",side_effect=AssertionError("Do not search existing images")):
                    self.assertEqual(app.playlist_track_cover(favorite["id"],rendered["id"]),{"image":rendered["image"]})
        handler = object.__new__(app.Handler)
        handler.client_address = ("172.30.32.2",1)
        handler.path = "/prefix/api/playlist-art/"+identity
        from unittest.mock import Mock
        import io
        handler.send_response=Mock(); handler.send_header=Mock(); handler.end_headers=Mock();handler.reply=Mock()
        handler.wfile=io.BytesIO()
        handler.do_GET()
        self.assertEqual(handler.wfile.getvalue(),picture)
        handler.send_header.assert_any_call("Content-Type","image/png")
        handler.path="/api/playlist-art/..%2Foptions.json"
        handler.do_GET()
        self.assertEqual(handler.reply.call_args.args[0],404)
        handler.path="/api/playlist-art/"+identity
        handler.client_address=("127.0.0.1",1)
        handler.do_GET()
        self.assertEqual(handler.reply.call_args.args[0],403)

    def test_invalid_unauthorized_or_unuploaded_covers_do_not_modify_playlist(self):
        before = app.LIBRARY_FILE.read_bytes()
        data=b"\xff\xd8\xfffixture"
        identity=hashlib.sha256(data).hexdigest()
        payload={"cover":identity,"data":base64.b64encode(data).decode()}
        self.assertEqual(self.request(path="/api/playlist-artwork-sync",body=payload,headers={"Authorization":"wrong"})[0],403)
        for bad in ({**payload,"cover":"../options"},{**payload,"cover":"f"*64}, {**payload,"data":"bad base64"},
                    {"cover":hashlib.sha256(b"<svg></svg>").hexdigest(),"data":base64.b64encode(b"<svg></svg>").decode()}):
            self.assertEqual(self.request(path="/api/playlist-artwork-sync",body=bad)[0],400)
        for reference in (identity,"../options"):
            self.assertEqual(self.request(body={"name":"Mix","tracks":[{**self.tracks[0],"cover":reference}]})[0],400)
        self.assertEqual(app.LIBRARY_FILE.read_bytes(),before)

    def test_invalid_missing_and_duplicate_names_leave_file_unchanged(self):
        before = app.LIBRARY_FILE.read_bytes()
        for body in ({"name": "Unknown", "tracks": self.tracks}, {"name": "Album", "tracks": self.tracks},
                     {"name": "Mix", "tracks": [{"name": "Missing artist"}]},
                     {"name": "Mix", "tracks": self.tracks * 334}, {"name": "Mix", "tracks": "wrong"}):
            self.assertEqual(self.request(body=body)[0], 400)
            self.assertEqual(app.LIBRARY_FILE.read_bytes(), before)
        app.LIBRARY_FILE.write_text(json.dumps(self.items + [{"name": "Mix", "kind": "Playlist", "search": "Different"}]))
        before = app.LIBRARY_FILE.read_bytes()
        self.assertEqual(self.request()[0], 400)
        self.assertEqual(app.LIBRARY_FILE.read_bytes(), before)

    def test_optional_creation_is_playlist_only_and_never_overwrites_existing_metadata(self):
        status, result = self.request(body={"name": "New", "tracks": self.tracks, "create": True,
                                           "kind": "Album", "command": "untrusted command"})
        self.assertEqual(status, 200)
        self.assertTrue(result["created"])
        item = app.library_snapshot()["items"][-1]
        self.assertEqual(item["kind"], "Playlist")
        self.assertEqual(item["command"], "spiel playlist New")
        status, result = self.request(body={"name": "Mix", "tracks": self.tracks, "create": True,
                                           "command": "replace command"})
        self.assertEqual(status, 200)
        self.assertFalse(result["created"])
        self.assertEqual(app.library_snapshot()["items"][0]["command"], "spiel meine Playlist")
        self.assertEqual(self.request(body={"name": "Another", "tracks": self.tracks, "create": "true"})[0], 400)
        items = [{"kind": "Playlist", "name": str(i), "search": str(i)} for i in range(50)]
        app.LIBRARY_FILE.write_text(json.dumps(items))
        self.assertEqual(self.request(body={"name": "Too many", "tracks": self.tracks, "create": True})[0], 400)
        self.assertEqual(app.library_snapshot()["items"], items)

    def test_no_key_wrong_key_browser_origin_and_wrong_content_type_denied(self):
        before = app.LIBRARY_FILE.read_bytes()
        for headers in ({"Authorization": ""}, {"Authorization": "Bearer wrong"},
                        {"Origin": "https://evil.example"}, {"Sec-Fetch-Site": "cross-site"}):
            self.assertEqual(self.request(headers=headers)[0], 403)
        for token in ("", "short", None):
            with patch.object(app, "options", return_value={"playlist_sync_token": token}):
                self.assertEqual(self.request()[0], 403)
        self.assertEqual(self.request(headers={"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(app.LIBRARY_FILE.read_bytes(), before)

    def test_key_cannot_access_other_routes_or_bypass_ingress(self):
        for path in ("/", "/api/apple-library", "/api/radio_power", "/api/apple_music",
                     "/api/playlist-sync?alias=1", "/other/api/playlist-sync"):
            self.assertEqual(self.request(path=path)[0], 403)
        self.assertEqual(self.request(method="GET")[0], 403)

    def test_duplicate_authorization_and_oversized_body_rejected(self):
        client = HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        self.addCleanup(client.close)
        client.putrequest("POST", "/api/playlist-sync")
        client.putheader("Authorization", "Bearer " + self.key)
        client.putheader("Authorization", "Bearer " + self.key)
        client.putheader("Content-Length", "2")
        client.putheader("Content-Type", "application/json")
        client.endheaders(b"{}")
        response = client.getresponse()
        self.assertEqual(response.status, 403)
        response.read()
        self.assertEqual(self.request(headers={"Content-Length": "2097153"})[0], 400)

    def test_damaged_storage_and_disk_failure_do_not_report_success(self):
        app.LIBRARY_FILE.write_text("broken")
        self.assertEqual(self.request()[0], 400)
        self.assertEqual(app.LIBRARY_FILE.read_text(), "broken")
        app.LIBRARY_FILE.write_text(json.dumps(self.items))
        with patch.object(app, "write_durable_json", side_effect=OSError("secret details")):
            status, result = self.request()
        self.assertEqual(status, 502)
        self.assertNotIn("secret", result["error"])

    def test_helper_posts_unicode_to_real_server_and_preserves_on_failed_export(self):
        config = {"url": self.url, "token": self.key, "playlists": [{"music": "Mac Mix", "ha_music": "Mix"}]}
        with patch.object(helper, "music_tracks", return_value=self.tracks):
            self.assertEqual(helper.sync(config), 0)
        before = app.LIBRARY_FILE.read_bytes()
        for failure in (subprocess.CalledProcessError(1, "osascript"), subprocess.TimeoutExpired("osascript", 120)):
            with patch.object(helper, "music_tracks", side_effect=failure):
                self.assertEqual(helper.sync(config), 1)
            self.assertEqual(app.LIBRARY_FILE.read_bytes(), before)
        with patch.object(helper, "music_tracks", return_value=[]):
            self.assertEqual(helper.sync(config), 1)
        self.assertEqual(app.LIBRARY_FILE.read_bytes(), before)
        config["playlists"][0]["allow_empty"] = True
        with patch.object(helper, "music_tracks", return_value=[]):
            self.assertEqual(helper.sync(config), 0)
        self.assertEqual(app.library_snapshot()["items"][0]["tracks"], [])

    def test_helper_continues_after_one_playlist_failure(self):
        config = {"url": self.url, "token": self.key, "playlists": [
            {"music": "Missing", "ha_music": "Unknown"}, {"music": "Mac Mix", "ha_music": "Mix"}]}
        with patch.object(helper, "music_tracks", return_value=self.tracks):
            self.assertEqual(helper.sync(config), 1)
        self.assertEqual(len(app.library_snapshot()["items"][0]["tracks"]), 3)

    def test_helper_validates_urls_and_never_follows_redirects(self):
        for url in ("http://public.example:8099", "http://user:key@homeassistant.local:8099", "file:///tmp/a", "https://host/path", "http://127.0.0.1/?token=secret"):
            with self.assertRaises(ValueError):
                helper.endpoint(url)
        self.assertEqual(helper.endpoint("https://host:8099/"), "https://host:8099/api/playlist-sync")
        self.assertIsNone(helper.NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil.example"))

    def test_music_reader_escapes_names_and_rejects_bad_metadata(self):
        name = 'Quotes " and \\ with Grüße'
        result = subprocess.CompletedProcess([], 0, json.dumps(self.tracks), "")
        with patch.object(helper.subprocess, "run", return_value=result) as run:
            self.assertEqual(helper.music_tracks(name), self.tracks)
        self.assertIn(json.dumps(name), run.call_args.kwargs["input"])
        self.assertEqual(run.call_args.args[0], ["/usr/bin/osascript", "-l", "JavaScript", "-"])
        for tracks in ([{"name": "Title", "artist": ""}], {"name": "not a list"}):
            result.stdout = json.dumps(tracks)
            with patch.object(helper.subprocess, "run", return_value=result), self.assertRaises(ValueError):
                helper.music_tracks("Mix")

    def test_setup_creates_schedule_only_after_successful_sync(self):
        folder = app.LIBRARY_FILE.parent / "mac-helper"
        config = folder / "config.json"
        home = app.LIBRARY_FILE.parent / "home"
        home.mkdir()
        with patch.object(helper, "FOLDER", folder), patch.object(helper, "CONFIG", config), \
             patch.object(helper, "HOME", home), patch.object(helper.sys, "platform", "darwin"), \
             patch.object(helper.os, "getuid", return_value=501, create=True), \
             patch.object(helper.secrets, "token_urlsafe", return_value=self.key), \
             patch.object(helper.subprocess, "run") as run, patch.object(helper, "sync", return_value=1) as sync:
            answers = [self.url, "Mac Mix", "Mix", "", ""]
            with patch("builtins.input", side_effect=answers):
                self.assertEqual(helper.setup(), 1)
            self.assertFalse((home / "Library/LaunchAgents").exists())
            run.assert_not_called()
            sync.return_value = 0
            with patch("builtins.input", side_effect=answers):
                self.assertEqual(helper.setup(), 0)
            self.assertEqual(json.loads(config.read_text())["token"], self.key)
            plist = home / "Library/LaunchAgents" / (helper.LABEL + ".plist")
            import plistlib
            job = plistlib.loads(plist.read_bytes())
            self.assertEqual(job["StartInterval"], 1800)
            self.assertTrue(job["RunAtLoad"])
            self.assertEqual(job["ProgramArguments"], [sys.executable, str(folder / "mac_playlist_sync.py")])
            self.assertNotIn(self.key, plist.read_text())
            self.assertEqual(run.call_args.args[0][:3], ["/bin/launchctl", "bootstrap", "gui/501"])


if __name__ == "__main__":
    unittest.main()
