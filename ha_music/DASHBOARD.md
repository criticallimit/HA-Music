# HA Music im Home-Assistant-Dashboard

Die HA Music Ingress-Seite wird auch als Lovelace-Karte angeboten (analog TV Guide).
Beim Start des Add-ons werden die Karten-Dateien nach `/config/www/` kopiert.

1. HA Music in Home Assistant **Neu aufbauen** und **starten**.
2. Unter **Einstellungen → Dashboards → Ressourcen** folgende JavaScript-Ressource als **Modul** registrieren:
   `/local/ha-music-card-loader.js`
3. Dashboard bearbeiten → **Karte hinzufügen** → **HA Music** auswählen.
   Falls die Karte im Auswahldialog noch nicht erscheint, Browseransicht neu laden.
4. Alternativ eine manuelle Karte verwenden:

```yaml
type: custom:ha-music-card
```

Die Karte übernimmt die Spaltenbreite des Dashboards und passt sich automatisch an die verbleibende Bildschirmhöhe unter ihrer Position an. Es gibt keine Breiten-/Höheneingabe und keine äußeren Karten-Scrollbalken. Lange Playlist- und Albumlisten sind innerhalb ihres jeweiligen Bereichs scrollbar. Die Karte verwendet dieselbe Ingress-Oberfläche wie das Add-on und übernimmt die aktiven Home-Assistant-Theme-Farben. Ein optionales `theme`-Feld erlaubt eine abweichende Theme-Auswahl für diese Karte.

```yaml
type: custom:ha-music-card
theme: Dirk
```

Die Karte benötigt eine laufende HA-Music-Installation mit Supervisor-Ingress. Die Resource-Dateien werden beim Start auf die Home-Assistant-Konfiguration kopiert; die Eintragung der Dashboard-Ressource erfolgt einmalig in Home Assistant. Es wird kein eigener HTTP-Port und keine zusätzliche Wiedergabe-Integration benötigt.
