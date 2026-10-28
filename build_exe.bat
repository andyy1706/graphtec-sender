@echo off
rem ==========================================================================
rem  build_exe.bat - baut GraphtecSender.exe aus gtsend_gui.py (PyInstaller)
rem
rem  Einfach im Plotter-Ordner doppelklicken. Ergebnis im Unterordner "dist":
rem      dist\GraphtecSender.exe
rem      dist\gtsend_illustrator.jsx   (Illustrator-Skript, gehoert neben die EXE)
rem
rem  Baut ohne Admin-Rechte. Reihenfolge der Suche:
rem    1. vorhandenes pyinstaller.exe im PATH
rem    2. Python-Launcher "py"
rem    3. Python unter %LOCALAPPDATA%\Programs\Python  (nicht das von Inkscape o. a.)
rem ==========================================================================
setlocal EnableDelayedExpansion
set "DIR=%~dp0"
set "WORK=%TEMP%\gtsend_build"
cd /d "%DIR%"

set "PYI="
set "PY="

rem --- 1. pyinstaller.exe direkt -------------------------------------------
for /f "delims=" %%P in ('where pyinstaller.exe 2^>nul') do (
    if not defined PYI set "PYI=%%P"
)
if defined PYI (
    echo PyInstaller: !PYI!
    goto build
)

rem --- 2. py-Launcher ---------------------------------------------------------
where py >nul 2>&1 && set "PY=py"

rem --- 3. Python-Installation des Benutzers (neueste zuerst) -----------------
if not defined PY (
    for /f "delims=" %%D in ('dir /b /ad /o-n "%LOCALAPPDATA%\Programs\Python\Python3*" 2^>nul') do (
        if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\%%D\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\%%D\python.exe"
    )
)
if not defined PY (
    echo Kein passendes Python gefunden ^(weder pyinstaller.exe, py noch %%LOCALAPPDATA%%\Programs\Python^).
    goto fail
)
echo Python: "!PY!"
"!PY!" --version

"!PY!" -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo Installiere PyInstaller fuer den aktuellen Benutzer ...
    "!PY!" -m pip install --user --upgrade pyinstaller
    if errorlevel 1 (
        echo.
        echo pip konnte PyInstaller nicht laden. Hinter einem Firmen-Proxy
        echo ^(z. B. Zscaler^) hilft meist, das Firmen-Stammzertifikat anzugeben:
        echo     "!PY!" -m pip config set global.cert "C:\Pfad\zum\Firmen-Root.pem"
        goto fail
    )
)

:build
echo.
echo Baue GraphtecSender.exe ...
if defined PY (
    "!PY!" -m PyInstaller --noconfirm --clean --onefile --windowed --name GraphtecSender --distpath "%DIR%dist" --workpath "%WORK%" --specpath "%WORK%" --icon "%DIR%assets\graphtec_sender.ico" --add-data "%DIR%assets;assets" --hidden-import gtsend --paths "%DIR%." "%DIR%gtsend_gui.py"
) else (
    "!PYI!" --noconfirm --clean --onefile --windowed --name GraphtecSender --distpath "%DIR%dist" --workpath "%WORK%" --specpath "%WORK%" --icon "%DIR%assets\graphtec_sender.ico" --add-data "%DIR%assets;assets" --hidden-import gtsend --paths "%DIR%." "%DIR%gtsend_gui.py"
)
if errorlevel 1 goto fail
if not exist "%DIR%dist\GraphtecSender.exe" goto fail

copy /y "%DIR%gtsend_illustrator.jsx" "%DIR%dist\" >nul

echo.
echo ==========================================================
echo  Fertig:  %DIR%dist\GraphtecSender.exe
echo           %DIR%dist\gtsend_illustrator.jsx
echo ==========================================================
explorer "%DIR%dist"
pause
exit /b 0

:fail
echo.
echo Build abgebrochen.
pause
exit /b 1
