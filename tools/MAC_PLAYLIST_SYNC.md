# Apple-Music-Playlists automatisch vom Mac synchronisieren

Für Einrichtung ohne Terminal/Python gibt es jetzt auch die
[anklickbare Mac-App](MAC_PLAYLIST_APP.md). Der folgende separate Helfer bleibt
für einen Zeitplan unabhängig vom geöffneten App-Fenster verfügbar.

Der Mac liest die ausgewählten Playlists aus seiner Musik-App und aktualisiert
alle 30 Minuten ihre Titellisten in HA Music. Kein Apple-Developer-Konto, keine
Musikdateien, keine Apple-Zugangsdaten. Die Synchronisierung startet keine
Wiedergabe. Namen, Alexa-Befehle, Alben, Lautstärke und Radio bleiben erhalten.

## Einmalige Einrichtung

1. Die neue Version aus `main` muss im HA-Music-Add-on installiert sein.
   Ein GitHub-Commit allein aktualisiert das laufende Add-on nicht.
2. Auf dem Mac in Musik mit deinem Apple Account anmelden und unter
   **Musik → Einstellungen → Allgemein → Mediathek synchronisieren** aktivieren.
   Warten, bis die gewünschten Playlists und Titel vollständig sichtbar sind.
3. Die Zielplaylists in HA Music anlegen. Jeder angezeigte Playlistname muss
   eindeutig sein. Der Mac-Name darf abweichen; die Einrichtung fragt beide ab.
4. Python 3 auf dem Mac bereitstellen (falls `python3 --version` nicht funktioniert:
   [Python für macOS](https://www.python.org/downloads/macos/)).
5. [mac_playlist_sync.py herunterladen](https://raw.githubusercontent.com/criticallimit/HA-Music/main/tools/mac_playlist_sync.py)
   und im Ordner Downloads als `mac_playlist_sync.py` speichern.
6. Terminal öffnen und ausführen:

   ```sh
   python3 ~/Downloads/mac_playlist_sync.py --setup
   ```

7. Die Adresse des HA-Music-Synchronisierungsports eingeben, beispielsweise
   `http://homeassistant.local:8099` oder `http://192.168.1.20:8099`.
   **Keine** Lovelace-, Ingress- oder Home-Assistant-Adresse mit Port 8123 verwenden.
   Danach die Playlistnamen eingeben; leere Eingabe beendet die Auswahl.
8. Den vom Helfer angezeigten Schlüssel in der Add-on-Konfiguration unter
   **Schlüssel für Mac-Playlist-Synchronisierung** (`playlist_sync_token`)
   speichern. Unter **Netzwerk** den bisher deaktivierten Port `8099/tcp` auf
   Host-Port `8099` setzen (bei Bedarf „deaktivierte Ports anzeigen“ einschalten).
   Speichern und Add-on neu starten. Bei einem anderen Host-Port die Mac-Adresse
   entsprechend anpassen. Nur im vertrauenswürdigen Heimnetz verwenden;
   keine Router-Portweiterleitung. Für Übertragung außerhalb des Heimnetzes
   wird HTTPS mit vertrauenswürdigem Zertifikat benötigt.
9. Im Terminal Enter drücken. macOS fragt gegebenenfalls, ob Python/Terminal
   die Musik-App steuern darf: erlauben. Nach erfolgreichem Test wird der
   Zeitplan installiert. Bei einem Fehler bleibt der Zeitplan uninstalliert;
   Ursache prüfen und `--setup` erneut ausführen. Der vorhandene Schlüssel wird
   bei erneuter Einrichtung weiterverwendet.

Nach der Einrichtung auch den ersten automatischen Hintergrundlauf in den
Protokollen prüfen. macOS kann für diesen Ausführungskontext eine zusätzliche
Automationsfreigabe verlangen; ein erfolgreicher Terminal-Test allein bestätigt
noch nicht den Hintergrundbetrieb.

Der Mac muss eingeschaltet und dein Benutzer angemeldet sein. Nach Ruhemodus
oder Ausschalten wird beim nächsten Lauf nach dem Aufwachen beziehungsweise
Anmelden wieder synchronisiert. Dazwischen bleiben die zuletzt gespeicherten
Listen verfügbar. Eine bereits offene Titelauswahl nach Änderungen neu öffnen.

## Kontrolle und Änderungen

Manuell synchronisieren:

```sh
python3 "$HOME/Library/Application Support/HA Music Playlist Sync/mac_playlist_sync.py"
```

Konfiguration: `~/Library/Application Support/HA Music Playlist Sync/config.json`.
Sie enthält den privaten Schlüssel und die Namenszuordnungen; nur dein Benutzer
kann die Datei lesen. Zum Ändern der Auswahl die Einrichtung erneut ausführen.
Pro Playlist sind bis zu 1000 Titel erlaubt. Fehlende, mehrfach gleich benannte
Mac-Playlists oder unvollständige Titelinformationen werden abgewiesen.
Duplikate und Reihenfolge bleiben erhalten. Nicht ausgewählte Playlists und
fehlgeschlagene Übertragungen verändern die bisher gespeicherten Listen nicht.
Die Übertragung erfolgt einzeln pro Playlist; andere Playlists können bei einem
Fehler trotzdem erfolgreich aktualisiert werden.

Leere Mac-Playlists werden vorsorglich nicht übertragen, um eine noch nicht
synchronisierte Mediathek nicht als Löschauftrag zu behandeln. Soll eine
ausgewählte Playlist ausdrücklich leer übertragen werden, in ihrer Zuordnung
in `config.json` zusätzlich `"allow_empty": true` eintragen.

Protokolle: `sync.log` und `sync-error.log` im gleichen Ordner.
Bei `CalledProcessError`: Musik-App öffnen, Namen prüfen und unter
**Systemeinstellungen → Datenschutz & Sicherheit → Automation** die Steuerung
erlauben. Bei `HTTPError`: Schlüssel, Zielplaylist und Port prüfen; bei
`URLError`: Erreichbarkeit/Adresse prüfen. Ein abgelehnter Import überschreibt
die Liste nicht. Umbenannte Playlists benötigen eine aktualisierte Zuordnung.

Der Sync-Schlüssel erlaubt ausschließlich das Aktualisieren von Titelname und
Interpret von Playlists und auf ausdrücklichen Wunsch der Mac-App das Anlegen
neuer Playlist-Favoriten. Dieser ältere Helfer legt keine Favoriten an.
Der Schlüssel erlaubt weder Wiedergabe noch Zugriff
auf andere Add-on-Funktionen. Der Netzwerkport ist standardmäßig deaktiviert,
und ohne gültigen Schlüssel ist auch der Sync-Endpunkt gesperrt.

## Abschalten

```sh
launchctl bootout "gui/$(id -u)/local.ha-music.playlist-sync"
rm "$HOME/Library/LaunchAgents/local.ha-music.playlist-sync.plist"
```

Im Add-on `playlist_sync_token` leeren und den Netzwerkport deaktivieren;
anschließend neu starten. Vorhandene Titellisten bleiben erhalten.

Die Mac-Musik-App und ihre Automationsberechtigung müssen auf dem tatsächlichen
Mac geprüft werden. Repository-Tests prüfen Server, Schutz des Endpunkts,
Übertragung und Fehlerfälle, ersetzen diesen Gerätetest aber nicht.
