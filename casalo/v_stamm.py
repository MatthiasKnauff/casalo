"""Gemeinschaften, Einheiten, Eigentümer, Konten, Kostenarten."""
import datetime

from flask import abort, flash, redirect, render_template, request

from . import bank, daten, db
from .web import bp, f, f_betrag, f_int, f_zahl, gemeinschaft_oder_404, zurueck

STANDARD_KOSTENARTEN = [
    ("Allgemeinstrom", "mea", "strom,stadtwerke,energie"),
    ("Gebäudeversicherung", "mea", "versicherung"),
    ("Hausmeister", "flaeche", "hausmeister,hauswart"),
    ("Treppenhausreinigung", "flaeche", "reinigung,gebäudereinigung"),
    ("Gartenpflege", "flaeche", "garten,grünpflege"),
    ("Müllabfuhr", "einheiten", "entsorgung,müll,abfall,ebe"),
    ("Wasser / Abwasser", "verbrauch", "wasser,abwasser"),
    ("Heizung und Warmwasser", "heizung", "gas,fernwärme,heizöl,brennstoff,wärme,techem,ista,brunata,minol"),
    ("Aufzug", "flaeche", "aufzug,lift"),
    ("Instandhaltung", "mea", "reparatur,instand,handwerk,dach,sanitär"),
    ("Verwaltervergütung", "einheiten", "verwaltung,verwaltervergütung"),
    ("Bankgebühren", "mea", "kontoführung,entgelt,gebühr"),
]
TABS = [("einheiten", "Einheiten & Eigentümer"), ("konten", "Konten"), ("kostenarten", "Kostenarten"),
        ("finanzen", "Wirtschaftsplan & Abrechnung"), ("beschluesse", "Beschlusssammlung"), ("vorgaenge", "Vorgänge"),
        ("einstellungen", "Einstellungen")]


@bp.get("/gemeinschaften")
def gemeinschaften():
    rows = db.q("SELECT g.*, (SELECT COUNT(*) FROM einheit e WHERE e.gemeinschaft_id = g.id) AS n_einheiten, "
                "(SELECT COALESCE(SUM(mea), 0) FROM einheit e WHERE e.gemeinschaft_id = g.id) AS summe_mea "
                "FROM gemeinschaft g ORDER BY g.nr, g.name")
    return render_template("gemeinschaften.html", gemeinschaften=rows)


@bp.post("/gemeinschaften")
def gemeinschaft_neu():
    if not f("name"):
        flash("Bitte einen Namen angeben (meist die Anschrift).", "fehler")
        return redirect("/gemeinschaften")
    gid = db.ex("INSERT INTO gemeinschaft (nr, name, strasse, plz, ort) VALUES (?, ?, ?, ?, ?)",
                (f("nr"), f("name"), f("strasse"), f("plz"), f("ort")))
    for n, (name, s, w) in enumerate(STANDARD_KOSTENARTEN):
        db.ex("INSERT INTO kostenart (gemeinschaft_id, name, schluessel, stichworte, reihenfolge) VALUES (?, ?, ?, ?, ?)",
              (gid, name, s, w, n))
    flash("Gemeinschaft angelegt – mit üblichen Kostenarten. Als Nächstes die Einheiten eintragen.", "ok")
    return redirect(f"/g/{gid}")


@bp.get("/g/<int:gid>")
def gemeinschaft(gid):
    g = gemeinschaft_oder_404(gid)
    tab = request.args.get("tab", "einheiten")
    ctx = {"g": g, "tab": tab, "tabs": TABS, "jahr": datetime.date.today().year}
    if tab == "einheiten":
        einheiten = daten.einheiten(gid)
        offen = daten.offene_hausgelder(gid, ctx["jahr"])
        wp = daten.wirtschaftsplan(gid, ctx["jahr"])
        for e in einheiten:
            e["eigentuemer"] = daten.eigentuemer(e["id"])
            e["offen"] = offen.get(e["id"])
            e["monat"] = wp["einheiten"][e["id"]]["monat"] if wp["wp"] else None
        ctx["einheiten"] = einheiten
        ctx["summe_mea"] = sum(e["mea"] for e in einheiten)
        ctx["personen"] = db.q("SELECT id, vorname, nachname, firma FROM person ORDER BY nachname, vorname, firma")
    elif tab == "konten":
        ctx["konten"] = [dict(k) | {"saldo": daten.konto_saldo(k, datetime.date.today().isoformat())}
                         for k in db.q("SELECT * FROM konto WHERE gemeinschaft_id = ? ORDER BY art, id", (gid,))]
    elif tab == "kostenarten":
        ctx["kostenarten"] = db.q("SELECT * FROM kostenart WHERE gemeinschaft_id = ? ORDER BY reihenfolge, id", (gid,))
    elif tab == "finanzen":
        ctx["jahre"] = list(range(ctx["jahr"] + 1, ctx["jahr"] - 4, -1))
        ctx["wps"] = {r["jahr"]: r for r in db.q("SELECT * FROM wirtschaftsplan WHERE gemeinschaft_id = ?", (gid,))}
    elif tab == "beschluesse":
        ctx["beschluesse"] = db.q("SELECT * FROM beschluss WHERE gemeinschaft_id = ? ORDER BY lfd DESC", (gid,))
    elif tab == "vorgaenge":
        ctx["vorgaenge"] = db.q("SELECT v.*, e.nr AS e_nr FROM vorgang v LEFT JOIN einheit e ON e.id = v.einheit_id "
                                "WHERE v.gemeinschaft_id = ? ORDER BY v.status, v.angelegt DESC", (gid,))
    return render_template("gemeinschaft.html", **ctx)


@bp.post("/g/<int:gid>/einstellungen")
def gemeinschaft_speichern(gid):
    gemeinschaft_oder_404(gid)
    modus = "selbst" if f("heizkosten_modus") == "selbst" else "messdienst"
    grund = min(50, max(30, f_int("heiz_grundanteil", 30)))
    prinzip = f("stimmprinzip") if f("stimmprinzip") in ("kopf", "mea", "objekt") else "kopf"
    db.ex("UPDATE gemeinschaft SET nr = ?, name = ?, strasse = ?, plz = ?, ort = ?, heizkosten_modus = ?, "
          "heiz_grundanteil = ?, stimmprinzip = ?, hybrid_erlaubt = ?, glaeubiger_id = ?, verwalter_seit = ?, notiz = ? "
          "WHERE id = ?", (f("nr"), f("name"), f("strasse"), f("plz"), f("ort"), modus, grund, prinzip,
                           1 if request.form.get("hybrid_erlaubt") else 0, f("glaeubiger_id").upper(),
                           f("verwalter_seit"), f("notiz"), gid))
    flash("Gespeichert.", "ok")
    return redirect(f"/g/{gid}?tab=einstellungen")


# ------------------------------------------------------------------ Einheiten
@bp.post("/g/<int:gid>/einheit")
def einheit_speichern(gid):
    gemeinschaft_oder_404(gid)
    if not f("nr"):
        flash("Bitte eine Nummer für die Einheit angeben.", "fehler")
        return redirect(f"/g/{gid}")
    werte = (f("nr"), f("lage"), f("art") or "Wohnung", f_zahl("wohnflaeche"), f_zahl("mea"), f("notiz"))
    eid = f_int("id")
    if eid:
        db.ex("UPDATE einheit SET nr = ?, lage = ?, art = ?, wohnflaeche = ?, mea = ?, notiz = ? "
              "WHERE id = ? AND gemeinschaft_id = ?", werte + (eid, gid))
    else:
        db.ex("INSERT INTO einheit (nr, lage, art, wohnflaeche, mea, notiz, gemeinschaft_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
              werte + (gid,))
    flash("Einheit gespeichert.", "ok")
    return redirect(f"/g/{gid}")


@bp.post("/g/<int:gid>/einheit/<int:eid>/loeschen")
def einheit_loeschen(gid, eid):
    belegt = db.q("SELECT 1 FROM buchung WHERE einheit_id = ? UNION SELECT 1 FROM eigentum WHERE einheit_id = ?",
                  (eid, eid), one=True)
    if belegt:
        flash("Die Einheit hat schon Eigentümer oder Buchungen und kann nicht gelöscht werden.", "fehler")
    else:
        db.ex("DELETE FROM verbrauch WHERE einheit_id = ?", (eid,))
        db.ex("DELETE FROM heizung WHERE einheit_id = ?", (eid,))
        db.ex("DELETE FROM einheit WHERE id = ? AND gemeinschaft_id = ?", (eid, gid))
        flash("Einheit gelöscht.", "ok")
    return redirect(f"/g/{gid}")


# ------------------------------------------------------------------ Eigentümer
PERSON_FELDER = ("anrede", "vorname", "nachname", "firma", "strasse", "plz", "ort", "email", "telefon", "telefon2",
                 "versand", "zahlweise", "iban", "bic", "mandat_ref", "mandat_datum", "notiz")


def _person_werte():
    w = {k: f(k) for k in PERSON_FELDER}
    w["versand"] = w["versand"] if w["versand"] in ("email", "post", "portal") else "email"
    w["zahlweise"] = "lastschrift" if w["zahlweise"] == "lastschrift" else "ueberweisung"
    w["iban"] = bank.iban_normal(w["iban"])
    return w


def _person_pruefen(w):
    fehler = []
    if not (w["nachname"] or w["firma"]):
        fehler.append("Nachname oder Firma angeben.")
    if w["iban"] and not bank.iban_gueltig(w["iban"]):
        fehler.append("Die IBAN ist ungültig.")
    if w["zahlweise"] == "lastschrift" and not (w["iban"] and w["mandat_ref"] and w["mandat_datum"]):
        fehler.append("Für Lastschrift werden IBAN, Mandatsreferenz und Datum der Unterschrift gebraucht.")
    if w["versand"] == "email" and not w["email"]:
        fehler.append("Hinweis: keine E-Mail-Adresse – Post wird automatisch verwendet.")
    return fehler


@bp.post("/g/<int:gid>/eigentuemer")
def eigentuemer_zuordnen(gid):
    gemeinschaft_oder_404(gid)
    eid = f_int("einheit_id")
    if not db.q("SELECT 1 FROM einheit WHERE id = ? AND gemeinschaft_id = ?", (eid, gid), one=True):
        abort(400)
    pid = f_int("person_id")
    if not pid:
        w = _person_werte()
        fehler = _person_pruefen(w)
        harte = [x for x in fehler if not x.startswith("Hinweis")]
        if harte:
            for x in harte:
                flash(x, "fehler")
            return redirect(f"/g/{gid}")
        for x in fehler:
            flash(x, "ok")
        pid = db.ex(f"INSERT INTO person ({', '.join(PERSON_FELDER)}) VALUES ({', '.join('?' * len(PERSON_FELDER))})",
                    tuple(w[k] for k in PERSON_FELDER))
    von = f("von")
    if request.form.get("wechsel"):
        # Eigentümerwechsel: bisherige Eigentümer enden am Vortag
        if not von:
            flash("Für einen Wechsel bitte das Datum des Übergangs angeben.", "fehler")
            return redirect(f"/g/{gid}")
        vortag = (datetime.date.fromisoformat(von) - datetime.timedelta(days=1)).isoformat()
        db.ex("UPDATE eigentum SET bis = ? WHERE einheit_id = ? AND bis IS NULL", (vortag, eid))
    db.ex("INSERT INTO eigentum (einheit_id, person_id, von) VALUES (?, ?, ?)", (eid, pid, von))
    flash("Eigentümer eingetragen.", "ok")
    return redirect(f"/g/{gid}")


@bp.post("/eigentum/<int:tid>/entfernen")
def eigentum_entfernen(tid):
    db.ex("DELETE FROM eigentum WHERE id = ?", (tid,))
    flash("Zuordnung entfernt.", "ok")
    return zurueck()


@bp.route("/person/<int:pid>", methods=["GET", "POST"])
def person(pid):
    p = db.q("SELECT * FROM person WHERE id = ?", (pid,), one=True)
    if not p:
        abort(404)
    if request.method == "POST":
        w = _person_werte()
        fehler = _person_pruefen(w)
        harte = [x for x in fehler if not x.startswith("Hinweis")]
        if harte:
            for x in harte:
                flash(x, "fehler")
            return render_template("person.html", p=dict(p) | w, eigentum=_eigentum_der_person(pid))
        db.ex(f"UPDATE person SET {', '.join(k + ' = ?' for k in PERSON_FELDER)} WHERE id = ?",
              tuple(w[k] for k in PERSON_FELDER) + (pid,))
        for x in fehler:
            flash(x, "ok")
        flash("Gespeichert.", "ok")
        return redirect(f"/person/{pid}")
    return render_template("person.html", p=p, eigentum=_eigentum_der_person(pid))


def _eigentum_der_person(pid):
    return db.q("SELECT t.*, e.nr, e.lage, g.name AS g_name, g.id AS gid FROM eigentum t JOIN einheit e ON e.id = t.einheit_id "
                "JOIN gemeinschaft g ON g.id = e.gemeinschaft_id WHERE t.person_id = ? ORDER BY t.bis IS NOT NULL, g.name",
                (pid,))


@bp.get("/personen")
def personen():
    such = request.args.get("q", "").strip()
    sql = "SELECT * FROM person"
    args = ()
    if such:
        sql += " WHERE nachname LIKE ? OR vorname LIKE ? OR firma LIKE ? OR email LIKE ? OR telefon LIKE ?"
        args = tuple(f"%{such}%" for _ in range(5))
    rows = db.q(sql + " ORDER BY nachname, vorname, firma LIMIT 300", args)
    return render_template("personen.html", personen=rows, such=such)


# ------------------------------------------------------------------ Konten
@bp.post("/g/<int:gid>/konto")
def konto_speichern(gid):
    gemeinschaft_oder_404(gid)
    iban = bank.iban_normal(f("iban"))
    if iban and not bank.iban_gueltig(iban):
        flash("Die IBAN ist ungültig.", "fehler")
        return redirect(f"/g/{gid}?tab=konten")
    try:
        anfang = f_betrag("anfangssaldo")
    except ValueError:
        return redirect(f"/g/{gid}?tab=konten")
    zugang = f("zugang") if f("zugang") in ("fints", "finapi", "datei") else "datei"
    werte = [f("art") if f("art") in ("hausgeld", "ruecklage") else "hausgeld", f("bank"), iban, f("bic").upper(),
             zugang, f("fints_blz"), f("fints_url"), f("fints_login"), anfang, f("anfangsdatum")]
    kid = f_int("id")
    if kid:
        db.ex("UPDATE konto SET art = ?, bank = ?, iban = ?, bic = ?, zugang = ?, fints_blz = ?, fints_url = ?, "
              "fints_login = ?, anfangssaldo_cent = ?, anfangsdatum = ? WHERE id = ? AND gemeinschaft_id = ?",
              tuple(werte) + (kid, gid))
    else:
        kid = db.ex("INSERT INTO konto (art, bank, iban, bic, zugang, fints_blz, fints_url, fints_login, anfangssaldo_cent, "
                    "anfangsdatum, gemeinschaft_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", tuple(werte) + (gid,))
    if request.form.get("fints_pin"):
        db.ex("UPDATE konto SET fints_pin = ? WHERE id = ?", (db.encrypt(request.form["fints_pin"]), kid))
    flash("Konto gespeichert.", "ok")
    return redirect(f"/g/{gid}?tab=konten")


# ------------------------------------------------------------------ Kostenarten
@bp.post("/g/<int:gid>/kostenart")
def kostenart_speichern(gid):
    gemeinschaft_oder_404(gid)
    s = f("schluessel") if f("schluessel") in ("mea", "flaeche", "einheiten", "verbrauch", "heizung", "direkt") else "mea"
    kid = f_int("id")
    if not f("name"):
        flash("Name fehlt.", "fehler")
    elif kid:
        db.ex("UPDATE kostenart SET name = ?, schluessel = ?, stichworte = ?, reihenfolge = ? WHERE id = ? AND gemeinschaft_id = ?",
              (f("name"), s, f("stichworte"), f_int("reihenfolge"), kid, gid))
    else:
        db.ex("INSERT INTO kostenart (gemeinschaft_id, name, schluessel, stichworte, reihenfolge) VALUES (?, ?, ?, ?, ?)",
              (gid, f("name"), s, f("stichworte"), f_int("reihenfolge", 99)))
    return redirect(f"/g/{gid}?tab=kostenarten")


@bp.post("/g/<int:gid>/kostenart/<int:kid>/loeschen")
def kostenart_loeschen(gid, kid):
    if db.q("SELECT 1 FROM buchung WHERE kostenart_id = ? UNION SELECT 1 FROM wp_ansatz WHERE kostenart_id = ?",
            (kid, kid), one=True):
        flash("Die Kostenart ist schon gebucht oder im Wirtschaftsplan und bleibt erhalten.", "fehler")
    else:
        db.ex("DELETE FROM verbrauch WHERE kostenart_id = ?", (kid,))
        db.ex("DELETE FROM kostenart WHERE id = ? AND gemeinschaft_id = ?", (kid, gid))
    return redirect(f"/g/{gid}?tab=kostenarten")


# ------------------------------------------------------------------ Beschlusssammlung (manuelle Einträge)
@bp.post("/g/<int:gid>/beschluss")
def beschluss_neu(gid):
    gemeinschaft_oder_404(gid)
    lfd = (db.q("SELECT MAX(lfd) AS m FROM beschluss WHERE gemeinschaft_id = ?", (gid,), one=True)["m"] or 0) + 1
    if not f("text") or not f("datum"):
        flash("Datum und Wortlaut angeben.", "fehler")
    else:
        db.ex("INSERT INTO beschluss (gemeinschaft_id, lfd, datum, art, text, ergebnis, vermerk) VALUES (?, ?, ?, ?, ?, ?, ?)",
              (gid, lfd, f("datum"), f("art") or "umlauf", f("text"), f("ergebnis"), f("vermerk")))
        flash(f"Eingetragen als Nr. {lfd}.", "ok")
    return redirect(f"/g/{gid}?tab=beschluesse")


@bp.post("/beschluss/<int:bid>/vermerk")
def beschluss_vermerk(bid):
    """Vermerke (z. B. Anfechtung, Aufhebung) – Einträge selbst bleiben unverändert (§ 24 Abs. 7 WEG)."""
    b = db.q("SELECT * FROM beschluss WHERE id = ?", (bid,), one=True)
    if not b:
        abort(404)
    neu = (b["vermerk"] + "\n" if b["vermerk"] else "") + f"{datetime.date.today():%d.%m.%Y}: {f('vermerk')}"
    db.ex("UPDATE beschluss SET vermerk = ? WHERE id = ?", (neu, bid))
    return redirect(f"/g/{b['gemeinschaft_id']}?tab=beschluesse")
