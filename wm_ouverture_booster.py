"""
Outil "ouvertureBooster" : ouvre un booster avec l'animation complete
(clic sur le paquet, defilement des 5 cartes, clic sur Continuer).

Ne relance pas de nouveau Chrome a chaque appel : il se branche sur la
fenetre persistante geree par wm_open_booster.py (port CDP_PORT fixe). Si
elle n'est pas deja ouverte, il la lance lui-meme. Reutilise donc le code
de wm_open_booster.py (meme selecteurs, meme sequence) plutot que de le
dupliquer.

    python wm_open_booster.py          # une fois, pour ouvrir la fenetre
    python wm_ouverture_booster.py     # ouvre un booster
    python wm_ouverture_booster.py     # rappel plus tard : reutilise la meme fenetre

Consomme un vrai booster a chaque appel. Attends au moins quelques
secondes entre deux appels (cf CLAUDE.md, compte en jeu si ca ressemble a
un bot) — ce script n'ouvre qu'un seul pack par lancement, pas de boucle.
"""

from playwright.sync_api import sync_playwright

import wm_open_booster as booster


def main():
    if not booster.STATE.exists():
        raise SystemExit("Pas de storage_state.json — lance d'abord wm_session.py")

    with sync_playwright() as p:
        ok = booster.run_ouverture(p)

    if ok:
        print("\nBooster ouvert. Fenetre laissee ouverte pour le prochain appel.")
    else:
        print("\nSequence incomplete — regarde la fenetre Chrome ou les captures d'ecran.")


if __name__ == "__main__":
    main()
