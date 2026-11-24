#!/usr/bin/env python3
"""Morgenzeitung: RSS-Feeds → Volltext → EPUB → Download-Seite für den Tolino.

Ablauf (jede Stufe ist eine eigene Funktion, damit du sie einzeln testen kannst):
  1. kandidaten_sammeln()  Feeds lesen, frische Einträge pro Quelle auswählen
  2. artikel_holen()       Seite laden, Artikeltext extrahieren
  3. auswaehlen()          so viele Artikel nehmen, dass es ~20 Minuten Lesezeit sind
  4. epub_bauen()          Titelseite, Inhaltsverzeichnis, ein Kapitel pro Artikel
  5. downloadseite()       index.html, die der Tolino-Browser öffnet

Aufruf:
  python morgenzeitung.py              # Ausgabe bauen
  python morgenzeitung.py --config PFAD  # andere Konfigurationsdatei
  python morgenzeitung.py --nur-feeds  # zeigt nur, was ausgewählt würde (schnell)
"""
import html
import logging
import re
import sys
import time
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import layout
from layout import datum_lang

log = logging.getLogger("morgenzeitung")
HIER = Path(__file__).resolve().parent

# Programm und Nutzerdaten sind getrennt (Linux-Konvention "XDG"):
#   Programm  ~/morgenzeitung/                    ← git-Repo, wird per "git pull" ersetzt
#   Konfig    ~/.config/morgenzeitung/            ← config.toml, IMAP-Passwort
#   Daten     ~/.local/share/morgenzeitung/       ← Ausgaben, ZEIT-EPUBs, Log
KONFIG_ORDNER = Path("~/.config/morgenzeitung").expanduser()
DATEN_ORDNER = Path("~/.local/share/morgenzeitung").expanduser()
UA = ("Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")


# --------------------------------------------------------------------------- Konfiguration
def config_finden(pfad=None) -> Path:
    """Suchreihenfolge: --config PFAD → Umgebungsvariable MORGENZEITUNG_CONFIG →
    ~/.config/morgenzeitung/config.toml → (alt) config.toml im Programmordner."""
    import os
    kandidaten = [pfad, os.environ.get("MORGENZEITUNG_CONFIG"), KONFIG_ORDNER / "config.toml"]
    for k in kandidaten:
        if k and Path(k).expanduser().exists():
            return Path(k).expanduser()
    alt = HIER / "config.toml"
    if alt.exists():
        log.warning("config.toml liegt noch im Programmordner – bitte umziehen: "
                    "sh umzug.sh (siehe README)")
        return alt
    raise SystemExit(f"Keine Konfiguration gefunden. Anlegen mit:\n"
                     f"  mkdir -p {KONFIG_ORDNER} && cp {HIER / 'config.example.toml'} "
                     f"{KONFIG_ORDNER / 'config.toml'}")


def lade_config(pfad=None) -> dict:
    pfad = config_finden(pfad)
    with open(pfad, "rb") as f:
        cfg = tomllib.load(f)
    z = cfg.setdefault("zeitung", {})
    z.setdefault("titel", "Morgenzeitung")
    z.setdefault("untertitel", "")
    z.setdefault("lesezeit_minuten", 20)
    z.setdefault("woerter_pro_minute", 230)
    z.setdefault("max_alter_stunden", 20)
    z.setdefault("ausgabe_ordner", str(DATEN_ORDNER / "ausgaben"))
    z.setdefault("aufbewahren_tage", 7)
    import bilder
    bilder.standard(cfg)
    for q in cfg.get("quelle", []):
        q.setdefault("artikel", 2)
        q.setdefault("modus", "volltext")
        q.setdefault("min_woerter", 150)
        q.setdefault("ausschliessen", [])
        q.setdefault("nur_mit", [])
        q.setdefault("sprache", "de")
    return cfg


# --------------------------------------------------------------------------- Datenmodell
@dataclass
class Artikel:
    quelle: str
    titel: str
    url: str
    zeit: datetime | None
    anriss: str = ""
    html: str = ""                 # extrahierter Artikeltext als HTML
    woerter: int = 0
    autor: str = ""
    ressort: str = ""
    dachzeile: str = ""
    sprache: str = "de"
    bilder: list = field(default_factory=list)   # fertige JPEGs, im HTML als BILDNR:n referenziert
    bild_quellen: list = field(default_factory=list)  # [(url, unterschrift)] – noch nicht geladen
    notizen: list = field(default_factory=list)


def woerter_zaehlen(text: str) -> int:
    return len(re.sub(r"<[^>]+>", " ", text).split())


def ohne_tags(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", text or "")).strip()


# --------------------------------------------------------------------------- HTTP
class Netz:
    """Eine gemeinsame requests-Session: hält die Verbindung offen und schickt
    einen normalen Browser-User-Agent (manche Seiten blocken Python sonst)."""

    def __init__(self):
        import requests
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Accept-Language": "de-DE,de;q=0.9,fr;q=0.6"})

    def holen(self, url: str) -> bytes:
        r = self.s.get(url, timeout=25)
        r.raise_for_status()
        return r.content


# --------------------------------------------------------------------------- 1. Feeds
def kandidaten_sammeln(cfg: dict, netz) -> dict[str, list[Artikel]]:
    import feedparser
    grenze = datetime.now() - timedelta(hours=cfg["zeitung"]["max_alter_stunden"])
    ergebnis, gesehen = {}, set()
    for q in cfg["quelle"]:
        try:
            feed = feedparser.parse(netz.holen(q["feed"]))
        except Exception as e:
            log.warning("%s: Feed nicht erreichbar (%s)", q["name"], e)
            ergebnis[q["name"]] = []
            continue
        liste = []
        for e in feed.entries:
            url = e.get("link", "").split("?")[0]
            titel = ohne_tags(e.get("title", ""))
            if not url or url in gesehen:
                continue
            if any(m.lower() in (titel + " " + url).lower() for m in q["ausschliessen"]):
                continue
            anriss = ohne_tags(e.get("summary", ""))[:400]
            if q["nur_mit"] and not any(m.lower() in (titel + " " + anriss).lower()
                                        for m in q["nur_mit"]):
                continue
            t = e.get("published_parsed") or e.get("updated_parsed")
            zeit = datetime.fromtimestamp(time.mktime(t)) if t else None
            if zeit and zeit < grenze:
                continue
            gesehen.add(url)
            liste.append(Artikel(q["name"], titel, url, zeit, anriss=anriss, sprache=q["sprache"]))
        log.info("%s: %d frische Einträge", q["name"], len(liste))
        ergebnis[q["name"]] = liste
    return ergebnis


# --------------------------------------------------------------------------- 2. Volltext
def text_extrahieren(roh: bytes, url: str) -> tuple[str, str, list[tuple[str, str]]]:
    """Gibt (html, autor, bilder) zurück. trafilatura wirft Navigation, Werbung,
    Kommentare usw. weg und lässt nur den eigentlichen Artikel übrig – samt
    Zwischentiteln, Zitatblöcken, Listen und Bildern.

    Bildunterschriften verwirft trafilatura leider. Deshalb suchen wir sie selbst
    in der Originalseite: Zu jedem Bild die <figcaption> der umgebenden <figure>.
    bilder = [(bild_url, unterschrift), …], im HTML stehen Platzhalter BILD:n."""
    import trafilatura
    from urllib.parse import urljoin
    from lxml import html as lh
    import bilder as bm
    ergebnis = trafilatura.extract(roh, url=url, output_format="html", include_images=True,
                                   include_links=False, include_tables=False,
                                   include_comments=False, include_formatting=True,
                                   favor_precision=True)
    meta = trafilatura.extract_metadata(roh)
    autor = (meta.author or "") if meta else ""
    if not ergebnis:
        return "", autor, []

    # Originalseite: Bilddatei → Unterschrift (Dateiname als Schlüssel, weil Größen-
    # Parameter in den Adressen oft abweichen)
    def schluessel(adresse):
        return adresse.split("?")[0].rstrip("/").rsplit("/", 1)[-1].lower()

    unterschriften = {}
    try:
        original = lh.document_fromstring(roh)
        for img in original.iter("img"):
            kandidaten = [img.get(a, "") for a in ("src", "data-src", "data-original")]
            kandidaten += [t.split()[0] for t in (img.get("srcset") or img.get("data-srcset") or "").split(",") if t.strip()]
            text = bm.unterschrift_finden(img)
            for k in kandidaten:
                if k and text:
                    unterschriften.setdefault(schluessel(k), text)
        aufmacher = original.xpath("//meta[@property='og:image']/@content")
    except Exception:
        aufmacher = []

    body = lh.fromstring(ergebnis).find(".//body")
    teile, bildliste = [], []

    def bild_hinzu(adresse):
        adresse = urljoin(url, adresse)
        if adresse.startswith("data:") or any(schluessel(adresse) == schluessel(a) for a, _ in bildliste):
            return None
        bildliste.append((adresse, unterschriften.get(schluessel(adresse), "")))
        return bm.figur(len(bildliste) - 1, bildliste[-1][1])

    # Aufmacherbild (og:image) steht oft außerhalb des Artikeltexts – dann kommt es nach vorn
    im_text = {schluessel(urljoin(url, i.get("src", ""))) for i in body.iter("img")} if body is not None else set()
    if aufmacher and schluessel(urljoin(url, aufmacher[0])) not in im_text:
        figur = bild_hinzu(aufmacher[0])
        if figur:
            teile.append(figur)

    for el in body if body is not None else []:
        if el.tag == "h1":            # Titel setzen wir selbst
            continue
        if el.tag == "img":
            figur = bild_hinzu(el.get("src", ""))
            if figur:
                teile.append(figur)
            continue
        for img in el.iter("img"):    # Bild mitten im Absatz → hinter den Absatz
            figur = bild_hinzu(img.get("src", ""))
            img.drop_tree()
            if figur:
                teile.append(figur)
        # Manche Seiten wiederholen die Bildunterschrift als Absatz – dann nur einmal
        if " ".join(el.text_content().split()) in {u for _, u in bildliste if u}:
            continue
        teile.append(lh.tostring(el, encoding="unicode", method="xml"))

    return "\n".join(teile), autor, bildliste


def artikel_holen(art: Artikel, quelle: dict, netz) -> bool:
    """True, wenn der Artikel brauchbar ist. Zu kurz = vermutlich Paywall-Teaser."""
    if quelle["modus"] == "schlagzeilen":
        art.woerter = woerter_zaehlen(art.titel + " " + art.anriss)
        return True
    try:
        roh = netz.holen(art.url)
    except Exception as e:
        # 401/402/403 heißt: Der Server sperrt uns grundsätzlich aus (Paywall,
        # Bot-Schutz). Das merken wir uns pro Quelle, damit wir nicht jeden
        # einzelnen Artikel vergeblich anfragen.
        status = getattr(getattr(e, "response", None), "status_code", None)
        if status in (401, 402, 403):
            quelle["_gesperrt"] = quelle.get("_gesperrt", 0) + 1
        log.warning("  ✗ %s (%s)", art.titel[:60], status or e)
        return False
    art.html, art.autor, art.bild_quellen = text_extrahieren(roh, art.url)
    art.woerter = woerter_zaehlen(re.sub(r"<figcaption>.*?</figcaption>", " ", art.html, flags=re.S))
    if art.woerter < quelle["min_woerter"]:
        log.info("  ✗ %s – nur %d Wörter (Paywall?)", art.titel[:60], art.woerter)
        return False
    log.info("  ✓ %s – %d Wörter", art.titel[:60], art.woerter)
    return True


def bilder_laden(art: Artikel, netz, b: dict):
    """Erst wenn ein Artikel sicher in die Zeitung kommt, werden seine Bilder geladen –
    so lädt der Pi keine Bilder für Artikel, die am Ende doch nicht reinpassen."""
    import bilder
    if not art.bild_quellen:
        return
    roh = []
    for adresse, unterschrift in art.bild_quellen[: b["max_pro_artikel"] + 3]:
        try:
            roh.append((netz.holen(adresse), unterschrift))
        except Exception as e:
            log.info("    Bild nicht geladen (%s)", e)
            roh.append((None, unterschrift))
    roh += [(None, u) for _, u in art.bild_quellen[len(roh):]]   # Rest: Platzhalter entfernen
    art.html, art.bilder = bilder.einsetzen(art.html, roh, b)
    if art.bilder:
        log.info("    %d Bild(er)", len(art.bilder))


# --------------------------------------------------------------------------- 3. Auswahl
def auswaehlen(cfg: dict, kandidaten: dict, netz, budget: int | None = None) -> list[Artikel]:
    """Reihum aus jeder Quelle einen Artikel nehmen, bis das Wortbudget voll ist.

    Warum reihum statt "erst alle GA-Artikel, dann alle WDR-Artikel"?
    Damit bei knappem Budget jede Quelle vertreten ist, statt dass die
    ersten Quellen alles auffressen."""
    z = cfg["zeitung"]
    if budget is None:
        budget = z["lesezeit_minuten"] * z["woerter_pro_minute"]
    quellen = {q["name"]: q for q in cfg["quelle"]}
    zeiger = {name: 0 for name in quellen}
    genommen = {name: [] for name in quellen}
    summe = 0
    fortschritt = True
    while fortschritt and summe < budget:
        fortschritt = False
        for name, q in quellen.items():
            if len(genommen[name]) >= q["artikel"]:
                continue
            if q.get("_gesperrt", 0) >= 2:      # zweimal abgewiesen → Quelle für heute aufgeben
                if not q.get("_gemeldet"):
                    log.warning("%s sperrt den Abruf – Tipp: modus = \"schlagzeilen\"", name)
                    q["_gemeldet"] = True
                continue
            liste = kandidaten.get(name, [])
            while zeiger[name] < len(liste) and q.get("_gesperrt", 0) < 2:
                art = liste[zeiger[name]]
                zeiger[name] += 1
                if not artikel_holen(art, q, netz):
                    continue
                # Ein einzelner Riesenartikel darf das Budget um max. 15 % überziehen
                if summe + art.woerter > budget * 1.15 and genommen[name]:
                    break
                genommen[name].append(art)
                summe += art.woerter
                if "bilder" in cfg:
                    bilder_laden(art, netz, cfg["bilder"])
                fortschritt = True
                break
            if summe >= budget:
                break
    auswahl = [a for name in quellen for a in genommen[name]]
    log.info("Auswahl: %d Artikel, %d Wörter ≈ %d Minuten",
             len(auswahl), summe, round(summe / z["woerter_pro_minute"]))
    return auswahl


# --------------------------------------------------------------------------- 4. EPUB
def epub_bauen(cfg: dict, auswahl: list[Artikel], ziel: Path, heute: datetime) -> Path:
    """Die Gestaltung steckt in layout.py – hier nur der Aufruf und das Log."""
    pfad = layout.epub_bauen(cfg, auswahl, ziel, heute)
    log.info("EPUB geschrieben: %s (%d KB)", pfad, pfad.stat().st_size // 1024)
    return pfad


def aufraeumen(ordner: Path, tage: int):
    grenze = time.time() - tage * 86400
    for f in ordner.glob("Morgenzeitung-*.epub"):
        if f.stat().st_mtime < grenze:
            f.unlink()
            log.info("Alte Ausgabe gelöscht: %s", f.name)


def downloadseite(cfg: dict, ordner: Path):
    """Schreibt index.html in den Ausgabe-Ordner: die neueste Ausgabe als großer
    Knopf oben (gut zu treffen auf E-Ink), ältere darunter. Bewusst ohne
    JavaScript und mit wenig CSS – der Tolino-Browser ist sehr einfach."""
    dateien = sorted(ordner.glob("Morgenzeitung-*.epub"), reverse=True)
    zeilen = []
    for i, f in enumerate(dateien):
        d = datetime.strptime(f.stem.split("-", 1)[1], "%Y-%m-%d")
        name = html.escape(f.name)
        if i == 0:
            zeilen.append(f'<p><a class="heute" href="{name}">Heute laden<br>'
                          f'<small>{datum_lang(d)}</small></a></p>')
            if len(dateien) > 1:
                zeilen.append("<h2>Ältere Ausgaben</h2>")
        else:
            zeilen.append(f'<p><a href="{name}">{datum_lang(d)}</a></p>')
    seite = f"""<!DOCTYPE html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(cfg["zeitung"]["titel"])}</title>
<style>
body {{ font-family: serif; margin: 2em; font-size: 1.3em; }}
a {{ color: #000; }}
a.heute {{ display: block; border: 3px solid #000; padding: 1em; text-align: center;
          font-size: 1.6em; font-weight: bold; text-decoration: none; }}
h2 {{ font-size: 1em; margin-top: 2em; border-bottom: 1px solid #000; }}
</style></head><body>
<h1>{html.escape(cfg["zeitung"]["titel"])}</h1>
{"".join(zeilen) or "<p>Noch keine Ausgabe vorhanden.</p>"}
</body></html>"""
    (ordner / "index.html").write_text(seite, encoding="utf-8")


AUSGABEN = {                       # Abschnitt in config.toml → Bezeichnung in der Zeitung
    "sz": "Süddeutsche Zeitung",
    "zeit": "Die ZEIT · Lange Lektüre",
}


def ausgabe_artikel(cfg: dict, abschnitt: str, heute: datetime) -> list[Artikel]:
    """Artikel aus einer fertigen EPUB-Ausgabe (SZ oder ZEIT) – siehe ausgabe.py."""
    import ausgabe
    ac = ausgabe.standard(cfg, abschnitt)
    if not ac["aktiv"]:
        return []
    try:
        ausgabe.mails_abholen(ac)
    except Exception as e:                     # Postfach weg? Dann eben die vorhandene Ausgabe
        log.warning("%s: Postfach nicht abrufbar (%s)", ac["name"], e)
    try:
        status = Path(ac["ordner"]).expanduser() / "status.json"
        gewaehlt = ausgabe.auswahl_fuer_heute(ac, status, heute.strftime("%Y-%m-%d"))
    except Exception as e:
        log.warning("%s: Ausgabe nicht lesbar (%s)", ac["name"], e)
        return []
    import bilder
    ergebnis = []
    for a in gewaehlt:
        text, jpegs = bilder.einsetzen(a["html"], a.get("bilder_roh", []), cfg["bilder"])
        ergebnis.append(Artikel(AUSGABEN[abschnitt], a["titel"], "", None, html=text,
                                woerter=a["woerter"], ressort=a["ressort"], bilder=jpegs,
                                anriss=a.get("vorspann", ""), autor=a.get("autor", ""),
                                dachzeile=a.get("dachzeile", "")))
    return ergebnis


# --------------------------------------------------------------------------- Hauptprogramm
def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    config_pfad = argv[argv.index("--config") + 1] if "--config" in argv else None
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = lade_config(config_pfad)
    netz = Netz()
    heute = datetime.now()

    # 1. Fertige Ausgaben zuerst: Die SZ ist Hauptquelle, die ZEIT liefert Longreads.
    #    Beide verkleinern (falls zaehlt_zur_lesezeit) das Budget für die Feeds.
    sz = ausgabe_artikel(cfg, "sz", heute)
    longreads = ausgabe_artikel(cfg, "zeit", heute)

    # 2. Ersatzquellen (z. B. Tagesschau mit ersatz_fuer = "sz") nur, wenn die SZ fehlt
    if sz:
        cfg["quelle"] = [q for q in cfg["quelle"] if q.get("ersatz_fuer") != "sz"]
    elif any(q.get("ersatz_fuer") == "sz" for q in cfg["quelle"]) and cfg["sz"]["aktiv"]:
        log.warning("SZ fehlt heute – Ersatzquelle springt ein")

    kandidaten = kandidaten_sammeln(cfg, netz)
    if "--nur-feeds" in argv:
        for name, liste in kandidaten.items():
            print(f"\n== {name}")
            for a in liste[:10]:
                print(f"  {a.zeit:%H:%M}  {a.titel[:80]}" if a.zeit else f"  {a.titel[:80]}")
        return 0

    z = cfg["zeitung"]
    budget = z["lesezeit_minuten"] * z["woerter_pro_minute"]
    for teil, abschnitt in ((sz, "sz"), (longreads, "zeit")):
        if teil and cfg[abschnitt]["zaehlt_zur_lesezeit"]:
            budget -= sum(a.woerter for a in teil)
    budget = max(budget, z["lesezeit_minuten"] * z["woerter_pro_minute"] // 4)
    auswahl = sz + auswaehlen(cfg, kandidaten, netz, budget) + longreads
    if not auswahl:
        log.error("Keine Artikel – Abbruch (Netz weg?)")
        return 1
    ordner = Path(cfg["zeitung"]["ausgabe_ordner"]).expanduser()
    pfad = epub_bauen(cfg, auswahl, ordner, heute)
    aufraeumen(ordner, cfg["zeitung"]["aufbewahren_tage"])

    downloadseite(cfg, ordner)
    return 0


if __name__ == "__main__":
    sys.exit(main())
