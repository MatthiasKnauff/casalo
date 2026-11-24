"""Sonderumlagen, Mahnwesen, § 35a."""
import datetime

from casalo import daten, db, forderungen


def _umlage(app, weg, betrag=1000000, faellig="2026-03-01", schl="mea"):
    with app.app_context():
        return db.ex("INSERT INTO sonderumlage (gemeinschaft_id, titel, betrag_cent, schluessel, faellig) VALUES (?, 'Dach', ?, ?, ?)",
                     (weg["gid"], betrag, schl, faellig))


def test_umlage_verteilt_centgenau(app, weg):
    uid = _umlage(app, weg, betrag=1000001)
    with app.app_context():
        u = db.q("SELECT * FROM sonderumlage WHERE id = ?", (uid,), one=True)
        a = forderungen.umlage_anteile(u)
        assert sum(a.values()) == 1000001
        assert a[weg["e"]["W 03"]] > a[weg["e"]["W 01"]]          # 334 statt 333 MEA
        st = forderungen.umlage_stand(u)
        assert st["offen"] == 1000001 and st["ist"] == 0


def test_umlage_anlegen_und_seiten(client, app, weg):
    gid = weg["gid"]
    r = client.post(f"/g/{gid}/sonderumlagen", data={"titel": "Fenster", "betrag": "9.000,00", "schluessel": "einheiten",
                                                      "faellig": "2026-02-01", "zweck": "ruecklage", "beschlossen_am": "2025-11-20"})
    assert r.status_code == 302
    uid = int(r.headers["Location"].rsplit("/", 1)[1])
    assert client.get(f"/g/{gid}/sonderumlagen").status_code == 200
    seite = client.get(f"/sonderumlage/{uid}").get_data(as_text=True)
    assert "3.000,00" in seite
    r = client.get(f"/sonderumlage/{uid}/anforderung.pdf")
    assert r.status_code == 200 and r.data[:4] == b"%PDF"
    r = client.post(f"/sonderumlage/{uid}/lastschrift")        # Becker zahlt per Lastschrift
    assert r.status_code == 200 and b"pain.008" in r.data and b"3000.00" in r.data


def test_bank_erkennt_sonderumlage(app, weg):
    from casalo import v_bank
    uid = _umlage(app, weg, betrag=900000, schl="einheiten", faellig="2025-11-01")
    with app.app_context():
        konto = db.q("SELECT * FROM konto WHERE id = ?", (weg["rl"],), one=True)
        u = {"datum": "2025-11-03", "betrag_cent": 300000, "name": "Martin Hahn", "iban": "", "zweck": "Sonderumlage Dach W 03"}
        neu, gebucht = v_bank.speichern(konto, [u | {"eigene_iban": ""}])
        assert neu == 1
        b = db.q("SELECT * FROM buchung WHERE sonderumlage_id = ?", (uid,), one=True)
        um = db.q("SELECT * FROM umsatz", one=True)
        if b is None:                                   # nicht „sicher“: Vorschlag muss trotzdem Sonderumlage sein
            assert '"sonderumlage"' in um["vorschlag"]
            v_bank.buchen(um["id"], "sonderumlage", einheit_id=weg["e"]["W 03"], sonderumlage_id=uid)
        st = forderungen.umlage_stand(db.q("SELECT * FROM sonderumlage WHERE id = ?", (uid,), one=True))
        assert st["ist"] == 300000
        # Hausgeld-Ist bleibt unberührt
        assert daten.zahlungen(weg["gid"], 2025)[weg["e"]["W 03"]] == \
            daten.wirtschaftsplan(weg["gid"], 2025)["einheiten"][weg["e"]["W 03"]]["monat"] * 10


def test_offene_posten_und_mahnstufen(app, weg):
    e3 = weg["e"]["W 03"]
    with app.app_context():
        monat = daten.wirtschaftsplan(weg["gid"], 2025)["einheiten"][e3]["monat"]
        posten = forderungen.offene_posten(e3, datetime.date(2026, 1, 15))
        # 2025: zwei Monate offen (Nov, Dez); 2026: Januar offen (nichts gezahlt)
        texte = [p["text"] for p in posten]
        assert texte[:2] == ["Hausgeld 11/2025", "Hausgeld 12/2025"]
        assert sum(p["betrag"] for p in posten if "2025" in p["text"]) == 2 * monat

        tag = datetime.date(2026, 1, 15)
        v = forderungen.mahnvorschlag(e3, tag)
        assert v["stufe"] == 1
        p = daten.eigentuemer(e3)[0]
        forderungen.mahnung_anlegen(e3, 1, p, tag, "email")
        assert forderungen.mahnvorschlag(e3, tag + datetime.timedelta(days=5))["stufe"] is None      # Frist läuft
        assert forderungen.mahnvorschlag(e3, tag + datetime.timedelta(days=15))["stufe"] == 2
        forderungen.mahnung_anlegen(e3, 2, p, tag + datetime.timedelta(days=15))
        m2 = db.q("SELECT * FROM mahnung WHERE stufe = 2", one=True)
        assert m2["gebuehr_cent"] == 500
        # alles bezahlt → Mahnungen erledigt
        db.ex("INSERT INTO buchung (gemeinschaft_id, datum, betrag_cent, art, einheit_id) VALUES (?, '2025-12-20', ?, 'hausgeld', ?)",
              (weg["gid"], 2 * monat, e3))
        monat26 = daten.wirtschaftsplan(weg["gid"], 2026)["einheiten"][e3]["monat"]
        db.ex("INSERT INTO buchung (gemeinschaft_id, datum, betrag_cent, art, einheit_id) VALUES (?, '2026-02-01', ?, 'hausgeld', ?)",
              (weg["gid"], 2 * monat26, e3))
        v = forderungen.mahnvorschlag(e3, datetime.date(2026, 2, 10))
        assert v["stufe"] is None
        assert db.q("SELECT COUNT(*) AS n FROM mahnung WHERE status = 'offen'", one=True)["n"] == 0


def test_mahnwesen_seite_und_versand(client, app, weg):
    gid = weg["gid"]
    assert client.get(f"/g/{gid}/mahnwesen").status_code == 200
    e3 = weg["e"]["W 03"]
    r = client.post(f"/g/{gid}/mahnwesen", data={"einheit": str(e3)})
    assert r.status_code == 302
    with app.app_context():
        m = db.q("SELECT * FROM mahnung WHERE einheit_id = ?", (e3,), one=True)
        assert m and m["stufe"] == 1
        assert m["versand"] == "post"            # SMTP nicht eingerichtet → als Brief vorgemerkt
    r = client.get(f"/g/{gid}/mahnungen.pdf?ids={m['id']}")
    assert r.status_code == 200 and r.data[:4] == b"%PDF"
    r = client.post("/mahnwesen/einstellungen", data={"frist_tage": "21", "schwelle": "10,00", "gebuehr_1": "0",
                                                      "gebuehr_2": "7,50", "gebuehr_3": "15"})
    with app.app_context():
        st = forderungen.mahn_einstellungen()
        assert st["frist_tage"] == 21 and st["schwelle"] == 1000 and st["gebuehr"][2] == 750


def test_35a_lohnanteil_und_rechnungswert(app, weg):
    gid = weg["gid"]
    with app.app_context():
        db.ex("UPDATE kostenart SET steuer35a = 'dienstleistung', lohnanteil = 100 WHERE id = ?", (weg["ka"]["Hausmeister"],))
        db.ex("UPDATE kostenart SET steuer35a = 'handwerker', lohnanteil = 50 WHERE id = ?", (weg["ka"]["Allgemeinstrom"],))
        b = forderungen.bescheinigung_35a(gid, 2025)
        gesamt = {z["kostenart"]: z["gesamt"] for z in b["zeilen"]}
        assert gesamt["Hausmeister"] == 240000
        assert gesamt["Hausmeister (aus der Rücklage)"] == 250000
        assert gesamt["Allgemeinstrom"] == 65500
        summe_dl = sum(d["summen"]["dienstleistung"] for d in b["einheiten"].values())
        assert summe_dl == 490000                                  # verteilt ohne Cent-Verlust
        # Arbeitskosten laut Rechnung gehen vor
        bid = db.q("SELECT id FROM buchung WHERE kostenart_id = ? AND art = 'kosten'", (weg["ka"]["Allgemeinstrom"],), one=True)["id"]
        db.ex("UPDATE buchung SET lohn_cent = 10000 WHERE id = ?", (bid,))
        b = forderungen.bescheinigung_35a(gid, 2025)
        assert {z["kostenart"]: z["gesamt"] for z in b["zeilen"]}["Allgemeinstrom"] == 10000


def test_35a_seiten_und_zelle(client, app, weg):
    gid = weg["gid"]
    ka = weg["ka"]["Hausmeister"]
    r = client.post(f"/feld/kostenart/{ka}/steuer35a", data={"wert": "dienstleistung"})
    assert r.status_code == 200
    r = client.post(f"/feld/kostenart/{ka}/lohnanteil", data={"wert": "150"})
    assert "zwischen 0 und 100" in r.get_data(as_text=True)
    assert client.get(f"/g/{gid}/steuer35a/2025").status_code == 200
    r = client.get(f"/g/{gid}/steuer35a/2025/{weg['e']['W 01']}.pdf")
    assert r.status_code == 200 and r.data[:4] == b"%PDF"
    assert client.get(f"/g/{gid}/steuer35a/2025/alle.pdf").data[:4] == b"%PDF"
    with app.app_context():
        bid = db.q("SELECT id, betrag_cent FROM buchung WHERE kostenart_id = ? AND art = 'kosten' AND konto_id = ?",
                   (ka, weg["hg"]), one=True)
    r = client.post(f"/feld/buchung/{bid['id']}/lohn_cent", data={"wert": "1.000,00"})
    assert r.status_code == 200
    with app.app_context():
        assert db.q("SELECT lohn_cent FROM buchung WHERE id = ?", (bid["id"],), one=True)["lohn_cent"] == 100000
    assert "davon Lohn" in client.get(f"/g/{gid}/buchungen?jahr=2025").get_data(as_text=True)


def test_neue_gemeinschaft_hat_35a_vorbelegung(client, app):
    client.post("/gemeinschaften", data={"name": "Kruppstr. 20", "plz": "45128", "ort": "Essen"})
    with app.app_context():
        g = db.q("SELECT * FROM gemeinschaft WHERE name = 'Kruppstr. 20'", one=True)
        hm = db.q("SELECT * FROM kostenart WHERE gemeinschaft_id = ? AND name = 'Hausmeister'", (g["id"],), one=True)
        assert hm["steuer35a"] == "dienstleistung" and hm["lohnanteil"] == 100
