import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture()
def app(tmp_path):
    from casalo import create_app
    a = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)})
    yield a


@pytest.fixture()
def client(app):
    c = app.test_client()
    # Ersteinrichtung
    r = c.get("/anmelden")
    tok = re.search(r'name="csrf" content="([^"]+)"', r.get_data(as_text=True)).group(1)
    c.post("/anmelden", data={"name": "Test", "email": "admin@example.org", "passwort": "geheim12345",
                              "passwort2": "geheim12345"})
    r = c.get("/")
    tok = re.search(r'name="csrf" content="([^"]+)"', r.get_data(as_text=True)).group(1)
    c.csrf = tok
    _post = c.post

    def post(url, data=None, **kw):
        data = dict(data or {})
        data.setdefault("_csrf", tok)
        return _post(url, data=data, **kw)

    c.post = post
    return c


@pytest.fixture()
def weg(app):
    """Beispielgemeinschaft mit 3 Einheiten, Eigentümern, Konten und Buchungen 2025."""
    from casalo import db
    with app.app_context():
        gid = db.ex("INSERT INTO gemeinschaft (nr, name, strasse, plz, ort, glaeubiger_id) VALUES "
                    "('WEG 1', 'Musterweg 1', 'Musterweg 1', '45130', 'Essen', 'DE98ZZZ09999999999')")
        ka = {}
        for n, (name, s, w) in enumerate([("Allgemeinstrom", "mea", "strom"), ("Hausmeister", "flaeche", "hausmeister"),
                                          ("Müllabfuhr", "einheiten", "müll,entsorgung"), ("Wasser", "verbrauch", "wasser"),
                                          ("Heizung", "heizung", "gas,fernwärme"), ("Reparatur Einheit", "direkt", "")]):
            ka[name] = db.ex("INSERT INTO kostenart (gemeinschaft_id, name, schluessel, stichworte, reihenfolge) VALUES (?, ?, ?, ?, ?)",
                             (gid, name, s, w, n))
        e = {}
        for nr, lage, wf, mea in [("W 01", "EG", 70.0, 333.0), ("W 02", "1. OG", 80.0, 333.0), ("W 03", "2. OG", 100.0, 334.0)]:
            e[nr] = db.ex("INSERT INTO einheit (gemeinschaft_id, nr, lage, wohnflaeche, mea) VALUES (?, ?, ?, ?, ?)",
                          (gid, nr, lage, wf, mea))
        p = {}
        p["becker"] = db.ex("INSERT INTO person (anrede, vorname, nachname, email, telefon, iban, zahlweise, mandat_ref, mandat_datum) "
                            "VALUES ('Frau', 'Sabine', 'Becker', 'becker@example.org', '0201 123456', 'DE89370400440532013000', "
                            "'lastschrift', 'M-001', '2024-01-15')")
        p["krueger"] = db.ex("INSERT INTO person (vorname, nachname, versand, telefon) VALUES ('Thomas', 'Krüger', 'post', '+49 171 5550001')")
        p["hahn"] = db.ex("INSERT INTO person (vorname, nachname, email) VALUES ('Martin', 'Hahn', 'hahn@example.org')")
        for nr, pid in [("W 01", p["becker"]), ("W 02", p["krueger"]), ("W 03", p["hahn"])]:
            db.ex("INSERT INTO eigentum (einheit_id, person_id, von) VALUES (?, ?, '2020-01-01')", (e[nr], pid))
        hg = db.ex("INSERT INTO konto (gemeinschaft_id, art, bank, iban, zugang, anfangssaldo_cent, anfangsdatum) "
                   "VALUES (?, 'hausgeld', 'Sparkasse', 'DE02120300000000202051', 'datei', 500000, '2025-01-01')", (gid,))
        rl = db.ex("INSERT INTO konto (gemeinschaft_id, art, bank, iban, zugang, anfangssaldo_cent, anfangsdatum) "
                   "VALUES (?, 'ruecklage', 'Sparkasse', 'DE12500105170648489890', 'datei', 3000000, '2025-01-01')", (gid,))
        # Wirtschaftsplan 2025 und 2026
        for jahr in (2025, 2026):
            wp = db.ex("INSERT INTO wirtschaftsplan (gemeinschaft_id, jahr, ruecklage_cent, beschlossen_am) VALUES (?, ?, 300000, '2024-12-01')",
                       (gid, jahr))
            for name, c in [("Allgemeinstrom", 120000), ("Hausmeister", 240000), ("Müllabfuhr", 90000), ("Wasser", 150000),
                            ("Heizung", 600000)]:
                db.ex("INSERT INTO wp_ansatz (wirtschaftsplan_id, kostenart_id, betrag_cent) VALUES (?, ?, ?)", (wp, ka[name], c))
        # Kosten 2025
        for name, c in [("Allgemeinstrom", -131000), ("Hausmeister", -240000), ("Müllabfuhr", -91500), ("Wasser", -162000),
                        ("Heizung", -580000)]:
            db.ex("INSERT INTO buchung (gemeinschaft_id, konto_id, datum, betrag_cent, art, kostenart_id) VALUES (?, ?, '2025-06-30', ?, 'kosten', ?)",
                  (gid, hg, c, ka[name]))
        db.ex("INSERT INTO buchung (gemeinschaft_id, konto_id, datum, betrag_cent, art, kostenart_id, einheit_id) "
              "VALUES (?, ?, '2025-03-01', -12000, 'kosten', ?, ?)", (gid, hg, ka["Reparatur Einheit"], e["W 02"]))
        # Entnahme aus der Rücklage (wird nicht umgelegt)
        db.ex("INSERT INTO buchung (gemeinschaft_id, konto_id, datum, betrag_cent, art, kostenart_id) VALUES (?, ?, '2025-08-01', -250000, 'kosten', ?)",
              (gid, rl, ka["Hausmeister"]))
        # Verbrauch Wasser, Heizkosten laut Messdienst
        for nr, w, hz in [("W 01", 50, 150000), ("W 02", 70, 190000), ("W 03", 80, 240000)]:
            db.ex("INSERT INTO verbrauch (kostenart_id, einheit_id, jahr, wert) VALUES (?, ?, 2025, ?)", (ka["Wasser"], e[nr], w))
            db.ex("INSERT INTO heizung (gemeinschaft_id, einheit_id, jahr, betrag_cent) VALUES (?, ?, 2025, ?)", (gid, e[nr], hz))
        # Hausgeldzahlungen 2025: W 03 hat zwei Monate nicht gezahlt
        from casalo import daten
        wp25 = daten.wirtschaftsplan(gid, 2025)
        for nr in e:
            monat = wp25["einheiten"][e[nr]]["monat"]
            monate = 10 if nr == "W 03" else 12
            db.ex("INSERT INTO buchung (gemeinschaft_id, konto_id, datum, betrag_cent, art, einheit_id) VALUES (?, ?, '2025-12-01', ?, 'hausgeld', ?)",
                  (gid, hg, monat * monate, e[nr]))
        return {"gid": gid, "ka": ka, "e": e, "p": p, "hg": hg, "rl": rl}
