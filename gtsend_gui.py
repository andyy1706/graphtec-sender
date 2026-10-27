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
import tkinter.font as tkfont
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
    "theme": "auto", "collapsed": {"settings": True},
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


# --------------------------------------------------------------------------
# Design: Farben, Schriften, Stile
# --------------------------------------------------------------------------
PALETTES = {
    "light": dict(
        bg="#f3f4f6", surface="#ffffff", surface2="#f1f3f6", hover="#e7eaef", border="#e1e4e9",
        text="#1f2328", muted="#6b7280", accent="#2563eb", accent_hover="#1d4ed8", accent_fg="#ffffff",
        accent_soft="#e8effd", ok="#15803d", err="#c62828", warn="#a15c07", warn_bg="#fdf1d8",
        cv_bg="#ffffff", media="#f6f8fa", media_line="#cdd4dc", grid="#edf0f3", grid2="#dfe4ea",
        ink="#111827", weed="#2563eb", guide="#a9c3f5", origin="#dc2626", handle="#ffffff", sel="#94a3b8",
        label="#8a93a0", tip_bg="#1f2328", tip_fg="#ffffff"),
    "dark": dict(
        bg="#17181b", surface="#212327", surface2="#2a2d32", hover="#33363c", border="#33373d",
        text="#e7e9ec", muted="#9aa1ab", accent="#4c8dff", accent_hover="#6ea2ff", accent_fg="#ffffff",
        accent_soft="#22324f", ok="#4ade80", err="#f87171", warn="#f6c453", warn_bg="#3a3017",
        cv_bg="#1c1e22", media="#262a2f", media_line="#464b53", grid="#2c3036", grid2="#373b42",
        ink="#f3f4f6", weed="#60a5fa", guide="#3a5585", origin="#f87171", handle="#212327", sel="#6b7280",
        label="#7d8590", tip_bg="#e7e9ec", tip_fg="#17181b"),
}
STATUS_KIND = {"#070": "ok", "#b00": "err"}       # alte Farbcodes -> Bedeutung


def windows_dark_mode():
    if os.name != "nt":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except OSError:
        return False


def enable_dpi_awareness():
    """Scharfe Schrift auf hochauflösenden Windows-Bildschirmen."""
    if os.name != "nt":
        return
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        try:
            import ctypes
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


class Tooltip:
    """Kleiner Hinweis beim Überfahren mit der Maus."""

    def __init__(self, app, widget, text):
        self.app, self.w, self.text = app, widget, text
        self.tip = self.job = None
        widget.bind("<Enter>", self._enter, add="+")
        widget.bind("<Leave>", self._leave, add="+")
        widget.bind("<ButtonPress>", self._leave, add="+")

    def _enter(self, _):
        self.job = self.w.after(550, self._show)

    def _leave(self, _):
        if self.job:
            self.w.after_cancel(self.job)
            self.job = None
        if self.tip:
            self.tip.destroy()
            self.tip = None

    def _show(self):
        p = self.app.pal
        self.tip = tk.Toplevel(self.w)
        self.tip.wm_overrideredirect(True)
        self.tip.attributes("-topmost", True)
        x = self.w.winfo_rootx() + 6
        y = self.w.winfo_rooty() + self.w.winfo_height() + 6
        self.tip.wm_geometry(f"+{x}+{y}")
        tk.Label(self.tip, text=self.text, justify="left", bg=p["tip_bg"], fg=p["tip_fg"],
                 font=self.app.f_small, padx=9, pady=6, wraplength=340).pack()


class Section(ttk.Frame):
    """Karte mit Überschrift, per Klick einklappbar."""

    def __init__(self, parent, app, key, title, collapsed=False):
        super().__init__(parent, style="Card.TFrame", padding=(14, 9, 14, 11))
        self.app, self.key, self.collapsed = app, key, collapsed
        head = ttk.Frame(self, style="Plain.Card.TFrame", cursor="hand2")
        head.pack(fill="x")
        self.lbl = ttk.Label(head, text=title.upper(), style="Section.TLabel", cursor="hand2")
        self.lbl.pack(side="left")
        self.chev = ttk.Label(head, style="Chevron.TLabel", cursor="hand2")
        self.chev.pack(side="right")
        self.body = ttk.Frame(self, style="Plain.Card.TFrame")
        for w in (head, self.lbl, self.chev):
            w.bind("<Button-1>", lambda e: self.toggle())
        self._apply()

    def toggle(self):
        self.collapsed = not self.collapsed
        self.app.cfg.setdefault("collapsed", {})[self.key] = self.collapsed
        self._apply()

    def _apply(self):
        self.chev.config(text="▸" if self.collapsed else "▾")
        if self.collapsed:
            self.body.pack_forget()
        else:
            self.body.pack(fill="x", pady=(8, 0))


class ScrollFrame(ttk.Frame):
    """Senkrecht scrollbarer Bereich; Scrollleiste nur, wenn nötig."""

    def __init__(self, parent, app, width):
        super().__init__(parent)
        self.app = app
        self.cv = tk.Canvas(self, highlightthickness=0, bd=0, width=width)
        self.sb = ttk.Scrollbar(self, orient="vertical", command=self.cv.yview, style="Thin.Vertical.TScrollbar")
        self.inner = ttk.Frame(self.cv)
        self.win = self.cv.create_window(0, 0, window=self.inner, anchor="nw")
        self.cv.configure(yscrollcommand=self.sb.set)
        self.cv.pack(side="left", fill="both", expand=True)
        self._sb_on = False
        self.inner.bind("<Configure>", self._update)
        self.cv.bind("<Configure>", self._update)
        app.bind_all("<MouseWheel>", self._wheel, add="+")
        app.bind_all("<Button-4>", self._wheel, add="+")
        app.bind_all("<Button-5>", self._wheel, add="+")

    def _update(self, _=None):
        self.cv.itemconfigure(self.win, width=self.cv.winfo_width())
        self.cv.configure(scrollregion=(0, 0, self.cv.winfo_width(), self.inner.winfo_reqheight()))
        need = self.inner.winfo_reqheight() > self.cv.winfo_height() + 1
        if need and not self._sb_on:
            self.sb.pack(side="right", fill="y")
        elif not need and self._sb_on:
            self.sb.pack_forget()
            self.cv.yview_moveto(0)
        self._sb_on = need

    def _wheel(self, e):
        if not self._sb_on:
            return
        w = self.winfo_containing(e.x_root, e.y_root)
        if w is None or not str(w).startswith(str(self)):
            return
        step = -1 if (getattr(e, "num", 0) == 4 or e.delta > 0) else 1
        self.cv.yview_scroll(step * 3, "units")


class App(tk.Tk):
    def __init__(self, start_file=None):
        enable_dpi_awareness()
        super().__init__()
        self.title(APP)
        self.geometry("1200x780")
        self.minsize(940, 600)
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
        self._rjob = None
        self._status_kind = "muted"
        self._pill_state = None
        self._closing = False
        self._uiq = queue.Queue()      # Aufträge aus Hintergrund-Threads für das Fenster
        self.busy = False
        self.seen = {}

        self._init_fonts()
        self.style = ttk.Style(self)
        self.style.theme_use("clam")
        self.pal = PALETTES[self._theme_name()]
        self._build()
        self._apply_theme()
        self._apply_cfg()
        self._bind_live_refresh()
        self._bind_keys()
        self.protocol("WM_DELETE_WINDOW", self._close)
        if start_file:
            self.open_file(start_file)
        self.inbox = queue.Queue()
        self.after(40, self._pump_ui)
        self.after(1500, self._poll)
        self.after(300, self._check_inbox)
        self.after(800, self._plotter_watch)

    # ------------------------------------------------------------ Design
    def _init_fonts(self):
        fams = set(tkfont.families(self))
        fam = next((f for f in ("Segoe UI", "Inter", "Helvetica Neue", "Arial", "DejaVu Sans") if f in fams),
                   "TkDefaultFont")
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont", "TkCaptionFont"):
            try:
                tkfont.nametofont(name, self).configure(family=fam, size=10)
            except tk.TclError:
                pass
        self.f_base = tkfont.Font(self, family=fam, size=10)
        self.f_bold = tkfont.Font(self, family=fam, size=10, weight="bold")
        self.f_small = tkfont.Font(self, family=fam, size=9)
        self.f_tiny = tkfont.Font(self, family=fam, size=8)
        self.f_section = tkfont.Font(self, family=fam, size=8, weight="bold")
        self.f_title = tkfont.Font(self, family=fam, size=13, weight="bold")
        self.f_big = tkfont.Font(self, family=fam, size=11, weight="bold")
        self.f_empty = tkfont.Font(self, family=fam, size=12)

    def _theme_name(self):
        t = self.cfg.get("theme", "auto")
        if t == "auto":
            return "dark" if windows_dark_mode() else "light"
        return t if t in PALETTES else "light"

    def cycle_theme(self):
        order = ["auto", "light", "dark"]
        cur = self.cfg.get("theme", "auto")
        self.cfg["theme"] = order[(order.index(cur) + 1) % 3] if cur in order else "auto"
        self.pal = PALETTES[self._theme_name()]
        self._apply_theme()
        self.draw()
        label = {"auto": "wie Windows", "light": "hell", "dark": "dunkel"}[self.cfg["theme"]]
        self.set_status(f"Darstellung: {label}")

    def _apply_theme(self):
        p, st = self.pal, self.style
        st.configure(".", background=p["bg"], foreground=p["text"], font=self.f_base,
                     bordercolor=p["border"], lightcolor=p["bg"], darkcolor=p["bg"],
                     troughcolor=p["surface2"], focuscolor=p["accent"],
                     selectbackground=p["accent"], selectforeground=p["accent_fg"],
                     fieldbackground=p["surface"], insertcolor=p["text"], relief="flat")
        st.configure("TFrame", background=p["bg"])
        st.configure("Header.TFrame", background=p["surface"])
        st.configure("Card.TFrame", background=p["surface"], bordercolor=p["border"],
                     lightcolor=p["surface"], darkcolor=p["surface"], relief="solid", borderwidth=1)
        st.configure("Plain.Card.TFrame", relief="flat", borderwidth=0)
        st.configure("Pill.TFrame", background=p["surface2"])
        st.configure("TLabel", background=p["bg"], foreground=p["text"])
        for name, bg in (("Card", p["surface"]), ("Header", p["surface"]), ("Pill", p["surface2"])):
            st.configure(f"{name}.TLabel", background=bg, foreground=p["text"])
            st.configure(f"{name}Muted.TLabel", background=bg, foreground=p["muted"], font=self.f_small)
        st.configure("Title.TLabel", background=p["surface"], foreground=p["text"], font=self.f_title)
        st.configure("Section.TLabel", background=p["surface"], foreground=p["muted"], font=self.f_section)
        st.configure("Chevron.TLabel", background=p["surface"], foreground=p["muted"], font=self.f_small)
        st.configure("File.TLabel", background=p["bg"], foreground=p["text"], font=self.f_bold)
        st.configure("Muted.TLabel", background=p["bg"], foreground=p["muted"], font=self.f_small)
        st.configure("Warn.TLabel", background=p["warn_bg"], foreground=p["warn"], font=self.f_small,
                     padding=(8, 2))
        st.configure("Status.TLabel", background=p["bg"], foreground=p["muted"], font=self.f_small)
        for k in ("ok", "err", "muted"):
            st.configure(f"Dot{k}.TLabel", background=p["bg"], foreground=p[k], font=self.f_small)
            st.configure(f"PillDot{k}.TLabel", background=p["surface2"], foreground=p[k], font=self.f_small)
        st.configure("PillDotwait.TLabel", background=p["surface2"], foreground=p["label"], font=self.f_small)

        # Buttons
        st.configure("TButton", background=p["surface2"], foreground=p["text"], bordercolor=p["border"],
                     lightcolor=p["surface2"], darkcolor=p["surface2"], padding=(10, 6), relief="flat",
                     focusthickness=0, anchor="center")
        st.map("TButton",
               background=[("disabled", p["surface2"]), ("pressed", p["border"]), ("active", p["hover"])],
               lightcolor=[("pressed", p["border"]), ("active", p["hover"])],
               darkcolor=[("pressed", p["border"]), ("active", p["hover"])],
               foreground=[("disabled", p["muted"])])
        st.configure("Accent.TButton", background=p["accent"], foreground=p["accent_fg"],
                     bordercolor=p["accent"], lightcolor=p["accent"], darkcolor=p["accent"],
                     font=self.f_big, padding=(12, 11))
        st.map("Accent.TButton",
               background=[("disabled", p["border"]), ("pressed", p["accent_hover"]), ("active", p["accent_hover"])],
               lightcolor=[("active", p["accent_hover"])], darkcolor=[("active", p["accent_hover"])],
               bordercolor=[("active", p["accent_hover"])], foreground=[("disabled", p["muted"])])
        st.configure("Soft.TButton", background=p["accent_soft"], foreground=p["accent"],
                     bordercolor=p["accent_soft"], lightcolor=p["accent_soft"], darkcolor=p["accent_soft"],
                     font=self.f_bold, padding=(10, 8))
        st.map("Soft.TButton", background=[("disabled", p["surface2"]), ("active", p["hover"])],
               lightcolor=[("active", p["hover"])], darkcolor=[("active", p["hover"])],
               bordercolor=[("active", p["hover"])], foreground=[("disabled", p["muted"])])
        st.configure("Ghost.TButton", background=p["surface"], foreground=p["muted"],
                     bordercolor=p["surface"], lightcolor=p["surface"], darkcolor=p["surface"], padding=(8, 4),
                     font=self.f_small)
        st.map("Ghost.TButton", background=[("active", p["surface2"])], foreground=[("active", p["text"])],
               lightcolor=[("active", p["surface2"])], darkcolor=[("active", p["surface2"])],
               bordercolor=[("active", p["surface2"])])
        st.configure("Icon.TButton", background=p["surface"], foreground=p["muted"], bordercolor=p["surface"],
                     lightcolor=p["surface"], darkcolor=p["surface"], padding=(8, 4), font=self.f_big)
        st.map("Icon.TButton", background=[("active", p["surface2"])], foreground=[("active", p["text"])],
               lightcolor=[("active", p["surface2"])], darkcolor=[("active", p["surface2"])],
               bordercolor=[("active", p["surface2"])])

        # Segmentschalter (Drehen, Werkzeug)
        st.configure("Seg.Toolbutton", background=p["surface2"], foreground=p["text"], bordercolor=p["border"],
                     lightcolor=p["surface2"], darkcolor=p["surface2"], padding=(9, 5), anchor="center",
                     font=self.f_small)
        st.map("Seg.Toolbutton",
               background=[("selected", p["accent"]), ("active", p["hover"])],
               foreground=[("selected", p["accent_fg"])],
               lightcolor=[("selected", p["accent"]), ("active", p["hover"])],
               darkcolor=[("selected", p["accent"]), ("active", p["hover"])],
               bordercolor=[("selected", p["accent"])])

        # Eingabefelder
        st.configure("TEntry", fieldbackground=p["surface"], foreground=p["text"], bordercolor=p["border"],
                     lightcolor=p["surface"], darkcolor=p["surface"], padding=(6, 4), insertcolor=p["text"])
        st.map("TEntry", bordercolor=[("focus", p["accent"])], lightcolor=[("focus", p["accent"])],
               darkcolor=[("focus", p["accent"])])

        # Häkchen
        for name, bg in (("TCheckbutton", p["surface"]), ("Bar.TCheckbutton", p["surface"])):
            st.configure(name, background=bg, foreground=p["text"], indicatorbackground=p["surface"],
                         indicatorforeground=p["accent_fg"], indicatorrelief="flat", indicatormargin=(0, 0, 6, 0),
                         upperbordercolor=p["border"], lowerbordercolor=p["border"], focusthickness=0)
            st.map(name, indicatorbackground=[("selected", p["accent"]), ("active", p["hover"])],
                   background=[("active", bg)],
                   upperbordercolor=[("selected", p["accent"])], lowerbordercolor=[("selected", p["accent"])])

        st.configure("Thin.Vertical.TScrollbar", background=p["sel"], troughcolor=p["bg"],
                     bordercolor=p["bg"], lightcolor=p["sel"], darkcolor=p["sel"], arrowsize=0, width=8)
        st.map("Thin.Vertical.TScrollbar", background=[("active", p["muted"])])
        st.configure("TSeparator", background=p["border"])
        st.configure("Vert.TSeparator", background=p["border"])

        self.configure(background=p["bg"])
        for cv, key in getattr(self, "_tk_bg", []):
            cv.configure(background=p[key])
        self._draw_logo()
        self._restyle_status()
        if self._pill_state is not None:
            self._set_pill(*self._pill_state)
        self._set_window_icon()
        self._dark_titlebar(self._theme_name() == "dark")

    def _dark_titlebar(self, on):
        if os.name != "nt":
            return
        try:
            import ctypes
            self.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            val = ctypes.c_int(1 if on else 0)
            for attr in (20, 19):          # DWMWA_USE_IMMERSIVE_DARK_MODE (neu / alt)
                if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(val), 4) == 0:
                    break
        except (AttributeError, OSError):
            pass

    def _set_window_icon(self):
        """Programm-Symbol: blaues Quadrat mit Messer."""
        try:
            n = 32
            img = tk.PhotoImage(master=self, width=n, height=n)
            acc = self.pal["accent"]
            rows = []
            for y in range(n):
                row = []
                for x in range(n):
                    cx = min(max(x, 7), n - 8)
                    cy = min(max(y, 7), n - 8)
                    inside = (x - cx) ** 2 + (y - cy) ** 2 <= 49
                    # Messerklinge: dicke Diagonale + Spitze
                    t = max(0.0, min(1.0, ((x - 9) * 14 + (9 - (n - 1 - y)) * -14) / (2 * 14 * 14)))
                    px, py = 9 + 14 * t, (n - 10) - 14 * t
                    blade = (x - px) ** 2 + (y - py) ** 2 <= 4.5
                    row.append("#ffffff" if inside and blade else acc if inside else "#000000")
                rows.append("{" + " ".join(row) + "}")
            img.put(" ".join(rows))
            for y in range(n):
                for x in range(n):
                    cx = min(max(x, 7), n - 8)
                    cy = min(max(y, 7), n - 8)
                    if (x - cx) ** 2 + (y - cy) ** 2 > 49:
                        img.transparency_set(x, y, True)
            self._icon = img
            self.iconphoto(True, img)
        except (tk.TclError, AttributeError):
            pass

    def _draw_logo(self):
        cv = getattr(self, "logo", None)
        if cv is None:
            return
        p = self.pal
        cv.delete("all")
        cv.configure(background=p["surface"])
        r, w = 8, 30
        pts = [r, 0, w - r, 0, w, 0, w, r, w, w - r, w, w, w - r, w, r, w, 0, w, 0, w - r, 0, r, 0, 0]
        cv.create_polygon(pts, smooth=True, fill=p["accent"], outline="")
        cv.create_line(9, 21, 21, 9, fill="#ffffff", width=3, capstyle="round")
        cv.create_line(8, 23, 13, 23, fill="#ffffff", width=2, capstyle="round")

    # ------------------------------------------------------------------ UI
    def _field(self, parent, row, col, label, var, unit="", width=7, tip=None):
        ttk.Label(parent, text=label, style="Card.TLabel").grid(row=row, column=col, sticky="w", pady=3,
                                                              padx=(0 if col == 0 else 12, 8))
        box = ttk.Frame(parent, style="Plain.Card.TFrame", padding=0)
        box.grid(row=row, column=col + 1, sticky="w")
        e = ttk.Entry(box, textvariable=var, width=width, justify="right")
        e.pack(side="left")
        if unit:
            ttk.Label(box, text=unit, style="CardMuted.TLabel").pack(side="left", padx=(4, 0))
        e.bind("<Return>", lambda ev: self.refresh())
        e.bind("<FocusOut>", lambda ev: self.refresh())
        if tip:
            Tooltip(self, e, tip)
        return e

    def _build(self):
        self._tk_bg = []
        self.columnconfigure(1, weight=1)
        self.rowconfigure(2, weight=1)

        # ---------------- Kopfzeile
        hdr = ttk.Frame(self, style="Header.TFrame", padding=(16, 10, 12, 10))
        hdr.grid(row=0, column=0, columnspan=2, sticky="ew")
        self.logo = tk.Canvas(hdr, width=30, height=30, highlightthickness=0, bd=0)
        self.logo.pack(side="left")
        tbox = ttk.Frame(hdr, style="Header.TFrame")
        tbox.pack(side="left", padx=(10, 0))
        ttk.Label(tbox, text=APP, style="Title.TLabel").pack(anchor="w")
        ttk.Label(tbox, text="SVG direkt an den Schneideplotter", style="HeaderMuted.TLabel").pack(anchor="w")
        btn_theme = ttk.Button(hdr, text="◐", style="Icon.TButton", width=3, command=self.cycle_theme)
        btn_theme.pack(side="right")
        Tooltip(self, btn_theme, "Darstellung umschalten: wie Windows → hell → dunkel")
        pill = ttk.Frame(hdr, style="Pill.TFrame", padding=(10, 5), cursor="hand2")
        pill.pack(side="right", padx=(0, 10))
        self.pill_dot = ttk.Label(pill, text="●", style="PillDotwait.TLabel", cursor="hand2")
        self.pill_dot.pack(side="left")
        self.pill_txt = ttk.Label(pill, text="Plotter wird geprüft …", style="Pill.TLabel", cursor="hand2")
        self.pill_txt.pack(side="left", padx=(6, 0))
        self.pill_ip = ttk.Label(pill, text="", style="PillMuted.TLabel", cursor="hand2")
        self.pill_ip.pack(side="left", padx=(8, 0))
        for w in (pill, self.pill_dot, self.pill_txt, self.pill_ip):
            w.bind("<Button-1>", lambda e: self._probe_now())
        Tooltip(self, pill, "Erreichbarkeit des Plotters (wird alle 20 s geprüft, Klick = jetzt prüfen)")
        ttk.Separator(self, orient="horizontal").grid(row=1, column=0, columnspan=2, sticky="ew")

        # ---------------- Seitenleiste
        sidewrap = ttk.Frame(self, padding=(12, 12, 6, 12))
        sidewrap.grid(row=2, column=0, sticky="ns")
        sidewrap.rowconfigure(0, weight=1)
        self.sidescroll = ScrollFrame(sidewrap, self, width=296)
        self.sidescroll.grid(row=0, column=0, sticky="ns")
        self._tk_bg.append((self.sidescroll.cv, "bg"))
        side = self.sidescroll.inner
        coll = self.cfg.get("collapsed", {})

        # Motiv
        sec = Section(side, self, "source", "Motiv", coll.get("source", False))
        sec.pack(fill="x", pady=(0, 6))
        b = sec.body
        b.columnconfigure(0, weight=1)
        b.columnconfigure(1, weight=1)
        self.btn_vc = ttk.Button(b, text="↓  VectorCraft", style="Soft.TButton", command=self.from_vectorcraft)
        self.btn_vc.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        Tooltip(self, self.btn_vc, "Aktuelle Zeichnung aus VectorCraft holen\n"
                                   "(VectorCraft muss mit Fernsteuerung laufen)")
        self.btn_ai = ttk.Button(b, text="↓  Illustrator", style="Soft.TButton", command=self.from_illustrator)
        self.btn_ai.grid(row=0, column=1, sticky="ew", padx=(4, 0))
        Tooltip(self, self.btn_ai, "Aktuelles Dokument aus dem laufenden Adobe Illustrator holen")
        self.v_vcsel = tk.BooleanVar()
        ttk.Checkbutton(b, text="nur Auswahl übernehmen", variable=self.v_vcsel).grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(6, 6))
        vcrow = ttk.Frame(b, style="Plain.Card.TFrame")
        vcrow.grid(row=2, column=0, columnspan=2, sticky="ew")
        self.btn_vcstart = ttk.Button(vcrow, text="▶  VectorCraft starten", command=self.start_vectorcraft)
        self.btn_vcstart.pack(side="left", fill="x", expand=True)
        Tooltip(self, self.btn_vcstart, "Startet VectorCraft mit  --control 7979")
        b_vcpath = ttk.Button(vcrow, text="…", width=3, command=self.ask_vc_exe)
        b_vcpath.pack(side="left", padx=(4, 0))
        Tooltip(self, b_vcpath, "Pfad zu vectorcraft.exe wählen")
        orow = ttk.Frame(b, style="Plain.Card.TFrame")
        orow.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        orow.columnconfigure(0, weight=1)
        orow.columnconfigure(1, weight=1)
        b_open = ttk.Button(orow, text="SVG öffnen …", command=self.ask_open)
        b_open.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        Tooltip(self, b_open, "SVG-Datei öffnen (Strg+O)")
        ttk.Button(orow, text="Testquadrat", command=self.load_test).grid(row=0, column=1, sticky="ew", padx=(4, 0))

        # Lage
        sec = Section(side, self, "layout", "Lage", coll.get("layout", False))
        sec.pack(fill="x", pady=(0, 6))
        b = sec.body
        ttk.Label(b, text="Drehen", style="Card.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 6))
        self.v_rot = tk.StringVar()
        seg = ttk.Frame(b, style="Plain.Card.TFrame")
        seg.grid(row=0, column=1, columnspan=3, sticky="w", pady=(0, 6))
        for val in ("0", "90", "180", "270"):
            ttk.Radiobutton(seg, text=f"{val}°", value=val, variable=self.v_rot, style="Seg.Toolbutton",
                            command=self.refresh, width=4).pack(side="left")
        self.v_mirror = tk.BooleanVar()
        ttk.Checkbutton(b, text="Spiegeln (Flex-/Flockfolie)", variable=self.v_mirror,
                        command=self.refresh).grid(row=1, column=0, columnspan=4, sticky="w", pady=(0, 4))
        self.v_scale = tk.StringVar()
        self.v_x = tk.StringVar()
        self.v_y = tk.StringVar()
        self._field(b, 2, 0, "Größe", self.v_scale, "%", 6, "Skalierung in Prozent – oder in der Vorschau "
                                                            "an einer Ecke ziehen")
        self._field(b, 3, 0, "X", self.v_x, "mm", 5, "Versatz vom Nullpunkt in Vorschubrichtung – "
                                                      "oder das Motiv in der Vorschau ziehen")
        self._field(b, 3, 2, "Y", self.v_y, "mm", 5, "Versatz vom Nullpunkt quer zur Folie")

        # Folie
        sec = Section(side, self, "media", "Folie", coll.get("media", False))
        sec.pack(fill="x", pady=(0, 6))
        b = sec.body
        self.v_media = tk.StringVar()
        self.v_mlen = tk.StringVar()
        self.v_margin = tk.StringVar()
        self._field(b, 0, 0, "Breite", self.v_media, "mm", 5)
        self._field(b, 0, 2, "Länge", self.v_mlen, "mm", 5, "0 = Rollenware ohne Längenbegrenzung")
        self._field(b, 1, 0, "Rand", self.v_margin, "mm", 5, "Abstand zur Folienkante beim Einpassen")
        self.v_fitrot = tk.BooleanVar()
        ttk.Checkbutton(b, text="beim Einpassen drehen erlaubt", variable=self.v_fitrot).grid(
            row=3, column=0, columnspan=4, sticky="w", pady=(4, 8))
        frow = ttk.Frame(b, style="Plain.Card.TFrame")
        frow.grid(row=4, column=0, columnspan=4, sticky="ew")
        frow.columnconfigure(0, weight=1)
        frow.columnconfigure(1, weight=1)
        b.columnconfigure(3, weight=1)
        self.btn_query = ttk.Button(frow, text="Vom Plotter lesen", command=self.do_query)
        self.btn_query.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        Tooltip(self, self.btn_query, "Breite und Schneidelänge der eingelegten Folie abfragen")
        b_fit = ttk.Button(frow, text="Einpassen", command=self.do_fit)
        b_fit.grid(row=0, column=1, sticky="ew", padx=(4, 0))
        Tooltip(self, b_fit, "Motiv proportional so groß wie möglich auf die Folie setzen")

        # Einstellungen (Plotter, Überwachungsordner)
        sec = Section(side, self, "settings", "Einstellungen", coll.get("settings", True))
        sec.pack(fill="x", pady=(0, 6))
        b = sec.body
        self.v_host = tk.StringVar()
        self.v_port = tk.StringVar()
        self._field(b, 0, 0, "Plotter-IP", self.v_host, "", 14)
        self._field(b, 1, 0, "Port", self.v_port, "", 6)
        ttk.Separator(b).grid(row=2, column=0, columnspan=2, sticky="ew", pady=10)
        self.v_watch = tk.BooleanVar()
        ttk.Checkbutton(b, text="Überwachungsordner: neue SVGs laden", variable=self.v_watch,
                        command=self._watch_toggled).grid(row=3, column=0, columnspan=2, sticky="w")
        self.lbl_watch = ttk.Label(b, text="(kein Ordner)", style="CardMuted.TLabel", wraplength=250)
        self.lbl_watch.grid(row=4, column=0, columnspan=2, sticky="w", pady=(4, 6))
        ttk.Button(b, text="Ordner wählen …", command=self.ask_watch_dir).grid(row=5, column=0, columnspan=2,
                                                                             sticky="ew")

        # feste Aktionsleiste unten
        act = ttk.Frame(sidewrap, style="Card.TFrame", padding=(14, 12))
        act.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        self.btn_send = ttk.Button(act, text="An Plotter senden", style="Accent.TButton", command=self.do_send)
        self.btn_send.pack(fill="x")
        Tooltip(self, self.btn_send, "Schneiden (Strg+Enter) – vorher kommt eine Bestätigung")
        b_save = ttk.Button(act, text="HP-GL speichern …", style="Ghost.TButton", command=self.save_hpgl)
        b_save.pack(pady=(6, 0))
        Tooltip(self, b_save, "Schneidedaten als .plt-Datei speichern (Strg+S)")

        # ---------------- Hauptbereich
        main = ttk.Frame(self, padding=(6, 12, 14, 8))
        main.grid(row=2, column=1, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(2, weight=1)

        info = ttk.Frame(main)
        info.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self.lbl_file = ttk.Label(info, text="Kein Motiv geladen", style="File.TLabel")
        self.lbl_file.pack(side="left")
        self.lbl_dims = ttk.Label(info, text="", style="Muted.TLabel")
        self.lbl_dims.pack(side="left", padx=(12, 0))
        self.lbl_warn = ttk.Label(info, text="", style="Warn.TLabel")

        bar = ttk.Frame(main, style="Card.TFrame", padding=(12, 8))
        bar.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        groups = []

        def group():
            g = ttk.Frame(bar, style="Plain.Card.TFrame")
            sep = ttk.Separator(g, orient="vertical")
            g._sep = sep                                  # Trenner vor der Gruppe (nur mitten in der Zeile)
            groups.append(g)
            return g

        g = group()
        ttk.Label(g, text="ENTGITTERN", style="Section.TLabel").pack(side="left", padx=(0, 12))
        self.v_tool = tk.StringVar(value="select")
        for val, lab, tip in (("select", "Auswahl", "Motiv ziehen = verschieben, Ecke ziehen = Größe ändern"),
                              ("h", "+ waagerecht", "Klick in die Vorschau setzt eine waagerechte Entgitterlinie"),
                              ("v", "+ senkrecht", "Klick in die Vorschau setzt eine senkrechte Entgitterlinie")):
            rb = ttk.Radiobutton(g, text=lab, value=val, variable=self.v_tool, style="Seg.Toolbutton",
                                 command=self.draw)
            rb.pack(side="left")
            Tooltip(self, rb, tip)

        g = group()
        self.v_wbox = tk.BooleanVar()
        cb = ttk.Checkbutton(g, text="Rahmen", variable=self.v_wbox, command=self.refresh)
        cb.pack(side="left")
        Tooltip(self, cb, "Entgitterrahmen ums Motiv schneiden")
        self.v_wmargin = tk.StringVar()
        self.v_wgap = tk.StringVar()
        self.v_wgrid = tk.StringVar()
        for lab, var, tip in (("Abstand", self.v_wmargin, "Abstand des Rahmens zum Motiv"),
                              ("Lücke", self.v_wgap, "Abstand der Entgitterlinien zum Motiv – "
                                                     "das Motiv wird nie durchgeschnitten")):
            ttk.Label(g, text=lab, style="Card.TLabel").pack(side="left", padx=(12, 4))
            e = ttk.Entry(g, textvariable=var, width=4, justify="right")
            e.pack(side="left")
            ttk.Label(g, text="mm", style="CardMuted.TLabel").pack(side="left", padx=(3, 0))
            e.bind("<Return>", lambda ev: self.refresh())
            e.bind("<FocusOut>", lambda ev: self.refresh())
            Tooltip(self, e, tip)

        g = group()
        ttk.Label(g, text="Raster", style="Card.TLabel").pack(side="left", padx=(0, 4))
        e = ttk.Entry(g, textvariable=self.v_wgrid, width=4, justify="right")
        e.pack(side="left")
        ttk.Label(g, text="mm", style="CardMuted.TLabel").pack(side="left", padx=(3, 6))
        b_grid = ttk.Button(g, text="Setzen", command=self.weed_grid)
        b_grid.pack(side="left")
        Tooltip(self, b_grid, "Linien gleichmäßig im Rasterabstand verteilen")
        b_clear = ttk.Button(g, text="Löschen", style="Ghost.TButton", command=self.weed_clear)
        b_clear.pack(side="left", padx=(6, 0))
        Tooltip(self, b_clear, "Alle Entgitterlinien entfernen (einzelne: Rechtsklick auf die Linie)")
        self._bar_layout = None

        def relayout(_=None):
            avail = max(1, bar.winfo_width() - 24)
            sep_w = 25
            rows, cur, used = [], [], 0
            for gr in groups:
                w = gr.winfo_reqwidth() - (gr._sep.winfo_reqwidth() + 24 if gr._sep.winfo_ismapped() else 0)
                extra = sep_w if cur else 0
                if cur and used + extra + w > avail:
                    rows.append(cur)
                    cur, used, extra = [], 0, 0
                cur.append(gr)
                used += extra + w
            rows.append(cur)
            layout = tuple(tuple(id(gr) for gr in r) for r in rows)
            if layout == self._bar_layout:
                return
            self._bar_layout = layout
            for gr in groups:
                gr.grid_forget()
                gr._sep.pack_forget()
            for r, row in enumerate(rows):
                for c, gr in enumerate(row):
                    if c:
                        kids = [k for k in gr.winfo_children() if k is not gr._sep]
                        gr._sep.pack(side="left", fill="y", padx=12, before=kids[0])
                    gr.grid(row=r, column=c, sticky="w", pady=(0 if r == 0 else 8, 0))

        bar.bind("<Configure>", relayout)
        self.after(50, relayout)

        cvcard = ttk.Frame(main, style="Card.TFrame", padding=1)
        cvcard.grid(row=2, column=0, sticky="nsew")
        self.canvas = tk.Canvas(cvcard, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self._tk_bg.append((self.canvas, "cv_bg"))
        self.canvas.bind("<Configure>", lambda e: self.draw())
        self.canvas.bind("<Button-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Button-3>", self._on_delete)
        self.canvas.bind("<Motion>", self._on_hover)

        sbar = ttk.Frame(main)
        sbar.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        self.status_dot = ttk.Label(sbar, text="●", style="Dotmuted.TLabel")
        self.status_dot.pack(side="left")
        self.status = ttk.Label(sbar, text="Bereit.", style="Status.TLabel")
        self.status.pack(side="left", padx=(6, 0))
        ttk.Label(sbar, text="Strg+O öffnen · Strg+Enter senden", style="Muted.TLabel").pack(side="right")

    def _bind_live_refresh(self):
        for v in (self.v_scale, self.v_x, self.v_y, self.v_media, self.v_mlen, self.v_margin,
                  self.v_wmargin, self.v_wgap):
            v.trace_add("write", lambda *_: self._schedule_refresh())

    def _schedule_refresh(self):
        if self._drag:
            return                      # beim Ziehen wird ohnehin laufend neu gezeichnet
        if self._rjob:
            self.after_cancel(self._rjob)
        self._rjob = self.after(400, self._run_scheduled_refresh)

    def _run_scheduled_refresh(self):
        self._rjob = None
        self.refresh()

    def _bind_keys(self):
        self.bind_all("<Control-o>", lambda e: self.ask_open())
        self.bind_all("<Control-Return>", lambda e: self.do_send())
        self.bind_all("<Control-s>", lambda e: self.save_hpgl())

    # ------------------------------------------- Threads -> Oberfläche
    def _ui(self, fn):
        """Aus einem Hintergrund-Thread etwas im Fenster ausführen lassen (thread-sicher)."""
        self._uiq.put(fn)

    def _pump_ui(self):
        try:
            while True:
                fn = self._uiq.get_nowait()
                try:
                    fn()
                except Exception as e:  # noqa: BLE001 - ein Fehler darf die Schleife nicht stoppen
                    print("UI-Fehler:", e, file=sys.stderr)
        except queue.Empty:
            pass
        if not self._closing:
            self.after(40, self._pump_ui)

    # --------------------------------------------------- Plotter-Status
    def _plotter_watch(self):
        def work():
            while not self._closing:
                self._probe_once()
                for _ in range(40):            # 20 s
                    if self._closing or getattr(self, "_probe_flag", False):
                        break
                    time.sleep(0.5)
                self._probe_flag = False

        threading.Thread(target=work, daemon=True).start()

    def _probe_now(self):
        self._set_pill(None, self.cfg.get("host", ""))
        self._probe_flag = True

    def _probe_once(self):
        if self.busy:
            return
        host, port = self.cfg.get("host", gt.DEFAULT_HOST), int(self.cfg.get("port", gt.DEFAULT_PORT))
        try:
            socket.create_connection((host, port), timeout=1.5).close()
            ok = True
        except OSError:
            ok = False
        self._ui(lambda: self._set_pill(ok, host))

    def _set_pill(self, ok, host):
        self._pill_state = (ok, host)
        if ok is None:
            self.pill_dot.configure(style="PillDotwait.TLabel")
            self.pill_txt.configure(text="prüfe …")
        elif ok:
            self.pill_dot.configure(style="PillDotok.TLabel")
            self.pill_txt.configure(text="Plotter bereit")
        else:
            self.pill_dot.configure(style="PillDoterr.TLabel")
            self.pill_txt.configure(text="Plotter nicht erreichbar")
        self.pill_ip.configure(text=host)

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
        self._status_kind = STATUS_KIND.get(color, "muted")
        self.status.config(text=text)
        self._restyle_status()

    def _restyle_status(self):
        if not hasattr(self, "status"):
            return
        k = self._status_kind
        self.status_dot.configure(style=f"Dot{k}.TLabel")
        p = self.pal
        self.style.configure("Status.TLabel", foreground=p["text"] if k != "muted" else p["muted"])

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
            self._ui(lambda: (
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
                self._ui(lambda: self._got_vc(dst, title, sel))
            except OSError as e:
                err = str(e)
                self._ui(lambda: (self.set_status("Holen aus VectorCraft fehlgeschlagen.", "#b00"),
                                       messagebox.showerror(APP, f"Aus VectorCraft holen fehlgeschlagen:\n{err}\n\n"
                                                                 "VectorCraft muss mit  --control 7979  laufen.")))
            finally:
                self._ui(lambda: (setattr(self, "busy", False), self.btn_vc.state(["!disabled"])))

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
                self._ui(lambda: self._got_app_file(dst, title, used_sel, "Illustrator"))
            except OSError as e:
                err = str(e)
                self._ui(lambda: (self.set_status("Holen aus Illustrator fehlgeschlagen.", "#b00"),
                                       messagebox.showerror(APP, f"Aus Illustrator holen fehlgeschlagen:\n{err}")))
            finally:
                self._ui(lambda: (setattr(self, "busy", False), self.btn_ai.state(["!disabled"])))

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
            self._set_info(None)
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
            warn = f"⚠  breiter als die Folie ({y1:.0f} > {c['media_width']:g} mm)"
        elif c["media_length"] and x1 > c["media_length"] + 0.01:
            warn = f"⚠  länger als die Folie ({x1:.0f} > {c['media_length']:g} mm)"
        dims = (f"{x1 - x0:.1f} × {y1 - y0:.1f} mm  ·  {len(self.placed)} Linienzüge"
                + (f"  ·  {len(self.weed_h) + len(self.weed_v)} Entgitterlinien" if self.area else ""))
        self._set_info(self.src_name, dims, warn)
        self.draw()

    def _set_info(self, name, dims="", warn=""):
        self.lbl_file.configure(text=name or "Kein Motiv geladen")
        self.lbl_dims.configure(text=dims)
        if warn:
            self.lbl_warn.configure(text=warn)
            self.lbl_warn.pack(side="left", padx=(12, 0))
        else:
            self.lbl_warn.pack_forget()

    def draw(self):
        cv = self.canvas
        cv.delete("all")
        W, H = cv.winfo_width(), cv.winfo_height()
        if W < 50 or H < 50:
            return
        p = self.pal
        padl, padb, padt, padr = 44, 34, 26, 18
        media = self.cfg.get("media_width", 600.0) or 600.0
        mlen = self.cfg.get("media_length", 0.0) or 0.0
        if self.placed:
            _, _, x1, y1 = gt.bbox(self._all())
        else:
            x1, y1 = 100.0, 100.0
        # Plotter-X (Vorschub) nach rechts, Plotter-Y (quer) nach oben
        span_x = max(x1, mlen, 50.0) * 1.05
        span_y = max(y1, media, 50.0) * 1.04
        if self._view_lock:                  # während des Ziehens Maßstab festhalten
            span_x, span_y = self._view_lock
        self._view_spans = (span_x, span_y)
        s = min((W - padl - padr) / span_x, (H - padt - padb) / span_y)
        ox, oy = padl, H - padb
        self._xf = (ox, oy, s)

        def P(x, y):
            return ox + x * s, oy - y * s

        # Folie
        fx1 = P(mlen, 0)[0] if mlen else W - padr
        _, ym = P(0, media)
        ym = max(ym, padt / 2)
        cv.create_rectangle(ox, ym, fx1, oy, fill=p["media"], outline="")
        # mm-Raster: Schrittweite so wählen, dass Linien mind. ~14 px auseinander liegen
        step = next((st for st in (5, 10, 20, 50, 100, 200, 500) if st * s >= 14), 1000)
        major = step * (5 if step in (10, 100) else 2 if step in (5, 50, 500) else 5)
        vis_x = (fx1 - ox) / s
        vis_y = (oy - ym) / s
        n = int(vis_x // step)
        for i in range(1, n + 1):
            xx = i * step
            X = ox + xx * s
            is_major = abs(xx % major) < 1e-6
            cv.create_line(X, ym, X, oy, fill=p["grid2"] if is_major else p["grid"])
        n = int(vis_y // step)
        for i in range(1, n + 1):
            yy = i * step
            Y = oy - yy * s
            is_major = abs(yy % major) < 1e-6
            cv.create_line(ox, Y, fx1, Y, fill=p["grid2"] if is_major else p["grid"])
        cv.create_rectangle(ox, ym, fx1, oy, outline=p["media_line"])
        # Lineale (Beschriftung an den Hauptlinien)
        for i in range(0, int(vis_x // major) + 1):
            xx = i * major
            X = ox + xx * s
            if X > fx1 - 20:
                break
            cv.create_text(X, oy + 6, text=f"{xx:g}", anchor="n", fill=p["label"], font=self.f_tiny)
        for i in range(1, int(vis_y // major) + 1):
            yy = i * major
            Y = oy - yy * s
            if Y < ym + 8:
                break
            cv.create_text(ox - 6, Y, text=f"{yy:g}", anchor="e", fill=p["label"], font=self.f_tiny)
        cv.create_text(fx1, oy + 18, text="Vorschub →  mm", anchor="ne", fill=p["label"], font=self.f_tiny)
        label = f"Folie {media:g} mm" + (f" × {mlen:g} mm" if mlen else "  ·  Rolle")
        cv.create_text(fx1, ym - 5, text=label, anchor="se", fill=p["label"], font=self.f_tiny)
        # Rand
        m = self.cfg.get("margin", 0.0) or 0.0
        if m and m * 2 < media:
            mx1 = P(mlen - m, 0)[0] if mlen else fx1
            cv.create_rectangle(ox + m * s, ym + m * s, mx1, oy - m * s, outline=p["media_line"], dash=(2, 4))
        # Nullpunkt
        cv.create_oval(ox - 4, oy - 4, ox + 4, oy + 4, fill=p["origin"], outline=p["cv_bg"], width=2)

        if not self.placed:
            cx, cy = (ox + fx1) / 2, (ym + oy) / 2
            cv.create_text(cx, cy - 12, text="Noch kein Motiv", fill=p["muted"], font=self.f_empty)
            cv.create_text(cx, cy + 12, text="Aus VectorCraft oder Illustrator holen, SVG öffnen "
                                             "oder das Testquadrat laden", fill=p["label"], font=self.f_small)
            return
        # Hilfslinien (gestrichelt über die ganze Entgitterfläche)
        if self.area:
            X0, Y0, X1, Y1 = self.area
            for f in self.weed_h:
                y = Y0 + f * (Y1 - Y0)
                cv.create_line(*P(X0, y), *P(X1, y), fill=p["guide"], dash=(4, 3))
            for f in self.weed_v:
                x = X0 + f * (X1 - X0)
                cv.create_line(*P(x, Y0), *P(x, Y1), fill=p["guide"], dash=(4, 3))
        for group, color, width in ((self.placed, p["ink"], 1.5), (self.wlines, p["weed"], 2),
                                    (self.wframe, p["weed"], 1.5)):
            for poly in group:
                pts = []
                for x, y in poly:
                    pts.extend(P(x, y))
                if len(pts) >= 4:
                    cv.create_line(*pts, fill=color, width=width, joinstyle="round", capstyle="round")
        # Anfasser: Rahmen ums Motiv, Ecken zum Skalieren
        if self.v_tool.get() == "select":
            c = self._corners_px()
            xs, ys = [q[1] for q in c], [q[2] for q in c]
            cv.create_rectangle(min(xs), min(ys), max(xs), max(ys), outline=p["accent"], dash=(3, 3))
            for _, px, py in c:
                cv.create_oval(px - 5, py - 5, px + 5, py + 5, fill=p["handle"], outline=p["accent"], width=2)

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
                self._ui(lambda: self._got_media(ml, mw))
            except OSError as e:
                err = str(e)
                self._ui(lambda: (self.set_status("Abfrage fehlgeschlagen.", "#b00"),
                                       messagebox.showerror(APP, f"Folienmaß nicht abfragbar:\n{err}\n\n"
                                                                 "Plotter an, Folie eingelegt und erfasst?")))
            finally:
                self._ui(lambda: (setattr(self, "busy", False), self.btn_query.state(["!disabled"])))

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
                self._ui(lambda: self.set_status(
                    f"Gesendet: {self.src_name} ({len(data)} Byte) – {time.strftime('%H:%M:%S')}", "#070"))
            except OSError as e:
                err = str(e)
                self._ui(lambda: (self.set_status("Senden fehlgeschlagen.", "#b00"),
                                       messagebox.showerror(APP, f"Senden fehlgeschlagen:\n{err}\n\n"
                                                                 "Plotter an und im Netz? IP/Port prüfen.")))
            finally:
                self._ui(self._done)

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
        self._closing = True
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
