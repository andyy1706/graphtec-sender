@echo off
rem ==========================================================================
rem  Plotter.bat - startet den Graphtec-Sender (gtsend_gui.py) ohne Konsole
rem
rem  Plotter.bat                      nur das Plotter-Tool
rem  Plotter.bat /vc                  Plotter-Tool + VectorCraft im Steuermodus
rem  Plotter.bat motiv.svg            Plotter-Tool mit dieser Datei
rem  (SVG-Datei auf die Batch ziehen funktioniert auch)
rem ==========================================================================
setlocal
set "DIR=%~dp0"
set "ARGS="

:args
if "%~1"=="" goto run
if /i "%~1"=="/vc" (
    set "ARGS=%ARGS% --start-vectorcraft"
) else (
    set "ARGS=%ARGS% "%~f1""
)
shift
goto args

:run
if not exist "%DIR%gtsend_gui.py" (
    echo gtsend_gui.py nicht gefunden in "%DIR%"
    pause
    exit /b 1
)

rem Python suchen: zuerst der Windows-Launcher (pyw), dann pythonw im PATH
where pyw >nul 2>&1
if %errorlevel%==0 (
    start "" /D "%DIR%" pyw "%DIR%gtsend_gui.py"%ARGS%
    exit /b 0
)
where pythonw >nul 2>&1
if %errorlevel%==0 (
    start "" /D "%DIR%" pythonw "%DIR%gtsend_gui.py"%ARGS%
    exit /b 0
)

echo Python wurde nicht gefunden (weder "pyw" noch "pythonw" im PATH).
echo Bitte Python installieren oder den Pfad in dieser Datei eintragen.
pause
exit /b 1
