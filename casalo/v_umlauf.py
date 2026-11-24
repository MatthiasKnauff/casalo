"""Umlaufbeschlüsse (§ 23 Abs. 3 WEG): Beschluss ohne Versammlung, Stimmabgabe in Textform – im Portal, per E-Mail oder Brief.

Grundsatz: alle Eigentümer müssen zustimmen. Einfache Mehrheit nur, wenn die Versammlung das für diesen Gegenstand
beschlossen hat (§ 23 Abs. 3 Satz 2). Der Beschluss entsteht mit Feststellung und Mitteilung des Ergebnisses.
"""
import datetime

from flask import abort, flash, redirect, render_template, request

from . import db, mail, versammlung as vr
from .v_finanzen import empfaenger_weg
from .v_versammlung import _eigentuemer_der_gemeinschaft, beschluss_eintragen
from .web import bp, f, f_int, gemeinschaft_oder_404

STIMMEN = {"ja": "Ja", "nein": "Nein", "enthaltung": "Enthaltung"}
MEHRHEIT_UMLAUF = {"allstimmig": "alle Eigentümer müssen zustimmen (§ 23 Abs. 3 Satz 1)",
                   "einfach": "Mehrheit der abgegebenen Stimmen (nach Beschluss gem. § 23 Abs. 3 Satz 2)"}


def _u(uid):
    u = db.q("SELECT * FROM umlauf WHERE id = ?", (uid,), one=True)
    if not u:
        abort(404)
    return u


def stand(u):
    berechtigte = _eigentuemer_der_gemeinschaft(u["gemeinschaft_id"])
    stimmen = {r["person_id"]: dict(r) for r in db.q("SELECT * FROM umlauf_stimme WHERE umlauf_id = ?", (u["id"],))}
    zaehl = {k: sum(1 for s in stimmen.values() if s["stimme"] == k) for k in STIMMEN}
    return {"berechtigte": berechtigte, "stimmen": stimmen, "zaehl": zaehl,
            "fehlen": [p for p in berechtigte if p["id"] not in stimmen],
            "vorlaeufig": vr.umlauf_auswerten(u["mehrheit"], {p: s["stimme"] for p, s in stimmen.items()},
                                              {p["id"] for p in berechtigte})}


def stimme_abgeben(uid, pid, stimme, weg):
    db.ex("INSERT INTO umlauf_stimme (umlauf_id, person_id, stimme, zeit, weg) VALUES (?, ?, ?, ?, ?) "
          "ON CONFLICT(umlauf_id, person_id) DO UPDATE SET stimme = excluded.stimme, zeit = excluded.zeit, weg = excluded.weg",
          (uid, pid, stimme, datetime.datetime.now().isoformat(timespec="seconds"), weg))


@bp.route("/g/<int:gid>/umlauf", methods=["GET", "POST"])
def umlaeufe(gid):
    g = gemeinschaft_oder_404(gid)
    if request.method == "POST":
        if not f("titel") or not f("antrag") or not f("frist"):
            flash("Titel, Beschlussantrag und Frist angeben.", "fehler")
            return redirect(request.url)
        mehrheit = "einfach" if f("mehrheit") == "einfach" else "allstimmig"
        if mehrheit == "einfach" and not f("ermaechtigung"):
            flash("Einfache Mehrheit nur mit Ermächtigungsbeschluss der Versammlung – bitte angeben.", "fehler")
            return redirect(request.url)
        uid = db.ex("INSERT INTO umlauf (gemeinschaft_id, titel, antrag, mehrheit, ermaechtigung, frist) VALUES (?, ?, ?, ?, ?, ?)",
                    (gid, f("titel"), f("antrag"), mehrheit, f("ermaechtigung") if mehrheit == "einfach" else "", f("frist")))
        return redirect(f"/umlauf/{uid}")
    liste = [dict(u) | {"stand": stand(u)} for u in db.q("SELECT * FROM umlauf WHERE gemeinschaft_id = ? ORDER BY id DESC", (gid,))]
    return render_template("umlaeufe.html", g=g, liste=liste, mehrheiten=MEHRHEIT_UMLAUF,
                           beschluesse=db.q("SELECT * FROM beschluss WHERE gemeinschaft_id = ? ORDER BY lfd DESC", (gid,)))


@bp.get("/umlauf/<int:uid>")
def umlauf(uid):
    u = _u(uid)
    g = gemeinschaft_oder_404(u["gemeinschaft_id"])
    return render_template("umlauf.html", u=u, g=g, s=stand(u), heute=datetime.date.today().isoformat(), STIMMEN=STIMMEN, mehrheiten=MEHRHEIT_UMLAUF,
                           beschluss=db.q("SELECT * FROM beschluss WHERE id = ?", (u["beschluss_id"],), one=True) if u["beschluss_id"] else None)


@bp.post("/umlauf/<int:uid>/starten")
def umlauf_starten(uid):
    u = _u(uid)
    if u["status"] != "entwurf":
        return redirect(f"/umlauf/{uid}")
    g = gemeinschaft_oder_404(u["gemeinschaft_id"])
    db.ex("UPDATE umlauf SET status = 'laeuft', gestartet = ? WHERE id = ?", (datetime.date.today().isoformat(), uid))
    frist = datetime.date.fromisoformat(u["frist"])
    mails, post, fehler = 0, 0, []
    for p in _eigentuemer_der_gemeinschaft(g["id"]):
        if empfaenger_weg(p) == "post":
            post += 1
            continue
        try:
            mail.senden(p["email"], f"Umlaufbeschluss – WEG {g['name']}: {u['titel']}",
                        f"Guten Tag,\n\ndie Verwaltung bittet um Ihre Stimme zu folgendem Beschlussantrag (ohne Versammlung, "
                        f"§ 23 Abs. 3 WEG):\n\n{u['antrag']}\n\n"
                        f"{'Der Beschluss kommt nur zustande, wenn alle Eigentümer zustimmen.' if u['mehrheit'] == 'allstimmig' else 'Es entscheidet die Mehrheit der abgegebenen Stimmen.'}\n"
                        f"Bitte stimmen Sie bis zum {frist:%d.%m.%Y} im Eigentümerportal ab ({request.host_url}portal) "
                        f"oder antworten Sie auf diese E-Mail mit „Ja“, „Nein“ oder „Enthaltung“.\n\n"
                        f"Mit freundlichen Grüßen\n{db.setting('verwaltung_name')}")
            mails += 1
        except mail.MailFehler as ex:
            fehler.append(str(ex))
            post += 1
    flash(f"Umlaufbeschluss gestartet – {mails} E-Mails" + (f", {post} Eigentümer per Brief anschreiben" if post else "") + ".", "ok")
    for x in fehler[:3]:
        flash(x, "fehler")
    return redirect(f"/umlauf/{uid}")


@bp.post("/umlauf/<int:uid>/stimme")
def umlauf_stimme_buero(uid):
    """Stimme, die per E-Mail oder Brief eingegangen ist, im Büro eintragen."""
    u = _u(uid)
    if u["status"] != "laeuft":
        abort(400)
    pid = f_int("person_id")
    if pid not in {p["id"] for p in _eigentuemer_der_gemeinschaft(u["gemeinschaft_id"])} or f("stimme") not in STIMMEN:
        abort(400)
    stimme_abgeben(uid, pid, f("stimme"), "post" if f("weg") == "post" else "email")
    flash("Stimme eingetragen.", "ok")
    return redirect(f"/umlauf/{uid}")


@bp.post("/umlauf/<int:uid>/beenden")
def umlauf_beenden(uid):
    """Ergebnis feststellen, in die Beschluss-Sammlung eintragen und den Eigentümern mitteilen."""
    u = _u(uid)
    if u["status"] != "laeuft":
        return redirect(f"/umlauf/{uid}")
    s = stand(u)
    heute = datetime.date.today().isoformat()
    if s["fehlen"] and heute <= u["frist"]:
        flash(f"Die Frist läuft bis {datetime.date.fromisoformat(u['frist']):%d.%m.%Y} und es fehlen noch "
              f"{len(s['fehlen'])} Stimmen – vorher kann das Ergebnis nicht festgestellt werden.", "fehler")
        return redirect(f"/umlauf/{uid}")
    ergebnis = s["vorlaeufig"]
    z = s["zaehl"]
    text_ergebnis = (f"{ergebnis} – Umlaufverfahren, {z['ja']} Ja / {z['nein']} Nein / {z['enthaltung']} Enthaltungen, "
                     f"{len(s['fehlen'])} ohne Stimme von {len(s['berechtigte'])} Eigentümern")
    bid = beschluss_eintragen(u["gemeinschaft_id"], heute, "umlauf", f"{u['titel']}: {u['antrag']}", text_ergebnis)
    db.ex("UPDATE umlauf SET status = 'beendet', beendet = ?, ergebnis = ?, beschluss_id = ? WHERE id = ?", (heute, ergebnis, bid, uid))
    g = gemeinschaft_oder_404(u["gemeinschaft_id"])
    n = 0
    for p in s["berechtigte"]:
        if empfaenger_weg(p) != "email":
            continue
        try:
            mail.senden(p["email"], f"Ergebnis Umlaufbeschluss – WEG {g['name']}: {u['titel']}",
                        f"Guten Tag,\n\nder Beschlussantrag „{u['titel']}“ ist {ergebnis}.\n{text_ergebnis}.\n\n"
                        f"Der Beschluss steht unter Nr. {db.q('SELECT lfd FROM beschluss WHERE id = ?', (bid,), one=True)['lfd']} "
                        f"in der Beschluss-Sammlung.\n\nMit freundlichen Grüßen\n{db.setting('verwaltung_name')}")
            n += 1
        except mail.MailFehler:
            pass
    flash(f"Ergebnis: {ergebnis}. In die Beschluss-Sammlung eingetragen" + (f", {n} Eigentümer per E-Mail informiert" if n else "")
          + ". Eigentümer ohne E-Mail bitte per Brief informieren.", "ok")
    return redirect(f"/umlauf/{uid}")


@bp.post("/umlauf/<int:uid>/loeschen")
def umlauf_loeschen(uid):
    u = _u(uid)
    if u["status"] != "entwurf":
        flash("Nur Entwürfe lassen sich löschen.", "fehler")
        return redirect(f"/umlauf/{uid}")
    db.ex("DELETE FROM umlauf WHERE id = ?", (uid,))
    return redirect(f"/g/{u['gemeinschaft_id']}/umlauf")
