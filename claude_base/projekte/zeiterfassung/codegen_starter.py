"""Startet Chromium mit gespeicherter Session, navigiert auf Zeiterfassung,
und schaltet den Playwright-Recorder ein. Aktionen werden in codegen-output.py
geschrieben (über das interne Playwright-Recorder-Setup, das auch hinter
`playwright codegen` steckt).

Output:
- auth.json           Session-State (refresh nach jedem Lauf)
- codegen-output.py   generierter Python-Code aus den Klicks

Wenn fertig: Chromium schliessen (X).
"""
from pathlib import Path
import os

URL = "https://zeiterfassung.ibf-solutions.com"
HERE = Path(__file__).resolve().parent
AUTH = HERE / "auth.json"
OUT  = HERE / "codegen-output.py"

# Recorder via Env-Variable aktivieren (trick aus Playwright-internas):
# PLAYWRIGHT_BROWSERS_PATH ist hier nicht relevant; PWDEBUG=1 oeffnet Inspector
os.environ["PWDEBUG"] = "1"

from playwright.sync_api import sync_playwright


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=200)
        ctx_kwargs = {"ignore_https_errors": True, "record_video_dir": None}
        if AUTH.exists():
            ctx_kwargs["storage_state"] = str(AUTH)
            print(f"[INFO] Session aus {AUTH.name} geladen")
        ctx = browser.new_context(**ctx_kwargs)
        ctx.set_default_timeout(300000)
        ctx.set_default_navigation_timeout(300000)
        page = ctx.new_page()

        print(f"-> Navigiere zu {URL} (timeout 120s, wait_until=domcontentloaded)")
        try:
            page.goto(URL, wait_until="domcontentloaded", timeout=120000)
            print(f"   OK: {page.url}")
        except Exception as e:
            print(f"   [WARN] {type(e).__name__}: {e}")

        print()
        print("================================================================")
        print(" PWDEBUG=1 ist gesetzt -> Inspector mit RECORD-Button erscheint.")
        print(" Im Inspector:")
        print("   1. RECORD-Button (rotes Symbol) druecken um Aufnahme zu starten")
        print("   2. Klicke dich durch -> Knopf druecken")
        print("   3. Im Inspector-Fenster wird der Python-Code mitgeschrieben")
        print("   4. Code im Inspector kopieren ODER Browser schliessen")
        print("================================================================")

        try:
            page.pause()
        except Exception as e:
            print(f"[INFO] Pause beendet: {type(e).__name__}")

        # Sicherheitshalber Storage State erneut sichern
        try:
            ctx.storage_state(path=str(AUTH))
            print(f"[OK] Session aktualisiert: {AUTH}")
        except Exception as e:
            print(f"[WARN] Konnte Session nicht updaten: {e}")

        try:
            browser.close()
        except Exception:
            pass

    # Hinweis falls codegen-output.py noch alte Boilerplate enthaelt
    if OUT.exists():
        size = OUT.stat().st_size
        print(f"\ncodegen-output.py: {size} bytes")
        if size < 500:
            print("(klein -- evtl. wurde RECORD nicht aktiviert; Code muss aus Inspector "
                  "manuell rauskopiert werden)")


if __name__ == "__main__":
    main()
