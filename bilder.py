"""Bilder für den E-Reader aufbereiten.

Warum überhaupt bearbeiten? Ein Pressefoto hat oft 2–5 MB und 3000 px Breite.
Der Tolino zeigt ~1000 px in 16 Graustufen. Also:
  * in Graustufen umwandeln (Farbe bringt auf E-Ink nichts, kostet aber Platz)
  * auf Reader-Breite verkleinern
  * als einfaches ("baseline") JPEG speichern – das verdaut jeder E-Reader
So wird aus 3 MB ein Bild von 40–80 KB.

Infografiken (Diagramme, Karten, Tabellen) sollen draußen bleiben. Erkannt werden
sie an zwei Merkmalen:
  1. Wörter in Bildunterschrift/Alt-Text wie "Grafik", "Karte", "Quelle:"
  2. Das Bild selbst: Wie viele Graustufen kommen nennenswert vor? Ein Foto hat
     durch Licht, Schatten und Rauschen fast immer 20 und mehr der 64 Stufen –
     auch ein Porträt vor hellem Hintergrund. Diagramme, Karten, Illustrationen
     und Plakate bestehen aus wenigen Flächen und kommen auf deutlich weniger.
     (An echten ZEIT-Bildern kalibriert: Fotos ab ~20, Grafiken/Cover 9–18.)
"""
import io
import logging
import re

log = logging.getLogger("morgenzeitung.bilder")

GRAFIK_WOERTER = re.compile(r"\b(info)?grafik|\bkarte\b|\bdiagramm|\bchart\b|\btabelle\b|"
                            r"\bquelle\s*:|\bscreenshot|\blogo\b|\bgraphique|\binfographie",
                            re.IGNORECASE)
FOTO_WOERTER = re.compile(r"\bfoto\b|\bfotos\b|\bphoto\b|\bbild:|©|\(c\)|dpa|afp|reuters|getty|imago",
                          re.IGNORECASE)


def standard(cfg: dict) -> dict:
    b = cfg.setdefault("bilder", {})
    b.setdefault("aktiv", True)
    b.setdefault("max_pro_artikel", 6)
    b.setdefault("max_gesamt", 40)       # Schutz für Tolino und Pi: höchstens so viele pro Ausgabe
    b.setdefault("breite", 1000)
    b.setdefault("qualitaet", 70)
    b.setdefault("infografiken", False)
    return b


def ist_grafik_laut_text(text: str) -> bool:
    return bool(GRAFIK_WOERTER.search(text or "")) and not FOTO_WOERTER.search(text or "")


GRAFIK_GRENZE = 20


def grauwert_stufen(img) -> int:
    """Wie viele der 64 Graustufen belegen mindestens 0,2 % der Fläche?"""
    klein = img.convert("L")
    klein.thumbnail((256, 256))
    hist = klein.histogram()
    gesamt = sum(hist)
    return sum(1 for i in range(0, 256, 4) if sum(hist[i:i + 4]) > gesamt * 0.002)


def aufbereiten(roh: bytes, b: dict, beschreibung: str = "") -> bytes | None:
    """Gibt fertiges JPEG zurück – oder None, wenn das Bild nicht in die Zeitung soll."""
    from PIL import Image, ImageOps
    if not b["infografiken"] and ist_grafik_laut_text(beschreibung):
        log.info("    Bild übersprungen (Grafik laut Text): %s", beschreibung[:50])
        return None
    try:
        img = Image.open(io.BytesIO(roh))
        if getattr(img, "is_animated", False):
            return None
        # JPEG direkt verkleinert dekodieren: spart auf dem Pi viel Speicher und Zeit
        img.draft("L", (b["breite"], b["breite"]))
        img = ImageOps.exif_transpose(img)
    except Exception as e:
        log.info("    Bild nicht lesbar (%s)", e)
        return None
    w, h = img.size
    if w < 300 or h < 180 or w / h > 4 or h / w > 3:     # Icons, Logos, Banner, Trennlinien
        return None
    if img.mode in ("RGBA", "LA", "P"):                    # Transparenz auf Weiß legen
        img = img.convert("RGBA")
        weiss = Image.new("RGBA", img.size, (255, 255, 255, 255))
        img = Image.alpha_composite(weiss, img)
    if not b["infografiken"] and grauwert_stufen(img) < GRAFIK_GRENZE and not FOTO_WOERTER.search(beschreibung):
        log.info("    Bild übersprungen (sieht aus wie eine Grafik)")
        return None
    img = img.convert("L")
    if w > b["breite"]:
        img = img.resize((b["breite"], round(h * b["breite"] / w)), Image.LANCZOS)
    img = ImageOps.autocontrast(img, cutoff=0.5)           # flaue Fotos wirken auf E-Ink sonst grau
    puffer = io.BytesIO()
    img.save(puffer, "JPEG", quality=b["qualitaet"], optimize=True, progressive=False)
    return puffer.getvalue()


# --------------------------------------------------------------------------- Einbau in Artikel
# Beim Zerlegen eines Artikels wird jedes Bild zunächst durch einen Platzhalter
# ersetzt: <figure class="bild"><img src="BILD:3" …/><figcaption>…</figcaption></figure>.
# Erst einsetzen() entscheidet, welche Bilder bleiben (Foto? Budget noch frei?),
# und nummeriert die übrigen fortlaufend. layout.py macht daraus echte Dateipfade.

def figur(nr: int, unterschrift: str) -> str:
    import html
    text = f"<figcaption>{html.escape(unterschrift)}</figcaption>" if unterschrift else ""
    return f'<figure class="bild"><img src="BILD:{nr}" alt=""/>{text}</figure>'


UNTERSCHRIFT_KLASSE = re.compile(r"caption|credit|copyright|bildunterschrift|legende|source|bu\b", re.I)


def unterschrift_finden(img) -> str:
    """Bildunterschrift zu einem <img>. Gesucht wird
      1. die <figcaption> der umgebenden <figure>,
      2. sonst Elemente mit 'caption', 'credit', 'copyright' o. Ä. im Klassennamen –
         erst im Elternelement, dann eine Ebene höher (viele Seiten verschachteln
         das Bild in <p> oder <span>, die Unterschrift steht daneben)."""
    figur_el = next((a for a in img.iterancestors() if a.tag == "figure"), None)
    if figur_el is not None:
        teile = [c.text_content() for c in figur_el.iter("figcaption")]
        if not teile:
            teile = [c.text_content() for c in figur_el.iter() if isinstance(c.tag, str)
                     and UNTERSCHRIFT_KLASSE.search(c.get("class", ""))]
    else:
        teile, bereich = [], img.getparent()
        for _ in range(2):
            if bereich is None or teile:
                break
            teile = [c.text_content() for c in bereich.iter() if isinstance(c.tag, str)
                     and c is not img and UNTERSCHRIFT_KLASSE.search(c.get("class", ""))]
            bereich = bereich.getparent()
    return " ".join(" ".join(teile).split())[:400]


def einsetzen(html_text: str, roh: list[tuple[bytes, str]], b: dict) -> tuple[str, list[bytes]]:
    """Platzhalter BILD:n → aufbereitete Bilder. Verworfene Bilder verschwinden samt Unterschrift."""
    fertig = []
    for nr, (daten, unterschrift) in enumerate(roh):
        muster = re.compile(r'<figure class="bild"><img src="BILD:%d"[^>]*/>(<figcaption>.*?</figcaption>)?</figure>' % nr,
                            re.S)
        jpeg = None
        if b["aktiv"] and len(fertig) < b["max_pro_artikel"] and b.get("_verbraucht", 0) < b["max_gesamt"]:
            jpeg = aufbereiten(daten, b, unterschrift) if daten else None
        if jpeg is None:
            html_text = muster.sub("", html_text)
            continue
        html_text = html_text.replace(f'src="BILD:{nr}"', f'src="BILDNR:{len(fertig)}"', 1)
        fertig.append(jpeg)
        b["_verbraucht"] = b.get("_verbraucht", 0) + 1
    return html_text, fertig
