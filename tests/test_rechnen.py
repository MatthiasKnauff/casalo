from casalo import rechnen
from casalo.rechnen import Kostenart

E = [{"id": 1, "mea": 333, "wohnflaeche": 70}, {"id": 2, "mea": 333, "wohnflaeche": 80}, {"id": 3, "mea": 334, "wohnflaeche": 100}]


def test_verteile_summe_stimmt_immer():
    for total in (1, 2, 100, 99999, 131000, 7):
        anteile = rechnen.verteile(total, {1: 333, 2: 333, 3: 334})
        assert sum(anteile.values()) == total
    assert rechnen.verteile(-100, {1: 1, 2: 1, 3: 1}) == {1: -34, 2: -33, 3: -33}


def test_verteile_ohne_gewicht():
    assert rechnen.verteile(500, {1: 0, 2: 0}) == {1: 0, 2: 0}


def test_heizkosten_selbst_nach_heizkostenv():
    anteile, hinweise = rechnen.heizkosten(100000, "selbst", E, 30, verbrauch={1: 10, 2: 10, 3: 20})
    assert sum(anteile.values()) == 100000
    # 30 % nach Fläche (250 m²), 70 % nach Verbrauch (40)
    assert anteile[3] == round(30000 * 100 / 250) + round(70000 * 20 / 40)
    assert not hinweise


def test_heizkosten_grundanteil_begrenzt():
    _, hinweise = rechnen.heizkosten(1000, "selbst", E, 70, verbrauch={1: 1, 2: 1, 3: 1})
    assert any("30–50" in h for h in hinweise)


def test_heizkosten_messdienst_abweichung():
    anteile, hinweise = rechnen.heizkosten(1000, "messdienst", E, messdienst={1: 300, 2: 300, 3: 300})
    assert anteile == {1: 300, 2: 300, 3: 300}
    assert hinweise and "Abweichung" in hinweise[0]


def test_vorschuss_aufgerundet():
    assert rechnen.vorschuss_monat(1201) == 101
    assert rechnen.vorschuss_monat(1200) == 100
    assert rechnen.vorschuss_monat(0) == 0


def test_wirtschaftsplan_und_ersatzschluessel():
    kas = [Kostenart(10, "Strom", "mea"), Kostenart(11, "Wasser", "verbrauch")]
    wp = rechnen.wirtschaftsplan(E, kas, {10: 120000, 11: 60000}, 30000)
    assert wp["summe"] == 210000
    assert sum(d["jahr"] for d in wp["einheiten"].values()) == 210000
    assert any("Wasser" in h for h in wp["hinweise"])          # keine Vorjahreswerte → Fläche


def test_jahresabrechnung_spitze_und_pruefung():
    kas = [Kostenart(10, "Strom", "mea"), Kostenart(12, "Reparatur", "direkt")]
    a = rechnen.jahresabrechnung(2025, E, kas, {10: 90000, 12: 5000}, direkt={12: {2: 5000}},
                                 ruecklage_soll=30000, vorschuss_soll={1: 40000, 2: 40000, 3: 40000},
                                 zahlungen_ist={1: 40000, 2: 40000, 3: 20000})
    assert a.summe_kosten == 95000
    assert a.einheiten[2]["kosten"][12] == 5000
    assert a.einheiten[1]["kosten"].get(12, 0) == 0
    gesamt = sum(d["summe"] for d in a.einheiten.values())
    assert gesamt == 95000 + 30000
    assert a.einheiten[3]["rueckstand"] == 20000
    assert all(ok for _, ok in a.pruefungen)
    assert a.einheiten[1]["spitze"] == a.einheiten[1]["summe"] - 40000


def test_betraege_lesen_und_schreiben():
    assert rechnen.parse_betrag("1.234,56") == 123456
    assert rechnen.parse_betrag("1234.56") == 123456
    assert rechnen.parse_betrag("-12") == -1200
    assert rechnen.parse_betrag("") == 0
    assert rechnen.fmt(-123456) == "−1.234,56 €"
    assert rechnen.fmt(5) == "0,05 €"
