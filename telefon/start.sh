#!/bin/sh
# Konfiguration aus Umgebungsvariablen erzeugen, dann Asterisk und Sprachbot starten.
set -e
: "${FRITZBOX_HOST:=fritz.box}" "${SIP_PORT:=5070}" "${RTP_VON:=10000}" "${RTP_BIS:=10100}"
# Casalo läuft im selben Stack: Adresse über den Port auf dem Server, Schlüssel aus dem gemeinsamen Volume
: "${CASALO_URL:=http://127.0.0.1:${CASALO_PORT:-8000}}"
# Casalo legt den Schlüssel beim Start an – kurz warten, falls der Telefon-Container schneller ist
n=0
while [ -z "${CASALO_TOKEN:-}" ] && [ ! -s /casalo-daten/telefon.token ] && [ $n -lt 60 ]; do sleep 2; n=$((n + 1)); done
if [ -z "${CASALO_TOKEN:-}" ] && [ -s /casalo-daten/telefon.token ]; then
  CASALO_TOKEN=$(cat /casalo-daten/telefon.token)
fi
export FRITZBOX_HOST SIP_PORT RTP_VON RTP_BIS CASALO_URL CASALO_TOKEN

fehlt=""
for v in SIP_BENUTZER SIP_PASSWORT CASALO_TOKEN DEEPGRAM_API_KEY ANTHROPIC_API_KEY; do
  eval "wert=\${$v:-}"
  [ -n "$wert" ] || fehlt="$fehlt $v"
done
if [ -n "$fehlt" ]; then
  echo "KI-Telefon nicht eingerichtet – es fehlt:$fehlt. Der Container wartet (kein Neustart-Kreislauf)."
  exec sleep infinity
fi

for f in /opt/casalo/asterisk/*.conf; do
  envsubst '${FRITZBOX_HOST} ${SIP_PORT} ${SIP_BENUTZER} ${SIP_PASSWORT} ${RTP_VON} ${RTP_BIS}' < "$f" > "/etc/asterisk/$(basename "$f")"
done
asterisk -f -U asterisk -G asterisk &
cd /opt/casalo
exec python3 -m sprachbot.haupt
