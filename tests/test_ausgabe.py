"""Tests für das Zerlegen fertiger Ausgaben (ZEIT, SZ) mit selbst gebauten Mini-Ausgaben."""
import json
import sys
from pathlib import Path

from ebooklib import epub

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ausgabe as zeit  # noqa: E402  (kurzer Name, damit die Tests lesbar bleiben)


def text(n):
    return "<p>" + " ".join(["Wort"] * n) + "</p>"


ZEIT_ARTIKEL = {
    "Politik": [("Die Koalition wackelt", 1500), ("Kurz notiert", 200), ("Europas Zukunft", 1800)],
    "Wirtschaft": [("Die Bahn im Stresstest", 1600)],
    "Wissen": [("Wie Wale schlafen", 1200), ("Riesentext", 6000)],
    "Hamburg": [("Elbphilharmonie wird teurer", 1400)],
    "Leserbriefe": [("Leserbriefe zu Nr. 47", 1300)],
}


def mini_ausgabe(pfad: Path, name="DIE_ZEIT_48.epub", artikel=None):
    """Eine erfundene Ausgabe: Ressorts als Abschnitte, darunter Artikel."""
    b = epub.EpubBook()
    b.set_identifier(name)
    b.set_title("DIE ZEIT")
    b.set_language("de")
    artikel = artikel or ZEIT_ARTIKEL
    toc, spine = [], []
    for ri, (ressort, liste) in enumerate(artikel.items()):
        kapitel = []
        for ai, (titel, n) in enumerate(liste):
            k = epub.EpubHtml(title=titel, file_name=f"r{ri}a{ai}.xhtml", lang="de")
            k.content = (f'<html><body><div class="artikel"><h1 class="titel">{titel}</h1>'
                         f'<p class="vorspann"><em>Vorspann</em> zum Text</p>{text(n)}'
                         f'<figure><img src="x.jpg"/><figcaption>Bild</figcaption></figure>'
                         f'<h2>Zwischentitel</h2></div></body></html>')
            b.add_item(k)
            kapitel.append(k)
            spine.append(k)
        toc.append((epub.Section(ressort), kapitel))
    b.toc = toc
    b.add_item(epub.EpubNcx())
    b.add_item(epub.EpubNav())
    b.spine = spine
    pfad.mkdir(parents=True, exist_ok=True)
    epub.write_epub(str(pfad / name), b)
    return pfad / name


def cfg(tmp_path):
    return zeit.standard({"zeit": {"aktiv": True, "ordner": str(tmp_path / "zeit")}}, "zeit")


def test_zerlegen(tmp_path):
    datei = mini_ausgabe(tmp_path / "zeit")
    arts = {a["titel"]: a for a in zeit.artikel_lesen(datei)}
    a = arts["Die Koalition wackelt"]
    assert a["ressort"] == "Politik"
    assert 1500 <= a["woerter"] <= 1510
    assert "<em>Vorspann</em>" in a["html"]            # Hervorhebung bleibt
    assert "<h2>Zwischentitel</h2>" in a["html"]
    assert "<h1" not in a["html"] and 'class="titel"' not in a["html"]
    # Bild wird Platzhalter mit Unterschrift; die Datei fehlt im Mini-EPUB → keine Daten
    assert '<figure class="bild"><img src="BILD:0" alt=""/><figcaption>Bild</figcaption></figure>' in a["html"]
    assert a["bilder_roh"] == [(None, "Bild")]


def test_rotation_ueber_die_woche(tmp_path):
    z = cfg(tmp_path)
    mini_ausgabe(tmp_path / "zeit")
    status = tmp_path / "zeit" / "status.json"

    tag1 = zeit.auswahl_fuer_heute(z, status, "2026-11-25")
    # ein Schwerpunkt (Politik zuerst) + eine Entdeckung aus einem anderen Ressort
    assert [a["titel"] for a in tag1] == ["Die Koalition wackelt", "Wie Wale schlafen"]
    # zweiter Lauf am selben Tag → dieselben Artikel
    assert [a["titel"] for a in zeit.auswahl_fuer_heute(z, status, "2026-11-25")] == \
        [a["titel"] for a in tag1]
    tag2 = zeit.auswahl_fuer_heute(z, status, "2026-11-26")
    assert [a["titel"] for a in tag2] == ["Europas Zukunft", "Die Bahn im Stresstest"]
    # alles verbraucht: kurze Texte, Riesentext, Hamburg und Leserbriefe bleiben draußen
    assert zeit.auswahl_fuer_heute(z, status, "2026-11-27") == []


def test_neue_ausgabe_beginnt_von_vorn(tmp_path):
    import os
    import time
    z = cfg(tmp_path)
    status = tmp_path / "zeit" / "status.json"
    mini_ausgabe(tmp_path / "zeit", "DIE_ZEIT_48.epub")
    zeit.auswahl_fuer_heute(z, status, "2026-11-25")
    neu = mini_ausgabe(tmp_path / "zeit", "DIE_ZEIT_49.epub")
    os.utime(neu, (time.time() + 60, time.time() + 60))
    tag = zeit.auswahl_fuer_heute(z, status, "2026-12-02")
    assert [a["titel"] for a in tag][0] == "Die Koalition wackelt"
    assert json.loads(status.read_text())["ausgabe"] == "DIE_ZEIT_49.epub"


def test_drm_wird_erkannt(tmp_path):
    import zipfile
    import pytest
    datei = mini_ausgabe(tmp_path / "zeit")
    with zipfile.ZipFile(datei, "a") as zf:
        zf.writestr("META-INF/encryption.xml", "<encryption><EncryptedData/></encryption>")
    with pytest.raises(ValueError, match="DRM"):
        zeit.artikel_lesen(datei)


def test_epub2_inhaltsverzeichnis(tmp_path):
    """Ältere EPUBs haben nur eine toc.ncx statt nav.xhtml."""
    import zipfile
    alt = mini_ausgabe(tmp_path / "zeit")
    neu = tmp_path / "epub2.epub"
    with zipfile.ZipFile(alt) as a, zipfile.ZipFile(neu, "w") as b:
        for info in a.infolist():
            daten = a.read(info)
            if info.filename.endswith(".opf"):
                daten = daten.replace(b'properties="nav"', b"")
            b.writestr(info, daten)
    ressorts = {a["titel"]: a["ressort"] for a in zeit.artikel_lesen(neu)}
    assert ressorts["Die Koalition wackelt"] == "Politik"
    assert ressorts["Wie Wale schlafen"] == "Wissen"


def test_mails_abholen(tmp_path, monkeypatch):
    """Ein gefälschtes Postfach mit einer ZEIT-Mail samt EPUB-Anhang."""
    from email.message import EmailMessage
    mail = EmailMessage()
    mail["From"] = "abo@zeit.de"
    mail.set_content("Ihre neue Ausgabe")
    mail.add_attachment(b"PK-epub", maintype="application", subtype="epub+zip",
                        filename="DIE_ZEIT_48.epub")

    class FakeIMAP:
        def __init__(self, server, port):
            self.gelesen_nur = None
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def login(self, user, pw): assert pw == "geheim"
        def select(self, box, readonly): self.gelesen_nur = readonly; assert readonly
        def search(self, _, kriterium):
            assert 'FROM "zeit.de"' in kriterium
            return "OK", [b"1"]
        def fetch(self, nr, _): return "OK", [(b"1", mail.as_bytes())]

    monkeypatch.setattr(zeit.imaplib, "IMAP4_SSL", FakeIMAP)
    (tmp_path / "pw").write_text("geheim\n")
    z = zeit.standard({"zeit": {"ordner": str(tmp_path / "zeit"), "imap": {
        "server": "imap.test", "benutzer": "leon", "passwort_datei": str(tmp_path / "pw")}}}, "zeit")
    assert zeit.mails_abholen(z) == 1
    assert (tmp_path / "zeit" / "DIE_ZEIT_48.epub").read_bytes() == b"PK-epub"
    assert zeit.mails_abholen(z) == 0                    # schon da → nicht doppelt


def test_sz_seite_drei_immer_und_kein_lokalteil(tmp_path):
    """SZ: Seite Drei kommt immer – auch wenn sie länger als max_woerter ist –,
    München und Bayern nie, Politik und Wirtschaft bevorzugt."""
    sz = {
        "Seite Drei": [("Eine Reise ans Ende der Welt", 3200)],
        "Politik": [("Bundestag beschließt Haushalt", 700), ("Wahl in Frankreich", 650)],
        "Wirtschaft": [("Zinsen sinken", 600)],
        "München": [("Neue Tram für Schwabing", 500)],
        "Bayern": [("Landtag streitet", 550)],
        "Feuilleton": [("Ein neuer Roman", 900)],
        "Meinung": [("Kommentar zum Haushalt", 400)],
    }
    mini_ausgabe(tmp_path / "sz", "SZ_2026-11-25.epub", sz)
    z = zeit.standard({"sz": {"aktiv": True, "ordner": str(tmp_path / "sz")}}, "sz")
    titel = [a["titel"] for a in zeit.auswahl_fuer_heute(z, tmp_path / "sz" / "status.json", "2026-11-25")]
    assert titel[0] == "Eine Reise ans Ende der Welt"
    assert len(titel) == 5
    assert {"Bundestag beschließt Haushalt", "Zinsen sinken"} <= set(titel)
    assert not {"Neue Tram für Schwabing", "Landtag streitet"} & set(titel)
