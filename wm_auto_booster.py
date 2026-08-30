"""
Automatise l'ouverture de boosters en boucle, toutes les ~10 minutes, avec
l'animation complete (meme sequence que l'outil ouvertureBooster :
wm_ouverture_booster.py). Tourne indefiniment jusqu'a Ctrl+C.

Reutilise wm_open_booster.py (run_ouverture) : attache la fenetre
persistante existante au lieu d'en relancer une a chaque cycle.

    python wm_auto_booster.py                  # boucle indefinie, ~10 min entre deux
    python wm_auto_booster.py --interval 15     # ~15 min entre deux
    python wm_auto_booster.py --max-runs 5      # s'arrete apres 5 ouvertures

L'intervalle n'est jamais exactement fixe (+/- 20% de variation aleatoire
autour de --interval) : un cron parfaitement regulier est justement la
signature la plus facile a reperer pour un systeme anti-bot. Meme logique
que DELAY ailleurs dans ce projet (cf CLAUDE.md : le risque est de perdre
le compte, pas juridique).

S'arrete tout seul si la session expire (redirige vers /login) plutot que
d'boucler dans le vide. Toute autre erreur (reseau, Chrome ferme...) est
juste loggee : la boucle continue au prochain cycle.

Ctrl+C pour arreter proprement. La fenetre Chrome persistante n'est jamais
fermee par ce script, comme d'habitude.
"""

import random
import sys
import time
from datetime import datetime

from playwright.sync_api import sync_playwright

import wm_open_booster as booster


def run_once() -> bool:
    with sync_playwright() as p:
        return booster.run_ouverture(p)


def main():
    interval_min = 10.0
    if "--interval" in sys.argv:
        idx = sys.argv.index("--interval")
        interval_min = float(sys.argv[idx + 1])

    max_runs = None
    if "--max-runs" in sys.argv:
        idx = sys.argv.index("--max-runs")
        max_runs = int(sys.argv[idx + 1])

    if not booster.STATE.exists():
        raise SystemExit("Pas de storage_state.json — lance d'abord wm_session.py")

    run = 0
    while max_runs is None or run < max_runs:
        run += 1
        now = datetime.now().strftime("%H:%M:%S")
        print(f"\n[{now}] Ouverture #{run}...")
        try:
            ok = run_once()
            print(f"[{now}] -> {'reussie' if ok else 'incomplete (voir captures/screenshots)'}")
        except SystemExit as e:
            print(f"[{now}] Arret : {e}")
            return
        except Exception as e:
            print(f"[{now}] Erreur inattendue ({e.__class__.__name__}: {e}) — on continue au prochain cycle.")

        if max_runs is not None and run >= max_runs:
            break

        jitter = random.uniform(-0.2, 0.2) * interval_min
        wait_min = max(1.0, interval_min + jitter)
        next_at = time.strftime("%H:%M:%S", time.localtime(time.time() + wait_min * 60))
        print(f"Prochaine ouverture vers {next_at} (dans ~{wait_min:.1f} min).")
        time.sleep(wait_min * 60)

    print("\nNombre maximal d'ouvertures atteint. Fenetre laissee ouverte.")


if __name__ == "__main__":
    main()
