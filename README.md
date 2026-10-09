# HA Music

Home-Assistant-Add-on für Alexa-Multiroom-Radio mit Ingress-Oberfläche und Lovelace-Karte. Entwicklungsstand auf `main`, Version **0.0.6**. Apple Music ist weiterhin ein Platzhalter; es gibt keinen Apple-Music-Login.

## Installation

`https://github.com/criticallimit/HA-Music` als Add-on-Repository in Home Assistant hinzufügen. Änderungen ohne Versionssprung erfordern einen **Neuaufbau** des Add-ons. Einrichtung der Karte: [Dashboard-Anleitung](ha_music/DASHBOARD.md).

## Aktuelles Verhalten

- Nach Add-on-Start bleibt das Netzwerk im Standby. Nur der Einschaltbutton in HA Music startet den Ablauf; ein externer Schalterwechsel oder Browserreload startet keinen Sender.
- Nach Einschalten: 45 Sekunden warten, Alexa-Zustände aktualisieren, bekannte aktive Räume auf 1% setzen, zwei Sekunden warten, gespeicherten Sender anfordern und individuelle Lautstärken wiederherstellen. Stumme Räume und ein stummer Master bleiben bei 0%.
- Die 50-Sekunden-Anzeige ist eine Schätzung. Ready wird erst nach erfolgreichen Serviceaufrufen gesetzt; hörbare Wiedergabe kann später beginnen. Fehler sind sichtbar, Lautstärkebestätigungen erfolgen asynchron.
- Beim Ausschalten werden Startabläufe und Metadaten abgebrochen. Nach zehn Sekunden sperrt das Add-on neue HA-Anfragen. Grenzen bereits laufender Netzwerk-/Alexa-Aufträge: [Prüfbericht](AUDIT.md).
- Raumregler speichern ihre Sollwerte. `*` bedeutet: HA meldet noch einen anderen Wert. Stummschaltung setzt die Lautstärke auf 0, ohne Räume aus einer Alexa-Gruppe zu entfernen.
- Sender, Radio-/Apple-Ansicht und Lautstärkewünsche liegen dauerhaft unter `/data`. Die Radioansicht während Standby überschreibt die gespeicherte Ansicht nicht.
- Alexa-Entities werden über `integration_entities` der Integrationen `alexa_devices` und `alexa_media` erkannt. `Wohnung` wird als Gruppe, Fire TV / This Device werden separat behandelt.

Die konkrete Installation verwendet weiterhin `switch.alexa_alle`, `input_boolean.alexa_hochgefahren`, `media_player.wohnung`, `media_player.wohnzimmer`, `media_player.kueche` und `media_player.bad`. Diese Namen sind derzeit nicht konfigurierbar. Die Sender-Phrasen setzen die vorhandene Alexa-Gruppe Wohnung voraus.

## Entwicklung und Prüfung

```sh
python -m py_compile ha_music/app.py ha_music/metadata.py ha_music/metadata_feed.py ha_music/logo_sources.py
python tests/test_scaffold.py
python tests/test_smoke.py
python -m unittest discover -s tests -p 'test_runtime.py' -v
node --test tests/frontend.test.cjs
node --check ha_music/web/app.js
node --check ha_music/lovelace/ha-music-card.js
node --check ha_music/lovelace/ha-music-card-loader.js
```

Die CI führt diese Prüfungen bei Push auf `main` aus. Entwicklung direkt auf `main`; Releases nur nach ausdrücklicher Freigabe. Befunde, Änderungen, Testumfang und verbleibende Risiken: [Codeprüfung vom 9. Oktober 2026](AUDIT.md).
