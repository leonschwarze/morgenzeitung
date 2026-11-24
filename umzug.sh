#!/bin/sh
# Einmaliger Umzug: Nutzerdaten aus dem Programmordner an ihren neuen Platz.
#   Konfig  → ~/.config/morgenzeitung/config.toml
#   Daten   → ~/.local/share/morgenzeitung/{ausgaben,zeit,morgenzeitung.log}
# Das Skript verschiebt nur, es löscht nichts, und es überschreibt nichts,
# was am Ziel schon existiert. Mehrfaches Ausführen ist harmlos.
set -eu
PROG="$(cd "$(dirname "$0")" && pwd)"
KONFIG="$HOME/.config/morgenzeitung"
DATEN="$HOME/.local/share/morgenzeitung"
mkdir -p "$KONFIG" "$DATEN"

verschiebe() {   # verschiebe QUELLE ZIEL
    if [ -e "$1" ] && [ ! -e "$2" ]; then
        mv "$1" "$2" && echo "verschoben: $1 → $2"
    elif [ -e "$1" ]; then
        echo "ÜBERSPRUNGEN (Ziel existiert schon): $1 – bitte von Hand prüfen"
    fi
}

verschiebe "$PROG/config.toml"        "$KONFIG/config.toml"
verschiebe "$PROG/ausgaben"           "$DATEN/ausgaben"
verschiebe "$PROG/zeit"               "$DATEN/zeit"
verschiebe "$PROG/morgenzeitung.log"  "$DATEN/morgenzeitung.log"

# Alte Pfade in der Konfiguration auf die neuen umstellen (vorher Sicherungskopie)
if [ -f "$KONFIG/config.toml" ] && grep -q '~/morgenzeitung/' "$KONFIG/config.toml"; then
    cp "$KONFIG/config.toml" "$KONFIG/config.toml.bak"
    sed -i 's#~/morgenzeitung/ausgaben#~/.local/share/morgenzeitung/ausgaben#; s#~/morgenzeitung/zeit#~/.local/share/morgenzeitung/zeit#' \
        "$KONFIG/config.toml"
    echo "Pfade in config.toml angepasst (Sicherung: config.toml.bak)"
fi

echo
echo "Fertig. Noch zwei Dinge von Hand (siehe README, Abschnitt 'Umzug'):"
echo "  1. crontab -e  → Zeile durch die aus crontab.example ersetzen"
echo "  2. Webserver-Dienst auf die neue Version umstellen"
