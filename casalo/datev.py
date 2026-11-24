"""DATEV-Export: Buchungsstapel im Format EXTF (Version 700, Kategorie 21) für den Steuerberater.

Jede Buchung wird vom Bankkonto der Gemeinschaft aus gebucht: Konto = Finanzkonto der Bank, Gegenkonto = Sachkonto
(Kostenart, Hausgeld-, Umlagen- oder Sammelkonto). Soll = Geld kommt auf das Bankkonto, Haben = Geld geht ab.
"""
import datetime

from . import daten, db

SPALTEN = ["Umsatz (ohne Soll/Haben-Kz)", "Soll/Haben-Kennzeichen", "WKZ Umsatz", "Kurs", "Basis-Umsatz", "WKZ Basis-Umsatz",
           "Konto", "Gegenkonto (ohne BU-Schlüssel)", "BU-Schlüssel", "Belegdatum", "Belegfeld 1", "Belegfeld 2", "Skonto",
           "Buchungstext"]
STANDARD_BANK = {"hausgeld": "1200", "ruecklage": "1210"}


def _text(t, laenge):
    t = (t or "").replace('"', "'").replace("\n", " ").replace(";", ",")
    return '"' + t[:laenge] + '"'


def _betrag(cent):
    return f"{abs(cent) // 100},{abs(cent) % 100:02d}"


def pruefen(gid):
    """Fehlende Angaben, die der Steuerberater sonst nachfragt."""
    hinweise = []
    if not db.setting("datev_berater") or not db.setting("datev_mandant"):
        hinweise.append("Beraternummer und Mandantennummer fehlen (Einstellungen → DATEV).")
    for k in db.q("SELECT * FROM kostenart WHERE gemeinschaft_id = ? AND datev_konto = ''", (gid,)):
        hinweise.append(f"Kostenart „{k['name']}“ hat kein Sachkonto – es wird {db.setting('datev_kosten_standard')} verwendet.")
    return hinweise


def buchungsstapel(gid, jahr, jetzt=None):
    """→ (dateiname, bytes in Windows-1252)"""
    jetzt = jetzt or datetime.datetime.now()
    g = daten.gemeinschaft(gid)
    von, bis = f"{jahr}-01-01", f"{jahr}-12-31"
    konten = {k["id"]: k for k in db.q("SELECT * FROM konto WHERE gemeinschaft_id = ?", (gid,))}
    hausgeld_konto = next((k for k in konten.values() if k["art"] == "hausgeld"), None)
    kas = {k["id"]: k for k in db.q("SELECT * FROM kostenart WHERE gemeinschaft_id = ?", (gid,))}
    einheiten = {e["id"]: e for e in daten.einheiten(gid)}
    belege = {}
    for b in db.q("SELECT buchung_id, MIN(nr) AS nr FROM beleg WHERE gemeinschaft_id = ? AND buchung_id IS NOT NULL GROUP BY buchung_id", (gid,)):
        belege[b["buchung_id"]] = b["nr"]
    zeilen = []
    for b in db.q("SELECT * FROM buchung WHERE gemeinschaft_id = ? AND datum BETWEEN ? AND ? ORDER BY datum, id", (gid, von, bis)):
        if not b["betrag_cent"]:
            continue
        k = konten.get(b["konto_id"]) or hausgeld_konto
        bank = (k["datev_konto"] if k and k["datev_konto"] else STANDARD_BANK.get(k["art"] if k else "hausgeld", "1200"))
        if b["art"] == "kosten":
            ka = kas.get(b["kostenart_id"])
            gegen = (ka["datev_konto"] if ka and ka["datev_konto"] else db.setting("datev_kosten_standard"))
        elif b["art"] == "hausgeld":
            gegen = db.setting("datev_erloes_hausgeld")
        elif b["art"] == "sonderumlage":
            gegen = db.setting("datev_erloes_umlage")
        else:
            gegen = db.setting("datev_konto_sonstig")
        datum = datetime.date.fromisoformat(b["datum"])
        e = einheiten.get(b["einheit_id"])
        zeilen.append(";".join([
            _betrag(b["betrag_cent"]), '"S"' if b["betrag_cent"] > 0 else '"H"', '"EUR"', "", "", "",
            bank, gegen, "", f"{datum:%d%m}", _text(belege.get(b["id"]) or str(b["id"]), 36), _text(e["nr"] if e else "", 12),
            "", _text(b["text"], 60)]))
    kopf = ['"EXTF"', "700", "21", '"Buchungsstapel"', "13", f"{jetzt:%Y%m%d%H%M%S}{jetzt.microsecond // 1000:03d}", "", '"CA"', '""', '""',
            db.setting("datev_berater") or "0", db.setting("datev_mandant") or "0", f"{jahr}0101", "4",
            f"{jahr}0101", f"{jahr}1231", _text(f"Casalo WEG {g['name']} {jahr}", 30), '""', "1", "0", "0", '"EUR"',
            "", '""', "", "", '""', '""']
    text = "\r\n".join([";".join(kopf), ";".join(SPALTEN)] + zeilen) + "\r\n"
    name = f"EXTF_Buchungsstapel_{g['nr'] or g['id']}_{jahr}.csv".replace(" ", "_").replace("/", "-")
    return name, text.encode("cp1252", errors="replace")
