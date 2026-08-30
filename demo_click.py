"""
Demo isolee pour voir Playwright cliquer, sans toucher a WikiMasters.

    python demo_click.py
"""

from playwright.sync_api import sync_playwright


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, channel="chrome")
        page = browser.new_page()

        page.goto("https://playwright.dev")
        print(f"Page chargee : {page.title()}")

        # Cible un lien par son role accessible + son texte visible.
        link = page.get_by_role("link", name="Get started")
        link.click()
        page.wait_for_load_state("networkidle")
        print(f"Apres clic : {page.url}")

        input("\nEntree pour fermer...")
        browser.close()


if __name__ == "__main__":
    main()
