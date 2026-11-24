"""Dienstleister, Aufträge, Wartung, Belege, DATEV, Eigentümerportal."""
import datetime
import io
import re

from casalo import db

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"


def _dienstleister(client, app):
    client.post("/dienstleister", data={"firma": "Rohr & Co", "gewerk": "Sanitär", "email": "info@rohr.example", "telefon": "0201 999"})
    with app.app_context():
        return db.q("SELECT id FROM dienstleister WHERE firma = 'Rohr & Co'", one=True)["id"]


def test_auftrag_aus_vorgang_bis_bezahlt(client, app, weg):
    gid = weg["gid"]
    did = _dienstleister(client, app)
    with app.app_context():
        vid = db.ex("INSERT INTO vorgang (gemeinschaft_id, einheit_id, titel, text, angelegt) VALUES (?, ?, 'Rohrbruch Keller', 'tropft', '2026-11-20')",
                    (gid, weg["e"]["W 02"]))
    r = client.post(f"/vorgang/{vid}/auftrag")
    aid = int(r.headers["Location"].rsplit("/", 1)[1])
    assert client.get(f"/auftrag/{aid}").status_code == 200
    with app.app_context():
        a = db.q("SELECT * FROM auftrag WHERE id = ?", (aid,), one=True)
        assert a["nr"].startswith("A-") and a["vorgang_id"] == vid
        assert db.q("SELECT auftrag_id FROM vorgang WHERE id = ?", (vid,), one=True)["auftrag_id"] == aid
    # Beauftragen ohne Dienstleister geht nicht
    client.post(f"/auftrag/{aid}/status", data={"status": "beauftragt"})
    with app.app_context():
        assert db.q("SELECT status FROM auftrag WHERE id = ?", (aid,), one=True)["status"] == "entwurf"
    client.post(f"/auftrag/{aid}/zuordnen", data={"dienstleister_id": did, "kostenart_id": weg["ka"]["Reparatur Einheit"],
                                                   "einheit_id": weg["e"]["W 02"]})
    r = client.post(f"/auftrag/{aid}/status", data={"status": "beauftragt", "senden": "1"})      # SMTP fehlt → trotzdem vermerkt
    assert client.get(f"/auftrag/{aid}.pdf").data[:4] == b"%PDF"
    client.post(f"/auftrag/{aid}/status", data={"status": "erledigt"})
    with app.app_context():
        assert db.q("SELECT status FROM vorgang WHERE id = ?", (vid,), one=True)["status"] == "erledigt"
    client.post(f"/feld/auftrag/{aid}/rechnung_cent", data={"wert": "1.234,50"})
    client.post(f"/feld/auftrag/{aid}/rechnung_nr", data={"wert": "R-4711"})
    # Bankabgleich erkennt die Rechnung
    from casalo import v_bank
    with app.app_context():
        konto = db.q("SELECT * FROM konto WHERE id = ?", (weg["hg"],), one=True)
        neu, gebucht = v_bank.speichern(konto, [{"datum": "2026-11-22", "betrag_cent": -123450, "name": "Rohr & Co", "iban": "",
                                                 "zweck": "Rechnung R-4711", "eigene_iban": ""}])
        assert gebucht == 1
        a = db.q("SELECT * FROM auftrag WHERE id = ?", (aid,), one=True)
        assert a["status"] == "abgerechnet" and a["buchung_id"]
        b = db.q("SELECT * FROM buchung WHERE id = ?", (a["buchung_id"],), one=True)
        assert b["kostenart_id"] == weg["ka"]["Reparatur Einheit"] and b["dienstleister_id"] == did


def test_wartung_naechster_termin_und_kuendigung(client, app, weg):
    gid = weg["gid"]
    did = _dienstleister(client, app)
    heute = datetime.date.today()
    bis = (heute + datetime.timedelta(days=100)).isoformat()
    client.post(f"/g/{gid}/wartung", data={"titel": "Rauchwarnmelder", "letzte": "2025-12-01", "dienstleister_id": did,
                                           "vertrag_bis": bis, "kosten": "240,00"})
    with app.app_context():
        w = db.q("SELECT * FROM wartung WHERE gemeinschaft_id = ?", (gid,), one=True)
        assert w["intervall_monate"] == 12 and w["naechste"] == "2026-12-01" and "47" in w["pflicht"]
        assert w["kosten_jahr_cent"] == 24000
        from casalo.v_betrieb import faellige_wartungen
        f = faellige_wartungen(30, gid)
        assert [x["titel"] for x in f["kuendigung"]] == ["Rauchwarnmelder"]     # 3 Monate Frist vor Vertragsende
    assert client.get(f"/g/{gid}/wartung").status_code == 200
    assert "Rauchwarnmelder" in client.get("/").get_data(as_text=True)               # Fristen auf der Übersicht
    r = client.post(f"/wartung/{w['id']}/auftrag")
    aid = int(r.headers["Location"].rsplit("/", 1)[1])
    client.post(f"/auftrag/{aid}/status", data={"status": "beauftragt"})
    client.post(f"/auftrag/{aid}/status", data={"status": "erledigt", "datum": "2026-11-28"})
    with app.app_context():
        w = db.q("SELECT * FROM wartung WHERE id = ?", (w["id"],), one=True)
        assert w["letzte"] == "2026-11-28" and w["naechste"] == "2027-11-28"


def test_belege(client, app, weg):
    gid = weg["gid"]
    with app.app_context():
        bid = db.q("SELECT id FROM buchung WHERE gemeinschaft_id = ? AND art = 'kosten' ORDER BY id LIMIT 1", (gid,), one=True)["id"]
    r = client.post("/beleg/hochladen", data={"buchung_id": bid, "datei": (io.BytesIO(PDF), "rechnung.pdf")},
                    content_type="multipart/form-data")
    assert r.status_code == 302
    r = client.post("/beleg/hochladen", data={"buchung_id": bid, "datei": (io.BytesIO(b"<script>"), "boese.html")},
                    content_type="multipart/form-data")
    with app.app_context():
        belege = db.q("SELECT * FROM beleg")
        assert len(belege) == 1 and belege[0]["nr"] == "B-2025-0001" and belege[0]["mime"] == "application/pdf"
    r = client.get(f"/beleg/{belege[0]['id']}")
    assert r.data == PDF and r.headers["X-Content-Type-Options"] == "nosniff"
    seite = client.get(f"/g/{gid}/belege?jahr=2025").get_data(as_text=True)
    assert "B-2025-0001" in seite and "Kosten ohne Beleg" in seite
    assert "📎" in client.get(f"/g/{gid}/buchungen?jahr=2025").get_data(as_text=True)
    client.post(f"/beleg/{belege[0]['id']}/loeschen")
    with app.app_context():
        assert not db.q("SELECT * FROM beleg")


def test_datev_buchungsstapel(client, app, weg):
    gid = weg["gid"]
    client.post("/einstellungen", data={"bereich": "datev", "datev_berater": "1234567", "datev_mandant": "10001",
                                        "datev_erloes_hausgeld": "8000", "datev_erloes_umlage": "8010",
                                        "datev_konto_sonstig": "1590", "datev_kosten_standard": "4900"})
    client.post(f"/feld/kostenart/{weg['ka']['Hausmeister']}/datev_konto", data={"wert": "4250"})
    assert client.get(f"/g/{gid}/datev?jahr=2025").status_code == 200
    r = client.get(f"/g/{gid}/datev?jahr=2025&laden=1")
    assert r.status_code == 200 and "attachment" in r.headers["Content-Disposition"]
    text = r.data.decode("cp1252")
    zeilen = text.strip().split("\r\n")
    assert zeilen[0].startswith('"EXTF";700;21;"Buchungsstapel"') and ";1234567;10001;20250101;4;" in zeilen[0]
    assert zeilen[1].startswith("Umsatz (ohne Soll/Haben-Kz)")
    hm = [z for z in zeilen if ";1200;4250;" in z]
    assert hm and hm[0].startswith('2400,00;"H"')                                     # Hausmeister vom Hausgeldkonto
    assert any(';"S";' in z and ";1200;8000;" in z for z in zeilen)                   # Hausgeld-Eingang
    assert any(";1210;4250;" in z for z in zeilen)                                    # Entnahme Rücklage
    with app.app_context():
        n = db.q("SELECT COUNT(*) AS n FROM buchung WHERE gemeinschaft_id = ? AND datum LIKE '2025%'", (gid,), one=True)["n"]
    assert len(zeilen) == 2 + n                                                       # Kopf, Spalten, je Buchung eine Zeile


def _portal_login(app, client, email):
    from casalo import v_portal
    import secrets
    with app.app_context():
        p = db.q("SELECT * FROM person WHERE email = ?", (email,), one=True)
        tok = secrets.token_urlsafe(16)
        db.ex("INSERT INTO portal_zugang (person_id, token_hash, gueltig_bis) VALUES (?, ?, ?)",
              (p["id"], v_portal._hash(tok), (datetime.datetime.now() + datetime.timedelta(minutes=5)).isoformat()))
    return client.get(f"/portal/link/{tok}")


def test_portal(app, weg):
    c = app.test_client()
    assert c.get("/portal").headers["Location"].endswith("/portal/anmelden")
    seite = c.get("/portal/anmelden").get_data(as_text=True)
    tok = re.search(r'name="_csrf" value="([^"]+)"', seite).group(1)
    r = c.post("/portal/anmelden", data={"_csrf": tok, "email": "hahn@example.org"})
    assert "Bitte E-Mails prüfen" in r.get_data(as_text=True)
    r = c.post("/portal/anmelden", data={"_csrf": tok, "email": "niemand@example.org"})
    assert "Bitte E-Mails prüfen" in r.get_data(as_text=True)                       # verrät nicht, wer Eigentümer ist
    with app.app_context():
        assert db.q("SELECT COUNT(*) AS n FROM portal_zugang", one=True)["n"] == 1
    r = _portal_login(app, c, "hahn@example.org")
    assert r.headers["Location"].endswith("/portal")
    seite = c.get("/portal").get_data(as_text=True)
    assert "Einheit W 03" in seite and "W 01" not in seite.split("Nachricht an die Verwaltung")[0].replace("W 03", "")
    assert "Wirtschaftsplan 2026" in seite and "Jahresabrechnung 2025" not in seite          # Abrechnung noch nicht freigegeben
    with app.app_context():
        db.set_setting(f"portal_abrechnung_{weg['gid']}_2025", "2026-05-01")
    seite = c.get("/portal").get_data(as_text=True)
    assert "Jahresabrechnung 2025" in seite
    e3, e1 = weg["e"]["W 03"], weg["e"]["W 01"]
    assert c.get(f"/portal/dok/{e3}/abrechnung/2025").data[:4] == b"%PDF"
    assert c.get(f"/portal/dok/{e3}/wp/2026").data[:4] == b"%PDF"
    assert c.get(f"/portal/dok/{e1}/abrechnung/2025").status_code == 404                     # fremde Einheit
    assert c.get("/").status_code == 302                                                    # kein Zugang zur Verwaltung
    tok = re.search(r'name="_csrf" value="([^"]+)"', seite).group(1)
    c.post("/portal/meldung", data={"_csrf": tok, "einheit_id": e3, "art": "schaden", "titel": "Licht defekt", "text": "Treppenhaus 2. OG"})
    c.post("/portal/meldung", data={"_csrf": tok, "einheit_id": e1, "art": "schaden", "titel": "x", "text": "y"})
    with app.app_context():
        v = db.q("SELECT * FROM vorgang WHERE quelle = 'portal'")
        assert len(v) == 1 and v[0]["einheit_id"] == e3
    # Link ist nur einmal gültig
    with app.app_context():
        z = db.q("SELECT * FROM portal_zugang WHERE benutzt != ''", one=True)
        assert z


def test_neue_seiten_laden(client, weg):
    gid = weg["gid"]
    for u in ["/dienstleister", f"/g/{gid}/auftraege", f"/g/{gid}/auftraege?status=alle", f"/g/{gid}/wartung", f"/g/{gid}/belege",
              f"/g/{gid}/datev", "/einstellungen"]:
        assert client.get(u).status_code == 200, u
