"""Eigentümerportal: Anmeldung per E-Mail-Link, eigene Einheiten, Zahlungen, Dokumente, Meldungen an die Verwaltung.

Getrennt von der Anmeldung der Verwaltung (Sitzungsschlüssel „portal_pid“ statt „uid“). Eigentümer sehen nur, was zu
ihren aktuellen Einheiten gehört; Dokumente erst, wenn die Verwaltung sie versandt bzw. freigegeben hat.
"""
import datetime
import hashlib
import secrets

from flask import Response, abort, flash, redirect, render_template, request, session

from . import daten, db, forderungen, mail, pdf
from .web import bp, csrf_token, f, f_int

GUELTIG_MIN = 30


def _hash(t):
    return hashlib.sha256(t.encode()).hexdigest()


def _person():
    pid = session.get("portal_pid")
    if not pid or db.setting("portal_aktiv") != "1":
        return None
    return db.q("SELECT * FROM person WHERE id = ?", (pid,), one=True)


def _pruefe_csrf():
    tok = request.form.get("_csrf", "")
    if not tok or not secrets.compare_digest(tok, session.get("csrf", "")):
        abort(400, "Sitzung abgelaufen – bitte Seite neu laden.")


def _einheiten(pid):
    """Aktuelle Einheiten der Person (Eigentum ohne Ende)."""
    return [dict(r) for r in db.q("SELECT e.*, g.name AS g_name, g.strasse AS g_strasse, g.plz AS g_plz, g.ort AS g_ort FROM eigentum t "
                                  "JOIN einheit e ON e.id = t.einheit_id JOIN gemeinschaft g ON g.id = e.gemeinschaft_id "
                                  "WHERE t.person_id = ? AND (t.bis IS NULL OR t.bis = '' OR t.bis >= ?) ORDER BY g.name, e.nr",
                                  (pid, datetime.date.today().isoformat()))]


def _eigene_einheit(pid, eid):
    e = next((x for x in _einheiten(pid) if x["id"] == eid), None)
    if not e:
        abort(404)
    return e


def freigegeben(gid, jahr):
    return bool(db.setting(f"portal_abrechnung_{gid}_{jahr}", ""))


# ------------------------------------------------------------------ Anmeldung
@bp.route("/portal/anmelden", methods=["GET", "POST"])
def portal_anmelden():
    if db.setting("portal_aktiv") != "1":
        return render_template("portal_anmelden.html", aus=True, csrf_token=csrf_token())
    if request.method == "POST":
        _pruefe_csrf()
        email = f("email").lower()
        p = db.q("SELECT * FROM person WHERE lower(email) = ? AND email != ''", (email,), one=True) if "@" in email else None
        if p and _einheiten(p["id"]):
            token = secrets.token_urlsafe(32)
            db.ex("INSERT INTO portal_zugang (person_id, token_hash, gueltig_bis) VALUES (?, ?, ?)",
                  (p["id"], _hash(token), (datetime.datetime.now() + datetime.timedelta(minutes=GUELTIG_MIN)).isoformat(timespec="seconds")))
            link = f"{request.host_url}portal/link/{token}"
            try:
                mail.senden(p["email"], "Ihr Zugang zum Eigentümerportal",
                            f"Guten Tag,\n\nmit diesem Link melden Sie sich im Eigentümerportal an (gültig {GUELTIG_MIN} Minuten, "
                            f"nur einmal verwendbar):\n\n{link}\n\nHaben Sie das nicht angefordert, können Sie diese E-Mail ignorieren.\n\n"
                            f"Mit freundlichen Grüßen\n{db.setting('verwaltung_name')}")
            except mail.MailFehler:
                pass                         # gleiche Antwort wie bei unbekannter Adresse – verrät nicht, wer Eigentümer ist
        return render_template("portal_anmelden.html", gesendet=True, csrf_token=csrf_token())
    return render_template("portal_anmelden.html", csrf_token=csrf_token())


@bp.get("/portal/link/<token>")
def portal_link(token):
    z = db.q("SELECT * FROM portal_zugang WHERE token_hash = ?", (_hash(token),), one=True)
    if not z or z["benutzt"] or z["gueltig_bis"] < datetime.datetime.now().isoformat(timespec="seconds"):
        return render_template("portal_anmelden.html", abgelaufen=True, csrf_token=csrf_token())
    db.ex("UPDATE portal_zugang SET benutzt = ? WHERE id = ?", (datetime.datetime.now().isoformat(timespec="seconds"), z["id"]))
    uid = session.get("uid")                 # Anmeldung der Verwaltung im selben Browser bleibt erhalten
    session.clear()
    if uid:
        session["uid"] = uid
    session.permanent = True
    session["portal_pid"] = z["person_id"]
    return redirect("/portal")


@bp.post("/portal/abmelden")
def portal_abmelden():
    _pruefe_csrf()
    session.pop("portal_pid", None)
    return redirect("/portal/anmelden")


# ------------------------------------------------------------------ Übersicht
@bp.get("/portal")
def portal():
    p = _person()
    if not p:
        return redirect("/portal/anmelden")
    heute = datetime.date.today()
    einheiten = _einheiten(p["id"])
    for e in einheiten:
        gid = e["gemeinschaft_id"]
        wp = daten.wirtschaftsplan(gid, heute.year)
        e["monat"] = wp["einheiten"][e["id"]]["monat"] if wp["wp"] else None
        t = db.q("SELECT von FROM eigentum WHERE einheit_id = ? AND person_id = ? AND bis IS NULL", (e["id"], p["id"]), one=True)
        seit = t["von"] if t and t["von"] else None
        e["posten"] = forderungen.offene_posten(e["id"], heute, seit)      # nur die eigene Zeit als Eigentümer
        e["offen"] = sum(x["betrag"] for x in e["posten"])
        konto = db.q("SELECT * FROM konto WHERE gemeinschaft_id = ? AND art = 'hausgeld' ORDER BY id LIMIT 1", (gid,), one=True)
        e["konto"] = dict(konto) if konto else None
        e["dokumente"] = _dokumente(e, heute)
        e["beschluesse"] = db.q("SELECT * FROM beschluss WHERE gemeinschaft_id = ? ORDER BY lfd DESC LIMIT 15", (gid,))
        e["versammlungen"] = db.q("SELECT * FROM versammlung WHERE gemeinschaft_id = ? AND beginn >= ? AND einladung_versandt != '' "
                                  "ORDER BY beginn", (gid, heute.isoformat()))
        e["umlaeufe"] = [dict(u) | {"meine": next((r["stimme"] for r in db.q("SELECT stimme FROM umlauf_stimme WHERE umlauf_id = ? AND person_id = ?",
                                                                            (u["id"], p["id"]))), None)}
                         for u in db.q("SELECT * FROM umlauf WHERE gemeinschaft_id = ? AND status = 'laeuft' ORDER BY frist", (gid,))]
        e["dokumente"] += [{"titel": d["titel"], "datum": d["datum"] or d["hochgeladen"][:10], "url": f"/portal/datei/{d['id']}"}
                           for d in db.q("SELECT * FROM dokument WHERE gemeinschaft_id = ? AND portal = 1", (gid,))]
        e["dokumente"].sort(key=lambda d: d["datum"], reverse=True)
        e["vorgaenge"] = db.q("SELECT * FROM vorgang WHERE einheit_id = ? AND person_id = ? ORDER BY angelegt DESC LIMIT 5", (e["id"], p["id"]))
        e["belege"] = db.q("SELECT b.*, x.datum, x.betrag_cent, x.text AS b_text FROM beleg b LEFT JOIN buchung x ON x.id = b.buchung_id "
                           "WHERE b.gemeinschaft_id = ? AND (b.nr LIKE ? OR b.nr LIKE ?) ORDER BY b.nr DESC",
                           (gid, f"B-{heute.year}-%", f"B-{heute.year - 1}-%")) if db.setting("portal_belege") == "1" else []
    return render_template("portal.html", p=p, einheiten=einheiten, csrf_token=csrf_token(), heute=heute)


def _dokumente(e, heute):
    gid, eid = e["gemeinschaft_id"], e["id"]
    out = []
    for jahr in range(heute.year - 3, heute.year + 2):
        wp = db.q("SELECT * FROM wirtschaftsplan WHERE gemeinschaft_id = ? AND jahr = ? AND beschlossen_am != ''", (gid, jahr), one=True)
        if wp:
            out.append({"titel": f"Wirtschaftsplan {jahr}", "datum": wp["beschlossen_am"], "url": f"/portal/dok/{eid}/wp/{jahr}"})
        if freigegeben(gid, jahr):
            out.append({"titel": f"Jahresabrechnung {jahr} – Einheit {e['nr']}", "datum": db.setting(f"portal_abrechnung_{gid}_{jahr}"),
                        "url": f"/portal/dok/{eid}/abrechnung/{jahr}"})
            out.append({"titel": f"Bescheinigung § 35a EStG {jahr}", "datum": db.setting(f"portal_abrechnung_{gid}_{jahr}"),
                        "url": f"/portal/dok/{eid}/35a/{jahr}"})
    for v in db.q("SELECT * FROM versammlung WHERE gemeinschaft_id = ? AND einladung_versandt != '' ORDER BY beginn DESC LIMIT 6", (gid,)):
        out.append({"titel": f"Einladung Versammlung {datetime.datetime.fromisoformat(v['beginn']):%d.%m.%Y}",
                    "datum": v["einladung_versandt"][:10], "url": f"/portal/dok/{eid}/einladung/{v['id']}"})
        if v["status"] == "durchgefuehrt":
            out.append({"titel": f"Niederschrift Versammlung {datetime.datetime.fromisoformat(v['beginn']):%d.%m.%Y}",
                        "datum": v["beginn"][:10], "url": f"/portal/dok/{eid}/niederschrift/{v['id']}"})
    for u in db.q("SELECT * FROM sonderumlage WHERE gemeinschaft_id = ? AND angefordert != '' ORDER BY faellig DESC", (gid,)):
        out.append({"titel": f"Sonderumlage „{u['titel']}“", "datum": u["angefordert"], "url": f"/portal/dok/{eid}/umlage/{u['id']}"})
    out.sort(key=lambda d: d["datum"], reverse=True)
    return out


def _pdf_antwort(inhalt, name):
    return Response(inhalt, mimetype="application/pdf", headers={"Content-Disposition": f'inline; filename="{name}"'})


@bp.get("/portal/dok/<int:eid>/<art>/<int:nr>")
def portal_dokument(eid, art, nr):
    p = _person()
    if not p:
        return redirect("/portal/anmelden")
    e = _eigene_einheit(p["id"], eid)
    gid = e["gemeinschaft_id"]
    g = daten.gemeinschaft(gid)
    person = next((x for x in daten.eigentuemer(eid) if x["id"] == p["id"]), dict(p))
    if art == "wp":
        if not db.q("SELECT 1 FROM wirtschaftsplan WHERE gemeinschaft_id = ? AND jahr = ? AND beschlossen_am != ''", (gid, nr), one=True):
            abort(404)
        einheiten = daten.einheiten(gid)
        for x in einheiten:
            x["eigentuemer"] = ""                    # keine Namen anderer Eigentümer im Portal
        return _pdf_antwort(pdf.wirtschaftsplan(g, nr, daten.wirtschaftsplan(gid, nr), daten.kostenarten(gid), einheiten),
                            f"Wirtschaftsplan-{nr}.pdf")
    if art in ("abrechnung", "35a"):
        if not freigegeben(gid, nr):
            abort(404)
        if art == "abrechnung":
            a = daten.jahresabrechnung(gid, nr)
            return _pdf_antwort(pdf.einzelabrechnung(g, e, person, a, daten.kostenarten(gid)), f"Einzelabrechnung-{nr}-{e['nr']}.pdf")
        b = forderungen.bescheinigung_35a(gid, nr)
        d = b["einheiten"].get(eid, {"posten": [], "summen": {k: 0 for k in forderungen.KAT_35A}})
        return _pdf_antwort(pdf.bescheinigung_35a_pdf(g, nr, e, person, d), f"Bescheinigung-35a-{nr}.pdf")
    if art in ("einladung", "niederschrift"):
        v = db.q("SELECT * FROM versammlung WHERE id = ? AND gemeinschaft_id = ? AND einladung_versandt != ''", (nr, gid), one=True)
        if not v or (art == "niederschrift" and v["status"] != "durchgefuehrt"):
            abort(404)
        tops = db.q("SELECT * FROM top WHERE versammlung_id = ? ORDER BY nr", (nr,))
        if art == "einladung":
            return _pdf_antwort(pdf.einladung_pdf(g, v, tops, person), "Einladung.pdf")
        return _pdf_antwort(pdf.protokoll_pdf(g, v, tops, db.q("SELECT * FROM beschluss WHERE versammlung_id = ?", (nr,))),
                            "Niederschrift.pdf")
    if art == "umlage":
        u = db.q("SELECT * FROM sonderumlage WHERE id = ? AND gemeinschaft_id = ? AND angefordert != ''", (nr, gid), one=True)
        if not u:
            abort(404)
        from .v_forderungen import _beschluss_text, _konto
        anteil = forderungen.umlage_anteile(u).get(eid, 0)
        return _pdf_antwort(pdf.umlage_anforderungen(g, u, [(e, person, anteil)], _konto(gid, u["zweck"]), _beschluss_text(u)),
                            f"Sonderumlage-{e['nr']}.pdf")
    abort(404)


@bp.get("/portal/beleg/<int:bid>")
def portal_beleg(bid):
    p = _person()
    if not p or db.setting("portal_belege") != "1":
        abort(404)
    b = db.q("SELECT * FROM beleg WHERE id = ?", (bid,), one=True)
    if not b or b["gemeinschaft_id"] not in {e["gemeinschaft_id"] for e in _einheiten(p["id"])}:
        abort(404)
    from .v_betrieb import beleg_senden
    return beleg_senden(b)


# ------------------------------------------------------------------ Meldungen an die Verwaltung
@bp.post("/portal/meldung")
def portal_meldung():
    p = _person()
    if not p:
        return redirect("/portal/anmelden")
    _pruefe_csrf()
    e = _eigene_einheit(p["id"], f_int("einheit_id"))
    art = f("art")
    titel = f("titel")[:120]
    if art == "kontakt":
        titel = "Kontaktdaten ändern"
    if not titel or not f("text"):
        flash("Bitte Betreff und Beschreibung angeben.", "fehler")
        return redirect("/portal#melden")
    db.ex("INSERT INTO vorgang (gemeinschaft_id, einheit_id, person_id, titel, text, quelle, angelegt) VALUES (?, ?, ?, ?, ?, 'portal', ?)",
          (e["gemeinschaft_id"], e["id"], p["id"], titel, f("text")[:4000], datetime.datetime.now().isoformat(timespec="minutes")))
    flash("Danke – Ihre Nachricht ist bei der Verwaltung angekommen.", "ok")
    return redirect("/portal")


@bp.get("/portal/datei/<int:did>")
def portal_datei(did):
    p = _person()
    if not p:
        return redirect("/portal/anmelden")
    d = db.q("SELECT * FROM dokument WHERE id = ? AND portal = 1", (did,), one=True)
    if not d or d["gemeinschaft_id"] not in {e["gemeinschaft_id"] for e in _einheiten(p["id"])}:
        abort(404)
    from .v_betrieb import beleg_senden
    return beleg_senden(d)


@bp.post("/portal/umlauf/<int:uid>")
def portal_umlauf(uid):
    """Stimme im Portal; die Bestätigung per E-Mail hält die Erklärung in Textform fest (§ 126b BGB)."""
    p = _person()
    if not p:
        return redirect("/portal/anmelden")
    _pruefe_csrf()
    from .v_umlauf import STIMMEN, stimme_abgeben
    from .v_versammlung import _eigentuemer_der_gemeinschaft
    u = db.q("SELECT * FROM umlauf WHERE id = ? AND status = 'laeuft'", (uid,), one=True)
    if not u or p["id"] not in {x["id"] for x in _eigentuemer_der_gemeinschaft(u["gemeinschaft_id"])} or f("stimme") not in STIMMEN:
        abort(404)
    stimme_abgeben(uid, p["id"], f("stimme"), "portal")
    g = daten.gemeinschaft(u["gemeinschaft_id"])
    try:
        mail.senden(p["email"], f"Ihre Stimme: Umlaufbeschluss – WEG {g['name']}",
                    f"Guten Tag,\n\nhiermit bestätigen wir Ihre Erklärung vom {datetime.datetime.now():%d.%m.%Y, %H:%M} Uhr:\n\n"
                    f"{daten.name(dict(p))} stimmt dem Beschlussantrag „{u['titel']}“ – {u['antrag']} – mit „{STIMMEN[f('stimme')]}“.\n\n"
                    f"Bis zum Ende der Frist können Sie Ihre Stimme im Portal ändern.\n\nMit freundlichen Grüßen\n{db.setting('verwaltung_name')}")
    except mail.MailFehler:
        pass
    flash(f"Ihre Stimme „{STIMMEN[f('stimme')]}“ ist gespeichert.", "ok")
    return redirect("/portal")
