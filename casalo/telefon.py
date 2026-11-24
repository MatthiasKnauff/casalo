"""KI-Telefon – Logik auf der Seite von Casalo.

Der Sprachbot (eigener Container mit Asterisk) fragt über die Schnittstelle /api/telefon/… nach:
wer ruft an, stimmt die Rückfrage, was darf gesagt werden, Vorgang anlegen, was tun bei Notfall.

Stufen der Identifikation:
  nein      – Rufnummer unbekannt: nur Allgemeines (Bürozeiten, Termine der Gemeinschaften)
  nummer    – Rufnummer gehört zu einem Eigentümer: zusätzlich Stand der eigenen Vorgänge
  eindeutig – Rufnummer + richtige Rückfrage (Einheit und Postleitzahl): auch Persönliches
              (Hausgeld, Rückstand, Ergebnis der letzten Abrechnung)
"""
import datetime
import json
import re

from . import daten, db, rechnen

MAX_FEHLVERSUCHE = 2
NOTFALLARTEN = {
    "wasser": "Wasserschaden / Rohrbruch",
    "heizung": "Heizungsausfall",
    "sturm": "Sturmschaden",
    "strom": "Stromausfall im Gemeinschaftseigentum",
    "aufzug": "Aufzug steckt fest",
    "sonstiges": "sonstiger Notfall",
}
AKTIONEN = {
    "weiterleiten": "an eine Nummer weiterleiten",
    "ansagen": "Notdienstnummer ansagen",
    "aufnehmen": "nur aufnehmen (Vorgang als Notfall)",
}
TAGE = {"mo": 0, "di": 1, "mi": 2, "do": 3, "fr": 4, "sa": 5, "so": 6}


def jetzt():
    return datetime.datetime.now()


def nummer_normal(n):
    d = re.sub(r"[^\d+]", "", n or "")
    if d.startswith("+49"):
        d = "0" + d[3:]
    elif d.startswith("0049"):
        d = "0" + d[4:]
    elif d.startswith("+"):
        d = "00" + d[1:]
    return re.sub(r"\D", "", d)


def ist_buerozeit(text, zeitpunkt=None):
    """'Mo-Fr 08:00-17:00; Sa 09:00-12:00' → True/False."""
    z = zeitpunkt or jetzt()
    for teil in (text or "").split(";"):
        m = re.match(r"\s*([A-Za-z]{2})(?:\s*-\s*([A-Za-z]{2}))?\s+(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})", teil)
        if not m:
            continue
        t1 = TAGE.get(m.group(1).lower())
        t2 = TAGE.get((m.group(2) or m.group(1)).lower())
        if t1 is None or t2 is None:
            continue
        if not (t1 <= z.weekday() <= t2):
            continue
        von = datetime.time(int(m.group(3)), int(m.group(4)))
        bis = datetime.time(int(m.group(5)), int(m.group(6)))
        if von <= z.time() < bis:
            return True
    return False


def personen_zur_nummer(nummer):
    n = nummer_normal(nummer)
    if len(n) < 5:
        return []
    out = []
    for p in db.q("SELECT * FROM person WHERE telefon != '' OR telefon2 != ''"):
        if n in (nummer_normal(p["telefon"]), nummer_normal(p["telefon2"])):
            out.append(dict(p))
    return out


def einheiten_der_person(pid):
    return [dict(r) for r in db.q(
        "SELECT e.*, g.name AS g_name, g.plz AS g_plz, g.strasse AS g_strasse, g.ort AS g_ort, g.id AS gid "
        "FROM eigentum t JOIN einheit e ON e.id = t.einheit_id JOIN gemeinschaft g ON g.id = e.gemeinschaft_id "
        "WHERE t.person_id = ? AND t.bis IS NULL ORDER BY g.name, e.nr", (pid,))]


def anruf_starten(nummer):
    personen = personen_zur_nummer(nummer)
    ident = "nummer" if len(personen) == 1 else "nein"
    pid = personen[0]["id"] if len(personen) == 1 else None
    aid = db.ex("INSERT INTO anruf (beginn, nummer, person_id, identifiziert) VALUES (?, ?, ?, ?)",
                (jetzt().isoformat(timespec="seconds"), nummer or "", pid, ident))
    return {
        "anruf_id": aid,
        "identifiziert": ident,
        "begruessung": db.setting("telefon_begruessung"),
        "buerozeit": ist_buerozeit(db.setting("buerozeiten")),
        "anonymisieren": db.setting("anonymisieren") == "1",
    }


def _anruf(aid):
    a = db.q("SELECT * FROM anruf WHERE id = ?", (aid,), one=True)
    if not a:
        raise KeyError("Anruf unbekannt")
    return a


def _ziffern(t):
    return (re.sub(r"\D", "", t or "").lstrip("0")) or ""


def pruefen(aid, einheit, plz):
    """Rückfrage: Einheit (Nummer, z. B. 'Wohnung 3') und Postleitzahl der Gemeinschaft."""
    a = _anruf(aid)
    if a["identifiziert"] == "eindeutig":
        return {"ergebnis": "eindeutig"}
    if a["fehlversuche"] >= MAX_FEHLVERSUCHE:
        return {"ergebnis": "gesperrt", "hinweis": "Zu viele Fehlversuche – keine persönlichen Auskünfte in diesem Anruf."}
    if not a["person_id"]:
        return {"ergebnis": "nicht_moeglich",
                "hinweis": "Die Rufnummer ist keinem Eigentümer zugeordnet – persönliche Auskünfte nur nach Rückruf."}
    z_einheit = _ziffern(einheit)
    z_plz = re.sub(r"\D", "", plz or "")
    for e in einheiten_der_person(a["person_id"]):
        if z_einheit and _ziffern(e["nr"]) == z_einheit and z_plz and e["g_plz"] == z_plz:
            db.ex("UPDATE anruf SET identifiziert = 'eindeutig', einheit_id = ? WHERE id = ?", (e["id"], aid))
            return {"ergebnis": "eindeutig"}
    db.ex("UPDATE anruf SET fehlversuche = fehlversuche + 1 WHERE id = ?", (aid,))
    rest = MAX_FEHLVERSUCHE - a["fehlversuche"] - 1
    return {"ergebnis": "falsch", "versuche_uebrig": max(0, rest)}


def kontext(aid):
    """Alles, was die KI in diesem Anruf wissen darf – abhängig von der Identifikation."""
    a = _anruf(aid)
    heute = datetime.date.today()
    k = {
        "verwaltung": db.setting("verwaltung_name"),
        "buerozeiten": db.setting("buerozeiten"),
        "ist_buerozeit": ist_buerozeit(db.setting("buerozeiten")),
        "heute": heute.isoformat(),
        "identifiziert": a["identifiziert"],
        "gemeinschaften": [],
    }
    for g in db.q("SELECT * FROM gemeinschaft ORDER BY name"):
        v = db.q("SELECT beginn, ort, hybrid FROM versammlung WHERE gemeinschaft_id = ? AND beginn >= ? "
                 "ORDER BY beginn LIMIT 1", (g["id"], heute.isoformat()), one=True)
        k["gemeinschaften"].append({
            "adresse": f"{g['strasse']}, {g['plz']} {g['ort']}".strip(", "),
            "naechste_versammlung": (f"{datetime.datetime.fromisoformat(v['beginn']):%d.%m.%Y %H:%M} Uhr, {v['ort']}"
                                     + (" (auch online)" if v["hybrid"] else "")) if v else None,
        })
    if a["identifiziert"] in ("nummer", "eindeutig") and a["person_id"]:
        p = db.q("SELECT * FROM person WHERE id = ?", (a["person_id"],), one=True)
        k["anrufer"] = {"anrede": p["anrede"], "vorname": p["vorname"], "nachname": p["nachname"] or p["firma"]}
        k["einheiten"] = [{"nr": e["nr"], "gemeinschaft": f"{e['g_strasse']}, {e['g_ort']}"}
                          for e in einheiten_der_person(p["id"])]
        k["vorgaenge"] = [{"titel": r["titel"], "status": r["status"], "angelegt": r["angelegt"][:10],
                           "rueckruf_bis": r["rueckruf_bis"]}
                          for r in db.q("SELECT * FROM vorgang WHERE person_id = ? ORDER BY angelegt DESC LIMIT 5",
                                        (p["id"],))]
    if a["identifiziert"] == "eindeutig" and a["einheit_id"]:
        e = db.q("SELECT * FROM einheit WHERE id = ?", (a["einheit_id"],), one=True)
        jahr = heute.year
        wp = daten.wirtschaftsplan(e["gemeinschaft_id"], jahr)
        pers = {}
        if wp["wp"]:
            pers["hausgeld_monat"] = rechnen.fmt(wp["einheiten"][e["id"]]["monat"])
            offen = daten.offene_hausgelder(e["gemeinschaft_id"], jahr).get(e["id"], 0)
            pers["rueckstand"] = rechnen.fmt(offen) if offen > 0 else "keiner"
        ja = daten.jahresabrechnung(e["gemeinschaft_id"], jahr - 1)
        d = ja.einheiten.get(e["id"])
        if d and ja.summe_kosten:
            sp = d["spitze"]
            pers[f"abrechnung_{jahr - 1}"] = (f"Nachzahlung {rechnen.fmt(sp)}" if sp > 0 else
                                             f"Guthaben {rechnen.fmt(-sp)}" if sp < 0 else "ausgeglichen")
        k["persoenlich"] = pers
    return k


def notfall(gid, art, zeitpunkt=None):
    """Was tun bei einem Notfall? Regel der Gemeinschaft vor allgemeiner Regel."""
    if ist_buerozeit(db.setting("buerozeiten"), zeitpunkt):
        return {"aktion": "aufnehmen", "text": "Während der Bürozeit wird die Verwaltung sofort informiert."}
    r = None
    if gid:
        r = db.q("SELECT * FROM notfallregel WHERE gemeinschaft_id = ? AND notfallart = ?", (gid, art), one=True)
    if not r:
        r = db.q("SELECT * FROM notfallregel WHERE gemeinschaft_id IS NULL AND notfallart = ?", (art,), one=True)
    if not r:
        r = db.q("SELECT * FROM notfallregel WHERE gemeinschaft_id IS NULL AND notfallart = 'sonstiges'", one=True)
    if not r:
        return {"aktion": "aufnehmen", "text": "Der Notfall wird aufgenommen; die Verwaltung meldet sich zu Bürozeiten."}
    nummer = r["nummer"]
    if r["dienstleister_id"]:                 # Notdienst des Dienstleisters – bleibt aktuell, wenn sich die Nummer ändert
        d = db.q("SELECT * FROM dienstleister WHERE id = ?", (r["dienstleister_id"],), one=True)
        if d and (d["notdienst"] or d["telefon"]):
            nummer = d["notdienst"] or d["telefon"]
    return {"aktion": r["aktion"], "nummer": nummer, "text": r["ansage"]}


def vorgang_anlegen(aid, titel, text, notfallart="", rueckruf=True):
    a = _anruf(aid)
    gid = None
    if a["einheit_id"]:
        gid = db.q("SELECT gemeinschaft_id FROM einheit WHERE id = ?", (a["einheit_id"],), one=True)[0]
    elif a["person_id"]:
        ein = einheiten_der_person(a["person_id"])
        if len({e["gid"] for e in ein}) == 1:
            gid = ein[0]["gid"]
    rueckruf_bis = ""
    if rueckruf:
        rueckruf_bis = naechster_rueckruf().isoformat(timespec="minutes")
    vid = db.ex("INSERT INTO vorgang (gemeinschaft_id, einheit_id, person_id, titel, text, quelle, notfallart, "
                "rueckruf_bis, rueckruf_nummer, angelegt) VALUES (?, ?, ?, ?, ?, 'telefon', ?, ?, ?, ?)",
                (gid, a["einheit_id"], a["person_id"], titel[:200], text, notfallart or "", rueckruf_bis,
                 a["nummer"], jetzt().isoformat(timespec="seconds")))
    db.ex("UPDATE anruf SET vorgang_id = ?, status = 'vorgang' WHERE id = ?", (vid, aid))
    out = {"vorgang_id": vid, "rueckruf_bis": rueckruf_bis}
    if notfallart:
        out["notfall"] = notfall(gid, notfallart)
    return out


def naechster_rueckruf(zeitpunkt=None):
    """Rückruf bis zum Ende des nächsten Bürotags (spätestens 4 Bürostunden)."""
    z = zeitpunkt or jetzt()
    buero = db.setting("buerozeiten")
    t = z.replace(second=0, microsecond=0)
    stunden = 0
    for _ in range(24 * 14 * 4):                     # höchstens zwei Wochen in Viertelstunden
        t += datetime.timedelta(minutes=15)
        if ist_buerozeit(buero, t):
            stunden += 0.25
            if stunden >= 4:
                return t
        elif stunden > 0:
            return t                                 # Ende der Bürozeit
    return z + datetime.timedelta(days=1)


def anruf_beenden(aid, dauer_s, verlauf, anliegen="", status=None, weiterleitung=""):
    a = _anruf(aid)
    st = status or (a["status"] if a["status"] != "laeuft" else "beantwortet")
    db.ex("UPDATE anruf SET dauer_s = ?, verlauf = ?, anliegen = ?, status = ?, weiterleitung = ? WHERE id = ?",
          (int(dauer_s or 0), json.dumps(verlauf or [], ensure_ascii=False), (anliegen or "")[:300], st,
           weiterleitung or "", aid))
    return {"ok": True}


def ki_protokollieren(aid, inhalt):
    db.ex("INSERT INTO ki_protokoll (zeit, anruf_id, inhalt) VALUES (?, ?, ?)",
          (jetzt().isoformat(timespec="seconds"), aid, inhalt[:20000]))


# ------------------------------------------------------------------ Verbindung (Fritzbox, Dienste) – in der Oberfläche einstellbar
VERBINDUNG = [
    # schluessel, Bezeichnung, Standard, geheim
    ("tel_fritzbox_host", "Fritzbox (IP-Adresse)", "fritz.box", False),
    ("tel_sip_benutzer", "Benutzername des IP-Telefons", "", False),
    ("tel_sip_passwort", "Kennwort des IP-Telefons", "", True),
    ("tel_sip_port", "SIP-Port auf dem Server", "5070", False),
    ("tel_rtp_von", "Sprachdaten (RTP) von Port", "10000", False),
    ("tel_rtp_bis", "bis Port", "10100", False),
    ("tel_deepgram_key", "Deepgram API-Schlüssel", "", True),
    ("tel_deepgram_modell", "Deepgram-Modell", "nova-2", False),
    ("tel_anthropic_key", "Claude API-Schlüssel", "", True),
    ("tel_claude_modell", "Claude-Modell", "claude-haiku-5-5", False),
]
PFLICHT = ("tel_sip_benutzer", "tel_sip_passwort", "tel_deepgram_key", "tel_anthropic_key")


def verbindung(klartext=False):
    """Einstellungen fürs Telefon. Geheime Werte liegen verschlüsselt und werden nur für den Container entschlüsselt."""
    out = {}
    for k, _, standard, geheim in VERBINDUNG:
        wert = db.setting(k, "")
        if geheim:
            wert = db.decrypt(wert) if klartext else ("gesetzt" if wert else "")
        out[k] = wert or ("" if geheim else standard)
    return out


def verbindung_speichern(form):
    fehler = []
    for k, name, standard, geheim in VERBINDUNG:
        wert = (form.get(k) or "").strip()
        if geheim:
            if wert:                                   # leer = unverändert lassen
                db.set_setting(k, db.encrypt(wert))
            elif form.get(k + "_loeschen"):
                db.set_setting(k, "")
            continue
        if k in ("tel_sip_port", "tel_rtp_von", "tel_rtp_bis") and wert and not (wert.isdigit() and 1024 <= int(wert) <= 65535):
            fehler.append(f"{name}: Zahl zwischen 1024 und 65535.")
            continue
        db.set_setting(k, wert or standard)
    v = verbindung()
    if int(v["tel_rtp_bis"]) <= int(v["tel_rtp_von"]):
        fehler.append("Der RTP-Bereich ist ungültig (bis muss größer als von sein).")
    return fehler


def konfig_fuer_container():
    v = verbindung(klartext=True)
    v["vollstaendig"] = all(v.get(k) for k in PFLICHT)
    return v


def status_melden(daten):
    db.set_setting("tel_status", json.dumps({**daten, "zeit": jetzt().isoformat(timespec="seconds")}, ensure_ascii=False))


def status():
    s = json.loads(db.setting("tel_status", "") or "{}")
    if s.get("zeit"):
        alter = (jetzt() - datetime.datetime.fromisoformat(s["zeit"])).total_seconds()
        s["aktuell"] = alter < 180                       # Container meldet sich jede Minute
    return s
