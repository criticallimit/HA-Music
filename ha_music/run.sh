#!/usr/bin/with-contenv sh
set -eu
mkdir -p /homeassistant/www
cp /app/lovelace/ha-music-card.js /homeassistant/www/.ha-music-card.js.tmp
cp /app/lovelace/ha-music-card-loader.js /homeassistant/www/.ha-music-card-loader.js.tmp
mv /homeassistant/www/.ha-music-card.js.tmp /homeassistant/www/ha-music-card.js
mv /homeassistant/www/.ha-music-card-loader.js.tmp /homeassistant/www/ha-music-card-loader.js
exec python3 /app/app.py
