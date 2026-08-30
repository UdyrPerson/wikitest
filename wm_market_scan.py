"""
Recupere un echantillon d'enchetes actives sur /marketplace, par appel API
direct (comme wm_open_booster.py --api), pour etudier ce qui correle avec
le prix des cartes.

Pas d'endpoint pour les ventes conclues : /api/marketplace ne renvoie que
les enchetes actives (+ mes propres selling/bidding/history/won, vides ou
minces). Le meilleur proxy de valeur dispo est donc la mise actuelle
(current_bid si present, sinon base_amount) sur les encheres en cours.

    python wm_market_scan.py                  # 20 pages (~1000 annonces), sort=recent
    python wm_market_scan.py --pages 50 --sort price_desc
    python wm_market_scan.py --pages 250 --only-bid   # balayage complet du marche,
                                                       # ne garde que les annonces
                                                       # avec une vraie mise (current_bid
                                                       # non nul) -- une mise de depart
                                                       # seule ne veut rien dire, le
                                                       # vendeur choisit ce chiffre
                                                       # arbitrairement.

--pages n'a pas besoin d'etre exact : le scan s'arrete de lui-meme des que
l'API signale qu'il n'y a plus de page (hasMore=false), donc mettre une
valeur large (ex. 250, largement au-dessus du nombre reel de pages) revient
a balayer tout le marche sans risquer de s'arreter trop tot.

Ecrit data/marketplace-<horodatage>.json (liste brute des enchetes vues,
dedupliquees par id, filtree sur --only-bid si demande).
"""

import json
import random
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from wm_session_io import ensure_fresh, persist

BASE = "https://www.wiki-masters.com"
STATE = Path("storage_state.json")
DATA = Path("data")

# Meme garde-fou que partout ailleurs dans ce projet : appel API direct,
# donc plus "bot-like", DELAY ne descend pas sous 2s (cf CLAUDE.md).
DELAY = (2.0, 3.5)

VALID_SORTS = {"ending_soon", "recent", "price_asc", "price_desc"}


def scan(req_ctx, pages: int, sort: str):
    seen_ids = {}
    for page_num in range(1, pages + 1):
        url = f"/api/marketplace?page={page_num}&limit=50&sort={sort}"
        resp = req_ctx.get(url)

        if resp.status in (401, 403):
            raise SystemExit(f"{resp.status} sur {url} — session expiree. Relance wm_session.py.")
        if resp.status == 429:
            print("    429 : on ralentit franchement (60s)")
            time.sleep(60)
            continue
        if resp.status >= 400:
            print(f"    {resp.status} sur {url} — on arrete")
            break

        payload = resp.json()
        auctions = payload.get("auctions", [])
        new = 0
        for a in auctions:
            if a["id"] not in seen_ids:
                seen_ids[a["id"]] = a
                new += 1

        print(f"    page {page_num}/{pages} : {len(auctions)} recues, {new} nouvelles "
              f"(total unique {len(seen_ids)}, total marche {payload.get('total')})")

        if not auctions or not payload.get("hasMore"):
            print("    plus de resultats — on arrete")
            break
        if page_num < pages:
            time.sleep(random.uniform(*DELAY))

    return list(seen_ids.values())


def main():
    if not STATE.exists():
        raise SystemExit("Pas de storage_state.json — lance d'abord wm_session.py")

    pages = 20
    if "--pages" in sys.argv:
        idx = sys.argv.index("--pages")
        pages = int(sys.argv[idx + 1])

    sort = "recent"
    if "--sort" in sys.argv:
        idx = sys.argv.index("--sort")
        sort = sys.argv[idx + 1]
        if sort not in VALID_SORTS:
            raise SystemExit(f"--sort doit etre dans {sorted(VALID_SORTS)}")

    only_bid = "--only-bid" in sys.argv

    with sync_playwright() as p:

# Les scripts qui lisent l'API font tourner le jeton cote serveur comme les
# autres : sans ensure_fresh/persist, ce script laisse une session perimee
# derriere lui et le prochain run GitHub Actions tombe en 401
# (cf wm_session_io.py, cause racine du 30/08/2026).
        req_ctx = ensure_fresh(p, STATE, BASE)
        results = scan(req_ctx, pages, sort)
        persist(req_ctx, STATE)
        req_ctx.dispose()

    total_scanned = len(results)
    if only_bid:
        results = [a for a in results if a.get("current_bid") is not None]
        print(f"\nFiltre --only-bid : {len(results)}/{total_scanned} annonces "
              f"ont une vraie mise (current_bid non nul), le reste n'a qu'une "
              f"mise de depart arbitraire choisie par le vendeur -- exclu.")

    DATA.mkdir(exist_ok=True)
    stamp = time.strftime("%Y-%m-%d_%H%M%S")
    suffix = "-onlybid" if only_bid else ""
    out = DATA / f"marketplace-{sort}{suffix}-{stamp}.json"
    out.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n{len(results)} annonces ecrites dans {out.resolve()}")


if __name__ == "__main__":
    main()
