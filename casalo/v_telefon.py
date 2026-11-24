"""KI-Telefon: Anrufliste und Einstellungen (Oberfläche) sowie die Schnittstelle für den Sprachbot."""
import json
import secrets

from flask import abort, flash, jsonify, redirect, render_template, request

from . import db, telefon
from .web import bp, f, f_int, nur_admin


# ------------------------------------------------------------------ Oberfläche
@bp.get("/telefon")
def telefon_seite():
    anrufe = db.q("SELECT a.*, p.vorname, p.nachname, p.firma, v.titel AS v_titel FROM anruf a "
                  "LEFT JOIN person p ON p.id = a.person_id LEFT JOIN vorgang v ON v.id = a.vorgang_id "
                  "ORDER BY a.beginn DESC LIMIT 200")
    return render_template("telefon.html", anrufe=anrufe)


@bp.get("/anruf/<int:aid>")
def anruf(aid):
    a = db.q("SELECT a.*, p.vorname, p.nachname, p.firma, e.nr AS e_nr FROM anruf a LEFT JOIN person p ON p.id = a.person_id "
             "LEFT JOIN einheit e ON e.id = a.einheit_id WHERE a.id = ?", (aid,), one=True)
    if not a:
        abort(404)
    vorgang = db.q("SELECT * FROM vorgang WHERE id = ?", (a["vorgang_id"],), one=True) if a["vorgang_id"] else None
    ki = db.q("SELECT * FROM ki_protokoll WHERE anruf_id = ? ORDER BY id", (aid,))
    return render_template("anruf.html", a=a, verlauf=json.loads(a["verlauf"] or "[]"), vorgang=vorgang, ki=ki)


@bp.route("/telefon/einstellungen", methods=["GET", "POST"])
@nur_admin
def telefon_einstellungen():
    if request.method == "POST":
        bereich = f("bereich")
        if bereich == "allgemein":
            for k in ("telefon_begruessung", "buerozeiten"):
                db.set_setting(k, f(k))
            db.set_setting("anonymisieren", "1" if request.form.get("anonymisieren") else "0")
        elif bereich == "regel":
            art = f("notfallart") if f("notfallart") in telefon.NOTFALLARTEN else "sonstiges"
            aktion = f("aktion") if f("aktion") in telefon.AKTIONEN else "aufnehmen"
            if aktion in ("weiterleiten", "ansagen") and not f("nummer"):
                flash("Für Weiterleiten oder Ansagen wird eine Nummer gebraucht.", "fehler")
                return redirect("/telefon/einstellungen")
            gid = f_int("gemeinschaft_id") or None
            rid = f_int("id")
            if rid:
                db.ex("UPDATE notfallregel SET gemeinschaft_id = ?, notfallart = ?, aktion = ?, nummer = ?, ansage = ? WHERE id = ?",
                      (gid, art, aktion, f("nummer"), f("ansage"), rid))
            else:
                db.ex("INSERT INTO notfallregel (gemeinschaft_id, notfallart, aktion, nummer, ansage) VALUES (?, ?, ?, ?, ?)",
                      (gid, art, aktion, f("nummer"), f("ansage")))
        elif bereich == "regel_loeschen":
            db.ex("DELETE FROM notfallregel WHERE id = ?", (f_int("id"),))
        elif bereich == "token":
            db.telefon_token(neu=True)
            flash("Neuer Schlüssel erzeugt – der Telefon-Container im selben Stack übernimmt ihn sofort.", "ok")
            return redirect("/telefon/einstellungen")
        flash("Gespeichert.", "ok")
        return redirect("/telefon/einstellungen")
    regeln = db.q("SELECT r.*, g.name AS g_name FROM notfallregel r LEFT JOIN gemeinschaft g ON g.id = r.gemeinschaft_id "
                  "ORDER BY g.name IS NOT NULL, g.name, r.notfallart")
    return render_template("telefon_einstellungen.html", regeln=regeln, token=db.telefon_token(),
                           begruessung=db.setting("telefon_begruessung"), buerozeiten=db.setting("buerozeiten"),
                           anonymisieren=db.setting("anonymisieren") == "1",
                           ist_buerozeit=telefon.ist_buerozeit(db.setting("buerozeiten")),
                           gemeinschaften=db.q("SELECT id, name FROM gemeinschaft ORDER BY name"),
                           NOTFALLARTEN=telefon.NOTFALLARTEN, AKTIONEN=telefon.AKTIONEN)


# ------------------------------------------------------------------ Schnittstelle für den Sprachbot
def _api_pruefen():
    tok = request.headers.get("X-Casalo-Token", "")
    if not tok or not secrets.compare_digest(tok, db.telefon_token()):
        abort(401)


def _json():
    return request.get_json(silent=True) or {}


@bp.post("/api/telefon/anruf")
def api_start():
    _api_pruefen()
    return jsonify(telefon.anruf_starten(_json().get("nummer", "")))


@bp.post("/api/telefon/anruf/<int:aid>/pruefen")
def api_pruefen(aid):
    _api_pruefen()
    d = _json()
    try:
        return jsonify(telefon.pruefen(aid, d.get("einheit", ""), d.get("plz", "")))
    except KeyError:
        abort(404)


@bp.get("/api/telefon/anruf/<int:aid>/kontext")
def api_kontext(aid):
    _api_pruefen()
    try:
        return jsonify(telefon.kontext(aid))
    except KeyError:
        abort(404)


@bp.post("/api/telefon/anruf/<int:aid>/vorgang")
def api_vorgang(aid):
    _api_pruefen()
    d = _json()
    art = d.get("notfallart") or ""
    if art and art not in telefon.NOTFALLARTEN:
        art = "sonstiges"
    try:
        return jsonify(telefon.vorgang_anlegen(aid, d.get("titel") or "Anliegen am Telefon", d.get("text", ""),
                                               art, d.get("rueckruf", True)))
    except KeyError:
        abort(404)


@bp.post("/api/telefon/anruf/<int:aid>/ende")
def api_ende(aid):
    _api_pruefen()
    d = _json()
    try:
        return jsonify(telefon.anruf_beenden(aid, d.get("dauer_s", 0), d.get("verlauf", []), d.get("anliegen", ""),
                                             d.get("status"), d.get("weiterleitung", "")))
    except KeyError:
        abort(404)


@bp.post("/api/telefon/anruf/<int:aid>/ki")
def api_ki(aid):
    """Der Sprachbot meldet, was (pseudonymisiert) an die KI ging – für die Nachvollziehbarkeit."""
    _api_pruefen()
    telefon.ki_protokollieren(aid, _json().get("inhalt", ""))
    return jsonify(ok=True)
