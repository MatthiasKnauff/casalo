import datetime
import io
import json

from casalo import daten, db, telefon

from test_bank import CAMT


def test_ohne_anmeldung_umgeleitet(app):
    c = app.test_client()
    assert c.get("/").status_code == 302
    assert c.get("/health").json["status"] == "ok"


def test_post_ohne_csrf_abgelehnt(client):
    from flask.testing import FlaskClient
    r = FlaskClient.post(client, "/gemeinschaften", data={"name": "x"})
    assert r.status_code == 400


def test_alle_seiten_laden(client, weg):
    gid = weg["gid"]
    urls = ["/", "/gemeinschaften", "/personen", "/versammlungen", "/vorgaenge", "/telefon", "/telefon/einstellungen",
            "/bank", "/einstellungen", f"/person/{weg['p']['becker']}",
            f"/g/{gid}/buchungen?jahr=2025", f"/g/{gid}/wirtschaftsplan/2026", f"/g/{gid}/abrechnung/2025",
            f"/g/{gid}/heizkosten/2025", f"/g/{gid}/verbrauch/{weg['ka']['Wasser']}/2025", f"/g/{gid}/lastschrift"]
    urls += [f"/g/{gid}?tab={t}" for t in ("einheiten", "konten", "kostenarten", "finanzen", "beschluesse", "vorgaenge", "einstellungen")]
    for u in urls:
        r = client.get(u)
        assert r.status_code == 200, u


def test_gemeinschaft_anlegen_mit_kostenarten(client, app):
    r = client.post("/gemeinschaften", data={"name": "Kruppstr. 20", "plz": "45128", "ort": "Essen"})
    assert r.status_code == 302
    with app.app_context():
        g = db.q("SELECT * FROM gemeinschaft WHERE name = 'Kruppstr. 20'", one=True)
        assert len(db.q("SELECT * FROM kostenart WHERE gemeinschaft_id = ?", (g["id"],))) >= 10


def test_jahresabrechnung_beispiel(app, weg):
    with app.app_context():
        a = daten.jahresabrechnung(weg["gid"], 2025)
        assert a.summe_kosten == 131000 + 240000 + 91500 + 162000 + 580000 + 12000
        assert all(ok for t, ok in a.pruefungen if "Hinweise" not in t), a.pruefungen
        e = weg["e"]
        # direkte Kosten nur bei W 02, Heizung exakt laut Messdienst
        assert a.einheiten[e["W 02"]]["kosten"][weg["ka"]["Reparatur Einheit"]] == 12000
        assert a.einheiten[e["W 03"]]["kosten"][weg["ka"]["Heizung"]] == 240000
        # Messdienst verteilt 580.000 = gebucht → kein Hinweis zur Heizung
        assert not any("Heizung" in h for h in a.hinweise)
        # Rücklage: Entnahme 2.500 €, Zuführungen 0 (keine Überweisung gebucht)
        assert a.ruecklage["anfang"] == 3000000 and a.ruecklage["entnahme"] == -250000
        # W 03 hat zwei Monate nicht gezahlt
        monat = daten.wirtschaftsplan(weg["gid"], 2025)["einheiten"][e["W 03"]]["monat"]
        assert a.einheiten[e["W 03"]]["rueckstand"] == 2 * monat


def test_pdfs(client, weg):
    gid = weg["gid"]
    for u in [f"/g/{gid}/abrechnung/2025/gesamt.pdf", f"/g/{gid}/abrechnung/2025/einheit/{weg['e']['W 01']}.pdf",
              f"/g/{gid}/abrechnung/2025/post.pdf", f"/g/{gid}/wirtschaftsplan/2026.pdf"]:
        r = client.get(u)
        assert r.status_code == 200 and r.data[:4] == b"%PDF", u


def test_versammlung_ablauf(client, app, weg):
    gid = weg["gid"]
    tag = (datetime.date.today() + datetime.timedelta(days=40)).isoformat()
    r = client.post("/versammlungen", data={"gemeinschaft_id": gid, "datum": tag, "uhrzeit": "18:30", "ort": "Saal"})
    vid = int(r.headers["Location"].rsplit("/", 1)[1])
    client.post(f"/versammlung/{vid}/top", data={"titel": "Dachrinne erneuern", "vorschlag": "Auftrag an Firma A.", "mehrheit": "einfach"})
    with app.app_context():
        tops = db.q("SELECT * FROM top WHERE versammlung_id = ? ORDER BY nr", (vid,))
        assert tops[-1]["titel"] == "Verschiedenes"                     # neuer TOP vor „Verschiedenes“
        daten_form = {}
        for t in tops:
            if t["mehrheit"] != "keine":
                daten_form[f"ja_{t['id']}"] = "2"
                daten_form[f"nein_{t['id']}"] = "1"
                daten_form[f"enthaltung_{t['id']}"] = "0"
    assert client.get(f"/versammlung/{vid}/einladung.pdf").data[:4] == b"%PDF"
    assert client.get(f"/versammlung/{vid}/einladung.pdf?post=1").data[:4] == b"%PDF"
    client.post(f"/versammlung/{vid}/abstimmung", data=daten_form)
    client.post(f"/versammlung/{vid}/abschliessen")
    with app.app_context():
        b = db.q("SELECT * FROM beschluss WHERE gemeinschaft_id = ? ORDER BY lfd", (gid,))
        assert [x["lfd"] for x in b] == list(range(1, len(b) + 1)) and len(b) == 4
        assert db.q("SELECT status FROM versammlung WHERE id = ?", (vid,), one=True)["status"] == "durchgefuehrt"
    assert client.get(f"/versammlung/{vid}/niederschrift.pdf").data[:4] == b"%PDF"


def test_hybrid_nur_mit_beschluss(client, app, weg):
    tag = (datetime.date.today() + datetime.timedelta(days=40)).isoformat()
    client.post("/versammlungen", data={"gemeinschaft_id": weg["gid"], "datum": tag, "hybrid": "1"})
    with app.app_context():
        assert db.q("SELECT hybrid FROM versammlung ORDER BY id DESC LIMIT 1", one=True)["hybrid"] == 0


def test_bank_import_und_automatik(client, app, weg):
    r = client.post("/bank/import", data={"konto_id": weg["hg"], "datei": (io.BytesIO(CAMT), "auszug.xml")},
                    content_type="multipart/form-data")
    assert r.status_code == 302
    with app.app_context():
        u = db.q("SELECT * FROM umsatz ORDER BY datum")
        assert len(u) == 2
        strom = [x for x in u if x["betrag_cent"] < 0][0]
        assert json.loads(strom["vorschlag"])["kostenart_id"] == weg["ka"]["Allgemeinstrom"]
    # gleicher Auszug noch einmal → keine Doppelten
    client.post("/bank/import", data={"konto_id": weg["hg"], "datei": (io.BytesIO(CAMT), "auszug.xml")},
                content_type="multipart/form-data")
    with app.app_context():
        assert len(db.q("SELECT * FROM umsatz")) == 2
        offen = db.q("SELECT * FROM umsatz WHERE status = 'neu' AND betrag_cent > 0", one=True)
    if offen:
        client.post(f"/bank/umsatz/{offen['id']}", data={"ziel": f"e:{weg['e']['W 03']}"})
        with app.app_context():
            assert db.q("SELECT status FROM umsatz WHERE id = ?", (offen["id"],), one=True)["status"] == "gebucht"


def test_lastschrift_download(client, weg):
    tag = (datetime.date.today() + datetime.timedelta(days=10)).isoformat()
    r = client.post(f"/g/{weg['gid']}/lastschrift?jahr=2026", data={"faellig": tag, "jahr": "2026"})
    assert r.status_code == 200 and b"pain.008.001.08" in r.data and b"M-001" in r.data


# ------------------------------------------------------------------ Telefon-Schnittstelle
def _api(client, app):
    with app.app_context():
        tok = db.telefon_token()
    return {"X-Casalo-Token": tok}


def test_api_ohne_token(client):
    from flask.testing import FlaskClient
    assert FlaskClient.post(client, "/api/telefon/anruf", json={"nummer": "1"}).status_code == 401


def test_telefon_identifikation_stufen(client, app, weg):
    from flask.testing import FlaskClient
    h = _api(client, app)
    post = lambda url, **kw: FlaskClient.post(client, url, headers=h, **kw)

    # unbekannte Nummer: nur Allgemeines, keine Rückfrage möglich
    a = post("/api/telefon/anruf", json={"nummer": "0201 999999"}).json
    assert a["identifiziert"] == "nein" and "KI-Assistenten" in a["begruessung"]
    k = client.get(f"/api/telefon/anruf/{a['anruf_id']}/kontext", headers=h).json
    assert "persoenlich" not in k and "anrufer" not in k
    assert post(f"/api/telefon/anruf/{a['anruf_id']}/pruefen", json={"einheit": "1", "plz": "45130"}).json["ergebnis"] == "nicht_moeglich"

    # bekannte Nummer (+49-Schreibweise) → Rufnummer erkannt, nach richtiger Rückfrage eindeutig
    a = post("/api/telefon/anruf", json={"nummer": "+49201123456"}).json
    assert a["identifiziert"] == "nummer"
    aid = a["anruf_id"]
    k = client.get(f"/api/telefon/anruf/{aid}/kontext", headers=h).json
    assert k["anrufer"]["nachname"] == "Becker" and "persoenlich" not in k
    assert post(f"/api/telefon/anruf/{aid}/pruefen", json={"einheit": "Wohnung 2", "plz": "45130"}).json["ergebnis"] == "falsch"
    assert post(f"/api/telefon/anruf/{aid}/pruefen", json={"einheit": "Wohnung eins", "plz": "45130"}).json["ergebnis"] == "falsch"
    # nach zwei Fehlversuchen gesperrt – auch mit richtigen Angaben
    assert post(f"/api/telefon/anruf/{aid}/pruefen", json={"einheit": "1", "plz": "45130"}).json["ergebnis"] == "gesperrt"

    a = post("/api/telefon/anruf", json={"nummer": "0201/123456"}).json
    aid = a["anruf_id"]
    assert post(f"/api/telefon/anruf/{aid}/pruefen", json={"einheit": "W 01", "plz": "45130"}).json["ergebnis"] == "eindeutig"
    k = client.get(f"/api/telefon/anruf/{aid}/kontext", headers=h).json
    assert "hausgeld_monat" in k["persoenlich"] and "abrechnung_2025" in k["persoenlich"]

    # Vorgang mit Notfall außerhalb der Bürozeit
    with app.app_context():
        db.set_setting("buerozeiten", "")              # nie Bürozeit
        db.ex("INSERT INTO notfallregel (gemeinschaft_id, notfallart, aktion, nummer, ansage) VALUES (NULL, 'wasser', 'weiterleiten', '0171 1234567', '')")
    v = post(f"/api/telefon/anruf/{aid}/vorgang", json={"titel": "Wasser im Keller", "text": "läuft aus der Decke",
                                                         "notfallart": "wasser"}).json
    assert v["notfall"]["aktion"] == "weiterleiten" and v["notfall"]["nummer"] == "0171 1234567"
    post(f"/api/telefon/anruf/{aid}/ende", json={"dauer_s": 95, "verlauf": [{"wer": "ki", "text": "Hallo"}], "anliegen": "Wasserschaden"})
    with app.app_context():
        an = db.q("SELECT * FROM anruf WHERE id = ?", (aid,), one=True)
        assert an["status"] == "vorgang" and an["dauer_s"] == 95
        vg = db.q("SELECT * FROM vorgang WHERE id = ?", (v["vorgang_id"],), one=True)
        assert vg["gemeinschaft_id"] == weg["gid"] and vg["notfallart"] == "wasser"
    assert client.get(f"/anruf/{aid}").status_code == 200


def test_buerozeit():
    mo = datetime.datetime(2026, 11, 23, 9, 0)     # Montag
    assert telefon.ist_buerozeit("Mo-Fr 08:00-17:00", mo)
    assert not telefon.ist_buerozeit("Mo-Fr 08:00-17:00", mo.replace(hour=18))
    assert not telefon.ist_buerozeit("Mo-Fr 08:00-17:00", datetime.datetime(2026, 11, 22, 10))
    assert telefon.ist_buerozeit("Mo-Fr 08:00-17:00; Sa 09:00-12:00", datetime.datetime(2026, 11, 28, 10))


def test_nummern():
    assert telefon.nummer_normal("+49 (201) 12-34 56") == "0201123456"
    assert telefon.nummer_normal("0049201123456") == "0201123456"


def test_telefon_schluessel_datei(client, app, tmp_path):
    import os
    datei = os.path.join(os.environ["DATA_DIR"], "telefon.token")
    with app.app_context():
        alt = db.telefon_token()
    assert open(datei).read() == alt
    client.post("/telefon/einstellungen", data={"bereich": "token"})
    with app.app_context():
        neu = db.telefon_token()
    assert neu != alt and open(datei).read() == neu


def test_telefon_verbindung_einstellbar(client, app):
    from flask.testing import FlaskClient
    r = client.post("/telefon/einstellungen", data={"bereich": "verbindung", "tel_fritzbox_host": "192.168.131.1",
                                                    "tel_sip_benutzer": "casalo", "tel_sip_passwort": "geheim123",
                                                    "tel_sip_port": "5070", "tel_rtp_von": "10000", "tel_rtp_bis": "10100",
                                                    "tel_deepgram_key": "dg", "tel_anthropic_key": "sk-ant"})
    assert r.status_code == 302
    seite = client.get("/telefon/einstellungen").get_data(as_text=True)
    assert "geheim123" not in seite and "sk-ant" not in seite and "192.168.131.1" in seite
    h = _api(client, app)
    k = client.get("/api/telefon/konfig", headers=h).json
    assert k["tel_sip_passwort"] == "geheim123" and k["vollstaendig"] and k["tel_claude_modell"] == "claude-haiku-5-5"
    # leeres Kennwortfeld lässt den gespeicherten Wert stehen
    client.post("/telefon/einstellungen", data={"bereich": "verbindung", "tel_sip_benutzer": "casalo", "tel_rtp_von": "10000", "tel_rtp_bis": "10100"})
    assert client.get("/api/telefon/konfig", headers=h).json["tel_sip_passwort"] == "geheim123"
    # ungültiger Port wird abgewiesen
    client.post("/telefon/einstellungen", data={"bereich": "verbindung", "tel_sip_port": "80", "tel_rtp_von": "10000", "tel_rtp_bis": "10100"})
    assert client.get("/api/telefon/konfig", headers=h).json["tel_sip_port"] == "5070"
    # Status vom Container
    assert FlaskClient.post(client, "/api/telefon/status", headers=h, json={"angemeldet": True, "text": "an der Fritzbox angemeldet"}).json["ok"]
    assert "Fritzbox: angemeldet" in client.get("/telefon/einstellungen").get_data(as_text=True)
    assert FlaskClient.get(client, "/api/telefon/konfig").status_code == 401
