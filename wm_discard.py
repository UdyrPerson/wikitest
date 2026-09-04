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
    python wm_discard.py storage_state_2.json   # sur un autre compte
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

# La console Windows est en cp1252 : afficher un titre de carte contenant un
# caractere hors de cette table leve un UnicodeEncodeError qui tue le script
# en plein milieu. Invisible sur un runner GitHub (UTF-8), fatal en local.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


from wm_session_io import ensure_fresh, persist

BASE = "https://www.wiki-masters.com"

DELAY = (2.0, 3.0)
DEFAULT_RARITIES = ["C", "PC", "R", "SR"]

# Liste blanche codee en dur. Rien d'autre ne peut etre defausse, quoi que
# demande la ligne de commande et quoi que renvoie le serveur.
#
# Ce garde-fou existe a cause d'un incident reel le 30/08/2026 : une
# approche par l'interface ("tout selectionner" puis "defausser") avait
# defausse des SR parce que le filtre de rarete s'etait desactive entre
# deux etapes. La lecon : ne jamais faire confiance a une selection dont on
# ne controle pas le contenu. Ici on n'envoie que des identifiants dont on
# a verifie la rarete un par un, juste avant l'appel.
ALLOWED_RARITIES = {"C", "PC", "R", "SR"}
NEVER_DISCARD = {"UR", "L"}

# L'interface defausse par lots de 50 ; on s'aligne dessus.
BATCH = 50


def safe_ids(rows, rarity: str):
    """Ne garde que les cartes dont la rarete est verifiee ET autorisee.

    Double controle volontaire : le filtre ?rarity= est deja applique cote
    serveur, mais on revalide chaque ligne ici. Si le filtre serveur etait
    ignore ou changeait de semantique, une UR ou une L pourrait se glisser
    dans la reponse -- ce garde-fou l'ecarte quoi qu'il arrive."""
    ids, refused = [], []
    for r in rows:
        actual = (r.get("card") or {}).get("rarity")
        if actual in NEVER_DISCARD or actual not in ALLOWED_RARITIES or actual != rarity:
            refused.append((r.get("id", "?")[:8], actual))
            continue
        ids.append(r["id"])
    if refused:
        print(f"    ECARTEES (rarete inattendue) : {refused}")
    return ids


def discard_rarity(req_ctx, rarity: str, remaining_budget) -> int:
    """Defausse par lots de 50 via /api/user-cards/bulk-discard.

    On relit systematiquement la page 0 apres chaque lot plutot que de
    paginer : les cartes defaussees disparaissent, donc la page 0 se
    remplit toute seule avec les suivantes. Robuste quel que soit le
    comportement exact de la suppression."""
    if rarity not in ALLOWED_RARITIES:
        raise SystemExit(f"REFUS : rarete {rarity!r} hors liste blanche {sorted(ALLOWED_RARITIES)}.")

    discarded = 0
    while remaining_budget[0] > 0:
        resp = req_ctx.get(f"/api/my-collection?sort=rarity&rarity={rarity}&page=0&stats=0")
        if resp.status == 401:
            raise SystemExit("401 sur my-collection — session expiree. Relance wm_session_auto.py.")
        if resp.status == 403:
            # 403 n'est pas forcement une session morte (cf /api/packs/open,
            # ou il signale "plus de paquets") : on montre le corps.
            raise SystemExit(f"403 sur my-collection : {resp.text()[:300]}")
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

        ids = safe_ids(collection, rarity)
        if not ids:
            # Rien d'eligible sur cette page alors qu'elle n'est pas vide :
            # on arrete plutot que de boucler indefiniment.
            print(f"    aucune carte eligible en page 0 pour {rarity} — on arrete")
            break

        # remaining_budget vaut float("inf") sans --max : on ne peut pas le
        # passer a int(), d'ou le cas separe.
        budget = remaining_budget[0]
        taille = BATCH if budget == float("inf") else min(BATCH, int(budget))
        ids = ids[:taille]

        d_resp = req_ctx.post("/api/user-cards/bulk-discard", data={"card_ids": ids})
        if d_resp.status == 401:
            raise SystemExit("401 sur bulk-discard — session expiree. Relance wm_session_auto.py.")
        if d_resp.status == 403:
            raise SystemExit(f"403 sur bulk-discard : {d_resp.text()[:300]}")
        if d_resp.status == 429:
            print("    429 sur bulk-discard : on ralentit franchement (60s)")
            time.sleep(60)
            continue
        if d_resp.status >= 400:
            print(f"    {d_resp.status} sur bulk-discard ({rarity}) : {d_resp.text()[:200]} — on arrete")
            break

        payload = d_resp.json()
        n = payload.get("discarded_count", 0)
        balance = payload.get("balance")
        failed = payload.get("failed") or []
        if failed:
            print(f"    {len(failed)} echec(s) signale(s) par l'API : {failed[:3]}")
        if n == 0:
            print(f"    lot de {len(ids)} refuse sans erreur — on arrete pour ne pas boucler")
            break

        discarded += n
        remaining_budget[0] -= n
        print(f"    [{rarity}] lot de {n} carte(s) defaussee(s) — solde={balance}")

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
        rarities = [r.strip().upper() for r in sys.argv[idx + 1].split(",") if r.strip()]

    # Refus net plutot qu'un filtrage silencieux : si quelqu'un demande UR
    # ou L, c'est une erreur qu'il faut voir, pas absorber.
    interdites = [r for r in rarities if r not in ALLOWED_RARITIES]
    if interdites:
        raise SystemExit(
            f"REFUS : rarete(s) {interdites} hors liste blanche "
            f"{sorted(ALLOWED_RARITIES)}. UR et L ne sont jamais defaussees."
        )

    max_total = None
    if "--max" in sys.argv:
        idx = sys.argv.index("--max")
        max_total = int(sys.argv[idx + 1])

    remaining_budget = [max_total if max_total is not None else float("inf")]

    with sync_playwright() as p:
        # Force la rotation du jeton avant la boucle : sans ca, une
        # expiration en cours de defausse revoque la session (cf
        # wm_session_io.ensure_fresh).
        req_ctx = ensure_fresh(p, state_path, BASE)

        total = 0
        # try/finally : discard_rarity leve SystemExit sur 401/403, et le
        # serveur a pu faire tourner le refresh token avant. Sans
        # sauvegarde, la session sera revoquee au prochain usage
        # (cf wm_session_io).
        try:
            for rarity in rarities:
                print(f"\n--- {rarity} ---")
                n = discard_rarity(req_ctx, rarity, remaining_budget)
                print(f"  {n} carte(s) '{rarity}' defaussee(s)")
                total += n
                if remaining_budget[0] <= 0:
                    print("Limite --max atteinte — arret.")
                    break
        finally:
            persist(req_ctx, state_path)
            req_ctx.dispose()

    print(f"\nTotal : {total} carte(s) defaussee(s) sur {rarities}.")


if __name__ == "__main__":
    main()
