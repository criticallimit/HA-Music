# HA Music

Home-Assistant-Add-on für Alexa-Multiroom-Radio und Apple-Music-Favoriten mit Ingress-Oberfläche und Lovelace-Karte. Entwicklungsstand auf `main`, Version **0.0.6**. Apple Music wird über das in Alexa verknüpfte Konto abgespielt. Titellisten ausgewählter Playlists können über den [Mac-Helfer](tools/MAC_PLAYLIST_SYNC.md) automatisch synchronisiert werden; ein direkter Apple-Music-API-Mediathekabruf ist nicht implementiert.

## Automatische Wiederanbindung nach Add-on-Neustart

Ein Add-on-Update oder Prozessneustart startet keine Musik und setzt keine Lautstärken. HA Music prüft automatisch die bereits in Home Assistant vorliegenden Zustände von `switch.alexa_alle` und den aktiv konfigurierten Media Playern. Meldet der Schalter „on“ und dasselbe aktive Gerät in zwei aufeinanderfolgenden Abfragen „playing“ oder „paused“, werden die gespeicherte Radio-/Apple-Ansicht und ihre Bedienelemente freigegeben. Auch ohne laufenden Titel wird die eingeschaltete Sitzung übernommen, wenn in zwei aufeinanderfolgenden Abfragen zusätzlich `input_boolean.alexa_hochgefahren` „on“ meldet und dasselbe konfigurierte Gerät verfügbar ist (`on`, `idle`, `playing` oder `paused`). Der Einschalt-Schalter allein löst niemals die Startsequenz aus. Einschalt-Anfragen während der Wiederanbindungsprüfung werden auch im Backend abgewiesen. Pausierte Musik bleibt pausiert. Es gibt keinen zusätzlichen Button.

Die Wiederanbindung ruft weder Alexa-Reload noch `update_entity`, Wiedergabe-, Lautstärke-, Power- oder Ready-Helfer-Dienste auf. Raumregler verwenden dabei beobachtete Lautstärken; der virtuelle Master behält seinen gespeicherten Sollwert. Alte Sender-/Favoritenauswahl wird nicht als Beweis für die aktuelle Musik verwendet: Titel und Cover kommen aus den laufenden bzw. pausierten Geräten, bis der Nutzer selbst wieder einen Sender oder Apple-Favoriten auswählt.

Die lokale Betriebsabsicht liegt atomar unter `/data/session.json`. Nach bewusstem Ausschalten bleiben auch Neustarts ohne HA-Zustandsabfragen im Netzwerk-Standby. Beim ersten Update ohne diese Datei erfolgt automatisch eine begrenzte, ausschließlich lesende Prüfung gegen Home Assistant. Ein gemeldetes „off“ hat Vorrang vor alten Playback-Meldungen. Unklare Zustände/HA-Ausfälle werden bis zu zwölfmal mit fünf Sekunden Abstand erneut geprüft; danach bleibt die Oberfläche gesperrt und zeigt die fehlende Bestätigung an. Die Erkennung ist auf die in HA verfügbaren Zustände angewiesen und löst keine Geräteaktualisierung aus.

Ein ausdrücklich gewünschtes normales Einschalten verwendet die Startsequenz mit der unten beschriebenen Master-Lautstärke-Wiederherstellung. Ein Prozessende schreibt keine Ausschaltabsicht. Die Add-on-Version wird durch diese Änderung nicht erhöht.

## Einstellungen sofort speichern und letzte Quelle wiederherstellen

**Aktuelle Startregel:** Bei normalem Aus-/Einschalten bestimmt ausschließlich der gespeicherte Master die Lautstärke aller aktiven, nicht stumm gespeicherten Räume. Beispiel: Master 23%, alte Raumwerte 1%, 45% und 0% → Startwerte 23%, 23% und 0%. Ein Raum mit 0% bleibt stumm; ein Master mit 0% startet alle Räume stumm. Positive Einzelraumänderungen gelten nur für die laufende Sitzung. Nur die Raum-Auswahl stumm/aktiv bleibt gespeichert. Die 1%-Startprobe und der 1%-Rückfallwert sind entfernt. Bei einem reinen Add-on-Neustart mit laufender Musik gilt weiterhin die ausschließlich lesende Wiederanbindung ohne Lautstärkeänderung.

Sicherung vor dieser Startregel: [backup/main-2026-10-09-before-master-start-af97242](https://github.com/criticallimit/HA-Music/tree/backup/main-2026-10-09-before-master-start-af97242), Commit `af9724248a98930f9a945534f2f68df80ab5e076`.

Jeder von Home Assistant angenommene Sender- oder Apple-Favoritenbefehl speichert unmittelbar die zuletzt gewählte Wiedergabequelle unter `/data/last_source.json`. Playlist und Album werden über die ID des konfigurierten Favoriten gespeichert. Ausschalten löscht diese Auswahl nicht. Beim späteren ausdrücklichen Einschalten wird diese Quelle einmal angefordert: War zuletzt Apple Music ausgewählt, startet kein alter Radiosender. Fehlt die neue Datei beim Umstieg, wird der bisher gespeicherte Radiosender übernommen. Eine beschädigte Quelldatei, ein gelöschter Favorit oder ein fehlgeschlagener Apple-Befehl führen niemals zu einem ersatzweisen Radio-Start; Fehler bei der Wiederherstellung erscheinen in der Wiedergabeanzeige. Abgelehnte oder durch Ausschalten abgebrochene Auswahlbefehle überschreiben die letzte erfolgreich gespeicherte Quelle nicht.

Master, Raum-Stummschaltungen, Ansicht, Wiedergabequelle und Ein-/Aus-Absicht werden unmittelbar gespeichert. Positive Einzelraumlautstärken und Entstumm-Werte sind nur für die laufende Sitzung vorhanden. Die Dateien werden vor dem atomaren Ersetzen auf den Datenträger geschrieben (`fsync`); unter Linux wird auch das Verzeichnis synchronisiert. Die Speicherung wartet nicht auf das Ausschalten oder Prozessende. Bei normalem Einschalten erhalten alle nicht stumm gespeicherten Räume den gespeicherten Masterwert. Ein gespeicherter Raumwert von 0 bleibt 0; alte positive Raumwerte werden nicht als Startlautstärke verwendet. Stumme Räume und ein stummer Master bleiben stumm. Die Geräte- und Favoritenkonfiguration bleibt in den Supervisor-Optionen.

Ein Add-on-Neustart bindet bereits laufende oder pausierte Wiedergabe weiterhin ausschließlich lesend wieder an. Nur ein ausdrücklich gewünschtes Einschalten nach Ausschalten fordert die gespeicherte Quelle erneut an. Dies speichert die zuletzt gewählte Playlist bzw. das Album, nicht die Alexa-Warteschlange, den exakten Titel, die Abspielposition oder einen Shuffle-Modus. Von Alexa außerhalb der Oberfläche gewechselte Quellen werden nicht als konfigurierter Favorit erraten. Bei einem Verbindungsabbruch ohne Befehlsbestätigung ist nicht sicher erkennbar, ob Alexa den Befehl trotzdem ausgeführt hat; er wird nicht automatisch wiederholt. Die Dateien benötigen dauerhaft erhaltenes `/data`; die Code-Sicherung ersetzt kein Home-Assistant-Datenbackup.

Bei einer aus einer älteren Version wieder angebundenen Sitzung ohne gespeicherte Quellen-ID bleibt die tatsächliche Auswahl unbekannt. Beim Ausschalten wird das gespeichert, damit beim späteren Einschalten kein alter Radiosender versehentlich startet. Ein danach in der Oberfläche gewählter Sender oder Favorit ersetzt diesen unbekannten Zustand dauerhaft.

Beim normalen Einschalten werden keine vorübergehenden 1%-Befehle mehr gesendet. Der Master ist ein virtueller Regler; seine gespeicherte Zahl wird nicht zusätzlich als echter Alexa-Gruppenlautstärkebefehl gesendet. Nach dem einmaligen Medienbefehl erhalten die aktiv konfigurierten Räume den gespeicherten Masterwert, sofern der Raum nicht mit 0 gespeichert ist. Räume mit gespeichertem 0-Wert und alle Räume bei stummem Master werden bereits vor dem Medienstart auf 0 gesetzt und anschließend weiter stumm gehalten. Fehlende Raumwerte werden zuvor aus HA bzw. dem gespeicherten Master ergänzt. Die bestehende begrenzte Bestätigungsprüfung darf nur denselben gewünschten Raumwert erneut senden; spätere manuelle Änderungen und Ausschalten haben Vorrang.

Hintergrund: [Alexa Media Player](https://github.com/alandtse/alexa_media_player/blob/dev/custom_components/alexa_media/media_player.py) startet die eigentliche Lautstärkeanfrage als Hintergrundauftrag und übernimmt den angeforderten Wert bereits in seine Anzeige. Eine erfolgreiche HA-Anfrage und ein passender Prozentwert beweisen daher weder die Ausführungsreihenfolge noch hörbaren Ton. Im lokalen Test mit umgekehrter Ausführungsreihenfolge konnten die bisherigen 1%- und Gruppenbefehle die einzelnen Restore-Werte überschreiben; mit der vereinfachten Folge bleiben die gewünschten Raumwerte erhalten. Das belegt die Korrektur dieses Konflikts, nicht die Ursache jedes möglichen Tonausfalls auf echten Echos.

Sicherung vor der Korrektur der Startlautstärke: [backup/main-2026-10-09-before-start-volume-b37deb8](https://github.com/criticallimit/HA-Music/tree/backup/main-2026-10-09-before-start-volume-b37deb8), Commit `b37deb843d63c8b212cb0b01017722cba65a2315`.

Sicherung vor dieser Änderung: [backup/main-2026-10-09-before-source-persistence-0dc6ac9](https://github.com/criticallimit/HA-Music/tree/backup/main-2026-10-09-before-source-persistence-0dc6ac9), Commit `0dc6ac90436786864d1b2048adc9a6f5d8e3f6db`.

Beim normalen Einschalten folgt die Startansicht der gespeicherten Wiedergabequelle: Radio startet in der Radioansicht, ein Apple-Favorit in der Apple-Ansicht. Ein zuvor nur zum Stöbern geöffneter Tab setzt sich dabei nicht über die Quelle hinweg. Auch ein angenommener Sender-/Apple-Auswahlbefehl speichert die passende Ansicht. Anschließend lassen sich beide Tabs weiterhin ohne Wiedergabebefehl öffnen. Bei der ausschließlich lesenden Wiederanbindung bleibt die gespeicherte Ansicht erhalten, solange die tatsächliche Quelle nicht bestätigt ist; sie wird nicht aus einem alten Sendernamen erraten.

Sicherung vor der Korrektur der Startansicht: [backup/main-2026-10-09-before-source-view-686cf8e](https://github.com/criticallimit/HA-Music/tree/backup/main-2026-10-09-before-source-view-686cf8e), Commit `686cf8ec9c03f1d06720c2ff5c8b6989f276d65a`.

## Apple-Music-Oberfläche

Titel, Künstler, Album und Cover der Apple-Auswahl werden unverändert aus einem frischen HA-Datensatz des Players gelesen, den auch Vor/Zurück steuert. Die Oberfläche übernimmt keine einzelnen Felder aus anderen Räumen und wählt keinen Künstler anhand eines vorherigen Titels. Das aktuelle `media_image_url` hat Vorrang vor einem allgemeinen `entity_picture`. Nach einem ausdrücklich angeklickten Vor/Zurück wird genau dieser Player einmal mit `homeassistant.update_entity` aktualisiert, danach die Anzeige neu eingelesen. Ein fehlgeschlagenes Metadaten-Update wiederholt den bereits angenommenen Titelbefehl nicht. Bei der automatischen Wiederanbindung gibt es weiterhin kein `update_entity` und keine Gerätebefehle. Bereits falsch oder verzögert von HA gelieferte Felder werden nicht durch Vermutungen ersetzt; der Audiostream selbst wird bei Apple Music nicht gelesen.

Sicherung vor der Apple-Metadatenkorrektur: [backup/main-2026-10-09-before-apple-metadata-95e8948](https://github.com/criticallimit/HA-Music/tree/backup/main-2026-10-09-before-apple-metadata-95e8948), Commit `95e89482b2d28d570da9465c9d7956c7c65164bb`.

Bei regulärem Einschalten werden nicht bestätigte Raumlautstärken anhand frischer HA-Zustände geprüft und höchstens zweimal mit exakt dem bereits angeforderten Wert erneut gesendet. Kein zusätzlicher Sender-/Playlist-Start und keine Änderung des gespeicherten Sollwerts. Ausschalten, deaktivierte Geräte und spätere manuelle Lautstärkeänderungen brechen alte Wiederholungen ab. Fehlende Bestätigungen werden im Wiedergabestatus sichtbar. Raumregler und Prozentzahl zeigen ausschließlich den aktuell gemeldeten HA-Wert, ohne gespeicherten Sollwert oder zusätzliche „HA:“-Zahl. Fehlt der HA-Wert, erscheint „–“. Der virtuelle Master behält seine unabhängige Einstellung. „Alexa meldet Wiedergabe“ ist kein Nachweis hörbaren Tons. Die ausschließlich lesende Wiederanbindung nach Add-on-Neustart sendet auch weiterhin keine Lautstärkebefehle.

Sicherung vor der vereinfachten Raumlautstärke-Anzeige: [backup/main-2026-10-09-before-single-volume-4030c64](https://github.com/criticallimit/HA-Music/tree/backup/main-2026-10-09-before-single-volume-4030c64), Commit `4030c647d266327ec87a5d1ca87d30e769d976f6`.

Apple-Playlists, Alben und eigene Radiotexte verwenden `custom` für Alexa Media Player beziehungsweise `alexa_devices.send_text_command` für Alexa Devices. Gerät und Integration werden aus HA verifiziert; bei unklarer Zuordnung wird kein anderes Gerät verwendet. Quelle: [Alexa Devices-Abspielimplementierung](https://github.com/home-assistant/core/blob/dev/homeassistant/components/alexa_devices/media_player.py), [Textbefehl-Service](https://github.com/home-assistant/core/blob/dev/homeassistant/components/alexa_devices/services.yaml), [Alexa Media Player: Apple Music](https://github.com/alandtse/alexa_media_player/wiki#apple-music).

Nach einem erfolgreich gesendeten Apple-Favoritenbefehl verschwindet das alte Senderlogo sofort, auch während einer bereits laufenden Zustandsabfrage. Bekannte alte Radio-Logos und Radio-/Channel-Metadaten werden in dieser Auswahl verworfen. Das bestätigt den gesendeten Befehl, nicht die tatsächliche Apple-Wiedergabe.

Sicherung vor dieser Korrektur: [backup/main-2026-10-09-before-volume-confirmation-3d87ad9](https://github.com/criticallimit/HA-Music/tree/backup/main-2026-10-09-before-volume-confirmation-3d87ad9), Commit `3d87ad92ad5390b71482a8fe78248737a229e913`.

Unten rechts im Cover-/Logofeld sitzen **Vorheriger Titel**, **Shuffle** und **Nächster Titel**. Beide Ansichten verwenden dieselben drei Bedienelemente. Verfügbarkeit und Shuffle-Markierung stammen aus den gemeldeten HA-Funktionen und Zuständen des laufenden/pausierten Gruppenplayers, ersatzweise des konfigurierten Steuergeräts. Andere Räume werden nicht automatisch als Ziel gewählt. Live-Radio-Presets haben keine Titelwarteschlange; dort bleiben diese Symbole deaktiviert.

Apple-Playlists und Alben senden den gespeicherten vollständigen Alexa-Text unverändert über custom. Anzeigename und Alexa-Text werden unabhängig bearbeitet. Bei bestehenden Einträgen ohne Text bleibt der bisherige Befehl `spiel playlist <Name>` bzw. `spiel album <Name>` erhalten. Shuffle-/Gruppen-/Dienstzusätze werden nur gesendet, wenn sie im Text stehen; die Shuffle-Markierung folgt dem gemeldeten Zustand. Die lesende Wiederanbindung nach Add-on-Neustart bleibt unverändert.

Sicherung vor dem Playlist-Shuffle-Standard: [backup/main-2026-10-09-before-playlist-shuffle-0e1fb53](https://github.com/criticallimit/HA-Music/tree/backup/main-2026-10-09-before-playlist-shuffle-0e1fb53), Commit `0e1fb535b8a95c712ef272bbb053138295591ad4`.

Die offizielle [Alexa Devices-Integration](https://github.com/home-assistant/core/blob/dev/homeassistant/components/alexa_devices/media_player.py) stellt Vor/Zurück je nach aktuellem Inhalt bereit, derzeit aber kein Shuffle. [Alexa Media Player](https://github.com/alandtse/alexa_media_player/blob/dev/custom_components/alexa_media/media_player.py) implementiert auch Shuffle. Ob Amazon Music oder Apple Music den konkreten Befehl annimmt, hängt von der Alexa-Sitzung und den verfügbaren Funktionen ab. Fehlende Funktionen oder ein unbekannter Shuffle-Zustand bleiben gesperrt; es gibt keine ersatzweisen Sprachbefehle und keinen Playlist-Neustart. Titelbefehle laufen nur nach einem Klick, erneuter Ziel-/Funktionsprüfung und bei freigegebener Oberfläche. Shuffle wird erst anhand des von HA gemeldeten Zustands markiert.

Alexa erlaubt laut [SetShuffle-Dokumentation](https://developer.amazon.com/en-US/docs/alexa/device-apis/alexa-media-playqueue.html) den Wechsel der Reihenfolge während einer laufenden Musikwarteschlange. Der Shuffle-Button sendet dafür ausschließlich `media_player.shuffle_set` mit `shuffle: true` bzw. `false` an den geprüften aktiven Player, auch bei einer Apple-Playlist. Er startet die Playlist nicht erneut und verändert weder Lautstärken noch Power oder Play/Pause. Das Symbol ist bei gemeldetem Shuffle **hell**, bei gemeldeter normaler Reihenfolge **abgedunkelt**. Ein Klick schaltet den gewünschten Modus um; anschließend wird die Anzeige aus Home Assistant neu eingelesen. Bei unbekanntem Zustand bleibt der Button deaktiviert. Diese Anzeige bestätigt den HA-Zustand; eine echte Apple-/Alexa-Sitzung wurde hier nicht live getestet.

Der vorhandene Button **Apple Music** öffnet eine Seite im gleichen Layout wie Radio. Cover/Logo, laufende Wiedergabe, Master-Lautstärke, Play/Pause und sämtliche aktiv ausgewählten Raumgeräte werden gemeinsam verwendet. Die bestehenden Elemente werden beim Umschalten verschoben, nicht dupliziert; dadurch bleiben Lautstärken, Bedienung und vorhandene Abfragen erhalten. Anstelle der Radiosender erscheinen konfigurierte **Playlists** und **Alben**. Ohne Favoriten zeigen diese Bereiche Leeranzeigen.

Das Umschalten der Ansicht startet oder stoppt keine Musik und verändert keine Lautstärken. Ein bereits laufender Sender bleibt sichtbar und hörbar. Erst ein Klick auf eine Apple-Music-Kachel sendet einen Abspielbefehl. Dann endet die zusätzliche Radio-Metadatenabfrage; Titel, Interpret und Cover kommen aus Home Assistants Alexa-Media-Player-Zuständen. Die Auswahl gilt gemeinsam für alle geöffneten Ingress-Ansichten. Ein Klick auf einen Radiosender stellt die Radioanzeige wieder her.

### Apple Music einrichten

1. In der **Alexa-App → Musik & Podcasts** Apple Music mit dem gewünschten Apple Account verknüpfen. Der Account muss Apple Music nutzen dürfen, etwa als Mitglied eines Apple-Music-Familienabos. Ein Apple-Passwort wird im Add-on nicht benötigt.
2. Die Standardzuordnung ist bereits enthalten: Wie Radio sendet Apple Music den Befehl an **Wohnzimmer** für die Alexa-Gruppe **Wohnung**. Wohnzimmer muss unter **Alexa-Geräte** auf **Aktiv** stehen. Steuergerät und Gruppe müssen nicht erneut eingetragen werden.
3. Nur bei einer bewusst abweichenden Einrichtung können über **Optionale Konfigurationsoptionen anzeigen** ein anderes Apple-Steuergerät und eine andere Gruppe angegeben werden. Vorhandene abweichende Werte bleiben erhalten. Ein ausdrücklich leerer Gruppenwert bedeutet Wiedergabe direkt auf dem Steuergerät. Diese Ausnahmen ändern weder die Radio-Zuordnung noch die Master-/Raumregler oder die Alexa-Gruppenmitgliedschaft.
4. HA Music einschalten, **Apple Music** öffnen und auf das kleine **+** neben **Playlists** oder **Alben** klicken.
5. Vorhandene Einträge bearbeiten oder mit **+ Hinzufügen** ergänzen. **Anzeigename** für die Kachel und vollständigen **Text an Alexa** ohne Weckwort eintragen. **Übernehmen** speichert beide Felder dauerhaft und aktualisiert die Kacheln ohne Neustart. **Abbrechen** verwirft Änderungen. Eine spätere Namensänderung verändert den Alexa-Text nicht automatisch.

### Playlists und Alben direkt in HA Music verwalten

Favoriten werden nicht mehr im Home-Assistant-Konfigurationsformular gepflegt. Nach dem einmaligen Neuaufbau dieser Erweiterung sind alle gültigen bisherigen Favoriten automatisch im neuen Fenster vorhanden. Bestehende Favoriten-IDs bleiben bei der Übernahme gleich, damit die gespeicherte letzte Quelle gültig bleibt. Die alte Supervisor-Abfrage alle fünf Sekunden entfällt.

**Übernehmen** schreibt die vollständige Liste atomar und mit Dateisynchronisierung unter `/data/apple_music_library.json`. Der Browser zeigt Erfolg erst nach bestätigter Speicherung. Schreibfehler lassen das Fenster mit den Eingaben offen; ein älterer Entwurf aus einem zweiten geöffneten Fenster darf einen neueren Stand nicht überschreiben. Namen werden als Text angezeigt. Bis zu 50 Einträge mit je 200 Zeichen für Anzeigename und Alexa-Name sind möglich. Die jeweils andere Kategorie bleibt beim Bearbeiten erhalten. Eine bewusst geleerte Liste bleibt auch nach Neustart leer und wird nicht erneut aus der alten Konfiguration gefüllt.

Die Verwaltung sendet keine HA-, Alexa- oder Supervisor-Anfrage. Sie verändert weder Stromschalter, Ready, Lautstärken noch die laufende Quelle. Bearbeiten oder Entfernen stoppt keine laufende Musik. Entfernen oder Umbenennen des gerade laufenden Favoriten kann die gespeicherte Auswahl für ein späteres normales Einschalten ungültig machen; dafür anschließend eine gültige Kachel auswählen. Es wird kein ersatzweiser Radiosender gestartet. Geräteoptionen werden weiterhin im Add-on-Konfigurationsformular gepflegt.

Sicherung vor der Oberflächenverwaltung: [backup/main-2026-10-09-before-library-editor-4c5ddf9](https://github.com/criticallimit/HA-Music/tree/backup/main-2026-10-09-before-library-editor-4c5ddf9), Commit `4c5ddf9193395871fe873c4c0d2236ce42ab1070`. Der Backup-Code enthält die neue Bibliotheksdatei nicht; bei Rückkehr müssen neu eingetragene Favoriten wieder in dessen Konfiguration übernommen werden. Die lokale Bibliothek bleibt erhalten.

Es gibt keine automatische Erkennung deiner Playlists und keine Apple-Kontoprüfung. Beim ausdrücklichen Einschalten wird die zuletzt gespeicherte Radio- oder Apple-Quelle wiederhergestellt, wie oben beschrieben. Ein Add-on-Neustart bindet die bestehende Wiedergabe ausschließlich lesend wieder an. Im Standby und während der Startvorbereitung sind Abspielbefehle gesperrt. Fehler stehen im Add-on-Protokoll/Browserprotokoll, ohne zusätzliche Meldungsfenster.

### Vereinfachte Gerätekonfiguration

Die native Home-Assistant-Geräteliste zeigt zuerst **Gerätename**, dann **Aktiv/Inaktiv** und zuletzt die technische Entity-ID. Home Assistant setzt die Zeile selbst aus den Schemafeldern zusammen; das Add-on kann die ID dort nicht allein ausblenden. Zum Umbenennen nur den Gerätenamen bearbeiten, nicht die technische Zuordnung.

Die Standardzuordnung Wohnzimmer/Wohnung wird gemeinsam für Radio und Apple Music im Code definiert. Redundante gespeicherte Apple-Standardwerte werden beim Start aus den Supervisor-Optionen entfernt; dadurch verschwinden diese beiden optionalen Felder aus der normalen Ansicht. Abweichende Altwerte und ausdrücklich direkte Wiedergabe (leere Gruppe) werden erhalten. Diese Konfigurationsbereinigung sendet keine Alexa-, Lautstärke-, Wiedergabe- oder Power-Befehle. Die optionale Schema-Unterstützung bleibt zur Kompatibilität erhalten. Eine fehlgeschlagene Supervisor-Speicherung belässt die bisherige Konfiguration und wird protokolliert.

Sicherung vor dieser Vereinfachung: [backup/main-2026-10-09-before-config-clarity-8db0cb5](https://github.com/criticallimit/HA-Music/tree/backup/main-2026-10-09-before-config-clarity-8db0cb5), Commit `8db0cb57c29155553b25f58794f05fb7874d2f73`.

Alexa wertet die Playlist-/Albumsuche anhand des Namens aus. Eine angenommene HA-Serviceanfrage bestätigt weder hörbare Wiedergabe noch die exakte Auswahl; doppelte Playlistnamen und verzögerte Titel-/Covermeldungen müssen mit dem konkreten Account praktisch getestet werden. Das Add-on setzt bei einem manuellen Favoritenstart keine Lautstärken neu und sendet keine wiederholten Startbefehle. Eine automatisch geladene Mediathek wäre eine spätere zusätzliche MusicKit/Apple-Music-API-Erweiterung. Dokumentation: [Apple Music mit Alexa](https://support.apple.com/de-de/119922), [Alexa Media Player](https://github.com/alandtse/alexa_media_player/wiki).

## Installation

`https://github.com/criticallimit/HA-Music` als Add-on-Repository in Home Assistant hinzufügen. Änderungen ohne Versionssprung erfordern einen **Neuaufbau** des Add-ons. Einrichtung der Karte: [Dashboard-Anleitung](ha_music/DASHBOARD.md).

## Aktuelles Verhalten

- Nach Add-on-Start erfolgt bei bisher aktivem oder noch unbekanntem Betriebszustand die oben beschriebene automatische Wiederanbindung. Ein gespeichertes „aus“ bleibt im Netzwerk-Standby. Nur der Einschaltbutton in HA Music startet die normale Einschaltsequenz; ein externer Schalterwechsel oder Browserreload startet keinen Sender.
- Nach Einschalten folgt der Ablauf: 45 Sekunden warten → Alexa-Integration einmal neu laden → 20 Sekunden warten → aktiv konfigurierte Geräte mit homeassistant.update_entity aktualisieren → 5 Sekunden warten → Raumwerte vorbereiten und nur gespeicherte Stummschaltungen anwenden → Bereit-Helfer/Oberfläche → gespeicherte Radio- oder Apple-Quelle einmal anfordern → Masterwert auf nicht stumme Räume anwenden. Es gibt keine vorübergehende 1%-Probe und keinen echten Gruppenlautstärkebefehl. Stumme Räume und ein stummer Master bleiben bei 0%. Danach sind die Räume unabhängig regelbar.
- Der Countdown zeigt die festen Pausen von insgesamt 70 Sekunden. Die Laufzeit von Reload, Aktualisierung und Lautstärkebefehlen kommt hinzu. Die Oberfläche wird nach der Startlautstärke freigegeben; die Bedienung wartet noch auf Senderstart und Restore. Gerätezustände wie unknown/unavailable sperren diese Schritte nicht. Servicefehler werden protokolliert und nachfolgende Schritte nach Möglichkeit fortgesetzt. Ready bestätigt keine hörbare Wiedergabe. Der zugehörige Integrationseintrag wird über ein aktiv konfiguriertes Gerät gewählt, ohne feste Config-Entry-ID.
- Während Startzeit, Reload und nachfolgende Lautstärke-/Senderbefehle abgeschlossen werden, bleiben die Regler gesperrt, damit manuelle Befehle nicht mit der Wiederherstellung kollidieren. Fehler dabei stehen im Add-on-Protokoll und verstecken die Oberfläche nicht. Unverfügbare Einzelgeräte bleiben als solche erkennbar. Manuelle Befehle prüfen weiterhin die Gerätefreigabe, aber nicht den gemeldeten Verfügbarkeitszustand; ein automatischer Senderbefehl wird höchstens einmal versucht, ohne vorgeschaltete Verfügbarkeitswartephase. Restore-Werte werden an alle aktiv ausgewählten Räume gesendet, auch bei unknown/unavailable. Ein fehlgeschlagener Stummschaltungs- oder update_entity-Aufruf verhindert den einmaligen Senderbefehl nicht. Fehlende Raumwerte übernehmen den Master, außer ein beobachteter Raumwert von 0 zeigt Stummschaltung an. Fehlt der Master und sein gültiger HA-Wert, wird der zuletzt gespeicherte positive Master verwendet; fehlt auch dieser, bleibt die Startlautstärke 0. Es gibt keinen 1%-Rückfallwert. Damit kann die tatsächliche Startlautstärke bei abgelehnten Alexa-Befehlen nicht garantiert werden. Das bestehende feste Senderziel ist `media_player.wohnzimmer`.
- Beim Ausschalten werden Startabläufe und Metadaten abgebrochen. Nach zehn Sekunden sperrt das Add-on neue HA-Anfragen. Grenzen bereits laufender Netzwerk-/Alexa-Aufträge: [Prüfbericht](AUDIT.md).
- Raumregler speichern ihre Sollwerte, zeigen aber ausschließlich die von HA gemeldete aktuelle Lautstärke. Stummschaltung setzt die Lautstärke auf 0, ohne Räume aus einer Alexa-Gruppe zu entfernen.
- Sender, Radio-/Apple-Ansicht und Lautstärkewünsche liegen dauerhaft unter `/data`. Die Radioansicht während Standby überschreibt die gespeicherte Ansicht nicht.
- Radiotext wird bei Freigabe der gespeicherten Radioansicht unabhängig von Alexa-Play-Aufrufen geladen. Live-Ereignisse und die vorhandene Playback-Statusabfrage liefern denselben gemeinsamen Metadatenstand. Radiotext ist keine Bestätigung hörbarer Alexa-Wiedergabe.
- Alexa-Entities werden aus den Integrationen `alexa_devices` und `alexa_media` erkannt. Zusätzlich wird das vollständige Entitätenverzeichnis jedes gefundenen Alexa-Kontos abgefragt, damit auch nicht geladene Media Player erscheinen. Gerätenamen stammen bei fehlendem Live-Zustand aus dem Geräteverzeichnis. Unter **Add-on → Konfiguration → Alexa-Geräte** entscheidet der Schalter **In Ingress anzeigen und steuern**, welche Geräte angezeigt und gesteuert werden.
- Gleichzeitige Geräteerkennungen verwenden einen gemeinsamen Katalog mit 30 Sekunden Gültigkeit. Ein Integrationsreload verwirft ihn; Standby sperrt auch den Zugriff auf diesen Cache. Beim Lautstärke-Restore werden auch offline gemeldete, aktiv ausgewählte Räume angesprochen.

Die konkrete Installation verwendet weiterhin `switch.alexa_alle`, `input_boolean.alexa_hochgefahren` und die Master-Gruppe `media_player.wohnung`. Senderbefehle werden über `media_player.wohnzimmer` an die vorhandene Alexa-Gruppe Wohnung geschickt. Für diese Sender muss das Wohnzimmer in der Geräteauswahl aktiviert bleiben. Zusätzliche Raumgeräte lassen sich über die Konfiguration einbeziehen; ihre Zugehörigkeit zur echten Alexa-Multiroom-Gruppe wird dadurch nicht geändert.

## Räume und Gruppenwiedergabe steuern

Die Raum-Schalter zeigen dasselbe kleine **Lautsprechersymbol** wie der Master: normal bei hörbarer Lautstärke, durchgestrichen bei Stummschaltung. Ein Klick schaltet ausschließlich die Raumlautstärke um; die Alexa-Gruppenwiedergabe läuft weiter. Beim Stummschalten wird die zuletzt eingestellte positive Lautstärke nur für die laufende Sitzung gemerkt und beim erneuten Hörbarschalten wiederhergestellt. Ohne Sitzungswert wird eine positive Master-Lautstärke verwendet, andernfalls 30%. Die Stummschaltung selbst bleibt dauerhaft gespeichert. Lautstärkeregler bleiben im Betrieb unabhängig bedienbar; beim nächsten normalen Einschalten gilt der Master für nicht stumme Räume.

Beim Ziehen des Masters folgen die betroffenen Raumregler, Prozentzahlen und Lautsprechersymbole sofort dem gewünschten Wert, noch bevor der Abspielgeräte-Befehl bestätigt ist. Der Befehl wird weiterhin erst beim Abschließen der Regleränderung gesendet. Auch Master-Stumm/Hörbar zeigt die betroffenen Räume sofort an. Einzeln stumme Räume bleiben unverändert; eine Master-Stummschaltung behält die vorherige Aktiv-Auswahl für späteres Hörbarschalten. Backend und Oberfläche verwenden dafür dieselbe Auswahl der betroffenen Räume.

Verzögerte HA-Werte setzen diese lokale Anzeige bis zu 60 Sekunden nicht zurück. Sobald der gewünschte Wert gemeldet wird, folgt der Raum wieder den regulären HA-Werten. Bei einem abgelehnten Befehl wird die Vorschau verworfen; ohne Bestätigung endet sie nach der Frist bei der nächsten Abfrage. Neuere einzelne Raumbedienungen und Power-Wechsel haben Vorrang vor alten Antworten. Die sofortige Anzeige ist eine Bedienvorschau und kein Beleg für die tatsächliche Geräte-Lautstärke. Außerhalb dieser Master-Bedienvorschau bleibt die Raumdarstellung bei den gemeldeten HA-Werten; es gibt weiterhin nur eine Prozentzahl.

Sicherung vor der sofortigen Master-Anzeige: [backup/main-2026-10-09-before-instant-master-6461902](https://github.com/criticallimit/HA-Music/tree/backup/main-2026-10-09-before-instant-master-6461902), Commit `64619028b3b190c856f3708ecb99419ebcf976df`.

Direkt neben dem Lautsprechersymbol des Masters sitzt ein gleich großes **Play/Pause-Symbol** für die gesamte Gruppe. Es zeigt Pause bei laufender Wiedergabe und Play bei pausierter Wiedergabe. Es ist nur aktiv, wenn Home Assistant den passenden Gruppenzustand und die benötigte Funktion meldet. Fortsetzen startet keinen neuen Sender aus einem inaktiven/unbekannten Zustand. Raum-Schalter, Gruppen-Pause und das zentrale Ausschalten sind unterschiedliche Funktionen: Nur der zentrale HA-Music-Ausschaltbutton startet den Netzwerk-Standby. Ein Pausebefehl an ein einzelnes Alexa-Gruppenmitglied könnte die gesamte Gruppe pausieren; deshalb gibt es keine individuellen Pausebuttons.

## Geräte auswählen

1. Nach dieser Änderung das Add-on-Repository aktualisieren und HA Music **neu aufbauen**.
2. HA Music einmal über den lokalen Einschaltbutton starten. Nach der Startvorbereitung (70 Sekunden feste Pausen plus Befehlslaufzeiten), Senderstart und Restore werden alle registrierten Alexa-Media-Player in der Add-on-Konfiguration ergänzt.
3. **Add-on → Konfiguration** neu öffnen. Unter **Alexa-Geräte** beim gewünschten Eintrag den **Stift** öffnen und **HA-Music-Status** auf **Aktiv** oder **Inaktiv** setzen. **Nicht löschen**: Bei Inaktiv bleibt das Gerät zur späteren Aktivierung in der Liste. Bei Bedarf sind Entity-ID und Anzeigename editierbar; die Entity muss in einer unterstützten Alexa-Integration registriert sein.
4. **Speichern** und das Add-on **neu starten**. Bereits laufende oder pausierte Wiedergabe wird automatisch wieder angebunden. War HA Music bewusst ausgeschaltet, anschließend lokal einschalten.

Wohnung, Wohnzimmer, Küche und Bad bleiben als bisherige Geräte standardmäßig aktiviert. Weitere neu gefundene Geräte sind zunächst deaktiviert. Gespeicherte Geräteauswahl und Anzeigenamen und zeitweise nicht gefundene Geräte bleiben erhalten. Eine leere Geräteauswahl aktiviert keine Geräte automatisch.

Deaktivierte Geräte erscheinen weder als Raumregler noch als Gruppe in Ingress. Sie erhalten keine Stummschaltungs-, Restore-, Master-Angleichungs-, `update_entity`- oder manuellen Lautstärkebefehle durch HA Music. Das Einschalten eines Eintrags schaltet das physische Gerät nicht ein: Ein nicht verfügbares Gerät wird als nicht verfügbar angezeigt. Der gemeinsame Radioschalter und die tatsächliche Alexa-Gruppenmitgliedschaft bleiben eigenständig.

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
python -m unittest discover -s tests -p 'test_logo_sources.py' -v
node --test tests/frontend.test.cjs
node --check ha_music/web/app.js
node --check ha_music/lovelace/ha-music-card.js
node --check ha_music/lovelace/ha-music-card-loader.js
```

Die CI führt diese Prüfungen bei Push auf `main` aus. Geprüfte Änderungen werden auf `main` übernommen; vor jeder Änderung wird dessen exakter bisheriger Commit in einem neuen Backup-Branch gesichert und überprüft. Die Arbeitsregeln stehen in [AGENTS.md](AGENTS.md). Releases, Versionsänderungen und Installationen nur nach ausdrücklicher Freigabe. Befunde, Änderungen, Testumfang und verbleibende Risiken: [Codeprüfung vom 9. Oktober 2026](AUDIT.md).

Bei der Gesamtprüfung wurden unbenutzte Sender-/Gerätekataloge, alte Lautstärke-Angleichung, tote Frontend-Zähler und die unbenutzte Lautstärke-Reconciliation entfernt. Die Statusanzeige braucht keine zusätzliche HA-Abfrage; tatsächliche Playerzustände werden weiterhin separat gelesen. Raum-Stumm/Hörbar richtet sich nach der angezeigten HA-Lautstärke, statt durch einen alten Sollwert blockiert zu werden. Nach Wiederanbindung behält Master-Stumm/Hörbar die zuvor beobachtete Raum-Auswahl. Metadaten inaktiver Geräte und unabhängiger Räume werden nicht als aktuelle bekannte Gruppenwiedergabe ausgegeben. Beim Wechsel einer Quelle ist Gruppen-Play/Pause ebenfalls gesperrt. Die alten Tests zur Master-Angleichung wurden durch Tests der tatsächlich geforderten Raum-Wiederherstellung ersetzt. Die Konfigurationsbeschreibung nennt die bereits vorhandene Apple-Favoritenunterstützung.

Sicherung vor dieser Gesamtprüfung: [backup/main-2026-10-09-before-addon-audit-603f18c](https://github.com/criticallimit/HA-Music/tree/backup/main-2026-10-09-before-addon-audit-603f18c), Commit `603f18ccf4bfd0982801ea061c04a1f3f40eb0e0`. Frühere, teilweise abgelöste Prüfschritte stehen getrennt in [AUDIT_HISTORY.md](AUDIT_HISTORY.md).

Rückkehrpunkt vor der automatischen Wiederanbindung und den Titel-/Shuffle-Bedienelementen: [backup/main-2026-10-09-before-playback-recovery-7d93a7c](https://github.com/criticallimit/HA-Music/tree/backup/main-2026-10-09-before-playback-recovery-7d93a7c), Commit `7d93a7ca1965850bdb6899ec330096b608e52e6b`, Add-on-Version `0.0.6`. Die GitHub-Prüfung dieses Stands war erfolgreich; die bekannten Neustartfehler sind darin noch enthalten. Für eine Rückkehr wird nach Sicherung des dann aktuellen `main` ein neuer Commit mit dem gesicherten Dateistand erstellt. So bleibt die Historie erhalten. Die Code-Sicherung umfasst keine laufenden HA-Zustände oder Add-on-Daten unter `/data`; vor einer Installation zusätzlich eine Home-Assistant-Sicherung anlegen.

Alte Gerätewerte `enabled: true/false` bleiben beim Umstieg gültig. Beim nächsten Start des Add-ons werden sie automatisch in `status: Aktiv/Inaktiv` umgewandelt und aus der gespeicherten Liste entfernt. Anschließend die Konfigurationsseite neu öffnen. Das optionale Feld „Bisherige Auswahl“ dient nur der Kompatibilität; wenn ein HA-Music-Status gesetzt ist, gilt dieser. Neu erkannte Geräte erhalten `Inaktiv`.

Die Konfiguration lässt sich bei ausgeschaltetem Radio bearbeiten. Zur Übernahme älterer Werte muss nur das Add-on gestartet werden, nicht das Radio. Die Optionsmigration liest/speichert ausschließlich die eigenen Supervisor-Optionen; die separate Wiederanbindung liest gegebenenfalls HA-Zustände wie oben beschrieben. Alexa und der Radioschalter erhalten dabei keine Befehle. Nach Änderungen speichern und das Add-on neu starten.


### Titelauswahl für importierte Playlists

Playlistkacheln öffnen wie Albumkacheln eine nummerierte Titelliste. „Ganze Playlist abspielen“ sendet weiterhin den gespeicherten Alexa-Text. Einzelne Titel werden über denselben Apple-Music-Wiedergabeweg wie Albumtitel als Titel und Interpret an Alexa gesendet. Importiert wird nur die Titelliste; Audiodateien und Apple-Anmeldedaten werden nicht benötigt. Alexa muss den gewählten Song auf Apple Music finden können; reine lokale Dateien sind dadurch nicht als Audio verfügbar.

Unter **Apple Music → Plus neben Playlists → Titelliste importieren** beim gewünschten Eintrag eine Datei auswählen und anschließend **Übernehmen** drücken. Unterstützt werden der XML-Export einer einzelnen Apple-Music-/iTunes-Playlist sowie tabulatorgetrennter Text und CSV mit den Spalten `Name`/`Artist` oder `Titel`/`Interpret`. CSV darf Komma oder Semikolon verwenden; Unicode-Text in UTF-8 oder UTF-16 wird unterstützt. Maximal 1 MB und 1000 Titel je Export. Reihenfolge und doppelte Titel bleiben erhalten. Fehlgeschlagene Importe und Abbrechen ändern die gespeicherte Titelliste nicht. Import und Speichern sind auch im Standby rein lokal möglich.

Auf dem Mac: Playlist auswählen, **Ablage → Mediathek → Playlist exportieren**, Format XML oder Text wählen ([Apple-Anleitung](https://support.apple.com/de-de/guide/music/mus27cd5060f/mac)). Alternativ eine CSV mit Titel und Interpret importieren. Ein erneuter Import ersetzt nach Übernehmen die vorherige Titelliste; Änderungen in Apple Music werden nicht automatisch synchronisiert. Playlists ohne Import können im Dialog weiterhin vollständig abgespielt werden.

Die bestehende Albumauswahl, Radio-/Standby-Steuerung und automatische Kartenanpassung bleiben erhalten. Die tatsächliche Alexa-Erkennung eines importierten Songs muss auf den Geräten geprüft werden. Kein Developer-Zugang, keine MusicKit-Anmeldung, kein Release oder Versionswechsel.

### Quadratische Kacheln und Albumcover

Playlist- und Albumkacheln haben ein Seitenverhältnis von 1:1 ohne Innenabstand oder sichtbaren Rahmen. Der Name liegt unten innerhalb der Kachel, die aktive Auswahl bleibt markiert. Albumcover werden über die öffentliche Apple-Katalogsuche ergänzt; persönliche Playlistcover werden weiterhin nicht abgerufen. Alexa liefert das Bild der laufenden Wiedergabe; dieses ist keine verlässliche Identifikation eines Playlistcovers. Vorababruf persönlicher Playlists benötigt Apple Music API/MusicKit mit Developer- und Music-User-Token; öffentliche Albumcover können über die iTunes-Suche ermittelt werden, müssen bei mehrdeutigen Treffern bestätigt werden. Alternativ ist eine eigene Coverdatei pro Favorit möglich.

Quellen: [Apple Music API](https://developer.apple.com/documentation/applemusicapi/), [persönliche Playlists](https://developer.apple.com/documentation/applemusicapi/get-all-library-playlists), [iTunes-Suche](https://performance-partners.apple.com/search-api), [Alexa Media Player](https://github.com/alandtse/alexa_media_player).

Sicherung davor: [backup/main-2026-10-09-before-square-tiles-0987f50](https://github.com/criticallimit/HA-Music/tree/backup/main-2026-10-09-before-square-tiles-0987f50), Commit 0987f5066209bc35d3a1e93525324e5f4053098c.


### Albumcover und Playlistnamen

Albumkacheln zeigen randfüllende Cover aus Apples öffentlichem iTunes-Katalog (DE). Die Suche erfolgt automatisch im bereiten Betrieb, vor dem Abspielen, anhand des gespeicherten Alexa-Namens beziehungsweise Anzeigenamens. Nur genau ein passender Albumtitel oder die genaue Kombination aus Interpret und Albumtitel wird automatisch zugeordnet. Keine Übernahme des laufenden Songcovers als Playlistbild. Bei mehreren Versionen, fehlenden Treffern oder abweichenden Namen bleibt die beschriftete Kachel bedienbar.

Im Albumfenster kann **Cover suchen** mehrere Treffer mit Cover, Interpret und Albumtitel anzeigen. Den passenden Treffer auswählen und **Übernehmen** drücken. Die gewählte Katalog-ID bleibt dauerhaft gespeichert; sie ändert weder den Alexa-Abspielnamen noch die Favoriten-ID oder die zuletzt gespeicherte Quelle. Änderungen an Name oder Suchbegriff lösen die bisherige Coverzuordnung. Unterschiedliche Versionen desselben Albums benötigen unterschiedliche Anzeigenamen.

Coverdaten werden unter /data/album_artwork.json für 24 Stunden zwischengespeichert (bis zu 100 Such-/ID-Einträge). Bestätigte Albumcover werden einmal von Apples Bildserver geladen und dauerhaft lokal gespeichert; unbestätigte Suchvorschauen verwenden weiterhin Apples Bildserver. Bei Bedarf wird beim ersten Download die ursprüngliche kleinere Auflösung verwendet. Kein Developer-Token oder Apple-Login erforderlich. Ein separater Apple-Music-Link unter dem Cover führt zum zugeordneten Album; der Kachelbutton startet weiterhin über Alexa. Coverabruf sendet keine HA-/Alexa-Befehle und keine Supervisor- oder HA-Zugangsdaten. Neue Kataloganfragen sind in Standby oder Startvorbereitung gesperrt und beim Ausschalten abbrechbar; ein Mindestabstand begrenzt die Anfragen auch bei mehreren Browserfenstern.

Playlists verwenden ausschließlich ihren eingetragenen Namen, mittig und mit Zeilenumbrüchen in der quadratischen Kachel. Lange Namen erhalten kleinere Schrift; der vollständige Name bleibt als Tooltip und barrierefreier Buttonname vorhanden.

Sicherung davor: [backup/main-2026-10-09-before-album-covers-babb353](https://github.com/criticallimit/HA-Music/tree/backup/main-2026-10-09-before-album-covers-babb353), Commit babb353eb8bd01fcd2b86d7bad5c017b00308da3. Die zusätzliche Album-ID ist vom älteren Code noch nicht unterstützt; bei einer Rückkehr die Bibliotheksdatei separat sichern und diesen optionalen Wert aus den Einträgen entfernen.


### Bestätigte Cover dauerhaft lokal speichern

Bereits gespeicherte Album-IDs werden beim ersten bereiten Betrieb automatisch übernommen: Das bestätigte Bild wird nach /data/album_covers/<Album-ID>.image geladen, geprüft und atomar mit Dateisynchronisierung gespeichert. Die zugehörigen Albuminformationen liegen daneben als JSON. Die Oberfläche bezieht das Bild anschließend über den eigenen Ingress-Endpunkt api/album-art/<Album-ID>; Browser dürfen diese unveränderliche Zuordnung langfristig zwischenspeichern. Nach Neustart wird das lokale Bild wiederverwendet, auch wenn der Suchcache abgelaufen ist oder das Internet fehlt. Für bestätigte lokale Bilder gibt es keine 24-Stunden-Neuladung. Bilder in noch unbestätigten Suchtreffern bleiben Vorschauen vom Apple-Bildserver.

Nur JPEG, PNG und WebP bis 3 MiB werden übernommen; HTML, SVG, ungültige Antworten und abgebrochene Downloads werden nicht als Cover ausgeliefert. Es wird keine HA-Zugangsinformation an Apple gesendet. Der lokale Bild-Endpunkt bleibt ingressbeschränkt, verwendet ausschließlich numerische Album-IDs und löst keine Internetanfrage aus. Der erstmalige Download ist weiterhin nur bei bereitem HA Music erlaubt und wird bei Ausschalten abgebrochen.

### Playlistdiagnose Dirk / Dirk Favoriten

Der Vergleich vor und nach der Oberflächenverwaltung zeigt denselben Abspieltext. Die Migration und das erneute Speichern verwenden weiterhin den Alexa-Namen, nicht den Anzeigenamen. Ein Regressionstest prüft Dirk als Anzeigename und Dirk Favoriten als Alexa-Name mit identischer Favoriten-ID und genau einer unveränderten Wiedergabeanfrage. Warum Alexa die Playlist in der konkreten Sitzung nicht findet, ist ohne Geräteantwort noch nicht bestätigt. Daher wird keine automatische alternative Wiedergabe oder Wiederholung gestartet. Bei jedem Kachelstart protokolliert Apple playback request den tatsächlichen Alexa-Namen, das Zielgerät, die Gruppe und den vollständigen Befehl, ohne Token oder Zugangsdaten. Diese Zeile ist die Grundlage der weiteren Diagnose; ein angenommener Servicebefehl bestätigt weiterhin nicht Alexas Suchergebnis.

Sicherung davor: [backup/main-2026-10-09-before-local-artwork-8e93376](https://github.com/criticallimit/HA-Music/tree/backup/main-2026-10-09-before-local-artwork-8e93376), Commit 8e93376fd8eae18d76ab3e26ccf683ae61a4e63a.


### Gespeicherte Cover ohne Warteschlange anzeigen

Lokale Coveradressen werden direkt in der Albumliste der normalen Zustandsantwort geliefert. Sie erscheinen beim Aufbau der Kacheln, ohne zusätzlichen album-covers-Aufruf oder die bisherigen 4,5 Sekunden Pause pro Album. Lokale Bilder werden sofort geladen, auch wenn der Apple-Bereich noch verborgen ist; Browsercache und Ingress liefern die vorhandenen Dateien. Frühere externe Coveradressen im Browser haben keinen Vorrang vor der lokalen Datei. Der Server prüft dafür nur Dateigröße und Bildsignatur statt bei jeder Zustandsabfrage alle Bilder vollständig einzulesen. Die Warteschlange bleibt für fehlende, erstmalig zu suchende bzw. herunterzuladende Cover bestehen. Favoriten-IDs, Playback und Standby bleiben unverändert.

Sicherung davor: [backup/main-2026-10-09-before-immediate-covers-9b51d91](https://github.com/criticallimit/HA-Music/tree/backup/main-2026-10-09-before-immediate-covers-9b51d91), Commit 9b51d91fec4ff6179a05d120c93c76e0a9e33744.

### Dezente Musik-Kacheln

Playlist-Kacheln zeigen ein lokal eingebettetes Apple-Logo mit 14% Deckkraft hinter dem lesbaren Namen. Aktive Radio-, Playlist- und Albumkacheln behalten die normale Fläche und werden mit einem Rand in der bisherigen Akzentfarbe markiert; der Albumrand liegt über dem Cover. Playlist- und Albumbereiche sind bündig mit den Lautstärkekarten ausgerichtet. Mobile Ansicht und quadratische Albumcover bleiben erhalten. Das Logo benötigt keine Netzwerkanfrage. SVG-Quelle: [Simple Icons Apple](https://github.com/simple-icons/simple-icons/blob/develop/icons/apple.svg).

Sicherung davor: [backup/main-2026-10-09-before-library-style-a81ca6a](https://github.com/criticallimit/HA-Music/tree/backup/main-2026-10-09-before-library-style-a81ca6a), Commit `a81ca6a7e5474130bc906eff6abf40b14e83c11f`.

### Kompakte Kopfzeile

Radio und Apple Music stehen als getrennte Buttons mit etwas Abstand. Der aktive Tab verwendet einen Akzentrand statt einer farbigen Fläche, auch in der Dashboard-Karte. Die Kopfzeile ist niedriger, die rechten Bedienelemente sind kleiner und deaktivierte Ein-/Aus-Buttons deutlich abgedimmt. Die bestehende Aktivierung der Bedienelemente bleibt unverändert.

Sicherung davor: [backup/main-2026-10-09-before-compact-header-8d07aa1](https://github.com/criticallimit/HA-Music/tree/backup/main-2026-10-09-before-compact-header-8d07aa1), Commit `8d07aa1f4162b10726f00ed4a016bfc3f9c2faf3`.

### Master nach angelaufener Wiedergabe anwenden

Beim normalen Einschalten reicht ein gemeldeter Lautstärkewert nicht mehr als Abschluss der Wiederherstellung: Alexa Media Player kann ihn bereits vor der Geräteausführung setzen. Zusätzlich zum ersten Restore erhält jeder aktive Raum seinen gleichen Start-Master einmal erneut, nachdem er dreimal im Abstand von zwei Sekunden `playing` gemeldet hat. Anschließend gilt die begrenzte Lautstärkeprüfung. Es werden höchstens 15 Abfragen zur Einschwingphase durchgeführt; Räume ohne stabile Wiedergabemeldung erhalten keinen zusätzlichen positiven Befehl. Stumme Räume/Master bei 0, deaktivierte Räume, Ausschalten und neuere manuelle Lautstärken bleiben geschützt. Ein späteres Pausieren verhindert weitere positive Retry-Befehle. Sender oder Playlist werden hierbei nicht neu gestartet. Die ausschließlich lesende Wiederanbindung nach Add-on-Neustart/Update startet diese Funktion nicht.

Im Protokoll bestätigt `Startup master reapplied after playback` den zusätzlichen Befehl; die Annahme und HA-Werte sind weiterhin kein Nachweis hörbarer Geräteausgabe.

Sicherung davor: [backup/main-2026-10-09-before-delayed-start-volume-7addae9](https://github.com/criticallimit/HA-Music/tree/backup/main-2026-10-09-before-delayed-start-volume-7addae9), Commit `7addae96aa53eb1cd1624701e9a065ecea93c6af`.

### Apple-Music-Anbieterübergabe

Apple-Favoriten starten jetzt mit `media_content_type: APPLE_MUSIC`. Die Suchanfrage enthält den unveränderten Alexa-Namen, den Typ (`meine Playlist` oder `Album`), bei Playlists den Shuffle-Wunsch und gegebenenfalls `auf <Gruppenname>`. Der vollständige Sprachbefehl mit `spiele` und `auf Apple Music` entfällt. Es wird genau ein Abspielauftrag gesendet, ohne zusätzlichen Textbefehl oder Wiederholungsstart. Auswahl und Wiederanlauf verwenden denselben Anbieterweg. Ob Alexa persönliche Playlists, Gruppen und Shuffle damit auf der Installation richtig erkennt, muss nach Neuaufbau getestet werden; ein angenommener Serviceaufruf bestätigt keine hörbare Wiedergabe.

Sicherung davor: [backup/main-2026-10-09-before-apple-provider-76b7012](https://github.com/criticallimit/HA-Music/tree/backup/main-2026-10-09-before-apple-provider-76b7012), Commit `76b70123329aa7679c24baa6020c2eb60f1d1d58`.

### Englischer Playlist-Aufruf (zurückgenommen)

Playlists verwenden jetzt genau `play MY playlist <Alexa-Name> on shuffle on Apple Music`, bei eingestellter Gruppe ergänzt um ` on <Gruppenname>`. Dies ist ein vollständiger Textbefehl (`custom` bzw. offizieller Alexa-Devices-Textservice), keine APPLE_MUSIC-Suchanfrage. Alben behalten APPLE_MUSIC. Ein Medienstart je Auswahl, keine automatische zweite Variante; derselbe Aufruf bei normalem Einschalten. Shuffle bleibt ein Wunsch und wird nicht als Gerätebestätigung dargestellt. Die Erkennung persönlicher Playlists, englischer Befehle auf deutsch eingestellten Geräten sowie der Gruppe muss live geprüft werden.

Sicherung davor: [backup/main-2026-10-09-before-english-playlists-de1a2e5](https://github.com/criticallimit/HA-Music/tree/backup/main-2026-10-09-before-english-playlists-de1a2e5), Commit `de1a2e51b1557bf792a6e443f3bda3f469705558`.

### Rückkehr zum deutschen Apple-Anbieteraufruf

Der englische custom-Playlistversuch wurde auf Nutzerwunsch zurückgenommen. Playlists und Alben verwenden wieder APPLE_MUSIC. Playlist-Suchanfrage: `meine Playlist <Alexa-Name> in zufälliger Reihenfolge auf <Gruppe>`; ohne konfigurierte Gruppe entfällt der letzte Zusatz. Alben verwenden weiter `Album <Alexa-Name> auf <Gruppe>`. Radio unverändert. Keine automatischen Fallbacks oder zusätzlichen Medienstarts; bestehende Standby-/Update- und Lautstärkesicherheit unverändert. Persönliche Playlist-Erkennung bleibt live unbestätigt.

Sicherung davor: [backup/main-2026-10-09-before-german-provider-942cb3c](https://github.com/criticallimit/HA-Music/tree/backup/main-2026-10-09-before-german-provider-942cb3c), Commit `942cb3c243a7a726b00e8939e3e86d86add8f259`.

### Einfacher bestätigter Playlist-Wortlaut

Auf Nutzerwunsch entspricht der Playlist-Text jetzt exakt dem erfolgreichen Sprachbefehl: `spiel playlist <gespeicherter Alexa-Name>`. Kein Weckwort, kein meine, kein Dienst-, Gruppen- oder Shuffle-Zusatz. Verwendung des Textbefehlwegs (custom bzw. Alexa Devices send_text_command), ein einziger Start an das freigegebene Steuergerät. Alben bleiben APPLE_MUSIC, Radio bleibt unverändert. Auch der gespeicherte Playlist-Wiederanlauf verwendet diesen Wortlaut; Add-on-Neustart-Recovery bleibt rein lesend. Alexa entscheidet anhand ihrer Kontoeinstellungen über den Musikdienst. Gleicher Wortlaut über die Integration sowie tatsächliche Geräteausgabe müssen noch live geprüft werden.

Sicherung davor: [backup/main-2026-10-09-before-simple-playlist-ecf912a](https://github.com/criticallimit/HA-Music/tree/backup/main-2026-10-09-before-simple-playlist-ecf912a), Commit `ecf912aa81cc416e14105b0f657dac8877c6f94f`.

### Apple-Favoriten als Textbefehl mit Listenname

Playlists und Alben verwenden auf Nutzerwunsch wieder ausschließlich den custom-Textbefehlweg. Verwendet wird der sichtbare Name aus der Liste, nicht das bisherige separate Alexa-/Suchnamenfeld: `spiel playlist <Name>` bzw. `spiel album <Name>`. Keine Gruppen-, Shuffle- oder Dienstzusätze; Alexa bestimmt den Dienst anhand ihrer Einstellungen. Das Bearbeitungsfenster benennt den Namen für Anzeige und Wiedergabe entsprechend. Das Suchnamenfeld ist bei Playlists ausgeblendet und bei Alben nur als Cover-Suchname beschriftet. Bestehende Suchnamen und Favoriten-IDs bleiben für gespeicherte Auswahlen/Cover erhalten; keine Datenmigration oder Löschung. Normales Wiederanlaufen verwendet denselben Befehl, Add-on-Recovery bleibt rein lesend. Tatsächliche Alexa-Erkennung muss live geprüft werden.

Sicherung davor: [backup/main-2026-10-09-before-custom-list-names-0ec281a](https://github.com/criticallimit/HA-Music/tree/backup/main-2026-10-09-before-custom-list-names-0ec281a), Commit `0ec281ab0cf290c2483519c71bdc5616b258daa3`.

### Anzeigename und vollständiger Alexa-Text

Das Bearbeitungsfenster enthält jetzt zwei sichtbare Eingabefelder: Anzeigename und Text an Alexa. Der vollständige Text wird als command dauerhaft in der lokalen Bibliothek gespeichert und exakt über custom bzw. den offiziellen Alexa-Textservice gesendet. Keine automatischen Präfixe, Suffixe, Dienst-/Gruppen-/Shuffle-Zusätze und keine automatischen Wiederholungen. Der gespeicherte Text wird auch beim normalen Wiederanlauf verwendet. Alte Einträge ohne command behalten ihren bisherigen generierten Befehl; das Fenster zeigt ihn als editierbaren Text. Neues command-Feld ist unabhängig vom Namen und vom internen Cover-Suchnamen. Ändern allein des Befehls erhält die Favoriten-ID und Cover-Zuordnung. Leere/ungültige Texte oder widersprüchliche Duplikate werden vor Speicherung abgelehnt; Revisionsprüfung und dauerhafte Speicherung bleiben bestehen. Maximal 500 Zeichen, keine Zeilenumbrüche/Steuerzeichen. Lokales Speichern startet keine Wiedergabe, auch im Standby. Bestehende Radio-Presets unverändert.

Sicherung davor: [backup/main-2026-10-09-before-editable-alexa-text-ac18388](https://github.com/criticallimit/HA-Music/tree/backup/main-2026-10-09-before-editable-alexa-text-ac18388), Commit `ac183883db4f6712881b2e6798b504ca6f43f26a`.

## Lovelace-Kartengröße

Radio und Apple Music verwenden dieselbe verfügbare Höhe und Skalierung.
Die Playlist-/Album-Auswahl wird auf den verbleibenden Platz der Radioansicht
begrenzt, statt Cover und Regler zusätzlich zu verkleinern. Bei wenig Höhe
stehen Playlists und Alben nebeneinander als kompakte, separat scrollbare Listen.
Bei mehr Platz bleiben die beiden Bereiche untereinander. Das Apple-Cover wird
in der eingebetteten Karte vollständig eingepasst. Die direkte Add-on-Ansicht
behält ihr bisheriges Layout. Sicherung davor:
[backup/main-2026-10-10-apple-layout-2fb0b8d](https://github.com/criticallimit/HA-Music/tree/backup/main-2026-10-10-apple-layout-2fb0b8d), Commit `2fb0b8d5cb57117ca91754d38555eaa56a1bad5b`.

Die Karte übernimmt die verfügbare Dashboard-Spaltenbreite. Ihre Höhe richtet sich automatisch nach dem verbleibenden Bildschirmplatz unterhalb der Kartenposition, mit 16 Pixeln Abstand zum unteren Rand. Die gesamte Oberfläche bleibt ohne äußere Scrollbalken sichtbar; falls Bedienelemente mehr Platz benötigen, wird sie proportional verkleinert. Die Berechnung reagiert auf Fenstergröße und mobile Bildschirmhöhe.

```yaml
type: custom:ha-music-card
```

Breiten- und Höhenfelder sind aus dem Karteneditor entfernt. Alte `width`-/`height`-Angaben werden ignoriert und bei der nächsten Theme-Änderung im Editor entfernt. Playlists und Alben besitzen jeweils einen begrenzten, intern scrollbareren Bereich; zusätzliche Favoriten vergrößern die Karte nicht. Die direkte Add-on-Seite behält ihr normales responsives Layout. Bearbeitungs- und Titelauswahldialoge können weiterhin intern scrollen.
## Automatische Playlist-Titellisten vom Mac

Playlists können ohne Apple-Developer-Konto automatisch aus der Musik-App auf
dem Mac synchronisiert werden. Der optionale, durch einen eigenen Schlüssel
geschützte Endpunkt aktualisiert Playlist-Titel und kann auf ausdrücklichen Wunsch
neue Playlist-Favoriten anlegen, ohne Wiedergabe zu starten.
Der Helfer läuft bei Anmeldung und alle 30 Minuten. Einrichtung und Abschalten:
[Mac-Playlist-Synchronisierung](tools/MAC_PLAYLIST_SYNC.md).

Rückkehrpunkt vor dieser Erweiterung: [backup/main-2026-10-10-mac-playlist-sync-867020a](https://github.com/criticallimit/HA-Music/tree/backup/main-2026-10-10-mac-playlist-sync-867020a), Commit `867020a2c29a79d9e64926bd0c7dcfdebb49107a`. Keine Versionsänderung und keine Installation durch das Aktualisieren von `main`.

### Anklickbare Mac-App

[HA Music Playlist Sync](tools/MAC_PLAYLIST_APP.md) bietet ein natives Fenster
mit Playlist-Auswahl, editierbaren Zielnamen, Statusmeldungen und Schlüsselbund.
Sie benötigt weder Python noch Terminal. GitHub-CI baut ein Universal-App-ZIP
für Intel und Apple Silicon ab macOS 12, prüft die Übertragung und erzeugt eine
Vorschau der Oberfläche. Beim nächsten Öffnen kann automatisch synchronisiert
werden; optional alle 30 Minuten, solange die App offen bleibt. Neue Playlists
werden nur mit aktivierter Auswahl angelegt. Bestehende Alexa-Befehle, Alben,
Radio, Standby und Kartenskalierung bleiben erhalten.

Die App ist ad hoc signiert, nicht mit Developer ID signiert/notarisiert;
der erste Start kann eine macOS-Freigabe benötigen. Keine Release-Veröffentlichung
und keine Add-on-Versionsänderung. Sicherung davor:
[backup/main-2026-10-10-mac-click-app-2f4e880](https://github.com/criticallimit/HA-Music/tree/backup/main-2026-10-10-mac-click-app-2f4e880), Commit `2f4e8804e24d421ef12c14c418ef41585e762dc6`.

## Stabile Lautstärkeregler und Stummschalter

Master- und Raumregler zeigen Eingaben sofort an. Während des Ziehens werden die
Regler nicht durch Statusabfragen ersetzt. Raum-Stummschalter reagieren sofort und
senden den konkret angezeigten Zielwert; beim Einschalten wird der zuletzt gewählte
positive Wert wiederhergestellt. Verzögerte Alexa-Werte überschreiben diese Anzeige
bis zur Bestätigung nicht (höchstens 60 Sekunden nach der Befehlsantwort, mit einer
dreisekündigen Einschwingfrist). Danach gelten wieder gemeldete Werte und externe
Änderungen. Fehlgeschlagene Befehle werden angezeigt und die Vorschau zurückgenommen.
Radio und Apple Music verwenden dieselben Regler; Standby und Wiedergabe bleiben
unverändert. Die Browserprüfungen simulieren verzögerte Rückmeldungen; tatsächliche
Geräteausführung muss im Heimnetz geprüft werden.

Sicherung davor:
[backup/main-2026-10-10-volume-controls-0462046](https://github.com/criticallimit/HA-Music/tree/backup/main-2026-10-10-volume-controls-0462046), Commit `0462046186c6f710500a61fd5aa74fdd173b0a9c`.

## Playlists und Alben selbst sortieren

Neben jeder Überschrift aktiviert **↕** den Sortiermodus für die jeweilige Liste.
Kacheln mit Maus oder Finger an die gewünschte Stelle ziehen; der Zielplatz wird
hervorgehoben. Lange Listen scrollen beim Ziehen am Rand automatisch weiter.
Mit Tastatur eine Kachel fokussieren und mit den Pfeiltasten verschieben.
**✓** beendet den Sortiermodus. Während des Sortierens öffnen Kachelklicks keine
Titelliste und starten keine Wiedergabe.

Jede Verschiebung wird sofort zentral im Add-on gespeichert, unabhängig vom
Browser, und bleibt nach Neuladen sowie Add-on-Neustart erhalten. Playlists und
Alben werden getrennt sortiert. Cover, Titel, Alexa-Befehle und die andere Liste
bleiben erhalten. Gleichzeitige Änderungen oder Mac-Importe werden durch eine
Revisionsprüfung geschützt; bei Konflikten wird die aktuelle Liste geladen und
die fehlgeschlagene Sortierung gemeldet.

Sicherung davor:
[backup/main-2026-10-10-favorite-order-c918c45](https://github.com/criticallimit/HA-Music/tree/backup/main-2026-10-10-favorite-order-c918c45), Commit `c918c455454d98ab1a4897bbdb71f368ab68c37a`.
