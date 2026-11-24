"""Tests für Bilder, Bildunterschriften und Zitatblöcke."""
import io
import random
import sys
import zipfile
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import bilder  # noqa: E402
import morgenzeitung as mz  # noqa: E402
import test_offline as to  # noqa: E402


def foto(breite=1600, hoehe=1000) -> bytes:
    """Fotoähnlich: Verläufe mit Rauschen – viele verschiedene Grauwerte."""
    random.seed(breite)
    img = Image.effect_noise((breite, hoehe), 60).convert("RGB")
    verlauf = Image.linear_gradient("L").resize((breite, hoehe)).convert("RGB")
    img = Image.blend(img, verlauf, 0.5).filter(ImageFilter.GaussianBlur(1))
    puffer = io.BytesIO()
    img.save(puffer, "JPEG")
    return puffer.getvalue()


def grafik() -> bytes:
    """Balkendiagramm: wenige große einfarbige Flächen."""
    img = Image.new("RGB", (1200, 800), "white")
    d = ImageDraw.Draw(img)
    for i, h in enumerate([300, 500, 420, 610]):
        d.rectangle([150 + i * 230, 700 - h, 300 + i * 230, 700], fill=(200, 30, 30))
    puffer = io.BytesIO()
    img.save(puffer, "PNG")
    return puffer.getvalue()


def test_foto_wird_verkleinert_und_grau():
    b = bilder.standard({})
    jpeg = bilder.aufbereiten(foto(), b, "Der Kanzler. Foto: dpa")
    bild = Image.open(io.BytesIO(jpeg))
    assert bild.mode == "L" and bild.size[0] == 1000
    assert len(jpeg) < 150_000


def test_grafik_und_icon_fliegen_raus():
    b = bilder.standard({})
    assert bilder.aufbereiten(grafik(), b, "") is None                       # am Bild erkannt
    assert bilder.aufbereiten(foto(), b, "Grafik: SZ; Quelle: Destatis") is None   # am Text erkannt
    assert bilder.aufbereiten(foto(120, 120), b, "") is None                 # zu klein
    b["infografiken"] = True
    assert bilder.aufbereiten(grafik(), b, "") is not None


ARTIKEL_MIT_BILDERN = """<html><head><title>Haushalt</title>
<meta property="og:image" content="https://bild.example/aufmacher.jpg"></head><body>
<nav>Menü</nav><article><h1>Haushalt</h1>
<p>{text}</p>
<figure><img src="/medien/plenum.jpg?w=800" alt="Plenum"><figcaption>Abgeordnete im Plenum.
Foto: Kay Nietfeld/dpa</figcaption></figure>
<p>{text}</p>
<blockquote><p>„Wir werden sparen müssen“, sagte der Minister.</p></blockquote>
<figure><img src="/medien/kurve.png"><figcaption>Grafik: Entwicklung der Schulden. Quelle: BMF</figcaption></figure>
<p>{text}</p></article><footer>Impressum</footer></body></html>"""


def test_webartikel_mit_bildern_unterschrift_und_zitat(tmp_path):
    cfg, netz = to.config(tmp_path), to.FakeNetz()
    text = " ".join(["Bonn"] + ["Wort"] * 200) + "."
    netz.seiten["https://ga.example/1"] = ARTIKEL_MIT_BILDERN.format(text=text).encode()
    netz.seiten["https://bild.example/aufmacher.jpg"] = foto(1400, 900)
    netz.seiten["https://ga.example/medien/plenum.jpg?w=800"] = foto()
    netz.seiten["https://ga.example/medien/kurve.png"] = grafik()

    auswahl = mz.auswaehlen(cfg, mz.kandidaten_sammeln(cfg, netz), netz)
    art = auswahl[0]
    assert len(art.bilder) == 2                                   # Aufmacher + Plenum, keine Grafik
    assert art.html.index("BILDNR:0") < art.html.index("BILDNR:1") < art.html.index("<blockquote")
    assert "Foto: Kay Nietfeld/dpa" in art.html and "Quelle: BMF" not in art.html
    assert 590 <= art.woerter <= 640                              # Unterschriften zählen nicht mit

    pfad = mz.epub_bauen(cfg, auswahl, tmp_path / "out", datetime(2026, 11, 25, 6))
    with zipfile.ZipFile(pfad) as z:
        namen = z.namelist()
        seite = z.read("EPUB/b1q0a0.xhtml").decode()
    assert sum(n.startswith("EPUB/bilder/b") and n.endswith(".jpg") for n in namen) == 2
    assert 'src="bilder/b1q0a0_0.jpg"' in seite and "<figcaption>" in seite and "<blockquote>" in seite


def test_bilder_aus_epub_ausgabe(tmp_path):
    """Bilder und Zitate aus der SZ-/ZEIT-Ausgabe werden übernommen."""
    import ausgabe
    from ebooklib import epub
    b = epub.EpubBook()
    b.set_identifier("sz")
    b.set_title("SZ")
    b.set_language("de")
    b.add_item(epub.EpubImage(uid="f1", file_name="img/reportage.jpg", media_type="image/jpeg",
                              content=foto()))
    k = epub.EpubHtml(title="Reportage", file_name="text/a1.xhtml", lang="de")
    k.content = ("<html><body><h1>Reportage</h1><p>Erster Absatz.</p>"
                 '<div class="figure"><p><img src="../img/reportage.jpg"/></p>'
                 '<p class="caption">Am Hafen von Marseille. Foto: Max Muster</p></div>'
                 "<blockquote><p>Ein Zitat</p><p>über zwei Absätze</p></blockquote>"
                 "<p>Letzter Absatz.</p></body></html>")
    b.add_item(k)
    b.toc = [(epub.Section("Seite Drei"), [k])]
    b.add_item(epub.EpubNcx())
    b.add_item(epub.EpubNav())
    b.spine = [k]
    epub.write_epub(str(tmp_path / "sz.epub"), b)

    a = ausgabe.artikel_lesen(tmp_path / "sz.epub")[0]
    assert a["bilder_roh"][0][0] is not None
    assert a["bilder_roh"][0][1] == "Am Hafen von Marseille. Foto: Max Muster"
    assert "<blockquote><p>Ein Zitat</p><p>über zwei Absätze</p></blockquote>" in a["html"]
    assert a["html"].index("Erster") < a["html"].index("BILD:0") < a["html"].index("Ein Zitat")
    assert a["html"].count("Am Hafen von Marseille") == 1           # nur in der figcaption
    html, jpegs = bilder.einsetzen(a["html"], a["bilder_roh"], bilder.standard({}))
    assert len(jpegs) == 1 and "BILDNR:0" in html
