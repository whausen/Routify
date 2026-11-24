"""Kern der Planung: Anfrage -> Parameter -> Orte -> Routen -> Etappen/POIs/Wetter -> Antwort."""
from datetime import date, timedelta
from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import asyncio
import httpx
import konto
import radwege
import time

from analyse import build_stages, fahrzeit_h, hoehen_analyse, notausstiege, poi_kandidaten, poi_nach_name, pois_along, wege_analyse, wegpunkte_ordnen
from daten import POI_ICON, POI_KATEGORIEN
from geo import cumulative_km, downsample, haversine_km, wind_relativ
from ortssuche import geocode
from plausibel import im_routing_gebiet, ort_bei, point_at_km, realismus_check, rueckfrage_ausserhalb, tage_konflikt
from routing import ANZAHL_VARIANTEN, brouter, brouter_legs, choose_profile, lange_strecke_varianten, profil_mit_vorlieben, radweg_variante, rund_varianten
from verstehen import extract_params, voreinstellungen_anwenden
from wetter import besten_tag_finden, weather


class PlanRequest(BaseModel):
    text: str
    vorher: dict | None = None  # aktuelle Planung -> Nachricht wird als Änderung verstanden
    params: dict | None = None  # gesetzt bei Antwort auf eine Rückfrage -> LLM wird übersprungen


async def radweg_orte_ergaenzen(client, params, hinweise):
    """Radfernweg ohne Start/Ziel: Ziel entlang des Radwegs bestimmen (Tage × km/Tag, sonst Ende des Radwegs),
    ohne Start: an einem Ende des Radwegs beginnen. Ortsnamen offline über die Gemeinde des nächsten Bahnhofs."""
    eintrag = radwege.INDEX.get(radwege._norm(params["radweg"])) or radwege.erkennen(params["radweg"])
    if not eintrag:
        return
    try:
        linien = await radwege.geometrie(client, eintrag)
        start = await geocode(client, params["start"]) if params.get("start") else None
    except Exception:
        return
    km = None
    if params.get("tage"):
        km = params["tage"] * (params.get("km_pro_tag") or 50)  # wie bei der Etappenteilung: 50 km/Tag
    elif params.get("laenge_km"):
        km = params["laenge_km"]
    e = radwege.endpunkt(linien, start, km)
    if not e:
        return
    if not params.get("start"):
        params["start"] = ort_bei(*e["start"])
    ziel = ort_bei(*e["ziel"])
    if ziel and _norm_ort(ziel) != _norm_ort(params.get("start")):
        params["ziel"] = ziel
        hinweise.append(f"Ziel aus dem Verlauf des {eintrag['name']} bestimmt: {ziel} "
                        f"(≈ {e['km']:.0f} km {'– so weit reichen deine Tage' if km else 'bis zum Ende des Radwegs'}).")


def _norm_ort(n):
    return (n or "").split(",")[0].strip().lower()


async def planen(req: PlanRequest, request: Request | None = None):
    """Komplette Planung einer Anfrage (wird von POST /api/plan aufgerufen)."""
    ich = konto.nutzer(request.cookies.get(konto.COOKIE)) if request is not None else None
    hinweise = []
    zeiten, _t = {}, [time.perf_counter()]

    def messen(schritt):  # Dauer seit letzter Messung in Sekunden
        jetzt = time.perf_counter()
        zeiten[schritt] = round(zeiten.get(schritt, 0) + jetzt - _t[0], 2)
        _t[0] = jetzt
    async with httpx.AsyncClient() as client:
        if req.params:
            params = req.params
        else:
            try:
                params = await extract_params(client, req.text, req.vorher)
            except Exception as e:
                return JSONResponse({"fehler": f"LLM nicht erreichbar ({e}). Läuft Ollama?"}, 503)
            if ich:
                konto.verlauf_merken(ich["id"], req.text)
                if not req.vorher:  # neue Anfrage: Voreinstellungen füllen, was der Text offen lässt
                    voreinstellungen_anwenden(params, req.text, ich["voreinst"])

        if params.get("rundtour") and params.get("start"):
            params["ziel"] = params["start"]
        if params.get("radweg"):  # „Elberadweg“ ist kein Ort: als Start/Ziel verworfen
            for feld in ("start", "ziel"):
                if params.get(feld) and radwege.erkennen(params[feld]):
                    params[feld] = None
        if params.get("radweg") and not params.get("rundtour") and not (params.get("start") and params.get("ziel")):
            await radweg_orte_ergaenzen(client, params, hinweise)
        if not params.get("start") or not params.get("ziel"):
            return {"params": params, "rueckfrage":
                    "Von wo nach wo soll es gehen? Nenn mir Start und Ziel – oder einen Startort mit "
                    "„Rundtour“ bzw. einen Radfernweg, z. B. „3 Tage auf dem Elberadweg ab Dresden“."}

        names = [params["start"], *params.get("zwischenziele", []), params["ziel"]]
        messen("llm")
        start_pt = await geocode(client, names[0])
        rest = await asyncio.gather(*(geocode(client, n, near=start_pt, bezugsort=names[0]) for n in names[1:])) \
            if start_pt else [None] * (len(names) - 1)
        points = [start_pt, *rest]
        if any(pt and pt.get("offline") for pt in points):
            hinweise.append("Die Online-Ortssuche war nicht erreichbar – Orte stammen aus dem eigenen Verzeichnis "
                            "(Ortsmitte, etwas ungenauer).")
        missing = [n for n, p in zip(names, points) if p is None]
        if missing:
            return {"params": params, "rueckfrage": f"Diese Orte habe ich nicht gefunden: {', '.join(missing)}"}

        for pt in points[1:-1]:
            pt["typ"] = "ort"

        # Wunsch-POIs: konkrete Namen und bereits ausgewählte POIs werden Zwischenstopps
        vias, offene_wuensche = [], []
        for v in params.get("via_pois") or []:
            vias.append({**v, "name": v.get("name") or "Stopp", "label": f"{POI_ICON.get(v.get('kat'), '📍')} {v.get('name')}", "poi": True, "typ": "poi"})
        for w in params.get("poi_wuensche") or []:
            if any(v.get("kat") == w["kategorie"] for v in params.get("via_pois") or []):
                continue  # schon ausgewählt
            if w.get("name"):
                treffer = poi_nach_name(w["kategorie"], w["name"], points[0], points[-1])
                if treffer:
                    vias.append({**treffer, "label": f"{POI_ICON.get(treffer['kat'], '📍')} {treffer['name']}", "poi": True, "typ": "poi"})
                else:
                    hinweise.append(f"„{w['name']}“ habe ich nicht gefunden – zeige stattdessen Vorschläge.")
                    offene_wuensche.append({"kategorie": w["kategorie"], "name": None})
            else:
                offene_wuensche.append(w)
        # Übernachtungen (in der Planung gewählte Unterkünfte) sind feste Etappenenden
        for u in params.get("uebernachtungen") or []:
            vias.append({**u, "name": u.get("name") or "Unterkunft", "label": f"🛏️ {u.get('name') or 'Unterkunft'}", "typ": "nacht"})
        points = wegpunkte_ordnen(points, vias, params.get("wegpunkte"))

        messen("ortssuche")
        aussen = [p for p in points if not im_routing_gebiet(p["lon"], p["lat"])]
        if aussen:
            return rueckfrage_ausserhalb(params, points, aussen)

        # Profil aus Zielgruppe/Rad + Vorlieben aus der Anfrage als BRouter-Parameter ("trekking|uphillcost=80|…")
        profile = profil_mit_vorlieben(choose_profile(params), params.get("vorlieben"))
        # Nach einer Änderung an Wegpunkten nur die gewählte Variante neu rechnen (andere ausgeblendet)
        nur = params.get("nur_variante")
        nur = nur if isinstance(nur, int) and 0 <= nur <= 3 else None
        rechne = (lambda i: nur is None or nur == i)
        radweg_info = None
        if params.get("radweg") and rechne(0):
            radweg_info = await radweg_variante(client, params["radweg"], points, profile, hinweise)
        # Kurze Strecken: Hauptroute + BRouter-Alternative. Lange Strecken: 2 Korridore in parallelen Teilstücken
        # (eine einzelne 700-km-Suche dauert 40–120 s, Teilstücke à ~200 km nur wenige Sekunden).
        luftlinie = sum(haversine_km(a["lon"], a["lat"], b["lon"], b["lat"]) for a, b in zip(points, points[1:]))
        async def nichts():
            return None
        rund_wps = None
        if params.get("rundtour") and len(points) == 2:
            routes, rund_wps, titel = await rund_varianten(client, points[0], params.get("laenge_km") or 50, profile, nur)
            ueber = [[titel[i]] + [v["name"] for v in (rund_wps[i] or [])] for i in range(len(routes))]
        elif params.get("radweg") and (radweg_info or nur == 1):
            # A: auf dem Radweg, B: direkte Route zum Vergleich
            direkt = await ((brouter(client, points, profile, 0) if luftlinie <= 200 else brouter_legs(client, points, profile))
                            if rechne(1) else nichts())
            routes = [radweg_info["route"] if radweg_info else None, direkt]
            ueber = [[radweg_info["name"] if radweg_info else params["radweg"]], ["direkt (ohne Radweg)"]]
        elif luftlinie <= 200:
            routes = await asyncio.gather(*(brouter(client, points, profile, a) if rechne(a) else nichts() for a in range(ANZAHL_VARIANTEN)))
            ueber = [[] for _ in routes]
        else:
            routes, korridore = await lange_strecke_varianten(client, points, profile, luftlinie, nur)
            ueber = [[p["label"] for p in k[1:-1]] if k else [] for k in korridore]
        messen("routing")
        erste = next((r for r in routes if r), None)

        if erste and not params.get("bestaetigt"):
            frage = realismus_check(params, points, erste)
            if frage:
                return frage

        # Rückfrage, wenn Tage x km/Tag nicht zur Routenlänge passt
        if erste and tage_konflikt(params, cumulative_km(erste["coords"])[-1]):
            coords = erste["coords"]
            km = cumulative_km(coords)
            total, tage, kpt = km[-1], params["tage"], params["km_pro_tag"]
            n = max(1, round(total / kpt))
            optionen = [{"label": f"{n} Tage à ca. {round(total / n)} km",
                         "params": {**params, "tage": n, "km_pro_tag": round(total / n), "bestaetigt": True}}]
            if total / tage >= 15:  # "100 Tage à 8 km" ist keine sinnvolle Option
                optionen.append({"label": f"{tage} Tage à ca. {round(total / tage)} km",
                                 "params": {**params, "km_pro_tag": round(total / tage), "bestaetigt": True}})
            if tage * kpt < total:
                lon, lat, *_ = point_at_km(coords, km, tage * kpt)
                ort = ort_bei(lon, lat)
                if ort:
                    optionen.append({
                        "label": f"Kürzer: {tage} Tage à {round(kpt)} km, nur bis {ort}",
                        "params": {**params, "ziel": ort, "bestaetigt": True}})
                vergleich = f"{tage} Tage à {round(kpt)} km sind aber nur {round(tage * kpt)} km."
            else:
                vergleich = (f"{tage} Tage à {round(kpt)} km wären aber {round(tage * kpt)} km – "
                             "mehr als die Strecke.")
            return {
                "params": params,
                "rueckfrage": (f"Die Route {params['start']} → {params['ziel']} ist ca. {round(total)} km lang. "
                               f"{vergleich} Wie möchtest du planen?"),
                "optionen": optionen,
            }

        km_pro_tag = params.get("km_pro_tag")
        tage = params.get("tage")
        start_day = date.today() + timedelta(days=1)
        if params.get("startdatum"):
            try:
                start_day = date.fromisoformat(params["startdatum"])
            except ValueError:
                pass

        _t[0] = time.perf_counter()
        alternativen, seen_len = [], set()
        for i, r in enumerate(routes):
            if not r:
                continue
            coords = r["coords"]
            km = cumulative_km(coords)
            total = km[-1]
            if round(total) in seen_len:  # BRouter liefert manchmal identische Alternativen
                continue
            seen_len.add(round(total))
            kpt = km_pro_tag or (total / tage if tage else 50)
            stages, soll = build_stages(coords, km, kpt, tuple(params.get("unterkunft_typ") or ()),
                                        [p for p in points if p.get("typ") == "nacht"])
            wege = wege_analyse(r["props"].get("messages") or [], stages, coords, km)
            hoehe = hoehen_analyse(coords, km, stages, r["props"].get("messages") or [])
            if hoehe:
                prof, anstieg_m, hoehen_quelle = hoehe["profil"], hoehe["anstieg_m"], hoehe["quelle"]
            else:  # Rückfall, falls BRouter keine Höhen je Punkt liefert
                prof = downsample([[round(k, 2), c[2]] for k, c in zip(km, coords) if len(c) > 2], 600)
                anstieg_m, hoehen_quelle = int(r["props"].get("filtered ascend", 0)), "BRouter (SRTM)"
            ausstiege = notausstiege(coords, km)
            alt_wps = None
            if rund_wps and rund_wps[i]:
                alt_wps = [{k: v for k, v in w.items() if k in ("typ", "name", "label", "lon", "lat", "kat", "detail", "ort",
                                                                  "adresse", "telefon", "web", "quelle", "art", "typen")}
                           for w in rund_wps[i]]
            for st in stages:
                st["notausstiege"] = [a for a in ausstiege if st["von_km"] <= a["route_km"] <= st["bis_km"]]
                st["fahrzeit_h"] = fahrzeit_h(st["laenge_km"], st.get("anstieg_m"), params)
            alternativen.append({
                "notausstiege": ausstiege,
                "wegpunkte": alt_wps,
                "titel": f"Richtung {ueber[i][0]}" if rund_wps else None,
                "idx": i, "profil": profile.split("|")[0], "profil_optionen": profile.split("|")[1:], "ueber": ueber[i],
                "fahrzeit_h": round(sum(st["fahrzeit_h"] for st in stages), 1),
                "laenge_km": round(total, 1),
                "anstieg_m": anstieg_m,
                "max_steigung": hoehe["max_steigung"] if hoehe else None,
                "hoehen_quelle": hoehen_quelle,
                "dauer_h": round(int(r["props"].get("total-time", 0)) / 3600, 1),
                "geometrie": [[c[1], c[0]] for c in downsample(coords, 2500)],
                "hoehenprofil": prof,
                "etappen": stages,
                "km_pro_etappe": round(soll, 1),
                "rad_pois": pois_along(coords, km),
                "wege": wege,
            })
        if not alternativen:
            if luftlinie > 200:
                return JSONResponse({"params": params, "fehler":
                    f"Die Strecke ist mit {round(luftlinie)} km Luftlinie zu lang für den lokalen Router "
                    f"(Profil {profile.split('|')[0]}, Zeitlimit ca. 60 s). Bitte eine kürzere Strecke oder ein Zwischenziel wählen."}, 502)
            return JSONResponse({"params": params, "fehler": "BRouter hat keine Route gefunden. Läuft der Server?"}, 502)

        # Wetter je Etappenende für die erste Alternative (Orte der Alternativen liegen nah beieinander)
        haupt = alternativen[0]

        # Allgemeine Wünsche ("ein Fahrradverleih"): Kandidaten entlang der Hauptroute zur Auswahl
        poi_auswahl = []
        if offene_wuensche:
            h_coords = routes[haupt["idx"]]["coords"]
            h_km = cumulative_km(h_coords)
            for w in offene_wuensche:
                k = w["kategorie"]
                kand = poi_kandidaten(k, h_coords, h_km)
                if kand:
                    poi_auswahl.append({"kategorie": k, "label": POI_KATEGORIEN[k], "icon": POI_ICON[k], "kandidaten": kand})
                else:
                    hinweise.append(f"Kein {POI_KATEGORIEN[k]} im Umkreis von 2 km entlang der Route gefunden.")
        messen("etappen_pois_wege")
        wetter_wahl = None
        if params.get("wetter_suche"):
            wetter_wahl = await besten_tag_finden(client, haupt["etappen"], points[0], params["wetter_suche"])
            if wetter_wahl:
                # Nutzer hat in der Übersicht einen anderen Tag gewählt -> diesen nehmen, sonst den besten
                gewaehlt = params.get("wetter_tag") if any(k["start"] == params.get("wetter_tag")
                                                          for k in wetter_wahl["kandidaten"]) else wetter_wahl["bester"]
                for k in wetter_wahl["kandidaten"]:
                    k["gewaehlt"] = k["start"] == gewaehlt
                start_day = date.fromisoformat(gewaehlt)
                params["startdatum"] = gewaehlt
            else:
                hinweise.append("Für den Zeitraum liegt keine Wettervorhersage vor – Datum nicht nach Wetter gewählt.")
        wetter = await asyncio.gather(*(
            weather(client, s["ende"]["lon"], s["ende"]["lat"], start_day + timedelta(days=s["nr"] - 1))
            for s in haupt["etappen"]
        ))
        for alt in alternativen:
            for s, w in zip(alt["etappen"], wetter):
                s["wetter"] = dict(w)
                if w and not w.get("hinweis") and not params.get("rundtour"):
                    # Wind relativ zur Fahrtrichtung der Etappe (Windrichtung = woher der Wind kommt)
                    gegen, art = wind_relativ(s.get("richtung"), w.get("wind_richtung"), w.get("wind_kmh"))
                    s["wetter"].update({"gegenwind_kmh": gegen, "wind_relativ": art})

        if tage and len(haupt["etappen"]) != tage:
            hinweise.append(
                f"Bei {haupt['laenge_km']} km und ca. {km_pro_tag or 50} km/Tag ergeben sich "
                f"{len(haupt['etappen'])} statt {tage} Tage."
            )
        naechte = [s for s in haupt["etappen"] if not s["letzte"]]
        if naechte and not all(s.get("unterkuenfte") for s in naechte):
            hinweise.append("Für mindestens ein Etappenende wurde keine Unterkunft gefunden.")

        return {
            "params": params,
            "orte": points,
            "startdatum": start_day.isoformat(),
            "alternativen": alternativen,
            "poi_auswahl": poi_auswahl,
            "radweg": {"name": radweg_info["name"], "quelle": radwege.QUELLE} if radweg_info else None,
            "wetter_wahl": wetter_wahl,
            "nur_variante": nur,
            "stopps": [v for v in points if v.get("poi")],
            "wegpunkte": [{k: v for k, v in p.items() if k in ("typ", "name", "label", "lon", "lat", "kat", "detail", "ort",
                                                             "adresse", "telefon", "web", "quelle", "nacht", "art",
                                                             "typen")}
                          for p in points[1:-1]],
            "hinweise": hinweise,
            "zeiten": {**zeiten, "wetter": round(time.perf_counter() - _t[0], 2)},
        }
