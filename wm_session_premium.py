"""
Session du COMPTE PRINCIPAL (premium), en deux temps.

Contrairement aux comptes de test (wm_session_auto.py), le mot de passe du
compte principal ne passe JAMAIS par un script -- principe pose des le
debut du projet et conserve ici. Tu te connectes toi-meme dans la fenetre.

Deux temps, parce qu'attendre un "appuie sur Entree" ne marche pas quand le
script est lance par un agent sans terminal interactif : la fenetre est
lancee en processus independant (comme wm_open_booster.py), elle survit a
la fin du script, et un second appel vient recuperer les cookies.

    python wm_session_premium.py            # 1) ouvre la fenetre sur /login
    python wm_session_premium.py --save     # 2) une fois connecte, sauvegarde

La session va dans storage_state_premium.json, JAMAIS dans
storage_state.json : ce dernier porte le compte de test 1, et l'ecraser
casserait l'automatisation GitHub Actions.
"""

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

import wm_open_booster as booster

BASE = "https://www.wiki-masters.com"
PREMIUM_STATE = Path("storage_state_premium.json")

# Port dedie, distinct de ceux des comptes de test (9224-9228) pour que la
# fenetre premium ne soit jamais confondue avec les leurs.
PREMIUM_PORT = 9230


def open_window():
    booster.launch_independent_chrome(PREMIUM_PORT)
    with sync_playwright() as p:
        browser = booster.connect_with_retry(p, PREMIUM_PORT)
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(f"{BASE}/login")
        print(f"Fenetre ouverte sur {page.url} (port CDP {PREMIUM_PORT}).")
        print("Connecte-toi a la main, puis relance avec --save.")


def save_session():
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(f"http://localhost:{PREMIUM_PORT}")
        except Exception:
            raise SystemExit(
                f"Aucune fenetre sur le port {PREMIUM_PORT} — relance sans --save d'abord."
            )
        ctx = browser.contexts[0]
        page = None
        for pg in ctx.pages:
            if "wiki-masters.com" in pg.url:
                page = pg
                break
        if page is None:
            raise SystemExit("Aucun onglet WikiMasters dans cette fenetre.")
        if "/login" in page.url:
            raise SystemExit(f"Toujours sur {page.url} — la connexion n'est pas terminee.")

        ctx.storage_state(path=str(PREMIUM_STATE))
        print(f"Onglet : {page.url}")
        print(f"Session premium sauvegardee dans {PREMIUM_STATE.resolve()}.")


def main():
    if "--save" in sys.argv:
        save_session()
    else:
        open_window()


if __name__ == "__main__":
    main()
