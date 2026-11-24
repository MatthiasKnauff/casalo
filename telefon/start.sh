#!/bin/sh
# Casalo-Telefon starten. Fritzbox-Zugang und Schlüssel kommen aus Casalo (KI-Telefon → Einstellungen),
# der Sprachbot schreibt daraus die Asterisk-Dateien und startet Asterisk selbst.
set -e
# Casalo läuft im selben Stack: Adresse über den Port auf dem Server, Schlüssel aus dem gemeinsamen Volume
: "${CASALO_URL:=http://127.0.0.1:${CASALO_PORT:-8000}}"
export CASALO_URL
n=0
while [ -z "${CASALO_TOKEN:-}" ] && [ ! -s /casalo-daten/telefon.token ] && [ $n -lt 60 ]; do sleep 2; n=$((n + 1)); done
cd /opt/casalo
exec python3 -m sprachbot.haupt
