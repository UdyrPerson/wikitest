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
   c'est ce que l'enchere degressive vient rattraper.

4. Cartes dont la moyenne n'est PAS exploitable : elles ne sont pas
   ecartees, elles sont releguees. Elles passent toujours apres les
   cartes fiables, quel que soit leur score, et ne servent qu'a occuper un
   emplacement dont aucune carte fiable ne veut -- un emplacement vide ne
   rapporte rien.

   Leur prix suit une ENCHERE DEGRESSIVE : on part de la valeur maximale
   jamais atteinte par la carte, et chaque passage sans acheteur divise le
   prix par deux, jusqu'au plancher de PRIX_PLANCHER. Ces cartes sont
   typiquement bimodales (une masse de ventes au plancher, quelques
   envolees) : partir du haut ne coute que du temps, alors que partir du
   bas laisserait passer l'envolee.

   L'etat de la descente n'est pas stocke localement -- un runner GitHub
   part d'un checkout propre. Il est relu depuis l'historique du compte
   (voir derniers_prix).

5. Une seule annonce par carte distincte et par passage : deux exemplaires
   de la meme carte mis en vente ensemble se concurrencent et tirent le
   prix vers le bas.

La duree de 6 h (DUREE_MIN) et la marge de 10% viennent de la consigne, de
meme que la liste des durees autorisees (DUREES_VALIDES).
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

# Durees proposees par le jeu : 1 h, 3 h, 6 h, 12 h, 24 h. Rien d'autre.
#
# Le serveur est plus permissif que ca -- il accepte n'importe quelle valeur
# entre 10 min et 24 h (verifie le 03/09/2026 : 15 min accepte, 5 min
# refuse avec "Duree invalide (entre 10 minutes et 24 heures)"). On s'en
# tient malgre tout aux paliers de l'interface : une annonce a une duree
# que le jeu ne propose pas se signale comme automatisee.
DUREES_VALIDES = (60, 180, 360, 720, 1440)
DUREE_MIN = 360  # 6 h
SLOTS_MAX = 3    # consigne ; borne aussi par ce que renvoie le serveur

# Fiabilite de la moyenne. Voir le point 2 du docstring : seuil sur la
# dispersion interquartile, pas sur l'ecart min-max.
DISPERSION_MAX = 0.70
N_MIN = 5

# Prix plancher. Toutes les raretes montrent une masse de ventes a
# exactement 10 wb : c'est le plancher pratique du marche, et la division
# par deux des cartes non fiables s'y arrete.
PRIX_PLANCHER = 10


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


def derniers_prix(req_ctx):
    """Dernier prix demande pour chaque carte restee invendue.

    C'est la memoire de l'enchere degressive, et elle vit sur le site, pas
    en local : un runner GitHub part d'un checkout propre, un fichier
    d'etat ne survivrait pas d'un run a l'autre.

    On retient le prix le PLUS BAS vu pour une carte, ce qui donne le
    dernier palier atteint sans avoir a trier par date : les prix ne font
    que descendre, division par deux apres division par deux."""
    r = req_ctx.get("/api/marketplace?page=1&limit=50&sort=ending_soon&mine=1")
    if r.status >= 400:
        return {}
    out = {}
    for a in r.json().get("history") or []:
        # Une annonce qui a trouve preneur ne se rejoue pas.
        if a.get("winner_id") or a.get("final_price"):
            continue
        cid = a.get("card_id") or (a.get("card") or {}).get("id")
        prix = a.get("base_amount")
        if cid and prix is not None and (cid not in out or prix < out[cid]):
            out[cid] = prix
    return out


def proba_vente(prix_observes, demande: float) -> float:
    """Part des ventes passees qui ont atteint ou depasse le prix demande."""
    if not prix_observes:
        return 0.0
    return sum(1 for p in prix_observes if p >= demande) / len(prix_observes)


def prix_degressif(fiche, dernier_demande):
    """Prix d'une carte dont la moyenne n'est pas exploitable.

    Enchere degressive : on part de la VALEUR MAXIMALE jamais atteinte par
    la carte, et chaque passage infructueux divise le prix par deux. Ces
    cartes sont typiquement bimodales -- une masse de ventes au plancher et
    quelques envolees -- donc partir du haut coute juste du temps, alors
    que partir du bas laisserait passer l'envolee.

    L'etat n'est pas stocke localement : un runner GitHub part d'un
    checkout propre. On repart du dernier prix demande, relu sur le site.
    """
    if dernier_demande is None:
        return max(int(fiche["max"]), PRIX_PLANCHER)
    return max(int(dernier_demande) // 2, PRIX_PLANCHER)


def candidats(rows, ref, exclues, derniers=None):
    """Possessions vendables, en deux niveaux de priorite.

    Niveau 1 -- la moyenne est exploitable : prix = moyenne x MARGE,
    classement par esperance de gain.

    Niveau 2 -- elle ne l'est pas : enchere degressive depuis le maximum.
    Ces cartes passent TOUJOURS apres les autres, quel que soit leur score,
    et ne servent qu'a occuper un emplacement dont personne d'autre ne veut
    (consigne du 03/09/2026). Un emplacement vide ne rapporte rien.
    """
    derniers = derniers or {}
    out, vus = [], set()
    for row in rows:
        card = row.get("card") or {}
        cid = card.get("id")
        if not cid or cid in exclues or cid in vus:
            continue

        fiche = ref.get(cid)
        if not fiche or fiche["med"] <= 0:
            continue  # carte absente de la table : aucun prix de reference

        dispersion = (fiche["q3"] - fiche["q1"]) / fiche["med"]
        fiable = fiche["n"] >= N_MIN and dispersion <= DISPERSION_MAX

        if fiable:
            demande = int(round(fiche["moy"] * MARGE))
        else:
            demande = prix_degressif(fiche, derniers.get(cid))
        if demande <= 0:
            continue

        vus.add(cid)
        out.append({
            "possession": row["id"],
            "card_id": cid,
            "titre": fiche["t"],
            "rarete": card.get("rarity", "?"),
            "n": fiche["n"],
            "moyenne": fiche["moy"],
            "dispersion": dispersion,
            "niveau": 1 if fiable else 2,
            "demande": demande,
            "p_vente": proba_vente(fiche["prix"], demande),
        })

    for c in out:
        c["score"] = c["demande"] * c["p_vente"]

    # Le niveau prime sur le score : une carte non fiable ne passe jamais
    # devant une carte fiable, meme si son esperance calculee est plus
    # elevee.
    out.sort(key=lambda c: (c["niveau"], -c["score"]))
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
    if duree not in DUREES_VALIDES:
        raise SystemExit(
            f"REFUS : duree {duree} min. Le jeu ne propose que "
            f"{', '.join(str(d) for d in DUREES_VALIDES)} minutes."
        )
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
                print(f"{len(exclues)} carte(s) deja sur le marche, ecartee(s).")
            derniers = derniers_prix(ctx)
            if derniers:
                print(f"{len(derniers)} carte(s) invendue(s) precedemment : prix divise par deux.")
            print()

            tous = []
            for rarete in raretes:
                ref = charger_reference(rarete)
                if not ref:
                    continue
                rows = collection(ctx, rarete)
                cands = candidats(rows, ref, exclues, derniers)
                n1 = sum(1 for c in cands if c["niveau"] == 1)
                print(f"## {rarete} — {len(rows)} possession(s), "
                      f"{n1} fiable(s) + {len(cands) - n1} en enchere degressive")
                tous += cands

            if not tous:
                print("\nAucune carte ne passe les criteres.")
                return

            tous.sort(key=lambda c: (c["niveau"], -c["score"]))
            print(f"\n{'':2s} {'niv':>3s} {'carte':38s} {'n':>4s} {'moy':>8s} {'disp':>6s} "
                  f"{'demande':>8s} {'p':>5s} {'score':>8s}")
            for i, c in enumerate(tous[:12], 1):
                marque = "->" if i <= libres else "  "
                print(f"{marque} {c['niveau']:>3d} {c['titre'][:38]:38s} {c['n']:>4d} "
                      f"{c['moyenne']:>8.0f} {c['dispersion']:>6.2f} {c['demande']:>8d} "
                      f"{c['p_vente']:>5.0%} {c['score']:>8.0f}")

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
