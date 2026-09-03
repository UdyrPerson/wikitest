"""
Ouvre une session sur N'IMPORTE QUEL compte, par connexion manuelle, en
deux temps. Generalisation de wm_session_premium.py (qui reste dedie au
compte principal et a son port 9230).

    python wm_session_window.py --state storage_state_5.json --port 9228
    # ... tu te connectes a la main dans la fenetre ...
    python wm_session_window.py --state storage_state_5.json --port 9228 --save --expect oursours

--expect verifie le pseudo REELLEMENT connecte avant d'enregistrer, et
supprime le fichier en cas d'ecart. A utiliser systematiquement des que la
session est destinee a un secret : le 03/09/2026, une session de tigrewiki
enregistree comme "compte 5" a ecrase celle d'oursours dans son secret,
sans retour possible.

Pourquoi la connexion manuelle plutot que wm_session_auto.py : le
formulaire est protege par Cloudflare Turnstile. La connexion automatisee
depuis un runner GitHub echouait systematiquement dessus -- c'est la raison
pour laquelle refresh-sessions.yml a ete supprime (commit 423102a). En
local, dans un vrai Chrome, tu passes le challenge toi-meme.

Pourquoi en deux temps : attendre un "appuie sur Entree" ne marche pas
quand le script est lance par un agent sans terminal interactif. La fenetre
est donc lancee en processus independant, elle survit a la fin du script,
et un second appel vient recuperer les cookies.

ATTENTION -- une vraie connexion peut invalider la session Supabase deja
en cours pour ce compte, y compris celle stockee dans le secret GitHub
(constate lors de l'audit du commit 423102a). Apres avoir travaille avec
la session obtenue ici, repousse-la dans son secret pour que les workflows
repartent :

    gh secret set WM_TEST5_STORAGE_STATE --repo UdyrPerson/wikitest < storage_state_5.json
"""

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

import wm_open_booster as booster
from wm_session_io import identite

BASE = "https://www.wiki-masters.com"


def open_window(port: int):
    booster.launch_independent_chrome(port)
    with sync_playwright() as p:
        browser = booster.connect_with_retry(p, port)
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(f"{BASE}/login")
        print(f"Fenetre ouverte sur {page.url} (port CDP {port}).")
        print("Connecte-toi a la main, puis relance avec --save.")


def save_session(port: int, state: Path, attendu: str | None = None):
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(f"http://localhost:{port}")
        except Exception:
            raise SystemExit(f"Aucune fenetre sur le port {port} — relance sans --save d'abord.")

        ctx = browser.contexts[0]
        page = next((pg for pg in ctx.pages if "wiki-masters.com" in pg.url), None)
        if page is None:
            raise SystemExit("Aucun onglet WikiMasters dans cette fenetre.")
        if "/login" in page.url:
            raise SystemExit(f"Toujours sur {page.url} — la connexion n'est pas terminee.")

        ctx.storage_state(path=str(state))
        pseudo = identite(state)
        print(f"Onglet  : {page.url}")
        print(f"COMPTE  : {pseudo or '(pseudo illisible)'}")

        # Verification d'identite. Le 03/09/2026, une session de tigrewiki
        # (compte 4) a ete enregistree en croyant tenir le compte 5, puis
        # poussee dans WM_TEST5_STORAGE_STATE : la session d'oursours a ete
        # ecrasee, definitivement (un secret GitHub ne se relit pas). On
        # verifie donc AVANT que le fichier ne parte dans un secret.
        if attendu:
            if pseudo is None:
                state.unlink(missing_ok=True)
                raise SystemExit("Pseudo illisible dans la session — fichier supprime, rien enregistre.")
            if pseudo.lower() != attendu.lower():
                state.unlink(missing_ok=True)
                raise SystemExit(
                    f"REFUS : connecte en tant que {pseudo!r}, attendu {attendu!r}.\n"
                    f"Fichier supprime. Deconnecte-toi dans la fenetre et recommence "
                    f"avec le bon compte."
                )
            print(f"Identite confirmee : {pseudo}")

        print(f"Session sauvegardee dans {state.resolve()}.")
        if not attendu:
            print("Verifie ce pseudo avant de pousser ce fichier dans un secret "
                  "(ou relance avec --expect <pseudo>).")


def main():
    argv = sys.argv[1:]

    def opt(nom, defaut=None):
        return argv[argv.index(nom) + 1] if nom in argv else defaut

    state = Path(opt("--state", "storage_state.json"))
    port = int(opt("--port", 9228))
    attendu = opt("--expect")

    # Le premium a son propre script et son propre port : on evite qu'un
    # essai ici ecrase sa session par distraction.
    if "premium" in state.name:
        raise SystemExit("Pour le compte principal, utilise wm_session_premium.py.")

    if "--save" in argv:
        save_session(port, state, attendu)
    else:
        open_window(port)


if __name__ == "__main__":
    main()
