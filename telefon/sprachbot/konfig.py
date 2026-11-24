"""Einstellungen aus Casalo holen (Fritzbox, Schlüssel) und Asterisk danach einrichten.

Der Container fragt jede Minute /api/telefon/konfig ab. Ändert sich der Fritzbox-Zugang, werden die
Asterisk-Dateien neu geschrieben und PJSIP neu geladen – ohne Neustart des Containers. Schlüssel für
Deepgram und Claude gelten ab dem nächsten Anruf.
"""
import asyncio
import logging
import os
import re
import subprocess

log = logging.getLogger("sprachbot.konfig")
VORLAGEN = os.environ.get("ASTERISK_VORLAGEN", "/opt/casalo/asterisk")
ZIEL = os.environ.get("ASTERISK_ZIEL", "/etc/asterisk")
SIP_FELDER = ("tel_fritzbox_host", "tel_sip_benutzer", "tel_sip_passwort", "tel_sip_port", "tel_rtp_von", "tel_rtp_bis")


def ausfuellen(text, werte):
    """${NAME} durch Werte ersetzen; Asterisk-Variablen (${CASALO_ID} usw.) bleiben stehen."""
    return re.sub(r"\$\{([A-Z_]+)\}", lambda m: str(werte[m.group(1)]) if m.group(1) in werte else m.group(0), text)


def asterisk_werte(k):
    return {"FRITZBOX_HOST": k.get("tel_fritzbox_host") or "fritz.box", "SIP_BENUTZER": k.get("tel_sip_benutzer", ""),
            "SIP_PASSWORT": k.get("tel_sip_passwort", ""), "SIP_PORT": k.get("tel_sip_port") or "5070",
            "RTP_VON": k.get("tel_rtp_von") or "10000", "RTP_BIS": k.get("tel_rtp_bis") or "10100"}


def dateien(k, vorlagen=VORLAGEN):
    """Inhalt aller Asterisk-Dateien. Ohne Fritzbox-Zugang nur der Transport (keine Anmeldung)."""
    werte = asterisk_werte(k)
    out = {}
    for name in sorted(os.listdir(vorlagen)):
        if not name.endswith(".conf"):
            continue
        with open(os.path.join(vorlagen, name)) as fh:
            text = fh.read()
        if name == "pjsip.conf" and not (werte["SIP_BENUTZER"] and werte["SIP_PASSWORT"]):
            text = text[:text.index("[fritzbox-registrierung]")] if "[fritzbox-registrierung]" in text else text
        out[name] = ausfuellen(text, werte)
    return out


def schreiben(k):
    for name, text in dateien(k).items():
        pfad = os.path.join(ZIEL, name)
        with open(pfad, "w") as fh:
            fh.write(text)
        os.chmod(pfad, 0o640)
        try:
            import shutil
            shutil.chown(pfad, "asterisk", "asterisk")
        except (LookupError, PermissionError, OSError):
            pass


def cli(befehl):
    try:
        return subprocess.run(["asterisk", "-rx", befehl], capture_output=True, text=True, timeout=15).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def angemeldet():
    """(angemeldet, text) aus 'pjsip show registrations'."""
    aus = cli("pjsip show registrations")
    if "Registered" in aus:
        return True, "an der Fritzbox angemeldet"
    for zeile in aus.splitlines():
        if "fritzbox-registrierung" in zeile and "/" in zeile:
            return False, "Anmeldung: " + " ".join(zeile.split()[1:])[:200]
    return False, "keine Anmeldung eingerichtet" if not aus else "Anmeldung läuft noch"


class Verwaltung:
    """Hält die aktuellen Einstellungen und richtet Asterisk ein."""

    def __init__(self, casalo):
        self.casalo = casalo
        self.k = {}
        self.asterisk = None

    def wert(self, name, standard=""):
        return self.k.get(name) or standard

    @property
    def bereit(self):
        return bool(self.k.get("vollstaendig"))

    async def laden(self):
        neu = await self.casalo.konfig()
        sip_geaendert = any(neu.get(f) != self.k.get(f) for f in SIP_FELDER)
        self.k = neu
        if sip_geaendert:
            schreiben(neu)
            if self.asterisk and self.asterisk.poll() is None:
                cli("module reload res_pjsip.so")
                cli("module reload res_rtp_asterisk.so")
                log.info("Fritzbox-Zugang geändert – Asterisk neu geladen")
        return sip_geaendert

    def asterisk_starten(self):
        if self.asterisk is None or self.asterisk.poll() is not None:
            self.asterisk = subprocess.Popen(["asterisk", "-f", "-U", "asterisk", "-G", "asterisk"])
            log.info("Asterisk gestartet")

    async def schleife(self, intervall=60):
        while True:
            await asyncio.sleep(intervall)
            try:
                await self.laden()
                self.asterisk_starten()            # falls Asterisk abgestürzt ist
                ok, text = angemeldet()
                if not self.k.get("tel_sip_benutzer"):
                    ok, text = False, "Fritzbox-Zugang in Casalo noch nicht eingetragen"
                await self.casalo.status(ok, text)
            except Exception as e:  # Casalo kurz nicht erreichbar o. ä.
                log.warning("Einstellungen nicht geladen: %s", e)
