"""Bank: Abruf, Import, Zuordnung von Umsätzen, SEPA-Lastschrift."""
import datetime
import json

from flask import Response, abort, flash, redirect, render_template, request

from . import bank, daten, db, finapi, forderungen, rechnen
from .web import bp, f, f_int, gemeinschaft_oder_404


# ------------------------------------------------------------------ Kern (auch für den Hintergrundabruf)
def kandidaten(gid, jahr):
    wp = daten.wirtschaftsplan(gid, jahr)
    out = []
    for e in daten.einheiten(gid):
        out.append({"einheit_id": e["id"], "nr": e["nr"], "eigentuemer": daten.eigentuemer(e["id"]),
                    "monat_cent": wp["einheiten"][e["id"]]["monat"] if wp["wp"] else 0})
    return out


def fruehere_zuordnungen(gid):
    rows = db.q("SELECT u.name, b.kostenart_id FROM buchung b JOIN umsatz u ON u.id = b.umsatz_id "
                "WHERE b.gemeinschaft_id = ? AND b.art = 'kosten' AND u.name != '' ORDER BY b.id", (gid,))
    return {r["name"].strip().lower(): r["kostenart_id"] for r in rows}


def vorschlag(u, konto):
    gid = konto["gemeinschaft_id"]
    if u["betrag_cent"] > 0:
        v = bank.vorschlag_eingang(u, kandidaten(gid, int(u["datum"][:4])))
        return sonderumlage_erkennen(u, v)
    from .v_betrieb import vorschlag_rechnung
    rechnung = vorschlag_rechnung(u, gid)
    if rechnung:
        return rechnung
    kas = [dict(k) for k in db.q("SELECT * FROM kostenart WHERE gemeinschaft_id = ?", (gid,))]
    return bank.vorschlag_ausgang(u, kas, fruehere_zuordnungen(gid))


def sonderumlage_erkennen(u, v):
    """Eingang einer Einheit: Stichwort „Sonderumlage“ oder genau ein offener Umlage-Anteil → Sonderumlage statt Hausgeld."""
    if not v or not v.get("einheit_id"):
        return v
    offen = forderungen.offene_umlagen(v["einheit_id"])
    if not offen:
        return v
    zweck = (u.get("zweck") or "").lower()
    passend = [o for o in offen if o["offen"] == u["betrag_cent"] or o["soll"] == u["betrag_cent"]]
    if "sonderumlage" in zweck:
        treffer = (passend or offen)[0]
    elif len(passend) == 1:
        treffer = passend[0]
    else:
        return v
    nr = v["text"].replace("Hausgeld ", "")
    return v | {"art": "sonderumlage", "sonderumlage_id": treffer["id"], "text": f"Sonderumlage {treffer['titel']} {nr}",
                "gruende": v["gruende"] + ["offene Sonderumlage"]}


def buchen(umsatz_id, art, kostenart_id=None, einheit_id=None, text="", sonderumlage_id=None, auftrag_id=None):
    u = db.q("SELECT u.*, k.gemeinschaft_id FROM umsatz u JOIN konto k ON k.id = u.konto_id WHERE u.id = ?",
             (umsatz_id,), one=True)
    if not u or u["status"] != "neu":
        return None
    bid = db.ex("INSERT INTO buchung (gemeinschaft_id, konto_id, datum, betrag_cent, art, kostenart_id, einheit_id, text, umsatz_id, "
                "sonderumlage_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (u["gemeinschaft_id"], u["konto_id"], u["datum"], u["betrag_cent"], art, kostenart_id, einheit_id,
                 text or f"{u['name']} {u['zweck']}"[:200], umsatz_id, sonderumlage_id))
    db.ex("UPDATE umsatz SET status = 'gebucht' WHERE id = ?", (umsatz_id,))
    if auftrag_id:
        from .v_betrieb import auftrag_abrechnen
        auftrag_abrechnen(auftrag_id, bid)
    return bid


def speichern(konto, liste, automatisch=True):
    """Neue Umsätze speichern, Vorschläge berechnen, sichere Treffer gleich buchen. → (neu, gebucht)"""
    neu = gebucht = 0
    eigene = bank.iban_normal(konto["iban"])
    for u in liste:
        if u.get("eigene_iban") and eigene and u["eigene_iban"] != eigene:
            continue                       # Umsatz eines anderen Kontos in derselben Datei
        ref = bank.umsatz_ref(konto["id"], u["datum"], u["betrag_cent"], u["name"], u["zweck"], u.get("extra", ""))
        if db.q("SELECT 1 FROM umsatz WHERE ref = ?", (ref,), one=True):
            continue
        v = vorschlag(u, konto)
        uid = db.ex("INSERT INTO umsatz (konto_id, datum, betrag_cent, name, iban, zweck, ref, vorschlag) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (konto["id"], u["datum"], u["betrag_cent"], u["name"], u["iban"], u["zweck"], ref,
                     json.dumps(v, ensure_ascii=False) if v else ""))
        neu += 1
        if automatisch and v and v["sicherheit"] == "sicher":
            buchen(uid, v["art"], v.get("kostenart_id"), v.get("einheit_id"), v["text"], v.get("sonderumlage_id"), v.get("auftrag_id"))
            gebucht += 1
    db.ex("UPDATE konto SET letzter_abruf = ? WHERE id = ?", (datetime.datetime.now().isoformat(timespec="minutes"), konto["id"]))
    return neu, gebucht


def abrufen(konto, tan=None):
    """FinTS-Abruf; wirft bank.TanNoetig, wenn die Bank eine Freigabe verlangt."""
    bis = datetime.date.today()
    von = datetime.date.fromisoformat(konto["letzter_abruf"][:10]) - datetime.timedelta(days=10) \
        if konto["letzter_abruf"] else bis - datetime.timedelta(days=90)
    zustand = db.decrypt(konto["fints_zustand"]) if konto["fints_zustand"] else None
    try:
        liste, _ = bank.fints_abrufen(konto["fints_blz"], konto["fints_login"], db.decrypt(konto["fints_pin"]),
                                      konto["fints_url"], konto["iban"], von, bis, db.setting("fints_produkt_id"),
                                      zustand=zustand, tan=tan)
    except bank.TanNoetig as t:
        db.ex("UPDATE konto SET fints_zustand = ? WHERE id = ?", (db.encrypt(t.zustand), konto["id"]))
        db.set_setting(f"tan_hinweis_{konto['id']}", t.hinweis)
        raise
    db.ex("UPDATE konto SET fints_zustand = '', tan_gueltig_bis = ? WHERE id = ?",
          ((bis + datetime.timedelta(days=90)).isoformat() if zustand else konto["tan_gueltig_bis"], konto["id"]))
    return speichern(konto, liste)


# ------------------------------------------------------------------ Seiten
@bp.get("/bank")
def bank_seite():
    konten = db.q("SELECT k.*, g.name AS g_name FROM konto k JOIN gemeinschaft g ON g.id = k.gemeinschaft_id "
                  "ORDER BY g.name, k.art")
    konten = [dict(k) | {"saldo": daten.konto_saldo(k, datetime.date.today().isoformat()),
                         "tan_hinweis": db.setting(f"tan_hinweis_{k['id']}", "") if k["fints_zustand"] else ""}
              for k in konten]
    offen = db.q("SELECT u.*, k.gemeinschaft_id, k.art AS konto_art, g.name AS g_name FROM umsatz u "
                 "JOIN konto k ON k.id = u.konto_id JOIN gemeinschaft g ON g.id = k.gemeinschaft_id "
                 "WHERE u.status = 'neu' ORDER BY u.datum DESC")
    liste = []
    auswahl = {}
    for u in offen:
        gid = u["gemeinschaft_id"]
        if gid not in auswahl:
            ein = daten.einheiten(gid)
            umlagen = [{"wert": f"u:{x['id']}:{e['id']}", "text": f"{x['titel']} – {e['nr']} ({rechnen.fmt(x['offen'])})"}
                       for e in ein for x in forderungen.offene_umlagen(e["id"])]
            from .v_betrieb import offene_rechnungen
            auswahl[gid] = {"einheiten": ein, "umlagen": umlagen, "rechnungen": offene_rechnungen(gid),
                            "kostenarten": db.q("SELECT * FROM kostenart WHERE gemeinschaft_id = ? ORDER BY reihenfolge", (gid,))}
        liste.append(dict(u) | {"v": json.loads(u["vorschlag"]) if u["vorschlag"] else None})
    gebucht = db.q("SELECT COUNT(*) AS n FROM umsatz WHERE status = 'gebucht'", one=True)["n"]
    return render_template("bank.html", konten=konten, umsaetze=liste, auswahl=auswahl, gebucht=gebucht,
                           produkt_id=bool(db.setting("fints_produkt_id")), finapi_bereit=finapi.eingerichtet())


@bp.post("/bank/konto/<int:kid>/abruf")
def bank_abruf(kid):
    k = db.q("SELECT * FROM konto WHERE id = ?", (kid,), one=True)
    if not k:
        abort(404)
    if k["zugang"] == "finapi":
        return _finapi_abruf(k)
    if k["zugang"] != "fints":
        flash("Dieses Konto wird per Datei eingelesen.", "fehler")
        return redirect("/bank")
    try:
        neu, auto = abrufen(k, tan=f("tan") or None)
        flash(f"{neu} neue Umsätze, davon {auto} automatisch gebucht.", "ok")
    except bank.TanNoetig as t:
        flash(f"Freigabe nötig: {t.hinweis}", "fehler")
    except Exception as e:  # Bankfehler verständlich anzeigen
        db.ex("UPDATE konto SET fints_zustand = '' WHERE id = ?", (kid,))
        flash(f"Abruf fehlgeschlagen: {e}", "fehler")
    return redirect("/bank")


@bp.post("/bank/konto/<int:kid>/tan-verwerfen")
def bank_tan_verwerfen(kid):
    db.ex("UPDATE konto SET fints_zustand = '' WHERE id = ?", (kid,))
    return redirect("/bank")


@bp.post("/bank/import")
def bank_import():
    k = db.q("SELECT * FROM konto WHERE id = ?", (f_int("konto_id"),), one=True)
    datei = request.files.get("datei")
    if not k or not datei or not datei.filename:
        flash("Konto und Datei wählen.", "fehler")
        return redirect("/bank")
    roh = datei.read()
    try:
        if roh.lstrip().startswith(b"<"):
            liste = bank.lese_camt(roh)
        else:
            text = roh.decode("utf-8-sig", errors="strict") if _utf8(roh) else roh.decode("cp1252")
            liste = bank.lese_csv(text)
    except Exception as e:
        flash(f"Datei nicht lesbar: {e}", "fehler")
        return redirect("/bank")
    neu, auto = speichern(k, liste)
    flash(f"{len(liste)} Umsätze gelesen, {neu} neu, {auto} automatisch gebucht.", "ok")
    return redirect("/bank")


def _utf8(b):
    try:
        b.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


@bp.post("/bank/umsatz/<int:uid>")
def umsatz_buchen(uid):
    u = db.q("SELECT * FROM umsatz WHERE id = ?", (uid,), one=True)
    if not u:
        abort(404)
    if request.form.get("ignorieren"):
        db.ex("UPDATE umsatz SET status = 'ignoriert' WHERE id = ?", (uid,))
        flash("Umsatz ausgeblendet.", "ok")
        return redirect("/bank")
    ziel = f("ziel")                    # "e:<einheit_id>" | "u:<umlage>:<einheit>" | "a:<auftrag_id>" | "k:<kostenart_id>" | "s"
    if ziel.startswith("e:"):
        buchen(uid, "hausgeld", einheit_id=int(ziel[2:]))
    elif ziel.startswith("u:"):
        _, sid, eid = ziel.split(":")
        su = db.q("SELECT * FROM sonderumlage WHERE id = ?", (int(sid),), one=True)
        buchen(uid, "sonderumlage", einheit_id=int(eid), sonderumlage_id=int(sid),
               text=f"Sonderumlage {su['titel'] if su else ''}")
    elif ziel.startswith("a:"):
        a = db.q("SELECT * FROM auftrag WHERE id = ?", (int(ziel[2:]),), one=True)
        if not a or not a["kostenart_id"]:
            flash("Dem Auftrag fehlt die Kostenart – erst beim Auftrag eintragen.", "fehler")
            return redirect("/bank")
        buchen(uid, "kosten", kostenart_id=a["kostenart_id"], text=f"{a['nr']} {a['titel']}", auftrag_id=a["id"])
    elif ziel.startswith("k:"):
        buchen(uid, "kosten", kostenart_id=int(ziel[2:]))
    elif ziel == "s":
        buchen(uid, "sonstig")
    else:
        flash("Bitte wählen, wohin der Umsatz gehört.", "fehler")
        return redirect("/bank")
    flash("Gebucht.", "ok")
    return redirect("/bank")


# ------------------------------------------------------------------ finAPI
def finapi_abrufen(konto):
    """Wie abrufen() für FinTS: aktualisieren, Umsätze holen, speichern. Wirft finapi.FormularNoetig."""
    try:
        return speichern(konto, finapi.abrufen_daten(konto))
    except finapi.FormularNoetig as f:
        db.ex("UPDATE konto SET finapi_formular = ? WHERE id = ?", (f.formular_id, konto["id"]))
        raise


def _finapi_abruf(k):
    if not k["finapi_konto"]:
        flash("Das Konto ist noch nicht mit finAPI verbunden – „Mit finAPI verbinden“.", "fehler")
        return redirect("/bank")
    try:
        neu, auto = finapi_abrufen(k)
        flash(f"{neu} neue Umsätze, davon {auto} automatisch gebucht.", "ok")
    except finapi.FormularNoetig as f:
        flash("Die Bank verlangt eine Freigabe – bitte im finAPI-Formular bestätigen und danach „Freigegeben – weiter“.", "fehler")
        if f.url:
            return redirect(f.url)
    except finapi.FinapiFehler as e:
        flash(str(e), "fehler")
    return redirect("/bank")


@bp.post("/bank/konto/<int:kid>/finapi/verbinden")
def finapi_verbinden(kid):
    k = db.q("SELECT * FROM konto WHERE id = ?", (kid,), one=True)
    if not k or k["zugang"] != "finapi":
        abort(404)
    try:
        url, fid = finapi.formular_verbinden(k)
    except finapi.FinapiFehler as e:
        flash(str(e), "fehler")
        return redirect("/bank")
    db.ex("UPDATE konto SET finapi_formular = ? WHERE id = ?", (fid, kid))
    return redirect(url)


@bp.post("/bank/konto/<int:kid>/finapi/weiter")
def finapi_weiter(kid):
    """Nach dem finAPI-Formular: Bankverbindung übernehmen bzw. Abruf fortsetzen."""
    k = db.q("SELECT * FROM konto WHERE id = ?", (kid,), one=True)
    if not k or not k["finapi_formular"]:
        abort(404)
    try:
        status, verbindung = finapi.formular_ergebnis(k["finapi_formular"])
        if status != "COMPLETED":
            flash(f"Das finAPI-Formular ist noch nicht abgeschlossen (Stand: {status}).", "fehler")
            return redirect("/bank")
        if verbindung and not k["finapi_konto"]:
            konto_id = finapi.konto_zuordnen(k, verbindung)
            db.ex("UPDATE konto SET finapi_verbindung = ?, finapi_konto = ? WHERE id = ?", (str(verbindung), konto_id, kid))
        db.ex("UPDATE konto SET finapi_formular = '' WHERE id = ?", (kid,))
    except finapi.FinapiFehler as e:
        flash(str(e), "fehler")
        return redirect("/bank")
    return _finapi_abruf(db.q("SELECT * FROM konto WHERE id = ?", (kid,), one=True))


# ------------------------------------------------------------------ SEPA-Lastschrift
@bp.route("/g/<int:gid>/lastschrift", methods=["GET", "POST"])
def lastschrift(gid):
    g = gemeinschaft_oder_404(gid)
    heute = datetime.date.today()
    jahr = int(request.values.get("jahr", heute.year))
    wp = daten.wirtschaftsplan(gid, jahr)
    konto = db.q("SELECT * FROM konto WHERE gemeinschaft_id = ? AND art = 'hausgeld' ORDER BY id LIMIT 1", (gid,), one=True)
    posten = []
    for e in daten.einheiten(gid):
        for p in daten.eigentuemer(e["id"]):
            if p["zahlweise"] != "lastschrift":
                continue
            anteil = len(daten.eigentuemer(e["id"]))
            betrag = (wp["einheiten"][e["id"]]["monat"] // anteil) if wp["wp"] else 0
            posten.append({"einheit": e, "person": p, "betrag_cent": betrag,
                           "ok": bool(p["iban"] and p["mandat_ref"] and p["mandat_datum"] and betrag > 0)})
    if request.method == "POST":
        try:
            faellig = datetime.date.fromisoformat(f("faellig"))
        except ValueError:
            flash("Fälligkeitsdatum angeben.", "fehler")
            return redirect(request.url)
        if faellig < heute + datetime.timedelta(days=2):
            flash("Die Fälligkeit muss mindestens zwei Bankarbeitstage in der Zukunft liegen.", "fehler")
            return redirect(request.url)
        monat = f("monat") or f"{faellig:%m/%Y}"
        if not konto:
            flash("Kein Hausgeldkonto angelegt.", "fehler")
            return redirect(request.url)
        try:
            xml = bank.lastschrift_xml(
                {"name": f"WEG {g['name']}", "iban": konto["iban"], "bic": konto["bic"], "glaeubiger_id": g["glaeubiger_id"]},
                [{"name": daten.name(x["person"]), "iban": x["person"]["iban"], "bic": x["person"]["bic"],
                  "betrag_cent": x["betrag_cent"], "mandat_ref": x["person"]["mandat_ref"],
                  "mandat_datum": x["person"]["mandat_datum"], "zweck": f"Hausgeld {monat} Einheit {x['einheit']['nr']}"}
                 for x in posten if x["ok"]],
                faellig)
        except ValueError as e:
            flash(str(e), "fehler")
            return redirect(request.url)
        return Response(xml, mimetype="application/xml",
                        headers={"Content-Disposition": f'attachment; filename="Lastschrift-{g["nr"] or gid}-{faellig}.xml"'})
    return render_template("lastschrift.html", g=g, jahr=jahr, posten=posten, konto=konto, wp=wp,
                           vorschlag=(heute.replace(day=1) + datetime.timedelta(days=32)).replace(day=3),
                           summe=sum(x["betrag_cent"] for x in posten if x["ok"]))
