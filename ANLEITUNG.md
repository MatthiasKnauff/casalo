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
- Umgebungsvariablen: keine nötig. Fritzbox-Zugang und Schlüssel für das KI-Telefon trägt man in Casalo
  ein (KI-Telefon → Einstellungen). Optional `ABRUF_UHRZEIT` (Standard `06:00`) für den Bankabruf.
  Solange in Casalo nichts eingetragen ist, wartet der Telefon-Container nur.

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

## Bedienung

- **Gemeinschaft oben wählen** – links erscheinen dann ihre Seiten (Einheiten, Buchungen, Wirtschaftsplan, Abrechnung …).
- **Direkt in der Tabelle bearbeiten:** Feld anklicken, Enter speichert, Esc verwirft, Tab springt zum nächsten Feld.
  Geprüft wird auf dem Server (z. B. IBAN, doppelte Nummern, Lastschrift nur mit Mandat).
- **Rückgängig / Wiederholen:** oben rechts ↶ ↷ oder Strg+Z / Strg+Y, die Zahl daneben zeigt den Verlauf.
- **Details rechts:** Zeile anklicken – Einheit, Eigentümer, Konto, Vorgang oder Anruf erscheinen rechts, auch dort bearbeitbar.
- **Tabellen:** Spaltenkopf anklicken sortiert, rechte Maustaste auf den Kopf (oder ☰) blendet Spalten ein/aus und ordnet sie,
  rechte Maustaste auf eine Zeile öffnet das Kontextmenü. Ab acht Zeilen gibt es ein Suchfeld.
- **Assistent:** Gemeinschaften → „Mit dem Assistenten einrichten“: Gemeinschaft, Einheiten (auch als Serie, MEA aus der
  Wohnfläche), Eigentümer, Konten, Wirtschaftsplan. Jeder Schritt speichert sofort; „Einrichtung fortsetzen“ führt zurück.
- **Für mich (⚙):** Farbe, Schrift, Schriftgröße und Abstände – gilt nur für den eigenen Benutzer.

Technik: Die Logik liegt vollständig auf dem Server (Python). Die Oberfläche ist HTML vom Server, nachgeladen mit
htmx 2.0.4 (eine Datei, fest im Repo unter `casalo/static/vendor/`, keine Build-Kette) und kleinen eigenen Modulen
in `casalo/static/js/` (Kern, Rückgängig, Tabellen, Bereich rechts).

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
Telefonie → Telefoniegeräte → **Neues Gerät einrichten** → Telefon (mit und ohne Anrufbeantworter) →
**LAN/WLAN (IP-Telefon)** → Name „Casalo“ → Benutzername und Kennwort vergeben → ausgehende Rufnummer
(für Notfall-Weiterleitungen) → ankommende Anrufe nur für die Nummer der Hausverwaltung.

### In Casalo
KI-Telefon → Einstellungen → **Verbindung zur Fritzbox und zu den Diensten**:
- Fritzbox als IP-Adresse (Heimnetz → Netzwerk → Netzwerkeinstellungen → IPv4), Benutzername und Kennwort
  des IP-Telefons, SIP-Port (5070) und RTP-Bereich (10000–10100) auf dem Server.
- **Deepgram**-Schlüssel (deepgram.com) und **Claude**-Schlüssel (Claude Console), jeweils mit Modell.

Der Telefon-Container holt die Einstellungen jede Minute ab und meldet zurück, ob er an der Fritzbox
angemeldet ist – das steht oben im Kasten („Fritzbox: angemeldet“). Schlüssel liegen verschlüsselt im
Datenordner; geheime Felder leer lassen heißt „unverändert“.
Für einen zweiten Server (Test) in der Fritzbox ein eigenes IP-Telefon anlegen – je Gerät eine Anmeldung.

### Container
Läuft im selben Stack wie Casalo, im Netzwerk des Servers (SIP und Sprache sind UDP). Adresse und Schlüssel
der Casalo-Schnittstelle liest er selbst aus dem gemeinsamen Volume. Updates kommen mit jeder Freigabe.

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
