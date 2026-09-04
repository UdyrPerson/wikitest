"""
Met une carte aux encheres sur le marche. Pure API, pas de navigateur.

Endpoints (releves dans captures/index.jsonl, capture du 29/08/2026) :

    POST /api/marketplace
         {"card_id": <user_card_id>, "base_amount": N, "duration_minutes": M}
         -> 201 {"auction_id": "..."}

    GET  /api/marketplace/mine        -> {"sellingCount": N, "maxConcurrentAuctions": 5}
    GET  /api/marketplace/{id}        -> l'enchere complete (carte, mises, fin)

    python wm_sell.py state5.json                       # etat des lieux, aucune ecriture
    python wm_sell.py state5.json --rarity L --limit 3  # ce qui SERAIT mis en vente
    python wm_sell.py state5.json --card <user_card_id> --price 500 --duration 60 --go
    python wm_sell.py state5.json --mine                # mes encheres en cours

PIEGE D'IDENTIFIANT -- le meme que pour la defausse, et il est verifie ici :
le champ s'appelle "card_id" mais il attend l'identifiant de la POSSESSION
(le "id" d'une ligne de /api/my-collection), pas l'identifiant global de la
carte. Preuve dans la capture : le POST envoie 0ec8e44d..., et l'enchere
creee renvoie card_id e6617907... -- deux valeurs differentes. Envoyer
l'identifiant global echoue (ou pire, vend la mauvaise possession).

RIEN N'EST ANNULABLE. Aucun endpoint de retrait d'enchere n'a ete observe.
Une fois postee, l'enchere va a son terme : si quelqu'un mise, la carte est
vendue au prix atteint ; si personne ne mise, elle revient (a confirmer par
l'observation, cf --mine apres expiration). D'ou le fonctionnement par
defaut en simulation : --go est obligatoire pour ecrire quoi que ce soit.

PLAFOND D'ENCHERES SIMULTANEES, ET IL DEPEND DU COMPTE. La capture du
29/08/2026 sur un compte de test montrait maxConcurrentAuctions=5 ; le
compte premium renvoie 10 (verifie le 03/09/2026). Le plafond est donc lie
au statut du compte, pas fixe pour le site. Le script lit toujours la
valeur courante via /api/marketplace/mine au lieu de la supposer, et
refuse de la depasser plutot que de se prendre l'erreur serveur. C'est
cette contrainte qui structurera la strategie : 5 comptes de test x 5
emplacements = 25 encheres simultanees.

COMPTE PREMIUM : reserve au scrape de reference (wm_sales_reference.py).
Toute mise en vente de test passe par un compte de test -- regle posee le
03/09/2026. Le script refuse --go sur la session premium.
"""

import json
import random
import statistics
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
DATA = Path("data")

# Meme cadence que le reste du projet (cf CLAUDE.md) : sequentiel, >= 2s.
# Une mise en vente est une ecriture, on ne cherche pas a aller vite.
DELAY = (2.0, 3.0)

DEFAULT_DURATION = 60


def slots(req_ctx):
    """(encheres en cours, plafond). Lecture seule."""
    r = req_ctx.get("/api/marketplace/mine")
    if r.status == 401:
        raise SystemExit("401 sur /api/marketplace/mine — session expiree.")
    if r.status >= 400:
        raise SystemExit(f"{r.status} sur /api/marketplace/mine : {r.text()[:200]}")
    d = r.json()
    return d.get("sellingCount", 0), d.get("maxConcurrentAuctions", 5)


def mes_encheres(req_ctx):
    """Mes annonces actives, via le filtre mine=1 du marche."""
    r = req_ctx.get("/api/marketplace?page=1&limit=50&sort=ending_soon&mine=1")
    if r.status >= 400:
        print(f"  {r.status} sur le marche (mine=1) : {r.text()[:200]}")
        return []
    d = r.json()
    return d.get("auctions", d) if isinstance(d, dict) else d


def collection(req_ctx, rarity: str, page: int = 0):
    """Une page de ma collection pour une rarete donnee."""
    r = req_ctx.get(f"/api/my-collection?sort=rarity&rarity={rarity}&page={page}&stats=0")
    if r.status == 401:
        raise SystemExit("401 sur /api/my-collection — session expiree.")
    if r.status >= 400:
        raise SystemExit(f"{r.status} sur /api/my-collection : {r.text()[:200]}")
    # La cle est "collection" (et non "cards" comme sur /api/cards) --
    # verifie dans wm_discard.py, qui exploite cet endpoint en production.
    return r.json().get("collection", [])


def reference(rarity: str):
    """Prix medians par carte, depuis data/sales-{rarity}.jsonl s'il existe.

    C'est le produit de wm_sales_reference.py. Absent, on ne propose pas de
    prix : mieux vaut pas de suggestion qu'une suggestion inventee."""
    path = DATA / f"sales-{rarity}.jsonl"
    if not path.exists():
        return {}
    table = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if rec.get("stats"):
            table[rec["card_id"]] = rec["stats"]
    return table


def decrire(row, ref):
    """Une ligne de collection resumee, avec son prix de reference si connu."""
    card = row.get("card") or {}
    stats = ref.get(card.get("id"))
    prix = f"med={stats['mediane']} (n={stats['n']})" if stats else "pas de reference"
    return f"{row.get('id','?')[:8]}  {card.get('rarity','?'):3s} {(card.get('wikipedia_title') or '?')[:38]:38s} {prix}"


def mettre_en_vente(req_ctx, user_card_id: str, prix: int, duree: int):
    """POST /api/marketplace. Retourne l'auction_id."""
    payload = {"card_id": user_card_id, "base_amount": prix, "duration_minutes": duree}
    r = req_ctx.post("/api/marketplace", data=payload)

    if r.status == 401:
        raise SystemExit("401 sur la mise en vente — session expiree.")
    if r.status == 429:
        raise SystemExit("429 sur la mise en vente — on s'arrete plutot que d'insister.")
    if r.status >= 400:
        raise SystemExit(f"{r.status} sur la mise en vente : {r.text()[:400]}")

    return r.json().get("auction_id")


def verifier(req_ctx, auction_id: str):
    """Relit l'enchere creee : c'est la seule facon de confirmer qu'on a
    bien vendu la carte qu'on croyait (cf le piege d'identifiant)."""
    r = req_ctx.get(f"/api/marketplace/{auction_id}")
    if r.status >= 400:
        print(f"  relecture impossible ({r.status})")
        return None
    return r.json().get("auction")


def main():
    argv = sys.argv[1:]

    def opt(nom, defaut=None):
        return argv[argv.index(nom) + 1] if nom in argv else defaut

    # Session en premier argument positionnel, meme convention que
    # wm_discard.py / wm_trade_gift_wb.py.
    state = next((a for a in argv if not a.startswith("--") and a.endswith(".json")), None)
    if state is None:
        state = "storage_state.json"
    if not Path(state).exists():
        raise SystemExit(f"{state} introuvable.")

    # Le compte premium sert au scrape de reference, pas aux essais de
    # vente : une carte vendue par erreur la-bas n'est pas remplacable.
    if "--go" in argv and "premium" in Path(state).name:
        raise SystemExit(
            "REFUS : pas de mise en vente sur la session premium. "
            "Utilise une session de compte de test."
        )

    go = "--go" in argv
    card_id = opt("--card")
    prix = int(opt("--price", 0) or 0)
    duree = int(opt("--duration", DEFAULT_DURATION))
    rarity = (opt("--rarity") or "").upper()
    limite = int(opt("--limit", 5))

    if go and not card_id:
        raise SystemExit("--go exige --card <user_card_id> : on ne met en vente qu'une carte designee.")
    if go and prix <= 0:
        raise SystemExit("--go exige --price <entier positif>.")

    with sync_playwright() as p:
        ctx = ensure_fresh(p, state, BASE)
        try:
            en_cours, plafond = slots(ctx)
            print(f"Session : {state}")
            print(f"Encheres en cours : {en_cours}/{plafond}\n")

            if "--mine" in argv:
                for a in mes_encheres(ctx):
                    c = a.get("card") or {}
                    print(f"  {a.get('id','?')[:8]}  {c.get('rarity','?'):3s} "
                          f"{(c.get('wikipedia_title') or '?')[:34]:34s} "
                          f"base={a.get('base_amount')} mise={a.get('current_bid')} "
                          f"fin={a.get('end_at','?')[:16]} {a.get('status','?')}")
                return

            if go:
                if en_cours >= plafond:
                    raise SystemExit(f"REFUS : {en_cours}/{plafond} emplacements occupes.")

                # On ne fait jamais confiance a un identifiant passe en
                # ligne de commande : on le retrouve dans la collection et
                # on lit sa vraie rarete avant d'ecrire. Meme lecon que la
                # defausse du 30/08/2026.
                trouvee = None
                for r7 in ("C", "PC", "R", "SR", "UR", "L"):
                    for row in collection(ctx, r7):
                        if row.get("id") == card_id:
                            trouvee = row
                            break
                    if trouvee:
                        break
                    time.sleep(random.uniform(*DELAY))

                if not trouvee:
                    raise SystemExit(f"Possession {card_id} introuvable dans la collection de {state}.")

                c = trouvee.get("card") or {}
                print(f"Carte    : {c.get('wikipedia_title')} [{c.get('rarity')}]")
                print(f"Prix     : {prix} wb, duree {duree} min")
                auction_id = mettre_en_vente(ctx, card_id, prix, duree)
                print(f"\nEnchere creee : {auction_id}")

                a = verifier(ctx, auction_id)
                if a:
                    vendue = (a.get("card") or {}).get("wikipedia_title")
                    print(f"  carte mise en vente : {vendue} [{a.get('snapshot_rarity')}]")
                    print(f"  base {a.get('base_amount')} — fin {a.get('end_at')} — {a.get('status')}")
                    if vendue != c.get("wikipedia_title"):
                        print("  !! ATTENTION : ce n'est pas la carte attendue.")
                return

            # --- simulation (defaut) ---
            raretes = [rarity] if rarity else ["L", "UR"]
            for r7 in raretes:
                ref = reference(r7)
                rows = collection(ctx, r7)
                print(f"## {r7} — {len(rows)} possession(s) page 0, "
                      f"{len(ref)} carte(s) avec prix de reference")
                for row in rows[:limite]:
                    print("   " + decrire(row, ref))
                print()
                time.sleep(random.uniform(*DELAY))

            print("Simulation uniquement. Pour vendre :")
            print("  python wm_sell.py --premium --card <id> --price <wb> --duration 60 --go")
        finally:
            persist(ctx, state)
            ctx.dispose()


if __name__ == "__main__":
    main()
