#!/usr/bin/env python3
"""
vcprobe.py - prüft, ob VectorCraft fernsteuerbar ist, und zeigt die
Beschreibung der Export-/Speicher-Befehle. Ändert nichts am Dokument.

Vorher VectorCraft so starten:   vectorcraft.exe --control 7979
Dann:                            python vcprobe.py
"""
import json
import socket
import sys

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 7979
WANTED = ["file.saveCopy", "file.export.svg", "document.export", "file.exportAs",
          "clipboard.exportSvg", "file.exportInBackground", "file.formatOptions", "file.saveAs"]


def call(f, sock, method, params=None, _id=[0]):
    _id[0] += 1
    sock.sendall((json.dumps({"id": _id[0], "method": method, "params": params or {}}) + "\n").encode())
    line = f.readline()
    return json.loads(line) if line else None


def main():
    try:
        s = socket.create_connection(("127.0.0.1", PORT), timeout=5)
    except OSError as e:
        sys.exit(f"Keine Verbindung zu 127.0.0.1:{PORT} ({e}).\nLäuft VectorCraft mit  --control {PORT} ?")
    f = s.makefile("r", encoding="utf-8")
    r = call(f, s, "engine.commands")
    if not r or not r.get("ok"):
        sys.exit(f"Antwort auf engine.commands: {r}")
    res = r["result"]
    cmds = res if isinstance(res, list) else res.get("commands", res)

    def key(c):
        return c if isinstance(c, str) else (c.get("id") or c.get("name") or c.get("command"))

    items = cmds if isinstance(cmds, list) else [dict(id=k, **(v if isinstance(v, dict) else {"info": v}))
                                                  for k, v in cmds.items()]
    print("Form eines Eintrags:", json.dumps(items[0], ensure_ascii=False)[:300], "\n")
    for w in WANTED:
        hit = [c for c in items if key(c) == w]
        print(f"== {w}")
        print("   ", json.dumps(hit[0], ensure_ascii=False, indent=1)[:1500] if hit else "(nicht gefunden)")
    d = call(f, s, "ui.inspect")
    if d and d.get("ok"):
        r = d["result"]
        print("\nOffene Dokumente:", json.dumps(r.get("documents"), ensure_ascii=False)[:400])
        print("Aktiv:", json.dumps(r.get("activeDocument"), ensure_ascii=False)[:300])
    s.close()


if __name__ == "__main__":
    main()
