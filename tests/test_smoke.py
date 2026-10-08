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

# A group volume change is permitted, but unregistered players are rejected.
with patch.object(app, "classify_devices", return_value={"groups":[{"name":"Wohnung","entity_id":"media_player.wohnung","state":"idle","volume":0.3}],"players":[],"excluded":[]}):
    with patch.object(app,"ha_request",return_value={} ) as mocked:
        app.perform("volume",{"entity_id":"media_player.wohnung","volume":0.4})
        assert mocked.call_args.args[1]["entity_id"] == "media_player.wohnung"
        try:
            app.perform("volume",{"entity_id":"media_player.unknown","volume":0.4})
            raise AssertionError("Unknown device accepted")
        except ValueError:
            pass
with patch.object(app, "ha_request", return_value=[
    {"entity_id":"media_player.wohnzimmer","state":"playing",
     "attributes":{"media_title":"Testtitel","media_artist":"Künstler","entity_picture":"/api/media_player_proxy/test"}},
    {"entity_id":"media_player.wohnung","state":"idle","attributes":{}}
]):
    result = app.playback_status()
    assert result["playing"] is True
    assert result["details"]["title"] == "Testtitel"
