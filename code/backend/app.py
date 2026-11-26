"""Routify – Backend (FastAPI): Schnittstellen.

Die Fachlogik liegt in eigenen Modulen:
  konfig     Pfade, .env, Dienst-Adressen
  daten      aufbereitete Geodaten + Indizes          geo        Distanzen, Gitter-Index
  verstehen  LLM-Parameter + Textregeln                ortssuche  Ortsname -> Koordinate
  routing    BRouter, Varianten, Radwege, Rundtouren   analyse    Etappen, Höhen, Wege, POIs
  plausibel  Rückfragen                                wetter     DWD/Bright Sky, bester Tag
  planung    Ablauf einer Planung                      linked_data Ontologie, RDF, SPARQL
  konto      Konten, Touren, Verlauf                   hoehen     Höhenprofil aus BRouter-Höhen
  radwege    benannte Radfernwege (OSM)

Start:  .venv/bin/uvicorn app:app --app-dir code/backend --reload --port 8000
"""
import asyncio
import os
import sys
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parent))
# konfig zuerst: lädt .env, bevor andere Module Einstellungen lesen
from konfig import BROUTER_URL, FRONTEND, LLM_MODEL, OLLAMA_URL  # noqa: E402
import konto  # noqa: E402
from daten import BAHNHOEFE, POIS  # noqa: E402
from planung import PlanRequest, planen  # noqa: E402
import linked_data  # noqa: E402
import pruefung  # noqa: E402
import sprache  # noqa: E402
import strassenfotos  # noqa: E402

app = FastAPI(title="Routify", docs_url=None, redoc_url=None, openapi_url=None)  # keine offene API-Beschreibung im Netz


@app.exception_handler(Exception)
async def unerwarteter_fehler(request: Request, exc: Exception):
    """Nie eine rohe Fehlerseite: das Frontend bekommt immer eine verständliche Meldung."""
    import traceback
    traceback.print_exc()
    return JSONResponse({"fehler": "Da ist intern etwas schiefgelaufen. Bitte nochmal versuchen – "
                                   f"falls es bleibt, steht der Grund im Backend-Log ({type(exc).__name__})."}, 500)


@app.post("/api/plan")
async def plan(req: PlanRequest, request: Request):
    return await planen(req, request)


@app.on_event("startup")
async def llm_vorwaermen():
    """Lädt das LLM beim Start in den Speicher, damit die erste Anfrage nicht ~8 s länger dauert."""
    async def _warm():
        try:
            async with httpx.AsyncClient() as client:
                await client.post(f"{OLLAMA_URL}/api/generate",
                                  json={"model": LLM_MODEL, "prompt": "", "keep_alive": "2h"}, timeout=120)
        except Exception:
            pass
    asyncio.create_task(_warm())
    # Whisper-Modell im Hintergrund laden (ca. 0,5 s), damit die erste Spracheingabe sofort geht
    asyncio.get_running_loop().run_in_executor(None, _whisper_laden)


def _whisper_laden():
    try:
        sprache.modell()
    except Exception as e:
        print("Spracheingabe nicht verfügbar:", e)


@app.post("/api/sprache")
async def sprache_erkennen(request: Request):
    """Audio (webm/ogg/mp4/wav) -> Text, lokal mit Whisper. Nichts verlässt den Rechner."""
    daten = await request.body()
    if not daten:
        return JSONResponse({"fehler": "Keine Aufnahme erhalten."}, 400)
    if len(daten) > 25_000_000:
        return JSONResponse({"fehler": "Aufnahme zu lang."}, 413)
    typ = request.headers.get("content-type", "")
    endung = ".mp4" if "mp4" in typ else ".ogg" if "ogg" in typ else ".wav" if "wav" in typ else ".webm"
    try:
        return await asyncio.get_running_loop().run_in_executor(None, sprache.transkribieren, daten, endung)
    except Exception as e:
        return JSONResponse({"fehler": f"Spracherkennung nicht verfügbar: {type(e).__name__}"}, 503)


@app.get("/api/config")
async def config():
    # Nur Keys, die ohnehin im Browser landen (Kartenkacheln). Nie LLM-Keys o. Ä. hier ausgeben.
    return {"thunderforest_key": os.getenv("THUNDERFOREST_KEY") or None,
            "turnstile_site_key": TURNSTILE_SITE_KEY or None,
            "strassenfotos": strassenfotos.aktiv()}  # Mapillary-Token bleibt auf dem Server


def _ich(request):
    return konto.nutzer(request.cookies.get(konto.COOKIE))


def _mit_cookie(antwort, token):
    r = JSONResponse(antwort)
    r.set_cookie(konto.COOKIE, token, max_age=konto.SITZUNG_SEKUNDEN, httponly=True, samesite="lax")
    return r


class KontoDaten(BaseModel):
    name: str
    passwort: str
    captcha_token: str = Field(default="", max_length=2048)


TURNSTILE_SITE_KEY = os.getenv("TURNSTILE_SITE_KEY", "").strip()
TURNSTILE_SECRET_KEY = os.getenv("TURNSTILE_SECRET_KEY", "").strip()
TURNSTILE_HOSTNAME = os.getenv("TURNSTILE_HOSTNAME", "").strip()


async def konto_captcha(token: str):
    if not any((TURNSTILE_SITE_KEY, TURNSTILE_SECRET_KEY, TURNSTILE_HOSTNAME)):
        return None
    nicht_verfuegbar = {"fehler": "Die Sicherheitsprüfung ist derzeit nicht verfügbar."}
    if not all((TURNSTILE_SITE_KEY, TURNSTILE_SECRET_KEY, TURNSTILE_HOSTNAME)):
        return JSONResponse(nicht_verfuegbar, 503)
    if token:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                antwort = await client.post(
                    "https://challenges.cloudflare.com/turnstile/v0/siteverify",
                    data={"secret": TURNSTILE_SECRET_KEY, "response": token},
                )
                antwort.raise_for_status()
                ergebnis = antwort.json()
        except (httpx.HTTPError, ValueError):
            return JSONResponse(nicht_verfuegbar, 503)
        if (ergebnis.get("success") is True
                and ergebnis.get("hostname") == TURNSTILE_HOSTNAME
                and ergebnis.get("action") == "konto"):
            return None
    return JSONResponse({"fehler": "Bitte wiederhole die Sicherheitsprüfung."}, 400)


@app.post("/api/konto/registrieren")
async def konto_registrieren(d: KontoDaten):
    if fehler := await konto_captcha(d.captcha_token):
        return fehler
    try:
        token = konto.registrieren(d.name, d.passwort)
    except ValueError as e:
        return JSONResponse({"fehler": str(e)}, 400)
    return _mit_cookie({"name": d.name.strip(), "voreinst": {}}, token)


@app.post("/api/konto/anmelden")
async def konto_anmelden(d: KontoDaten):
    if fehler := await konto_captcha(d.captcha_token):
        return fehler
    try:
        token = konto.anmelden(d.name, d.passwort)
    except ValueError as e:
        return JSONResponse({"fehler": str(e)}, 401)
    ich = konto.nutzer(token)
    return _mit_cookie({"name": ich["name"], "voreinst": ich["voreinst"]}, token)


@app.post("/api/konto/abmelden")
async def konto_abmelden(request: Request):
    konto.abmelden(request.cookies.get(konto.COOKIE))
    r = JSONResponse({"ok": True})
    r.delete_cookie(konto.COOKIE)
    return r


@app.get("/api/konto")
async def konto_info(request: Request):
    ich = _ich(request)
    return {"angemeldet": bool(ich), **({"name": ich["name"], "voreinst": ich["voreinst"]} if ich else {})}


@app.put("/api/konto/voreinstellungen")
async def konto_voreinst(werte: dict, request: Request):
    ich = _ich(request)
    if not ich:
        return JSONResponse({"fehler": "Nicht angemeldet."}, 401)
    return {"voreinst": konto.voreinstellungen_speichern(ich["id"], werte)}


class TourDaten(BaseModel):
    name: str
    params: dict
    variante: int | None = None
    kurz: dict | None = None
    notiz: str = ""


@app.get("/api/touren")
async def touren_liste(request: Request):
    ich = _ich(request)
    if not ich:
        return JSONResponse({"fehler": "Nicht angemeldet."}, 401)
    return {"touren": konto.touren(ich["id"])}


@app.post("/api/touren")
async def tour_neu(d: TourDaten, request: Request):
    ich = _ich(request)
    if not ich:
        return JSONResponse({"fehler": "Nicht angemeldet."}, 401)
    return {"id": konto.tour_speichern(ich["id"], d.name, d.params, d.variante, d.kurz, d.notiz)}


@app.patch("/api/touren/{tour_id}")
async def tour_aendern(tour_id: int, werte: dict, request: Request):
    ich = _ich(request)
    if not ich:
        return JSONResponse({"fehler": "Nicht angemeldet."}, 401)
    return {"ok": konto.tour_aendern(ich["id"], tour_id, werte.get("name"), werte.get("notiz"))}


@app.delete("/api/touren/{tour_id}")
async def tour_loeschen(tour_id: int, request: Request):
    ich = _ich(request)
    if not ich:
        return JSONResponse({"fehler": "Nicht angemeldet."}, 401)
    return {"ok": konto.tour_loeschen(ich["id"], tour_id)}


@app.get("/api/verlauf")
async def verlauf_liste(request: Request):
    ich = _ich(request)
    if not ich:
        return JSONResponse({"fehler": "Nicht angemeldet."}, 401)
    return {"verlauf": konto.verlauf(ich["id"])}


# ---------------------------------------------------------------- Linked Data (Ontologie, RDF, SPARQL)

class RdfAnfrage(BaseModel):
    plan: dict
    variante: int = 0
    format: str = "turtle"
    abfrage: str | None = None
    anfrage_text: str | None = None


@app.post("/api/rdf")
async def rdf_export(d: RdfAnfrage):
    """Planung als RDF (Turtle oder JSON-LD) nach der Routify-Ontologie."""
    g = linked_data.plan_graph(d.plan, d.variante, d.anfrage_text)
    return {"daten": linked_data.serialisieren(g, d.format), "tripel": len(g), "format": d.format}


@app.get("/api/sparql/abfragen")
async def sparql_abfragen():
    return {"abfragen": linked_data.abfragen_liste()}


@app.post("/api/sparql")
async def sparql_abfrage(d: RdfAnfrage):
    """SPARQL-SELECT auf dem Graphen der aktuellen Planung (vordefiniert oder eigene Abfrage)."""
    g = linked_data.plan_graph(d.plan, d.variante, d.anfrage_text)
    try:
        return {**linked_data.sparql(g, d.abfrage or "etappen"), "tripel": len(g)}
    except ValueError as e:
        return JSONResponse({"fehler": str(e)}, 400)
    except Exception as e:  # Syntaxfehler in eigener Abfrage
        return JSONResponse({"fehler": f"SPARQL-Fehler: {e}"}, 400)


@app.post("/api/pruefen")
async def tour_pruefen(d: RdfAnfrage):
    """SHACL-Prüfung: Wünsche aus der Anfrage als Regeln gegen den RDF-Graphen der Tour."""
    return await asyncio.get_running_loop().run_in_executor(None, pruefung.pruefen, d.plan, d.variante, d.anfrage_text)


class FotoAnfrage(BaseModel):
    geometrie: list            # [[lat, lon], …] der gewählten Variante
    von_km: float = 0
    bis_km: float | None = None
    anzahl: int = 18


@app.post("/api/fotos")
async def fotos(d: FotoAnfrage):
    """Straßenfotos (Mapillary) auf dem Track – nur auf Anforderung (Karte „Straßenfotos“ aufgeklappt)."""
    if not strassenfotos.aktiv():
        return JSONResponse({"fehler": "Straßenfotos nicht eingerichtet (MAPILLARY_TOKEN fehlt)."}, 503)
    try:
        return await strassenfotos.entlang(d.geometrie, d.von_km, d.bis_km, max(1, min(d.anzahl, 40)))
    except Exception as e:  # Dienst nicht erreichbar o. Ä. – Planung bleibt davon unberührt
        return JSONResponse({"fehler": f"Straßenfotos nicht verfügbar: {type(e).__name__}"}, 503)


@app.get("/api/foto/{bild_id}")
async def foto(bild_id: str, gross: int = 0):
    """Ein Foto als JPEG, über den Server geladen (der Browser kontaktiert Mapillary/Meta nicht)."""
    if not bild_id.isdigit():
        return JSONResponse({"fehler": "ungültige ID"}, 400)
    try:
        daten_bild = await strassenfotos.bild(bild_id, bool(gross))
    except Exception as e:
        print("Foto", bild_id, e)
        return JSONResponse({"fehler": "Foto nicht verfügbar."}, 404)
    return Response(daten_bild, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})


@app.get("/ontologie/routify.ttl")
async def ontologie():
    return FileResponse(linked_data.ONTO_DATEI, media_type="text/turtle")


@app.get("/api/health")
async def health():
    status = {"pois": len(POIS), "bahnhoefe": len(BAHNHOEFE), "sprache": sprache.status()}
    async with httpx.AsyncClient() as client:
        for name, url in (("ollama", f"{OLLAMA_URL}/api/tags"), ("brouter", f"{BROUTER_URL}/brouter")):
            try:
                await client.get(url, timeout=3)
                status[name] = "ok"
            except Exception:
                status[name] = "nicht erreichbar"
    return status


@app.get("/")
async def index():
    return FileResponse(FRONTEND / "index.html")


@app.get("/teilen")
async def teilen_seite():
    """Seite mit QR-Code, damit andere im selben Netz Routify auf dem Handy öffnen können."""
    return FileResponse(FRONTEND / "teilen.html")


def _netz_adresse():
    """IP-Adresse dieses Rechners im lokalen Netz (UDP-„Verbindung“ sendet nichts, liefert nur die Absenderadresse)."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


@app.get("/api/teilen")
async def teilen(request: Request):
    """Adresse für den QR-Code und ob der Server von anderen Geräten aus erreichbar ist."""
    import socket
    ip = _netz_adresse()
    if not ip:
        return {"url": None, "erreichbar": False, "https": False}

    def offen(port):
        try:
            with socket.create_connection((ip, port), timeout=1):
                return True
        except OSError:
            return False
    if offen(8443):  # HTTPS-Tor (code/scripts/https_tor.py) läuft: damit geht auch das Mikrofon auf dem Handy
        return {"url": f"https://{ip}:8443/", "erreichbar": True, "https": True}
    port = 8000
    return {"url": f"http://{ip}:{port}/", "erreichbar": offen(port), "https": False}


# Logo, App-Symbol und weitere Bilder: code/frontend/assets/ -> http://localhost:8000/assets/…
(FRONTEND / "assets").mkdir(exist_ok=True)
app.mount("/assets", StaticFiles(directory=FRONTEND / "assets"), name="assets")
# Gestaltung und Skripte der Oberfläche: code/frontend/static/{css,js}
app.mount("/static", StaticFiles(directory=FRONTEND / "static"), name="static")
