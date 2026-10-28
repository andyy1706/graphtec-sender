<img src="assets/logo-wordmark.svg" alt="Graphtec Sender" height="96">

# Graphtec Sender

SVG-Dateien direkt per Netzwerk (TCP 9100) an einen Graphtec-Schneideplotter
(z. B. CE8000/CE8600) senden – ohne Admin-Rechte, ohne Graphtec Studio.
Nur Python-Standardbibliothek (Python 3.9+), optional als einzelne EXE.

Ablauf: SVG → Linienzüge (mm) → HP-GL → Plotter

## Funktionen

- **Oberfläche** mit Vorschau in Plotter-Lage, mm-Raster und Linealen; hell/dunkel
  (folgt Windows, umschaltbar), Plotter-Status in der Kopfzeile
- **Lage:** spiegeln, drehen (0/90/180/270°), skalieren, verschieben – auch direkt mit
  der Maus (Motiv ziehen, an einer Ecke ziehen)
- **Folie:** Breite und Schneidelänge vom Plotter lesen (HP-GL `OW`), Motiv einpassen
  (optional mit Drehung)
- **Entgittern:** Rahmen ums Motiv und frei platzierbare Linien (oder Raster); die Linien
  werden am Motiv unterbrochen, das Motiv wird nie durchgeschnitten
- **VectorCraft:** starten mit `--control 7979` und aktuelle Zeichnung per Knopf holen
- **Adobe Illustrator:** Zeichnung (oder Auswahl) per Knopf holen oder per Skript aus
  Illustrator senden; Illustrator-SVGs (72 dpi) werden maßstabsrichtig gelesen
- **Überwachungsordner:** neue SVGs werden automatisch geladen (geschnitten wird nur per Klick)
- Ein laufendes Fenster übernimmt Dateien von weiteren Aufrufen (kein zweites Fenster)

## Dateien

| Datei | Zweck |
|---|---|
| `gtsend.py` | Kern + CLI: SVG → HP-GL, senden, Folie abfragen, einpassen, entgittern |
| `gtsend_gui.py` | Oberfläche |
| `gtsend_illustrator.jsx` | Illustrator-Skript „An Plotter senden“ (Datei › Skripten › Anderes Skript …) |
| `Plotter.bat` | Startet die Oberfläche ohne Konsole (`/vc` = mit VectorCraft); nimmt die EXE, falls vorhanden |
| `build_exe.bat` | Baut `dist\GraphtecSender.exe` mit PyInstaller |
| `gtprobe.py` | Fragt Schneidebereich/Status des Plotters ab (bewegt nichts) |
| `vcprobe.py` | Prüft die Fernsteuerung von VectorCraft |
| `assets/` | Logo (`logo.svg`, `logo-wordmark.svg`), Symbole (`icon-*.png`, `graphtec_sender.ico`); neu erzeugen mit `python assets\make_icons.py` (braucht Inkscape) |

## Plotter-Adresse

Die IP des Plotters steht nicht im Code. Reihenfolge:

1. `--host` auf der Kommandozeile
2. Umgebungsvariable `GTSEND_HOST` (dauerhaft: `setx GTSEND_HOST 192.168.1.50`)
3. In der Oberfläche: Feld „Plotter“, wird in `%APPDATA%\gtsend\settings.json` gespeichert

## Nutzung

```bash
pythonw gtsend_gui.py [motiv.svg] [--start-vectorcraft]

python gtsend.py --test                          # Testquadrat 20 mm
python gtsend.py motiv.svg --dry-run             # nur anzeigen
python gtsend.py motiv.svg --mirror              # gespiegelt (Flexfolie)
python gtsend.py --media                         # Folienmaß abfragen
python gtsend.py motiv.svg --fit --fit-rotate    # auf Folie einpassen
python gtsend.py motiv.svg --weed-box 3 --weed-grid 25
python gtsend.py --from-vectorcraft --fit
python gtsend.py motiv.svg --host 1.2.3.4
```

Tastenkürzel in der Oberfläche: **Strg+O** öffnen, **Strg+Enter** senden,
**Strg+S** HP-GL speichern.

## Plotter-Einstellung

Befehlsmodus am Plotter auf **HP-GL** oder **AUTO**. Messerdruck und Geschwindigkeit
kommen aus den Plotter-Einstellungen (`--force`/`--speed` sind ungetestet).

## EXE bauen

`build_exe.bat` doppelklicken → `dist\GraphtecSender.exe` + `dist\gtsend_illustrator.jsx`.
Für andere Arbeitsplätze reichen diese beiden Dateien, Python ist dort nicht nötig.

Einstellungen liegen in `%APPDATA%\gtsend\settings.json`.
