@echo off
REM Startet Playwright Codegen fuer https://zeiterfassung.ibf-solutions.com
REM
REM - oeffnet Chromium + Code-Recorder daneben
REM - jeder Klick / jede Eingabe wird als Python-Code generiert
REM - beim Schliessen wird die Session in auth.json gespeichert
REM - Generierten Code aus dem Recorder-Fenster kopieren / in zeiterfassung_button.py einkleben
REM
REM Du machst:
REM   1. Login durchspielen (User + Passwort eingeben, abschicken)
REM   2. Den gewuenschten Knopf druecken
REM   3. Codegen-Browser schliessen
REM
REM Resultat:
REM   - auth.json mit gespeicherter Session
REM   - Code im Recorder-Fenster (Python-Skript)

cd /d "%~dp0"
echo.
echo Codegen mit gespeicherter Session startet.
echo.
echo Im Browser:
echo   1. URL https://zeiterfassung.ibf-solutions.com in die Adressleiste
echo   2. Du solltest direkt eingeloggt sein (auth.json wird geladen)
echo   3. Zum Knopf navigieren + druecken
echo   4. Browser-Fenster schliessen wenn fertig
echo.
echo Generierter Code landet in codegen-output.py
echo.
python -m playwright codegen ^
    --target=python ^
    --load-storage=auth.json ^
    --save-storage=auth.json ^
    --output=codegen-output.py
