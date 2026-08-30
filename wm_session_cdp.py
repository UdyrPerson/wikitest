"""
Repli pour wm_session.py quand Cloudflare bloque le Chrome pilote par
Playwright des le depart, meme sans les flags d'automatisation visibles :
la connexion CDP elle-meme (utilisee par Playwright pour piloter Chrome)
laisse une empreinte que certains challenges detectent.

Ici, Chrome est lance de facon totalement normale, a la main, hors
Playwright, avec juste le port de debogage distant ouvert. Au moment ou tu
passes le challenge Cloudflare et te connectes, rien ne distingue ce Chrome
d'un Chrome ordinaire. Playwright se branche seulement APRES, une fois que
tu es deja connecte, pour lire les cookies.

Etape 1 - lance Chrome (profil temporaire, separe de ton profil habituel) :

    & "C:\Program Files\Google\Chrome\Application\chrome.exe" `
        --remote-debugging-port=9222 `
        --user-data-dir="$env:TEMP\wm-chrome-profile" `
        https://www.wiki-masters.com/login

Etape 2 - connecte-toi a la main dans cette fenetre, navigue vers ta
collection pour verifier.

Etape 3 - lance ce script : il se branche sur ce Chrome deja ouvert et
sauvegarde storage_state.json.

    python wm_session_cdp.py
"""

import os
import stat
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

STATE = Path("storage_state.json")
CDP_URL = "http://localhost:9222"


def main():
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(CDP_URL)
        except Exception as e:
            raise SystemExit(
                f"Impossible de se connecter a {CDP_URL} : {e}\n"
                "Verifie que Chrome tourne bien avec --remote-debugging-port=9222 "
                "(voir le docstring de ce fichier pour la commande de lancement)."
            )

        if not browser.contexts:
            raise SystemExit("Aucun contexte trouve — Chrome vient-il d'etre lance ?")

        ctx = browser.contexts[0]
        if not ctx.pages:
            raise SystemExit("Aucun onglet ouvert dans ce Chrome.")

        page = ctx.pages[-1]
        print(f"Onglet actif : {page.url}")

        if "/login" in page.url:
            print("Toujours sur /login — connecte-toi dans Chrome avant de relancer ce script.")
            sys.exit(1)

        ctx.storage_state(path=str(STATE))
        # Pas de browser.close() ici : ce Chrome n'a pas ete lance par ce
        # script, on ne ferme que la connexion CDP en sortant du bloc "with".

    os.chmod(STATE, stat.S_IRUSR | stat.S_IWUSR)
    print(f"\nSession sauvegardee dans {STATE.resolve()} (chmod 600).")
    print("Tu peux fermer la fenetre Chrome ouverte pour l'occasion.")


if __name__ == "__main__":
    main()
