#!/usr/bin/env python3
"""
gtprobe.py - fragt den Graphtec-Plotter nach Schneidebereich/Folienmaß.
Sendet NUR Abfragebefehle, bewegt weder Kopf noch Messer.

  python gtprobe.py              # Plotter 192.0.2.10:9100
  python gtprobe.py --host 1.2.3.4
"""
import argparse
import socket
import time

ESC = "\x1b"
# (Beschreibung, Befehl) - alles reine Ausgabe-/Statusbefehle
QUERIES = [
    ("HP-GL OH  Hard-Clip-Grenzen (= nutzbare Folie)", "OH;"),
    ("HP-GL OW  Fenster (Schneidebereich)",            "OW;"),
    ("HP-GL OI  Gerätekennung",                        "OI;"),
    ("HP-GL OS  Status",                               "OS;"),
    ("HP-GL OE  Fehlercode",                           "OE;"),
    ("HP-GL OA  aktuelle Position",                    "OA;"),
    ("ESC.A     Modell/Kennung",                       ESC + ".A"),
    ("ESC.O     Gerätestatus",                         ESC + ".O"),
    ("ESC.B     freier Puffer",                        ESC + ".B"),
    ("Graphtec  ESC ENQ Status",                       ESC + "\x05"),
]


def ask(host, port, cmd, wait):
    with socket.create_connection((host, port), timeout=5) as s:
        s.settimeout(wait)
        s.sendall(cmd.encode("latin-1"))
        buf = b""
        t0 = time.time()
        while time.time() - t0 < wait:
            try:
                b = s.recv(4096)
            except socket.timeout:
                break
            if not b:
                break
            buf += b
            if buf.endswith((b"\r", b"\n", b"\x03")):
                break
        return buf


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="192.0.2.10")
    ap.add_argument("--port", type=int, default=9100)
    ap.add_argument("--wait", type=float, default=2.0, help="Sekunden auf Antwort warten")
    a = ap.parse_args()
    print(f"Plotter {a.host}:{a.port} – Folie eingelegt und Medium gesetzt?\n")
    for desc, cmd in QUERIES:
        try:
            r = ask(a.host, a.port, cmd, a.wait)
            shown = r.decode("latin-1").strip() if r else "(keine Antwort)"
            print(f"{desc:48s} -> {shown!r}  {r.hex() if r else ''}")
        except OSError as e:
            print(f"{desc:48s} -> FEHLER {e}")


if __name__ == "__main__":
    main()
