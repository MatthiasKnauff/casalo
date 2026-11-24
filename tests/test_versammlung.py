import datetime

from casalo import versammlung as vr


def test_frist():
    beginn = datetime.datetime(2026, 12, 22, 18)
    assert vr.einladung_bis(beginn) == datetime.date(2026, 12, 1)
    assert vr.frist_status(beginn, "", datetime.date(2026, 11, 1))[0] == "ok"
    assert vr.frist_status(beginn, "", datetime.date(2026, 11, 26))[0] == "warnung"
    assert vr.frist_status(beginn, "", datetime.date(2026, 12, 2))[0] == "fehler"
    assert vr.frist_status(beginn, "2026-11-30", datetime.date(2026, 12, 2))[0] == "ok"
    assert vr.frist_status(beginn, "2026-12-05", datetime.date(2026, 12, 6))[0] == "fehler"


def test_mehrheiten():
    assert vr.auswerten("einfach", 5, 4, 10) == "angenommen"          # Enthaltungen zählen nicht
    assert vr.auswerten("einfach", 4, 4) == "abgelehnt"
    assert vr.auswerten("doppelt", 7, 3, 0, ja_mea=600, gesamt_mea=1000) == "angenommen"
    assert vr.auswerten("doppelt", 7, 3, 0, ja_mea=400, gesamt_mea=1000) == "abgelehnt"
    assert vr.auswerten("doppelt", 6, 3, 0, ja_mea=600, gesamt_mea=1000) == "abgelehnt"   # genau 2/3 reicht nicht
    assert vr.auswerten("allstimmig", 10, 0, 1) == "abgelehnt"
    assert vr.auswerten("keine", 1, 0) == ""
