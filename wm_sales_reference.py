"""
Construit une table de reference des prix de vente reels pour les cartes
d'une rarete donnee, depuis le COMPTE PREMIUM (l'historique des ventes
conclues n'est expose qu'aux comptes premium).

    python wm_sales_reference.py --rarity L
    python wm_sales_reference.py --rarity UR --delay 2.0
    python wm_sales_reference.py --rarity L --stats-only    # pas d'appel reseau

Deux endpoints :
    GET /api/cards?rarity=X&page=N          -> catalogue global (50/page)
    GET /api/marketplace/cards/{id}/sales   -> ventes conclues d'une carte
                                               (final_price, settled_at)

Sortie : data/sales-{rarity}.jsonl, une ligne JSON par carte. Le format
JSONL est choisi pour que le fichier soit ecrit AU FIL DE L'EAU : un scrape
de 12 000 cartes dure des heures, il doit survivre a une interruption.

REPRISE : au demarrage, les cartes deja presentes dans le fichier sont
sautees. Relancer la meme commande apres un Ctrl+C reprend ou ca s'est
arrete, sans re-interroger ce qui est deja connu.

SESSION : une boucle de plusieurs heures traverse forcement l'expiration du
jeton (1h). Sans precaution, le serveur fait tourner le refresh token en
plein milieu et la requete suivante, partie avec l'ancien cookie, fait
REVOQUER la session (cf wm_session_io.py -- c'est ce qui a casse les
comptes de test le 30/08/2026). On rafraichit donc proactivement des qu'il
reste moins de FRESH_MARGIN_S de validite, et on sauvegarde a chaque fois.
"""

import json
import random
import statistics
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from wm_session_io import ensure_fresh, persist, token_expires_in, FRESH_MARGIN_S

# La console Windows est en cp1252 : afficher un titre contenant un
# caractere hors de cette table (ex. "c" accent aigu polonais) leve un
# UnicodeEncodeError qui TUE le script en plein scrape, sans rien ecrire
# dans le journal. C'est ce qui a fait mourir silencieusement plusieurs
# lancements le 03/09/2026. On force donc une sortie UTF-8 tolerante.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = "https://www.wiki-masters.com"
STATE = Path("storage_state_premium.json")
DATA = Path("data")

# CLAUDE.md fixe 2s comme regle generale. L'utilisateur a explicitement
# demande 0.2s (5 appels/s) pour ce scrape, en connaissance du risque et en
# reprenant la cadence deja convenue lors d'un scrape precedent : c'est une
# lecture ponctuelle et bornee (~14 000 cartes, ~47 min), pas une activite
# de fond permanente. Decision assumee le 03/09/2026.
DEFAULT_DELAY = 0.2
MIN_DELAY = 0.2  # plancher dur : en dessous, on refuse
PAGE_SIZE = 50


def catalog_ids(req_ctx, rarity: str):
    """Tous les (id, titre) du catalogue global pour une rarete."""
    out, page = [], 0
    while True:
        r = req_ctx.get(f"/api/cards?rarity={rarity}&page={page}")
        if r.status == 401:
            raise SystemExit("401 sur /api/cards — session premium expiree.")
        if r.status >= 400:
            print(f"  {r.status} sur /api/cards page {page} — on arrete la pagination")
            break
        cards = r.json().get("cards", [])
        if not cards:
            break
        out += [(c["id"], c.get("wikipedia_title", "?")) for c in cards]
        page += 1
        if len(cards) < PAGE_SIZE:
            break
        time.sleep(0.05)  # pagination : 246 pages pour UR, la pause dominait

    # Dedoublonnage indispensable : la pagination du catalogue n'a pas un
    # ordre stable, la meme carte peut revenir sur plusieurs pages. Sans ca
    # on l'interroge plusieurs fois et le fichier de sortie se remplit de
    # doublons (constate le 03/09/2026 : 776 doublons sur 1800 cartes L).
    vus, uniques = set(), []
    for cid, titre in out:
        if cid in vus:
            continue
        vus.add(cid)
        uniques.append((cid, titre))
    return uniques


def summarize(prices):
    """Statistiques utiles pour fixer une mise a prix.

    La mediane et les quartiles comptent plus que la moyenne : une seule
    vente aberrante (enchere emballee ou bradee) suffit a fausser cette
    derniere, alors qu'on cherche le prix auquel une carte se vend
    habituellement."""
    if not prices:
        return None
    s = sorted(prices)
    return {
        "n": len(s),
        "moyenne": round(statistics.mean(s), 1),
        "mediane": statistics.median(s),
        "min": s[0],
        "max": s[-1],
        "ecart_type": round(statistics.stdev(s), 1) if len(s) > 1 else 0,
        "q1": s[len(s) // 4],
        "q3": s[(3 * len(s)) // 4],
    }


def load_done(path: Path):
    """Identifiants deja scrapes, pour la reprise."""
    done = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                done.add(json.loads(line)["card_id"])
            except Exception:
                continue
    return done


def main():
    rarity = "L"
    if "--rarity" in sys.argv:
        rarity = sys.argv[sys.argv.index("--rarity") + 1].upper()
    delay = DEFAULT_DELAY
    if "--delay" in sys.argv:
        delay = float(sys.argv[sys.argv.index("--delay") + 1])
    if delay < MIN_DELAY:
        raise SystemExit(f"REFUS : --delay sous {MIN_DELAY}s.")

    DATA.mkdir(exist_ok=True)
    out_path = DATA / f"sales-{rarity}.jsonl"

    if "--stats-only" in sys.argv:
        report(out_path, rarity)
        return

    if not STATE.exists():
        raise SystemExit(f"{STATE} introuvable — lance wm_session_premium.py d'abord.")

    done = load_done(out_path)
    print(f"Rarete {rarity} — {len(done)} carte(s) deja connue(s), on reprend.")

    with sync_playwright() as p:
        # Contexte simple, PAS ensure_fresh : ce dernier ferme et recree le
        # contexte quand le jeton approche de l'expiration, ce qui cassait
        # le scrape au demarrage (03/09/2026). Inutile ici de toute façon :
        # le serveur renouvelle la session tout seul a la premiere requete
        # postérieure a l'expiration, et le contexte absorbe la rotation.
        # On sauvegarde periodiquement et en fin de course, c'est suffisant.
        ctx = p.request.new_context(storage_state=str(STATE), base_url=BASE)
        try:
            # Catalogue mis en cache : le paginer coute ~113s pour les
            # 246 pages d'UR, et il etait rejoue a chaque reprise de
            # tranche. Il ne bouge quasiment pas d'une heure a l'autre.
            cache = DATA / f"catalog-{rarity}.json"
            if cache.exists():
                cards = [tuple(x) for x in json.loads(cache.read_text(encoding="utf-8"))]
                print(f"Catalogue {rarity} relu du cache ({len(cards)} cartes).")
            else:
                cards = catalog_ids(ctx, rarity)
                cache.write_text(json.dumps(cards, ensure_ascii=False), encoding="utf-8")
            todo = [(i, t) for i, t in cards if i not in done]
            print(f"Catalogue {rarity} : {len(cards)} carte(s), {len(todo)} a traiter.")
            eta = len(todo) * max(delay, 0.27) / 3600
            print(f"Duree estimee : {eta:.1f} h a {delay}s par carte.\n")

            with out_path.open("a", encoding="utf-8") as fh:
                for n, (cid, title) in enumerate(todo, 1):
                    t_iter = time.time()
                    # PAS de rafraichissement par iteration. Premiere version
                    # le faisait des qu'il restait moins de FRESH_MARGIN_S,
                    # mais le serveur ne renouvelle qu'APRES expiration : le
                    # jeton restait sous le seuil et chaque carte declenchait
                    # un cycle complet (sauvegarde + reconnexion + requete),
                    # soit ~5s par carte au lieu de 0.5s (constate le
                    # 03/09/2026 : 93 cartes en 9 minutes).
                    #
                    # C'etait inutile : le contexte de requetes garde ses
                    # cookies en memoire, donc une rotation en cours de route
                    # est absorbee de façon transparente. Il suffit de
                    # sauvegarder regulierement pour que le fichier reste a
                    # jour si le run est interrompu.
                    if n % 200 == 0:
                        persist(ctx, STATE)

                    r = ctx.get(f"/api/marketplace/cards/{cid}/sales")
                    if r.status == 429:
                        print(f"  [{n}] 429 — pause 60s")
                        time.sleep(60)
                        continue
                    if r.status == 401:
                        raise SystemExit("401 — session premium expiree, relance wm_session_premium.py.")
                    if r.status >= 400:
                        rec = {"card_id": cid, "titre": title, "erreur": r.status}
                    else:
                        d = r.json()
                        ventes = d.get("sales", []) or []
                        prix = [v["final_price"] for v in ventes if v.get("final_price") is not None]
                        rec = {
                            "card_id": cid,
                            "titre": d.get("wikipedia_title", title),
                            "rarete": rarity,
                            "ventes": ventes,
                            "stats": summarize(prix),
                        }
                    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    fh.flush()

                    if rec.get("stats"):
                        s = rec["stats"]
                        print(f"  [{n}/{len(todo)}] {rec['titre'][:42]:42s} n={s['n']:3d} med={s['mediane']:>6} moy={s['moyenne']:>7}")
                    elif n % 250 == 0:
                        print(f"  [{n}/{len(todo)}] ... (sans vente)")

                    # Cadencement par DEBIT CIBLE, pas pause additive :
                    # l'appel dure deja ~0.27s, donc ajouter 0.2s donnait
                    # 0.47s/carte (2.1/s) alors que le plafond autorise est
                    # 5/s. On ne dort que le reliquat, et rien si l'appel a
                    # deja pris plus longtemps que l'intervalle visé.
                    reste_a_attendre = delay - (time.time() - t_iter)
                    if reste_a_attendre > 0:
                        time.sleep(reste_a_attendre)
        finally:
            persist(ctx, STATE)
            ctx.dispose()

    report(out_path, rarity)


def report(path: Path, rarity: str):
    if not path.exists():
        print(f"Aucun fichier {path}.")
        return
    recs = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            recs.append(json.loads(line))
        except Exception:
            continue
    avec = [r for r in recs if r.get("stats")]
    print(f"\n## Reference {rarity} — {len(recs)} carte(s) lue(s), {len(avec)} avec ventes\n")
    if not avec:
        return
    avec.sort(key=lambda r: r["stats"]["mediane"], reverse=True)
    print("| Carte | ventes | mediane | moyenne | min | max |")
    print("|---|---|---|---|---|---|")
    for r in avec[:30]:
        s = r["stats"]
        print(f"| {r['titre'][:44]} | {s['n']} | {s['mediane']} | {s['moyenne']} | {s['min']} | {s['max']} |")
    med_globale = statistics.median([r["stats"]["mediane"] for r in avec])
    print(f"\nMediane des medianes ({rarity}) : **{med_globale} wb**")


if __name__ == "__main__":
    main()
