"""Smoke tests for conservative automatic Echo discovery and Ingress."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from http.server import HTTPServer
from urllib.request import urlopen
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ha_music"))
with tempfile.TemporaryDirectory() as temp:
    os.environ["VOLUME_FILE"] = str(Path(temp) / "volumes.json")
    import app
    states = [
        {"entity_id": "media_player.echo_kitchen", "state": "playing", "attributes": {"friendly_name": "Echo Küche", "volume_level": 0.4}},
        {"entity_id": "media_player.alexa_group", "state": "playing", "attributes": {"friendly_name": "Alexa Multiroom Gruppe", "volume_level": 0.3}},
        {"entity_id": "media_player.tv", "state": "playing", "attributes": {"friendly_name": "TV", "volume_level": 0.6}},
    ]
    with patch.object(app, "ha_request", return_value=states):
        assert [p["entity_id"] for p in app.players()] == ["media_player.echo_kitchen"]
        assert app.allowed_entities() == {"media_player.echo_kitchen"}
        assert len([p for p in app.detected_devices() if p["possible_group"]]) == 1
        try:
            app.perform("radio", {"station": "wdr2"})
            raise AssertionError("Old Alexa voice command should not exist")
        except ValueError:
            pass
    app.save_remembered("media_player.echo_kitchen", 0.4)
    assert app.remembered()["media_player.echo_kitchen"] == 0.4
    with patch.object(app, "TOKEN", "fake-token"):
        with patch.object(app, "urlopen") as mocked:
            response = mocked.return_value.__enter__.return_value
            response.read.return_value = b""
            assert app.ha_request("/services/media_player/volume_set", {"volume_level": 0.4}) == {}
    server = HTTPServer(("127.0.0.1", 0), app.Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        url = "http://127.0.0.1:" + str(server.server_port)
        with urlopen(url + "/test_ingress/") as response:
            assert b"Apple Music" in response.read()
        with urlopen(url + "/test_ingress/api/status") as response:
            assert json.load(response)["radio"] == "direct_playback_pending"
    finally:
        server.shutdown()
        worker.join(timeout=2)
        server.server_close()
print("HA Music smoke checks passed")
