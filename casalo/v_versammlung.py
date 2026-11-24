"""Eigentümerversammlungen: Tagesordnung, Einladung, Abstimmung, Niederschrift, Beschlusssammlung."""
import datetime

from flask import Response, abort, flash, redirect, render_template, request

from . import daten, db, mail, pdf, versammlung as vr
from .v_finanzen import empfaenger_weg
from .web import bp, f, f_int, f_zahl, gemeinschaft_oder_404


def _v(vid):
    v = db.q("SELECT * FROM versammlung WHERE id = ?", (vid,), one=True)
    if not v:
        abort(404)
    return v


def _tops(vid):
    return db.q("SELECT * FROM top WHERE versammlung_id = ? ORDER BY nr", (vid,))


def _eigentuemer_der_gemeinschaft(gid):
    """Jeder aktuelle Eigentümer einmal (auch bei mehreren Einheiten)."""
    rows = db.q("SELECT DISTINCT p.* FROM eigentum t JOIN einheit e ON e.id = t.einheit_id JOIN person p ON p.id = t.person_id "
                "WHERE e.gemeinschaft_id = ? AND t.bis IS NULL ORDER BY p.nachname, p.firma", (gid,))
    return [dict(r) for r in rows]


@bp.get("/versammlungen")
def versammlungen():
    rows = db.q("SELECT v.*, g.name AS g_name, (SELECT COUNT(*) FROM top t WHERE t.versammlung_id = v.id) AS n_tops "
                "FROM versammlung v JOIN gemeinschaft g ON g.id = v.gemeinschaft_id ORDER BY v.beginn DESC")
    liste = []
    for v in rows:
        art, text = vr.frist_status(vr.parse_beginn(v["beginn"]), v["einladung_versandt"])
        liste.append(dict(v) | {"frist_art": art, "frist_text": text})
    return render_template("versammlungen.html", versammlungen=liste,
                           gemeinschaften=db.q("SELECT * FROM gemeinschaft ORDER BY name"))


@bp.post("/versammlungen")
def versammlung_neu():
    g = gemeinschaft_oder_404(f_int("gemeinschaft_id"))
    try:
        beginn = datetime.datetime.fromisoformat(f"{f('datum')}T{f('uhrzeit') or '18:00'}")
    except ValueError:
        flash("Datum und Uhrzeit angeben.", "fehler")
        return redirect("/versammlungen")
    hybrid = 1 if request.form.get("hybrid") and g["hybrid_erlaubt"] else 0
    if request.form.get("hybrid") and not g["hybrid_erlaubt"]:
        flash("Online-Teilnahme ist für diese Gemeinschaft noch nicht beschlossen (§ 23 Abs. 1a WEG) – "
              "erst in den Einstellungen der Gemeinschaft freischalten.", "fehler")
    vid = db.ex("INSERT INTO versammlung (gemeinschaft_id, beginn, ort, art, hybrid, videolink) VALUES (?, ?, ?, ?, ?, ?)",
                (g["id"], beginn.isoformat(timespec="minutes"), f("ort"), f("art") or "ordentlich", hybrid, f("videolink")))
    standard = [("Begrüßung und Feststellung der ordnungsgemäßen Einladung", "", "keine"),
                ("Bericht der Verwaltung und des Verwaltungsbeirats", "", "keine")]
    jahr = beginn.year
    if f("art") != "ausserordentlich":
        standard += [(f"Beschluss über den Wirtschaftsplan {jahr + (1 if beginn.month >= 9 else 0)}",
                      "Die Vorschüsse werden entsprechend dem vorgelegten Wirtschaftsplan festgelegt.", "einfach"),
                     (f"Beschluss über die Jahresabrechnung {jahr - 1} (Nachschüsse / Anpassung der Vorschüsse)",
                      "Die Nachschüsse bzw. die Anpassung der Vorschüsse laut den vorgelegten Einzelabrechnungen werden beschlossen.",
                      "einfach"),
                     ("Entlastung des Verwaltungsbeirats", "Dem Verwaltungsbeirat wird Entlastung erteilt.", "einfach")]
    standard.append(("Verschiedenes", "", "keine"))
    for n, (t, vor, m) in enumerate(standard, 1):
        db.ex("INSERT INTO top (versammlung_id, nr, titel, vorschlag, mehrheit) VALUES (?, ?, ?, ?, ?)", (vid, n, t, vor, m))
    flash("Versammlung angelegt – Tagesordnung mit üblichen Punkten vorbefüllt.", "ok")
    return redirect(f"/versammlung/{vid}")


@bp.get("/versammlung/<int:vid>")
def versammlung(vid):
    v = _v(vid)
    g = gemeinschaft_oder_404(v["gemeinschaft_id"])
    art, text = vr.frist_status(vr.parse_beginn(v["beginn"]), v["einladung_versandt"])
    personen = _eigentuemer_der_gemeinschaft(g["id"])
    wege = {"email": sum(1 for p in personen if empfaenger_weg(p) == "email"),
            "post": sum(1 for p in personen if empfaenger_weg(p) == "post")}
    gesamt_mea = sum(e["mea"] for e in daten.einheiten(g["id"]))
    beschluesse = db.q("SELECT * FROM beschluss WHERE versammlung_id = ?", (vid,))
    return render_template("versammlung.html", v=v, g=g, tops=_tops(vid), frist=(art, text), wege=wege,
                           anzahl=len(personen), gesamt_mea=gesamt_mea, MEHRHEITEN=vr.MEHRHEITEN,
                           beschluesse={b["id"]: b for b in beschluesse})


@bp.post("/versammlung/<int:vid>")
def versammlung_speichern(vid):
    v = _v(vid)
    g = gemeinschaft_oder_404(v["gemeinschaft_id"])
    try:
        beginn = datetime.datetime.fromisoformat(f"{f('datum')}T{f('uhrzeit')}")
    except ValueError:
        flash("Datum/Uhrzeit ungültig.", "fehler")
        return redirect(f"/versammlung/{vid}")
    hybrid = 1 if request.form.get("hybrid") and g["hybrid_erlaubt"] else 0
    db.ex("UPDATE versammlung SET beginn = ?, ort = ?, art = ?, hybrid = ?, videolink = ?, protokoll = ? WHERE id = ?",
          (beginn.isoformat(timespec="minutes"), f("ort"), f("art") or "ordentlich", hybrid, f("videolink"),
           request.form.get("protokoll", v["protokoll"]), vid))
    flash("Gespeichert.", "ok")
    return redirect(f"/versammlung/{vid}")


@bp.post("/versammlung/<int:vid>/top")
def top_speichern(vid):
    v = _v(vid)
    if v["status"] == "durchgefuehrt":
        flash("Die Versammlung ist abgeschlossen.", "fehler")
        return redirect(f"/versammlung/{vid}")
    tid = f_int("id")
    m = f("mehrheit") if f("mehrheit") in vr.MEHRHEITEN else "einfach"
    if request.form.get("loeschen") and tid:
        db.ex("DELETE FROM top WHERE id = ? AND versammlung_id = ?", (tid, vid))
        for n, t in enumerate(_tops(vid), 1):
            db.ex("UPDATE top SET nr = ? WHERE id = ?", (n, t["id"]))
    elif tid:
        db.ex("UPDATE top SET titel = ?, vorschlag = ?, mehrheit = ? WHERE id = ? AND versammlung_id = ?",
              (f("titel"), f("vorschlag"), m, tid, vid))
    elif f("titel"):
        tops = _tops(vid)
        # neuer Punkt vor „Verschiedenes“
        letzter = tops[-1] if tops else None
        if letzter and letzter["titel"].lower().startswith("verschiedenes"):
            db.ex("UPDATE top SET nr = ? WHERE id = ?", (letzter["nr"] + 1, letzter["id"]))
            nr = letzter["nr"]
        else:
            nr = len(tops) + 1
        db.ex("INSERT INTO top (versammlung_id, nr, titel, vorschlag, mehrheit) VALUES (?, ?, ?, ?, ?)",
              (vid, nr, f("titel"), f("vorschlag"), m))
    return redirect(f"/versammlung/{vid}#tops")


@bp.get("/versammlung/<int:vid>/einladung.pdf")
def einladung_pdf(vid):
    v = _v(vid)
    g = gemeinschaft_oder_404(v["gemeinschaft_id"])
    nur_post = request.args.get("post") == "1"
    personen = _eigentuemer_der_gemeinschaft(g["id"])
    if nur_post:
        personen = [p for p in personen if empfaenger_weg(p) == "post"]
        daten_pdf = pdf.einladungen_pdf(g, v, _tops(vid), personen)
    else:
        daten_pdf = pdf.einladung_pdf(g, v, _tops(vid))
    return Response(daten_pdf, mimetype="application/pdf", headers={"Content-Disposition": 'inline; filename="Einladung.pdf"'})


@bp.post("/versammlung/<int:vid>/einladen")
def einladen(vid):
    v = _v(vid)
    g = gemeinschaft_oder_404(v["gemeinschaft_id"])
    tops = _tops(vid)
    beginn = vr.parse_beginn(v["beginn"])
    mails, post, fehler = 0, 0, []
    for p in _eigentuemer_der_gemeinschaft(g["id"]):
        if empfaenger_weg(p) == "post":
            post += 1
            continue
        try:
            mail.senden(p["email"], f"Einladung zur Eigentümerversammlung am {beginn:%d.%m.%Y} – {g['name']}",
                        f"Guten Tag,\n\nanbei erhalten Sie die Einladung zur Eigentümerversammlung am {beginn:%d.%m.%Y} "
                        f"um {beginn:%H:%M} Uhr ({v['ort']})."
                        + (f"\nOnline-Teilnahme: {v['videolink']}" if v["hybrid"] and v["videolink"] else "")
                        + f"\n\nMit freundlichen Grüßen\n{db.setting('verwaltung_name')}",
                        [("Einladung.pdf", pdf.einladung_pdf(g, v, tops, p), "application/pdf")])
            mails += 1
        except mail.MailFehler as ex:
            fehler.append(str(ex))
    if fehler and not mails:
        for x in fehler[:5]:
            flash(x, "fehler")
        return redirect(f"/versammlung/{vid}")
    db.ex("UPDATE versammlung SET einladung_versandt = ?, status = 'eingeladen' WHERE id = ?",
          (datetime.date.today().isoformat(), vid))
    flash(f"Einladung versandt: {mails} per E-Mail" + (f", {post} per Post – Druckdatei bitte heute abschicken." if post else "."), "ok")
    for x in fehler[:5]:
        flash(x, "fehler")
    return redirect(f"/versammlung/{vid}")


@bp.post("/versammlung/<int:vid>/abstimmung")
def abstimmung(vid):
    v = _v(vid)
    if v["status"] == "durchgefuehrt":
        flash("Die Versammlung ist abgeschlossen.", "fehler")
        return redirect(f"/versammlung/{vid}")
    gesamt_mea = sum(e["mea"] for e in daten.einheiten(v["gemeinschaft_id"])) or 1000
    for t in _tops(vid):
        if t["mehrheit"] == "keine":
            continue
        roh = {k: request.form.get(f"{k}_{t['id']}", "").strip() for k in ("ja", "nein", "enthaltung", "ja_mea")}
        if not roh["ja"] and not roh["nein"]:
            continue
        try:
            ja, nein = f_zahl(f"ja_{t['id']}"), f_zahl(f"nein_{t['id']}")
            ent = f_zahl(f"enthaltung_{t['id']}")
            ja_mea = f_zahl(f"ja_mea_{t['id']}") if roh["ja_mea"] else None
        except ValueError:
            flash(f"TOP {t['nr']}: bitte Zahlen eintragen.", "fehler")
            continue
        erg = vr.auswerten(t["mehrheit"], ja, nein, ent, ja_mea, gesamt_mea)
        db.ex("UPDATE top SET ja = ?, nein = ?, enthaltung = ?, ja_mea = ?, ergebnis = ? WHERE id = ?",
              (ja, nein, ent, ja_mea, erg, t["id"]))
    flash("Abstimmungsergebnisse gespeichert.", "ok")
    return redirect(f"/versammlung/{vid}#abstimmung")


@bp.post("/versammlung/<int:vid>/abschliessen")
def abschliessen(vid):
    """Beschlüsse in die Beschlusssammlung übernehmen (fortlaufend nummeriert, § 24 Abs. 7 WEG)."""
    v = _v(vid)
    if v["status"] == "durchgefuehrt":
        return redirect(f"/versammlung/{vid}")
    gid = v["gemeinschaft_id"]
    datum = v["beginn"][:10]
    n = 0
    for t in _tops(vid):
        if t["mehrheit"] == "keine" or not t["ergebnis"] or t["beschluss_id"]:
            continue
        lfd = (db.q("SELECT MAX(lfd) AS m FROM beschluss WHERE gemeinschaft_id = ?", (gid,), one=True)["m"] or 0) + 1
        text = f"TOP {t['nr']} – {t['titel']}: {t['vorschlag']}".strip(": ")
        ergebnis = f"{t['ergebnis']} ({t['ja']:g} Ja / {t['nein']:g} Nein / {t['enthaltung'] or 0:g} Enthaltungen)"
        bid = db.ex("INSERT INTO beschluss (gemeinschaft_id, lfd, datum, versammlung_id, art, text, ergebnis) "
                    "VALUES (?, ?, ?, ?, 'versammlung', ?, ?)", (gid, lfd, datum, vid, text, ergebnis))
        db.ex("UPDATE top SET beschluss_id = ? WHERE id = ?", (bid, t["id"]))
        n += 1
    db.ex("UPDATE versammlung SET status = 'durchgefuehrt' WHERE id = ?", (vid,))
    # beschlossener Wirtschaftsplan: Datum eintragen
    for t in _tops(vid):
        if t["ergebnis"] == "angenommen" and t["titel"].lower().startswith("beschluss über den wirtschaftsplan"):
            jahr = "".join(ch for ch in t["titel"] if ch.isdigit())[-4:]
            if jahr:
                db.ex("UPDATE wirtschaftsplan SET beschlossen_am = ? WHERE gemeinschaft_id = ? AND jahr = ?",
                      (datum, gid, int(jahr)))
    flash(f"Versammlung abgeschlossen – {n} Beschlüsse in die Beschlusssammlung übernommen.", "ok")
    return redirect(f"/versammlung/{vid}")


@bp.get("/versammlung/<int:vid>/niederschrift.pdf")
def niederschrift_pdf(vid):
    v = _v(vid)
    g = gemeinschaft_oder_404(v["gemeinschaft_id"])
    beschluesse = db.q("SELECT * FROM beschluss WHERE versammlung_id = ?", (vid,))
    return Response(pdf.protokoll_pdf(g, v, _tops(vid), beschluesse), mimetype="application/pdf",
                    headers={"Content-Disposition": 'inline; filename="Niederschrift.pdf"'})
