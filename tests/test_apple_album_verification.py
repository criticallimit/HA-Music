"""Conservative Apple Music album verification tests."""
import sys
import unittest
import json
from unittest.mock import patch, MagicMock
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ha_music"))
import app


class AppleAlbumVerificationTests(unittest.TestCase):
    def setUp(self):
        self.album = {"kind": "Album", "name": "Gaudi"}

    def test_pending_does_not_claim_confirmation(self):
        self.assertEqual(app.verify_apple_album(self.album, {"state": "playing", "album": "Gaudi"}, 2)["status"], "pending")

    def test_confirmed_album(self):
        self.assertEqual(app.verify_apple_album(self.album, {"state": "playing", "album": "Gaudi"}, 25)["status"], "confirmed")

    def test_wrong_album_is_detected(self):
        self.assertEqual(app.verify_apple_album(self.album, {"state": "playing", "album": "Eye in the Sky"}, 25)["status"], "mismatch")

    def test_missing_information_is_not_false_mismatch(self):
        for details in (None, {"state": "idle"}, {"state": "playing", "album": None}):
            self.assertEqual(app.verify_apple_album(self.album, details, 25)["status"], "unverifiable")

    def test_playlists_not_checked_as_albums(self):
        self.assertIsNone(app.verify_apple_album({"kind": "Playlist", "name": "Gaudi"}, {"state": "playing", "album": "X"}, 25))

    def test_track_dialog_cover_belongs_to_verified_collection(self):
        favorite = {"id":"a", "kind":"Album", "name":"Gaudi", "album_id":12}
        records = [{"wrapperType":"collection", "collectionId":12, "artistName":"Singer",
                    "artworkUrl100":"https://is1.mzstatic.com/cover.jpg"},
                   {"wrapperType":"track", "collectionId":12, "trackId":9,
                    "trackName":"Song", "artistName":"Singer", "trackNumber":1}]
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({"results":records}).encode()
        with patch.object(app, "apple_music_selection", return_value={"items":[favorite]}), \
             patch.object(app, "READY", True), patch.object(app, "PREPARING", False), \
             patch.object(app.STANDBY, "is_set", return_value=False), \
             patch.object(app, "check_generation"), patch.object(app, "urlopen", return_value=response), \
             patch.object(app, "stored_album", return_value=None):
            result = app.album_tracks("a")
            self.assertEqual(result["image"], "https://is1.mzstatic.com/cover.jpg")
            self.assertEqual(result["tracks"][0]["id"], 9)
            records[0]["artworkUrl100"] = "https://untrusted.test/cover.jpg"
            response.__enter__.return_value.read.return_value = json.dumps({"results":records}).encode()
            self.assertEqual(app.album_tracks("a")["image"], "")
            with patch.object(app, "stored_album", return_value={"image":"api/album-art/12"}):
                self.assertEqual(app.album_tracks("a")["image"], "api/album-art/12")


if __name__ == "__main__":
    unittest.main()
