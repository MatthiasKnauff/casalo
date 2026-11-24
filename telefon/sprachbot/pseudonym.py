"""Namen und Adressen ersetzen, bevor Text an die KI geht – und in der Antwort zurücktauschen.

Bekannte Werte (aus Casalo: Name des Anrufers, Adressen der Gemeinschaften) werden durch Kennungen
wie [Person 1] oder [Adresse 2] ersetzt. Was der Anrufer selbst sagt, wird mit denselben Werten
bereinigt; unbekannte Namen, die er nennt, kann niemand vorher kennen.
"""
import re


class Pseudonymisierer:
    def __init__(self, aktiv=True):
        self.aktiv = aktiv
        self._hin = {}          # klartext -> kennung
        self._rueck = {}        # kennung -> klartext
        self._zaehler = {}

    def merken(self, wert, art="Person"):
        wert = (wert or "").strip()
        if not self.aktiv or len(wert) < 3 or wert in self._hin:
            return self._hin.get(wert, wert)
        n = self._zaehler.get(art, 0) + 1
        self._zaehler[art] = n
        k = f"[{art} {n}]"
        self._hin[wert] = k
        self._rueck[k] = wert
        return k

    def kontext_merken(self, k):
        """Alle schützenswerten Felder aus dem Kontext von Casalo eintragen."""
        a = k.get("anrufer") or {}
        if a.get("vorname") and a.get("nachname"):
            self.merken(f"{a['vorname']} {a['nachname']}", "Person")
        if a.get("nachname"):
            self.merken(a["nachname"], "Person")
        if a.get("vorname"):
            self.merken(a["vorname"], "Vorname")
        for g in k.get("gemeinschaften", []):
            adr = g.get("adresse") or ""
            self.merken(adr, "Adresse")
            strasse = adr.split(",")[0].strip()
            self.merken(strasse, "Adresse")
        for e in k.get("einheiten", []):
            self.merken(e.get("gemeinschaft"), "Adresse")
            self.merken((e.get("gemeinschaft") or "").split(",")[0].strip(), "Adresse")

    def hin(self, text):
        if not self.aktiv or not text:
            return text
        # längste Werte zuerst, damit „Sabine Becker“ vor „Becker“ ersetzt wird
        for wert in sorted(self._hin, key=len, reverse=True):
            text = re.sub(r"(?<!\w)" + re.escape(wert) + r"(?!\w)", self._hin[wert], text, flags=re.IGNORECASE)
        return text

    def rueck(self, text):
        if not self.aktiv or not text:
            return text
        for k, wert in self._rueck.items():
            text = text.replace(k, wert)
        return text

    def objekt_hin(self, obj):
        """Rekursiv für den Kontext (dict/list/str)."""
        if isinstance(obj, str):
            return self.hin(obj)
        if isinstance(obj, list):
            return [self.objekt_hin(x) for x in obj]
        if isinstance(obj, dict):
            return {k: self.objekt_hin(v) for k, v in obj.items()}
        return obj
