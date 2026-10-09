# HA Music

Home-Assistant-Add-on für Alexa-Multiroom-Radio und Apple-Music-Favoriten mit Ingress-Oberfläche und Lovelace-Karte. Entwicklungsstand auf `main`, Version **0.0.6**. Apple Music wird über das in Alexa verknüpfte Konto abgespielt; ein automatischer Mediathekabruf ist noch nicht implementiert.

## Automatische Wiederanbindung nach Add-on-Neustart

Ein Add-on-Update oder Prozessneustart startet keine Musik und setzt keine Lautstärken. HA Music prüft automatisch die bereits in Home Assistant vorliegenden Zustände von `switch.alexa_alle` und den aktiv konfigurierten Media Playern. Meldet der Schalter „on“ und dasselbe aktive Gerät in zwei aufeinanderfolgenden Abfragen „playing“ oder „paused“, werden die gespeicherte Radio-/Apple-Ansicht und ihre Bedienelemente freigegeben. Pausierte Musik bleibt pausiert. Es gibt keinen zusätzlichen Button.

Die Wiederanbindung ruft weder Alexa-Reload noch `update_entity`, Wiedergabe-, Lautstärke-, Power- oder Ready-Helfer-Dienste auf. Raumregler verwenden dabei beobachtete Lautstärken; der virtuelle Master behält seinen gespeicherten Sollwert. Alte Sender-/Favoritenauswahl wird nicht als Beweis für die aktuelle Musik verwendet: Titel und Cover kommen aus den laufenden bzw. pausierten Geräten, bis der Nutzer selbst wieder einen Sender oder Apple-Favoriten auswählt.

Die lokale Betriebsabsicht liegt atomar unter `/data/session.json`. Nach bewusstem Ausschalten bleiben auch Neustarts ohne HA-Zustandsabfragen im Netzwerk-Standby. Beim ersten Update ohne diese Datei erfolgt automatisch eine begrenzte, ausschließlich lesende Prüfung gegen Home Assistant. Ein gemeldetes „off“ hat Vorrang vor alten Playback-Meldungen. Unklare Zustände/HA-Ausfälle werden bis zu zwölfmal mit fünf Sekunden Abstand erneut geprüft; danach bleibt die Oberfläche gesperrt und zeigt die fehlende Bestätigung an. Die Erkennung ist auf die in HA verfügbaren Zustände angewiesen und löst keine Geräteaktualisierung aus.

Ein ausdrücklich gewünschtes normales Einschalten verwendet weiterhin die bisherige Startsequenz. Ein Prozessende schreibt keine Ausschaltabsicht. Die Add-on-Version wird durch diese Änderung nicht erhöht.

## Apple-Music-Oberfläche

Der vorhandene Button **Apple Music** öffnet eine Seite im gleichen Layout wie Radio. Cover/Logo, laufende Wiedergabe, Master-Lautstärke, Play/Pause und sämtliche aktiv ausgewählten Raumgeräte werden gemeinsam verwendet. Die bestehenden Elemente werden beim Umschalten verschoben, nicht dupliziert; dadurch bleiben Lautstärken, Bedienung und vorhandene Abfragen erhalten. Anstelle der Radiosender erscheinen konfigurierte **Playlists** und **Alben**. Ohne Favoriten zeigen diese Bereiche Leeranzeigen.

Das Umschalten der Ansicht startet oder stoppt keine Musik und verändert keine Lautstärken. Ein bereits laufender Sender bleibt sichtbar und hörbar. Erst ein Klick auf eine Apple-Music-Kachel sendet einen Abspielbefehl. Dann endet die zusätzliche Radio-Metadatenabfrage; Titel, Interpret und Cover kommen aus Home Assistants Alexa-Media-Player-Zuständen. Die Auswahl gilt gemeinsam für alle geöffneten Ingress-Ansichten. Ein Klick auf einen Radiosender stellt die Radioanzeige wieder her.

### Apple Music einrichten

1. In der **Alexa-App → Musik & Podcasts** Apple Music mit dem gewünschten Apple Account verknüpfen. Der Account muss Apple Music nutzen dürfen, etwa als Mitglied eines Apple-Music-Familienabos. Ein Apple-Passwort wird im Add-on nicht benötigt.
2. Nach einem Neuaufbau unter **Add-on → Konfiguration** das **Apple-Music-Steuergerät** auswählen: Entity-ID eines unter Alexa-Geräte auf **Aktiv** gesetzten Echo. Standard: `media_player.wohnzimmer`.
3. Als **Apple-Music-Lautsprechergruppe** den exakten Alexa-Gruppennamen eintragen, standardmäßig `Wohnung`. Leer bedeutet Wiedergabe direkt auf dem Steuergerät. Die bestehenden Master-/Raumregler beziehen sich weiterhin auf die vorhandene Wohnung-Konfiguration. Eine andere Alexa-Gruppe wird durch diese Option nicht automatisch mit neuen Masterreglern eingerichtet.
4. Unter **Apple-Music-Favoriten → Hinzufügen** Anzeigename und Art (`Playlist` oder `Album`) hinterlegen. Optional als Suchbegriff den exakten persönlichen Playlistnamen oder Albumtitel mit Interpret angeben. Ohne Suchbegriff wird der Anzeigename verwendet.
5. Speichern, das Add-on neu starten, HA Music einschalten und nach der bisherigen Startvorbereitung **Apple Music** öffnen. Eine Kachel startet die Auswahl über einen Textbefehl wie „spiele meine Playlist Abendmusik auf Apple Music auf Wohnung“.

Es gibt keine automatische Erkennung deiner Playlists, keine Apple-Kontoprüfung und keinen automatischen Apple-Music-Start beim Einschalten: Die vorhandene feste Startreihenfolge und der zuletzt gespeicherte Radiosender bleiben unverändert. Die Apple-Auswahl wird beim Ausschalten/Neustart zurückgesetzt; die konfigurierten Favoriten und die bevorzugte Ansicht bleiben erhalten. Im Standby und während der Startvorbereitung sind Abspielbefehle gesperrt. Fehler stehen im Add-on-Protokoll/Browserprotokoll, ohne zusätzliche Meldungsfenster.

Alexa wertet die Playlist-/Albumsuche anhand des Namens aus. Eine angenommene HA-Serviceanfrage bestätigt weder hörbare Wiedergabe noch die exakte Auswahl; doppelte Playlistnamen und verzögerte Titel-/Covermeldungen müssen mit dem konkreten Account praktisch getestet werden. Das Add-on setzt bei einem manuellen Favoritenstart keine Lautstärken neu und sendet keine wiederholten Startbefehle. Eine automatisch geladene Mediathek wäre eine spätere zusätzliche MusicKit/Apple-Music-API-Erweiterung. Dokumentation: [Apple Music mit Alexa](https://support.apple.com/de-de/119922), [Alexa Media Player](https://github.com/alandtse/alexa_media_player/wiki).

## Installation

`https://github.com/criticallimit/HA-Music` als Add-on-Repository in Home Assistant hinzufügen. Änderungen ohne Versionssprung erfordern einen **Neuaufbau** des Add-ons. Einrichtung der Karte: [Dashboard-Anleitung](ha_music/DASHBOARD.md).

## Aktuelles Verhalten

- Nach Add-on-Start erfolgt bei bisher aktivem oder noch unbekanntem Betriebszustand die oben beschriebene automatische Wiederanbindung. Ein gespeichertes „aus“ bleibt im Netzwerk-Standby. Nur der Einschaltbutton in HA Music startet die normale Einschaltsequenz; ein externer Schalterwechsel oder Browserreload startet keinen Sender.
- Nach Einschalten folgt der Ablauf der bisherigen Automation mit allen Schritten aktiv: 45 Sekunden warten → Alexa-Integration einmal neu laden → 20 Sekunden warten → aktiv konfigurierte Geräte mit homeassistant.update_entity aktualisieren → 5 Sekunden warten → Startlautstärke → Bereit-Helfer/Oberfläche → gespeicherten Sender einmal anfordern → Lautstärken wiederherstellen. Die Gruppe wird vor den Räumen gesetzt: zunächst 1%, danach der gespeicherte Master. Nicht stumme Räume übernehmen beim Start den Master; stumme Räume und ein stummer Master bleiben bei 0%. Danach sind die Räume unabhängig regelbar. Statt der alten festen 30% und des externen WDR2-Skripts werden die gewünschte 1%-Probe und der gespeicherte Sender verwendet.
- Der Countdown zeigt die festen Pausen von insgesamt 70 Sekunden. Die Laufzeit von Reload, Aktualisierung und Lautstärkebefehlen kommt hinzu. Die Oberfläche wird nach der Startlautstärke freigegeben; die Bedienung wartet noch auf Senderstart und Restore. Gerätezustände wie unknown/unavailable sperren diese Schritte nicht. Servicefehler werden protokolliert und nachfolgende Schritte nach Möglichkeit fortgesetzt. Ready bestätigt keine hörbare Wiedergabe. Der zugehörige Integrationseintrag wird über ein aktiv konfiguriertes Gerät gewählt, ohne feste Config-Entry-ID.
- Während Startzeit, Reload und nachfolgende Lautstärke-/Senderbefehle abgeschlossen werden, bleiben die Regler gesperrt, damit manuelle Befehle nicht mit der 1%-Probe und Wiederherstellung kollidieren. Fehler dabei stehen im Add-on-Protokoll und verstecken die Oberfläche nicht. Unverfügbare Einzelgeräte bleiben als solche erkennbar. Manuelle Befehle prüfen weiterhin die Gerätefreigabe, aber nicht den gemeldeten Verfügbarkeitszustand; ein automatischer Senderbefehl wird höchstens einmal versucht, ohne vorgeschaltete Verfügbarkeitswartephase. Startlautstärken werden an alle aktiv ausgewählten Räume gesendet, auch bei unknown/unavailable. Ein fehlgeschlagener Lautstärke- oder update_entity-Aufruf verhindert den einmaligen Senderbefehl nicht. Fehlende gespeicherte Raumwerte übernehmen den Master; fehlt auch dieser, werden 1% verwendet. Damit kann die tatsächliche Startlautstärke bei abgelehnten Alexa-Befehlen nicht garantiert werden. Das bestehende feste Senderziel ist `media_player.wohnzimmer`.
- Beim Ausschalten werden Startabläufe und Metadaten abgebrochen. Nach zehn Sekunden sperrt das Add-on neue HA-Anfragen. Grenzen bereits laufender Netzwerk-/Alexa-Aufträge: [Prüfbericht](AUDIT.md).
- Raumregler speichern ihre Sollwerte. `*` bedeutet: HA meldet noch einen anderen Wert. Stummschaltung setzt die Lautstärke auf 0, ohne Räume aus einer Alexa-Gruppe zu entfernen.
- Sender, Radio-/Apple-Ansicht und Lautstärkewünsche liegen dauerhaft unter `/data`. Die Radioansicht während Standby überschreibt die gespeicherte Ansicht nicht.
- Radiotext wird bei Freigabe der gespeicherten Radioansicht unabhängig von Alexa-Play-Aufrufen geladen. Live-Ereignisse und die vorhandene Playback-Statusabfrage liefern denselben gemeinsamen Metadatenstand. Radiotext ist keine Bestätigung hörbarer Alexa-Wiedergabe.
- Alexa-Entities werden aus den Integrationen `alexa_devices` und `alexa_media` erkannt. Zusätzlich wird das vollständige Entitätenverzeichnis jedes gefundenen Alexa-Kontos abgefragt, damit auch nicht geladene Media Player erscheinen. Gerätenamen stammen bei fehlendem Live-Zustand aus dem Geräteverzeichnis. Unter **Add-on → Konfiguration → Alexa-Geräte** entscheidet der Schalter **In Ingress anzeigen und steuern**, welche Geräte angezeigt und gesteuert werden.
- Gleichzeitige Geräteerkennungen verwenden einen gemeinsamen Katalog mit 30 Sekunden Gültigkeit. Ein Integrationsreload verwirft ihn; Standby sperrt auch den Zugriff auf diesen Cache. Beim Lautstärke-Restore werden auch offline gemeldete, aktiv ausgewählte Räume angesprochen.

Die konkrete Installation verwendet weiterhin `switch.alexa_alle`, `input_boolean.alexa_hochgefahren` und die Master-Gruppe `media_player.wohnung`. Senderbefehle werden über `media_player.wohnzimmer` an die vorhandene Alexa-Gruppe Wohnung geschickt. Für diese Sender muss das Wohnzimmer in der Geräteauswahl aktiviert bleiben. Zusätzliche Raumgeräte lassen sich über die Konfiguration einbeziehen; ihre Zugehörigkeit zur echten Alexa-Multiroom-Gruppe wird dadurch nicht geändert.

## Räume und Gruppenwiedergabe steuern

Die Raum-Schalter zeigen dasselbe kleine **Lautsprechersymbol** wie der Master: normal bei hörbarer Lautstärke, durchgestrichen bei Stummschaltung. Ein Klick schaltet ausschließlich die Raumlautstärke um; die Alexa-Gruppenwiedergabe läuft weiter. Beim Stummschalten wird die zuletzt eingestellte positive Lautstärke dauerhaft gespeichert und beim erneuten Hörbarschalten wiederhergestellt. Ohne gespeicherten Wert wird eine positive Master-Lautstärke verwendet, andernfalls 30%. Lautstärkeregler bleiben unabhängig bedienbar.

Direkt neben dem Lautsprechersymbol des Masters sitzt ein gleich großes **Play/Pause-Symbol** für die gesamte Gruppe. Es zeigt Pause bei laufender Wiedergabe und Play bei pausierter Wiedergabe. Es ist nur aktiv, wenn Home Assistant den passenden Gruppenzustand und die benötigte Funktion meldet. Fortsetzen startet keinen neuen Sender aus einem inaktiven/unbekannten Zustand. Raum-Schalter, Gruppen-Pause und das zentrale Ausschalten sind unterschiedliche Funktionen: Nur der zentrale HA-Music-Ausschaltbutton startet den Netzwerk-Standby. Ein Pausebefehl an ein einzelnes Alexa-Gruppenmitglied könnte die gesamte Gruppe pausieren; deshalb gibt es keine individuellen Pausebuttons.

## Geräte auswählen

1. Nach dieser Änderung das Add-on-Repository aktualisieren und HA Music **neu aufbauen**.
2. HA Music einmal über den lokalen Einschaltbutton starten. Nach der Startvorbereitung (70 Sekunden feste Pausen plus Befehlslaufzeiten), Senderstart und Restore werden alle registrierten Alexa-Media-Player in der Add-on-Konfiguration ergänzt.
3. **Add-on → Konfiguration** neu öffnen. Unter **Alexa-Geräte** beim gewünschten Eintrag den **Stift** öffnen und **HA-Music-Status** auf **Aktiv** oder **Inaktiv** setzen. **Nicht löschen**: Bei Inaktiv bleibt das Gerät zur späteren Aktivierung in der Liste. Bei Bedarf sind Entity-ID und Anzeigename editierbar; die Entity muss in einer unterstützten Alexa-Integration registriert sein.
4. **Speichern** und das Add-on **neu starten**. Bereits laufende oder pausierte Wiedergabe wird automatisch wieder angebunden. War HA Music bewusst ausgeschaltet, anschließend lokal einschalten.

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

Die Konfiguration lässt sich bei ausgeschaltetem Radio bearbeiten. Zur Übernahme älterer Werte muss nur das Add-on gestartet werden, nicht das Radio. Die Optionsmigration liest/speichert ausschließlich die eigenen Supervisor-Optionen; die separate Wiederanbindung liest gegebenenfalls HA-Zustände wie oben beschrieben. Alexa und der Radioschalter erhalten dabei keine Befehle. Nach Änderungen speichern und das Add-on neu starten.

