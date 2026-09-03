"""
Condense data/sales-{rarete}.jsonl en une table de reference compacte et
VERSIONNEE : reference/{rarete}.json.

    python wm_reference_build.py --rarity L

Pourquoi ce fichier existe : le scrape brut (data/sales-L.jsonl, 5,6 Mo)
contient l'historique complet de chaque vente, et surtout il vit dans
data/, qui est dans le .gitignore. Un runner GitHub Actions part d'un
checkout propre : sans table versionnee, le workflow de vente n'a aucun
prix de reference. On extrait donc le strict necessaire au calcul du prix,
et on le commite.

On conserve la LISTE DES PRIX de chaque carte, pas seulement les
statistiques agregees. Ca coute quelques centaines de kilo-octets, et ca
permet de recalculer a l'execution la probabilite de vente pour n'importe
quelle marge -- alors qu'une probabilite precalculee figerait la marge
choisie aujourd'hui.
"""

import json
import statistics
import sys
from pathlib import Path

DATA = Path("data")
REF = Path("reference")


def build(rarity: str):
    src = DATA / f"sales-{rarity}.jsonl"
    if not src.exists():
        raise SystemExit(f"{src} introuvable — lance wm_sales_reference.py --rarity {rarity} d'abord.")

    table = {}
    lues = 0
    for line in src.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except Exception:
            continue
        lues += 1
        stats = rec.get("stats")
        if not stats:
            continue  # carte sans vente connue : rien a en tirer

        prix = sorted(
            v["final_price"] for v in rec.get("ventes", []) if v.get("final_price") is not None
        )
        if not prix:
            continue

        table[rec["card_id"]] = {
            "t": rec.get("titre", "?"),
            "n": len(prix),
            "moy": round(stats["moyenne"], 1),
            "med": stats["mediane"],
            "q1": stats["q1"],
            "q3": stats["q3"],
            "min": prix[0],
            "max": prix[-1],
            "prix": prix,
        }

    REF.mkdir(exist_ok=True)
    out = REF / f"{rarity}.json"
    out.write_text(json.dumps(table, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    disp = sorted((c["q3"] - c["q1"]) / c["med"] for c in table.values() if c["med"] > 0)
    print(f"{lues} ligne(s) lue(s) — {len(table)} carte(s) avec ventes")
    print(f"Ecrit : {out} ({out.stat().st_size / 1024:.0f} Ko)")
    if disp:
        print(f"Dispersion interquartile — mediane {disp[len(disp) // 2]:.2f}, "
              f"p25 {disp[len(disp) // 4]:.2f}, p75 {disp[3 * len(disp) // 4]:.2f}")
        print(f"Prix median du catalogue : {statistics.median([c['med'] for c in table.values()])} wb")


def main():
    rarity = "L"
    if "--rarity" in sys.argv:
        rarity = sys.argv[sys.argv.index("--rarity") + 1].upper()
    build(rarity)


if __name__ == "__main__":
    main()
