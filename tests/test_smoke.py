"""Smoke tests for HA Alexa integration entity discovery."""
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ha_music"))
with tempfile.TemporaryDirectory() as temp:
    os.environ["VOLUME_FILE"] = str(Path(temp) / "volumes.json")
    import app
    states = [
        {"entity_id": "media_player.kueche", "state": "playing", "attributes": {"friendly_name": "Küche", "volume_level": 0.4}},
        {"entity_id": "media_player.wohnung", "state": "idle", "attributes": {"friendly_name": "Wohnung", "volume_level": 0.3}},
        {"entity_id": "media_player.fire_tv", "state": "idle", "attributes": {"friendly_name": "Dirks Fire TV", "volume_level": 0.0}},
        {"entity_id": "media_player.tv", "state": "playing", "attributes": {"friendly_name": "TV", "volume_level": 0.6}}
    ]
    def request(path, payload=None):
        if path == "/template": return '{"alexa_devices":["media_player.kueche","media_player.wohnung","media_player.fire_tv"],"alexa_media":[]}'
        if path == "/states": return states
        return {}
    with patch.object(app, "ha_request", side_effect=request):
        assert [p["entity_id"] for p in app.players()] == ["media_player.kueche"]
        assert app.allowed_entities() == {"media_player.kueche"}
        assert [x["name"] for x in app.classify_devices()["groups"]] == ["Wohnung"]
        assert [x["name"] for x in app.classify_devices()["excluded"]] == ["Dirks Fire TV"]
        try:
            app.perform("radio", {"station": "wdr2"})
            raise AssertionError("Voice commands must not be enabled")
        except ValueError: pass
    with patch.object(app, "TOKEN", "test-token"):
        with patch.object(app, "urlopen") as fake:
            resp = fake.return_value.__enter__.return_value
            resp.read.return_value = b'["media_player.kueche"]'
            assert app.ha_request("/template", {"template": "test"}) == '["media_player.kueche"]'
            resp.read.return_value = b""
            assert app.ha_request("/services/media_player/volume_set", {"volume_level": 0.4}) == {}
    app.save_remembered("media_player.kueche", 0.4)
    assert app.remembered()["media_player.kueche"] == 0.4
print("HA Music smoke checks passed")

# WDR 2 test is unavailable unless Wohnung explicitly advertises PLAY_MEDIA.
with patch.object(app, "classify_devices", return_value={"groups":[{"name":"Wohnung","entity_id":"media_player.wohnung","state":"idle","features":0}],"players":[],"excluded":[]}):
    assert app.playback_capability()["available"] is False
    try:
        app.perform("test_wdr2", {})
        raise AssertionError("Unsupported group must not receive a play command")
    except ValueError:
        pass
with patch.object(app, "classify_devices", return_value={"groups":[{"name":"Wohnung","entity_id":"media_player.wohnung","state":"idle","features":512}],"players":[],"excluded":[]}):
    with patch.object(app, "ha_request", return_value={}) as request:
        app.perform("test_wdr2", {})
        assert request.call_args.args[0] == "/services/media_player/play_media"
        assert request.call_args.args[1]["entity_id"] == "media_player.wohnung"

with patch.object(app, "classify_devices", return_value={"groups":[{"name":"Wohnung","entity_id":"media_player.wohnung","state":"idle","features":512}],"players":[],"excluded":[]}):
    with patch.object(app, "ha_request", return_value={}) as mocked:
        app.perform("test_tunein_wdr2", {})
        assert mocked.call_args.args[1]["media_content_type"] == "TUNEIN"
        assert mocked.call_args.args[1]["media_content_id"] == "WDR 2"

# Radio actions are restricted to the seven known Home Assistant script entities.
with patch.object(app, "ha_request") as mock:
    mock.side_effect = lambda path, payload=None: ([{"entity_id": "script.radio_wdr2_uberall", "state": "off"}, {"entity_id":"switch.alexa_alle","state":"off"}] if path == "/states" else {})
    app.perform("radio_script", {"station":"wdr2"})
    assert mock.call_args.args[0] == "/services/script/turn_on"
    assert mock.call_args.args[1]["entity_id"] == "script.radio_wdr2_uberall"
    app.perform("radio_power", {"on":True})
    assert mock.call_args.args[0] == "/services/switch/turn_on"
    try:
        app.perform("radio_script", {"station":"arbitrary"})
        raise AssertionError("Unknown script key must be rejected")
    except ValueError:
        pass
