/*
  gtsend_illustrator.jsx  –  "An Plotter senden" für Adobe Illustrator

  Exportiert das aktive Dokument (oder nur die Auswahl, falls etwas markiert
  ist) als SVG mit Text in Pfaden und übergibt es an das Plotter-Tool
  (Plotter.bat / gtsend_gui.py im SELBEN Ordner wie dieses Skript).
  Das Dokument selbst wird nicht verändert.

  Aufruf in Illustrator:
    Datei > Skripten > Anderes Skript …  (Strg+F12)  -> diese Datei wählen
  Tipp: Über eine Aktion ("Menübefehl einfügen") lässt sich ein F-Tasten-
  Kürzel darauf legen.

  Das Plotter-Tool nutzt die Funktion gtsendExport() auch selbst über die
  Illustrator-Fernsteuerung (Knopf "Aus Illustrator holen").
*/

function gtsendSafe(s) {
    return String(s).replace(/\.[^.]*$/, "").replace(/[^A-Za-z0-9._-]+/g, "_") || "illustrator";
}

/* Exportiert nach `path`; selection: true = nur Auswahl, false = ganzes Dokument,
   "auto" = Auswahl falls vorhanden. Rückgabe: "OK|<pfad>|<titel>|<auswahl 0/1>" oder "ERR|<meldung>" */
function gtsendExport(path, selection) {
    try {
        if (app.documents.length === 0) return "ERR|In Illustrator ist kein Dokument geöffnet.";
        var doc = app.activeDocument;
        var title = doc.name;
        var hasSel = doc.selection && doc.selection.length > 0;
        var useSel = (selection === "auto") ? hasSel : !!selection;
        if (useSel && !hasSel) return "ERR|Nichts ausgewählt.";

        var opts = new ExportOptionsSVG();
        opts.fontType = SVGFontType.OUTLINEFONT;           // Text -> Pfade
        opts.embedRasterImages = false;
        opts.coordinatePrecision = 3;
        opts.documentEncoding = SVGDocumentEncoding.UTF8;
        opts.DTD = SVGDTDVersion.SVG1_1;
        opts.cssProperties = SVGCSSPropertyLocation.PRESENTATIONATTRIBUTES;
        try { opts.saveMultipleArtboards = false; } catch (e1) {}

        var oldLevel = app.userInteractionLevel;          // keine Dialoge, die den Export blockieren
        app.userInteractionLevel = UserInteractionLevel.DONTDISPLAYALERTS;
        try {
            return gtsendDoExport(doc, title, path, useSel, opts);
        } finally {
            app.userInteractionLevel = oldLevel;
        }
    } catch (e) {
        return "ERR|" + e.message + (e.line ? " (Zeile " + e.line + ")" : "");
    }
}

function gtsendDoExport(doc, title, path, useSel, opts) {
    var f = new File(path);
    if (f.parent && !f.parent.exists) f.parent.create();
    if (f.exists) f.remove();

    if (useSel) {
        // Auswahl in ein Hilfsdokument kopieren, dort exportieren, Hilfsdokument verwerfen
        var items = [];
        for (var i = 0; i < doc.selection.length; i++) items.push(doc.selection[i]);
        var tmp = app.documents.add(doc.documentColorSpace, doc.width, doc.height);
        try {
            var target = tmp.layers[0];
            for (var k = 0; k < items.length; k++) {
                items[k].duplicate(target, ElementPlacement.PLACEATEND);
            }
            tmp.artboards[0].artboardRect = tmp.visibleBounds;
            tmp.exportFile(f, ExportType.SVG, opts);
        } finally {
            tmp.close(SaveOptions.DONOTSAVECHANGES);
            try { app.activeDocument = doc; } catch (e2) {}
        }
    } else {
        doc.exportFile(f, ExportType.SVG, opts);
    }
    if (!f.exists) return "ERR|Export fehlgeschlagen: " + f.fsName;
    return "OK|" + f.fsName + "|" + title + "|" + (useSel ? "1" : "0");
}

/* Menü-Aufruf: exportieren und an das Plotter-Tool übergeben */
function gtsendMain() {
    var here = new File($.fileName).parent;
    // bevorzugt die EXE, sonst die Batch (Python-Variante)
    var bat = new File(here.fsName + "\\GraphtecSender.exe");
    if (!bat.exists) bat = new File(here.fsName + "\\Plotter.bat");
    if (!bat.exists) {
        alert("Weder GraphtecSender.exe noch Plotter.bat gefunden in:\n" + here.fsName +
              "\n\nDieses Skript muss im selben Ordner wie das Plotter-Tool liegen.");
        return;
    }
    if (app.documents.length === 0) { alert("Kein Dokument geöffnet."); return; }
    var doc = app.activeDocument;
    var sel = doc.selection && doc.selection.length > 0;
    var folder = Folder.temp.fsName + "\\gtsend";
    var out = folder + "\\ai_" + gtsendSafe(doc.name) + (sel ? "_auswahl" : "") + ".svg";
    var r = gtsendExport(out, "auto");
    if (r.indexOf("OK|") !== 0) { alert("An Plotter senden:\n" + r.substring(4)); return; }
    var svg = r.split("|")[1];

    // kleine Start-Batch schreiben und ausführen (übergibt die Datei an ein laufendes
    // Plotter-Fenster oder öffnet ein neues)
    var launcher = new File(folder + "\\_an_plotter.bat");
    launcher.encoding = "UTF-8";
    launcher.lineFeed = "Windows";
    launcher.open("w");
    launcher.writeln("rem gtsend");          // falls ein BOM geschrieben wird, trifft es diese Zeile
    launcher.writeln("@echo off");
    launcher.writeln("chcp 65001 >nul");
    if (/\.exe$/i.test(bat.fsName)) {
        launcher.writeln('start "" "' + bat.fsName + '" "' + svg + '"');
    } else {
        launcher.writeln('call "' + bat.fsName + '" "' + svg + '"');
    }
    launcher.close();
    launcher.execute();
}

if (typeof GTSEND_NO_MAIN === "undefined") {
    gtsendMain();
}
