"""Objektbetreuung: Handwerker/Dienstleister, Aufträge, Wartungsverträge, Belege."""
import datetime
import os
import uuid

from flask import Response, abort, flash, redirect, render_template, request, send_file

from . import daten, db, mail, pdf, rechnen
from .v_komfort import AUFTRAG_STATUS, GEWERKE, KARTEN
from .web import bp, f, f_int, gemeinschaft_oder_404

# Vorschläge für Wartungen: Titel → (Intervall in Monaten, Grundlage, Gewerk)
WARTUNG_VORLAGEN = {
    "Aufzug – Wartung": (6, "Herstellervorgabe; Prüfung durch ZÜS nach BetrSichV", "Aufzug"),
    "Aufzug – Hauptprüfung (ZÜS)": (24, "BetrSichV Anhang 2 Abschnitt 2", "Aufzug"),
    "Heizungsanlage": (12, "Herstellervorgabe, Erhalt der Gewährleistung", "Heizung"),
    "Rauchwarnmelder": (12, "BauO NRW § 47 Abs. 3, DIN 14676", "Elektro"),
    "Trinkwasser – Legionellenprüfung": (36, "TrinkwV § 31 (Großanlagen)", "Sanitär"),
    "Feuerlöscher": (24, "DIN 14406-4", "Sonstiges"),
    "Rückstauverschluss": (6, "DIN EN 13564 / DIN 1986-3", "Sanitär"),
    "Dachrinnen und Dach": (12, "Verkehrssicherung", "Dach"),
    "Spielplatz": (12, "DIN EN 1176-7", "Garten"),
    "Schornsteinfeger / Feuerstättenschau": (12, "Feuerstättenbescheid (SchfHwG)", "Schornsteinfeger"),
}
BELEG_TYPEN = {b"%PDF": ("application/pdf", ".pdf"), b"\x89PNG": ("image/png", ".png"), b"\xff\xd8\xff": ("image/jpeg", ".jpg")}


def _heute():
    return datetime.date.today()


def _plus_monate(iso, monate):
    d = datetime.date.fromisoformat(iso)
    m = d.month - 1 + monate
    j, m = d.year + m // 12, m % 12 + 1
    tag = min(d.day, [31, 29 if j % 4 == 0 and (j % 100 or j % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1])
    return datetime.date(j, m, tag).isoformat()


def _zeile_oder_404(tabelle, oid):
    r = db.q(f"SELECT * FROM {tabelle} WHERE id = ?", (oid,), one=True)
    if not r:
        abort(404)
    return r


# ------------------------------------------------------------------ Dienstleister
@bp.route("/dienstleister", methods=["GET", "POST"])
def dienstleister():
    if request.method == "POST":
        if not f("firma"):
            flash("Firma angeben.", "fehler")
            return redirect("/dienstleister")
        did = db.ex("INSERT INTO dienstleister (firma, gewerk, ansprechpartner, telefon, notdienst, email, ort) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (f("firma"), f("gewerk") if f("gewerk") in GEWERKE else "Sonstiges", f("ansprechpartner"), f("telefon"),
                     f("notdienst"), f("email"), f("ort")))
        flash("Dienstleister angelegt.", "ok")
        return redirect(request.form.get("_zurueck") or f"/dienstleister#d{did}")
    gewerk = request.args.get("gewerk", "")
    rows = db.q("SELECT d.*, (SELECT COUNT(*) FROM auftrag a WHERE a.dienstleister_id = d.id AND a.status IN ('beauftragt','erledigt')) AS offen, "
                "(SELECT COUNT(*) FROM wartung w WHERE w.dienstleister_id = d.id) AS wartungen FROM dienstleister d "
                "WHERE d.aktiv = 1" + (" AND d.gewerk = ?" if gewerk else "") + " ORDER BY d.gewerk, d.firma", (gewerk,) if gewerk else ())
    return render_template("dienstleister.html", liste=rows, gewerke=GEWERKE, gewerk=gewerk)


@bp.post("/dienstleister/<int:did>/loeschen")
def dienstleister_loeschen(did):
    _zeile_oder_404("dienstleister", did)
    benutzt = db.q("SELECT 1 FROM auftrag WHERE dienstleister_id = ? UNION SELECT 1 FROM wartung WHERE dienstleister_id = ?",
                   (did, did), one=True)
    if benutzt:
        db.ex("UPDATE dienstleister SET aktiv = 0 WHERE id = ?", (did,))
        flash("Ausgeblendet – Aufträge und Wartungen behalten den Dienstleister.", "ok")
    else:
        db.ex("DELETE FROM dienstleister WHERE id = ?", (did,))
        flash("Dienstleister gelöscht.", "ok")
    return redirect("/dienstleister")


def karte_dienstleister(oid):
    d = _zeile_oder_404("dienstleister", oid)
    return render_template("karten/dienstleister.html", d=d,
                           auftraege=db.q("SELECT a.*, g.name AS g_name FROM auftrag a JOIN gemeinschaft g ON g.id = a.gemeinschaft_id "
                                          "WHERE a.dienstleister_id = ? ORDER BY a.angelegt DESC LIMIT 6", (oid,)),
                           wartungen=db.q("SELECT w.*, g.name AS g_name FROM wartung w JOIN gemeinschaft g ON g.id = w.gemeinschaft_id "
                                          "WHERE w.dienstleister_id = ? ORDER BY w.naechste", (oid,)))


KARTEN["dienstleister"] = karte_dienstleister


# ------------------------------------------------------------------ Aufträge
def naechste_auftragsnr(gid, jahr):
    n = db.q("SELECT COUNT(*) AS n FROM auftrag WHERE gemeinschaft_id = ? AND nr LIKE ?", (gid, f"A-{jahr}-%"), one=True)["n"]
    return f"A-{jahr}-{n + 1:03d}"


def auftrag_anlegen(gid, titel, text="", einheit_id=None, vorgang_id=None, dienstleister_id=None, kostenart_id=None,
                    wartung_id=None, termin=""):
    heute = _heute()
    aid = db.ex("INSERT INTO auftrag (gemeinschaft_id, einheit_id, vorgang_id, dienstleister_id, wartung_id, kostenart_id, nr, titel, "
                "text, angelegt, termin) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (gid, einheit_id, vorgang_id, dienstleister_id, wartung_id, kostenart_id, naechste_auftragsnr(gid, heute.year),
                 titel, text, heute.isoformat(), termin))
    if vorgang_id:
        db.ex("UPDATE vorgang SET auftrag_id = ? WHERE id = ?", (aid, vorgang_id))
    return aid


def auftrag_abrechnen(aid, buchung_id):
    """Rechnung bezahlt: Buchung verknüpfen, Status „abgerechnet“."""
    a = db.q("SELECT * FROM auftrag WHERE id = ?", (aid,), one=True)
    if not a:
        return
    db.ex("UPDATE auftrag SET buchung_id = ?, status = 'abgerechnet', erledigt_am = CASE WHEN erledigt_am = '' THEN ? ELSE erledigt_am END "
          "WHERE id = ?", (buchung_id, _heute().isoformat(), aid))
    db.ex("UPDATE buchung SET dienstleister_id = ? WHERE id = ?", (a["dienstleister_id"], buchung_id))
    db.ex("UPDATE beleg SET buchung_id = ? WHERE auftrag_id = ? AND buchung_id IS NULL", (buchung_id, aid))
    _wartung_nachziehen(a, _heute().isoformat())


def _wartung_nachziehen(a, datum):
    if not a["wartung_id"]:
        return
    w = db.q("SELECT * FROM wartung WHERE id = ?", (a["wartung_id"],), one=True)
    if w and (not w["letzte"] or datum > w["letzte"]):
        db.ex("UPDATE wartung SET letzte = ?, naechste = ? WHERE id = ?",
              (datum, _plus_monate(datum, w["intervall_monate"]) if w["intervall_monate"] else "", w["id"]))


def offene_rechnungen(gid):
    """Aufträge mit Rechnungsbetrag, die noch nicht bezahlt sind (für den Bankabgleich)."""
    return db.q("SELECT a.*, d.iban AS d_iban, d.firma FROM auftrag a LEFT JOIN dienstleister d ON d.id = a.dienstleister_id "
                "WHERE a.gemeinschaft_id = ? AND a.rechnung_cent IS NOT NULL AND a.buchung_id IS NULL AND a.status IN ('beauftragt','erledigt')",
                (gid,))


def vorschlag_rechnung(u, gid):
    """Ausgang = offene Rechnung eines Auftrags? → Vorschlag mit auftrag_id."""
    betrag = -u["betrag_cent"]
    from .bank import iban_normal
    for a in offene_rechnungen(gid):
        if a["rechnung_cent"] != betrag:
            continue
        gruende = ["Betrag = Rechnung " + a["nr"]]
        zweck = f"{u.get('zweck', '')} {u.get('name', '')}".lower()
        if a["rechnung_nr"] and a["rechnung_nr"].lower() in zweck:
            gruende.append("Rechnungsnummer")
        if a["d_iban"] and u.get("iban") and iban_normal(u["iban"]) == iban_normal(a["d_iban"]):
            gruende.append("IBAN des Dienstleisters")
        return {"art": "kosten", "kostenart_id": a["kostenart_id"], "auftrag_id": a["id"],
                "text": f"{a['firma'] or ''} {a['nr']} {a['titel']}".strip()[:200],
                "sicherheit": "sicher" if len(gruende) > 1 and a["kostenart_id"] else "wahrscheinlich",
                "punkte": 50 + 25 * (len(gruende) - 1), "gruende": gruende}
    return None


@bp.route("/g/<int:gid>/auftraege", methods=["GET", "POST"])
def auftraege(gid):
    g = gemeinschaft_oder_404(gid)
    if request.method == "POST":
        if not f("titel"):
            flash("Leistung angeben.", "fehler")
            return redirect(request.url)
        aid = auftrag_anlegen(gid, f("titel"), f("text"), f_int("einheit_id") or None, None, f_int("dienstleister_id") or None,
                              f_int("kostenart_id") or None, termin=f("termin"))
        return redirect(f"/auftrag/{aid}")
    status = request.args.get("status", "laufend")
    bed = {"laufend": "a.status IN ('entwurf','beauftragt','erledigt')", "alle": "1"}.get(status, "a.status = ?")
    args = (gid,) if status in ("laufend", "alle") else (gid, status)
    rows = db.q("SELECT a.*, d.firma, e.nr AS e_nr, k.name AS ka_name FROM auftrag a LEFT JOIN dienstleister d ON d.id = a.dienstleister_id "
                "LEFT JOIN einheit e ON e.id = a.einheit_id LEFT JOIN kostenart k ON k.id = a.kostenart_id "
                f"WHERE a.gemeinschaft_id = ? AND {bed} ORDER BY a.angelegt DESC, a.id DESC", args)
    return render_template("auftraege.html", g=g, liste=rows, status=status, stati=AUFTRAG_STATUS, **_auswahl(gid))


def _auswahl(gid):
    return {"dienstleister": db.q("SELECT * FROM dienstleister WHERE aktiv = 1 ORDER BY gewerk, firma"),
            "einheiten": daten.einheiten(gid),
            "kostenarten": db.q("SELECT * FROM kostenart WHERE gemeinschaft_id = ? ORDER BY reihenfolge, id", (gid,))}


@bp.post("/vorgang/<int:vid>/auftrag")
def vorgang_auftrag(vid):
    v = _zeile_oder_404("vorgang", vid)
    if v["auftrag_id"]:
        return redirect(f"/auftrag/{v['auftrag_id']}")
    if not v["gemeinschaft_id"]:
        flash("Dem Vorgang ist keine Gemeinschaft zugeordnet – erst zuordnen.", "fehler")
        return redirect("/vorgaenge")
    aid = auftrag_anlegen(v["gemeinschaft_id"], v["titel"], v["text"], v["einheit_id"], vid)
    flash("Auftrag aus dem Vorgang angelegt – Dienstleister wählen und beauftragen.", "ok")
    return redirect(f"/auftrag/{aid}")


@bp.get("/auftrag/<int:aid>")
def auftrag(aid):
    a = _zeile_oder_404("auftrag", aid)
    g = gemeinschaft_oder_404(a["gemeinschaft_id"])
    d = db.q("SELECT * FROM dienstleister WHERE id = ?", (a["dienstleister_id"],), one=True) if a["dienstleister_id"] else None
    return render_template("auftrag.html", a=a, g=g, d=d, stati=dict(AUFTRAG_STATUS),
                           e=db.q("SELECT * FROM einheit WHERE id = ?", (a["einheit_id"],), one=True) if a["einheit_id"] else None,
                           v=db.q("SELECT * FROM vorgang WHERE id = ?", (a["vorgang_id"],), one=True) if a["vorgang_id"] else None,
                           w=db.q("SELECT * FROM wartung WHERE id = ?", (a["wartung_id"],), one=True) if a["wartung_id"] else None,
                           b=db.q("SELECT * FROM buchung WHERE id = ?", (a["buchung_id"],), one=True) if a["buchung_id"] else None,
                           belege=db.q("SELECT * FROM beleg WHERE auftrag_id = ? ORDER BY id", (aid,)),
                           konten=db.q("SELECT * FROM konto WHERE gemeinschaft_id = ? ORDER BY art, id", (g["id"],)),
                           heute=_heute().isoformat(), **_auswahl(g["id"]))


@bp.post("/auftrag/<int:aid>/zuordnen")
def auftrag_zuordnen(aid):
    a = _zeile_oder_404("auftrag", aid)
    did, kid, eid = f_int("dienstleister_id") or None, f_int("kostenart_id") or None, f_int("einheit_id") or None
    if kid and not db.q("SELECT 1 FROM kostenart WHERE id = ? AND gemeinschaft_id = ?", (kid, a["gemeinschaft_id"]), one=True):
        abort(400)
    if eid and not db.q("SELECT 1 FROM einheit WHERE id = ? AND gemeinschaft_id = ?", (eid, a["gemeinschaft_id"]), one=True):
        abort(400)
    db.ex("UPDATE auftrag SET dienstleister_id = ?, kostenart_id = ?, einheit_id = ? WHERE id = ?", (did, kid, eid, aid))
    flash("Gespeichert.", "ok")
    return redirect(f"/auftrag/{aid}")


def _auftrag_pdf(a):
    g = daten.gemeinschaft(a["gemeinschaft_id"])
    d = db.q("SELECT * FROM dienstleister WHERE id = ?", (a["dienstleister_id"],), one=True) if a["dienstleister_id"] else None
    e = db.q("SELECT * FROM einheit WHERE id = ?", (a["einheit_id"],), one=True) if a["einheit_id"] else None
    kontakt = None
    if e:
        ps = daten.eigentuemer(e["id"])
        if ps:
            kontakt = f"{daten.name(ps[0])}{', Tel. ' + ps[0]['telefon'] if ps[0].get('telefon') else ''}"
    return pdf.auftrag(g, dict(a), dict(d) if d else None, dict(e) if e else None, kontakt)


@bp.get("/auftrag/<int:aid>.pdf")
def auftrag_pdf(aid):
    a = _zeile_oder_404("auftrag", aid)
    return Response(_auftrag_pdf(a), mimetype="application/pdf", headers={"Content-Disposition": f'inline; filename="Auftrag-{a["nr"]}.pdf"'})


@bp.post("/auftrag/<int:aid>/status")
def auftrag_status(aid):
    a = _zeile_oder_404("auftrag", aid)
    neu = f("status")
    if neu not in dict(AUFTRAG_STATUS) or neu == "abgerechnet":
        abort(400)
    heute = _heute().isoformat()
    if neu == "beauftragt":
        d = db.q("SELECT * FROM dienstleister WHERE id = ?", (a["dienstleister_id"],), one=True) if a["dienstleister_id"] else None
        if not d:
            flash("Erst einen Dienstleister wählen.", "fehler")
            return redirect(f"/auftrag/{aid}")
        db.ex("UPDATE auftrag SET status = 'beauftragt', beauftragt_am = ? WHERE id = ?", (heute, aid))
        if request.form.get("senden") and d["email"]:
            g = daten.gemeinschaft(a["gemeinschaft_id"])
            try:
                mail.senden(d["email"], f"Auftrag {a['nr']} – WEG {g['name']}: {a['titel']}",
                            f"Guten Tag,\n\nhiermit beauftragen wir Sie im Namen der Gemeinschaft der Wohnungseigentümer {g['name']} "
                            f"mit: {a['titel']}.\nEinzelheiten stehen im angehängten Auftrag. Bitte geben Sie die Auftragsnummer "
                            f"{a['nr']} auf der Rechnung an und weisen Sie die Arbeitskosten gesondert aus.\n\n"
                            f"Mit freundlichen Grüßen\n{db.setting('verwaltung_name')}",
                            [(f"Auftrag-{a['nr']}.pdf", _auftrag_pdf(db.q("SELECT * FROM auftrag WHERE id = ?", (aid,), one=True)),
                              "application/pdf")])
                flash(f"Auftrag an {d['email']} gesendet.", "ok")
            except mail.MailFehler as ex:
                flash(str(ex) + " Der Auftrag ist trotzdem als beauftragt vermerkt – PDF ausdrucken oder erneut senden.", "fehler")
        else:
            flash("Als beauftragt vermerkt.", "ok")
    elif neu == "erledigt":
        datum = f("datum") or heute
        db.ex("UPDATE auftrag SET status = 'erledigt', erledigt_am = ? WHERE id = ?", (datum, aid))
        _wartung_nachziehen(a, datum)
        if a["vorgang_id"]:
            db.ex("UPDATE vorgang SET status = 'erledigt' WHERE id = ?", (a["vorgang_id"],))
        flash("Erledigt." + (" Der Vorgang ist ebenfalls erledigt." if a["vorgang_id"] else ""), "ok")
    else:
        db.ex("UPDATE auftrag SET status = ? WHERE id = ?", (neu, aid))
        flash("Status geändert.", "ok")
    return redirect(f"/auftrag/{aid}")


@bp.post("/auftrag/<int:aid>/bezahlt")
def auftrag_bezahlt(aid):
    """Rechnung ohne Bankumsatz buchen (z. B. bar oder vorab gebucht)."""
    a = _zeile_oder_404("auftrag", aid)
    if not a["rechnung_cent"] or not a["kostenart_id"]:
        flash("Rechnungsbetrag und Kostenart eintragen.", "fehler")
        return redirect(f"/auftrag/{aid}")
    konto = f_int("konto_id") or None
    if konto and not db.q("SELECT 1 FROM konto WHERE id = ? AND gemeinschaft_id = ?", (konto, a["gemeinschaft_id"]), one=True):
        abort(400)
    d = db.q("SELECT firma FROM dienstleister WHERE id = ?", (a["dienstleister_id"],), one=True) if a["dienstleister_id"] else None
    bid = db.ex("INSERT INTO buchung (gemeinschaft_id, konto_id, datum, betrag_cent, art, kostenart_id, einheit_id, text) "
                "VALUES (?, ?, ?, ?, 'kosten', ?, ?, ?)",
                (a["gemeinschaft_id"], konto, f("datum") or _heute().isoformat(), -abs(a["rechnung_cent"]), a["kostenart_id"],
                 a["einheit_id"] if f("direkt") else None,
                 f"{d['firma'] + ' ' if d else ''}{a['nr']} {a['titel']}"[:200]))
    auftrag_abrechnen(aid, bid)
    flash("Rechnung gebucht.", "ok")
    return redirect(f"/auftrag/{aid}")


def karte_auftrag(oid):
    a = db.q("SELECT a.*, d.firma, d.telefon AS d_tel FROM auftrag a LEFT JOIN dienstleister d ON d.id = a.dienstleister_id "
             "WHERE a.id = ?", (oid,), one=True)
    if not a:
        abort(404)
    return render_template("karten/auftrag.html", a=a, stati=dict(AUFTRAG_STATUS))


KARTEN["auftrag"] = karte_auftrag


# ------------------------------------------------------------------ Wartung
def faellige_wartungen(tage=30, gid=None):
    """Wartungen mit Termin in den nächsten `tage` Tagen (oder überfällig) und Verträge mit nahender Kündigungsfrist."""
    grenze = (_heute() + datetime.timedelta(days=tage)).isoformat()
    sql = ("SELECT w.*, g.name AS g_name, d.firma FROM wartung w JOIN gemeinschaft g ON g.id = w.gemeinschaft_id "
           "LEFT JOIN dienstleister d ON d.id = w.dienstleister_id WHERE " + ("w.gemeinschaft_id = ? AND " if gid else ""))
    termine = db.q(sql + "w.naechste != '' AND w.naechste <= ? ORDER BY w.naechste", ((gid,) if gid else ()) + (grenze,))
    kuendigung = []
    for w in db.q(sql + "w.vertrag_bis != ''", (gid,) if gid else ()):
        frist = _plus_monate(w["vertrag_bis"], -w["kuendigung_monate"])
        if _heute().isoformat() <= frist <= grenze:
            kuendigung.append(dict(w) | {"kuendigen_bis": frist})
    return {"termine": termine, "kuendigung": kuendigung}


@bp.route("/g/<int:gid>/wartung", methods=["GET", "POST"])
def wartung(gid):
    g = gemeinschaft_oder_404(gid)
    if request.method == "POST":
        titel = f("titel")
        if not titel:
            flash("Bezeichnung angeben.", "fehler")
            return redirect(request.url)
        vorlage = WARTUNG_VORLAGEN.get(titel)
        intervall = f_int("intervall_monate", vorlage[0] if vorlage else 12)
        letzte = f("letzte")
        naechste = f("naechste") or (_plus_monate(letzte, intervall) if letzte and intervall else "")
        db.ex("INSERT INTO wartung (gemeinschaft_id, dienstleister_id, kostenart_id, titel, intervall_monate, letzte, naechste, pflicht, "
              "vertrag_bis, kosten_jahr_cent) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
              (gid, f_int("dienstleister_id") or None, f_int("kostenart_id") or None, titel, intervall, letzte, naechste,
               f("pflicht") or (vorlage[1] if vorlage else ""), f("vertrag_bis"),
               abs(rechnen.parse_betrag(f("kosten") or "0"))))
        flash("Wartung angelegt.", "ok")
        return redirect(f"/g/{gid}/wartung")
    rows = db.q("SELECT w.*, d.firma, k.name AS ka_name, (SELECT a.id FROM auftrag a WHERE a.wartung_id = w.id AND a.status IN "
                "('entwurf','beauftragt') ORDER BY a.id DESC LIMIT 1) AS offener_auftrag FROM wartung w "
                "LEFT JOIN dienstleister d ON d.id = w.dienstleister_id LEFT JOIN kostenart k ON k.id = w.kostenart_id "
                "WHERE w.gemeinschaft_id = ? ORDER BY w.naechste = '', w.naechste, w.titel", (gid,))
    return render_template("wartung.html", g=g, liste=rows, vorlagen=WARTUNG_VORLAGEN, faellig=faellige_wartungen(30, gid),
                           heute=_heute().isoformat(), bald=(_heute() + datetime.timedelta(days=30)).isoformat(), **_auswahl(gid))


@bp.post("/wartung/<int:wid>/auftrag")
def wartung_auftrag(wid):
    w = _zeile_oder_404("wartung", wid)
    aid = auftrag_anlegen(w["gemeinschaft_id"], w["titel"], w["notiz"], dienstleister_id=w["dienstleister_id"],
                          kostenart_id=w["kostenart_id"], wartung_id=wid, termin=w["naechste"])
    return redirect(f"/auftrag/{aid}")


@bp.post("/wartung/<int:wid>/loeschen")
def wartung_loeschen(wid):
    w = _zeile_oder_404("wartung", wid)
    db.ex("UPDATE auftrag SET wartung_id = NULL WHERE wartung_id = ?", (wid,))
    db.ex("DELETE FROM wartung WHERE id = ?", (wid,))
    flash("Wartung gelöscht.", "ok")
    return redirect(f"/g/{w['gemeinschaft_id']}/wartung")


def karte_wartung(oid):
    w = _zeile_oder_404("wartung", oid)
    return render_template("karten/wartung.html", w=w,
                           auftraege=db.q("SELECT * FROM auftrag WHERE wartung_id = ? ORDER BY angelegt DESC LIMIT 5", (oid,)))


KARTEN["wartung"] = karte_wartung


# ------------------------------------------------------------------ Belege
def beleg_ordner():
    p = os.path.join(db.data_dir(), "belege")
    os.makedirs(p, exist_ok=True)
    return p


def naechste_belegnr(gid, jahr):
    n = db.q("SELECT COUNT(*) AS n FROM beleg WHERE gemeinschaft_id = ? AND nr LIKE ?", (gid, f"B-{jahr}-%"), one=True)["n"]
    return f"B-{jahr}-{n + 1:04d}"


def beleg_speichern(gid, datei, buchung_id=None, auftrag_id=None):
    """Datei prüfen (PDF, PNG, JPEG am Inhalt erkannt) und ablegen. → beleg_id; wirft ValueError."""
    inhalt = datei.read()
    if not inhalt:
        raise ValueError("Die Datei ist leer.")
    typ = next((t for sig, t in BELEG_TYPEN.items() if inhalt.startswith(sig)), None)
    if not typ:
        raise ValueError("Nur PDF, PNG oder JPEG.")
    name = os.path.basename(datei.filename or "beleg")[:120] or "beleg"
    datei_name = f"{uuid.uuid4().hex}{typ[1]}"
    with open(os.path.join(beleg_ordner(), datei_name), "wb") as fh:
        fh.write(inhalt)
    jahr = _heute().year
    if buchung_id:
        b = db.q("SELECT datum FROM buchung WHERE id = ?", (buchung_id,), one=True)
        jahr = int(b["datum"][:4]) if b else jahr
    return db.ex("INSERT INTO beleg (gemeinschaft_id, buchung_id, auftrag_id, nr, name, datei, mime, groesse, hochgeladen) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (gid, buchung_id, auftrag_id, naechste_belegnr(gid, jahr), name, datei_name, typ[0], len(inhalt),
                  datetime.datetime.now().isoformat(timespec="minutes")))


@bp.post("/beleg/hochladen")
def beleg_hochladen():
    bid, aid = f_int("buchung_id") or None, f_int("auftrag_id") or None
    quelle = db.q("SELECT gemeinschaft_id FROM buchung WHERE id = ?", (bid,), one=True) if bid else \
        db.q("SELECT gemeinschaft_id, buchung_id FROM auftrag WHERE id = ?", (aid,), one=True) if aid else None
    if not quelle:
        abort(400)
    if aid and not bid:
        bid = quelle["buchung_id"]
    dateien = [d for d in request.files.getlist("datei") if d and d.filename]
    if not dateien:
        flash("Keine Datei gewählt.", "fehler")
    for d in dateien:
        try:
            beleg_speichern(quelle["gemeinschaft_id"], d, bid, aid)
            flash(f"Beleg „{d.filename}“ abgelegt.", "ok")
        except ValueError as e:
            flash(f"{d.filename}: {e}", "fehler")
    return redirect(request.form.get("_zurueck") or request.referrer or "/")


@bp.get("/beleg/<int:bid>")
def beleg(bid):
    b = _zeile_oder_404("beleg", bid)
    return beleg_senden(b)


def beleg_senden(b):
    pfad = os.path.join(beleg_ordner(), b["datei"])
    if not os.path.isfile(pfad):
        abort(404)
    r = send_file(pfad, mimetype=b["mime"], download_name=b["name"], as_attachment=False)
    r.headers["X-Content-Type-Options"] = "nosniff"
    return r


@bp.post("/beleg/<int:bid>/loeschen")
def beleg_loeschen(bid):
    b = _zeile_oder_404("beleg", bid)
    try:
        os.remove(os.path.join(beleg_ordner(), b["datei"]))
    except FileNotFoundError:
        pass
    db.ex("DELETE FROM beleg WHERE id = ?", (bid,))
    flash(f"Beleg {b['nr']} gelöscht.", "ok")
    return redirect(request.referrer or "/")


@bp.get("/g/<int:gid>/belege")
def belege(gid):
    g = gemeinschaft_oder_404(gid)
    jahr = int(request.args.get("jahr", _heute().year))
    rows = db.q("SELECT b.*, x.datum, x.betrag_cent, x.text AS b_text, a.nr AS a_nr FROM beleg b LEFT JOIN buchung x ON x.id = b.buchung_id "
                "LEFT JOIN auftrag a ON a.id = b.auftrag_id WHERE b.gemeinschaft_id = ? AND b.nr LIKE ? ORDER BY b.nr DESC",
                (gid, f"B-{jahr}-%"))
    ohne = db.q("SELECT x.*, k.name AS ka_name FROM buchung x LEFT JOIN kostenart k ON k.id = x.kostenart_id "
                "WHERE x.gemeinschaft_id = ? AND x.art = 'kosten' AND x.datum BETWEEN ? AND ? "
                "AND NOT EXISTS (SELECT 1 FROM beleg b WHERE b.buchung_id = x.id) ORDER BY x.datum DESC",
                (gid, f"{jahr}-01-01", f"{jahr}-12-31"))
    return render_template("belege.html", g=g, jahr=jahr, belege=rows, ohne=ohne)


# ------------------------------------------------------------------ DATEV
@bp.get("/g/<int:gid>/datev")
def datev_seite(gid):
    from . import datev
    g = gemeinschaft_oder_404(gid)
    jahr = int(request.args.get("jahr", _heute().year - 1))
    if request.args.get("laden"):
        name, inhalt = datev.buchungsstapel(gid, jahr)
        return Response(inhalt, mimetype="text/csv; charset=windows-1252",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})
    anzahl = db.q("SELECT COUNT(*) AS n FROM buchung WHERE gemeinschaft_id = ? AND datum BETWEEN ? AND ?",
                  (gid, f"{jahr}-01-01", f"{jahr}-12-31"), one=True)["n"]
    return render_template("datev.html", g=g, jahr=jahr, anzahl=anzahl, hinweise=datev.pruefen(gid),
                           kostenarten=db.q("SELECT * FROM kostenart WHERE gemeinschaft_id = ? ORDER BY reihenfolge, id", (gid,)),
                           konten=db.q("SELECT * FROM konto WHERE gemeinschaft_id = ? ORDER BY art, id", (gid,)),
                           standard_bank=datev.STANDARD_BANK)
