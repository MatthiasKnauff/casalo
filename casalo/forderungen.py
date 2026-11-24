"""Forderungen an Eigentümer: Sonderumlagen, offene Posten, Mahnstufen – und die Bescheinigung nach § 35a EStG.

Alles in Cent. Offene Posten werden ältester zuerst ausgeglichen (Zahlungen tilgen die älteste Forderung).
"""
import datetime
import json

from . import daten, db, rechnen

STUFEN = {1: "Zahlungserinnerung", 2: "Mahnung", 3: "Letzte Mahnung"}
KAT_35A = {
    "minijob": ("Haushaltsnahe Beschäftigungsverhältnisse (geringfügig)", "§ 35a Abs. 1 EStG", 20, 51000),
    "dienstleistung": ("Haushaltsnahe Dienstleistungen", "§ 35a Abs. 2 EStG", 20, 400000),
    "handwerker": ("Handwerkerleistungen", "§ 35a Abs. 3 EStG", 20, 120000),
}


# ------------------------------------------------------------------ Sonderumlagen
def umlage_anteile(u, einheiten=None):
    """{einheit_id: cent} – Verteilung der Sonderumlage nach ihrem Schlüssel."""
    einheiten = einheiten or daten.einheiten(u["gemeinschaft_id"])
    schluessel = u["schluessel"] if u["schluessel"] in ("mea", "flaeche", "einheiten") else "mea"
    return rechnen.verteile(u["betrag_cent"], rechnen.gewichte(schluessel, einheiten))


def umlage_gezahlt(uid):
    return {r["einheit_id"]: r["c"] for r in db.q(
        "SELECT einheit_id, SUM(betrag_cent) AS c FROM buchung WHERE sonderumlage_id = ? GROUP BY einheit_id", (uid,))}


def umlage_stand(u):
    ein = daten.einheiten(u["gemeinschaft_id"])
    anteile = umlage_anteile(u, ein)
    gezahlt = umlage_gezahlt(u["id"])
    zeilen = []
    for e in ein:
        soll, ist = anteile.get(e["id"], 0), gezahlt.get(e["id"], 0)
        zeilen.append({"einheit": e, "soll": soll, "ist": ist, "offen": soll - ist})
    return {"zeilen": zeilen, "soll": sum(z["soll"] for z in zeilen), "ist": sum(z["ist"] for z in zeilen),
            "offen": sum(max(0, z["offen"]) for z in zeilen)}


def offene_umlagen(einheit_id, stichtag=None):
    """Fällige, noch nicht (ganz) gezahlte Sonderumlagen einer Einheit."""
    e = db.q("SELECT * FROM einheit WHERE id = ?", (einheit_id,), one=True)
    out = []
    for u in db.q("SELECT * FROM sonderumlage WHERE gemeinschaft_id = ? ORDER BY faellig", (e["gemeinschaft_id"],)):
        if stichtag and u["faellig"] > stichtag:
            continue
        soll = umlage_anteile(u).get(einheit_id, 0)
        ist = umlage_gezahlt(u["id"]).get(einheit_id, 0)
        if soll - ist > 0:
            out.append({"id": u["id"], "titel": u["titel"], "faellig": u["faellig"], "offen": soll - ist, "soll": soll})
    return out


def offene_umlagen_summe(gid, stichtag):
    """Offene Sonderumlage-Forderungen zum Stichtag (für den Vermögensbericht)."""
    summe = 0
    for u in db.q("SELECT * FROM sonderumlage WHERE gemeinschaft_id = ? AND faellig <= ?", (gid, stichtag)):
        gezahlt = {r["einheit_id"]: r["c"] for r in db.q(
            "SELECT einheit_id, SUM(betrag_cent) AS c FROM buchung WHERE sonderumlage_id = ? AND datum <= ? GROUP BY einheit_id",
            (u["id"], stichtag))}
        summe += sum(max(0, a - gezahlt.get(eid, 0)) for eid, a in umlage_anteile(u).items())
    return summe


# ------------------------------------------------------------------ Offene Posten je Einheit
def _monate(jahr, bis):
    if bis.year < jahr:
        return 0
    return 12 if bis.year > jahr else bis.month


def offene_posten(einheit_id, stichtag=None, ab=None):
    """Liste fälliger, offener Forderungen gegen die Einheit, älteste zuerst.

    Hausgeld: Vorschüsse laut Wirtschaftsplan, je Monat zum Ersten fällig (Vorjahr und laufendes Jahr).
    Sonderumlagen: ab Fälligkeit. Zahlungen tilgen zuerst die älteste Forderung.
    ab: nur Forderungen und Zahlungen ab diesem Tag (Eigentümer seit) – Rückstände des Vorbesitzers bleiben dessen Sache.
    """
    stichtag = stichtag or datetime.date.today()
    ab_iso = ab or "0000-00-00"
    e = db.q("SELECT * FROM einheit WHERE id = ?", (einheit_id,), one=True)
    gid = e["gemeinschaft_id"]
    posten = []
    for jahr in (stichtag.year - 1, stichtag.year):
        wp = daten.wirtschaftsplan(gid, jahr)
        if not wp["wp"]:
            continue
        monat = wp["einheiten"][einheit_id]["monat"]
        soll = [(datetime.date(jahr, m, 1), monat) for m in range(1, _monate(jahr, stichtag) + 1)
                if datetime.date(jahr, m, 1).isoformat() >= ab_iso]
        ist = db.q("SELECT COALESCE(SUM(betrag_cent), 0) AS c FROM buchung WHERE einheit_id = ? AND art = 'hausgeld' "
                   "AND datum BETWEEN ? AND ?", (einheit_id, max(f"{jahr}-01-01", ab_iso), f"{jahr}-12-31"), one=True)["c"]
        for faellig, betrag in soll:                       # Zahlungen des Jahres tilgen die ältesten Monate
            getilgt = min(betrag, max(0, ist))
            ist -= getilgt
            if betrag - getilgt > 0:
                posten.append({"text": f"Hausgeld {faellig:%m/%Y}", "faellig": faellig.isoformat(), "betrag": betrag - getilgt})
    for u in db.q("SELECT * FROM sonderumlage WHERE gemeinschaft_id = ? AND faellig <= ? AND faellig >= ? ORDER BY faellig",
                  (gid, stichtag.isoformat(), ab_iso)):
        soll = umlage_anteile(u).get(einheit_id, 0)
        ist = db.q("SELECT COALESCE(SUM(betrag_cent), 0) AS c FROM buchung WHERE sonderumlage_id = ? AND einheit_id = ? AND datum >= ?",
                   (u["id"], einheit_id, ab_iso), one=True)["c"]
        if soll - ist > 0:
            posten.append({"text": f"Sonderumlage „{u['titel']}“", "faellig": u["faellig"], "betrag": soll - ist})
    posten.sort(key=lambda p: p["faellig"])
    return posten


def eigentuemer_seit(einheit_id):
    """Beginn des Eigentums der aktuellen Eigentümer (spätestes „von“), sonst None."""
    r = db.q("SELECT MAX(von) AS v FROM eigentum WHERE einheit_id = ? AND bis IS NULL AND von != ''", (einheit_id,), one=True)
    return r["v"] if r and r["v"] else None


# ------------------------------------------------------------------ Mahnwesen
def mahn_einstellungen():
    return {"frist_tage": int(db.setting("mahn_frist_tage")), "schwelle": int(db.setting("mahn_schwelle_cent")),
            "gebuehr": {s: int(db.setting(f"mahn_gebuehr_{s}")) for s in (1, 2, 3)}}


def mahnvorschlag(einheit_id, heute=None):
    """→ {posten, summe, letzte, stufe (None = nichts zu tun), grund}"""
    heute = heute or datetime.date.today()
    seit = eigentuemer_seit(einheit_id)
    posten = offene_posten(einheit_id, heute, seit)          # gemahnt werden die aktuellen Eigentümer für ihre Zeit
    summe = sum(p["betrag"] for p in posten)
    vorbesitzer = (sum(p["betrag"] for p in offene_posten(einheit_id, heute)) - summe) if seit else 0
    letzte = db.q("SELECT * FROM mahnung WHERE einheit_id = ? AND status = 'offen' ORDER BY stufe DESC, datum DESC LIMIT 1",
                  (einheit_id,), one=True)
    st = mahn_einstellungen()
    if summe < st["schwelle"]:
        if letzte:   # alles bezahlt → laufende Mahnungen erledigt
            db.ex("UPDATE mahnung SET status = 'bezahlt' WHERE einheit_id = ? AND status = 'offen'", (einheit_id,))
        return {"posten": posten, "summe": summe, "vorbesitzer": max(0, vorbesitzer), "letzte": None, "stufe": None, "grund": "kein Rückstand"}
    if not letzte:
        return {"posten": posten, "summe": summe, "vorbesitzer": max(0, vorbesitzer), "letzte": None, "stufe": 1, "grund": "erste Zahlungserinnerung"}
    if heute.isoformat() <= letzte["frist"]:
        return {"posten": posten, "summe": summe, "vorbesitzer": max(0, vorbesitzer), "letzte": letzte, "stufe": None,
                "grund": f"Frist der {STUFEN[letzte['stufe']]} läuft bis {datetime.date.fromisoformat(letzte['frist']):%d.%m.%Y}"}
    if letzte["stufe"] >= 3:
        return {"posten": posten, "summe": summe, "vorbesitzer": max(0, vorbesitzer), "letzte": letzte, "stufe": None,
                "grund": "letzte Mahnung erfolglos – gerichtliches Mahnverfahren prüfen"}
    return {"posten": posten, "summe": summe, "vorbesitzer": max(0, vorbesitzer), "letzte": letzte, "stufe": letzte["stufe"] + 1,
            "grund": f"Frist der {STUFEN[letzte['stufe']]} abgelaufen"}


def mahnung_anlegen(einheit_id, stufe, person, heute=None, versand="post"):
    heute = heute or datetime.date.today()
    st = mahn_einstellungen()
    v = mahnvorschlag(einheit_id, heute)
    e = db.q("SELECT * FROM einheit WHERE id = ?", (einheit_id,), one=True)
    frist = heute + datetime.timedelta(days=st["frist_tage"])
    return db.ex("INSERT INTO mahnung (gemeinschaft_id, einheit_id, person_id, stufe, datum, frist, rueckstand_cent, gebuehr_cent, "
                 "aufstellung, versand) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (e["gemeinschaft_id"], einheit_id, person["id"] if person else None, stufe, heute.isoformat(), frist.isoformat(),
                  v["summe"], st["gebuehr"].get(stufe, 0), json.dumps(v["posten"], ensure_ascii=False), versand))


# ------------------------------------------------------------------ § 35a EStG
def kostenarten_35a(gid):
    return {r["id"]: dict(r) for r in db.q("SELECT * FROM kostenart WHERE gemeinschaft_id = ? AND steuer35a != ''", (gid,))}


def bescheinigung_35a(gid, jahr):
    """Je Einheit: [{kategorie, kostenart, gesamt (Arbeitskosten WEG), anteil}] und Summen je Kategorie.

    Arbeitskosten: Lohnanteil laut Rechnung (Buchung) oder Anteil laut Kostenart. Bezahlt im Jahr (Abfluss).
    Verteilt wie die Kosten der Einzelabrechnung; aus der Rücklage bezahlte Maßnahmen nach MEA.
    """
    kas = kostenarten_35a(gid)
    if not kas:
        return {"kostenarten": [], "einheiten": {}}
    von, bis = f"{jahr}-01-01", f"{jahr}-12-31"
    ein = daten.einheiten(gid)
    a = daten.jahresabrechnung(gid, jahr)
    anteile_abr = {z["kostenart"].id: z["anteile"] for z in a.gesamt}
    zeilen = []
    for kid, ka in kas.items():
        for quelle, kontoart in (("Hausgeld", "hausgeld"), ("Erhaltungsrücklage", "ruecklage")):
            rows = db.q("SELECT b.betrag_cent, b.lohn_cent FROM buchung b LEFT JOIN konto k ON k.id = b.konto_id "
                        "WHERE b.gemeinschaft_id = ? AND b.kostenart_id = ? AND b.art = 'kosten' AND b.datum BETWEEN ? AND ? "
                        "AND " + ("(k.art IS NULL OR k.art = 'hausgeld')" if kontoart == "hausgeld" else "k.art = 'ruecklage'"),
                        (gid, kid, von, bis))
            lohn = 0
            for r in rows:
                kosten = -r["betrag_cent"]
                lohn += r["lohn_cent"] if r["lohn_cent"] is not None else round(kosten * ka["lohnanteil"] / 100)
            if lohn <= 0:
                continue
            gewichte = anteile_abr.get(kid) if kontoart == "hausgeld" else None
            if not gewichte or sum(gewichte.values()) <= 0:
                gewichte = rechnen.gewichte("mea", ein)
            zeilen.append({"kategorie": ka["steuer35a"], "kostenart": ka["name"] + ("" if kontoart == "hausgeld" else " (aus der Rücklage)"),
                           "gesamt": lohn, "anteile": rechnen.verteile(lohn, gewichte)})
    out = {}
    for e in ein:
        pos = [{"kategorie": z["kategorie"], "kostenart": z["kostenart"], "gesamt": z["gesamt"], "anteil": z["anteile"].get(e["id"], 0)}
               for z in zeilen]
        summen = {k: sum(p["anteil"] for p in pos if p["kategorie"] == k) for k in KAT_35A}
        out[e["id"]] = {"posten": pos, "summen": summen}
    return {"kostenarten": list(kas.values()), "einheiten": out, "zeilen": zeilen}
