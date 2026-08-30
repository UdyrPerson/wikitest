"""
Recupere l'historique de VRAIES ventes conclues (final_price, pas une mise en
cours) pour une liste de cartes, via l'endpoint decouvert le 30/08/2026 sur
le compte premium :

    GET /api/marketplace/cards/{card_id}/sales
    -> {"wikipedia_title": str, "sales": [{"final_price", "settled_at", "rarity"}, ...], "recent": [...]}

C'est la seule vraie source de prix de vente reels du site (verifie : pas de
flux global "sold", pas d'endpoint par lot -- une requete par carte, cf
CLAUDE.md et la discussion du 30/08/2026 sur le compromis vitesse/perimetre).

Vitesse adaptative (decision explicite : sur le compte premium, on ne baisse
pas le delai a l'aveugle) :
  - demarre a DELAY_STAGES[0] (2.0-3.5s, le delai de securite habituel du
    projet) ;
  - apres STAGE_PROMOTE_AFTER requetes consecutives sans 429, passe au palier
    suivant (plus rapide) ;
  - au moindre 429, retombe immediatement au palier le plus prudent et
    attend 60s avant de reprendre (meme logique que les autres scripts du
    projet) ;
  - ne descend jamais sous DELAY_STAGES[-1].

Usage :
    python wm_sales_scan.py data/sales_candidates.json
    python wm_sales_scan.py data/sales_candidates.json --limit 500   # test rapide
    python wm_sales_scan.py data/sales_candidates.json --max-speed   # voir plus bas

--max-speed : decision explicite de l'utilisateur le 30/08/2026, en toute
connaissance du risque signale (compte premium reel, regle DELAY>=2s du
projet volontairement mise de cote). Aucun delai entre les requetes (aussi
vite que le serveur repond, ~0.2s/requete). La reaction a un vrai 429 reste
active meme dans ce mode : ignorer un signal d'erreur explicite du serveur
ne serait pas de la vitesse, ce serait juste continuer a taper dans le vide.

Ecrit au fur et a mesure (une ligne JSON par carte traitee) dans
data/sales-<horodatage>.jsonl, pour ne rien perdre en cas d'interruption.
"""

import json
import random
import sys
import time
from pathlib import Path
from statistics import mean, median

from playwright.sync_api import sync_playwright

from wm_session_io import ensure_fresh, persist

BASE = "https://www.wiki-masters.com"
STATE = Path("storage_state.json")
DATA = Path("data")

# Paliers de delai (secondes, min/max pour random.uniform), du plus prudent
# au plus rapide. On ne descend jamais sous le dernier palier.
DELAY_STAGES = [
    (2.0, 3.5),
    (1.2, 2.0),
    (0.6, 1.0),
    (0.3, 0.5),
]
STAGE_PROMOTE_AFTER = 150  # requetes sans 429 avant de passer au palier suivant
COOLDOWN_ON_429 = 60


def scan(req_ctx, candidates: list[dict], out_path: Path, max_speed: bool = False):
    stages = [(0.0, 0.0)] if max_speed else DELAY_STAGES
    stage = 0
    clean_streak = 0
    n_by_stage_used = {i: 0 for i in range(len(stages))}

    with out_path.open("a", encoding="utf-8") as out:
        for i, cand in enumerate(candidates, 1):
            card_id = cand["card_id"]
            rarity = cand["rarity"]

            resp = req_ctx.get(f"/api/marketplace/cards/{card_id}/sales")

            if resp.status in (401, 403):
                raise SystemExit(f"{resp.status} sur {card_id} — session expiree. Relance wm_session.py.")

            if resp.status == 429:
                if max_speed:
                    print(f"  [{i}/{len(candidates)}] 429 sur {card_id} — pause {COOLDOWN_ON_429}s "
                          f"(mode --max-speed : on reprend a la meme vitesse ensuite, "
                          f"mais on ne tape pas dans le vide sur une erreur explicite)")
                else:
                    print(f"  [{i}/{len(candidates)}] 429 sur {card_id} — repli au palier le plus prudent, pause {COOLDOWN_ON_429}s")
                    stage = 0
                clean_streak = 0
                time.sleep(COOLDOWN_ON_429)
                # on retente une fois cette meme carte apres la pause
                resp = req_ctx.get(f"/api/marketplace/cards/{card_id}/sales")
                if resp.status != 200:
                    print(f"    toujours {resp.status} apres la pause — on passe cette carte")
                    continue

            if resp.status >= 400:
                print(f"  [{i}/{len(candidates)}] {resp.status} sur {card_id} — carte ignoree")
                continue

            payload = resp.json()
            sales = payload.get("sales", [])
            prices = [s["final_price"] for s in sales]

            record = {
                "card_id": card_id,
                "rarity": rarity,
                "wikipedia_title": payload.get("wikipedia_title"),
                "n_sales": len(prices),
                "avg_price": mean(prices) if prices else None,
                "median_price": median(prices) if prices else None,
                "min_price": min(prices) if prices else None,
                "max_price": max(prices) if prices else None,
            }
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            out.flush()

            n_by_stage_used[stage] += 1
            clean_streak += 1
            if clean_streak >= STAGE_PROMOTE_AFTER and stage < len(stages) - 1:
                stage += 1
                clean_streak = 0
                print(f"  [{i}/{len(candidates)}] {STAGE_PROMOTE_AFTER} requetes sans 429 — "
                      f"palier suivant : delai {stages[stage]}")

            if i % 200 == 0 or i == len(candidates):
                print(f"  [{i}/{len(candidates)}] {card_id} ({rarity}) : "
                      f"{record['n_sales']} vente(s), moyenne={record['avg_price']}, "
                      f"palier actuel={stages[stage]}")

            if i < len(candidates) and stages[stage] != (0.0, 0.0):
                time.sleep(random.uniform(*stages[stage]))

    print("\nRepartition du temps passe par palier de delai (nb de requetes) :")
    for s, n in n_by_stage_used.items():
        print(f"  palier {stages[s]} : {n} requetes")


def main():
    if not STATE.exists():
        raise SystemExit("Pas de storage_state.json — lance d'abord wm_session.py")
    if len(sys.argv) < 2:
        raise SystemExit("Usage: python wm_sales_scan.py <candidates.json> [--limit N]")

    candidates_path = Path(sys.argv[1])
    candidates = json.loads(candidates_path.read_text(encoding="utf-8"))

    if "--limit" in sys.argv:
        idx = sys.argv.index("--limit")
        limit = int(sys.argv[idx + 1])
        candidates = candidates[:limit]

    max_speed = "--max-speed" in sys.argv
    if max_speed:
        print("Mode --max-speed : aucun delai entre les requetes (decision utilisateur "
              "du 30/08/2026, risque de compte signale et assume).")

    print(f"{len(candidates)} cartes a scanner.")

    DATA.mkdir(exist_ok=True)
    stamp = time.strftime("%Y-%m-%d_%H%M%S")
    out_path = DATA / f"sales-{stamp}.jsonl"
    print(f"Ecriture au fur et a mesure dans {out_path.resolve()}\n")

    with sync_playwright() as p:

# Les scripts qui lisent l'API font tourner le jeton cote serveur comme les
# autres : sans ensure_fresh/persist, ce script laisse une session perimee
# derriere lui et le prochain run GitHub Actions tombe en 401
# (cf wm_session_io.py, cause racine du 30/08/2026).
        req_ctx = ensure_fresh(p, STATE, BASE)
        scan(req_ctx, candidates, out_path, max_speed=max_speed)
        persist(req_ctx, STATE)
        req_ctx.dispose()

    print(f"\nTermine. Resultats dans {out_path.resolve()}")


if __name__ == "__main__":
    main()
