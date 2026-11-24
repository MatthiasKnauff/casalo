"""Datenhaltung: SQLite im Datenordner (Volume), Einstellungen, Verschlüsselung von Zugangsdaten.

Geldbeträge liegen überall als ganze Cent (int) vor, nie als Kommazahl.
"""
import base64
import hashlib
import os
import secrets
import sqlite3
import threading

from cryptography.fernet import Fernet, InvalidToken
from flask import g

_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS einstellung (schluessel TEXT PRIMARY KEY, wert TEXT);

CREATE TABLE IF NOT EXISTS benutzer (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    pw_hash TEXT NOT NULL,
    rolle TEXT NOT NULL DEFAULT 'mitarbeiter'          -- admin | mitarbeiter
);

CREATE TABLE IF NOT EXISTS gemeinschaft (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nr TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL,
    strasse TEXT NOT NULL DEFAULT '',
    plz TEXT NOT NULL DEFAULT '',
    ort TEXT NOT NULL DEFAULT '',
    heizkosten_modus TEXT NOT NULL DEFAULT 'messdienst', -- messdienst | selbst
    heiz_grundanteil INTEGER NOT NULL DEFAULT 30,         -- % Grundkosten nach Wohnfläche (HeizkostenV 30–50)
    stimmprinzip TEXT NOT NULL DEFAULT 'kopf',            -- kopf | mea | objekt
    hybrid_erlaubt INTEGER NOT NULL DEFAULT 0,            -- Beschluss nach § 23 Abs. 1a WEG liegt vor
    glaeubiger_id TEXT NOT NULL DEFAULT '',               -- SEPA-Gläubiger-ID der Gemeinschaft
    verwalter_seit TEXT NOT NULL DEFAULT '',
    notiz TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS einheit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gemeinschaft_id INTEGER NOT NULL REFERENCES gemeinschaft(id),
    nr TEXT NOT NULL,
    lage TEXT NOT NULL DEFAULT '',
    art TEXT NOT NULL DEFAULT 'Wohnung',                  -- Wohnung | Gewerbe | Stellplatz | Sonstiges
    wohnflaeche REAL NOT NULL DEFAULT 0,
    mea REAL NOT NULL DEFAULT 0,
    notiz TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS person (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    anrede TEXT NOT NULL DEFAULT '',
    vorname TEXT NOT NULL DEFAULT '',
    nachname TEXT NOT NULL DEFAULT '',
    firma TEXT NOT NULL DEFAULT '',
    strasse TEXT NOT NULL DEFAULT '',
    plz TEXT NOT NULL DEFAULT '',
    ort TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    telefon TEXT NOT NULL DEFAULT '',
    telefon2 TEXT NOT NULL DEFAULT '',
    versand TEXT NOT NULL DEFAULT 'email',                -- email | post | portal
    zahlweise TEXT NOT NULL DEFAULT 'ueberweisung',       -- ueberweisung | lastschrift
    iban TEXT NOT NULL DEFAULT '',
    bic TEXT NOT NULL DEFAULT '',
    mandat_ref TEXT NOT NULL DEFAULT '',
    mandat_datum TEXT NOT NULL DEFAULT '',
    notiz TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS eigentum (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    einheit_id INTEGER NOT NULL REFERENCES einheit(id),
    person_id INTEGER NOT NULL REFERENCES person(id),
    von TEXT NOT NULL DEFAULT '',
    bis TEXT                                              -- NULL = aktueller Eigentümer
);

CREATE TABLE IF NOT EXISTS konto (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gemeinschaft_id INTEGER NOT NULL REFERENCES gemeinschaft(id),
    art TEXT NOT NULL DEFAULT 'hausgeld',                 -- hausgeld | ruecklage
    bank TEXT NOT NULL DEFAULT '',
    iban TEXT NOT NULL DEFAULT '',
    bic TEXT NOT NULL DEFAULT '',
    zugang TEXT NOT NULL DEFAULT 'fints',                 -- fints | finapi | datei
    fints_blz TEXT NOT NULL DEFAULT '',
    fints_url TEXT NOT NULL DEFAULT '',
    fints_login TEXT NOT NULL DEFAULT '',
    fints_pin TEXT NOT NULL DEFAULT '',                   -- verschlüsselt
    fints_zustand TEXT NOT NULL DEFAULT '',               -- gespeicherter Dialog bei TAN-Abfrage (verschlüsselt)
    tan_gueltig_bis TEXT NOT NULL DEFAULT '',
    anfangssaldo_cent INTEGER NOT NULL DEFAULT 0,
    anfangsdatum TEXT NOT NULL DEFAULT '',
    letzter_abruf TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS kostenart (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gemeinschaft_id INTEGER NOT NULL REFERENCES gemeinschaft(id),
    name TEXT NOT NULL,
    schluessel TEXT NOT NULL DEFAULT 'mea',               -- mea | flaeche | einheiten | verbrauch | heizung | direkt
    stichworte TEXT NOT NULL DEFAULT '',                  -- für die Zuordnung von Bankumsätzen
    reihenfolge INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS verbrauch (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kostenart_id INTEGER NOT NULL REFERENCES kostenart(id),
    einheit_id INTEGER NOT NULL REFERENCES einheit(id),
    jahr INTEGER NOT NULL,
    wert REAL NOT NULL DEFAULT 0,
    UNIQUE (kostenart_id, einheit_id, jahr)
);

CREATE TABLE IF NOT EXISTS heizung (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gemeinschaft_id INTEGER NOT NULL REFERENCES gemeinschaft(id),
    einheit_id INTEGER NOT NULL REFERENCES einheit(id),
    jahr INTEGER NOT NULL,
    betrag_cent INTEGER,                                  -- Modus messdienst: Kosten laut Messdienst
    verbrauch REAL,                                       -- Modus selbst: Verbrauchseinheiten
    UNIQUE (einheit_id, jahr)
);

CREATE TABLE IF NOT EXISTS wirtschaftsplan (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gemeinschaft_id INTEGER NOT NULL REFERENCES gemeinschaft(id),
    jahr INTEGER NOT NULL,
    ruecklage_cent INTEGER NOT NULL DEFAULT 0,            -- Zuführung Erhaltungsrücklage im Jahr
    beschlossen_am TEXT NOT NULL DEFAULT '',
    UNIQUE (gemeinschaft_id, jahr)
);

CREATE TABLE IF NOT EXISTS wp_ansatz (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wirtschaftsplan_id INTEGER NOT NULL REFERENCES wirtschaftsplan(id),
    kostenart_id INTEGER NOT NULL REFERENCES kostenart(id),
    betrag_cent INTEGER NOT NULL DEFAULT 0,
    UNIQUE (wirtschaftsplan_id, kostenart_id)
);

CREATE TABLE IF NOT EXISTS buchung (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gemeinschaft_id INTEGER NOT NULL REFERENCES gemeinschaft(id),
    konto_id INTEGER REFERENCES konto(id),
    datum TEXT NOT NULL,
    betrag_cent INTEGER NOT NULL,                         -- + Eingang, − Ausgang
    art TEXT NOT NULL,                                    -- kosten | hausgeld | sonstig
    kostenart_id INTEGER REFERENCES kostenart(id),
    einheit_id INTEGER REFERENCES einheit(id),
    text TEXT NOT NULL DEFAULT '',
    umsatz_id INTEGER REFERENCES umsatz(id)
);

CREATE TABLE IF NOT EXISTS umsatz (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    konto_id INTEGER NOT NULL REFERENCES konto(id),
    datum TEXT NOT NULL,
    betrag_cent INTEGER NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    iban TEXT NOT NULL DEFAULT '',
    zweck TEXT NOT NULL DEFAULT '',
    ref TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'neu',                   -- neu | gebucht | ignoriert
    vorschlag TEXT NOT NULL DEFAULT ''                    -- JSON
);

CREATE TABLE IF NOT EXISTS versammlung (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gemeinschaft_id INTEGER NOT NULL REFERENCES gemeinschaft(id),
    beginn TEXT NOT NULL,                                 -- 2026-12-22T18:00
    ort TEXT NOT NULL DEFAULT '',
    art TEXT NOT NULL DEFAULT 'ordentlich',
    hybrid INTEGER NOT NULL DEFAULT 0,
    videolink TEXT NOT NULL DEFAULT '',
    einladung_versandt TEXT NOT NULL DEFAULT '',
    protokoll TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'geplant'                -- geplant | eingeladen | durchgefuehrt
);

CREATE TABLE IF NOT EXISTS top (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    versammlung_id INTEGER NOT NULL REFERENCES versammlung(id),
    nr INTEGER NOT NULL,
    titel TEXT NOT NULL,
    vorschlag TEXT NOT NULL DEFAULT '',
    mehrheit TEXT NOT NULL DEFAULT 'einfach',             -- keine | einfach | doppelt | allstimmig
    ja REAL, nein REAL, enthaltung REAL,
    ja_mea REAL,
    ergebnis TEXT NOT NULL DEFAULT '',                    -- angenommen | abgelehnt
    beschluss_id INTEGER REFERENCES beschluss(id)
);

CREATE TABLE IF NOT EXISTS beschluss (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gemeinschaft_id INTEGER NOT NULL REFERENCES gemeinschaft(id),
    lfd INTEGER NOT NULL,
    datum TEXT NOT NULL,
    versammlung_id INTEGER REFERENCES versammlung(id),
    art TEXT NOT NULL DEFAULT 'versammlung',              -- versammlung | umlauf | urteil
    text TEXT NOT NULL,
    ergebnis TEXT NOT NULL DEFAULT '',
    vermerk TEXT NOT NULL DEFAULT '',
    UNIQUE (gemeinschaft_id, lfd)
);

CREATE TABLE IF NOT EXISTS vorgang (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gemeinschaft_id INTEGER REFERENCES gemeinschaft(id),
    einheit_id INTEGER REFERENCES einheit(id),
    person_id INTEGER REFERENCES person(id),
    titel TEXT NOT NULL,
    text TEXT NOT NULL DEFAULT '',
    quelle TEXT NOT NULL DEFAULT 'manuell',               -- manuell | telefon
    notfallart TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'offen',                 -- offen | erledigt
    rueckruf_bis TEXT NOT NULL DEFAULT '',
    rueckruf_nummer TEXT NOT NULL DEFAULT '',
    angelegt TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS anruf (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    beginn TEXT NOT NULL,
    dauer_s INTEGER NOT NULL DEFAULT 0,
    nummer TEXT NOT NULL DEFAULT '',
    person_id INTEGER REFERENCES person(id),
    einheit_id INTEGER REFERENCES einheit(id),
    identifiziert TEXT NOT NULL DEFAULT 'nein',           -- nein | nummer | eindeutig
    fehlversuche INTEGER NOT NULL DEFAULT 0,
    anliegen TEXT NOT NULL DEFAULT '',
    verlauf TEXT NOT NULL DEFAULT '[]',                   -- JSON [{wer, text}]
    vorgang_id INTEGER REFERENCES vorgang(id),
    weiterleitung TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'laeuft'                 -- laeuft | beantwortet | vorgang | rueckruf | weitergeleitet
);

CREATE TABLE IF NOT EXISTS notfallregel (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gemeinschaft_id INTEGER REFERENCES gemeinschaft(id),  -- NULL = alle Gemeinschaften
    notfallart TEXT NOT NULL,                             -- wasser | heizung | sturm | strom | aufzug | sonstiges
    aktion TEXT NOT NULL DEFAULT 'aufnehmen',             -- weiterleiten | ansagen | aufnehmen
    nummer TEXT NOT NULL DEFAULT '',
    ansage TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS ki_protokoll (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    zeit TEXT NOT NULL,
    anruf_id INTEGER,
    inhalt TEXT NOT NULL                                  -- was an die KI ging (pseudonymisiert)
);
"""


def data_dir():
    return os.environ.get("DATA_DIR", "/data")


def db_path():
    return os.path.join(data_dir(), "casalo.db")


def connect(path=None):
    con = sqlite3.connect(path or db_path(), timeout=30, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    return con


def init():
    os.makedirs(data_dir(), exist_ok=True)
    with _lock:
        con = connect()
        con.executescript(SCHEMA)
        con.commit()
        con.close()


def get():
    """Verbindung für die laufende Anfrage."""
    if "db" not in g:
        g.db = connect()
    return g.db


def close(_exc=None):
    con = g.pop("db", None)
    if con is not None:
        con.close()


def q(sql, args=(), one=False):
    cur = get().execute(sql, args)
    rows = cur.fetchall()
    return (rows[0] if rows else None) if one else rows


def ex(sql, args=()):
    con = get()
    cur = con.execute(sql, args)
    con.commit()
    return cur.lastrowid


# ------------------------------------------------------------------ Einstellungen
DEFAULTS = {
    "buerozeiten": "Mo-Fr 08:00-17:00",
    "telefon_begruessung": ("Hausverwaltung, guten Tag. Sie sprechen mit dem KI-Assistenten der Verwaltung. "
                            "Das Gespräch wird automatisch verarbeitet und protokolliert. Wie kann ich helfen?"),
    "verwaltung_name": "Hausverwaltung",
    "verwaltung_anschrift": "",
    "smtp_host": "", "smtp_port": "587", "smtp_user": "", "smtp_pass": "", "smtp_absender": "",
    "fints_produkt_id": "",
    "anonymisieren": "1",
}


def setting(key, default=None):
    row = q("SELECT wert FROM einstellung WHERE schluessel = ?", (key,), one=True)
    if row is not None:
        return row["wert"]
    return DEFAULTS.get(key, default) if default is None else default


def set_setting(key, value):
    ex("INSERT INTO einstellung (schluessel, wert) VALUES (?, ?) "
       "ON CONFLICT(schluessel) DO UPDATE SET wert = excluded.wert", (key, value))


def secret_key():
    """Geheimer Schlüssel im Datenordner (bleibt über Updates erhalten)."""
    path = os.path.join(data_dir(), "secret.key")
    with _lock:
        if not os.path.exists(path):
            with open(path, "w") as fh:
                fh.write(secrets.token_hex(32))
            os.chmod(path, 0o600)
        with open(path) as fh:
            return fh.read().strip()


def _fernet():
    key = hashlib.sha256(("casalo-zugang:" + secret_key()).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt(text):
    return _fernet().encrypt(text.encode()).decode() if text else ""


def decrypt(token):
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        return ""


def telefon_token(neu=False):
    """Schlüssel für die Telefon-Schnittstelle. Liegt zusätzlich als Datei im Datenordner,
    damit der Telefon-Container im selben Stack ihn ohne Abschreiben lesen kann."""
    tok = "" if neu else setting("telefon_token", "")
    if not tok:
        tok = secrets.token_urlsafe(32)
        set_setting("telefon_token", tok)
    pfad = os.path.join(data_dir(), "telefon.token")
    try:
        with open(pfad) as fh:
            aktuell = fh.read().strip()
    except OSError:
        aktuell = ""
    if aktuell != tok:
        with open(pfad, "w") as fh:
            fh.write(tok)
        os.chmod(pfad, 0o644)
    return tok
