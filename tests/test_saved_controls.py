"""Saved room intent and cancellation-safe single-song completion."""
import json
import plistlib
import threading
import time
import unittest
from unittest.mock import patch

import test_runtime as fixtures
app = fixtures.app


class SavedControlsTests(unittest.TestCase):
    setUp = fixtures.RuntimeTests.setUp

    def test_one_room_command_never_changes_other_rooms_and_survives_restart(self):
        app.READY = True
        initial = {"media_player.wohnung": .2, "media_player.bad": .5,
                   "media_player.kueche": .35, "media_player.wohnzimmer": .25}
        app.save_speaker_levels(initial)
        devices = {"groups": [{"entity_id": "media_player.wohnung", "volume": .5}],
                   "players": [{"entity_id": entity, "volume": .5} for entity in initial if entity != "media_player.wohnung"]}
        with patch.object(app, "classify_devices", return_value=devices), patch.object(app, "ha_request", return_value={}) as request:
            app.perform("volume", {"entity_id": "media_player.bad", "volume": .1})
            request.assert_called_once_with("/services/media_player/volume_set", {"entity_id": "media_player.bad", "volume_level": .1})
            expected = {**initial, "media_player.bad": .1}
            for external in (.1, .5, 0, .8):
                for player in devices["players"]:
                    player["volume"] = external
                app.ROOM_TARGETS.clear()
                app.ROOM_REMEMBERED.clear()
                app.RECOVERED_SESSION = True
                self.assertEqual({entity: app.displayed_speaker_levels()[entity] for entity in initial}, expected)
                self.assertEqual(app.speaker_levels(), expected)
                self.assertEqual(app.remembered()["media_player.bad"], .1)
            self.assertEqual(request.call_count, 1)

    def test_slider_mute_remembers_own_percentage_durably_despite_external_group_volume(self):
        app.READY = True
        app.save_speaker_levels({"media_player.wohnung": .2, "media_player.bad": .13})
        devices = {"groups": [], "players": [{"entity_id": "media_player.bad", "volume": .5}]}
        with patch.object(app, "classify_devices", return_value=devices), patch.object(app, "ha_request", return_value={}) as request:
            app.perform("volume", {"entity_id": "media_player.bad", "volume": 0})
            app.ROOM_TARGETS.clear()
            app.ROOM_REMEMBERED.clear()
            self.assertEqual(app.remembered()["media_player.bad"], .13)
            result = app.perform("room_audio", {"entity_id": "media_player.bad", "on": True})
            self.assertEqual(result["volume"], .13)
            self.assertEqual([call.args[1]["volume_level"] for call in request.call_args_list], [0, .13])

    def test_legacy_active_marker_migrates_once_and_new_100_percent_is_real(self):
        app.SPEAKER_FILE.write_text(json.dumps({"media_player.wohnung": .2, "media_player.bad": 1,
                                               "media_player.kueche": 0, "media_player.wohnzimmer": .17}))
        self.assertEqual(app.speaker_levels()["media_player.bad"], .2)
        self.assertEqual(app.speaker_levels()["media_player.wohnzimmer"], .17)
        app.save_speaker_levels({"media_player.bad": 1})
        self.assertEqual(app.speaker_levels()["media_player.bad"], 1)
        self.assertEqual(json.loads(app.SPEAKER_FILE.read_text())["_version"], 2)

    def test_master_change_and_mute_persist_latest_room_unmute_value(self):
        app.READY = True
        app.save_speaker_levels({"media_player.wohnung": .2, "media_player.bad": .13, "media_player.kueche": 0})
        app.save_remembered("media_player.bad", .13)
        devices = {"groups": [{"entity_id": "media_player.wohnung", "volume": .5}],
                   "players": [{"entity_id": "media_player.bad", "volume": .5}, {"entity_id": "media_player.kueche", "volume": .5}]}
        with patch.object(app, "classify_devices", return_value=devices), patch.object(app, "ha_request", return_value={}) as request:
            app.perform("volume", {"entity_id": "media_player.wohnung", "volume": .6})
            app.perform("volume", {"entity_id": "media_player.wohnung", "volume": 0})
            app.ROOM_TARGETS.clear()
            app.ROOM_REMEMBERED.clear()
            self.assertEqual(app.remembered()["media_player.bad"], .6)
            self.assertEqual(app.displayed_speaker_levels()["media_player.bad"], 0)
            result = app.perform("room_audio", {"entity_id": "media_player.bad", "on": True})
            self.assertEqual(result["volume"], .6)
            self.assertEqual([call.args[1] for call in request.call_args_list], [
                {"entity_id": "media_player.bad", "volume_level": .6},
                {"entity_id": "media_player.bad", "volume_level": 0},
                {"entity_id": "media_player.bad", "volume_level": .6}])
            self.assertEqual(app.speaker_levels()["media_player.kueche"], 0)

    def test_duration_metadata_is_optional_validated_and_survives_xml_import(self):
        xml = plistlib.dumps({"Tracks": {"1": {"Name": "Song", "Artist": "Band", "Total Time": 243500}},
                              "Playlists": [{"Name": "Mix", "Playlist Items": [{"Track ID": 1}]}]}).decode()
        imported = app.import_playlist({"content": xml})["tracks"]
        self.assertEqual(imported[0]["duration"], 243.5)
        self.assertEqual(app.normalize_playlist_tracks(imported), imported)
        self.assertNotIn("duration", app.normalize_playlist_tracks([{"name": "Song", "artist": "Band"}])[0])
        for invalid in (0, -1, 86401, True, "243", float("inf"), float("nan")):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                app.normalize_playlist_tracks([{"name": "Song", "artist": "Band", "duration": invalid}])


class SingleTrackTests(unittest.TestCase):
    setUp = fixtures.RuntimeTests.setUp

    def session(self, duration=None):
        track = {"name": "Song", "artist": "Band"}
        if duration is not None:
            track["duration"] = duration
        return {"track": track, "target": "media_player.wohnzimmer", "generation": 10,
                "started": 100, "cancel": threading.Event()}

    def snapshot(self, **attributes):
        return {"media_player.wohnzimmer": {"state": "playing", "attributes": {
            "media_title": "Song", "media_artist": "Band", **attributes}}}

    def test_imported_duration_stops_without_needing_next_title(self):
        session = self.session(10)
        states = self.snapshot()
        for now in (100, 101, 103, 109.9):
            self.assertFalse(app.single_track_step(session, states, now))
        self.assertTrue(app.single_track_step(session, states, 110))

    def test_reported_position_timestamp_uses_remaining_time_not_whole_duration(self):
        session = self.session(100)
        states = self.snapshot(media_duration=240, media_position=30, media_position_updated_at="1970-01-01T00:16:30+00:00")
        with patch.object(app.time, "time", return_value=1000):
            self.assertFalse(app.single_track_step(session, states, 100))
        self.assertEqual(session["deadline"], 300)
        self.assertTrue(app.single_track_step(session, states, 300))

    def test_pause_freezes_remaining_time_and_resume_does_not_count_paused_time(self):
        session = self.session(10)
        states = self.snapshot(media_position=0)
        self.assertFalse(app.single_track_step(session, states, 100))
        states[session["target"]]["state"] = "paused"
        self.assertFalse(app.single_track_step(session, states, 103))
        self.assertFalse(app.single_track_step(session, states, 200))
        states[session["target"]]["state"] = "playing"
        self.assertFalse(app.single_track_step(session, states, 200))
        self.assertEqual(session["deadline"], 207)
        self.assertFalse(app.single_track_step(session, states, 206))
        self.assertTrue(app.single_track_step(session, states, 207))

    def test_seek_to_zero_updates_timer(self):
        session = self.session(10)
        states = self.snapshot(media_position=5)
        self.assertFalse(app.single_track_step(session, states, 100))
        states[session["target"]]["attributes"]["media_position"] = 0
        self.assertFalse(app.single_track_step(session, states, 102))
        self.assertEqual(session["deadline"], 112)
        self.assertTrue(app.single_track_step(session, states, 112))

    def test_repeated_stale_position_with_new_timestamps_cannot_extend_song_forever(self):
        session = self.session(10)
        for now in (100, 101, 105, 109):
            states = self.snapshot(media_duration=float("nan"), media_position=0,
                                   media_position_updated_at=f"1970-01-01T00:01:{now-60:02d}+00:00")
            with patch.object(app.time, "time", return_value=now):
                self.assertFalse(app.single_track_step(session, states, now))
        self.assertEqual(session["deadline"], 110)
        self.assertTrue(app.single_track_step(session, states, 110))

    def test_lost_metadata_after_confirmation_times_out_instead_of_following_music_forever(self):
        session = self.session()
        self.assertFalse(app.single_track_step(session, self.snapshot(), 100))
        self.assertFalse(app.single_track_step(session, {}, 159))
        self.assertTrue(app.single_track_step(session, {}, 160))

    def test_without_duration_next_title_or_idle_ends_single_track(self):
        for ended in ("another", "idle"):
            with self.subTest(ended=ended):
                session = self.session()
                states = self.snapshot()
                self.assertFalse(app.single_track_step(session, states, 100))
                if ended == "idle":
                    states[session["target"]]["state"] = "idle"
                else:
                    states[session["target"]]["attributes"]["media_title"] = "Automatic next song"
                self.assertTrue(app.single_track_step(session, states, 105))

    def test_old_or_wrong_metadata_is_allowed_to_settle_but_not_play_forever(self):
        for attributes in ({"media_title": "Old song"}, {"media_artist": "Wrong band"}, {"media_title": ""}):
            session = self.session(10)
            states = self.snapshot(**attributes)
            self.assertFalse(app.single_track_step(session, states, 100))
            self.assertFalse(app.single_track_step(session, states, 159))
            self.assertTrue(app.single_track_step(session, states, 160))
            self.assertFalse(session.get("confirmed", False))

    def test_group_metadata_is_bound_once_so_stale_room_title_cannot_hide_next_song(self):
        session = self.session()
        states = self.snapshot()
        states["media_player.wohnung"] = {"state": "playing", "attributes": {"media_title": "Song", "media_artist": "Band"}}
        self.assertFalse(app.single_track_step(session, states, 100))
        self.assertEqual(session["stop_target"], "media_player.wohnung")
        states["media_player.wohnung"]["attributes"]["media_title"] = "Next"
        self.assertTrue(app.single_track_step(session, states, 101))

    def test_unrelated_group_changes_do_not_stop_solo_track(self):
        session = self.session(10)
        states = self.snapshot()
        states["media_player.wohnung"] = {"state": "playing", "attributes": {"media_title": "Different"}}
        self.assertFalse(app.single_track_step(session, states, 100))
        states["media_player.wohnung"]["attributes"]["media_title"] = "Another"
        self.assertFalse(app.single_track_step(session, states, 101))
        self.assertEqual(session["stop_target"], session["target"])

    def test_end_worker_sends_only_one_pause_for_both_alexa_integrations(self):
        app.READY = True
        for integration in ("alexa_media", "alexa_devices"):
            session = self.session(10)
            session["confirmed"] = True
            app.SINGLE_TRACK = session
            app.INVENTORY_CACHE = (time.monotonic(), {integration: [session["target"]]})
            device = "b" * 32
            replies = [json.dumps({"domain": "alexa_devices", "device_id": device}), {}] if integration == "alexa_devices" else [{}]
            with patch.object(app, "single_track_step", return_value=True), patch.object(app, "state_snapshot", return_value={}), patch.object(app, "ha_request", side_effect=replies) as request:
                app.single_track_worker(session)
            if integration == "alexa_devices":
                request.assert_called_with("/services/alexa_devices/send_text_command", {"device_id": device, "text_command": "pause"})
            else:
                request.assert_called_once_with("/services/media_player/play_media", {"entity_id": session["target"],
                    "media": {"media_content_type": "custom", "media_content_id": "pause", "metadata": {}}})
            self.assertIsNone(app.SINGLE_TRACK)

    def test_cancel_after_metadata_read_cannot_pause_new_playback(self):
        app.READY = True
        session = self.session(10)
        app.SINGLE_TRACK = session
        def completed(*args):
            app.cancel_single_track()
            return True
        with patch.object(app, "single_track_step", side_effect=completed), patch.object(app, "state_snapshot", return_value={}), patch.object(app, "play_on_target") as play:
            app.single_track_worker(session)
        play.assert_not_called()
        self.assertTrue(session["cancel"].is_set())

    def test_supported_group_pause_targets_only_the_confirmed_group(self):
        app.READY = True
        session = self.session(10)
        states = {"media_player.wohnung": {"state": "playing", "attributes": {
            "media_title": "Song", "media_artist": "Band", "media_position": 10, "supported_features": 1}}}
        app.SINGLE_TRACK = session
        with patch.object(app, "state_snapshot", return_value=states), patch.object(app, "ha_request", return_value={}) as request:
            app.single_track_worker(session)
        request.assert_called_once_with("/services/media_player/media_pause", {"entity_id": "media_player.wohnung"})
        self.assertIsNone(app.SINGLE_TRACK)

    def test_new_playlist_and_radio_commands_cancel_the_previous_end_guard(self):
        app.READY = True
        favorite = fixtures.RuntimeTests.apple_favorite(self)
        for action, body in (("apple_music", {"favorite": favorite}), ("radio_direct", {"station": "wdr2"})):
            old = self.session(10)
            app.SINGLE_TRACK = old
            with patch.object(app, "state_snapshot", return_value=self.states), patch.object(app, "play_on_target", return_value={}):
                app.perform(action, body)
            self.assertTrue(old["cancel"].is_set())
            self.assertIsNone(app.SINGLE_TRACK)

    def test_start_replaces_old_guard_and_power_off_cancels_it(self):
        old = self.session(10)
        app.SINGLE_TRACK = old
        with patch.object(app.threading, "Thread") as thread:
            fixtures.START_SINGLE_TRACK({"name": "Next", "artist": "Band", "duration": 30}, old["target"], 10)
            current = app.SINGLE_TRACK
            self.assertTrue(old["cancel"].is_set())
            thread.assert_called_once_with(target=app.single_track_worker, args=(current,), daemon=True)
            app.transition_power(False)
            self.assertTrue(current["cancel"].is_set())
            self.assertIsNone(app.SINGLE_TRACK)

    def test_three_monitor_errors_clear_guard_and_report_failure(self):
        app.READY = True
        session = self.session(10)
        app.SINGLE_TRACK = session
        with patch.object(app, "state_snapshot", side_effect=OSError("offline")), patch.object(session["cancel"], "wait", return_value=False):
            app.single_track_worker(session)
        self.assertIsNone(app.SINGLE_TRACK)
        self.assertIn("nicht überwacht", app.SOURCE_RESTORE_ERROR)


if __name__ == "__main__":
    unittest.main()
