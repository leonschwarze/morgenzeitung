# Morgenzeitung für den Tolino

Jeden Morgen um 05:50 Uhr baut der Raspberry Pi aus Tagesschau, WDR (Bonn & Landespolitik),
franceinfo, Le Monde und heise eine EPUB-Zeitung für ca. 20 Minuten Lektüre – plus zwei
Longreads aus der aktuellen ZEIT-Ausgabe. Am Tolino öffnest du ein Lesezeichen, tippst auf
„Heute laden“ – fertig.

Keine Logins, keine Cookies, keine Cloud. Die Nachrichten kommen aus frei zugänglichen Quellen,
die ZEIT aus der EPUB-Ausgabe, die dein Abo dir ohnehin per Mail schickt.

## So funktioniert es

```
 RSS-Feeds  ──►  Volltext holen  ──►  Auswahl nach   ──►  EPUB bauen  ──►  Download-Seite
 4 Quellen       (trafilatura)        Wortbudget          (ebooklib)       index.html im
                                      20 min × 230/min                     Heimnetz, Port 8080
```

| Datei | Aufgabe |
|---|---|
| `morgenzeitung.py` | Nachrichten holen, auswählen, EPUB bauen |
| `layout.py` | Gestaltung: Titelblatt, Inhaltsseite, Artikelseiten, CSS |
| `zeit.py` | ZEIT-Ausgabe aus dem Postfach holen, zerlegen, zwei Longreads pro Tag wählen |
| `config.toml` | Quellen, Lesezeit, Titel |
| `crontab.example` | startet das Programm jeden Morgen |
| `morgenzeitung-web.service` | Mini-Webserver, über den der Tolino die Datei lädt |
| `tests/` | Offline-Tests mit erfundenen Feeds |

**Drei Ideen, die du im Code wiederfindest:**

- **Reihum auswählen** (`auswaehlen()`): Aus jeder Quelle kommt abwechselnd ein Artikel dazu, bis
  ca. 4.600 Wörter beisammen sind. So ist jede Quelle vertreten. Die Reihenfolge in `config.toml`
  ist die Priorität.
- **Text statt Webseite** (`text_extrahieren()`): trafilatura wirft Menüs, Werbung und Kommentare
  weg und behält nur den Artikel.
- **Paywall-Filter über die Länge**: Le-Monde-Abo-Artikel liefern nur den Anfang. Alles unter
  `min_woerter` wird übersprungen, und der nächste Artikel rückt nach.

- **Stichwortfilter für den WDR** (`nur_mit`): Der allgemeine WDR-Feed deckt ganz NRW ab. Es
  kommen nur Meldungen durch, deren Titel oder Anriss eines der Stichwörter enthält (Bonn,
  Rhein-Sieg, Landtag, Landesregierung …).

## ZEIT-Longreads

Ein EPUB ist ein ZIP-Archiv voller XHTML-Dateien. `zeit.py` liest das Inhaltsverzeichnis
(daher kommen Titel und Ressort) und nimmt jede Datei als einen Artikel. Ausgewählt werden
Texte zwischen 1.000 und 2.500 Wörtern, bevorzugt in der Reihenfolge Dossier → Politik →
Wirtschaft → Wissen → Feuilleton, möglichst aus zwei verschiedenen Ressorts. In
`zeit/zeit_status.json` steht, was schon dran war. Kommt eine neue Ausgabe, beginnt die
Rotation von vorn. Eine Ausgabe reicht damit locker für die ganze Woche.

**So kommt die Ausgabe auf den Pi:**

- *Automatisch:* Den Abschnitt `[zeit.imap]` in `config.toml` ausfüllen. Das Programm schaut jeden
  Morgen nur lesend ins Postfach und holt neue `.epub`-Anhänge vom Absender `zeit.de`.
  Nimm ein App-Passwort, wenn dein Mailanbieter das anbietet, und schütze die Datei:
  `chmod 600 ~/.config/morgenzeitung/imap-passwort`.
- *Von Hand:* Die EPUB-Datei einfach nach `~/morgenzeitung/zeit/` kopieren.

**Lesezeit:** Mit `zaehlt_zur_lesezeit = true` werden die Longreads auf die 20 Minuten
angerechnet, und es kommen entsprechend weniger Nachrichten. Ein Drittel des Budgets bleibt immer
für Nachrichten reserviert. Mit `false` kommen die Longreads obendrauf.

**Erster Test mit deiner echten Ausgabe:**

```bash
.venv/bin/python -c "import zeit; [print(a['woerter'], a['ressort'], '|', a['titel']) \
  for a in zeit.artikel_lesen(__import__('pathlib').Path('zeit/DATEINAME.epub'))]"
```

Damit siehst du, ob Titel, Ressorts und Wortzahlen sinnvoll erkannt werden. Falls nicht, schick
mir die Ausgabe dieses Befehls (nicht das EPUB selbst), dann passe ich die Zerlegung an.

## Installation auf dem Pi (einmalig, ca. 15 Minuten)

```bash
sudo apt install python3-venv fonts-dejavu-core
# Archiv auf den Pi kopieren und entpacken, dann:
cd ~/morgenzeitung
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp config.example.toml config.toml
.venv/bin/python -m pytest -q          # sollte "11 passed" melden
```

**Erster Lauf von Hand:**

```bash
.venv/bin/python morgenzeitung.py --nur-feeds   # Kommen die Feeds an? (schnell)
.venv/bin/python morgenzeitung.py               # Zeitung bauen
ls ~/morgenzeitung/ausgaben/                    # EPUB + index.html da?
```

Im Log steht pro Artikel ✓ mit Wortzahl oder ✗ mit Grund, am Ende die geschätzte Lesezeit.

**Webserver einrichten:**

```bash
sudo cp morgenzeitung-web.service /etc/systemd/system/
sudo systemctl enable --now morgenzeitung-web
hostname -I                                     # zeigt die IP des Pi, z. B. 192.168.178.40
```

**Jeden Morgen automatisch:** `crontab -e` und die Zeile aus `crontab.example` einfügen.

## Am Tolino

1. Den eingebauten Webbrowser öffnen und `http://<IP-des-Pi>:8080` aufrufen.
2. Seite als Lesezeichen speichern.
3. Morgens: Lesezeichen öffnen → „Heute laden“. Die Ausgabe landet in deiner Bibliothek.

Tipp: Gib dem Pi in der Fritzbox eine feste IP („Diesem Netzwerkgerät immer die gleiche
IPv4-Adresse zuweisen“), sonst ist das Lesezeichen irgendwann tot.

## Wenn etwas hakt

| Problem | Lösung |
|---|---|
| Eine Quelle bringt immer „Feed nicht erreichbar“ | Feed-Adresse hat sich geändert → auf der Website neue RSS-Adresse suchen, in `config.toml` eintragen |
| WDR liefert zu wenig | Stichwörter in `nur_mit` ergänzen – oder einen eigenen Rheinland-Feed suchen (siehe Übung 2) |
| ZEIT: „kopiergeschützt (DRM)“ | Diese Ausgabe lässt sich nicht zerlegen → beim Abo das DRM-freie EPUB wählen, falls angeboten |
| Zeitung zu kurz/lang | `lesezeit_minuten` oder `artikel` pro Quelle anpassen |
| Tolino findet die Seite nicht | Pi und Tolino im selben WLAN? `systemctl status morgenzeitung-web` |

## Übungen zum Vertiefen

1. **Wortbudget verstehen.** Setze `lesezeit_minuten = 5` und starte das Programm. Welche Quellen
   fallen weg, und warum gerade diese? Tipp: Lies `auswaehlen()`.
2. **Feed selbst finden.** Webseiten verraten ihre Feeds oft im Quelltext. Probier auf dem Pi:
   `curl -s https://www1.wdr.de/nachrichten/rheinland/index.html | grep -o 'https[^"]*\.feed' | sort -u`.
   Trag einen gefundenen Feed als eigene Quelle ein, lösche dort `nur_mit` und vergleiche mit
   `--nur-feeds`. Welche Variante bringt mehr Bonn?
3. **Test schreiben.** Ergänze in `tests/test_offline.py` einen Test, der prüft, dass ein Artikel
   mit *genau* `min_woerter` Wörtern genommen wird. Was verrät das über `<` und `<=` in
   `artikel_holen()`?
