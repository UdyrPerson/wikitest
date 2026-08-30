"""
Ouvre un vrai Chrome, te laisse te connecter a la main, et sauvegarde la session.

A lancer une seule fois, puis a relancer quand la session expire.
Ton mot de passe ne passe jamais par le script : tu le tapes dans le navigateur.

    python wm_session.py
"""

import os
import stat
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "https://www.wiki-masters.com"
STATE = Path("storage_state.json")


def main():
    with sync_playwright() as p:
        # channel="chrome" utilise ton Chrome installe plutot que le Chromium
        # embarque de Playwright. headless=False parce que tu dois voir le
        # formulaire pour t'y connecter.
        # Cloudflare fait echouer son challenge quand il detecte navigator.webdriver
        # ou le flag --enable-automation : on les retire pour que la connexion
        # manuelle (faite par toi, un humain) passe normalement.
        browser = p.chromium.launch(
            headless=False,
            channel="chrome",
            args=["--disable-blink-features=AutomationControlled"],
            ignore_default_args=["--enable-automation"],
        )
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        page = ctx.new_page()
        page.goto(f"{BASE}/login")

        print("Connecte-toi dans la fenetre Chrome qui vient de s'ouvrir.")
        print("Navigue ensuite une fois vers ta collection pour verifier que ca marche.")
        input("Puis reviens ici et appuie sur Entree...")

        # Si la connexion a ouvert un nouvel onglet (popup, redirection), "page"
        # peut encore pointer sur l'onglet d'origine reste sur /login pendant
        # que l'onglet visible a l'ecran est ailleurs. On prend le dernier onglet
        # ouvert du contexte, qui est celui que tu regardes.
        page = ctx.pages[-1]
        current = page.url
        print(f"Onglet actif : {current}")

        if "/login" in current:
            print(f"\nToujours sur {current} — la connexion n'a pas abouti. Rien sauvegarde.")
            browser.close()
            sys.exit(1)

        ctx.storage_state(path=str(STATE))
        browser.close()

    # Le fichier contient des cookies de session : lisible par toi seul.
    os.chmod(STATE, stat.S_IRUSR | stat.S_IWUSR)
    print(f"\nSession sauvegardee dans {STATE.resolve()} (chmod 600).")
    print("Ce fichier vaut un mot de passe. Il est deja dans le .gitignore.")


if __name__ == "__main__":
    main()
