"""Regeln rund um die Eigentümerversammlung (WEG in der Fassung ab 01.12.2020)."""
import datetime

EINLADUNGSFRIST_TAGE = 21          # § 24 Abs. 4 Satz 2 WEG: mindestens drei Wochen

MEHRHEITEN = {
    "keine": "kein Beschluss",
    "einfach": "einfache Mehrheit",
    "doppelt": "mehr als 2/3 der abgegebenen Stimmen und mehr als die Hälfte aller MEA",
    "allstimmig": "alle Eigentümer",
}


def parse_beginn(text):
    return datetime.datetime.fromisoformat(text)


def einladung_bis(beginn):
    """Letzter Tag, an dem die Einladung zugehen muss."""
    return (beginn.date() if isinstance(beginn, datetime.datetime) else beginn) - datetime.timedelta(days=EINLADUNGSFRIST_TAGE)


def frist_status(beginn, versandt, heute=None):
    """(art, text) – art: ok | warnung | fehler."""
    heute = heute or datetime.date.today()
    bis = einladung_bis(beginn)
    if versandt:
        v = datetime.date.fromisoformat(versandt[:10])
        if v <= bis:
            return "ok", f"Einladung am {v:%d.%m.%Y} versandt – Frist gewahrt."
        return "fehler", (f"Einladung erst am {v:%d.%m.%Y} versandt, nötig bis {bis:%d.%m.%Y}. "
                          "Nur bei besonderer Dringlichkeit zulässig.")
    tage = (bis - heute).days
    if tage < 0:
        return "fehler", f"Einladungsfrist am {bis:%d.%m.%Y} verpasst – Termin verschieben oder Dringlichkeit begründen."
    if tage <= 7:
        return "warnung", f"Einladung spätestens am {bis:%d.%m.%Y} versenden (noch {tage} Tage)."
    return "ok", f"Einladung bis {bis:%d.%m.%Y} versenden."


def auswerten(mehrheit, ja, nein, enthaltung=0, ja_mea=None, gesamt_mea=1000.0):
    """Ergebnis eines Beschlusses: 'angenommen' | 'abgelehnt' | '' (kein Beschluss)."""
    if mehrheit == "keine" or ja is None or nein is None:
        return ""
    ja, nein, enthaltung = float(ja), float(nein), float(enthaltung or 0)
    if mehrheit == "einfach":
        return "angenommen" if ja > nein else "abgelehnt"
    if mehrheit == "doppelt":
        abgegeben = ja + nein
        if ja_mea is None:
            return ""
        ok = abgegeben > 0 and ja > abgegeben * 2 / 3 and float(ja_mea) > gesamt_mea / 2
        return "angenommen" if ok else "abgelehnt"
    if mehrheit == "allstimmig":
        return "angenommen" if nein == 0 and enthaltung == 0 and ja > 0 else "abgelehnt"
    return ""


def beschluss_nr(jahr, lfd):
    return f"{lfd} ({jahr})"


def umlauf_auswerten(mehrheit, stimmen, berechtigte):
    """Umlaufbeschluss (§ 23 Abs. 3 WEG). stimmen: {person_id: ja|nein|enthaltung}, berechtigte: {person_id}.

    allstimmig: angenommen nur, wenn jeder Berechtigte in Textform zugestimmt hat.
    einfach (nur nach Ermächtigungsbeschluss, § 23 Abs. 3 Satz 2): mehr Ja- als Nein-Stimmen der abgegebenen; Köpfe.
    """
    gueltig = {p: s for p, s in stimmen.items() if p in berechtigte}
    if mehrheit == "allstimmig":
        return "angenommen" if berechtigte and all(gueltig.get(p) == "ja" for p in berechtigte) else "abgelehnt"
    ja = sum(1 for s in gueltig.values() if s == "ja")
    nein = sum(1 for s in gueltig.values() if s == "nein")
    return "angenommen" if ja > nein else "abgelehnt"
