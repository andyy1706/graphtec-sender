@echo off
rem ==========================================================================
rem  Plotter.bat - startet den Graphtec-Sender ohne Konsole
rem
rem  Plotter.bat                      nur das Plotter-Tool
rem  Plotter.bat /vc                  Plotter-Tool + VectorCraft im Steuermodus
rem  Plotter.bat motiv.svg            Plotter-Tool mit dieser Datei
rem  (SVG-Datei auf die Batch ziehen funktioniert auch)
rem
rem  Liegt GraphtecSender.exe im selben Ordner, wird die EXE gestartet,
rem  sonst gtsend_gui.py mit Python. Python-Suche: py-Launcher (pyw), dann
rem  %LOCALAPPDATA%\Programs\Python, zuletzt pythonw im PATH.
rem ==========================================================================
setlocal EnableDelayedExpansion
set "DIR=%~dp0"
set "ARGS="

:args
if "%~1"=="" goto run
if /i "%~1"=="/vc" (
    set "ARGS=!ARGS! --start-vectorcraft"
) else (
    set "ARGS=!ARGS! "%~f1""
)
shift
goto args

:run
rem --- EXE vorhanden? ---------------------------------------------------------
if exist "%DIR%GraphtecSender.exe" (
    start "" /D "%DIR%" "%DIR%GraphtecSender.exe"!ARGS!
    exit /b 0
)

if not exist "%DIR%gtsend_gui.py" (
    echo Weder GraphtecSender.exe noch gtsend_gui.py gefunden in "%DIR%"
    pause
    exit /b 1
)

rem --- Python suchen ----------------------------------------------------------
set "PYW="
where pyw >nul 2>&1 && set "PYW=pyw"
if not defined PYW (
    for /f "delims=" %%D in ('dir /b /ad /o-n "%LOCALAPPDATA%\Programs\Python\Python3*" 2^>nul') do (
        if not defined PYW if exist "%LOCALAPPDATA%\Programs\Python\%%D\pythonw.exe" set "PYW=%LOCALAPPDATA%\Programs\Python\%%D\pythonw.exe"
    )
)
if not defined PYW (
    for /f "delims=" %%P in ('where pythonw 2^>nul') do (
        if not defined PYW set "PYW=%%P"
    )
)
if not defined PYW (
    echo Python wurde nicht gefunden.
    echo Bitte Python installieren oder GraphtecSender.exe in diesen Ordner legen.
    pause
    exit /b 1
)

start "" /D "%DIR%" "!PYW!" "%DIR%gtsend_gui.py"!ARGS!
exit /b 0
