"""
Connexion AUTOMATIQUE (email + mot de passe remplis par le script), pour le
COMPTE DE TEST uniquement. Contrairement a wm_session.py (qui garde
volontairement le principe "le mot de passe ne passe jamais par le script"
pour le compte principal), ce script est fait pour un compte jetable ou la
consequence d'une fuite des identifiants est nulle -- decision explicite de
l'utilisateur le 30/08/2026.

Les identifiants ne sont JAMAIS ecrits en dur ici : ils viennent des
variables d'environnement WM_TEST_EMAIL et WM_TEST_PASSWORD, fixees par toi
juste avant de lancer ce script (voir lancer_wikimasters.txt, non versionne).

Le formulaire de connexion est protege par un widget Cloudflare Turnstile
(verifie le 30/08/2026) : avec les memes flags anti-detection que
wm_session.py, il se resout automatiquement ("Succes !") sans clic requis,
la plupart du temps. Si ce n'est pas le cas (Turnstile plus mefiant, IP
differente, etc.), le script bascule sur le meme repli manuel que
wm_session.py : la fenetre reste ouverte, tu termines toi-meme.

    python wm_session_auto.py
"""

import os
import stat
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "https://www.wiki-masters.com"
STATE = Path("storage_state.json")


def main():
    email = os.environ.get("WM_TEST_EMAIL")
    password = os.environ.get("WM_TEST_PASSWORD")
    if not email or not password:
        raise SystemExit(
            "WM_TEST_EMAIL et/ou WM_TEST_PASSWORD non definis dans l'environnement. "
            "Voir lancer_wikimasters.txt pour la commande complete."
        )

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            channel="chrome",
            args=["--disable-blink-features=AutomationControlled"],
            ignore_default_args=["--enable-automation"],
        )
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        page = ctx.new_page()
        page.goto(f"{BASE}/login")
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass

        page.fill("#email", email)
        page.fill("#password", password)

        # Laisse le temps a Turnstile de se resoudre automatiquement
        # (observe : quelques secondes suffisent avec ces flags).
        page.wait_for_timeout(3000)

        try:
            page.get_by_role("button", name="Connexion", exact=True).click(timeout=5000)
        except Exception as e:
            print(f"Clic sur 'Connexion' impossible ({e.__class__.__name__}) — complete a la main.")

        try:
            page.wait_for_url(lambda url: "/login" not in url, timeout=10000)
        except Exception:
            pass

        page = ctx.pages[-1]
        current = page.url
        print(f"Onglet actif : {current}")

        if "/login" in current:
            print("\nToujours sur /login — la connexion automatique n'a pas abouti")
            print("(Turnstile a peut-etre demande une interaction, ou identifiants incorrects).")
            print("Termine la connexion a la main dans la fenetre Chrome, puis reviens ici.")
            input("Appuie sur Entree une fois connecte...")
            page = ctx.pages[-1]
            current = page.url
            if "/login" in current:
                print(f"\nToujours sur {current} — rien sauvegarde.")
                browser.close()
                sys.exit(1)

        ctx.storage_state(path=str(STATE))
        browser.close()

    os.chmod(STATE, stat.S_IRUSR | stat.S_IWUSR)
    print(f"\nSession sauvegardee dans {STATE.resolve()}.")


if __name__ == "__main__":
    main()
