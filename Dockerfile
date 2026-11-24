# Casalo – WEG-Verwaltung. Ein Container, Daten (SQLite, Schlüssel) im Volume /data.
FROM python:3.12-slim AS basis
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY casalo ./casalo

# Tests laufen beim Bauen – schlägt einer fehl, entsteht kein Image.
FROM basis AS test
COPY tests ./tests
RUN pip install --no-cache-dir pytest && python -m pytest -q tests && touch /tests-ok

FROM basis
COPY --from=test /tests-ok /tests-ok
ARG APP_VERSION=dev
ENV APP_VERSION=$APP_VERSION DATA_DIR=/data PYTHONUNBUFFERED=1 TZ=Europe/Berlin
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/health')"
# Ein Prozess mit Threads: SQLite und der tägliche Bankabruf laufen so genau einmal.
CMD ["gunicorn", "-b", "0.0.0.0:8000", "-w", "1", "--threads", "8", "--timeout", "120", "casalo:create_app()"]
