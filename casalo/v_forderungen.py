"""Sonderumlagen, Mahnwesen und Bescheinigung nach § 35a EStG."""
import datetime
import json

from flask import abort, flash, redirect, render_template, request

from . import daten, db, forderungen, mail, pdf, rechnen
from .v_finanzen import _empfaenger_liste, _pdf, empfaenger_weg
from .web import bp, f, f_betrag, f_int, gemeinschaft_oder_404, nur_admin

SCHLUESSEL_UMLAGE = [("mea", "Miteigentumsanteile"), ("flaeche", "Wohnfläche"), ("einheiten", "je Einheit gleich")]


def _konto(gid, art="hausgeld"):
    return db.q("SELECT * FROM konto WHERE gemeinschaft_id = ? AND art = ? ORDER BY id LIMIT 1", (gid, art), one=True) \
        or db.q("SELECT * FROM konto WHERE gemeinschaft_id = ? ORDER BY id LIMIT 1", (gid,), one=True)


def _umlage_oder_404(uid):
    u = db.q("SELECT * FROM sonderumlage WHERE id = ?", (uid,), one=True)
    if not u:
        abort(404)
    return u


def _beschluss_text(u):
    if not u["beschluss_id"]:
        return f"Die Eigentümer haben am {datetime.date.fromisoformat(u['beschlossen_am']):%d.%m.%Y} eine Sonderumlage beschlossen." \
            if u["beschlossen_am"] else ""
    b = db.q("SELECT * FROM beschluss WHERE id = ?", (u["beschluss_id"],), one=True)
    return f"Beschluss Nr. {b['lfd']} vom {datetime.date.fromisoformat(b['datum']):%d.%m.%Y}: {b['text']}" if b else ""


def _verteilung_personen(u):
    """[(einheit, person, anteil)] – bei mehreren Eigentümern einer Einheit geht der volle Anteil an jeden (Gesamtschuldner)."""
    anteile = forderungen.umlage_anteile(u)
    return [(e, p, anteile.get(e["id"], 0)) for e, p in _empfaenger_liste(u["gemeinschaft_id"])]


# ------------------------------------------------------------------ Sonderumlagen
@bp.route("/g/<int:gid>/sonderumlagen", methods=["GET", "POST"])
def sonderumlagen(gid):
    g = gemeinschaft_oder_404(gid)
    if request.method == "POST":
        try:
            betrag = abs(f_betrag("betrag"))
        except ValueError:
            return redirect(request.url)
        if not f("titel") or not betrag or not f("faellig"):
            flash("Titel, Betrag und Fälligkeit angeben.", "fehler")
            return redirect(request.url)
        schl = f("schluessel") if f("schluessel") in dict(SCHLUESSEL_UMLAGE) else "mea"
        bid = f_int("beschluss_id") or None
        if bid and not db.q("SELECT 1 FROM beschluss WHERE id = ? AND gemeinschaft_id = ?", (bid, gid), one=True):
            abort(400)
        uid = db.ex("INSERT INTO sonderumlage (gemeinschaft_id, titel, betrag_cent, schluessel, faellig, beschluss_id, beschlossen_am, zweck) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (gid, f("titel"), betrag, schl, f("faellig"), bid, f("beschlossen_am"),
                     "hausgeld" if f("zweck") == "hausgeld" else "ruecklage"))
        flash("Sonderumlage angelegt – Anteile sind verteilt.", "ok")
        return redirect(f"/sonderumlage/{uid}")
    liste = [dict(u) | forderungen.umlage_stand(u) for u in
             db.q("SELECT * FROM sonderumlage WHERE gemeinschaft_id = ? ORDER BY faellig DESC", (gid,))]
    return render_template("sonderumlagen.html", g=g, umlagen=liste, schluessel=SCHLUESSEL_UMLAGE,
                           beschluesse=db.q("SELECT * FROM beschluss WHERE gemeinschaft_id = ? ORDER BY lfd DESC", (gid,)))


@bp.get("/sonderumlage/<int:uid>")
def sonderumlage(uid):
    u = _umlage_oder_404(uid)
    g = gemeinschaft_oder_404(u["gemeinschaft_id"])
    stand = forderungen.umlage_stand(u)
    for z in stand["zeilen"]:
        z["eigentuemer"] = daten.eigentuemer_text(z["einheit"]["id"])
        z["lastschrift"] = any(p["zahlweise"] == "lastschrift" for p in daten.eigentuemer(z["einheit"]["id"]))
    return render_template("sonderumlage.html", g=g, u=u, stand=stand, beschluss=_beschluss_text(u),
                           schluessel=dict(SCHLUESSEL_UMLAGE), heute=datetime.date.today().isoformat())


@bp.post("/sonderumlage/<int:uid>/loeschen")
def sonderumlage_loeschen(uid):
    u = _umlage_oder_404(uid)
    if db.q("SELECT 1 FROM buchung WHERE sonderumlage_id = ?", (uid,), one=True):
        flash("Es sind schon Zahlungen darauf gebucht – erst diese Buchungen löschen.", "fehler")
        return redirect(f"/sonderumlage/{uid}")
    db.ex("DELETE FROM sonderumlage WHERE id = ?", (uid,))
    flash("Sonderumlage gelöscht.", "ok")
    return redirect(f"/g/{u['gemeinschaft_id']}/sonderumlagen")


@bp.get("/sonderumlage/<int:uid>/anforderung.pdf")
def sonderumlage_pdf(uid):
    u = _umlage_oder_404(uid)
    g = gemeinschaft_oder_404(u["gemeinschaft_id"])
    nur_post = request.args.get("post") == "1"
    liste = [x for x in _verteilung_personen(u) if not nur_post or empfaenger_weg(x[1]) == "post"]
    konto = _konto(g["id"], u["zweck"])
    return _pdf(pdf.umlage_anforderungen(g, u, liste, konto, _beschluss_text(u)), f"Sonderumlage-{u['id']}.pdf")


@bp.post("/sonderumlage/<int:uid>/versand")
def sonderumlage_versand(uid):
    u = _umlage_oder_404(uid)
    g = gemeinschaft_oder_404(u["gemeinschaft_id"])
    konto = _konto(g["id"], u["zweck"])
    text = _beschluss_text(u)
    mails, post, fehler = 0, 0, []
    for e, p, anteil in _verteilung_personen(u):
        if empfaenger_weg(p) == "post":
            post += 1
            continue
        try:
            mail.senden(p["email"], f"Sonderumlage „{u['titel']}“ – {g['name']}, Einheit {e['nr']}",
                        f"Guten Tag,\n\nanbei erhalten Sie die Anforderung der Sonderumlage „{u['titel']}“ für Ihre Einheit "
                        f"{e['nr']}: {rechnen.fmt(anteil)} €, fällig am {datetime.date.fromisoformat(u['faellig']):%d.%m.%Y}.\n\n"
                        f"Mit freundlichen Grüßen\n{db.setting('verwaltung_name')}",
                        [(f"Sonderumlage-{e['nr']}.pdf", pdf.umlage_anforderungen(g, u, [(e, p, anteil)], konto, text), "application/pdf")])
            mails += 1
        except mail.MailFehler as ex:
            fehler.append(str(ex))
    db.ex("UPDATE sonderumlage SET angefordert = ? WHERE id = ?", (datetime.date.today().isoformat(), uid))
    if mails:
        flash(f"{mails} Anforderungen per E-Mail versandt.", "ok")
    if post:
        flash(f"{post} Eigentümer bekommen Post – Druckdatei „Für den Postversand“.", "ok")
    for x in fehler[:5]:
        flash(x, "fehler")
    return redirect(f"/sonderumlage/{uid}")


@bp.post("/sonderumlage/<int:uid>/lastschrift")
def sonderumlage_lastschrift(uid):
    from flask import Response

    from . import bank
    u = _umlage_oder_404(uid)
    g = gemeinschaft_oder_404(u["gemeinschaft_id"])
    konto = _konto(g["id"], u["zweck"])
    faellig = max(datetime.date.fromisoformat(u["faellig"]), datetime.date.today() + datetime.timedelta(days=2))
    offen = {z["einheit"]["id"]: z["offen"] for z in forderungen.umlage_stand(u)["zeilen"]}
    posten = []
    for e in daten.einheiten(g["id"]):
        ps = [p for p in daten.eigentuemer(e["id"]) if p["zahlweise"] == "lastschrift"]
        if not ps or offen.get(e["id"], 0) <= 0:
            continue
        p = ps[0]                                   # eine Lastschrift je Einheit
        posten.append({"name": daten.name(p), "iban": p["iban"], "bic": p["bic"], "betrag_cent": offen[e["id"]],
                       "mandat_ref": p["mandat_ref"], "mandat_datum": p["mandat_datum"],
                       "zweck": f"Sonderumlage {u['titel']} Einheit {e['nr']}"[:140]})
    try:
        xml = bank.lastschrift_xml({"name": f"WEG {g['name']}", "iban": konto["iban"] if konto else "", "bic": konto["bic"] if konto else "",
                                    "glaeubiger_id": g["glaeubiger_id"]}, posten, faellig)
    except ValueError as e:
        flash(str(e), "fehler")
        return redirect(f"/sonderumlage/{uid}")
    return Response(xml, mimetype="application/xml",
                    headers={"Content-Disposition": f'attachment; filename="Lastschrift-Sonderumlage-{uid}.xml"'})


# ------------------------------------------------------------------ Mahnwesen
@bp.route("/g/<int:gid>/mahnwesen", methods=["GET", "POST"])
def mahnwesen(gid):
    g = gemeinschaft_oder_404(gid)
    heute = datetime.date.today()
    if request.method == "POST":
        gewaehlt = [int(x) for x in request.form.getlist("einheit") if x.isdigit()]
        neu = []
        for eid in gewaehlt:
            e = db.q("SELECT * FROM einheit WHERE id = ? AND gemeinschaft_id = ?", (eid, gid), one=True)
            if not e:
                continue
            v = forderungen.mahnvorschlag(eid, heute)
            if not v["stufe"]:
                continue
            for p in daten.eigentuemer(eid) or [None]:
                weg = empfaenger_weg(p) if p else "post"
                neu.append(forderungen.mahnung_anlegen(eid, v["stufe"], p, heute, weg))
        mails, post, fehler = _mahnungen_versenden(g, neu)
        if mails:
            flash(f"{mails} Mahnschreiben per E-Mail versandt.", "ok")
        if post:
            flash(f"{post} Mahnschreiben für die Post – Druckdatei unten.", "ok")
        if not neu:
            flash("Keine Einheit gewählt, für die eine Mahnung fällig ist.", "fehler")
        for x in fehler[:5]:
            flash(x, "fehler")
        return redirect(f"/g/{gid}/mahnwesen" + (f"?post={','.join(map(str, neu))}" if post else ""))
    zeilen = []
    for e in daten.einheiten(gid):
        v = forderungen.mahnvorschlag(e["id"], heute)
        if v["summe"] <= 0 and not v["letzte"]:
            continue
        zeilen.append({"einheit": e, "eigentuemer": daten.eigentuemer_text(e["id"]), **v})
    verlauf = db.q("SELECT m.*, e.nr AS e_nr, p.vorname, p.nachname, p.firma FROM mahnung m JOIN einheit e ON e.id = m.einheit_id "
                   "LEFT JOIN person p ON p.id = m.person_id WHERE m.gemeinschaft_id = ? ORDER BY m.datum DESC, m.id DESC LIMIT 200", (gid,))
    post_ids = request.args.get("post", "")
    return render_template("mahnwesen.html", g=g, zeilen=zeilen, verlauf=verlauf, stufen=forderungen.STUFEN,
                           einstellungen=forderungen.mahn_einstellungen(), post_ids=post_ids)


def _mahnungen_versenden(g, ids):
    konto = _konto(g["id"])
    mails = post = 0
    fehler = []
    for mid in ids:
        m = db.q("SELECT * FROM mahnung WHERE id = ?", (mid,), one=True)
        if m["versand"] != "email":
            post += 1
            continue
        e = db.q("SELECT * FROM einheit WHERE id = ?", (m["einheit_id"],), one=True)
        p = dict(db.q("SELECT * FROM person WHERE id = ?", (m["person_id"],), one=True))
        try:
            mail.senden(p["email"], f"{forderungen.STUFEN[m['stufe']]} – {g['name']}, Einheit {e['nr']}",
                        f"Guten Tag,\n\nanbei erhalten Sie eine {forderungen.STUFEN[m['stufe']]} zu offenen Zahlungen für Ihre "
                        f"Einheit {e['nr']}. Haben Sie inzwischen gezahlt, ist das Schreiben gegenstandslos.\n\n"
                        f"Mit freundlichen Grüßen\n{db.setting('verwaltung_name')}",
                        [(f"{forderungen.STUFEN[m['stufe']].replace(' ', '-')}-{e['nr']}.pdf",
                          pdf.mahnungen(g, [(m, e, p)], konto), "application/pdf")])
            mails += 1
        except mail.MailFehler as ex:
            db.ex("UPDATE mahnung SET versand = 'post' WHERE id = ?", (mid,))
            fehler.append(str(ex) + " – als Brief vorgemerkt.")
            post += 1
    return mails, post, fehler


@bp.get("/g/<int:gid>/mahnungen.pdf")
def mahnungen_pdf(gid):
    g = gemeinschaft_oder_404(gid)
    ids = [int(x) for x in request.args.get("ids", "").split(",") if x.isdigit()]
    liste = []
    for mid in ids:
        m = db.q("SELECT * FROM mahnung WHERE id = ? AND gemeinschaft_id = ?", (mid, gid), one=True)
        if not m:
            continue
        e = db.q("SELECT * FROM einheit WHERE id = ?", (m["einheit_id"],), one=True)
        p = db.q("SELECT * FROM person WHERE id = ?", (m["person_id"],), one=True) if m["person_id"] else None
        liste.append((m, e, dict(p) if p else None))
    return _pdf(pdf.mahnungen(g, liste, _konto(gid)), "Mahnungen.pdf")


@bp.post("/mahnung/<int:mid>/status")
def mahnung_status(mid):
    m = db.q("SELECT * FROM mahnung WHERE id = ?", (mid,), one=True)
    if not m:
        abort(404)
    st = f("status")
    if st not in ("offen", "bezahlt", "storniert"):
        abort(400)
    db.ex("UPDATE mahnung SET status = ? WHERE id = ?", (st, mid))
    flash({"storniert": "Mahnung storniert.", "bezahlt": "Als bezahlt markiert.", "offen": "Wieder offen."}[st], "ok")
    return redirect(f"/g/{m['gemeinschaft_id']}/mahnwesen")


@bp.post("/mahnwesen/einstellungen")
@nur_admin
def mahn_einstellungen():
    try:
        db.set_setting("mahn_frist_tage", str(max(7, min(60, f_int("frist_tage", 14)))))
        db.set_setting("mahn_schwelle_cent", str(abs(rechnen.parse_betrag(f("schwelle") or "0"))))
        for s in (1, 2, 3):
            db.set_setting(f"mahn_gebuehr_{s}", str(abs(rechnen.parse_betrag(f(f"gebuehr_{s}") or "0"))))
    except ValueError as e:
        flash(str(e), "fehler")
        return redirect(request.referrer or "/")
    flash("Mahn-Einstellungen gespeichert.", "ok")
    return redirect(request.referrer or "/")


# ------------------------------------------------------------------ § 35a EStG
@bp.get("/g/<int:gid>/steuer35a/<int:jahr>")
def steuer35a(gid, jahr):
    g = gemeinschaft_oder_404(gid)
    b = forderungen.bescheinigung_35a(gid, jahr)
    ein = daten.einheiten(gid)
    zeilen = [{"einheit": e, "eigentuemer": daten.eigentuemer_text(e["id"]), **b["einheiten"].get(e["id"], {"summen": {}})}
              for e in ein]
    return render_template("steuer35a.html", g=g, jahr=jahr, b=b, zeilen=zeilen, kat=forderungen.KAT_35A,
                           kostenarten=db.q("SELECT * FROM kostenart WHERE gemeinschaft_id = ? ORDER BY reihenfolge, id", (gid,)))


def bescheinigung_pdf_fuer(g, jahr, e, p, b=None):
    b = b or forderungen.bescheinigung_35a(g["id"], jahr)
    d = b["einheiten"].get(e["id"], {"posten": [], "summen": {k: 0 for k in forderungen.KAT_35A}})
    return pdf.bescheinigung_35a_pdf(g, jahr, e, p, d)


@bp.get("/g/<int:gid>/steuer35a/<int:jahr>/<int:eid>.pdf")
def steuer35a_pdf(gid, jahr, eid):
    g = gemeinschaft_oder_404(gid)
    e = db.q("SELECT * FROM einheit WHERE id = ? AND gemeinschaft_id = ?", (eid, gid), one=True)
    if not e:
        abort(404)
    p = (daten.eigentuemer(eid) or [None])[0]
    return _pdf(bescheinigung_pdf_fuer(g, jahr, dict(e), p), f"Bescheinigung-35a-{jahr}-{e['nr']}.pdf")


@bp.get("/g/<int:gid>/steuer35a/<int:jahr>/alle.pdf")
def steuer35a_alle_pdf(gid, jahr):
    g = gemeinschaft_oder_404(gid)
    b = forderungen.bescheinigung_35a(gid, jahr)
    nur_post = request.args.get("post") == "1"
    leer = {"posten": [], "summen": {k: 0 for k in forderungen.KAT_35A}}
    liste = [(e, p, b["einheiten"].get(e["id"], leer)) for e, p in _empfaenger_liste(gid)
             if not nur_post or empfaenger_weg(p) == "post"]
    return _pdf(pdf.bescheinigungen_35a_pdf(g, jahr, liste), f"Bescheinigungen-35a-{jahr}.pdf")
