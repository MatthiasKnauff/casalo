"""Anmeldung, Übersicht, Vorgänge, Einstellungen."""
import datetime

from flask import flash, jsonify, redirect, render_template, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from . import VERSION, daten, db, mail, versammlung
from .web import aktueller_benutzer, bp, f, nur_admin, zurueck


@bp.get("/health")
def health():
    import os
    return jsonify(status="ok", version=VERSION, umgebung=os.environ.get("APP_ENV", "dev"))


@bp.route("/anmelden", methods=["GET", "POST"])
def anmelden():
    erststart = db.q("SELECT COUNT(*) AS n FROM benutzer", one=True)["n"] == 0
    if request.method == "POST":
        email, pw = f("email").lower(), request.form.get("passwort", "")
        if erststart:
            if not f("name") or "@" not in email or len(pw) < 10 or pw != request.form.get("passwort2"):
                flash("Name, E-Mail und ein Passwort mit mindestens 10 Zeichen (zweimal gleich) angeben.", "fehler")
                return render_template("anmelden.html", erststart=True)
            uid = db.ex("INSERT INTO benutzer (name, email, pw_hash, rolle) VALUES (?, ?, ?, 'admin')",
                        (f("name"), email, generate_password_hash(pw)))
        else:
            u = db.q("SELECT * FROM benutzer WHERE email = ?", (email,), one=True)
            if not u or not check_password_hash(u["pw_hash"], pw):
                flash("E-Mail oder Passwort falsch.", "fehler")
                return render_template("anmelden.html", erststart=False)
            uid = u["id"]
        session.clear()
        session.permanent = True
        session["uid"] = uid
        nxt = request.args.get("next", "/")
        return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else "/")
    return render_template("anmelden.html", erststart=erststart)


@bp.get("/abmelden")
def abmelden():
    session.clear()
    return redirect("/anmelden")


@bp.get("/")
def uebersicht():
    heute = datetime.date.today()
    gs = db.q("SELECT * FROM gemeinschaft ORDER BY name")
    kz = {
        "gemeinschaften": len(gs),
        "einheiten": db.q("SELECT COUNT(*) AS n FROM einheit", one=True)["n"],
        "vorgaenge": db.q("SELECT COUNT(*) AS n FROM vorgang WHERE status = 'offen'", one=True)["n"],
        "umsaetze": db.q("SELECT COUNT(*) AS n FROM umsatz WHERE status = 'neu'", one=True)["n"],
    }
    rueck_n, rueck_summe = 0, 0
    for g in gs:
        for eid, c in daten.offene_hausgelder(g["id"], heute.year).items():
            if c > 0:
                rueck_n += 1
                rueck_summe += c
    kz["rueckstaende"], kz["rueckstand_summe"] = rueck_n, rueck_summe
    anrufe = db.q("SELECT a.*, p.vorname, p.nachname, p.firma FROM anruf a LEFT JOIN person p ON p.id = a.person_id "
                  "WHERE a.beginn >= ? ORDER BY a.beginn DESC LIMIT 8", (heute.isoformat(),))
    fristen = []
    for v in db.q("SELECT v.*, g.name AS g_name FROM versammlung v JOIN gemeinschaft g ON g.id = v.gemeinschaft_id "
                  "WHERE v.beginn >= ? ORDER BY v.beginn", (heute.isoformat(),)):
        art, text = versammlung.frist_status(versammlung.parse_beginn(v["beginn"]), v["einladung_versandt"])
        fristen.append({"datum": versammlung.einladung_bis(versammlung.parse_beginn(v["beginn"])),
                        "was": f"Versammlung {v['g_name']}: {text}", "art": art, "link": f"/versammlung/{v['id']}"})
    for g in gs:
        if not db.q("SELECT 1 FROM wirtschaftsplan WHERE gemeinschaft_id = ? AND jahr = ?", (g["id"], heute.year + 1), one=True) \
                and heute.month >= 10:
            fristen.append({"datum": datetime.date(heute.year, 12, 31), "art": "warnung",
                            "was": f"{g['name']}: Wirtschaftsplan {heute.year + 1} fehlt noch",
                            "link": f"/g/{g['id']}/wirtschaftsplan/{heute.year + 1}"})
    fristen.sort(key=lambda x: x["datum"])
    konten_tan = db.q("SELECT k.*, g.name AS g_name FROM konto k JOIN gemeinschaft g ON g.id = k.gemeinschaft_id "
                      "WHERE k.fints_zustand != ''")
    vorgaenge = db.q("SELECT v.*, g.name AS g_name FROM vorgang v LEFT JOIN gemeinschaft g ON g.id = v.gemeinschaft_id "
                     "WHERE v.status = 'offen' ORDER BY (v.notfallart != '') DESC, v.angelegt DESC LIMIT 8")
    return render_template("uebersicht.html", kz=kz, anrufe=anrufe, fristen=fristen[:8], konten_tan=konten_tan,
                           vorgaenge=vorgaenge, heute=heute)


# ------------------------------------------------------------------ Vorgänge
@bp.get("/vorgaenge")
def vorgaenge():
    status = request.args.get("status", "offen")
    rows = db.q("SELECT v.*, g.name AS g_name, e.nr AS e_nr, p.vorname, p.nachname, p.firma FROM vorgang v "
                "LEFT JOIN gemeinschaft g ON g.id = v.gemeinschaft_id LEFT JOIN einheit e ON e.id = v.einheit_id "
                "LEFT JOIN person p ON p.id = v.person_id WHERE v.status = ? ORDER BY (v.notfallart != '') DESC, "
                "v.angelegt DESC", (status,))
    gs = db.q("SELECT id, name FROM gemeinschaft ORDER BY name")
    return render_template("vorgaenge.html", vorgaenge=rows, status=status, gemeinschaften=gs)


@bp.post("/vorgaenge")
def vorgang_neu():
    if not f("titel"):
        flash("Bitte einen Titel angeben.", "fehler")
        return zurueck("/vorgaenge")
    db.ex("INSERT INTO vorgang (gemeinschaft_id, titel, text, angelegt) VALUES (?, ?, ?, ?)",
          (request.form.get("gemeinschaft_id") or None, f("titel"), f("text"),
           datetime.datetime.now().isoformat(timespec="seconds")))
    flash("Vorgang angelegt.", "ok")
    return zurueck("/vorgaenge")


@bp.post("/vorgang/<int:vid>/status")
def vorgang_status(vid):
    db.ex("UPDATE vorgang SET status = ? WHERE id = ?", ("erledigt" if f("status") == "erledigt" else "offen", vid))
    return zurueck("/vorgaenge")


# ------------------------------------------------------------------ Einstellungen
@bp.route("/einstellungen", methods=["GET", "POST"])
@nur_admin
def einstellungen():
    if request.method == "POST":
        bereich = f("bereich")
        if bereich == "verwaltung":
            for k in ("verwaltung_name", "verwaltung_anschrift", "fints_produkt_id"):
                db.set_setting(k, f(k))
        elif bereich == "mail":
            for k in ("smtp_host", "smtp_port", "smtp_user", "smtp_absender"):
                db.set_setting(k, f(k))
            if request.form.get("smtp_pass"):
                db.set_setting("smtp_pass", db.encrypt(request.form["smtp_pass"]))
            if f("test_an"):
                try:
                    mail.senden(f("test_an"), "Casalo – Testnachricht", "Der E-Mail-Versand funktioniert.")
                    flash(f"Testnachricht an {f('test_an')} versandt.", "ok")
                except mail.MailFehler as e:
                    flash(str(e), "fehler")
                    return redirect("/einstellungen")
        elif bereich == "benutzer":
            pw = request.form.get("passwort", "")
            if not f("name") or "@" not in f("email") or len(pw) < 10:
                flash("Name, E-Mail und ein Passwort mit mindestens 10 Zeichen angeben.", "fehler")
                return redirect("/einstellungen")
            try:
                db.ex("INSERT INTO benutzer (name, email, pw_hash, rolle) VALUES (?, ?, ?, ?)",
                      (f("name"), f("email").lower(), generate_password_hash(pw),
                       "admin" if f("rolle") == "admin" else "mitarbeiter"))
            except Exception:
                flash("Diese E-Mail ist schon vergeben.", "fehler")
                return redirect("/einstellungen")
        elif bereich == "passwort":
            u = aktueller_benutzer()
            pw = request.form.get("passwort", "")
            if len(pw) < 10:
                flash("Mindestens 10 Zeichen.", "fehler")
                return redirect("/einstellungen")
            db.ex("UPDATE benutzer SET pw_hash = ? WHERE id = ?", (generate_password_hash(pw), u["id"]))
        flash("Gespeichert.", "ok")
        return redirect("/einstellungen")
    werte = {k: db.setting(k) for k in ("verwaltung_name", "verwaltung_anschrift", "fints_produkt_id", "smtp_host",
                                        "smtp_port", "smtp_user", "smtp_absender")}
    werte["smtp_pass_gesetzt"] = bool(db.setting("smtp_pass"))
    return render_template("einstellungen.html", w=werte, benutzerliste=db.q("SELECT * FROM benutzer ORDER BY name"))


@bp.post("/benutzer/<int:uid>/loeschen")
@nur_admin
def benutzer_loeschen(uid):
    if uid == aktueller_benutzer()["id"]:
        flash("Sie können sich nicht selbst löschen.", "fehler")
    else:
        db.ex("DELETE FROM benutzer WHERE id = ?", (uid,))
        flash("Benutzer gelöscht.", "ok")
    return redirect("/einstellungen")
