# Playlist-Sync-Prüfung, 11. Oktober 2026

Ausgangspunkt: `main` bei `37eea4b03f80282e29ea2299f0034c6cb9e39876`.
Vor der ersten Änderung auf GitHub gesichert als
[backup/main-2026-10-11-ipad-sync-37eea4b](https://github.com/criticallimit/HA-Music/tree/backup/main-2026-10-11-ipad-sync-37eea4b).
Dies sichert Repository-Code, keine Add-on-Daten oder Zugangsdaten.

## Ergebnis

Nach Nutzerentscheidung verwendet der iPad-Kurzbefehl den bestehenden Mac-Transport:
kleine JPEG-Cover separat als `{cover, data}` an `/api/playlist-artwork-sync`,
danach `{name, tracks, create}` an `/api/playlist-sync`. Keine zusätzliche Uploadart,
keine neuen Endpunkte und keine Änderung von Add-on-Version, Alexa oder Wiedergabe.
Die Mac-App bereitet Cover auf 320×320 bei JPEG-Qualität 0,75 auf (maximal 128 KiB).
Der ältere Python-Helfer sendet nur Titel/Interpret. Der Server verändert empfangene
Bildbytes nicht. Die iPad-Anleitung verwendet eine vergleichbare JPEG-Aufbereitung;
Originalauflösung wird dabei bewusst nicht erhalten.

| Fehler | Prüfung/Korrektur |
|---|---|
| Wiederholungsergebnisse als verschachtelte JSON-Strings | Gemeinsamer Parser akzeptiert Wörterbuchlisten, JSON-Arrays als Text, einzelne Wörterbücher und zeilenweise JSON, begrenzt auf acht Entpackungsschritte. Ungültige Werte bleiben Fehler. |
| Playlistname statt Titelname | Anleitung speichert äußeres und inneres Wiederholungselement ausdrücklich als getrennte Variablen. Der Server kann semantisch falsche, aber gültige Namen nicht erkennen. |
| Dauer `m:ss` | Gemeinsamer Mac-Endpunkt normalisiert Sekunden, `m:ss` und `h:mm:ss`, danach unveränderte Werteprüfung. |
| `Ingress only` | Anleitung verwendet direkten optionalen Sync-Port und exakte Pfade. Ingress-Schutz und Token-Grenzen bleiben unverändert. |
| Ungültiges Bild / großer PNG-Base64-Body | JPEG aus dem Albumcover erstellen, erst danach Hash und Base64 ohne Zeilenumbrüche; Bilder getrennt von Metadaten senden. |
| Doppelte Coverprüfungen | Kurzbefehl prüft neue Hashes gebündelt, merkt erfolgreiche Hashes innerhalb des Laufs und lädt nur fehlende Bilder. Der ältere iPad-Batch-Endpunkt validiert jede referenzierte Datei jetzt einmal je Request. |
| Unvollständige Titelliste | Optionales `track_count` muss zur tatsächlichen Liste passen. Fehler vor dem Schreiben. Leere iPad-Listen im Kurzbefehl überspringen; ausdrücklich erlaubte leere Mac-Listen bleiben kompatibel. |

Cover-Speicherung nutzt eine gemeinsame atomare Routine; der alte Batch-Import muss
bereits dekodierte Cover nicht erneut als Base64 codieren und dekodieren. Vorhandene
Playlists, optionale Metadaten, Favoriten, doppelte Titel und Alexa-Befehle bleiben
nach den bisherigen Regeln erhalten. Mac und iPad übertragen je Playlist; ein späterer
Fehler rollt frühere erfolgreich übertragene Playlists nicht zurück. Der alte
`/api/ipad-playlists-sync` bleibt für bereits eingerichtete Clients kompatibel.

## Validierung und praktische Grenzen

25 HTTP-/Mac-Helfer-Sync-Tests, darunter neue Regressionen für verschachtelte Strings,
denselben JPEG-/JSON-Transport beider Geräte, Wiederholung ohne Änderung, unvollständige
Listen, deduplizierte Covervalidierung, begrenzte Verschachtelung und Token-Rotation.
Alle lokal ausführbaren Prüfungen aus `.github/workflows/check.yml` bestanden,
einschließlich Chromium-Layouts mit dem festgelegten Playwright 1.62.1.
Die native Mac-App wird im unveränderten macOS-CI-Job gebaut und geprüft;
der vollständige CI-Status muss für den veröffentlichten main-Commit geprüft werden.

Keine echte iPad-/Mac-Musikmediathek, kein laufendes Add-on und kein Echo wurden hier
verändert oder live geprüft. Apples Bibliotheksvollständigkeit, tatsächliche
Kurzbefehl-Ausgabe und zeitgesteuerte iPad-Ausführung erfordern einen Gerätetest.
`track_count` prüft die Vollständigkeit der Übertragung, nicht der Apple-Mediathek.
Der vorherige Chat war nur als begrenzte Vorschau abrufbar; sein Screenshot und
der private Kurzbefehl wurden nicht vollständig ausgelesen.

Der sichtbar gewordene Token muss vom Nutzer im laufenden Add-on und auf den
Sync-Geräten ersetzt werden, wie ausdrücklich vereinbart. Der neue Schlüssel
gehört nicht in Chat oder Repository. Regressionen verwenden ausschließlich Testschlüssel.
Anleitung: [IPAD_PLAYLIST_SYNC.md](IPAD_PLAYLIST_SYNC.md).
