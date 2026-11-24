"""ZEIT-Longreads: die Wochenausgabe (EPUB) zerlegen und täglich zwei Artikel liefern.

Ablauf:
  1. mails_abholen()   neue ZEIT-EPUBs per IMAP aus dem Postfach holen (optional)
  2. artikel_lesen()   die neueste Ausgabe in einzelne Artikel zerlegen
  3. longreads_fuer_heute()  zwei noch nicht gelesene Artikel auswählen und
                       in zeit_status.json vermerken, damit nichts doppelt kommt

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

log = logging.getLogger("morgenzeitung.zeit")

NS = {"opf": "http://www.idpf.org/2007/opf", "c": "urn:oasis:names:tc:opendocument:xmlns:container",
      "ncx": "http://www.daisy.org/z3986/2005/ncx/", "x": "http://www.w3.org/1999/xhtml",
      "ops": "http://www.idpf.org/2007/ops"}


def standard(cfg: dict) -> dict:
    z = cfg.setdefault("zeit", {})
    z.setdefault("aktiv", False)
    z.setdefault("ordner", "~/morgenzeitung/zeit")
    z.setdefault("pro_tag", 2)
    z.setdefault("min_woerter", 1000)
    z.setdefault("max_woerter", 2500)
    z.setdefault("zaehlt_zur_lesezeit", True)
    z.setdefault("ressorts_bevorzugt", ["Dossier", "Politik", "Wirtschaft", "Wissen", "Feuilleton"])
    z.setdefault("ausschliessen", ["Leserbrief", "Impressum", "Inhalt", "Rätsel", "Kreuzwort",
                                   "Anzeige", "Chancen", "Stellenmarkt", "Was bisher geschah"])
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


def longreads_fuer_heute(z: dict, status_datei: Path, heute: str) -> list[dict]:
    """Wählt pro Tag z['pro_tag'] Artikel. Wird das Programm am selben Tag
    zweimal gestartet, kommen dieselben Artikel – nichts wird 'verbraucht'."""
    ausgabe = neueste_ausgabe(z)
    if ausgabe is None:
        log.info("ZEIT: noch keine Ausgabe im Ordner")
        return []
    status = json.loads(status_datei.read_text()) if status_datei.exists() else {}
    if status.get("ausgabe") != ausgabe.name:              # neue Woche → von vorn
        status = {"ausgabe": ausgabe.name, "tage": {}}

    alle = {a["id"]: a for a in artikel_lesen(ausgabe)}
    if heute in status["tage"]:
        return [alle[i] for i in status["tage"][heute] if i in alle]

    benutzt = {i for ids in status["tage"].values() for i in ids}
    muster = [m.lower() for m in z["ausschliessen"]]
    kandidaten = [a for a in alle.values()
                  if a["id"] not in benutzt
                  and z["min_woerter"] <= a["woerter"] <= z["max_woerter"]
                  and not any(m in (a["titel"] + " " + a["ressort"]).lower() for m in muster)]
    rang = {r.lower(): i for i, r in enumerate(z["ressorts_bevorzugt"])}
    kandidaten.sort(key=lambda a: rang.get(a["ressort"].lower(), len(rang)))

    # Möglichst aus verschiedenen Ressorts – sonst zwei Politik-Texte hintereinander
    auswahl, ressorts = [], set()
    for a in kandidaten:
        if a["ressort"] not in ressorts or not a["ressort"]:
            auswahl.append(a)
            ressorts.add(a["ressort"])
        if len(auswahl) == z["pro_tag"]:
            break
    for a in kandidaten:                                   # auffüllen, falls Ressorts knapp
        if len(auswahl) == z["pro_tag"]:
            break
        if a not in auswahl:
            auswahl.append(a)

    status["tage"][heute] = [a["id"] for a in auswahl]
    status_datei.parent.mkdir(parents=True, exist_ok=True)
    status_datei.write_text(json.dumps(status, indent=2, ensure_ascii=False))
    rest = len(kandidaten) - len(auswahl)
    log.info("ZEIT: %d Longreads (%s), noch %d unbenutzte in dieser Ausgabe",
             len(auswahl), ", ".join(a["titel"][:40] for a in auswahl), rest)
    return auswahl
