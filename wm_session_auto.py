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

WM_TEST_STATE_PATH (optionnelle) redirige la sauvegarde vers un autre
fichier que storage_state.json -- utile pour gerer plusieurs comptes de
test sans que l'un ecrase la session de l'autre.

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
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "https://www.wiki-masters.com"
SCREENSHOTS = Path("captures/screenshots")

# Repli si la case Cloudflare n'est pas cliquable via son DOM (constate le
# 30/08/2026 : la case vit dans une structure imbriquee -- frame.locator()
# et document.body.innerHTML renvoient vide -- qu'aucun selecteur
# structurel ne parvient a percer). Coordonnees fixes valables UNIQUEMENT
# parce que ce script force un viewport 1280x900 et que la page de login
# est une mise en page statique centree, verifiees stables sur plusieurs
# essais reels.
TURNSTILE_FALLBACK_POS = (510, 552)


def main():
    STATE = Path(os.environ.get("WM_TEST_STATE_PATH", "storage_state.json"))
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
        # (observe : ca marche la plupart du temps avec ces flags, mais pas
        # toujours -- constate le 30/08/2026 sur un 3e compte). Repli 1 :
        # clic sur la case via son DOM. Repli 2 (si le repli 1 ne trouve
        # rien -- la case peut vivre dans une structure imbriquee non
        # accessible) : clic par coordonnees fixes, verifie fonctionner de
        # facon fiable sur cette page (viewport et mise en page statiques).
        page.wait_for_timeout(3000)
        cf_frame = None
        for f in page.frames:
            if "challenges.cloudflare.com" in f.url:
                cf_frame = f
                break
        clicked = False
        if cf_frame is not None:
            try:
                checkbox = cf_frame.locator("input[type='checkbox']")
                if checkbox.count() > 0 and checkbox.first.is_visible():
                    checkbox.first.click(timeout=3000)
                    print("Case Cloudflare cliquee via son DOM.")
                    clicked = True
                    page.wait_for_timeout(2500)
            except Exception:
                pass
        if not clicked:
            print(f"Repli : clic par coordonnees {TURNSTILE_FALLBACK_POS}.")
            page.mouse.click(*TURNSTILE_FALLBACK_POS)
            page.wait_for_timeout(2500)

        try:
            page.get_by_role("button", name="Connexion", exact=True).click(timeout=8000)
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
            SCREENSHOTS.mkdir(parents=True, exist_ok=True)
            shot = SCREENSHOTS / f"login-fail-{time.strftime('%Y%m%d-%H%M%S')}.png"
            try:
                page.screenshot(path=str(shot))
                print(f"Capture d'ecran : {shot.resolve()}")
            except Exception:
                pass
            try:
                print("Termine la connexion a la main dans la fenetre Chrome, puis reviens ici.")
                input("Appuie sur Entree une fois connecte...")
                page = ctx.pages[-1]
                current = page.url
            except EOFError:
                print("(pas de terminal interactif ici -- pas d'attente manuelle possible)")
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
