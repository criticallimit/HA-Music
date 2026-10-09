# HA Music

Home-Assistant-Add-on für Alexa-Multiroom-Radio mit Ingress-Oberfläche und Lovelace-Karte. Entwicklungsstand auf `main`, Version **0.0.6**. Apple Music ist weiterhin ein Platzhalter; es gibt keinen Apple-Music-Login.

## Installation

`https://github.com/criticallimit/HA-Music` als Add-on-Repository in Home Assistant hinzufügen. Änderungen ohne Versionssprung erfordern einen **Neuaufbau** des Add-ons. Einrichtung der Karte: [Dashboard-Anleitung](ha_music/DASHBOARD.md).

## Aktuelles Verhalten

- Nach Add-on-Start bleibt das Netzwerk im Standby. Nur der Einschaltbutton in HA Music startet den Ablauf; ein externer Schalterwechsel oder Browserreload startet keinen Sender.
- Nach Einschalten: zunächst 50 Sekunden für das Einschalten der Geräte warten und die Oberfläche freigeben. Erst danach den Alexa-Integrationseintrag einmal mit homeassistant.reload_config_entry neu laden und zehn Sekunden für dessen Aktualisierung pausieren. Dann Alexa-Zustände aktualisieren, bekannte aktive Räume auf 1% setzen, zwei Sekunden warten, gespeicherten Sender anfordern und die gespeicherte Master-Lautstärke auf alle nicht stummen Räume anwenden. Stumme Räume und ein stummer Master bleiben bei 0%. Diese Angleichung erfolgt nur beim Start; anschließend lassen sich die Räume unabhängig regeln. Beim nächsten Start werden aktive Räume erneut an den Master angeglichen.
- Nach der 50-Sekunden-Wartezeit wird die Oberfläche eingeblendet. Die Startprüfung verlangt keinen aktiven/verfügbaren Player und keinen bestätigten Ready-Helfer. Die zusätzliche Zwölf-Sekunden-Verfügbarkeitsprüfung entfällt. Ready bedeutet jetzt ausschließlich, dass die Oberfläche freigegeben ist; es bestätigt keine hörbare Wiedergabe. Der Reload verlängert die Wartezeit bis zur Anzeige nicht mehr. Die zehn Sekunden danach sind eine feste Pause vor Medienbefehlen, keine Verfügbarkeitsprüfung.
- Während Reload, Pause und nachfolgende Lautstärke-/Senderbefehle abgeschlossen werden, bleiben die Regler gesperrt, damit manuelle Befehle nicht mit der 1%-Probe und Wiederherstellung kollidieren. Fehler dabei stehen im Add-on-Protokoll und verstecken die Oberfläche nicht. Unverfügbare Einzelgeräte bleiben als solche erkennbar. Manuelle Befehle prüfen weiterhin Ziel und Verfügbarkeit; ein automatischer Senderbefehl wird höchstens einmal versucht, ohne vorgeschaltete Verfügbarkeitswartephase. Er erfordert einen erfolgreichen Startlautstärke-Aufruf für das Senderziel; übersprungene Proben dürfen keinen ungesicherten Senderstart auslösen. Das bestehende feste Senderziel ist `media_player.wohnzimmer`.
- Beim Ausschalten werden Startabläufe und Metadaten abgebrochen. Nach zehn Sekunden sperrt das Add-on neue HA-Anfragen. Grenzen bereits laufender Netzwerk-/Alexa-Aufträge: [Prüfbericht](AUDIT.md).
- Raumregler speichern ihre Sollwerte. `*` bedeutet: HA meldet noch einen anderen Wert. Stummschaltung setzt die Lautstärke auf 0, ohne Räume aus einer Alexa-Gruppe zu entfernen.
- Sender, Radio-/Apple-Ansicht und Lautstärkewünsche liegen dauerhaft unter `/data`. Die Radioansicht während Standby überschreibt die gespeicherte Ansicht nicht.
- Alexa-Entities werden aus den Integrationen `alexa_devices` und `alexa_media` erkannt. Zusätzlich wird das vollständige Entitätenverzeichnis jedes gefundenen Alexa-Kontos abgefragt, damit auch nicht geladene Media Player erscheinen. Gerätenamen stammen bei fehlendem Live-Zustand aus dem Geräteverzeichnis. Unter **Add-on → Konfiguration → Alexa-Geräte** entscheidet der Schalter **In Ingress anzeigen und steuern**, welche Geräte angezeigt und gesteuert werden.
- Gleichzeitige Geräteerkennungen verwenden einen gemeinsamen Katalog mit 30 Sekunden Gültigkeit. Ein Integrationsreload verwirft ihn; Standby sperrt auch den Zugriff auf diesen Cache. Beim Lautstärke-Restore werden offline befindliche Räume übersprungen und ihre gespeicherten Werte behalten.

Die konkrete Installation verwendet weiterhin `switch.alexa_alle`, `input_boolean.alexa_hochgefahren` und die Master-Gruppe `media_player.wohnung`. Senderbefehle werden über `media_player.wohnzimmer` an die vorhandene Alexa-Gruppe Wohnung geschickt. Für diese Sender muss das Wohnzimmer in der Geräteauswahl aktiviert bleiben. Zusätzliche Raumgeräte lassen sich über die Konfiguration einbeziehen; ihre Zugehörigkeit zur echten Alexa-Multiroom-Gruppe wird dadurch nicht geändert.

## Räume und Gruppenwiedergabe steuern

Die Raum-Schalter zeigen **Hörbar** oder **Stumm**. Ein Klick schaltet ausschließlich die Raumlautstärke um; die Alexa-Gruppenwiedergabe läuft weiter. Beim Stummschalten wird die zuletzt eingestellte positive Lautstärke dauerhaft gespeichert und beim erneuten Hörbarschalten wiederhergestellt. Ohne gespeicherten Wert wird eine positive Master-Lautstärke verwendet, andernfalls 30%. Lautstärkeregler bleiben unabhängig bedienbar.

Direkt neben dem Lautsprechersymbol des Masters sitzt ein gleich großes **Play/Pause-Symbol** für die gesamte Gruppe. Es zeigt Pause bei laufender Wiedergabe und Play bei pausierter Wiedergabe. Es ist nur aktiv, wenn Home Assistant den passenden Gruppenzustand und die benötigte Funktion meldet. Fortsetzen startet keinen neuen Sender aus einem inaktiven/unbekannten Zustand. Raum-Schalter, Gruppen-Pause und das zentrale Ausschalten sind unterschiedliche Funktionen: Nur der zentrale HA-Music-Ausschaltbutton startet den Netzwerk-Standby. Ein Pausebefehl an ein einzelnes Alexa-Gruppenmitglied könnte die gesamte Gruppe pausieren; deshalb gibt es keine individuellen Pausebuttons.

## Geräte auswählen

1. Nach dieser Änderung das Add-on-Repository aktualisieren und HA Music **neu aufbauen**.
2. HA Music einmal über den lokalen Einschaltbutton starten. Nach der 50-Sekunden-Wartezeit werden alle registrierten Alexa-Media-Player in der Add-on-Konfiguration ergänzt.
3. **Add-on → Konfiguration** neu öffnen. Unter **Alexa-Geräte** beim gewünschten Eintrag den **Stift** öffnen und **HA-Music-Status** auf **Aktiv** oder **Inaktiv** setzen. **Nicht löschen**: Bei Inaktiv bleibt das Gerät zur späteren Aktivierung in der Liste. Bei Bedarf sind Entity-ID und Anzeigename editierbar; die Entity muss in einer unterstützten Alexa-Integration registriert sein.
4. **Speichern** und das Add-on **neu starten**. Anschließend HA Music wieder lokal einschalten.

Wohnung, Wohnzimmer, Küche und Bad bleiben als bisherige Geräte standardmäßig aktiviert. Weitere neu gefundene Geräte sind zunächst deaktiviert. Gespeicherte Geräteauswahl und Anzeigenamen und zeitweise nicht gefundene Geräte bleiben erhalten. Eine leere Geräteauswahl aktiviert keine Geräte automatisch.

Deaktivierte Geräte erscheinen weder als Raumregler noch als Gruppe in Ingress. Sie erhalten keine 1%-Probe, Master-Angleichung, `update_entity`- oder manuellen Lautstärkebefehle durch HA Music. Das Einschalten eines Eintrags schaltet das physische Gerät nicht ein: Ein nicht verfügbares Gerät wird als nicht verfügbar angezeigt. Der gemeinsame Radioschalter und die tatsächliche Alexa-Gruppenmitgliedschaft bleiben eigenständig.

Neue Geräte werden beim nächsten lokalen HA-Music-Start ergänzt. Im Netzwerk-Standby findet keine Geräteerkennung statt. Einstellungen werden über die eigenen Supervisor-Endpunkte gespeichert; HA Music benötigt dafür keine zusätzliche Manager-/Admin-Rolle.

Ein ausgeschalteter Echo Dot oder ein Fire TV wird ebenfalls ergänzt, sofern seine `media_player`-Entity in einer unterstützten Alexa-Integration registriert ist. Ein Amazon-Konto-Gerät ohne entsprechende HA-Entity kann das Add-on nicht direkt steuern; es muss zuerst in Home Assistant eingebunden werden. Fehlen Einträge, den lokalen HA-Music-Start abschließen lassen und danach die Konfiguration neu öffnen.

Die Erkennung unterscheidet Geräte über ihre Entity-ID. Zwei gleich benannte Fire TVs werden daher getrennt ergänzt. Auch „This Device“ wird berücksichtigt, sofern ein Media-Player-Eintrag vorhanden ist. Im Add-on-Protokoll zeigt `Alexa discovery: ... media players` die gefundenen IDs; die anschließende Zeile `Device configuration: ... entries` bestätigt den gespeicherten Katalog. Zum Erkennen neuer Geräte muss mindestens eine Entität des betreffenden Alexa-Kontos geladen sein; bestehende Konfigurationseinträge bleiben erhalten, wenn die Integration vollständig deaktiviert ist. Fehlende Media-Player-Einträge werden nicht künstlich erfunden.

Die Liste mit Stift/Papierkorb wird vom Home-Assistant-Konfigurationsformular vorgegeben. Das Add-on kann dort keine eigenen An/Aus-Schalter direkt neben den Zeilen einsetzen. Im Bearbeitungsdialog steht stattdessen die Auswahl Aktiv/Inaktiv bereit. Ausschalten der Anzeige verhindert HA-Music-Befehle, entfernt das Gerät aber nicht aus einer Alexa-Multiroom-Gruppe: Alexa kann den Gruppenstream weiterhin an dieses Gerät senden.

## Entwicklung und Prüfung

```sh
python -m pip install -r tests/requirements.txt
python -m py_compile ha_music/app.py ha_music/metadata.py ha_music/metadata_feed.py ha_music/logo_sources.py
python tests/test_scaffold.py
python tests/test_smoke.py
python -m unittest discover -s tests -p 'test_runtime.py' -v
python -m unittest discover -s tests -p 'test_discovery_template.py' -v
node --test tests/frontend.test.cjs
node --check ha_music/web/app.js
node --check ha_music/lovelace/ha-music-card.js
node --check ha_music/lovelace/ha-music-card-loader.js
```

Die CI führt diese Prüfungen bei Push auf `main` aus. Entwicklung direkt auf `main`; Releases nur nach ausdrücklicher Freigabe. Befunde, Änderungen, Testumfang und verbleibende Risiken: [Codeprüfung vom 9. Oktober 2026](AUDIT.md).

Alte Gerätewerte `enabled: true/false` bleiben beim Umstieg gültig. Beim nächsten Start des Add-ons werden sie automatisch in `status: Aktiv/Inaktiv` umgewandelt und aus der gespeicherten Liste entfernt. Anschließend die Konfigurationsseite neu öffnen. Das optionale Feld „Bisherige Auswahl“ dient nur der Kompatibilität; wenn ein HA-Music-Status gesetzt ist, gilt dieser. Neu erkannte Geräte erhalten `Inaktiv`.

Die Konfiguration lässt sich bei ausgeschaltetem Radio bearbeiten. Zur Übernahme älterer Werte muss nur das Add-on gestartet werden, nicht das Radio. Dabei werden einmalig ausschließlich die lokalen Supervisor-Optionen gelesen/gespeichert; Alexa und der Radioschalter bleiben unangetastet. Nach Änderungen speichern und das Add-on neu starten.
