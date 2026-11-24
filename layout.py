"""Gestaltung der Morgenzeitung: Titelbild, Inhaltsseite, Artikelseiten.

Gestaltungsregeln für E-Ink, die hier umgesetzt sind:
  * Nur Schwarz auf Weiß. Hellgrau verschwimmt auf E-Ink, deshalb gliedern
    Linien, Versalien und Schriftgrößen statt Farben.
  * Keine festen Schriften: Der Tolino ersetzt sie ohnehin, wenn du in den
    Leseeinstellungen eine andere Schrift wählst. Wir geben nur "serif" vor.
  * Trennung und Blocksatz: lang="de" bzw. "fr" pro Artikel sorgt dafür, dass
    der Reader richtig trennt – wichtig auf dem schmalen Bildschirm.
  * Größen in em statt px: skaliert mit der Schriftgröße, die du am Gerät einstellst.
"""
import html
from datetime import datetime
from io import BytesIO
from pathlib import Path

WOCHENTAGE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
MONATE = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
          "September", "Oktober", "November", "Dezember"]


def datum_lang(d: datetime) -> str:
    return f"{WOCHENTAGE[d.weekday()]}, {d.day}. {MONATE[d.month - 1]} {d.year}"


def esc(t: str) -> str:
    return html.escape(t or "")


CSS = """
body { font-family: serif; line-height: 1.42; margin: 0 .3em; }
p { margin: 0; text-indent: 1.2em; text-align: justify;
    -webkit-hyphens: auto; hyphens: auto; adobe-hyphenate: auto; }
.text > p:first-child, h2 + p, h3 + p, p.ohne { text-indent: 0; }
.text h2, .text h3 { font-size: 1em; font-weight: bold; margin: 1.1em 0 .3em; text-indent: 0; }

/* --- Artikelkopf ----------------------------------------------------------- */
.kicker { font-family: sans-serif; font-size: .72em; font-weight: bold; letter-spacing: .12em;
          text-transform: uppercase; text-indent: 0; margin: .2em 0 .5em;
          border-bottom: 2px solid #000; padding-bottom: .25em; }
h1 { font-size: 1.55em; line-height: 1.15; margin: 0 0 .45em; font-weight: bold;
     text-align: left; -webkit-hyphens: manual; hyphens: manual; }
.vorspann { font-weight: bold; text-indent: 0; text-align: left; margin-bottom: .6em; }
.meta { font-family: sans-serif; font-size: .72em; text-indent: 0; margin-bottom: 1.1em; }
.text > p:first-child::first-line { font-weight: bold; }
.ende { text-indent: 0; }
.quelle-link { font-family: sans-serif; font-size: .62em; text-indent: 0; text-align: left;
               margin-top: 1.4em; word-break: break-all; }

/* --- Bilder und Zitate ------------------------------------------------------ */
figure.bild { margin: 1em 0; padding: 0; text-align: center; page-break-inside: avoid; }
figure.bild img { max-width: 100%; height: auto; }
figcaption { font-family: sans-serif; font-size: .7em; line-height: 1.3; text-align: left;
             margin-top: .35em; padding-bottom: .3em; border-bottom: 1px solid #000; }
figure.bild + p { text-indent: 0; }
blockquote { margin: 1em 0 1em .2em; padding: .1em 0 .1em .8em; border-left: 3px solid #000;
             font-style: italic; font-size: 1.05em; page-break-inside: avoid; }
blockquote p { text-indent: 0; text-align: left; margin-bottom: .3em; }
blockquote + p { text-indent: 0; }
.text ul, .text ol { margin: .6em 0 .6em 1.2em; padding: 0; }
.text li { margin-bottom: .3em; }

/* --- Titel-/Inhaltsseite ------------------------------------------------- */
.kopf { text-align: center; margin-top: .4em; }
.marke { font-size: 2.1em; font-weight: bold; letter-spacing: .06em; text-indent: 0;
         text-align: center; margin: 0; line-height: 1.1; }
.datumszeile { font-family: sans-serif; font-size: .7em; text-indent: 0; text-align: center;
               border-top: 3px double #000; border-bottom: 1px solid #000;
               padding: .3em 0; margin: .4em 0 1em; letter-spacing: .04em; }
.aufmacher-kicker { font-family: sans-serif; font-size: .7em; font-weight: bold;
                    letter-spacing: .12em; text-transform: uppercase; text-indent: 0; }
.aufmacher { font-size: 1.35em; line-height: 1.18; font-weight: bold; text-indent: 0;
             text-align: left; margin: .15em 0 .35em; }
.aufmacher a, .liste a { color: #000; text-decoration: none; }
.aufmacher-text { text-indent: 0; text-align: left; margin-bottom: 1em; }
.rubrik { text-indent: 0; font-family: sans-serif; font-size: .72em; font-weight: bold; letter-spacing: .12em;
          text-transform: uppercase; border-bottom: 1px solid #000; margin: 1.1em 0 .4em;
          padding-bottom: .15em; }
.liste p { text-indent: 0; text-align: left; margin: 0 0 .45em; }
.min { font-family: sans-serif; font-size: .7em; white-space: nowrap; }

/* --- Kurz notiert ---------------------------------------------------------- */
.meldung { border-top: 1px solid #000; padding-top: .45em; margin-top: .45em; }
.kicker + .meldung { border-top: none; padding-top: 0; }
.meldung b { display: block; margin-bottom: .1em; }
.meldung p { text-indent: 0; text-align: left; }

/* --- Schluss ---------------------------------------------------------------- */
.schluss { text-align: center; margin-top: 30%; }
.schluss p { text-indent: 0; text-align: center; margin-bottom: .6em; }
"""


# --------------------------------------------------------------------------- Titelbild
def _schrift(groesse, fett=False, serif=True):
    from PIL import ImageFont
    art = "Serif" if serif else "Sans"
    name = f"DejaVu{art}-Bold.ttf" if fett else f"DejaVu{art}.ttf"
    for ordner in ("/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/TTF"):
        try:
            return ImageFont.truetype(f"{ordner}/{name}", groesse)
        except OSError:
            pass
    return ImageFont.load_default(groesse)


def _umbrechen(d, text, font, breite, max_zeilen):
    """Zeilenumbruch von Hand: Pillow kann das nicht selbst."""
    zeilen, zeile = [], ""
    for wort in text.split():
        probe = f"{zeile} {wort}".strip()
        if d.textlength(probe, font=font) <= breite:
            zeile = probe
        else:
            zeilen.append(zeile)
            zeile = wort
    zeilen.append(zeile)
    if len(zeilen) > max_zeilen:
        zeilen = zeilen[:max_zeilen]
        zeilen[-1] = zeilen[-1].rstrip(" ,.;:") + " …"
    return zeilen


def titelbild(titel: str, heute: datetime, nummer: int, minuten: int,
              schlagzeilen: list[tuple[str, str]], quellen: list[str]) -> bytes:
    """Titelseite wie bei einer Zeitung – mit den Schlagzeilen des Tages.
    So erkennst du schon in der Bibliotheksansicht, was drinsteht."""
    from PIL import Image, ImageDraw
    W, H, R = 1072, 1448, 70                    # Tolino-Auflösung, Rand
    img = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(img)
    breite = W - 2 * R

    # Zeitungskopf
    f = _schrift(118, True)
    w = d.textlength(titel, font=f)
    if w > breite:
        f = _schrift(int(118 * breite / w), True)
        w = d.textlength(titel, font=f)
    d.text(((W - w) / 2, 70), titel, font=f, fill=0)
    y = 225
    d.line([R, y, W - R, y], fill=0, width=6)
    d.line([R, y + 12, W - R, y + 12], fill=0, width=2)
    links, rechts = datum_lang(heute), f"Nr. {nummer} · {minuten} Min."
    fz = _schrift(30, serif=False)
    d.text((R, y + 26), links, font=fz, fill=0)
    d.text((W - R - d.textlength(rechts, font=fz), y + 26), rechts, font=fz, fill=0)
    y += 78
    d.line([R, y, W - R, y], fill=0, width=2)
    y += 40

    # Aufmacher groß, danach kleinere Schlagzeilen mit Rubrik
    for i, (rubrik, zeile) in enumerate(schlagzeilen[:5]):
        groesse, zeilen_max = (66, 4) if i == 0 else (44, 2)
        fk, fs = _schrift(24 if i else 28, True, serif=False), _schrift(groesse, True)
        if y + 40 + groesse * 1.2 * zeilen_max > H - 180:
            break
        d.text((R, y), rubrik.upper(), font=fk, fill=0)
        y += 40
        for z in _umbrechen(d, zeile, fs, breite, zeilen_max):
            d.text((R, y), z, font=fs, fill=0)
            y += int(groesse * 1.18)
        y += 26
        d.line([R, y, W - R if i == 0 else R + 220, y], fill=0, width=3 if i == 0 else 1)
        y += 34

    # Fußzeile mit den Quellen
    d.line([R, H - 130, W - R, H - 130], fill=0, width=2)
    fq = _schrift(26, serif=False)
    zeile = " · ".join(quellen)
    while d.textlength(zeile, font=fq) > breite and fq.size > 16:
        fq = _schrift(fq.size - 2, serif=False)
    d.text(((W - d.textlength(zeile, font=fq)) / 2, H - 105), zeile, font=fq, fill=0)

    puffer = BytesIO()
    img.save(puffer, "PNG", optimize=True)
    return puffer.getvalue()


# --------------------------------------------------------------------------- EPUB
def _vorspann_noetig(anriss: str, text_html: str) -> bool:
    """Viele Feeds wiederholen im Anriss den ersten Absatz. Dann nicht doppelt zeigen."""
    import re
    if len(anriss) < 40:
        return False
    anfang = re.sub(r"\W+", " ", anriss[:60]).strip().lower()
    return anfang not in re.sub(r"\W+", " ", re.sub(r"<[^>]+>", " ", text_html)).lower()


def epub_bauen(cfg: dict, auswahl: list, ziel: Path, heute: datetime) -> Path:
    from ebooklib import epub
    z = cfg["zeitung"]
    wpm = z["woerter_pro_minute"]
    modus = {q["name"]: q.get("modus", "volltext") for q in cfg.get("quelle", [])}
    quellen = list(dict.fromkeys(a.quelle for a in auswahl))
    minuten_gesamt = max(1, round(sum(a.woerter for a in auswahl) / wpm))
    nummer = heute.timetuple().tm_yday

    buch = epub.EpubBook()
    buch.set_identifier(f"morgenzeitung-{heute:%Y%m%d}")
    buch.set_title(f"{z['titel']} – {heute:%d.%m.%Y}")
    buch.set_language("de")
    buch.add_author(z["titel"])
    stil = epub.EpubItem(uid="stil", file_name="stil.css", media_type="text/css", content=CSS)
    buch.add_item(stil)

    def seite(datei, titel, inhalt, sprache="de"):
        k = epub.EpubHtml(title=titel, file_name=datei, lang=sprache)
        k.content = f'<html lang="{sprache}"><body>{inhalt}</body></html>'
        k.add_item(stil)
        buch.add_item(k)
        return k

    kapitel, toc, liste, schlagzeilen = [], [], [], []
    aufmacher = None
    for qi, q in enumerate(quellen):
        arts = [a for a in auswahl if a.quelle == q]
        if modus.get(q) == "schlagzeilen":
            datei = f"q{qi}.xhtml"
            teile = [f'<p class="kicker">Kurz notiert · {esc(q)}</p>']
            for a in arts:
                teile.append(f'<div class="meldung"><b>{esc(a.titel)}</b>'
                             f'<p>{esc(a.anriss)}</p></div>')
            k = seite(datei, f"Kurz notiert: {q}", "\n".join(teile), arts[0].sprache)
            kapitel.append(k)
            toc.append(k)
            liste.append(f'<p class="rubrik">Kurz notiert · {esc(q)}</p><div class="liste">'
                         f'<p><a href="{datei}">{len(arts)} Meldungen</a></p></div>')
            continue

        eintraege = []
        unter = []
        for ai, a in enumerate(arts):
            datei = f"q{qi}a{ai}.xhtml"
            minuten = max(1, round(a.woerter / wpm))
            kicker = " · ".join(x for x in [q, a.ressort,
                                            a.zeit.strftime("%H:%M Uhr") if a.zeit else ""] if x)
            meta = " · ".join(x for x in [f"Von {a.autor}" if a.autor else "",
                                          f"{minuten} Min. Lesezeit"] if x)
            vorspann = (f'<p class="vorspann">{esc(a.anriss)}</p>'
                        if _vorspann_noetig(a.anriss, a.html) else "")
            # Schlusszeichen ■ ans Ende des letzten Absatzes – wie in der gedruckten Zeitung
            text = a.html.rstrip()
            if text.endswith("</p>"):
                text, schluss = text[:-4] + "&#160;■</p>", ""
            else:
                schluss = '<p class="ende">■</p>'
            # Bilder als eigene Dateien ins EPUB, Platzhalter BILDNR:n → echter Pfad
            for n, jpeg in enumerate(getattr(a, "bilder", [])):
                bildname = f"bilder/q{qi}a{ai}_{n}.jpg"
                buch.add_item(epub.EpubImage(uid=f"bild_q{qi}a{ai}_{n}", file_name=bildname,
                                             media_type="image/jpeg", content=jpeg))
                text = text.replace(f'src="BILDNR:{n}"', f'src="{bildname}"')
            koerper = f'<div class="text">{text}</div>'
            link = f'<p class="quelle-link">{esc(a.url)}</p>' if a.url else ""
            k = seite(datei, a.titel,
                      f'<p class="kicker">{esc(kicker)}</p><h1>{esc(a.titel)}</h1>'
                      f'{vorspann}<p class="meta">{esc(meta)}</p>{koerper}{schluss}{link}',
                      a.sprache)
            kapitel.append(k)
            unter.append(k)
            if aufmacher is None:
                aufmacher = (q, a, datei, minuten)
                continue
            schlagzeilen.append((q if not a.ressort else f"{q} · {a.ressort}", a.titel))
            eintraege.append(f'<p><a href="{datei}">{esc(a.titel)}</a> '
                             f'<span class="min">{minuten} Min.</span></p>')
        if eintraege:
            liste.append(f'<p class="rubrik">{esc(q)}</p><div class="liste">{"".join(eintraege)}</div>')
        toc.append((epub.Section(q), unter))

    # Inhaltsseite = erste Seite: Zeitungskopf, Aufmacher, dann alles nach Rubriken
    kopf = (f'<div class="kopf"><p class="marke">{esc(z["titel"])}</p></div>'
            f'<p class="datumszeile">{datum_lang(heute)} · Nr. {nummer} · '
            f'{minuten_gesamt} Min. Lesezeit</p>')
    if aufmacher:
        q, a, datei, minuten = aufmacher
        schlagzeilen.insert(0, (q, a.titel))
        anriss = a.anriss or ""
        kopf += (f'<p class="aufmacher-kicker">{esc(q)} · {minuten} Min.</p>'
                 f'<p class="aufmacher"><a href="{datei}">{esc(a.titel)}</a></p>'
                 + (f'<p class="aufmacher-text">{esc(anriss[:260])}</p>' if anriss else ""))
    vorne = seite("inhalt.xhtml", "Inhalt", kopf + "".join(liste))

    ende = seite("ende.xhtml", "Ende", f'<div class="schluss"><p>■</p>'
                 f'<p>Das war die {esc(z["titel"])} für {WOCHENTAGE[heute.weekday()]}.</p>'
                 f'<p>{len(auswahl)} Beiträge aus {len(quellen)} Quellen.</p>'
                 f'<p>Die nächste Ausgabe liegt morgen früh bereit.</p></div>')

    buch.set_cover("cover.png", titelbild(z["titel"], heute, nummer, minuten_gesamt,
                                          schlagzeilen, quellen))
    buch.toc = [vorne] + toc
    buch.add_item(epub.EpubNcx())
    buch.add_item(epub.EpubNav())
    buch.spine = [vorne] + kapitel + [ende]

    ziel.mkdir(parents=True, exist_ok=True)
    pfad = ziel / f"Morgenzeitung-{heute:%Y-%m-%d}.epub"
    epub.write_epub(str(pfad), buch)
    return pfad
