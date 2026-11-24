import json
import sqlite3

from casalo import db


def _ein(app, weg, nr="W 01"):
    with app.app_context():
        return dict(db.q("SELECT * FROM einheit WHERE id = ?", (weg["e"][nr],), one=True))


def test_zelle_bearbeiten_und_rueckgaengig(client, app, weg):
    eid = weg["e"]["W 01"]
    r = client.get(f"/feld/einheit/{eid}/mea")
    assert r.status_code == 200 and 'class="zelle-form"' in r.get_data(as_text=True)
    r = client.post(f"/feld/einheit/{eid}/mea", data={"wert": "340,5"})
    assert r.status_code == 200 and "340,5" in r.get_data(as_text=True)
    schritt = json.loads(r.headers["HX-Trigger"])["casalo-schritt"]
    assert schritt["alt"] == "333.0" and schritt["neu"] == "340.5" and schritt["ziel"] == f"#z-einheit-{eid}-mea"
    assert _ein(app, weg)["mea"] == 340.5
    # Zurücknehmen: alter Wert, ohne neuen Schritt
    r = client.post(f"/feld/einheit/{eid}/mea", data={"wert": schritt["alt"], "rueck": "1"})
    assert "HX-Trigger" not in r.headers and _ein(app, weg)["mea"] == 333.0


def test_zelle_pruefungen(client, app, weg):
    eid = weg["e"]["W 01"]
    r = client.post(f"/feld/einheit/{eid}/mea", data={"wert": "viel"})
    assert "bitte eine Zahl" in r.get_data(as_text=True) and "HX-Trigger" not in r.headers
    assert "gibt es in dieser Gemeinschaft schon" in client.post(f"/feld/einheit/{eid}/nr", data={"wert": "W 02"}).get_data(as_text=True)
    assert "darf nicht leer" in client.post(f"/feld/einheit/{eid}/nr", data={"wert": " "}).get_data(as_text=True)
    assert "ungültige Auswahl" in client.post(f"/feld/einheit/{eid}/art", data={"wert": "Schloss"}).get_data(as_text=True)
    hahn = weg["p"]["hahn"]
    assert "Mandat" in client.post(f"/feld/person/{hahn}/zahlweise", data={"wert": "lastschrift"}).get_data(as_text=True)
    assert "IBAN ist ungültig" in client.post(f"/feld/person/{hahn}/iban", data={"wert": "DE00 1234"}).get_data(as_text=True)
    assert "E-Mail-Adresse" in client.post(f"/feld/person/{hahn}/email", data={"wert": "kein-at"}).get_data(as_text=True)
    assert "Nachname oder Firma" in client.post(f"/feld/person/{hahn}/nachname", data={"wert": ""}).get_data(as_text=True)
    r = client.post(f"/feld/person/{hahn}/mandat_datum", data={"wert": "15.01.2024"})
    assert "15.01.2024" in r.get_data(as_text=True)
    with app.app_context():
        assert db.q("SELECT mandat_datum FROM person WHERE id = ?", (hahn,), one=True)[0] == "2024-01-15"
    # nicht freigegebene Felder und Tabellen
    assert client.get(f"/feld/einheit/{eid}/gemeinschaft_id").status_code == 404
    assert client.get("/feld/benutzer/1/pw_hash").status_code == 404
    assert client.post(f"/feld/einheit/{eid}/_tabelle", data={"wert": "x"}).status_code == 404


def test_detailkarten(client, app, weg):
    with app.app_context():
        vid = db.ex("INSERT INTO vorgang (gemeinschaft_id, einheit_id, titel, angelegt) VALUES (?, ?, 'Test', '2026-11-24T10:00')",
                    (weg["gid"], weg["e"]["W 01"]))
        aid = db.ex("INSERT INTO anruf (beginn, verlauf) VALUES ('2026-11-24T10:00', '[{\"wer\":\"ki\",\"text\":\"Hallo\"}]')")
        kid = db.q("SELECT id FROM kostenart WHERE gemeinschaft_id = ? LIMIT 1", (weg["gid"],), one=True)[0]
    for url, text in [(f"/karte/einheit/{weg['e']['W 01']}", "Becker"), (f"/karte/person/{weg['p']['becker']}", "W 01"),
                      (f"/karte/vorgang/{vid}", "Test"), (f"/karte/konto/{weg['hg']}", "Saldo heute"),
                      (f"/karte/anruf/{aid}", "Hallo"), (f"/karte/kostenart/{kid}", "Stichworte")]:
        r = client.get(url)
        assert r.status_code == 200 and text in r.get_data(as_text=True), url
        assert "<html" not in r.get_data(as_text=True)          # nur das Stück für den Bereich rechts
    assert client.get("/karte/einheit/9999").status_code == 404
    assert client.get("/karte/unbekannt/1").status_code == 404


def test_gemeinschaft_oben_gemerkt(client, weg):
    assert "wählen …" in client.get("/").get_data(as_text=True)
    client.get(f"/g/{weg['gid']}")
    seite = client.get("/vorgaenge").get_data(as_text=True)
    assert "WEG Musterweg 1" in seite and f"/g/{weg['gid']}/buchungen" in seite


def test_darstellung_je_benutzer(client):
    seite = client.get("/").get_data(as_text=True)
    assert 'data-theme="dunkel"' in seite and "--akz-d:#ff5fb3" in seite                 # Standard wie die Bauplattform
    client.post("/fuer-mich", data={"schema": "hell", "farbe": "blau", "schrift": "serifen", "groesse": "17", "dichte": "kompakt"})
    seite = client.get("/").get_data(as_text=True)
    assert 'data-theme="hell"' in seite and "--akz-h:#2f6fd6" in seite and "--gr:17px" in seite
    assert 'data-dichte="kompakt"' in seite and "Georgia" in seite
    client.post("/fuer-mich", data={"schema": "lila", "farbe": "rosa", "groesse": "99"})        # Unsinn → Standard
    seite = client.get("/").get_data(as_text=True)
    assert 'data-theme="dunkel"' in seite and "--akz-d:#ff5fb3" in seite and "--gr:15px" in seite


def test_assistent_ganzer_ablauf(client, app):
    r = client.post("/assistent", data={"name": "Kruppstr. 20", "plz": "4512"})
    assert "fünf Ziffern" in r.get_data(as_text=True)
    r = client.post("/assistent", data={"name": "Kruppstr. 20", "strasse": "Kruppstr. 20", "plz": "45128", "ort": "Essen"})
    gid = int(r.headers["Location"].split("/")[2])
    with app.app_context():
        assert db.q("SELECT assistent FROM gemeinschaft WHERE id = ?", (gid,), one=True)[0] == 2
        assert len(db.q("SELECT * FROM kostenart WHERE gemeinschaft_id = ?", (gid,))) >= 10
    assert client.get(f"/assistent/{gid}/2").status_code == 200
    # ohne Einheiten geht es nicht weiter
    assert client.post(f"/assistent/{gid}/2", data={}).headers["Location"].endswith(f"/assistent/{gid}/2")
    client.post(f"/assistent/{gid}/2", data={"aktion": "serie", "anzahl": "3", "praefix": "W"})
    with app.app_context():
        ein = db.q("SELECT * FROM einheit WHERE gemeinschaft_id = ? ORDER BY nr", (gid,))
    assert [e["nr"] for e in ein] == ["W 01", "W 02", "W 03"]
    client.post(f"/assistent/{gid}/2", data={"aktion": "mea"})                  # ohne Flächen: abgelehnt
    for e, qm in zip(ein, ("70", "80", "100")):
        client.post(f"/feld/einheit/{e['id']}/wohnflaeche", data={"wert": qm})
    client.post(f"/assistent/{gid}/2", data={"aktion": "mea", "summe": "1000"})
    with app.app_context():
        mea = [r[0] for r in db.q("SELECT mea FROM einheit WHERE gemeinschaft_id = ? ORDER BY nr", (gid,))]
    assert round(sum(mea), 6) == 1000 and mea == [280.0, 320.0, 400.0]
    assert client.get(f"/assistent/{gid}/3").status_code == 200
    for e in ein:
        client.post(f"/assistent/{gid}/3", data={"aktion": "eigentuemer", "einheit_id": e["id"], "nachname": f"Muster{e['id']}"})
    r = client.post(f"/assistent/{gid}/4", data={"hausgeld_iban": "DE00 0000", "hausgeld_saldo": "100"})
    assert r.headers["Location"].endswith(f"/assistent/{gid}/4")
    client.post(f"/assistent/{gid}/4", data={"hausgeld_bank": "Sparkasse", "hausgeld_iban": "DE02120300000000202051",
                                             "hausgeld_saldo": "1.000,00", "hausgeld_zugang": "datei"})
    seite = client.get(f"/assistent/{gid}/5").get_data(as_text=True)
    assert "2.500,00" in seite                                                  # Rücklagen-Vorschlag 250 m² × 10 €
    with app.app_context():
        kas = db.q("SELECT id FROM kostenart WHERE gemeinschaft_id = ? ORDER BY reihenfolge", (gid,))
    client.post(f"/assistent/{gid}/5", data={"aktion": "rechnen", f"ka_{kas[0]['id']}": "1.200", "ruecklage": "2.500"})
    seite = client.get(f"/assistent/{gid}/6").get_data(as_text=True)
    assert seite.count('class="ok"') >= 4, seite
    client.post(f"/assistent/{gid}/6")
    with app.app_context():
        assert db.q("SELECT assistent FROM gemeinschaft WHERE id = ?", (gid,), one=True)[0] == 0


def test_alte_datenbank_wird_ergaenzt(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    con = sqlite3.connect(tmp_path / "casalo.db")
    con.execute("CREATE TABLE benutzer (id INTEGER PRIMARY KEY, name TEXT, email TEXT, pw_hash TEXT, rolle TEXT)")
    con.execute("CREATE TABLE gemeinschaft (id INTEGER PRIMARY KEY, name TEXT)")
    con.commit()
    con.close()
    db.init()
    con = sqlite3.connect(tmp_path / "casalo.db")
    assert "darstellung" in {r[1] for r in con.execute("PRAGMA table_info(benutzer)")}
    assert "assistent" in {r[1] for r in con.execute("PRAGMA table_info(gemeinschaft)")}
