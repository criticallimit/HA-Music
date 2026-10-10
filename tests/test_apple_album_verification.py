"""Conservative Apple Music album verification tests."""
import sys
import unittest
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


if __name__ == "__main__":
    unittest.main()
