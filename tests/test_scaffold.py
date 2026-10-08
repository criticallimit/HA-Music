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
assert "now-playing" in js
assert "echo_entities" in config
assert "save_remembered" in source
assert "allowed_entities" in source
print("Scaffold validation passed")
