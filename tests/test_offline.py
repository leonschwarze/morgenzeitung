"""Offline-Test: baut eine komplette Ausgabe aus erfundenen Feeds und Artikeln.
Kein Netz, kein Tolino. Aufruf:  python -m pytest -q"""
import sys
import zipfile
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import morgenzeitung as mz  # noqa: E402

JETZT = format_datetime(datetime.now(timezone.utc))
ALT = "Mon, 01 Jan 2024 06:00:00 GMT"


def feed(eintraege):
    items = "".join(f"<item><title>{t}</title><link>{u}</link><pubDate>{d}</pubDate>"
                    f"<description>Anriss zu {t}</description></item>" for t, u, d in eintraege)
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>{items}</channel></rss>'.encode()


def artikelseite(titel, woerter):
    absatz = " ".join(["Bonn"] + ["Wort"] * (woerter - 1))
    return (f"<html><head><title>{titel}</title><meta name='author' content='Erika Muster'></head>"
            f"<body><nav>Menü Startseite Abo</nav><article><h1>{titel}</h1>"
            f"<p>{absatz}.</p></article><footer>Impressum</footer></body></html>").encode()


class FakeNetz:
    def __init__(self):
        self.seiten = {
            "https://ga.example/feed": feed([("GA eins", "https://ga.example/1", JETZT),
                                              ("GA Teaser", "https://ga.example/2", JETZT),
                                              ("GA alt", "https://ga.example/3", ALT)]),
            "https://ts.example/feed": feed([("TS Liveblog", "https://ts.example/liveblog/1", JETZT),
                                              ("TS eins", "https://ts.example/1", JETZT),
                                              ("GA eins doppelt", "https://ga.example/1", JETZT)]),
            "https://heise.example/feed": feed([("heise+ | Bezahl", "https://heise.example/p", JETZT),
                                                 ("Neue CPU", "https://heise.example/1", JETZT)]),
            "https://ga.example/1": artikelseite("GA eins", 600),
            "https://ga.example/2": artikelseite("GA Teaser", 40),
            "https://ts.example/1": artikelseite("TS eins", 500),
        }
    def holen(self, url):
        return self.seiten[url]


def config(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(f"""
[zeitung]
titel = "Testzeitung"
lesezeit_minuten = 5
ausgabe_ordner = "{tmp_path / 'out'}"
[[quelle]]
name = "GA"
feed = "https://ga.example/feed"
artikel = 3
min_woerter = 100
[[quelle]]
name = "Tagesschau"
feed = "https://ts.example/feed"
ausschliessen = ["liveblog"]
[[quelle]]
name = "heise"
feed = "https://heise.example/feed"
modus = "schlagzeilen"
ausschliessen = ["heise+"]
""")
    return mz.lade_config(p)


def test_komplette_ausgabe(tmp_path):
    cfg, netz = config(tmp_path), FakeNetz()
    kand = mz.kandidaten_sammeln(cfg, netz)
    assert [a.titel for a in kand["GA"]] == ["GA eins", "GA Teaser"]       # alt fliegt raus
    assert [a.titel for a in kand["Tagesschau"]] == ["TS eins"]            # Liveblog + Dublette raus
    assert [a.titel for a in kand["heise"]] == ["Neue CPU"]                # heise+ raus

    auswahl = mz.auswaehlen(cfg, kand, netz)
    titel = [a.titel for a in auswahl]
    assert "GA Teaser" not in titel                                        # Paywall-Teaser erkannt
    assert titel == ["GA eins", "TS eins", "Neue CPU"]

    pfad = mz.epub_bauen(cfg, auswahl, tmp_path / "out", datetime(2026, 11, 24, 6, 0))
    with zipfile.ZipFile(pfad) as z:
        namen = z.namelist()
        alles = " ".join(z.read(n).decode("utf-8", "ignore") for n in namen if n.endswith(".xhtml"))
    assert any(n.endswith("cover.png") for n in namen)
    assert "Dienstag, 24. November 2026" in alles
    assert "Menü Startseite" not in alles                                  # Navigation entfernt
    assert "Neue CPU" in alles and "Erika Muster" in alles


def test_budget_wird_eingehalten(tmp_path):
    cfg, netz = config(tmp_path), FakeNetz()
    cfg["zeitung"]["lesezeit_minuten"] = 2        # 460 Wörter: nur der erste Artikel passt
    auswahl = mz.auswaehlen(cfg, mz.kandidaten_sammeln(cfg, netz), netz)
    assert [a.titel for a in auswahl] == ["GA eins"]


def test_downloadseite(tmp_path):
    cfg = config(tmp_path)
    ordner = tmp_path / "out"
    ordner.mkdir()
    for tag in ("2026-11-23", "2026-11-24"):
        (ordner / f"Morgenzeitung-{tag}.epub").write_bytes(b"PK")
    mz.downloadseite(cfg, ordner)
    seite = (ordner / "index.html").read_text()
    assert seite.index("Morgenzeitung-2026-11-24.epub") < seite.index("Morgenzeitung-2026-11-23.epub")
    assert "Heute laden" in seite and "<script" not in seite



def test_gesperrte_quelle_wird_aufgegeben(tmp_path):
    """Antwortet eine Quelle mit 402, wird sie nach zwei Versuchen übersprungen."""
    import requests
    cfg, netz = config(tmp_path), FakeNetz()
    netz.seiten["https://ga.example/feed"] = feed(
        [(f"GA {i}", f"https://ga.example/{i}", JETZT) for i in range(10)])
    kand = mz.kandidaten_sammeln(cfg, netz)
    aufrufe = []

    def holen(url):
        aufrufe.append(url)
        if url.startswith("https://ga.example/"):
            antwort = requests.Response()
            antwort.status_code = 402
            raise requests.HTTPError("402 Payment Required", response=antwort)
        return netz.seiten[url]
    netz.holen = holen
    titel = [a.titel for a in mz.auswaehlen(cfg, kand, netz)]
    assert sum(u.startswith("https://ga.example/") for u in aufrufe) == 2
    assert titel == ["TS eins", "Neue CPU"]


def test_nur_mit_filter(tmp_path):
    cfg, netz = config(tmp_path), FakeNetz()
    cfg["quelle"][0]["nur_mit"] = ["eins"]
    kand = mz.kandidaten_sammeln(cfg, netz)
    assert [a.titel for a in kand["GA"]] == ["GA eins"]


def test_config_suchreihenfolge(tmp_path, monkeypatch):
    """--config schlägt Umgebungsvariable, die schlägt ~/.config."""
    a, b, c = tmp_path / "a.toml", tmp_path / "b.toml", tmp_path / "home.toml"
    for p in (a, b, c):
        p.write_text("")
    monkeypatch.setattr(mz, "KONFIG_ORDNER", tmp_path / "leer")
    monkeypatch.delenv("MORGENZEITUNG_CONFIG", raising=False)
    monkeypatch.setattr(mz, "HIER", tmp_path / "programm")
    try:
        mz.config_finden()
        assert False, "ohne Konfiguration muss es eine klare Fehlermeldung geben"
    except SystemExit as e:
        assert "config.example.toml" in str(e)
    (tmp_path / "leer").mkdir()
    c.rename(tmp_path / "leer" / "config.toml")
    assert mz.config_finden() == tmp_path / "leer" / "config.toml"
    monkeypatch.setenv("MORGENZEITUNG_CONFIG", str(b))
    assert mz.config_finden() == b
    assert mz.config_finden(str(a)) == a


def test_sz_ersetzt_tagesschau(tmp_path, monkeypatch):
    """Liegt eine SZ-Ausgabe vor, fällt die Ersatzquelle weg – sonst springt sie ein."""
    import test_ausgabe
    aufrufe = []
    echte_lade_config = mz.lade_config

    class ZaehlNetz(FakeNetz):
        def holen(self, url):
            aufrufe.append(url)
            return super().holen(url)

    def lauf(mit_sz):
        aufrufe.clear()
        monkeypatch.setattr(mz, "lade_config", echte_lade_config)
        cfg = config(tmp_path)
        cfg["quelle"][1]["ersatz_fuer"] = "sz"              # Tagesschau
        cfg["sz"] = {"aktiv": True, "ordner": str(tmp_path / ("sz" if mit_sz else "leer"))}
        monkeypatch.setattr(mz, "lade_config", lambda *a: cfg)
        monkeypatch.setattr(mz, "Netz", ZaehlNetz)
        assert mz.main([]) == 0
        return any("ts.example" in u for u in aufrufe)

    test_ausgabe.mini_ausgabe(tmp_path / "sz", "SZ.epub", {
        "Seite Drei": [("Reportage", 2000)], "Politik": [("Haushalt", 600)]})
    assert lauf(mit_sz=True) is False
    assert lauf(mit_sz=False) is True
