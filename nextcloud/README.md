# AcousticGalaxy als Nextcloud-App (ExApp)

Diese Variante bindet die Galaxie an die Nextcloud **Music**-App an: Die
Titel kommen über die Subsonic-Schnittstelle von Music, die Favoriten
(„Fixsterne“) aus deren Starred-Liste. Es wird kein lokaler Musikordner
gescannt.

## Voraussetzungen

- Nextcloud 31–36 mit **AppAPI** (ExApps) und laufendem Deploy-Daemon
  (Docker Socket Proxy oder HaRP)
- **Music**-App mit aktivierter Subsonic-Schnittstelle
- Ein in der Music-App erzeugter **API-Key** pro Benutzer
  (Einstellungen → Musik → API-Key); das Nextcloud-Passwort funktioniert nicht

## Image bauen und veröffentlichen

Der Build-Kontext ist das Repository-Wurzelverzeichnis, weil das Frontend aus
`static/` mitkopiert wird.

```bash
docker build -f nextcloud/Dockerfile -t ghcr.io/henrik-aalto/acousticgalaxy:0.1.0 .
docker push ghcr.io/henrik-aalto/acousticgalaxy:0.1.0
```

Registry, Image und Tag stehen in `nextcloud/appinfo/info.xml` und müssen zu
dem passen, was im Deploy-Daemon hinterlegt ist.

## Installation

1. `nextcloud/appinfo` als App-Verzeichnis auf dem Server ablegen, z. B.
   `custom_apps/acousticgalaxy/appinfo/info.xml`.
2. App registrieren (lädt das Image):
   ```bash
   occ app_api:app:register acousticgalaxy --force-scrutiny --wait-finish
   ```
3. In den AppAPI-Einstellungen die App für die gewünschten Benutzer oder
   Gruppen freigeben.
4. `/index.php/apps/acousticgalaxy/` öffnen und im Dialog **Musik** den
   API-Key eintragen. Danach startet die Analyse automatisch.

Alternativ lässt sich für Einzelbenutzer-Instanzen ein globaler Key über die
Umgebungsvariable `ACOUSTIC_MUSIC_APIKEY` setzen.

## Was anders ist als in der Standalone-Version

- Keine Pfadauswahl; die Bibliothek ist die der Music-App.
- Favoriten werden über `star`/`unstar` in Music gespeichert und über
  `getStarred2` geladen.
- Cover, Streaming und Lyrics laufen über die Subsonic-Endpunkte.
- Analyseergebnisse und Feature-Cache liegen pro Benutzer im persistenten
  ExApp-Volume (`APP_PERSISTENT_STORAGE`).

## Entwicklung

```bash
python3 -m pytest nextcloud/tests -q
```

Die Tests laufen ohne Nextcloud und ohne Musikdateien (Subsonic wird gemockt).
`nextcloud/ex_app/lib/main.py` benötigt `nc_py_api` und lässt sich nur im
Container oder mit installiertem `nc_py_api[app]` importieren.

Hinweis: Dieses Image nutzt den klassischen Deploy-Daemon (DSP). Für HaRP muss
zusätzlich der FRP-Client im Container gestartet werden.
