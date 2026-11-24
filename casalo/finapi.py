"""finAPI (Access + Web Form 2.0) für Banken ohne FinTS-Zugang.

Ablauf: Casalo legt bei finAPI einen Nutzer für die Verwaltung an. Für ein Konto öffnet ein Web-Formular von finAPI die
Anmeldung bei der Bank (die Zugangsdaten sieht Casalo nie). Danach holt Casalo die Umsätze über die Access-Schnittstelle;
täglich stößt es eine Hintergrund-Aktualisierung an. Verlangt die Bank eine neue Freigabe (PSD2, spätestens nach 90 Tagen),
kommt wieder ein Web-Formular.
"""
import datetime
import secrets
import time

import requests

from . import bank, db

HOSTS = {"sandbox": ("https://sandbox.finapi.io", "https://webform-sandbox.finapi.io"),
         "live": ("https://live.finapi.io", "https://webform-live.finapi.io")}
TIMEOUT = 30
WARTEN_S = 90
schlafen = time.sleep          # in Tests ersetzt


class FinapiFehler(Exception):
    pass


class FormularNoetig(Exception):
    """Die Bank verlangt eine Eingabe des Nutzers; url führt zum Web-Formular."""
    def __init__(self, url, formular_id):
        super().__init__("Die Bank verlangt eine Freigabe im finAPI-Formular.")
        self.url, self.formular_id = url, formular_id


def eingerichtet():
    return bool(db.setting("finapi_client_id") and db.setting("finapi_client_secret"))


def _hosts():
    return HOSTS["live" if db.setting("finapi_umgebung") == "live" else "sandbox"]


def _anfrage(methode, url, **kw):
    """Eine HTTP-Anfrage; Fehler von finAPI kommen als verständlicher Text zurück."""
    try:
        r = requests.request(methode, url, timeout=TIMEOUT, **kw)
    except requests.RequestException as e:
        raise FinapiFehler(f"finAPI nicht erreichbar: {e}")
    if r.status_code >= 400:
        try:
            meld = "; ".join(x.get("message", "") for x in r.json().get("errors", [])) or r.text[:200]
        except ValueError:
            meld = r.text[:200]
        raise FinapiFehler(f"finAPI ({r.status_code}): {meld}")
    return r.json() if r.content else {}


def _token(**daten):
    access, _ = _hosts()
    return _anfrage("POST", f"{access}/api/v2/oauth/token",
                    data={"client_id": db.setting("finapi_client_id"),
                          "client_secret": db.decrypt(db.setting("finapi_client_secret")), **daten})["access_token"]


def _nutzer_token():
    """Token des finAPI-Nutzers der Verwaltung; legt den Nutzer beim ersten Mal an."""
    if not eingerichtet():
        raise FinapiFehler("finAPI ist nicht eingerichtet (Einstellungen → finAPI).")
    if not db.setting("finapi_user"):
        access, _ = _hosts()
        pw = secrets.token_urlsafe(24)
        u = _anfrage("POST", f"{access}/api/v2/users", json={"password": pw},
                     headers={"Authorization": f"Bearer {_token(grant_type='client_credentials')}"})
        db.set_setting("finapi_user", u["id"])
        db.set_setting("finapi_user_pw", db.encrypt(pw))
    return _token(grant_type="password", username=db.setting("finapi_user"), password=db.decrypt(db.setting("finapi_user_pw")))


def _kopf(token):
    return {"Authorization": f"Bearer {token}"}


def formular_verbinden(konto):
    """Neues Web-Formular, um die Bank anzubinden. → (url, formular_id)"""
    _, wf = _hosts()
    r = _anfrage("POST", f"{wf}/api/webForms/bankConnectionImport", headers=_kopf(_nutzer_token()),
                 json={"accountTypes": ["CHECKING", "SAVINGS"]})
    return r["url"], r["id"]


def formular_ergebnis(formular_id):
    """Status eines Web-Formulars. → (status, bankConnectionId oder None)"""
    _, wf = _hosts()
    r = _anfrage("GET", f"{wf}/api/webForms/{formular_id}", headers=_kopf(_nutzer_token()))
    return r.get("status"), (r.get("payload") or {}).get("bankConnectionId")


def konto_zuordnen(konto, verbindung_id):
    """Konto der Bankverbindung mit derselben IBAN suchen. → accountId"""
    access, _ = _hosts()
    r = _anfrage("GET", f"{access}/api/v2/accounts", headers=_kopf(_nutzer_token()), params={"bankConnectionIds": verbindung_id})
    ziel = bank.iban_normal(konto["iban"])
    for a in r.get("accounts", []):
        if bank.iban_normal(a.get("iban")) == ziel:
            return str(a["id"])
    ibans = ", ".join(bank.iban_format(a.get("iban") or "") for a in r.get("accounts", []))
    raise FinapiFehler(f"In der Bankverbindung ist kein Konto mit der IBAN {bank.iban_format(ziel)} (gefunden: {ibans or 'keins'}).")


def aktualisieren(konto):
    """Hintergrund-Aktualisierung bei der Bank anstoßen; wirft FormularNoetig, wenn die Bank eine Freigabe will."""
    _, wf = _hosts()
    r = _anfrage("POST", f"{wf}/api/tasks/backgroundUpdate", headers=_kopf(_nutzer_token()),
                 json={"bankConnectionIds": [int(konto["finapi_verbindung"])]})
    for _ in range(WARTEN_S // 3):           # die Bank antwortet nicht sofort – Aufgabe bis zum Ende verfolgen
        if r.get("status") == "WEB_FORM_REQUIRED":
            form = (r.get("payload") or {}).get("webForm") or {}
            raise FormularNoetig(form.get("url", ""), form.get("id", ""))
        if r.get("status") in ("COMPLETED", "COMPLETED_WITH_ERROR"):
            return r
        schlafen(3)
        r = _anfrage("GET", f"{wf}/api/tasks/{r['id']}", headers=_kopf(_nutzer_token()))
    return r


def umsaetze(konto, von):
    """Umsätze des Kontos ab Datum, im Format von bank.lese_camt."""
    access, _ = _hosts()
    tok = _nutzer_token()
    out, seite = [], 1
    while True:
        r = _anfrage("GET", f"{access}/api/v2/transactions", headers=_kopf(tok),
                     params={"view": "userView", "accountIds": konto["finapi_konto"], "minBankBookingDate": von.isoformat(),
                             "perPage": 500, "page": seite})
        for t in r.get("transactions", []):
            betrag = round(float(t["amount"]) * 100)
            out.append({"datum": t.get("bankBookingDate") or t.get("valueDate"), "betrag_cent": betrag,
                        "name": t.get("counterpartName") or "", "iban": bank.iban_normal(t.get("counterpartIban")),
                        "zweck": t.get("purpose") or "", "eigene_iban": "", "extra": f"finapi:{t['id']}"})
        paging = r.get("paging") or {}
        if seite >= paging.get("pageCount", 1):
            return out
        seite += 1


def abrufen_daten(konto):
    """Aktualisieren und Umsätze seit dem letzten Abruf (minus 10 Tage) holen."""
    aktualisieren(konto)
    bis = datetime.date.today()
    von = datetime.date.fromisoformat(konto["letzter_abruf"][:10]) - datetime.timedelta(days=10) if konto["letzter_abruf"] \
        else bis - datetime.timedelta(days=90)
    return umsaetze(konto, von)
