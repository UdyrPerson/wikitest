"""
Ouvre une fenetre Chrome persistante par compte connu du projet, chacune
sur son propre port CDP, cote a cote. Reutilise attach_chrome()/get_page()
de wm_open_booster.py (generalises pour accepter un port et un fichier de
session en parametre).

Comme les autres fenetres persistantes du projet : lancees en processus
independant (detach), jamais fermees par ce script -- ferme-les toi-meme
quand tu veux.

    python wm_open_all_sessions.py
"""

import time
from pathlib import Path

from playwright.sync_api import sync_playwright

import wm_open_booster as booster

# (label, port CDP dedie, fichier de session)
ACCOUNTS = [
    ("compte test 1 (compte 1)", 9224, Path("storage_state.json")),
    ("compte test 2 (compte 2)", 9225, Path("storage_state_test2.json")),
    ("collecteur", 9226, Path("storage_state_test3.json")),
]


def main():
    missing = [str(state) for _, _, state in ACCOUNTS if not state.exists()]
    if missing:
        raise SystemExit(f"Fichier(s) de session manquant(s) : {', '.join(missing)}")

    with sync_playwright() as p:
        for label, port, state_path in ACCOUNTS:
            print(f"\n--- {label} (port {port}) ---")
            browser = booster.attach_chrome(p, port=port)
            ctx, page = booster.get_page(browser, state_path=state_path)

            if "/pulls" not in page.url:
                page.goto(f"{booster.BASE}/pulls")
                try:
                    page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    pass

            if "/login" in page.url:
                print(f"  Redirige vers /login — session expiree pour {label}.")
                continue

            print(f"  Fenetre prete sur {page.url}")
            time.sleep(1.0)  # laisse chaque Chrome demarrer avant le suivant

    print("\nLes 3 fenetres restent ouvertes.")


if __name__ == "__main__":
    main()
