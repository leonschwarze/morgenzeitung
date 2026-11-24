"""Fertige Zeitungsausgaben als EPUB (ZEIT, SZ) zerlegen und Artikel für heute auswählen.

Ablauf:
  1. mails_abholen()        neue EPUBs per IMAP aus dem Postfach holen (optional)
  2. artikel_lesen()        die neueste Ausgabe in einzelne Artikel zerlegen
  3. auswahl_fuer_heute()   Artikel nach Ressort-Regeln auswählen und in
                            <ordner>/status.json vermerken, damit nichts doppelt kommt

Dieselben Funktionen bedienen [zeit] und [sz] in der Konfiguration – nur die
Regeln (welche Ressorts, wie viele Artikel) unterscheiden sich.

Ein EPUB ist nur ein ZIP-Archiv mit XHTML-Dateien. Die Datei content.opf
legt die Lesereihenfolge fest (der "spine"), das Inhaltsverzeichnis (nav.xhtml
oder toc.ncx) liefert Titel und Ressorts. Mehr braucht es nicht – deshalb
lesen wir es direkt mit zipfile + lxml statt mit einer schweren Bibliothek.
"""
import email
import email.header
import html
import imaplib
import json
import logging
import posixpath
import re
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import unquote
from zipfile import ZipFile

from lxml import etree
from lxml import html as lh

log = logging.getLogger("morgenzeitung.ausgabe")

NS = {"opf": "http://www.idpf.org/2007/opf", "c": "urn:oasis:names:tc:opendocument:xmlns:container",
      "ncx": "http://www.daisy.org/z3986/2005/ncx/", "x": "http://www.w3.org/1999/xhtml",
      "ops": "http://www.idpf.org/2007/ops"}


STANDARDS = {
    "zeit": {
        "name": "ZEIT",
        "ordner": "~/.local/share/morgenzeitung/zeit",
        "pro_tag": 2,
        "davon_andere": 1,          # so viele "Entdeckungen" aus nicht bevorzugten Ressorts
        "min_woerter": 1000,
        "max_woerter": 2500,
        "zaehlt_zur_lesezeit": True,
        "immer": [],
        "ressorts_bevorzugt": ["Dossier", "Politik", "Wirtschaft"],
        "ressorts_ausschliessen": ["Hamburg", "Osten", "Schweiz"],
        "ausschliessen": ["Leserbrief", "Impressum", "Inhalt", "Rätsel", "Kreuzwort", "Anzeige",
                          "Chancen", "Stellenmarkt", "Was bisher geschah"],
    },
    "sz": {
        "name": "SZ",
        "ordner": "~/.local/share/morgenzeitung/sz",
        "pro_tag": 5,
        "davon_andere": 1,
        "min_woerter": 250,
        "max_woerter": 2000,
        "zaehlt_zur_lesezeit": True,
        "immer": ["Seite Drei", "Seite 3"],
        "ressorts_bevorzugt": ["Politik", "Wirtschaft"],
        "ressorts_ausschliessen": ["München", "Bayern", "Region", "Landkreis", "Dachau", "Ebersberg",
                                   "Erding", "Freising", "Fürstenfeldbruck", "Starnberg",
                                   "Wolfratshausen", "Bad Tölz"],
        "ausschliessen": ["Impressum", "Inhalt", "Rätsel", "Kreuzwort", "Anzeige", "Traueranzeige",
                          "Fernsehprogramm", "TV-Programm", "Wetter"],
    },
}


def standard(cfg: dict, abschnitt: str = "zeit") -> dict:
    z = cfg.setdefault(abschnitt, {})
    z.setdefault("aktiv", False)
    for k, v in STANDARDS[abschnitt].items():
        z.setdefault(k, list(v) if isinstance(v, list) else v)
    z.setdefault("imap", {})
    return z


# --------------------------------------------------------------------------- 1. Postfach
def mails_abholen(z: dict) -> int:
    """Lädt EPUB-Anhänge der letzten 10 Tage vom Absender herunter.
    Das Postfach wird nur gelesen (readonly) – nichts wird markiert oder gelöscht."""
    imap = z["imap"]
    if not imap.get("server"):
        return 0
    ordner = Path(z["ordner"]).expanduser()
    ordner.mkdir(parents=True, exist_ok=True)
    passwort = Path(imap["passwort_datei"]).expanduser().read_text().strip()
    seit = (datetime.now() - timedelta(days=10)).strftime("%d-%b-%Y")
    neu = 0
    with imaplib.IMAP4_SSL(imap["server"], imap.get("port", 993)) as m:
        m.login(imap["benutzer"], passwort)
        m.select(imap.get("postfach", "INBOX"), readonly=True)
        _, treffer = m.search(None, f'(SINCE {seit} FROM "{imap.get("absender_enthaelt", "zeit.de")}")')
        for nr in treffer[0].split():
            _, daten = m.fetch(nr, "(RFC822)")
            nachricht = email.message_from_bytes(daten[0][1])
            for teil in nachricht.walk():
                name = teil.get_filename() or ""
                if not name.lower().endswith(".epub"):
                    continue
                name = str(email.header.make_header(email.header.decode_header(name)))
                ziel = ordner / name.replace("/", "_")
                if not ziel.exists():
                    ziel.write_bytes(teil.get_payload(decode=True))
                    log.info("ZEIT-Ausgabe aus dem Postfach geholt: %s", ziel.name)
                    neu += 1
    return neu


# --------------------------------------------------------------------------- 2. Zerlegen
def _toc(zf: ZipFile, opf_pfad: str, opf) -> dict[str, tuple[str, str]]:
    """Datei → (Titel, Ressort) aus dem Inhaltsverzeichnis. Ressort = der
    übergeordnete Eintrag, z. B. 'Politik' über 'Die Koalition wackelt'."""
    basis = posixpath.dirname(opf_pfad)
    ergebnis = {}

    def merken(href, titel, ressort):
        datei = posixpath.normpath(posixpath.join(basis, unquote(href.split("#")[0])))
        ergebnis.setdefault(datei, (titel.strip(), ressort.strip()))

    nav = opf.find(".//opf:manifest/opf:item[@properties='nav']", NS)
    if nav is not None:                                    # EPUB 3
        pfad = posixpath.normpath(posixpath.join(basis, nav.get("href")))
        baum = lh.fromstring(zf.read(pfad))
        basis_nav = posixpath.dirname(pfad)

        def ablaufen(liste, ressort=""):
            for li in liste.findall("li"):
                a = li.find("a")
                if a is None:
                    a = li.find("span")
                titel = a.text_content() if a is not None else ""
                unter = li.find("ol")
                # Ein Eintrag mit Untereinträgen ist ein Ressort, kein Artikel
                if a is not None and a.get("href") and unter is None:
                    datei = posixpath.normpath(posixpath.join(basis_nav, unquote(a.get("href").split("#")[0])))
                    ergebnis.setdefault(datei, (titel.strip(), ressort.strip()))
                if unter is not None:
                    ablaufen(unter, titel if not ressort else ressort)
        for ol in baum.xpath("//nav[@*[local-name()='type']='toc']/ol") or baum.xpath("//nav/ol")[:1]:
            ablaufen(ol)
        return ergebnis

    ncx_item = opf.find(".//opf:manifest/opf:item[@media-type='application/x-dtbncx+xml']", NS)
    if ncx_item is not None:                               # EPUB 2
        pfad = posixpath.normpath(posixpath.join(basis, ncx_item.get("href")))
        ncx = etree.fromstring(zf.read(pfad))
        basis = posixpath.dirname(pfad)

        def punkte(knoten, ressort=""):
            for p in knoten.findall("ncx:navPoint", NS):
                titel = "".join(p.find("ncx:navLabel", NS).itertext())
                if p.find("ncx:navPoint", NS) is not None:   # hat Kinder → Ressort
                    punkte(p, titel if not ressort else ressort)
                else:
                    merken(p.find("ncx:content", NS).get("src"), titel, ressort)
        punkte(ncx.find("ncx:navMap", NS))
    return ergebnis


BLOCK = {"p", "h2", "h3", "h4", "li"}
INLINE = {"em", "strong", "i", "b", "br"}


def _saeubern(body) -> str:
    """Nur Absätze, Zwischenüberschriften und Hervorhebungen behalten – keine
    Bilder, Klassen oder Skripte. So sieht jeder Longread im Tolino gleich aus
    wie der Rest der Zeitung."""
    for el in body.xpath(".//script|.//style|.//img|.//svg|.//figure|.//aside|.//nav|.//h1"):
        el.drop_tree()
    teile = []
    for el in body.iter():
        if not isinstance(el.tag, str) or etree.QName(el).localname not in BLOCK:
            continue
        if any(isinstance(v.tag, str) and etree.QName(v).localname in BLOCK for v in el.iterancestors()):
            continue                                      # schon über den Elternblock erfasst
        kopie = deepcopy(el)
        for unter in list(kopie.iterdescendants()):
            if not isinstance(unter.tag, str):
                continue
            name = etree.QName(unter).localname
            if name in INLINE:
                unter.tag = name
                unter.attrib.clear()
            else:
                unter.drop_tag()
        innen = html.escape(kopie.text or "") + "".join(
            lh.tostring(k, encoding="unicode", method="xml") for k in kopie)
        if innen.strip():
            tag = "p" if etree.QName(el).localname == "li" else etree.QName(el).localname
            teile.append(f"<{tag}>{innen.strip()}</{tag}>")
    return "\n".join(teile)


def artikel_lesen(pfad: Path) -> list[dict]:
    """Zerlegt eine Ausgabe in Artikel: [{id, titel, ressort, html, woerter}, …]"""
    with ZipFile(pfad) as zf:
        namen = set(zf.namelist())
        if "META-INF/encryption.xml" in namen and b"font" not in zf.read("META-INF/encryption.xml").lower():
            raise ValueError(f"{pfad.name} ist kopiergeschützt (DRM) – kann nicht zerlegt werden")
        container = etree.fromstring(zf.read("META-INF/container.xml"))
        opf_pfad = container.find(".//c:rootfile", NS).get("full-path")
        opf = etree.fromstring(zf.read(opf_pfad))
        basis = posixpath.dirname(opf_pfad)
        manifest = {i.get("id"): i.get("href") for i in opf.findall(".//opf:manifest/opf:item", NS)}
        toc = _toc(zf, opf_pfad, opf)

        artikel = []
        for ref in opf.findall(".//opf:spine/opf:itemref", NS):
            datei = posixpath.normpath(posixpath.join(basis, unquote(manifest[ref.get("idref")])))
            baum = lh.fromstring(zf.read(datei))
            body = baum.find(".//body")
            if body is None:
                continue
            ueberschrift = body.xpath("(.//h1|.//h2)[1]")
            titel, ressort = toc.get(datei, ("", ""))
            titel = titel or (ueberschrift[0].text_content().strip() if ueberschrift else "")
            text = _saeubern(body)
            artikel.append({"id": datei, "titel": re.sub(r"\s+", " ", titel), "ressort": ressort,
                            "html": text, "woerter": len(re.sub(r"<[^>]+>", " ", text).split())})
    return artikel


# --------------------------------------------------------------------------- 3. Auswahl
def neueste_ausgabe(z: dict) -> Path | None:
    dateien = sorted(Path(z["ordner"]).expanduser().glob("*.epub"), key=lambda p: p.stat().st_mtime)
    return dateien[-1] if dateien else None


def _passt(text: str, muster: list[str]) -> bool:
    text = text.lower()
    return any(m.lower() in text for m in muster)


def auswahl_fuer_heute(z: dict, status_datei: Path, heute: str) -> list[dict]:
    """Wählt die Artikel für heute nach diesen Regeln:

      1. "immer"   – Ressorts/Titel, die jeden Tag rein sollen (z. B. Seite Drei),
                     unabhängig von der Länge
      2. bevorzugt – die übrigen Plätze aus den Schwerpunkt-Ressorts,
                     möglichst aus verschiedenen Ressorts
      3. "andere"  – davon_andere Plätze für Entdeckungen aus anderen Ressorts;
                     es kommt das Ressort dran, das am längsten nicht dran war

    Wird das Programm am selben Tag zweimal gestartet, kommen dieselben Artikel."""
    ausgabe = neueste_ausgabe(z)
    if ausgabe is None:
        log.info("%s: noch keine Ausgabe im Ordner", z["name"])
        return []
    status = json.loads(status_datei.read_text()) if status_datei.exists() else {}
    if status.get("ausgabe") != ausgabe.name:              # neue Ausgabe → von vorn
        status = {"ausgabe": ausgabe.name, "tage": {}, "ressorts_zuletzt": status.get("ressorts_zuletzt", {})}
    status.setdefault("ressorts_zuletzt", {})

    alle = {a["id"]: a for a in artikel_lesen(ausgabe)}
    if heute in status["tage"]:
        return [alle[i] for i in status["tage"][heute] if i in alle]

    benutzt = {i for ids in status["tage"].values() for i in ids}
    frei = [a for a in alle.values()
            if a["id"] not in benutzt
            and not _passt(a["ressort"], z["ressorts_ausschliessen"])
            and not _passt(a["titel"] + " " + a["ressort"], z["ausschliessen"])
            and a["woerter"] > 0]

    # 1. immer dabei – höchstens ein Artikel pro Muster
    auswahl = []
    for m in z["immer"]:
        treffer = [a for a in frei if _passt(a["ressort"] + " " + a["titel"], [m]) and a not in auswahl]
        schon_da = any(_passt(b["ressort"] + " " + b["titel"], [m]) for b in auswahl)
        if treffer and not schon_da:
            auswahl.append(max(treffer, key=lambda a: a["woerter"]))

    laenge_ok = [a for a in frei if a not in auswahl
                 and z["min_woerter"] <= a["woerter"] <= z["max_woerter"]]
    bevorzugt = [a for a in laenge_ok if _passt(a["ressort"], z["ressorts_bevorzugt"])]
    andere = [a for a in laenge_ok if a not in bevorzugt]
    rang = {r.lower(): i for i, r in enumerate(z["ressorts_bevorzugt"])}
    bevorzugt.sort(key=lambda a: min((i for r, i in rang.items() if r in a["ressort"].lower()),
                                     default=len(rang)))

    def verschiedene(liste, anzahl):
        """Erst ein Artikel pro Ressort, dann auffüllen."""
        gewaehlt, ressorts = [], set()
        for a in liste:
            if len(gewaehlt) == anzahl:
                break
            if a["ressort"] not in ressorts:
                gewaehlt.append(a)
                ressorts.add(a["ressort"])
        for a in liste:
            if len(gewaehlt) == anzahl:
                break
            if a not in gewaehlt:
                gewaehlt.append(a)
        return gewaehlt

    plaetze = max(0, z["pro_tag"] - len(auswahl))
    n_andere = min(z["davon_andere"], plaetze)
    # 3. Entdeckungen: Ressort, das am längsten nicht dran war; darin der längste Text
    zuletzt = status["ressorts_zuletzt"]
    andere.sort(key=lambda a: (zuletzt.get(a["ressort"], ""), -a["woerter"]))
    entdeckungen = verschiedene(andere, n_andere)
    # 2. Schwerpunkte
    schwerpunkte = verschiedene(bevorzugt, plaetze - len(entdeckungen))
    auswahl += schwerpunkte + entdeckungen
    if len(auswahl) < z["pro_tag"]:                        # zu wenig Schwerpunkt-Texte → auffüllen
        auswahl += [a for a in andere if a not in auswahl][: z["pro_tag"] - len(auswahl)]

    for a in auswahl:
        zuletzt[a["ressort"]] = heute
    status["tage"][heute] = [a["id"] for a in auswahl]
    status_datei.parent.mkdir(parents=True, exist_ok=True)
    status_datei.write_text(json.dumps(status, indent=2, ensure_ascii=False))
    log.info("%s: %d Artikel – %s", z["name"], len(auswahl),
             "; ".join(f"{a['ressort'] or '?'}: {a['titel'][:35]}" for a in auswahl))
    return auswahl


if __name__ == "__main__":
    # Prüfbefehl: zeigt, wie eine Ausgabe zerlegt wird – Wortzahl, Ressort, Titel.
    #   .venv/bin/python ausgabe.py ~/.local/share/morgenzeitung/sz/DATEI.epub
    import sys
    for a in artikel_lesen(Path(sys.argv[1]).expanduser()):
        print(f"{a['woerter']:>6}  {a['ressort'][:22]:<22}  {a['titel'][:70]}")
