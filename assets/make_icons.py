"""
make_icons.py - erzeugt die Programm-Symbole aus den SVG-Quellen

    python assets\\make_icons.py [pfad\\zu\\inkscape.exe]

Braucht Inkscape (1.x) zum Rendern, sonst nur die Python-Standardbibliothek.
Kleine Größen haben eigene, vereinfachte SVGs (dickere Linien, weniger Details).

Ergebnis im Ordner assets:
    icon-16.png … icon-256.png    Fenster-/Kopfzeilen-Symbol
    graphtec_sender.ico           Windows-Symbol (EXE, Taskleiste)
"""
import os
import shutil
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# Größe -> SVG-Quelle
SOURCES = {16: "logo-16.svg", 24: "logo-16.svg", 32: "logo-32.svg", 48: "logo-48.svg",
           64: "logo-48.svg", 128: "logo.svg", 256: "logo.svg"}
ICO_SIZES = (16, 24, 32, 48, 64, 256)


def find_inkscape():
    if len(sys.argv) > 1:
        return sys.argv[1]
    exe = shutil.which("inkscape")
    if exe:
        return exe
    for base in (os.environ.get("ProgramFiles", ""), os.environ.get("LOCALAPPDATA", "")):
        p = os.path.join(base, "Inkscape", "bin", "inkscape.exe")
        if os.path.exists(p):
            return p
    sys.exit("Inkscape nicht gefunden - Pfad als Argument angeben.")


def render(inkscape, size):
    out = os.path.join(HERE, f"icon-{size}.png")
    subprocess.run([inkscape, os.path.join(HERE, SOURCES[size]), "--export-type=png",
                    f"--export-filename={out}", f"--export-width={size}", f"--export-height={size}"],
                   check=True, capture_output=True)
    return out


def write_ico(path, pngs):
    """ICO mit eingebetteten PNGs (ab Windows Vista unterstützt)."""
    data = [open(p, "rb").read() for _, p in pngs]
    head = struct.pack("<HHH", 0, 1, len(pngs))
    offset = 6 + 16 * len(pngs)
    entries = b""
    for (size, _), blob in zip(pngs, data):
        s = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", s, s, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
    with open(path, "wb") as f:
        f.write(head + entries + b"".join(data))


def main():
    inkscape = find_inkscape()
    pngs = {size: render(inkscape, size) for size in SOURCES}
    write_ico(os.path.join(HERE, "graphtec_sender.ico"), [(s, pngs[s]) for s in ICO_SIZES])
    print("Fertig:", ", ".join(f"icon-{s}.png" for s in SOURCES), "+ graphtec_sender.ico")


if __name__ == "__main__":
    main()
