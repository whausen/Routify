"""Separater MCP-Dienst für die strukturierte Routify-Planung."""
import json
import os
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlencode

from fastapi.responses import JSONResponse
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel, Field, StringConstraints

import linked_data
from planung import PlanRequest, planen
from routing import PROFILE_ERLAUBT, VORLIEBE_PARAMETER

KARTE_URI = "ui://routify/route-map.html"
KARTE_DATEI = Path(__file__).with_name("mcp_route_map.html")
ONTOLOGIE_URI = "https://w3id.org/routify/ontologie"
POI_BEGRIFFE = {
    "fahrradladen": str(linked_data.RTF.FahrradwerkstattKategorie),
    "verleih": str(linked_data.RTF.FahrradverleihKategorie),
    "bahnhof": str(linked_data.RTF.BahnhofKategorie),
}

Ort = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
Profil = Literal[*PROFILE_ERLAUBT]
Vorliebe = Literal[*VORLIEBE_PARAMETER]
PoiKategorie = Literal[*POI_BEGRIFFE]

mcp = FastMCP(
    "Routify",
    stateless_http=True,
    json_response=True,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[
            "localhost", "localhost:*", "127.0.0.1", "127.0.0.1:*",
            *os.getenv("ROUTIFY_MCP_ALLOWED_HOSTS", "").split(),
        ],
        allowed_origins=os.getenv("ROUTIFY_MCP_ALLOWED_ORIGINS", "").split(),
    ),
)


class Geometrie(BaseModel):
    type: Literal["LineString"] = "LineString"
    coordinates: list[tuple[float, float]]


class Route(BaseModel):
    laenge_km: float
    fahrzeit_h: float
    anstieg_m: int
    profil: str
    profil_optionen: list[str]
    geometrie: Geometrie


class OrtAnRoute(BaseModel):
    name: str
    lon: float
    lat: float
    route_km: float
    dist_km: float
    quelle: str | None = None


class PoiGruppe(BaseModel):
    kategorie: PoiKategorie
    begriff_uri: str
    suchradius_km: float
    orte: list[OrtAnRoute]


class Ergebnis(BaseModel):
    status: Literal["ok", "rueckfrage"]
    ontologie_uri: str = ONTOLOGIE_URI
    route: Route | None = None
    routify_url: str | None = Field(None, description="Direktlink zum Öffnen dieser Planung in Routify. Verwende diese URL für Links zur Route.")
    poi: list[PoiGruppe] = Field(default_factory=list)
    hinweise: list[str] = Field(default_factory=list)
    rueckfrage: str | None = None
    turtle: str | None = None


@mcp.resource(ONTOLOGIE_URI, name="Routify-Ontologie", mime_type="text/turtle")
def ontologie() -> str:
    """Routify-Anwendungsontologie auf Basis der GDI-DE-Radnetz-Ontologie."""
    return linked_data.ONTO_DATEI.read_text(encoding="utf-8")


@mcp.resource(
    KARTE_URI,
    name="Routify-Karte",
    mime_type="text/html;profile=mcp-app",
    meta={
        "ui": {
            "csp": {
                "resourceDomains": [
                    "https://cdn.jsdelivr.net",
                    "https://sgx.geodatenzentrum.de",
                ],
            },
        },
    },
)
def routenkarte() -> str:
    """Interaktive Karte für das Ergebnis der Routenberechnung."""
    return KARTE_DATEI.read_text(encoding="utf-8")


@mcp.tool(meta={
    "ui": {"resourceUri": KARTE_URI},
    "openai/outputTemplate": KARTE_URI,
})
async def fahrradroute_berechnen(
    start: Ort,
    ziel: Ort,
    zwischenziele: Annotated[list[Ort], Field(max_length=5)] = [],
    profil: Profil = "trekking",
    vorlieben: Annotated[list[Vorliebe], Field(max_length=len(VORLIEBE_PARAMETER))] = [],
    poi_kategorien: Annotated[list[PoiKategorie], Field(max_length=len(POI_BEGRIFFE))] = [],
    rdf: bool = False,
) -> Ergebnis:
    """Berechnet eine Fahrradroute mit BRouter aus Ortsnamen und optionalen Zwischenzielen.

    POIs sind Vorschläge entlang der Strecke. fahrradladen umfasst Werkstätten und
    Reparaturstationen, verleih Fahrradverleihe, bahnhof Bahnhöfe mit Zugverkehr.
    Vorlieben greifen je nach BRouter-Profil. profil_optionen nennt die tatsächlich
    an BRouter übergebenen Optionen. Die Fahrzeit ist eine Schätzung ohne Pausen.
    Mit rdf=true kommt die Tour zusätzlich als RDF/Turtle nach der Routify-Ontologie zurück.
    """
    kategorien = list(dict.fromkeys(poi_kategorien))
    params = {
        "start": start,
        "ziel": ziel,
        "zwischenziele": zwischenziele,
        "profil": profil,
        "vorlieben": vorlieben,
        "poi_wuensche": [{"kategorie": k} for k in kategorien if k != "bahnhof"],
        "nur_variante": 0,
    }
    plan = await planen(PlanRequest(text="", params=params))
    if isinstance(plan, JSONResponse):
        raise ValueError(json.loads(plan.body)["fehler"])
    hinweise = plan.get("hinweise", [])
    if "rueckfrage" in plan:
        return Ergebnis(status="rueckfrage", rueckfrage=plan["rueckfrage"], hinweise=hinweise)

    haupt = plan["alternativen"][0]
    kandidaten = {p["kategorie"]: p["kandidaten"] for p in plan["poi_auswahl"]}
    kandidaten["bahnhof"] = haupt["notausstiege"]
    query = urlencode({
        "start": start,
        "ziel": ziel,
        "profil": profil,
        "via": zwischenziele,
        "vorliebe": vorlieben,
        "poi": kategorien,
    }, doseq=True)
    basis = os.getenv("ROUTIFY_PUBLIC_URL", "https://routify-dev.hack4gdi.de").rstrip("/")
    return Ergebnis(
        status="ok",
        routify_url=f"{basis}/?{query}",
        route=Route(
            laenge_km=haupt["laenge_km"],
            fahrzeit_h=haupt["fahrzeit_h"],
            anstieg_m=haupt["anstieg_m"],
            profil=haupt["profil"],
            profil_optionen=haupt["profil_optionen"],
            geometrie=Geometrie(coordinates=[(lon, lat) for lat, lon in haupt["geometrie"]]),
        ),
        poi=[
            PoiGruppe(
                kategorie=k,
                begriff_uri=POI_BEGRIFFE[k],
                suchradius_km=5 if k == "bahnhof" else 2,
                orte=[OrtAnRoute.model_validate(p) for p in kandidaten.get(k, [])],
            )
            for k in kategorien
        ],
        hinweise=hinweise,
        turtle=linked_data.serialisieren(linked_data.plan_graph(plan)) if rdf else None,
    )


app = mcp.streamable_http_app()
