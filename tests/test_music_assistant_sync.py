"""Unit tests for read-only Music Assistant synchronization."""
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ha_music"))
from music_assistant_sync import load_imported, sync_music_assistant


class MusicAssistantImportTests(unittest.TestCase):
    def test_apple_filter_pagination_and_duplicates(self):
        calls = []
        def reply(url, token, command, offset):
            calls.append((command, offset))
            if command.endswith("playlists/library_items"):
                return [
                    {"name": "Dirk", "provider_mappings": [{"provider_domain": "apple_music"}]},
                    {"name": "Unrelated", "provider_mappings": [{"provider_domain": "spotify"}]},
                    {"name": "Dirk", "provider_mappings": [{"provider_domain": "apple_music"}]},
                ]
            return [{"name": "Gaudi", "provider": "apple_music"}]
        with patch("music_assistant_sync._request", side_effect=reply):
            data = sync_music_assistant("http://192.168.1.2:8095", "secret")
        self.assertEqual(data, [
            {"kind": "Playlist", "name": "Dirk", "search": "Dirk"},
            {"kind": "Album", "name": "Gaudi", "search": "Gaudi"},
        ])
        self.assertEqual(len(calls), 2)

    def test_invalid_urls_are_rejected_before_network(self):
        for url in ("file:///etc/passwd", "http://user:pass@host", "http://host/path",
                    "http://host?x=1", "http://host/#x"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                sync_music_assistant(url, "secret")

    def test_api_error_does_not_update_import_file(self):
        with patch("music_assistant_sync._request", side_effect=RuntimeError("offline")):
            with self.assertRaises(RuntimeError):
                sync_music_assistant("http://localhost:8095", "secret")

    def test_import_cache_validation(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / "library.json"
            self.assertEqual(load_imported(path), [])
            path.write_text('[{"kind":"Playlist","name":"Dirk","search":"Dirk"}]')
            self.assertEqual(load_imported(path)[0]["name"], "Dirk")
            path.write_text('{"incorrect":true}')
            with self.assertRaises(ValueError):
                load_imported(path)


if __name__ == "__main__":
    unittest.main()
