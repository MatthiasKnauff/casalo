"""PDF-Ausgaben (reportlab): Abrechnungen, Wirtschaftsplan, Einladung, Protokoll."""
import datetime
import io
import json

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from . import db, rechnen

_ss = getSampleStyleSheet()
S = {
    "text": ParagraphStyle("t", parent=_ss["Normal"], fontName="Helvetica", fontSize=10, leading=13.5),
    "klein": ParagraphStyle("k", parent=_ss["Normal"], fontName="Helvetica", fontSize=8.5, leading=11,
                            textColor=colors.HexColor("#555555")),
    "h1": ParagraphStyle("h1", parent=_ss["Heading1"], fontName="Helvetica-Bold", fontSize=15, leading=19,
                         spaceAfter=4),
    "h2": ParagraphStyle("h2", parent=_ss["Heading2"], fontName="Helvetica-Bold", fontSize=11.5, leading=15,
                         spaceBefore=10, spaceAfter=4),
    "rechts": ParagraphStyle("r", parent=_ss["Normal"], fontName="Helvetica", fontSize=10, alignment=TA_RIGHT),
}


def eur(c):
    return rechnen.fmt(c).replace("−", "-")


def esc(t):
    return (str(t or "")).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("−", "-")


def _fuss(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#777777"))
    links = db.setting("verwaltung_name")
    anschrift = db.setting("verwaltung_anschrift")
    canvas.drawString(20 * mm, 12 * mm, f"{links}{' · ' + anschrift if anschrift else ''}"[:120])
    canvas.drawRightString(190 * mm, 12 * mm, f"Seite {doc.page}")
    canvas.restoreState()


def _bauen(story, titel):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm,
                            bottomMargin=20 * mm, title=titel, author=db.setting("verwaltung_name"))
    doc.build(story, onFirstPage=_fuss, onLaterPages=_fuss)
    return buf.getvalue()


def _tabelle(daten, breiten, summenzeile=False, rechts_ab=1):
    t = Table(daten, colWidths=breiten, repeatRows=1)
    st = [
        ("FONT", (0, 0), (-1, -1), "Helvetica", 9.5),
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#444444")),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.HexColor("#999999")),
        ("LINEBELOW", (0, 1), (-1, -1), 0.25, colors.HexColor("#dddddd")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    if rechts_ab is not None:
        st.append(("ALIGN", (rechts_ab, 0), (-1, -1), "RIGHT"))
    if summenzeile:
        st += [("FONT", (0, -1), (-1, -1), "Helvetica-Bold", 9.5),
               ("LINEABOVE", (0, -1), (-1, -1), 0.8, colors.HexColor("#444444"))]
    t.setStyle(TableStyle(st))
    return t


def _anschrift(person):
    if not person:
        return []
    zeilen = [person.get("firma") or " ".join(x for x in (person.get("anrede"), person.get("vorname"),
                                                           person.get("nachname")) if x),
              person.get("strasse"), " ".join(x for x in (person.get("plz"), person.get("ort")) if x)]
    return [Paragraph(esc(z), S["text"]) for z in zeilen if z] + [Spacer(1, 10 * mm)]


def _kopf(g, titel, untertitel=""):
    return [Paragraph(esc(f"Gemeinschaft der Wohnungseigentümer {g['name']}"), S["klein"]),
            Paragraph(esc(titel), S["h1"])] + ([Paragraph(esc(untertitel), S["text"])] if untertitel else []) + \
           [Spacer(1, 4 * mm)]


# ------------------------------------------------------------------ Abrechnung
def _einzel_story(g, einheit, person, a, kostenarten, schluessel_text):
    d = a.einheiten[einheit["id"]]
    story = _anschrift(person)
    story += _kopf(g, f"Einzelabrechnung {a.jahr} – Einheit {einheit['nr']}",
                   f"{einheit['lage']} · MEA {einheit['mea']:g} · Wohnfläche {einheit['wohnflaeche']:g} m²")
    namen = {ka.id: ka.name for ka in kostenarten}
    zeilen = [["Kostenart", "Schlüssel", "Gesamtkosten", "Ihr Anteil"]]
    for z in a.gesamt:
        ka = z["kostenart"]
        zeilen.append([Paragraph(esc(ka.name), S["text"]), schluessel_text.get(ka.schluessel, ka.schluessel),
                       eur(z["betrag"]), eur(d["kosten"].get(ka.id, 0))])
    if a.ruecklage_soll:
        zeilen.append(["Zuführung Erhaltungsrücklage", "MEA", eur(a.ruecklage_soll), eur(d["ruecklage"])])
    zeilen.append(["Summe", "", eur(a.summe_kosten + a.ruecklage_soll), eur(d["summe"])])
    story.append(_tabelle(zeilen, [70 * mm, 32 * mm, 34 * mm, 34 * mm], summenzeile=True, rechts_ab=2))
    story.append(Spacer(1, 5 * mm))
    erg = [["", ""], ["Ihre Kosten", eur(d["summe"])],
           ["abzüglich Vorschüsse laut Wirtschaftsplan", eur(-d["vorschuss_soll"])]]
    sp = d["spitze"]
    erg.append(["Nachschuss (zu zahlen)" if sp > 0 else "Guthaben (Anpassung der Vorschüsse)" if sp < 0 else "Ergebnis",
                eur(abs(sp))])
    t = _tabelle(erg, [136 * mm, 34 * mm], summenzeile=True)
    story.append(t)
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph(
        "Nach § 28 Abs. 2 WEG beschließt die Versammlung über die Einforderung von Nachschüssen bzw. die "
        "Anpassung der beschlossenen Vorschüsse (Abrechnungsspitze). Zahlungsrückstände aus dem Jahr bleiben "
        "davon unberührt.", S["klein"]))
    if d["rueckstand"] > 0:
        story.append(Spacer(1, 2 * mm))
        story.append(Paragraph(esc(f"Hinweis: Aus {a.jahr} sind noch {eur(d['rueckstand'])} Hausgeld offen."), S["text"]))
    return story


def einzelabrechnung(g, einheit, person, a, kostenarten):
    from .rechnen import SCHLUESSEL
    kurz = {"mea": "MEA", "flaeche": "Wohnfläche", "einheiten": "je Einheit", "verbrauch": "Verbrauch",
            "heizung": "Messung" if g["heizkosten_modus"] == "messdienst" else "HeizkostenV", "direkt": "direkt"}
    return _bauen(_einzel_story(g, einheit, person, a, kostenarten, kurz or SCHLUESSEL), f"Einzelabrechnung {a.jahr}")


def alle_einzelabrechnungen(g, liste, a, kostenarten):
    """liste: [(einheit, person)] → ein PDF mit Seitenumbrüchen (für den Postversand)."""
    kurz = {"mea": "MEA", "flaeche": "Wohnfläche", "einheiten": "je Einheit", "verbrauch": "Verbrauch",
            "heizung": "Messung" if g["heizkosten_modus"] == "messdienst" else "HeizkostenV", "direkt": "direkt"}
    story = []
    for n, (e, p) in enumerate(liste):
        if n:
            story.append(PageBreak())
        story += _einzel_story(g, e, p, a, kostenarten, kurz)
    return _bauen(story or [Paragraph("Keine Einheiten.", S["text"])], f"Einzelabrechnungen {a.jahr}")


def gesamtabrechnung(g, a, einheiten_namen):
    story = _kopf(g, f"Gesamtabrechnung und Vermögensbericht {a.jahr}")
    zeilen = [["Kostenart", "Betrag"]] + [[Paragraph(esc(z["kostenart"].name), S["text"]), eur(z["betrag"])] for z in a.gesamt]
    zeilen.append(["Summe Kosten", eur(a.summe_kosten)])
    story.append(_tabelle(zeilen, [136 * mm, 34 * mm], summenzeile=True))
    story.append(Paragraph("Verteilung auf die Einheiten", S["h2"]))
    zeilen = [["Einheit", "Eigentümer", "Kosten", "Rücklage", "Vorschüsse", "Spitze"]]
    for eid, d in a.einheiten.items():
        nr, name = einheiten_namen.get(eid, ("?", ""))
        zeilen.append([nr, Paragraph(esc(name), S["text"]), eur(d["summe_kosten"]), eur(d["ruecklage"]),
                       eur(d["vorschuss_soll"]), eur(d["spitze"])])
    story.append(_tabelle(zeilen, [18 * mm, 56 * mm, 26 * mm, 24 * mm, 24 * mm, 22 * mm], rechts_ab=2))
    r = getattr(a, "ruecklage", None)
    if r:
        story.append(Paragraph("Erhaltungsrücklage", S["h2"]))
        story.append(_tabelle([["", ""], [f"Stand 01.01.{a.jahr}", eur(r["anfang"])], ["Zuführungen", eur(r["zufuehrung"])],
                               ["Entnahmen", eur(r["entnahme"])], [f"Stand 31.12.{a.jahr}", eur(r["ende"])]],
                              [136 * mm, 34 * mm], summenzeile=True))
    v = getattr(a, "vermoegen", None)
    if v:
        story.append(Paragraph("Vermögensbericht (§ 28 Abs. 4 WEG)", S["h2"]))
        zeilen = [["Konto", "Stand 31.12."]] + [[f"{k['bank']} {k['iban']} ({'Rücklage' if k['art'] == 'ruecklage' else 'Hausgeld'})",
                                                 eur(k["saldo"])] for k in v["konten"]]
        zeilen.append(["Summe Bankguthaben", eur(v["summe_konten"])])
        story.append(_tabelle(zeilen, [136 * mm, 34 * mm], summenzeile=True))
        story.append(Spacer(1, 2 * mm))
        story.append(Paragraph(esc(f"Offene Hausgeldforderungen gegen Eigentümer: {eur(v['forderungen'])}"), S["text"]))
        if v.get("umlagen"):
            story.append(Paragraph(esc(f"Offene Sonderumlagen gegen Eigentümer: {eur(v['umlagen'])}"), S["text"]))
    return _bauen(story, f"Gesamtabrechnung {a.jahr}")


def wirtschaftsplan(g, jahr, wp, kostenarten, einheiten):
    story = _kopf(g, f"Wirtschaftsplan {jahr}")
    namen = {ka.id: ka.name for ka in kostenarten}
    zeilen = [["Kostenart", "Ansatz"]] + [[Paragraph(esc(z["kostenart"].name), S["text"]), eur(z["betrag"])] for z in wp["zeilen"]]
    if wp["wp"] and wp["wp"]["ruecklage_cent"]:
        zeilen.append(["Zuführung Erhaltungsrücklage", eur(wp["wp"]["ruecklage_cent"])])
    zeilen.append(["Summe", eur(wp["summe"])])
    story.append(_tabelle(zeilen, [136 * mm, 34 * mm], summenzeile=True))
    story.append(Paragraph("Vorschüsse je Einheit", S["h2"]))
    zeilen = [["Einheit", "Eigentümer", "je Jahr", "je Monat"]]
    for e in einheiten:
        d = wp["einheiten"][e["id"]]
        zeilen.append([e["nr"], Paragraph(esc(e.get("eigentuemer", "")), S["text"]), eur(d["jahr"]), eur(d["monat"])])
    story.append(_tabelle(zeilen, [22 * mm, 88 * mm, 30 * mm, 30 * mm], rechts_ab=2))
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph("Die monatlichen Vorschüsse sind auf volle Cent aufgerundet.", S["klein"]))
    return _bauen(story, f"Wirtschaftsplan {jahr}")


# ------------------------------------------------------------------ Versammlung
def einladung(g, v, tops, person=None):
    from .versammlung import MEHRHEITEN
    beginn = datetime.datetime.fromisoformat(v["beginn"])
    story = _anschrift(person)
    story += _kopf(g, "Einladung zur Eigentümerversammlung",
                   f"{'Ordentliche' if v['art'] == 'ordentlich' else 'Außerordentliche'} Versammlung am "
                   f"{beginn:%d.%m.%Y} um {beginn:%H:%M} Uhr · {v['ort']}")
    if v["hybrid"]:
        story.append(Paragraph(esc("Sie können auch online teilnehmen und abstimmen (§ 23 Abs. 1a WEG). "
                                   f"Zugang: {v['videolink'] or 'wird rechtzeitig mitgeteilt'}"), S["text"]))
        story.append(Spacer(1, 3 * mm))
    story.append(Paragraph("Tagesordnung", S["h2"]))
    for t in tops:
        teile = [Paragraph(esc(f"TOP {t['nr']}: {t['titel']}"), ParagraphStyle("tb", parent=S["text"], fontName="Helvetica-Bold"))]
        if t["vorschlag"]:
            teile.append(Paragraph(esc(f"Beschlussvorschlag: {t['vorschlag']}"), S["text"]))
        if t["mehrheit"] != "keine":
            teile.append(Paragraph(esc(f"Erforderlich: {MEHRHEITEN.get(t['mehrheit'], '')}"), S["klein"]))
        teile.append(Spacer(1, 3 * mm))
        story.append(KeepTogether(teile))
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph("Die Versammlung ist unabhängig von der Zahl der Anwesenden beschlussfähig. Wenn Sie nicht "
                           "teilnehmen können, erteilen Sie bitte eine Vollmacht in Textform.", S["text"]))
    story.append(Spacer(1, 8 * mm))
    story.append(Paragraph("Mit freundlichen Grüßen<br/>" + esc(db.setting("verwaltung_name")), S["text"]))
    return story


def einladung_pdf(g, v, tops, person=None):
    return _bauen(einladung(g, v, tops, person), "Einladung")


def einladungen_pdf(g, v, tops, personen):
    story = []
    for n, p in enumerate(personen):
        if n:
            story.append(PageBreak())
        story += einladung(g, v, tops, p)
    return _bauen(story, "Einladungen")


def protokoll_pdf(g, v, tops, beschluesse):
    beginn = datetime.datetime.fromisoformat(v["beginn"])
    story = _kopf(g, "Niederschrift der Eigentümerversammlung", f"{beginn:%d.%m.%Y}, {beginn:%H:%M} Uhr · {v['ort']}")
    if v["protokoll"]:
        story.append(Paragraph(esc(v["protokoll"]).replace("\n", "<br/>"), S["text"]))
        story.append(Spacer(1, 3 * mm))
    bnr = {b["id"]: b["lfd"] for b in beschluesse}
    for t in tops:
        teile = [Paragraph(esc(f"TOP {t['nr']}: {t['titel']}"), S["h2"])]
        if t["vorschlag"]:
            teile.append(Paragraph(esc(f"Beschlussantrag: {t['vorschlag']}"), S["text"]))
        if t["mehrheit"] != "keine" and t["ja"] is not None:
            teile.append(Paragraph(esc(f"Abstimmung: {t['ja']:g} Ja · {t['nein']:g} Nein · {t['enthaltung'] or 0:g} Enthaltungen"
                                       f" → {t['ergebnis'] or 'offen'}"
                                       + (f" (Beschluss-Sammlung Nr. {bnr[t['beschluss_id']]})" if t["beschluss_id"] in bnr else "")),
                                   S["text"]))
        story.append(KeepTogether(teile))
    story.append(Spacer(1, 14 * mm))
    story.append(_tabelle([["", "", ""], ["Versammlungsleitung", "Eigentümer/in", "Beirat (falls bestellt)"]],
                          [56 * mm, 56 * mm, 58 * mm], rechts_ab=3))
    story.append(Paragraph("Unterschriften nach § 24 Abs. 6 WEG.", S["klein"]))
    return _bauen(story, "Niederschrift")


# ------------------------------------------------------------------ Forderungen
def _gruss():
    return [Spacer(1, 8 * mm), Paragraph("Mit freundlichen Grüßen<br/>" + esc(db.setting("verwaltung_name")), S["text"])]


def _datum(iso):
    return datetime.date.fromisoformat(iso).strftime("%d.%m.%Y")


def _konto_text(konto):
    if not konto:
        return ""
    from .bank import iban_format
    return f"{konto['bank'] or 'Konto der Gemeinschaft'} · IBAN {iban_format(konto['iban'])}" + (f" · BIC {konto['bic']}" if konto["bic"] else "")


def umlage_anforderung(g, u, einheit, person, anteil, konto, beschluss_text=""):
    story = _anschrift(person)
    story += _kopf(g, f"Anforderung Sonderumlage „{u['titel']}“", f"Einheit {einheit['nr']} · {einheit['lage'] or ''}")
    if beschluss_text:
        story.append(Paragraph(esc(beschluss_text), S["text"]))
        story.append(Spacer(1, 3 * mm))
    schl = {"mea": f"Miteigentumsanteilen ({einheit['mea']:g} MEA)", "flaeche": f"Wohnfläche ({einheit['wohnflaeche']:g} m²)",
            "einheiten": "Einheiten (je Einheit gleich)"}.get(u["schluessel"], "Miteigentumsanteilen")
    story.append(_tabelle([["", ""], ["Sonderumlage gesamt", eur(u["betrag_cent"])], [f"Ihr Anteil nach {schl}", eur(anteil)]],
                          [136 * mm, 34 * mm], summenzeile=True))
    story.append(Spacer(1, 4 * mm))
    zweck = f"Sonderumlage {u['titel']} Einheit {einheit['nr']}"
    if person and person.get("zahlweise") == "lastschrift" and person.get("mandat_ref"):
        story.append(Paragraph(esc(f"Der Betrag wird zum {_datum(u['faellig'])} von Ihrem Konto per Lastschrift eingezogen "
                                   f"(Mandat {person['mandat_ref']}). Bitte sorgen Sie für Deckung."), S["text"]))
    else:
        story.append(Paragraph(esc(f"Bitte überweisen Sie den Betrag bis zum {_datum(u['faellig'])} auf das Konto der Gemeinschaft:"), S["text"]))
        story.append(Paragraph(esc(_konto_text(konto)), S["text"]))
        story.append(Paragraph(esc(f"Verwendungszweck: {zweck}"), S["text"]))
    if u["zweck"] == "ruecklage":
        story.append(Spacer(1, 3 * mm))
        story.append(Paragraph("Die Sonderumlage wird der Erhaltungsrücklage zugeführt.", S["klein"]))
    return story + _gruss()


def umlage_anforderungen(g, u, liste, konto, beschluss_text=""):
    """liste: [(einheit, person, anteil)]"""
    story = []
    for n, (e, p, a) in enumerate(liste):
        if n:
            story.append(PageBreak())
        story += umlage_anforderung(g, u, e, p, a, konto, beschluss_text)
    return _bauen(story or [Paragraph("Keine Empfänger.", S["text"])], f"Sonderumlage {u['titel']}")


def mahnung(g, m, einheit, person, konto):
    from .forderungen import STUFEN
    posten = json.loads(m["aufstellung"] or "[]")
    story = _anschrift(person)
    story += _kopf(g, STUFEN[m["stufe"]], f"Einheit {einheit['nr']} · Stand {_datum(m['datum'])}")
    einleitung = {
        1: "sicher ist es Ihrer Aufmerksamkeit entgangen: Folgende Zahlungen an die Gemeinschaft sind noch offen.",
        2: "trotz unserer Zahlungserinnerung sind folgende Beträge weiterhin offen.",
        3: "trotz Erinnerung und Mahnung sind folgende Beträge weiterhin offen. Dies ist unsere letzte Mahnung.",
    }[m["stufe"]]
    anrede = "Sehr geehrte Damen und Herren," if not person or person.get("firma") else \
        f"Guten Tag {' '.join(x for x in (person.get('anrede'), person.get('nachname')) if x)},"
    story.append(Paragraph(esc(anrede), S["text"]))
    story.append(Paragraph(esc(einleitung), S["text"]))
    story.append(Spacer(1, 3 * mm))
    zeilen = [["Forderung", "fällig seit", "offen"]] + [[esc(p["text"]), _datum(p["faellig"]), eur(p["betrag"])] for p in posten]
    if m["gebuehr_cent"]:
        zeilen.append(["Mahngebühr", "", eur(m["gebuehr_cent"])])
    zeilen.append(["Zu zahlen", "", eur(m["rueckstand_cent"] + m["gebuehr_cent"])])
    story.append(_tabelle(zeilen, [100 * mm, 36 * mm, 34 * mm], summenzeile=True, rechts_ab=1))
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph(esc(f"Bitte überweisen Sie den Betrag bis zum {_datum(m['frist'])}:"), S["text"]))
    story.append(Paragraph(esc(_konto_text(konto)), S["text"]))
    story.append(Paragraph(esc(f"Verwendungszweck: Rückstand Einheit {einheit['nr']}"), S["text"]))
    story.append(Spacer(1, 3 * mm))
    if m["stufe"] == 3:
        story.append(Paragraph("Geht der Betrag nicht fristgerecht ein, wird die Verwaltung die Forderung für die Gemeinschaft "
                               "gerichtlich geltend machen (§ 27 Abs. 1, § 9b WEG). Dadurch entstehen weitere Kosten, die Sie tragen.",
                               S["text"]))
    story.append(Paragraph("Haben Sie inzwischen gezahlt, betrachten Sie dieses Schreiben bitte als gegenstandslos.", S["klein"]))
    return story + _gruss()


def mahnungen(g, liste, konto):
    """liste: [(mahnung, einheit, person)]"""
    story = []
    for n, (m, e, p) in enumerate(liste):
        if n:
            story.append(PageBreak())
        story += mahnung(g, m, e, p, konto)
    return _bauen(story or [Paragraph("Keine Mahnungen.", S["text"])], "Mahnungen")


def bescheinigung_35a(g, jahr, einheit, person, d):
    from .forderungen import KAT_35A
    story = _anschrift(person)
    story += _kopf(g, f"Bescheinigung über Aufwendungen nach § 35a EStG – {jahr}",
                   f"Einheit {einheit['nr']} · {einheit['lage'] or ''} · {g['strasse']}, {g['plz']} {g['ort']}")
    story.append(Paragraph(esc(f"Für das Kalenderjahr {jahr} sind für das Gemeinschaftseigentum folgende Arbeitskosten "
                               "(einschließlich Umsatzsteuer, ohne Material) unbar bezahlt worden. Der auf Ihre Einheit entfallende "
                               "Anteil ist nach dem Verteilerschlüssel der Jahresabrechnung ermittelt."), S["text"]))
    for kat, (titel, norm, _, _) in KAT_35A.items():
        pos = [p for p in d["posten"] if p["kategorie"] == kat and p["anteil"]]
        if not pos:
            continue
        story.append(Paragraph(esc(f"{titel} ({norm})"), S["h2"]))
        zeilen = [["Leistung", "Arbeitskosten gesamt", "Ihr Anteil"]] + \
                 [[Paragraph(esc(p["kostenart"]), S["text"]), eur(p["gesamt"]), eur(p["anteil"])] for p in pos]
        zeilen.append(["Summe", "", eur(d["summen"][kat])])
        story.append(_tabelle(zeilen, [100 * mm, 36 * mm, 34 * mm], summenzeile=True))
    if not any(d["summen"].values()):
        story.append(Spacer(1, 3 * mm))
        story.append(Paragraph("Im Kalenderjahr sind keine begünstigten Aufwendungen angefallen.", S["text"]))
    story.append(Spacer(1, 5 * mm))
    story.append(Paragraph(
        "Die Steuerermäßigung beträgt 20 % der Aufwendungen, höchstens 510 € (geringfügige Beschäftigung), 4.000 € "
        "(haushaltsnahe Dienstleistungen) bzw. 1.200 € (Handwerkerleistungen) im Jahr. Begünstigt sind nur Arbeits-, "
        "Fahrt- und Maschinenkosten, nicht Material; Voraussetzung ist eine Rechnung und Zahlung auf ein Konto des "
        "Leistenden (§ 35a Abs. 5 EStG). Bei vermieteten Einheiten gehören die Kosten zu den Werbungskosten; "
        "eine Weitergabe an Mieter ist über deren Nebenkostenabrechnung möglich. Diese Bescheinigung ersetzt keine Steuerberatung.",
        S["klein"]))
    return story + _gruss()


def bescheinigung_35a_pdf(g, jahr, einheit, person, d):
    return _bauen(bescheinigung_35a(g, jahr, einheit, person, d), f"Bescheinigung 35a {jahr}")


def bescheinigungen_35a_pdf(g, jahr, liste):
    story = []
    for n, (e, p, d) in enumerate(liste):
        if n:
            story.append(PageBreak())
        story += bescheinigung_35a(g, jahr, e, p, d)
    return _bauen(story or [Paragraph("Keine Empfänger.", S["text"])], f"Bescheinigungen 35a {jahr}")


# ------------------------------------------------------------------ Auftrag an Dienstleister
def auftrag(g, a, d, einheit, kontakt):
    story = _anschrift({"firma": d["firma"], "strasse": d["strasse"], "plz": d["plz"], "ort": d["ort"]} if d else None)
    story += _kopf(g, f"Auftrag {a['nr']}", f"vom {_datum(a['beauftragt_am'] or a['angelegt'])}")
    zeilen = [["", ""],
              ["Objekt", Paragraph(esc(f"{g['strasse']}, {g['plz']} {g['ort']}" + (f" – Einheit {einheit['nr']} {einheit['lage'] or ''}" if einheit else "")), S["text"])],
              ["Leistung", Paragraph(esc(a["titel"]), S["text"])]]
    if a["text"]:
        zeilen.append(["Beschreibung", Paragraph(esc(a["text"]).replace("\n", "<br/>"), S["text"])])
    if a["termin"]:
        zeilen.append(["Termin", _datum(a["termin"])])
    if kontakt:
        zeilen.append(["Zugang / Kontakt", Paragraph(esc(kontakt), S["text"])])
    if a["angebot_cent"]:
        zeilen.append(["Auftragswert", f"{eur(a['angebot_cent'])} laut Angebot (brutto)"])
    t = _tabelle(zeilen, [40 * mm, 130 * mm], rechts_ab=None)
    story.append(t)
    story.append(Spacer(1, 5 * mm))
    story.append(Paragraph("Rechnungsanschrift", S["h2"]))
    story.append(Paragraph(esc(f"Gemeinschaft der Wohnungseigentümer {g['name']}, {g['strasse']}, {g['plz']} {g['ort']}, "
                               f"vertreten durch {db.setting('verwaltung_name')}"
                               + (f", {db.setting('verwaltung_anschrift')}" if db.setting("verwaltung_anschrift") else "")), S["text"]))
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph(esc(f"Bitte geben Sie auf der Rechnung die Auftragsnummer {a['nr']} an und weisen Sie Arbeits-, Fahrt- und "
                               "Maschinenkosten getrennt vom Material aus (Bescheinigung nach § 35a EStG für die Eigentümer). "
                               "Mehrkosten über den Auftragswert hinaus bitte vorher mit uns abstimmen."), S["text"]))
    return _bauen(story + _gruss(), f"Auftrag {a['nr']}")


# ------------------------------------------------------------------ Verkauf einer Einheit
def verkauf_auskunft(d):
    g, e = d["g"], d["einheit"]
    story = _kopf(g, f"Verwalterauskunft zur Einheit {e['nr']}", f"Stand {d['stichtag']:%d.%m.%Y} · {g['strasse']}, {g['plz']} {g['ort']} · "
                                                                f"{e['lage'] or ''} · MEA {e['mea']:g} · {e['wohnflaeche']:g} m²")
    verk = ", ".join(" ".join(x for x in (p.get("vorname"), p.get("nachname")) if x) or p.get("firma", "") for p in d["verkaeufer"])
    story.append(Paragraph(esc(f"Eigentümer laut Verwaltungsunterlagen: {verk or '–'}. Die Auskunft wird mit Einverständnis des Eigentümers erteilt."), S["text"]))
    story.append(Paragraph("Hausgeld und Rückstände", S["h2"]))
    zeilen = [["", ""], ["Hausgeld monatlich laut Wirtschaftsplan", eur(d["monat"]) if d["monat"] is not None else "kein Wirtschaftsplan"]]
    zeilen += [[esc(p["text"]) + f" (fällig {_datum(p['faellig'])})", eur(p["betrag"])] for p in d["posten"]]
    zeilen.append(["Rückstände gesamt", eur(sum(p["betrag"] for p in d["posten"]))])
    story.append(_tabelle(zeilen, [130 * mm, 40 * mm], summenzeile=True))
    if d["kuenftig"]:
        story.append(Paragraph("Beschlossene, noch nicht fällige Sonderumlagen", S["h2"]))
        story.append(_tabelle([["Sonderumlage", "fällig", "Anteil der Einheit"]] +
                              [[esc(k["titel"]), _datum(k["faellig"]), eur(k["anteil"])] for k in d["kuenftig"]], [90 * mm, 40 * mm, 40 * mm]))
    story.append(Paragraph("Erhaltungsrücklage", S["h2"]))
    story.append(Paragraph(esc(f"Stand der Rücklagenkonten: {eur(d['ruecklage'])}; rechnerisch auf die Einheit entfallend (MEA): "
                               f"{eur(d['ruecklage_anteil'])}. Die Rücklage ist Vermögen der Gemeinschaft und wird beim Verkauf nicht ausgezahlt."),
                           S["text"]))
    story.append(Paragraph("Beschlüsse der letzten drei Jahre", S["h2"]))
    if d["beschluesse"]:
        story.append(_tabelle([["Nr.", "Datum", "Beschluss", "Ergebnis"]] +
                              [[str(b["lfd"]), _datum(b["datum"]), Paragraph(esc(b["text"]), S["klein"]), Paragraph(esc(b["ergebnis"]), S["klein"])]
                               for b in d["beschluesse"]], [12 * mm, 22 * mm, 96 * mm, 40 * mm], rechts_ab=None))
    else:
        story.append(Paragraph("Keine.", S["text"]))
    if d["umlaeufe"]:
        story.append(Spacer(1, 2 * mm))
        story.append(Paragraph(esc("Laufende Umlaufverfahren: " + "; ".join(u["titel"] for u in d["umlaeufe"])), S["text"]))
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph("Ab Eintragung im Grundbuch schuldet der Erwerber das Hausgeld und die danach fällig werdenden Beträge; "
                           "Rückstände des Veräußerers gehen nicht auf ihn über. Nach § 18 Abs. 4 WEG kann die Beschluss-Sammlung "
                           "eingesehen werden.", S["klein"]))
    return _bauen(story + _gruss(), f"Verwalterauskunft {e['nr']}")


def verkauf_zustimmung(d, kaeufer, urkunde):
    g, e = d["g"], d["einheit"]
    verk = ", ".join(" ".join(x for x in (p.get("vorname"), p.get("nachname")) if x) or p.get("firma", "") for p in d["verkaeufer"])
    story = _kopf(g, "Zustimmung zur Veräußerung (§ 12 WEG)")
    story.append(Paragraph(esc(
        f"Als Verwalterin der Gemeinschaft der Wohnungseigentümer {g['name']}, {g['strasse']}, {g['plz']} {g['ort']}, stimmt "
        f"{db.setting('verwaltung_name')} der Veräußerung des Wohnungseigentums Nr. {e['nr']} ({e['lage'] or ''}, "
        f"{e['mea']:g}/{d['mea_summe']:g} Miteigentumsanteile) "
        f"durch {verk or '________________'} an {kaeufer or '________________'} zu"
        + (f", beurkundet unter {urkunde}" if urkunde else "") + "."), S["text"]))
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph("Die Zustimmung wird gemäß der Gemeinschaftsordnung erteilt. Für das Grundbuchamt ist die Unterschrift "
                           "öffentlich zu beglaubigen; die Verwaltereigenschaft wird durch die Niederschrift über den "
                           "Bestellungsbeschluss mit öffentlich beglaubigten Unterschriften nachgewiesen (§ 26 Abs. 4 WEG).", S["text"]))
    story.append(Spacer(1, 22 * mm))
    story.append(_tabelle([["", ""], ["Ort, Datum", f"Unterschrift für {db.setting('verwaltung_name')}"]], [70 * mm, 100 * mm], rechts_ab=None))
    return _bauen(story, f"Zustimmung § 12 {e['nr']}")
