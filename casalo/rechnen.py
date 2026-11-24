"""Rechenkern: Kosten verteilen, Wirtschaftsplan, Jahresabrechnung, Heizkosten.

Reine Funktionen ohne Datenbank – dadurch gut testbar. Beträge in Cent (int).
Verteilt wird mit dem Verfahren der größten Reste: die Summe der Anteile ergibt
immer genau den Gesamtbetrag (keine Rundungscent verloren).
"""
from dataclasses import dataclass, field

SCHLUESSEL = {
    "mea": "Miteigentumsanteile",
    "flaeche": "Wohnfläche",
    "einheiten": "je Einheit",
    "verbrauch": "Verbrauch",
    "heizung": "Heizkosten (HeizkostenV / Messdienst)",
    "direkt": "direkt der Einheit zugeordnet",
}


def verteile(total, gewichte):
    """Verteilt `total` Cent nach Gewichten {schlüssel: gewicht}. Summe der Ergebnisse == total."""
    ids = list(gewichte)
    summe = sum(max(0.0, float(gewichte[i])) for i in ids)
    if not ids:
        return {}
    if summe <= 0:
        return {i: 0 for i in ids}
    vorzeichen = -1 if total < 0 else 1
    rest_total = abs(int(total))
    roh = {i: rest_total * max(0.0, float(gewichte[i])) / summe for i in ids}
    basis = {i: int(roh[i]) for i in ids}
    fehlend = rest_total - sum(basis.values())
    # größte Reste zuerst; bei Gleichstand stabile Reihenfolge der Eingabe
    reihenfolge = sorted(ids, key=lambda i: (-(roh[i] - basis[i]), ids.index(i)))
    for i in reihenfolge[:fehlend]:
        basis[i] += 1
    return {i: vorzeichen * basis[i] for i in ids}


def gewichte(schluessel, einheiten, werte=None):
    """Gewichte je Einheit für einen Verteilerschlüssel.

    einheiten: Liste von dicts mit id, mea, wohnflaeche.
    werte: {einheit_id: wert} für Verbrauch/Heizung bzw. Vorjahreswerte.
    """
    if schluessel == "mea":
        return {e["id"]: float(e["mea"] or 0) for e in einheiten}
    if schluessel == "flaeche":
        return {e["id"]: float(e["wohnflaeche"] or 0) for e in einheiten}
    if schluessel == "einheiten":
        return {e["id"]: 1.0 for e in einheiten}
    werte = werte or {}
    return {e["id"]: float(werte.get(e["id"]) or 0) for e in einheiten}


# ------------------------------------------------------------------ Heizkosten
def heizkosten(total, modus, einheiten, grundanteil=30, messdienst=None, verbrauch=None):
    """Heizkosten verteilen.

    modus 'messdienst': Beträge je Einheit aus der Abrechnung des Messdienstes übernehmen.
    modus 'selbst': HeizkostenV – Grundkosten (30–50 %) nach Wohnfläche, Rest nach Verbrauch.
    Gibt (anteile, hinweise) zurück.
    """
    hinweise = []
    if modus == "messdienst":
        messdienst = messdienst or {}
        anteile = {e["id"]: int(messdienst.get(e["id"]) or 0) for e in einheiten}
        summe = sum(anteile.values())
        if not messdienst:
            hinweise.append("Die Abrechnung des Messdienstes ist noch nicht eingelesen.")
        elif summe != total:
            hinweise.append(f"Messdienst verteilt {fmt(summe)}, gebucht sind {fmt(total)} "
                            f"(Abweichung {fmt(total - summe)}). Bitte dem Messdienst die gebuchten Kosten melden.")
        return anteile, hinweise

    gewuenscht = int(grundanteil or 30)
    grundanteil = min(50, max(30, gewuenscht))
    if grundanteil != gewuenscht:
        hinweise.append("Grundkostenanteil auf 30–50 % begrenzt (§ 7 HeizkostenV).")
    grund = round(total * grundanteil / 100)
    verbrauchsteil = total - grund
    gw_v = gewichte("verbrauch", einheiten, verbrauch)
    if sum(gw_v.values()) <= 0:
        hinweise.append("Keine Verbrauchswerte erfasst – Verbrauchskosten vorläufig nach Wohnfläche verteilt.")
        gw_v = gewichte("flaeche", einheiten)
    a = verteile(grund, gewichte("flaeche", einheiten))
    b = verteile(verbrauchsteil, gw_v)
    return {i: a[i] + b[i] for i in a}, hinweise


# ------------------------------------------------------------------ Wirtschaftsplan
def vorschuss_monat(jahresbetrag):
    """Monatlicher Vorschuss, auf volle Cent aufgerundet."""
    if jahresbetrag <= 0:
        return 0
    return -(-jahresbetrag // 12)


@dataclass
class Kostenart:
    id: int
    name: str
    schluessel: str


def wirtschaftsplan(einheiten, kostenarten, ansaetze, ruecklage, vorjahr_verbrauch=None,
                    vorjahr_heizung=None, vorjahr_direkt=None):
    """Vorschüsse je Einheit.

    ansaetze: {kostenart_id: cent}; ruecklage: Zuführung in Cent (nach MEA).
    vorjahr_*: Gewichte aus dem Vorjahr für Verbrauch/Heizung/direkt – sonst Ersatzschlüssel.
    Rückgabe: {"zeilen": [...], "einheiten": {id: {"jahr": cent, "monat": cent, "anteile": {ka: cent}}},
               "summe": cent, "hinweise": [...]}
    """
    vorjahr_verbrauch = vorjahr_verbrauch or {}
    vorjahr_heizung = vorjahr_heizung or {}
    vorjahr_direkt = vorjahr_direkt or {}
    hinweise = []
    proeinheit = {e["id"]: {"anteile": {}, "jahr": 0} for e in einheiten}
    zeilen = []
    for ka in kostenarten:
        betrag = int(ansaetze.get(ka.id, 0) or 0)
        if not betrag:
            continue
        s = ka.schluessel
        if s in ("mea", "flaeche", "einheiten"):
            gw = gewichte(s, einheiten)
        else:
            quelle = {"verbrauch": vorjahr_verbrauch, "heizung": vorjahr_heizung, "direkt": vorjahr_direkt}[s].get(ka.id, {})
            gw = gewichte("verbrauch", einheiten, quelle)
            if sum(gw.values()) <= 0:
                ersatz = "flaeche" if s in ("verbrauch", "heizung") else "mea"
                gw = gewichte(ersatz, einheiten)
                hinweise.append(f"{ka.name}: keine Vorjahreswerte – vorläufig nach {SCHLUESSEL[ersatz]} verteilt.")
        anteile = verteile(betrag, gw)
        for eid, c in anteile.items():
            proeinheit[eid]["anteile"][ka.id] = c
            proeinheit[eid]["jahr"] += c
        zeilen.append({"kostenart": ka, "betrag": betrag})
    if ruecklage:
        anteile = verteile(int(ruecklage), gewichte("mea", einheiten))
        for eid, c in anteile.items():
            proeinheit[eid]["anteile"]["ruecklage"] = c
            proeinheit[eid]["jahr"] += c
    for eid, d in proeinheit.items():
        d["monat"] = vorschuss_monat(d["jahr"])
        d["jahr_soll"] = d["monat"] * 12
    summe = sum(z["betrag"] for z in zeilen) + int(ruecklage or 0)
    return {"zeilen": zeilen, "einheiten": proeinheit, "summe": summe, "hinweise": hinweise}


# ------------------------------------------------------------------ Jahresabrechnung
@dataclass
class Abrechnung:
    jahr: int
    gesamt: list = field(default_factory=list)          # [{kostenart, betrag, anteile{eid: cent}}]
    einheiten: dict = field(default_factory=dict)       # eid -> {kosten, ruecklage, summe, vorschuss_soll, spitze, ist, rueckstand}
    summe_kosten: int = 0
    ruecklage_soll: int = 0
    hinweise: list = field(default_factory=list)
    pruefungen: list = field(default_factory=list)      # [(text, ok)]


def jahresabrechnung(jahr, einheiten, kostenarten, kosten, direkt=None, verbrauch=None,
                     heiz_modus="messdienst", heiz_grundanteil=30, heiz_messdienst=None, heiz_verbrauch=None,
                     ruecklage_soll=0, vorschuss_soll=None, zahlungen_ist=None):
    """Jahresabrechnung nach § 28 Abs. 2 WEG.

    kosten: {kostenart_id: cent} tatsächliche Ausgaben aus dem Hausgeldkonto im Jahr.
    direkt: {kostenart_id: {einheit_id: cent}} bei Schlüssel 'direkt'.
    verbrauch: {kostenart_id: {einheit_id: wert}} bei Schlüssel 'verbrauch'.
    vorschuss_soll: {einheit_id: cent} Vorschüsse laut Wirtschaftsplan (Jahr).
    zahlungen_ist: {einheit_id: cent} tatsächlich gezahltes Hausgeld (nur zur Information / Rückstände).
    Beschlossen wird nur die Abrechnungsspitze (Nachschuss bzw. Anpassung der Vorschüsse).
    """
    direkt = direkt or {}
    verbrauch = verbrauch or {}
    vorschuss_soll = vorschuss_soll or {}
    zahlungen_ist = zahlungen_ist or {}
    a = Abrechnung(jahr=jahr, ruecklage_soll=int(ruecklage_soll or 0))
    je = {e["id"]: {"kosten": {}, "ruecklage": 0} for e in einheiten}

    for ka in kostenarten:
        betrag = int(kosten.get(ka.id, 0) or 0)
        if not betrag and ka.schluessel != "direkt":
            continue
        s = ka.schluessel
        if s in ("mea", "flaeche", "einheiten"):
            anteile = verteile(betrag, gewichte(s, einheiten))
        elif s == "verbrauch":
            gw = gewichte("verbrauch", einheiten, verbrauch.get(ka.id))
            if sum(gw.values()) <= 0 and betrag:
                a.hinweise.append(f"{ka.name}: keine Verbrauchswerte – vorläufig nach Wohnfläche verteilt.")
                gw = gewichte("flaeche", einheiten)
            anteile = verteile(betrag, gw)
        elif s == "heizung":
            anteile, h = heizkosten(betrag, heiz_modus, einheiten, heiz_grundanteil, heiz_messdienst, heiz_verbrauch)
            a.hinweise += [f"{ka.name}: {x}" for x in h]
        else:  # direkt
            d = direkt.get(ka.id, {})
            anteile = {e["id"]: int(d.get(e["id"], 0)) for e in einheiten}
            betrag = sum(anteile.values()) if not betrag else betrag
            if sum(anteile.values()) != betrag:
                a.hinweise.append(f"{ka.name}: nicht alle Buchungen sind einer Einheit zugeordnet.")
        if not betrag and not any(anteile.values()):
            continue
        for eid, c in anteile.items():
            je[eid]["kosten"][ka.id] = c
        a.gesamt.append({"kostenart": ka, "betrag": betrag, "anteile": anteile})
        a.summe_kosten += betrag

    if a.ruecklage_soll:
        for eid, c in verteile(a.ruecklage_soll, gewichte("mea", einheiten)).items():
            je[eid]["ruecklage"] = c

    for e in einheiten:
        eid = e["id"]
        d = je[eid]
        d["summe_kosten"] = sum(d["kosten"].values())
        d["summe"] = d["summe_kosten"] + d["ruecklage"]
        d["vorschuss_soll"] = int(vorschuss_soll.get(eid, 0))
        d["spitze"] = d["summe"] - d["vorschuss_soll"]          # + Nachschuss, − Guthaben
        d["ist"] = int(zahlungen_ist.get(eid, 0))
        d["rueckstand"] = d["vorschuss_soll"] - d["ist"]        # + offen, − überzahlt
        a.einheiten[eid] = d

    verteilt = sum(d["summe_kosten"] for d in a.einheiten.values())
    a.pruefungen.append(("Summe der Einzelabrechnungen = Gesamtkosten", verteilt == a.summe_kosten))
    rl = sum(d["ruecklage"] for d in a.einheiten.values())
    a.pruefungen.append(("Rücklagenzuführung vollständig verteilt", rl == a.ruecklage_soll))
    a.pruefungen.append(("Keine offenen Hinweise", not a.hinweise))
    return a


# ------------------------------------------------------------------ Format
def fmt(cent, waehrung=True):
    """Cent → '1.234,56 €'."""
    if cent is None:
        return ""
    neg = cent < 0
    cent = abs(int(cent))
    s = f"{cent // 100:,}".replace(",", ".") + f",{cent % 100:02d}"
    return ("−" if neg else "") + s + (" €" if waehrung else "")


def parse_betrag(text):
    """'1.234,56' / '1234.56' / '-12' → Cent. Leer → 0."""
    t = (text or "").strip().replace("€", "").replace(" ", "").replace("−", "-")
    if not t:
        return 0
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    elif t.count(".") > 1:
        t = t.replace(".", "")
    from decimal import Decimal, InvalidOperation
    try:
        return int((Decimal(t) * 100).quantize(Decimal("1")))
    except InvalidOperation:
        raise ValueError(f"„{text}“ ist kein Betrag.")


def parse_zahl(text):
    t = (text or "").strip().replace(" ", "")
    if not t:
        return 0.0
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    return float(t)
