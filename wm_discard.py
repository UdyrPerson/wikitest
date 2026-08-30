"""
Defausse automatiquement toutes les cartes d'une ou plusieurs raretes de ma
collection (par defaut C, PC, R), via l'API directe -- pas de navigateur.

Endpoint decouvert le 30/08/2026 sur le compte de test :

    POST /api/user-cards/{user_card_id}/discard
    -> {"balance": <nouveau solde wikibidous>}

{user_card_id} est le champ "id" d'une ligne de
GET /api/my-collection?rarity=X&page=0 (PAS card_id, qui est l'identifiant
global de la carte -- user-cards/{id} designe la possession, pas la carte).

La confirmation UI ("Defausser cette carte ? ... C'est votre derniere
copie...") indique que chaque appel retire UNE unite (si plusieurs
exemplaires sont possedes, "count" doit decrementer plutot que la ligne
disparaitre completement) -- mais le compte de test n'a que des count=1
pour l'instant, donc ce n'est pas verifie empiriquement. Peu importe :
la strategie retenue ici est robuste dans les deux cas -- on relit la
page 0 a chaque fois et on defausse le premier element trouve, en boucle
jusqu'a ce que la liste renvoyee pour cette rarete soit vide. Que discard
vide une pile entiere ou une unite a la fois, ce motif finit toujours par
tout vider correctement.

    python wm_discard.py                      # storage_state.json, C/PC/R par defaut
    python wm_discard.py storage_state_test2.json   # sur un autre compte
    python wm_discard.py --rarities C,PC       # juste ces deux-la
    python wm_discard.py --max N               # s'arrete apres N defausses au total

Le fichier de session est le premier argument positionnel (celui qui ne
commence pas par "--"), storage_state.json par defaut si omis -- meme
convention que wm_trade_gift_wb.py / wm_trade_accept_all.py, ajoutee le
30/08/2026 quand la defausse est devenue un workflow independant capable
de traiter plusieurs comptes dans le meme run.

Respecte DELAY (cf CLAUDE.md) : sequentiel, >=2s entre deux appels.
"""

import json
import random
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "https://www.wiki-masters.com"

DELAY = (2.0, 3.0)
DEFAULT_RARITIES = ["C", "PC", "R"]


def discard_rarity(req_ctx, rarity: str, remaining_budget) -> int:
    discarded = 0
    while remaining_budget[0] > 0:
        resp = req_ctx.get(f"/api/my-collection?sort=rarity&rarity={rarity}&page=0&stats=0")
        if resp.status in (401, 403):
            raise SystemExit(f"{resp.status} sur my-collection — session expiree. Relance wm_session.py.")
        if resp.status == 429:
            print("    429 : on ralentit franchement (60s)")
            time.sleep(60)
            continue
        if resp.status >= 400:
            print(f"    {resp.status} sur my-collection ({rarity}) — on arrete cette rarete")
            break

        data = resp.json()
        collection = data.get("collection", [])
        if not collection:
            break

        row = collection[0]
        user_card_id = row["id"]
        title = row.get("card", {}).get("wikipedia_title", "?")

        d_resp = req_ctx.post(f"/api/user-cards/{user_card_id}/discard")
        if d_resp.status in (401, 403):
            raise SystemExit(f"{d_resp.status} sur discard — session expiree. Relance wm_session.py.")
        if d_resp.status == 429:
            print("    429 sur discard : on ralentit franchement (60s)")
            time.sleep(60)
            continue
        if d_resp.status >= 400:
            print(f"    {d_resp.status} sur discard de {user_card_id} ({title!r}) — on arrete cette rarete")
            break

        balance = d_resp.json().get("balance")
        discarded += 1
        remaining_budget[0] -= 1
        print(f"    [{rarity}] defausse : {title!r} (id={user_card_id[:8]}...) — solde={balance}")

        if remaining_budget[0] <= 0:
            break
        time.sleep(random.uniform(*DELAY))

    return discarded


def main():
    # Distingue l'argument positionnel (fichier de session) des flags a
    # valeur (--rarities X, --max N) : sauter la valeur qui suit chacun
    # de ces flags plutot que de la prendre par erreur pour le fichier.
    positional = []
    skip_next = False
    for a in sys.argv[1:]:
        if skip_next:
            skip_next = False
            continue
        if a in ("--rarities", "--max"):
            skip_next = True
            continue
        if not a.startswith("--"):
            positional.append(a)
    state_path = Path(positional[0]) if positional else Path("storage_state.json")

    if not state_path.exists():
        raise SystemExit(f"{state_path} introuvable — lance d'abord wm_session.py ou wm_session_auto.py.")

    rarities = DEFAULT_RARITIES
    if "--rarities" in sys.argv:
        idx = sys.argv.index("--rarities")
        rarities = sys.argv[idx + 1].split(",")

    max_total = None
    if "--max" in sys.argv:
        idx = sys.argv.index("--max")
        max_total = int(sys.argv[idx + 1])

    remaining_budget = [max_total if max_total is not None else float("inf")]

    with sync_playwright() as p:
        req_ctx = p.request.new_context(storage_state=str(state_path), base_url=BASE)

        total = 0
        for rarity in rarities:
            print(f"\n--- {rarity} ---")
            n = discard_rarity(req_ctx, rarity, remaining_budget)
            print(f"  {n} carte(s) '{rarity}' defaussee(s)")
            total += n
            if remaining_budget[0] <= 0:
                print("Limite --max atteinte — arret.")
                break

        req_ctx.dispose()

    print(f"\nTotal : {total} carte(s) defaussee(s) sur {rarities}.")


if __name__ == "__main__":
    main()
