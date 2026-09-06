"""
Achete au prix demande toutes les encheres actives d'un vendeur donne,
depuis un compte designe. Pure API, pas de navigateur.

    python wm_market_buy.py state3.json              # simulation
    python wm_market_buy.py state3.json --go         # mise reelle
    python wm_market_buy.py state3.json --go --complet

LE VENDEUR CIBLE N'EST PAS DANS LE CODE

Il est lu dans la variable d'environnement WM_MARKET_SELLER (secret
GitHub du meme nom), et n'est JAMAIS imprime : le depot est public, et
ses journaux d'execution le sont donc aussi. La variable accepte
indifferemment l'UUID du vendeur (recommande, opaque) ou son pseudo.

CE QUE VEUT DIRE « AU PRIX FIXE PAR LE VENDEUR »

La mise minimale du jeu, lue dans le JS du site le 06/09/2026 :

    current_bid == null -> base_amount
    sinon               -> max(ceil(1.1 * current_bid), current_bid + 1)

Autrement dit, le prix demande n'est atteignable QUE tant que personne
d'autre n'a mise. Des qu'un tiers se positionne, le minimum saute de 10 %
et depasse le prix fixe. Ces encheres-la sont donc SAUTEES, jamais
surencheries : c'est la consigne, et c'est aussi ce qui empeche le script
de se faire entrainer dans une montee sans plafond.

POURQUOI UN BALAYAGE, ET CE QU'IL NE GARANTIT PAS

Aucun filtre par vendeur n'existe : seller_id, seller, user_id, sellerId
et search sont tous ignores par /api/marketplace (verifie le 06/09/2026,
le total reste identique et les resultats non filtres). Aucune route
« encheres d'un joueur » n'existe non plus dans le JS du site. Il faut
donc parcourir la liste globale -- environ 15 500 encheres actives.

Or cette liste bouge pendant qu'on la pagine. Un balayage complet du
06/09/2026 a vu 15 223 encheres distinctes pour un total annonce a
15 336 : les encheres qui se reglent en cours de route decalent les
suivantes vers l'avant, et ce qui passe sous le curseur n'est jamais lu.
Un balayage complet n'est donc PAS une garantie d'exhaustivite.

D'ou le mode par defaut, qui est plus sur que le mode complet : avec
sort=recent la liste est triee par date de creation decroissante, donc
les encheres recentes sont en tete. On s'arrete des qu'une page entiere
est plus vieille que la fenetre. Trois consequences :

- le balayage dure quelques secondes au lieu de trois minutes, donc la
  liste bouge beaucoup moins sous le curseur ;
- une enchere creee depuis le dernier passage est forcement dans la
  fenetre, quelle que soit sa duree ;
- --complet reste disponible pour un rattrapage, en acceptant sa perte.

IDEMPOTENCE

Repasser sur une enchere deja remportee ne coute rien : on saute celles
dont on est deja le meneur (current_bidder_id == notre UUID). C'est ce
qui rend le workflow sur-declenchable sans effet de bord.
"""

import argparse
import math
import os
import sys
import time
import unicodedata
from datetime import datetime, timedelta, timezone
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

from wm_session_io import ensure_fresh, identifiant, persist

BASE = "https://www.wiki-masters.com"
DELAY = 2.0          # entre deux mises, cf CLAUDE.md
DELAY_PAGE = 0.3     # entre deux pages de lecture, borne et ponctuel
FENETRE_MIN = 90     # on remonte 90 min en arriere par defaut

# PLAFOND DUR DE L'API, verifie le 06/09/2026 : limit=50 renvoie 50
# encheres, limit=100 et limit=200 en renvoient ZERO -- sans erreur, sans
# message. Demander plus grand ne rend pas le balayage plus rapide, il le
# rend aveugle : le script conclurait « aucune enchere du vendeur cible »
# sur un marche parfaitement fourni. La valeur est donc bornee, pas
# seulement documentee.
LIMITE = 50


def norm(s):
    """Forme comparable d'un pseudo : sans accents, sans casse, sans separateurs."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return "".join(c for c in s.lower() if c.isalnum())


def vise(auction, cible_norm) -> bool:
    """Cette enchere est-elle celle du vendeur cible ?

    On accepte l'UUID comme le pseudo : le secret peut contenir l'un ou
    l'autre, et l'UUID est preferable (stable meme si le vendeur se
    renomme, et il ne dit rien a qui lit le journal)."""
    v = auction.get("seller") or {}
    return cible_norm in {norm(v.get("id") or auction.get("seller_id")),
                          norm(v.get("username"))}


def mise_minimale(a) -> int:
    """Mise minimale acceptee, transcrite du JS du site (06/09/2026)."""
    cb = a.get("current_bid")
    if cb is None:
        return int(a.get("base_amount") or 0)
    return max(math.ceil(1.1 * cb), cb + 1)


def balayer(req, cible_norm, fenetre_min, complet, limite):
    """Encheres actives du vendeur cible, du plus recent au plus ancien."""
    seuil = datetime.now(timezone.utc) - timedelta(minutes=fenetre_min)
    vus, cibles = set(), []
    page, total, pages_lues = 1, None, 0

    while True:
        r = req.get(f"/api/marketplace?page={page}&limit={limite}&sort=recent", timeout=60000)
        if r.status == 401:
            raise SystemExit("401 sur /api/marketplace — session expiree.")
        if r.status != 200:
            print(f"  page {page} : HTTP {r.status} — arret du balayage")
            break

        d = r.json()
        lot = d.get("auctions") or []
        total = d.get("total", total)
        pages_lues += 1
        if not lot:
            # Une premiere page vide alors que le marche annonce des
            # milliers d'encheres n'est pas une fin de liste : c'est un
            # symptome (limite hors bornes, filtre inattendu). Se taire
            # ici ferait conclure « aucune enchere cible » a tort.
            if page == 1 and (total or 0) > 0:
                raise SystemExit(
                    f"Page 1 vide alors que le marche annonce {total} encheres — "
                    f"parametres suspects (limite={limite})."
                )
            break

        recentes = 0
        for a in lot:
            aid = a.get("id")
            if aid in vus:
                continue
            vus.add(aid)
            cree = a.get("created_at") or ""
            try:
                if datetime.fromisoformat(cree.replace("Z", "+00:00")) >= seuil:
                    recentes += 1
            except ValueError:
                recentes += 1  # date illisible : on ne s'arrete pas dessus
            if a.get("status") == "active" and vise(a, cible_norm):
                cibles.append(a)

        # Arret des qu'une page entiere est plus ancienne que la fenetre :
        # le tri etant par date de creation decroissante, tout ce qui suit
        # l'est aussi.
        if not complet and recentes == 0:
            break
        if not d.get("hasMore"):
            break
        page += 1
        time.sleep(DELAY_PAGE)

    print(f"  {pages_lues} page(s) lue(s), {len(vus)} enchere(s) vues"
          + (f" sur {total} annoncees" if total else "")
          + (" (balayage complet)" if complet else f" (fenetre {fenetre_min} min)"))
    return cibles


def acheter(req, state_path, cible_norm, go, fenetre_min, complet, limite, plafond):
    moi = identifiant(state_path)
    if not moi:
        raise SystemExit("Impossible de lire l'UUID du compte dans la session — session vide ?")

    solde_resp = req.get("/api/wikibidous")
    if solde_resp.status == 401:
        raise SystemExit("401 sur /api/wikibidous — session expiree.")
    solde = int(solde_resp.json().get("balance") or 0)
    budget = solde if plafond is None else min(solde, plafond)
    print(f"Solde : {solde} wb" + (f" (plafond de depense : {plafond} wb)" if plafond else ""))

    cibles = balayer(req, cible_norm, fenetre_min, complet, limite)
    print(f"  {len(cibles)} enchere(s) active(s) du vendeur cible\n")

    achetees = depense = 0
    menees = depassees = trop_cheres = 0

    for a in cibles:
        titre = ((a.get("card") or {}).get("wikipedia_title")) or "?"
        prix = int(a.get("base_amount") or 0)

        if a.get("current_bidder_id") == moi:
            menees += 1
            print(f"  = deja meneur   {titre[:44]:46s} {prix} wb")
            continue

        # Un tiers a mise : le minimum depasse desormais le prix fixe par
        # le vendeur. On ne surenchérit pas, c'est la consigne.
        if a.get("current_bid") is not None:
            depassees += 1
            print(f"  ~ prix depasse  {titre[:44]:46s} demande {prix}, "
                  f"minimum {mise_minimale(a)} wb")
            continue

        if prix > budget - depense:
            trop_cheres += 1
            print(f"  ! budget court  {titre[:44]:46s} {prix} wb "
                  f"(reste {budget - depense} wb)")
            continue

        if not go:
            print(f"  . simulation    {titre[:44]:46s} {prix} wb")
            depense += prix
            achetees += 1
            continue

        resp = req.post(f"/api/marketplace/{a['id']}/bid",
                        data={"amount": prix}, timeout=90000)
        if resp.status >= 400:
            try:
                err = resp.json()
            except Exception:
                err = {}
            # Course perdue : quelqu'un a mise entre notre lecture et notre
            # POST, donc le minimum a saute de 10 %. Ce n'est pas une panne,
            # c'est exactement le cas ou la consigne dit de ne pas suivre.
            if err.get("code") == "bid_too_low":
                depassees += 1
                print(f"  ~ prix depasse  {titre[:44]:46s} demande {prix}, "
                      f"minimum {err.get('min', '?')} wb (mise pendant le balayage)")
            else:
                print(f"  x echec ({resp.status})  {titre[:44]:46s} {resp.text()[:120]}")
        else:
            achetees += 1
            depense += prix
            corps = resp.json() if resp.status != 204 else {}
            # Le serveur renvoie le solde a jour : plus fiable que notre
            # soustraction, les wikibidous etant retenus des la mise.
            if isinstance(corps.get("bidder_balance"), int):
                budget = corps["bidder_balance"] + depense
            print(f"  + mise posee    {titre[:44]:46s} {prix} wb")
        time.sleep(DELAY)

    verbe = "misees" if go else "retenues (simulation)"
    print(f"\n{achetees} enchere(s) {verbe} pour {depense} wb.")
    if menees or depassees or trop_cheres:
        print(f"Sautees : {menees} deja menee(s), {depassees} au prix depasse, "
              f"{trop_cheres} hors budget.")
    if not go:
        print("Simulation — relance avec --go pour miser reellement.")


def main():
    ap = argparse.ArgumentParser(description="Achete les encheres d'un vendeur cible.")
    ap.add_argument("state", help="fichier de session du compte acheteur")
    ap.add_argument("--go", action="store_true", help="mise reellement (sinon simulation)")
    ap.add_argument("--complet", action="store_true",
                    help="balaie tout le marche au lieu de la fenetre recente")
    ap.add_argument("--fenetre-min", type=int, default=FENETRE_MIN,
                    help=f"minutes d'anciennete balayees (defaut {FENETRE_MIN})")
    ap.add_argument("--limite", type=int, default=LIMITE,
                    help=f"taille de page, plafonnee a {LIMITE} par l'API")
    ap.add_argument("--max-depense", type=int, default=None,
                    help="plafond de depense pour ce passage, en wb")
    args = ap.parse_args()

    state_path = Path(args.state)
    if not state_path.exists():
        raise SystemExit(f"{state_path} introuvable.")

    limite = max(1, min(args.limite, LIMITE))
    if limite != args.limite:
        print(f"--limite {args.limite} ramene a {limite} (plafond de l'API).")

    cible = (os.environ.get("WM_MARKET_SELLER") or "").strip()
    if not cible:
        raise SystemExit(
            "WM_MARKET_SELLER est vide. Le vendeur cible n'est pas dans le code "
            "(depot public) : passe son UUID ou son pseudo par cette variable."
        )

    with sync_playwright() as p:
        req = ensure_fresh(p, state_path, BASE)
        try:
            acheter(req, state_path, norm(cible), args.go, args.fenetre_min,
                    args.complet, limite, args.max_depense)
        finally:
            # Le serveur a pu faire tourner le refresh token pendant ces
            # appels : sans sauvegarde, la session est revoquee au prochain
            # usage (cf wm_session_io).
            persist(req, state_path)
            req.dispose()


if __name__ == "__main__":
    main()
