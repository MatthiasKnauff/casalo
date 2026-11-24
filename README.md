# Casalo

WEG-Verwaltung mit KI-Telefon: Gemeinschaften, Einheiten, Eigentümer, Buchungen, Wirtschaftsplan,
Jahresabrechnung (§ 28 WEG) mit Heizkosten, Eigentümerversammlungen und Beschlusssammlung,
Bankabruf (FinTS, CAMT, CSV) mit automatischer Zuordnung, SEPA-Lastschrift.

- `casalo/` – Weboberfläche (Flask, SQLite im Volume `/data`)
- `telefon/` – Telefon-Container: Asterisk an der Fritzbox, Sprachbot (Deepgram → Claude → Piper)
- `tests/`, `telefon/tests/` – laufen beim Bauen der Images

Einrichtung: siehe [ANLEITUNG.md](ANLEITUNG.md).

Lokal starten:

    pip install -r requirements.txt
    DATA_DIR=./daten AUTO_ABRUF=0 flask --app "casalo:create_app()" run
