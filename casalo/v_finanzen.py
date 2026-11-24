"""Buchungen, Wirtschaftsplan, Jahresabrechnung, Heizkosten, Verbrauch."""
import csv
import datetime
import io
import re

from flask import Response, abort, flash, redirect, render_template, request

from . import daten, db, forderungen, mail, pdf, rechnen
from .web import bp, f, f_betrag, f_int, gemeinschaft_oder_404


def _pdf(daten_bytes, name):
    return Response(daten_bytes, mimetype="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{name}"'})


def empfaenger_weg(p):
    """Versandweg eines Eigentümers: E-Mail ist Standard; ohne Adresse automatisch Post."""
    if p.get("versand") in ("email", "portal") and p.get("email"):
        return "email"
    return "post"


# ------------------------------------------------------------------ Buchungen
@bp.route("/g/<int:gid>/buchungen", methods=["GET", "POST"])
def buchungen(gid):
    g = gemeinschaft_oder_404(gid)
    jahr = int(request.args.get("jahr", datetime.date.today().year))
    if request.method == "POST":
        try:
            betrag = f_betrag("betrag")
        except ValueError:
            return redirect(request.url)
        art = f("art") if f("art") in ("kosten", "hausgeld", "sonderumlage", "sonstig") else "sonstig"
        if art == "kosten":
            betrag = -abs(betrag) if f("richtung") != "ein" else abs(betrag)
        elif art in ("hausgeld", "sonderumlage"):
            betrag = abs(betrag)
        elif f("richtung") == "aus":
            betrag = -abs(betrag)
        if art == "kosten" and not f_int("kostenart_id"):
            flash("Kosten brauchen eine Kostenart.", "fehler")
            return redirect(request.url)
        if art in ("hausgeld", "sonderumlage") and not f_int("einheit_id"):
            flash("Zahlungen von Eigentümern brauchen eine Einheit.", "fehler")
            return redirect(request.url)
        if art == "sonderumlage" and not f_int("sonderumlage_id"):
            flash("Bitte die Sonderumlage wählen.", "fehler")
            return redirect(request.url)
        db.ex("INSERT INTO buchung (gemeinschaft_id, konto_id, datum, betrag_cent, art, kostenart_id, einheit_id, text, sonderumlage_id) "
              "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
              (gid, f_int("konto_id") or None, f("datum") or datetime.date.today().isoformat(), betrag, art,
               f_int("kostenart_id") or None, f_int("einheit_id") or None, f("text"),
               (f_int("sonderumlage_id") or None) if art == "sonderumlage" else None))
        flash("Gebucht.", "ok")
        return redirect(f"/g/{gid}/buchungen?jahr={jahr}")
    rows = db.q("SELECT b.*, (SELECT MIN(x.id) FROM beleg x WHERE x.buchung_id = b.id) AS beleg_id, k.name AS ka_name, k.steuer35a AS ka_35a, e.nr AS e_nr, ko.art AS konto_art, ko.bank AS konto_bank "
                "FROM buchung b LEFT JOIN kostenart k ON k.id = b.kostenart_id LEFT JOIN einheit e ON e.id = b.einheit_id "
                "LEFT JOIN konto ko ON ko.id = b.konto_id WHERE b.gemeinschaft_id = ? AND b.datum BETWEEN ? AND ? "
                "ORDER BY b.datum DESC, b.id DESC", (gid, f"{jahr}-01-01", f"{jahr}-12-31"))
    return render_template("buchungen.html", g=g, jahr=jahr, buchungen=rows,
                           konten=db.q("SELECT * FROM konto WHERE gemeinschaft_id = ? ORDER BY art", (gid,)),
                           kostenarten=db.q("SELECT * FROM kostenart WHERE gemeinschaft_id = ? ORDER BY reihenfolge", (gid,)),
                           einheiten=daten.einheiten(gid),
                           umlagen=db.q("SELECT * FROM sonderumlage WHERE gemeinschaft_id = ? ORDER BY faellig DESC", (gid,)))


@bp.post("/buchung/<int:bid>/loeschen")
def buchung_loeschen(bid):
    b = db.q("SELECT * FROM buchung WHERE id = ?", (bid,), one=True)
    if not b:
        abort(404)
    db.ex("DELETE FROM buchung WHERE id = ?", (bid,))
    if b["umsatz_id"]:
        db.ex("UPDATE umsatz SET status = 'neu' WHERE id = ?", (b["umsatz_id"],))
    flash("Buchung gelöscht." + (" Der Bankumsatz ist wieder offen." if b["umsatz_id"] else ""), "ok")
    return redirect(f"/g/{b['gemeinschaft_id']}/buchungen?jahr={b['datum'][:4]}")


# ------------------------------------------------------------------ Wirtschaftsplan
@bp.route("/g/<int:gid>/wirtschaftsplan/<int:jahr>", methods=["GET", "POST"])
def wirtschaftsplan(gid, jahr):
    g = gemeinschaft_oder_404(gid)
    if request.method == "POST":
        wp = db.q("SELECT * FROM wirtschaftsplan WHERE gemeinschaft_id = ? AND jahr = ?", (gid, jahr), one=True)
        try:
            rl = f_betrag("ruecklage")
            ansaetze = {ka.id: rechnen.parse_betrag(request.form.get(f"ka_{ka.id}", "")) for ka in daten.kostenarten(gid)}
        except ValueError as e:
            flash(str(e), "fehler")
            return redirect(request.url)
        if wp:
            db.ex("UPDATE wirtschaftsplan SET ruecklage_cent = ?, beschlossen_am = ? WHERE id = ?",
                  (rl, f("beschlossen_am"), wp["id"]))
            wid = wp["id"]
        else:
            wid = db.ex("INSERT INTO wirtschaftsplan (gemeinschaft_id, jahr, ruecklage_cent, beschlossen_am) VALUES (?, ?, ?, ?)",
                        (gid, jahr, rl, f("beschlossen_am")))
        for kid, c in ansaetze.items():
            db.ex("INSERT INTO wp_ansatz (wirtschaftsplan_id, kostenart_id, betrag_cent) VALUES (?, ?, ?) "
                  "ON CONFLICT(wirtschaftsplan_id, kostenart_id) DO UPDATE SET betrag_cent = excluded.betrag_cent",
                  (wid, kid, c))
        flash("Wirtschaftsplan gespeichert.", "ok")
        return redirect(request.url)
    wp = daten.wirtschaftsplan(gid, jahr)
    kas = daten.kostenarten(gid)
    vorjahr = daten.kosten(gid, jahr - 1)
    einheiten = daten.einheiten(gid)
    for e in einheiten:
        e["eigentuemer"] = daten.eigentuemer_text(e["id"])
    return render_template("wirtschaftsplan.html", g=g, jahr=jahr, wp=wp, kostenarten=kas, vorjahr=vorjahr,
                           einheiten=einheiten)


@bp.post("/g/<int:gid>/wirtschaftsplan/<int:jahr>/vorjahr")
def wirtschaftsplan_aus_vorjahr(gid, jahr):
    """Ansätze aus den Ist-Kosten des Vorjahres übernehmen (aufgerundet auf volle 10 €)."""
    gemeinschaft_oder_404(gid)
    ist = daten.kosten(gid, jahr - 1)
    wp = db.q("SELECT * FROM wirtschaftsplan WHERE gemeinschaft_id = ? AND jahr = ?", (gid, jahr), one=True)
    vor = db.q("SELECT * FROM wirtschaftsplan WHERE gemeinschaft_id = ? AND jahr = ?", (gid, jahr - 1), one=True)
    wid = wp["id"] if wp else db.ex("INSERT INTO wirtschaftsplan (gemeinschaft_id, jahr, ruecklage_cent) VALUES (?, ?, ?)",
                                    (gid, jahr, vor["ruecklage_cent"] if vor else 0))
    for kid, c in ist.items():
        if kid and c > 0:
            c = -(-c // 1000) * 1000
            db.ex("INSERT INTO wp_ansatz (wirtschaftsplan_id, kostenart_id, betrag_cent) VALUES (?, ?, ?) "
                  "ON CONFLICT(wirtschaftsplan_id, kostenart_id) DO UPDATE SET betrag_cent = excluded.betrag_cent",
                  (wid, kid, c))
    flash(f"Ansätze aus den Kosten {jahr - 1} übernommen – bitte prüfen.", "ok")
    return redirect(f"/g/{gid}/wirtschaftsplan/{jahr}")


@bp.get("/g/<int:gid>/wirtschaftsplan/<int:jahr>.pdf")
def wirtschaftsplan_pdf(gid, jahr):
    g = gemeinschaft_oder_404(gid)
    einheiten = daten.einheiten(gid)
    for e in einheiten:
        e["eigentuemer"] = daten.eigentuemer_text(e["id"])
    return _pdf(pdf.wirtschaftsplan(g, jahr, daten.wirtschaftsplan(gid, jahr), daten.kostenarten(gid), einheiten),
                f"Wirtschaftsplan-{jahr}.pdf")


# ------------------------------------------------------------------ Jahresabrechnung
@bp.get("/g/<int:gid>/abrechnung/<int:jahr>")
def abrechnung(gid, jahr):
    g = gemeinschaft_oder_404(gid)
    a = daten.jahresabrechnung(gid, jahr)
    einheiten = daten.einheiten(gid)
    for e in einheiten:
        e["eigentuemer"] = daten.eigentuemer_text(e["id"])
    return render_template("abrechnung.html", g=g, jahr=jahr, a=a, einheiten=einheiten,
                           im_portal=db.setting(f"portal_abrechnung_{gid}_{jahr}", ""),
                           einheit_sel=request.args.get("einheit", type=int))


@bp.get("/g/<int:gid>/abrechnung/<int:jahr>/gesamt.pdf")
def abrechnung_gesamt_pdf(gid, jahr):
    g = gemeinschaft_oder_404(gid)
    a = daten.jahresabrechnung(gid, jahr)
    namen = {e["id"]: (e["nr"], daten.eigentuemer_text(e["id"])) for e in daten.einheiten(gid)}
    return _pdf(pdf.gesamtabrechnung(g, a, namen), f"Gesamtabrechnung-{jahr}.pdf")


@bp.get("/g/<int:gid>/abrechnung/<int:jahr>/einheit/<int:eid>.pdf")
def abrechnung_einheit_pdf(gid, jahr, eid):
    g = gemeinschaft_oder_404(gid)
    e = db.q("SELECT * FROM einheit WHERE id = ? AND gemeinschaft_id = ?", (eid, gid), one=True)
    if not e:
        abort(404)
    a = daten.jahresabrechnung(gid, jahr)
    p = (daten.eigentuemer(eid) or [None])[0]
    return _pdf(pdf.einzelabrechnung(g, dict(e), p, a, daten.kostenarten(gid)), f"Einzelabrechnung-{jahr}-{e['nr']}.pdf")


def _empfaenger_liste(gid):
    """[(einheit, person)] je aktuellem Eigentümer."""
    out = []
    for e in daten.einheiten(gid):
        for p in daten.eigentuemer(e["id"]):
            out.append((e, p))
    return out


@bp.get("/g/<int:gid>/abrechnung/<int:jahr>/post.pdf")
def abrechnung_post_pdf(gid, jahr):
    g = gemeinschaft_oder_404(gid)
    a = daten.jahresabrechnung(gid, jahr)
    liste = [(e, p) for e, p in _empfaenger_liste(gid) if empfaenger_weg(p) == "post"]
    return _pdf(pdf.alle_einzelabrechnungen(g, liste, a, daten.kostenarten(gid)), f"Einzelabrechnungen-Post-{jahr}.pdf")


@bp.post("/g/<int:gid>/abrechnung/<int:jahr>/versand")
def abrechnung_versand(gid, jahr):
    g = gemeinschaft_oder_404(gid)
    a = daten.jahresabrechnung(gid, jahr)
    if not all(ok for txt, ok in a.pruefungen if "Hinweise" not in txt):
        flash("Die Prüfungen sind nicht alle grün – Versand abgebrochen.", "fehler")
        return redirect(f"/g/{gid}/abrechnung/{jahr}")
    kas = daten.kostenarten(gid)
    b35 = forderungen.bescheinigung_35a(gid, jahr)
    mails, post, fehler = 0, 0, []
    for e, p in _empfaenger_liste(gid):
        if empfaenger_weg(p) == "post":
            post += 1
            continue
        anhaenge = [(f"Einzelabrechnung-{jahr}-{e['nr']}.pdf", pdf.einzelabrechnung(g, e, p, a, kas), "application/pdf")]
        d35 = b35["einheiten"].get(e["id"])
        if d35 and any(d35["summen"].values()):
            anhaenge.append((f"Bescheinigung-35a-{jahr}-{e['nr']}.pdf", pdf.bescheinigung_35a_pdf(g, jahr, e, p, d35), "application/pdf"))
        try:
            mail.senden(p["email"], f"Jahresabrechnung {jahr} – {g['name']}, Einheit {e['nr']}",
                        f"Guten Tag,\n\nanbei erhalten Sie die Einzelabrechnung {jahr} für Ihre Einheit {e['nr']}"
                        + (" und die Bescheinigung nach § 35a EStG für Ihre Steuererklärung" if len(anhaenge) > 1 else "") + ".\n\n"
                        f"Mit freundlichen Grüßen\n{db.setting('verwaltung_name')}", anhaenge)
            mails += 1
        except mail.MailFehler as ex:
            fehler.append(str(ex))
    db.set_setting(f"portal_abrechnung_{gid}_{jahr}", datetime.date.today().isoformat())     # ab jetzt im Eigentümerportal
    if mails:
        flash(f"{mails} Abrechnungen per E-Mail versandt.", "ok")
    if post:
        flash(f"{post} Eigentümer bekommen Post – Druckdatei unten bei „Für den Postversand“.", "ok")
    for x in fehler[:5]:
        flash(x, "fehler")
    return redirect(f"/g/{gid}/abrechnung/{jahr}")


@bp.post("/g/<int:gid>/abrechnung/<int:jahr>/portal")
def abrechnung_portal(gid, jahr):
    gemeinschaft_oder_404(gid)
    schluessel = f"portal_abrechnung_{gid}_{jahr}"
    if request.form.get("zeigen"):
        db.set_setting(schluessel, datetime.date.today().isoformat())
        flash(f"Abrechnung {jahr} ist im Eigentümerportal sichtbar.", "ok")
    else:
        db.ex("DELETE FROM einstellung WHERE schluessel = ?", (schluessel,))
        flash(f"Abrechnung {jahr} aus dem Eigentümerportal genommen.", "ok")
    return redirect(f"/g/{gid}/abrechnung/{jahr}")


# ------------------------------------------------------------------ Heizkosten
def _csv_zeilen(datei):
    text = datei.read().decode("utf-8-sig", errors="replace")
    trenner = ";" if text.count(";") >= text.count(",") else ","
    return [z for z in csv.reader(io.StringIO(text), delimiter=trenner) if z]


def _einheit_finden(einheiten, text):
    t = re.sub(r"\s+", "", (text or "").lower())
    for e in einheiten:
        if re.sub(r"\s+", "", e["nr"].lower()) == t:
            return e
    z = re.sub(r"\D", "", text or "").lstrip("0")
    treffer = [e for e in einheiten if re.sub(r"\D", "", e["nr"]).lstrip("0") == z and z]
    return treffer[0] if len(treffer) == 1 else None


@bp.route("/g/<int:gid>/heizkosten/<int:jahr>", methods=["GET", "POST"])
def heizkosten(gid, jahr):
    g = gemeinschaft_oder_404(gid)
    einheiten = daten.einheiten(gid)
    feld = "betrag_cent" if g["heizkosten_modus"] == "messdienst" else "verbrauch"
    if request.method == "POST":
        werte = {}
        if request.files.get("datei") and request.files["datei"].filename:
            nicht = []
            for z in _csv_zeilen(request.files["datei"]):
                if len(z) < 2:
                    continue
                e = _einheit_finden(einheiten, z[0])
                if not e:
                    if re.search(r"\d", z[0]):
                        nicht.append(z[0])
                    continue
                werte[e["id"]] = z[-1]
            if nicht:
                flash(f"Nicht zugeordnet: {', '.join(nicht[:10])}", "fehler")
        else:
            werte = {e["id"]: request.form.get(f"e_{e['id']}", "") for e in einheiten}
        try:
            for eid, w in werte.items():
                if feld == "betrag_cent":
                    v = rechnen.parse_betrag(w) if str(w).strip() else None
                else:
                    v = rechnen.parse_zahl(w) if str(w).strip() else None
                db.ex(f"INSERT INTO heizung (gemeinschaft_id, einheit_id, jahr, {feld}) VALUES (?, ?, ?, ?) "
                      f"ON CONFLICT(einheit_id, jahr) DO UPDATE SET {feld} = excluded.{feld}", (gid, eid, jahr, v))
        except ValueError as ex:
            flash(str(ex), "fehler")
            return redirect(request.url)
        flash(f"{len(werte)} Werte gespeichert.", "ok")
        return redirect(request.url)
    hz = daten.heizwerte(gid, jahr)
    vorhanden = hz["betrag"] if feld == "betrag_cent" else hz["verbrauch"]
    kosten = sum(c for kid, c in daten.kosten(gid, jahr).items()
                 if kid in {k.id for k in daten.kostenarten(gid) if k.schluessel == "heizung"})
    return render_template("heizkosten.html", g=g, jahr=jahr, einheiten=einheiten, werte=vorhanden, feld=feld,
                           kosten=kosten, summe=sum(v for v in vorhanden.values() if v) if feld == "betrag_cent" else None)


# ------------------------------------------------------------------ Verbrauch (z. B. Wasser)
@bp.route("/g/<int:gid>/verbrauch/<int:kid>/<int:jahr>", methods=["GET", "POST"])
def verbrauch(gid, kid, jahr):
    g = gemeinschaft_oder_404(gid)
    ka = db.q("SELECT * FROM kostenart WHERE id = ? AND gemeinschaft_id = ?", (kid, gid), one=True)
    if not ka:
        abort(404)
    einheiten = daten.einheiten(gid)
    if request.method == "POST":
        try:
            for e in einheiten:
                w = request.form.get(f"e_{e['id']}", "")
                db.ex("INSERT INTO verbrauch (kostenart_id, einheit_id, jahr, wert) VALUES (?, ?, ?, ?) "
                      "ON CONFLICT(kostenart_id, einheit_id, jahr) DO UPDATE SET wert = excluded.wert",
                      (kid, e["id"], jahr, rechnen.parse_zahl(w)))
        except ValueError:
            flash("Bitte nur Zahlen eintragen.", "fehler")
            return redirect(request.url)
        flash("Verbrauchswerte gespeichert.", "ok")
        return redirect(request.url)
    return render_template("verbrauch.html", g=g, ka=ka, jahr=jahr, einheiten=einheiten,
                           werte=daten.verbrauchswerte(kid, jahr))
