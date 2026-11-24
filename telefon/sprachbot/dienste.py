"""Verbindungen nach außen: Casalo-Schnittstelle und Claude (Messages API mit Werkzeugen)."""
import os


class Casalo:
    def __init__(self, url=None, token=None):
        import httpx
        self.url = (url or os.environ["CASALO_URL"]).rstrip("/")
        self._token = token or os.environ.get("CASALO_TOKEN", "")
        self.http = httpx.AsyncClient(timeout=15, verify=os.environ.get("CASALO_TLS_PRUEFEN", "1") == "1",
                                      event_hooks={"request": [self._schluessel]})

    async def _schluessel(self, request):
        """Schlüssel bei jeder Anfrage aus dem gemeinsamen Volume lesen – ein neuer Schlüssel gilt sofort."""
        tok = self._token
        try:
            with open(os.environ.get("CASALO_TOKEN_DATEI", "/casalo-daten/telefon.token")) as fh:
                tok = fh.read().strip() or tok
        except OSError:
            pass
        request.headers["X-Casalo-Token"] = tok

    async def _post(self, pfad, daten):
        r = await self.http.post(self.url + pfad, json=daten)
        r.raise_for_status()
        return r.json()

    async def start(self, nummer):
        return await self._post("/api/telefon/anruf", {"nummer": nummer})

    async def kontext(self, aid):
        r = await self.http.get(f"{self.url}/api/telefon/anruf/{aid}/kontext")
        r.raise_for_status()
        return r.json()

    async def pruefen(self, aid, einheit, plz):
        return await self._post(f"/api/telefon/anruf/{aid}/pruefen", {"einheit": einheit, "plz": plz})

    async def vorgang(self, aid, titel, text, notfallart=""):
        return await self._post(f"/api/telefon/anruf/{aid}/vorgang", {"titel": titel, "text": text, "notfallart": notfallart})

    async def ende(self, aid, **daten):
        return await self._post(f"/api/telefon/anruf/{aid}/ende", daten)

    async def ki_protokoll(self, aid, inhalt):
        try:
            await self._post(f"/api/telefon/anruf/{aid}/ki", {"inhalt": inhalt})
        except Exception:
            pass


class Claude:
    URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, schluessel=None, modell=None):
        import httpx
        self.modell = modell or os.environ.get("CLAUDE_MODELL", "claude-haiku-5-5")
        self.http = httpx.AsyncClient(timeout=30, headers={
            "x-api-key": schluessel or os.environ["ANTHROPIC_API_KEY"],
            "anthropic-version": "2023-06-01", "content-type": "application/json"})

    async def antwort(self, system, nachrichten, werkzeuge):
        r = await self.http.post(self.URL, json={"model": self.modell, "max_tokens": 400, "system": system,
                                                 "messages": nachrichten, "tools": werkzeuge})
        r.raise_for_status()
        return r.json()
