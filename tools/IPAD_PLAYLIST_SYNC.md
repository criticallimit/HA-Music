# iPad: alle Apple-Music-Playlists synchronisieren

## Einmal einrichten

1. **Den sichtbar gewordenen Sync-Schlüssel ersetzen:** in der HA-Music-Add-on-Konfiguration
   `playlist_sync_token` durch einen neuen zufälligen Schlüssel (32–128 Zeichen,
   Buchstaben, Ziffern, `_` oder `-`) ersetzen, speichern und Add-on neu starten.
   Den neuen Wert nur im privaten Kurzbefehl und in den Mac-Sync-Einstellungen einsetzen.
   Alte geteilte Kurzbefehle/Screenshots entfernen. Keine Schlüssel in URLs, Screenshots oder Logs.
2. Die hier beschriebenen Serveränderungen müssen im laufenden Add-on installiert sein.
   Ein Commit auf GitHub aktualisiert es nicht. Unter Netzwerk `8099/tcp` aktivieren.
   Basisadresse z. B. `http://192.168.1.20:8099`, ohne abschließenden Schrägstrich.
   Port 8123 und Ingress-Adressen sind für diesen Sync falsch.
3. Auf dem iPad die Musikmediathek synchronisieren und Actions den Musikzugriff erlauben.
   Einen Kurzbefehl „HA Music Sync“ anlegen. Zwei feste Variablen: `Basisadresse`, `Schlüssel`.
   Alle HTTP-Aktionen: POST, Header `Authorization` = `Bearer ` gefolgt vom Schlüssel.
   Im Heimnetz bleiben; für externen Zugriff vertrauenswürdiges HTTPS verwenden.

## Kurzbefehl, in dieser Reihenfolge

1. **Actions → Get Music Playlists**. Ausgabe als `Playlistnamen` speichern.
   **Mit jedem wiederholen** über diese Namen; äußeres Element als `Playlistname` speichern.
2. **Playlist abrufen** mit `Playlistname`, Titelanzahl als `Titelanzahl` speichern.
   Leere Playlists überspringen. Innere **Mit jedem wiederholen** über die Titel;
   inneres Element als `Titel` speichern. Nicht den Playlistnamen als Titelnamen verwenden.
3. **Details von Musik abrufen** aus `Titel`: Name, Interpret, Album, Albuminterpret,
   Dauer, Albumcover. Titelwörterbuch: `name`, `artist`, `album`, `album_artist`, `duration`.
   Fehlende optionale Werte weglassen. Fehlender Name/Interpret: Kurzbefehl stoppen.
   Sekunden und angezeigte Dauer `m:ss`/`h:mm:ss` akzeptiert der Server direkt.
4. Vorhandenes **Albumcover** wie auf dem Mac vorbereiten: **Bildgröße ändern** auf
   320 Pixel Breite (Höhe automatisch), **Bild konvertieren → JPEG**, Qualität ca. 75 %,
   Metadaten nicht übernehmen. Bilddatei als `Coverdatei` speichern. Nur dieses fertige
   JPEG verwenden, nicht das Musikobjekt und nicht dessen Textdarstellung.
   Der Mac setzt das Bild in eine 320×320-Fläche; das iPad darf das Seitenverhältnis behalten.
5. **Hash generieren → SHA256** aus `Coverdatei`, Ergebnis in Kleinbuchstaben als
   `Coverhash`. Titelwörterbuch ergänzen: `cover` = `Coverhash`, `local_covers` = Wahr.
   Ein Wörterbuch `Coverdateien` je Lauf hält Hash → fertige Bilddatei; gleichen Hash
   nur einmal aufnehmen. Ohne verfügbares Cover die beiden Felder weglassen.
6. Innere Wiederholung zuletzt **Titelwörterbuch** ausgeben lassen; Ergebnisse als
   `Titelliste` speichern. Danach alle noch nicht geprüften Hashes aus `Coverdateien`
   **einmal je Playlist gebündelt** an `/api/playlist-artwork-check` senden:
   Anfragetext JSON `covers` = Hashliste. Antwort `ok` = Wahr prüfen. Bereits in diesem
   Lauf erfolgreich geprüfte/hochgeladene Hashes in der Liste `BekannteCover` sammeln.
7. Nur über `missing` der Antwort wiederholen: Bilddatei aus `Coverdateien` holen,
   **Base64 codieren**, Zeilenumbrüche **Keine**. An `/api/playlist-artwork-sync` senden:
   Anfragetext **JSON**, `cover` = Hash, `data` = Base64-Text. Antwort `ok` = Wahr prüfen.
   Bei HTTP-/Netzwerkfehler oder fehlendem `ok`: Kurzbefehl stoppen. Erst nach erfolgreicher
   Prüfung/Übertragung die Hashes zu `BekannteCover` hinzufügen.
8. Wie die Mac-App an `/api/playlist-sync` senden: Anfragetext **JSON**, `name` =
   **Playlistname**, `tracks` = **Titelliste**, `track_count` = **Titelanzahl**, `create` = Wahr.
   Antwort `ok` = Wahr prüfen. Nächste Playlist. Nur vollständig bestätigte Playlists zählen.
9. Nach erfolgreichem manuellen Test persönliche Automation zur gewünschten Uhrzeit:
   **Kurzbefehl ausführen → HA Music Sync**, automatische Ausführung aktivieren.
   Musikberechtigung beim ersten manuellen Lauf erlauben und ersten automatischen Lauf prüfen.

`Coverdateien` (leeres Wörterbuch) und `BekannteCover` (leere Liste) vor der äußeren
Wiederholung initialisieren. HTTP-URLs sind `Basisadresse` plus angegebener Pfad;
alle Requests haben `Content-Type: application/json` und den Authorization-Header.
Wörterbücher und Wiederholungsergebnisse als Variablen übergeben, kein JSON von Hand
mit Anführungszeichen zusammensetzen. Kein Cover in der Playlist-JSON.

Actions dokumentiert [Get Music Playlists](https://gist.github.com/sindresorhus/fbba65a774fb9da915e624807a02a6d2)
als Ausgabe aller Playlistnamen. Apple dokumentiert den
[JSON-Anfragetext](https://support.apple.com/en-mt/guide/shortcuts/apd58d46713f/ios).
Aktionsnamen können je Sprache variieren. Ein signierter/importierbarer iCloud-Kurzbefehl
liegt hier nicht vor; die Schritte werden im vorhandenen Kurzbefehl ergänzt.
## Verhalten und Grenzen

Eine gebündelte Prüfung neuer Hashes je Playlist, ein Upload je fehlendem Bildhash und ein Metadatenrequest je Playlist.
Keine Cover in der großen Playlist-JSON, keine doppelte Hashberechnung im Kurzbefehl,
keine Presence-Abfrage je Titel. Auch zwischen Playlists gleiche Dateien nur einmal übertragen.
Verschiedene Bildbytes desselben Albums sind verschiedene Hashes; Albumname allein ist kein
sicherer Bildschlüssel. Der Server speichert PNG/JPEG/WebP bytegenau bis **3 MiB je Cover**.
Größere Dateien werden abgewiesen, nicht still verkleinert. Die Qualität vor der Ausgabe
durch Apple/Kurzbefehle kann der Server nicht wiederherstellen.

Maximal 50 Favoriten insgesamt, 1000 Titel je Playlist, 2 MiB je Playlist-Sync (32 MiB beim älteren iPad-Gesamtimport) und
1 MiB je als Text serialisierter Sammlung. Native Wörterbuchlisten sind vorzuziehen.
Der Server toleriert auch JSON-Arrays als Text, einzelne serialisierte Wörterbücher,
verschachtelte Textlisten und zeilenweise JSON, mit begrenzter Verschachtelung.

Ungültige Metadaten, fehlende Cover oder abweichende `track_count` verändern keine Playlist.
Abgebrochene HTTP-Bodies werden abgewiesen. Bilder werden vor den Metadaten atomar gespeichert;
ein späterer Fehler kann unreferenzierte Cover hinterlassen, aber keine halben Playlists. Wie auf dem Mac können frühere Playlists eines Laufs schon erfolgreich gespeichert sein.
Leere Listen werden übersprungen. Nicht gelieferte Playlists werden nicht gelöscht.
Reihenfolge, doppelte Titel, vorhandene Alexa-Befehle und Albumfavoriten bleiben erhalten.
Fehlende optionale Metadaten bleiben für dieselbe Titel-/Interpret-Vorkommnis erhalten;
bei geändertem Album wird kein altes Cover übernommen.

Die Geräteprüfung bleibt notwendig: Stimmen Playlistanzahl und Titelanzahl mit Musik überein?
Kommt ein Cover als unterstützte Bilddatei? Bleibt ein zweiter Lauf unverändert und lädt
keine vorhandenen Cover hoch? Funktionieren Mac-Sync und Alexa danach? Der Server kann
nicht erkennen, ob Apples Mediathek schon vollständig auf dem iPad geladen ist.

## Vergleich mit dem Mac

`mac_playlist_sync.py` sendet nur Titel/Interpret an `/api/playlist-sync`.
Die native Mac-App sendet Album, Albuminterpret, Dauer und Coverhash, dedupliziert Uploads
innerhalb eines Laufs und überträgt Cover separat als Base64-JSON an denselben Cover-Endpunkt.
Sie erzeugt bislang 320-Pixel-JPEGs bis 128 KiB. Diese vorhandene Mac-Logik und ihr Format
bleiben kompatibel; das iPad verwendet denselben JSON-Transport und eine vergleichbare Bildaufbereitung.

`Ingress only` bedeutet falscher Pfad/Port (auch zusätzliche Queryparameter sind falsch),
nicht fehlende Bildrechte. `Ungültiges Bildformat` entsteht bei Text/Musikobjekt/HEIC statt
PNG/JPEG/WebP. Große Base64-Gesamtpakete entfallen durch kleine JPEGs und getrennte Requests.
