"""Umlaufbeschlüsse, Dokumente, Notdienst im KI-Telefon, Verkauf einer Einheit, Eigentümerwechsel."""
import datetime
import io
import re

from casalo import db, forderungen, telefon, versammlung as vr

PDF = b"%PDF-1.4\n%%EOF"


def test_umlauf_auswertung():
    alle = {1, 2, 3}
    assert vr.umlauf_auswerten("allstimmig", {1: "ja", 2: "ja", 3: "ja"}, alle) == "angenommen"
    assert vr.umlauf_auswerten("allstimmig", {1: "ja", 2: "ja"}, alle) == "abgelehnt"              # einer fehlt
    assert vr.umlauf_auswerten("allstimmig", {1: "ja", 2: "ja", 3: "enthaltung"}, alle) == "abgelehnt"
    assert vr.umlauf_auswerten("einfach", {1: "ja", 2: "nein", 3: "enthaltung"}, alle) == "abgelehnt"
    assert vr.umlauf_auswerten("einfach", {1: "ja"}, alle) == "angenommen"
    assert vr.umlauf_auswerten("allstimmig", {1: "ja", 2: "ja", 3: "ja", 9: "nein"}, alle) == "angenommen"   # 9 nicht berechtigt


def _portal(app, pid):
    from casalo import v_portal
    c = app.test_client()
    with app.app_context():
        db.ex("INSERT INTO portal_zugang (person_id, token_hash, gueltig_bis) VALUES (?, ?, ?)",
              (pid, v_portal._hash(f"t{pid}"), (datetime.datetime.now() + datetime.timedelta(minutes=5)).isoformat()))
    c.get(f"/portal/link/t{pid}")
    tok = re.search(r'name="_csrf" value="([^"]+)"', c.get("/portal").get_data(as_text=True)).group(1)
    return c, tok


def test_umlauf_ablauf(client, app, weg):
    gid = weg["gid"]
    r = client.post(f"/g/{gid}/umlauf", data={"titel": "Neue Briefkästen", "antrag": "Die Gemeinschaft beschließt den Austausch.",
                                              "frist": "2026-12-31", "mehrheit": "allstimmig"})
    uid = int(r.headers["Location"].rsplit("/", 1)[1])
    client.post(f"/umlauf/{uid}/starten")
    c, tok = _portal(app, weg["p"]["hahn"])
    assert "Neue Briefkästen" in c.get("/portal").get_data(as_text=True)
    c.post(f"/portal/umlauf/{uid}", data={"_csrf": tok, "stimme": "ja"})
    client.post(f"/umlauf/{uid}/stimme", data={"person_id": weg["p"]["becker"], "stimme": "ja", "weg": "email"})
    client.post(f"/umlauf/{uid}/stimme", data={"person_id": weg["p"]["krueger"], "stimme": "ja", "weg": "post"})
    seite = client.get(f"/umlauf/{uid}").get_data(as_text=True)
    assert "angenommen" in seite
    client.post(f"/umlauf/{uid}/beenden")
    with app.app_context():
        u = db.q("SELECT * FROM umlauf WHERE id = ?", (uid,), one=True)
        b = db.q("SELECT * FROM beschluss WHERE id = ?", (u["beschluss_id"],), one=True)
        assert u["ergebnis"] == "angenommen" and b["art"] == "umlauf" and b["lfd"] == 1 and "3 Ja" in b["ergebnis"]
        assert {s["weg"] for s in db.q("SELECT weg FROM umlauf_stimme WHERE umlauf_id = ?", (uid,))} == {"portal", "email", "post"}
    # einfache Mehrheit nur mit Ermächtigung
    client.post(f"/g/{gid}/umlauf", data={"titel": "X", "antrag": "Y", "frist": "2026-12-31", "mehrheit": "einfach"})
    with app.app_context():
        assert db.q("SELECT COUNT(*) AS n FROM umlauf", one=True)["n"] == 1
    assert client.get(f"/g/{gid}/umlauf").status_code == 200


def test_dokumente_und_portal(client, app, weg):
    gid = weg["gid"]
    client.post(f"/g/{gid}/dokumente", data={"datei": (io.BytesIO(PDF), "TE.pdf"), "titel": "Teilungserklärung",
                                             "kategorie": "teilungserklaerung", "portal": "1"}, content_type="multipart/form-data")
    client.post(f"/g/{gid}/dokumente", data={"datei": (io.BytesIO(PDF), "Vertrag.pdf"), "titel": "Hausmeistervertrag",
                                             "kategorie": "vertrag"}, content_type="multipart/form-data")
    seite = client.get(f"/g/{gid}/dokumente").get_data(as_text=True)
    assert "Teilungserklärung" in seite and "Hausmeistervertrag" in seite
    with app.app_context():
        te, hv = [d["id"] for d in db.q("SELECT id FROM dokument ORDER BY id")]
    assert client.get(f"/dokument/{hv}").data == PDF
    c, _ = _portal(app, weg["p"]["hahn"])
    portal = c.get("/portal").get_data(as_text=True)
    assert "Teilungserklärung" in portal and "Hausmeistervertrag" not in portal
    assert c.get(f"/portal/datei/{te}").data == PDF and c.get(f"/portal/datei/{hv}").status_code == 404
    client.post(f"/dokument/{te}/loeschen")
    with app.app_context():
        assert db.q("SELECT COUNT(*) AS n FROM dokument", one=True)["n"] == 1


def test_notdienst_des_dienstleisters(client, app, weg):
    client.post("/dienstleister", data={"firma": "Rohr & Co", "gewerk": "Sanitär", "notdienst": "0171 111"})
    with app.app_context():
        did = db.q("SELECT id FROM dienstleister", one=True)["id"]
    client.post("/telefon/einstellungen", data={"bereich": "regel", "notfallart": "wasser", "aktion": "weiterleiten", "dienstleister_id": did})
    with app.app_context():
        db.set_setting("buerozeiten", "")                       # immer außerhalb der Bürozeit
        assert telefon.notfall(weg["gid"], "wasser", datetime.datetime(2026, 11, 29, 3, 0))["nummer"] == "0171 111"
        db.ex("UPDATE dienstleister SET notdienst = '0171 222' WHERE id = ?", (did,))
        assert telefon.notfall(weg["gid"], "wasser", datetime.datetime(2026, 11, 29, 3, 0))["nummer"] == "0171 222"


def test_verkauf_und_vorbesitzer(client, app, weg):
    gid, e3 = weg["gid"], weg["e"]["W 03"]
    assert client.get(f"/einheit/{e3}/verkauf").status_code == 200
    assert client.get(f"/einheit/{e3}/verkauf/auskunft.pdf").data[:4] == b"%PDF"
    assert client.get(f"/einheit/{e3}/verkauf/zustimmung.pdf").status_code == 404       # Gemeinschaft verlangt keine Zustimmung
    with app.app_context():
        db.ex("UPDATE gemeinschaft SET zustimmung_12 = 1 WHERE id = ?", (gid,))
    assert client.get(f"/einheit/{e3}/verkauf/zustimmung.pdf?kaeufer=Erika+Neu").data[:4] == b"%PDF"
    tag = datetime.date(2026, 1, 15)
    with app.app_context():
        vorher = sum(p["betrag"] for p in forderungen.offene_posten(e3, tag))
        neu = db.ex("INSERT INTO person (vorname, nachname, email) VALUES ('Erika', 'Neu', 'neu@example.org')")
        db.ex("UPDATE eigentum SET bis = '2025-12-31' WHERE einheit_id = ? AND bis IS NULL", (e3,))
        db.ex("INSERT INTO eigentum (einheit_id, person_id, von) VALUES (?, ?, '2026-01-01')", (e3, neu))
        v = forderungen.mahnvorschlag(e3, tag)
        monat = sum(p["betrag"] for p in forderungen.offene_posten(e3, tag, "2026-01-01"))
        assert [p["text"] for p in v["posten"]] == ["Hausgeld 01/2026"] and v["summe"] == monat
        assert v["vorbesitzer"] == vorher - monat > 0                                    # Nov./Dez. 2025 beim Verkäufer


def test_umlauf_nicht_vor_fristende(client, app, weg):
    r = client.post(f"/g/{weg['gid']}/umlauf", data={"titel": "Fahrradständer", "antrag": "Aufstellen.", "frist": "2099-12-31"})
    uid = int(r.headers["Location"].rsplit("/", 1)[1])
    client.post(f"/umlauf/{uid}/starten")
    client.post(f"/umlauf/{uid}/stimme", data={"person_id": weg["p"]["becker"], "stimme": "ja", "weg": "email"})
    client.post(f"/umlauf/{uid}/beenden")
    with app.app_context():
        assert db.q("SELECT status FROM umlauf WHERE id = ?", (uid,), one=True)["status"] == "laeuft"
