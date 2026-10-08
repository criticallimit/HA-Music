"""HA Music smoke checks without Home Assistant network access."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from http.server import HTTPServer
from urllib.request import urlopen
from unittest.mock import patch
from io import BytesIO

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ha_music"))
with tempfile.TemporaryDirectory() as temp:
    os.environ["VOLUME_FILE"] = str(Path(temp) / "volumes.json")
    os.environ["OPTIONS_FILE"] = str(Path(temp) / "options.json")
    import app
    with patch.object(app, "TOKEN", "fake-token"):
        with patch.object(app, "urlopen") as open_mock:
            response = open_mock.return_value.__enter__.return_value
            response.read.return_value = b""
            assert app.ha_request("/services/media_player/volume_set", {"entity_id": "media_player.echo", "volume_level": 0.2}) == {}
            response.read.return_value = b"[]"
            assert app.ha_request("/states") == []
    Path(os.environ["OPTIONS_FILE"]).write_text(json.dumps({
        "echo_entities": "media_player.echo_kitchen, media_player.echo_livingroom, sensor.other",
        "alexa_group_name": "Alle Echo", "command_device_id": "a" * 32
    }))
    assert app.allowed_entities() == {"media_player.echo_kitchen", "media_player.echo_livingroom"}
    app.save_remembered("media_player.echo_kitchen", 0.4)
    assert app.remembered()["media_player.echo_kitchen"] == 0.4
    app.save_remembered("media_player.echo_livingroom", 0.6)
    assert app.remembered()["media_player.echo_kitchen"] == 0.4
    with patch.object(app, "ha_request", return_value=[]):
        assert app.players() == []
    with patch.object(app, "ha_request", return_value=[{
        "entity_id": "media_player.echo_kitchen", "state": "playing",
        "attributes": {"friendly_name": "Küche", "volume_level": 0.4}
    }, {
        "entity_id": "media_player.other", "state": "playing", "attributes": {}
    }]):
        assert [p["entity_id"] for p in app.players()] == ["media_player.echo_kitchen"]
    server = HTTPServer(("127.0.0.1", 0), app.Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        base = "http://127.0.0.1:" + str(server.server_port)
        with urlopen(base + "/sample_ingress_token/") as response:
            assert b"Apple Music" in response.read()
        with urlopen(base + "/sample_ingress_token/api/status") as response:
            assert json.load(response)["apple_music"] == "planned"
        with urlopen(base + "/sample_ingress_token/app.js") as response:
            assert b"now-playing" in response.read()
    finally:
        server.shutdown()
        worker.join(timeout=2)
        server.server_close()
print("HA Music smoke checks passed")
