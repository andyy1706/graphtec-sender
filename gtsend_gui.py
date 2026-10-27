#!/usr/bin/env python3
"""
gtsend_gui.py - kleine Oberfläche für gtsend.py

- SVG öffnen (auch per "Öffnen mit" / Kommandozeile), Vorschau in Plotter-Lage
- Spiegeln, Drehen, Skalieren, Versatz zum Nullpunkt
- Testquadrat, Senden, HP-GL speichern
- Überwachungsordner: neue/geänderte SVGs werden automatisch geladen
  (gesendet wird trotzdem nur per Klick)

Benötigt gtsend.py im selben Ordner. Nur Python-Standardbibliothek.

Aufruf:  pythonw gtsend_gui.py [motiv.svg] [--start-vectorcraft]
"""

import base64
import json
import os
import queue
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gtsend as gt  # noqa: E402

APP = "Graphtec Sender"
# Ordner des Programms: bei der EXE der Ordner der EXE, sonst der des Skripts
HERE = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
IPC_PORT = 7978                 # Übergabe an ein bereits laufendes Fenster
CFG_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "gtsend")
CFG_FILE = os.path.join(CFG_DIR, "settings.json")
DEFAULTS = {
    "host": gt.DEFAULT_HOST, "port": gt.DEFAULT_PORT, "mirror": False, "rotate": 0,
    "scale": 100.0, "x": 0.0, "y": 0.0, "watch_dir": "", "watch": False,
    "media_width": 600.0, "media_length": 0.0, "margin": 5.0, "fit_rotate": True,
    "weed_box": False, "weed_margin": 3.0, "weed_gap": 1.0, "weed_grid": 25.0,
    "vc_port": 7979, "vc_selection": False, "vc_exe": "",
    "last_dir": "",
}


def load_cfg():
    cfg = dict(DEFAULTS)
    try:
        with open(CFG_FILE, encoding="utf-8") as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    return cfg


def save_cfg(cfg):
    try:
        os.makedirs(CFG_DIR, exist_ok=True)
        with open(CFG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except OSError:
        pass


class App(tk.Tk):
    def __init__(self, start_file=None):
        super().__init__()
        self.title(APP)
        self.geometry("980x640")
        self.minsize(760, 480)
        self.cfg = load_cfg()
        self.src_polys = None      # Linienzüge in mm (SVG-Lage)
        self.src_name = None
        self.src_path = None
        self.src_mtime = None
        self.placed = None         # Motiv in Plotterlage (mm)
        self.wlines = []           # Entgitterlinien (geschnittene Stücke)
        self.wframe = []           # Entgitterrahmen
        self.area = None           # Entgitterfläche (X0, Y0, X1, Y1)
        self.weed_h = []           # waagerechte Linien, Anteil 0..1 der Fläche
        self.weed_v = []           # senkrechte Linien, Anteil 0..1 der Fläche
        self._xf = None            # Vorschau-Transformation (ox, oy, s)
        self._drag = None
        self._view_lock = None     # Maßstab während des Ziehens
        self._view_spans = None
        self.busy = False
        self.seen = {}

        self._build()
        self._apply_cfg()
        self.protocol("WM_DELETE_WINDOW", self._close)
        if start_file:
            self.open_file(start_file)
        self.inbox = queue.Queue()
        self.after(1500, self._poll)
        self.after(300, self._check_inbox)

    # ------------------------------------------------------------------ UI
    def _build(self):
        side = ttk.Frame(self, padding=10)
        side.pack(side="left", fill="y")
        main = ttk.Frame(self, padding=(0, 10, 10, 10))
        main.pack(side="left", fill="both", expand=True)

        vcrow = ttk.Frame(side)
        vcrow.pack(fill="x", pady=(0, 4))
        self.btn_vcstart = ttk.Button(vcrow, text="▶ VectorCraft starten", command=self.start_vectorcraft)
        self.btn_vcstart.pack(side="left", fill="x", expand=True)
        ttk.Button(vcrow, text="…", width=3, command=self.ask_vc_exe).pack(side="left", padx=(3, 0))
        self.btn_vc = ttk.Button(side, text="⇣  Aus VectorCraft holen", command=self.from_vectorcraft)
        self.btn_vc.pack(fill="x", ipady=3)
        self.btn_ai = ttk.Button(side, text="⇣  Aus Illustrator holen", command=self.from_illustrator)
        self.btn_ai.pack(fill="x", ipady=3, pady=(3, 0))
        self.v_vcsel = tk.BooleanVar()
        ttk.Checkbutton(side, text="nur Auswahl", variable=self.v_vcsel).pack(anchor="w", pady=(2, 6))
        ttk.Button(side, text="SVG öffnen …", command=self.ask_open).pack(fill="x")
        ttk.Button(side, text="Testquadrat 20 mm", command=self.load_test).pack(fill="x", pady=(4, 0))

        lf = ttk.LabelFrame(side, text="Lage", padding=8)
        lf.pack(fill="x", pady=10)
        self.v_mirror = tk.BooleanVar()
        ttk.Checkbutton(lf, text="Spiegeln (Flexfolie)", variable=self.v_mirror,
                        command=self.refresh).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(lf, text="Drehen").grid(row=1, column=0, sticky="w", pady=2)
        self.v_rot = tk.StringVar()
        cb = ttk.Combobox(lf, textvariable=self.v_rot, values=["0", "90", "180", "270"], width=6, state="readonly")
        cb.grid(row=1, column=1, sticky="w")
        cb.bind("<<ComboboxSelected>>", lambda e: self.refresh())
        self.v_scale = tk.StringVar()
        self.v_x = tk.StringVar()
        self.v_y = tk.StringVar()
        for r, (lab, var) in enumerate([("Skalierung %", self.v_scale),
                                        ("Versatz X mm", self.v_x),
                                        ("Versatz Y mm", self.v_y)], start=2):
            ttk.Label(lf, text=lab).grid(row=r, column=0, sticky="w", pady=2)
            e = ttk.Entry(lf, textvariable=var, width=8)
            e.grid(row=r, column=1, sticky="w")
            e.bind("<Return>", lambda ev: self.refresh())
            e.bind("<FocusOut>", lambda ev: self.refresh())

        mf = ttk.LabelFrame(side, text="Folie", padding=8)
        mf.pack(fill="x")
        self.v_media = tk.StringVar()
        self.v_mlen = tk.StringVar()
        self.v_margin = tk.StringVar()
        for r, (lab, var) in enumerate([("Breite mm", self.v_media),
                                        ("Länge mm (0 = Rolle)", self.v_mlen),
                                        ("Rand mm", self.v_margin)]):
            ttk.Label(mf, text=lab).grid(row=r, column=0, sticky="w", pady=2)
            e = ttk.Entry(mf, textvariable=var, width=7)
            e.grid(row=r, column=1, sticky="w")
            e.bind("<Return>", lambda ev: self.refresh())
            e.bind("<FocusOut>", lambda ev: self.refresh())
        self.v_fitrot = tk.BooleanVar()
        ttk.Checkbutton(mf, text="beim Einpassen drehen erlaubt", variable=self.v_fitrot
                        ).grid(row=3, column=0, columnspan=2, sticky="w")
        self.btn_query = ttk.Button(mf, text="Vom Plotter abfragen", command=self.do_query)
        self.btn_query.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(6, 2))
        ttk.Button(mf, text="An Folie anpassen", command=self.do_fit
                   ).grid(row=5, column=0, columnspan=2, sticky="ew")

        nf = ttk.LabelFrame(side, text="Plotter", padding=8)
        nf.pack(fill="x", pady=(10, 0))
        self.v_host = tk.StringVar()
        self.v_port = tk.StringVar()
        for r, (lab, var, w) in enumerate([("IP", self.v_host, 14), ("Port", self.v_port, 6)]):
            ttk.Label(nf, text=lab).grid(row=r, column=0, sticky="w", pady=2)
            e = ttk.Entry(nf, textvariable=var, width=w)
            e.grid(row=r, column=1, sticky="w")

        wf = ttk.LabelFrame(side, text="Überwachungsordner", padding=8)
        wf.pack(fill="x", pady=10)
        self.v_watch = tk.BooleanVar()
        ttk.Checkbutton(wf, text="neue SVGs automatisch laden", variable=self.v_watch,
                        command=self._watch_toggled).pack(anchor="w")
        self.lbl_watch = ttk.Label(wf, text="(kein Ordner)", foreground="#666", wraplength=200)
        self.lbl_watch.pack(anchor="w", pady=(2, 4))
        ttk.Button(wf, text="Ordner wählen …", command=self.ask_watch_dir).pack(fill="x")

        self.btn_send = ttk.Button(side, text="▶  An Plotter senden", command=self.do_send)
        self.btn_send.pack(fill="x", pady=(6, 2), ipady=6)
        ttk.Button(side, text="HP-GL speichern …", command=self.save_hpgl).pack(fill="x")

        # Vorschau
        self.lbl_info = ttk.Label(main, text="Keine Datei geladen.", anchor="w")
        self.lbl_info.pack(fill="x")

        # Entgittern – Werkzeugleiste über der Vorschau
        wb = ttk.LabelFrame(main, text="Entgittern", padding=(8, 4))
        wb.pack(fill="x", pady=(6, 0))
        r1 = ttk.Frame(wb)
        r1.pack(fill="x")
        self.v_wbox = tk.BooleanVar()
        ttk.Checkbutton(r1, text="Rahmen", variable=self.v_wbox, command=self.refresh).pack(side="left")
        self.v_wmargin = tk.StringVar()
        self.v_wgap = tk.StringVar()
        self.v_wgrid = tk.StringVar()
        for lab, var in (("Abstand Rahmen mm", self.v_wmargin), ("Abstand Linie–Motiv mm", self.v_wgap)):
            ttk.Label(r1, text=lab).pack(side="left", padx=(12, 3))
            e = ttk.Entry(r1, textvariable=var, width=5)
            e.pack(side="left")
            e.bind("<Return>", lambda ev: self.refresh())
            e.bind("<FocusOut>", lambda ev: self.refresh())
        ttk.Label(r1, text="Raster mm").pack(side="left", padx=(12, 3))
        ttk.Entry(r1, textvariable=self.v_wgrid, width=5).pack(side="left")
        ttk.Button(r1, text="Raster setzen", command=self.weed_grid).pack(side="left", padx=(4, 0))
        ttk.Button(r1, text="Linien löschen", command=self.weed_clear).pack(side="left", padx=(8, 0))
        r2 = ttk.Frame(wb)
        r2.pack(fill="x", pady=(4, 0))
        self.v_tool = tk.StringVar(value="select")
        for val, lab in (("select", "Auswahl / Verschieben"), ("h", "+ waagerecht"), ("v", "+ senkrecht")):
            ttk.Radiobutton(r2, text=lab, value=val, variable=self.v_tool,
                            command=self.draw).pack(side="left", padx=(0, 10))
        ttk.Label(r2, text="Motiv ziehen = verschieben · Ecke ziehen = Größe · Linie: Klick setzen, ziehen, Rechtsklick löschen",
                  foreground="#777").pack(side="left", padx=(10, 0))

        self.canvas = tk.Canvas(main, background="white", highlightthickness=1, highlightbackground="#ccc")
        self.canvas.pack(fill="both", expand=True, pady=(6, 0))
        self.canvas.bind("<Configure>", lambda e: self.draw())
        self.canvas.bind("<Button-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Button-3>", self._on_delete)
        self.canvas.bind("<Motion>", self._on_hover)
        self.status = ttk.Label(main, text="Bereit.", anchor="w", foreground="#444")
        self.status.pack(fill="x", pady=(6, 0))

    def _apply_cfg(self):
        c = self.cfg
        self.v_mirror.set(bool(c["mirror"]))
        self.v_rot.set(str(c["rotate"]))
        self.v_scale.set(f'{c["scale"]:g}')
        self.v_x.set(f'{c["x"]:g}')
        self.v_y.set(f'{c["y"]:g}')
        self.v_host.set(c["host"])
        self.v_port.set(str(c["port"]))
        self.v_media.set(f'{c["media_width"]:g}')
        self.v_mlen.set(f'{c["media_length"]:g}')
        self.v_margin.set(f'{c["margin"]:g}')
        self.v_fitrot.set(bool(c["fit_rotate"]))
        self.v_vcsel.set(bool(c["vc_selection"]))
        self.v_wbox.set(bool(c["weed_box"]))
        self.v_wmargin.set(f'{c["weed_margin"]:g}')
        self.v_wgap.set(f'{c["weed_gap"]:g}')
        self.v_wgrid.set(f'{c["weed_grid"]:g}')
        self.v_watch.set(bool(c["watch"]) and bool(c["watch_dir"]))
        self._show_watch_dir()

    def _collect_cfg(self):
        c = self.cfg
        c["mirror"] = self.v_mirror.get()
        c["rotate"] = int(self.v_rot.get() or 0)
        c["scale"] = self._num(self.v_scale, 100.0)
        c["x"] = self._num(self.v_x, 0.0)
        c["y"] = self._num(self.v_y, 0.0)
        c["host"] = self.v_host.get().strip() or gt.DEFAULT_HOST
        c["port"] = int(self._num(self.v_port, gt.DEFAULT_PORT))
        c["media_width"] = self._num(self.v_media, 600.0)
        c["media_length"] = max(0.0, self._num(self.v_mlen, 0.0))
        c["margin"] = max(0.0, self._num(self.v_margin, 5.0))
        c["fit_rotate"] = self.v_fitrot.get()
        c["vc_selection"] = self.v_vcsel.get()
        c["weed_box"] = self.v_wbox.get()
        c["weed_margin"] = max(0.0, self._num(self.v_wmargin, 3.0))
        c["weed_gap"] = max(0.0, self._num(self.v_wgap, 1.0))
        c["weed_grid"] = max(0.0, self._num(self.v_wgrid, 25.0))
        c["watch"] = self.v_watch.get()
        return c

    @staticmethod
    def _num(var, default):
        try:
            return float(var.get().replace(",", "."))
        except ValueError:
            return default

    def set_status(self, text, color="#444"):
        self.status.config(text=text, foreground=color)

    # -------------------------------------------------------------- Laden
    def ask_open(self):
        p = filedialog.askopenfilename(title="SVG öffnen", initialdir=self.cfg.get("last_dir") or None,
                                       filetypes=[("SVG-Dateien", "*.svg"), ("Alle Dateien", "*.*")])
        if p:
            self.open_file(p)

    def open_file(self, path):
        try:
            polys = [p for p in gt.load_svg(path) if len(p) > 1]
        except Exception as e:  # noqa: BLE001 - Fehler dem Nutzer zeigen
            messagebox.showerror(APP, f"SVG konnte nicht gelesen werden:\n{e}")
            return
        if not polys:
            messagebox.showwarning(APP, "Keine schneidbaren Linien gefunden.\n"
                                        "Text vor dem Export in Pfade umwandeln.")
            return
        if path != self.src_path:            # andere Datei -> eigene Linien
            self.weed_h, self.weed_v = [], []
        self.src_polys = polys
        self.src_path = path
        self.src_name = os.path.basename(path)
        try:
            self.src_mtime = os.path.getmtime(path)
        except OSError:
            self.src_mtime = None
        self.cfg["last_dir"] = os.path.dirname(path)
        self.refresh()
        self.set_status(f"Geladen: {path}")

    # --- VectorCraft im Steuermodus starten ---------------------------------
    @staticmethod
    def _vc_candidates():
        names = ("vectorcraft.exe", "VectorCraft.exe", "vectorcraft")
        roots = [os.environ.get(k) for k in ("LOCALAPPDATA", "ProgramFiles", "ProgramFiles(x86)", "USERPROFILE")]
        subs = ["Programs\\VectorCraft", "VectorCraft", "Programs\\vectorcraft", "ArtCraft\\VectorCraft",
                "Tools\\VectorCraft", "Tools\\vectorcraft", "Apps\\VectorCraft"]
        for n in names:
            w = shutil.which(n)
            if w:
                yield w
        for r in filter(None, roots):
            for sub in subs:
                for n in names:
                    yield os.path.join(r, sub, n)

    def _vc_exe(self):
        exe = self.cfg.get("vc_exe")
        if exe and os.path.isfile(exe):
            return exe
        for cand in self._vc_candidates():
            if os.path.isfile(cand):
                self.cfg["vc_exe"] = cand
                return cand
        return None

    def ask_vc_exe(self):
        start = os.path.dirname(self.cfg.get("vc_exe") or "") or None
        p = filedialog.askopenfilename(title="vectorcraft.exe auswählen", initialdir=start,
                                       filetypes=[("Programm", "*.exe"), ("Alle Dateien", "*.*")])
        if p:
            self.cfg["vc_exe"] = p
            self.set_status(f"VectorCraft-Pfad gespeichert: {p}")
        return p or None

    @staticmethod
    def _port_open(port):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            return False

    def start_vectorcraft(self):
        port = int(self.cfg.get("vc_port", 7979))
        if self._port_open(port):
            self.set_status(f"VectorCraft läuft bereits mit Fernsteuerung (Port {port}).", "#070")
            return
        exe = self._vc_exe() or self.ask_vc_exe()
        if not exe:
            return
        flags = 0
        if os.name == "nt":
            flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        try:
            subprocess.Popen([exe, "--control", str(port)], cwd=os.path.dirname(exe) or None,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=flags, close_fds=True)
        except OSError as e:
            messagebox.showerror(APP, f"VectorCraft konnte nicht gestartet werden:\n{e}")
            return
        self.btn_vcstart.state(["disabled"])
        self.set_status("Starte VectorCraft …")

        def wait():
            ok = False
            for _ in range(60):                 # bis zu 30 s auf den Steuerport warten
                if self._port_open(port):
                    ok = True
                    break
                time.sleep(0.5)
            self.after(0, lambda: (
                self.btn_vcstart.state(["!disabled"]),
                self.set_status("VectorCraft bereit – „Aus VectorCraft holen“ funktioniert jetzt.", "#070") if ok
                else self.set_status("VectorCraft gestartet, aber der Steuerport antwortet nicht. "
                                     "Unterstützt diese Version  --control ?", "#b00")))

        threading.Thread(target=wait, daemon=True).start()

    def from_vectorcraft(self):
        if self.busy:
            return
        c = self._collect_cfg()
        sel, port = c["vc_selection"], int(c.get("vc_port", 7979))
        tmpdir = os.path.join(tempfile.gettempdir(), "gtsend")
        os.makedirs(tmpdir, exist_ok=True)
        self.busy = True
        self.btn_vc.state(["disabled"])
        self.set_status("Hole Zeichnung aus VectorCraft …")

        def work():
            try:
                out, title = gt.vc_export(os.path.join(tmpdir, "_export.svg"), sel, port)
                # fester Pfad je Dokument: so bleiben Entgitterlinien beim erneuten Holen erhalten
                safe = re.sub(r'[^\w.-]+', "_", os.path.splitext(title)[0]) or "vectorcraft"
                dst = os.path.join(tmpdir, f"vc_{safe}{'_auswahl' if sel else ''}.svg")
                shutil.copyfile(out, dst)
                self.after(0, lambda: self._got_vc(dst, title, sel))
            except OSError as e:
                err = str(e)
                self.after(0, lambda: (self.set_status("Holen aus VectorCraft fehlgeschlagen.", "#b00"),
                                       messagebox.showerror(APP, f"Aus VectorCraft holen fehlgeschlagen:\n{err}\n\n"
                                                                 "VectorCraft muss mit  --control 7979  laufen.")))
            finally:
                self.after(0, lambda: (setattr(self, "busy", False), self.btn_vc.state(["!disabled"])))

        threading.Thread(target=work, daemon=True).start()

    def _got_vc(self, path, title, sel):
        self.open_file(path)
        self.src_name = f"{title}" + (" (Auswahl)" if sel else "") + " – aus VectorCraft"
        self.src_mtime = None          # Temp-Datei nicht auf Änderungen überwachen
        self.refresh()
        self.set_status(f"Aus VectorCraft geholt: {title}" + (" – nur Auswahl" if sel else ""), "#070")

    # --- Adobe Illustrator (Windows, über COM + ExtendScript) ----------------
    def _illustrator_export(self, path, selection):
        """Lässt das laufende Illustrator exportieren. Rückgabe (pfad, titel, auswahl)."""
        if os.name != "nt":
            raise OSError("Illustrator-Anbindung gibt es nur unter Windows")
        jsx = os.path.join(HERE, "gtsend_illustrator.jsx")
        if not os.path.isfile(jsx):
            raise OSError(f"gtsend_illustrator.jsx fehlt in {HERE}")
        js = ("var GTSEND_NO_MAIN = true; $.evalFile(new File(" + json.dumps(jsx.replace("\\", "/")) + ")); "
              "gtsendExport(" + json.dumps(path) + ", " + ("true" if selection else "false") + ");")
        ps = (
            "try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}\n"
            "try { $ai = [Runtime.InteropServices.Marshal]::GetActiveObject('Illustrator.Application') }\n"
            "catch { 'ERR|Illustrator läuft nicht oder ist nicht erreichbar.'; exit }\n"
            "try { $r = $ai.DoJavaScript('" + js.replace("'", "''") + "'); Write-Output $r }\n"
            "catch { 'ERR|' + $_.Exception.Message }\n"
        )
        enc = base64.b64encode(ps.encode("utf-16-le")).decode("ascii")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            cp = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", enc],
                                capture_output=True, timeout=180, creationflags=flags)
        except subprocess.TimeoutExpired:
            raise OSError("Illustrator antwortet nicht (offener Dialog in Illustrator?)") from None
        out = cp.stdout.decode("utf-8", "replace").strip().splitlines()
        line = next((ln for ln in reversed(out) if ln.startswith(("OK|", "ERR|"))), "")
        if not line:
            err = cp.stderr.decode("utf-8", "replace").strip()
            raise OSError(f"keine Antwort von Illustrator {err[:300]}")
        if line.startswith("ERR|"):
            raise OSError(line[4:])
        _, outpath, title, sel = (line.split("|") + ["", "", "0"])[:4]
        return outpath, title, sel == "1"

    def from_illustrator(self):
        if self.busy:
            return
        c = self._collect_cfg()
        sel = c["vc_selection"]
        tmpdir = os.path.join(tempfile.gettempdir(), "gtsend")
        os.makedirs(tmpdir, exist_ok=True)
        self.busy = True
        self.btn_ai.state(["disabled"])
        self.set_status("Hole Zeichnung aus Illustrator …")

        def work():
            try:
                out, title, used_sel = self._illustrator_export(os.path.join(tmpdir, "_ai_export.svg"), sel)
                safe = re.sub(r'[^\w.-]+', "_", os.path.splitext(title)[0]) or "illustrator"
                dst = os.path.join(tmpdir, f"ai_{safe}{'_auswahl' if used_sel else ''}.svg")
                shutil.copyfile(out, dst)
                self.after(0, lambda: self._got_app_file(dst, title, used_sel, "Illustrator"))
            except OSError as e:
                err = str(e)
                self.after(0, lambda: (self.set_status("Holen aus Illustrator fehlgeschlagen.", "#b00"),
                                       messagebox.showerror(APP, f"Aus Illustrator holen fehlgeschlagen:\n{err}")))
            finally:
                self.after(0, lambda: (setattr(self, "busy", False), self.btn_ai.state(["!disabled"])))

        threading.Thread(target=work, daemon=True).start()

    def _got_app_file(self, path, title, sel, source):
        self.open_file(path)
        self.src_name = f"{title}" + (" (Auswahl)" if sel else "") + f" – aus {source}"
        self.src_mtime = None
        self.refresh()
        self.set_status(f"Aus {source} geholt: {title}" + (" – nur Auswahl" if sel else ""), "#070")

    # --- Übergabe von außen (Illustrator-Skript, Plotter.bat mit Datei) ------
    def _check_inbox(self):
        try:
            while True:
                kind, arg = self.inbox.get_nowait()
                if kind == "open" and arg and os.path.isfile(arg):
                    name = os.path.basename(arg)
                    self.open_file(arg)
                    if name.startswith("ai_"):
                        self.src_name = name[3:-4].replace("_auswahl", " (Auswahl)") + " – aus Illustrator"
                        self.src_mtime = None
                        self.refresh()
                    self.set_status(f"Übernommen: {name}", "#070")
                elif kind == "vc":
                    self.start_vectorcraft()
                self.deiconify()
                self.lift()
                self.attributes("-topmost", True)
                self.after(400, lambda: self.attributes("-topmost", False))
        except queue.Empty:
            pass
        self.after(300, self._check_inbox)

    def load_test(self):
        self.src_polys = gt.test_pattern(20.0)
        self.weed_h, self.weed_v = [], []
        self.src_name = "Testquadrat 20 mm"
        self.src_path = None
        self.src_mtime = None
        self.refresh()
        self.set_status("Testmuster geladen. Das kleine Dreieck markiert die Ecke am Nullpunkt.")

    # ---------------------------------------------------------- Vorschau
    def refresh(self):
        c = self._collect_cfg()
        if not self.src_polys:
            self.placed = None
            self.wlines, self.wframe, self.area = [], [], None
            self.draw()
            return
        placed = gt.place(self.src_polys, c["rotate"], c["mirror"], c["scale"] / 100.0, (c["x"], c["y"]))
        if self._weeding_on():
            bm = c["weed_margin"]
            self.placed, self.wlines, self.wframe = gt.weeding(
                placed, bm, c["weed_box"], self.weed_h, self.weed_v, c["weed_gap"])
            mx0, my0, mx1, my1 = gt.bbox(self.placed)
            self.area = (mx0 - bm, my0 - bm, mx1 + bm, my1 + bm)
        else:
            self.placed, self.wlines, self.wframe, self.area = placed, [], [], None
        x0, y0, x1, y1 = gt.bbox(self._all())
        warn = ""
        if y1 > c["media_width"] + 0.01:
            warn = f"   ⚠ breiter als Folie ({y1:.0f} > {c['media_width']:g} mm)"
        elif c["media_length"] and x1 > c["media_length"] + 0.01:
            warn = f"   ⚠ länger als Folie ({x1:.0f} > {c['media_length']:g} mm)"
        self.lbl_info.config(text=f"{self.src_name}   –   {x1 - x0:.1f} × {y1 - y0:.1f} mm   "
                                  f"({len(self.placed)} Linienzüge"
                                  + (f", {len(self.weed_h) + len(self.weed_v)} Entgitterlinien" if self.area else "")
                                  + f"){warn}",
                             foreground="#b00" if warn else "")
        self.draw()

    def draw(self):
        cv = self.canvas
        cv.delete("all")
        W, H = cv.winfo_width(), cv.winfo_height()
        if W < 50 or H < 50:
            return
        pad = 30
        media = self.cfg.get("media_width", 600.0) or 600.0
        mlen = self.cfg.get("media_length", 0.0) or 0.0
        if self.placed:
            _, _, x1, y1 = gt.bbox(self._all())
        else:
            x1, y1 = 100.0, 100.0
        # Plotter-X (Vorschub) nach rechts, Plotter-Y (quer) nach oben
        span_x = max(x1, mlen, 50.0) * 1.05
        span_y = max(y1, media, 50.0) * 1.08
        if self._view_lock:                  # während des Ziehens Maßstab festhalten
            span_x, span_y = self._view_lock
        self._view_spans = (span_x, span_y)
        s = min((W - 2 * pad) / span_x, (H - 2 * pad) / span_y)
        ox, oy = pad, H - pad

        self._xf = (ox, oy, s)

        def P(x, y):
            return ox + x * s, oy - y * s

        # Folie (hellgrau hinterlegt) und Rand
        fx1 = P(mlen, 0)[0] if mlen else W - pad / 2
        _, ym = P(0, media)
        cv.create_rectangle(ox, ym, fx1, oy, fill="#f4f6f8", outline="#c8ccd2")
        m = self.cfg.get("margin", 0.0) or 0.0
        if m and m * 2 < media:
            mx1 = P(mlen - m, 0)[0] if mlen else fx1
            cv.create_rectangle(ox + m * s, ym + m * s, mx1, oy - m * s, outline="#d8dce2", dash=(2, 3))
        label = f"Folie {media:g} mm breit" + (f" × {mlen:g} mm" if mlen else " (Rolle)")
        cv.create_text(fx1 - 4, ym - 8, text=label, anchor="e", fill="#7a8290", font=("Segoe UI", 8))
        # Achsen
        cv.create_line(ox, oy, W - pad / 2, oy, fill="#bbb", arrow="last")
        cv.create_line(ox, oy, ox, pad / 2, fill="#bbb", arrow="last")
        cv.create_text(W - pad / 2, oy + 12, text="Vorschub (X)", anchor="e", fill="#999", font=("Segoe UI", 8))
        cv.create_text(ox + 4, pad / 2, text="quer (Y)", anchor="nw", fill="#999", font=("Segoe UI", 8))
        cv.create_oval(ox - 4, oy - 4, ox + 4, oy + 4, outline="#d00", width=2)
        cv.create_text(ox + 8, oy - 8, text="Nullpunkt", anchor="sw", fill="#d00", font=("Segoe UI", 8))

        if not self.placed:
            cv.create_text(W / 2, H / 2, text="SVG öffnen oder Testquadrat laden",
                           fill="#999", font=("Segoe UI", 12))
            return
        # Hilfslinien (gestrichelt über die ganze Entgitterfläche)
        if self.area:
            X0, Y0, X1, Y1 = self.area
            for f in self.weed_h:
                y = Y0 + f * (Y1 - Y0)
                cv.create_line(*P(X0, y), *P(X1, y), fill="#b9d3f5", dash=(3, 3))
            for f in self.weed_v:
                x = X0 + f * (X1 - X0)
                cv.create_line(*P(x, Y0), *P(x, Y1), fill="#b9d3f5", dash=(3, 3))
        for group, color, width in ((self.placed, "#111", 1), (self.wlines, "#1f6feb", 2),
                                    (self.wframe, "#1f6feb", 1)):
            for p in group:
                pts = []
                for x, y in p:
                    pts.extend(P(x, y))
                if len(pts) >= 4:
                    cv.create_line(*pts, fill=color, width=width)
        # Anfasser: Rahmen ums Motiv, Ecken zum Skalieren
        if self.v_tool.get() == "select":
            c = self._corners_px()
            xs, ys = [p[1] for p in c], [p[2] for p in c]
            cv.create_rectangle(min(xs), min(ys), max(xs), max(ys), outline="#9aa4b2", dash=(2, 2))
            for _, px, py in c:
                cv.create_rectangle(px - 4, py - 4, px + 4, py + 4, fill="white", outline="#1f6feb", width=1.5)

    # ---------------------------------------------------------- Entgittern
    def _weeding_on(self):
        return bool(self.v_wbox.get() or self.weed_h or self.weed_v)

    def _all(self):
        return list(self.placed or []) + self.wlines + self.wframe

    def _to_mm(self, ev):
        ox, oy, s = self._xf
        return (ev.x - ox) / s, (oy - ev.y) / s

    def _area_for_new_line(self):
        """Entgitterfläche – auch dann, wenn noch keine Linie existiert."""
        if self.area:
            return self.area
        if not self.placed:
            return None
        bm = self.cfg.get("weed_margin", 3.0)
        x0, y0, x1, y1 = gt.bbox(self.placed)
        return (x0, y0, x1 + 2 * bm, y1 + 2 * bm)   # so liegt sie nach dem Verschieben

    def _hit(self, ev, tol_px=6):
        """Linie unter dem Mauszeiger: ('h'|'v', index) oder None."""
        if not (self.area and self._xf):
            return None
        X0, Y0, X1, Y1 = self.area
        x, y = self._to_mm(ev)
        s = self._xf[2]
        tol = tol_px / s
        best = None
        if X0 - tol <= x <= X1 + tol:
            for i, f in enumerate(self.weed_h):
                d = abs(y - (Y0 + f * (Y1 - Y0)))
                if d <= tol and (best is None or d < best[0]):
                    best = (d, "h", i)
        if Y0 - tol <= y <= Y1 + tol:
            for i, f in enumerate(self.weed_v):
                d = abs(x - (X0 + f * (X1 - X0)))
                if d <= tol and (best is None or d < best[0]):
                    best = (d, "v", i)
        return best[1:] if best else None

    # --- Motiv anfassen: Verschieben und Größe ändern ----------------------
    def _corners_px(self):
        """Eckpunkte des Motivs in Pixeln: Liste (index, x, y). 0=unten-links, 1=unten-rechts,
        2=oben-rechts, 3=oben-links (Plotterlage)."""
        if not (self.placed and self._xf):
            return []
        ox, oy, s = self._xf
        x0, y0, x1, y1 = gt.bbox(self.placed)
        pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        return [(i, ox + x * s, oy - y * s) for i, (x, y) in enumerate(pts)]

    def _hit_handle(self, ev, tol=8):
        for i, px, py in self._corners_px():
            if abs(ev.x - px) <= tol and abs(ev.y - py) <= tol:
                return i
        return None

    def _inside_motif(self, ev):
        c = self._corners_px()
        if not c:
            return False
        xs, ys = [p[1] for p in c], [p[2] for p in c]
        return min(xs) - 2 <= ev.x <= max(xs) + 2 and min(ys) - 2 <= ev.y <= max(ys) + 2

    def _on_hover(self, ev):
        if self._drag:
            return
        h = self._hit(ev)
        hd = self._hit_handle(ev)
        tool = self.v_tool.get()
        if h:
            cur = "sb_v_double_arrow" if h[0] == "h" else "sb_h_double_arrow"
        elif hd is not None:
            cur = "size_ne_sw" if hd in (0, 2) else "size_nw_se"
        elif tool in ("h", "v") and self.placed:
            cur = "crosshair"
        elif self._inside_motif(ev):
            cur = "fleur"
        else:
            cur = ""
        try:
            self.canvas.config(cursor=cur)
        except tk.TclError:                 # Cursorname auf dieser Plattform unbekannt
            self.canvas.config(cursor="")

    def _start_view_lock(self):
        self._view_lock = self._view_spans

    def _on_press(self, ev):
        if not (self.placed and self._xf):
            return
        hit = self._hit(ev)
        if hit:
            self._drag = hit
            self._start_view_lock()
            return
        hd = self._hit_handle(ev)
        if hd is not None:
            x0, y0, x1, y1 = gt.bbox(self.placed)
            pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
            corner, anchor = pts[hd], pts[(hd + 2) % 4]
            shift = self.cfg["weed_margin"] if self._weeding_on() else 0.0
            self._drag = ("scale", corner, anchor, self._num(self.v_scale, 100.0), shift)
            self._start_view_lock()
            return
        tool = self.v_tool.get()
        if tool not in ("h", "v"):
            if self._inside_motif(ev):
                self._drag = ("move", self._to_mm(ev), self._num(self.v_x, 0.0), self._num(self.v_y, 0.0))
                self._start_view_lock()
            return
        area = self._area_for_new_line()
        if not area:
            return
        X0, Y0, X1, Y1 = area
        x, y = self._to_mm(ev)
        if not (X0 <= x <= X1 and Y0 <= y <= Y1):
            self.set_status("Linien nur innerhalb der Entgitterfläche (Motiv + Rahmenabstand) setzen.")
            return
        if tool == "h":
            self.weed_h.append((y - Y0) / (Y1 - Y0))
            self._drag = ("h", len(self.weed_h) - 1)
        else:
            self.weed_v.append((x - X0) / (X1 - X0))
            self._drag = ("v", len(self.weed_v) - 1)
        self._start_view_lock()
        self.refresh()

    def _on_drag(self, ev):
        if not self._drag:
            return
        x, y = self._to_mm(ev)
        kind = self._drag[0]
        if kind == "move":
            (sx, sy), x0, y0 = self._drag[1], self._drag[2], self._drag[3]
            self.v_x.set(f"{max(0.0, x0 + x - sx):.1f}")
            self.v_y.set(f"{max(0.0, y0 + y - sy):.1f}")
            self.refresh()
            self.set_status(f"Versatz {self.v_x.get()} / {self.v_y.get()} mm")
            return
        if kind == "scale":
            (cx, cy), (ax, ay), s0, shift = self._drag[1], self._drag[2], self._drag[3], self._drag[4]
            dx, dy = cx - ax, cy - ay
            L2 = dx * dx + dy * dy
            if L2 <= 0:
                return
            f = ((x - ax) * dx + (y - ay) * dy) / L2          # Projektion auf die Diagonale
            f_max = 50.0
            if cx < ax:                                         # wächst nach links -> Nullpunkt ist Grenze
                f_max = min(f_max, (ax - shift) / abs(dx))
            if cy < ay:                                         # wächst nach unten
                f_max = min(f_max, (ay - shift) / abs(dy))
            f = min(f_max, max(0.02, f))
            w, h = abs(dx) * f, abs(dy) * f
            nx0 = ax if cx > ax else ax - w                     # gegenüberliegende Ecke bleibt stehen
            ny0 = ay if cy > ay else ay - h
            self.v_scale.set(f"{s0 * f:.1f}")
            self.v_x.set(f"{max(0.0, nx0 - shift):.1f}")
            self.v_y.set(f"{max(0.0, ny0 - shift):.1f}")
            self.refresh()
            self.set_status(f"Skalierung {s0 * f:.1f} %  –  {w:.1f} × {h:.1f} mm")
            return
        if not self.area:
            return
        X0, Y0, X1, Y1 = self.area
        i = self._drag[1]
        if kind == "h":
            self.weed_h[i] = min(0.995, max(0.005, (y - Y0) / (Y1 - Y0)))
        else:
            self.weed_v[i] = min(0.995, max(0.005, (x - X0) / (X1 - X0)))
        self.refresh()

    def _on_release(self, ev):
        d = self._drag
        self._drag = None
        self._view_lock = None
        if not d:
            return
        if d[0] in ("move", "scale"):
            self.draw()          # Ansicht wieder an den Inhalt anpassen
            return
        if self.area:
            kind, i = d
            X0, Y0, X1, Y1 = self.area
            pos = self.weed_h[i] * (Y1 - Y0) if kind == "h" else self.weed_v[i] * (X1 - X0)
            edge = "Unterkante" if kind == "h" else "linker Kante"
            self.set_status(f"{'Waagerechte' if kind == 'h' else 'Senkrechte'} Linie bei {pos:.1f} mm "
                            f"ab {edge} der Entgitterfläche.")
            self.draw()

    def _on_delete(self, ev):
        hit = self._hit(ev, tol_px=8)
        if not hit:
            return
        kind, i = hit
        (self.weed_h if kind == "h" else self.weed_v).pop(i)
        self.refresh()
        self.set_status("Linie gelöscht.")

    def weed_grid(self):
        if not self.placed:
            messagebox.showinfo(APP, "Erst eine SVG öffnen oder das Testquadrat laden.")
            return
        c = self._collect_cfg()
        if c["weed_grid"] <= 0:
            messagebox.showwarning(APP, "Rasterabstand in mm angeben, z. B. 25.")
            return
        area = self._area_for_new_line()
        X0, Y0, X1, Y1 = area
        self.weed_h = gt.grid_positions(Y1 - Y0, c["weed_grid"])
        self.weed_v = gt.grid_positions(X1 - X0, c["weed_grid"])
        self.refresh()
        self.set_status(f"Raster gesetzt: {len(self.weed_h)} waagerecht, {len(self.weed_v)} senkrecht. "
                        "Einzelne Linien kannst du verschieben oder per Rechtsklick löschen.")

    def weed_clear(self):
        self.weed_h, self.weed_v = [], []
        self.refresh()
        self.set_status("Entgitterlinien gelöscht.")

    # ------------------------------------------------- Folie / Einpassen
    def do_query(self):
        if self.busy:
            return
        c = self._collect_cfg()
        host, port = c["host"], c["port"]
        self.busy = True
        self.btn_query.state(["disabled"])
        self.set_status(f"Frage Folie bei {host}:{port} ab …")

        def work():
            try:
                ml, mw = gt.query_media(host, port)
                self.after(0, lambda: self._got_media(ml, mw))
            except OSError as e:
                err = str(e)
                self.after(0, lambda: (self.set_status("Abfrage fehlgeschlagen.", "#b00"),
                                       messagebox.showerror(APP, f"Folienmaß nicht abfragbar:\n{err}\n\n"
                                                                 "Plotter an, Folie eingelegt und erfasst?")))
            finally:
                self.after(0, lambda: (setattr(self, "busy", False), self.btn_query.state(["!disabled"])))

        threading.Thread(target=work, daemon=True).start()

    def _got_media(self, ml, mw):
        self.v_media.set(f"{mw:.1f}")
        self.v_mlen.set(f"{ml:.1f}")
        self.refresh()
        self.set_status(f"Folie erkannt: {mw:.1f} mm breit, Schneidelänge {ml:.1f} mm.", "#070")

    def do_fit(self):
        if not self.src_polys:
            messagebox.showinfo(APP, "Erst eine SVG öffnen oder das Testquadrat laden.")
            return
        c = self._collect_cfg()
        if c["media_width"] <= 2 * c["margin"]:
            messagebox.showwarning(APP, "Folienbreite fehlt oder ist kleiner als der doppelte Rand.")
            return
        # Entgitterrahmen gehört mit auf die Folie
        extra = c["weed_margin"] if self._weeding_on() else 0.0
        s, rot = gt.fit(self.src_polys, c["media_length"], c["media_width"], c["margin"] + extra,
                        c["rotate"], c["mirror"], c["fit_rotate"])
        self.v_scale.set(f"{s * 100:.1f}")
        self.v_rot.set(str(rot))
        self.v_x.set(f'{c["margin"]:g}')
        self.v_y.set(f'{c["margin"]:g}')
        self.refresh()
        self.set_status(f"Eingepasst: {s * 100:.1f} %, Drehung {rot}°, Rand {c['margin']:g} mm.")

    # ---------------------------------------------------------- Senden
    def _hpgl(self):
        if not self.placed:
            messagebox.showinfo(APP, "Erst eine SVG öffnen oder das Testquadrat laden.")
            return None
        data, _ = gt.to_hpgl(self.placed, after=[self.wlines, self.wframe])
        return data

    def do_send(self):
        if self.busy:
            return
        self.refresh()
        data = self._hpgl()
        if data is None:
            return
        c = self.cfg
        x0, y0, x1, y1 = gt.bbox(self._all())
        if not messagebox.askokcancel(
                APP, f"„{self.src_name}“ schneiden?\n\n"
                     f"Größe: {x1 - x0:.1f} × {y1 - y0:.1f} mm\n"
                     f"Spiegeln: {'ja' if c['mirror'] else 'nein'}   Drehen: {c['rotate']}°\n"
                     + (f"Entgittern: {len(self.weed_h) + len(self.weed_v)} Linien"
                        + (", Rahmen" if self.wframe else "") + "\n" if self.area else "")
                     + f"Plotter: {c['host']}:{c['port']}"):
            return
        self.busy = True
        self.btn_send.state(["disabled"])
        self.set_status(f"Sende an {c['host']}:{c['port']} …")
        host, port = c["host"], c["port"]

        def work():
            try:
                gt.send(host, port, data)
                self.after(0, lambda: self.set_status(
                    f"Gesendet: {self.src_name} ({len(data)} Byte) – {time.strftime('%H:%M:%S')}", "#070"))
            except OSError as e:
                err = str(e)
                self.after(0, lambda: (self.set_status("Senden fehlgeschlagen.", "#b00"),
                                       messagebox.showerror(APP, f"Senden fehlgeschlagen:\n{err}\n\n"
                                                                 "Plotter an und im Netz? IP/Port prüfen.")))
            finally:
                self.after(0, self._done)

        threading.Thread(target=work, daemon=True).start()

    def _done(self):
        self.busy = False
        self.btn_send.state(["!disabled"])

    def save_hpgl(self):
        self.refresh()
        data = self._hpgl()
        if data is None:
            return
        base = os.path.splitext(self.src_name or "plot")[0] + ".plt"
        p = filedialog.asksaveasfilename(title="HP-GL speichern", initialfile=base, defaultextension=".plt",
                                         filetypes=[("HP-GL", "*.plt *.hpgl"), ("Alle Dateien", "*.*")])
        if p:
            with open(p, "w", encoding="ascii") as f:
                f.write(data)
            self.set_status(f"Gespeichert: {p}")

    # ------------------------------------------------- Überwachungsordner
    def ask_watch_dir(self):
        d = filedialog.askdirectory(title="Überwachungsordner wählen",
                                    initialdir=self.cfg.get("watch_dir") or None)
        if d:
            self.cfg["watch_dir"] = d
            self.seen = self._scan(d)
            self.v_watch.set(True)
            self._show_watch_dir()
            self.set_status(f"Überwache {d} – neue SVGs werden automatisch geladen.")

    def _show_watch_dir(self):
        d = self.cfg.get("watch_dir")
        self.lbl_watch.config(text=d or "(kein Ordner)")
        if d and not self.seen:
            self.seen = self._scan(d)

    def _watch_toggled(self):
        if self.v_watch.get() and not self.cfg.get("watch_dir"):
            self.ask_watch_dir()
        elif self.v_watch.get():
            self.seen = self._scan(self.cfg["watch_dir"])

    @staticmethod
    def _scan(d):
        res = {}
        try:
            for e in os.scandir(d):
                if e.is_file() and e.name.lower().endswith(".svg"):
                    res[e.path] = e.stat().st_mtime
        except OSError:
            pass
        return res

    def _poll(self):
        try:
            # geöffnete Datei neu laden, wenn sie geändert wurde (z. B. erneuter Export)
            if self.src_path and self.src_mtime:
                try:
                    m = os.path.getmtime(self.src_path)
                    if m != self.src_mtime:
                        time.sleep(0.3)
                        self.open_file(self.src_path)
                        self.set_status(f"Neu geladen (Datei geändert): {self.src_name}")
                except OSError:
                    pass
            d = self.cfg.get("watch_dir")
            if self.v_watch.get() and d:
                now = self._scan(d)
                new = [p for p, m in now.items() if self.seen.get(p) != m]
                self.seen = now
                if new:
                    newest = max(new, key=lambda p: now[p])
                    time.sleep(0.3)   # Export fertig schreiben lassen
                    self.open_file(newest)
                    self.set_status(f"Neue Datei im Ordner: {os.path.basename(newest)} – zum Schneiden „Senden“ klicken.")
                    self.deiconify()
                    self.lift()
                    self.attributes("-topmost", True)
                    self.after(400, lambda: self.attributes("-topmost", False))
        finally:
            self.after(1500, self._poll)

    def _close(self):
        save_cfg(self._collect_cfg())
        self.destroy()


def hand_over(msgs):
    """An ein bereits laufendes Fenster übergeben. True = erledigt."""
    try:
        with socket.create_connection(("127.0.0.1", IPC_PORT), timeout=1.0) as s:
            s.sendall(("GTSEND\n" + "".join(m + "\n" for m in msgs)).encode("utf-8"))
        return True
    except OSError:
        return False


def serve_ipc(app):
    """Nimmt 'OPEN <pfad>', 'VC' bzw. 'SHOW' von weiteren Aufrufen entgegen."""
    try:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", IPC_PORT))
        srv.listen(4)
    except OSError:
        return None                 # Port belegt – dann eben ohne Übergabe

    def loop():
        while True:
            try:
                c, _ = srv.accept()
            except OSError:
                return
            data = b""
            with c:
                c.settimeout(2)
                try:
                    while len(data) < 65536:
                        b = c.recv(4096)
                        if not b:
                            break
                        data += b
                except OSError:
                    pass
            lines = data.decode("utf-8", "replace").splitlines()
            if not lines or lines[0] != "GTSEND":     # fremde Verbindungen ignorieren
                continue
            for line in lines[1:]:
                if line.startswith("OPEN "):
                    app.inbox.put(("open", line[5:].strip()))
                elif line.strip() == "VC":
                    app.inbox.put(("vc", None))
                elif line.strip() == "SHOW":
                    app.inbox.put(("show", None))

    threading.Thread(target=loop, daemon=True).start()
    return srv


if __name__ == "__main__":
    args = sys.argv[1:]
    start_vc = "--start-vectorcraft" in args
    files = [os.path.abspath(a) for a in args if not a.startswith("--")]
    msgs = ([f"OPEN {f}" for f in files[:1]] + (["VC"] if start_vc else [])) or ["SHOW"]
    if hand_over(msgs):             # läuft schon -> Datei dort öffnen, fertig
        sys.exit(0)
    app = App(files[0] if files else None)
    serve_ipc(app)
    if start_vc:
        app.after(300, app.start_vectorcraft)
    app.mainloop()
