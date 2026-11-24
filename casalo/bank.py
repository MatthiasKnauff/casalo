"""Bank: Umsätze einlesen (FinTS, CAMT, CSV), zuordnen, SEPA-Lastschriften erzeugen."""
import csv
import datetime
import hashlib
import io
import re
import unicodedata
import xml.etree.ElementTree as ET
from decimal import Decimal

from .rechnen import parse_betrag


# ------------------------------------------------------------------ IBAN
def iban_normal(iban):
    return re.sub(r"\s+", "", iban or "").upper()


def iban_gueltig(iban):
    i = iban_normal(iban)
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{10,30}", i):
        return False
    umgestellt = i[4:] + i[:4]
    zahl = "".join(str(int(c, 36)) for c in umgestellt)
    return int(zahl) % 97 == 1


def iban_format(iban):
    i = iban_normal(iban)
    return " ".join(i[n:n + 4] for n in range(0, len(i), 4))


def umsatz_ref(konto_id, datum, betrag, name, zweck, extra=""):
    """Eindeutiger Schlüssel – verhindert doppelte Umsätze bei erneutem Abruf."""
    roh = f"{konto_id}|{datum}|{betrag}|{name.strip()}|{zweck.strip()}|{extra}"
    return hashlib.sha256(roh.encode()).hexdigest()[:32]


# ------------------------------------------------------------------ CAMT.053 / CAMT.052
def _ohne_ns(tree):
    for el in tree.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return tree


def _t(el, pfad):
    x = el.find(pfad)
    return (x.text or "").strip() if x is not None and x.text else ""


def lese_camt(daten):
    """CAMT.053 (Kontoauszug) oder CAMT.052 einlesen. Gibt Liste von Umsätzen (dict) zurück."""
    root = _ohne_ns(ET.fromstring(daten))
    out = []
    for stmt in list(root.iter("Stmt")) + list(root.iter("Rpt")):
        eigene_iban = _t(stmt, "Acct/Id/IBAN")
        for ntry in stmt.findall("Ntry"):
            if _t(ntry, "Sts") in ("PDNG",) or _t(ntry, "Sts/Cd") == "PDNG":
                continue
            betrag = parse_betrag(_t(ntry, "Amt"))
            if _t(ntry, "CdtDbtInd") == "DBIT":
                betrag = -betrag
            datum = _t(ntry, "BookgDt/Dt") or _t(ntry, "BookgDt/DtTm")[:10] or _t(ntry, "ValDt/Dt")
            txs = ntry.findall("NtryDtls/TxDtls") or [None]
            for tx in txs:
                src = tx if tx is not None else ntry
                if _t(ntry, "CdtDbtInd") == "DBIT":
                    name = _t(src, "RltdPties/Cdtr/Nm") or _t(src, "RltdPties/Cdtr/Pty/Nm")
                    iban = _t(src, "RltdPties/CdtrAcct/Id/IBAN")
                else:
                    name = _t(src, "RltdPties/Dbtr/Nm") or _t(src, "RltdPties/Dbtr/Pty/Nm")
                    iban = _t(src, "RltdPties/DbtrAcct/Id/IBAN")
                zweck = " ".join(x.text.strip() for x in src.findall("RmtInf/Ustrd") if x.text) or _t(ntry, "AddtlNtryInf")
                tx_betrag = betrag
                if tx is not None and len(txs) > 1 and _t(tx, "AmtDtls/TxAmt/Amt"):
                    tx_betrag = parse_betrag(_t(tx, "AmtDtls/TxAmt/Amt")) * (-1 if betrag < 0 else 1)
                out.append({"datum": datum, "betrag_cent": tx_betrag, "name": name, "iban": iban_normal(iban),
                            "zweck": zweck, "eigene_iban": iban_normal(eigene_iban),
                            "extra": _t(ntry, "AcctSvcrRef") + _t(src, "Refs/EndToEndId")})
    return out


# ------------------------------------------------------------------ CSV (Bank-Export)
CSV_SPALTEN = {
    "datum": ("buchungstag", "buchungsdatum", "datum", "valutadatum", "wertstellung"),
    "betrag": ("betrag", "betrag (€)", "betrag (eur)", "umsatz"),
    "name": ("beguenstigter/zahlungspflichtiger", "name zahlungsbeteiligter", "auftraggeber/empfänger",
             "empfänger", "auftraggeber", "zahlungsempfänger", "name"),
    "iban": ("kontonummer/iban", "iban zahlungsbeteiligter", "iban", "kontonummer"),
    "zweck": ("verwendungszweck", "buchungstext", "zweck"),
}


def _norm(s):
    return unicodedata.normalize("NFC", (s or "").strip().strip('"').lower())


def _datum(text):
    t = (text or "").strip()
    for f in ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(t, f).date().isoformat()
        except ValueError:
            pass
    raise ValueError(f"Datum „{text}“ nicht erkannt.")


def lese_csv(text):
    """CSV-Export der Bank (Sparkasse, Volksbank, … – Spalten werden am Namen erkannt)."""
    probe = text[:4096]
    trenner = ";" if probe.count(";") >= probe.count(",") else ","
    zeilen = list(csv.reader(io.StringIO(text), delimiter=trenner))
    kopf_idx = next((i for i, z in enumerate(zeilen[:30])
                     if any(_norm(c) in CSV_SPALTEN["betrag"] for c in z)
                     and any(_norm(c) in CSV_SPALTEN["datum"] for c in z)), None)
    if kopf_idx is None:
        raise ValueError("In der CSV fehlen erkennbare Spalten für Datum und Betrag.")
    kopf = [_norm(c) for c in zeilen[kopf_idx]]

    def spalte(feld):
        for name in CSV_SPALTEN[feld]:
            if name in kopf:
                return kopf.index(name)
        return None

    idx = {f: spalte(f) for f in CSV_SPALTEN}
    out = []
    for z in zeilen[kopf_idx + 1:]:
        if not z or all(not c.strip() for c in z):
            continue
        def w(f):
            i = idx[f]
            return z[i] if i is not None and i < len(z) else ""
        try:
            out.append({"datum": _datum(w("datum")), "betrag_cent": parse_betrag(w("betrag")), "name": w("name").strip(),
                        "iban": iban_normal(w("iban")), "zweck": w("zweck").strip(), "eigene_iban": "", "extra": ""})
        except ValueError:
            continue
    return out


# ------------------------------------------------------------------ Zuordnung
def _woerter(text):
    t = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return set(re.findall(r"[a-z]{3,}", t))


def _nr_varianten(nr):
    """'W 03' → {'w03', 'w3', 'we3', 'wohnung3', 'whg3', 'nr3', 'einheit3'}"""
    ziffern = re.sub(r"\D", "", nr or "").lstrip("0") or "0"
    kompakt = re.sub(r"\s+", "", (nr or "").lower())
    return {kompakt, re.sub(r"0+(\d)", r"\1", kompakt)} | {p + ziffern for p in ("w", "we", "wohnung", "whg", "einheit", "nr", "tg", "sp")}


def _zweck_hat_nr(zweck, nr):
    kompakt = re.sub(r"[\s.\-/:#]+", "", (zweck or "").lower())
    for v in _nr_varianten(nr):
        if len(v) >= 2 and re.search(re.escape(v) + r"(?!\d)", kompakt):
            return True
    return False


def vorschlag_eingang(u, kandidaten):
    """Eingang einer Einheit zuordnen.

    kandidaten: [{einheit_id, nr, eigentuemer: [{nachname, firma, iban}], monat_cent}]
    Ergebnis: {"art": "hausgeld", "einheit_id", "text", "sicherheit": sicher|wahrscheinlich|unklar, "punkte"} oder None
    """
    bewertet = []
    name_w = _woerter(u["name"])
    for k in kandidaten:
        p, gruende = 0, []
        ibans = {iban_normal(e.get("iban")) for e in k["eigentuemer"] if e.get("iban")}
        if u.get("iban") and u["iban"] in ibans:
            p += 60
            gruende.append("IBAN des Eigentümers")
        namen = set()
        for e in k["eigentuemer"]:
            namen |= _woerter(e.get("nachname")) | _woerter(e.get("firma"))
        if namen and namen & name_w:
            p += 25
            gruende.append("Name")
        if _zweck_hat_nr(u.get("zweck"), k["nr"]):
            p += 25
            gruende.append("Einheit im Verwendungszweck")
        m = k.get("monat_cent") or 0
        if m and u["betrag_cent"] > 0 and u["betrag_cent"] % m == 0 and u["betrag_cent"] // m <= 12:
            p += 20
            anz = u["betrag_cent"] // m
            gruende.append("Betrag = Hausgeld" if anz == 1 else f"Betrag = {anz} × Hausgeld")
        if p:
            bewertet.append((p, k, gruende))
    if not bewertet:
        return None
    bewertet.sort(key=lambda x: -x[0])
    p, k, gruende = bewertet[0]
    zweiter = bewertet[1][0] if len(bewertet) > 1 else 0
    if p >= 80 and p - zweiter >= 30:
        sicher = "sicher"
    elif p >= 45 and p > zweiter:
        sicher = "wahrscheinlich"
    else:
        sicher = "unklar"
    return {"art": "hausgeld", "einheit_id": k["einheit_id"], "text": f"Hausgeld {k['nr']}",
            "sicherheit": sicher, "punkte": p, "gruende": gruende}


def vorschlag_ausgang(u, kostenarten, fruehere):
    """Ausgang einer Kostenart zuordnen.

    kostenarten: [{id, name, stichworte}]; fruehere: {zahlungsempfänger_klein: kostenart_id} aus gebuchten Umsätzen.
    """
    name = (u.get("name") or "").strip().lower()
    if name and name in fruehere:
        ka = next((k for k in kostenarten if k["id"] == fruehere[name]), None)
        if ka:
            return {"art": "kosten", "kostenart_id": ka["id"], "text": ka["name"], "sicherheit": "sicher",
                    "punkte": 90, "gruende": ["schon früher so gebucht"]}
    text = f"{u.get('name', '')} {u.get('zweck', '')}".lower()
    treffer = []
    for k in kostenarten:
        worte = [w.strip().lower() for w in (k.get("stichworte") or "").split(",") if w.strip()]
        worte.append(k["name"].lower())
        n = sum(1 for w in worte if w and w in text)
        if n:
            treffer.append((n, k))
    if not treffer:
        return None
    treffer.sort(key=lambda x: -x[0])
    n, k = treffer[0]
    eindeutig = len(treffer) == 1 or treffer[1][0] < n
    return {"art": "kosten", "kostenart_id": k["id"], "text": k["name"],
            "sicherheit": "wahrscheinlich" if eindeutig else "unklar", "punkte": 50, "gruende": ["Stichwort"]}


# ------------------------------------------------------------------ SEPA-Lastschrift (pain.008.001.08)
def _xml_text(s, laenge=140):
    """Nur SEPA-zulässige Zeichen."""
    ersatz = {"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss", "&": "+"}
    t = "".join(ersatz.get(c, c) for c in (s or ""))
    t = re.sub(r"[^A-Za-z0-9/\-?:().,'+ ]", " ", t)
    return re.sub(r"\s+", " ", t).strip()[:laenge]


def lastschrift_xml(glaeubiger, posten, faellig, msg_id=None):
    """pain.008.001.08 (SEPA-Basislastschrift).

    glaeubiger: {name, iban, bic, glaeubiger_id}
    posten: [{name, iban, bic, betrag_cent, mandat_ref, mandat_datum, zweck, e2e}]
    """
    if not posten:
        raise ValueError("Keine Lastschriften.")
    for p in posten:
        if not iban_gueltig(p["iban"]):
            raise ValueError(f"IBAN von {p['name']} ist ungültig.")
        if not p.get("mandat_ref") or not p.get("mandat_datum"):
            raise ValueError(f"Für {p['name']} fehlt das Mandat (Referenz und Datum).")
        if p["betrag_cent"] <= 0:
            raise ValueError(f"Betrag für {p['name']} muss größer als 0 sein.")
    if not iban_gueltig(glaeubiger["iban"]):
        raise ValueError("IBAN des Gemeinschaftskontos ist ungültig.")
    if not glaeubiger.get("glaeubiger_id"):
        raise ValueError("Die Gemeinschaft hat noch keine Gläubiger-ID.")

    ns = "urn:iso:std:iso:20022:tech:xsd:pain.008.001.08"
    jetzt = datetime.datetime.now().replace(microsecond=0)
    msg_id = msg_id or f"CASALO-{jetzt:%Y%m%d%H%M%S}"
    summe = sum(p["betrag_cent"] for p in posten)

    def fmt_betrag(c):
        return f"{c // 100}.{c % 100:02d}"

    doc = ET.Element("Document", xmlns=ns)
    root = ET.SubElement(doc, "CstmrDrctDbtInitn")
    gh = ET.SubElement(root, "GrpHdr")
    ET.SubElement(gh, "MsgId").text = msg_id[:35]
    ET.SubElement(gh, "CreDtTm").text = jetzt.isoformat()
    ET.SubElement(gh, "NbOfTxs").text = str(len(posten))
    ET.SubElement(gh, "CtrlSum").text = fmt_betrag(summe)
    ET.SubElement(ET.SubElement(gh, "InitgPty"), "Nm").text = _xml_text(glaeubiger["name"], 70)

    pi = ET.SubElement(root, "PmtInf")
    ET.SubElement(pi, "PmtInfId").text = (msg_id + "-1")[:35]
    ET.SubElement(pi, "PmtMtd").text = "DD"
    ET.SubElement(pi, "NbOfTxs").text = str(len(posten))
    ET.SubElement(pi, "CtrlSum").text = fmt_betrag(summe)
    pti = ET.SubElement(pi, "PmtTpInf")
    ET.SubElement(ET.SubElement(pti, "SvcLvl"), "Cd").text = "SEPA"
    ET.SubElement(ET.SubElement(pti, "LclInstrm"), "Cd").text = "CORE"
    ET.SubElement(pti, "SeqTp").text = "RCUR"
    ET.SubElement(pi, "ReqdColltnDt").text = faellig.isoformat()
    ET.SubElement(ET.SubElement(pi, "Cdtr"), "Nm").text = _xml_text(glaeubiger["name"], 70)
    ET.SubElement(ET.SubElement(ET.SubElement(pi, "CdtrAcct"), "Id"), "IBAN").text = iban_normal(glaeubiger["iban"])
    agt = ET.SubElement(ET.SubElement(pi, "CdtrAgt"), "FinInstnId")
    if glaeubiger.get("bic"):
        ET.SubElement(agt, "BICFI").text = glaeubiger["bic"].strip().upper()
    else:
        ET.SubElement(ET.SubElement(agt, "Othr"), "Id").text = "NOTPROVIDED"
    ET.SubElement(pi, "ChrgBr").text = "SLEV"
    sid = ET.SubElement(ET.SubElement(ET.SubElement(ET.SubElement(pi, "CdtrSchmeId"), "Id"), "PrvtId"), "Othr")
    ET.SubElement(sid, "Id").text = glaeubiger["glaeubiger_id"].strip().upper()
    ET.SubElement(ET.SubElement(sid, "SchmeNm"), "Prtry").text = "SEPA"

    for n, p in enumerate(posten, 1):
        tx = ET.SubElement(pi, "DrctDbtTxInf")
        ET.SubElement(ET.SubElement(tx, "PmtId"), "EndToEndId").text = (p.get("e2e") or f"{msg_id}-{n}")[:35]
        ET.SubElement(tx, "InstdAmt", Ccy="EUR").text = fmt_betrag(p["betrag_cent"])
        mri = ET.SubElement(ET.SubElement(tx, "DrctDbtTx"), "MndtRltdInf")
        ET.SubElement(mri, "MndtId").text = _xml_text(p["mandat_ref"], 35)
        ET.SubElement(mri, "DtOfSgntr").text = p["mandat_datum"]
        dagt = ET.SubElement(ET.SubElement(tx, "DbtrAgt"), "FinInstnId")
        if p.get("bic"):
            ET.SubElement(dagt, "BICFI").text = p["bic"].strip().upper()
        else:
            ET.SubElement(ET.SubElement(dagt, "Othr"), "Id").text = "NOTPROVIDED"
        ET.SubElement(ET.SubElement(tx, "Dbtr"), "Nm").text = _xml_text(p["name"], 70)
        ET.SubElement(ET.SubElement(ET.SubElement(tx, "DbtrAcct"), "Id"), "IBAN").text = iban_normal(p["iban"])
        ET.SubElement(ET.SubElement(tx, "RmtInf"), "Ustrd").text = _xml_text(p.get("zweck") or "Hausgeld")
    ET.indent(doc)
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(doc, encoding="utf-8")


# ------------------------------------------------------------------ FinTS
class TanNoetig(Exception):
    """Die Bank verlangt eine TAN. `hinweis` zeigt die Aufforderung, `zustand` wird zum Fortsetzen gespeichert."""

    def __init__(self, hinweis, zustand, bild=None):
        super().__init__(hinweis)
        self.hinweis = hinweis
        self.zustand = zustand
        self.bild = bild


def fints_abrufen(blz, login, pin, url, iban, von, bis, produkt_id, zustand=None, tan=None):
    """Umsätze per FinTS (PIN/TAN) abrufen.

    Beim ersten Mal (oder alle ~90–180 Tage) verlangt die Bank eine TAN. Dann wird TanNoetig mit
    dem gespeicherten Dialogzustand geworfen; nach Bestätigung (pushTAN in der App, oder TAN-Eingabe)
    wird mit zustand + tan fortgesetzt.
    """
    try:
        from fints.client import FinTS3PinTanClient, NeedTANResponse, NeedRetryResponse
    except ImportError:
        raise RuntimeError("Das Paket python-fints ist nicht installiert.")
    import pickle
    import base64

    if not produkt_id:
        raise RuntimeError("FinTS-Produkt-ID fehlt (Einstellungen). Sie wird kostenlos bei der Deutschen Kreditwirtschaft registriert.")

    def umsaetze(client):
        konten = client.get_sepa_accounts()
        konto = next((k for k in konten if iban_normal(k.iban) == iban_normal(iban)), None)
        if konto is None:
            raise RuntimeError("Das Konto wurde unter diesem Zugang nicht gefunden – IBAN prüfen.")
        res = client.get_transactions(konto, von, bis)
        if isinstance(res, NeedTANResponse):
            return res
        return abbilden(res)

    def abbilden(res):
        out = []
        for t in res or []:
            d = t.data
            amount = d.get("amount")
            wert = amount.amount if hasattr(amount, "amount") else Decimal(str(amount))
            out.append({"datum": (d.get("entry_date") or d.get("date")).isoformat(),
                        "betrag_cent": int((Decimal(wert) * 100).quantize(Decimal("1"))),
                        "name": d.get("applicant_name") or "", "iban": iban_normal(d.get("applicant_iban") or ""),
                        "zweck": d.get("purpose") or "", "eigene_iban": iban_normal(iban),
                        "extra": d.get("bank_reference") or ""})
        return out

    def tan_frage(antwort, client):
        dialog = client.pause_dialog()
        daten = {"client": client.deconstruct(including_private=True), "dialog": dialog,
                 "antwort": antwort.get_data()}
        bild = None
        if getattr(antwort, "challenge_matrix", None):
            bild = antwort.challenge_matrix[1]
        hinweis = antwort.challenge or "Bitte den Auftrag in Ihrer TAN-App freigeben."
        raise TanNoetig(hinweis, base64.b64encode(pickle.dumps(daten)).decode(), bild)

    if zustand:
        daten = pickle.loads(base64.b64decode(zustand))
        client = FinTS3PinTanClient(blz, login, pin, url, product_id=produkt_id, from_data=daten["client"])
        antwort = NeedRetryResponse.from_data(daten["antwort"])
        with client.resume_dialog(daten["dialog"]):
            erg = client.send_tan(antwort, tan or "")
            if isinstance(erg, NeedTANResponse):
                tan_frage(erg, client)
            if isinstance(erg, list):
                return abbilden(erg), None
            erg = umsaetze(client)
            if isinstance(erg, NeedTANResponse):
                tan_frage(erg, client)
            return erg, None

    client = FinTS3PinTanClient(blz, login, pin, url, product_id=produkt_id)
    if not client.get_current_tan_mechanism():
        client.fetch_tan_mechanisms()
        mechs = list(client.get_tan_mechanisms().items())
        if mechs:
            client.set_tan_mechanism(mechs[0][0])
    with client:
        if client.init_tan_response:
            tan_frage(client.init_tan_response, client)
        erg = umsaetze(client)
        if isinstance(erg, NeedTANResponse):
            tan_frage(erg, client)
        return erg, None
