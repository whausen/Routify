"""Anfrage verstehen: LLM-Parameterextraktion mit festem Schema + Regeln, die den Originaltext prüfen.

Prinzip: Das LLM schlägt vor, der Text entscheidet (Tage, Datum, Rad, Unterkunft, Stopps, Radweg, Wetter …)."""
from datetime import date, timedelta
import json
import radwege
import re

POI_KATEGORIEN = {"fahrradladen": "Fahrradwerkstatt", "verleih": "Fahrradverleih"}
from konfig import LLM_MODEL, OLLAMA_URL


PARAM_SCHEMA = {
    "type": "object",
    "properties": {
        "start": {"type": ["string", "null"]},
        "ziel": {"type": ["string", "null"]},
        "zwischenziele": {"type": "array", "items": {"type": "string"}},
        "tage": {"type": ["integer", "null"]},
        "km_pro_tag": {"type": ["number", "null"]},
        "zielgruppe": {"type": "string", "enum": ["familie", "normal", "sportlich", "senioren"]},
        "fahrrad": {"type": "string", "enum": ["normal", "gravel", "rennrad", "mtb", "ebike"]},
        "startdatum": {"type": ["string", "null"]},
        "unterkunft_typ": {"type": "array", "items": {"type": "string",
                                                      "enum": ["hotel", "guest_house", "hostel", "camp_site"]}},
        "wuensche": {"type": "array", "items": {"type": "string"}},
        "bahnhoefe_zeigen": {"type": "boolean"},
        "poi_wuensche": {"type": "array", "items": {"type": "object", "properties": {
            "kategorie": {"type": "string", "enum": list(POI_KATEGORIEN)},
            "name": {"type": ["string", "null"]},
        }, "required": ["kategorie", "name"]}},
    },
    "required": ["start", "ziel", "zwischenziele", "tage", "km_pro_tag", "zielgruppe",
                 "fahrrad", "startdatum", "unterkunft_typ", "wuensche", "poi_wuensche",
                 "bahnhoefe_zeigen"],
}


def system_prompt():
    heute = date.today()
    return (
        "Du extrahierst Parameter einer Radreise-Anfrage als JSON. Erfinde nichts: "
        "Was nicht ausdrücklich genannt ist, ist null bzw. leere Liste.\n"
        "- start/ziel: Ortsnamen in Deutschland. Bei benannten Radwegen (z. B. Elberadweg) "
        "nur setzen, wenn Orte genannt sind.\n"
        "- tage: NUR die Anzahl Reisetage, wenn sie ausdrücklich genannt ist (\"3 Tage\", \"Wochenende\" = 2). "
        "\"100 km am Tag\" ist KEINE Tagesanzahl, sondern km_pro_tag. Sonst null.\n"
        "- km_pro_tag: Kilometer pro Tag, sonst null.\n"
        "- zielgruppe: 'familie' bei Kindern, 'sportlich' bei sportlich/schnell, 'senioren' bei Senioren, sonst 'normal'.\n"
        "- fahrrad: 'gravel' bei Gravelbike, 'rennrad' bei Rennrad, 'mtb' bei Mountainbike, 'ebike' bei E-Bike/Pedelec, sonst 'normal'.\n"
        "- startdatum: YYYY-MM-DD nur wenn ein Datum/Wochentag genannt ist, sonst null. "
        f"Heute ist {heute.isoformat()} ({heute.strftime('%A')}).\n"
        "- unterkunft_typ: Werte aus hotel, guest_house, hostel, camp_site.\n"
        "- bahnhoefe_zeigen: true NUR wenn nach Bahnhöfen/Ausstiegsmöglichkeiten gefragt wird "
        "(z. B. \"falls ich nicht mehr kann\", \"mit dem Zug zurück\"), sonst false.\n"
        "- poi_wuensche: Stopps unterwegs (fahrradladen = Werkstatt/Fahrradladen, verleih = Fahrradverleih). "
        "name NUR setzen, wenn ein konkreter Eigenname genannt ist (z. B. \"Radhaus Müller\"), "
        "sonst null (\"eine Fahrradwerkstatt\", \"irgendein Verleih\").\n"
        "Beispiel: \"Berlin nach Leipzig, 80 km am Tag mit dem Rennrad\" -> "
        "tage null, km_pro_tag 80, fahrrad rennrad."
    )


ZAHLWORTE = {"einem": 1, "einen": 1, "eine": 1, "ein": 1, "zwei": 2, "drei": 3, "vier": 4, "fünf": 5, "sechs": 6,
             "sieben": 7, "acht": 8, "neun": 9, "zehn": 10, "elf": 11, "zwölf": 12, "vierzehn": 14}


def tage_im_text(text):
    """Erkennt ausdrücklich genannte Reisetage."""
    t = text.lower()

    adjektive = {
        "eintägig": 1,
        "zweitägig": 2,
        "dreitägig": 3,
        "viertägig": 4,
        "fünftägig": 5,
        "sechstägig": 6,
        "siebentägig": 7,
    }

    for wort, tage in adjektive.items():
        if re.search(r"\b" + wort + r"\w*", t):
            return tage

    zahl = r"(\d+|" + "|".join(ZAHLWORTE) + r")"
    wert = lambda w: int(w) if w.isdigit() else ZAHLWORTE[w]

    t = ZEITRAUM.sub(" ", t)

    m = re.search(
        zahl + r"\s*-?\s*(tage|tagen|tagestour|nächte|nächten|übernachtungen)\b",
        t
    )
    if not m:
        m = re.search(r"\b" + zahl + r"\s+(tag)\b", t)

    if m:
        n = wert(m.group(1))
        return n + 1 if m.group(2).startswith(("nächte", "übernachtung")) else n

    m = re.search(zahl + r"\s+wochen?\b", t)
    if m:
        return 7 * wert(m.group(1))

    if "wochenende" in t:
        return 2

    return None

POI_WOERTER = {
    "fahrradladen": ("fahrradwerkstatt", "fahrradladen", "radladen", "werkstatt", "fahrradreparatur", "reparatur",
                     "fahrradgeschäft", "ersatzschlauch", "panne"),
    "verleih": ("fahrradverleih", "rad leihen", "fahrrad leihen", "e-bike leihen", "radverleih", "mieten"),
}

# Vorlieben (werden in BRouter-Parameter übersetzt und bestimmen die Empfehlung unter den Varianten)
VORLIEBEN = {
    "flach": ("flach", "wenig steigung", "wenig höhenmeter", "keine berge", "ohne berge", "nicht bergig",
              "nicht zu bergig", "nicht so bergig", "eben", "bergarm", "ohne steigung", "wenig anstieg", "keine hügel"),
    "ruhig": ("wenig verkehr", "verkehrsarm", "ruhige", "ruhig", "keine hauptstraße", "ohne hauptstraße", "sicher",
              "abseits der straße", "autofrei", "ohne autos", "keine autos", "nicht an der straße"),
    "leise": ("leise", "ohne lärm", "wenig lärm"),
    "wasser": ("am wasser", "am fluss", "entlang des flusses", "am see", "an seen", "am ufer", "flussnah", "an der küste"),
    "wald": ("durch den wald", "im wald", "im grünen", "durch die natur", "in der natur", "schattig", "durch parks"),
    "land": ("städte meiden", "ländlich", "über land", "abseits der städte", "ohne städte", "nicht durch städte"),
    "radrouten": ("ausgeschilderte", "beschilderte", "auf radrouten", "radrouten folgen", "nur radwege"),
    "asphalt": ("asphalt", "glatter untergrund", "kein schotter", "ohne schotter", "befestigte wege", "keine feldwege"),
    "kurz": ("kürzeste", "schnellste", "möglichst kurz", "möglichst direkt", "auf direktem weg"),
    "keine_treppen": ("anhänger", "kinderanhänger", "kinderwagen", "lastenrad", "keine treppen", "ohne treppen"),
    "keine_faehre": ("ohne fähre", "keine fähre", "keine fähren"),
}


def vorlieben_im_text(t, bisher=()):
    """Vorlieben aus dem Text; "egal"/"ohne Vorlieben" setzt zurück. Bisherige bleiben bei Änderungen erhalten."""
    if re.search(r"\b(vorlieben|präferenzen) (zurücksetzen|löschen)|ist mir egal", t):
        return []
    neu = set(bisher or [])
    for k, woerter in VORLIEBEN.items():
        if any(w in t for w in woerter):
            neu.add(k)
    if "ohne fähre" in t or "keine fähre" in t:
        neu.add("keine_faehre")
    return sorted(neu)


def _staemme(text):
    """Wortstämme (erste 5 Buchstaben) – robust gegen Beugung: "Deutschen Museum" ~ "Deutsches Museum"."""
    return [w[:5] for w in re.split(r"\W+", text.lower()) if len(w) > 2]


def _stamm_im_text(name, t):
    return all(st in t for st in _staemme(name))


def poi_wuensche_pruefen(wuensche, t):
    """LLM-Vorschlag gegen den Text prüfen: Kategorie muss im Text vorkommen, Name muss wörtlich im Text stehen."""
    erkannt = {k for k, woerter in POI_WOERTER.items() if any(w in t for w in woerter)}
    out, gesehen = [], set()
    for w in wuensche:
        k, name = w.get("kategorie"), (w.get("name") or "").strip()
        if k not in erkannt:
            continue
        if not name or not _stamm_im_text(name, t) or name.lower() in POI_WOERTER[k] or len(name) < 4:
            name = None
        if (k, name) not in gesehen:
            gesehen.add((k, name))
            out.append({"kategorie": k, "name": name})
    for k in erkannt - {w["kategorie"] for w in out}:  # LLM hat Kategorie übersehen
        out.append({"kategorie": k, "name": None})
    return out


BAHN_AN = ("bahnhof", "bahnhöfe", "bahnhoefe", "notausstieg", "aussteig", "zug zurück", "mit dem zug",
           "mit der bahn", "bahn zurück", "nicht mehr kann", "nicht schaffe", "zutraue", "abbrechen", "ausstieg")
BAHN_AUS = ("ohne bahnhöfe", "keine bahnhöfe", "bahnhöfe ausblenden", "bahnhöfe weg", "ohne bahnhof", "keine bahnhof")


def bahn_im_text(t, bisher=False):
    """Bahnhöfe nur zeigen, wenn danach gefragt wird; "ohne Bahnhöfe" blendet sie wieder aus."""
    if any(w in t for w in BAHN_AUS):
        return False
    if any(w in t for w in BAHN_AN):
        return True
    return bisher


UNTERKUNFT_WOERTER = {
    "hotel": ("hotel", "motel"),
    "guest_house": ("pension", "gasthof", "gasthaus", "ferienwohnung", "b&b", "bed and breakfast"),
    "hostel": ("jugendherberge", "hostel", "herberge", "djh"),
    "camp_site": ("camping", "zelt", "zeltplatz", "campingplatz"),
}

RUND_WOERTER = ("rundtour", "rundkurs", "rundfahrt", "rundweg", "rundroute", "eine runde", "kleine runde", "große runde",
                "start und ziel gleich", "start gleich ziel", "zurück zum start", "wieder zurück", "im kreis", "schleife",
                "ringtour", "tagestour ab", "tagestrip ab", "ausflug ab")


def rundtour_im_text(text, params):
    """Rundtour erkennen ("60 km Rundkurs ab Oldenburg"). Die km-Angabe ist dann die Gesamtlänge."""
    t = text.lower()
    rund = any(w in t for w in RUND_WOERTER) or (params.get("start") and params.get("ziel")
                                                 and params["start"].strip().lower() == params["ziel"].strip().lower())
    if not rund:
        return {"rundtour": False, "laenge_km": None, "km_pro_tag": None}
    laenge, kpt = None, None
    m = re.search(r"(\d+)\s*(km|kilometer)", t)
    if m:
        zahl = int(m.group(1))
        pro_tag = re.search(r"^\s*(pro|am|je|/)\s*tag", t[m.end():])
        tage = tage_im_text(text)
        laenge = zahl * (tage or 1) if pro_tag else zahl
    laenge = laenge or 50
    if not tage_im_text(text) and laenge <= 150:
        kpt = laenge  # Tagestour: eine Etappe
    return {"rundtour": True, "laenge_km": laenge, "km_pro_tag": kpt}


ZEITRAUM = re.compile(r"\b(?:in\s+den\s+|innerhalb\s+der\s+|in\s+der\s+)?(?:nächsten|kommenden)\s+"
                      r"(\d+|zwei|drei|vier|fünf|sechs|sieben|acht|neun|zehn|vierzehn)?\s*(tage|tagen|woche|wochen)\b")
WETTER_TROCKEN = ("trocken", "kein regen", "ohne regen", "nicht nass", "regenfrei")
WETTER_WIND = ("rückenwind", "wind von hinten", "kein gegenwind", "ohne gegenwind", "wenig gegenwind")
WETTER_GUT = ("gutes wetter", "schönes wetter", "schönem wetter", "gutem wetter", "sonne", "sonnig", "bestes wetter",
              "besten wetter", "beste wetter", "wetter passt", "wetter am besten")


DATUMS_WOERTER = ("heute", "morgen", "übermorgen", "montag", "dienstag", "mittwoch", "donnerstag", "freitag",
                  "samstag", "sonntag", "wochenende", "januar", "februar", "märz", "april", "mai", "juni", "juli",
                  "august", "september", "oktober", "november", "dezember", "nächste woche", "ab dem")


def wetter_suche_im_text(text):
    """Erkennt Anfragen wie "in den nächsten 7 Tagen, wann am besten …, wenn es trocken ist".
    Rückgabe {"tage": N, "kriterium": "trocken"|"gut"} oder None."""
    t = text.lower()
    hat = lambda woerter: any(re.search(rf"\b{re.escape(w)}\b", t) for w in woerter)  # Wortgrenzen: nicht „Sonneberg“
    trocken, gut, wind = hat(WETTER_TROCKEN), hat(WETTER_GUT), hat(WETTER_WIND)
    if not (trocken or gut or wind):
        return None
    m = ZEITRAUM.search(t)
    # "bei gutem Wetter", "an dem Tag mit Rückenwind": ohne festes Datum -> besten Tag der nächsten Woche suchen
    if not m and "wann" not in t and any(re.search(rf"\b{re.escape(w)}\b", t) for w in DATUMS_WOERTER):
        return None
    tage = 7
    if m:
        n = m.group(1)
        einheit = m.group(2)
        n = int(n) if n and n.isdigit() else ZAHLWORTE.get(n, 1) if n else 1
        tage = n * 7 if einheit.startswith("woche") else n
    return {"tage": max(2, min(10, tage)), "kriterium": "rueckenwind" if wind else "trocken" if trocken else "gut"}




def datum_im_text(text, heute=None):
    """Relative Datumsangaben unabhängig vom LLM berechnen."""
    heute = heute or date.today()
    t = text.lower()

    if re.search(r"\bübermorgen\b", t):
        return (heute + timedelta(days=2)).isoformat()

    if re.search(r"\bmorgen\b", t):
        return (heute + timedelta(days=1)).isoformat()

    wochentage = {
        "montag": 0, "dienstag": 1, "mittwoch": 2,
        "donnerstag": 3, "freitag": 4,
        "samstag": 5, "sonntag": 6,
    }

    for name, nummer in wochentage.items():
        muster = r"\b(?:nächsten|kommenden)\s+" + name + r"\b"
        if re.search(muster, t):
            abstand = (nummer - heute.weekday()) % 7
            if abstand == 0:
                abstand = 7
            return (heute + timedelta(days=abstand)).isoformat()

    return None


def zwischenziele_aus_anfrage(text):
    """Zwischenziel zwischen 'über' und 'nach' erkennen."""
    muster = (
        r"\büber\s+([A-ZÄÖÜ][A-Za-zÄÖÜäöüß-]*)"
        r"\s+nach\s+[A-ZÄÖÜ][A-Za-zÄÖÜäöüß-]*"
    )
    m = re.search(muster, text, re.IGNORECASE)
    return [m.group(1)] if m else []


def plausibilisieren(params, text):
    """Korrigiert typische Fehler kleiner LLMs anhand des Originaltexts."""
    t = text.lower()

    datum = datum_im_text(text)
    if datum:
        params["startdatum"] = datum

    zwischenziele = zwischenziele_aus_anfrage(text)
    if zwischenziele:
        params["zwischenziele"] = zwischenziele

    # Fahrradtyp ausschließlich aus ausdrücklicher Angabe ableiten
    radtypen = [
        (r"\b(gravelbike|gravelrad|gravel)\b", "gravel"),
        (r"\b(rennrad|rennräder)\b", "rennrad"),
        (r"\b(mountainbike|mtb)\b", "mtb"),
        (r"\b(e-bike|ebike|pedelec)\b", "ebike"),
    ]

    params["fahrrad"] = "normal"
    for muster, radtyp in radtypen:
        if re.search(muster, t):
            params["fahrrad"] = radtyp
            break

    tage_text = tage_im_text(text)
    if tage_text or params.get("tage"):
        params["tage"] = tage_text  # Text ist maßgeblich (LLM erfindet z. B. Tage aus "100 km am Tag")
    unterkunft = [typ for typ, woerter in UNTERKUNFT_WOERTER.items() if any(w in t for w in woerter)]
    if unterkunft:
        params["unterkunft_typ"] = unterkunft
    if params.get("km_pro_tag") is not None and params["km_pro_tag"] < 5:
        params["km_pro_tag"] = None  # LLM setzt manchmal 0 statt null
    if params.get("startdatum") and not (any(w in t for w in DATUMS_WOERTER) or re.search(r"\d{1,2}\.\d{1,2}\.", t)):
        params["startdatum"] = None
    params["poi_wuensche"] = poi_wuensche_pruefen(params.get("poi_wuensche") or [], t)
    params["bahnhoefe_zeigen"] = bahn_im_text(t)  # Text entscheidet, nicht das LLM
    params["vorlieben"] = vorlieben_im_text(t)
    rund = rundtour_im_text(text, params)
    params["rundtour"] = rund["rundtour"]
    params["laenge_km"] = rund["laenge_km"]
    if rund["rundtour"]:
        params["ziel"] = params.get("start")
        if rund["km_pro_tag"] is not None:
            params["km_pro_tag"] = rund["km_pro_tag"]
    params["wetter_suche"] = wetter_suche_im_text(text)
    if params["wetter_suche"]:
        params["startdatum"] = None  # Datum wird nach dem Wetter gewählt
    rw = radwege.erkennen(text)
    params["radweg"] = rw["name"] if rw else None
    if rw:  # Radweg-Name ist kein Zwischenziel
        params["zwischenziele"] = [z for z in params.get("zwischenziele") or []
                                   if radwege._norm(z) not in radwege._norm(rw["name"]) and "radweg" not in z.lower()]
    # Konkrete POIs nicht zusätzlich als Zwischenort geocodieren
    poi_namen = {(w.get("name") or "").lower() for w in params["poi_wuensche"] if w.get("name")}
    params["zwischenziele"] = [z for z in params.get("zwischenziele") or []
                               if z.lower() not in poi_namen and not any(z.lower() in n or n in z.lower() for n in poi_namen)]
    for wort, rad in (("gravel", "gravel"), ("rennrad", "rennrad"), ("mountainbike", "mtb"), ("mtb", "mtb"),
                      ("e-bike", "ebike"), ("ebike", "ebike"), ("pedelec", "ebike")):
        if wort in t:
            params["fahrrad"] = rad
            break
    return params


KONTEXT_FELDER = ("start", "ziel", "zwischenziele", "tage", "km_pro_tag", "zielgruppe", "fahrrad",
                  "startdatum", "unterkunft_typ", "poi_wuensche", "bahnhoefe_zeigen", "profil", "radweg", "wetter_suche", "vorlieben",
                  "wetter_tag", "rundtour", "laenge_km")
GRUPPEN_WOERTER = ("kind", "familie", "sohn", "tochter", "sportlich", "schnell", "senior", "rentner", "gemütlich",
                   "entspannt", "normal", "allein", "alleine")
RAD_WOERTER = ("gravel", "rennrad", "mountainbike", "mtb", "e-bike", "ebike", "pedelec", "trekkingrad", "normalen rad", "normales rad", "citybike")




def schnell_aendern(text, vorher):
    """Eindeutige Ortsänderungen ohne Ollama bearbeiten."""
    if not vorher:
        return None

    t = text.strip()

    # Eindeutige Zieländerung
    m = re.fullmatch(
        r"doch\s+nach\s+([A-ZÄÖÜ][A-Za-zÄÖÜäöüß-]*)\s+statt\s+"
        r"([A-ZÄÖÜ][A-Za-zÄÖÜäöüß-]*)[.!]?",
        t, re.IGNORECASE
    )
    if m and _gleich(m.group(2), vorher.get("ziel")):
        p = dict(vorher)
        p["ziel"] = m.group(1)
        p["wegpunkte"] = None
        p["via_pois"] = []
        p["uebernachtungen"] = []
        return p

    # Kinder zur bestehenden Tour hinzufügen
    if re.fullmatch(r"wir\s+nehmen\s+die\s+kinder\s+mit[.!]?", t, re.IGNORECASE):
        p = dict(vorher)
        p["zielgruppe"] = "familie"
        return p

    # Neue Tour mit ausdrücklich genanntem Start und Ziel
    m = re.fullmatch(
        r"neue\s+tour\s*:\s*von\s+"
        r"([A-ZÄÖÜ][A-Za-zÄÖÜäöüß-]*)\s+nach\s+"
        r"([A-ZÄÖÜ][A-Za-zÄÖÜäöüß-]*)[.!]?",
        t, re.IGNORECASE
    )
    if m:
        p = {k: None for k in KONTEXT_FELDER}
        p.update(
            start=m.group(1),
            ziel=m.group(2),
            zwischenziele=[],
            zielgruppe="normal",
            fahrrad="normal",
            unterkunft_typ=[],
            poi_wuensche=[],
            bahnhoefe_zeigen=False,
        )
        return plausibilisieren(p, text)

    wort = r"[A-ZÄÖÜ][A-Za-zÄÖÜäöüß-]*"
    ort = rf"{wort}(?:\s+(?:am|an|der|den|im|in|bei|{wort}))*"

    # Mehrdeutige Begriffe niemals als Ortsnamen interpretieren.
    keine_orte = {
        "asphalt", "schotter", "umwege", "verkehr",
        "autobahn", "straßen", "strassen", "berge",
        "steigungen", "regen", "wind", "campingplatz",
        "campingplätze", "pausen"
    }

    def gueltiger_ort(name):
        return name.strip().lower() not in keine_orte

    def vorhanden(name):
        return any(
            _gleich(name, z)
            for z in (vorher.get("zwischenziele") or [])
        )

    # Ziel ändern
    m = re.fullmatch(
        rf"Fahre stattdessen nach ({ort})\.",
        t, re.IGNORECASE
    )
    if m and gueltiger_ort(m.group(1)):
        return aendern(vorher, dict(vorher), t)

    # Start ändern
    m = re.fullmatch(
        rf"Starte lieber in ({ort})\.",
        t, re.IGNORECASE
    )
    if m and gueltiger_ort(m.group(1)):
        return aendern(vorher, dict(vorher), t)

    # Zwischenziel hinzufügen
    m = re.fullmatch(
        rf"Fahre (?:lieber|zusätzlich) über ({ort})\.",
        t, re.IGNORECASE
    )
    if m and gueltiger_ort(m.group(1)):
        return aendern(vorher, dict(vorher), t)

    # Zwischenziel entfernen
    m = re.fullmatch(
        rf"Fahre (?:nicht mehr über|ohne) ({ort})\.",
        t, re.IGNORECASE
    )
    if m and vorhanden(m.group(1)):
        return aendern(vorher, dict(vorher), t)

    # Zwischenziel ersetzen
    m = re.fullmatch(
        rf"Fahre statt über ({ort}) über ({ort})\.",
        t, re.IGNORECASE
    )
    if (
        m
        and vorhanden(m.group(1))
        and gueltiger_ort(m.group(2))
    ):
        return aendern(vorher, dict(vorher), t)

    return None


async def extract_params(client, text, vorher=None):
    """Text -> Parameter. Mit `vorher` (aktuelle Planung) wird die Nachricht als Änderung verstanden."""

    # Eindeutige Folgeanfragen direkt verarbeiten, ohne Ollama
    if vorher:
        schnell = schnell_aendern(text, vorher)
        if schnell is not None:
            return schnell

    messages = [{"role": "system", "content": system_prompt()}]
    if vorher:
        aktuell = {k: vorher.get(k) for k in KONTEXT_FELDER}
        messages.append({"role": "system", "content":
            "Aktuelle Planung: " + json.dumps(aktuell, ensure_ascii=False) + "\n"
            "Die Nachricht ist wahrscheinlich eine ÄNDERUNG dieser Planung (z. B. \"lieber 3 Tage\", "
            "\"doch nach Bonn\", \"mit Campingplatz\"). Gib die vollständige geänderte Planung aus: "
            "Übernimm alle Werte, die nicht geändert werden. Nur wenn die Nachricht eine ganz neue Tour mit "
            "neuem Start UND Ziel beschreibt, beginne neu."})
    messages.append({"role": "user", "content": text})
    body = {
        "model": LLM_MODEL, "stream": False, "think": False, "format": PARAM_SCHEMA,
        "options": {"temperature": 0},
        "keep_alive": "2h",  # Modell im Speicher halten (sonst nach 5 min entladen -> erste Anfrage +8 s)
        "messages": messages,
    }
    r = await client.post(f"{OLLAMA_URL}/api/chat", json=body, timeout=120)
    r.raise_for_status()
    roh = json.loads(r.json()["message"]["content"])
    return aendern(vorher, roh, text) if vorher else plausibilisieren(roh, text)


def _gleich(a, b):
    return (a or "").strip().lower() == (b or "").strip().lower()



def ortsaenderungen_im_text(text):
    """Erkennt einfache Ortsänderungen einschließlich mehrteiliger Ortsnamen."""
    ergebnis = {}

    wort = r"[A-ZÄÖÜ][A-Za-zÄÖÜäöüß-]*"
    ort = rf"{wort}(?:\s+(?:am|an|der|den|im|in|bei|{wort}))*"

    start = re.search(
        r"\b(?:starte|starten|start)\s+(?:lieber\s+)?"
        r"(?:in|ab|von)\s+(" + ort + r")",
        text, re.IGNORECASE
    )
    if start:
        ergebnis["start"] = start.group(1).strip()

    ziel = re.search(
        r"\b(?:fahre|fahren|radle|reise)\s+"
        r"(?:stattdessen\s+|lieber\s+|doch\s+)?"
        r"nach\s+(" + ort + r")",
        text, re.IGNORECASE
    )
    if ziel:
        name = ziel.group(1).strip()

        # Anweisungen nach dem Ortsnamen abschneiden
        name = re.split(
            r"\s+(?:und|aber|mit|ohne|sowie|für)\s+",
            name,
            maxsplit=1,
            flags=re.IGNORECASE
        )[0].strip()

        ergebnis["ziel"] = name

    via = re.search(
        r"\b(?:über|via)\s+(.+?)(?:[.!?]|$)",
        text
    )
    if via:
        teile = re.split(r"\s*(?:,|\bund\b)\s*", via.group(1))
        orte = []
        for teil in teile:
            teil = teil.strip()
            if re.fullmatch(ort, teil):
                orte.append(teil)
        if orte:
            ergebnis["zwischenziele"] = orte

    return ergebnis



def zwischenziele_bearbeiten(text, bisher):
    """Zwischenziele hinzufügen, entfernen oder ersetzen."""
    bisher = list(bisher or [])
    ort = r"[A-ZÄÖÜ][A-Za-zÄÖÜäöüß-]*(?:\s+(?:am|an|der|den|im|in|bei|[A-ZÄÖÜ][A-Za-zÄÖÜäöüß-]*))*"

    # Entfernen: "nicht mehr über Bingen", "ohne Bingen"
    entfernen = re.search(
        r"\b(?:nicht mehr über|ohne)\s+(" + ort + r")",
        text, re.IGNORECASE
    )
    if entfernen:
        name = entfernen.group(1)
        return [z for z in bisher if not _gleich(z, name)]

    # Ersetzen: "statt über Bingen über Wiesbaden"
    ersetzen = re.search(
        r"\bstatt\s+über\s+(" + ort + r")\s+über\s+(" + ort + r")",
        text, re.IGNORECASE
    )
    if ersetzen:
        alt, neu = ersetzen.group(1), ersetzen.group(2)
        return [
            neu if _gleich(z, alt) else z
            for z in bisher
        ]

    # Ergänzen: vorhandene Funktion nutzen
    orte = ortsaenderungen_im_text(text)
    genannt = orte.get("zwischenziele")

    if genannt:
        rest = [
            z for z in bisher
            if not any(_gleich(z, n) for n in genannt)
        ]
        return genannt + rest

    return bisher




def km_pro_tag_im_text(text):
    """Explizite Tagesdistanz unabhängig vom LLM erkennen."""
    muster = [
        r"\b(\d{1,3})\s*(?:km|kilometer)\s*(?:pro|je|am)\s*tag\b",
        r"\b(?:pro|je)\s*tag\s*(\d{1,3})\s*(?:km|kilometer)\b",
    ]
    for m in muster:
        treffer = re.search(m, text, re.IGNORECASE)
        if treffer:
            km = int(treffer.group(1))
            if 5 <= km <= 300:
                return km
    return None


def aendern(vorher, roh, text):
    """Änderung auf die bisherige Planung anwenden. Nur, was der Text belegt, wird geändert (LLM schlägt vor)."""
    t = text.lower()
    neu = plausibilisieren(dict(roh), text)
    # Ganz neue Tour (neuer Start UND neues Ziel): nichts übernehmen
    if neu.get("start") and neu.get("ziel") and not _gleich(neu["start"], vorher.get("start")) \
            and not _gleich(neu["ziel"], vorher.get("ziel")) \
            and _stamm_im_text(neu["start"], t) \
            and _stamm_im_text(neu["ziel"], t) \
            and re.search(r"\b(?:neue[nrs]?\s+(?:tour|route)|(?:tour|route)\s+neu\s+planen)\b", t):
        # LLM hat evtl. Werte aus der alten Planung mitgenommen -> nur behalten, was der Text belegt
        if not any(w in t for ws in UNTERKUNFT_WOERTER.values() for w in ws):
            neu["unterkunft_typ"] = []
        if not any(w in t for w in GRUPPEN_WOERTER):
            neu["zielgruppe"] = "normal"
        if not any(w in t for w in RAD_WOERTER):
            neu["fahrrad"] = "normal"
        if not re.search(r"\d+\s*(km|kilometer)", t):
            neu["km_pro_tag"] = None
        neu["zwischenziele"] = [z for z in neu.get("zwischenziele") or [] if _stamm_im_text(z, t)]
        return neu
    p = {k: vorher.get(k) for k in KONTEXT_FELDER}
    # Bestehende Orte nur bei ausdrücklicher Nennung ändern
    start_neu = neu.get("start")
    ziel_neu = neu.get("ziel")

    p["start"] = (
        start_neu if start_neu and _stamm_im_text(start_neu, t)
        else vorher.get("start")
    )
    p["ziel"] = (
        ziel_neu if ziel_neu and _stamm_im_text(ziel_neu, t)
        else vorher.get("ziel")
    )

    # Ausdrücklich genannte Zwischenorte erkennen
    via = re.search(
        r"\b(?:über|via)\s+([A-ZÄÖÜ][A-Za-zÄÖÜäöüß-]+)",
        text
    )

    zwischenziele = neu.get("zwischenziele") or []
    zwischenziele = [
        z for z in zwischenziele if _stamm_im_text(z, t)
    ]

    if via:
        ort = via.group(1)
        if not any(_gleich(ort, z) for z in zwischenziele):
            zwischenziele.append(ort)

    if zwischenziele or any(w in t for w in ("ohne zwischen", "direkt", "nicht über", "ohne umweg")):
        p["zwischenziele"] = zwischenziele
    if tage_im_text(text):
        p["tage"] = tage_im_text(text)
    if re.search(r"\d+\s*(km|kilometer)", t) and neu.get("km_pro_tag"):
        p["km_pro_tag"] = neu["km_pro_tag"]
        if not tage_im_text(text):
            p["tage"] = None  # neue Tagesdistanz -> Tage neu ausrechnen
    if any(w in t for w in GRUPPEN_WOERTER):
        p["zielgruppe"] = neu.get("zielgruppe") or p["zielgruppe"]
    if any(w in t for w in RAD_WOERTER):
        p["fahrrad"] = neu.get("fahrrad") or p["fahrrad"]
    if any(w in t for ws in UNTERKUNFT_WOERTER.values() for w in ws):
        p["unterkunft_typ"] = neu["unterkunft_typ"]
    if neu.get("rundtour"):
        p["rundtour"], p["laenge_km"] = True, neu.get("laenge_km") or vorher.get("laenge_km")
        p["ziel"] = p["start"]
    elif p.get("rundtour") and re.search(r"\d+\s*(km|kilometer)", t):
        p["laenge_km"] = rundtour_im_text(text, {"start": "x", "ziel": "x"})["laenge_km"]
        p["km_pro_tag"] = p["laenge_km"] if not p.get("tage") else None
    p["wetter_suche"] = neu.get("wetter_suche") or (None if neu.get("startdatum") else vorher.get("wetter_suche"))
    if neu.get("startdatum"):
        p["startdatum"] = neu["startdatum"]
    # Wunsch-Stopps: neue hinzufügen, "ohne …/kein …" entfernt
    wuensche = {w["kategorie"]: w for w in vorher.get("poi_wuensche") or []}
    for w in neu.get("poi_wuensche") or []:
        wuensche[w["kategorie"]] = w
    for k, woerter in POI_WOERTER.items():
        if any(re.search(r"\b(ohne|kein|keine|keinen|nicht)\b[^.,;]{0,25}" + re.escape(w), t) for w in woerter):
            wuensche.pop(k, None)
    p["poi_wuensche"] = list(wuensche.values())
    p["bahnhoefe_zeigen"] = bahn_im_text(t, bool(vorher.get("bahnhoefe_zeigen")))
    p["vorlieben"] = vorlieben_im_text(t, vorher.get("vorlieben") or [])
    if neu.get("radweg"):
        p["radweg"] = neu["radweg"]
    elif re.search(r"\b(ohne|nicht (auf|über) den|kein)\b[^.,;]{0,20}(radweg|route)|direkte?[rn]? (weg|route)", t):
        p["radweg"] = None
    # Gewählte Stopps behalten, solange Start/Ziel gleich bleiben und der Wunsch noch besteht
    if _gleich(p["start"], vorher.get("start")) and _gleich(p["ziel"], vorher.get("ziel")):
        p["via_pois"] = [v for v in vorher.get("via_pois") or [] if v.get("kat") in wuensche]
        p["uebernachtungen"] = vorher.get("uebernachtungen") or []
        if p["zwischenziele"] == (vorher.get("zwischenziele") or []):
            p["wegpunkte"] = vorher.get("wegpunkte")
    for k in ("wuensche",):
        p[k] = neu.get(k) or vorher.get(k) or []
    # Explizite Tagesdistanz hat Vorrang vor der KI-Ausgabe
    km_text = km_pro_tag_im_text(text)
    if km_text is not None:
        p["km_pro_tag"] = km_text

    # Explizite Ortsänderungen haben Vorrang vor der KI-Ausgabe
    orte = ortsaenderungen_im_text(text)

    if "start" in orte:
        p["start"] = orte["start"]

    if "ziel" in orte:
        p["ziel"] = orte["ziel"]

    # Zwischenziele gezielt ergänzen, entfernen oder ersetzen
    p["zwischenziele"] = zwischenziele_bearbeiten(
        text, vorher.get("zwischenziele")
    )

    return p


def voreinstellungen_anwenden(params, text, vor):
    """Profil-Voreinstellungen nur dort einsetzen, wo die Anfrage nichts sagt."""
    if not vor:
        return
    t = text.lower()
    genutzt = []
    if vor.get("zielgruppe") and params.get("zielgruppe") in (None, "normal") and not any(w in t for w in GRUPPEN_WOERTER):
        params["zielgruppe"] = vor["zielgruppe"]; genutzt.append("zielgruppe")
    if vor.get("fahrrad") and params.get("fahrrad") in (None, "normal") and not any(w in t for w in RAD_WOERTER):
        params["fahrrad"] = vor["fahrrad"]; genutzt.append("fahrrad")
    if vor.get("km_pro_tag") and not params.get("km_pro_tag") and not params.get("tage") and not params.get("rundtour"):
        params["km_pro_tag"] = vor["km_pro_tag"]; genutzt.append("km_pro_tag")
    if vor.get("unterkunft_typ") and not params.get("unterkunft_typ"):
        params["unterkunft_typ"] = vor["unterkunft_typ"]; genutzt.append("unterkunft_typ")
    params["voreinst_genutzt"] = genutzt
