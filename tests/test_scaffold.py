"""Dependency-free checks for the first development scaffold."""
from pathlib import Path
import ast

root = Path(__file__).resolve().parents[1]
source = (root / "ha_music/app.py").read_text()
ast.parse(source)
html = (root / "ha_music/web/index.html").read_text()
js = (root / "ha_music/web/app.js").read_text()
config = (root / "ha_music/config.yaml").read_text()
for expected in ("Radio", "Apple Music", "In Vorbereitung", "station-list", "Lautsprecher"):
    assert expected in html, expected
for expected in ("radio-page", "apple-page", "radio-tab", "apple-tab"):
    assert expected in html and expected in js, expected
assert "ingress: true" in config
assert "ingress_port: 8099" in config
assert "api/events" in js
assert "echo_entities" not in config
assert "save_remembered" in source
assert "detected_devices" in source
assert "send_text_command" not in source
print("Scaffold validation passed")

# The container must ship every local Python module imported by the entrypoint.
dockerfile = (root / "ha_music/Dockerfile").read_text()
assert "metadata.py" in dockerfile
assert (root / "ha_music/metadata.py").exists()

# Supervisor injects its token through the s6 container environment.
assert 'CMD ["/usr/bin/with-contenv", "python3", "/app/app.py"]' in dockerfile

# The current radio UI shows live metadata and the selected station.
assert 'id="current-title"' in html
assert 'id="now-ticker"' in html
assert 'id="now-ticker-text"' in html
assert 'async function updateSong()' in js
assert 'id="metadata-probe"' not in html
assert 'id="stream-test"' not in html
assert 'stream_test' not in source

# A single shared monitor serves all browser clients; the frontend subscribes to SSE.
backend = (root / "ha_music/metadata_feed.py").read_text()
assert "class MetadataMonitor" in backend
assert "ensure_icy_worker(station)" in backend
assert "self.sequence += 1" in backend
assert 'name == "events"' in source
assert "MONITOR.select(body" in source
assert 'new EventSource("api/events")' in js
assert "if (!isRadio)" in js and "details?.image" in js
assert "metadata_feed.py" in dockerfile
assert "now-playing?station=" not in js
assert "icy-diagnostics" not in source
assert "metadata-probe" not in source
# Station pictures must never be overwritten by delayed Alexa radio artwork.
assert "if (!isRadio) {\n      const cover" in js
print("Radio event-stream and Amazon artwork contracts passed")
