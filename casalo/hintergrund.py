"""Hintergrundaufgaben: täglicher Bankabruf (FinTS und finAPI) um ABRUF_UHRZEIT (Standard 06:00)."""
import datetime
import logging
import os
import threading
import time

log = logging.getLogger("casalo.hintergrund")
_gestartet = False


def _naechster(jetzt, uhrzeit):
    h, m = (int(x) for x in uhrzeit.split(":"))
    ziel = jetzt.replace(hour=h, minute=m, second=0, microsecond=0)
    return ziel if ziel > jetzt else ziel + datetime.timedelta(days=1)


def alle_abrufen(app):
    from . import bank, db, finapi
    from .v_bank import abrufen, finapi_abrufen
    with app.app_context():
        for k in db.q("SELECT * FROM konto WHERE zugang = 'finapi' AND finapi_konto != '' AND finapi_formular = ''"):
            try:
                neu, auto = finapi_abrufen(k)
                log.info("Konto %s (finAPI): %s neu, %s gebucht", k["iban"], neu, auto)
            except finapi.FormularNoetig:
                log.info("Konto %s (finAPI): Bank verlangt Freigabe – wartet auf Bestätigung in Casalo", k["iban"])
            except Exception as e:  # ein Konto darf die anderen nicht aufhalten
                log.warning("Konto %s (finAPI): Abruf fehlgeschlagen: %s", k["iban"], e)
        for k in db.q("SELECT * FROM konto WHERE zugang = 'fints' AND fints_pin != '' AND fints_zustand = ''"):
            try:
                neu, auto = abrufen(k)
                log.info("Konto %s: %s neu, %s gebucht", k["iban"], neu, auto)
            except bank.TanNoetig:
                log.info("Konto %s: Bank verlangt Freigabe – wartet auf Bestätigung in Casalo", k["iban"])
            except Exception as e:  # ein Konto darf die anderen nicht aufhalten
                log.warning("Konto %s: Abruf fehlgeschlagen: %s", k["iban"], e)


def starten(app):
    global _gestartet
    if _gestartet:
        return
    _gestartet = True
    uhrzeit = os.environ.get("ABRUF_UHRZEIT", "06:00")

    def schleife():
        while True:
            warte = (_naechster(datetime.datetime.now(), uhrzeit) - datetime.datetime.now()).total_seconds()
            time.sleep(max(30, warte))
            try:
                alle_abrufen(app)
            except Exception as e:
                log.warning("Bankabruf: %s", e)

    threading.Thread(target=schleife, name="bankabruf", daemon=True).start()
