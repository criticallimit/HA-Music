#!/usr/bin/with-contenv sh
set -eu

# The homeassistant_config mount uses /homeassistant, as in TV Guide.
www_dir="/homeassistant/www"
mkdir -p "$www_dir"

for name in ha-music-card.js ha-music-card-loader.js; do
  source="/app/lovelace/$name"
  target="$www_dir/$name"
  test -s "$source" || { echo "[HA Music] ERROR: missing source: $source" >&2; exit 1; }
  cp "$source" "$www_dir/.$name.tmp"
  mv "$www_dir/.$name.tmp" "$target"
  test -s "$target" || { echo "[HA Music] ERROR: missing installed resource: $target" >&2; exit 1; }
  echo "[HA Music] Installed Lovelace resource: $target"
done

exec python3 /app/app.py
