# Casalo-Telefon: Asterisk (an der Fritzbox) + Sprachbot (Deepgram → Claude → Piper).
# Läuft mit network_mode: host, weil SIP und RTP (UDP) direkt erreichbar sein müssen.
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 TZ=Europe/Berlin
RUN apt-get update \
 && apt-get install -y --no-install-recommends asterisk asterisk-modules gettext-base python3 python3-venv ca-certificates curl tzdata \
 && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
COPY requirements.txt /opt/casalo/requirements.txt
RUN pip install --no-cache-dir -r /opt/casalo/requirements.txt
# Deutsche Stimme für Piper (Thorsten, mittlere Qualität) – beim Bauen geladen, läuft danach offline
ARG PIPER_STIMME=de/de_DE/thorsten/medium/de_DE-thorsten-medium
RUN mkdir -p /opt/piper \
 && curl -fsSL -o /opt/piper/stimme.onnx "https://huggingface.co/rhasspy/piper-voices/resolve/main/${PIPER_STIMME}.onnx" \
 && curl -fsSL -o /opt/piper/stimme.onnx.json "https://huggingface.co/rhasspy/piper-voices/resolve/main/${PIPER_STIMME}.onnx.json"
COPY asterisk /opt/casalo/asterisk
COPY sprachbot /opt/casalo/sprachbot
COPY tests /opt/casalo/tests
RUN cd /opt/casalo && pip install --no-cache-dir pytest && python -m pytest -q tests && pip uninstall -y pytest
COPY start.sh /opt/casalo/start.sh
ARG APP_VERSION=dev
ENV APP_VERSION=$APP_VERSION
CMD ["/opt/casalo/start.sh"]
