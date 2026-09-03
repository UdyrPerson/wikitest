"""
Choisit et met en vente les meilleures cartes d'un compte, sur la base de
la table de reference des prix reels (reference/{rarete}.json).

    python wm_sell_auto.py state5.json                    # simulation
    python wm_sell_auto.py state5.json --go               # met vraiment en vente
    python wm_sell_auto.py state5.json --rarities L,UR    # quand UR sera scrape

STRATEGIE (definie le 03/09/2026)

1. Prix demande = moyenne des ventes reelles x 1.10.

2. On n'utilise cette moyenne que si elle veut dire quelque chose. Le
   critere initialement envisage etait l'ecart min-max ; les donnees l'ont
   ecarte : avec ~28 ventes par carte, min et max sont des valeurs
   extremes par construction, et (max-min)/moyenne vaut 2.72 en mediane
   sur les 1798 L. Meme les 10% de cartes les plus regulieres sont a 1.57 :
   ce critere rejetterait tout le catalogue sans rien distinguer.

   On mesure donc la dispersion par l'INTERQUARTILE, (q3-q1)/mediane, qui
   ignore les extremes et discrimine reellement : mediane 0.75, un seuil a
   0.70 retient 45% des cartes, a 0.50 il en retient 20%.

3. Priorisation. Les emplacements d'enchere sont rares (SLOTS_MAX), donc
   la question n'est pas "quelles cartes peut-on vendre" mais "lesquelles
   rapportent le plus par emplacement occupe". On classe par ESPERANCE DE
   GAIN :

       score = prix_demande x P(une vente passee >= prix_demande)

   La probabilite est recalculee a l'execution depuis la liste des prix
   observes, donc elle suit la marge choisie. C'est ce qui evite de
   monopoliser un emplacement avec une carte chere qui ne partira jamais,
   ou de le gacher avec une carte qui part a coup sur mais ne rapporte
   rien.

   Ordre de grandeur a garder en tete : a moyenne x 1.10, seules ~33% des
   ventes passees atteignaient le prix demande. Environ deux annonces sur
   trois n'auront donc pas preneur au premier tour -- c'est attendu, et
   c'est ce que le repositionnement de prix viendra rattraper.

4. Une seule annonce par carte distincte et par passage : deux exemplaires
   de la meme carte mis en vente ensemble se concurrencent et tirent le
   prix vers le bas.

La duree de 6 h (DUREE_MIN) et la marge de 10% viennent de la consigne.
"""

import json
import random
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from wm_session_io import ensure_fresh, persist

BASE = "https://www.wiki-masters.com"
REF = Path("reference")

DELAY = (2.0, 3.0)

MARGE = 1.10
DUREE_MIN = 360  # 6 h
SLOTS_MAX = 3    # consigne ; borne aussi par ce que renvoie le serveur

# Fiabilite de la moyenne. Voir le point 2 du docstring : seuil sur la
# dispersion interquartile, pas sur l'ecart min-max.
DISPERSION_MAX = 0.70
N_MIN = 5


def charger_reference(rarity: str):
    path = REF / f"{rarity}.json"
    if not path.exists():
        print(f"  pas de table de reference pour {rarity} ({path}) — rarete ignoree")
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def collection(req_ctx, rarity: str):
    """Toutes les possessions d'une rarete, toutes pages confondues."""
    rows, page = [], 0
    while True:
        r = req_ctx.get(f"/api/my-collection?sort=rarity&rarity={rarity}&page={page}&stats=0")
        if r.status == 401:
            raise SystemExit("401 sur my-collection — session expiree.")
        if r.status >= 400:
            print(f"  {r.status} sur my-collection ({rarity}) page {page} — on arrete la pagination")
            break
        lot = r.json().get("collection", [])
        if not lot:
            break
        rows += lot
        page += 1
        time.sleep(random.uniform(*DELAY))
    return rows


def slots_libres(req_ctx):
    r = req_ctx.get("/api/marketplace/mine")
    if r.status >= 400:
        raise SystemExit(f"{r.status} sur /api/marketplace/mine : {r.text()[:200]}")
    d = r.json()
    en_cours = d.get("sellingCount", 0)
    plafond = d.get("maxConcurrentAuctions", SLOTS_MAX)
    return en_cours, plafond, max(0, min(SLOTS_MAX, plafond - en_cours))


def cartes_deja_en_vente(req_ctx):
    """Identifiants de cartes deja sur le marche pour ce compte.

    PIEGE : mine=1 ne filtre PAS le tableau "auctions" -- celui-ci reste le
    marche entier (16 793 annonces actives le 03/09/2026). Le parametre
    ajoute a cote des tableaux "selling" / "bidding" / "history" / "won",
    et ce sont eux qui concernent le compte. Lire "auctions" ici revenait
    a s'interdire 50 cartes prises au hasard sur le marche."""
    r = req_ctx.get("/api/marketplace?page=1&limit=50&sort=ending_soon&mine=1")
    if r.status >= 400:
        return set()
    d = r.json()
    out = set()
    for a in d.get("selling") or []:
        cid = a.get("card_id") or (a.get("card") or {}).get("id")
        if cid:
            out.add(cid)
    return out


def proba_vente(prix_observes, demande: float) -> float:
    """Part des ventes passees qui ont atteint ou depasse le prix demande."""
    if not prix_observes:
        return 0.0
    return sum(1 for p in prix_observes if p >= demande) / len(prix_observes)


def candidats(rows, ref, exclues):
    """Possessions vendables, evaluees et triees par esperance de gain."""
    out, vus = [], set()
    for row in rows:
        card = row.get("card") or {}
        cid = card.get("id")
        if not cid or cid in exclues or cid in vus:
            continue

        fiche = ref.get(cid)
        if not fiche:
            continue  # carte absente de la table : aucun prix de reference

        if fiche["n"] < N_MIN or fiche["med"] <= 0:
            continue
        dispersion = (fiche["q3"] - fiche["q1"]) / fiche["med"]
        if dispersion > DISPERSION_MAX:
            continue

        demande = int(round(fiche["moy"] * MARGE))
        if demande <= 0:
            continue
        p = proba_vente(fiche["prix"], demande)

        vus.add(cid)
        out.append({
            "possession": row["id"],
            "card_id": cid,
            "titre": fiche["t"],
            "rarete": card.get("rarity", "?"),
            "n": fiche["n"],
            "moyenne": fiche["moy"],
            "dispersion": dispersion,
            "demande": demande,
            "p_vente": p,
            "score": demande * p,
        })

    out.sort(key=lambda c: c["score"], reverse=True)
    return out


def vendre(req_ctx, possession: str, prix: int, duree: int):
    r = req_ctx.post(
        "/api/marketplace",
        data={"card_id": possession, "base_amount": prix, "duration_minutes": duree},
    )
    if r.status == 401:
        raise SystemExit("401 sur la mise en vente — session expiree.")
    if r.status == 429:
        raise SystemExit("429 sur la mise en vente — on s'arrete plutot que d'insister.")
    if r.status >= 400:
        print(f"    {r.status} : {r.text()[:300]}")
        return None
    return r.json().get("auction_id")


def main():
    argv = sys.argv[1:]

    def opt(nom, defaut=None):
        return argv[argv.index(nom) + 1] if nom in argv else defaut

    state = next((a for a in argv if not a.startswith("--") and a.endswith(".json")), "storage_state.json")
    if not Path(state).exists():
        raise SystemExit(f"{state} introuvable.")
    if "premium" in Path(state).name:
        raise SystemExit("REFUS : le compte premium ne sert qu'au scrape de reference.")

    raretes = [r.strip().upper() for r in (opt("--rarities", "L")).split(",") if r.strip()]
    duree = int(opt("--duration", DUREE_MIN))
    marge = float(opt("--margin", MARGE))
    go = "--go" in argv

    with sync_playwright() as p:
        ctx = ensure_fresh(p, state, BASE)
        try:
            en_cours, plafond, libres = slots_libres(ctx)
            print(f"Session : {state}")
            print(f"Encheres : {en_cours}/{plafond} en cours — {libres} emplacement(s) utilisable(s)")
            print(f"Reglage  : moyenne x {marge}, duree {duree} min, "
                  f"dispersion <= {DISPERSION_MAX}, n >= {N_MIN}\n")

            exclues = cartes_deja_en_vente(ctx)
            if exclues:
                print(f"{len(exclues)} carte(s) deja sur le marche, ecartee(s).\n")

            tous = []
            for rarete in raretes:
                ref = charger_reference(rarete)
                if not ref:
                    continue
                rows = collection(ctx, rarete)
                cands = candidats(rows, ref, exclues)
                print(f"## {rarete} — {len(rows)} possession(s), {len(cands)} vendable(s) apres filtrage")
                tous += cands

            if not tous:
                print("\nAucune carte ne passe les criteres.")
                return

            tous.sort(key=lambda c: c["score"], reverse=True)
            print(f"\n{'':2s} {'carte':40s} {'n':>4s} {'moy':>8s} {'disp':>5s} {'demande':>8s} {'p':>5s} {'score':>8s}")
            for i, c in enumerate(tous[:10], 1):
                marque = "->" if i <= libres else "  "
                print(f"{marque} {c['titre'][:40]:40s} {c['n']:>4d} {c['moyenne']:>8.0f} "
                      f"{c['dispersion']:>5.2f} {c['demande']:>8d} {c['p_vente']:>5.0%} {c['score']:>8.0f}")

            retenues = tous[:libres]
            if not go:
                print(f"\nSimulation. {len(retenues)} carte(s) seraient mises en vente.")
                print("Ajoute --go pour executer.")
                return

            if libres == 0:
                print("\nAucun emplacement libre — rien a faire.")
                return

            print()
            for c in retenues:
                aid = vendre(ctx, c["possession"], c["demande"], duree)
                if aid:
                    print(f"  en vente — {c['titre'][:40]} a {c['demande']} wb "
                          f"(p~{c['p_vente']:.0%}) — enchere {aid}")
                else:
                    print(f"  ECHEC — {c['titre'][:40]}")
                time.sleep(random.uniform(*DELAY))
        finally:
            persist(ctx, state)
            ctx.dispose()


if __name__ == "__main__":
    main()
