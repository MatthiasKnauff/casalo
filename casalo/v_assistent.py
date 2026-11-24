"""Assistent: neue Gemeinschaft Schritt für Schritt einrichten – jederzeit verlassen, später fortsetzen.

Jeder Schritt speichert sofort echte Daten; „Assistent verlassen“ verliert nichts. Der offene Schritt steht in
gemeinschaft.assistent (0 = fertig) und wird oben links als „Einrichtung fortsetzen“ angeboten.
"""
import datetime

from flask import abort, flash, redirect, render_template, request

from . import bank, daten, db, rechnen
from .v_stamm import neue_gemeinschaft
from .web import bp, f, f_int, gemeinschaft_oder_404

SCHRITTE = [(1, "Gemeinschaft"), (2, "Einheiten"), (3, "Eigentümer"), (4, "Konten"), (5, "Wirtschaftsplan"), (6, "Fertig")]


def wp_jahr():
    h = datetime.date.today()
    return h.year + 1 if h.month >= 10 else h.year


def _stand(gid):
    """Was ist schon da? → für Häkchen in der Schrittleiste und die Abschlussseite."""
    ein = daten.einheiten(gid)
    ohne = [e for e in ein if not daten.eigentuemer(e["id"])]
    konten = db.q("SELECT * FROM konto WHERE gemeinschaft_id = ?", (gid,))
    wp = db.q("SELECT * FROM wirtschaftsplan WHERE gemeinschaft_id = ? AND jahr = ?", (gid, wp_jahr()), one=True)
    summe_mea = round(sum(e["mea"] for e in ein), 4)
    return {
        1: True,
        2: bool(ein) and summe_mea > 0,
        3: bool(ein) and not ohne,
        4: any(k["art"] == "hausgeld" for k in konten),
        5: bool(wp) and bool(db.q("SELECT 1 FROM wp_ansatz WHERE wirtschaftsplan_id = ? AND betrag_cent > 0", (wp["id"],), one=True)),
        "einheiten": ein, "ohne_eigentuemer": ohne, "konten": konten, "wp": wp, "summe_mea": summe_mea,
    }


@bp.route("/assistent", methods=["GET", "POST"])
def assistent_start():
    if request.method == "POST":
        if not f("name"):
            flash("Bitte einen Namen angeben – meist die Anschrift der Wohnanlage.", "fehler")
            return render_template("assistent.html", n=1, g=None, SCHRITTE=SCHRITTE, stand={1: False}, w=request.form)
        if f("plz") and not (f("plz").isdigit() and len(f("plz")) == 5):
            flash("Die Postleitzahl hat fünf Ziffern.", "fehler")
            return render_template("assistent.html", n=1, g=None, SCHRITTE=SCHRITTE, stand={1: False}, w=request.form)
        gid = neue_gemeinschaft(f("nr"), f("name"), f("strasse") or f("name"), f("plz"), f("ort"), assistent=2)
        db.ex("UPDATE gemeinschaft SET verwalter_seit = ? WHERE id = ?", (f("verwalter_seit"), gid))
        return redirect(f"/assistent/{gid}/2")
    return render_template("assistent.html", n=1, g=None, SCHRITTE=SCHRITTE, stand={1: False}, w={})


@bp.get("/assistent/<int:gid>/<int:n>")
def assistent(gid, n):
    g = gemeinschaft_oder_404(gid)
    if n not in dict(SCHRITTE):
        abort(404)
    if n < 6:
        db.ex("UPDATE gemeinschaft SET assistent = ? WHERE id = ?", (n, gid))
    stand = _stand(gid)
    ctx = {"g": daten.gemeinschaft(gid), "n": n, "SCHRITTE": SCHRITTE, "stand": stand, "w": dict(g), "jahr": wp_jahr()}
    if n == 3:
        ctx["eigentum"] = {e["id"]: daten.eigentuemer(e["id"]) for e in stand["einheiten"]}
    if n == 4:
        ctx["hg"] = next((k for k in stand["konten"] if k["art"] == "hausgeld"), None)
        ctx["rl"] = next((k for k in stand["konten"] if k["art"] == "ruecklage"), None)
    if n == 5:
        wp = daten.wirtschaftsplan(gid, wp_jahr())
        ctx["wp"] = wp
        ctx["kostenarten"] = daten.kostenarten(gid)
        ctx["vorschlag_ruecklage"] = _ruecklage_vorschlag(stand["einheiten"])
    return render_template("assistent.html", **ctx)


def _ruecklage_vorschlag(einheiten):
    """Grober Richtwert zum Einstieg: rund 10 € je m² Wohnfläche und Jahr – die Versammlung beschließt den Betrag."""
    flaeche = sum(e["wohnflaeche"] for e in einheiten)
    return int(round(flaeche * 10)) * 100


@bp.post("/assistent/<int:gid>/<int:n>")
def assistent_speichern(gid, n):
    gemeinschaft_oder_404(gid)
    aktion = f("aktion") or "weiter"
    weiter = f"/assistent/{gid}/{n + 1}"
    hier = f"/assistent/{gid}/{n}"

    if n == 1:
        if not f("name"):
            flash("Bitte einen Namen angeben.", "fehler")
            return redirect(hier)
        db.ex("UPDATE gemeinschaft SET nr = ?, name = ?, strasse = ?, plz = ?, ort = ?, verwalter_seit = ? WHERE id = ?",
              (f("nr"), f("name"), f("strasse"), f("plz"), f("ort"), f("verwalter_seit"), gid))
        return redirect(weiter)

    if n == 2:
        if aktion == "einheit":
            if not f("nr"):
                flash("Nummer der Einheit fehlt.", "fehler")
            elif db.q("SELECT 1 FROM einheit WHERE gemeinschaft_id = ? AND nr = ?", (gid, f("nr")), one=True):
                flash(f"Die Nummer {f('nr')} gibt es schon.", "fehler")
            else:
                try:
                    db.ex("INSERT INTO einheit (gemeinschaft_id, nr, lage, art, wohnflaeche, mea) VALUES (?, ?, ?, ?, ?, ?)",
                          (gid, f("nr"), f("lage"), f("art") or "Wohnung", rechnen.parse_zahl(f("wohnflaeche")),
                           rechnen.parse_zahl(f("mea"))))
                except ValueError:
                    flash("Wohnfläche und MEA bitte als Zahl.", "fehler")
            return redirect(hier + "#neu")
        if aktion == "serie":
            anzahl = min(200, max(0, f_int("anzahl")))
            praefix = f("praefix") or "W"
            stellen = 2 if anzahl < 100 else 3
            vorhanden = {e["nr"] for e in daten.einheiten(gid)}
            neu = 0
            for i in range(1, anzahl + 1):
                nr = f"{praefix} {i:0{stellen}d}"
                if nr in vorhanden:
                    continue
                db.ex("INSERT INTO einheit (gemeinschaft_id, nr, art) VALUES (?, ?, ?)",
                      (gid, nr, "Stellplatz" if praefix.upper() in ("TG", "SP", "ST") else "Wohnung"))
                neu += 1
            flash(f"{neu} Einheiten angelegt – Lage, Fläche und MEA jetzt direkt in der Tabelle eintragen.", "ok")
            return redirect(hier)
        if aktion == "mea":
            ein = daten.einheiten(gid)
            if not any(e["wohnflaeche"] for e in ein):
                flash("Erst die Wohnflächen eintragen.", "fehler")
                return redirect(hier)
            summe = f_int("summe", 1000) or 1000
            anteile = rechnen.verteile(summe * 10, {e["id"]: e["wohnflaeche"] for e in ein})
            for eid, zehntel in anteile.items():
                db.ex("UPDATE einheit SET mea = ? WHERE id = ?", (zehntel / 10, eid))
            flash(f"MEA nach Wohnfläche auf {summe} verteilt. Steht in der Teilungserklärung etwas anderes, gilt das – dann bitte übertragen.", "ok")
            return redirect(hier)
        if not daten.einheiten(gid):
            flash("Bitte mindestens eine Einheit anlegen.", "fehler")
            return redirect(hier)
        return redirect(weiter)

    if n == 3:
        if aktion == "eigentuemer":
            eid = f_int("einheit_id")
            if not db.q("SELECT 1 FROM einheit WHERE id = ? AND gemeinschaft_id = ?", (eid, gid), one=True):
                abort(400)
            if not (f("nachname") or f("firma")):
                flash("Nachname oder Firma angeben.", "fehler")
                return redirect(hier + f"#e{eid}")
            pid = f_int("person_id")
            if not pid:
                pid = db.ex("INSERT INTO person (anrede, vorname, nachname, firma, email, telefon, strasse, plz, ort, versand) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (f("anrede"), f("vorname"), f("nachname"), f("firma"), f("email"), f("telefon"),
                             f("strasse"), f("plz"), f("ort"), "email" if f("email") else "post"))
            db.ex("INSERT INTO eigentum (einheit_id, person_id, von) VALUES (?, ?, ?)", (eid, pid, f("von")))
            return redirect(hier + f"#e{eid}")
        return redirect(weiter)

    if n == 4:
        for art in ("hausgeld", "ruecklage"):
            iban = bank.iban_normal(f(f"{art}_iban"))
            if not iban:
                continue
            if not bank.iban_gueltig(iban):
                flash(f"IBAN des {'Hausgeld' if art == 'hausgeld' else 'Rücklagen'}kontos ist ungültig.", "fehler")
                return redirect(hier)
            try:
                saldo = rechnen.parse_betrag(f(f"{art}_saldo"))
            except ValueError as e:
                flash(str(e), "fehler")
                return redirect(hier)
            werte = (f(f"{art}_bank"), iban, f(f"{art}_zugang") if f(f"{art}_zugang") in ("fints", "finapi", "datei") else "datei",
                     saldo, f(f"{art}_datum"))
            k = db.q("SELECT id FROM konto WHERE gemeinschaft_id = ? AND art = ?", (gid, art), one=True)
            if k:
                db.ex("UPDATE konto SET bank = ?, iban = ?, zugang = ?, anfangssaldo_cent = ?, anfangsdatum = ? WHERE id = ?",
                      werte + (k["id"],))
            else:
                db.ex("INSERT INTO konto (bank, iban, zugang, anfangssaldo_cent, anfangsdatum, gemeinschaft_id, art) "
                      "VALUES (?, ?, ?, ?, ?, ?, ?)", werte + (gid, art))
        if f("glaeubiger_id"):
            db.ex("UPDATE gemeinschaft SET glaeubiger_id = ? WHERE id = ?", (f("glaeubiger_id").upper(), gid))
        return redirect(weiter)

    if n == 5:
        if aktion == "ueberspringen":
            return redirect(weiter)
        jahr = wp_jahr()
        try:
            rl = rechnen.parse_betrag(f("ruecklage"))
            ansaetze = {ka.id: rechnen.parse_betrag(request.form.get(f"ka_{ka.id}", "")) for ka in daten.kostenarten(gid)}
        except ValueError as e:
            flash(str(e), "fehler")
            return redirect(hier)
        wp = db.q("SELECT * FROM wirtschaftsplan WHERE gemeinschaft_id = ? AND jahr = ?", (gid, jahr), one=True)
        wid = wp["id"] if wp else db.ex("INSERT INTO wirtschaftsplan (gemeinschaft_id, jahr, ruecklage_cent) VALUES (?, ?, ?)",
                                        (gid, jahr, rl))
        db.ex("UPDATE wirtschaftsplan SET ruecklage_cent = ? WHERE id = ?", (rl, wid))
        for kid, c in ansaetze.items():
            db.ex("INSERT INTO wp_ansatz (wirtschaftsplan_id, kostenart_id, betrag_cent) VALUES (?, ?, ?) "
                  "ON CONFLICT(wirtschaftsplan_id, kostenart_id) DO UPDATE SET betrag_cent = excluded.betrag_cent", (wid, kid, c))
        if aktion == "rechnen":
            return redirect(hier + "#vorschuesse")
        return redirect(weiter)

    if n == 6:
        db.ex("UPDATE gemeinschaft SET assistent = 0 WHERE id = ?", (gid,))
        flash("Einrichtung abgeschlossen.", "ok")
        return redirect(f"/g/{gid}")
    abort(404)
