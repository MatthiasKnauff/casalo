# Casalo – Anleitung

Casalo verwaltet Wohnungseigentümergemeinschaften: Einheiten und Eigentümer, Buchungen, Wirtschaftsplan,
Jahresabrechnung mit Heizkosten, Eigentümerversammlungen mit Beschlusssammlung, Bankabruf mit automatischer
Zuordnung, SEPA-Lastschrift – und ein KI-Telefon, das Anrufe annimmt.

Beide Container laufen in **einem** Stack, den die Deploy-Automatik anlegt:

| Container | Inhalt | Netz |
|---|---|---|
| **casalo-…** | Weboberfläche, Datenbank (SQLite), Bankabruf, PDFs, E-Mail | Port 8000 im Container, Daten im Volume |
| **casalo-telefon-…** | Asterisk (an der Fritzbox) + Sprachbot (Deepgram → Claude → Piper) | Netzwerk des Servers (`host`) |

Die ZIP bringt dafür eine eigene `deploy/compose.template.yml` und `deploy/images.txt` mit; die
Deploy-Automatik (ab 1.3.0) übernimmt beides und baut das Telefon-Image mit demselben Stand wie die App.

## 1. Mit der Deploy-Automatik einrichten

Neues Projekt → Vorlage **„Eigenes Programm (ZIP hochladen)“** → `casalo.zip`.

- Name: `casalo`
- Port im Container: `8000`
- Datenordner im Container: `/data`
- Domain Produktiv: `hausverwaltung.knauff.info` (später änderbar)
- Umgebungsvariablen **nur auf dem Produktivserver** (für das KI-Telefon):
  `SIP_BENUTZER`, `SIP_PASSWORT`, `DEEPGRAM_API_KEY`, `ANTHROPIC_API_KEY`,
  optional `FRITZBOX_HOST` (Standard `fritz.box`), `SIP_PORT` (5070), `RTP_VON`/`RTP_BIS` (10000–10100),
  `CLAUDE_MODELL`, `ABRUF_UHRZEIT` (06:00).
  Ohne diese Werte wartet der Telefon-Container nur – auf dem Testserver also einfach weglassen.
  Wer das Telefon auch auf dem Testserver will: in der Fritzbox ein zweites IP-Telefon anlegen
  (eine Anmeldung je Gerät).

Beim Bauen laufen alle Tests – schlägt einer fehl, entsteht kein Image. Der Testserver zeigt oben
„Testserver – wird vX.Y.Z“. Produktiv kommt eine Version erst nach Freigabe (Release).

Erster Aufruf: Sie legen sich als Administrator an (Name, E-Mail, Passwort).

## 2. Erste Schritte in Casalo

1. **Einstellungen**: Name der Verwaltung, Anschrift (Fußzeile der PDFs), E-Mail-Versand (SMTP), FinTS-Produkt-ID.
2. **Gemeinschaften → Neue Gemeinschaft** – übliche Kostenarten mit Verteilerschlüssel werden angelegt.
3. **Einheiten** mit Wohnfläche und MEA, dann **Eigentümer zuordnen** (Anschrift, E-Mail, Telefon, Versandweg,
   Lastschrift mit Mandat).
4. **Konten**: Hausgeld- und Rücklagenkonto mit Anfangssaldo; Abruf per FinTS oder Datei-Import.
5. **Wirtschaftsplan** anlegen → Vorschüsse je Einheit, PDF.
6. Unter **Einstellungen der Gemeinschaft**: Heizkosten (Messdienst-Datei oder selbst nach HeizkostenV),
   Stimmrecht, Online-Teilnahme (wenn beschlossen), Gläubiger-ID für Lastschriften.

## 3. Bank

- **FinTS** (Standard): BLZ, FinTS-Adresse der Bank, Anmeldename und PIN beim Konto eintragen. Beim ersten Abruf
  und dann etwa alle 90 Tage verlangt die Bank eine Freigabe (pushTAN in der App bestätigen, dann in Casalo
  „Freigegeben – weiter“). Danach ruft Casalo jeden Morgen selbst ab.
  Nötig ist eine **FinTS-Produkt-ID** – kostenlos bei der Deutschen Kreditwirtschaft registrieren.
- **Datei-Import**: Kontoauszug als CAMT.053 (XML) oder CSV-Export der Bank hochladen. Doppelte werden erkannt.
- **finAPI** für Banken ohne FinTS: als Zugang wählbar, die Anbindung folgt in einer der nächsten Versionen –
  bis dahin diese Konten per Datei einlesen.
- Zuordnung: Eingänge über IBAN, Name, Wohnungsnummer im Verwendungszweck und Betrag; Ausgänge über frühere
  Buchungen und Stichworte der Kostenarten. „Sicher“ wird sofort gebucht, der Rest steht unter „Zu prüfen“.
- **Lastschrift**: Gemeinschaft → Lastschrift → SEPA-Datei (pain.008) erzeugen und im Onlinebanking hochladen.

## 4. KI-Telefon einrichten

### Fritzbox
Telefonie → Telefoniegeräte → **Neues Gerät einrichten** → Telefon → **LAN/WLAN (IP-Telefon)**.
Benutzername und Kennwort vergeben (das sind `SIP_BENUTZER` und `SIP_PASSWORT`) und festlegen, auf welche
Rufnummer das Gerät reagiert. Unter Telefonie → Eigene Rufnummern → Anschlusseinstellungen muss
„Anmeldung aus dem Internet“ nicht erlaubt sein – der Container meldet sich im Heimnetz an.

### Schlüssel
- **Deepgram** (Spracherkennung): Konto auf deepgram.com, API-Schlüssel.
- **Claude**: API-Schlüssel aus der Claude Console.
- Die Verbindung zu Casalo braucht nichts: Der Telefon-Container liest Adresse und Schlüssel selbst
  (gemeinsames Volume im selben Stack). Ein neuer Schlüssel (KI-Telefon → Einstellungen) gilt sofort.

### Container
Läuft im Netzwerk des Servers (SIP und Sprache sind UDP); belegt werden SIP-Port 5070 und RTP 10000–10100.
Updates kommen mit jeder Freigabe zusammen mit der App.

### Verhalten
- Begrüßung mit Hinweis auf KI und Protokoll (Einstellungen).
- Anrufer erkennen: über die Rufnummer, persönliche Auskünfte (Hausgeld, Rückstand, Abrechnung) erst nach
  richtiger Rückfrage nach Wohnungsnummer und Postleitzahl; nach zwei Fehlversuchen nicht mehr in diesem Anruf.
- Unbekannte Nummern: nur Allgemeines (Bürozeiten, Versammlungstermine) und Rückruf.
- Schäden werden Vorgänge mit Rückruftermin. Notfälle außerhalb der Bürozeiten nach Regel je Gemeinschaft
  und Notfallart: weiterleiten, Nummer ansagen oder nur aufnehmen.
- Namen und Adressen gehen nur als Kennungen ([Person 1], [Adresse 1]) an Claude; was an die KI ging,
  steht beim Anruf.

## 5. Daten und Sicherung

Alles liegt im Volume (Datenordner `/data`): `casalo.db` (SQLite) und `secret.key`
(verschlüsselt Bank-PINs und SMTP-Passwort). **Beide zusammen sichern** – ohne `secret.key` sind die
gespeicherten Zugänge nicht mehr lesbar.
