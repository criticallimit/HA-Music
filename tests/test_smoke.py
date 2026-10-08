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
        {"entity_id": "media_player.tv", "state": "playing", "attributes": {"friendly_name": "TV", "volume_level": 0.6}}
    ]
    def request(path, payload=None):
        if path == "/template": return '["media_player.kueche"]'
        if path == "/states": return states
        return {}
    with patch.object(app, "ha_request", side_effect=request):
        assert [p["entity_id"] for p in app.players()] == ["media_player.kueche"]
        assert app.allowed_entities() == {"media_player.kueche"}
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
