"""
Rendement d'ouverture de chaque compte sur les 24 dernieres heures, en
Markdown, a coller dans le resume d'un run.

    python wm_pack_report.py packlog.json

CE QUE MESURE CE RAPPORT, ET POURQUOI

Le plafond du jeu vaut **144 paquets par 24 h glissantes**, etabli le
06/09/2026 sur 190 evenements de limite (cf CLAUDE.md). Ce nombre n'est
pas une regle anti-triche distincte : 144 = 1440 min / 10 min, soit
exactement la regeneration d'une journee pour un compte gratuit
(PACK_REGEN_PERIOD_MINUTES = 10, lu dans le JS du site).

D'ou le renversement que ce rapport rend visible : **etre limite n'est
pas un incident, c'est la preuve du rendement maximal.** Un compte a
144/144 travaille parfaitement. Le signal a surveiller est l'inverse --
un compte NETTEMENT EN DESSOUS de 144 n'a pas un probleme de quota mais
de disponibilite, presque toujours une session morte.

C'est un detecteur de panne plus fin que le code de sortie du job : un
compte dont la session meurt en milieu de journee laisse tous les runs
verts et ne se trahit que par un rendement qui s'effondre.

POURQUOI UN JOURNAL LOCAL PLUTOT QU'UNE LECTURE SERVEUR

Aucune route de statistiques n'existe cote site, et le message du 429 ne
chiffre pas le plafond (contrairement a celui des echanges, qui annonce
« 50/jour · PRO : 200/jour »). Le compte doit donc etre accumule par
wm_open_booster.py --compteur au fil des passages.
"""

import json
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

PLAFOND = 144          # paquets par 24 h glissantes, compte gratuit
CARTES_PAR_PAQUET = 5
SEUIL_ALERTE = 0.80    # en dessous : on signale explicitement


def charger(chemin: Path) -> dict:
    try:
        d = json.loads(chemin.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def main():
    if len(sys.argv) < 2:
        raise SystemExit("Usage: python wm_pack_report.py <packlog.json>")
    chemin = Path(sys.argv[1])
    journal = charger(chemin)

    minute = int(time.time() // 60)
    limite = minute - 24 * 60

    totaux = {}
    for label, entrees in journal.items():
        totaux[label] = sum(
            n for e in entrees
            if isinstance(e, list) and len(e) == 2 and e[0] >= limite
            for n in [e[1]]
        )

    print("## Rendement d'ouverture sur 24 h glissantes")
    print()
    if not totaux:
        print("Aucune donnee : le journal est vide ou vient d'etre cree.")
        print()
        print("Le compteur se remplit passage apres passage — un premier")
        print("chiffre complet demande 24 h de runs.")
        return

    # Le journal est jeune tant qu'il ne couvre pas 24 h : afficher
    # « 12 % » sur trois heures de donnees ferait paniquer pour rien.
    plus_vieux = min(
        (e[0] for entrees in journal.values() for e in entrees
         if isinstance(e, list) and len(e) == 2),
        default=minute,
    )
    couverture_h = (minute - plus_vieux) / 60
    partiel = couverture_h < 23

    print("| Compte | Paquets | Sur 144 | Cartes | |")
    print("|---|---:|---:|---:|---|")
    faibles = []
    for label in sorted(totaux, key=lambda x: (len(x), x)):
        n = totaux[label]
        part = n / PLAFOND
        barre = "█" * round(part * 20)
        marque = "" if partiel or part >= SEUIL_ALERTE else " ⚠"
        if not partiel and part < SEUIL_ALERTE:
            faibles.append(label)
        print(f"| {label} | {n} | {part * 100:.0f} % | "
              f"{n * CARTES_PAR_PAQUET} | `{barre}`{marque} |")

    total = sum(totaux.values())
    attendu = PLAFOND * len(totaux)
    print(f"| **Total** | **{total}** | **{total / attendu * 100:.0f} %** | "
          f"**{total * CARTES_PAR_PAQUET}** | |")
    print()

    if partiel:
        print(f"*Journal partiel : {couverture_h:.1f} h de recul seulement. "
              f"Les pourcentages ne seront comparables a 144 qu'apres 24 h.*")
        return

    if faibles:
        print(f"**Sous {SEUIL_ALERTE:.0%} du plafond : {', '.join(faibles)}.** "
              "Le quota n'est pas en cause — 144/24 h est la régénération "
              "même du jeu, donc un compte au plafond va bien. Un compte en "
              "dessous n'ouvre pas ce qu'il pourrait : vérifier sa session.")
    else:
        print("Tous les comptes sont au plafond ou proches : "
              "rien n'est perdu, le jeu ne régénère pas plus vite.")


if __name__ == "__main__":
    main()
