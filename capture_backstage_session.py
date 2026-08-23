"""One-time local helper to securely capture a Backstage browser session.

The generated file is ignored by Git and must be stored only as an encrypted
GitHub Actions secret after base64 encoding.
"""

from pathlib import Path

from playwright.sync_api import sync_playwright


OUTPUT = Path(__file__).with_name("tiktok-storage-state.json")

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.goto("https://live-backstage.tiktok.com/")
    input("Sign in to LIVE Backstage in the browser, then return here and press Enter...")
    context.storage_state(path=OUTPUT)
    browser.close()

print(f"Saved the encrypted-session source file to {OUTPUT}")
print("Do not upload this file to GitHub. Add its base64 value as TIKTOK_STORAGE_STATE_B64 in GitHub Actions Secrets.")
