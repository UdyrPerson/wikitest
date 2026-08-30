"""
Rapport en lecture seule : agrege les cartes rares (UR et L par defaut) de
plusieurs comptes, et affiche le solde de wikibidous d'un compte designe.

N'ecrit rien sur le site -- que des GET. C'est le pendant "informationnel"
des workflows d'action (ouverture, defausse, trade).

    python wm_report_rares.py compte1=state1.json collecteur=state3.json
    python wm_report_rares.py --rarities UR,L,SR --balance-of collecteur ...

Chaque argument positionnel est une paire `label=chemin_session`. Le label
sert uniquement a l'affichage (le site n'expose pas le pseudo du compte
courant sur les endpoints utilises ici).

La sortie est du Markdown, pour etre lisible telle quelle dans le resume
d'un run GitHub Actions ($GITHUB_STEP_SUMMARY) comme dans un terminal.

Un compte dont la session est expiree est signale et saute, sans faire
echouer le rapport des autres -- meme principe que le continue-on-error des
workflows de defausse et de trade (les jetons Supabase expirent en 1h).
"""

import random
import sys
import time
from collections import defaultdict
from pathlib import Path

from playwright.sync_api import sync_playwright

from wm_session_io import ensure_fresh, persist

BASE = "https://www.wiki-masters.com"

DELAY = (2.0, 3.0)
DEFAULT_RARITIES = ["UR", "L"]

# De la plus rare a la moins rare, pour trier l'affichage.
RARITY_ORDER = ["L", "UR", "SR", "R", "PC", "C"]


def fetch_rarity(req_ctx, rarity: str):
    """Toutes les cartes d'une rarete pour ce compte, en paginant jusqu'a
    une page vide. Leve SystemExit sur session expiree."""
    rows = []
    page = 0
    while True:
        resp = req_ctx.get(f"/api/my-collection?sort=rarity&rarity={rarity}&page={page}&stats=0")
        if resp.status == 401:
            raise SystemExit("401 sur my-collection — session expiree.")
        if resp.status == 403:
            raise SystemExit(f"403 sur my-collection : {resp.text()[:300]}")
        if resp.status == 429:
            print(f"    429 sur {rarity} — pause 60s", file=sys.stderr)
            time.sleep(60)
            continue
        if resp.status >= 400:
            print(f"    {resp.status} sur my-collection ({rarity}) — on arrete cette rarete", file=sys.stderr)
            break

        batch = resp.json().get("collection", [])
        if not batch:
            break
        rows.extend(batch)
        page += 1
        time.sleep(random.uniform(*DELAY))

    return rows


def get_balance(req_ctx):
    resp = req_ctx.get("/api/wikibidous")
    if resp.status >= 400:
        return None
    return resp.json().get("balance")


def main():
    accounts = []
    rarities = DEFAULT_RARITIES
    balance_of = None

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--rarities":
            rarities = args[i + 1].split(",")
            i += 2
        elif a == "--balance-of":
            balance_of = args[i + 1]
            i += 2
        else:
            if "=" not in a:
                raise SystemExit(f"Argument invalide {a!r} — attendu label=chemin_session.")
            label, path = a.split("=", 1)
            accounts.append((label, Path(path)))
            i += 1

    if not accounts:
        raise SystemExit(
            "Usage: python wm_report_rares.py <label>=<session.json> [...] "
            "[--rarities UR,L] [--balance-of <label>]"
        )

    # title -> {"rarity": str, "holders": {label: count}}
    aggregate = {}
    skipped = []
    balance = None

    with sync_playwright() as p:
        for label, path in accounts:
            if not path.exists():
                skipped.append((label, f"{path} introuvable"))
                continue

            req_ctx = ensure_fresh(p, path, BASE)
            try:
                for rarity in rarities:
                    for row in fetch_rarity(req_ctx, rarity):
                        card = row.get("card", {})
                        title = card.get("wikipedia_title", "?")
                        entry = aggregate.setdefault(
                            title,
                            {"rarity": card.get("rarity", rarity), "holders": defaultdict(int)},
                        )
                        entry["holders"][label] += row.get("count", 1)

                if balance_of is not None and label == balance_of:
                    balance = get_balance(req_ctx)
            except SystemExit as e:
                skipped.append((label, str(e)))
            finally:
                # Meme un simple GET peut faire tourner le refresh token
                # cote serveur : sans sauvegarde, ce rapport en lecture
                # seule revoquerait les sessions qu'il consulte
                # (cf wm_session_io).
                persist(req_ctx, path)
                req_ctx.dispose()

    # --- Rapport Markdown ---
    print(f"## Cartes {'/'.join(rarities)} — {len(accounts)} comptes\n")

    if aggregate:
        def sort_key(item):
            title, data = item
            rank = RARITY_ORDER.index(data["rarity"]) if data["rarity"] in RARITY_ORDER else len(RARITY_ORDER)
            return (rank, title.lower())

        total_copies = sum(sum(d["holders"].values()) for d in aggregate.values())
        print(f"**{len(aggregate)} carte(s) distincte(s)**, {total_copies} exemplaire(s) au total.\n")
        print("| Carte | Rarete | Detenue par |")
        print("|---|---|---|")
        for title, data in sorted(aggregate.items(), key=sort_key):
            holders = ", ".join(
                f"{lbl} x{n}" if n > 1 else lbl
                for lbl, n in sorted(data["holders"].items())
            )
            print(f"| {title} | {data['rarity']} | {holders} |")
    else:
        print("_Aucune carte de ces raretes sur les comptes lus._")

    if balance_of is not None:
        print()
        if balance is None:
            print(f"**Solde de {balance_of}** : indisponible (session expiree ou compte non lu).")
        else:
            print(f"**Solde de {balance_of}** : {balance} wb")

    if skipped:
        print("\n### Comptes non lus\n")
        for label, reason in skipped:
            print(f"- **{label}** : {reason}")


if __name__ == "__main__":
    main()
