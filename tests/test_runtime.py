"""Regression tests for lifecycle, delayed operations and stream cancellation."""
from contextlib import ExitStack
import io
import json
from http.client import HTTPResponse
from email.message import Message
import socket
import time
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ha_music"))
import app
import metadata
import metadata_feed
VERIFY = app.verify_restored_volumes
SYNCHRONIZE = app.synchronize_device_configuration


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        folder = Path(self.stack.enter_context(tempfile.TemporaryDirectory(dir=ROOT)))
        for name in ("VOLUME_FILE", "STATION_FILE", "SPEAKER_FILE", "VIEW_FILE", "SESSION_FILE", "SOURCE_FILE", "OPTIONS"):
            self.stack.enter_context(patch.object(app, name, folder / (name + ".json")))
        self.stack.enter_context(patch.object(app, "SUPERVISOR_OPTIONS", None))
        self.stack.enter_context(patch.object(app, "ACTIVE_APPLE", None))
        self.stack.enter_context(patch.object(app, "RECOVERING", False))
        self.stack.enter_context(patch.object(app, "RECOVERED_SESSION", False))
        self.stack.enter_context(patch.object(app, "SOURCE_UNCONFIRMED", False))
        self.stack.enter_context(patch.object(app, "SOURCE_RESTORE_ERROR", None))
        self.stack.enter_context(patch.object(app, "RECOVERY_MESSAGE", None))
        self.stack.enter_context(patch.dict(app.REGISTERED_DEVICE_NAMES, {}, clear=True))
        app.OPTIONS.write_text(json.dumps({"devices": [{"entity_id": entity, "name": entity, "enabled": True} for entity in ("media_player.wohnung", "media_player.wohnzimmer", "media_player.kueche", "media_player.bad", "media_player.buero")]}))
        self.stack.enter_context(patch.object(app, "synchronize_device_configuration"))
        self.monitor = self.stack.enter_context(patch.object(app, "MONITOR"))
        self.monitor.snapshot.return_value = (0, "", None)
        self.timer = self.stack.enter_context(patch.object(app.threading, "Timer"))
        self.stack.enter_context(patch.object(app, "urlopen", side_effect=AssertionError("Unexpected network")))
        self.stack.enter_context(patch.object(app, "verify_restored_volumes"))
        app.CANCEL.set()
        app.CANCEL = threading.Event()
        app.STANDBY.clear()
        app.RESTORE_GENERATION = 10
        app.LAST_POWER = "on"
        app.READY = False
        app.PREPARING = False
        self.stack.enter_context(patch.object(app, "INVENTORY_CACHE", (time.monotonic(), {"alexa_media":list(app.enabled_device_ids()), "alexa_devices":[]})))
        app.ROOM_TARGETS.clear()
        app.VOLUME_CONFIRMATION.clear()
        app.STARTUP_ERROR = None
        app.STARTED_AT = None
        app.STATE_CACHE = (0, None)
        self.states = {
            app.RADIO_SWITCH: {"state": "on"},
            "media_player.wohnung": {"state": "idle", "attributes": {"volume_level": 0.4}},
            "media_player.wohnzimmer": {"state": "idle", "attributes": {"volume_level": 0.4}},
            "media_player.kueche": {"state": "idle", "attributes": {"volume_level": 0}},
        }

    def startup(self, request=None):
        with patch.object(app, "wait_for_start", return_value=True), \
             patch.object(app, "state_snapshot", return_value=self.states), \
             patch.object(app, "allowed_entities", return_value={entity for entity in self.states if entity.startswith("media_player.")}), \
             patch.object(app, "ha_request", side_effect=request or (lambda *args: {})) as calls:
            app.radio_start_sequence(app.RESTORE_GENERATION)
            return calls

    def test_track_commands_target_live_group_and_never_change_volume(self):
        app.READY = True
        self.states["media_player.wohnung"] = {"state":"playing", "attributes":{
            "supported_features":16 | 32 | 32768, "shuffle":False}}
        for command, service in (("previous","media_previous_track"), ("next","media_next_track"), ("shuffle","shuffle_set")):
            with self.subTest(command=command), patch.object(app, "state_snapshot", return_value=self.states) as snapshot, \
                 patch.object(app, "allowed_entities", return_value=app.enabled_device_ids()), patch.object(app, "ha_request", return_value={}) as request:
                body = {"command":command, "entity_id":"media_player.wohnung"}
                if command == "shuffle":
                    body["shuffle"] = True
                app.perform("track_transport", body)
                expected = {"entity_id":"media_player.wohnung"}
                if command == "shuffle":
                    expected["shuffle"] = True
                self.assertEqual(request.call_args_list[0].args, ("/services/media_player/" + service, expected))
                self.assertEqual(request.call_count, 1 if command == "shuffle" else 2)
                if command != "shuffle":
                    self.assertEqual(request.call_args_list[1].args, ("/services/homeassistant/update_entity", {"entity_id":"media_player.wohnung"}))
                snapshot.assert_called_once_with(fresh=True)

    def test_apple_playlist_shuffle_on_and_off_only_changes_playback_order(self):
        app.READY = True
        app.ACTIVE_APPLE = {"target":"media_player.wohnzimmer", "type":"playlist"}
        for playback_state in ("playing", "paused"):
            for current in (False, True):
                self.states["media_player.wohnung"] = {"state":playback_state, "attributes":{
                    "supported_features":32768, "shuffle":current, "media_content_type":"music"}}
                with self.subTest(state=playback_state, shuffle=current), \
                     patch.object(app, "state_snapshot", return_value=self.states), \
                     patch.object(app, "allowed_entities", return_value=app.enabled_device_ids()), \
                     patch.object(app, "ha_request", return_value={}) as request:
                    self.assertEqual(app.track_transport_state(self.states)["shuffle"], current)
                    app.perform("track_transport", {"command":"shuffle", "entity_id":"media_player.wohnung", "shuffle":not current})
                    request.assert_called_once_with("/services/media_player/shuffle_set", {
                        "entity_id":"media_player.wohnung", "shuffle":not current})

    def test_track_capabilities_handle_official_alexa_without_shuffle(self):
        self.states["media_player.wohnung"] = {"state":"paused", "attributes":{"supported_features":16 | 32}}
        result = app.track_transport_state(self.states)
        self.assertTrue(result["can_next"])
        self.assertTrue(result["can_previous"])
        self.assertFalse(result["can_shuffle"])
        self.assertIsNone(result["shuffle"])

    def test_track_transport_falls_back_only_to_configured_controller(self):
        self.states["media_player.wohnzimmer"] = {"state":"playing", "attributes":{"supported_features":32}}
        self.states["media_player.kueche"] = {"state":"playing", "attributes":{"supported_features":16 | 32 | 32768}}
        self.assertEqual(app.track_transport_state(self.states)["entity_id"], "media_player.wohnzimmer")
        self.states["media_player.wohnzimmer"]["state"] = "unavailable"
        self.assertIsNone(app.track_transport_state(self.states)["entity_id"])

    def test_apple_transport_uses_its_configured_target_if_group_unavailable(self):
        app.ACTIVE_APPLE = {"target":"media_player.bad"}
        self.states["media_player.bad"] = {"state":"playing", "attributes":{"supported_features":32}}
        self.assertEqual(app.track_transport_state(self.states)["entity_id"], "media_player.bad")

    def test_live_radio_has_no_track_controls_even_with_static_alexa_features(self):
        app.save_selected_station("wdr2")
        self.states["media_player.wohnung"] = {"state":"playing", "attributes":{"supported_features":16 | 32 | 32768, "shuffle":False}}
        state = app.track_transport_state(self.states)
        self.assertFalse(state["can_next"])
        self.assertFalse(state["can_previous"])
        self.assertFalse(state["can_shuffle"])

    def test_unsupported_or_changed_track_target_cannot_send_service(self):
        app.READY = True
        self.states["media_player.wohnung"] = {"state":"playing", "attributes":{"supported_features":32}}
        for body in ({"command":"next", "entity_id":"media_player.kueche"},
                     {"command":"previous", "entity_id":"media_player.wohnung"},
                     {"command":"shuffle", "entity_id":"media_player.wohnung", "shuffle":True},
                     {"command":"turn_on", "entity_id":"media_player.wohnung"}):
            with self.subTest(body=body), patch.object(app, "state_snapshot", return_value=self.states), \
                 patch.object(app, "allowed_entities", return_value=app.enabled_device_ids()), patch.object(app, "ha_request") as request:
                with self.assertRaises(ValueError):
                    app.perform("track_transport", body)
                request.assert_not_called()

    def test_shuffle_requires_boolean_state_and_boolean_request(self):
        app.READY = True
        self.states["media_player.wohnung"] = {"state":"playing", "attributes":{"supported_features":32768}}
        self.assertFalse(app.track_transport_state(self.states)["can_shuffle"])
        self.states["media_player.wohnung"]["attributes"]["shuffle"] = False
        for value in (None, "true", 1):
            with self.subTest(value=value), patch.object(app, "state_snapshot", return_value=self.states), \
                 patch.object(app, "allowed_entities", return_value=app.enabled_device_ids()), patch.object(app, "ha_request") as request:
                with self.assertRaises(ValueError):
                    app.perform("track_transport", {"command":"shuffle", "entity_id":"media_player.wohnung", "shuffle":value})
                request.assert_not_called()

    def test_track_commands_cannot_wake_standby_or_run_during_preparation(self):
        for standby, ready, preparing in ((True,True,False), (False,False,False), (False,True,True)):
            app.READY, app.PREPARING = ready, preparing
            if standby:
                app.STANDBY.set()
            else:
                app.STANDBY.clear()
            with patch.object(app, "ha_request") as request:
                with self.assertRaises((app.StartupCancelled, ValueError)):
                    app.perform("track_transport", {"command":"next", "entity_id":"media_player.wohnung"})
                request.assert_not_called()

    def test_fresh_track_state_read_bypasses_old_cached_capabilities(self):
        app.STATE_CACHE = (time.monotonic(), self.states)
        with patch.object(app, "ha_request", return_value=[]) as request:
            self.assertEqual(app.state_snapshot(fresh=True), {})
        request.assert_called_once_with("/states")

    def recovery(self, replies=None):
        app.STANDBY.set()
        app.LAST_POWER = "off"
        app.RECOVERING = True
        raw = [{"entity_id": entity, **state} for entity, state in self.states.items()]
        cancellation = Mock()
        cancellation.is_set.return_value = False
        cancellation.wait.return_value = False
        with patch.object(app, "ha_request", side_effect=replies or [raw, raw]) as request:
            app.recover_session(10, cancellation)
        return request

    def test_upgrade_reattaches_radio_without_any_device_or_helper_command(self):
        self.states["media_player.wohnzimmer"]["state"] = "playing"
        app.save_selected_station("wdr2")
        app.save_speaker_levels({"media_player.wohnzimmer": 0.8})
        request = self.recovery()
        self.assertEqual(request.call_count, 2)
        for call in request.call_args_list:
            self.assertEqual(call.args, ("/states",))
            self.assertEqual(call.kwargs, {"recovery_read": True})
        self.assertTrue(app.READY)
        self.assertFalse(app.STANDBY.is_set())
        self.assertTrue(app.RECOVERED_SESSION)
        self.assertEqual(app.session_intent(), "on")
        self.assertEqual(app.speaker_levels()["media_player.wohnzimmer"], 0.8)
        self.assertNotIn("media_player.wohnzimmer", app.displayed_speaker_levels())
        self.monitor.assert_not_called()
        self.assertEqual(self.monitor.method_calls, [("resume", (), {})])
        with patch.object(app, "state_snapshot", return_value=self.states):
            self.assertEqual(app.radio_state()["last_station"], "")

    def test_update_reattaches_already_on_ready_idle_devices_without_startup(self):
        self.states[app.RADIO_READY] = {"state":"on"}
        with patch.object(app, "radio_start_sequence") as startup:
            request = self.recovery()
        self.assertEqual(request.call_count, 2)
        self.assertTrue(app.READY)
        self.assertEqual(app.LAST_POWER, "on")
        self.assertFalse(app.STANDBY.is_set())
        self.assertTrue(all(call.args == ("/states",) for call in request.call_args_list))
        startup.assert_not_called()

    def test_already_on_without_ready_or_playback_never_runs_startup(self):
        self.states[app.RADIO_READY] = {"state":"off"}
        raw = [{"entity_id": entity, **state} for entity, state in self.states.items()]
        with patch.object(app, "radio_start_sequence") as startup:
            request = self.recovery([raw] * 12)
        self.assertFalse(app.READY)
        self.assertTrue(app.STANDBY.is_set())
        self.assertTrue(all(call.args == ("/states",) for call in request.call_args_list))
        startup.assert_not_called()

    def test_stale_ready_without_available_device_does_not_unlock_ui(self):
        self.states[app.RADIO_READY] = {"state":"on"}
        for entity in app.enabled_device_ids():
            self.states[entity] = {"state":"unavailable"}
        raw = [{"entity_id": entity, **state} for entity, state in self.states.items()]
        request = self.recovery([raw] * 12)
        self.assertEqual(request.call_count, 12)
        self.assertFalse(app.READY)
        self.assertTrue(app.STANDBY.is_set())

    def test_ready_idle_recovery_requires_two_consistent_observations(self):
        ready = [{"entity_id": entity, **state} for entity, state in self.states.items()] + [{"entity_id":app.RADIO_READY, "state":"on"}]
        not_ready = [{"entity_id": entity, **state} for entity, state in self.states.items()] + [{"entity_id":app.RADIO_READY, "state":"off"}]
        request = self.recovery([ready, not_ready, ready, ready])
        self.assertEqual(request.call_count, 4)
        self.assertTrue(app.READY)

    def test_switch_off_wins_over_ready_idle_devices(self):
        self.states[app.RADIO_SWITCH] = {"state":"off"}
        self.states[app.RADIO_READY] = {"state":"on"}
        request = self.recovery()
        self.assertEqual(request.call_count, 1)
        self.assertTrue(app.STANDBY.is_set())
        self.assertFalse(app.READY)

    def test_power_on_during_update_check_cannot_launch_startup_or_cancel_recovery(self):
        app.RECOVERING = True
        app.STANDBY.set()
        app.LAST_POWER = "off"
        with patch.object(app, "ha_request") as request, patch.object(app, "radio_start_sequence") as startup:
            with self.assertRaisesRegex(ValueError, "keine Startsequenz"):
                app.power_command(True)
        request.assert_not_called()
        startup.assert_not_called()
        self.assertTrue(app.RECOVERING)
        self.assertTrue(app.STANDBY.is_set())
        self.assertEqual(app.RESTORE_GENERATION, 10)
        self.assertIsNone(app.session_intent())

    def test_apple_metadata_comes_entirely_from_track_control_group(self):
        app.ACTIVE_APPLE = {"target":"media_player.wohnzimmer"}
        self.states["media_player.wohnzimmer"] = {"state":"playing", "attributes":{
            "media_title":"Alter Titel", "media_artist":"Absolutely Positively", "media_image_url":"/old.jpg"}}
        self.states["media_player.wohnung"] = {"state":"playing", "attributes":{
            "supported_features":16 | 32, "media_title":"Better Together", "media_artist":"Jack Johnson",
            "media_album_name":"In Between Dreams", "media_image_url":"/new.jpg", "entity_picture":"/old-group.jpg"}}
        with patch.object(app, "state_snapshot", return_value=self.states) as read:
            status = app.playback_status()
        read.assert_called_once_with(fresh=True)
        self.assertEqual(status["details"]["entity_id"], status["track_transport"]["entity_id"])
        self.assertEqual(status["details"]["entity_id"], "media_player.wohnung")
        self.assertEqual((status["details"]["title"], status["details"]["artist"], status["details"]["album"], status["details"]["image"]),
                         ("Better Together", "Jack Johnson", "In Between Dreams", "/new.jpg"))

    def test_apple_missing_group_artist_is_not_filled_from_other_room(self):
        app.ACTIVE_APPLE = {"target":"media_player.wohnzimmer"}
        self.states["media_player.wohnzimmer"] = {"state":"playing", "attributes":{"media_artist":"Alter Künstler"}}
        self.states["media_player.wohnung"] = {"state":"playing", "attributes":{"supported_features":32, "media_title":"Aktueller Titel"}}
        with patch.object(app, "state_snapshot", return_value=self.states):
            details = app.playback_status()["details"]
        self.assertEqual(details["title"], "Aktueller Titel")
        self.assertIsNone(details["artist"])
        self.assertIsNone(details["image"])

    def test_apple_metadata_forwards_same_snapshot_artist_without_guessing(self):
        app.ACTIVE_APPLE = {"target":"media_player.wohnzimmer"}
        self.states["media_player.wohnzimmer"] = {"state":"playing", "attributes":{"supported_features":32, "media_title":"Titel A", "media_artist":"Gleicher Künstler"}}
        with patch.object(app, "state_snapshot", return_value=self.states):
            self.assertEqual(app.playback_status()["details"]["artist"], "Gleicher Künstler")
            self.states["media_player.wohnzimmer"]["attributes"]["media_title"] = "Titel B"
            details = app.playback_status()["details"]
        self.assertEqual((details["title"], details["artist"]), ("Titel B", "Gleicher Künstler"))

    def test_metadata_refresh_failure_does_not_repeat_accepted_next_track(self):
        app.READY = True
        self.states["media_player.wohnung"] = {"state":"playing", "attributes":{"supported_features":32}}
        with patch.object(app, "state_snapshot", return_value=self.states), patch.object(app, "allowed_entities", return_value=app.enabled_device_ids()), patch.object(app, "ha_request", side_effect=[{}, OSError("refresh unavailable")]) as request:
            app.perform("track_transport", {"command":"next", "entity_id":"media_player.wohnung"})
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args_list[0].args[0], "/services/media_player/media_next_track")
        self.assertEqual(request.call_args_list[1].args[0], "/services/homeassistant/update_entity")

    def test_apple_session_restores_view_without_claiming_old_radio_or_playlist(self):
        app.save_selected_view("apple")
        app.save_selected_station("wdr2")
        self.states["media_player.wohnzimmer"] = {"state": "playing", "attributes": {
            "media_title": "Aktueller Titel", "media_artist": "Interpret", "volume_level": 0.25}}
        self.recovery()
        with patch.object(app, "state_snapshot", return_value=self.states):
            state = app.radio_state()
            playback = app.playback_status()
        self.assertEqual(state["selected_view"], "apple")
        self.assertEqual(state["last_station"], "")
        self.assertIsNone(state["apple_music"]["active"])
        self.assertEqual(playback["details"]["title"], "Aktueller Titel")

    def test_paused_session_reattaches_without_resuming(self):
        self.states["media_player.wohnung"]["state"] = "paused"
        request = self.recovery()
        self.assertTrue(app.READY)
        self.assertTrue(all(call.args == ("/states",) for call in request.call_args_list))

    def test_explicit_station_selection_after_recovery_restores_radio_metadata(self):
        self.states["media_player.wohnzimmer"]["state"] = "playing"
        self.recovery()
        with patch.object(app, "ha_request", return_value={}), patch.object(app, "state_snapshot", return_value=self.states):
            app.perform("radio_direct", {"station": "wdr2"})
            state = app.radio_state()
        self.assertFalse(state["recovered_session"])
        self.assertEqual(state["last_station"], "wdr2")
        self.monitor.resume.assert_called_once()
        self.monitor.select.assert_called_once_with("wdr2")
        self.assertTrue(app.RECOVERED_SESSION)

    def test_explicit_apple_selection_after_recovery_keeps_active_favorite(self):
        favorite = self.apple_favorite()
        self.states["media_player.wohnzimmer"]["state"] = "playing"
        self.recovery()
        with patch.object(app, "ha_request", return_value={}), patch.object(app, "state_snapshot", return_value=self.states):
            app.perform("apple_music", {"favorite": favorite})
            state = app.radio_state()
        self.assertFalse(state["recovered_session"])
        self.assertEqual(state["apple_music"]["active"]["id"], favorite)

    def test_recovered_playback_does_not_use_idle_devices_old_metadata(self):
        self.states["media_player.wohnzimmer"]["state"] = "playing"
        self.states["media_player.kueche"]["attributes"]["media_title"] = "Alte Playlist"
        self.recovery()
        with patch.object(app, "state_snapshot", return_value=self.states):
            info = app.playback_status()
        self.assertIsNone(info["details"]["title"])
        self.assertEqual(info["details"]["entity_id"], "media_player.wohnzimmer")

    def test_normal_playback_does_not_fill_missing_fields_from_idle_room(self):
        self.states["media_player.wohnzimmer"]["state"] = "playing"
        self.states["media_player.kueche"]["attributes"].update(media_title="Alter Titel", media_artist="Alter Künstler")
        with patch.object(app, "state_snapshot", return_value=self.states):
            info = app.playback_status()
        self.assertTrue(info["playing"])
        self.assertEqual(info["details"]["entity_id"], "media_player.wohnzimmer")
        self.assertIsNone(info["details"]["title"])
        self.assertIsNone(info["details"]["artist"])

    def test_all_idle_players_have_no_current_track(self):
        self.states["media_player.wohnzimmer"]["attributes"]["media_title"] = "Alter Titel"
        with patch.object(app, "state_snapshot", return_value=self.states):
            info = app.playback_status()
        self.assertFalse(info["playing"])
        self.assertIsNone(info["details"])

    def test_known_queue_does_not_display_music_from_unrelated_room(self):
        self.states["media_player.kueche"] = {"state":"playing", "attributes":{
            "media_title":"Andere Musik", "media_artist":"Anderer Künstler"}}
        with patch.object(app, "state_snapshot", return_value=self.states):
            info = app.playback_status()
        self.assertFalse(info["playing"])
        self.assertIsNone(info["details"])

    def test_paused_control_group_is_not_replaced_by_stale_playing_controller(self):
        self.states["media_player.wohnung"] = {"state":"paused", "attributes":{
            "media_title":"Aktuelle Pause", "supported_features":32}}
        self.states["media_player.wohnzimmer"] = {"state":"playing", "attributes":{
            "media_title":"Alte Meldung", "supported_features":32}}
        with patch.object(app, "state_snapshot", return_value=self.states):
            info = app.playback_status()
        self.assertEqual(info["details"]["entity_id"], info["track_transport"]["entity_id"])
        self.assertEqual(info["details"]["title"], "Aktuelle Pause")
        self.assertFalse(info["playing"])

    def test_reattached_transport_uses_configured_controller_not_old_default(self):
        config = json.loads(app.OPTIONS.read_text())
        config["apple_music_target"] = "media_player.bad"
        app.OPTIONS.write_text(json.dumps(config))
        self.states["media_player.bad"] = {"state":"playing", "attributes":{"supported_features":32}}
        self.states["media_player.wohnzimmer"] = {"state":"playing", "attributes":{"supported_features":32}}
        self.recovery()
        with patch.object(app, "state_snapshot", return_value=self.states):
            info = app.playback_status()
        self.assertEqual(info["track_transport"]["entity_id"], "media_player.bad")
        self.assertEqual(info["details"]["entity_id"], "media_player.bad")

    def test_master_after_recovery_does_not_unmute_room_from_stale_saved_level(self):
        self.states["media_player.wohnzimmer"]["state"] = "playing"
        app.save_speaker_levels({"media_player.kueche": 0.8, "media_player.wohnung": 0.4})
        self.recovery()
        found = {"groups": [{"entity_id":"media_player.wohnung", "volume":0.4}], "players": [
            {"entity_id":"media_player.wohnzimmer", "volume":0.4},
            {"entity_id":"media_player.kueche", "volume":0}], "excluded": []}
        with patch.object(app, "classify_devices", return_value=found), patch.object(app, "ha_request", return_value={}) as request:
            app.perform("volume", {"entity_id":"media_player.wohnung", "volume":0.3})
        request.assert_called_once_with("/services/media_player/volume_set", {
            "entity_id":"media_player.wohnzimmer", "volume_level":0.3})

    def test_external_off_immediately_after_recovery_is_not_missed(self):
        self.states["media_player.wohnzimmer"]["state"] = "playing"
        self.recovery()
        self.states[app.RADIO_SWITCH]["state"] = "off"
        with patch.object(app, "state_snapshot", return_value=self.states), patch.object(app, "ha_request", return_value={}), \
             patch.object(app.RADIO_MONITOR_STOP, "wait", side_effect=[False, True]):
            app.radio_switch_monitor()
        self.assertFalse(app.READY)
        self.assertEqual(app.session_intent(), "off")

    def test_saved_off_never_reads_ha_even_if_cached_playback_is_active(self):
        app.save_session_intent(False)
        app.STANDBY.set()
        with patch.object(app, "ha_request") as request, patch.object(app.threading, "Thread") as thread:
            app.start_session_recovery()
        request.assert_not_called()
        thread.assert_not_called()
        self.assertTrue(app.STANDBY.is_set())

    def test_active_or_missing_intent_automatically_schedules_read_only_recovery(self):
        for intent in (None, True):
            with self.subTest(intent=intent):
                if intent:
                    app.save_session_intent(True)
                app.STANDBY.set()
                app.RECOVERING = False
                with patch.object(app.threading, "Thread") as thread:
                    app.start_session_recovery()
                    app.start_session_recovery()
                thread.assert_called_once()
                self.assertEqual(thread.call_args.kwargs["target"], app.recover_session)
                self.assertTrue(app.RECOVERING)

    def test_confirmed_switch_off_wins_over_stale_playing_state(self):
        self.states[app.RADIO_SWITCH]["state"] = "off"
        self.states["media_player.wohnzimmer"]["state"] = "playing"
        request = self.recovery()
        self.assertEqual(request.call_count, 1)
        self.assertEqual(app.session_intent(), "off")
        self.assertTrue(app.STANDBY.is_set())
        self.assertFalse(app.READY)

    def test_unknown_or_disabled_devices_never_unlock_from_saved_ready(self):
        for state, switch in (("unknown", "on"), ("unavailable", "on"), ("playing", "unknown")):
            with self.subTest(state=state, switch=switch):
                self.states[app.RADIO_SWITCH]["state"] = switch
                self.states[app.RADIO_READY] = {"state": "on"}
                for entity in app.enabled_device_ids():
                    self.states[entity] = {"state":state}
                raw = [{"entity_id": entity, **item} for entity, item in self.states.items()]
                self.recovery([raw] * 12)
                self.assertTrue(app.STANDBY.is_set())
                self.assertFalse(app.READY)
                self.assertIsNotNone(app.RECOVERY_MESSAGE)
        self.states[app.RADIO_SWITCH]["state"] = "on"
        for entity in app.enabled_device_ids():
            self.states[entity] = {"state":"unavailable"}
        self.states["media_player.wohnzimmer"]["state"] = "playing"
        with patch.object(app, "enabled_device_ids", return_value={"media_player.bad"}):
            raw = [{"entity_id": entity, **item} for entity, item in self.states.items()]
            self.recovery([raw] * 12)
        self.assertFalse(app.READY)

    def test_recovery_retries_ha_outage_but_never_runs_startup(self):
        self.states["media_player.wohnzimmer"]["state"] = "playing"
        raw = [{"entity_id": entity, **item} for entity, item in self.states.items()]
        with patch.object(app, "radio_start_sequence") as startup:
            self.recovery([OSError("offline"), raw, raw])
        startup.assert_not_called()
        self.assertTrue(app.READY)
        self.assertIsNone(app.RECOVERY_MESSAGE)

    def test_delayed_recovery_reply_cannot_undo_explicit_power_off(self):
        self.states["media_player.wohnzimmer"]["state"] = "playing"
        raw = [{"entity_id": entity, **item} for entity, item in self.states.items()]
        def reply(*args, **kwargs):
            app.power_command(False)
            return raw
        app.STANDBY.set()
        app.RECOVERING = True
        with patch.object(app, "ha_request", side_effect=reply):
            app.recover_session(10, app.CANCEL)
        self.assertEqual(app.session_intent(), "off")
        self.assertTrue(app.STANDBY.is_set())
        self.assertFalse(app.READY)
        self.assertFalse(app.RECOVERING)

    def test_recovery_admission_allows_only_get_states_while_standby(self):
        app.STANDBY.set()
        app.RECOVERING = True
        with patch.object(app, "TOKEN", "test"), patch.object(app, "urlopen") as opened:
            opened.return_value.__enter__.return_value.read.return_value = b"[]"
            self.assertEqual(app.ha_request("/states", recovery_read=True), [])
            self.assertEqual(opened.call_args.args[0].get_method(), "GET")
            for path, payload, supervisor in (("/states", {}, False), ("/states", None, True),
                                               ("/services/switch/turn_on", None, False), ("/template", None, False)):
                with self.assertRaises(ValueError):
                    app.ha_request(path, payload, supervisor=supervisor, recovery_read=True)
            self.assertEqual(opened.call_count, 1)
        app.RECOVERING = False
        with self.assertRaises(ValueError):
            app.ha_request("/states", recovery_read=True)

    def test_failed_intent_write_does_not_open_network_on_power_on(self):
        app.STANDBY.set()
        with patch.object(app, "save_session_intent", side_effect=OSError("disk full")), patch.object(app, "ha_request") as request:
            with self.assertRaises(OSError):
                app.power_command(True)
        self.assertTrue(app.STANDBY.is_set())
        request.assert_not_called()

    def test_full_disk_does_not_prevent_explicit_power_off(self):
        app.READY = True
        with patch.object(app, "save_session_intent", side_effect=OSError("disk full")), patch.object(app, "ha_request", return_value={}) as request:
            app.power_command(False)
        self.assertFalse(app.READY)
        self.assertEqual(app.LAST_POWER, "off")
        request.assert_any_call("/services/switch/turn_off", {"entity_id":app.RADIO_SWITCH})

    def test_invalid_intent_is_unknown_and_normal_power_transitions_persist(self):
        for text in ('{', '[]', '{"version":1,"intent":"invalid"}', '{"version":2,"intent":"on"}'):
            app.SESSION_FILE.write_text(text)
            self.assertIsNone(app.session_intent())
        app.transition_power(True)
        self.assertEqual(app.session_intent(), "on")
        app.transition_power(False)
        self.assertEqual(app.session_intent(), "off")

    def apple_favorite(self, kind="Playlist", group="Wohnung"):
        config = json.loads(app.OPTIONS.read_text())
        config.update(apple_music_group=group, apple_music_favorites=[{"name":"Abendmusik", "kind":kind, "search":"Abendmusik von Dirk"}])
        app.OPTIONS.write_text(json.dumps(config))
        return app.apple_music_selection()["items"][0]["id"]

    def test_apple_playlist_and_album_persist_and_restart_instead_of_old_radio(self):
        for kind in ("Playlist", "Album"):
            with self.subTest(kind=kind):
                app.READY, app.PREPARING = True, False
                app.save_selected_station("wdr2")
                favorite = self.apple_favorite(kind)
                with patch.object(app, "ha_request", return_value={}):
                    app.perform("apple_music", {"favorite":favorite})
                self.assertEqual(app.last_selected_source(), {"kind":"apple", "id":favorite})
                app.transition_power(False)
                self.assertIsNone(app.ACTIVE_APPLE)
                self.assertEqual(app.last_selected_source()["id"], favorite)
                app.transition_power(True)
                requests = self.startup().call_args_list
                playback = [c for c in requests if c.args[0].endswith("play_media")]
                self.assertEqual(len(playback), 1)
                self.assertIn("auf Apple Music", playback[0].args[1]["media"]["media_content_id"])
                self.assertEqual(app.ACTIVE_APPLE["id"], favorite)
                self.assertIsNone(app.SOURCE_RESTORE_ERROR)
                self.assertEqual(app.last_selected_station(), "wdr2")

    def test_normal_radio_start_replaces_saved_apple_browsing_view(self):
        app.save_selected_station("wdr2")
        app.save_selected_view("apple")
        self.startup()
        self.assertEqual(app.selected_view(), "radio")
        self.assertEqual(app.radio_state()["selected_view"], "radio")

    def test_normal_apple_start_replaces_saved_radio_browsing_view(self):
        favorite = self.apple_favorite()
        app.save_selected_source("apple", favorite)
        app.save_selected_view("radio")
        self.startup()
        self.assertEqual(app.selected_view(), "apple")
        self.assertEqual(app.radio_state()["apple_music"]["active"]["id"], favorite)

    def test_accepted_source_selection_persists_corresponding_view(self):
        app.READY = True
        favorite = self.apple_favorite()
        with patch.object(app, "ha_request", return_value={}) as request:
            app.perform("apple_music", {"favorite":favorite})
            self.assertEqual(app.selected_view(), "apple")
            app.perform("radio_direct", {"station":"wdr2"})
            self.assertEqual(app.selected_view(), "radio")
        self.assertEqual(request.call_count, 2)
        self.assertTrue(all(c.args[0].endswith("play_media") for c in request.call_args_list))

    def test_rejected_source_selection_does_not_switch_view(self):
        app.READY = True
        favorite = self.apple_favorite()
        app.save_selected_view("radio")
        with patch.object(app, "ha_request", side_effect=OSError("offline")):
            with self.assertRaises(OSError):
                app.perform("apple_music", {"favorite":favorite})
        self.assertEqual(app.selected_view(), "radio")

    def test_deleted_apple_favorite_never_restarts_old_radio(self):
        app.save_selected_station("wdr2")
        favorite = self.apple_favorite()
        app.save_selected_source("apple", favorite)
        config = json.loads(app.OPTIONS.read_text())
        config["apple_music_favorites"] = []
        app.OPTIONS.write_text(json.dumps(config))
        requests = self.startup().call_args_list
        self.assertFalse(any(c.args[0].endswith("play_media") for c in requests))
        self.assertIn("Unbekannter Apple-Music-Favorit", app.SOURCE_RESTORE_ERROR)
        self.assertEqual(app.last_selected_source()["id"], favorite)

    def test_rejected_apple_restart_is_not_retried_or_replaced_by_radio(self):
        app.save_selected_station("wdr2")
        favorite = self.apple_favorite()
        app.save_selected_source("apple", favorite)
        def request(path, body=None):
            if path.endswith("play_media"):
                raise OSError("offline")
            return {}
        requests = self.startup(request).call_args_list
        self.assertEqual(sum(c.args[0].endswith("play_media") for c in requests), 1)
        self.assertIn("offline", app.SOURCE_RESTORE_ERROR)
        self.assertEqual(app.last_selected_source()["kind"], "apple")
        self.assertTrue(any(c.args[0].endswith("volume_set") and c.args[1]["volume_level"] == .4 for c in requests))

    def test_missing_source_migrates_legacy_radio_but_corruption_never_does(self):
        app.STATION_FILE.write_text(json.dumps({"station":"wdr2"}))
        self.assertEqual(app.last_selected_source(), {"kind":"radio", "id":"wdr2"})
        for value in ("{", "null", '{"version":2,"kind":"radio","id":"wdr2"}', '{"version":1,"kind":"apple","id":"bad"}'):
            with self.subTest(value=value):
                app.SOURCE_FILE.write_text(value)
                self.assertIsNone(app.last_selected_source())
                requests = self.startup().call_args_list
                self.assertFalse(any(c.args[0].endswith("play_media") for c in requests))
                self.assertIsNotNone(app.SOURCE_RESTORE_ERROR)

    def test_radio_selection_replaces_persisted_apple_source(self):
        app.READY = True
        favorite = self.apple_favorite()
        with patch.object(app, "ha_request", return_value={}):
            app.perform("apple_music", {"favorite":favorite})
            app.perform("radio_direct", {"station":"1live"})
        self.assertEqual(app.last_selected_source(), {"kind":"radio", "id":"1live"})

    def test_failed_selection_cannot_replace_persisted_source(self):
        app.READY = True
        app.save_selected_station("wdr2")
        favorite = self.apple_favorite()
        with patch.object(app, "ha_request", side_effect=OSError("offline")):
            with self.assertRaises(OSError):
                app.perform("apple_music", {"favorite":favorite})
        self.assertEqual(app.last_selected_source(), {"kind":"radio", "id":"wdr2"})

    def test_addon_restart_with_saved_apple_is_read_only(self):
        favorite = self.apple_favorite()
        app.save_selected_source("apple", favorite)
        self.states["media_player.wohnzimmer"]["state"] = "playing"
        requests = self.recovery()
        self.assertTrue(app.READY)
        self.assertTrue(all(c.args == ("/states",) for c in requests.call_args_list))
        self.assertEqual(app.last_selected_source(), {"kind":"apple", "id":favorite})

    def test_legacy_unconfirmed_session_cannot_revive_old_radio_after_off(self):
        app.STATION_FILE.write_text(json.dumps({"station":"wdr2"}))
        self.states["media_player.wohnzimmer"]["state"] = "playing"
        self.recovery()
        self.assertTrue(app.SOURCE_UNCONFIRMED)
        app.transition_power(False)
        app.transition_power(True)
        requests = self.startup().call_args_list
        self.assertFalse(any(c.args[0].endswith("play_media") for c in requests))
        self.assertIsNotNone(app.SOURCE_RESTORE_ERROR)

    def test_settings_flush_before_replacing_last_good_file(self):
        app.save_selected_station("wdr2")
        with patch.object(app.os, "fsync", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                app.save_selected_source("apple", "a"*24)
        self.assertEqual(app.last_selected_source(), {"kind":"radio", "id":"wdr2"})

    def test_each_saved_setting_is_flushed_to_disk_immediately(self):
        with patch.object(app.os, "fsync", wraps=app.os.fsync) as flush:
            operations = [lambda: app.save_selected_station("wdr2"),
                          lambda: app.save_selected_source("apple", "a"*24),
                          lambda: app.save_speaker_levels({"media_player.bad":.35}),
                          lambda: app.save_remembered("media_player.bad", .35),
                          lambda: app.save_selected_view("apple"),
                          lambda: app.save_session_intent(False)]
            for operation in operations:
                before = flush.call_count
                operation()
                self.assertGreater(flush.call_count, before)

    def test_apple_favorite_starts_group_without_touching_volumes_or_saved_radio(self):
        app.READY = True
        app.save_selected_station("wdr2")
        favorite = self.apple_favorite()
        with patch.object(app, "ha_request", return_value={}) as request:
            app.perform("apple_music", {"favorite":favorite})
        request.assert_called_once_with("/services/media_player/play_media", {
            "entity_id":"media_player.wohnzimmer", "media":{
                "media_content_type":"custom", "media_content_id":"spiele meine Playlist Abendmusik von Dirk auf Apple Music auf Wohnung", "metadata":{}}})
        self.assertEqual(app.ACTIVE_APPLE["id"], favorite)
        self.assertEqual(app.last_selected_station(), "wdr2")
        self.monitor.select.assert_called_once_with("")

    def test_apple_album_can_play_on_single_configured_echo(self):
        app.READY = True
        favorite = self.apple_favorite("Album", "")
        with patch.object(app, "ha_request", return_value={}) as request:
            app.perform("apple_music", {"favorite":favorite})
        self.assertEqual(request.call_args.args[1]["media"]["media_content_id"], "spiele das Album Abendmusik von Dirk auf Apple Music")

    def test_official_alexa_apple_uses_text_command_for_same_registered_device(self):
        app.READY = True
        favorite = self.apple_favorite()
        app.INVENTORY_CACHE = (time.monotonic(), {"alexa_devices":["media_player.wohnzimmer"], "alexa_media":[]})
        device = "a" * 32
        with patch.object(app, "ha_request", side_effect=[json.dumps({"domain":"alexa_devices", "device_id":device}), {}]) as request:
            app.perform("apple_music", {"favorite":favorite})
        self.assertEqual(request.call_args_list[0].args[0], "/template")
        self.assertEqual(request.call_args_list[1].args, ("/services/alexa_devices/send_text_command", {
            "device_id":device, "text_command":"spiele meine Playlist Abendmusik von Dirk auf Apple Music auf Wohnung"}))
        self.assertEqual(app.ACTIVE_APPLE["id"], favorite)

    def test_official_alexa_radio_uses_text_command_without_other_device_or_volume(self):
        app.READY = True
        app.INVENTORY_CACHE = (time.monotonic(), {"alexa_devices":["media_player.wohnzimmer"], "alexa_media":[]})
        device = "b" * 32
        with patch.object(app, "ha_request", side_effect=[json.dumps({"domain":"alexa_devices", "device_id":device}), {}]) as request:
            app.perform("radio_direct", {"station":"1live"})
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args.args, ("/services/alexa_devices/send_text_command", {
            "device_id":device, "text_command":app.DIRECT_STATIONS["1live"]["media_content_id"]}))

    def test_official_alexa_missing_or_changed_mapping_cannot_send_playback(self):
        app.READY = True
        favorite = self.apple_favorite()
        app.INVENTORY_CACHE = (time.monotonic(), {"alexa_devices":["media_player.wohnzimmer"], "alexa_media":[]})
        for mapping in ({"domain":"alexa_media", "device_id":"a"*32}, {"domain":"alexa_devices", "device_id":None}):
            with self.subTest(mapping=mapping), patch.object(app, "ha_request", return_value=json.dumps(mapping)) as request:
                with self.assertRaises(ValueError):
                    app.perform("apple_music", {"favorite":favorite})
                self.assertEqual(request.call_count, 1)
                self.assertIsNone(app.ACTIVE_APPLE)

    def test_apple_unknown_favorite_cannot_send_an_arbitrary_command(self):
        app.READY = True
        self.apple_favorite()
        with patch.object(app, "ha_request") as request:
            with self.assertRaises(ValueError):
                app.perform("apple_music", {"favorite":"spiele irgendeinen anderen Befehl"})
        request.assert_not_called()

    def test_apple_favorite_rejected_during_startup_and_standby(self):
        favorite = self.apple_favorite()
        with patch.object(app, "ha_request") as request:
            with self.assertRaises(ValueError):
                app.perform("apple_music", {"favorite":favorite})
            app.READY = True
            app.PREPARING = True
            with self.assertRaises(ValueError):
                app.perform("apple_music", {"favorite":favorite})
            app.PREPARING = False
            app.STANDBY.set()
            with self.assertRaises(RuntimeError):
                app.perform("apple_music", {"favorite":favorite})
        request.assert_not_called()

    def test_apple_disabled_command_device_receives_no_request(self):
        app.READY = True
        favorite = self.apple_favorite()
        with patch.object(app, "enabled_device_ids", return_value={"media_player.kueche"}), patch.object(app, "ha_request") as request:
            with self.assertRaises(ValueError):
                app.perform("apple_music", {"favorite":favorite})
        request.assert_not_called()

    def test_apple_failed_or_cancelled_command_preserves_previous_source(self):
        app.READY = True
        favorite = self.apple_favorite()
        with patch.object(app, "ha_request", side_effect=OSError("offline")):
            with self.assertRaises(OSError):
                app.perform("apple_music", {"favorite":favorite})
        self.assertIsNone(app.ACTIVE_APPLE)
        self.monitor.select.assert_not_called()
        def turn_off(*args):
            app.transition_power(False)
            return {}
        with patch.object(app, "ha_request", side_effect=turn_off):
            with self.assertRaises(app.StartupCancelled):
                app.perform("apple_music", {"favorite":favorite})
        self.assertIsNone(app.ACTIVE_APPLE)
        self.monitor.select.assert_not_called()

    def test_radio_selection_clears_apple_source_and_power_cycle_clears_it(self):
        app.READY = True
        favorite = self.apple_favorite()
        with patch.object(app, "ha_request", return_value={}):
            app.perform("apple_music", {"favorite":favorite})
            app.perform("radio_direct", {"station":"wdr2"})
            self.assertIsNone(app.ACTIVE_APPLE)
            app.perform("apple_music", {"favorite":favorite})
        app.transition_power(False)
        self.assertIsNone(app.ACTIVE_APPLE)
        self.assertEqual(app.last_selected_station(), "wdr2")

    def test_apple_configuration_filters_invalid_entries_and_is_local_in_standby(self):
        app.OPTIONS.write_text(json.dumps({"apple_music_favorites":[{},None,{"name":"Test", "kind":[]},{"name":"Bad\nName","kind":"Playlist"},{"name":"Guter Name","kind":"Album"}]}))
        app.STANDBY.set()
        selection = app.apple_music_selection()
        self.assertFalse(selection["available"])
        self.assertEqual([item["name"] for item in selection["items"]], ["Guter Name"])
        first = selection["items"][0]["id"]
        self.assertEqual(app.apple_music_selection()["items"][0]["id"], first)

    def test_inventory_cache_returns_copies_expires_and_respects_standby(self):
        app.INVENTORY_CACHE = (0.0, None)
        inventory = {"media_player.wohnung": {"integration": "alexa_media"}}
        with patch.object(app, "read_integration_inventory", return_value=inventory) as read, patch.object(app.time, "monotonic", return_value=100) as now:
            first = app.integration_inventory()
            first.clear()
            self.assertEqual(app.integration_inventory(), {"media_player.wohnung": {"integration": "alexa_media"}})
            self.assertEqual(read.call_count, 1)
            now.return_value = 131
            app.integration_inventory()
            self.assertEqual(read.call_count, 2)
            app.STANDBY.set()
            with self.assertRaises(RuntimeError):
                app.integration_inventory()
            self.assertEqual(read.call_count, 2)

    def test_inventory_parallel_requests_share_one_registry_read(self):
        app.INVENTORY_CACHE = (0.0, None)
        entered, release = threading.Event(), threading.Event()
        results = []
        def read():
            entered.set()
            self.assertTrue(release.wait(2))
            return {"media_player.wohnung": {"integration": "alexa_media"}}
        with patch.object(app, "read_integration_inventory", side_effect=read) as request:
            threads = [threading.Thread(target=lambda: results.append(app.integration_inventory())) for _ in range(3)]
            for thread in threads:
                thread.start()
            self.assertTrue(entered.wait(2))
            release.set()
            for thread in threads:
                thread.join(2)
                self.assertFalse(thread.is_alive())
            self.assertEqual(len(results), 3)
            self.assertEqual(request.call_count, 1)

    def test_invalid_persisted_station_and_volume_are_ignored(self):
        for value in ([], {}, True, 1, {"station": []}, {"station": {}}, {"station": True}):
            app.STATION_FILE.write_text(json.dumps(value))
            self.assertEqual(app.last_selected_station(), "")
        app.VOLUME_FILE.write_text(json.dumps({"media_player.kueche": 0.2, "media_player.bad": True,
                                              "media_player.buero": 2, "bad key": 0.3,
                                              "media_player.wohnzimmer": "0.4"}))
        self.assertEqual(app.remembered(), {"media_player.kueche": 0.2})

    def test_timed_ready_is_independent_of_device_and_helper_availability(self):
        app.READY = True
        self.states[app.RADIO_SWITCH] = {"state": "unavailable"}
        self.states[app.RADIO_READY] = {"state": "unavailable"}
        self.states["media_player.wohnzimmer"]["state"] = "unavailable"
        with patch.object(app, "state_snapshot", return_value=self.states):
            state = app.radio_state()
        self.assertEqual(state["ready"], "on")
        self.assertEqual(state["power"], "on")
        self.assertTrue(next(s for s in state["stations"] if s["id"] == "wdr2")["available"])

    def test_offline_room_restore_is_attempted_and_keeps_individual_levels(self):
        self.states["media_player.buero"] = {"state": "unavailable"}
        app.save_speaker_levels({"media_player.wohnung": 0.25, "media_player.wohnzimmer": 0.4,
                                 "media_player.buero": 0.6})
        with patch.object(app, "state_snapshot", return_value=self.states), patch.object(app, "allowed_entities", return_value=set(self.states)), patch.object(app, "ha_request") as calls:
            app.restore_speakers(10)
        self.assertEqual([c.args[1]["entity_id"] for c in calls.call_args_list], ["media_player.buero", "media_player.wohnzimmer"])
        self.assertEqual(app.speaker_levels()["media_player.buero"], 0.6)
        self.assertEqual(app.speaker_levels()["media_player.wohnzimmer"], 0.4)
        self.assertEqual([c.args[1]["volume_level"] for c in calls.call_args_list], [.6, .4])

    def test_cancelled_empty_master_command_cannot_save_new_volume(self):
        app.READY = True
        app.save_speaker_levels({"media_player.wohnung": 0.4})
        def classify():
            app.transition_power(False)
            return {"players": [], "groups": [{"entity_id": "media_player.wohnung", "state": "idle", "volume": 0.4}]}
        with patch.object(app, "classify_devices", side_effect=classify):
            with self.assertRaises(app.StartupCancelled):
                app.perform("volume", {"entity_id": "media_player.wohnung", "volume": 0.8})
        self.assertEqual(app.speaker_levels()["media_player.wohnung"], 0.4)

    def test_success_order_and_saved_mute(self):
        app.save_speaker_levels({"media_player.wohnzimmer": 0.4, "media_player.kueche": 0})
        app.save_selected_station("wdr2")
        calls = self.startup().call_args_list
        paths = [c.args[0] for c in calls]
        self.assertTrue(app.READY)
        self.assertLess(paths.index("/services/homeassistant/update_entity"), paths.index("/services/media_player/play_media"))
        self.assertLess(paths.index("/services/homeassistant/update_entity"), paths.index("/services/media_player/volume_set"))
        self.assertTrue(all(c.args[1]["volume_level"] == 0 for c in calls[:paths.index("/services/media_player/play_media")] if c.args[0].endswith("volume_set")))
        self.assertLess(paths.index("/services/input_boolean/turn_on"), paths.index("/services/media_player/play_media"))
        volumes = [c.args[1] for c in calls if c.args[0].endswith("volume_set")]
        self.assertFalse(any(c["entity_id"] == "media_player.wohnung" for c in volumes))
        restores = [c.args[1] for c in calls[paths.index("/services/media_player/play_media") + 1:] if c.args[0].endswith("volume_set")]
        self.assertEqual(next(c for c in restores if c["entity_id"].endswith("wohnzimmer"))["volume_level"], 0.4)
        self.assertEqual([c["volume_level"] for c in volumes if c["entity_id"].endswith("wohnzimmer")], [0.4])
        self.assertTrue(all(c["volume_level"] == 0 for c in volumes if c["entity_id"].endswith("kueche")))
        self.assertEqual(app.speaker_levels()["media_player.wohnzimmer"], 0.4)

    def test_delayed_alexa_volume_execution_cannot_undo_startup_room_levels(self):
        for source_kind in ("radio", "apple"):
            with self.subTest(source=source_kind):
                config = app.options()
                config["apple_music_favorites"] = [{"name":"Dirk", "kind":"Playlist"}]
                app.OPTIONS.write_text(json.dumps(config))
                identity = "wdr2" if source_kind == "radio" else app.apple_music_selection()["items"][0]["id"]
                app.save_selected_source(source_kind, identity)
                wanted = {"media_player.wohnzimmer":0.4, "media_player.bad":0.65,
                          "media_player.kueche":0, "media_player.buero":0.3}
                app.save_speaker_levels({"media_player.wohnung":0.25, **wanted})
                queued, media = [], []
                actual = {entity:0 for entity in wanted}
                def request(path, body=None):
                    if path.endswith("volume_set"):
                        # HA reports the requested value before Alexa executes it.
                        self.states.setdefault(body["entity_id"], {"state":"playing", "attributes":{}})["attributes"]["volume_level"] = body["volume_level"]
                        queued.append(dict(body))
                    if path.endswith("play_media"):
                        media.append(body)
                    return {}
                self.startup(request)
                # Adversarial completion order: later requests can finish first.
                for body in reversed(queued):
                    if body["entity_id"] == "media_player.wohnung":
                        actual.update(dict.fromkeys(actual, body["volume_level"]))
                    else:
                        actual[body["entity_id"]] = body["volume_level"]
                self.assertEqual(actual, wanted)
                self.assertEqual(len(media), 1)
                self.assertEqual(app.speaker_levels()["media_player.wohnung"], 0.25)

    def test_preparation_preserves_positive_levels_and_pre_mutes_saved_silent_rooms(self):
        app.save_speaker_levels({"media_player.wohnung":0.25, "media_player.wohnzimmer":0.6,
                                "media_player.kueche":0, "media_player.bad":0.4})
        with patch.object(app, "state_snapshot", return_value=self.states), patch.object(app, "ha_request", return_value={}) as request:
            app.prepare_speaker_levels(10)
        self.assertEqual([call.args[1] for call in request.call_args_list],
                         [{"entity_id":"media_player.kueche", "volume_level":0.0}])
        self.assertEqual(app.speaker_levels()["media_player.wohnzimmer"], 0.6)
        self.assertEqual(app.speaker_levels()["media_player.wohnung"], 0.25)

    def test_reload_once_after_power_wait_and_before_volume_preparation(self):
        app.save_selected_station("wdr2")
        self.states["media_player.wohnzimmer"]["state"] = "unavailable"
        phases = []
        def request(path, body=None):
            phases.append(path)
            if path.endswith("reload_config_entry"):
                self.assertIn("wait:45", phases)
                self.assertFalse(app.READY)
                self.assertTrue(app.PREPARING)
                self.assertEqual(body, {"entity_id": "media_player.wohnzimmer"})
                self.states["media_player.wohnzimmer"]["state"] = "idle"
            return {}
        def wait(generation, seconds):
            if seconds == 45:
                self.assertNotIn("/services/homeassistant/reload_config_entry", phases)
            if seconds == 20:
                self.assertEqual(phases[-1], "/services/homeassistant/reload_config_entry")
                self.assertFalse(any(p.endswith("volume_set") for p in phases))
            if seconds == 5:
                self.assertEqual(phases[-1], "/services/homeassistant/update_entity")
                self.assertFalse(any(p.endswith("volume_set") for p in phases))
            phases.append(f"wait:{seconds}")
            return True
        with patch.object(app, "wait_for_start", side_effect=wait), patch.object(app, "state_snapshot", return_value=self.states), patch.object(app, "allowed_entities", return_value={"media_player.wohnung", "media_player.wohnzimmer"}), patch.object(app, "ha_request", side_effect=request):
            app.radio_start_sequence(10)
        self.assertTrue(app.READY)
        self.assertEqual(phases.count("/services/homeassistant/reload_config_entry"), 1)
        self.assertEqual([p for p in phases if p.startswith("wait:")], ["wait:45", "wait:20", "wait:5"])

    def test_reload_failure_does_not_block_timed_interface_release(self):
        app.save_selected_station("wdr2")
        def request(path, body=None):
            if path.endswith("reload_config_entry"):
                raise TimeoutError("Reload timed out")
            return {}
        with patch("builtins.print") as log:
            calls = self.startup(request).call_args_list
        self.assertTrue(app.READY)
        self.assertFalse(app.PREPARING)
        self.assertIsNone(app.STARTUP_ERROR)
        self.assertTrue(any(c.args[0].endswith("play_media") for c in calls))
        self.assertTrue(any("reload failed" in str(c) for c in log.call_args_list))

    def test_boot_migration_works_with_radio_off_and_no_alexa_queries(self):
        original = app.options()
        app.STANDBY.set()
        app.LAST_POWER = "off"
        with patch.object(app, "detected_devices", side_effect=AssertionError("No Alexa queries")), patch.object(app, "supervisor_request", side_effect=[{"options": original}, {"options": original}, {}]) as request:
            SYNCHRONIZE(None, migrate_only=True, bootstrap=True)
        self.assertTrue(all(call.kwargs.get("bootstrap") for call in request.call_args_list))
        self.assertTrue(all(entry["status"] == "Aktiv" and "enabled" not in entry for entry in app.options()["devices"]))
        self.assertTrue(app.STANDBY.is_set())
        self.assertEqual(app.LAST_POWER, "off")
        self.assertFalse(app.READY)

    def test_boot_access_cannot_query_home_assistant_or_other_supervisor_paths(self):
        app.STANDBY.set()
        for path, supervisor in (("/states", False), ("/services/homeassistant/reload_config_entry", False), ("/addons/other/info", True)):
            with self.subTest(path=path), self.assertRaises(ValueError):
                app.ha_request(path, supervisor=supervisor, startup_configuration=True)
        with patch.object(app, "TOKEN", "test"), patch.object(app, "urlopen") as opened:
            opened.return_value.__enter__.return_value.read.return_value = b'{"result":"ok","data":{}}'
            self.assertEqual(app.supervisor_request("/addons/self/info", bootstrap=True), {})
            self.assertEqual(opened.call_count, 1)
        self.assertTrue(app.STANDBY.is_set())

    def test_cancel_during_reload_stops_wait_and_all_later_commands(self):
        def request(path, body=None):
            if path.endswith("reload_config_entry"):
                app.transition_power(False)
            return {}
        calls = self.startup(request).call_args_list
        self.assertFalse(app.READY)
        self.assertIsNone(app.STARTUP_ERROR)
        self.assertEqual([c.args[0] for c in calls], ["/services/input_boolean/turn_off", "/services/homeassistant/reload_config_entry"])

    def test_failed_station_restores_but_keeps_interface_visible(self):
        app.save_selected_station("wdr2")
        app.save_speaker_levels({"media_player.wohnung": 0.25, "media_player.wohnzimmer": 0.6})
        def request(path, body=None):
            if path.endswith("play_media"):
                raise TimeoutError("Station timed out")
            return {}
        with patch("builtins.print") as log:
            calls = self.startup(request).call_args_list
        self.assertTrue(app.READY)
        self.assertFalse(app.PREPARING)
        self.assertIsNone(app.STARTUP_ERROR)
        self.assertTrue(any("Station timed out" in str(c) for c in log.call_args_list))
        self.assertTrue(any(c.args[0].endswith("volume_set") and c.args[1] == {"entity_id": "media_player.wohnzimmer", "volume_level": 0.6} for c in calls))
        app.synchronize_device_configuration.assert_called_once_with(10)

    def test_unavailable_target_is_restored_and_sender_is_requested(self):
        app.save_selected_station("wdr2")
        self.states["media_player.wohnzimmer"]["state"] = "unavailable"
        with patch.object(app, "wait_for_start", return_value=True) as wait, patch.object(app, "state_snapshot", return_value=self.states), patch.object(app, "allowed_entities", return_value={"media_player.wohnung", "media_player.wohnzimmer"}), patch.object(app, "ha_request", return_value={}) as calls:
            app.radio_start_sequence(10)
        self.assertTrue(app.READY)
        self.assertFalse(app.PREPARING)
        delays = [c.args[1] for c in wait.call_args_list]
        self.assertEqual(delays, [45, 20, 5])
        self.assertEqual(sum(c.args[0].endswith("play_media") for c in calls.call_args_list), 1)
        self.assertTrue(any(c.args[0].endswith("volume_set") and c.args[1]["entity_id"] == "media_player.wohnzimmer" for c in calls.call_args_list))
        self.monitor.select.assert_any_call("wdr2")

    def test_playback_status_includes_cached_radiotext_without_new_fetch(self):
        item = {"status": "available", "title": "Track", "artist": "Artist"}
        self.monitor.snapshot.return_value = (3, "wdr2", item)
        with patch.object(app, "state_snapshot", return_value=self.states), patch.object(app, "ha_request", side_effect=AssertionError("No extra request")):
            response = app.playback_status()
        self.assertEqual(response["radio_metadata"], {"station": "wdr2", "metadata": item})
        self.monitor.select.assert_not_called()

    def test_ready_follows_preparation_and_manual_controls_wait_for_restore(self):
        app.save_selected_station("wdr2")
        def request(path, body=None):
            if path.endswith("update_entity"):
                self.assertFalse(app.READY)
            if path.endswith("turn_on") or path.endswith("play_media"):
                self.assertTrue(app.READY)
            if path.endswith("update_entity") or path.endswith("volume_set"):
                self.assertTrue(app.PREPARING)
                with self.assertRaises(ValueError):
                    app.perform("volume", {"entity_id": "media_player.wohnzimmer", "volume": 0.8})
            return {}
        self.startup(request)
        self.assertTrue(app.READY)
        self.assertFalse(app.PREPARING)

    def test_cancel_during_initial_wait_prevents_ready_and_media_commands(self):
        def cancel(generation, seconds):
            self.assertEqual(seconds, 45)
            app.transition_power(False)
            return False
        with patch.object(app, "wait_for_start", side_effect=cancel), patch.object(app, "ha_request", return_value={}) as calls:
            app.radio_start_sequence(10)
        self.assertFalse(app.READY)
        self.assertFalse(app.PREPARING)
        self.assertEqual([c.args[0] for c in calls.call_args_list], ["/services/input_boolean/turn_off"])

    def test_cancel_after_late_reload_prevents_all_media_commands(self):
        def wait(generation, seconds):
            if seconds == 20:
                app.transition_power(False)
                return False
            return True
        with patch.object(app, "wait_for_start", side_effect=wait), patch.object(app, "ha_request", return_value={}) as calls:
            app.radio_start_sequence(10)
        self.assertFalse(app.READY)
        self.assertFalse(app.PREPARING)
        self.assertEqual([c.args[0] for c in calls.call_args_list], ["/services/input_boolean/turn_off", "/services/homeassistant/reload_config_entry"])

    def test_restore_failure_is_logged_without_hiding_interface(self):
        def request(path, body=None):
            if path.endswith("volume_set") and body["volume_level"] == 0.4:
                raise OSError("offline")
        with patch("builtins.print") as log:
            self.startup(request)
        self.assertTrue(app.READY)
        self.assertFalse(app.PREPARING)
        self.assertTrue(any("nicht wiederhergestellt" in str(c) for c in log.call_args_list))

    def test_pre_mute_failure_is_logged_but_sender_and_restore_are_attempted(self):
        app.save_selected_station("wdr2")
        def request(path, body=None):
            if path.endswith("volume_set") and body["volume_level"] == 0:
                raise TimeoutError("Pre-mute failed")
            return {}
        calls = self.startup(request).call_args_list
        self.assertTrue(app.READY)
        self.assertFalse(app.PREPARING)
        self.assertTrue(any(c.args[0].endswith("play_media") for c in calls))
        self.assertTrue(any(c.args[0].endswith("volume_set") and c.args[1]["volume_level"] == 0.4 for c in calls))

    def test_failed_refresh_does_not_stop_restore_or_sender(self):
        app.save_selected_station("wdr2")
        def request(path, body=None):
            if path.endswith("update_entity"):
                raise TimeoutError("Refresh timed out")
            return {}
        calls = self.startup(request).call_args_list
        self.assertTrue(any(c.args[0].endswith("play_media") for c in calls))
        self.assertTrue(any(c.args[0].endswith("volume_set") for c in calls))
        self.assertTrue(app.READY)

    def test_manual_sender_and_room_commands_accept_unavailable_state(self):
        app.READY = True
        app.save_speaker_levels({"media_player.wohnzimmer": 0.4})
        self.states["media_player.wohnzimmer"]["state"] = "unavailable"
        with patch.object(app, "state_snapshot", return_value=self.states), patch.object(app, "classify_devices", return_value={"players":[{"entity_id":"media_player.wohnzimmer", "state":"unavailable", "volume":0.4}], "groups":[]}), patch.object(app, "ha_request") as calls:
            app.perform("radio_direct", {"station":"wdr2"})
            app.perform("volume", {"entity_id":"media_player.wohnzimmer", "volume":0.2})
        self.assertEqual([c.args[0] for c in calls.call_args_list], ["/services/media_player/play_media", "/services/media_player/volume_set"])

    def test_power_off_does_not_queue_behind_media_lock(self):
        with patch.object(app, "ha_request", return_value={}) as request:
            with app.COMMAND_LOCK:
                thread = threading.Thread(target=app.power_command, args=(False,))
                thread.start()
                thread.join(1)
                self.assertFalse(thread.is_alive())
            self.assertEqual(request.call_args_list[0].args[0], "/services/switch/turn_off")

    def test_cancel_during_refresh_prevents_play_restore_ready(self):
        def request(path, body=None):
            if path.endswith("update_entity"):
                app.transition_power(False)
        calls = self.startup(request).call_args_list
        self.assertEqual([c.args[0] for c in calls], ["/services/input_boolean/turn_off", "/services/homeassistant/reload_config_entry", "/services/homeassistant/update_entity"])
        self.assertFalse(app.READY)

    def test_no_stored_station_does_not_play(self):
        calls = self.startup().call_args_list
        self.assertFalse(any(c.args[0].endswith("play_media") for c in calls))
        self.assertTrue(app.READY)

    def test_unknown_first_use_volume_uses_master_and_is_restored(self):
        self.states["media_player.wohnzimmer"]["attributes"] = {}
        calls = self.startup().call_args_list
        self.assertTrue(any(c.args[0].endswith("volume_set") and c.args[1]["entity_id"].endswith("wohnzimmer") for c in calls))

    def test_master_zero_remains_silent(self):
        app.save_speaker_levels({"media_player.wohnung": 0, "media_player.wohnzimmer": 0.4})
        calls = self.startup().call_args_list
        self.assertTrue(all(c.args[1]["volume_level"] == 0 for c in calls if c.args[0].endswith("volume_set")))
        self.assertEqual(app.speaker_levels()["media_player.wohnzimmer"], 0.4)
        self.assertEqual(app.displayed_speaker_levels()["media_player.wohnzimmer"], 0)

    def test_start_restores_distinct_saved_room_levels_and_mutes(self):
        self.states["media_player.bad"] = {"state": "idle", "attributes": {"volume_level": 0.7}}
        self.states["media_player.buero"] = {"state": "idle", "attributes": {"volume_level": 0.6}}
        app.save_speaker_levels({"media_player.wohnung": 0.25, "media_player.wohnzimmer": 0.15,
                                 "media_player.bad": 0.7, "media_player.kueche": 0})
        calls = self.startup().call_args_list
        for entity, level in (("media_player.wohnzimmer", .15), ("media_player.bad", .7), ("media_player.buero", .6)):
            commands = [c.args[1]["volume_level"] for c in calls if c.args[0].endswith("volume_set") and c.args[1]["entity_id"] == entity]
            self.assertEqual(commands, [level])
            self.assertEqual(app.speaker_levels()[entity], level)
        self.assertEqual(app.speaker_levels()["media_player.kueche"], 0)

    def test_individual_changes_after_start_do_not_follow_master(self):
        app.save_speaker_levels({"media_player.wohnung": 0.25, "media_player.wohnzimmer": 0.15})
        self.startup()
        devices = {"players": [{"entity_id": "media_player.wohnzimmer", "state": "idle", "volume": 0.25}], "groups": [], "excluded": []}
        with patch.object(app, "classify_devices", return_value=devices), patch.object(app, "ha_request", return_value={}) as request:
            app.perform("volume", {"entity_id": "media_player.wohnzimmer", "volume": 0.55})
        self.assertEqual(request.call_count, 1)
        self.assertEqual(app.speaker_levels()["media_player.wohnung"], 0.25)
        self.assertEqual(app.displayed_speaker_levels()["media_player.wohnzimmer"], 0.55)
        self.assertEqual(app.speaker_levels()["media_player.wohnzimmer"], 0.55)
        app.transition_power(False)
        app.transition_power(True)
        calls = self.startup().call_args_list
        commands = [c.args[1]["volume_level"] for c in calls if c.args[0].endswith("volume_set") and c.args[1]["entity_id"] == "media_player.wohnzimmer"]
        self.assertEqual(commands, [0.55])

    def test_individual_room_can_be_raised_after_start_with_master_zero(self):
        app.save_speaker_levels({"media_player.wohnung": 0, "media_player.wohnzimmer": 0.4})
        self.startup()
        devices = {"players": [{"entity_id": "media_player.wohnzimmer", "state": "idle", "volume": 0}], "groups": [], "excluded": []}
        with patch.object(app, "classify_devices", return_value=devices), patch.object(app, "ha_request", return_value={}):
            app.perform("volume", {"entity_id": "media_player.wohnzimmer", "volume": 0.3})
        self.assertEqual(app.displayed_speaker_levels()["media_player.wohnzimmer"], 0.3)
        self.assertEqual(app.speaker_levels()["media_player.wohnung"], 0)

    def test_missing_master_uses_one_percent_fallback(self):
        self.states.pop("media_player.wohnung")
        calls = self.startup().call_args_list
        self.assertTrue(app.READY)
        self.assertFalse(app.PREPARING)
        self.assertIsNone(app.STARTUP_ERROR)
        self.assertTrue(any(c.args[0].endswith("volume_set") and c.args[1]["volume_level"] == 0.01 for c in calls))

        self.assertEqual(app.speaker_levels()["media_player.wohnung"], 0.01)

    def test_wait_is_interruptible(self):
        app.CANCEL.set()
        self.assertFalse(app.wait_for_start(10, 50))

    def test_automation_has_three_fixed_waits(self):
        with patch.object(app, "wait_for_start", return_value=True) as wait, patch.object(app, "state_snapshot", return_value=self.states), patch.object(app, "allowed_entities", return_value=set()), patch.object(app, "ha_request"):
            app.radio_start_sequence(10)
        delays = [c.args[1] for c in wait.call_args_list]
        self.assertEqual(delays, [45, 20, 5])

    def test_countdown_covers_seventy_seconds_and_does_not_claim_ready(self):
        app.STARTED_AT = 100
        with patch.object(app.time, "monotonic", return_value=107) as now:
            self.assertEqual(app.startup_remaining(), 63)
            now.return_value = 175
            self.assertEqual(app.startup_remaining(), 0)
        self.assertFalse(app.READY)

    def test_cancel_during_post_refresh_wait_prevents_restore_ready_and_play(self):
        app.save_selected_station("wdr2")
        def wait(generation, seconds):
            if seconds == 5:
                app.transition_power(False)
                return False
            return True
        with patch.object(app, "wait_for_start", side_effect=wait), patch.object(app, "ha_request", return_value={}) as calls:
            app.radio_start_sequence(10)
        self.assertFalse(app.READY)
        self.assertFalse(app.PREPARING)
        self.assertEqual([c.args[0] for c in calls.call_args_list], ["/services/input_boolean/turn_off", "/services/homeassistant/reload_config_entry", "/services/homeassistant/update_entity"])

    def test_verification_cancellation_does_not_poll(self):
        with patch.object(app, "wait_for_start", return_value=False), patch.object(app, "state_snapshot") as states:
            VERIFY(10, {"media_player.wohnzimmer": 0.4})
        states.assert_not_called()

    def test_unconfirmed_startup_volume_retries_only_same_level_twice(self):
        entity = "media_player.wohnzimmer"
        app.ROOM_TARGETS[entity] = 0.4
        self.states[entity]["attributes"]["volume_level"] = 0.01
        with patch.object(app, "wait_for_start", return_value=True), patch.object(app, "state_snapshot", return_value=self.states) as snapshot, patch.object(app, "ha_request", return_value={}) as request:
            VERIFY(10, {entity:0.4})
        self.assertEqual(snapshot.call_count, 3)
        self.assertTrue(all(c.kwargs == {"fresh":True} for c in snapshot.call_args_list))
        self.assertEqual(request.call_count, 2)
        self.assertTrue(all(c.args == ("/services/media_player/volume_set", {"entity_id":entity, "volume_level":0.4}) for c in request.call_args_list))
        self.assertEqual(app.VOLUME_CONFIRMATION[entity], {"expected":0.4, "observed":0.01})

    def test_confirmed_startup_volume_stops_retry_and_clears_warning(self):
        entity = "media_player.wohnzimmer"
        app.ROOM_TARGETS[entity] = 0.4
        app.VOLUME_CONFIRMATION[entity] = {"expected":0.4, "observed":0.01}
        with patch.object(app, "wait_for_start", return_value=True), patch.object(app, "state_snapshot", return_value=self.states), patch.object(app, "ha_request") as request:
            VERIFY(10, {entity:0.4})
        request.assert_not_called()
        self.assertNotIn(entity, app.VOLUME_CONFIRMATION)

    def test_startup_volume_retry_cannot_overwrite_later_manual_change(self):
        entity = "media_player.wohnzimmer"
        app.ROOM_TARGETS[entity] = 0.2
        with patch.object(app, "wait_for_start", return_value=True), patch.object(app, "state_snapshot", return_value=self.states), patch.object(app, "ha_request") as request:
            VERIFY(10, {entity:0.4})
        request.assert_not_called()
        self.assertFalse(app.VOLUME_CONFIRMATION)

    def test_standby_closes_inflight_ha_response(self):
        stream = Mock()
        with patch.object(app, "ACTIVE_RESPONSES", {stream}):
            generation = app.transition_power(False)
            app.enter_standby(generation)
        stream.close.assert_called_once()

    def test_off_timeout_still_schedules_ten_second_standby(self):
        with patch.object(app, "ha_request", side_effect=TimeoutError("offline")):
            with self.assertRaises(TimeoutError):
                app.power_command(False)
        args = self.timer.call_args
        self.assertEqual(args.args[:2], (10, app.enter_standby))
        app.enter_standby(*args.kwargs["args"])
        self.assertTrue(app.STANDBY.is_set())
        self.assertFalse(app.READY)

    def test_old_off_timer_cannot_stop_new_generation(self):
        old = app.transition_power(False)
        app.transition_power(True)
        app.enter_standby(old)
        self.assertFalse(app.STANDBY.is_set())

    def test_standby_reads_are_local_and_writes_cannot_wake(self):
        app.STANDBY.set()
        with patch.object(app, "ha_request", side_effect=AssertionError("network")):
            self.assertTrue(app.radio_state()["standby"])
            app.save_selected_view("apple")
            self.assertEqual(app.selected_view(), "apple")
            with self.assertRaises(app.StartupCancelled):
                app.perform("radio_direct", {"station": "wdr2"})

    def test_ready_state_does_not_depend_on_unused_ha_read(self):
        app.READY = True
        with patch.object(app, "ha_request", side_effect=OSError("HA temporarily offline")) as request:
            state = app.radio_state()
        self.assertEqual(state["power"], "on")
        self.assertEqual(state["ready"], "on")
        request.assert_not_called()

    def test_reattached_master_mute_and_restore_preserve_observed_room_intent(self):
        app.READY = True
        app.RECOVERED_SESSION = True
        app.save_speaker_levels({"media_player.wohnung":.4,
                                 "media_player.wohnzimmer":.8, "media_player.kueche":.8})
        devices = {"players":[{"entity_id":"media_player.wohnzimmer", "volume":.35},
                              {"entity_id":"media_player.kueche", "volume":0}],
                   "groups":[{"entity_id":"media_player.wohnung", "volume":.4}]}
        with patch.object(app, "classify_devices", return_value=devices), patch.object(app, "ha_request", return_value={}) as request:
            app.perform("volume", {"entity_id":"media_player.wohnung", "volume":0})
            devices["players"][0]["volume"] = 0
            app.perform("volume", {"entity_id":"media_player.wohnung", "volume":.5})
        self.assertEqual([c.args[1] for c in request.call_args_list], [
            {"entity_id":"media_player.wohnzimmer", "volume_level":0},
            {"entity_id":"media_player.wohnzimmer", "volume_level":.5}])
        self.assertEqual(app.speaker_levels()["media_player.kueche"], 0)

    def test_failed_wake_returns_to_standby(self):
        app.STANDBY.set()
        with patch.object(app, "ha_request", side_effect=TimeoutError("offline")):
            with self.assertRaises(TimeoutError):
                app.power_command(True)
        self.assertTrue(app.STANDBY.is_set())

    def test_stale_command_rechecked_after_lock_wait(self):
        entered = threading.Event()
        result = []
        def worker():
            entered.set()
            try:
                app.startup_request(10, "/services/media_player/play_media", {})
            except app.StartupCancelled:
                result.append("cancelled")
        with app.COMMAND_LOCK:
            thread = threading.Thread(target=worker)
            thread.start()
            self.assertTrue(entered.wait(1))
            app.transition_power(False)
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result, ["cancelled"])

    def test_track_commands_recheck_device_admission_before_service(self):
        with patch.object(app, "enabled_device_ids", return_value=set()), patch.object(app, "ha_request") as request:
            for service in ("media_previous_track", "media_next_track", "shuffle_set"):
                with self.subTest(service=service), self.assertRaises(ValueError):
                    app.startup_request(10, "/services/media_player/" + service,
                                        {"entity_id":"media_player.wohnzimmer"})
        request.assert_not_called()

    def test_ready_helper_alone_cannot_unlock_ui(self):
        self.states[app.RADIO_READY] = {"state": "on"}
        with patch.object(app, "state_snapshot", return_value=self.states):
            self.assertEqual(app.radio_state()["ready"], "off")

    def test_state_requests_coalesced_and_standby_bypasses_cache(self):
        with patch.object(app, "ha_request", return_value=[{"entity_id": app.RADIO_SWITCH, "state": "on"}]) as request:
            app.state_snapshot()
            app.state_snapshot()
            self.assertEqual(request.call_count, 1)
            app.STANDBY.set()
            with self.assertRaises(RuntimeError):
                app.state_snapshot()

    def test_persistent_view_and_invalid_input(self):
        app.save_selected_view("apple")
        self.assertEqual(app.selected_view(), "apple")
        with self.assertRaises(ValueError):
            app.save_selected_view("other")
        self.assertEqual(app.selected_view(), "apple")
        app.VIEW_FILE.write_text("null")
        self.assertEqual(app.selected_view(), "radio")

    def test_invalid_volume_and_entity_rejected(self):
        app.READY = True
        for entity, volume in [("media_player.x", float("nan")), ("media_player.x", True), ("../x", 0.5)]:
            with self.assertRaises(ValueError):
                app.perform("volume", {"entity_id": entity, "volume": volume})


class RoomAndGroupControlTests(unittest.TestCase):
    def setUp(self):
        RuntimeTests.setUp(self)
        app.READY = True
        self.devices = {"players": [{"entity_id": "media_player.wohnzimmer", "state": "playing", "volume": 0.45}],
                        "groups": [{"entity_id": "media_player.wohnung", "state": "playing", "volume": 0.4}]}
        self.stack.enter_context(patch.object(app, "classify_devices", return_value=self.devices))
        self.stack.enter_context(patch.object(app, "state_snapshot", return_value=self.states))
        self.request = self.stack.enter_context(patch.object(app, "ha_request", return_value={}))

    def test_room_mute_restore_persists_without_playback_commands(self):
        app.perform("room_audio", {"entity_id": "media_player.wohnzimmer", "on": False})
        self.assertEqual(app.remembered()["media_player.wohnzimmer"], 0.45)
        app.ROOM_TARGETS.clear()  # Simulate a restart with the observed mute.
        self.devices["players"][0]["volume"] = 0
        result = app.perform("room_audio", {"entity_id": "media_player.wohnzimmer", "on": True})
        self.assertEqual(result["volume"], 0.45)
        self.assertEqual([call.args[1]["volume_level"] for call in self.request.call_args_list], [0, 0.45])
        self.assertTrue(all(call.args[0].endswith("volume_set") for call in self.request.call_args_list))

    def test_room_switch_uses_observed_volume_and_repeated_off_stays_muted(self):
        app.ROOM_TARGETS["media_player.wohnzimmer"] = 0.25
        app.perform("room_audio", {"entity_id": "media_player.wohnzimmer", "on": True})
        self.request.assert_not_called()
        app.perform("room_audio", {"entity_id": "media_player.wohnzimmer", "on": False})
        app.perform("room_audio", {"entity_id": "media_player.wohnzimmer", "on": False})
        self.assertEqual(self.request.call_count, 2)
        self.assertEqual(app.remembered()["media_player.wohnzimmer"], 0.45)
        self.assertTrue(all(c.args[1]["volume_level"] == 0 for c in self.request.call_args_list))

    def test_external_volume_changes_override_saved_room_mute(self):
        app.save_speaker_levels({"media_player.wohnzimmer":0})
        app.ROOM_TARGETS["media_player.wohnzimmer"] = 0
        app.perform("room_audio", {"entity_id":"media_player.wohnzimmer", "on":False})
        self.request.assert_called_once_with("/services/media_player/volume_set", {
            "entity_id":"media_player.wohnzimmer", "volume_level":0})
        self.assertEqual(app.remembered()["media_player.wohnzimmer"], .45)

    def test_external_mute_can_be_restored_despite_saved_positive_target(self):
        self.devices["players"][0]["volume"] = 0
        app.save_speaker_levels({"media_player.wohnzimmer":.65})
        app.save_remembered("media_player.wohnzimmer", .65)
        app.ROOM_TARGETS["media_player.wohnzimmer"] = .65
        app.perform("room_audio", {"entity_id":"media_player.wohnzimmer", "on":True})
        self.request.assert_called_once_with("/services/media_player/volume_set", {
            "entity_id":"media_player.wohnzimmer", "volume_level":.65})

    def test_unknown_volume_off_is_not_guessed_and_does_not_erase_restore(self):
        self.devices["players"][0]["volume"] = None
        app.save_remembered("media_player.wohnzimmer", .65)
        app.perform("room_audio", {"entity_id":"media_player.wohnzimmer", "on":False})
        self.assertEqual(self.request.call_args.args[1]["volume_level"], 0)
        self.assertEqual(app.remembered()["media_player.wohnzimmer"], .65)

    def test_room_switch_rejects_master_unknown_targets_and_non_bool(self):
        for body in ({"entity_id": "media_player.wohnung", "on": False},
                     {"entity_id": "media_player.hidden", "on": False},
                     {"entity_id": "media_player.wohnzimmer", "on": 1}):
            with self.assertRaises(ValueError):
                app.perform("room_audio", body)
        self.request.assert_not_called()

    def test_room_failure_keeps_saved_volume_and_intent(self):
        app.save_speaker_levels({"media_player.wohnzimmer": 0.45})
        self.request.side_effect = OSError("offline")
        with self.assertRaises(OSError):
            app.perform("room_audio", {"entity_id": "media_player.wohnzimmer", "on": False})
        self.assertEqual(app.speaker_levels()["media_player.wohnzimmer"], 0.45)
        self.assertNotIn("media_player.wohnzimmer", app.ROOM_TARGETS)

    def test_group_transport_targets_only_group_and_does_not_change_volumes(self):
        for state, command in (("playing", "pause"), ("paused", "play")):
            self.states["media_player.wohnung"] = {"state": state, "attributes": {"supported_features": 16385}}
            app.perform("group_transport", {"command": command, "entity_id": "media_player.wohnzimmer"})
            self.request.assert_called_with("/services/media_player/media_" + command, {"entity_id": "media_player.wohnung"})
        self.assertEqual(app.speaker_levels(), {})

    def test_group_transport_rejects_missing_features_and_idle(self):
        for state, features, command in (("idle", 16385, "play"), ("playing", 0, "pause"), ("paused", 1, "play")):
            self.states["media_player.wohnung"] = {"state": state, "attributes": {"supported_features": features}}
            with self.assertRaises(ValueError):
                app.perform("group_transport", {"command": command})
        self.request.assert_not_called()

    def test_controls_cannot_wake_standby_or_run_during_startup(self):
        for action, body in (("room_audio", {"entity_id": "media_player.wohnzimmer", "on": True}), ("group_transport", {"command": "play"})):
            app.READY = False
            with self.assertRaises(ValueError):
                app.perform(action, body)
            app.READY = True
            app.STANDBY.set()
            with self.assertRaises(app.StartupCancelled):
                app.perform(action, body)
            app.STANDBY.clear()
        self.request.assert_not_called()

    def test_disabled_group_cannot_receive_transport(self):
        app.OPTIONS.write_text(json.dumps({"devices": []}))
        self.states["media_player.wohnung"] = {"state": "playing", "attributes": {"supported_features": 16385}}
        with self.assertRaises(ValueError):
            app.perform("group_transport", {"command": "pause"})
        self.request.assert_not_called()


class DeviceConfigurationTests(unittest.TestCase):
    def setUp(self):
        RuntimeTests.setUp(self)
        self.found = [
            {"entity_id": "media_player.wohnung", "name": "Wohnung", "state": "idle", "volume": 0.4},
            {"entity_id": "media_player.wohnzimmer", "name": "Wohnzimmer", "state": "idle", "volume": 0.4},
            {"entity_id": "media_player.bad", "name": "Bad", "state": "off", "volume": 0.7},
            {"entity_id": "media_player.reserve", "name": "Reserve", "state": "unavailable", "volume": None},
        ]

    def config(self, entries):
        app.OPTIONS.write_text(json.dumps({"show_dashboard_setup": True, "devices": entries}))

    def test_disabled_rooms_are_hidden_and_enabled_names_are_preserved(self):
        self.config([{"entity_id": "media_player.wohnzimmer", "name": "Mein Wohnzimmer", "enabled": True},
                     {"entity_id": "media_player.bad", "name": "Bad", "enabled": False},
                     {"entity_id": "media_player.reserve", "name": "Reserve", "enabled": True}])
        with patch.object(app, "detected_devices", return_value=self.found):
            classified = app.classify_devices()
            self.assertEqual({p["entity_id"] for p in classified["players"]}, {"media_player.wohnzimmer", "media_player.reserve"})
            self.assertEqual(classified["players"][0]["name"], "Mein Wohnzimmer")
            self.assertEqual(app.allowed_entities(), {"media_player.wohnzimmer", "media_player.reserve"})

    def test_discovery_includes_registered_devices_without_states(self):
        inventory = {"alexa_devices": ["media_player.bad", "media_player.reserve"], "alexa_media": []}
        with patch.object(app, "state_snapshot", return_value={"media_player.bad": {"state": "off", "attributes": {"friendly_name": "Bad"}}}):
            devices = {p["entity_id"]: p for p in app.detected_devices(inventory)}
        self.assertEqual(devices["media_player.reserve"]["state"], "unavailable")
        self.assertEqual(devices["media_player.bad"]["state"], "off")

    def test_registry_names_survive_missing_live_states(self):
        app.INVENTORY_CACHE = (0.0, None)
        response = json.dumps({"alexa_media": ["media_player.mo", "media_player.fire_tv", "media_player.this_device"],
                               "alexa_devices": [], "device_names": {"media_player.mo": "Mo", "media_player.fire_tv": "Dirks Fire TV", "media_player.this_device": "This Device"}})
        with patch.object(app, "ha_request", return_value=response), patch.object(app, "state_snapshot", return_value={}):
            found = app.detected_devices()
        self.assertEqual({p["name"] for p in found}, {"Mo", "Dirks Fire TV", "This Device"})
        self.assertTrue(all(p["state"] == "unavailable" for p in found))
        config = app.merge_discovered_devices({"devices": []}, found)
        self.assertEqual(len(config["devices"]), 3)
        self.assertTrue(all(entry["status"] == "Inaktiv" for entry in config["devices"]))

    def test_new_devices_are_disabled_and_existing_settings_unchanged(self):
        initial = {"show_dashboard_setup": True, "devices": [
            {"entity_id": "media_player.wohnzimmer", "name": "Mein Name", "enabled": False},
            {"entity_id": "media_player.altes_geraet", "name": "Später wieder da", "enabled": True}]}
        merged = app.merge_discovered_devices(initial, self.found)
        self.assertEqual([entry["name"] for entry in merged["devices"][:2]], [entry["name"] for entry in initial["devices"]])
        self.assertEqual([entry["status"] for entry in merged["devices"][:2]], ["Inaktiv", "Aktiv"])
        self.assertTrue(all(entry["status"] == "Inaktiv" for entry in merged["devices"][2:]))
        self.assertEqual(len(initial["devices"]), 2)
        self.assertTrue(merged["show_dashboard_setup"])

    def test_sync_preserves_latest_user_edit_before_post(self):
        initial = {"show_dashboard_setup": False, "devices": [{"entity_id": "media_player.wohnzimmer", "name": "Wohnzimmer", "enabled": True}]}
        latest = {"show_dashboard_setup": True, "devices": [{"entity_id": "media_player.wohnzimmer", "name": "Neu", "enabled": False}]}
        with patch.object(app, "detected_devices", return_value=self.found), patch.object(app, "supervisor_request", side_effect=[{"options": initial}, {"options": latest}, {}]) as request:
            SYNCHRONIZE(10)
        posted = request.call_args_list[-1].args[1]["options"]
        self.assertEqual(posted["devices"][0], {"entity_id": "media_player.wohnzimmer", "name": "Neu", "status": "Inaktiv"})
        self.assertTrue(posted["show_dashboard_setup"])
        self.assertEqual(app.options(), posted)

    def test_standard_routing_migration_removes_redundant_fields_without_commands(self):
        original = app.options()
        original.update(apple_music_target=app.DEFAULT_PLAYBACK_TARGET,
                        apple_music_group=app.DEFAULT_PLAYBACK_GROUP,
                        apple_music_favorites=[{"name":"Dirk", "kind":"Playlist"}])
        with patch.object(app, "detected_devices", side_effect=AssertionError("No Alexa discovery")), \
             patch.object(app, "supervisor_request", side_effect=[{"options": original}, {"options": original}, {}]) as request:
            SYNCHRONIZE(None, migrate_only=True, bootstrap=True)
        posted = request.call_args_list[-1].args[1]["options"]
        self.assertNotIn("apple_music_target", posted)
        self.assertNotIn("apple_music_group", posted)
        self.assertEqual(posted["apple_music_favorites"], original["apple_music_favorites"])
        selection = app.apple_music_selection()
        self.assertEqual((selection["target"], selection["group"]),
                         (app.DEFAULT_PLAYBACK_TARGET, app.DEFAULT_PLAYBACK_GROUP))
        self.assertEqual(len(selection["items"]), 1)
        self.assertTrue(all(call.args[0] in ("/addons/self/info", "/addons/self/options") for call in request.call_args_list))
        self.assertEqual(app.merge_discovered_devices(posted, []), posted)

    def test_routing_migration_preserves_custom_device_group_and_direct_playback(self):
        for target, group in (("media_player.bad", "Oben"), ("media_player.bad", ""),
                              (app.DEFAULT_PLAYBACK_TARGET, ""), ("media_player.bad", app.DEFAULT_PLAYBACK_GROUP)):
            with self.subTest(target=target, group=group):
                original = {"devices":[], "apple_music_target":target, "apple_music_group":group}
                merged = app.merge_discovered_devices(original, [])
                self.assertEqual(merged.get("apple_music_target", app.DEFAULT_PLAYBACK_TARGET), target)
                self.assertEqual(merged.get("apple_music_group", app.DEFAULT_PLAYBACK_GROUP), group)
                self.assertEqual(original["apple_music_group"], group)

    def test_routing_migration_preserves_latest_custom_user_edit(self):
        initial = app.merge_discovered_devices(app.options(), [])
        initial.update(apple_music_target=app.DEFAULT_PLAYBACK_TARGET, apple_music_group=app.DEFAULT_PLAYBACK_GROUP)
        latest = {**initial, "apple_music_target":"media_player.bad", "apple_music_group":""}
        with patch.object(app, "supervisor_request", side_effect=[{"options":initial}, {"options":latest}]) as request:
            SYNCHRONIZE(None, migrate_only=True, bootstrap=True)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(app.options(), latest)

    def test_unchanged_inventory_does_not_write_options(self):
        config = app.merge_discovered_devices({"show_dashboard_setup": False, "devices": []}, self.found)
        with patch.object(app, "detected_devices", return_value=self.found), patch.object(app, "supervisor_request", return_value={"options": config}) as request:
            SYNCHRONIZE(10)
        self.assertEqual(request.call_count, 1)

    def test_migration_only_does_not_query_alexa_or_discover_devices(self):
        original = app.options()
        with patch.object(app, "detected_devices", side_effect=AssertionError("Alexa must not be queried")), patch.object(app, "supervisor_request", side_effect=[{"options": original}, {"options": original}, {}]) as request:
            SYNCHRONIZE(10, migrate_only=True)
        posted = request.call_args_list[-1].args[1]["options"]
        self.assertEqual(len(posted["devices"]), len(original["devices"]))
        self.assertTrue(all(entry["status"] == "Aktiv" and "enabled" not in entry for entry in posted["devices"]))
        self.assertEqual(app.options(), posted)

    def test_status_selection_filters_devices_and_overrides_legacy_value(self):
        self.config([{"entity_id": "media_player.wohnzimmer", "name": "Wohnzimmer", "status": "Aktiv", "enabled": False},
                     {"entity_id": "media_player.bad", "name": "Bad", "status": "Inaktiv", "enabled": True}])
        self.assertEqual(app.enabled_device_ids(), {"media_player.wohnzimmer"})
        with patch.object(app, "detected_devices", return_value=self.found):
            self.assertEqual(app.allowed_entities(), {"media_player.wohnzimmer"})

    def test_legacy_migration_preserves_selection_names_and_missing_devices(self):
        initial = {"devices": [{"entity_id": "media_player.reserve", "name": "Mein Reservegerät", "enabled": False},
                               {"entity_id": "media_player.wohnzimmer", "name": "Mein Wohnzimmer", "enabled": True}]}
        merged = app.merge_discovered_devices(initial, [])
        self.assertEqual(merged, {"devices": [{"entity_id": "media_player.reserve", "name": "Mein Reservegerät", "status": "Inaktiv"},
                                             {"entity_id": "media_player.wohnzimmer", "name": "Mein Wohnzimmer", "status": "Aktiv"}]})
        self.assertIn("enabled", initial["devices"][0])
        self.assertEqual(app.merge_discovered_devices(merged, []), merged)

    def test_invalid_status_cannot_fall_back_to_legacy_true(self):
        for value in ("true", "false", "", None, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                app.configured_devices({"devices": [{"entity_id": "media_player.bad", "name": "Bad", "status": value, "enabled": True}]})

    def test_sync_never_uses_network_in_standby(self):
        app.STANDBY.set()
        with patch.object(app, "supervisor_request") as request:
            with self.assertRaises(app.StartupCancelled):
                SYNCHRONIZE(10)
        request.assert_not_called()

    def test_failed_config_write_does_not_enable_new_rooms(self):
        original = app.options()
        with patch.object(app, "detected_devices", return_value=self.found), patch.object(app, "supervisor_request", side_effect=[{"options": original}, {"options": original}, OSError("write failed")]):
            with self.assertRaises(OSError):
                SYNCHRONIZE(10)
        self.assertIsNone(app.SUPERVISOR_OPTIONS)
        self.assertNotIn("media_player.reserve", app.enabled_device_ids())

    def test_disabled_device_cannot_receive_manual_commands(self):
        self.config([{"entity_id": "media_player.bad", "name": "Bad", "enabled": False}])
        app.READY = True
        with patch.object(app, "detected_devices", return_value=self.found), patch.object(app, "ha_request") as request:
            with self.assertRaises(ValueError):
                app.perform("volume", {"entity_id": "media_player.bad", "volume": 0.5})
        request.assert_not_called()

    def test_disabled_room_is_skipped_by_pre_mute_restore_and_update(self):
        self.config([{"entity_id": "media_player.wohnung", "name": "Wohnung", "enabled": True},
                     {"entity_id": "media_player.wohnzimmer", "name": "Wohnzimmer", "enabled": True},
                     {"entity_id": "media_player.bad", "name": "Bad", "enabled": False}])
        self.states["media_player.bad"] = {"state": "idle", "attributes": {"volume_level": 0.7}}
        app.save_speaker_levels({"media_player.wohnung": 0.25, "media_player.wohnzimmer": 0.4, "media_player.bad": 0.7})
        with patch.object(app, "detected_devices", return_value=self.found), patch.object(app, "state_snapshot", return_value=self.states), patch.object(app, "wait_for_start", return_value=True), patch.object(app, "ha_request", return_value={}) as request:
            app.radio_start_sequence(10)
        self.assertTrue(app.READY)
        for call in request.call_args_list:
            target = call.args[1].get("entity_id", [])
            self.assertNotIn("media_player.bad", [target] if isinstance(target, str) else target)
        self.assertEqual(app.speaker_levels()["media_player.bad"], 0.7)

    def test_registered_fire_tv_can_be_enabled_explicitly(self):
        self.config([{"entity_id": "media_player.fire_tv", "name": "Fire TV", "enabled": True}])
        with patch.object(app, "detected_devices", return_value=[{"entity_id": "media_player.fire_tv", "name": "Fire TV", "state": "idle", "volume": 0.2}]):
            self.assertEqual(app.allowed_entities(), {"media_player.fire_tv"})

    def test_disabled_station_target_cannot_play(self):
        self.config([])
        with patch.object(app, "ha_request") as request, patch.object(app, "state_snapshot", return_value=self.states):
            with self.assertRaises(ValueError):
                app.play_station("wdr2", 10)
        request.assert_not_called()

    def test_invalid_config_and_duplicate_entities_are_rejected(self):
        entry = {"entity_id": "media_player.bad", "name": "Bad", "enabled": True}
        for config in ({"devices": [entry, entry]}, {"devices": [{**entry, "enabled": "false"}]}, {"devices": [{**entry, "entity_id": "switch.x"}]}):
            with self.assertRaises(ValueError):
                app.configured_devices(config)

    def test_supervisor_uses_self_endpoint_and_validates_envelope(self):
        with patch.object(app, "ha_request", return_value={"result": "ok", "data": {"options": {}}}) as request:
            self.assertEqual(app.supervisor_request("/addons/self/info"), {"options": {}})
            self.assertTrue(request.call_args.kwargs["supervisor"])
        with self.assertRaises(ValueError):
            app.supervisor_request("/addons/other/options")
        with patch.object(app, "ha_request", return_value={"result": "error"}):
            with self.assertRaises(RuntimeError):
                app.supervisor_request("/addons/self/info")


class MetadataTests(unittest.TestCase):
    def tearDown(self):
        metadata.stop_icy_workers()

    def test_stop_resume_does_not_revive_old_worker(self):
        cancel = threading.Event()
        with patch.dict(metadata.ICY_CANCEL, {"wdr2": cancel}, clear=True):
            metadata.stop_icy_workers()
            metadata.resume_icy_workers()
            self.assertTrue(cancel.is_set())
            with patch.object(metadata, "urlopen") as request:
                metadata._icy_worker("wdr2", cancel)
                request.assert_not_called()

    def test_late_open_closed_without_read_after_cancellation(self):
        cancel = threading.Event()
        stream = Mock()
        stream.__enter__ = Mock(side_effect=lambda: (cancel.set(), stream)[1])
        stream.__exit__ = Mock(return_value=False)
        with patch.object(metadata, "urlopen", return_value=stream):
            self.assertEqual(list(metadata._icy_blocks("wdr2", cancel)), [])
        stream.read.assert_not_called()
        stream.__exit__.assert_called_once()

    def test_icy_stop_leaves_final_close_to_reader(self):
        stream = Mock()
        with patch.dict(metadata.ICY_CONNECTIONS, {"wdr2": stream}, clear=True):
            metadata.stop_icy_workers()
        self.assertEqual([c[0] for c in stream.mock_calls], ["fp.raw._sock.shutdown"])

    def test_real_blocked_response_is_interrupted(self):
        client, peer = socket.socketpair()
        client.settimeout(2)
        self.addCleanup(client.close)
        self.addCleanup(peer.close)
        peer.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 100000\r\n\r\n")
        response = HTTPResponse(client)
        response.begin()
        entered = threading.Event()
        def read():
            entered.set()
            try:
                metadata._read_exact(response, 8192)
            except (EOFError, OSError, ValueError):
                pass
        thread = threading.Thread(target=read, daemon=True)
        with patch.dict(metadata.ICY_CONNECTIONS, {"wdr2": response}, clear=True):
            thread.start()
            self.assertTrue(entered.wait(1))
            before = time.monotonic()
            metadata.stop_icy_workers()
            self.assertLess(time.monotonic()-before, 0.5, "Stop waited on the reader lock")
        thread.join(3)
        self.assertFalse(thread.is_alive(), "Blocked stream reader survived shutdown")

    def test_icy_response_read_and_close_have_one_owner_during_cancellation(self):
        client, peer = socket.socketpair()
        client.settimeout(2)
        self.addCleanup(client.close)
        self.addCleanup(peer.close)
        peer.sendall(b"HTTP/1.1 200 OK\r\nicy-metaint: 8192\r\nContent-Length: 100000\r\n\r\n")
        entered = threading.Event()
        closes, errors = [], []
        class Response(HTTPResponse):
            def read(self, amount):
                entered.set()
                return super().read(amount)
            def close(self):
                closes.append(threading.get_ident())
                super().close()
        response = Response(client)
        response.begin()
        cancel = threading.Event()
        def read():
            try:
                list(metadata._icy_blocks("wdr2", cancel))
            except (EOFError, OSError, ValueError):
                pass
            except Exception as exc:
                errors.append(exc)
        thread = threading.Thread(target=read, daemon=True)
        with patch.object(metadata, "urlopen", return_value=response), patch.dict(metadata.ICY_CANCEL, {"wdr2": cancel}, clear=True):
            thread.start()
            self.assertTrue(entered.wait(1))
            metadata.stop_icy_workers()
            thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(closes)
        self.assertEqual(set(closes), {thread.ident})

    def test_icy_partial_reads_and_eof(self):
        self.assertEqual(metadata._read_exact(io.BytesIO(b"abcd"), 4), b"abcd")
        with self.assertRaises(EOFError):
            metadata._read_exact(io.BytesIO(b"a"), 2)
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(EOFError):
            metadata._read_exact(io.BytesIO(b"abcd"), 4, cancel)

    def test_amazon_closes_radio_and_suspended_selection_ignored(self):
        with patch.object(metadata_feed.threading, "Thread"):
            monitor = metadata_feed.MetadataMonitor()
        with patch.object(metadata_feed, "stop_icy_workers") as stop, patch.object(metadata_feed, "resume_icy_workers"):
            monitor.resume()
            monitor.select("wdr2")
            monitor.select("charts")
            self.assertEqual(monitor.station, "")
            self.assertEqual(stop.call_count, 2)
            monitor.stop()
            monitor.select("wdr2")
            self.assertEqual(monitor.station, "")


class SecurityTests(unittest.TestCase):
    def body_handler(self, headers, body):
        handler = object.__new__(app.Handler)
        handler.headers = Message()
        for name, value in headers:
            handler.headers[name] = value
        handler.rfile = io.BytesIO(body)
        return handler

    def test_streamed_power_command_over_real_http(self):
        class LocalHandler(app.Handler):
            def ingress_allowed(self):
                return True  # Only this loopback-bound test server bypasses ingress.

            def log_message(self, *args):
                pass

        server = app.ThreadingHTTPServer(("127.0.0.1", 0), LocalHandler)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        try:
            with patch.object(app, "perform") as perform:
                with socket.create_connection(server.server_address, timeout=2) as client:
                    # Supervisor ingress_stream forwards POST with chunked framing.
                    client.sendall(b'POST /api/radio_power HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nTransfer-Encoding: chunked\r\n\r\n4\r\n{"on\r\n7;proxy=test\r\n":true}\r\n0\r\nX-Test: ingress\r\n\r\n')
                    response = bytearray()
                    while part := client.recv(4096):
                        response.extend(part)
                self.assertIn(b"200 OK", response)
                self.assertIn(b'"ok": true', response)
                perform.assert_called_once_with("radio_power", {"on": True})
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)

    def test_chunked_and_fixed_length_have_same_json_body(self):
        expected = b'{"on":false}'
        for headers, body in (([("Content-Length", "12")], expected),
                              ([("Transfer-Encoding", "Chunked")], b'C\r\n' + expected + b'\r\n0\r\n\r\n')):
            self.assertEqual(self.body_handler(headers, body).read_request_body(), expected)

    def test_ambiguous_or_missing_framing_rejected(self):
        for headers in ([], [("Content-Length", "1"), ("Content-Length", "1")],
                        [("Content-Length", "1"), ("Transfer-Encoding", "chunked")],
                        [("Transfer-Encoding", "gzip, chunked")],
                        [("Transfer-Encoding", "chunked"), ("Transfer-Encoding", "chunked")],
                        [("Content-Length", "-1")], [("Content-Length", "abc")]):
            with self.subTest(headers=headers), self.assertRaises(ValueError):
                self.body_handler(headers, b'x').read_request_body()

    def test_oversized_and_truncated_request_bodies_rejected(self):
        cases = [([("Content-Length", "2049")], b""), ([("Content-Length", "2")], b"x"),
                 ([("Transfer-Encoding", "chunked")], b"801\r\n"),
                 ([("Transfer-Encoding", "chunked")], b"800\r\n" + b"x" * 2048 + b"\r\n1\r\nx\r\n0\r\n\r\n")]
        for headers, body in cases:
            with self.subTest(body_size=len(body)), self.assertRaises(ValueError):
                self.body_handler(headers, body).read_request_body()

    def test_invalid_chunk_framing_rejected_without_dispatch(self):
        for body in (b"0\r\n\r\n", b"z\r\nx\r\n", b"1\nx\n0\n\n", b"2\r\nx", b"1\r\nxXX",
                     b"1\r\nx\r\n0\r\ninvalid-trailer\r\n\r\n", b"1;" + b"x" * 255 + b"\r\nx\r\n0\r\n\r\n",
                     b"1\r\nx\r\n0\r\n" + (b"X-Test: " + b"y" * 200 + b"\r\n") * 90):
            handler = self.body_handler([("Transfer-Encoding", "chunked"), ("Content-Type", "application/json")], body)
            handler.client_address = ("172.30.32.2", 1)
            handler.path = "/api/radio_power"
            handler.reply = Mock()
            with self.subTest(body_size=len(body)), patch.object(app, "perform") as perform:
                handler.do_POST()
                self.assertEqual(handler.reply.call_args.args[0], 400)
                perform.assert_not_called()

    def test_body_deadline_applies_across_chunks(self):
        handler = self.body_handler([("Transfer-Encoding", "chunked")], b"1\r\nx\r\n0\r\n\r\n")
        with patch.object(app.time, "monotonic", side_effect=[0, 1, 21]), self.assertRaises(TimeoutError):
            handler.read_request_body()

    def test_room_post_returns_restored_volume_to_browser(self):
        handler = object.__new__(app.Handler)
        handler.client_address = ("172.30.32.2", 1)
        handler.path = "/api/room_audio"
        body = json.dumps({"entity_id": "media_player.kueche", "on": True}).encode()
        handler.headers = Message()
        handler.headers["Content-Type"] = "application/json"
        handler.headers["Content-Length"] = str(len(body))
        handler.rfile = io.BytesIO(body)
        handler.reply = Mock()
        with patch.object(app, "perform", return_value={"ok": True, "volume": 0.45}):
            handler.do_POST()
        handler.reply.assert_called_once_with(200, {"ok": True, "volume": 0.45})

    def test_ingress_peer(self):
        handler = object.__new__(app.Handler)
        handler.client_address = ("172.30.33.8", 1234)
        self.assertFalse(handler.ingress_allowed())
        handler.client_address = ("172.30.32.2", 1234)
        self.assertTrue(handler.ingress_allowed())

    def test_post_rejects_simple_and_cross_site_requests(self):
        for content_type, site, expected in [("text/plain", "same-origin", 415), ("application/json", "cross-site", 403)]:
            handler = object.__new__(app.Handler)
            handler.client_address = ("172.30.32.2", 1)
            handler.path = "/api/radio_power"
            handler.headers = Message()
            handler.headers["Content-Type"] = content_type
            handler.headers["Sec-Fetch-Site"] = site
            handler.reply = Mock()
            handler.do_POST()
            self.assertEqual(handler.reply.call_args.args[0], expected)


if __name__ == "__main__":
    unittest.main()

