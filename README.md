# Graphtec Sender

SVG-Dateien direkt per Netzwerk (TCP 9100) an einen Graphtec-Schneideplotter
(z. B. CE8000/CE8600) senden – ohne Admin-Rechte, ohne Graphtec Studio.
Nur Python-Standardbibliothek (Python 3.9+).

Ablauf: SVG → Linienzüge (mm) → HP-GL → Plotter

## Dateien

| Datei | Zweck |
|---|---|
| `gtsend.py` | CLI: SVG → HP-GL, senden, Dry-Run, HP-GL speichern |
| `gtsend_gui.py` | Tkinter-Oberfläche mit Vorschau, Spiegeln/Drehen/Skalieren, Überwachungsordner |
| `gtprobe.py` | Fragt Schneidebereich/Status ab (bewegt nichts) |
| `vcprobe.py` | Prüft die Fernsteuerung von VectorCraft (`--control 7979`) |
| `gtsend_illustrator.jsx` | Illustrator-Skript „An Plotter senden“ |
| `Plotter.bat` | Startet die GUI ohne Konsole (`/vc` = mit VectorCraft) |
| `build_exe.bat` | Baut `dist\GraphtecSender.exe` mit PyInstaller |

## Nutzung

```bash
python gtsend.py --test                 # Testquadrat 20 mm
python gtsend.py motiv.svg --dry-run    # nur anzeigen
python gtsend.py motiv.svg --mirror     # gespiegelt (Flexfolie)
python gtsend.py motiv.svg --host 1.2.3.4
pythonw gtsend_gui.py motiv.svg
```

## EXE bauen

`build_exe.bat` doppelklicken → `dist\GraphtecSender.exe` + `dist\gtsend_illustrator.jsx`.
