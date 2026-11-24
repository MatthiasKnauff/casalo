"""PDF-Ausgaben (reportlab): Abrechnungen, Wirtschaftsplan, Einladung, Protokoll."""
import datetime
import io

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
        ("ALIGN", (rechts_ab, 0), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
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
