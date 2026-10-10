# HA Music Playlist Sync für Mac

Eine native Mac-App zum Anklicken, für Intel und Apple Silicon ab macOS 12.
Keine zusätzliche Python-Installation und keine Terminalbefehle nötig.

## Herunterladen und öffnen

Die [fertige Mac-App-ZIP in tools](https://github.com/criticallimit/HA-Music/raw/refs/heads/main/tools/HA-Music-Playlist-Sync.zip)
herunterladen, entpacken und **HA Music Playlist Sync.app** nach Programme ziehen.
Doppelklicken. Zusätzlich enthält der neueste erfolgreiche
[Check-Lauf auf main](https://github.com/criticallimit/HA-Music/actions/workflows/check.yml)
unter **Artifacts** das frisch gebaute Paket **HA-Music-Playlist-Sync-Mac** mit
Anleitung und Vorschau (GitHub-Anmeldung erforderlich, 90 Tage verfügbar).
Es wird kein GitHub-Release veröffentlicht.

Die App ist ad hoc signiert, nicht mit Apple Developer ID signiert oder notarisiert.
macOS kann sie deshalb beim ersten Start blockieren. Für die selbst aus diesem
Projekt bezogene App unter **Systemeinstellungen → Datenschutz & Sicherheit →
Dennoch öffnen** den ersten Start bestätigen. Gatekeeper nicht deaktivieren.

## Einmal einrichten

1. Das HA-Music-Add-on muss den aktuellen `main`-Stand enthalten.
2. In der Mac-Musik-App **Mediathek synchronisieren** aktivieren und warten,
   bis deine Playlists vollständig sichtbar sind.
3. In der Sync-App die Adresse eingeben, etwa `http://homeassistant.local:8099`.
   Das ist der HA-Music-Netzwerkport, nicht Home Assistant auf Port 8123.
4. Deinen vorhandenen Sync-Schlüssel eingeben oder **Neuer Schlüssel** und
   **Kopieren** klicken. Im HA-Music-Add-on unter **Schlüssel für
   Mac-Playlist-Synchronisierung** (`playlist_sync_token`) speichern.
5. Im Add-on unter **Netzwerk** den optionalen Port `8099/tcp` auf Host-Port 8099
   setzen (gegebenenfalls deaktivierte Ports anzeigen), speichern und neu starten.
   Einen anderen Host-Port auch in der App-Adresse angeben. Nur im
   vertrauenswürdigen Heimnetz freigeben; keine Router-Portweiterleitung.
6. macOS-Zugriff auf Musik erlauben. Die App liest deine Playlists automatisch ein.
   Gewünschte Playlists ankreuzen, den Namen in HA Music bei Bedarf ändern und
   **Jetzt synchronisieren** klicken.

Mit **Fehlende Playlists in HA Music anlegen** werden neue Playlist-Favoriten
automatisch angelegt. Ihr anfänglicher Alexa-Befehl lautet `spiel playlist NAME`
und kann später in HA Music bearbeitet werden. Bestehende Alexa-Befehle und Alben
bleiben erhalten. Du kannst diese Auswahl ausschalten, um ausschließlich
vorhandene Playlists zu aktualisieren. Der Sync-Schlüssel erlaubt diese beiden
Playlist-Operationen und die Übertragung zugehöriger Coverbilder, keine Wiedergabe
oder andere Add-on-Funktionen.

## Danach reicht Öffnen

Die App überträgt Album, Albuminterpret und verfügbare Coverbilder aus der Musik-App.
Cover werden als kleine JPEG-Bilder übertragen; identische Bilder nur einmal pro
Synchronisierung. HA Music speichert die Bilder dauerhaft im eigenen Datenverzeichnis
und liefert sie lokal aus. Die App zeigt die Anzahl übertragener Cover und Titel
ohne verfügbares Bild an. Vorhandene Playlists einmal mit der aktuellen App erneut
synchronisieren. Der Albumname wird auch im Einzeltitelbefehl verwendet.

Nur fehlende Bilder werden im öffentlichen Apple-Katalog gesucht und bei eindeutigem
Treffer dauerhaft lokal gespeichert. Ohne Treffer bleibt ein Musik-Platzhalter;
fehlende Cover werden frühestens nach 24 Stunden erneut gesucht. Gespeicherte Bilder
haben keine Ablauffrist. Diese Regeln gelten auch für Albumkacheln. Titel ohne
Albumangabe verwenden Titel und Interpret für die Coversuche und bleiben abspielbar.
Bei Standby werden keine Suchanfragen ausgeführt.

Adresse und Auswahl werden gespeichert, der Schlüssel im macOS-Schlüsselbund.
Bei aktivem **Beim Öffnen synchronisieren** reicht künftig ein Doppelklick auf
die App. Die Musik-App wird erneut ausgelesen und die ausgewählten Listen werden
übertragen. Playlists werden intern über ihre stabile Musik-ID zugeordnet;
gleichnamige Mac-Playlists können unterschiedliche HA-Music-Namen bekommen.

Optional wird alle 30 Minuten synchronisiert, solange die App geöffnet bleibt.
Der Mac muss wach sein und die Mediathek aktuell sein. App schließen beendet
diesen Zeitplan. Der separate ältere Python-Helfer wird dadurch nicht abgeschaltet;
falls eingerichtet, dessen Zeitplan zuerst wie in
[MAC_PLAYLIST_SYNC.md](MAC_PLAYLIST_SYNC.md#abschalten) beschrieben abschalten.

Bis zu 50 Favoriten in HA Music und 1000 Titel pro Playlist. Die Reihenfolge und
doppelte Titel bleiben erhalten. Leere oder unvollständige Listen überschreiben
keine gespeicherten Titel. Fehler werden in der App angezeigt; erfolgreich
übertragene Playlists bleiben gespeichert, andere behalten ihren alten Stand.
Eine bereits offene Titelauswahl in HA Music nach Änderungen erneut öffnen.
Die App spielt keine Musik ab und verändert weder Radio noch Lautstärke/Standby.

## Prüfungen und Grenzen

GitHub-CI baut und prüft eine Universal-App für beide Architekturen,
testet Übertragung und Zugriffsschutz mit einem lokalen HTTP-Server und erstellt
eine Vorschau der tatsächlichen App-Oberfläche. Die echte Musikmediathek,
Schlüsselbundfreigabe und macOS-Automationsberechtigung sind auf deinem Mac zu
prüfen. Unter **Datenschutz & Sicherheit → Automation** kann der Musik-Zugriff
nachträglich erlaubt werden. Ein Update der ad hoc signierten App kann erneut
eine Berechtigungs- oder Schlüsselbundrückfrage verursachen.

Für Entwickler: `bash tools/mac-app/build.sh` baut auf einem Mac mit Xcode-
Command-Line-Tools. Nutzer benötigen diese Werkzeuge nicht.
