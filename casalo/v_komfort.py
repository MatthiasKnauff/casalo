"""Bedienkomfort: Bearbeiten in der Zelle, Detailkarten, Gemeinschaftswahl, Darstellung je Benutzer.

Alle Prüfungen laufen hier auf dem Server – der Browser zeigt nur an, was zurückkommt (htmx).
"""
import datetime
import json
import re

from flask import abort, flash, make_response, redirect, render_template, request, session
from markupsafe import Markup

from . import bank, daten, db, rechnen, telefon
from .web import aktueller_benutzer, bp, f

# ------------------------------------------------------------------ Felder, die in der Zelle bearbeitet werden dürfen
ARTEN_EINHEIT = [("Wohnung", "Wohnung"), ("Gewerbe", "Gewerbe"), ("Stellplatz", "Stellplatz"), ("Sonstiges", "Sonstiges")]
VERSAND = [("email", "E-Mail"), ("post", "Post"), ("portal", "Portal")]
ZAHLWEISE = [("ueberweisung", "Überweisung"), ("lastschrift", "Lastschrift")]
STATUS = [("offen", "offen"), ("erledigt", "erledigt")]
KONTOART = [("hausgeld", "Hausgeld"), ("ruecklage", "Rücklage")]
ZUGANG = [("fints", "FinTS"), ("finapi", "finAPI"), ("datei", "Datei-Import")]
GEWERKE = ["Hausmeister", "Reinigung", "Garten", "Winterdienst", "Sanitär", "Heizung", "Elektro", "Dach", "Maler",
           "Schlüsseldienst", "Aufzug", "Rohrreinigung", "Schornsteinfeger", "Messdienst", "Sonstiges"]
AUFTRAG_STATUS = [("entwurf", "Entwurf"), ("beauftragt", "beauftragt"), ("erledigt", "erledigt"),
                  ("abgerechnet", "abgerechnet"), ("storniert", "storniert")]
STEUER35A = [("", "–"), ("dienstleistung", "Haushaltsnahe Dienstleistung"), ("handwerker", "Handwerkerleistung"),
             ("minijob", "Minijob (geringfügig)")]


def F(name, typ="text", pflicht=False, optionen=None, einheit="", leer=None):
    """leer: gespeicherter Wert, wenn das Feld geleert wird (nur Beträge; sonst NULL)."""
    return {"name": name, "typ": typ, "pflicht": pflicht, "optionen": optionen or [], "einheit": einheit, "leer": leer}


FELDER = {
    "einheit": {"_tabelle": "einheit", "_titel": lambda r: f"Einheit {r['nr']}",
                "nr": F("Nr.", pflicht=True), "lage": F("Lage"), "art": F("Art", "wahl", optionen=ARTEN_EINHEIT),
                "wohnflaeche": F("Wohnfläche", "zahl", einheit="m²"), "mea": F("MEA", "zahl"), "notiz": F("Notiz", "lang")},
    "person": {"_tabelle": "person", "_titel": lambda r: daten.name(dict(r)),
               "anrede": F("Anrede"), "vorname": F("Vorname"), "nachname": F("Nachname"), "firma": F("Firma"),
               "strasse": F("Straße"), "plz": F("PLZ"), "ort": F("Ort"), "email": F("E-Mail", "email"),
               "telefon": F("Telefon"), "telefon2": F("Telefon 2"), "versand": F("Versand", "wahl", optionen=VERSAND),
               "zahlweise": F("Hausgeld", "wahl", optionen=ZAHLWEISE), "iban": F("IBAN", "iban"), "bic": F("BIC"),
               "mandat_ref": F("Mandatsreferenz"), "mandat_datum": F("Mandat vom", "datum"), "notiz": F("Notiz", "lang")},
    "kostenart": {"_tabelle": "kostenart", "_titel": lambda r: r["name"],
                  "name": F("Kostenart", pflicht=True), "schluessel": F("Schlüssel", "wahl", optionen=list(rechnen.SCHLUESSEL.items())),
                  "stichworte": F("Stichworte"), "reihenfolge": F("#", "ganz"),
                  "datev_konto": F("DATEV-Konto"), "steuer35a": F("§ 35a", "wahl", optionen=STEUER35A), "lohnanteil": F("Lohnanteil", "ganz", einheit="%")},
    "konto": {"_tabelle": "konto", "_titel": lambda r: f"{r['bank']} {r['iban']}",
              "art": F("Art", "wahl", optionen=KONTOART), "bank": F("Bank"), "iban": F("IBAN", "iban"), "bic": F("BIC"),
              "zugang": F("Abruf", "wahl", optionen=ZUGANG), "datev_konto": F("DATEV-Konto")},
    "vorgang": {"_tabelle": "vorgang", "_titel": lambda r: r["titel"],
                "titel": F("Titel", pflicht=True), "text": F("Beschreibung", "lang"), "status": F("Status", "wahl", optionen=STATUS)},
    "gemeinschaft": {"_tabelle": "gemeinschaft", "_titel": lambda r: r["name"],
                     "nr": F("Nr."), "name": F("Name", pflicht=True), "strasse": F("Straße"), "plz": F("PLZ"), "ort": F("Ort"),
                     "glaeubiger_id": F("Gläubiger-ID")},
    "dienstleister": {"_tabelle": "dienstleister", "_titel": lambda r: r["firma"],
                      "firma": F("Firma", pflicht=True), "gewerk": F("Gewerk", "wahl", optionen=[(g, g) for g in GEWERKE]),
                      "ansprechpartner": F("Ansprechpartner"), "telefon": F("Telefon"), "notdienst": F("Notdienst"),
                      "email": F("E-Mail", "email"), "strasse": F("Straße"), "plz": F("PLZ"), "ort": F("Ort"),
                      "iban": F("IBAN", "iban"), "stichworte": F("Stichworte"), "notiz": F("Notiz", "lang")},
    "auftrag": {"_tabelle": "auftrag", "_titel": lambda r: r["titel"],
                "titel": F("Leistung", pflicht=True), "text": F("Beschreibung", "lang"), "termin": F("Termin", "datum"),
                "angebot_cent": F("Angebot", "betrag"), "rechnung_nr": F("Rechnungs-Nr."), "rechnung_cent": F("Rechnung", "betrag")},
    "wartung": {"_tabelle": "wartung", "_titel": lambda r: r["titel"],
                "titel": F("Wartung", pflicht=True), "intervall_monate": F("Intervall", "ganz", einheit="Mon."),
                "letzte": F("Zuletzt", "datum"), "naechste": F("Nächste", "datum"), "pflicht": F("Grundlage"),
                "vertrag_bis": F("Vertrag bis", "datum"), "kuendigung_monate": F("Kündigungsfrist", "ganz", einheit="Mon."),
                "kosten_jahr_cent": F("Kosten/Jahr", "betrag", leer=0), "notiz": F("Notiz", "lang")},
    "buchung": {"_tabelle": "buchung", "_titel": lambda r: r["text"] or "Buchung", "text": F("Text"),
                "lohn_cent": F("Arbeitskosten", "betrag")},
}


def _def(art, feld):
    d = FELDER.get(art)
    if not d or feld.startswith("_") or feld not in d:
        abort(404)
    return d[feld]


def _zeile(art, oid):
    r = db.q(f"SELECT * FROM {FELDER[art]['_tabelle']} WHERE id = ?", (oid,), one=True)
    if not r:
        abort(404)
    return r


def anzeige(fd, wert):
    if wert is None or wert == "":
        return ""
    t = fd["typ"]
    if t == "wahl":
        return dict(fd["optionen"]).get(wert, wert)
    if t == "zahl":
        return f"{float(wert):g}".replace(".", ",") + (f" {fd['einheit']}" if fd["einheit"] else "")
    if t == "datum":
        try:
            return datetime.date.fromisoformat(str(wert)).strftime("%d.%m.%Y")
        except ValueError:
            return str(wert)
    if t == "iban":
        return bank.iban_format(wert)
    if t == "betrag":
        return rechnen.fmt(wert)
    if t == "ganz" and fd["einheit"]:
        return f"{wert} {fd['einheit']}"
    return str(wert)


def pruefen(art, feld, fd, roh, zeile):
    """Eingabe prüfen und in den gespeicherten Wert wandeln. Wirft ValueError mit verständlichem Text."""
    roh = (roh or "").strip()
    if fd["pflicht"] and not roh:
        raise ValueError(f"{fd['name']} darf nicht leer sein.")
    t = fd["typ"]
    if t == "zahl":
        if not roh:
            return 0.0
        try:
            w = rechnen.parse_zahl(roh.replace(fd["einheit"], ""))
        except ValueError:
            raise ValueError(f"{fd['name']}: bitte eine Zahl eingeben.")
        if w < 0:
            raise ValueError(f"{fd['name']} darf nicht negativ sein.")
        return w
    if t == "ganz":
        roh = roh.replace(fd["einheit"], "").strip() if fd["einheit"] else roh
        if not roh.lstrip("-").isdigit():
            raise ValueError(f"{fd['name']}: bitte eine ganze Zahl eingeben.")
        if fd["einheit"] == "Mon." and int(roh) < 0:
            raise ValueError(f"{fd['name']} kann nicht negativ sein.")
        if fd["einheit"] == "%" and not 0 <= int(roh) <= 100:
            raise ValueError(f"{fd['name']}: zwischen 0 und 100 %.")
        return int(roh)
    if t == "betrag":
        if not roh:
            return fd["leer"]
        try:
            w = rechnen.parse_betrag(roh.replace("€", ""))
        except ValueError:
            raise ValueError(f"{fd['name']}: Betrag wie 1.234,56 eingeben.")
        if art == "buchung" and feld == "lohn_cent" and w > abs(zeile["betrag_cent"]):
            raise ValueError("Die Arbeitskosten können nicht höher sein als der Rechnungsbetrag.")
        return abs(w)
    if t == "wahl":
        if roh not in dict(fd["optionen"]):
            raise ValueError(f"{fd['name']}: ungültige Auswahl.")
        if art == "person" and feld == "zahlweise" and roh == "lastschrift" and not (zeile["iban"] and zeile["mandat_ref"] and zeile["mandat_datum"]):
            raise ValueError("Für Lastschrift erst IBAN, Mandatsreferenz und Datum des Mandats eintragen.")
        return roh
    if t == "iban":
        w = bank.iban_normal(roh)
        if w and not bank.iban_gueltig(w):
            raise ValueError("Die IBAN ist ungültig.")
        return w
    if t == "email":
        if roh and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", roh):
            raise ValueError("Die E-Mail-Adresse sieht nicht richtig aus.")
        return roh
    if t == "datum":
        if not roh:
            return ""
        for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y"):
            try:
                return datetime.datetime.strptime(roh, fmt).date().isoformat()
            except ValueError:
                pass
        raise ValueError(f"{fd['name']}: Datum als TT.MM.JJJJ eingeben.")
    if art == "person" and feld in ("nachname", "firma") and not roh:
        anderes = zeile["firma"] if feld == "nachname" else zeile["nachname"]
        if not anderes:
            raise ValueError("Nachname oder Firma muss ausgefüllt sein.")
    if art == "einheit" and feld == "nr":
        dop = db.q("SELECT 1 FROM einheit WHERE gemeinschaft_id = ? AND nr = ? AND id != ?",
                   (zeile["gemeinschaft_id"], roh, zeile["id"]), one=True)
        if dop:
            raise ValueError(f"Die Nummer {roh} gibt es in dieser Gemeinschaft schon.")
    return roh[:2000]


def zelle(art, obj, feld, klasse="", modus="anzeige", fehler="", roh=None):
    """<td>, die sich per Klick bearbeiten lässt (Server rendert Anzeige und Eingabe)."""
    fd = _def(art, feld)
    klasse = " ".join(k for k in (klasse or "").split() if k != "zahl")     # „zahl“ setzt der Feldtyp selbst
    oid = obj["id"]
    wert = obj[feld]
    return Markup(render_template("_zelle.html", art=art, oid=oid, feld=feld, fd=fd, wert=wert,
                                  text=anzeige(fd, wert), klasse=klasse + (" zahl" if fd["typ"] in ("zahl", "ganz", "betrag") else ""),
                                  modus=modus, fehler=fehler, roh=wert if roh is None else roh,
                                  sortwert=wert if fd["typ"] in ("zahl", "ganz", "betrag") else None))


@bp.app_template_global("zelle")
def _zelle_global(art, obj, feld, klasse=""):
    return zelle(art, obj, feld, klasse)


@bp.get("/feld/<art>/<int:oid>/<feld>")
def feld_bearbeiten(art, oid, feld):
    _def(art, feld)
    z = _zeile(art, oid)
    return zelle(art, z, feld, request.args.get("klasse", ""),
                 modus="anzeige" if request.args.get("anzeige") else "edit")


@bp.post("/feld/<art>/<int:oid>/<feld>")
def feld_speichern(art, oid, feld):
    fd = _def(art, feld)
    z = _zeile(art, oid)
    roh = request.form.get("wert", "")
    klasse = request.form.get("klasse", "")
    try:
        neu = pruefen(art, feld, fd, roh, z)
    except ValueError as e:
        return zelle(art, z, feld, klasse, modus="edit", fehler=str(e), roh=roh)
    alt = z[feld]
    resp = make_response()
    if neu != alt and not (alt in (None, "") and neu in (None, "")):
        db.ex(f"UPDATE {FELDER[art]['_tabelle']} SET {feld} = ? WHERE id = ?", (neu, oid))
        if not request.form.get("rueck"):
            titel = FELDER[art]["_titel"](z)
            resp.headers["HX-Trigger"] = json.dumps({"casalo-schritt": {
                "text": f"{titel} · {fd['name']}: {anzeige(fd, alt) or '–'} → {anzeige(fd, neu) or '–'}",
                "url": f"/feld/{art}/{oid}/{feld}", "alt": "" if alt is None else str(alt), "neu": str(neu),
                "ziel": f"#z-{art}-{oid}-{feld}"}})   # ASCII: Kopfzeilen vertragen keine Umlaute
    z = _zeile(art, oid)
    resp.set_data(zelle(art, z, feld, klasse))
    return resp


# ------------------------------------------------------------------ Detailkarten (Bereich rechts)
@bp.get("/karte/<art>/<int:oid>")
def karte(art, oid):
    heute = datetime.date.today()
    if art == "einheit":
        e = _zeile("einheit", oid)
        gid = e["gemeinschaft_id"]
        wp = daten.wirtschaftsplan(gid, heute.year)
        offen = daten.offene_hausgelder(gid, heute.year).get(oid)
        ja = daten.jahresabrechnung(gid, heute.year - 1)
        d = ja.einheiten.get(oid) if ja.summe_kosten else None
        return render_template("karten/einheit.html", e=e, g=daten.gemeinschaft(gid),
                               eigentum=db.q("SELECT t.*, p.vorname, p.nachname, p.firma, p.email, p.telefon FROM eigentum t "
                                             "JOIN person p ON p.id = t.person_id WHERE t.einheit_id = ? ORDER BY t.bis IS NOT NULL, t.von DESC", (oid,)),
                               monat=wp["einheiten"][oid]["monat"] if wp["wp"] else None, offen=offen,
                               abrechnung=d, jahr=heute.year,
                               vorgaenge=db.q("SELECT * FROM vorgang WHERE einheit_id = ? ORDER BY angelegt DESC LIMIT 5", (oid,)),
                               buchungen=db.q("SELECT * FROM buchung WHERE einheit_id = ? ORDER BY datum DESC LIMIT 6", (oid,)))
    if art == "person":
        p = _zeile("person", oid)
        return render_template("karten/person.html", p=p,
                               einheiten=db.q("SELECT t.*, e.nr, e.lage, g.name AS g_name, g.id AS gid FROM eigentum t "
                                              "JOIN einheit e ON e.id = t.einheit_id JOIN gemeinschaft g ON g.id = e.gemeinschaft_id "
                                              "WHERE t.person_id = ? ORDER BY t.bis IS NOT NULL, g.name", (oid,)),
                               anrufe=db.q("SELECT * FROM anruf WHERE person_id = ? ORDER BY beginn DESC LIMIT 3", (oid,)),
                               vorgaenge=db.q("SELECT * FROM vorgang WHERE person_id = ? ORDER BY angelegt DESC LIMIT 5", (oid,)))
    if art == "vorgang":
        v = _zeile("vorgang", oid)
        return render_template("karten/vorgang.html", v=v,
                               g=daten.gemeinschaft(v["gemeinschaft_id"]) if v["gemeinschaft_id"] else None,
                               p=db.q("SELECT * FROM person WHERE id = ?", (v["person_id"],), one=True) if v["person_id"] else None,
                               e=db.q("SELECT * FROM einheit WHERE id = ?", (v["einheit_id"],), one=True) if v["einheit_id"] else None,
                               anruf=db.q("SELECT * FROM anruf WHERE vorgang_id = ?", (oid,), one=True))
    if art == "konto":
        k = _zeile("konto", oid)
        return render_template("karten/konto.html", k=k, saldo=daten.konto_saldo(k, heute.isoformat()),
                               umsaetze=db.q("SELECT * FROM umsatz WHERE konto_id = ? ORDER BY datum DESC LIMIT 8", (oid,)))
    if art == "anruf":
        a = db.q("SELECT a.*, p.vorname, p.nachname, p.firma FROM anruf a LEFT JOIN person p ON p.id = a.person_id "
                 "WHERE a.id = ?", (oid,), one=True)
        if not a:
            abort(404)
        return render_template("karten/anruf.html", a=a, verlauf=json.loads(a["verlauf"] or "[]"))
    if art == "kostenart":
        k = _zeile("kostenart", oid)
        return render_template("karten/kostenart.html", k=k, ist=daten.kosten(k["gemeinschaft_id"], heute.year).get(oid, 0),
                               vorjahr=daten.kosten(k["gemeinschaft_id"], heute.year - 1).get(oid, 0))
    if art in KARTEN:
        return KARTEN[art](oid)
    abort(404)


@bp.get("/details")
def details_fenster():
    """Leeres Fenster für die Detailkarten (Bereich rechts als eigenes Browserfenster, z. B. auf dem zweiten Bildschirm)."""
    return render_template("details_fenster.html")


KARTEN = {}          # weitere Detailkarten, von anderen Modulen eingetragen: art → Funktion(oid)


# ------------------------------------------------------------------ Gemeinschaft oben wählen
UNTERSEITEN = ("auftrag", "sonderumlage", "versammlung", "umlauf")     # /<tabelle>/<id> gehört zu einer WEG


@bp.before_app_request
def gemeinschaft_merken():
    """Die zuletzt geöffnete Gemeinschaft bleibt oben gewählt – auch auf Seiten einzelner Aufträge, Umlagen, Versammlungen."""
    m = re.match(r"^/(?:g|assistent)/(\d+)", request.path)
    if m:
        session["gid"] = int(m.group(1))
        return
    m = re.match(r"^/(" + "|".join(UNTERSEITEN) + r")/(\d+)", request.path)
    if m and session.get("uid"):
        r = db.q(f"SELECT gemeinschaft_id FROM {m.group(1)} WHERE id = ?", (int(m.group(2)),), one=True)
        if r:
            session["gid"] = r["gemeinschaft_id"]


def aktuelle_gemeinschaft():
    gid = session.get("gid")
    return daten.gemeinschaft(gid) if gid else None


# ------------------------------------------------------------------ Darstellung je Benutzer
# Akzentfarben wie in der Bauplattform: (Name, Farbe auf dunklem Grund, Farbe auf hellem Grund)
FARBEN = {
    "pink": ("Pink", "#ff5fb3", "#c2185b"),
    "blau": ("Blau", "#6aa7ff", "#2f6fd6"),
    "gruen": ("Grün", "#3ddc84", "#1f9d55"),
    "orange": ("Orange", "#ffad5c", "#c46a10"),
    "violett": ("Violett", "#b18cff", "#7a4fd6"),
    "tuerkis": ("Türkis", "#2fd6c9", "#0f8f8f"),
}
SCHEMATA = {"dunkel": "Dunkel", "hell": "Hell", "auto": "wie das Gerät"}
SCHRIFTEN = {
    "standard": ("Ubuntu", "Ubuntu, 'Segoe UI', system-ui, -apple-system, sans-serif"),
    "klassisch": ("Klassisch", "'Segoe UI', Roboto, Helvetica, Arial, sans-serif"),
    "serifen": ("Mit Serifen", "Georgia, Cambria, 'Times New Roman', serif"),
}
GROESSEN = [13, 14, 15, 16, 17, 18]
DICHTE = {"normal": "Normal", "kompakt": "Kompakt"}
STANDARD = {"schema": "dunkel", "farbe": "pink", "schrift": "standard", "groesse": 15, "dichte": "normal"}


def darstellung(u=None):
    u = u or aktueller_benutzer()
    d = dict(STANDARD)
    if u:
        d.update(json.loads(u["darstellung"]))
    if d["farbe"] not in FARBEN:          # ältere Einstellung (Blau, Petrol …) → nächstliegende Farbe
        d["farbe"] = {"petrol": "tuerkis", "bordeaux": "pink", "graphit": "blau"}.get(d["farbe"], "pink")
    if d.get("schema") not in SCHEMATA:
        d["schema"] = "dunkel"
    farbe = FARBEN[d["farbe"]]
    return {**d, "akzent_dunkel": farbe[1], "akzent_hell": farbe[2],
            "schrift_css": SCHRIFTEN.get(d["schrift"], SCHRIFTEN["standard"])[1]}


@bp.route("/fuer-mich", methods=["GET", "POST"])
def fuer_mich():
    u = aktueller_benutzer()
    if request.method == "POST":
        d = {
            "schema": f("schema") if f("schema") in SCHEMATA else "dunkel",
            "farbe": f("farbe") if f("farbe") in FARBEN else "pink",
            "schrift": f("schrift") if f("schrift") in SCHRIFTEN else "standard",
            "groesse": int(f("groesse")) if f("groesse").isdigit() and int(f("groesse")) in GROESSEN else 15,
            "dichte": f("dichte") if f("dichte") in DICHTE else "normal",
        }
        db.ex("UPDATE benutzer SET darstellung = ? WHERE id = ?", (json.dumps(d), u["id"]))
        flash("Darstellung gespeichert – gilt nur für Sie.", "ok")
        return redirect("/fuer-mich")
    return render_template("fuer_mich.html", d=darstellung(u), FARBEN=FARBEN, SCHEMATA=SCHEMATA, SCHRIFTEN=SCHRIFTEN, GROESSEN=GROESSEN, DICHTE=DICHTE)


def kontext():
    """Für base.html: Gemeinschaftswahl, Darstellung, offene Einrichtungen."""
    if not aktueller_benutzer():
        return {}
    gs = db.q("SELECT id, name, nr, assistent FROM gemeinschaft ORDER BY name")
    g = aktuelle_gemeinschaft()
    wahl = [{"t": x["name"], "zusatz": x["nr"], "href": f"/g/{x['id']}", "an": bool(g and g["id"] == x["id"])} for x in gs]
    wahl.append({"t": "Neue Gemeinschaft (Assistent)", "ic": "＋", "href": "/assistent"})
    u = aktueller_benutzer()
    menue = [{"t": "Für mich: Darstellung", "ic": "◐", "href": "/fuer-mich"}]
    if u["rolle"] == "admin":
        menue += [{"t": "Einstellungen der Verwaltung", "ic": "⚙", "href": "/einstellungen"},
                  {"t": "KI-Telefon einrichten", "ic": "☎", "href": "/telefon/einstellungen"}]
    menue.append({"t": f"Abmelden ({u['name']})", "ic": "⎋", "href": "/abmelden"})
    return {"g_aktuell": g, "g_wahl": json.dumps(wahl, ensure_ascii=False), "g_viele": len(gs) > 6,
            "menue_json": json.dumps(menue, ensure_ascii=False),
            "darstellung": darstellung(), "einrichtung_offen": [x for x in gs if x["assistent"]]}
