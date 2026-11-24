"""finAPI-Anbindung mit nachgebildeter Schnittstelle (kein Netz)."""
import json

import pytest

from casalo import db, finapi


class Antwort:
    def __init__(self, daten, status=200):
        self.status_code, self._d = status, daten
        self.content = json.dumps(daten).encode()
        self.text = self.content.decode()

    def json(self):
        return self._d


@pytest.fixture()
def bank_sim(monkeypatch):
    """Nachbildung von finAPI: merkt sich Aufrufe, Aufgabe braucht einen Zwischenstand."""
    stand = {"aufrufe": [], "formular_status": "COMPLETED", "update": "COMPLETED"}

    def anfrage(methode, url, timeout=None, **kw):
        pfad = url.split(".io", 1)[1]
        stand["aufrufe"].append((methode, pfad))
        if pfad == "/api/v2/oauth/token":
            return Antwort({"access_token": "TOK-" + kw["data"]["grant_type"]})
        if pfad == "/api/v2/users":
            return Antwort({"id": "casalo-user", "password": kw["json"]["password"]})
        if pfad == "/api/webForms/bankConnectionImport":
            return Antwort({"id": "wf1", "url": "https://webform-sandbox.finapi.io/wf/wf1"})
        if pfad == "/api/webForms/wf1":
            return Antwort({"id": "wf1", "status": stand["formular_status"], "payload": {"bankConnectionId": 77}})
        if pfad == "/api/v2/accounts":
            return Antwort({"accounts": [{"id": 9001, "iban": "DE12500105170648489890"}, {"id": 9002, "iban": "DE02120300000000202051"}]})
        if pfad == "/api/tasks/backgroundUpdate":
            if stand["update"] == "WEB_FORM_REQUIRED":
                return Antwort({"id": "t1", "status": "WEB_FORM_REQUIRED", "payload": {"webForm": {"id": "wf2", "url": "https://wf/wf2"}}})
            return Antwort({"id": "t1", "status": "IN_PROGRESS"})
        if pfad == "/api/tasks/t1":
            return Antwort({"id": "t1", "status": "COMPLETED"})
        if pfad == "/api/v2/transactions":
            assert kw["params"]["accountIds"] == "9002"
            return Antwort({"transactions": [
                {"id": 1, "bankBookingDate": "2026-11-02", "amount": 478.80, "purpose": "Hausgeld W 03", "counterpartName": "Martin Hahn",
                 "counterpartIban": "DE44500105175407324931"},
                {"id": 2, "bankBookingDate": "2026-11-03", "amount": -131.00, "purpose": "Abschlag", "counterpartName": "Stadtwerke Essen",
                 "counterpartIban": None}], "paging": {"page": 1, "pageCount": 1}})
        if pfad == "/api/v2/fehler":
            return Antwort({"errors": [{"message": "kaputt"}]}, 422)
        raise AssertionError(pfad)

    monkeypatch.setattr(finapi.requests, "request", anfrage)
    monkeypatch.setattr(finapi, "schlafen", lambda s: None)
    return stand


def test_verbinden_und_abrufen(client, app, weg, bank_sim):
    kid = weg["hg"]
    with app.app_context():
        db.ex("UPDATE konto SET zugang = 'finapi' WHERE id = ?", (kid,))
    client.post("/einstellungen", data={"bereich": "finapi", "finapi_umgebung": "sandbox", "finapi_client_id": "cid",
                                        "finapi_client_secret": "geheim"})
    r = client.post(f"/bank/konto/{kid}/finapi/verbinden")
    assert r.headers["Location"] == "https://webform-sandbox.finapi.io/wf/wf1"
    with app.app_context():
        assert db.setting("finapi_user") == "casalo-user" and db.decrypt(db.setting("finapi_client_secret")) == "geheim"
        assert db.q("SELECT finapi_formular FROM konto WHERE id = ?", (kid,), one=True)[0] == "wf1"
    client.post(f"/bank/konto/{kid}/finapi/weiter")
    with app.app_context():
        k = db.q("SELECT * FROM konto WHERE id = ?", (kid,), one=True)
        assert (k["finapi_verbindung"], k["finapi_konto"], k["finapi_formular"]) == ("77", "9002", "")
        u = db.q("SELECT * FROM umsatz ORDER BY datum")
        assert [x["betrag_cent"] for x in u] == [47880, -13100]
    assert ("GET", "/api/tasks/t1") in bank_sim["aufrufe"]            # Aktualisierung bis zum Ende verfolgt
    # zweiter Abruf: nichts doppelt
    client.post(f"/bank/konto/{kid}/abruf")
    with app.app_context():
        assert db.q("SELECT COUNT(*) AS n FROM umsatz", one=True)["n"] == 2


def test_freigabe_noetig(client, app, weg, bank_sim):
    kid = weg["hg"]
    with app.app_context():
        db.ex("UPDATE konto SET zugang = 'finapi', finapi_verbindung = '77', finapi_konto = '9002' WHERE id = ?", (kid,))
        db.set_setting("finapi_client_id", "cid")
        db.set_setting("finapi_client_secret", db.encrypt("geheim"))
    bank_sim["update"] = "WEB_FORM_REQUIRED"
    r = client.post(f"/bank/konto/{kid}/abruf")
    assert r.headers["Location"] == "https://wf/wf2"
    with app.app_context():
        assert db.q("SELECT finapi_formular FROM konto WHERE id = ?", (kid,), one=True)[0] == "wf2"
    assert "Freigegeben – weiter" in client.get("/bank").get_data(as_text=True)


def test_fehlertext(app, bank_sim):
    with app.app_context():
        with pytest.raises(finapi.FinapiFehler, match="kaputt"):
            finapi._anfrage("GET", "https://sandbox.finapi.io/api/v2/fehler")
        with pytest.raises(finapi.FinapiFehler, match="nicht eingerichtet"):
            finapi._nutzer_token()
