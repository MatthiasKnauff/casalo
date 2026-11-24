import datetime
import xml.etree.ElementTree as ET

import pytest

from casalo import bank

CAMT = b"""<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.08"><BkToCstmrStmt><Stmt>
<Acct><Id><IBAN>DE02120300000000202051</IBAN></Id></Acct>
<Ntry><Amt Ccy="EUR">636.00</Amt><CdtDbtInd>CRDT</CdtDbtInd><Sts><Cd>BOOK</Cd></Sts><BookgDt><Dt>2026-11-23</Dt></BookgDt>
<NtryDtls><TxDtls><RltdPties><Dbtr><Pty><Nm>Hahn, Martin</Nm></Pty></Dbtr><DbtrAcct><Id><IBAN>DE44500105175407324931</IBAN></Id></DbtrAcct></RltdPties>
<RmtInf><Ustrd>Hausgeld Okt Nov Wohnung 3</Ustrd></RmtInf></TxDtls></NtryDtls></Ntry>
<Ntry><Amt Ccy="EUR">152.00</Amt><CdtDbtInd>DBIT</CdtDbtInd><Sts><Cd>BOOK</Cd></Sts><BookgDt><Dt>2026-11-22</Dt></BookgDt>
<NtryDtls><TxDtls><RltdPties><Cdtr><Nm>Stadtwerke Essen AG</Nm></Cdtr></RltdPties><RmtInf><Ustrd>Abschlag Strom 11/2026</Ustrd></RmtInf></TxDtls></NtryDtls></Ntry>
</Stmt></BkToCstmrStmt></Document>"""

CSV = """Auftragskonto;Buchungstag;Valutadatum;Buchungstext;Verwendungszweck;Beguenstigter/Zahlungspflichtiger;Kontonummer/IBAN;BIC (SWIFT-Code);Betrag;Waehrung;Info
DE02120300000000202051;23.11.26;23.11.26;GUTSCHR;Hausgeld W 01;Becker, Sabine;DE89370400440532013000;COBADEFFXXX;318,00;EUR;Umsatz gebucht
DE02120300000000202051;22.11.26;22.11.26;LASTSCHRIFT;Abfallgebuehren;EBE Essen;DE11111111111111111111;XXX;-330,00;EUR;Umsatz gebucht
"""


def test_iban():
    assert bank.iban_gueltig("DE89 3704 0044 0532 0130 00")
    assert not bank.iban_gueltig("DE89 3704 0044 0532 0130 01")
    assert bank.iban_format("DE89370400440532013000") == "DE89 3704 0044 0532 0130 00"


def test_camt():
    u = bank.lese_camt(CAMT)
    assert len(u) == 2
    assert u[0]["betrag_cent"] == 63600 and u[0]["name"] == "Hahn, Martin" and "Wohnung 3" in u[0]["zweck"]
    assert u[1]["betrag_cent"] == -15200 and u[1]["name"] == "Stadtwerke Essen AG"
    assert u[0]["eigene_iban"] == "DE02120300000000202051"


def test_csv_sparkasse():
    u = bank.lese_csv(CSV)
    assert [x["betrag_cent"] for x in u] == [31800, -33000]
    assert u[0]["datum"] == "2026-11-23" and u[0]["iban"] == "DE89370400440532013000"


KANDIDATEN = [
    {"einheit_id": 1, "nr": "W 01", "eigentuemer": [{"nachname": "Becker", "iban": "DE89370400440532013000"}], "monat_cent": 31800},
    {"einheit_id": 3, "nr": "W 03", "eigentuemer": [{"nachname": "Hahn", "iban": ""}], "monat_cent": 31800},
]


def test_zuordnung_sicher_ueber_iban_und_betrag():
    v = bank.vorschlag_eingang({"name": "Becker, Sabine", "iban": "DE89370400440532013000", "zweck": "Hausgeld",
                                "betrag_cent": 31800}, KANDIDATEN)
    assert v["einheit_id"] == 1 and v["sicherheit"] == "sicher"


def test_zuordnung_ueber_name_und_wohnungsnummer():
    v = bank.vorschlag_eingang({"name": "Hahn, Martin", "iban": "DE44500105175407324931", "zweck": "Hausgeld Okt Nov Wohnung 3",
                                "betrag_cent": 63600}, KANDIDATEN)
    assert v["einheit_id"] == 3 and v["sicherheit"] in ("sicher", "wahrscheinlich")
    assert "Betrag = 2 × Hausgeld" in v["gruende"]


def test_zuordnung_ausgang():
    kas = [{"id": 1, "name": "Allgemeinstrom", "stichworte": "strom,stadtwerke"},
           {"id": 2, "name": "Müllabfuhr", "stichworte": "abfall,ebe"}]
    v = bank.vorschlag_ausgang({"name": "Stadtwerke Essen AG", "zweck": "Abschlag Strom"}, kas, {})
    assert v["kostenart_id"] == 1
    v = bank.vorschlag_ausgang({"name": "EBE Essen", "zweck": "x"}, kas, {"ebe essen": 2})
    assert v["kostenart_id"] == 2 and v["sicherheit"] == "sicher"


def test_lastschrift_xml():
    xml = bank.lastschrift_xml({"name": "WEG Musterweg 1", "iban": "DE02120300000000202051", "bic": "",
                                "glaeubiger_id": "DE98ZZZ09999999999"},
                               [{"name": "Becker, Sabine", "iban": "DE89370400440532013000", "betrag_cent": 31800,
                                 "mandat_ref": "M-001", "mandat_datum": "2024-01-15", "zweck": "Hausgeld 12/2026 Einheit W 01"}],
                               datetime.date(2026, 12, 3))
    root = ET.fromstring(xml)
    ns = {"p": "urn:iso:std:iso:20022:tech:xsd:pain.008.001.08"}
    assert root.find(".//p:CtrlSum", ns).text == "318.00"
    assert root.find(".//p:MndtId", ns).text == "M-001"
    assert root.find(".//p:ReqdColltnDt", ns).text == "2026-12-03"


def test_lastschrift_ohne_mandat_abgelehnt():
    with pytest.raises(ValueError):
        bank.lastschrift_xml({"name": "x", "iban": "DE02120300000000202051", "glaeubiger_id": "DE98ZZZ09999999999"},
                             [{"name": "y", "iban": "DE89370400440532013000", "betrag_cent": 100, "mandat_ref": "", "mandat_datum": ""}],
                             datetime.date(2026, 12, 3))
