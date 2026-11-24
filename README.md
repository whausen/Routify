# Routify · Team „Pedal to the Metal“

hack4GDI 13./14.11.2026 · Challenge Fahrradtourismus · **2. Platz**

Team: Robin Hilker, Nico Heinze, Janina Wochermaier, Jan-Niklas Richter, Nils Kroner

Radtouren per Sprache oder Text planen: „3 Tage von Dresden nach Magdeburg mit Kind, 50 km am Tag, abends Hotel“
→ zwei Routenvarianten zum Vergleichen, Etappen mit Fahrzeit, Unterkünfte, Bahnhöfe als Notausstieg,
Wetter (inkl. Suche nach dem besten Tag und Rückenwind), Höhenprofil, Wege & Untergrund, GPX-Export
und die ganze Planung als **Linked Data** (Routify-Ontologie, angelehnt an die
GDI-DE-Radnetz-Ontologie, mit Wikidata). Die Wünsche aus der Anfrage werden als **SHACL-Regeln** gegen die Tour geprüft
(„3 von 4 Wünschen erfüllt – Tag 1 ohne Campingplatz“). Spracheingabe **lokal mit Whisper**: Das Audio verlässt den Rechner nicht.

Beispiele: „60 km Rundtour ab Oldenburg“, „3 Tage auf dem Weserradweg ab Hann. Münden“,
„Von Bonn nach Koblenz, möglichst flach, wenig Verkehr und am Wasser“, „Bei gutem Wetter von Mainz nach Bingen“.
Mehr steht in der App unter „Was kann ich fragen?“.

Wie eine Anfrage verarbeitet wird: [Ablaufdiagramm](docs/ablauf_anfrage.png) ([PDF](docs/ablauf_anfrage.pdf)).

> ℹ️ **Öffentliche Version.** Die beim Hackathon eingereichte Fassung nutzte zusätzlich Daten, die nur für den hack4GDI
> freigegeben waren (BKG DGM25, BKG POI-Bund, ADFC Bett+Bike), und Mapbox. Hier sind sie durch offene Quellen ersetzt
> bzw. entfernt – Details in [docs/datenquellen.md](docs/datenquellen.md#5-hintergrund-hackathon-version).

---

## Schnellstart

**1. Einmalig installieren:** Git, Python 3.11+, Java 17+ und Ollama (Laptop mit mind. 16 GB RAM empfohlen)

| macOS ([Homebrew](https://brew.sh)) | Windows (PowerShell) |
|---|---|
| `brew install git python@3.12`<br>`brew install --cask temurin@21 ollama` | `winget install Git.Git Python.Python.3.12 EclipseAdoptium.Temurin.21.JDK Ollama.Ollama`<br>(danach ein neues Terminal öffnen) |

**2. Holen und starten**

```sh
git clone <Repo-URL> routify
cd routify
./start.sh          # Windows: start.bat
```

Das Startskript erledigt den Rest:

1. legt die Python-Umgebung `.venv` an und installiert `requirements.txt` (nur beim ersten Mal),
2. **prüft, ob alles da ist** (`code/scripts/pruefen.py`) und lädt beim ersten Mal die großen Dateien herunter
   (BRouter, Routing-Daten Deutschland, Spracherkennung; zusammen ca. 1,4 GB, siehe unten),
   startet Ollama, falls es nicht läuft, und bietet an, das Sprachmodell `qwen3:8b` (ca. 5 GB) zu laden,
3. startet BRouter (Routing) und das Backend und öffnet **http://localhost:8000** im Browser.

Beenden mit `Ctrl+C`. Nur prüfen, ohne zu starten: `./start.sh pruefen` bzw. `start.bat pruefen`.

**Läuft alles?** http://localhost:8000/api/health zeigt den Status aller Dienste und Daten, z. B.

```json
{"pois": 61748, "bahnhoefe": 11325, "sprache": "Whisper small (lokal)", "ollama": "ok", "brouter": "ok"}
```

---

## Was im Hintergrund läuft

```
Browser  http://localhost:8000   (Karte, Chat, Höhenprofil)
   │
   ▼
Backend  FastAPI / Python          code/backend/                                   Port 8000
   ├── Ollama  (lokales LLM qwen3:8b, versteht die Anfrage)                         Port 11434
   ├── BRouter (Java, berechnet die Fahrradrouten)                                  Port 17777
   ├── data/prepared/  (Unterkünfte, Fahrrad-POIs, Bahnhöfe, Wikidata-Gemeinden)
   ├── data/brouter/   (Routing-Daten mit Höhen)     data/whisper/  (Spracherkennung)
   └── Internet: Photon (Ortssuche), Bright Sky (DWD-Wetter), Overpass (Radfernwege, einmalig),
                 Kartenkacheln, optional Mapillary (Straßenfotos)
```

LLM, Routing, Höhen, POIs und Spracherkennung laufen lokal. Ortssuche, Wetter und Hintergrundkarten brauchen Internet
(die Ortssuche hat einen Cache und ein Offline-Ortsverzeichnis als Rückfall). Es muss **kein API-Key** eingerichtet werden;
optionale Dienste (Straßenfotos, OpenCycleMap) lassen sich mit eigenem Key in `.env` einschalten.

Architektur, Module, Ontologie und Messwerte: [docs/architektur.md](docs/architektur.md) ·
Ablauf einer Anfrage: [docs/ablauf_anfrage.png](docs/ablauf_anfrage.png) ·
alle Daten und Dienste mit Lizenz: [docs/datenquellen.md](docs/datenquellen.md)

---

## Daten

Im Repo liegen nur die aufbereiteten Daten (ca. 75 MB). Beim ersten Start lädt `pruefen.py` automatisch:

| Was | Größe | Quelle |
|---|---|---|
| BRouter 1.7.10 (Routing-Server + Profile) | ca. 7 MB | GitHub-Release, per SHA-256 geprüft |
| Routing-Daten Deutschland (6 Kacheln, mit Höhen) | ca. 890 MB | brouter.de (aus OpenStreetMap + SRTM) |
| Spracherkennung Whisper „small“ | ca. 460 MB | Hugging Face (Systran/faster-whisper-small) |
| Sprachmodell `qwen3:8b` | ca. 5 GB | Ollama (fragt vorher) |

Inhalt und Herkunft aller Daten: [data/README.md](data/README.md).

Daten neu aufbereiten (nur nötig, wenn sich Rohdaten ändern; braucht zusätzlich GDAL/`ogr2ogr`):
`.venv/bin/python code/scripts/prepare_data.py`

---

## Mehr

### Mit anderen teilen (QR-Code, gleiches WLAN; macOS/Linux)

```sh
./start.sh teilen
```

Die Seite http://localhost:8000/teilen zeigt einen QR-Code mit der Netzadresse (z. B. `https://192.168.0.10:8443`).
Handys im selben WLAN scannen ihn und planen selbst – alles rechnet auf diesem Laptop.

- Für das Mikrofon auf dem Handy läuft ein kleines HTTPS-Tor (`code/scripts/https_tor.py`, Port 8443) mit selbst ausgestelltem
  Zertifikat (braucht `openssl`). Beim ersten Öffnen warnt das Handy: iPhone „Details einblenden“ → „diese Website besuchen“,
  Android „Erweitert“ → „Weiter zu …“.
- macOS fragt beim ersten Mal, ob Python eingehende Verbindungen annehmen darf → **Erlauben**.
- Hochschul-WLANs (z. B. eduroam) lassen Geräte oft nicht miteinander sprechen → Handy-Hotspot nutzen.

### Konto (optional)

Oben rechts auf 👤 → anmelden oder registrieren (Name + Passwort). Damit lassen sich Touren speichern, Voreinstellungen
setzen und der Verlauf ansehen. Die Konten liegen nur lokal in `data/routify.db` (entsteht beim ersten Start).

### Praktisch für Demo und Tests

- Direktlink mit Anfrage: `http://localhost:8000/?q=3 Tage von Dresden nach Magdeburg mit Kind`
- Mehrere Schritte nacheinander: `…/?q=Von Mainz nach Koblenz, 2 Tage&q=lieber 3 Tage mit Campingplatz`
- Route nach Untergrund eingefärbt: `…/?farbe=untergrund&q=…` · Einstellungen-Formular: `…/?einstellungen=1`
- Touren bis ca. 300 km gehen am schnellsten (längere Strecken dauern 20–30 s)

### Einstellungen (`.env`, optional)

`.env.example` nach `.env` kopieren. Z. B. `LLM_MODEL=qwen3:4b` für schwächere Laptops (vorher `ollama pull qwen3:4b`),
`THUNDERFOREST_KEY` für die OpenCycleMap, `MAPILLARY_TOKEN` für Straßenfotos entlang der Route.
Alle Variablen: [code/README.md](code/README.md).

### Tests (Server muss laufen)

- `.venv/bin/python code/tests/test_api.py` – 18 End-to-End-Fälle (Planung, Radweg, Rundtour, Wetter, Stopps, Konto,
  Linked Data, SHACL-Prüfung, Straßenfotos), ca. 2 min; mit `schnell` ohne LLM-Fälle
- `.venv/bin/python code/tests/test_llm_extraction.py` – 51 Anfragen Text → Parameter, ca. 5–10 min

### Linked Data

In der Planung „Daten & Herkunft“ aufklappen (SPARQL-Fragen, Download Turtle/JSON-LD).
Ontologie: http://localhost:8000/ontologie/routify.ttl · API: `POST /api/rdf`, `POST /api/sparql`, `GET /api/sparql/abfragen`, `POST /api/pruefen`

---

## Probleme?

| Symptom | Lösung |
|---|---|
| `pruefen.py` meldet ✗ | Die Meldung sagt, was fehlt; danach `./start.sh` erneut ausführen (abgebrochene Downloads werden neu geladen) |
| `"ollama": "nicht erreichbar"` | Ollama-App starten bzw. `ollama serve` |
| `"brouter": "nicht erreichbar"` | Log `/tmp/routify-brouter.log` ansehen; Java 17+ installiert? (Windows: BRouter-Fenster) |
| Port 8000 belegt | Läuft Routify schon? Sonst das andere Programm beenden |
| Seite reagiert nicht | Neu laden mit `Cmd+Shift+R` / `Ctrl+F5` |
| Antwort dauert lange | Erste Anfrage lädt das LLM (~10 s); sehr lange Strecken brauchen 20–30 s |
| Laptop zu langsam | In `.env`: `LLM_MODEL=qwen3:4b` (vorher `ollama pull qwen3:4b`) |

---

## Projektstruktur

```
start.sh / start.bat            prüfen + starten (ein Befehl)
requirements.txt                Python-Pakete
.env.example                    Vorlage für eigene Einstellungen (.env, optional)
code/
├── backend/                    FastAPI-Backend (app.py = Endpunkte, planung.py = Ablauf einer Planung, …)
├── frontend/                   index.html, teilen.html, static/ (CSS, JavaScript, lokale Bibliotheken), assets/ (Banner, Symbol)
├── ontologie/routify.ttl       Routify-Ontologie (an GDI-DE-Radnetz-Ontologie ausgerichtet)
├── scripts/                    pruefen.py, start_brouter.*, https_tor.py, prepare_data.py
└── tests/                      API- und LLM-Testreihe
docs/                           Architektur, Datenquellen + Lizenzen, Ablaufdiagramm (draw.io, PNG, PDF)
data/                           aufbereitete Daten; große Dateien werden beim ersten Start geladen (siehe data/README.md)
```

---

## Lizenz

Code: [MIT](LICENSE). Die Daten haben eigene Lizenzen (OpenStreetMap: ODbL, BKG POI-Open: dl-de/by-2.0, Wikidata: CC0),
siehe [data/README.md](data/README.md). Mitgelieferte Bibliotheken: [code/frontend/static/vendor/README.md](code/frontend/static/vendor/README.md).

## MCP für KI-Assistenten

Der MCP-Dienst nutzt dieselbe Planungslogik und denselben BRouter wie die Web-App.
Strukturierte Tool-Aufrufe überspringen die Ollama-Auswertung. Der Dienst läuft
als separater Prozess in der vorhandenen Backend-Umgebung.

```sh
.venv/bin/python -m pip install -r requirements-mcp.txt
.venv/bin/python -m uvicorn mcp_server:app --app-dir code/backend --host 127.0.0.1 --port 8103
```

Den MCP-Client per Streamable HTTP mit `http://127.0.0.1:8103/mcp` verbinden.
Für DEV kann der Reverse Proxy den exakten Pfad `/mcp` an diesen Prozess
weiterleiten. Bei weitergereichtem öffentlichem Host-Header
`ROUTIFY_MCP_ALLOWED_HOSTS=routify-dev.hack4gdi.de` setzen. Bei Browser-Clients
mit Origin-Header müssen die erlaubten Origins in
`ROUTIFY_MCP_ALLOWED_ORIGINS` stehen (durch Leerzeichen getrennt).

Aufruf des Tools `fahrradroute_berechnen` mit Beispielparametern:

```json
{
  "start": "Mainz",
  "ziel": "Bingen am Rhein",
  "profil": "trekking",
  "poi_kategorien": ["fahrradladen", "bahnhof"],
  "rdf": true
}
```

Das Ergebnis enthält Distanz, geschätzte Fahrzeit, GeoJSON-Geometrie und
gewünschte POI-Vorschläge mit ihrem Abstand zur Route. Mit `rdf: true` kommt
die Tour zusätzlich als Turtle zurück. Die bestehende Routify-Ontologie
inklusive SKOS-Kategorien ist über `resources/read` mit der URI
`https://w3id.org/routify/ontologie` abrufbar.
