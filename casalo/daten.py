"""Lädt Daten aus der Datenbank und ruft den Rechenkern auf."""
from . import db, rechnen
from .rechnen import Kostenart


def einheiten(gid):
    return [dict(r) for r in db.q("SELECT * FROM einheit WHERE gemeinschaft_id = ? ORDER BY nr", (gid,))]


def kostenarten(gid):
    return [Kostenart(r["id"], r["name"], r["schluessel"])
            for r in db.q("SELECT * FROM kostenart WHERE gemeinschaft_id = ? ORDER BY reihenfolge, id", (gid,))]


def eigentuemer(einheit_id, stichtag=None):
    """Aktueller (oder zum Stichtag gültiger) Eigentümer – bei mehreren: alle."""
    if stichtag:
        rows = db.q("SELECT p.* FROM eigentum e JOIN person p ON p.id = e.person_id WHERE e.einheit_id = ? "
                    "AND (e.von = '' OR e.von <= ?) AND (e.bis IS NULL OR e.bis >= ?) ORDER BY p.nachname",
                    (einheit_id, stichtag, stichtag))
    else:
        rows = db.q("SELECT p.* FROM eigentum e JOIN person p ON p.id = e.person_id "
                    "WHERE e.einheit_id = ? AND e.bis IS NULL ORDER BY p.nachname", (einheit_id,))
    return [dict(r) for r in rows]


def name(p):
    if not p:
        return ""
    n = ", ".join(x for x in (p.get("nachname"), p.get("vorname")) if x)
    return p.get("firma") or n


def eigentuemer_text(einheit_id, stichtag=None):
    return " · ".join(name(p) for p in eigentuemer(einheit_id, stichtag)) or "– kein Eigentümer –"


def _jahr(j):
    return (f"{j}-01-01", f"{j}-12-31")


def wirtschaftsplan(gid, jahr):
    wp = db.q("SELECT * FROM wirtschaftsplan WHERE gemeinschaft_id = ? AND jahr = ?", (gid, jahr), one=True)
    ansaetze = {}
    if wp:
        ansaetze = {r["kostenart_id"]: r["betrag_cent"]
                    for r in db.q("SELECT * FROM wp_ansatz WHERE wirtschaftsplan_id = ?", (wp["id"],))}
    ein = einheiten(gid)
    kas = kostenarten(gid)
    vj = jahr - 1
    vorjahr_verbrauch = {ka.id: verbrauchswerte(ka.id, vj) for ka in kas if ka.schluessel == "verbrauch"}
    hz = heizwerte(gid, vj)
    g = gemeinschaft(gid)
    hz_gw = hz["betrag"] if g["heizkosten_modus"] == "messdienst" else hz["verbrauch"]
    vorjahr_heizung = {ka.id: hz_gw for ka in kas if ka.schluessel == "heizung"}
    vorjahr_direkt = {ka.id: direktkosten(gid, vj).get(ka.id, {}) for ka in kas if ka.schluessel == "direkt"}
    erg = rechnen.wirtschaftsplan(ein, kas, ansaetze, wp["ruecklage_cent"] if wp else 0,
                                  vorjahr_verbrauch, vorjahr_heizung, vorjahr_direkt)
    erg["wp"] = wp
    erg["ansaetze"] = ansaetze
    return erg


def gemeinschaft(gid):
    return db.q("SELECT * FROM gemeinschaft WHERE id = ?", (gid,), one=True)


def verbrauchswerte(kostenart_id, jahr):
    return {r["einheit_id"]: r["wert"] for r in
            db.q("SELECT * FROM verbrauch WHERE kostenart_id = ? AND jahr = ?", (kostenart_id, jahr))}


def heizwerte(gid, jahr):
    rows = db.q("SELECT * FROM heizung WHERE gemeinschaft_id = ? AND jahr = ?", (gid, jahr))
    return {"betrag": {r["einheit_id"]: r["betrag_cent"] for r in rows if r["betrag_cent"] is not None},
            "verbrauch": {r["einheit_id"]: r["verbrauch"] for r in rows if r["verbrauch"] is not None}}


def direktkosten(gid, jahr):
    von, bis = _jahr(jahr)
    out = {}
    for r in db.q("SELECT b.kostenart_id, b.einheit_id, -SUM(b.betrag_cent) AS c FROM buchung b "
                  "LEFT JOIN konto k ON k.id = b.konto_id WHERE b.gemeinschaft_id = ? AND b.art = 'kosten' "
                  "AND (k.art IS NULL OR k.art = 'hausgeld') AND b.einheit_id IS NOT NULL "
                  "AND b.datum BETWEEN ? AND ? GROUP BY b.kostenart_id, b.einheit_id", (gid, von, bis)):
        out.setdefault(r["kostenart_id"], {})[r["einheit_id"]] = r["c"]
    return out


def kosten(gid, jahr):
    """Ausgaben je Kostenart aus dem Hausgeldkonto (Rücklagenkonto zählt als Entnahme, nicht als Umlage)."""
    von, bis = _jahr(jahr)
    return {r["kostenart_id"]: r["c"] for r in
            db.q("SELECT b.kostenart_id, -SUM(b.betrag_cent) AS c FROM buchung b LEFT JOIN konto k ON k.id = b.konto_id "
                 "WHERE b.gemeinschaft_id = ? AND b.art = 'kosten' AND (k.art IS NULL OR k.art = 'hausgeld') "
                 "AND b.datum BETWEEN ? AND ? GROUP BY b.kostenart_id", (gid, von, bis))}


def zahlungen(gid, jahr):
    von, bis = _jahr(jahr)
    return {r["einheit_id"]: r["c"] for r in
            db.q("SELECT einheit_id, SUM(betrag_cent) AS c FROM buchung WHERE gemeinschaft_id = ? AND art = 'hausgeld' "
                 "AND datum BETWEEN ? AND ? GROUP BY einheit_id", (gid, von, bis))}


def jahresabrechnung(gid, jahr):
    g = gemeinschaft(gid)
    ein = einheiten(gid)
    kas = kostenarten(gid)
    wp = wirtschaftsplan(gid, jahr)
    hz = heizwerte(gid, jahr)
    a = rechnen.jahresabrechnung(
        jahr, ein, kas, kosten(gid, jahr),
        direkt=direktkosten(gid, jahr),
        verbrauch={ka.id: verbrauchswerte(ka.id, jahr) for ka in kas if ka.schluessel == "verbrauch"},
        heiz_modus=g["heizkosten_modus"], heiz_grundanteil=g["heiz_grundanteil"],
        heiz_messdienst=hz["betrag"], heiz_verbrauch=hz["verbrauch"],
        ruecklage_soll=wp["wp"]["ruecklage_cent"] if wp["wp"] else 0,
        vorschuss_soll={eid: d["jahr_soll"] for eid, d in wp["einheiten"].items()} if wp["wp"] else {},
        zahlungen_ist=zahlungen(gid, jahr),
    )
    if not wp["wp"]:
        a.hinweise.append(f"Für {jahr} gibt es keinen Wirtschaftsplan – Vorschüsse fehlen in der Abrechnung.")
    a.ruecklage = ruecklage(gid, jahr)
    a.vermoegen = vermoegen(gid, jahr, a)
    return a


def konto_saldo(konto, stichtag):
    s = db.q("SELECT COALESCE(SUM(betrag_cent), 0) AS s FROM buchung WHERE konto_id = ? AND datum <= ? AND datum >= ?",
             (konto["id"], stichtag, konto["anfangsdatum"] or "0000"), one=True)["s"]
    return konto["anfangssaldo_cent"] + s


def ruecklage(gid, jahr):
    """Entwicklung der Erhaltungsrücklage (Ist auf den Rücklagenkonten)."""
    von, bis = _jahr(jahr)
    konten = db.q("SELECT * FROM konto WHERE gemeinschaft_id = ? AND art = 'ruecklage'", (gid,))
    anfang = sum(konto_saldo(k, f"{jahr - 1}-12-31") for k in konten)
    zu = ab = 0
    for k in konten:
        r = db.q("SELECT COALESCE(SUM(CASE WHEN betrag_cent > 0 THEN betrag_cent END), 0) AS zu, "
                 "COALESCE(SUM(CASE WHEN betrag_cent < 0 THEN betrag_cent END), 0) AS ab "
                 "FROM buchung WHERE konto_id = ? AND datum BETWEEN ? AND ?", (k["id"], von, bis), one=True)
        zu += r["zu"]
        ab += r["ab"]
    return {"anfang": anfang, "zufuehrung": zu, "entnahme": ab, "ende": anfang + zu + ab}


def vermoegen(gid, jahr, abrechnung=None):
    """Vermögensbericht (§ 28 Abs. 4 WEG): Kontostände, Rücklage, offene Hausgelder."""
    stichtag = f"{jahr}-12-31"
    konten = [dict(k) | {"saldo": konto_saldo(k, stichtag)}
              for k in db.q("SELECT * FROM konto WHERE gemeinschaft_id = ? ORDER BY art, id", (gid,))]
    rueck = 0
    if abrechnung:
        rueck = sum(max(0, d["rueckstand"]) for d in abrechnung.einheiten.values())
    from . import forderungen
    umlagen = forderungen.offene_umlagen_summe(gid, stichtag)
    return {"konten": konten, "summe_konten": sum(k["saldo"] for k in konten), "forderungen": rueck, "umlagen": umlagen}


def offene_hausgelder(gid, jahr):
    """Rückstände je Einheit im laufenden Jahr bis heute (Soll monatlich laut Wirtschaftsplan)."""
    import datetime
    heute = datetime.date.today()
    monate = 12 if heute.year > jahr else (heute.month if heute.year == jahr else 0)
    wp = wirtschaftsplan(gid, jahr)
    if not wp["wp"]:
        return {}
    ist = zahlungen(gid, jahr)
    return {eid: d["monat"] * monate - ist.get(eid, 0) for eid, d in wp["einheiten"].items()}
