import re
from playwright.sync_api import Playwright, sync_playwright, expect


def run(playwright: Playwright) -> None:
    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context(storage_state="auth.json")
    page = context.new_page()
    page.close()

    # ---------------------
    context.storage_state(path="auth.json")
    context.close()
    browser.close()


with sync_playwright() as playwright:
    run(playwright)
